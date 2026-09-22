"""Historial de descargas para el panel de control.

Responde a «¿quién pidió qué enlace y cómo acabó?» sin tener que bucear en los
registros del servidor. Por cada descarga se guardan cuatro cosas: la IP del
solicitante, el enlace de origen, el formato pedido y el resultado final.

Decisiones que importan:

* **Es acotado.** Se conservan las últimas `VDL_HISTORY_MAX` entradas (300 por
  defecto). Al superar el doble, el archivo se recorta. Un historial que crece
  sin límite acaba llenando el disco, y este es un servicio que se deja solo.
* **Es JSONL.** Una instantánea completa del estado de un trabajo por línea, así
  que el archivo se puede leer a mano y una línea corrupta no invalida el resto.
* **Sobrevive al reinicio.** Al arrancar se reconstruye el estado final de cada
  trabajo quedándose con su última línea. Los registros del servidor se pierden
  al rotar; esto no.
* **Se puede apagar.** Con `VDL_HISTORY_ENABLED=false` no se guarda nada.

El historial contiene direcciones IP y enlaces, que son datos personales: el
archivo está en `.gitignore` y el endpoint que lo expone va protegido con el
token de administración en cuanto hay uno configurado.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from . import config

log = logging.getLogger("history")

# job_id -> entrada. Ordenado de más antiguo a más reciente.
_entries: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
_loaded = False


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _host(url: str) -> str:
    """Dominio del enlace, sin «www.» y sin puerto.

    Se guarda el host tal cual en lugar de agrupar por «plataforma»: agrupar por
    las dos últimas etiquetas mete la pata con dominios tipo `algo.co.uk`, que
    acaban contados como `co.uk`.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "desconocido"


def _clean(text: Optional[str], limit: int = 300) -> Optional[str]:
    """Recorta un texto para que la entrada no crezca sin control."""
    if not text:
        return None
    text = " ".join(str(text).split())
    return text[:limit]


def _safe_image_url(url: Optional[str]) -> Optional[str]:
    """Acepta solo una URL de imagen absoluta por http(s).

    La miniatura llega del cliente (es la que devolvió el análisis del enlace),
    así que se trata como dato no confiable: fuera esquemas raros como
    `javascript:` o `data:`, y longitud acotada. La portada la carga además con
    `referrerpolicy="no-referrer"`, para no mandar a un tercero desde qué
    página se está viendo la miniatura.
    """
    if not url:
        return None
    candidate = str(url).strip()
    if not candidate or len(candidate) > 600:
        return None
    if urlparse(candidate).scheme.lower() not in ("http", "https"):
        return None
    return candidate


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------
def _file() -> Path:
    return Path(config.HISTORY_FILE)


def load() -> None:
    """Carga el historial del disco una sola vez, al primer uso."""
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True

        path = _file()
        if not path.is_file():
            return

        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # una línea rota no debe tumbar el resto
                    job_id = entry.get("job_id")
                    if not job_id:
                        continue
                    # La última línea de cada trabajo gana: es su estado final.
                    _entries.pop(job_id, None)
                    _entries[job_id] = entry

            _trim_locked(force=True)
            log.info("Historial cargado: %s entradas", len(_entries))
        except OSError as exc:
            log.warning("No se pudo leer el historial (%s): %s", path, exc)


def _trim_locked(force: bool = False) -> bool:
    """Deja solo las últimas `HISTORY_MAX` entradas. Devuelve si cambió algo.

    `force=True` reescribe el archivo siempre (se usa al recortar en memoria);
    con `force=False` solo actúa si hay algo que recortar.
    """
    limit = config.HISTORY_MAX
    excess = len(_entries) - limit
    if excess <= 0 and not force:
        return False
    while len(_entries) > limit:
        _, old = _entries.popitem(last=False)
        del old
    return excess > 0


def _save_locked() -> None:
    """Escribe el historial completo. Se llama con el lock tomado.

    Se reescribe entero en lugar de añadir líneas: con un máximo de 300 entradas
    el archivo son unas decenas de KB, y así el recorte y el guardado son la
    misma operación. El reemplazo es atómico para que un corte a mitad no deje
    un archivo a medias.
    """
    path = _file()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as handle:
            for entry in _entries.values():
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("No se pudo guardar el historial (%s): %s", path, exc)


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------
def record(job_id: str, url: str, kind: str, ip: Optional[str] = None,
           format_id: Optional[str] = None, title: Optional[str] = None,
           thumbnail: Optional[str] = None) -> None:
    """Anota una descarga en cuanto se pide.

    El título y la miniatura llegan del análisis que hizo el navegador, así que
    pueden faltar; al terminar la descarga, `finish()` los reemplaza por los
    reales que devuelve el motor.
    """
    if not config.HISTORY_ENABLED:
        return

    load()
    entry = {
        "job_id": job_id,
        "at": time.time(),
        "ip": _clean(ip, 64) or "desconocida",
        "url": _clean(url, 600),
        "host": _host(url),
        "kind": kind,
        "format_id": _clean(format_id, 64),
        "status": "queued",
        "title": _clean(title, 200),
        "thumbnail": _safe_image_url(thumbnail),
        "filesize": None,
        "error": None,
        "finished_at": None,
        "elapsed": None,
    }

    with _lock:
        _entries[job_id] = entry
        _trim_locked()
        _save_locked()


def finish(job_id: str, status: str, title: Optional[str] = None,
           filesize: Optional[int] = None, error: Optional[str] = None) -> None:
    """Cierra la entrada con el resultado real del trabajo."""
    if not config.HISTORY_ENABLED:
        return

    load()
    now = time.time()

    with _lock:
        entry = _entries.get(job_id)
        if entry is None:
            return

        entry["status"] = status
        entry["finished_at"] = now
        started = entry.get("at")
        entry["elapsed"] = round(now - started, 1) if started else None
        if title is not None:
            entry["title"] = _clean(title, 200)
        if filesize is not None:
            entry["filesize"] = filesize
        if error is not None:
            entry["error"] = _clean(error, 300)

        # Un trabajo terminado se mueve al final: el orden del archivo debe
        # seguir siendo «más reciente al final» aunque se haya cerrado tarde.
        _entries.move_to_end(job_id)
        _save_locked()


def clear() -> int:
    """Vacía el historial. Devuelve cuántas entradas había."""
    load()
    with _lock:
        removed = len(_entries)
        _entries.clear()
        _save_locked()
    log.info("Historial vaciado: %s entradas", removed)
    return removed


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
def entries(limit: int = 100, query: Optional[str] = None,
            status: Optional[str] = None, host: Optional[str] = None) -> list:
    """Entradas más recientes primero, con filtros opcionales."""
    load()

    needle = (query or "").strip().lower()
    wanted_status = (status or "").strip().lower()
    wanted_host = (host or "").strip().lower()

    with _lock:
        items = list(_entries.values())

    items.reverse()  # más reciente primero

    result = []
    for entry in items:
        if wanted_status and entry.get("status") != wanted_status:
            continue
        if wanted_host and wanted_host not in (entry.get("host") or ""):
            continue
        if needle:
            haystack = " ".join(
                str(entry.get(field) or "")
                for field in ("ip", "host", "url", "title", "kind", "status", "error")
            ).lower()
            if needle not in haystack:
                continue
        result.append(entry)
        if len(result) >= max(1, limit):
            break

    return result


def gallery(limit: int = 12) -> list:
    """Muestra pública de las últimas descargas, sin datos personales.

    Devuelve **solo** lo que puede ver cualquiera: título, miniatura, plataforma,
    formato y fecha. Nunca la IP, ni el enlace original, ni el motivo de un
    error. El enlace de origen puede llevar identificadores de quien lo pidió,
    así que se queda fuera a propósito.

    Se incluyen únicamente las descargas que terminaron bien: un error no es
    nada que enseñar, y prometer un archivo que no existe sería peor.
    """
    load()

    with _lock:
        items = list(_entries.values())

    items.reverse()  # más reciente primero

    out = []
    for entry in items:
        if entry.get("status") != "done":
            continue
        if not entry.get("thumbnail") and not entry.get("title"):
            continue
        out.append({
            "job_id": entry.get("job_id"),
            "title": entry.get("title") or entry.get("host") or "Sin título",
            "thumbnail": entry.get("thumbnail"),
            "host": entry.get("host"),
            "kind": entry.get("kind"),
            "at": entry.get("at"),
        })
        if len(out) >= max(1, limit):
            break

    return out


def stats() -> dict:
    """Resumen para la cabecera del panel."""
    load()
    now = time.time()

    with _lock:
        items = list(_entries.values())

    ok = sum(1 for e in items if e.get("status") == "done")
    failed = sum(1 for e in items if e.get("status") == "error")
    pending = sum(1 for e in items if e.get("status") in ("queued", "processing"))
    last_day = sum(1 for e in items if now - (e.get("at") or 0) < 86400)

    by_host: dict = {}
    for entry in items:
        name = entry.get("host") or "desconocido"
        by_host[name] = by_host.get(name, 0) + 1

    return {
        "enabled": config.HISTORY_ENABLED,
        "total": len(items),
        "ok": ok,
        "error": failed,
        "pendientes": pending,
        "ultimas_24h": last_day,
        "max": config.HISTORY_MAX,
        "por_host": sorted(by_host.items(), key=lambda kv: -kv[1])[:8],
        "desde": min((e.get("at") or 0) for e in items) if items else None,
        "hasta": max((e.get("at") or 0) for e in items) if items else None,
    }
