/* =========================================================
   Descargador de Video y MP3 — lógica del cliente
   ========================================================= */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    form: $("search-form"),
    input: $("url-input"),
    parseBtn: $("parse-btn"),
    errorBox: $("error-box"),
    skeleton: $("skeleton"),
    result: $("result"),
    thumb: $("thumb"),
    duration: $("duration"),
    title: $("title"),
    uploader: $("uploader"),
    source: $("source"),
    videoFormats: $("video-formats"),
    progressCard: $("progress-card"),
    progressLabel: $("progress-label"),
    progressPct: $("progress-pct"),
    barFill: $("bar-fill"),
    progressFile: $("progress-file"),
    downloadLink: $("download-link"),
    brand: $("brand"),
    ffmpegWarning: $("ffmpeg-warning"),
    ffmpegHint: $("ffmpeg-hint"),
    gallery: $("gallery"),
    galleryTrack: $("gallery-track"),
    galleryDots: $("gallery-dots"),
    galleryPrev: $("gallery-prev"),
    galleryNext: $("gallery-next"),
    privateToggle: $("private-toggle"),
    privateBody: $("private-body"),
    sourceInput: $("source-input"),
    privateBtn: $("private-btn"),
    privateResult: $("private-result"),
  };

  let currentUrl = "";
  let pollTimer = null;
  // Título y miniatura del último análisis, para acompañar a la descarga y que
  // el historial y el carrusel tengan algo que enseñar aunque falle.
  let currentMeta = null;

  /* ---------------- utilidades ---------------- */

  const API = {
    async post(path, body) {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
      return data;
    },
    async get(path) {
      const res = await fetch(path);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
      return data;
    },
  };

  function showError(message) {
    el.errorBox.textContent = message;
    el.errorBox.classList.remove("hidden");
  }

  function clearError() {
    el.errorBox.classList.add("hidden");
    el.errorBox.textContent = "";
  }

  function humanSize(bytes) {
    if (!bytes) return "";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    let n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
  }

  function humanDuration(seconds) {
    if (!seconds && seconds !== 0) return "";
    const s = Math.floor(seconds % 60);
    const m = Math.floor((seconds / 60) % 60);
    const h = Math.floor(seconds / 3600);
    const pad = (v) => String(v).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  /* ---------------- estado del servidor ----------------

     Sin indicador de estado visible: el visitante no ve si el servicio está
     «listo» ni ninguna píldora de salud. Solo se avisa de un problema real que
     le afecte a él (falta de FFmpeg, que impide el MP3), y el resto del estado
     se consulta en el panel de control.
     -------------------------------------------------------- */

  async function checkHealth() {
    try {
      const h = await API.get("/api/health");
      if (!h.ffmpeg?.available) {
        el.ffmpegHint.textContent = " " + (h.ffmpeg?.hint || "");
        el.ffmpegWarning.classList.remove("hidden");
      }
    } catch {
      // Sin conexión con el servidor: no se anuncia nada en la interfaz.
      // Cualquier acción real (analizar o descargar) ya muestra su propio error.
    }
  }

  /* ---------------- analizar enlace ---------------- */

  async function parseUrl(url) {
    clearError();
    el.result.classList.add("hidden");
    el.progressCard.classList.add("hidden");
    el.skeleton.classList.remove("hidden");
    el.parseBtn.disabled = true;
    el.parseBtn.textContent = "Analizando…";

    try {
      const info = await API.post("/api/parse", { url });
      renderResult(info, url);
    } catch (err) {
      showError(err.message);
    } finally {
      el.skeleton.classList.add("hidden");
      el.parseBtn.disabled = false;
      el.parseBtn.textContent = "Analizar";
    }
  }

  function renderResult(info, url) {
    currentUrl = info.webpage_url || url;
    currentMeta = {
      url: currentUrl,        // el que devuelve el motor (normalizado)
      source: url,            // el que pegó el usuario
      title: info.title || null,
      thumbnail: info.thumbnail || null,
    };

    el.thumb.src = info.thumbnail || "";
    el.thumb.alt = info.title ? `Miniatura de ${info.title}` : "Miniatura";
    el.title.textContent = info.title || "Video sin título";
    el.uploader.textContent = [
      info.uploader,
      humanDuration(info.duration),
      info.is_live ? "EN VIVO" : "",
    ].filter(Boolean).join("  ·  ");
    el.source.textContent = info.extractor || "sitio";

    el.videoFormats.innerHTML = "";
    if (!info.video_formats?.length) {
      el.videoFormats.innerHTML = '<span class="muted small">No hay formatos de video disponibles.</span>';
    }
    for (const f of info.video_formats || []) {
      const btn = document.createElement("button");
      btn.className = "chip";
      btn.type = "button";
      btn.dataset.kind = "mp4";
      btn.dataset.formatId = f.format_id;

      const main = document.createElement("span");
      main.className = "chip-main";
      main.textContent = f.label || f.format_id;

      const sub = document.createElement("span");
      sub.className = "chip-sub";
      sub.textContent = f.filesize ? humanSize(f.filesize) : (f.muted ? "requiere FFmpeg" : "con audio");

      btn.append(main, sub);
      btn.addEventListener("click", () => startDownload("mp4", f.format_id, url));
      el.videoFormats.appendChild(btn);
    }

    el.result.classList.remove("hidden");
    el.result.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  /* ---------------- descargar + seguir progreso ---------------- */

  async function startDownload(kind, formatId, url) {
    clearError();
    el.progressCard.classList.remove("hidden");
    el.downloadLink.classList.add("hidden");
    el.progressLabel.textContent = "Encolando…";
    el.progressPct.textContent = "0%";
    el.barFill.style.width = "0%";
    el.progressFile.textContent = "";

    const source = url || currentUrl;
    // Solo se adjunta el título/miniatura si corresponden a este mismo enlace:
    // si el usuario analizó un vídeo y luego pegó otro, no deben mezclarse.
    const meta =
      currentMeta && (currentMeta.url === source || currentMeta.source === source)
        ? currentMeta
        : null;

    try {
      const { job_id } = await API.post("/api/download", {
        url: source,
        kind,
        format_id: formatId || null,
        title: meta?.title || null,
        thumbnail: meta?.thumbnail || null,
      });
      pollJob(job_id, kind);
    } catch (err) {
      el.progressCard.classList.add("hidden");
      showError(err.message);
    }
  }

  function pollJob(jobId, kind) {
    clearInterval(pollTimer);
    let attempts = 0;

    const tick = async () => {
      attempts++;
      if (attempts > 900) { // ~15 min
        clearInterval(pollTimer);
        showError("La descarga tardó demasiado. Intenta de nuevo.");
        el.progressCard.classList.add("hidden");
        return;
      }

      try {
        const job = await API.get(`/api/jobs/${jobId}`);

        if (job.status === "queued") {
          el.progressLabel.textContent = "En cola…";
        } else if (job.status === "processing") {
          el.progressLabel.textContent = kind === "mp3" ? "Descargando y convirtiendo…" : "Descargando…";
          const pct = job.progress || 0;
          el.progressPct.textContent = `${pct.toFixed(0)}%`;
          el.barFill.style.width = `${Math.max(pct, 4)}%`;
          if (job.title) el.progressFile.textContent = job.title;
        } else if (job.status === "done") {
          clearInterval(pollTimer);
          el.progressLabel.textContent = "¡Listo!";
          el.progressPct.textContent = "100%";
          el.barFill.style.width = "100%";
          el.progressFile.textContent = [job.filename, humanSize(job.filesize)]
            .filter(Boolean).join("  ·  ");

          el.downloadLink.href = job.download_url;
          el.downloadLink.setAttribute("download", job.filename || "");
          el.downloadLink.classList.remove("hidden");
          el.downloadLink.click(); // inicia la descarga automáticamente

          // Acaba de entrar una descarga: el carrusel se refresca para incluirla.
          loadGallery();
        } else if (job.status === "error") {
          clearInterval(pollTimer);
          el.progressCard.classList.add("hidden");
          showError(job.error || "La descarga falló.");
        } else {
          clearInterval(pollTimer);
          el.progressCard.classList.add("hidden");
          showError("El trabajo expiró.");
        }
      } catch (err) {
        clearInterval(pollTimer);
        el.progressCard.classList.add("hidden");
        showError(err.message);
      }
    };

    tick();
    pollTimer = setInterval(tick, 900);
  }

  /* ---------------- carrusel de últimas descargas ----------------

     Lo alimenta /api/gallery, que solo expone título, miniatura, plataforma,
     formato y fecha: ni la IP ni el enlace de origen de nadie. Si el archivo
     sigue en el almacén temporal, la tarjeta reproduce el vídeo de verdad en
     lugar de quedarse en una foto; cuando el archivo expira, se queda la foto.
     ------------------------------------------------------------------ */

  function agoShort(ts) {
    if (!ts) return "";
    const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (delta < 60) return "ahora";
    if (delta < 3600) return `hace ${Math.floor(delta / 60)} min`;
    if (delta < 86400) return `hace ${Math.floor(delta / 3600)} h`;
    if (delta < 604800) return `hace ${Math.floor(delta / 86400)} d`;
    return "hace semanas";
  }

  // Sin miniatura (pasa con audios y con plataformas que no la dan) la tarjeta
  // no puede quedarse en un hueco vacío: se dibuja una pieza deliberada con el
  // icono del tipo y la plataforma, para que no parezca una imagen rota.
  function fallbackTile(item) {
    const box = document.createElement("span");
    box.className = "gal-fallback";

    const icon = document.createElement("span");
    icon.className = "gal-fallback-icon";
    icon.textContent = item.kind === "mp3" ? "♪" : "▶";

    const host = document.createElement("span");
    host.className = "gal-fallback-host";
    host.textContent = item.host || "";

    box.append(icon, host);
    return box;
  }

  function mediaNode(item) {
    const wrap = document.createElement("div");
    wrap.className = "gal-media";

    let media = null;

    if (item.preview_url && item.preview_kind === "video") {
      // Archivo todavía en el servidor: se puede reproducir, no solo mirar.
      media = document.createElement("video");
      media.src = item.preview_url;
      media.muted = true;
      media.loop = true;
      media.playsInline = true;
      media.preload = "none";
      media.setAttribute("playsinline", "");
      media.setAttribute("muted", "");
      if (item.thumbnail) media.poster = item.thumbnail;

      media.addEventListener("pointerenter", () => media.play().catch(() => {}));
      media.addEventListener("pointerleave", () => media.pause());
      media.addEventListener("click", () => {
        if (media.paused) media.play().catch(() => {});
        else media.pause();
      });
    } else if (item.thumbnail) {
      media = document.createElement("img");
      media.src = item.thumbnail;
      media.alt = "";
      media.loading = "lazy";
      // Sin referrer: el CDN de la miniatura no necesita saber desde qué
      // página se está viendo.
      media.referrerPolicy = "no-referrer";
      media.addEventListener("error", () => {
        media.replaceWith(fallbackTile(item));
      });
    }

    wrap.appendChild(media || fallbackTile(item));

    const flag = document.createElement("span");
    flag.className = "gal-flag";
    flag.textContent = (item.kind || "").toUpperCase();
    wrap.appendChild(flag);

    // Marca de reproducción: es un vídeo, aunque ahora solo se vea la foto.
    if (item.kind === "mp4") {
      const play = document.createElement("span");
      play.className = "gal-play";
      play.textContent = "▶";
      wrap.appendChild(play);
    }

    return wrap;
  }

  function cardStep() {
    const first = el.galleryTrack.querySelector(".gal-card");
    if (!first) return 180;
    return first.getBoundingClientRect().width + 10; // ancho + gap
  }

  function updateGalleryNav() {
    const track = el.galleryTrack;
    const limit = track.scrollWidth - track.clientWidth - 1;

    // Si todo cabe, las flechas no sirven para nada: mejor no enseñarlas que
    // dejar dos botones muertos que parecen rotos.
    const nav = el.gallery.querySelector(".gallery-nav");
    if (nav) nav.classList.toggle("hidden", limit <= 2);

    el.galleryPrev.disabled = track.scrollLeft <= 0;
    el.galleryNext.disabled = track.scrollLeft >= limit;

    const index = Math.round(track.scrollLeft / cardStep());
    [...el.galleryDots.children].forEach((dot, position) => {
      dot.classList.toggle("on", position === index);
    });
  }

  function renderGallery(data) {
    const items = (data.items || []).filter((item) => item.title);
    const minimum = data.min_items || 2;

    // Con muy poco material el carrusel se ve roto: mejor no enseñarlo.
    if (!data.enabled || items.length < minimum) {
      el.gallery.classList.add("hidden");
      return;
    }

    el.galleryTrack.innerHTML = "";
    el.galleryDots.innerHTML = "";

    for (const item of items) {
      const card = document.createElement("article");
      card.className = "gal-card";
      card.appendChild(mediaNode(item));

      const body = document.createElement("div");
      body.className = "gal-body";

      const title = document.createElement("p");
      title.className = "gal-title";
      title.textContent = item.title;
      title.title = item.title;

      const meta = document.createElement("p");
      meta.className = "gal-meta";
      meta.textContent = [item.host, agoShort(item.at)].filter(Boolean).join("  ·  ");

      body.append(title, meta);
      card.appendChild(body);
      el.galleryTrack.appendChild(card);

      el.galleryDots.appendChild(document.createElement("span"));
    }

    el.gallery.classList.remove("hidden");
    // El ancho real solo se conoce tras pintarlo.
    requestAnimationFrame(updateGalleryNav);
  }

  async function loadGallery() {
    try {
      const data = await API.get("/api/gallery?limit=12");
      renderGallery(data);
    } catch {
      el.gallery.classList.add("hidden");
    }
  }

  /* ---------------- video privado de Facebook ---------------- */

  async function extractPrivate() {
    const html = el.sourceInput.value.trim();
    if (html.length < 200) {
      el.privateResult.innerHTML = '<span class="alert alert-error">Pega el código fuente completo de la página.</span>';
      return;
    }

    el.privateBtn.disabled = true;
    el.privateBtn.textContent = "Extrayendo…";
    el.privateResult.innerHTML = "";

    try {
      const data = await API.post("/api/facebook/private", { html });
      el.privateResult.innerHTML = "";

      if (data.title) {
        const t = document.createElement("p");
        t.className = "muted small";
        t.textContent = `${data.title}${data.duration ? `  ·  ${humanDuration(data.duration)}` : ""}`;
        el.privateResult.appendChild(t);
      }

      for (const item of data.urls) {
        const a = document.createElement("a");
        a.href = item.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.setAttribute("download", "");
        a.innerHTML = `<span>Descargar video</span><span class="quality">${item.quality || ""}</span>`;
        el.privateResult.appendChild(a);
      }
    } catch (err) {
      el.privateResult.innerHTML = "";
      const p = document.createElement("span");
      p.className = "alert alert-error";
      p.textContent = err.message;
      el.privateResult.appendChild(p);
    } finally {
      el.privateBtn.disabled = false;
      el.privateBtn.textContent = "Extraer enlaces";
    }
  }

  /* ---------------- eventos ---------------- */

  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const url = el.input.value.trim();
    if (url) parseUrl(url);
  });

  document.querySelectorAll("[data-host]").forEach((btn) => {
    btn.addEventListener("click", () => {
      el.input.value = `https://www.${btn.dataset.host}/`;
      el.input.focus();
    });
  });

  document.querySelectorAll('[data-kind="mp3"]').forEach((btn) => {
    btn.addEventListener("click", () => startDownload("mp3", null, currentUrl));
  });

  el.privateToggle.addEventListener("click", () => {
    const expanded = el.privateToggle.getAttribute("aria-expanded") === "true";
    el.privateToggle.setAttribute("aria-expanded", String(!expanded));
    el.privateBody.classList.toggle("hidden", expanded);
  });

  el.privateBtn.addEventListener("click", extractPrivate);

  /* ---------------- carrusel: controles ---------------- */

  el.galleryPrev.addEventListener("click", () =>
    el.galleryTrack.scrollBy({ left: -cardStep(), behavior: "smooth" })
  );
  el.galleryNext.addEventListener("click", () =>
    el.galleryTrack.scrollBy({ left: cardStep(), behavior: "smooth" })
  );

  // Un solo recálculo por fotograma: el evento scroll dispara en ráfaga.
  let galleryFrame = null;
  el.galleryTrack.addEventListener("scroll", () => {
    if (galleryFrame) return;
    galleryFrame = requestAnimationFrame(() => {
      galleryFrame = null;
      updateGalleryNav();
    });
  });

  // Al girar el móvil o cambiar el ancho, el carrusel cabe o no cabe: las
  // flechas tienen que aparecer o desaparecer con él.
  let galleryResize = null;
  window.addEventListener("resize", () => {
    clearTimeout(galleryResize);
    galleryResize = setTimeout(updateGalleryNav, 150);
  });

  el.galleryTrack.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      el.galleryNext.click();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      el.galleryPrev.click();
    }
  });

  // Pausa los vídeos del carrusel cuando la pestaña queda en segundo plano:
  // un vídeo en bucle consumiendo batería a escondidas es lo peor del mundo.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) return;
    el.galleryTrack.querySelectorAll("video").forEach((v) => v.pause());
  });

  /* ---------------- acceso invisible al panel ----------------

     El panel de control (/monitor) no aparece en ningún menú, botón ni pie de
     página: no hay ninguna pista visual de que exista. La entrada es esta,
     discreta:

       · Cinco toques seguidos sobre el logotipo «Downloader» (ratón o dedo).
       · O el atajo Ctrl + Alt + M en el teclado.
       · O escribiendo /monitor a mano en la barra de direcciones.

     Cambia ACCESS_TAPS si quieres una combinación más larga.
     ----------------------------------------------------------- */

  const ACCESS_TAPS = 5;
  const ACCESS_WINDOW_MS = 2500;

  function installHiddenAccess() {
    let taps = 0;
    let lastTap = 0;

    if (el.brand) {
      el.brand.addEventListener("click", () => {
        const now = Date.now();
        taps = now - lastTap > ACCESS_WINDOW_MS ? 1 : taps + 1;
        lastTap = now;

        if (taps >= ACCESS_TAPS) {
          taps = 0;
          window.location.href = "/monitor";
        }
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.altKey && !e.shiftKey && e.key.toLowerCase() === "m") {
        e.preventDefault();
        window.location.href = "/monitor";
      }
    });
  }

  /* ---------------- arranque ---------------- */
  installHiddenAccess();
  checkHealth();
  setInterval(checkHealth, 60000);
  loadGallery();
})();
