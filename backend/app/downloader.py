"""El motor: envoltorio de yt-dlp.

Dos operaciones, exactamente el flujo parse -> download del diseño:

    parse(url)        -> yt-dlp con skip_download=True (solo pregunta qué hay)
    run_job(job)      -> yt-dlp descarga + FFmpeg postprocesa

Nada de esto descarga al recibir el link. Primero se resuelve.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import yt_dlp

from . import config
from .jobs import Job, store
from .media import find_ffmpeg
from .metrics import metrics
from .resilience import CircuitOpenError, with_retry

log = logging.getLogger("downloader")

# Formatos que tiene sentido ofrecer al usuario.
VIDEO_EXTS = {"mp4", "webm", "mkv", "mov"}
AUDIO_EXTS = {"m4a", "mp3", "opus", "webm", "ogg", "aac"}

# Errores que NO se reintentan: son permanentes, insistir solo hace esperar
# al usuario para nada. Reintentar un video borrado nunca lo va a resucitar.
PERMANENT_ERROR_MARKERS = (
    "unsupported url",
    "video unavailable",
    "this video is unavailable",
    "no video formats found",
    "not available in your country",
    "removed by the uploader",
    "does not exist",
    "is not a valid url",
    "unable to extract",
)

# Rotación de cookies: reparte las peticiones entre varias sesiones.
_cookie_lock = threading.Lock()
_cookie_index = 0


def _next_cookiefile() -> Optional[str]:
    """Devuelve el siguiente cookies*.txt disponible, rotando.

    Con varias sesiones, un bloqueo puntual de una cuenta no tumba el servicio.
    """
    global _cookie_index

    if config.COOKIES_DIR:
        directory = Path(config.COOKIES_DIR)
        if directory.is_dir():
            files = sorted(directory.glob("*.txt"))
            if files:
                with _cookie_lock:
                    chosen = files[_cookie_index % len(files)]
                    _cookie_index += 1
                return str(chosen)

    if config.COOKIES_FILE and Path(config.COOKIES_FILE).is_file():
        return config.COOKIES_FILE

    return None


def platform_of(url: str) -> str:
    """Nombre de plataforma para métricas y circuitos."""
    host = (urlparse(url).hostname or "").lower().replace("www.", "")
    known = {
        "youtube.com": "youtube", "youtu.be": "youtube", "m.youtube.com": "youtube",
        "facebook.com": "facebook", "fb.watch": "facebook", "fb.com": "facebook",
        "instagram.com": "instagram",
        "tiktok.com": "tiktok",
        "twitter.com": "twitter", "x.com": "twitter",
        "vimeo.com": "vimeo",
        "dailymotion.com": "dailymotion",
        "twitch.tv": "twitch",
        "reddit.com": "reddit",
    }
    for domain, name in known.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host.split(".")[0] if host else "desconocido"


class EngineError(Exception):
    """Error legible para el usuario (no un traceback de yt-dlp).

    Conserva el mensaje crudo en `raw` para poder clasificar el fallo sin
    perder la versión traducida que sí ve el usuario.
    """

    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw or message


class _QuietLogger:
    """Silencia la salida de yt-dlp y la redirige a nuestro logger."""

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            return
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.debug(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


def _proxy_for(platform: str | None) -> Optional[str]:
    """Proxy a usar: primero el específico de la plataforma, luego el general."""
    if platform and platform in config.PROXY_MAP:
        return config.PROXY_MAP[platform]
    return config.PROXY or None


def _common_opts(platform: str | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": _QuietLogger(),
        "socket_timeout": 30,
        "retries": 3,
        "extractor_retries": 3,
        # Navegador "de verdad": evita bloqueos por User-Agent.
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        },
    }

    proxy = _proxy_for(platform)
    if proxy:
        opts["proxy"] = proxy

    cookiefile = _next_cookiefile()
    if cookiefile:
        opts["cookiefile"] = cookiefile

    # Rotar clientes de reproducción es una de las defensas más eficaces:
    # cuando el sitio bloquea uno, otro suele seguir funcionando.
    if config.PLAYER_CLIENTS:
        opts["extractor_args"] = {"youtube": {"player_client": config.PLAYER_CLIENTS}}

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg

    return opts


def _is_permanent_error(exc: Exception) -> bool:
    """True si el error es definitivo y no tiene sentido reintentar."""
    message = f"{exc} {getattr(exc, 'raw', '')}".lower()
    return any(marker in message for marker in PERMANENT_ERROR_MARKERS)


def _human_error(exc: Exception) -> str:
    """Traduce los errores más comunes de yt-dlp a algo entendible."""
    raw = str(exc)
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)  # quita códigos ANSI
    raw = re.sub(r"^ERROR:\s*", "", raw).strip()
    low = raw.lower()

    # El orden importa: las causas más específicas van primero, porque
    # "not available in your country" contiene "not available" y debe
    # clasificarse como geobloqueo, no como video eliminado.
    mappings = [
        (("unsupported url", "no suitable extractor"),
         "Ese enlace no está soportado o no es un video."),
        (("not available in your country", "geo restricted", "geo-restricted",
          "blocked in your country", "not available from your location"),
         "El video está bloqueado en tu región."),
        (("private video", "video is private", "login required", "must log in",
          "you must be logged in", "requires authentication", "not granted"),
         "El contenido es privado: pega el código fuente de la página o configura cookies."),
        (("sign in to confirm", "confirm you're not a bot", "confirm you are not a bot"),
         "La plataforma pide verificación. Configura VDL_COOKIES_FILE."),
        (("timed out", "timeout"),
         "El servidor de origen no respondió a tiempo. Intenta de nuevo."),
        (("this video is unavailable", "video unavailable", "no longer available",
          "not available", "removed by", "has been removed", "does not exist",
          "unavailable"),
         "El video no existe, fue eliminado o no está disponible."),
        (("ffmpeg", "postprocessing"),
         "Falta FFmpeg para convertir el archivo."),
        (("unsupported file size", "file is larger"),
         "El archivo supera el tamaño máximo permitido."),
        (("unable to extract", "failed to parse"),
         "No se pudo leer el video. El sitio pudo haber cambiado: "
         "se corregirá con la próxima actualización automática del motor."),
    ]
    for needles, friendly in mappings:
        if any(n in low for n in needles):
            return friendly

    return raw[:300] if raw else "Error desconocido al procesar el enlace."


# ---------------------------------------------------------------------------
# Paso 2 del diseño: resolver el link
# ---------------------------------------------------------------------------
def parse(url: str, skip_retry: bool = False) -> dict:
    """Extrae metadatos y formatos SIN descargar nada.

    `skip_retry=True` lo usan los canarios: quieren saber si el motor
    funciona ahora mismo, no después de tres reintentos con espera.
    """
    platform = platform_of(url)
    opts = _common_opts(platform) | {"skip_download": True}

    def attempt() -> dict:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001 - se traduce a mensaje amable
            raise EngineError(_human_error(exc), raw=str(exc)) from exc

    try:
        if skip_retry:
            info = attempt()
        else:
            info = with_retry(
                attempt,
                url=url,
                operation=f"parse:{platform}",
                should_retry=lambda exc: not _is_permanent_error(exc),
            )
    except CircuitOpenError as exc:
        # El dominio está en cuarentena: fallar rápido y sin gastar recursos.
        metrics.record(platform, False, str(exc))
        metrics.inc("parse.fail")
        metrics.inc("circuit.blocked")
        raise EngineError(str(exc)) from exc
    except EngineError as exc:
        metrics.record(platform, False, str(exc))
        metrics.inc("parse.fail")
        raise
    except Exception as exc:  # noqa: BLE001
        metrics.record(platform, False, str(exc))
        metrics.inc("parse.fail")
        raise EngineError(_human_error(exc)) from exc

    metrics.record(platform, True)
    metrics.inc("parse.ok")

    # Si el enlace resuelve a una lista, nos quedamos con el primer video.
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise EngineError("El enlace no contiene ningún video.")
        info = entries[0]

    video_formats: List[dict] = []
    audio_formats: List[dict] = []

    for f in info.get("formats") or []:
        ext = f.get("ext")
        if not ext:
            continue
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        has_video = vcodec != "none"
        has_audio = acodec != "none"

        entry = {
            "format_id": f.get("format_id"),
            "ext": ext,
            "height": f.get("height"),
            "width": f.get("width"),
            "fps": f.get("fps"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "abr": f.get("abr"),
            "vcodec": vcodec,
            "acodec": acodec,
            "has_audio": has_audio,
            "protocol": f.get("protocol"),
        }

        if has_video and ext in VIDEO_EXTS:
            # Etiqueta legible: "1080p60 · MP4"
            height = f.get("height")
            label = f"{height}p" if height else (f.get("format_note") or ext.upper())
            if f.get("fps") and height and f["fps"] >= 50:
                label += f"{int(f['fps'])}"
            entry["label"] = f"{label} · {ext.upper()}"
            entry["muted"] = not has_audio  # sin pista de audio -> necesita merge
            video_formats.append(entry)

        elif has_audio and not has_video and ext in AUDIO_EXTS:
            abr = f.get("abr")
            entry["label"] = f"{int(abr)} kbps · {ext.upper()}" if abr else ext.upper()
            audio_formats.append(entry)

    # Orden: mejor calidad primero, y prefiere lo que ya trae audio (no requiere merge).
    video_formats.sort(
        key=lambda f: (
            f.get("height") or 0,
            not f.get("muted", False),
            1 if f.get("ext") == "mp4" else 0,
            f.get("filesize") or 0,
        ),
        reverse=True,
    )
    audio_formats.sort(key=lambda f: f.get("abr") or 0, reverse=True)

    # Deduplicar por resolución dejando la mejor variante de cada una.
    seen: set = set()
    unique_video = []
    for f in video_formats:
        key = (f.get("height"), f.get("muted"), f.get("ext"))
        if key in seen:
            continue
        seen.add(key)
        unique_video.append(f)

    return {
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail") or info.get("thumbnails", [{}])[-1].get("url"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "webpage_url": info.get("webpage_url") or url,
        "is_live": bool(info.get("is_live")),
        "video_formats": unique_video[:12],
        "audio_formats": audio_formats[:6],
    }


# ---------------------------------------------------------------------------
# Pasos 3 y 4 del diseño: descargar + convertir
# ---------------------------------------------------------------------------
def _format_strategies(job: Job) -> List[str]:
    """Cadena de estrategias, de la preferida a la más tolerante.

    Si la primera falla (formato retirado, pista que ya no existe), se prueba
    la siguiente en vez de devolver un error. Muchos fallos que ve el usuario
    son en realidad "esa calidad concreta ya no está", no "el sitio no funciona".
    """
    if job.kind == "mp3":
        return ["bestaudio/best", "ba/b", "b"]

    strategies: List[str] = []
    if job.format_id:
        strategies.append(f"{job.format_id}+bestaudio/{job.format_id}")
    strategies.append("bv*+ba/b")       # lo mejor disponible, con unión
    strategies.append("b[ext=mp4]/b")   # progresivo: no necesita FFmpeg
    return strategies


def _build_opts(job: Job, selector: str, workdir: Path, progress_hook) -> dict:
    opts = _common_opts(platform_of(job.url)) | {
        "outtmpl": str(workdir / "%(title).120B.%(ext)s"),
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [
            lambda d: store.update(job.id, progress=99.0)
            if d.get("status") == "started"
            else None
        ],
        "windowsfilenames": True,
        "overwrites": True,
        "format": selector,
    }

    if config.MAX_FILESIZE_MB:
        opts["max_filesize"] = config.MAX_FILESIZE_MB * 1024 * 1024

    if job.kind == "mp3":
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ]
    else:
        opts["merge_output_format"] = "mp4"

    return opts


def _run_attempt(job: Job, selector: str, workdir: Path, progress_hook) -> dict:
    with yt_dlp.YoutubeDL(_build_opts(job, selector, workdir, progress_hook)) as ydl:
        info = ydl.extract_info(job.url, download=True)

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else {}
    return info


def _locate_output(workdir: Path, kind: str) -> Optional[Path]:
    """Encuentra el archivo final (la extensión cambia tras el posprocesado)."""
    wanted = "mp3" if kind == "mp3" else "mp4"
    files = sorted(
        (p for p in workdir.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not files:
        return None
    return next((p for p in files if p.suffix.lower() == f".{wanted}"), files[0])


def _clean_failed_outputs(workdir: Path) -> None:
    """Borra restos de un intento fallido para no confundir al siguiente."""
    for path in workdir.iterdir():
        if not path.is_file():
            continue
        # Se conservan los .part activos; el resto se descarta.
        if path.suffix == ".part":
            continue
        try:
            path.unlink()
        except OSError:
            pass


def run_job(job: Job) -> None:
    """Ejecuta la descarga real. Se llama desde el pool de hilos."""
    store.update(job.id, status="processing")
    workdir = store.workdir(job.id)
    platform = platform_of(job.url)

    def progress_hook(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        downloaded = d.get("downloaded_bytes") or 0
        if total:
            store.update(job.id, progress=min(downloaded / total * 100, 99.0))

    attempts: List[str] = []
    last_error: Exception | None = None

    for index, selector in enumerate(_format_strategies(job), start=1):
        if index > 1:
            log.info("Job %s: probando estrategia alternativa %s", job.id, selector)
            store.update(job.id, progress=0.0)
            _clean_failed_outputs(workdir)

        try:
            info = with_retry(
                lambda sel=selector: _run_attempt(job, sel, workdir, progress_hook),
                url=job.url,
                operation=f"download:{platform}",
                should_retry=lambda exc: not _is_permanent_error(exc),
                on_retry=lambda n, exc: metrics.inc("download.retry"),
            )
        except CircuitOpenError as exc:
            last_error = exc
            attempts.append(f"{selector}: circuito abierto")
            metrics.inc("circuit.blocked")
            break  # el dominio está en cuarentena: las demás estrategias también fallarán
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempts.append(f"{selector}: {str(exc)[:160]}")
            log.warning("Job %s falló con '%s': %s", job.id, selector, str(exc)[:200])
            continue

        final = _locate_output(workdir, job.kind)
        if final is None:
            last_error = EngineError("La descarga terminó pero no se generó ningún archivo.")
            attempts.append(f"{selector}: sin archivo de salida")
            continue

        metrics.record(platform, True)
        metrics.inc("download.ok")
        store.update(
            job.id,
            status="done",
            progress=100.0,
            title=info.get("title"),
            filename=final.name,
            filepath=str(final),
            filesize=final.stat().st_size,
            finished_at=time.time(),
        )
        log.info("Job %s completado (%s): %s", job.id, selector, final.name)
        return

    # Todas las estrategias fallaron.
    metrics.record(platform, False, str(last_error))
    metrics.inc("download.fail")

    if isinstance(last_error, CircuitOpenError):
        message = str(last_error)
    elif last_error is None:
        message = "No se pudo descargar el video."
    else:
        message = _human_error(last_error)

    store.update(
        job.id,
        status="error",
        error=message,
        finished_at=time.time(),
    )
    log.error("Job %s falló tras %s estrategias: %s", job.id, len(attempts), attempts)


# ---------------------------------------------------------------------------
# Extractor del "código fuente" (patrón de video privado de Facebook)
# ---------------------------------------------------------------------------
_SOURCE_PATTERNS = [
    r'"playable_url_quality_hd"\s*:\s*"([^"]+)"',
    r'"playable_url"\s*:\s*"([^"]+)"',
    r'"browser_native_hd_url"\s*:\s*"([^"]+)"',
    r'"browser_native_sd_url"\s*:\s*"([^"]+)"',
    r'"(?:hd|sd)_src"\s*:\s*"([^"]+)"',
    r'"(?:hd_src_no_ratelimit|sd_src_no_ratelimit)"\s*:\s*"([^"]+)"',
]


def extract_from_source(html: str) -> dict:
    """Extrae URLs de video directas desde el HTML pegado por el usuario.

    Es el mismo principio que usa FDownloader: si el usuario ya está
    autenticado en su navegador, el HTML contiene la URL firmada del video,
    así que el servidor no necesita manejar cookies de sesión.
    """
    if len(html) < 200:
        raise EngineError("El contenido pegado es demasiado corto. Copia el código fuente completo de la página.")

    found: List[dict] = []
    seen: set = set()

    for pattern in _SOURCE_PATTERNS:
        for match in re.findall(pattern, html):
            url = _unescape(match)
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)

            height = None
            # La URL de Facebook no siempre declara la resolución; se infiere del patrón.
            if "hd" in pattern:
                height = "HD"
            elif "sd" in pattern:
                height = "SD"

            found.append(
                {
                    "url": url,
                    "quality": height,
                    "is_hd": "hd" in pattern.lower(),
                }
            )

    if not found:
        raise EngineError(
            "No se encontró ningún enlace de video en el código fuente. "
            "Asegúrate de copiar el HTML de la página del video ya cargado."
        )

    # HD primero.
    found.sort(key=lambda item: item["is_hd"], reverse=True)

    durations = re.findall(r'"video_duration"\s*:\s*(\d+)', html)
    title = None
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:200]

    return {
        "urls": found,
        "title": title,
        "duration": int(durations[0]) if durations else None,
        "count": len(found),
    }


def _unescape(value: str) -> str:
    """Facebook escapa las URLs como \\/ y \\u0025: hay que revertirlo."""
    return (
        value.replace("\\/", "/")
        .replace("\\u0025", "%")
        .replace("\\u0026", "&")
        .replace("\\u003D", "=")
    )
