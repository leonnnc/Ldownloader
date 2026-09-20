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
    token: $("token"),
    actionResult: $("action-result"),
    hostLabel: $("host-label"),
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

    if (!token()) {
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
        showResult(`Error ${res.status}\n\n${data.detail || JSON.stringify(data, null, 2)}`);
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

  el.token.value = localStorage.getItem(TOKEN_KEY) || "";
  el.token.addEventListener("input", () => {
    localStorage.setItem(TOKEN_KEY, token());
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
