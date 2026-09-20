"""Reintentos con backoff y circuit breaker por dominio.

Dos problemas distintos, dos mecanismos:

* **Reintentos con backoff** — un fallo aislado (red, timeout) merece otro
  intento. Esperar cada vez más evita martillar un servidor que ya está
  saturado y multiplicar el bloqueo.

* **Circuit breaker** — cuando un dominio falla N veces seguidas, seguir
  intentando es inútil: solo consume recursos y hace esperar al usuario.
  El circuito se abre, se falla rápido con un mensaje claro, y tras un
  enfriamiento se prueba de nuevo (medio abierto).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, TypeVar
from urllib.parse import urlparse

from . import config

log = logging.getLogger("resilience")

T = TypeVar("T")

CLOSED = "cerrado"          # todo normal
OPEN = "abierto"            # fallando rápido, sin intentos
HALF_OPEN = "medio_abierto"  # dejando pasar un intento de prueba


class CircuitOpenError(Exception):
    """El dominio está bloqueado temporalmente por fallos repetidos."""

    def __init__(self, host: str, retry_in: int) -> None:
        self.host = host
        self.retry_in = retry_in
        super().__init__(
            f"El sitio {host} está fallando repetidamente. "
            f"Reintentando automáticamente en {retry_in}s."
        )


@dataclass
class Circuit:
    host: str
    state: str = CLOSED
    failures: int = 0
    opened_at: float = 0.0
    total_opens: int = 0
    last_error: str | None = None
    # RLock, no Lock: varios métodos públicos se llaman entre sí (to_public
    # consulta retry_in) y con un Lock normal eso es un autobloqueo. El
    # endpoint del monitor se quedaba colgado sin devolver nunca.
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def allow(self) -> bool:
        """¿Se permite un intento contra este dominio ahora mismo?"""
        with self._lock:
            if self.state == CLOSED:
                return True

            if self.state == OPEN:
                elapsed = time.time() - self.opened_at
                if elapsed >= config.CIRCUIT_COOLDOWN_SECONDS:
                    self.state = HALF_OPEN
                    log.info("Circuito %s -> medio abierto (probando)", self.host)
                    return True
                return False

            # HALF_OPEN: se permite un intento de prueba.
            return True

    def retry_in(self) -> int:
        with self._lock:
            remaining = config.CIRCUIT_COOLDOWN_SECONDS - (time.time() - self.opened_at)
            return max(1, int(remaining))

    def record_success(self) -> None:
        with self._lock:
            if self.state != CLOSED:
                log.info("Circuito %s -> cerrado (recuperado)", self.host)
            self.state = CLOSED
            self.failures = 0
            self.last_error = None

    def record_failure(self, error: str | None = None) -> None:
        with self._lock:
            self.failures += 1
            self.last_error = (error or "")[:300]

            # Un fallo en medio abierto reabre el circuito de inmediato.
            if self.state == HALF_OPEN:
                self.state = OPEN
                self.opened_at = time.time()
                self.total_opens += 1
                log.warning("Circuito %s -> abierto otra vez (la prueba falló)", self.host)
                return

            if self.state == CLOSED and self.failures >= config.CIRCUIT_THRESHOLD:
                self.state = OPEN
                self.opened_at = time.time()
                self.total_opens += 1
                log.warning(
                    "Circuito %s -> abierto tras %s fallos seguidos",
                    self.host,
                    self.failures,
                )

    def to_public(self) -> dict:
        with self._lock:
            # Se calcula aquí en vez de llamar a retry_in(): aunque el lock es
            # reentrante, mantener las lecturas sin llamadas anidadas hace que
            # sea imposible reintroducir el bloqueo.
            retry_in = 0
            if self.state == OPEN:
                remaining = config.CIRCUIT_COOLDOWN_SECONDS - (time.time() - self.opened_at)
                retry_in = max(1, int(remaining))

            return {
                "host": self.host,
                "state": self.state,
                "failures": self.failures,
                "total_opens": self.total_opens,
                "last_error": self.last_error,
                "retry_in": retry_in,
            }


class CircuitRegistry:
    def __init__(self) -> None:
        self._circuits: Dict[str, Circuit] = {}
        self._lock = threading.Lock()

    def for_url(self, url: str) -> Circuit:
        host = (urlparse(url).hostname or "desconocido").lower()
        # Se agrupa por dominio raíz para que www.facebook.com y m.facebook.com
        # compartan circuito.
        parts = host.split(".")
        root = ".".join(parts[-2:]) if len(parts) >= 2 else host

        with self._lock:
            if root not in self._circuits:
                self._circuits[root] = Circuit(host=root)
            return self._circuits[root]

    def all(self) -> list[dict]:
        with self._lock:
            return [c.to_public() for c in self._circuits.values()]

    def open_count(self) -> int:
        with self._lock:
            return sum(1 for c in self._circuits.values() if c.state == OPEN)

    def reset_all(self) -> list[str]:
        """Cierra todos los circuitos. Acción de restablecimiento manual.

        Útil cuando sabes que el sitio ya se recuperó (por ejemplo, tras
        actualizar el motor) y no quieres esperar el enfriamiento.
        """
        # Se copia la lista para no mantener el lock del registro mientras se
        # toman los locks de cada circuito.
        with self._lock:
            circuits = list(self._circuits.values())

        affected = []
        for circuit in circuits:
            if circuit.state != CLOSED:
                circuit.record_success()
                affected.append(circuit.host)
        return affected


circuits = CircuitRegistry()


def backoff_delay(attempt: int) -> float:
    """Espera exponencial con jitter.

    attempt 1 -> ~2s, 2 -> ~4s, 3 -> ~8s (más un desvío aleatorio).
    El jitter evita que varios trabajos reintenten sincronizados.
    """
    base = config.RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    return base + random.uniform(0, base * 0.4)


def with_retry(
    fn: Callable[[], T],
    *,
    url: str,
    operation: str,
    attempts: int | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    should_retry: Callable[[Exception], bool] | None = None,
) -> T:
    """Ejecuta `fn` con reintentos, backoff y circuit breaker.

    `should_retry` permite no reintentar errores permanentes (por ejemplo,
    "el video no existe"): reintentar eso solo hace esperar al usuario.
    """
    attempts = attempts or config.RETRY_ATTEMPTS
    circuit = circuits.for_url(url)

    if not circuit.allow():
        raise CircuitOpenError(circuit.host, circuit.retry_in())

    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - se re-clasifica abajo
            last_error = exc

            if should_retry and not should_retry(exc):
                circuit.record_failure(str(exc))
                raise

            circuit.record_failure(str(exc))

            if attempt >= attempts:
                break

            delay = backoff_delay(attempt)
            log.warning(
                "%s: intento %s/%s falló (%s); reintentando en %.1fs",
                operation, attempt, attempts, str(exc)[:120], delay,
            )
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(delay)
        else:
            circuit.record_success()
            return result

    assert last_error is not None
    raise last_error
