"""Enlace de conexión del APK, y constancia de si la app ha llamado.

El problema que resuelve
------------------------
El widget de Android necesita dos datos para funcionar: la dirección del
servidor y el token de administración. Escribirlos a mano en el móvil es
incómodo, y además el enlace se rompe solo: la IP del PC la reparte el DHCP y
cambia cuando reinicias el router, y entonces el widget deja de conectar sin
explicar por qué.

Aquí se centralizan tres cosas:

* **Los enlaces candidatos** — qué direcciones puede usar el móvil para llegar
  a este servidor. Se deduce de la propia petición (así en producción sale el
  dominio real, con HTTPS) y, cuando se consulta desde la propia máquina, de
  las direcciones de la red local.
* **El enlace de conexión** — un único texto que lleva la dirección y el token
  juntos, para no tener que copiarlos por separado.
* **La presencia de la app** — la última vez que el APK habló con el servidor.
  Sin esto no se puede distinguir «la app nunca se configuró» de «la app se
  configuró y luego la IP se movió», y son dos problemas distintos que se
  arreglan de forma distinta.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Optional
from urllib.parse import urlencode

from fastapi import Request

from . import config

# Cabecera que envía el APK en cada petición. Es lo que permite saber que el
# que pregunta es el widget y no un navegador cualquiera.
CLIENT_HEADER = "x-vdl-client"

# Esquema del enlace de conexión. Se registra en el manifiesto de la app, así
# que abrirlo desde el móvil abre el widget con los datos ya rellenos.
PAIRING_SCHEME = "vdl"
PAIRING_HOST = "pair"

LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


# ---------------------------------------------------------------------------
# Direcciones de la red local
# ---------------------------------------------------------------------------
_ADDRESS_CACHE: dict = {"value": [], "at": 0.0}
ADDRESS_CACHE_SECONDS = 30


def _primary_ip() -> Optional[str]:
    """IP con la que el sistema sale a la red.

    Se abre un socket UDP y se pregunta la dirección local: no se envía ni un
    byte, pero el sistema elige la interfaz correcta (Wi-Fi, Ethernet…). Es la
    forma fiable de saber cuál de las direcciones de la máquina es la que ve el
    resto de la red.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _local_ips() -> list[str]:
    """Todas las IPv4 utilizables de esta máquina, la principal primero."""
    found: list[str] = []

    primary = _primary_ip()
    if primary:
        found.append(primary)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass

    # Fuera bucles, fuera enlaces automáticos (169.254.x.x: no hay red).
    return [
        ip
        for ip in found
        if ip not in LOOPBACK and not ip.startswith("127.") and not ip.startswith("169.254.")
    ]


def lan_addresses() -> list[str]:
    now = time.time()
    if now - _ADDRESS_CACHE["at"] < ADDRESS_CACHE_SECONDS and _ADDRESS_CACHE["value"]:
        return list(_ADDRESS_CACHE["value"])

    value = _local_ips()
    _ADDRESS_CACHE.update(value=value, at=now)
    return list(value)


def _listening_on(ip: str, port: int) -> bool:
    """¿Este proceso acepta conexiones en esa dirección?

    Comprueba el caso que rompe el widget con más frecuencia: el servidor
    escuchando solo en 127.0.0.1. El móvil entonces no puede alcanzarlo por
    ninguna dirección de la red, y el aviso del widget no lo explica.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.6)
    try:
        return sock.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Presencia de la app
# ---------------------------------------------------------------------------
_presence_lock = threading.Lock()
_presence: dict = {"at": None, "ip": None, "client": None}


def note_app_seen(client: Optional[str], ip: Optional[str]) -> None:
    """Registra que el APK acaba de hablar con el servidor."""
    if not client:
        return
    with _presence_lock:
        _presence.update(at=time.time(), ip=ip, client=client.strip()[:80])


def app_presence() -> dict:
    with _presence_lock:
        at = _presence["at"]
        record = dict(_presence)

    if not at:
        return {
            "seen": False,
            "at": None,
            "ago": None,
            "ip": None,
            "client": None,
            "version": None,
        }

    return {
        "seen": True,
        "at": at,
        "ago": _human_ago(at),
        "ip": record.get("ip"),
        "client": record.get("client"),
        # "android-widget/1.1" -> "1.1"
        "version": (record.get("client") or "").split("/")[-1] or None,
    }


def _human_ago(timestamp: float) -> str:
    delta = max(0, int(time.time() - timestamp))
    if delta < 45:
        return "ahora"
    if delta < 3600:
        return f"hace {delta // 60}m"
    if delta < 86400:
        return f"hace {delta // 3600}h"
    return f"hace {delta // 86400}d"


# ---------------------------------------------------------------------------
# Construcción del payload
# ---------------------------------------------------------------------------
def _mask(token: str) -> str:
    """Huella del token: sirve para saber cuál está puesto sin enseñarlo."""
    if len(token) <= 8:
        return "•" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def connection_urls(request: Request) -> tuple[list[str], str]:
    """Direcciones por las que el móvil puede llegar. Devuelve (urls, modo).

    Dos escenarios, y no conviene mezclarlos:

    * **Público** — se llama al monitor a través de un host real (el dominio de
      producción). Esa es la dirección buena y la única que se ofrece: en
      Docker las direcciones internas serían la IP del contenedor, que no le
      sirve a nadie.
    * **Local** — se llama desde la propia máquina (127.0.0.1), así que no hay
      host útil en la petición. Se ofrecen las direcciones de la red local.
    """
    host = request.url.hostname or ""
    base = str(request.base_url).rstrip("/")
    is_loopback = host in LOOPBACK or host.startswith("127.")

    port = request.url.port or (443 if request.url.scheme == "https" else 80)

    if base and not is_loopback:
        return [base], "publico"

    urls = [f"http://{ip}:{port}" for ip in lan_addresses()]
    return urls, "local"


_REACHABILITY_CACHE: dict = {"key": None, "at": 0.0, "value": None}
REACHABILITY_CACHE_SECONDS = 20


def _reachability(urls: list[str], mode: str) -> dict:
    """Avisos útiles sobre por qué el móvil podría no llegar.

    Comprobar la escucha implica abrir sockets con tiempo de espera, así que el
    resultado se guarda unos segundos: es un diagnóstico que cambia muy rara
    vez, y el monitor lo pide cada 15 s.
    """
    key = (tuple(urls), mode)
    now = time.time()
    if (
        _REACHABILITY_CACHE["value"] is not None
        and _REACHABILITY_CACHE["key"] == key
        and now - _REACHABILITY_CACHE["at"] < REACHABILITY_CACHE_SECONDS
    ):
        return _REACHABILITY_CACHE["value"]

    value = _reachability_uncached(urls, mode)
    _REACHABILITY_CACHE.update(key=key, at=now, value=value)
    return value


def _reachability_uncached(urls: list[str], mode: str) -> dict:
    port = config.PORT
    if mode == "publico":
        return {"listening": True, "warning": None}

    addresses = []
    for url in urls:
        # "http://192.168.100.8:8000" -> ("192.168.100.8", 8000)
        rest = url.split("://", 1)[-1]
        host, _, raw_port = rest.partition(":")
        addresses.append((host, int(raw_port or port)))

    if not addresses:
        return {
            "listening": False,
            "warning": (
                "No se detectó ninguna dirección de red local. Comprueba que el "
                "equipo esté conectado al Wi-Fi o al cable."
            ),
        }

    listening = any(_listening_on(ip, p) for ip, p in addresses)
    if listening:
        return {
            "listening": True,
            "warning": (
                "Si el móvil sigue sin conectar, comprueba que esté en la misma "
                "red Wi-Fi y que el puerto esté abierto en el Firewall de Windows "
                "(abrir-firewall.ps1, como administrador)."
            ),
        }

    return {
        "listening": False,
        "warning": (
            f"El servidor está escuchando solo en 127.0.0.1, así que el móvil no "
            f"puede alcanzarlo por ninguna dirección de la red. Arráncalo con "
            f"--host 0.0.0.0 (o define VDL_HOST=0.0.0.0)."
        ),
    }


def payload(request: Request, authorized: bool) -> dict:
    """Todo lo que el monitor necesita para la tarjeta de conexión.

    El token y el enlace completo solo salen si quien pregunta ha demostrado
    conocer el token (`authorized`). Sin esa comprobación, cualquiera que
    alcanzase el monitor se llevaría el token de administración, que permite
    reiniciar el servicio y borrar archivos.
    """
    urls, mode = connection_urls(request)
    preferred = urls[0] if urls else None
    port = request.url.port or config.PORT

    admin_token = config.ADMIN_TOKEN or ""
    token_configured = bool(admin_token)

    pairing_link = None
    if authorized and preferred and token_configured:
        pairing_link = "{}://{}?{}".format(
            PAIRING_SCHEME,
            PAIRING_HOST,
            urlencode({"url": preferred, "token": admin_token}),
        )

    presence = app_presence()

    reachability = _reachability(urls, mode)
    warning = reachability["warning"]
    listening = reachability["listening"]

    if not token_configured:
        warning = (
            "Los endpoints de administración están desactivados: define "
            "VDL_ADMIN_TOKEN y reinicia. Sin token, el widget funciona en modo "
            "solo lectura (no podrá reiniciar ni cerrar circuitos)."
        )

    return {
        "mode": mode,
        "urls": urls,
        "preferred_url": preferred,
        "port": port,
        "listening": listening,
        "token_configured": token_configured,
        "token_masked": _mask(admin_token) if token_configured else None,
        # Solo con el token demostrado:
        "token": admin_token if authorized else None,
        "pairing_link": pairing_link,
        "apk_link": f"{preferred}/app.apk" if preferred else None,
        "dashboard_link": f"{preferred}/monitor" if preferred else None,
        "app": presence,
        "warning": warning,
        "generated_at": int(time.time()),
    }
