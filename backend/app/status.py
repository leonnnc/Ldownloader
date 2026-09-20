"""Estado del sistema, calculado una sola vez para todos los consumidores.

Lo consumen tres cosas con necesidades distintas:

* **El widget de Android** — necesita textos cortos y un color. Los widgets de
  Android (RemoteViews) no interpretan CSS ni hacen peticiones de red: solo
  pintan cadenas y números que les pasa la app. Por eso hay un payload
  compacto aparte, con todo ya formateado.
* **El panel `/monitor`** — necesita el detalle completo.
* **Los endpoints de operación** — necesitan saber si conviene un reinicio.

Centralizar el cálculo evita que el widget y el panel digan cosas distintas.
"""

from __future__ import annotations

import time
from typing import List, Optional

from . import config, updater
from .alerts import latest_problem, recent_alerts
from .canary import canaries
from .jobs import store
from .media import ffmpeg_status
from .metrics import metrics
from .resilience import circuits

STATUS_OK = "ok"
STATUS_DEGRADED = "degradado"
STATUS_DOWN = "caido"

# Colores finales: el widget los pinta directamente, así que son hex, no tokens CSS.
COLORS = {
    STATUS_OK: "#1f9d6b",
    STATUS_DEGRADED: "#c68e31",
    STATUS_DOWN: "#c0392b",
}

LABELS = {
    STATUS_OK: "Sistema funcionando",
    STATUS_DEGRADED: "Funcionando con problemas",
    STATUS_DOWN: "Sistema caído",
}

# Umbrales: por debajo de aquí, el estado baja de nivel.
RATE_DEGRADED = 0.90
RATE_DOWN = 0.60
CIRCUITS_DEGRADED = 1
CIRCUITS_DOWN = 3

# Muestras mínimas antes de fiarse de la tasa de éxito.
# Con una sola petición fallida la tasa sería 0% y el monitor anunciaría
# "sistema caído". Eso sería un falso positivo: un usuario pegó un enlace roto,
# no se cayó el servicio. Hasta llegar a este mínimo, la tasa informa pero no
# decide el estado.
RATE_MIN_SAMPLES = 5


def human_ago(timestamp: Optional[float]) -> Optional[str]:
    """'ahora', 'hace 5m', 'hace 3h', 'hace 2d'."""
    if not timestamp:
        return None
    delta = max(0, int(time.time() - timestamp))
    if delta < 45:
        return "ahora"
    if delta < 3600:
        return f"hace {delta // 60}m"
    if delta < 86400:
        return f"hace {delta // 3600}h"
    return f"hace {delta // 86400}d"


def evaluate() -> dict:
    """Calcula el estado una sola vez. Es la fuente única de verdad."""
    ffmpeg = ffmpeg_status()
    snapshot = metrics.snapshot()

    requests = snapshot.get("requests", {})
    samples = sum(requests.values())
    rate = snapshot.get("overall_success_rate")
    # Hasta tener suficientes muestras, la tasa se informa pero no decide.
    trusted_rate = rate if samples >= RATE_MIN_SAMPLES else None

    platform_states = metrics.platform_states()
    degraded = sorted(n for n, s in platform_states.items() if s in ("degradado", "caido"))
    broken = sorted(n for n, s in platform_states.items() if s == "caido")

    open_circuits = circuits.open_count()
    canary_status = canaries.status()
    canary_failures = {
        name: strikes
        for name, strikes in canary_status.get("consecutive_failures", {}).items()
        if strikes > 0
    }

    active_jobs = store.jobs_waiting_for_upload()

    # --- Motivos: se listan para que el panel explique el diagnóstico ------
    reasons: List[str] = []
    if not ffmpeg["available"]:
        reasons.append("FFmpeg no disponible: no se puede convertir a MP3")
    if trusted_rate is not None and trusted_rate < RATE_DEGRADED:
        reasons.append(f"Tasa de éxito del {trusted_rate:.0%}")
    if open_circuits:
        reasons.append(
            f"{open_circuits} sitio{'s' if open_circuits > 1 else ''} en cuarentena por fallos"
        )
    for name, strikes in canary_failures.items():
        reasons.append(f"Canario de {name}: {strikes} fallo(s) seguido(s)")
    if broken:
        reasons.append("Plataformas caídas: " + ", ".join(broken))
    elif degraded:
        reasons.append("Plataformas degradadas: " + ", ".join(degraded))

    # --- Nivel de estado ---------------------------------------------------
    is_down = (
        not ffmpeg["available"]
        or (trusted_rate is not None and trusted_rate < RATE_DOWN)
        or open_circuits >= CIRCUITS_DOWN
        or bool(broken)
    )
    is_degraded = (
        not is_down
        and (
            open_circuits >= CIRCUITS_DEGRADED
            or bool(degraded)
            or bool(canary_failures)
            or (trusted_rate is not None and trusted_rate < RATE_DEGRADED)
        )
    )

    if is_down:
        status = STATUS_DOWN
    elif is_degraded:
        status = STATUS_DEGRADED
    else:
        status = STATUS_OK

    # --- ¿Conviene reiniciar? ---------------------------------------------
    # Reiniciar arregla: estado en memoria corrupto, circuitos atascados y
    # sobre todo cargar una versión nueva del motor ya instalada en disco.
    restart_reasons: List[str] = []
    if not ffmpeg["available"]:
        restart_reasons.append("FFmpeg no disponible")
    if status == STATUS_DOWN:
        restart_reasons.append("el sistema está caído")
    if open_circuits >= CIRCUITS_DOWN:
        restart_reasons.append(f"{open_circuits} circuitos abiertos")
    if trusted_rate is not None and trusted_rate < RATE_DOWN:
        restart_reasons.append(f"tasa de éxito muy baja ({trusted_rate:.0%})")

    state = updater.load_state()
    validated = state.get("validated_version")
    installed = updater.installed_version()
    if validated and installed and validated != installed:
        restart_reasons.append(
            f"hay una versión del motor validada sin aplicar ({validated})"
        )

    return {
        "status": status,
        "label": LABELS[status],
        "color": COLORS[status],
        "reasons": reasons,
        "restart_advised": bool(restart_reasons),
        "restart_reasons": restart_reasons,
        "success_rate": rate,
        "success_rate_label": f"{rate:.0%}" if rate is not None else "sin datos",
        "success_rate_trusted": trusted_rate is not None,
        "samples": samples,
        "degraded": degraded,
        "broken": broken,
        "circuits_open": open_circuits,
        "active_jobs": active_jobs,
        "canary_failures": canary_failures,
        "ffmpeg": ffmpeg["available"],
        "engine": {
            "installed": installed,
            "validated": validated,
            "channel": config.UPDATE_CHANNEL,
            "auto_update": config.AUTO_UPDATE,
        },
        "proxy_configured": bool(config.PROXY or config.PROXY_MAP),
        "last_canary_run": canaries.status().get("last_run_at"),
    }


def widget_payload() -> dict:
    """Payload compacto para un widget de Android.

    Todo viene ya formateado en cadenas cortas porque RemoteViews no tiene
    lógica: el widget solo puede pintar lo que recibe.
    """
    state = evaluate()
    problem = latest_problem()
    last_run = state.get("last_canary_run")

    # Un widget que dice "caído" sin decir por qué no sirve para decidir.
    # Si no hay una alerta registrada, se usa el primer motivo del diagnóstico.
    problem_title = (problem or {}).get("title")
    problem_ago = human_ago((problem or {}).get("at"))
    problem_level = (problem or {}).get("level")

    if not problem_title and state["reasons"]:
        problem_title = state["reasons"][0]
        problem_level = "error" if state["status"] == STATUS_DOWN else "warning"
        problem_ago = "ahora"

    return {
        # --- Lo que se pinta ---
        "status": state["status"],
        "status_label": state["label"],
        "color": state["color"],
        "success_rate": state["success_rate_label"],
        "circuits": state["circuits_open"],
        "jobs": state["active_jobs"],
        "engine": state["engine"]["installed"] or "?",
        "last_check": human_ago(last_run) if last_run else "sin revisar",
        # Primer problema, resumido a una línea.
        "problem": problem_title,
        "problem_ago": problem_ago,
        "problem_level": problem_level,
        # --- Decisión de reinicio ---
        "restart_advised": state["restart_advised"],
        "restart_reason": (
            state["restart_reasons"][0] if state["restart_reasons"] else None
        ),
        # --- Para los botones del widget ---
        "actions": {
            "refresh": "/api/widget",
            "restart": "/api/admin/restart",
            "reset_circuits": "/api/admin/circuits/reset",
            "run_canaries": "/api/admin/canaries/run",
            "purge": "/api/admin/storage/purge",
            "dashboard": "/monitor",
        },
        "auth": "header X-Admin-Token",
        "updated_at": int(time.time()),
        "updated": human_ago(time.time()),
    }


def full_status() -> dict:
    """Estado completo para el panel /monitor."""
    state = evaluate()
    return {
        **state,
        "metrics": metrics.snapshot(),
        "circuits": circuits.all(),
        "canaries": canaries.status(),
        "alerts": recent_alerts(limit=12),
        "jobs": store.counts(),
        "ffmpeg_detail": ffmpeg_status(),
        "config": {
            "auto_update": config.AUTO_UPDATE,
            "update_interval_hours": config.UPDATE_INTERVAL_HOURS,
            "canary_interval_minutes": config.CANARY_INTERVAL_MINUTES,
            "alert_webhook": bool(config.ALERT_WEBHOOK),
            "admin_enabled": bool(config.ADMIN_TOKEN),
            "file_ttl_minutes": config.FILE_TTL_MINUTES,
        },
        "server_time": int(time.time()),
    }
