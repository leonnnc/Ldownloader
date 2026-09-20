# Widget y monitor para Android

**El widget nativo está hecho y compilado.** El APK firmado está en
[`apk/monitor-descargador-1.0.apk`](apk/monitor-descargador-1.0.apk) — 1,78 MB.
Se instala directamente, sin Android Studio.

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

---

## 2. Las tres opciones de widget nativo

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
| [`apk/monitor-descargador-1.0.apk`](apk/monitor-descargador-1.0.apk) | 1,78 MB | **Release firmada** — la que se instala |
| [`apk/monitor-descargador-1.0-debug.apk`](apk/monitor-descargador-1.0-debug.apk) | 2,33 MB | Depuración (ID distinto, convive con la otra) |

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

1. Pasa `monitor-descargador-1.0.apk` al teléfono (cable, Google Drive, Telegram…).
2. Ábrelo. Android pedirá activar **"Instalar apps de origen desconocido"** para la
   app desde la que lo abras. Es normal: no está en Play Store.
3. Instálalo y ábrelo.
4. Escribe la dirección de tu servidor, por ejemplo `http://192.168.1.50:8000`
   (sin barra final), y el `VDL_ADMIN_TOKEN` si quieres poder reiniciar desde ahí.
5. Pulsa **Probar conexión**. Debe responder "Conexión correcta · Sistema funcionando".
6. Sal a la pantalla de inicio, mantén pulsado un hueco vacío → **Widgets** →
   **Monitor Descargador** → arrástralo.

La dirección correcta es la IP de tu equipo en la red local, no `127.0.0.1`: desde el
móvil, `127.0.0.1` es el propio móvil.

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
