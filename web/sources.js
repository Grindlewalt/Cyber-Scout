"use strict";
// sources.js: feature plugin (see docs/PLAN_UNIVERSAL.md) -- video sources, broadcast layout box editor and the
// camera calibration wizard.  Route #/<ev>/sources[/sources|layout|calib][/<source id | target>] (admin).
// Talks only to the API (server/routers/sources.py, /app/v1/events/<ev>/...); heavy steps are jobs it polls.
(function () {
  CS.nav({ label: "Video", key: "sources", href: ev => `#/${ev}/sources`, order: 80, group: "manage", icon: "video", roles: ["admin"] });

  const KCOL = { field: "#2dd4ee", overhead: "#a78bfa", station_red: "#ff5a5f", station_blue: "#3e8bff", clock: "#51cf66",
                 scoreboard: "#ffd43b", mask: "#ff922b", inset: "#cc5de8" };
  const KSHORT = { field: "Field area", overhead: "Overhead picture", station_red: "Red station cam", station_blue: "Blue station cam",
                   clock: "Clock", scoreboard: "Score bar", mask: "Mask", inset: "Other inset" };
  const ROLE_TIP = { wide: "fixed overhead / wide field camera (what the tracker needs)",
                     dynamic: "switching broadcast program (cuts between cameras; may carry station-cam insets)",
                     "alliance-side": "fixed driver-station cameras" };
  const TARGETS = [["overhead", "Overhead camera"], ["station_red", "Red station cam"], ["station_blue", "Blue station cam"]];
  const TABS = [["sources", "Sources"], ["layout", "Layout"], ["calib", "Calibration"]];
  const h = s => esc(s);
  const when = ts => ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "–";
  // security audit: only http(s) URLs are links; file paths, javascript:, data: etc. are plain escaped text
  const urlHtml = (u, code) => { const ok = CS.safeHref ? CS.safeHref(u) : (/^https?:\/\//i.test(u || "") ? u : null);
    return ok ? `<a href="${h(ok)}" target="_blank" rel="noopener noreferrer">${code ? `<code>${h(u)}</code>` : h(u)}</a>` : code ? `<code>${h(u)}</code>` : `<span>${h(u)}</span>`; };

  // ---------------------------------------------------------------- API (CS.app from setup.js when present)
  function cookie(n) { const m = document.cookie.match(new RegExp("(?:^|; )" + n + "=([^;]*)")); return m ? decodeURIComponent(m[1]) : ""; }
  async function A(method, path, body) {
    if (CS.app) return CS.app(method, path, body);
    const opt = { method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    if (method !== "GET") opt.headers["X-CSRF-Token"] = cookie("cs_csrf");
    const r = await fetch("app/v1/" + path, opt);
    const j = r.status === 204 ? null : await r.json().catch(() => null);
    if (!r.ok) { const e = new Error((j && (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail))) || r.statusText); e.status = r.status; e.data = j; throw e; }
    return j;
  }
  const errMsg = e => (e && (e.message || (e.data && e.data.detail))) || String(e);
  async function job(ev, start, status) {
    let j = await start;
    const t0 = Date.now();
    while (j.state === "queued" || j.state === "running") {
      if (status) status(j.state === "queued" ? (j.hint || "queued…") : `working… ${Math.round((Date.now() - t0) / 1000)} s`);
      await new Promise(r => setTimeout(r, 800));
      j = await A("GET", `events/${ev}/jobs/${j.id}`);
    }
    if (j.state === "error") throw new Error(j.error || "job failed");
    if (status) status("");
    return j.result;
  }
  const imgUrl = (p) => `app/v1/${p}?ts=${Date.now()}`;
  function loadImg(src) {
    return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error("no frame yet")); im.src = src; });
  }

  // ---------------------------------------------------------------- livestream recorder status
  // GET events/<ev>/livestream (signed in; server/routers/sources.py), else event.json "livestream" (small summary written by the
  // worker, may be minutes old). Shown as a card on the Sources tab and as one quiet line on the Dashboard (dash.js) while live.
  const LS_LABEL = { recording: "Recording", waiting: "Waiting for the stream", starting: "Starting", stream_over: "Stream over", idle: "Idle",
                     error: "Error", no_source: "No livestream source", disabled: "Off", stopped: "Stopped", not_running: "Not running" };
  const LS_ACTIVE = ["recording", "waiting", "starting", "error"];
  const dur = x => x == null ? "–" : x < 90 ? `${Math.round(x)} s` : x < 5400 ? `${Math.round(x / 60)} min` : `${(x / 3600).toFixed(1)} h`;
  const gb = x => x == null ? "–" : x >= 100 ? `${Math.round(x)} GB` : `${Number(x).toFixed(1)} GB`;
  const lsScore = sc => sc && sc.red != null && sc.blue != null ? `<span class="team r">${h(sc.red)}</span>–<span class="team b">${h(sc.blue)}</span>` : "";
  const lsMatch = m => m ? `${h((m.key && shortKey(m.key)) || m.name || m.id || "?")}${lsScore(m.score) ? " " + lsScore(m.score) : ""}` : "";
  const liveRec = {
    async load(ev, evj) {
      let st = null;
      if (CS.platform && CS.user) { try { st = await A("GET", `events/${ev}/livestream`); } catch (e) { st = null; } }
      if (!st || ((st.state === "no_source" || st.state === "not_running") && !st.updated)) {
        let s = evj ? evj.livestream : undefined;
        if (s === undefined) { try { s = (await api(`${ev}/event.json`)).livestream; } catch (e) { s = null; } }
        if (s && s.state) {
          const age = s.updated ? Date.now() / 1000 - s.updated : null;
          st = { ...s, age_s: age == null ? null : Math.round(age), summary: true, stale: age != null && age > 600 && LS_ACTIVE.includes(s.state) };
        }
      }
      return st;
    },
    /** recorder is doing something worth a line on the Dashboard (recording / waiting / error / stopped reporting) */
    active: st => !!st && LS_ACTIVE.includes(st.state),
    warn(st, short) {
      if (!st) return "";
      if (st.stale && LS_ACTIVE.includes(st.state)) return st.message && !st.summary && !short ? st.message : `recorder not reporting for ${dur(st.age_s)}`;
      return "";
    },
    dot: st => st.state === "error" ? "bad" : liveRec.warn(st) ? "warn" : st.state === "recording" ? "on" : "",
    /** one quiet line (Dashboard) */
    line(st, ev) {
      if (!liveRec.active(st)) return "";
      const w = liveRec.warn(st, true), lm = st.last_match;
      return `<div class="lsline" role="status"><span class="ls-dot ${liveRec.dot(st)}" aria-hidden="true"></span><span><b>Livestream</b> ${h((LS_LABEL[st.state] || st.state).toLowerCase())}</span>
        ${st.behind_live_s != null && st.state === "recording" && !w ? `<span>${dur(st.behind_live_s)} behind live</span>` : ""}
        ${lm ? `<span>last match ${lsMatch(lm)}</span>` : ""}
        ${w ? `<span class="ls-warn">${CS.icon("warn", "ico")} ${h(w)}</span>` : st.state === "error" && st.message ? `<span class="ls-err">${h(st.message)}</span>` : ""}
        ${CS.can && CS.can("admin") && ev ? `<a href="#/${ev}/sources">Recorder</a>` : ""}</div>`;
    },
    /** compact card (Video page, Sources tab) */
    card(st, ev) {
      if (!st) return "";
      const w = liveRec.warn(st), lm = st.last_match, src = st.source || {}, dk = st.disk || {}, an = st.analyser || {}, tr = st.tracking, m = st.matches || {};
      const errs = (st.errors || []).filter(Boolean);
      const help = `<span class="hl">The recorder service records the event's livestream and cuts out each match about 20 s after the final buzzer, with teams and score read off the broadcast. Those results show on the site (marked <b>LIVE · stream</b>) until TBA posts.</span>
        <span class="hl">Robots are tracked from the stream only when it shows a fixed field view: a <b>field</b> box in Layout and a calibration of that camera. Otherwise each match waits for its per-match upload.</span>`;
      if (st.state === "no_source" || st.state === "disabled") return `<div class="card ls-card"><div class="lsline"><span class="ls-dot" aria-hidden="true"></span><span><b>Livestream recorder</b> ${h((LS_LABEL[st.state] || st.state).toLowerCase())}</span>
        ${st.message ? `<span>${h(st.message)}</span>` : ""}${CS.help(help, "About the livestream recorder")}</div></div>`;
      const clip = lm && CS.mediaUrl && CS.mediaUrl(lm.clip);
      return `<div class="card ls-card"><div class="lsline"><span class="ls-dot ${liveRec.dot(st)}" aria-hidden="true"></span><span><b>Livestream recorder</b> ${h((LS_LABEL[st.state] || st.state).toLowerCase())}</span>
          ${st.behind_live_s != null && st.state === "recording" && !w ? `<span>${dur(st.behind_live_s)} behind live</span>` : ""}
          ${an.behind_chunks != null && an.behind_chunks > 2 ? `<span title="the analyser reads the recording in 5 s chunks">analyser ${dur(an.behind_chunks * 5)} behind</span>` : ""}
          ${st.age_s != null ? `<span class="muted">${st.summary ? "status from the site data, " : ""}updated ${dur(st.age_s)} ago</span>` : ""}
          ${CS.help(help, "About the livestream recorder")}</div>
        ${w ? `<div class="ls-alert">${CS.icon("warn", "ico")} ${h(w)}</div>` : st.state === "error" && st.message ? `<div class="ls-alert bad">${h(st.message)}</div>` : st.message ? `<div class="muted small">${h(st.message)}</div>` : ""}
        <dl class="ls-kv">
          ${src.label || src.url || st.video_id ? `<div><dt>Stream</dt><dd>${h(src.label || "")}${src.url ? ` ${urlHtml(src.url)}` : ""}${st.video_id ? ` <span class="muted">· video ${h(st.video_id)}${st.live_status ? `, ${h(String(st.live_status).replace(/_/g, " "))}` : ""}</span>` : ""}</dd></div>` : ""}
          <div><dt>Last match</dt><dd>${lm ? `${lsMatch(lm)}${clip ? ` · <a href="${h(clip)}" target="_blank" rel="noopener">clip</a>` : ""}` : "none detected yet"}${m.n_matches != null ? ` <span class="muted">· ${m.n_matches} match${m.n_matches === 1 ? "" : "es"} cut${m.n > m.n_matches ? `, ${m.n - m.n_matches} skipped` : ""}</span>` : ""}</dd></div>
          ${tr ? `<div><dt>Tracking</dt><dd>${tr.ok ? "from the stream" : `uploads only <span class="muted">· ${h(tr.note || "")}</span>`}</dd></div>` : ""}
          ${dk.free_gb != null || dk.seg_gb != null ? `<div><dt>Disk</dt><dd>${dk.free_gb != null ? `${gb(dk.free_gb)} free` : ""}${dk.seg_gb != null ? ` <span class="muted">· this stream ${gb(dk.seg_gb)} + clips ${gb(dk.clips_gb)}${dk.budget_gb != null ? ` (budget ${gb(dk.budget_gb)})` : ""}</span>` : ""}
            ${dk.free_gb != null && dk.min_free_gb != null && dk.free_gb < dk.min_free_gb ? ` <span class="ls-warn">low: old segments are being deleted</span>` : ""}</dd></div>` : ""}
        </dl>
        ${errs.length ? `<details class="ls-errs"><summary>${errs.length} recent error${errs.length === 1 ? "" : "s"}</summary><ul>${errs.map(e => `<li><code>${h(e)}</code></li>`).join("")}</ul></details>` : ""}</div>`;
    },
  };
  CS.liveRecorder = liveRec;

  // ---------------------------------------------------------------- page
  CS.route("sources", async (ev, tab, id) => {
    crumbs([[ev, `#/${ev}`], ["Video setup", `#/${ev}/sources`]]); $updated.textContent = "";
    if (CS.platform && !CS.can("admin")) { $app.innerHTML = `<div class="empty">Video setup is for admins.</div>`; return; }
    tab = TABS.some(t => t[0] === tab) ? tab : "sources";
    $app.innerHTML = `<style>${CSS}</style><h1>Video setup ${CS.help("Where the match video comes from, where things are in the frame, and where the cameras are. One-time per event; the pipeline reads it for every match.", "What video setup is for")}</h1>
      <div class="tabs">${TABS.map(([k, t]) => `<a class="tab ${k === tab ? "on" : ""}" href="#/${ev}/sources/${k}">${t}</a>`).join("")}</div>
      <div id="vs"></div>`;
    const el = document.getElementById("vs");
    try {
      await ({ sources: sourcesTab, layout: layoutTab, calib: calibTab })[tab](ev, el, id && decodeURIComponent(id));
    } catch (e) {
      el.innerHTML = `<div class="empty">${e.status === 503 ? h(errMsg(e)) : e.status === 404 ? "No video-setup API on this server (static site)." : "Couldn't load: " + h(errMsg(e))}</div>`;
    }
  });

  // ---------------------------------------------------------------- 1. sources
  async function sourcesTab(ev, el) {
    const d = await A("GET", `events/${ev}/sources`);
    const S = d.sources || [];
    const cov = s => {
      const c = s.coverage || {};
      if (s.kind === "per_match") return `${s.n_videos || 0} match video${s.n_videos === 1 ? "" : "s"}`;
      if (c.mode === "range") return `${h(c.from || "start")} – ${h(c.to || "end")}`;
      if (c.mode === "matches" && (c.matches || []).length) return `${c.matches.length} matches`;
      return "whole event";
    };
    el.innerHTML = `
      <div class="card vs-row">
        <button id="disc">Discover from The Blue Alliance</button>
        ${CS.help("Finds the event webcasts and the per-match uploads TBA links (grouped by title, e.g. “Wide Match N”).", "What Discover finds")}
        <span id="dst" class="muted small"></span>
      </div>
      <div id="lsrec"></div>
      <div class="card vs-tw" style="margin-top:12px">
        <table class="mini vs-src"><thead><tr><th>Source</th><th>Type</th><th title="${h(Object.entries(ROLE_TIP).map(([k, v]) => k + ": " + v).join("\n"))}">Camera role</th>
          <th title="the overhead video the tracker processes">Tracked</th><th>Covers</th><th>Layout</th><th>On</th><th></th></tr></thead>
        <tbody>${S.length ? S.map(s => `<tr data-id="${h(s.id)}">
          <td><b>${h(s.label || s.id)}</b><div class="muted small vs-url">${s.url ? urlHtml(s.url) :
            s.title_pattern ? `title ~ <code>${h(s.title_pattern)}</code>${s.example_title ? ` · e.g. “${h(s.example_title)}”` : ""}` : ""}</div></td>
          <td data-l="Type"><span><span class="pill">${h(s.kind)}</span> <span class="muted small">${h(s.provider || "")}${s.origin === "tba" ? " · TBA" : ""}</span></span></td>
          <td data-l="Camera role"><select data-f="role" aria-label="Camera role of ${h(s.label || s.id)}">${d.roles.map(r => `<option ${r === s.role ? "selected" : ""} title="${h(ROLE_TIP[r])}">${r}</option>`).join("")}</select></td>
          <td data-l="Tracked" class="vs-c"><input type="radio" name="prim" data-f="primary" aria-label="Track ${h(s.label || s.id)}" ${s.primary ? "checked" : ""}></td>
          <td data-l="Covers" class="small">${cov(s)}</td>
          <td data-l="Layout"><a href="#/${ev}/sources/layout/${encodeURIComponent(s.id)}">${s.has_layout ? "edit boxes" : "draw boxes"}</a></td>
          <td data-l="On" class="vs-c"><input type="checkbox" data-f="enabled" aria-label="Use ${h(s.label || s.id)}" ${s.enabled !== false ? "checked" : ""}></td>
          <td class="vs-x"><button class="small" data-del>Remove</button></td></tr>`).join("") :
          `<tr><td colspan="8" class="muted">No sources yet: discover them from TBA or paste a URL below.</td></tr>`}</tbody></table>
      </div>
      <div class="card" style="margin-top:12px">
        <h3 style="margin:0 0 8px">Add a source</h3>
        <form id="add" class="vs-form">
          <input name="url" required placeholder="YouTube / Twitch URL (livestream, video, playlist, channel), .m3u8/.mp4 URL, or /media/… file" style="flex:3 1 380px">
          <select name="role">${d.roles.map(r => `<option title="${h(ROLE_TIP[r])}">${r}</option>`).join("")}</select>
          <input name="label" placeholder="Label (optional)…" style="flex:1 1 160px">
          <label class="small"><input type="checkbox" name="primary"> tracked video</label>
          <button>Add</button>${CS.help("Alternate angles (e.g. a team's own overhead recording) can be added the same way; mark the one the tracker should use as <i>tracked</i>.", "About alternate angles")}<span id="ast" class="muted small"></span>
        </form>
      </div>
      <div class="card muted small" style="margin-top:12px">
        Worker video choice: prefer <code>${h((d.video_config || {}).prefer || "^Wide Match (default)")}</code>${(d.video_config || {}).reject ? `, skip <code>${h(d.video_config.reject)}</code>` : ""}${d.station_pattern ? ` · station-cam uploads <code>${h(d.station_pattern)}</code>` : ""}${d.livestream ? ` · livestream ${urlHtml(d.livestream, true)}` : ""}.
        ${d.jobs_inline ? "" : "Frame grabs and calibration run in the worker (its video job runner must be on)."}
        Next: <a href="#/${ev}/sources/layout">draw the layout</a>, then <a href="#/${ev}/sources/calib">calibrate the cameras</a>.
      </div>`;
    // livestream recorder status (refreshed while this tab is open)
    const lsEl = document.getElementById("lsrec");
    const lsDraw = async () => { const st = await liveRec.load(ev); if (lsEl.isConnected) lsEl.innerHTML = liveRec.card(st, ev); };
    lsDraw(); const lsIv = setInterval(lsDraw, 15000); cleanup.push(() => clearInterval(lsIv));
    document.getElementById("disc").onclick = async () => {
      const st = document.getElementById("dst");
      try { st.textContent = "asking TBA…"; await job(ev, A("POST", `events/${ev}/sources/discover`), t => st.textContent = t); route(false); }
      catch (e) { st.textContent = errMsg(e); }
    };
    el.querySelectorAll("tr[data-id]").forEach(tr => {
      const id = tr.dataset.id;
      tr.querySelectorAll("[data-f]").forEach(inp => inp.onchange = async () => {
        const f = inp.dataset.f, v = inp.type === "checkbox" || inp.type === "radio" ? inp.checked : inp.value;
        try { await A("PATCH", `events/${ev}/sources/${encodeURIComponent(id)}`, { [f]: v }); if (f === "primary") route(false); }
        catch (e) { alert(errMsg(e)); route(false); }
      });
      tr.querySelector("[data-del]").onclick = async () => {
        if (!confirm("Remove this source? (its layout boxes are kept)")) return;
        try { await A("DELETE", `events/${ev}/sources/${encodeURIComponent(id)}`); route(false); } catch (e) { alert(errMsg(e)); }
      };
    });
    document.getElementById("add").onsubmit = async e => {
      e.preventDefault();
      const f = e.target, st = document.getElementById("ast");
      try {
        const s = await A("POST", `events/${ev}/sources`, { url: f.url.value.trim(), role: f.role.value, label: f.label.value.trim() || null, primary: f.primary.checked });
        if (s.job) { st.textContent = "indexing the playlist…"; await job(ev, Promise.resolve(s.job), t => st.textContent = t); }
        route(false);
      } catch (x) { st.textContent = errMsg(x); }
    };
  }

  // ---------------------------------------------------------------- frame canvas (shared by the box editor + wizard)
  function FrameView(wrap, opts = {}) {
    const c = document.createElement("canvas"); c.className = "vs-canvas"; wrap.appendChild(c);
    const lens = document.createElement("canvas"); lens.className = "vs-lens"; lens.width = lens.height = 180; wrap.appendChild(lens);
    const ctx = c.getContext("2d"), lctx = lens.getContext("2d");
    const V = { img: null, W: 1920, H: 1080, s: 1, c, ctx, draw: () => { }, mouse: null, zoom: opts.zoom || 4 };
    V.size = () => {
      const w = wrap.clientWidth || 900, dpr = window.devicePixelRatio || 1;
      V.s = w / V.W; c.width = Math.round(w * dpr); c.height = Math.round(V.H * V.s * dpr);
      c.style.width = w + "px"; c.style.height = V.H * V.s + "px"; ctx.setTransform(dpr * V.s, 0, 0, dpr * V.s, 0, 0); V.render();
    };
    V.setImage = (img, W, H) => { V.img = img; V.W = W || img.naturalWidth; V.H = H || img.naturalHeight; V.size(); };
    V.toImg = ev => { const r = c.getBoundingClientRect(); return [(ev.clientX - r.left) / V.s, (ev.clientY - r.top) / V.s]; };
    V.render = () => {
      ctx.clearRect(0, 0, V.W, V.H);
      if (V.img) ctx.drawImage(V.img, 0, 0, V.W, V.H);
      else { ctx.fillStyle = "#1b2330"; ctx.fillRect(0, 0, V.W, V.H);
        if (V.empty) { ctx.fillStyle = "#c2d0d4"; ctx.font = `${15 / V.s}px system-ui, sans-serif`; ctx.textAlign = "center"; ctx.fillText(V.empty, V.W / 2, V.H / 2); ctx.textAlign = "start"; } }
      V.draw(ctx);
      if (V.mouse && V.img) {               // magnifier lens for precise clicks
        const [x, y] = V.mouse, z = V.zoom, L = lens.width / z;
        lctx.imageSmoothingEnabled = false; lctx.clearRect(0, 0, lens.width, lens.height);
        lctx.drawImage(c, (x - L / 2) * V.s * (c.width / (V.W * V.s)), (y - L / 2) * V.s * (c.height / (V.H * V.s)),
                       L * V.s * (c.width / (V.W * V.s)), L * V.s * (c.height / (V.H * V.s)), 0, 0, lens.width, lens.height);
        lctx.strokeStyle = "#fff"; lctx.lineWidth = 1; lctx.beginPath();
        lctx.moveTo(lens.width / 2, 0); lctx.lineTo(lens.width / 2, lens.height); lctx.moveTo(0, lens.height / 2); lctx.lineTo(lens.width, lens.height / 2); lctx.stroke();
        lens.style.display = "block";
        lens.style.left = (V.mouse[0] * V.s > (wrap.clientWidth / 2) ? 8 : wrap.clientWidth - lens.width - 8) + "px";
      } else lens.style.display = "none";
    };
    c.addEventListener("mousemove", e => { if (opts.lens) { V.mouse = V.toImg(e); V.render(); } });
    c.addEventListener("mouseleave", () => { V.mouse = null; V.render(); });
    const onR = () => V.size(); window.addEventListener("resize", onR); cleanup.push(() => window.removeEventListener("resize", onR));
    return V;
  }

  // ---------------------------------------------------------------- 2. layout box editor
  async function layoutTab(ev, el, sid) {
    const [d, L] = await Promise.all([A("GET", `events/${ev}/sources`), A("GET", `events/${ev}/layout`)]);
    const S = d.sources || [];
    if (!S.length) { el.innerHTML = `<div class="empty">Add a video source first (<a href="#/${ev}/sources/sources">Sources</a>).</div>`; return; }
    const src = S.find(s => s.id === sid) || S.find(s => s.primary) || S[0];
    const saved = ((L.layout || {}).sources || {})[src.id];
    let regions = saved ? saved.regions.map(r => ({ ...r })) : [];
    let sugg = [], sel = -1, kind = "field", W = saved ? saved.W : 1920, H = saved ? saved.H : 1080, dirty = false;
    el.innerHTML = `
      <div class="vs-row">
        <label class="small">Source <select id="srcsel">${S.map(s => `<option value="${h(s.id)}" ${s.id === src.id ? "selected" : ""}>${h(s.label || s.id)} (${h(s.role)})</option>`).join("")}</select></label>
        <label class="small">at <input id="ft" class="vs-num" type="number" step="1" min="0" placeholder="0" aria-label="Frame time in seconds"> s</label>
        <button id="grab">Grab frame</button>
        <button id="sug" title="graphics that never change, the 1 Hz clock, picture-in-picture rectangles, the field">Suggest boxes</button>
        <label class="small">compare with <select id="other"><option value="">(none)</option>${S.filter(s => s.id !== src.id).map(s => `<option value="${h(s.id)}">${h(s.label || s.id)}</option>`).join("")}</select></label>
        <span id="lst" class="muted small"></span>
      </div>
      <div class="vs-edit">
        <div class="vs-wrap" id="cv"></div>
        <div class="vs-side">
          <div class="small muted">Draw boxes ${CS.help("Pick a kind, drag on the frame. Click a box to select; drag to move, corners to resize; Delete removes.", "How to draw boxes")}</div>
          <div class="chips" id="kinds">${Object.keys(L.kinds).map(k => `<span class="chip ${k === kind ? "on" : ""}" data-k="${k}" title="${h(L.kinds[k])}"><i class="vs-sw" style="background:${KCOL[k]}"></i>${KSHORT[k] || k}</span>`).join("")}</div>
          <div id="sugbox"></div>
          <div id="rlist"></div>
          <div class="vs-row" style="margin-top:10px"><button id="save" class="primary">Save layout</button>
            <button id="dflt" title="the 2026 FIRST broadcast boxes the pipeline uses when there is no layout">Load 2026 FIRST defaults</button></div>
          <div class="small muted" id="savest">${saved ? `saved ${when(saved.updated_at)} by ${h(saved.updated_by || "?")}` : "not saved yet — the pipeline uses the 2026 FIRST defaults"}</div>
        </div>
      </div>`;
    const st = t => document.getElementById("lst").textContent = t;
    const V = FrameView(document.getElementById("cv"), { lens: true, zoom: 3 });
    V.empty = "No frame yet: Grab frame"; V.W = W; V.H = H; V.size();
    try { V.setImage(await loadImg(imgUrl(`events/${ev}/sources/${encodeURIComponent(src.id)}/frame.jpg`))); W = V.W; H = V.H; }
    catch (e) { st("no frame yet: Grab frame"); }
    document.getElementById("srcsel").onchange = e => { location.hash = `#/${ev}/sources/layout/${encodeURIComponent(e.target.value)}`; };
    document.getElementById("kinds").onclick = e => { const k = e.target.closest("[data-k]"); if (!k) return; kind = k.dataset.k; el.querySelectorAll("#kinds .chip").forEach(c => c.classList.toggle("on", c === k)); };
    document.getElementById("grab").onclick = async () => {
      const t = document.getElementById("ft").value;
      try {
        await job(ev, A("POST", `events/${ev}/sources/${encodeURIComponent(src.id)}/frame`, t !== "" ? { t: +t } : {}), st);
        V.setImage(await loadImg(imgUrl(`events/${ev}/sources/${encodeURIComponent(src.id)}/frame.jpg`))); W = V.W; H = V.H; st("");
      } catch (e) { st(errMsg(e)); }
    };
    document.getElementById("sug").onclick = async () => {
      const other = document.getElementById("other").value;
      try {
        const r = await job(ev, A("POST", `events/${ev}/layout/${encodeURIComponent(src.id)}/suggest`, other ? { other } : {}), st);
        const sx = W / r.W, sy = H / r.H;
        sugg = r.suggestions.map(s => ({ ...s, box: [s.box[0] * sx, s.box[1] * sy, s.box[2] * sx, s.box[3] * sy] }));
        st(`${sugg.length} suggestions: accept the ones that fit`); paint();
      } catch (e) { st(errMsg(e)); }
    };
    document.getElementById("dflt").onclick = () => {
      const hasInsets = regions.some(r => r.kind.startsWith("station_")) || src.role !== "wide";
      regions = (hasInsets ? L.defaults.program : L.defaults.wide).map(r => ({ ...r, box: [r.box[0] * W / L.defaults.W, r.box[1] * H / L.defaults.H, r.box[2] * W / L.defaults.W, r.box[3] * H / L.defaults.H] }));
      dirty = true; paint();
    };
    document.getElementById("save").onclick = async () => {
      try {
        await A("PUT", `events/${ev}/layout/${encodeURIComponent(src.id)}`, { W, H, regions: regions.map(r => ({ id: r.id, kind: r.kind, label: r.label, box: r.box.map(Math.round), light_on_dark: r.light_on_dark })) });
        dirty = false; document.getElementById("savest").textContent = "saved just now"; paint();
      } catch (e) { alert(errMsg(e)); }
    };
    const leave = e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", leave); cleanup.push(() => window.removeEventListener("beforeunload", leave));

    function paint() { V.render(); list(); }
    V.draw = ctx => {
      const lw = 2 / V.s;
      ctx.font = `${13 / V.s}px system-ui, sans-serif`;
      sugg.forEach(r => {
        ctx.setLineDash([8 / V.s, 6 / V.s]); ctx.strokeStyle = KCOL[r.kind] || "#fff"; ctx.lineWidth = lw;
        ctx.strokeRect(r.box[0], r.box[1], r.box[2] - r.box[0], r.box[3] - r.box[1]); ctx.setLineDash([]);
      });
      regions.forEach((r, i) => {
        const [x0, y0, x1, y1] = r.box, col = KCOL[r.kind] || "#fff";
        ctx.fillStyle = col + (r.kind === "mask" || r.kind === "inset" ? "40" : "1c"); ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
        ctx.strokeStyle = col; ctx.lineWidth = i === sel ? 3 * lw : lw; ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
        const label = r.label || KSHORT[r.kind];
        const tw = ctx.measureText(label).width + 8 / V.s;
        ctx.fillStyle = col; ctx.fillRect(x0, y0, tw, 17 / V.s); ctx.fillStyle = "#000"; ctx.fillText(label, x0 + 4 / V.s, y0 + 13 / V.s);
        if (i === sel) { ctx.fillStyle = "#fff"; [[x0, y0], [x1, y0], [x0, y1], [x1, y1]].forEach(([x, y]) => ctx.fillRect(x - 5 / V.s, y - 5 / V.s, 10 / V.s, 10 / V.s)); }
      });
      if (drag && drag.mode === "new") {
        const [x0, y0, x1, y1] = drag.box; ctx.strokeStyle = KCOL[kind]; ctx.lineWidth = lw; ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      }
    };
    function list() {
      document.getElementById("sugbox").innerHTML = sugg.length ? `<div class="vs-sug"><b class="small">Suggestions</b> <button class="small" id="accall">accept all</button> <button class="small" id="dropall">dismiss</button>
        ${sugg.map((s, i) => `<div class="vs-ri"><i class="vs-sw" style="background:${KCOL[s.kind]}"></i>${KSHORT[s.kind] || s.kind} <span class="muted small">${h(s.label || "")} ${s.score != null ? Math.round(s.score * 100) + "%" : ""}</span>
          <button class="small" data-acc="${i}">accept</button></div>`).join("")}</div>` : "";
      document.getElementById("rlist").innerHTML = regions.map((r, i) => `<div class="vs-ri ${i === sel ? "on" : ""}" data-i="${i}">
        <i class="vs-sw" style="background:${KCOL[r.kind]}"></i>
        <select data-k="${i}">${Object.keys(L.kinds).map(k => `<option value="${k}" ${k === r.kind ? "selected" : ""}>${KSHORT[k] || k}</option>`).join("")}</select>
        <input data-l="${i}" value="${h(r.label || "")}" placeholder="Label…" aria-label="Box label" style="width:110px">
        <span class="muted small">${r.box.map(v => Math.round(v)).join(",")}</span>
        ${r.kind === "clock" ? `<label class="small" title="digits lighter than their box"><input type="checkbox" data-ld="${i}" ${r.light_on_dark ? "checked" : ""}>light</label>` : ""}
        <button class="small icon" data-x="${i}" aria-label="Remove box">${CS.icon("close")}</button></div>`).join("") || `<div class="muted small" style="margin-top:8px">No boxes yet.</div>`;
      const acc = i => { const s = sugg[i]; regions.push({ id: "r" + Date.now().toString(36) + i, kind: s.kind, label: s.label, box: s.box.slice() }); dirty = true; };
      document.querySelectorAll("[data-acc]").forEach(b => b.onclick = () => { acc(+b.dataset.acc); sugg.splice(+b.dataset.acc, 1); paint(); });
      const aa = document.getElementById("accall"); if (aa) aa.onclick = () => { sugg.forEach((_, i) => acc(i)); sugg = []; paint(); };
      const da = document.getElementById("dropall"); if (da) da.onclick = () => { sugg = []; paint(); };
      document.querySelectorAll("[data-k]").forEach(s => s.onchange = () => { regions[+s.dataset.k].kind = s.value; dirty = true; paint(); });
      document.querySelectorAll("[data-l]").forEach(s => s.oninput = () => { regions[+s.dataset.l].label = s.value; dirty = true; V.render(); });
      document.querySelectorAll("[data-ld]").forEach(s => s.onchange = () => { regions[+s.dataset.ld].light_on_dark = s.checked; dirty = true; });
      document.querySelectorAll("[data-x]").forEach(b => b.onclick = () => { regions.splice(+b.dataset.x, 1); sel = -1; dirty = true; paint(); });
      document.querySelectorAll(".vs-ri[data-i]").forEach(d => d.onclick = e => { if (e.target.closest("select,input,button")) return; sel = +d.dataset.i; paint(); });
    }
    // mouse: draw / move / resize
    let drag = null;
    const hit = (x, y) => {
      const tol = 9 / V.s;
      for (let i = regions.length - 1; i >= 0; i--) {
        const [x0, y0, x1, y1] = regions[i].box;
        const cs = [[0, 0], [1, 0], [0, 1], [1, 1]].find(([a, b]) => Math.abs(x - (a ? x1 : x0)) < tol && Math.abs(y - (b ? y1 : y0)) < tol);
        if (cs) return { i, mode: "resize", corner: cs };
        if (x > x0 && x < x1 && y > y0 && y < y1) return { i, mode: "move" };
      }
      return null;
    };
    V.c.addEventListener("mousedown", e => {
      const [x, y] = V.toImg(e), t = hit(x, y);
      if (t) { sel = t.i; drag = { ...t, x, y, box0: regions[t.i].box.slice() }; }
      else { sel = -1; drag = { mode: "new", x, y, box: [x, y, x, y] }; }
      paint();
    });
    window.addEventListener("mousemove", onMove); window.addEventListener("mouseup", onUp);
    cleanup.push(() => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); window.removeEventListener("keydown", onKey); });
    function onMove(e) {
      if (!drag) { if (e.target === V.c) { const t = hit(...V.toImg(e)); V.c.style.cursor = t ? (t.mode === "move" ? "move" : "nwse-resize") : "crosshair"; } return; }
      let [x, y] = V.toImg(e); x = Math.max(0, Math.min(W, x)); y = Math.max(0, Math.min(H, y));
      if (drag.mode === "new") drag.box = [Math.min(drag.x, x), Math.min(drag.y, y), Math.max(drag.x, x), Math.max(drag.y, y)];
      else if (drag.mode === "move") {
        const dx = x - drag.x, dy = y - drag.y, b = drag.box0;
        const cx = Math.max(-b[0], Math.min(W - b[2], dx)), cy = Math.max(-b[1], Math.min(H - b[3], dy));
        regions[drag.i].box = [b[0] + cx, b[1] + cy, b[2] + cx, b[3] + cy]; dirty = true;
      } else {
        const b = regions[drag.i].box, [a, c] = drag.corner;
        if (a) b[2] = x; else b[0] = x; if (c) b[3] = y; else b[1] = y; dirty = true;
      }
      V.render();
    }
    function onUp() {
      if (!drag) return;
      if (drag.mode === "new") {
        const [x0, y0, x1, y1] = drag.box;
        if (x1 - x0 > 6 && y1 - y0 > 6) {
          if (["station_red", "station_blue", "clock", "field", "overhead"].includes(kind)) regions = regions.filter(r => r.kind !== kind);
          regions.push({ id: "r" + Date.now().toString(36), kind, label: KSHORT[kind], box: drag.box }); sel = regions.length - 1; dirty = true;
        }
      } else if (drag.mode === "resize") {
        const b = regions[drag.i].box; regions[drag.i].box = [Math.min(b[0], b[2]), Math.min(b[1], b[3]), Math.max(b[0], b[2]), Math.max(b[1], b[3])];
      }
      drag = null; paint();
    }
    function onKey(e) {
      if ((e.key === "Delete" || e.key === "Backspace") && sel >= 0 && !e.target.closest("input,select,textarea")) { regions.splice(sel, 1); sel = -1; dirty = true; paint(); }
    }
    window.addEventListener("keydown", onKey);
    paint();
  }

  // ---------------------------------------------------------------- 3. calibration wizard
  async function calibTab(ev, el, target) {
    const cal = await A("GET", `events/${ev}/calibration`);
    target = TARGETS.some(t => t[0] === target) ? target : "overhead";
    const stat = cal.targets.find(t => t.target === target) || {};
    const fmt = c => c ? [c.tag_rms_px != null ? `tags ${c.tag_rms_px} px` : "", c.point_rms_px != null ? `points ${c.point_rms_px} px` : "",
                         c.line_med_px != null ? `centre line ${c.line_med_px} px` : ""].filter(Boolean).join(", ") : "";
    el.innerHTML = `
      <div class="vs-row">${TARGETS.map(([k, t]) => {
        const s = cal.targets.find(x => x.target === k) || {};
        return `<a class="chip ${k === target ? "on" : ""}" href="#/${ev}/sources/calib/${k}">${t} ${s.saved ? `<span class="pill done">${s.where === "repo" ? "shipped" : "saved"}</span>` : `<span class="pill">none</span>`}</a>`;
      }).join("")}</div>
      <div class="muted small" style="margin:6px 0 10px">${stat.saved ? `Saved calibration: ${h(stat.file)}${stat.calib ? " (" + h(fmt(stat.calib)) + ", " + when(stat.calib.accepted_at || stat.calib.at) + ")" : stat.tag_rms != null ? ` (tag RMS ${stat.tag_rms.toFixed(2)} px)` : ""}. <button class="small" id="showcur">show it on the frame</button>` : "Not calibrated yet."}
        Steps: <b>1</b> detect AprilTags → <b>2</b> click a few field points the tags don't cover → <b>3</b> fit and check the grid sits on the field → <b>4</b> accept.</div>
      <div class="vs-row"><button id="det">1. Detect tags</button><button id="fit" disabled>3. Fit</button><button id="acc" disabled class="on">4. Accept &amp; save</button>
        <span class="small">show: <label><input type="checkbox" id="sg" checked> grid</label> <label><input type="checkbox" id="sh" checked> lines + hubs</label> <label><input type="checkbox" id="st" checked> tags</label></span>
        <span id="cst" class="muted small"></span></div>
      <div class="vs-edit">
        <div class="vs-wrap" id="ccv"></div>
        <div class="vs-side">
          <div id="rep"></div>
          <div class="small muted" style="margin-top:8px">2. Pick a line or point, then click on the frame ${CS.help("The magnifier follows the mouse; drag a marker to adjust, right-click removes it. Lines are easiest: any 2+ points along a visible edge. Field frame: blue wall X = 0, the blue outpost / chute is on the Y = 0 long side.", "How to place field points")}</div>
          <div id="plist"></div>
        </div>
      </div>`;
    const st = t => document.getElementById("cst").textContent = t;
    const V = FrameView(document.getElementById("ccv"), { lens: true, zoom: 4 });
    V.empty = "No frame yet: 1. Detect tags grabs one";
    let W = 1920, H = 1080, tags = {}, points = {}, NP = {}, NL = DEFAULT_LINES, lineClicks = {}, overlay = null, pick = null, report = null, drop = new Set();
    const show = { grid: true, lines: true, tags: true };
    ["sg", "sh", "st"].forEach((id, i) => document.getElementById(id).onchange = e => { show[["grid", "lines", "tags"][i]] = e.target.checked; V.render(); });
    async function loadFrame() {
      try { V.setImage(await loadImg(imgUrl(`events/${ev}/calibration/${target}/frame.jpg`)), W, H); } catch (e) { }
    }
    async function loadState() {
      try {
        const w = await A("GET", `events/${ev}/calibration/${target}/wizard`);
        W = w.W; H = w.H; tags = w.fit_tags || w.tags || {}; points = w.points || {}; NP = w.points_catalog || NP;
        NL = w.lines_catalog || NL; lineClicks = w.line_clicks || {};
        report = w.report || null; await loadFrame();
        document.getElementById("fit").disabled = false; document.getElementById("acc").disabled = !w.camera;
        if (w.camera) st(w.accepted_at && w.accepted_at > (w.fitted_at || 0) ? "this fit is saved" : "last fit not accepted yet: Fit again to see its overlay");
      } catch (e) { st("start with step 1"); await loadFrame(); V.size(); }
    }
    V.draw = ctx => {
      const lw = 1.6 / V.s;
      if (overlay) for (const L of overlay) {
        const isG = L.kind === "grid", isT = L.kind === "tag";
        if ((isG && !show.grid) || (isT && !show.tags) || (!isG && !isT && !show.lines)) continue;
        ctx.strokeStyle = isG ? "rgba(255,255,255,.35)" : isT ? "#51cf66" : L.kind === "field" ? "#ffd43b" : L.kind === "hub_red" ? "#ff5a5f" : L.kind === "hub_blue" ? "#3e8bff" : "#fff";
        ctx.lineWidth = isG ? lw * 0.7 : lw * 1.3; ctx.beginPath(); let pen = false;
        for (const p of L.pts) { if (!p) { pen = false; continue; } if (!pen) { ctx.moveTo(p[0], p[1]); pen = true; } else ctx.lineTo(p[0], p[1]); }
        ctx.stroke();
      }
      if (show.tags) for (const [id, t] of Object.entries(tags)) {
        const c = t.corners, off = drop.has(+id);
        ctx.strokeStyle = off ? "#888" : t.predicted ? "#22b8cf" : "#51cf66"; ctx.lineWidth = 2 / V.s;
        if (c) { ctx.beginPath(); c.forEach((p, k) => k ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.stroke(); }
        const ce = t.center || (c && [c.reduce((a, p) => a + p[0], 0) / 4, c.reduce((a, p) => a + p[1], 0) / 4]);
        ctx.fillStyle = ctx.strokeStyle; ctx.font = `bold ${12 / V.s}px system-ui`; ctx.fillText(id, ce[0] + 6 / V.s, ce[1] - 6 / V.s);
      }
      for (const [k, pts] of Object.entries(lineClicks)) {
        ctx.strokeStyle = pick === "line:" + k ? "#fff" : "#f06595"; ctx.lineWidth = 2 / V.s;
        (pts || []).forEach((p, i) => {
          ctx.beginPath(); ctx.arc(p[0], p[1], 4 / V.s, 0, 7); ctx.stroke();
          if (i === 0) { ctx.fillStyle = "#f06595"; ctx.font = `${11 / V.s}px system-ui`; ctx.fillText((NL[k] || {}).label || k, p[0] + 7 / V.s, p[1] - 7 / V.s); }
        });
      }
      for (const [k, p] of Object.entries(points)) {
        if (!p) continue;
        const it = report && (report.items || []).find(i => i.kind === "point" && i.id === k);
        ctx.strokeStyle = k === pick ? "#fff" : "#ff922b"; ctx.lineWidth = 2 / V.s;
        ctx.beginPath(); ctx.arc(p[0], p[1], 6 / V.s, 0, 7); ctx.moveTo(p[0] - 10 / V.s, p[1]); ctx.lineTo(p[0] + 10 / V.s, p[1]); ctx.moveTo(p[0], p[1] - 10 / V.s); ctx.lineTo(p[0], p[1] + 10 / V.s); ctx.stroke();
        if (it && it.pred) { ctx.strokeStyle = "#ffd43b"; ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(it.pred[0], it.pred[1]); ctx.stroke(); }
        ctx.fillStyle = "#ff922b"; ctx.font = `${11 / V.s}px system-ui`; ctx.fillText((NP[k] || {}).short || k, p[0] + 8 / V.s, p[1] + 14 / V.s);
      }
    };
    function plist() {
      const groups = {};
      Object.entries(NP).forEach(([k, v]) => { if (v.group !== "tags") (groups[v.group] = groups[v.group] || []).push([k, v]); });
      const tagIds = Object.keys(tags);
      document.getElementById("plist").innerHTML = `<div class="vs-pg"><b class="small">field lines — click 2+ points anywhere along one (easiest)</b>${Object.entries(NL).map(([k, v]) =>
        `<div class="vs-pt ${"line:" + k === pick ? "on" : ""} ${(lineClicks[k] || []).length ? "set" : ""}" data-p="line:${k}">${(lineClicks[k] || []).length ? "●" + lineClicks[k].length : "○"} ${h(v.label)}</div>`).join("")}</div>` +
        Object.entries(groups).map(([g, l]) => `<div class="vs-pg"><b class="small">${h(g)}</b>${l.map(([k, v]) =>
        `<div class="vs-pt ${k === pick ? "on" : ""} ${points[k] ? "set" : ""}" data-p="${k}">${points[k] ? "●" : "○"} ${h(v.label)}</div>`).join("")}</div>`).join("") +
        `<div class="vs-pg"><b class="small">AprilTag centres (when not detected)</b><div class="chips">${Object.entries(NP).filter(([k, v]) => v.group === "tags" && !tagIds.includes(k.slice(4)))
          .map(([k]) => `<span class="chip ${k === pick ? "on" : ""} ${points[k] ? "set" : ""}" data-p="${k}">${k.slice(4)}${points[k] ? "●" : ""}</span>`).join("")}</div></div>`;
      document.querySelectorAll("[data-p]").forEach(d => d.onclick = () => { pick = d.dataset.p === pick ? null : d.dataset.p; plist(); V.render(); });
    }
    function rep() {
      const r = report;
      const tl = Object.entries(tags).map(([id, t]) => `<label class="chip ${drop.has(+id) ? "" : "on"}" title="${t.predicted ? "found from the fit" : "detected"}${t.n ? `, ${t.n} frames` : ""}"><input type="checkbox" data-t="${id}" ${drop.has(+id) ? "" : "checked"} style="display:none">${id}${t.predicted ? "*" : ""}</label>`).join("");
      document.getElementById("rep").innerHTML = `<div class="small"><b>Tags</b> (click to ignore one): <div class="chips">${tl || `<span class="muted">none found — click field points</span>`}</div></div>` +
        (r ? `<div class="vs-kv small"><b>Fit</b>: ${h(fmt(r)) || "–"}<br>focal ${r.f} px · k1 ${r.k1} · camera at (${r.C.map(v => v.toFixed(1)).join(", ")}) m
          ${(r.warnings || []).map(w => `<div class="vs-warn">${CS.icon("warn", "ico")} ${h(w)}</div>`).join("")}
          ${Object.entries(r.lines || {}).map(([k, v]) => `<div>${h((NL[k.replace(" (auto)", "")] || {}).label || k)}${/auto/.test(k) ? " (found automatically)" : ""}: <b style="color:${v > 3 ? "var(--warn)" : "var(--good)"}">${v} px</b></div>`).join("")}
          ${(r.items || []).filter(i => i.kind === "point").map(i => `<div>${h((NP[i.id] || {}).label || i.id)}: <b style="color:${i.err_px > 6 ? "var(--bad)" : i.err_px > 3 ? "var(--warn)" : "var(--good)"}">${i.err_px} px</b></div>`).join("")}
          <div class="muted">Does the yellow outline / grid sit on the carpet everywhere, and the hub boxes on the hubs? Then accept.</div></div>` : "");
      document.querySelectorAll("[data-t]").forEach(c => c.onchange = () => { c.checked ? drop.delete(+c.dataset.t) : drop.add(+c.dataset.t); rep(); V.render(); });
    }
    // clicks: place / drag / clear named points
    let dragK = null;
    V.c.addEventListener("mousedown", e => {
      const [x, y] = V.toImg(e);
      if (e.button === 2) return;
      const near = Object.entries(points).find(([k, p]) => p && Math.hypot(p[0] - x, p[1] - y) < 10 / V.s);
      if (near) { dragK = near[0]; pick = near[0]; plist(); return; }
      for (const [k, pts] of Object.entries(lineClicks)) {
        const i = (pts || []).findIndex(p => Math.hypot(p[0] - x, p[1] - y) < 8 / V.s);
        if (i >= 0) { dragK = ["line", k, i]; pick = "line:" + k; plist(); return; }
      }
      if (pick && pick.startsWith("line:")) { const k = pick.slice(5); (lineClicks[k] = lineClicks[k] || []).push([x, y]); plist(); V.render(); return; }
      if (pick) { points[pick] = [x, y]; dragK = pick; plist(); V.render(); }
    });
    V.c.addEventListener("contextmenu", e => {
      e.preventDefault(); const [x, y] = V.toImg(e);
      const near = Object.entries(points).find(([k, p]) => p && Math.hypot(p[0] - x, p[1] - y) < 12 / V.s);
      if (near) { delete points[near[0]]; plist(); V.render(); return; }
      for (const [k, pts] of Object.entries(lineClicks)) {
        const i = (pts || []).findIndex(p => Math.hypot(p[0] - x, p[1] - y) < 10 / V.s);
        if (i >= 0) { pts.splice(i, 1); plist(); V.render(); return; }
      }
    });
    const mv = e => {
      if (!dragK) return;
      if (Array.isArray(dragK)) lineClicks[dragK[1]][dragK[2]] = V.toImg(e); else points[dragK] = V.toImg(e);
      V.render();
    };
    const up = () => { dragK = null; };
    window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
    cleanup.push(() => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); });

    document.getElementById("det").onclick = async () => {
      try {
        const r = await job(ev, A("POST", `events/${ev}/calibration/${target}/detect`, {}), st);
        W = r.W; H = r.H; tags = r.tags; NP = r.points || NP; NL = r.lines || NL; report = r.report || null; overlay = r.overlay || null; drop = new Set();
        await loadFrame(); document.getElementById("fit").disabled = false;
        st(`${Object.keys(tags).length} tags found${r.report ? " — a first fit is drawn; check it or add points" : " — click field points (≥ 4–6 spread over the field), then Fit"}`);
        plist(); rep(); V.render();
      } catch (e) { st(errMsg(e)); }
    };
    document.getElementById("fit").onclick = async () => {
      try {
        const r = await job(ev, A("POST", `events/${ev}/calibration/${target}/fit`, { points, line_clicks: lineClicks, drop_tags: [...drop] }), st);
        if (r.error) { st(r.error); return; }
        report = r.report; overlay = r.overlay; tags = r.tags || tags; lineClicks = r.line_clicks || lineClicks;
        document.getElementById("acc").disabled = false; st("fitted — check the overlay"); rep(); plist(); V.render();
      } catch (e) { st(errMsg(e)); }
    };
    document.getElementById("acc").onclick = async () => {
      if (!confirm("Save this calibration? The pipeline uses it for the next matches (the old file is kept as a backup).")) return;
      try { await A("POST", `events/${ev}/calibration/${target}/accept`); st("saved"); route(false); } catch (e) { st(errMsg(e)); }
    };
    const sc = document.getElementById("showcur");
    if (sc) sc.onclick = async () => {
      try { const r = await job(ev, A("POST", `events/${ev}/calibration/${target}/overlay`), st); overlay = r.overlay; await loadFrame(); V.render(); st(`showing the saved ${r.file}`); }
      catch (e) { st(errMsg(e)); }
    };
    await loadState();
    if (!Object.keys(NP).length) NP = DEFAULT_POINTS;
    plist(); rep(); V.render();
  }

  // named field points (same as live/calibrate.py named_points(); the server's list wins after "detect")
  const DEFAULT_POINTS = (() => {
    const P = {}, L = 16.541, Wd = 8.069;
    const add = (k, xyz, label, group) => { P[k] = { xyz, label, group }; };
    add("corner_blue_bo", [0, 0, 0], "Blue wall corner, blue-outpost side", "field");
    add("corner_blue_ro", [0, Wd, 0], "Blue wall corner, red-outpost side", "field");
    add("corner_red_ro", [L, Wd, 0], "Red wall corner, red-outpost side", "field");
    add("corner_red_bo", [L, 0, 0], "Red wall corner, blue-outpost side", "field");
    add("centre_bo", [L / 2, 0, 0], "Centre line end, blue-outpost side", "lines");
    add("centre_ro", [L / 2, Wd, 0], "Centre line end, red-outpost side", "lines");
    add("centre", [L / 2, Wd / 2, 0], "Field centre (centre line midpoint)", "lines");
    add("line_blue_bo", [4.028, 0, 0], "Blue alliance-zone line end, blue-outpost side", "lines");
    add("line_blue_ro", [4.028, Wd, 0], "Blue alliance-zone line end, red-outpost side", "lines");
    add("line_red_ro", [12.513, Wd, 0], "Red alliance-zone line end, red-outpost side", "lines");
    add("line_red_bo", [12.513, 0, 0], "Red alliance-zone line end, blue-outpost side", "lines");
    for (let i = 1; i <= 32; i++) add("tag_" + i, null, `AprilTag ${i} centre`, "tags");
    return P;
  })();

  const DEFAULT_LINES = {
    edge_ro: { label: "Carpet edge along the red-outpost side (long wall)" }, edge_bo: { label: "Carpet edge along the blue-outpost side (long wall)" },
    edge_blue: { label: "Carpet edge at the blue alliance wall" }, edge_red: { label: "Carpet edge at the red alliance wall" },
    centerline: { label: "Centre line (white tape)" }, zone_blue: { label: "Blue alliance-zone line" }, zone_red: { label: "Red alliance-zone line" } };

  const CSS = `
    .vs-row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
    .vs-form { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
    .vs-form input { background:var(--panel2); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:6px 8px; font:inherit; font-size:13px; }
    .vs-src td { vertical-align:top; } .vs-src code, .card code { font-size:11.5px; color:var(--ink-2); word-break:break-all; }
    .vs-src td:first-child { max-width:520px; } .vs-tw { overflow-x:auto; }
    .vs-edit { display:grid; grid-template-columns: minmax(0, 1fr) 300px; gap:14px; margin-top:10px; }
    @media (max-width: 900px) { .vs-edit { grid-template-columns: 1fr; } }
    .vs-wrap { position:relative; min-width:0; }
    .vs-canvas { display:block; width:100%; border-radius:6px; background:#1b2330; cursor:crosshair; user-select:none; }
    .vs-lens { position:absolute; top:8px; display:none; border:1px solid #fff; border-radius:6px; pointer-events:none; box-shadow:0 2px 12px rgba(0,0,0,.6); }
    .vs-side { font-size:13px; max-height:78vh; overflow:auto; }
    .vs-sw { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
    .vs-ri { display:flex; gap:5px; align-items:center; padding:4px; border-radius:6px; margin-top:4px; flex-wrap:wrap; }
    .vs-ri.on { background:var(--panel2); outline:1px solid var(--accent); }
    .vs-ri input { background:var(--panel2); color:var(--text); border:1px solid var(--line); border-radius:5px; padding:3px 5px; font-size:12px; }
    .vs-ri select { padding:3px; font-size:12px; }
    .vs-sug { border:1px dashed var(--line); border-radius:8px; padding:6px; margin:8px 0; }
    .vs-pg { margin-top:8px; } .vs-pt { padding:3px 6px; border-radius:5px; cursor:pointer; font-size:12.5px; }
    .vs-pt:hover { background:var(--panel2); } .vs-pt.on { background:var(--panel2); outline:1px solid var(--accent); } .vs-pt.set { color:var(--caution); }
    .chip.set { color:var(--caution); }
    .vs-kv { margin-top:8px; line-height:1.55; } .vs-warn { color:var(--warn); }
    button.small { padding:2px 8px; font-size:12px; }
    .vs-src td:first-child { white-space:normal; overflow-wrap:anywhere; min-width:200px; } .vs-src td.vs-c { text-align:center; }
    .vs-form input:not([type=checkbox]), .vs-num { background:var(--surface); color:var(--ink); border:1px solid var(--line-strong); border-radius:var(--r-s);
      padding:6px 10px; font:inherit; font-size:var(--fs-foot); min-height:34px; min-width:0; }
    .vs-form input:not([type=checkbox]):focus-visible, .vs-num:focus-visible { outline:2px solid var(--accent); outline-offset:0; border-color:var(--accent); }
    .vs-form select { min-height:34px; } .vs-num { width:76px; }
    .ls-card { margin-top:12px; display:grid; gap:8px; }
    .ls-alert { display:flex; gap:6px; align-items:center; color:var(--caution); font-weight:600; font-size:var(--fs-foot); } .ls-alert.bad { color:var(--bad); }
    .ls-kv { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:4px 14px; margin:0; font-size:var(--fs-foot); }
    .ls-kv div { display:contents; } .ls-kv dt { color:var(--ink-2); } .ls-kv dd { margin:0; min-width:0; overflow-wrap:anywhere; }
    .ls-kv .team { font-weight:600; font-variant-numeric:tabular-nums; }
    .ls-errs summary { cursor:pointer; font-size:var(--fs-foot); color:var(--ink-2); } .ls-errs ul { margin:6px 0 0; padding-left:18px; font-size:var(--fs-cap); }
    @media (max-width: 1000px) {
      .vs-tw { overflow:visible; }
      .vs-src thead { display:none; }
      .vs-src, .vs-src tbody, .vs-src tr { display:block; }
      .vs-src tr { padding:10px 0; border-bottom:1px solid var(--line); } .vs-src tr:last-child { border-bottom:0; }
      table.vs-src td { display:flex; align-items:center; gap:10px; padding:3px 0; border:0; white-space:normal; min-width:0; }
      table.vs-src td:first-child { display:block; max-width:none; min-width:0; padding-bottom:6px; }
      .vs-src td[data-l]::before { content:attr(data-l); flex:0 0 96px; color:var(--ink-2); font-size:var(--fs-cap); font-weight:400; }
      .vs-src td.vs-c { text-align:left; } .vs-src td.vs-x { justify-content:flex-end; }
    }`;
})();
