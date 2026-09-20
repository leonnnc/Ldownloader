"""Canarios: detección temprana de roturas.

Cada X minutos se prueba un enlace conocido de cada plataforma. Dos usos:

1. **Vigilancia** — si Facebook deja de funcionar a las 3 de la mañana, te
   enteras por una alerta y no cuando el primer usuario se queja.
2. **Validación de actualizaciones** — antes de aceptar una versión nueva de
   yt-dlp, se comprueba que los canarios siguen pasando. Si no, se revierte.

Sin esto, la única señal de que el motor se rompió es el tráfico cayendo.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from . import config
from .alerts import send_alert
from .metrics import metrics

log = logging.getLogger("canary")


@dataclass
class Canary:
    name: str
    url: str
    min_formats: int = 1
    enabled: bool = True


@dataclass
class CanaryResult:
    name: str
    url: str
    ok: bool
    formats: int = 0
    seconds: float = 0.0
    error: str | None = None
    checked_at: float = field(default_factory=time.time)

    def to_public(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "formats": self.formats,
            "seconds": round(self.seconds, 2),
            "error": self.error,
            "checked_at": self.checked_at,
        }


class CanaryRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._canaries: List[Canary] = []
        self._last_results: Dict[str, CanaryResult] = {}
        self._consecutive_failures: Dict[str, int] = {}
        self._last_run_at: float | None = None
        self._load()

    # -- carga --------------------------------------------------------------
    def _load(self) -> None:
        path = Path(config.CANARY_FILE)
        if not path.is_file():
            log.warning("No se encontró %s: los canarios están desactivados", path)
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("canaries.json inválido (%s): canarios desactivados", exc)
            return

        loaded: List[Canary] = []
        for entry in data.get("canaries", []):
            if not entry.get("url"):
                continue
            loaded.append(
                Canary(
                    name=str(entry.get("name") or "sin_nombre"),
                    url=str(entry["url"]),
                    min_formats=int(entry.get("min_formats", 1)),
                    enabled=bool(entry.get("enabled", True)),
                )
            )
        self._canaries = [c for c in loaded if c.enabled]
        log.info("Canarios cargados: %s", [c.name for c in self._canaries] or "ninguno")

    def reload(self) -> None:
        with self._lock:
            self._load()

    # -- ejecución ----------------------------------------------------------
    def run_one(self, canary: Canary) -> CanaryResult:
        """Prueba un canario. Importa el motor aquí para evitar ciclos."""
        from . import downloader

        started = time.time()
        try:
            info = downloader.parse(canary.url, skip_retry=True)
            count = len(info.get("video_formats") or [])
            ok = count >= canary.min_formats
            result = CanaryResult(
                name=canary.name,
                url=canary.url,
                ok=ok,
                formats=count,
                seconds=time.time() - started,
                error=None if ok else f"solo {count} formatos (esperaba {canary.min_formats})",
            )
        except Exception as exc:  # noqa: BLE001 - cualquier fallo es un canario rojo
            result = CanaryResult(
                name=canary.name,
                url=canary.url,
                ok=False,
                seconds=time.time() - started,
                error=str(exc)[:300],
            )

        metrics.record(f"canary::{canary.name}", result.ok, result.error)
        return result

    def run_all(self, quiet: bool = False) -> List[CanaryResult]:
        with self._lock:
            canaries = list(self._canaries)

        if not canaries:
            return []

        results: List[CanaryResult] = []
        for canary in canaries:
            result = self.run_one(canary)
            results.append(result)

            with self._lock:
                self._last_results[canary.name] = result
                if result.ok:
                    self._consecutive_failures[canary.name] = 0
                else:
                    self._consecutive_failures[canary.name] = (
                        self._consecutive_failures.get(canary.name, 0) + 1
                    )
                strikes = self._consecutive_failures[canary.name]

            status = "OK " if result.ok else "FALLO"
            log.info(
                "Canario %-10s %s  (%s formatos, %.1fs)",
                canary.name, status, result.formats, result.seconds,
            )

            # Alerta solo tras varios fallos seguidos: un fallo aislado puede
            # ser un timeout de red y no merece despertar a nadie.
            if not result.ok and strikes >= config.CANARY_ALERT_AFTER:
                send_alert(
                    title=f"Plataforma caída: {canary.name}",
                    message=(
                        f"{strikes} fallos consecutivos. Último error: {result.error}"
                    ),
                    level="error",
                    details=result.to_public(),
                    dedupe_key=f"canary:{canary.name}",
                )

        with self._lock:
            self._last_run_at = time.time()

        if not quiet:
            passing = sum(1 for r in results if r.ok)
            rate = passing / len(results) * 100
            if rate < config.CANARY_MIN_PASS_RATE:
                send_alert(
                    title="Salud general del motor por debajo del umbral",
                    message=(
                        f"Solo {passing}/{len(results)} canarios pasan "
                        f"({rate:.0f}%, mínimo {config.CANARY_MIN_PASS_RATE}%)."
                    ),
                    level="error",
                    details={"results": [r.to_public() for r in results]},
                    dedupe_key="canary:global",
                )

        return results

    # -- validación para el actualizador -----------------------------------
    def validation_report(self) -> dict:
        """Ejecuta los canarios y resume si el motor está sano.

        Lo usa el actualizador: si tras actualizar yt-dlp esto sale mal,
        se revierte la versión.
        """
        results = self.run_all(quiet=True)
        if not results:
            # Sin canarios no se puede validar: no se bloquea la actualización.
            return {"valid": True, "reason": "sin canarios configurados", "results": []}

        passing = sum(1 for r in results if r.ok)
        rate = passing / len(results) * 100
        valid = rate >= config.CANARY_MIN_PASS_RATE

        return {
            "valid": valid,
            "pass_rate": round(rate, 1),
            "passing": passing,
            "total": len(results),
            "reason": None if valid else f"solo {passing}/{len(results)} canarios pasan",
            "results": [r.to_public() for r in results],
        }

    # -- lectura ------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": config.CANARY_ENABLED and bool(self._canaries),
                "interval_minutes": config.CANARY_INTERVAL_MINUTES,
                "last_run_at": self._last_run_at,
                "min_pass_rate": config.CANARY_MIN_PASS_RATE,
                "alert_after": config.CANARY_ALERT_AFTER,
                "consecutive_failures": dict(self._consecutive_failures),
                "results": [r.to_public() for r in self._last_results.values()],
            }


canaries = CanaryRunner()
