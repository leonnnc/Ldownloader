"""Prueba de humo del MVP: salud, parse, descarga MP4 y conversión MP3."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# URL de prueba: video de dominio público (Big Buck Bunny, Blender Foundation).
TEST_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def call(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data is not None else "GET",
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


def wait_for_job(job_id: str, timeout: int = 240) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = call(f"/api/jobs/{job_id}")
        status = last.get("status")
        if status in ("done", "error", "expired"):
            return last
        time.sleep(2)
    return {**last, "status": "timeout"}


def main() -> int:
    print("=" * 62)
    print("1) GET /api/health")
    health = call("/api/health")
    print(json.dumps(health, indent=2)[:900])

    print("\n" + "=" * 62)
    print(f"2) POST /api/parse  ->  {TEST_URL}")
    parsed = call("/api/parse", {"url": TEST_URL})
    if "_http_error" in parsed:
        print(f"   FALLO {parsed['_http_error']}: {parsed.get('detail')}")
        return 1
    print(f"   título    : {parsed.get('title')}")
    print(f"   duración  : {parsed.get('duration')}s")
    print(f"   extractor : {parsed.get('extractor')}")
    vids = parsed.get("video_formats", [])
    auds = parsed.get("audio_formats", [])
    print(f"   video     : {len(vids)} formatos -> {[v['label'] for v in vids[:4]]}")
    print(f"   audio     : {len(auds)} formatos -> {[a['label'] for a in auds[:3]]}")

    if not vids:
        print("   Sin formatos de video: se omite la descarga.")
        return 1

    print("\n" + "=" * 62)
    print("3) POST /api/download  (mp3)")
    job = call("/api/download", {"url": TEST_URL, "kind": "mp3"})
    if "job_id" not in job:
        print(f"   FALLO: {job}")
        return 1
    print(f"   job_id    : {job['job_id']}")
    result = wait_for_job(job["job_id"])
    print(f"   estado    : {result.get('status')}  ({result.get('progress')}%)")
    if result.get("status") == "done":
        print(f"   archivo   : {result.get('filename')}  ({result.get('filesize')} bytes)")
        print(f"   enlace    : {result.get('download_url')}")
    else:
        print(f"   error     : {result.get('error')}")

    print("\n" + "=" * 62)
    print("4) POST /api/facebook/private  (HTML sintético)")
    fake_html = (
        '<html><head><title>Video de prueba</title></head><body>'
        + "x" * 250
        + r'"playable_url":"https:\/\/video.xx.fbcdn.net\/v\/t42.1790-2\/abc_n.mp4?_nc_cat=1\u0026oe=ABC"'
        + r'"playable_url_quality_hd":"https:\/\/video.xx.fbcdn.net\/v\/t42.1790-2\/abc_hd.mp4?_nc_cat=1\u0026oe=DEF"'
        + r'"video_duration":47'
        + "</body></html>"
    )
    extracted = call("/api/facebook/private", {"html": fake_html})
    print(json.dumps(extracted, indent=2)[:800])

    print("\n" + "=" * 62)
    print("OK: prueba de humo finalizada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
