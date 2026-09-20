"""Prueba del monitor: endpoint del widget, panel y acciones de restablecimiento.

Necesita el servidor corriendo CON un token de administración:

    VDL_ADMIN_TOKEN=token-de-prueba  (el mismo que se pase aquí)

Uso:  python smoke_monitor.py [token]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

BASE = "http://127.0.0.1:8000"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else "token-de-prueba"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'OK  ' if ok else 'FALLA'}] {name}" + (f"  — {detail}" if detail else ""))


def call(path: str, payload: dict | None = None, token: str | None = None,
         method: str | None = None, raw: bool = False):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {}
    if data:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Admin-Token"] = token

    req = urllib.request.Request(
        BASE + path, data=data, headers=headers,
        method=method or ("POST" if data is not None or token else "GET"),
    )
    try:
        with OPENER.open(req, timeout=180) as resp:
            body = resp.read()
            return body.decode("utf-8", "replace") if raw else json.loads(body.decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if raw:
            return f"[HTTP {exc.code}] {body[:200]}"
        try:
            return {"_http_error": exc.code, **json.loads(body)}
        except Exception:
            return {"_http_error": exc.code, "detail": body[:300]}


# ---------------------------------------------------------------------------
def test_widget() -> None:
    print("\n=== A. Endpoint del widget ===")

    started = time.perf_counter()
    widget = call("/api/widget")
    elapsed = time.perf_counter() - started

    if widget.get("_http_error"):
        check("el widget responde", False, str(widget))
        return

    # Un endpoint de monitorización se consulta cada pocos segundos: debe ser
    # barato. Un widget que tarda en responder no sirve como monitor.
    check("el widget responde rápido", elapsed < 2.0, f"{elapsed:.2f}s")

    check("el widget devuelve un estado", widget.get("status") in ("ok", "degradado", "caido"),
          f"status={widget.get('status')} · {widget.get('status_label')}")
    check("el widget trae color para pintar",
          isinstance(widget.get("color"), str) and widget["color"].startswith("#"),
          widget.get("color"))
    check("el widget formatea todo como texto corto",
          isinstance(widget.get("success_rate"), str) and isinstance(widget.get("engine"), str),
          f"tasa={widget.get('success_rate')} · motor={widget.get('engine')}")
    check("el widget expone las acciones",
          widget.get("actions", {}).get("restart") == "/api/admin/restart",
          f"{len(widget.get('actions', {}))} acciones")
    check("el widget indica si conviene reiniciar",
          "restart_advised" in widget,
          f"recomendado={widget.get('restart_advised')}")


def test_panel() -> None:
    print("\n=== B. Panel de control ===")

    page = call("/monitor", raw=True)
    check("la página del panel se sirve",
          "<!DOCTYPE html" in page and "Monitor del sistema" in page,
          f"{len(page)} bytes")

    for asset, marker in [
        ("/monitor.css", "--ok"),
        ("/monitor.js", "runAction"),
        ("/manifest.webmanifest", "Monitor del Descargador"),
        ("/icon.svg", "<svg"),
        ("/sw.js", "addEventListener"),
    ]:
        body = call(asset, raw=True)
        check(f"recurso {asset} disponible", marker in body, f"{len(body)} bytes")

    full = call("/api/monitor")
    check("el estado completo trae métricas y alertas",
          "metrics" in full and "alerts" in full and "circuits" in full,
          f"{len(full.get('alerts', []))} alertas")
    check("el panel informa cuántas muestras respaldan la tasa",
          "samples" in full and "success_rate_trusted" in full,
          f"muestras={full.get('samples')}, fiable={full.get('success_rate_trusted')}")


def test_security() -> None:
    print("\n=== C. Seguridad de las acciones ===")

    no_token = call("/api/admin/circuits/reset", method="POST")
    check("sin token no se puede actuar",
          no_token.get("_http_error") == 401, f"HTTP {no_token.get('_http_error')}")

    bad = call("/api/admin/circuits/reset", token="token-equivocado")
    check("con token incorrecto no se puede actuar",
          bad.get("_http_error") == 401, f"HTTP {bad.get('_http_error')}")

    widget = call("/api/widget")
    check("el widget público no expone el token",
          TOKEN not in json.dumps(widget),
          "el token no aparece en la respuesta")


def test_actions() -> None:
    print("\n=== D. Acciones de restablecimiento ===")

    circuits = call("/api/admin/circuits/reset", token=TOKEN)
    check("reiniciar circuitos", circuits.get("status") == "circuitos_reiniciados",
          circuits.get("nota", ""))

    canaries = call("/api/admin/canaries/run", token=TOKEN)
    check("ejecutar canarios bajo demanda",
          canaries.get("status") == "canarios_ejecutados",
          f"{canaries.get('pasando')}/{canaries.get('total')} pasan")

    purge = call("/api/admin/storage/purge", token=TOKEN)
    check("liberar espacio",
          purge.get("status") == "almacenamiento_liberado",
          f"{purge.get('freed_mb')} MB liberados, {purge.get('skipped_active')} activos respetados")

    clear = call("/api/admin/alerts/clear", token=TOKEN)
    check("limpiar el historial de alertas",
          clear.get("status") == "historial_limpiado")

    # El reinicio NO se prueba aquí: mataría el servidor de prueba.
    restart_probe = call("/api/admin/restart", token="token-equivocado")
    check("el reinicio exige token válido (no se ejecuta la prueba real)",
          restart_probe.get("_http_error") == 401, f"HTTP {restart_probe.get('_http_error')}")


def test_reflects_outage() -> None:
    print("\n=== E. El widget refleja una caída real ===")

    before = call("/api/widget")
    print(f"  estado inicial: {before.get('status')} · muestras previas={call('/api/monitor').get('samples')}")

    # Se provocan fallos reales hasta superar el mínimo de muestras.
    for i in range(5):
        call("/api/parse", {"url": "https://www.youtube.com/watch?v=00000000000"})

    started = time.perf_counter()
    after = call("/api/widget")
    full = call("/api/monitor")
    elapsed = time.perf_counter() - started

    check("el estado sigue respondiendo rápido tras los fallos",
          elapsed < 3.0, f"{elapsed:.2f}s")

    check("tras los fallos, el estado empeora",
          after.get("status") in ("degradado", "caido"),
          f"{before.get('status')} -> {after.get('status')}")
    check("el widget muestra un problema concreto",
          bool(after.get("problem")),
          f"{after.get('problem')} ({after.get('problem_ago')})")
    check("el widget recomienda reiniciar",
          after.get("restart_advised") is True,
          after.get("restart_reason") or "sin motivo")
    check("el panel explica los motivos",
          len(full.get("reasons", [])) > 0,
          " · ".join(full.get("reasons", [])[:3]))

    # Y tras el restablecimiento manual, vuelve a la normalidad.
    call("/api/admin/circuits/reset", token=TOKEN)
    call("/api/admin/alerts/clear", token=TOKEN)
    check("el restablecimiento de circuitos se aplica",
          call("/api/widget").get("circuits") == 0,
          "circuitos cerrados")


def main() -> int:
    test_widget()
    test_panel()
    test_security()
    test_actions()
    test_reflects_outage()

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
