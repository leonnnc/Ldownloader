# Descargador de Video y MP3 — MVP

Servicio de descargas funcional: recibe un enlace, lo resuelve, y entrega el archivo
en **MP4** o **MP3** según lo que elija el usuario.

**Motor:** `yt-dlp` (más de 1.700 sitios soportados) + `FFmpeg` para conversión.
**Backend:** FastAPI · **Frontend:** HTML/CSS/JS sin dependencias · **Deploy:** Docker.

---

## Arranque rápido

### Windows

```powershell
cd downloader
.\start.ps1
```

### macOS / Linux

```bash
cd downloader
./start.sh
```

Abre **http://127.0.0.1:8000** en el navegador.

El script crea el entorno virtual, instala dependencias y arranca el servidor con
recarga automática. La primera ejecución tarda un poco (descarga de paquetes).

### Docker

```bash
cd downloader
docker compose up --build
```

Disponible en **http://localhost:8000**. Los archivos quedan en el volumen `downloads`.

---

## FFmpeg

`FFmpeg` es necesario para **convertir a MP3** y para **unir video + audio** en calidades
altas. El backend lo busca en este orden:

1. Variable de entorno `VDL_FFMPEG_LOCATION`
2. `PATH` del sistema
3. Rutas típicas (`C:\ffmpeg\bin`, `/usr/local/bin`, Homebrew…)
4. Instalaciones de winget
5. Paquete `imageio-ffmpeg` (**incluido en `requirements.txt`**, funciona sin instalar nada)

Si el navegador muestra el aviso "FFmpeg no detectado", instala el binario real:

```powershell
winget install Gyan.FFmpeg      # Windows
brew install ffmpeg             # macOS
sudo apt install ffmpeg         # Debian/Ubuntu
```

En Docker ya viene instalado.

---

## Estructura

```
downloader/
├── backend/
│   ├── app/
│   │   ├── main.py        API FastAPI, rate limiting, validación, admin
│   │   ├── downloader.py  El motor: parse() y run_job() sobre yt-dlp
│   │   ├── jobs.py        Trabajos en memoria + limpieza por TTL
│   │   ├── media.py       Detección de FFmpeg
│   │   ├── config.py      Configuración por variables de entorno
│   │   ├── updater.py     Actualización del motor + validación + rollback
│   │   ├── canary.py      Canarios: detección temprana de roturas
│   │   ├── resilience.py  Reintentos con backoff + circuit breaker
│   │   ├── metrics.py     Tasa de éxito y salud por plataforma
│   │   ├── alerts.py      Avisos por webhook + historial
│   │   ├── history.py     Historial de descargas: quién pidió qué enlace
│   │   └── status.py      Estado del sistema (alimenta widget y panel)
│   ├── static/
│   │   ├── index.html …   Interfaz del descargador
│   │   ├── monitor.html … Panel de control (móvil, instalable)
│   │   ├── terminos.html    Términos de Servicio
│   │   └── privacidad.html  Política de Privacidad
│   ├── canaries.json      Enlaces de prueba por plataforma
│   └── requirements.txt
├── deploy/                Unidades systemd + plantilla de configuración
├── android-widget/        App Android (Kotlin) — fuentes del widget
├── apk/                   APKs compilados y firmados, listos para instalar
├── Dockerfile
├── docker-compose.yml
├── RESILIENCIA.md         Cómo evitar que el servicio se rompa
├── ANDROID-WIDGET.md      Widget, panel y monitor para Android
├── DESPLIEGUE.md          Puesta en producción paso a paso
├── abrir-firewall.ps1     Abre el puerto 8000 en el Firewall de Windows
├── smoke_test.py          Prueba de humo de la API
├── smoke_resilience.py    Prueba de la capa de resiliencia
├── smoke_monitor.py       Prueba del widget, panel y restablecimiento
├── start.ps1 / start.sh
└── storage/               Archivos temporales (se borran solos)
```

---

## Monitor y restablecimiento desde el móvil

`GET /monitor` es un panel de control pensado para el móvil: estado general, salud
por plataforma, **historial de descargas**, últimos problemas y **botones de
restablecimiento**. Se instala en la pantalla de inicio de Android como acceso directo
o como PWA.

### Carrusel de la portada

Encima del titular y del formulario hay una tira de **últimas descargas**: tarjetas
con la miniatura, el título, la plataforma y la marca MP4/MP3. Si el archivo todavía
está en el almacén temporal, la tarjeta **reproduce el vídeo de verdad** (al pasar el
ratón o al tocar); cuando expira, se queda la miniatura. Al lado, tres pasos escuetos
de cómo se descarga.

Lo alimenta `GET /api/gallery`, que es **público y solo expone título, miniatura,
plataforma, formato y fecha**. Nunca la IP ni el enlace de origen: el enlace puede
llevar identificadores de quien lo pidió. El carrusel no aparece si hay menos de
`VDL_GALLERY_MIN` entradas —una fila con una sola tarjeta se ve rota— y las flechas
‹ › se ocultan solas cuando todas caben sin desplazar.

Si no quieres que la portada enseñe la actividad de nadie:
`VDL_GALLERY_ENABLED=false` y desaparece.

### Historial de descargas

La primera tarjeta del panel responde a «¿quién pidió qué enlace y cómo acabó?». Por
cada descarga anota la **IP** de quien la pidió, el **enlace** de origen, el formato y
el resultado, con el motivo del error si falló. Tiene buscador propio (filtra por IP,
título, enlace o plataforma) y cada entrada lleva el enlace tal cual, para abrirlo o
copiarlo.

Se guarda en `backend/history.jsonl` (una línea por descarga) y **sobrevive al
reinicio**, a diferencia de los registros del servidor. Es acotado: conserva las
últimas `VDL_HISTORY_MAX` entradas —300 por defecto— y va soltando las más viejas. El
archivo contiene direcciones IP y enlaces, así que está en `.gitignore`; pon
`VDL_HISTORY_ENABLED=false` si no quieres conservar rastro ninguno.

> La IP que muestra el panel es la que **declara el cliente**. Mientras no se corrija
> el punto 4 de [REVISION.md](REVISION.md), sale de `X-Forwarded-For` y es
> falsificable, así que sirve como pista, no como prueba.

### Cómo se entra al panel (acceso deliberadamente invisible)

La página pública **no muestra ningún botón, enlace ni indicador de estado** hacia el
panel: ni en la barra superior, ni en el pie. Se entra de tres maneras:

| Entrada | Cómo |
|---|---|
| Toques en el logotipo | Cinco toques seguidos sobre «Downloader», en menos de 2,5 s (ratón o dedo) |
| Atajo de teclado | `Ctrl` + `Alt` + `M` |
| Dirección directa | `http://tu-servidor/monitor` |

La combinación se cambia en `backend/static/app.js` (`ACCESS_TAPS`, `ACCESS_WINDOW_MS`).
Los botones visibles que había antes —el enlace «Monitor» y la píldora de estado
«listo»— se retiraron: el estado del sistema se consulta aquí, no en la página pública.
El único aviso que sigue apareciendo al visitante es el de FFmpeg ausente, porque afecta
de verdad a lo que puede descargar.

Además, `GET /api/widget` devuelve un payload compacto y ya formateado, pensado
específicamente para un widget de Android (texto corto + color, sin lógica), y
soporta los botones de acción del widget.

**Acciones de restablecimiento disponibles** (requieren `VDL_ADMIN_TOKEN`):

| Acción | Qué hace | Cuándo usarla |
|---|---|---|
| `restart` | Reinicia el proceso | Tras actualizar el motor, o si el estado está corrupto |
| `reset_circuits` | Cierra los circuitos abiertos | Cuando sabes que el sitio ya se recuperó |
| `run_canaries` | Prueba todos los enlaces ahora | Para confirmar si algo sigue roto |
| `purge` | Libera espacio en disco | Si el disco se llena (respeta descargas activas) |
| `alerts_clear` | Vacía el historial de alertas | Tras resolver un incidente |

El panel incluye además una tarjeta **«Conexión con la app»**: el enlace del servidor y
el token en un solo sitio, con botón de copiar y un botón que configura el widget del
teléfono sin escribir nada. Y, sobre todo, el **estado real de la conexión**: cuándo
habló la app por última vez y desde qué IP, o «sin contacto» si nunca lo hizo. Eso
distingue las dos averías que desde el widget se ven igual: «nunca se configuró» y «se
configuró y luego cambió la IP». Detalle en
[ANDROID-WIDGET.md §1.3](ANDROID-WIDGET.md#13-el-enlace-de-conexión).

Un **vigilante** revisa el estado cada 2 minutos y avisa solo cuando **cambia**
(de sano a degradado, a caído, y también al recuperarse), en lugar de avisar en cada
error de usuario.

### Widget nativo para Android

APK compilado y firmado, listo para instalar:

| Archivo | Tamaño |
|---|---|
| [`apk/monitor-descargador-1.1.apk`](apk/monitor-descargador-1.1.apk) | 1,78 MB — release firmada |
| [`apk/monitor-descargador-1.1-debug.apk`](apk/monitor-descargador-1.1-debug.apk) | 2,33 MB — depuración |

También desde el propio móvil, sin cables: `GET /app.apk` sirve la versión más alta que
haya en `apk/`.

Muestra el estado en la pantalla de inicio (punto verde/ámbar/rojo), el último
problema, la tasa de éxito, los circuitos abiertos y la versión del motor, con botones
para **actualizar**, **cerrar circuitos** y **reiniciar el servicio**.

Si la conexión se pierde, no falla en silencio: reintenta antes de darse por vencido, y
tras dos fallos seguidos el punto pasa a gris con «Sin conexión con el servidor» y
aparece un botón **Reconectar** (los botones de administración se ocultan, porque sin
servidor no pueden hacer nada).

Se refresca cada 15 minutos — el mínimo que Android respeta de verdad; un widget no
puede ser tiempo real. Detalles, fuentes y cómo recompilarlo en
[ANDROID-WIDGET.md](ANDROID-WIDGET.md).

---

## Resiliencia: que no se caiga en semanas

Un descargador no se rompe por bugs propios, sino porque **los sitios cambian**.
El servicio incluye las defensas para sobrevivir a eso sin intervención:

| Defensa | Qué hace |
|---|---|
| **Auto-actualización validada** | Instala yt-dlp nuevo, prueba con canarios y revierte si empeora |
| **Canarios** | Prueba enlaces conocidos cada 30 min y avisa antes que los usuarios |
| **Circuit breaker** | Si un sitio falla repetidamente, falla rápido en vez de colgar recursos |
| **Reintentos con backoff** | Un timeout de red se recupera solo; un video borrado falla al instante |
| **Estrategias de respaldo** | Si la calidad pedida ya no existe, entrega la mejor disponible |
| **Métricas por plataforma** | Sabes que Facebook está al 40% aunque YouTube esté al 100% |
| **Proxies** | Evita el bloqueo por IP en servidores (requiere contratarlos) |

Todo el detalle, con el razonamiento de cada decisión: **[RESILIENCIA.md](RESILIENCIA.md)**

### Lo mínimo para producción

```bash
# 1. Un supervisor que reinicie el servicio (obligatorio):
#    systemd: Restart=always     Docker: restart: unless-stopped
# 2. En /etc/downloader.env:
VDL_RESTART_AFTER_UPDATE=true     # el servicio sale y el supervisor lo levanta
VDL_ALERT_WEBHOOK=https://...     # a dónde avisar cuando algo se rompe
```

Sin supervisor, la actualización se instala en disco pero **nunca llega a usarse**.

### Endpoints de operación

| Ruta | Para qué |
|---|---|
| `GET /api/health` | Estado general, versión del motor, circuitos abiertos |
| `GET /api/widget` | Payload compacto para el widget de Android |
| `GET /api/monitor` | Estado completo para el panel de control |
| `GET /monitor` | Panel de control (interfaz) |
| `GET /terminos` | Términos de Servicio (también responde en `/legal`) |
| `GET /privacidad` | Política de Privacidad |
| `GET /api/history` | Historial de descargas: IP, enlace, formato y resultado |
| `GET /api/gallery` | Últimas descargas para el carrusel (sin IP ni enlace) |
| `GET /api/preview/{job_id}` | Reproduce el archivo en línea mientras siga en el almacén |
| `GET /api/pairing` | Enlace y token para conectar la app, y si la app está viva |
| `GET /app.apk` | Descarga del widget de Android (la versión más alta de `apk/`) |
| `GET /api/metrics` | Tasa de éxito, salud por plataforma, canarios |
| `GET /api/canary` | Último resultado de cada canario |

Todas las rutas `/api/admin/*` requieren la cabecera `X-Admin-Token`:

| Ruta | Para qué |
|---|---|
| `POST /api/admin/update` | Actualizar el motor ahora |
| `POST /api/admin/restart` | Pedir el reinicio del servicio |
| `POST /api/admin/circuits/reset` | Cerrar los circuitos abiertos |
| `POST /api/admin/canaries/run` | Ejecutar los canarios ahora |
| `POST /api/admin/canaries/reload` | Recargar `canaries.json` sin reiniciar |
| `POST /api/admin/storage/purge` | Liberar espacio en disco |
| `POST /api/admin/alerts/clear` | Vaciar el historial de alertas |
| `POST /api/admin/history/clear` | Vaciar el historial de descargas |

---

## Cómo funciona

```
Usuario pega el link
   ↓  POST /api/parse      → yt-dlp con skip_download=True (solo resuelve, no baja)
Lista de formatos + metadatos
   ↓  POST /api/download   → encola el trabajo, devuelve job_id
   ↓  GET  /api/jobs/{id}  → progreso en vivo
yt-dlp descarga → FFmpeg convierte/une
   ↓  GET  /api/file/{id}  → entrega el archivo
```

Nada se descarga en el paso de análisis: primero se pregunta *qué hay* en el enlace y
solo se baja lo que el usuario elige.

---

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado del servicio y disponibilidad de FFmpeg |
| `POST` | `/api/parse` | `{"url":"..."}` → título, miniatura, formatos |
| `POST` | `/api/download` | `{"url":"...","kind":"mp4\|mp3","format_id":null}` → `job_id` |
| `GET` | `/api/jobs/{job_id}` | Estado, progreso y enlace de descarga |
| `GET` | `/api/file/{job_id}` | Descarga el archivo |
| `POST` | `/api/facebook/private` | `{"html":"..."}` → URLs de video extraídas |

Documentación interactiva automática: **http://127.0.0.1:8000/docs**

---

## Configuración

Todas las variables son opcionales.

| Variable | Por defecto | Descripción |
|---|---|---|
| `VDL_DATA_DIR` | `backend/storage` | Carpeta de archivos temporales |
| `VDL_FILE_TTL_MINUTES` | `15` | Minutos antes de borrar cada archivo |
| `VDL_MAX_CONCURRENT_JOBS` | `2` | Descargas simultáneas |
| `VDL_MAX_FILESIZE_MB` | `0` | Tamaño máximo por archivo (0 = sin límite) |
| `VDL_RATE_LIMIT_REQUESTS` | `20` | Solicitudes por ventana y por IP |
| `VDL_RATE_LIMIT_WINDOW` | `60` | Tamaño de la ventana, en segundos |
| `VDL_ALLOWED_DOMAINS` | vacío | Lista blanca, ej. `facebook.com,tiktok.com` |
| `VDL_COOKIES_FILE` | vacío | Ruta a `cookies.txt` para contenido que pide sesión |
| `VDL_COOKIES_DIR` | vacío | Carpeta con varios cookies, que se rotan |
| `VDL_FFMPEG_LOCATION` | autodetecta | Ruta al binario o carpeta de FFmpeg |
| `VDL_AUTO_UPDATE` | `true` | Actualización automática del motor |
| `VDL_ALERT_WEBHOOK` | vacío | Webhook para alertas (Slack, Discord, n8n) |
| `VDL_PROXY` | vacío | Proxy general para las descargas |
| `VDL_PROXY_MAP` | vacío | Proxies por plataforma |
| `VDL_ADMIN_TOKEN` | vacío | Habilita `/api/admin/*` |
| `VDL_HISTORY_ENABLED` | `true` | Guarda el historial de descargas del panel (IP + enlace) |
| `VDL_HISTORY_MAX` | `300` | Cuántas descargas se conservan; las más viejas se borran solas |
| `VDL_HISTORY_FILE` | `backend/history.jsonl` | Archivo del historial (ignorado por git) |
| `VDL_GALLERY_ENABLED` | `true` | Muestra el carrusel de últimas descargas en la portada |
| `VDL_GALLERY_MAX` | `12` | Tarjetas que ofrece el carrusel |
| `VDL_GALLERY_MIN` | `2` | Por debajo de esto el carrusel no se muestra |

Lista completa de variables de resiliencia: [RESILIENCIA.md](RESILIENCIA.md#13-referencia-variables-de-resiliencia)

---

## Pruebas

Con el servidor corriendo:

```bash
# API: salud, análisis, descarga MP3 real y extracción desde HTML
.venv\Scripts\python.exe smoke_test.py                          # Windows
.venv/bin/python smoke_test.py                                  # macOS/Linux

# Resiliencia: circuit breaker, reintentos, rollback, canarios y telemetría
.venv\Scripts\python.exe smoke_resilience.py
```

`smoke_resilience.py` cubre cuatro bloques: mecanismos offline (circuito, backoff,
clasificación de errores), telemetría HTTP, validación real de canarios y la
orquestación del rollback simulada.

---

## Antes de ponerlo en producción

Este MVP es de un solo proceso y guarda el estado en memoria. Para un servicio público:

1. **Supervisor activo** (systemd `Restart=always` o Docker `restart: unless-stopped`)
   + `VDL_RESTART_AFTER_UPDATE=true`. Sin esto, las actualizaciones del motor se
   instalan pero nunca se aplican.
2. **Proxies residenciales** (`VDL_PROXY` / `VDL_PROXY_MAP`) — sin ellos, los sitios
   grandes bloquean la IP del servidor en pocos días.
3. **Alertas** (`VDL_ALERT_WEBHOOK`) para enterarte antes que los usuarios.
4. **Cola real** (Redis) en lugar del `ThreadPoolExecutor`: hoy los trabajos en curso
   se pierden al reiniciar.
5. **Caché de metadatos y de archivos** — ahorra ancho de banda y proxies.
6. **Restringir CORS** al dominio del frontend (ahora acepta todos).
7. **Restringir `VDL_ALLOWED_DOMAINS`** si el servicio es público.
8. **HTTPS + rate limiting en el proxy** (Nginx o Cloudflare).
9. **Páginas legales.** Ya existen `/terminos` (condiciones, licencia de uso, derechos de
   autor con procedimiento de retirada, precisión de los materiales, descargo y
   limitaciones) y `/privacidad` (datos personales y no personales, cookies, publicidad y
   Google AdSense). **Antes de publicar hay que sustituir los marcadores entre corchetes**
   —dominio, correo de contacto y jurisdicción— en `backend/static/terminos.html` y
   `backend/static/privacidad.html`. Y recuerda que descargar contenido con derechos de
   autor puede infringir la ley y los términos de servicio de las plataformas: esto es una
   zona gris legal, consulta asesoría antes de lanzarlo como negocio.

Detalle y razonamiento de cada punto: [RESILIENCIA.md](RESILIENCIA.md)

### Ponerlo en producción

Guía completa: **[DESPLIEGUE.md](DESPLIEGUE.md)**

Lo esencial, en una línea: **sirve un VPS o un contenedor Docker, no un hosting
compartido.** Necesita FFmpeg, procesos largos y disco temporal. Y en un servidor
necesitarás **proxies residenciales**, o los sitios grandes bloquearán la IP en días.

Con Docker y HTTPS automático:

```bash
cp deploy/produccion.env.example deploy/produccion.env   # y editarlo
DOMAIN=tu.dominio.com docker compose -f deploy/docker-compose.prod.yml up -d --build
```

---

## Licencia y uso

Proyecto de uso personal y educativo. Úsalo solo con contenido que tengas derecho a
descargar.
