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
│   │   └── status.py      Estado del sistema (alimenta widget y panel)
│   ├── static/
│   │   ├── index.html …   Interfaz del descargador
│   │   └── monitor.html … Panel de control (móvil, instalable)
│   ├── canaries.json      Enlaces de prueba por plataforma
│   └── requirements.txt
├── deploy/                Unidades systemd + plantilla de configuración
├── android-widget/        App Android (Kotlin) — fuentes del widget
├── apk/                   APKs compilados y firmados, listos para instalar
├── Dockerfile
├── docker-compose.yml
├── RESILIENCIA.md         Cómo evitar que el servicio se rompa
├── ANDROID-WIDGET.md      Widget, panel y monitor para Android
├── smoke_test.py          Prueba de humo de la API
├── smoke_resilience.py    Prueba de la capa de resiliencia
├── smoke_monitor.py       Prueba del widget, panel y restablecimiento
├── start.ps1 / start.sh
└── storage/               Archivos temporales (se borran solos)
```

---

## Monitor y restablecimiento desde el móvil

`GET /monitor` es un panel de control pensado para el móvil: estado general, salud
por plataforma, últimos problemas y **botones de restablecimiento**. Se instala en la
pantalla de inicio de Android como acceso directo o como PWA.

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

Un **vigilante** revisa el estado cada 2 minutos y avisa solo cuando **cambia**
(de sano a degradado, a caído, y también al recuperarse), en lugar de avisar en cada
error de usuario.

### Widget nativo para Android

APK compilado y firmado, listo para instalar:

| Archivo | Tamaño |
|---|---|
| [`apk/monitor-descargador-1.0.apk`](apk/monitor-descargador-1.0.apk) | 1,78 MB — release firmada |
| [`apk/monitor-descargador-1.0-debug.apk`](apk/monitor-descargador-1.0-debug.apk) | 2,33 MB — depuración |

Muestra el estado en la pantalla de inicio (punto verde/ámbar/rojo), el último
problema, la tasa de éxito, los circuitos abiertos y la versión del motor, con botones
para **actualizar**, **cerrar circuitos** y **reiniciar el servicio**.

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
9. **Página y agente DMCA.** Descargar contenido con derechos de autor puede infringir
   la ley y los términos de servicio de las plataformas. Opera en zona gris legal:
   consulta asesoría antes de lanzarlo como negocio.

Detalle y razonamiento de cada punto: [RESILIENCIA.md](RESILIENCIA.md)

---

## Licencia y uso

Proyecto de uso personal y educativo. Úsalo solo con contenido que tengas derecho a
descargar.
