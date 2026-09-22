# Widget y monitor para Android

**El widget nativo está hecho y compilado.** El APK firmado está en
[`apk/monitor-descargador-1.1.apk`](apk/monitor-descargador-1.1.apk) — 1,78 MB.
Se instala directamente, sin Android Studio.

La versión 1.1 añade el **enlace de conexión**: el monitor web entrega la dirección y el
token ya juntos, así que no hay que escribirlos a mano en el móvil. Ver
[§1.3 El enlace de conexión](#13-el-enlace-de-conexión).

También hay un **panel web** (`GET /monitor`) que funciona sin instalar nada, y una
tercera vía sin compilar (KWGT + Tasker) por si prefieres no instalar APKs ajenos.

---

## 1. Lo que ya está hecho (funciona ahora)

Abre `http://<tu-servidor>:8000/monitor` en Chrome de Android → menú → **"Añadir a
pantalla de inicio"**.

Queda como un icono que abre el panel. Muestra:

- Estado general con color (verde / ámbar / rojo) y diagnóstico en texto
- Aviso destacado cuando **conviene reiniciar**, con el motivo
- Tasa de éxito, circuitos abiertos, descargas activas, versión del motor, FFmpeg
- Salud por plataforma (YouTube, Facebook…) y últimos problemas
- Botones: **Reiniciar servicio**, Reiniciar circuitos, Ejecutar canarios, Liberar
  espacio, Limpiar alertas

También está registrado como PWA (`manifest.webmanifest` + `sw.js`), pero ojo con un
detalle real: **el service worker solo se registra bajo HTTPS o en `localhost`**. Por
HTTP en una IP de red local, el acceso directo funciona igual, pero no la instalación
completa como PWA. Si quieres la PWA real, pon HTTPS (Nginx + Let's Encrypt o
Cloudflare Tunnel).

### Endpoint pensado para el widget

`GET /api/widget` devuelve exactamente lo que un widget necesita pintar, sin lógica:

```json
{
  "status": "caido",
  "status_label": "Sistema caído",
  "color": "#c0392b",
  "success_rate": "17%",
  "circuits": 1,
  "jobs": 0,
  "engine": "2026.08.19",
  "last_check": "hace 3m",
  "problem": "Tasa de éxito del 17%",
  "problem_ago": "ahora",
  "problem_level": "error",
  "restart_advised": true,
  "restart_reason": "el sistema está caído",
  "actions": {
    "refresh": "/api/widget",
    "restart": "/api/admin/restart",
    "reset_circuits": "/api/admin/circuits/reset",
    "run_canaries": "/api/admin/canaries/run",
    "purge": "/api/admin/storage/purge",
    "dashboard": "/monitor"
  },
  "auth": "header X-Admin-Token",
  "updated": "ahora"
}
```

Todo viene ya formateado como texto corto y un color hex. Es deliberado: un widget de
Android no ejecuta lógica propia, solo pinta lo que recibe.

### 1.3 El enlace de conexión

Configurar el widget a mano tiene dos problemas: escribir una dirección en el móvil es
incómodo, y el enlace se rompe solo cuando el router cambia la IP por DHCP. Para
arreglarlo, el monitor web tiene una tarjeta **«Conexión con la app»** con los dos datos
en un solo sitio:

- **el enlace del servidor** (la dirección a la que debe apuntar el widget), con botón
  de copiar;
- **el token**, con botón de copiar;
- **un botón que configura la app sola** desde el propio teléfono;
- y **el estado de la conexión**: cuándo habló la app con el servidor por última vez y
  desde qué IP, o «sin contacto» si nunca lo hizo.

Ese último punto es el que de verdad ahorra tiempo, porque distingue dos averías que
desde fuera se ven igual y no se arreglan igual:

| Lo que muestra | Lo que significa | Qué hacer |
|---|---|---|
| **sin contacto** | La app nunca llegó a hablar con el servidor | El enlace o el token están mal, o falta abrir el puerto en el firewall |
| **app conectada · hace 2m** | La app funciona; si el widget falla, es el móvil (red, batería) | Revisar la red del móvil |
| **app conectada · hace 3h** | Funcionaba y dejó de llegar | Probablemente la IP cambió: vuelve a conectar |

#### Cómo se conecta la app sin escribir nada

El botón **«Configurar la app en este teléfono»** abre un enlace con este formato:

```
vdl://pair?url=http%3A%2F%2F192.168.1.50%3A8000&token=e554beab…
```

Ese esquema `vdl://pair` está registrado en el manifiesto del APK, así que al abrirlo
Android entrega los datos a la app, que los guarda, refresca el widget y no vuelve a
preguntar. Para usarlo:

1. Abre `http://<servidor>:8000/monitor` **en el navegador del móvil**.
2. Escribe el token de administración una vez (se queda guardado en ese navegador).
3. Pulsa **Configurar la app en este teléfono**.

Si prefieres copiar los datos a mano, están en la misma tarjeta en dos campos separados.

#### `GET /api/pairing`

Todo lo anterior sale de este endpoint:

```json
{
  "mode": "local",
  "urls": ["http://192.168.1.50:8000"],
  "preferred_url": "http://192.168.1.50:8000",
  "listening": true,
  "token_configured": true,
  "token_masked": "e554…12a6",
  "token": null,
  "pairing_link": null,
  "apk_link": "http://192.168.1.50:8000/app.apk",
  "app": { "seen": true, "ago": "hace 2m", "ip": "192.168.1.50", "version": "1.1" },
  "warning": null
}
```

Dos detalles deliberados:

- **El token y el enlace completo solo salen si quien pregunta demuestra conocer el
  token** (cabecera `X-Admin-Token`). Sin esa comprobación, cualquiera que alcanzara el
  monitor se llevaría la credencial que reinicia el servicio y borra archivos. Por eso
  los campos salen en `null` y solo se enseña la huella (`token_masked`).
- **`mode`** distingue si la petición llegó por un host real (`publico`: en producción
  será tu dominio con HTTPS) o desde la propia máquina (`local`: entonces se listan las
  direcciones de la red local). En Docker no tendría sentido ofrecer la IP del
  contenedor, y en producción no tiene sentido ofrecer una IP interna.

Además, `warning` avisa de los dos fallos que dejan al móvil sin poder conectar y que no
se ven desde el widget: que el servidor esté escuchando **solo en `127.0.0.1`**, y que
falte abrir el puerto en el firewall.

#### Cómo sabe el servidor que la app está viva

El APK envía la cabecera `X-VDL-Client: android-widget/1.1` en cada petición. Un
middleware la registra, y de ahí sale el estado de la tarjeta. Es lo que permite decir
«la app habló con el servidor hace 2m» en lugar de dar por hecho que todo va bien
porque el servidor responde.

---

| | **A. Panel instalable** | **B. KWGT / Tasker** | **C. App nativa (APK)** |
|---|---|---|---|
| Estado | ✅ Hecho | 📋 Receta | ✅ **Compilada y firmada** |
| Widget real en pantalla de inicio | No (icono) | **Sí** | **Sí** |
| Actualización automática | Al abrir | 15–30 min | 15–30 min |
| Requiere compilar | No | No | **Sí** (Java + SDK) |
| Requiere apps de pago | No | Sí (KWGT ~5 €, Tasker ~4 €) | No |
| Botón de reinicio dentro del widget | No | **Sí** | **Sí** |
| Control total del diseño | Limitado | Alto | **Total** |
| Funciona en iOS | Sí (acceso directo) | No | No |

### Limitaciones reales de los widgets de Android

Conviene saberlas antes de elegir, porque condicionan lo que se puede prometer:

1. **Un widget no hace peticiones de red por sí mismo.** El sistema pide a la app que
   dibuje un `RemoteViews` con datos ya preparados. Alguien (WorkManager, AlarmManager)
   tiene que haber consultado la API antes.
2. **La frecuencia mínima razonable de actualización es ~15 minutos.** Para Android,
   `updatePeriodMillis` por debajo de 30 minutos no se respeta; se usa WorkManager con
   un mínimo de 15 minutos. **Un widget no puede ser "en tiempo real".**
3. **Botones:** funcionan con `PendingIntent` → el widget dispara un receptor que hace
   la llamada en segundo plano y luego se redibuja.
4. **La app tiene que estar instalada**, aunque sea mínima.

Si necesitas ver el estado al segundo, el panel web es mejor herramienta. El widget es
para **"echar un vistazo sin abrir nada"** y para tener el botón de reinicio a mano.

---

## 3. Opción C — la app nativa (compilada y firmada)

**Está hecha.** No quedó en código sin probar: instalé toda la cadena de herramientas
(JDK 17, Android SDK 34, Gradle 8.9) y compilé el APK en esta máquina.

| Archivo | Tamaño | Para qué |
|---|---|---|
| [`apk/monitor-descargador-1.1.apk`](apk/monitor-descargador-1.1.apk) | 1,78 MB | **Release firmada** — la que se instala |
| [`apk/monitor-descargador-1.1-debug.apk`](apk/monitor-descargador-1.1-debug.apk) | 2,33 MB | Depuración (ID distinto, convive con la otra) |

`GET /app.apk` sirve siempre **la versión más alta** que haya en `apk/`, comparando los
números del nombre como números (así 1.10 gana a 1.9) y prefiriendo el release sobre el
debug. Antes elegía alfabéticamente, y eso servía la 1.0 teniendo la 1.1 al lado.

### Qué se verificó

**En el sistema de archivos** (antes de ejecutar nada):

| Comprobación | Resultado |
|---|---|
| Compilación debug y release | ✅ `BUILD SUCCESSFUL` |
| Firma del APK release | ✅ APK Signature Scheme v2, 1 firmante, RSA 2048 |
| Empaquetado (badging) | ✅ `net.monitor.descargador`, minSdk 24, targetSdk 34 |
| Receptor del widget en el manifiesto | ✅ presente, con su `intent-filter` |
| Android Lint | ✅ **0 errores**, 14 avisos (solo cosméticos) |
| Layout del widget compatible con `RemoteViews` | ✅ sin avisos del detector `RemoteViewLayout` |

**Ejecutándose de verdad** en un emulador Android 14 (x86_64, aceleración WHPX):

| Comprobación | Resultado |
|---|---|
| Instalación del APK | ✅ `Success` |
| Arranque de la app | ✅ sin fallos en logcat |
| Interfaz de configuración | ✅ renderiza (ver [`capturas/01-settings.png`](android-widget/capturas/01-settings.png)) |
| Guardar configuración desde la UI | ✅ |
| Conexión con el backend | ✅ **"Conexión correcta · Sistema funcionando (100%)"** |
| Registro del proveedor en Android | ✅ `net.monitor.descargador.WidgetProvider` presente en `dumpsys appwidget` |
| Colocación del widget en el escritorio | ✅ |
| Widget mostrando datos reales | ✅ punto verde, "Sistema funcionando", "éxito 100%", "circuitos 0", "motor 2026.08.19" |
| Botón **CIRCUITOS** | ✅ `POST /api/admin/circuits/reset` → `200 OK`, y el widget mostró "Circuitos reiniciados" |
| Botón **REINICIAR** | ✅ `POST /api/admin/restart` → `200 OK` y **el backend se detuvo** |
| Recuperación tras reiniciar | ✅ el widget volvió a verde con datos nuevos |
| Panel web `/monitor` en el navegador del móvil | ✅ renderiza completo |

Capturas del proceso en [`android-widget/capturas/`](android-widget/capturas/).

### Verificación de la versión 1.1

Lo que se comprobó sobre el APK 1.1 recién compilado:

| Comprobación | Resultado |
|---|---|
| Compilación release y debug | ✅ `BUILD SUCCESSFUL` |
| Firma | ✅ mismo keystore (`monitor-release.jks`), RSA 2048, SHA-256 `a20c8e3b…` |
| Versión empaquetada | ✅ `versionCode=2`, `versionName=1.1` |
| Registro de `vdl://pair` en el manifiesto | ✅ esquema `vdl`, host `pair`, categoría `BROWSABLE` |
| `launchMode` de la pantalla de configuración | ✅ `singleTask` (para que `onNewIntent` reciba el enlace) |
| `usesCleartextTraffic` | ✅ `true` |
| `node --check` sobre `monitor.js` | ✅ sintaxis correcta |
| La tarjeta de conexión llega en `/monitor` | ✅ los 8 elementos presentes en el HTML servido |
| `/api/pairing` sin token | ✅ devuelve huella (`e554…12a6`) y `pairing_link: null` |
| `/api/pairing` con token | ✅ devuelve token y enlace completo |
| El enlace se interpreta y coincide con el token del servidor | ✅ comprobado con un analizador de URLs |
| Registro de `X-VDL-Client` | ✅ el servidor lo apunta con IP y hora |
| `GET /app.apk` sirve la 1.1 | ✅ sha256 idéntico al archivo de `apk/` |

**Lo que no se verificó:** el APK 1.1 **no se llegó a ejecutar** en el emulador ni en un
móvil. Todo lo anterior es estático (compilación, manifiesto, empaquetado, API), así que
queda razonablemente cubierto, pero conviene ser claro en lo que no: **no está probado
que el enlace `vdl://pair` abra la app en un dispositivo real**, ni que el botón
«Reconectar» aparezca y se oculte cuando toca. Es lo primero que hay que mirar al
instalarlo.

### Lo que sigue sin verificarse

- **Un móvil físico.** Todo se probó en un emulador Pixel genérico (320×640, densidad
  160). Los fabricantes modifican el lanzador, el ahorro de energía y los permisos de
  red en segundo plano; eso solo se comprueba en un dispositivo real.
- **Que el widget sobreviva a un reinicio del móvil** de forma sostenida. En el
  emulador, la instancia se perdió una vez al recargar el lanzador y hubo que volver a
  colocarla. El código pide el refresco periódico también en `onEnabled` y `onUpdate`,
  pero conviene comprobarlo en uso real durante unos días.
- **Marcas concretas de ahorro de energía** (Xiaomi, Huawei, Samsung) que matan el
  trabajo en segundo plano. Si el widget deja de actualizarse, suele ser eso: hay que
  excluir la app de la optimización de batería.

### Un bug que apareció al ejecutar

Al abrir el panel `/monitor` en el navegador del emulador se vio `(1/undefined)` en la
lista de plataformas. El backend enviaba `success` y `failure`, pero el JavaScript leía
`p.total`, que no existía. Corregido en `monitor.js` y añadido `total` al payload de
`metrics.py`.

Es el tipo de fallo que solo aparece al ejecutar: ninguna comprobación estática lo
habría detectado.

### Estructura real del proyecto

```
android-widget/
├── settings.gradle.kts · build.gradle.kts · gradle.properties
├── gradlew · gradlew.bat · gradle/wrapper/    (wrapper, reproducible)
├── local.properties        ruta del SDK (propia de cada máquina)
├── keystore.properties     credenciales de firma (no subir a git)
├── monitor-release.jks     clave de firma generada
└── app/
    ├── build.gradle.kts · proguard-rules.pro
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/net/monitor/descargador/
        │   ├── WidgetProvider.kt      Dibuja el widget en la pantalla de inicio
        │   ├── StatusRepository.kt    Llamada a /api/widget + caché local
        │   ├── MonitorWorker.kt       WorkManager: refresco cada 15 min y acciones
        │   ├── ActionReceiver.kt      Toques de los botones
        │   ├── SettingsActivity.kt    Configurar dirección del servidor y token
        │   └── Prefs.kt               Persistencia de configuración
        └── res/
            ├── xml/widget_info.xml    Metadatos del widget
            ├── layout/widget_status.xml
            ├── drawable/              Fondo, botones y el punto de estado
            └── values/                Colores, textos, estilos
```

### Decisiones técnicas (las reales, no las planeadas)

| Decisión | Elección final | Por qué |
|---|---|---|
| API de widget | **RemoteViews clásico** | Había planeado Glance, pero Glance obliga a acoplar el compilador de Compose con Kotlin 2.x. Con RemoteViews el proyecto compila a la primera y sin dependencias frágiles. Más XML, cero riesgo de versión |
| Refresco | **WorkManager**, cada 15 min | Es lo mínimo que Android respeta de verdad |
| Red | `HttpURLConnection` + `org.json` | Vienen en Android. Cero dependencias extra (nada de OkHttp ni Gson) |
| Caché | Último estado en `SharedPreferences` | El widget pinta al instante y sobrevive a la falta de red |
| Botones | `ActionReceiver` + `PendingIntent` | Con `data` distinta por acción; sin eso, los tres botones disparan la misma |
| Configuración | `SharedPreferences` privadas | Ver nota de seguridad al final |

### El detalle que más importa

**El widget debe distinguir "todo bien" de "no lo sé".** Si no hay conexión y pinta el
último estado conocido sin avisar, te creerás que el sistema funciona cuando en
realidad llevas horas sin saber nada. Por eso:

- Verde solo si el último dato es reciente (menos de 45 minutos).
- Gris con "Datos de hace X" cuando el dato es viejo, en lugar de un verde falso.
- El texto del problema explica siempre el motivo, nunca deja solo un "caído".

Es el mismo principio que el canario: **no saber nunca debe parecer "todo bien"**.

### Instalar en el móvil

1. Pasa `monitor-descargador-1.1.apk` al teléfono (cable, Google Drive, Telegram…).
2. Ábrelo. Android pedirá activar **"Instalar apps de origen desconocido"** para la
   app desde la que lo abras. Es normal: no está en Play Store.
3. Asegúrate de que el **móvil está en la misma red Wi-Fi** que el servidor.
4. Configúralo de la forma más cómoda:

   **Automática (recomendada).** Abre `http://<tu-servidor>:8000/monitor` en el
   navegador del teléfono, escribe el token en la tarjeta «Conexión con la app» y pulsa
   **Configurar la app en este teléfono**. El widget queda listo sin escribir nada más.

   **A mano.** Abre *Monitor Descargador* y pega el **enlace** y el **token** que
   muestra esa misma tarjeta. Son los dos campos que pide la pantalla.

5. Pulsa **Probar conexión**. Debe responder "Conexión correcta · Sistema funcionando".
6. Sal a la pantalla de inicio, mantén pulsado un hueco vacío → **Widgets** →
   **Monitor Descargador** → arrástralo.

La dirección correcta es la IP de tu equipo en la red local, no `127.0.0.1`: desde el
móvil, `127.0.0.1` es el propio móvil. Si el widget queda en «Sin conexión», el motivo
casi siempre está en el `warning` que acompaña al enlace en el monitor.

### Si la conexión se cae

- **Un corte pasajero no se nota.** Cada refresco reintenta hasta tres veces, así que un
  cambio de Wi-Fi a datos o un microcorte se resuelve solo y el widget no llega a
  parpadear.
- **Si sigue sin poder, el widget lo dice.** A los dos fallos seguidos el punto pasa a
  gris con el texto «Sin conexión con el servidor» —gris, no rojo: lo que falla es el
  enlace hasta el servicio, no el servicio— y aparece un botón **Reconectar**.
- **Los botones que no pueden funcionar desaparecen.** Sin servidor, «Circuitos» y
  «Reiniciar» se ocultan: ofrecerlos solo confundiría.
- **Para repararlo de verdad, vuelve a conectar.** Si la IP cambió, abre otra vez el
  monitor en el móvil y pulsa **Configurar la app en este teléfono**: el enlace nuevo
  entra solo, con el token incluido.

> **Lo que no hace todavía:** buscar el servidor por sí solo en la red. Si la IP cambia,
> el widget no puede adivinar la nueva sin descubrimiento mDNS/NSD. Eso está identificado
> como siguiente paso; hoy la reparación es «reintentar + reconectar con un toque».

### Recompilarlo tú

El proyecto está completo y con el wrapper incluido:

```bash
cd android-widget
./gradlew assembleRelease        # macOS / Linux
gradlew.bat assembleRelease      # Windows
```

Para abrirlo con interfaz gráfica: **Android Studio → Open** sobre la carpeta
`android-widget/`. Si instalas Android Studio, borra `local.properties` primero: se
regenera solo con tu ruta del SDK.

**Antes de publicar nada, cambia la clave de firma.** La que generé es de ejemplo y sus
datos están en `keystore.properties`, que **no se sube al repositorio** (junto con el
archivo `.jks`). Para crear la tuya:

```bash
keytool -genkeypair -v -keystore mi-clave.jks -alias monitor \
  -keyalg RSA -keysize 2048 -validity 10000
```

Si distribuyes el APK con una clave publicada, cualquiera que la conozca puede firmar
una actualización que tu móvil aceptará como legítima. Para uso personal da igual; para
repartirlo, genera una tuya y guarda el `.jks`. **Si pierdes la clave, no podrás
actualizar la app instalada**: habría que desinstalar y volver a instalar.

---

## 4. Opción B — KWGT / Tasker (sin compilar)

Si no quieres instalar Android Studio, esta vía da un **widget real** sin escribir
Kotlin:

1. Instala **Tasker** y crea una *Tarea HTTP Request* con método `GET` a
   `http://<servidor>:8000/api/widget`.
2. Guarda la respuesta en variables (`%status`, `%color`, `%problem`…).
3. Instala **KWGT** y crea un widget que pinte esas variables.
4. En Tasker, añade un *Perfil → Time* cada 15 minutos que ejecute la tarea.
5. Para el botón de reinicio: un segundo *HTTP Request* con método `POST` a
   `/api/admin/restart` y la cabecera `X-Admin-Token`.

**Ventaja:** cero código, resultado en una tarde.
**Límite:** depende de dos apps de pago y el diseño está acotado a lo que KWGT permita.

---

## 5. Mi recomendación

| Si… | Entonces |
|---|---|
| Quieres un widget real **ya** | Opción C: instala el APK de [`apk/`](apk/). |
| Prefieres no instalar apps fuera de Play Store | Panel `/monitor` o Opción B (KWGT + Tasker). |
| Quieres cambiar el diseño del widget | Opción C: el código es tuyo, en [`android-widget/`](android-widget/). |
| Necesitas ver el estado **al segundo** | Panel web. Un widget nunca será tiempo real. |

**Sea cual sea la opción, el requisito previo es el mismo:** define `VDL_ADMIN_TOKEN`.
Sin él, los endpoints de administración están deshabilitados (devuelven 404) y ningún
botón de reinicio funcionará, ni desde el panel ni desde el widget.

```bash
# Genera un token largo y aleatorio
openssl rand -hex 24
```

Y recuerda la condición que ya vimos: **reiniciar solo funciona si hay un supervisor**
(systemd `Restart=always` o Docker `restart: unless-stopped`). Sin él, el botón apaga
el servicio y no vuelve.

---

## 6. Seguridad

El token protege acciones destructivas (reiniciar, borrar archivos). Consideraciones:

- **No expongas el servicio directamente a internet** sin HTTPS. El token viaja en una
  cabecera: por HTTP plano, cualquiera en la misma red puede leerlo.
- Para acceso desde fuera de casa, usa **Cloudflare Tunnel**, **Tailscale** o una VPN
  antes que abrir el puerto.
- El token se guarda en el navegador (`localStorage`) en el panel, y en
  `SharedPreferences` privadas en la app. **No van cifradas.** Las protege el sandbox
  de Android —ninguna otra app puede leerlas en un dispositivo sin root—, pero en un
  móvil con root o comprometido serían legibles. Si eso te preocupa, usa un token
  dedicado que puedas revocar sin afectar a nada más.
- El APK se compila con `android:usesCleartextTraffic="true"`, necesario porque el
  servidor suele estar en la red local por HTTP. Es una concesión real: **si algún día
  expones el servicio a internet, ponle HTTPS** y considera desactivar el tráfico en
  claro.
- `POST /api/admin/restart` deja el servicio caído unos segundos. No lo pongas detrás
  de un botón que se pulse por accidente sin confirmación.
