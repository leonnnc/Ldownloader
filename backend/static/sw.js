/* =========================================================
   Service worker del panel de control.

   Regla clave: NUNCA cachear /api/*. Un monitor que muestra
   datos viejos es peor que un monitor sin conexión: te dice
   que todo va bien cuando el sistema lleva horas caído.
   ========================================================= */

const VERSION = "monitor-v1";
const SHELL = [
  "/monitor",
  "/monitor.css",
  "/monitor.js",
  "/manifest.webmanifest",
  "/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      // addAll falla entero si un solo recurso falla; se añaden de una en una.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Datos del sistema: siempre a la red. Si falla, falla.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request, { cache: "no-store" }).catch(
        () =>
          new Response(JSON.stringify({ detail: "Sin conexión con el servidor." }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          })
      )
    );
    return;
  }

  // Interfaz: red primero, caché como último recurso (para abrir el panel
  // aunque el servidor esté caído y así ver el último estado conocido).
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || Response.error()))
  );
});
