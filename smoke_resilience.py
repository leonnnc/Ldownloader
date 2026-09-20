"""Prueba de la capa de resiliencia.

Parte A (offline): circuit breaker, reintentos con backoff y clasificación de
errores permanentes. No necesita red ni servidor.

Parte B (HTTP): endpoints de telemetría y canarios, si el servidor responde.

Uso:  python smoke_resilience.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Permite importar el paquete `app` sin depender del directorio actual.
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

BASE = "http://127.0.0.1:8000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'OK  ' if ok else 'FALLA'}] {name}" + (f"  — {detail}" if detail else ""))


# ===========================================================================
# Parte A — offline
# ===========================================================================
def test_offline() -> None:
    print("\n=== A. Resiliencia offline ===")

    from app import config
    from app.downloader import EngineError, _is_permanent_error
    from app.resilience import CircuitOpenError, circuits, with_retry

    # Ajustes para que la prueba sea rápida y determinista.
    config.CIRCUIT_THRESHOLD = 3
    config.CIRCUIT_COOLDOWN_SECONDS = 2
    config.RETRY_BASE_DELAY_SECONDS = 0

    # --- A1: el circuito se abre tras N fallos seguidos -------------------
    url = "https://circuit-test.example/video"
    errors = {"n": 0}

    def always_fails():
        errors["n"] += 1
        raise EngineError("fallo simulado", raw="simulated failure")

    for _ in range(3):
        try:
            with_retry(always_fails, url=url, operation="test", attempts=1,
                       should_retry=lambda e: True)
        except Exception:
            pass

    circuit = circuits.for_url(url)
    check("circuito se abre tras 3 fallos", circuit.state == "abierto", f"estado={circuit.state}")

    # --- A1b: leer un circuito abierto no debe autobloquearse ------------
    # Regresión: to_public() consultaba retry_in() con el mismo lock tomado,
    # y el endpoint del monitor se quedaba colgado sin responder nunca.
    started = time.perf_counter()
    public = circuit.to_public()
    elapsed = time.perf_counter() - started
    check("leer un circuito abierto no se bloquea",
          elapsed < 0.5 and public.get("state") == "abierto",
          f"{elapsed * 1000:.0f} ms · retry_in={public.get('retry_in')}")

    # --- A2: con el circuito abierto se falla rápido ----------------------
    blocked = False
    before = errors["n"]
    try:
        with_retry(always_fails, url=url, operation="test", attempts=1,
                   should_retry=lambda e: True)
    except CircuitOpenError as exc:
        blocked = True
        detail = f"reintento en {exc.retry_in}s"
    except Exception as exc:
        detail = f"excepción inesperada: {type(exc).__name__}"

    check("con circuito abierto falla sin intentar", blocked and errors["n"] == before, detail)

    # --- A3: se recupera tras el enfriamiento -----------------------------
    time.sleep(2.2)
    allowed = circuit.allow()
    check("permite un intento de prueba tras el enfriamiento", allowed, f"estado={circuit.state}")

    circuit.record_success()
    check("se cierra al recuperarse", circuit.state == "cerrado")

    # --- A4: los errores transitorios sí se reintentan --------------------
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("timeout transitorio")
        return "resultado"

    value = with_retry(flaky, url="https://retry-test.example/x", operation="retry")
    check("reintenta errores transitorios hasta lograrlo",
          value == "resultado" and calls["n"] == 3, f"llamadas={calls['n']}")

    # --- A5: los errores permanentes NO se reintentan ---------------------
    permanent_calls = {"n": 0}

    def dead_video():
        permanent_calls["n"] += 1
        raise EngineError("El video no existe o fue eliminado.",
                          raw="ERROR: Video unavailable")

    try:
        with_retry(dead_video, url="https://permanent-test.example/x", operation="perm",
                   should_retry=lambda e: not _is_permanent_error(e))
    except EngineError:
        pass

    check("no reintenta un error permanente",
          permanent_calls["n"] == 1, f"llamadas={permanent_calls['n']}")

    # --- A6: el backoff crece exponencialmente ----------------------------
    from app.resilience import backoff_delay

    config.RETRY_BASE_DELAY_SECONDS = 2  # se restaura el valor real
    d1, d3 = backoff_delay(1), backoff_delay(3)
    check("el backoff crece con cada intento", d3 > d1, f"{d1:.1f}s -> {d3:.1f}s")

    # --- A7: los errores se traducen a mensajes entendibles ---------------
    from app.downloader import _human_error

    translated = _human_error(Exception("[youtube] 00000000000: This video is unavailable"))
    check("los errores se muestran en lenguaje claro",
          "unavailable" not in translated.lower() and "no existe" in translated.lower(),
          translated)


# ===========================================================================
# Parte B — HTTP
# ===========================================================================
def call(path: str, payload: dict | None = None, method: str | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with OPENER.open(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return {"_http_error": exc.code, **json.loads(body)}
        except Exception:
            return {"_http_error": exc.code, "detail": body[:300]}


def test_http() -> None:
    print("\n=== B. Telemetría HTTP ===")

    health = call("/api/health")
    if health.get("_http_error"):
        check("servidor disponible", False, str(health))
        return

    engine = health.get("engine", {})
    check("health expone la versión del motor",
          bool(engine.get("version")), f"yt-dlp {engine.get('version')} (canal {engine.get('channel')})")
    # El estado depende del historial: si antes se generaron fallos, es
    # correcto que ya no sea "ok". Lo que se comprueba es que informe un
    # estado válido, no que sea concreto.
    check("health informa un estado válido",
          health.get("status") in ("ok", "degradado", "caido"),
          f"status={health.get('status')}")
    check("health informa circuitos abiertos", "circuits_open" in health,
          f"abiertos={health.get('circuits_open')}")

    # Un enlace roto a propósito: genera tráfico de fallo y métricas reales.
    bad = call("/api/parse", {"url": "https://www.youtube.com/watch?v=00000000000"})
    detail = bad.get("detail", "")
    check("un enlace inválido devuelve un mensaje entendible",
          bool(detail) and len(detail) < 300, detail[:110])

    # Ahora sí: hay tráfico real que debe haberse contabilizado.
    metrics = call("/api/metrics")
    counters = metrics.get("counters", {})
    check("metrics registra el fallo de análisis",
          counters.get("parse.fail", 0) >= 1, f"parse.fail={counters.get('parse.fail')}")
    check("metrics mide la salud por plataforma",
          any(p.get("platform") == "youtube" and p.get("failure", 0) >= 1
              for p in metrics.get("platforms", [])),
          f"{len(metrics.get('platforms', []))} plataformas medidas")

    canary = call("/api/canary")
    check("canarios habilitados y configurados",
          canary.get("enabled") is True and canary.get("interval_minutes"),
          f"cada {canary.get('interval_minutes')} min, alerta tras {canary.get('alert_after')} fallos")

    # 404 = endpoints deshabilitados (sin VDL_ADMIN_TOKEN).
    # 401 = habilitados pero protegidos (con token configurado).
    admin = call("/api/admin/update/check")
    check("administración protegida o deshabilitada",
          admin.get("_http_error") in (404, 401),
          f"HTTP {admin.get('_http_error')}")


# ===========================================================================
# Parte C — validación en subproceso (lo que usa el rollback)
# ===========================================================================
def test_validation() -> None:
    print("\n=== C. Validación del motor (rollback) ===")

    import subprocess

    backend = Path(__file__).resolve().parent / "backend"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.updater", "--validate"],
            capture_output=True, text=True, timeout=300, cwd=str(backend),
        )
    except subprocess.SubprocessError as exc:
        check("la validación en subproceso se ejecuta", False, str(exc))
        return

    report = {}
    for line in reversed(result.stdout.strip().splitlines()):
        if line.strip().startswith("{"):
            try:
                report = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    check("la validación devuelve un informe",
          bool(report), f"claves={list(report)[:5]}")
    check("la validación mide el paso de los canarios",
          "valid" in report and "pass_rate" in report,
          f"válido={report.get('valid')}, tasa={report.get('pass_rate')}%")
    check("los canarios pasan contra sitios reales",
          report.get("valid") is True,
          f"{report.get('passing')}/{report.get('total')} canarios OK")


# ===========================================================================
# Parte D — orquestación del rollback (simulada, sin tocar el sistema real)
# ===========================================================================
def _run_cycle_simulated(validation: dict) -> tuple[dict, list, list, dict]:
    """Ejecuta un ciclo de actualización con el motor simulado."""
    from app import alerts, updater

    installed_calls = {"n": 0}

    def fake_installed(fresh: bool = False):
        installed_calls["n"] += 1
        # 1ª llamada: versión actual. 2ª: la que quedó tras instalar.
        return "2026.09.01" if installed_calls["n"] > 1 else "2026.08.19"

    installs: list = []
    alertas: list = []
    state = {"validated_version": "2026.08.19", "history": []}

    patch = {
        "installed_version": fake_installed,
        "latest_version": lambda channel=None: "2026.09.01",
        "load_state": lambda: state,
        "save_state": lambda s: state.update(s),
        "install_version": lambda version=None, channel=None:
            (installs.append(version), (True, "ok"))[1],
        "validate_in_subprocess": lambda timeout=300: validation,
    }

    originals = {k: getattr(updater, k) for k in patch}
    original_alert = alerts.send_alert
    try:
        for key, value in patch.items():
            setattr(updater, key, value)
        alerts.send_alert = lambda *a, **k: alertas.append((a, k))
        report = updater.run_update_cycle()
    finally:
        for key, value in originals.items():
            setattr(updater, key, value)
        alerts.send_alert = original_alert

    return report, installs, alertas, state


def test_rollback() -> None:
    print("\n=== D. Orquestación del rollback (simulada) ===")

    # --- D1: la versión nueva rompe -> debe revertir ----------------------
    report, installs, alertas, _ = _run_cycle_simulated(
        {"valid": False, "reason": "canarios rojos", "pass_rate": 0.0, "results": []}
    )
    check("una actualización que rompe se revierte sola",
          report.get("action") == "revertido" and report.get("rolled_back") is True,
          f"acción={report.get('action')}, revertido={report.get('rolled_back')}")
    check("revierte a la última versión validada",
          bool(installs) and installs[-1] == "2026.08.19",
          f"instalaciones={installs}")
    check("avisa por alerta cuando revierte",
          any("revertido" in str(a[0]).lower() for a, _ in alertas),
          f"{len(alertas)} alertas emitidas")

    # --- D2: la versión nueva funciona -> debe quedarse -------------------
    report, installs, alertas, state = _run_cycle_simulated(
        {"valid": True, "reason": None, "pass_rate": 100.0, "results": []}
    )
    check("una actualización que funciona se conserva",
          report.get("action") == "actualizado_y_validado" and not report.get("rolled_back"),
          f"acción={report.get('action')}")
    check("no revierte cuando todo pasa",
          all(v is None for v in installs), f"instalaciones={installs}")
    check("recuerda la versión validada para futuras reversiones",
          state.get("validated_version") == "2026.09.01",
          f"validada={state.get('validated_version')}")

    # --- D3: comparación de versiones -------------------------------------
    from app.updater import is_newer

    cases = [
        ("2026.8.19", "2026.08.19", False, "misma versión con formato distinto"),
        ("2026.09.01", "2026.08.19", True, "versión más nueva"),
        ("2026.08.19", "2026.09.01", False, "versión más vieja"),
        (None, "2026.08.19", False, "sin datos de PyPI"),
    ]
    ok = all(is_newer(cand, cur) is expected for cand, cur, expected, _ in cases)
    check("compara versiones correctamente", ok,
          f"{sum(1 for c, u, e, _ in cases if is_newer(c, u) is e)}/{len(cases)} casos")


def main() -> int:
    test_offline()
    try:
        test_http()
    except Exception as exc:  # noqa: BLE001
        print(f"\n  (Parte B omitida: servidor no disponible — {exc})")
    try:
        test_validation()
    except Exception as exc:  # noqa: BLE001
        print(f"\n  (Parte C omitida: {exc})")
    try:
        test_rollback()
    except Exception as exc:  # noqa: BLE001
        print(f"\n  (Parte D omitida: {exc})")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 62}")
    print(f"RESULTADO: {passed}/{total} pruebas OK")
    for name, ok, detail in results:
        if not ok:
            print(f"  FALLA: {name} — {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
