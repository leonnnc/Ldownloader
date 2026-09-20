"""Configuración central del backend.

Todo se controla por variables de entorno para que el mismo código sirva
en local y dentro de Docker.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# --- Almacenamiento ---------------------------------------------------------
# Directorio donde viven las descargas temporales.
DATA_DIR = Path(os.getenv("VDL_DATA_DIR", BASE_DIR / "storage")).resolve()

# Minutos antes de borrar un archivo terminado.
FILE_TTL_MINUTES = _env_int("VDL_FILE_TTL_MINUTES", 15)

# Cada cuántos segundos corre el recolector de basura.
CLEANUP_INTERVAL_SECONDS = _env_int("VDL_CLEANUP_INTERVAL", 60)

# --- Descargas --------------------------------------------------------------
# Máximo de descargas simultáneas (yt-dlp corre como subproceso).
MAX_CONCURRENT_JOBS = _env_int("VDL_MAX_CONCURRENT_JOBS", 2)

# Tamaño máximo aceptado por archivo, en MB. 0 = sin límite.
MAX_FILESIZE_MB = _env_int("VDL_MAX_FILESIZE_MB", 0)

# Timeout de la extracción de metadatos (segundos).
PARSE_TIMEOUT = _env_int("VDL_PARSE_TIMEOUT", 60)

# --- Rate limiting ----------------------------------------------------------
RATE_LIMIT_REQUESTS = _env_int("VDL_RATE_LIMIT_REQUESTS", 20)
RATE_LIMIT_WINDOW_SECONDS = _env_int("VDL_RATE_LIMIT_WINDOW", 60)

# --- Seguridad --------------------------------------------------------------
# Lista blanca de dominios. Vacía = permitir todos los que yt-dlp soporte.
# Se recomienda acotarla si el servicio es público.
ALLOWED_DOMAINS = [
    d.strip().lower()
    for d in os.getenv("VDL_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
]

# Archivo de cookies (formato Netscape) para contenido que requiere sesión.
COOKIES_FILE = os.getenv("VDL_COOKIES_FILE", "")

# --- FFmpeg -----------------------------------------------------------------
# Ruta explícita al binario o a su carpeta. Si está vacío se autodetecta.
FFMPEG_LOCATION = os.getenv("VDL_FFMPEG_LOCATION", "")

# --- Servidor ---------------------------------------------------------------
HOST = os.getenv("VDL_HOST", "127.0.0.1")
PORT = _env_int("VDL_PORT", 8000)


# ===========================================================================
# Resiliencia
#
# Esta es la parte que decide si el servicio sigue vivo en seis meses o si
# muere en tres semanas. Cada valor aquí existe para cubrir un modo de fallo.
# ===========================================================================


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


# --- Actualización automática del motor -------------------------------------
# yt-dlp publica versiones nuevas cada pocos días porque los sitios cambian.
# Sin esto, el servicio se degrada solo.
AUTO_UPDATE = _env_bool("VDL_AUTO_UPDATE", True)

# Cada cuántas horas revisar si hay versión nueva.
UPDATE_INTERVAL_HOURS = _env_int("VDL_UPDATE_INTERVAL_HOURS", 12)

# "stable" (recomendado) o "nightly" (arregla antes, rompe más seguido).
UPDATE_CHANNEL = os.getenv("VDL_UPDATE_CHANNEL", "stable").strip().lower()

# Si la versión nueva no pasa los canarios, se vuelve a esta porción.
# 0 = volver siempre a la última que funcionó.
# -1 = reemplazar por la última que funcionó (idéntico a 0). Ver updater.py.
UPDATE_ROLLBACK_ON_FAILURE = _env_bool("VDL_UPDATE_ROLLBACK", True)

# Archivo donde se recuerda la última versión que pasó la validación.
UPDATE_STATE_FILE = os.getenv(
    "VDL_UPDATE_STATE_FILE", str(BASE_DIR / "update_state.json")
)

# Tras una actualización validada, el proceso sigue ejecutando el yt-dlp viejo
# (Python ya lo tiene en memoria). Con un supervisor (systemd/docker) basta con
# salir para que vuelva a arrancar con la versión nueva.
# Actívalo SOLO si tienes un supervisor que reinicie el servicio.
RESTART_AFTER_UPDATE = _env_bool("VDL_RESTART_AFTER_UPDATE", False)

# Alternativa al reinicio propio: comando que reinicia el servicio desde fuera.
# Útil cuando el actualizador corre como tarea programada separada.
# Ejemplos:
#   systemctl restart downloader-api
#   docker restart downloader-api
RESTART_COMMAND = os.getenv("VDL_RESTART_COMMAND", "")

# --- Canarios (detección temprana de roturas) -------------------------------
# Prueban enlaces reales y conocidos cada X minutos. Si un sitio deja de
# funcionar, te enteras por una alerta, no porque los usuarios se quejen.
CANARY_ENABLED = _env_bool("VDL_CANARY_ENABLED", True)
CANARY_INTERVAL_MINUTES = _env_int("VDL_CANARY_INTERVAL_MINUTES", 30)
CANARY_FILE = os.getenv("VDL_CANARY_FILE", str(BASE_DIR / "canaries.json"))

# Porcentaje mínimo de canarios que deben pasar para considerar el motor sano.
CANARY_MIN_PASS_RATE = _env_int("VDL_CANARY_MIN_PASS_RATE", 60)

# Cuántos fallos seguidos de un canario antes de marcar la plataforma caída.
CANARY_ALERT_AFTER = _env_int("VDL_CANARY_ALERT_AFTER", 3)

# --- Alertas ----------------------------------------------------------------
# Webhook genérico (Slack, Discord, n8n, etc.). Se envía un POST con JSON.
ALERT_WEBHOOK = os.getenv("VDL_ALERT_WEBHOOK", "")

# --- Reintentos -------------------------------------------------------------
RETRY_ATTEMPTS = _env_int("VDL_RETRY_ATTEMPTS", 3)
RETRY_BASE_DELAY_SECONDS = _env_int("VDL_RETRY_BASE_DELAY", 2)

# --- Circuit breaker por dominio -------------------------------------------
# Tras N fallos seguidos contra un dominio, deja de intentar durante un rato.
# Evita colgar recursos reintentando contra un sitio que está caído.
CIRCUIT_THRESHOLD = _env_int("VDL_CIRCUIT_THRESHOLD", 5)
CIRCUIT_COOLDOWN_SECONDS = _env_int("VDL_CIRCUIT_COOLDOWN", 180)

# --- Cookies ----------------------------------------------------------------
# Carpeta con varios cookies*.txt. Se rotan para repartir la carga entre
# varias sesiones y reducir la probabilidad de que una sea bloqueada.
COOKIES_DIR = os.getenv("VDL_COOKIES_DIR", "")

# --- Proxies ----------------------------------------------------------------
# La capa que decide si el servicio sobrevive fuera de tu casa. En un servidor
# (datacenter), los sitios grandes bloquean la IP en cuestión de días.
# Sin proxy, el servicio funciona en local y muere en producción.
PROXY = os.getenv("VDL_PROXY", "")


def _parse_proxy_map(raw: str) -> dict:
    """Convierte "youtube=...,facebook=..." en un diccionario."""
    mapping: dict = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        platform, _, url = chunk.partition("=")
        platform, url = platform.strip().lower(), url.strip()
        if platform and url:
            mapping[platform] = url
    return mapping


# Proxies por plataforma. Ejemplo:
#   VDL_PROXY_MAP="youtube=http://user:pass@host:8080,facebook=http://user:pass@host:8081"
# Permite usar un pool distinto por sitio: si un proxy se quema en Facebook,
# YouTube sigue funcionando por otro.
PROXY_MAP = _parse_proxy_map(os.getenv("VDL_PROXY_MAP", ""))

# --- Administración ---------------------------------------------------------
# Token para los endpoints de administración (/api/admin/*). Vacío = deshabilitados.
ADMIN_TOKEN = os.getenv("VDL_ADMIN_TOKEN", "")

# --- Afinado del extractor --------------------------------------------------
# Clientes de reproducción para YouTube. Algunos funcionan cuando otros son
# bloqueados; rotarlos es una de las defensas más efectivas.
# Vacío = usar el comportamiento por defecto de yt-dlp (recomendado).
# Ejemplo para YouTube cuando hay bloqueos: "default,web_safari,android"
PLAYER_CLIENTS = [
    c.strip()
    for c in os.getenv("VDL_PLAYER_CLIENTS", "").split(",")
    if c.strip()
]
