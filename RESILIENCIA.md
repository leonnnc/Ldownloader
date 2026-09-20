# Resiliencia: cómo evitar que el servicio se caiga en semanas

**La pregunta:** ¿cómo hago que esto funcione mucho tiempo sin cortes?

**La respuesta honesta primero:** no existe forma de que un descargador *nunca* se
rompa. Depende de sitios que cambian cuando quieren, sin avisarte. Cualquiera que te
prometa lo contrario te está vendiendo algo.

Lo que **sí** se puede hacer —y es lo que convierte un juguete en un servicio— es
cambiar el objetivo:

| Objetivo ingenuo | Objetivo realista |
|---|---|
| Que nunca se rompa | Que se **repare solo** cuando se rompa |
| Enterarte cuando un usuario se queja | **Enterarte antes** que el usuario |
| Actualizar a mano cuando algo falla | **Actualizar, validar y revertir automáticamente** |

Un servicio que se auto-repara en 6 horas y te avisa no tiene "cortes": tiene
mantenimiento invisible. Eso es lo que se implementó aquí.

---

## 1. Por qué mueren estos servicios

Hay que entender los modos de fallo para poder cubrirlos. Son cinco, y **ninguno
tiene que ver con la calidad del código**:

### 1.1 El extractor deja de coincidir con el sitio

Facebook cambia el nombre de un campo en su JSON. YouTube altera cómo firma las URL.
TikTok añade una cabecera obligatoria. El código de extracción sigue siendo correcto,
pero ya no encaja con la realidad.

**Es el fallo número uno, con diferencia.** Y es el único que tiene cura automática:
la comunidad arregla yt-dlp en días (a veces horas), así que basta con **instalar la
versión nueva**. Sin eso, el servicio se queda congelado en una versión que ya no
funciona.

### 1.2 El sitio te bloquea por IP

Las IPs de datacenter (AWS, Hetzner, DigitalOcean) están en listas conocidas. El sitio
responde con "inicia sesión", un captcha, o simplemente deja de servir el video.

**Sin proxies residenciales, el servicio funciona en tu casa y muere en el servidor.**
Es un fallo especialmente traicionero porque no aparece en desarrollo.

### 1.3 Detección de bots

Más allá de la IP: el sitio analiza cabeceras, tokens de sesión, cookies y patrones de
tráfico. YouTube es el caso extremo: exige PO Tokens para muchas peticiones.

### 1.4 Caída silenciosa

Nadie configuró alertas. El servicio lleva tres días devolviendo errores y te enteras
porque un usuario escribe. **El daño real no es la caída: es no saber que está caído.**

### 1.5 Recursos agotados

Veinte descargas de 4K simultáneas llenan el disco o saturan la CPU. El servicio se
cae por su propio éxito.

---

## 2. Las siete capas de defensa

Cada capa cubre un modo de fallo concreto. Las seis primeras están implementadas y
probadas en este proyecto; la séptima es de infraestructura.

| # | Capa | Cubre | Estado |
|---|---|---|---|
| 1 | **Auto-actualización validada** | Extractor roto | ✅ Implementado |
| 2 | **Canarios sintéticos** | Caída silenciosa | ✅ Implementado |
| 3 | **Circuit breaker** | Recursos agotados, reintentos inútiles | ✅ Implementado |
| 4 | **Reintentos con backoff** | Fallos transitorios de red | ✅ Implementado |
| 5 | **Estrategias de respaldo** | Formato retirado / calidad inexistente | ✅ Implementado |
| 6 | **Métricas por plataforma** | No saber qué está roto | ✅ Implementado |
| 7 | **Proxies rotativos** | Bloqueo de IP y detección de bots | 📋 Infraestructura |

---

## 3. Capa 1 — Auto-actualización validada (la más importante)

Archivo: [`backend/app/updater.py`](backend/app/updater.py)

El problema tiene una trampa: si actualizas a ciegas, puedes cambiar una versión rota
por otra rota distinta. Actualizar sin validar es tan malo como no actualizar.

El ciclo implementado tiene tres pasos:

```
1. REVISAR    ¿hay versión nueva? (PyPI)
      ↓
2. VALIDAR    correr los canarios contra la versión ACTUAL  ← línea base
      ↓
3. INSTALAR   pip install --upgrade yt-dlp
      ↓
4. VALIDAR    correr los canarios de nuevo, con el código NUEVO
      ↓
   ¿pasa?  ──sí──►  actualización confirmada + reinicio
      │
      └──no──►  ROLLBACK a la última versión que funcionó + alerta
```

### Detalle crítico: la validación va en un subproceso

```python
# Tras `pip install`, el proceso vivo SIGUE usando el módulo viejo:
# Python ya lo tiene cargado en memoria. Probar en el mismo proceso
# daría un falso "todo bien" probando el código anterior.
validation = validate_in_subprocess()   # intérprete nuevo, código nuevo
```

Es el error más fácil de cometer aquí, y produce una confianza falsa: creerías haber
validado la versión nueva cuando en realidad validaste la vieja.

### Detalle crítico 2: comparar contra la línea base

Antes de actualizar se corren los canarios con la versión actual. Si **ya estaban
fallando**, no se usa el resultado como criterio de reversión: revertir a algo que
tampoco funciona no arregla nada. En ese caso el sistema actualiza y deja constancia,
en lugar de entrar en un bucle de reversiones inútiles.

### Configuración

| Variable | Por defecto | Qué hace |
|---|---|---|
| `VDL_AUTO_UPDATE` | `true` | Activa el ciclo |
| `VDL_UPDATE_INTERVAL_HOURS` | `12` | Cada cuánto revisa |
| `VDL_UPDATE_CHANNEL` | `stable` | `stable` o `nightly` |
| `VDL_UPDATE_ROLLBACK` | `true` | Revertir si la validación falla |
| `VDL_RESTART_AFTER_UPDATE` | `false` | Salir para que el supervisor reinicie |
| `VDL_RESTART_COMMAND` | vacío | Orden externa de reinicio |

**`stable` vs `nightly`:** `nightly` recibe arreglos antes, pero también recibe los
fallos antes. Para producción, `stable`. Si un día tu plataforma principal se rompe y
`stable` tarda en arreglarlo, cambiar a `nightly` temporalmente es una respuesta de
emergencia razonable.

---

## 4. Capa 2 — Canarios sintéticos

Archivo: [`backend/app/canary.py`](backend/app/canary.py) · Config: [`backend/canaries.json`](backend/canaries.json)

Un canario es un enlace conocido que se prueba cada X minutos. Si falla, algo se rompió.

> En minería, el canario era el pájaro que llevaban a la mina: si se moría, los mineros
> sabían que había gas antes de notarlo ellos. Aquí es igual, pero con enlaces.

**Doble función:**
1. **Vigilancia** — te avisa antes que los usuarios.
2. **Validación de actualizaciones** — es lo que decide si una versión nueva se queda
   o se revierte.

### Cómo elegir buenos canarios

| Sí | No |
|---|---|
| Contenido propio o de dominio público | Contenido con derechos de autor |
| Videos estables, publicados hace años | Videos recientes que pueden borrarse |
| Uno por plataforma que te importe | Uno solo para todo |
| Enlaces de videos completos | Enlaces que dependen de una sesión |

El canario que viene incluido es la película *Big Buck Bunny* de la Blender
Foundation: dominio público y publicada hace más de 15 años. Es improbable que
desaparezca.

**Añadir una plataforma** — edita `backend/canaries.json`:

```json
{
  "canaries": [
    { "name": "youtube",  "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
      "min_formats": 3, "enabled": true },
    { "name": "facebook", "url": "TU_ENLACE_ESTABLE_DE_FACEBOOK",
      "min_formats": 1, "enabled": true }
  ]
}
```

Recárgalo sin reiniciar: `POST /api/admin/canaries/reload`

### La lógica de alerta

Un fallo aislado puede ser un timeout de red y no merece despertar a nadie. El sistema
espera **3 fallos consecutivos** antes de alertar (`VDL_CANARY_ALERT_AFTER`), y
**deduplica** las alertas durante 10 minutos para no inundar el canal.

---

## 5. Capa 3 — Circuit breaker

Archivo: [`backend/app/resilience.py`](backend/app/resilience.py)

Cuando Facebook está caído, cada usuario que pega un enlace dispara tres reintentos
con espera. Eso no arregla nada: consume conexiones, ocupa hilos y hace esperar al
usuario 15 segundos para darle un error que ya se conocía.

El circuit breaker corta eso:

```
CERRADO ──5 fallos seguidos──► ABIERTO ──180s──► MEDIO ABIERTO
   ▲                                                     │
   │                                                     ├─ éxito ──► CERRADO
   └─────────────────────────────────────────────────────┴─ fallo ──► ABIERTO
```

- **Cerrado:** funcionamiento normal.
- **Abierto:** se falla **al instante** con un mensaje claro ("reintentando en 120s").
  El usuario no espera, y el servidor no gasta recursos.
- **Medio abierto:** tras el enfriamiento pasa **un** intento de prueba. Si funciona,
  se cierra; si no, se vuelve a abrir.

El circuito es **por dominio raíz**, así que `www.facebook.com` y `m.facebook.com`
comparten estado (si uno está caído, el otro también lo estará).

---

## 6. Capa 4 — Reintentos con backoff y clasificación de errores

No todos los errores merecen un reintento. Distinguirlos es lo que separa un sistema
que se recupera de uno que hace esperar al usuario para nada.

| Tipo de error | Ejemplo | Qué hace el sistema |
|---|---|---|
| **Transitorio** | Timeout, conexión cortada, 503 | Reintenta 3 veces con espera creciente |
| **Permanente** | "Video no disponible", geobloqueado | Falla al instante con mensaje claro |
| **Estructural** | "Unable to extract" | Falla y espera la próxima actualización |

```python
# La espera crece exponencialmente, con desvío aleatorio:
# intento 1 → ~2s   intento 2 → ~4s   intento 3 → ~8s
# El desvío evita que varios trabajos reintenten sincronizados.
```

**Por qué importa el jitter:** sin él, diez descargas que fallan a la vez reintentan
a la vez, creando un pico de tráfico contra un servidor que ya está sufriendo. Con
jitter, el pico se reparte.

---

## 7. Capa 5 — Estrategias de respaldo en la descarga

Archivo: [`backend/app/downloader.py`](backend/app/downloader.py) — `_format_strategies()`

Muchos "fallos" que ve el usuario no son "el sitio no funciona", sino "esa calidad
concreta ya no existe". En lugar de devolver un error, se prueban alternativas:

```
1. <formato elegido>+mejor audio     ← lo que pidió el usuario
2. bv*+ba/b                          ← lo mejor disponible, con unión
3. b[ext=mp4]/b                      ← progresivo, sin necesidad de FFmpeg
```

La tercera estrategia merece atención: **no requiere FFmpeg**. Si el FFmpeg del
servidor falla o no está, todavía se puede entregar un archivo. Es degradación
elegante en vez de error.

---

## 8. Capa 6 — Métricas por plataforma

Archivo: [`backend/app/metrics.py`](backend/app/metrics.py) · Endpoint: `GET /api/metrics`

Se mide cada plataforma por separado, porque es normal que YouTube funcione al 100%
mientras Facebook está caído. Una tasa global escondería el problema.

| Estado | Tasa de éxito | Significado |
|---|---|---|
| `sano` | ≥ 90% | Todo bien |
| `degradado` | 60–90% | Algo va mal, investigar |
| `caido` | < 60% | Intervención o esperar el arreglo de yt-dlp |

Se calcula sobre una **ventana deslizante** de los últimos 50 resultados, no sobre el
total histórico. Si no, una caída antigua ya resuelta seguiría arrastrando la media
hacia abajo y ocultarían una recuperación.

---

## 9. Capa 7 — Proxies rotativos

El soporte ya está implementado; lo que falta es **contratar los proxies**.

- En tu máquina, funciona sin proxy.
- En un VPS, los sitios grandes empezarán a bloquear en cuestión de días.
- La solución es enrutar las peticiones por **proxies residenciales**, idealmente del
  mismo país que el contenido.

```bash
# Un proxy para todo
VDL_PROXY=http://usuario:clave@proxy.example:8080

# O un pool distinto por plataforma: si un proxy se quema en Facebook,
# YouTube sigue funcionando por otro.
VDL_PROXY_MAP=youtube=http://u:p@host:8080,facebook=http://u:p@host:8081
```

El servicio avisa por sí solo: `GET /api/health` incluye un campo `proxy` que indica
si está configurado, y una advertencia explícita si no lo está.

**El coste es la decisión clave:** los proxies residenciales se cobran **por GB
transferido**, y una descarga en 4K puede consumir cientos de MB. Es la partida que
determina si el servicio es sostenible.

> **Recomendación:** antes de escalar, mide. Registra los GB descargados durante una
> semana y calcula el coste real por usuario antes de abrirlo al público.

---

## 10. Rutina de mantenimiento (lo que de verdad hay que hacer)

Con todo lo anterior funcionando, el trabajo humano baja a esto:

| Frecuencia | Qué mirar | Dónde |
|---|---|---|
| **Nunca** | Actualizar yt-dlp | Automático |
| **Cuando llegue una alerta** | Qué plataforma cayó y por qué | Webhook / `GET /api/metrics` |
| **Semanal** | Tasa de éxito por plataforma | `GET /api/metrics` |
| **Mensual** | Revisar canarios, añadir plataformas nuevas | `canaries.json` |
| **Mensual** | Coste de proxies vs. uso real | Panel del proveedor |

**La señal más importante es una sola:** `overall_success_rate` en `/api/metrics`.
Si baja del 90% y no se recupera en 24 horas, hay algo que el sistema no puede
arreglar solo — normalmente un cambio de sitio que yt-dlp todavía no ha cubierto.

---

## 11. Checklist antes de abrir al público

- [ ] `VDL_RESTART_AFTER_UPDATE=true` **y** un supervisor activo
      (systemd `Restart=always` o Docker `restart: unless-stopped`).
      Sin supervisor, la actualización se instala pero nunca se usa.
- [ ] `VDL_ALERT_WEBHOOK` configurado. Un sistema que se auto-repara pero no avisa
      sigue siendo un sistema del que no te enteras.
- [ ] Canarios para **cada** plataforma que ofrezcas, no solo YouTube.
- [ ] Proxies contratados y `VDL_PROXY` (o `VDL_PROXY_MAP`) configurado.
- [ ] `VDL_ALLOWED_DOMAINS` restringido a lo que realmente ofreces.
- [ ] CORS restringido a tu dominio (ahora acepta todos).
- [ ] `VDL_ADMIN_TOKEN` con un valor largo y aleatorio.
- [ ] HTTPS en el proxy inverso (Nginx o Cloudflare).
- [ ] Rate limiting también en el proxy inverso, no solo en la aplicación.
- [ ] Página y agente DMCA publicados.
- [ ] Backups de `canaries.json` y del archivo de estado del actualizador.

---

## 12. Qué más se puede añadir

Ordenado por relación valor/esfuerzo, después de lo ya implementado.

### Resiliencia adicional (alto valor)

| Mejora | Qué resuelve | Esfuerzo |
|---|---|---|
| **Cola real con Redis** | El estado vive en memoria: al reiniciar se pierden los trabajos en curso. Con Redis, sobreviven. | 1–2 días |
| **Múltiples versiones de yt-dlp en paralelo** | Poder volver a una versión concreta sin esperar a pip. | 1 día |
| **Caché de metadatos** | El mismo video popular se analiza mil veces. Con caché, mil veces menos peticiones al sitio. | 1 día |
| **Caché de archivos** | Si diez usuarios piden el mismo video, se descarga una vez. Ahorra ancho de banda y proxies. | 2 días |
| **Rotación de IP propia** | Alternativa más barata que los proxies: varias IPs en el mismo proveedor. | 2 días |
| **Contador de GB por proxy** | Saber qué proxy se está quemando y cuánto cuesta. | 1 día |
| **Banco de cookies con refresco** | Para contenido que exige sesión: varias cuentas que rotan y se refrescan. | 2–3 días |

### Funciones de producto

| Función | Por qué |
|---|---|
| **Más plataformas** | Ya está: yt-dlp soporta +1.700 sitios. Solo hay que dejar de filtrar dominios. |
| **Playlists y descarga por lotes** | Muy pedido; conversión de la cola en lista de trabajos. |
| **Subtítulos** | Descarga `.srt` / `.vtt` junto al video. |
| **Recorte antes de descargar** | El usuario elige inicio y fin; se recorta con FFmpeg. |
| **Elección de códec y contenedor** | MKV, WEBM, MP4 con códecs concretos. |
| **PWA instalable** | La app web funciona sin conexión y se instala en el móvil. |
| **Extensión de navegador** | Botón que envía el enlace actual al servicio. Es lo que más usa la gente. |
| **API con claves** | Base para planes de pago o uso interno. |
| **Panel de administración** | Ver métricas, circuitos y canarios en una interfaz, no por API. |

> **Nota sobre monetización:** los planes de pago sobre un servicio así traen
> implicaciones legales y fiscales distintas a las de un proyecto personal. Vale la
> pena consultar antes de construirlo, no después.

---

## 13. Referencia: variables de resiliencia

| Variable | Por defecto | Para qué |
|---|---|---|
| `VDL_AUTO_UPDATE` | `true` | Actualización automática del motor |
| `VDL_UPDATE_INTERVAL_HOURS` | `12` | Frecuencia de revisión |
| `VDL_UPDATE_CHANNEL` | `stable` | `stable` o `nightly` |
| `VDL_UPDATE_ROLLBACK` | `true` | Revertir si la validación falla |
| `VDL_UPDATE_STATE_FILE` | `backend/update_state.json` | Memoria de la última versión buena |
| `VDL_RESTART_AFTER_UPDATE` | `false` | Salir para que el supervisor reinicie |
| `VDL_RESTART_COMMAND` | vacío | Orden externa de reinicio |
| `VDL_CANARY_ENABLED` | `true` | Activar canarios |
| `VDL_CANARY_INTERVAL_MINUTES` | `30` | Frecuencia de prueba |
| `VDL_CANARY_MIN_PASS_RATE` | `60` | Umbral de salud (%) |
| `VDL_CANARY_ALERT_AFTER` | `3` | Fallos seguidos antes de alertar |
| `VDL_CANARY_FILE` | `backend/canaries.json` | Lista de canarios |
| `VDL_ALERT_WEBHOOK` | vacío | Destino de las alertas |
| `VDL_RETRY_ATTEMPTS` | `3` | Reintentos por operación |
| `VDL_RETRY_BASE_DELAY` | `2` | Base del backoff (segundos) |
| `VDL_CIRCUIT_THRESHOLD` | `5` | Fallos para abrir el circuito |
| `VDL_CIRCUIT_COOLDOWN` | `180` | Enfriamiento (segundos) |
| `VDL_PROXY` | vacío | Proxy general |
| `VDL_PROXY_MAP` | vacío | Proxies por plataforma |
| `VDL_COOKIES_FILE` | vacío | Archivo de cookies |
| `VDL_COOKIES_DIR` | vacío | Carpeta de cookies con rotación |
| `VDL_PLAYER_CLIENTS` | vacío | Clientes de reproducción (YouTube) |
| `VDL_ADMIN_TOKEN` | vacío | Habilita `/api/admin/*` |

---

## 14. Resumen: el cambio de mentalidad

Lo que hace que este servicio dure no es ninguna línea de código concreta. Es haber
aceptado que **se va a romper**, y haber construido en consecuencia:

1. **Se rompe** → se actualiza solo, se valida, y se revierte si empeora.
2. **Se rompe y no hay arreglo todavía** → se falla rápido y con un mensaje claro, sin
   agotar recursos ni hacer esperar al usuario.
3. **Se rompe y nadie lo sabe** → los canarios avisan antes que los usuarios.

Ese es el trabajo. Todo lo demás es optimización.
