"""Actualización automática del motor, con validación y rollback.

Este archivo es la respuesta a "no quiero que deje de funcionar en semanas".

El problema
-----------
yt-dlp no se rompe por tener bugs propios: se rompe porque **los sitios cambian**.
Facebook reordena su JSON, YouTube cambia cómo firma las URLs, TikTok añade una
cabecera nueva. La comunidad lo arregla en días, a veces en horas. Pero eso solo
te sirve si **instalas la versión nueva**.

La solución en tres pasos
-------------------------
1. **Actualizar** — revisar si hay versión nueva y instalarla solas.
2. **Validar** — antes de dar la versión por buena, correr los canarios contra
   sitios reales. Una versión nueva que rompe YouTube no debe quedarse.
3. **Revertir** — si la validación falla, volver a la última versión que
   funcionó. Automáticamente. Sin que nadie intervenga.

Detalle importante
------------------
Tras `pip install`, **el proceso en curso sigue usando el yt-dlp viejo** (Python
ya tiene el módulo en memoria). Por eso la validación se ejecuta en un
subproceso nuevo: es la única forma de probar de verdad el código recién
instalado. Y por eso el servicio necesita un supervisor (systemd o Docker) que
lo reinicie para empezar a usar la versión nueva.

Uso
---
    python -m app.updater --check      # ¿hay versión nueva?
    python -m app.updater --update     # actualizar + validar + revertir si falla
    python -m app.updater --validate   # solo validar la versión instalada
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from . import config

log = logging.getLogger("updater")

PACKAGE_FOR_CHANNEL = {
    "stable": "yt-dlp",
    # Los builds nocturnos se instalan con --pre sobre el mismo paquete.
    "nightly": "yt-dlp",
}

PYPI_URL = "https://pypi.org/pypi/{package}/json"


# ---------------------------------------------------------------------------
# Estado persistente
# ---------------------------------------------------------------------------
def _empty_state() -> dict:
    return {
        "validated_version": None,
        "last_check_at": None,
        "last_update_at": None,
        "history": [],
    }


def load_state() -> dict:
    path = Path(config.UPDATE_STATE_FILE)
    if not path.is_file():
        return _empty_state()
    try:
        return {**_empty_state(), **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return _empty_state()


def save_state(state: dict) -> None:
    path = Path(config.UPDATE_STATE_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Se conserva solo el historial reciente.
        state["history"] = state.get("history", [])[-20:]
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("No se pudo guardar el estado del actualizador: %s", exc)


# ---------------------------------------------------------------------------
# Versiones
# ---------------------------------------------------------------------------
# Leer la versión real implica lanzar un subproceso (arrancar Python + importar
# yt-dlp tarda segundos). El monitor llama a esto en cada petición, así que se
# cachea: la versión solo cambia cuando el actualizador instala algo.
_version_cache: dict = {"value": None, "at": 0.0}
VERSION_CACHE_SECONDS = 300


def installed_version(fresh: bool = False) -> str | None:
    """Versión realmente instalada en disco (no la que el proceso tenga cargada).

    `fresh=True` ignora la caché. Es obligatorio justo después de instalar:
    ahí lo único que importa es la versión real, no la anterior.
    """
    now = time.time()
    if (
        not fresh
        and _version_cache["value"]
        and now - _version_cache["at"] < VERSION_CACHE_SECONDS
    ):
        return _version_cache["value"]

    version: str | None = None

    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            version = result.stdout.strip().splitlines()[-1].strip() or None
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("No se pudo leer la versión instalada: %s", exc)

    if not version:
        # Respaldo: la versión cargada en memoria.
        try:
            from yt_dlp.version import __version__ as v  # type: ignore

            version = v
        except Exception:  # noqa: BLE001
            version = None

    if version:
        _version_cache.update(value=version, at=now)
    return version


def invalidate_version_cache() -> None:
    """Fuerza a releer la versión en la próxima consulta."""
    _version_cache.update(value=None, at=0.0)


def latest_version(channel: str | None = None) -> str | None:
    """Última versión publicada en PyPI. None si no se pudo consultar."""
    channel = (channel or config.UPDATE_CHANNEL).lower()
    package = PACKAGE_FOR_CHANNEL.get(channel, "yt-dlp")

    request = urllib.request.Request(
        PYPI_URL.format(package=package),
        headers={"Accept": "application/json", "User-Agent": "downloader-updater/0.1"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data.get("info", {}).get("version")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        log.warning("No se pudo consultar PyPI: %s", exc)
        return None


def _version_key(version: str) -> tuple:
    """Convierte '2026.08.19' en algo comparable."""
    parts: List[int] = []
    for chunk in version.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(candidate: str | None, current: str | None) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    return _version_key(candidate) > _version_key(current)


# ---------------------------------------------------------------------------
# Instalación
# ---------------------------------------------------------------------------
def _pip(args: List[str], timeout: int = 600) -> tuple[bool, str]:
    command = [sys.executable, "-m", "pip", *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.SubprocessError as exc:
        return False, f"error ejecutando pip: {exc}"

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, output[-1500:]
    return True, output[-800:]


def install_version(version: str | None = None, channel: str | None = None) -> tuple[bool, str]:
    """Instala la última versión (o una concreta, para revertir)."""
    channel = (channel or config.UPDATE_CHANNEL).lower()
    package = PACKAGE_FOR_CHANNEL.get(channel, "yt-dlp")

    if version:
        # Revertir a una versión concreta: se quita el --pre para no saltar a otra.
        return _pip(["install", "--no-cache-dir", f"{package}=={version}"])

    args = ["install", "--no-cache-dir", "--upgrade"]
    if channel == "nightly":
        args.append("--pre")
    args.append(package)
    return _pip(args)


# ---------------------------------------------------------------------------
# Validación en subproceso (usa el yt-dlp recién instalado)
# ---------------------------------------------------------------------------
def validate_in_subprocess(timeout: int = 300) -> dict:
    """Ejecuta los canarios en un intérprete nuevo."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.updater", "--validate"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
    except subprocess.SubprocessError as exc:
        return {"valid": False, "reason": f"no se pudo validar: {exc}", "results": []}

    stdout = result.stdout.strip()
    # Se busca la última línea que sea JSON válido.
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    return {
        "valid": False,
        "reason": f"salida de validación ilegible: {(result.stderr or stdout)[-300:]}",
        "results": [],
    }


# ---------------------------------------------------------------------------
# Reinicio tras actualizar
# ---------------------------------------------------------------------------
def restart_service() -> dict:
    """Pide el reinicio del servicio para cargar la versión recién instalada.

    Tras un `pip install`, el proceso vivo sigue ejecutando el yt-dlp viejo.
    Solo hay dos formas de empezar a usar el nuevo:

    * `VDL_RESTART_COMMAND` — una orden externa lo reinicia
      (por ejemplo `systemctl restart downloader-api`).
    * `VDL_RESTART_AFTER_UPDATE=true` — el propio proceso sale y el supervisor
      (systemd `Restart=always`, Docker `restart: unless-stopped`) lo levanta.

    Debe estar configurada una de las dos. Sin ninguna, la versión nueva queda
    instalada en disco pero sin usarse hasta el próximo reinicio manual.
    """
    if config.RESTART_COMMAND:
        try:
            result = subprocess.run(
                config.RESTART_COMMAND,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            ok = result.returncode == 0
            if not ok:
                log.warning("El comando de reinicio falló: %s", result.stderr[-300:])
            return {
                "mode": "comando",
                "ok": ok,
                "command": config.RESTART_COMMAND,
                "output": (result.stdout + result.stderr).strip()[-300:],
            }
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("No se pudo ejecutar el comando de reinicio: %s", exc)
            return {"mode": "comando", "ok": False, "error": str(exc)}

    if config.RESTART_AFTER_UPDATE:
        return {
            "mode": "propio",
            "ok": True,
            "nota": "el bucle interno solicitará el reinicio al terminar",
        }

    log.warning(
        "yt-dlp se actualizó en disco, pero el servicio sigue usando la versión "
        "anterior. Configura VDL_RESTART_COMMAND o VDL_RESTART_AFTER_UPDATE, "
        "o reinicia el servicio manualmente."
    )
    return {
        "mode": "manual",
        "ok": False,
        "nota": "reinicia el servicio para empezar a usar la versión nueva",
    }


# ---------------------------------------------------------------------------
# Ciclo completo
# ---------------------------------------------------------------------------
def check() -> dict:
    current = installed_version()
    latest = latest_version()
    state = load_state()
    state["last_check_at"] = time.time()
    save_state(state)

    return {
        "installed": current,
        "latest": latest,
        "update_available": is_newer(latest, current),
        "channel": config.UPDATE_CHANNEL,
        "last_validated": state.get("validated_version"),
        "auto_update": config.AUTO_UPDATE,
    }


def run_update_cycle(dry_run: bool = False) -> dict:
    """Actualiza, valida y revierte si hace falta. Devuelve un informe."""
    started = time.time()
    state = load_state()

    before = installed_version()
    latest = latest_version()

    report: Dict[str, Any] = {
        "channel": config.UPDATE_CHANNEL,
        "version_before": before,
        "version_latest": latest,
        "action": "ninguna",
        "validated": None,
        "rolled_back": False,
    }

    # 1. ¿Hay algo que hacer?
    if not is_newer(latest, before):
        report["action"] = "ya_actualizado"
        report["reason"] = "la versión instalada ya es la más reciente"
        state["last_check_at"] = started
        save_state(state)
        return report

    if dry_run:
        report["action"] = "simulado"
        return report

    # Versión de referencia a la que volver si algo sale mal.
    previous_good = state.get("validated_version") or before

    # 2. Instalar (antes se valida la versión actual para tener una referencia).
    baseline = None
    if config.UPDATE_ROLLBACK_ON_FAILURE:
        baseline = validate_in_subprocess()
        report["baseline"] = {
            "pass_rate": baseline.get("pass_rate"),
            "valid": baseline.get("valid"),
        }
        if not baseline.get("valid"):
            log.warning(
                "La versión actual tampoco pasa los canarios (%s). "
                "Se omite la validación comparativa para no revertir a algo peor.",
                baseline.get("reason"),
            )

    log.info("Actualizando yt-dlp: %s -> %s", before, latest)
    ok, output = install_version(channel=config.UPDATE_CHANNEL)
    report["install_ok"] = ok

    if not ok:
        report["action"] = "error_instalacion"
        report["reason"] = output
        from .alerts import send_alert

        send_alert(
            "Actualización de yt-dlp falló",
            f"No se pudo instalar {latest}. El servicio sigue con {before}.",
            level="warning",
            details={"output": output[:500]},
            dedupe_key="update:install",
        )
        return report

    # Obligatorio leer sin caché: acabamos de instalar y lo que importa es la
    # versión real en disco, no la anterior.
    after = installed_version(fresh=True)
    report["version_after"] = after

    # 3. Validar con el código nuevo.
    validation = validate_in_subprocess()
    report["validated"] = validation.get("valid")
    report["validation"] = {
        "pass_rate": validation.get("pass_rate"),
        "reason": validation.get("reason"),
        "results": validation.get("results"),
    }

    from .alerts import send_alert

    if validation.get("valid"):
        state["validated_version"] = after or latest
        state["last_update_at"] = time.time()
        state["history"].append(
            {
                "at": time.time(),
                "from": before,
                "to": after,
                "result": "validada",
                "pass_rate": validation.get("pass_rate"),
            }
        )
        save_state(state)
        report["action"] = "actualizado_y_validado"

        if baseline is not None and validation.get("pass_rate", 0) > (baseline.get("pass_rate") or 0):
            report["improvement"] = True

        send_alert(
            "yt-dlp actualizado correctamente",
            f"{before} -> {after} "
            f"(canarios: {validation.get('pass_rate')}%).",
            level="info",
            dedupe_key="update:ok",
        )

        # El proceso en curso sigue usando el módulo viejo en memoria: hay que
        # reiniciar para empezar a usar la versión nueva.
        report["restart"] = restart_service()
        return report

    # 4. La versión nueva no pasa: revertir.
    report["action"] = "revertido"
    target = previous_good or before

    if target:
        log.error(
            "La versión %s no pasó los canarios (%s). Revirtiendo a %s.",
            after, validation.get("reason"), target,
        )
        ok_rollback, rollback_output = install_version(version=target)
        invalidate_version_cache()
        report["rolled_back"] = ok_rollback
        report["rollback_to"] = target
        report["rollback_output"] = rollback_output if not ok_rollback else None

        state["history"].append(
            {
                "at": time.time(),
                "from": after,
                "to": target,
                "result": "revertida" if ok_rollback else "reversion_fallida",
                "pass_rate": validation.get("pass_rate"),
                "reason": validation.get("reason"),
            }
        )
        save_state(state)

        send_alert(
            "yt-dlp revertido automáticamente",
            (
                f"La versión {after} rompió el servicio "
                f"({validation.get('reason')}). "
                + (f"Se volvió a {target}." if ok_rollback else "FALLÓ la reversión, requiere intervención manual.")
            ),
            level="error" if not ok_rollback else "warning",
            details={"validation": validation, "previous_good": previous_good},
            dedupe_key="update:rollback",
        )
    else:
        report["action"] = "sin_reversion_posible"
        send_alert(
            "yt-dlp actualizado pero no validado",
            f"No hay versión de referencia para revertir. Motivo: {validation.get('reason')}",
            level="error",
            details={"validation": validation},
            dedupe_key="update:novalidation",
        )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: List[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if "--validate" in argv:
        # Se ejecuta desde un intérprete limpio: imprime JSON y nada más.
        from .canary import canaries

        report = canaries.validation_report()
        print(json.dumps(report, ensure_ascii=False))
        return 0

    if "--check" in argv:
        print(json.dumps(check(), indent=2, ensure_ascii=False))
        return 0

    if "--update" in argv:
        report = run_update_cycle(dry_run="--dry-run" in argv)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("action") != "error_instalacion" else 1

    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
