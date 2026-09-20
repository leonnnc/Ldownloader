"""Localización de FFmpeg.

yt-dlp necesita FFmpeg para dos cosas:
  1. Convertir audio a MP3  (FFmpegExtractAudio)
  2. Unir pistas separadas de video + audio (formatos DASH)

Orden de búsqueda:
  1. VDL_FFMPEG_LOCATION (ruta a binario o carpeta)
  2. PATH del sistema
  3. Rutas habituales de instalación en Windows / macOS / Linux
  4. Paquete `imageio-ffmpeg` (binario embebido, sin instalación manual)
"""

from __future__ import annotations

import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import config

_WINDOWS_CANDIDATES = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
]

_WINGET_GLOB = "FFmpeg/FFmpeg*/**/bin"

_POSIX_CANDIDATES = [
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",  # macOS Apple Silicon
]


def _usable(path: Optional[str]) -> Optional[str]:
    """Devuelve la ruta si apunta a un ejecutable real."""
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        return str(p)
    if p.is_dir():
        exe = p / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if exe.is_file():
            return str(exe)
    return None


def _from_winget() -> Optional[str]:
    """Busca una instalación de FFmpeg hecha con winget en Windows."""
    if sys.platform != "win32":
        return None
    local = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if not local.is_dir():
        return None
    for candidate in local.glob(f"{_WINGET_GLOB}/ffmpeg.exe"):
        return str(candidate)
    return None


def _from_imageio() -> Optional[str]:
    """Fallback: binario embebido que trae el paquete imageio-ffmpeg."""
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


@lru_cache(maxsize=1)
def find_ffmpeg() -> Optional[str]:
    """Ruta a un FFmpeg utilizable, o None si no hay ninguno."""
    # 1. Variable de entorno explícita
    explicit = _usable(config.FFMPEG_LOCATION)
    if explicit:
        return explicit

    # 2. PATH
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    # 3. Rutas típicas
    for candidate in (
        _WINDOWS_CANDIDATES + _POSIX_CANDIDATES if sys.platform == "win32" else _POSIX_CANDIDATES
    ):
        found = _usable(candidate)
        if found:
            return found

    winget = _from_winget()
    if winget:
        return winget

    # 4. Binario embebido
    return _from_imageio()


def ffmpeg_status() -> dict:
    """Resumen para el endpoint de salud y para la UI."""
    path = find_ffmpeg()
    return {
        "available": bool(path),
        "path": path,
        "hint": (
            None
            if path
            else "Instala FFmpeg para habilitar MP3 y videos en alta calidad: "
            "winget install Gyan.FFmpeg  ·  o  pip install imageio-ffmpeg"
        ),
    }
