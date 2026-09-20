"""Alertas: avisarte cuando algo se rompe, antes de que lo note el usuario.

Se envía un POST con JSON a VDL_ALERT_WEBHOOK. El payload incluye las claves
`text` (Slack) y `content` (Discord) para que funcione con ambos sin cambios,
y también con cualquier automatización genérica (n8n, Make, Zapier).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Deque, Dict, List

from . import config

log = logging.getLogger("alerts")

# Evita repetir la misma alerta en ráfaga (una cada 10 minutos por clave).
_dedupe: Dict[str, float] = {}
_dedupe_lock = threading.Lock()
DEDUPE_WINDOW_SECONDS = 600

# Historial reciente. El widget lo usa para mostrar "último problema".
_history: Deque[dict] = deque(maxlen=30)
_history_lock = threading.Lock()


def recent_alerts(limit: int = 10) -> List[dict]:
    """Últimas alertas, de la más reciente a la más antigua."""
    with _history_lock:
        return list(_history)[-limit:][::-1]


def latest_problem() -> dict | None:
    """La alerta más reciente que no sea informativa."""
    with _history_lock:
        for entry in reversed(_history):
            if entry.get("level") in ("warning", "error"):
                return entry
    return None


def clear_history() -> None:
    """Reinicia el historial (acción de restablecimiento)."""
    with _history_lock:
        _history.clear()


def _should_send(key: str) -> bool:
    now = time.time()
    with _dedupe_lock:
        last = _dedupe.get(key, 0)
        if now - last < DEDUPE_WINDOW_SECONDS:
            return False
        _dedupe[key] = now
        return True


def send_alert(
    title: str,
    message: str,
    level: str = "warning",
    details: Dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> bool:
    """Emite una alerta. Devuelve True si se entregó al webhook."""
    line = f"[{level.upper()}] {title} — {message}"

    if level == "error":
        log.error(line)
    elif level == "warning":
        log.warning(line)
    else:
        log.info(line)

    # Se guarda siempre, haya webhook o no: el widget y el panel lo consumen.
    with _history_lock:
        _history.append(
            {
                "title": title,
                "message": message,
                "level": level,
                "at": time.time(),
                "details": details or {},
            }
        )

    if not config.ALERT_WEBHOOK:
        return False

    if dedupe_key and not _should_send(dedupe_key):
        log.debug("Alerta '%s' silenciada por deduplicación", dedupe_key)
        return False

    payload = {
        "service": "downloader",
        "level": level,
        "title": title,
        "message": message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        # Compatibilidad directa con Slack y Discord:
        "text": line,
        "content": line,
        "details": details or {},
    }

    try:
        request = urllib.request.Request(
            config.ALERT_WEBHOOK,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Sin proxy: el webhook es un destino directo.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("No se pudo entregar la alerta al webhook: %s", exc)
        return False
