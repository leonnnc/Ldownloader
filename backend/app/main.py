"""API FastAPI del servicio de descargas.

Endpoints
---------
GET  /                        -> interfaz web (SPA estática)
GET  /api/health              -> estado + disponibilidad de FFmpeg
POST /api/parse               -> resuelve un link y devuelve formatos
POST /api/download            -> encola una descarga (mp4 | mp3)
GET  /api/jobs/{job_id}       -> progreso del trabajo
GET  /api/file/{job_id}       -> entrega el archivo terminado
POST /api/facebook/private    -> extrae URLs desde el HTML pegado
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Deque, Dict
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, downloader, updater
from . import status as system_status
from .alerts import clear_history, send_alert
from .canary import canaries
from .downloader import EngineError
from .jobs import store
from .media import ffmpeg_status
from .metrics import metrics
from .resilience import circuits

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")

executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_JOBS, thread_name_prefix="dl")

# Interfaz web y panel de control (se monta al final del archivo).
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
async def _cleanup_loop() -> None:
    """Borra archivos vencidos periódicamente."""
    while True:
        try:
            await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
            removed = await asyncio.to_thread(store.purge_expired)
            if removed:
                log.info("Limpiados %s archivos vencidos", removed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error en el recolector de archivos")


async def _canary_loop() -> None:
    """Prueba los canarios cada X minutos para detectar roturas temprano."""
    # Espera inicial: dejar que el servicio arranque antes de gastar red.
    await asyncio.sleep(25)
    while True:
        try:
            results = await asyncio.to_thread(canaries.run_all)
            if results:
                passing = sum(1 for r in results if r.ok)
                log.info("Canarios: %s/%s OK", passing, len(results))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error ejecutando canarios")

        await asyncio.sleep(config.CANARY_INTERVAL_MINUTES * 60)


async def _update_loop() -> None:
    """Mantiene yt-dlp al día: actualizar, validar, revertir si falla.

    Este bucle es lo que evita que el servicio muera solo. Los sitios cambian,
    yt-dlp publica arreglos en días, y este bucle los instala sin intervención.
    """
    if not config.AUTO_UPDATE:
        log.info("Actualización automática desactivada (VDL_AUTO_UPDATE=false)")
        return

    # Margen para que el arranque y los primeros canarios terminen.
    await asyncio.sleep(90)

    while True:
        try:
            report = await asyncio.to_thread(updater.run_update_cycle)
            action = report.get("action")

            if action == "actualizado_y_validado":
                log.info(
                    "yt-dlp actualizado y validado: %s -> %s",
                    report.get("version_before"), report.get("version_after"),
                )
                if config.RESTART_AFTER_UPDATE:
                    # El proceso en curso sigue usando el módulo viejo en memoria.
                    log.warning("Solicitando reinicio para cargar la versión nueva")
                    await asyncio.sleep(3)
                    os._exit(0)
            elif action == "revertido":
                log.warning(
                    "Actualización revertida a %s (la versión nueva no pasó los canarios)",
                    report.get("rollback_to"),
                )
            elif action == "ya_actualizado":
                log.debug("yt-dlp ya está en la última versión (%s)", report.get("version_before"))
            elif action == "error_instalacion":
                log.error("La instalación de la actualización falló: %s", report.get("reason"))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error en el ciclo de actualización")

        await asyncio.sleep(config.UPDATE_INTERVAL_HOURS * 3600)


async def _watchdog_loop() -> None:
    """Avisa cuando el estado del sistema CAMBIA, no en cada fallo.

    Un aviso por cada error de usuario sería ruido inservible. Lo que importa
    es la transición: de sano a degradado, de degradado a caído, y sobre todo
    el aviso de recuperación, que es el que confirma que el reinicio funcionó.
    """
    await asyncio.sleep(45)  # margen para que arranque y corran los canarios
    previous: str | None = None

    while True:
        try:
            state = await asyncio.to_thread(system_status.evaluate)
            current = state["status"]

            if previous is None:
                previous = current
                log.info("Vigilante de estado activo: %s", current)
            elif current != previous:
                if current == system_status.STATUS_OK:
                    send_alert(
                        "Sistema recuperado",
                        f"El servicio volvió a la normalidad (venía de «{previous}»).",
                        level="info",
                        details={"success_rate": state["success_rate"]},
                        dedupe_key="watchdog:recuperado",
                    )
                else:
                    send_alert(
                        f"Sistema {current}",
                        " · ".join(state["reasons"])[:400] or "Sin detalle disponible.",
                        level=(
                            "error"
                            if current == system_status.STATUS_DOWN
                            else "warning"
                        ),
                        details={
                            "reasons": state["reasons"],
                            "restart_advised": state["restart_advised"],
                        },
                        dedupe_key=f"watchdog:{current}",
                    )

                log.warning("Cambio de estado: %s -> %s", previous, current)
                previous = current

        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Error en el vigilante de estado")

        await asyncio.sleep(120)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_status()
    log.info("FFmpeg: %s", ffmpeg["path"] or "NO DISPONIBLE")
    if not ffmpeg["available"]:
        log.warning("MP3 y alta calidad no funcionarán hasta instalar FFmpeg. %s", ffmpeg["hint"])

    log.info("yt-dlp instalado: %s (canal %s)", updater.installed_version(), config.UPDATE_CHANNEL)

    tasks = [asyncio.create_task(_cleanup_loop())]
    if config.CANARY_ENABLED:
        tasks.append(asyncio.create_task(_canary_loop()))
    tasks.append(asyncio.create_task(_watchdog_loop()))
    tasks.append(asyncio.create_task(_update_loop()))

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Video & MP3 Downloader API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en producción: restringir al dominio del frontend
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Rate limiting (en memoria; usar Redis si hay más de una instancia)
# ---------------------------------------------------------------------------
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    ip = _client_ip(request)
    now = time.time()
    window = config.RATE_LIMIT_WINDOW_SECONDS
    limit = config.RATE_LIMIT_REQUESTS

    with _hits_lock:
        bucket = _hits[ip]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry = int(window - (now - bucket[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Demasiadas solicitudes. Reintenta en {retry}s.",
                headers={"Retry-After": str(retry)},
            )
        bucket.append(now)


# ---------------------------------------------------------------------------
# Validación de URLs
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise HTTPException(400, "El enlace debe empezar por http:// o https://")

    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise HTTPException(400, "El enlace no es válido.")

    # Bloquea SSRF hacia la red interna.
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        raise HTTPException(400, "Ese dominio no está permitido.")

    if config.ALLOWED_DOMAINS and not any(
        host == d or host.endswith("." + d) for d in config.ALLOWED_DOMAINS
    ):
        raise HTTPException(400, f"Dominio no soportado: {host}")

    return url


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class ParseRequest(BaseModel):
    url: str = Field(..., description="Enlace del video")


class DownloadRequest(BaseModel):
    url: str
    kind: str = Field("mp4", pattern="^(mp4|mp3)$")
    format_id: str | None = None


class PrivateSourceRequest(BaseModel):
    html: str = Field(..., min_length=200)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def require_admin(token: str | None) -> None:
    """Protege los endpoints de administración."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(
            404,
            "Los endpoints de administración están deshabilitados. "
            "Define VDL_ADMIN_TOKEN para activarlos.",
        )
    if token != config.ADMIN_TOKEN:
        raise HTTPException(401, "Token de administración inválido.")


@app.get("/api/health")
def health() -> dict:
    ffmpeg = ffmpeg_status()
    circuits_open = circuits.open_count()
    platform_states = metrics.platform_states()

    degraded = [name for name, state in platform_states.items() if state in ("degradado", "caido")]

    if not ffmpeg["available"] or circuits_open:
        overall = "degradado"
    elif degraded:
        overall = "degradado"
    else:
        overall = "ok"

    return {
        "status": overall,
        "version": "0.1.0",
        "engine": {
            "name": "yt-dlp",
            "version": updater.installed_version(),
            "channel": config.UPDATE_CHANNEL,
            "auto_update": config.AUTO_UPDATE,
            "next_check_hours": config.UPDATE_INTERVAL_HOURS,
        },
        "ffmpeg": ffmpeg,
        "queue": store.jobs_waiting_for_upload(),
        "file_ttl_minutes": config.FILE_TTL_MINUTES,
        "allowed_domains": config.ALLOWED_DOMAINS or "todas (sin restricción)",
        "cookies_configured": bool(config.COOKIES_FILE or config.COOKIES_DIR),
        "cookies_rotation": len(list(Path(config.COOKIES_DIR).glob("*.txt")))
        if config.COOKIES_DIR and Path(config.COOKIES_DIR).is_dir()
        else 0,
        "circuits_open": circuits_open,
        "platforms_degraded": degraded,
        "proxy": {
            "general": bool(config.PROXY),
            "por_plataforma": sorted(config.PROXY_MAP.keys()),
            "aviso": (
                None
                if (config.PROXY or config.PROXY_MAP)
                else "Sin proxy configurado. En un servidor, los sitios grandes "
                     "bloquearán la IP en pocos días."
            ),
        },
    }


@app.get("/api/metrics")
def api_metrics() -> dict:
    """Tasa de éxito, salud por plataforma, circuitos y canarios.

    Si esta tasa baja, algo se rompió. Es tu señal más temprana.
    """
    return {
        **metrics.snapshot(),
        "circuits": circuits.all(),
        "canaries": canaries.status(),
    }


@app.get("/api/canary")
def api_canary() -> dict:
    """Estado de los canarios y su última ejecución."""
    return canaries.status()


@app.post("/api/admin/update")
def api_admin_update(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Revisa y aplica actualizaciones del motor ahora mismo."""
    require_admin(token)
    check = updater.check()
    if not check["update_available"]:
        return {"action": "ya_actualizado", **check}
    return updater.run_update_cycle()


@app.get("/api/admin/update/check")
def api_admin_update_check(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Solo consulta si hay versión nueva, sin instalar nada."""
    require_admin(token)
    return updater.check()


@app.post("/api/admin/canaries/reload")
def api_admin_reload_canaries(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Recarga canaries.json sin reiniciar el servicio."""
    require_admin(token)
    canaries.reload()
    return canaries.status()


# ---------------------------------------------------------------------------
# Monitorización y restablecimiento
# ---------------------------------------------------------------------------
@app.get("/api/widget")
def api_widget() -> dict:
    """Payload compacto para el widget de Android y para atajos móviles.

    Todo viene ya formateado (texto corto + color) porque un widget de Android
    no ejecuta lógica propia: solo pinta lo que recibe.
    """
    return system_status.widget_payload()


@app.get("/api/monitor")
def api_monitor() -> dict:
    """Estado completo para el panel de control."""
    return system_status.full_status()


@app.post("/api/admin/circuits/reset")
def api_admin_reset_circuits(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Cierra todos los circuitos abiertos.

    Útil tras actualizar el motor: no tiene sentido esperar el enfriamiento
    de un sitio si ya sabemos que la causa se corrigió.
    """
    require_admin(token)
    affected = circuits.reset_all()
    log.info("Circuitos reiniciados: %s", affected or "ninguno estaba abierto")
    return {
        "status": "circuitos_reiniciados",
        "afectados": affected,
        "nota": (
            f"Se cerraron {len(affected)} circuitos."
            if affected
            else "No había ningún circuito abierto."
        ),
    }


@app.post("/api/admin/canaries/run")
def api_admin_run_canaries(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Ejecuta los canarios ahora, sin esperar al ciclo periódico."""
    require_admin(token)
    results = canaries.run_all(quiet=True)
    passing = sum(1 for r in results if r.ok)
    return {
        "status": "canarios_ejecutados",
        "pasando": passing,
        "total": len(results),
        "resultados": [r.to_public() for r in results],
    }


@app.post("/api/admin/storage/purge")
def api_admin_purge(
    keep_active: bool = True,
    token: str | None = Header(None, alias="X-Admin-Token"),
) -> dict:
    """Libera espacio borrando archivos temporales.

    Por defecto respeta las descargas en curso: borrar un archivo a medio
    escribir dejaría el trabajo en un estado roto.
    """
    require_admin(token)
    result = store.force_purge(keep_active=keep_active)
    log.info("Purga manual: %s", result)
    return {"status": "almacenamiento_liberado", **result}


@app.post("/api/admin/alerts/clear")
def api_admin_clear_alerts(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Vacía el historial de alertas (no borra nada del sistema)."""
    require_admin(token)
    clear_history()
    return {"status": "historial_limpiado"}


@app.post("/api/admin/restart")
def api_admin_restart(token: str | None = Header(None, alias="X-Admin-Token")) -> dict:
    """Sale del proceso para que el supervisor lo levante con la versión nueva."""
    require_admin(token)

    state = system_status.evaluate()
    before = {
        "status": state["status"],
        "circuits_open": state["circuits_open"],
        "success_rate": state["success_rate"],
    }

    def _exit_soon() -> None:
        time.sleep(1.5)
        log.warning("Reinicio solicitado por el administrador")
        os._exit(0)

    threading.Thread(target=_exit_soon, daemon=True).start()
    return {
        "status": "reiniciando",
        "estado_previo": before,
        "motivos": state["restart_reasons"],
        "nota": (
            "El servicio necesita un supervisor (systemd Restart=always o "
            "Docker restart: unless-stopped) para volver a arrancar. "
            "Sin supervisor, el proceso no volverá."
        ),
    }


@app.get("/monitor", include_in_schema=False)
def monitor_page() -> FileResponse:
    """Panel de control (versión web del widget)."""
    page = STATIC_DIR / "monitor.html"
    if not page.is_file():
        raise HTTPException(404, "El panel de control no está disponible.")
    return FileResponse(page, media_type="text/html")


@app.get("/app.apk", include_in_schema=False)
def download_apk() -> FileResponse:
    """Sirve el APK del widget.

    Permite instalarlo abriendo esta dirección en el navegador del móvil, sin
    cables ni servicios de transferencia. La carpeta `apk/` vive en la raíz del
    proyecto, fuera de `backend/`, así que puede no existir en instalaciones
    desplegadas sin ella.
    """
    apk_dir = STATIC_DIR.parent.parent / "apk"
    candidates = sorted(apk_dir.glob("*.apk")) if apk_dir.is_dir() else []

    if not candidates:
        raise HTTPException(
            404,
            "Esta instalación no incluye el APK. Descárgalo del repositorio: "
            "https://github.com/leonnnc/Ldownloader/tree/main/apk",
        )

    # Prefiere la versión release: la de depuración lleva otro identificador y
    # no es la que conviene instalar.
    release = [p for p in candidates if "debug" not in p.name]
    chosen = (release or candidates)[0]

    return FileResponse(
        chosen,
        media_type="application/vnd.android.package-archive",
        filename=chosen.name,
    )


@app.post("/api/parse")
async def api_parse(payload: ParseRequest, request: Request) -> dict:
    rate_limit(request)
    url = validate_url(payload.url)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(downloader.parse, url),
            timeout=config.PARSE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "La extracción tardó demasiado. Intenta de nuevo.")
    except EngineError as exc:
        raise HTTPException(422, str(exc))

    if not result["video_formats"] and not result["audio_formats"]:
        raise HTTPException(422, "No se encontraron formatos descargables en ese enlace.")

    return result


@app.post("/api/download", status_code=202)
async def api_download(payload: DownloadRequest, request: Request) -> dict:
    rate_limit(request)
    url = validate_url(payload.url)

    if payload.kind == "mp3" and not ffmpeg_status()["available"]:
        raise HTTPException(
            503,
            "Falta FFmpeg en el servidor: no se puede convertir a MP3.",
        )

    job = store.create(url=url, kind=payload.kind, format_id=payload.format_id)
    executor.submit(downloader.run_job, job)
    log.info("Job %s encolado (%s) -> %s", job.id, payload.kind, url)
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado o ya expirado.")
    return job.to_public()


@app.get("/api/file/{job_id}")
def api_file(job_id: str) -> FileResponse:
    job = store.get(job_id)
    if not job or not job.filepath:
        raise HTTPException(404, "Archivo no disponible.")

    path = Path(job.filepath)
    if not path.is_file():
        raise HTTPException(410, "El archivo expiró y fue eliminado.")

    # Nombre final presentable: Título [id].mp4
    stem = (job.title or "video").strip()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", stem)[:120].strip() or "video"
    filename = f"{stem}.{path.suffix.lstrip('.')}"

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/facebook/private")
async def api_private_source(payload: PrivateSourceRequest, request: Request) -> dict:
    rate_limit(request)
    try:
        return await asyncio.to_thread(downloader.extract_from_source, payload.html)
    except EngineError as exc:
        raise HTTPException(422, str(exc))


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


# ---------------------------------------------------------------------------
# Frontend estático (montado al final para no tapar /api)
# ---------------------------------------------------------------------------
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
