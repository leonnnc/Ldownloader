# Despliegue en producción

Guía para poner el servicio en internet. Empieza por lo que **no** funciona, porque
ahorrarás tiempo y dinero.

---

## 1. Lo primero: qué hosting NO sirve

Un descargador no es una web normal. Necesita **ejecutar procesos largos** (descargar
un video de 4K tarda minutos), **guardar archivos temporales** y tener **FFmpeg
instalado**. Eso descarta la mayoría de hostings baratos.

| Tipo de hosting | ¿Sirve? | Por qué |
|---|---|---|
| **Hosting compartido / cPanel / PHP** | ❌ **No** | No permite procesos en segundo plano ni instalar FFmpeg. Es el error más común |
| **Vercel / Netlify / Cloudflare Pages** | ❌ **No** | Son para frontend y funciones serverless con límite de ~10-60 s. Una descarga los revienta |
| **Railway / Render / Fly.io** | ⚠️ **Sí, con matices** | Docker funciona, pero vigila el disco, los límites de CPU y el apagado por inactividad |
| **VPS (Hetzner, DigitalOcean, Contabo…)** | ✅ **Recomendado** | Control total, FFmpeg sin problema, disco propio |
| **Oracle Cloud Free Tier** | ✅ Opción gratuita | VPS gratis de por vida (con disponibilidad irregular según la región) |

> **Regla práctica:** si tu hosting no te deja ejecutar `ffmpeg -version` por SSH,
> no sirve.

> **¿Y si solo quieres probarlo?** No necesitas servidor ni dominio: se levanta
> gratis en un Codespace de GitHub con su propia URL `https`. Salta al
> [§10](#10-probar-gratis-y-sin-servidor-github-codespaces).

---

## 2. Requisitos mínimos del servidor

| Recurso | Mínimo | Recomendado |
|---|---|---|
| CPU | 1 vCPU | 2 vCPU (FFmpeg usa CPU al convertir) |
| RAM | 1 GB | 2-4 GB |
| Disco | 20 GB | 40 GB (archivos temporales + imágenes Docker) |
| Ancho de banda | 1 TB/mes | **Depende del uso. Es la partida que más crece** |

**Sistema operativo:** Ubuntu 22.04 o 24.04 LTS. Es donde mejor funciona Docker.

---

## 3. Despliegue paso a paso (VPS con Docker)

### 3.1 Preparar el servidor

```bash
# Conéctate por SSH
ssh root@TU_IP

# Actualiza e instala Docker
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin git

# Cortafuegos: solo SSH y web
ufw allow 22
ufw allow 80
ufw allow 443
ufw enable
```

### 3.2 Traer el proyecto

```bash
git clone https://github.com/leonnnc/Ldownloader.git
cd Ldownloader
```

### 3.3 Configurar

```bash
cp deploy/produccion.env.example deploy/produccion.env
nano deploy/produccion.env
```

Lo mínimo que **debes** cambiar:

```bash
# Token de administración (genera uno nuevo, no reutilices el local)
VDL_ADMIN_TOKEN=$(openssl rand -hex 24)

# A dónde avisar cuando algo se rompa. IMPRESCINDIBLE.
VDL_ALERT_WEBHOOK=https://hooks.slack.com/services/TU/WEBHOOK
```

### 3.4 Apuntar el dominio

En tu proveedor de DNS, crea un registro **A**:

```
descargador.tudominio.com  →  TU_IP
```

Espera a que propague (de unos minutos a unas horas).

### 3.5 Levantar

```bash
DOMAIN=descargador.tudominio.com docker compose -f deploy/docker-compose.prod.yml up -d --build
```

Caddy obtendrá el certificado HTTPS automáticamente. En un minuto:

```bash
curl https://descargador.tudominio.com/api/health
```

### 3.6 Comprobar que funciona

```bash
# Estado
curl -s https://descargador.tudominio.com/api/health | python3 -m json.tool

# Registros en vivo
docker compose -f deploy/docker-compose.prod.yml logs -f api
```

---

## 4. Configuración que NO debes olvidar

Cada punto cubre un fallo que ya vimos durante el desarrollo:

| Ajuste | Por qué es obligatorio |
|---|---|
| `VDL_ALERT_WEBHOOK` | Sin esto, un servicio que se auto-repara sigue siendo uno del que no te enteras |
| `VDL_RESTART_AFTER_UPDATE=true` | La actualización de yt-dlp se instala en disco, pero el proceso sigue usando el módulo viejo hasta reiniciar |
| `restart: unless-stopped` | Ya viene en el compose: Docker es el supervisor que levanta el servicio tras el reinicio |
| `VDL_ALLOWED_DOMAINS` | Sin restricción, tu servidor es un proxy abierto para cualquiera |
| `VDL_MAX_FILESIZE_MB` | Un usuario descargando un video de 4K llena el disco |
| `VDL_ADMIN_TOKEN` | Protege reiniciar y borrar archivos |
| `VDL_RATE_LIMIT_REQUESTS` | Evita que un solo usuario consuma todo el ancho de banda |

---

## 5. Proxies: el gasto que decide si es viable

**Esto es lo que separa un proyecto que funciona de uno que se muere en una semana.**

En tu PC funciona sin proxy. En un servidor (IP de datacenter), Facebook, YouTube,
Instagram y TikTok bloquean la IP en cuestión de días. Verás errores tipo
"login required" o respuestas vacías.

```bash
# Un proxy para todo
VDL_PROXY=http://usuario:clave@proxy.tuproveedor.com:8080

# O un pool distinto por plataforma
VDL_PROXY_MAP=youtube=http://u:p@host1:8080,facebook=http://u:p@host2:8081
```

Los proxies residenciales se cobran **por GB transferido**. Una sola descarga en 4K
consume cientos de MB. Antes de abrir el servicio al público:

1. Despliega **sin** proxies y mide cuántos GB al día se descargan.
2. Multiplícalo por el coste por GB de tu proveedor.
3. Decide si lo absorbes, lo limitas o lo monetizas.

El endpoint `/api/health` incluye un campo `proxy` que te avisa si no está configurado.

---

## 6. Legal, antes de abrir al público

Esto no es un detalle burocrático: es lo que puede tumbar el servicio.

- Descargar contenido **con derechos de autor** puede infringir la ley y **viola los
  Términos de Servicio** de las plataformas. Facebook los prohíbe expresamente.
- Si el servicio es público, necesitas al menos: **página de DMCA**, **política de
  privacidad** y un **correo de contacto** para retiradas.
- **No alojes el contenido.** El diseño actual es correcto: los archivos viven minutos
  y se borran solos (`VDL_FILE_TTL_MINUTES`). Mantenlo así.
- Muchos proveedores de hosting dan de baja servicios por reclamaciones de copyright.
  Ten un plan B de servidor.
- Si vas a **cobrar**, añade la parte fiscal y de consumo a la consulta legal.

**Alternativa recomendada:** mantén el servicio **privado** (acceso con token o
limitado a tu red) para uso personal. Es lo que está construido y evita todo el
problema.

---

## 7. Después de desplegar

| Cuándo | Qué |
|---|---|
| Primeras 24 h | Vigila `docker stats` y el disco. Comprueba que los canarios pasan |
| Semanal | `curl https://tu-dominio/api/metrics` — mira la tasa de éxito por plataforma |
| Si llega una alerta | Revisa `/monitor` para ver qué plataforma cayó |
| Mensual | Coste de ancho de banda y de proxies frente al uso real |
| Mensual | `docker system prune -a` para liberar imágenes viejas |

### Copias de seguridad

Lo único que merece copia son dos archivos de configuración:

```bash
# En el servidor
cp deploy/produccion.env ~/copia-produccion.env
cp backend/canaries.json ~/copia-canaries.json
```

Y en local, **guarda el `monitor-release.jks`**: si lo pierdes, no podrás actualizar
el APK instalado en los móviles.

---

## 8. Actualizar el servicio desplegado

```bash
cd Ldownloader
git pull
DOMAIN=tu.dominio.com docker compose -f deploy/docker-compose.prod.yml up -d --build
```

El motor (`yt-dlp`) se actualiza solo, sin que hagas nada. Esta actualización es solo
para cambios en el código del proyecto, que son mucho menos frecuentes.

---

## 9. Antes de dar por bueno el despliegue

- [ ] `https://tu-dominio/api/health` responde y muestra `status: ok`
- [ ] El certificado HTTPS es válido (candado en el navegador)
- [ ] El webhook de alertas recibe un aviso de prueba
- [ ] `VDL_ADMIN_TOKEN` es distinto del que usaste en local
- [ ] `VDL_ALLOWED_DOMAINS` está restringido
- [ ] El botón de reiniciar del panel `/monitor` funciona **y el servicio vuelve solo**
- [ ] El widget de Android apunta al dominio nuevo y muestra datos
- [ ] Has probado una descarga desde el móvil, **fuera de tu red**
- [ ] Tienes los proxies contratados, o has medido cuánto gastas sin ellos
- [ ] La página de DMCA y el contacto están publicados (si es público)

---

## 10. Probar gratis y sin servidor: GitHub Codespaces

GitHub **no puede alojar el servicio** —Pages solo sirve archivos estáticos, y esto
necesita Python, FFmpeg, disco y procesos de minutos—. Pero sí puede **prestártelo
para probar**: un Codespace es una máquina Linux con Docker y terminal, y puedes
publicar el puerto 8000 para abrirlo desde el móvil, fuera de tu red. En otras
palabras, la app corre igual que en producción y con URL `https`, sin contratar nada.

### Qué está preparado ya

El repositorio trae `.devcontainer/`, así que al abrir el Codespace se hace todo solo:

| Pieza | Para qué |
|---|---|
| `.devcontainer/devcontainer.json` | Imagen de Python 3.12, instala FFmpeg y las dependencias, y reenvía el puerto 8000 |
| `.devcontainer/start.sh` | Arranca el servidor en `0.0.0.0:8000` (no en `127.0.0.1`: el reenvío no lo alcanzaría) |

### Paso a paso

1. En el repositorio, botón verde **Code** → pestaña **Codespaces** → **Create codespace on main**.
2. La primera vez tarda unos minutos: descarga la imagen, instala FFmpeg y pip.
3. Cuando termine, el servidor arranca solo. Aparecerá un aviso del puerto **8000**;
   pulsa **Open in Browser**. Esa URL (`https://TU-CODESPACE-8000.app.github.dev`)
   es tu descargador funcionando.
4. **Para abrirlo desde el móvil o compartirlo**: pestaña **PORTS** → clic derecho en
   el 8000 → **Port Visibility** → **Public**. Por defecto es privado y solo lo ve tu
   cuenta de GitHub. En público, cualquiera con el enlace puede usar el servicio.
5. Se para solo tras un rato inactivo. **Se reinicia desde la pestaña Codespaces**, no
   desde la URL: la dirección cambia cada vez que creas un Codespace nuevo.

### Límites que conviene saber

| Límite | Dato |
|---|---|
| Horas gratis | 120 core-hours al mes = **60 horas** en una máquina de 2 núcleos (la que usa esta configuración) |
| Al agotarlo | GitHub suspende los Codespaces hasta el mes siguiente |
| Almacenamiento | 15 GB-mes aparte |
| Caducidad | El Codespace se apaga por inactividad y la URL **deja de responder** |
| Uso previsto | Probar, no servir a nadie: es infraestructura de desarrollo |

### Lo que NO cambia por estar en un Codespace

Los avisos de la sección 4 siguen en pie: sin `VDL_ADMIN_TOKEN` la administración está
apagada y el historial queda a la vista de quien llegue; sin proxies, los sitios
grandes bloquean la IP; y `CORS` sigue abierto. Si vas a enseñar la URL a más gente,
dedica cinco minutos a esos tres puntos — son variables de entorno, no código.
