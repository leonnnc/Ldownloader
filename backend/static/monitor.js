/* =========================================================
   Panel de control — consulta el estado y ejecuta el
   restablecimiento del sistema.
   ========================================================= */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const REFRESH_MS = 15000;
  const TOKEN_KEY = "vdl_admin_token";

  const ACTIONS = {
    restart: { path: "/api/admin/restart", confirm: "¿Reiniciar el servicio ahora?" },
    reset_circuits: { path: "/api/admin/circuits/reset" },
    run_canaries: { path: "/api/admin/canaries/run" },
    purge: { path: "/api/admin/storage/purge" },
    alerts_clear: { path: "/api/admin/alerts/clear" },
    history_clear: {
      path: "/api/admin/history/clear",
      confirm: "¿Borrar todo el historial de descargas? No se puede deshacer.",
      // Misma regla que para leerlo: si el servidor no tiene token configurado
      // el listado ya es público, así que bloquear el borrado no protegería
      // nada y dejaría el botón inservible en local.
      optionalToken: true,
    },
  };

  // Cómo se llama cada estado en la lista. Los tres primeros son el ciclo del
  // trabajo; «expirado» aparece si el archivo se borró antes de terminar.
  const STATE_LABELS = {
    queued: "en cola",
    processing: "descargando",
    done: "ok",
    error: "error",
    expired: "expirado",
  };

  const el = {
    liveDot: $("live-dot"),
    updated: $("updated"),
    refreshBtn: $("refresh-btn"),
    statusCard: $("status-card"),
    statusPill: $("status-pill"),
    statusRate: $("status-rate"),
    statusLabel: $("status-label"),
    reasons: $("reasons"),
    advice: $("advice"),
    adviceReason: $("advice-reason"),
    adviceRestart: $("advice-restart"),
    metrics: {
      rate: $("m-rate"),
      circuits: $("m-circuits"),
      jobs: $("m-jobs"),
      engine: $("m-engine"),
      check: $("m-check"),
      ffmpeg: $("m-ffmpeg"),
    },
    platforms: $("platforms"),
    alerts: $("alerts"),
    history: $("history"),
    histSummary: $("hist-summary"),
    histNote: $("hist-note"),
    histFilter: $("hist-filter"),
    histRefresh: $("hist-refresh"),
    token: $("token"),
    actionResult: $("action-result"),
    hostLabel: $("host-label"),
    pairBadge: $("pair-badge"),
    pairText: $("pair-text"),
    pairExtra: $("pair-extra"),
    pairUrl: $("pair-url"),
    pairToken: $("pair-token"),
    pairWarning: $("pair-warning"),
    pairOpen: $("pair-open"),
    pairOpenNote: $("pair-open-note"),
    copyUrl: $("copy-url"),
    copyToken: $("copy-token"),
  };

  let timer = null;
  let failures = 0;

  /* ---------------- utilidades ---------------- */

  function ago(ts) {
    if (!ts) return "—";
    const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (delta < 45) return "ahora";
    if (delta < 3600) return `hace ${Math.floor(delta / 60)}m`;
    if (delta < 86400) return `hace ${Math.floor(delta / 3600)}h`;
    return `hace ${Math.floor(delta / 86400)}d`;
  }

  function setValue(node, text, level) {
    node.textContent = text;
    node.className = "metric-value" + (level ? ` ${level}` : "");
  }

  function empty(node, text) {
    node.innerHTML = "";
    const p = document.createElement("p");
    p.className = "muted small";
    p.textContent = text;
    node.appendChild(p);
  }

  function token() {
    return el.token.value.trim();
  }

  function when(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    const now = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    if (d.toDateString() === now.toDateString()) {
      return `${hh}:${mm}:${String(d.getSeconds()).padStart(2, "0")}`;
    }
    const day = String(d.getDate()).padStart(2, "0");
    const month = String(d.getMonth() + 1).padStart(2, "0");
    return `${day}/${month} ${hh}:${mm}`;
  }

  function size(bytes) {
    if (!bytes) return null;
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    let value = bytes;
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i++;
    }
    return `${value.toFixed(value < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
  }

  // El enlace completo puede medir 400 caracteres; se muestra el final, que es
  // la parte que identifica el video, y el título del enlace lleva el resto.
  function shortUrl(url) {
    if (!url) return "—";
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.replace(/^www\./, "");
      const tail = parsed.pathname + parsed.search;
      return host + (tail.length > 46 ? `${tail.slice(0, 44)}…` : tail);
    } catch (err) {
      return url.length > 60 ? `${url.slice(0, 58)}…` : url;
    }
  }

  /* ---------------- render ---------------- */

  function renderStatus(data) {
    el.statusCard.className = `status-card ${data.status}`;
    el.statusPill.textContent = data.status;
    el.statusRate.textContent = data.success_rate_label;
    el.statusLabel.textContent = data.label;

    el.reasons.innerHTML = "";
    for (const reason of data.reasons || []) {
      const li = document.createElement("li");
      li.textContent = reason;
      el.reasons.appendChild(li);
    }

    // Aviso de reinicio
    if (data.restart_advised && data.restart_reasons?.length) {
      el.adviceReason.textContent = data.restart_reasons.join(" · ");
      el.advice.classList.remove("hidden");
    } else {
      el.advice.classList.add("hidden");
    }

    // Métricas
    // Con pocas muestras la tasa informa pero no colorea: un solo enlace roto
    // no significa que el servicio esté mal.
    const rate = data.success_rate_trusted ? data.success_rate : null;
    setValue(
      el.metrics.rate,
      data.success_rate_label,
      rate === null ? null : rate >= 0.9 ? "ok" : rate >= 0.6 ? "warn" : "down"
    );
    setValue(
      el.metrics.circuits,
      String(data.circuits_open),
      data.circuits_open === 0 ? "ok" : data.circuits_open >= 3 ? "down" : "warn"
    );
    setValue(el.metrics.jobs, String(data.active_jobs));
    setValue(el.metrics.engine, data.engine?.installed || "—");
    setValue(el.metrics.check, ago(data.last_canary_run));
    setValue(el.metrics.ffmpeg, data.ffmpeg ? "OK" : "falta", data.ffmpeg ? "ok" : "down");
  }

  function renderPlatforms(metrics) {
    const list = metrics?.platforms || [];
    if (!list.length) {
      empty(el.platforms, "Sin datos todavía. Usa el servicio o ejecuta los canarios.");
      return;
    }

    el.platforms.innerHTML = "";
    for (const p of list) {
      const row = document.createElement("div");
      row.className = "platform-row";

      const name = document.createElement("span");
      name.className = "platform-name";
      const badge = document.createElement("span");
      badge.className = `platform-badge ${p.state}`;
      badge.textContent = p.state.replace("_", " ");
      name.append(badge, document.createTextNode(p.platform));

      const rate = document.createElement("span");
      rate.className = "platform-rate";
      // El backend envía success y failure por separado; el total se calcula
      // aquí. Antes se leía p.total, que no existe, y salía "(1/undefined)".
      const total = p.total ?? (p.success ?? 0) + (p.failure ?? 0);
      rate.textContent = `${Math.round((p.success_rate ?? 0) * 100)}%  (${p.success}/${total})`;

      row.append(name, rate);
      el.platforms.appendChild(row);
    }
  }

  function renderAlerts(alerts) {
    if (!alerts?.length) {
      empty(el.alerts, "Sin alertas registradas.");
      return;
    }

    el.alerts.innerHTML = "";
    for (const a of alerts) {
      const row = document.createElement("div");
      row.className = `alert-row ${a.level}`;

      const title = document.createElement("div");
      title.className = "alert-title";
      title.textContent = a.title;

      const meta = document.createElement("div");
      meta.className = "alert-meta";
      meta.textContent = `${ago(a.at)} · ${a.message}`;

      row.append(title, meta);
      el.alerts.appendChild(row);
    }
  }

  /* ---------------- historial de descargas ----------------

     Se trae una vez por refresco y se filtra en el navegador: el filtro debe
     responder al instante, y pedir al servidor en cada tecla dejaría el panel
     parpadeando. El servidor también sabe filtrar, por si algún día el
     historial crece y conviene pedir menos.
     -------------------------------------------------------- */

  let histEntries = [];

  function chip(text, level) {
    const span = document.createElement("span");
    span.className = "hist-chip" + (level ? ` ${level}` : "");
    span.textContent = text;
    return span;
  }

  function renderHistory(data) {
    const stats = data.stats || {};
    histEntries = data.entries || [];

    el.histSummary.innerHTML = "";
    el.histSummary.append(
      chip(`${stats.total ?? 0} descargas`),
      chip(`${stats.ok ?? 0} correctas`, "ok"),
      chip(`${stats.error ?? 0} con error`, stats.error ? "down" : null),
      chip(`últimas 24 h: ${stats.ultimas_24h ?? 0}`),
      chip(`se conservan ${stats.max ?? "—"}`)
    );

    // Dos avisos que conviene tener delante: si está desactivado y, sobre todo,
    // si no hay token, porque entonces cualquiera que abra el panel lo lee.
    const note = [];
    if (!data.enabled) {
      note.push("Historial desactivado en el servidor (VDL_HISTORY_ENABLED=false).");
    }
    note.push(
      data.protected
        ? "Protegido con el token de administración."
        : "SIN TOKEN: cualquiera que abra el panel puede leer este historial. Configura VDL_ADMIN_TOKEN antes de exponer el servidor."
    );
    if (stats.por_host?.length) {
      note.push(
        "Más usados: " +
          stats.por_host.slice(0, 3).map(([h, n]) => `${h} (${n})`).join(", ") + "."
      );
    }
    note.push("La IP es la que declara el cliente: con X-Forwarded-For puede venir falsificada.");
    el.histNote.textContent = note.join("  ·  ");
    el.histNote.className = data.protected ? "muted small" : "hist-note-warn";

    renderHistoryList();
  }

  function visibleEntries() {
    const needle = (el.histFilter.value || "").trim().toLowerCase();
    if (!needle) return histEntries;
    return histEntries.filter((entry) =>
      [entry.ip, entry.host, entry.url, entry.title, entry.kind, entry.status, entry.error]
        .map((value) => String(value || "").toLowerCase())
        .join(" ")
        .includes(needle)
    );
  }

  function renderHistoryList() {
    const list = visibleEntries();
    el.history.innerHTML = "";

    if (!list.length) {
      empty(
        el.history,
        histEntries.length
          ? "Ninguna entrada coincide con el filtro."
          : "Sin descargas registradas todavía. Aparecerán aquí en cuanto alguien use el descargador."
      );
      return;
    }

    for (const entry of list) {
      const item = document.createElement("article");
      item.className = "hist-item";
      if (entry.status === "error") item.classList.add("error");
      if (entry.status === "queued" || entry.status === "processing") {
        item.classList.add("pending");
      }

      const top = document.createElement("div");
      top.className = "hist-top";

      const time = document.createElement("span");
      time.className = "hist-when";
      time.textContent = when(entry.at);

      const ip = document.createElement("span");
      ip.className = "hist-ip";
      ip.textContent = entry.ip || "—";

      const kind = document.createElement("span");
      kind.className = "hist-flag";
      kind.textContent = (entry.kind || "").toUpperCase();

      const state = document.createElement("span");
      state.className = `hist-flag ${entry.status || ""}`;
      state.textContent = STATE_LABELS[entry.status] || entry.status || "—";

      top.append(time, ip, kind, state);
      item.appendChild(top);

      if (entry.title) {
        const title = document.createElement("p");
        title.className = "hist-title";
        title.textContent = entry.title;
        item.appendChild(title);
      }

      if (entry.error) {
        const error = document.createElement("p");
        error.className = "hist-error";
        error.textContent = entry.error;
        item.appendChild(error);
      }

      // El enlace descargado, tal cual: abrible y copiable.
      if (entry.url) {
        const row = document.createElement("div");
        row.className = "hist-link";

        const link = document.createElement("a");
        link.href = entry.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = shortUrl(entry.url);
        link.title = entry.url;

        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "btn btn-mini";
        copy.textContent = "Copiar";
        copy.addEventListener("click", () => copyToClipboard(entry.url, copy));

        row.append(link, copy);
        item.appendChild(row);
      }

      const meta = document.createElement("p");
      meta.className = "hist-meta";
      meta.textContent = [
        entry.host,
        size(entry.filesize),
        entry.elapsed != null ? `${entry.elapsed}s` : null,
        entry.format_id ? `formato ${entry.format_id}` : null,
      ]
        .filter(Boolean)
        .join("  ·  ");
      item.appendChild(meta);

      el.history.appendChild(item);
    }
  }

  async function loadHistory() {
    try {
      const headers = {};
      const value = token();
      if (value) headers["X-Admin-Token"] = value;

      const res = await fetch("/api/history?limit=100", { headers, cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      renderHistory(await res.json());
    } catch (err) {
      histEntries = [];
      el.histSummary.innerHTML = "";
      el.histNote.className = "hist-note-warn";
      el.histNote.textContent =
        err.message === "HTTP 401"
          ? "El servidor exige el token de administración: escríbelo en «Conexión con la app». Con el campo vacío, este listado responde 401."
          : "Sin conexión con el servidor.";
      empty(el.history, `No se pudo consultar el historial (${err.message}).`);
    }
  }

  /* ---------------- conexión con la app ---------------- */

  // Estado del enlace. Se guarda para poder copiarlo sin volver a pedirlo.
  let pairing = null;

  function setBadge(text, level) {
    el.pairBadge.textContent = text;
    el.pairBadge.className = "pair-badge" + (level ? ` ${level}` : "");
  }

  function renderPairing(data) {
    pairing = data;

    const url = data.preferred_url;
    el.pairUrl.textContent = url || "No se detectó ninguna dirección";
    el.copyUrl.disabled = !url;

    // El token completo solo llega si el que pregunta ha demostrado el suyo.
    if (data.token) {
      el.pairToken.textContent = data.token;
      el.copyToken.disabled = false;
    } else if (data.token_configured) {
      el.pairToken.textContent = `${data.token_masked}  (escrito arriba)`;
      el.copyToken.disabled = true;
    } else {
      el.pairToken.textContent = "sin configurar en el servidor";
      el.copyToken.disabled = true;
    }

    // Enlace directo: solo tiene sentido desde el propio móvil, donde el
    // sistema sabe abrir un vdl:// con la app.
    if (data.pairing_link) {
      el.pairOpen.href = data.pairing_link;
      el.pairOpen.classList.remove("hidden");
      el.pairOpenNote.textContent =
        "Pulsa ese botón desde el móvil y el widget queda configurado sin " +
        "escribir nada. Desde un ordenador no hace nada.";
    } else {
      el.pairOpen.classList.add("hidden");
      el.pairOpenNote.textContent =
        "En el widget del móvil: pantalla «Monitor del sistema» → pega el " +
        "enlace y el token → Guardar.";
    }

    el.pairExtra.classList.remove("hidden");
    el.pairWarning.textContent = data.warning || "";

    // Presencia de la app: distingue «nunca se configuró» de «se movió la IP».
    const app = data.app || {};
    if (app.seen) {
      setBadge("app conectada", "ok");
      const version = app.version ? ` (v${app.version})` : "";
      el.pairText.textContent =
        `La app habló con el servidor ${app.ago} desde ${app.ip}${version}.`;
    } else {
      setBadge("sin contacto", "unknown");
      el.pairText.textContent =
        "La app todavía no ha contactado con este servidor. Si ya la " +
        "configuraste, el enlace o el token no son los correctos.";
    }
  }

  function renderPairingError(message) {
    pairing = null;
    setBadge("sin datos", "warn");
    el.pairText.textContent = message;
    el.pairExtra.classList.add("hidden");
  }

  async function loadPairing() {
    try {
      const headers = {};
      const value = token();
      if (value) headers["X-Admin-Token"] = value;

      const res = await fetch("/api/pairing", { headers, cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      renderPairing(await res.json());
    } catch (err) {
      renderPairingError(`No se pudo consultar la conexión: ${err.message}`);
    }
  }

  async function copyToClipboard(text, button) {
    if (!text) return;
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copiado";
    } catch (err) {
      // Sin HTTPS el portapapeles puede no estar disponible.
      button.textContent = "Selecciona y copia";
    }
    setTimeout(() => (button.textContent = original), 2000);
  }

  /* ---------------- carga ---------------- */

  async function load(showSpinner = false) {
    if (showSpinner) el.refreshBtn.classList.add("busy");
    try {
      const res = await fetch("/api/monitor", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      failures = 0;
      renderStatus(data);
      renderPlatforms(data.metrics);
      renderAlerts(data.alerts);
      // El enlace depende de cómo se esté llamando al monitor, así que se
      // recalcula en cada refresco y no solo al arrancar.
      loadPairing();
      loadHistory();

      el.liveDot.className = `dot ${data.status}`;
      el.updated.textContent = ago(Math.floor(Date.now() / 1000));
    } catch (err) {
      failures++;
      el.liveDot.className = "dot offline";
      el.statusCard.className = "status-card caido";
      el.statusPill.textContent = "sin conexión";
      el.statusLabel.textContent = "No se pudo contactar con el servidor";
      el.statusRate.textContent = "—";
      el.reasons.innerHTML = "";
      const li = document.createElement("li");
      li.textContent =
        failures > 2
          ? "El servidor no responde. Si está caído, el reinicio desde aquí no funcionará: tendrás que hacerlo desde el propio servidor."
          : "Reintentando automáticamente…";
      el.reasons.appendChild(li);
      el.advice.classList.add("hidden");
    } finally {
      el.refreshBtn.classList.remove("busy");
    }
  }

  /* ---------------- acciones ---------------- */

  async function runAction(name) {
    const action = ACTIONS[name];
    if (!action) return;

    if (action.confirm && !window.confirm(action.confirm)) return;

    if (!token() && !action.optionalToken) {
      showResult("Falta el token de administración. Escríbelo arriba.\n\n" +
        "Si no lo has definido, activa los endpoints de administración con\n" +
        "VDL_ADMIN_TOKEN en la configuración del servidor.");
      return;
    }

    const buttons = document.querySelectorAll("[data-action]");
    buttons.forEach((b) => (b.disabled = true));
    showResult(`Ejecutando ${name}…`);

    try {
      const res = await fetch(action.path, {
        method: "POST",
        headers: { "X-Admin-Token": token() },
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        const hint =
          res.status === 401
            ? "\n\nEl servidor exige el token de administración: escríbelo en «Conexión con la app»."
            : "";
        showResult(
          `Error ${res.status}\n\n${data.detail || JSON.stringify(data, null, 2)}${hint}`
        );
        return;
      }

      showResult(JSON.stringify(data, null, 2));

      // Tras reiniciar, el servidor deja de responder un momento.
      if (name === "restart") {
        el.liveDot.className = "dot offline";
        setTimeout(() => load(true), 6000);
      } else {
        setTimeout(() => load(true), 1200);
      }
    } catch (err) {
      showResult(`No se pudo completar: ${err.message}`);
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  function showResult(text) {
    el.actionResult.textContent = text;
    el.actionResult.classList.remove("hidden");
  }

  /* ---------------- eventos ---------------- */

  el.refreshBtn.addEventListener("click", () => load(true));

  el.adviceRestart.addEventListener("click", () => {
    el.statusCard.scrollIntoView({ behavior: "smooth", block: "start" });
    document.querySelector('[data-action="restart"]')?.focus();
    runAction("restart");
  });

  document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => runAction(btn.dataset.action));
  });

  el.copyUrl.addEventListener("click", () =>
    copyToClipboard(pairing?.preferred_url, el.copyUrl)
  );
  el.copyToken.addEventListener("click", () =>
    copyToClipboard(pairing?.token, el.copyToken)
  );

  el.histRefresh.addEventListener("click", () => loadHistory());
  // Filtrado local: se repinta sin volver a pedir nada al servidor.
  el.histFilter.addEventListener("input", renderHistoryList);

  // Al escribir el token hay que volver a pedir el enlace: es lo que autoriza
  // que el servidor lo entregue completo.
  let tokenTimer = null;
  el.token.value = localStorage.getItem(TOKEN_KEY) || "";
  el.token.addEventListener("input", () => {
    localStorage.setItem(TOKEN_KEY, token());
    clearTimeout(tokenTimer);
    tokenTimer = setTimeout(() => {
      loadPairing();
      // El historial también depende del token: sin él responde 401.
      loadHistory();
    }, 600);
  });

  // Al volver a la pestaña, refresca de inmediato.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) load(true);
  });

  /* ---------------- arranque ---------------- */

  el.hostLabel.textContent = location.host;
  load(true);

  // Refresco periódico, pausado cuando la pestaña está oculta.
  timer = setInterval(() => {
    if (!document.hidden) load();
  }, REFRESH_MS);

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* Sin HTTPS el registro falla: el panel sigue funcionando igual. */
      });
    });
  }
})();
