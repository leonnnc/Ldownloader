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
    healthBadge: $("health-badge"),
    ffmpegWarning: $("ffmpeg-warning"),
    ffmpegHint: $("ffmpeg-hint"),
    privateToggle: $("private-toggle"),
    privateBody: $("private-body"),
    sourceInput: $("source-input"),
    privateBtn: $("private-btn"),
    privateResult: $("private-result"),
  };

  let currentUrl = "";
  let pollTimer = null;

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

  /* ---------------- estado del servidor ---------------- */

  async function checkHealth() {
    try {
      const h = await API.get("/api/health");
      if (h.ffmpeg?.available) {
        el.healthBadge.textContent = "listo";
        el.healthBadge.className = "badge ok";
      } else {
        el.healthBadge.textContent = "sin FFmpeg";
        el.healthBadge.className = "badge warn";
        el.ffmpegHint.textContent = " " + (h.ffmpeg?.hint || "");
        el.ffmpegWarning.classList.remove("hidden");
      }
    } catch {
      el.healthBadge.textContent = "servidor no disponible";
      el.healthBadge.className = "badge warn";
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

    try {
      const { job_id } = await API.post("/api/download", {
        url: url || currentUrl,
        kind,
        format_id: formatId || null,
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

  /* ---------------- arranque ---------------- */
  checkHealth();
  setInterval(checkHealth, 60000);
})();
