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
  const TABS = [["sources", "Videos"], ["layout", "Layout"], ["calib", "Calibration"]];
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

  // ---------------------------------------------------------------- 1. videos: the per-match plan (live/videoplan.py)
  // Every candidate upload of a match is checked once (a few low-res frames) and classified wide / broadcast / station cams;
  // the worker picks per match. This tab shows the coverage, explains each choice and takes the rare override.
  const TYPES = [["wide", "Wide", "Wide"], ["broadcast", "Broadcast", "Bcast"], ["stations", "Station cams", "Stn"]];
  const TYPE_L = { wide: "Wide", broadcast: "Broadcast", stations: "Station cams" };
  const TYPE_TIP = { wide: "fixed overhead camera, no cuts: what the tracker needs",
                     broadcast: "program feed that cuts between cameras: robots are tracked only while the overhead camera is on air",
                     stations: "the overhead view with the two driver-station cameras inset in the bottom corners" };
  const mmss = s => s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "–";
  const ico = (k, cls, label) => `<span class="vp-i ${cls || ""}"${label ? ` role="img" aria-label="${h(label)}" title="${h(label)}"` : ' aria-hidden="true"'}>${CS.icon(k)}</span>`;
  const ytId = s => /^[A-Za-z0-9_-]{11}$/.test(s || "") ? s : null;

  function planCounts(P) {
    const M = (P && P.plan && P.plan.matches) || {}, ov = (P && P.overrides) || {};
    const keys = Object.keys(M).filter(k => M[k].played || (M[k].candidates || []).length || M[k].live);
    const has = (e, t) => (e.candidates || []).some(c => c.type === t && !c.excluded);
    return {
      keys, n: keys.length,
      wide: keys.filter(k => has(M[k], "wide")).length, broadcast: keys.filter(k => has(M[k], "broadcast")).length,
      stations: keys.filter(k => M[k].stations).length, live: keys.filter(k => M[k].live).length,
      limited: keys.filter(k => M[k].limited).length, missing: keys.filter(k => !M[k].track && !(M[k].live && M[k].live.trackable)).length,
      nowide: keys.filter(k => !has(M[k], "wide")).length, problems: keys.filter(k => (M[k].problems || []).length).length,
      over: keys.filter(k => ov[k]).length, dups: keys.reduce((a, k) => a + (M[k].dups || 0), 0),
      pending: keys.reduce((a, k) => a + (M[k].pending || 0), 0),
    };
  }
  const FILTERS = [["all", "All"], ["nowide", "Missing wide"], ["limited", "Limited tracking"], ["problems", "Problems"], ["over", "Overridden"]];
  const keep = (f, e, ov) => f === "all" || (f === "nowide" ? !(e.candidates || []).some(c => c.type === "wide" && !c.excluded)
    : f === "limited" ? !!e.limited : f === "problems" ? (e.problems || []).length > 0 : f === "over" ? !!ov : true);

  function cellHtml(e, t) {
    const cs = (e.candidates || []).filter(c => c.type === t && !c.excluded);
    const pend = (e.candidates || []).some(c => c.pending);
    const dup = cs.filter(c => c.dup_of).length;
    const chosen = t === "stations" ? !!e.stations && (e.candidates || []).some(c => c.id === e.stations && c.type === "stations")
      : e.track_type === t;
    if (!cs.length && !(t === "stations" && e.stations)) return pend ? `<span class="vp-pend" title="being checked">…</span>` : `<span class="vp-none" aria-label="none">·</span>`;
    let s = chosen ? ico("starOn", "on", t === "stations" ? "station cams from this" : "tracked from this")
      : ico("check", "ok", `${cs.length} available`);
    if (e.track_type === t && e.limited) s += ico("warn", "lim", "tracked from this: limited tracking");
    if (cs.length > 1) s += `<span class="vp-n" title="${cs.length} uploads${dup ? `, ${dup} duplicate${dup > 1 ? "s" : ""}` : ""}">${cs.length}${dup ? `<i>dup ${dup}</i>` : ""}</span>`;
    return s;
  }
  function liveCell(e) {
    const L = e.live; if (!L) return `<span class="vp-none" aria-label="none">·</span>`;
    return L.trackable && !e.track ? ico("starOn", "on", "tracked from the livestream clip") : ico("check", "ok", L.clip ? "livestream clip" : "found in the livestream (no clip)");
  }
  function usesHtml(e) {
    if (e.track) return `<span class="vp-use ${e.limited ? "lim" : ""}">${h(TYPE_L[e.track_type] || e.track_type)}${e.pinned ? ` <span class="pill">pinned</span>` : ""}</span>`;
    if (e.live && e.live.trackable) return `<span class="vp-use">Livestream</span>`;
    return e.pending ? `<span class="muted">checking…</span>` : `<span class="vp-use none">none</span>`;
  }

  // ---------------------------------------------------------------- match video mode (sources.json video_mode)
  // auto = per-match uploads + the livestream clip when trackable; livestream = offseason, no uploads: TBA gives the match
  // list and every match is tracked from the recorded livestream (GET/PUT events/<ev>/video-mode, with a readiness checklist).
  const VM_HELP = `<span class="hl"><b>Uploads + livestream</b> (normal events): each match is tracked from its per-match YouTube upload (a wide camera upload first). The livestream gives fast results and is tracked only when its camera is calibrated.</span>
    <span class="hl"><b>Livestream only</b> (offseason events without uploads): TBA only gives the match list, lineups and results. Every match is tracked from the recorded livestream, which has to show a fixed, wide view of the field: add the stream, draw its field box and calibrate it once. No upload is ever looked up.</span>
    <span class="hl">A match the stream analyser can't read off the broadcast overlay is cut from the recording by TBA's match start time instead.</span>`;
  const VM_ICON = { ok: ["check", "ok", "done"], warn: ["warn", "lim", "check this"], bad: ["ban", "bad", "missing"] };
  function vmCard(ev, V) {
    if (!V) return "";
    const live = V.video_mode === "livestream", C = V.checks || [];
    const nBad = C.filter(c => c.state === "bad").length, sid = V.stream_source;
    const link = c => !c.tab || c.state === "ok" || c.tab === "sources" ? "" :
      `<a class="vm-go" href="#/${ev}/sources/${c.tab}${c.tab === "layout" && sid ? "/" + encodeURIComponent(sid) : ""}">${c.tab === "layout" ? "Draw boxes" : "Calibrate"}</a>`;
    const fix = c => c.key !== "source" || c.state === "ok" ? "" : c.state === "warn" && sid
      ? `<button type="button" class="small" id="vmtrack">Use it as the tracked wide stream</button>`
      : `<form class="vs-form vm-add" id="vmadd"><input name="u" required aria-label="Livestream URL" placeholder="Livestream URL: YouTube /live or watch link, Twitch channel…"><button>Add stream</button></form>`;
    return `<section class="card vm-card ${live ? "vm-on" : ""}" aria-labelledby="vmh">
      <div class="vm-head"><h2 id="vmh">Match video</h2>
        <div class="vm-seg" role="group" aria-label="Match video mode">
          <button type="button" class="chip ${!live ? "on" : ""}" data-vm="auto" aria-pressed="${!live}">Uploads + livestream</button>
          <button type="button" class="chip ${live ? "on" : ""}" data-vm="livestream" aria-pressed="${live}">Livestream only <span class="muted">· TBA match list</span></button>
        </div>${CS.help(VM_HELP, "About the match video mode")}<span id="vmst" class="muted small" role="status"></span></div>
      ${live ? `<p class="vm-sub small">${nBad ? `<b>${nBad} step${nBad > 1 ? "s" : ""} left</b> before matches can be tracked from the stream.` : `<b>Ready.</b> Each match is tracked from its livestream clip as soon as it is cut.`}
          <span class="muted">Uploads are not looked up for this event.</span></p>
        <ol class="vm-chk">${C.map(c => { const [ic, cl, al] = VM_ICON[c.state] || VM_ICON.warn;
          return `<li class="vm-${c.state}">${ico(ic, cl, al)}<div class="vm-t"><div><b>${h(c.label)}</b> <span class="muted small">${h(c.detail || "")}</span></div>${fix(c)}</div>${link(c)}</li>`; }).join("")}</ol>`
        : `<p class="vm-sub muted small">Offseason event with only a livestream? Switch to <b>Livestream only</b>.</p>`}
    </section>`;
  }
  function vmWire(ev, box, V, redraw) {
    const say = t => { const s = box.querySelector("#vmst"); if (s) s.textContent = t; };
    box.querySelectorAll("[data-vm]").forEach(b => b.onclick = async () => {
      if (b.dataset.vm === V.video_mode) return;
      say("saving…");
      try { await A("PUT", `events/${ev}/video-mode`, { video_mode: b.dataset.vm }); route(false); } catch (x) { say(errMsg(x)); }
    });
    const tb = box.querySelector("#vmtrack");
    if (tb) tb.onclick = async () => {
      try { await A("PATCH", `events/${ev}/sources/${encodeURIComponent(V.stream_source)}`, { role: "wide", primary: true }); redraw(); } catch (x) { say(errMsg(x)); }
    };
    const f = box.querySelector("#vmadd");
    if (f) f.onsubmit = async e => {
      e.preventDefault(); say("adding…");
      try { await A("POST", `events/${ev}/sources`, { url: f.u.value.trim(), role: "wide", label: "Event livestream", primary: true }); route(false); }
      catch (x) { say(errMsg(x)); }
    };
  }

  async function sourcesTab(ev, el) {
    const [P, d, V0] = await Promise.all([A("GET", `events/${ev}/video-plan`).catch(e => ({ error: e })), A("GET", `events/${ev}/sources`),
                                          A("GET", `events/${ev}/video-mode`).catch(() => null)]);
    let V = V0;
    const liveOnly = !!V && V.video_mode === "livestream";
    const S = d.sources || [];
    const M = (P.plan && P.plan.matches) || {};
    let filt = "all", st0 = null;
    try { filt = sessionStorage.getItem("cs:vpfilt") || "all"; } catch (e) { }
    if (!FILTERS.some(f => f[0] === filt)) filt = "all";
    const help = `<span class="hl">Every upload linked to a match is checked once: a few small frames, no download. It is classified as <b>wide</b> (fixed overhead camera), <b>broadcast</b> (cuts between cameras) or <b>station cams</b> (overhead view with the two driver-station cameras inset).</span>
      <span class="hl">Per match the tracker uses the best wide video, else a station-cam or broadcast video (<b>limited tracking</b>: insets hide the near corners, or cuts hide the field). Station cams feed the driver-station pass. Identical uploads count once; a title naming another match is never used.</span>
      <span class="hl">Open a match to see its videos and, rarely, pin or exclude one. The worker rebuilds this every poll; <b>Check videos</b> checks everything now.</span>`;
    el.innerHTML = `<div id="vmode">${vmCard(ev, V)}</div>
      <section class="card vp-sum" aria-label="Video coverage" ${liveOnly ? "hidden" : ""}><div id="vpsum"></div>
        <div class="vs-row vp-acts0"><button id="vpcheck" type="button">Check videos</button><button id="disc" type="button">Discover from TBA</button>
          ${CS.help(help, "How match videos are chosen")}<span id="dst" class="muted small" role="status"></span></div></section>
      <div id="lsrec"></div>
      <section class="card vp-cov" aria-labelledby="vpcovh" ${liveOnly ? "hidden" : ""}><div class="vp-covh"><h2 id="vpcovh">Matches</h2><div class="vp-filt" role="group" aria-label="Show matches" id="vpfilt"></div></div>
        <div id="vpgrid"></div>
        <p class="vp-legend muted small">${ico("starOn", "on")} chosen · ${ico("check", "ok")} available · ${ico("warn", "lim")} limited tracking · <span class="vp-n">2<i>dup 1</i></span> uploads, duplicates · <span class="vp-pend">…</span> being checked</p></section>
      <details class="card vp-adv" id="vpadv"><summary>Advanced: sources <span class="muted small">${S.length} · title families, webcasts, pasted URLs</span></summary><div id="vpadvb"></div></details>
      <dialog class="vp-sheet" id="vpsheet" aria-labelledby="vpsh"></dialog>`;
    // ---- summary line (+ livestream state)
    let lsSt = null;
    const drawSum = () => {
      const c = planCounts(P), box = document.getElementById("vpsum"); if (!box) return;
      if (P.error) { box.innerHTML = `<div class="muted">${P.error.status === 503 ? h(errMsg(P.error)) : "Couldn't load the video plan: " + h(errMsg(P.error))}</div>`; return; }
      if (!P.plan) { box.innerHTML = `<div class="vp-line"><b>No video plan yet.</b> <span class="muted">The worker builds it on its next poll, or press <b>Check videos</b>.</span></div>`; return; }
      const of = n => `${n}<span class="muted">/${c.n}</span>`;
      const ls = lsSt && lsSt.state ? `${(LS_LABEL[lsSt.state] || lsSt.state).toLowerCase()}${lsSt.state === "recording" && lsSt.behind_live_s != null ? ` (${dur(lsSt.behind_live_s)} behind)` : ""}` : "–";
      box.innerHTML = `<div class="vp-line"><span title="${h(TYPE_TIP.wide)}"><b>Wide</b> ${of(c.wide)}</span><span class="vp-dot" aria-hidden="true">·</span>
          <span title="${h(TYPE_TIP.broadcast)}"><b>Broadcast</b> ${of(c.broadcast)}</span><span class="vp-dot" aria-hidden="true">·</span>
          <span title="${h(TYPE_TIP.stations)}"><b>Station cams</b> ${of(c.stations)}</span><span class="vp-dot" aria-hidden="true">·</span>
          <span><b>Livestream</b> ${h(ls)}</span></div>
        <div class="vp-subl muted small">Tracker: ${c.n - c.missing - c.limited} wide${c.limited ? ` · <span class="vp-lim">${c.limited} limited</span>` : ""}${c.missing ? ` · ${c.missing} without a usable video` : ""}
          ${c.dups ? ` · ${c.dups} duplicate upload${c.dups > 1 ? "s" : ""} ignored` : ""}${c.pending ? ` · ${c.pending} video${c.pending > 1 ? "s" : ""} not checked yet` : ""}
          · built ${h(when(P.plan.built_at))}</div>`;
    };
    drawSum();
    // ---- coverage grid
    const drawGrid = () => {
      const c = planCounts(P), g = document.getElementById("vpgrid"), fb = document.getElementById("vpfilt"); if (!g) return;
      const cnt = { all: c.n, nowide: c.nowide, limited: c.limited, problems: c.problems, over: c.over };
      fb.innerHTML = FILTERS.map(([k, t]) => `<button type="button" class="chip ${k === filt ? "on" : ""}" data-f="${k}" aria-pressed="${k === filt}">${t} <span class="muted">${cnt[k]}</span></button>`).join("");
      if (!P.plan) { g.innerHTML = `<div class="empty small">Nothing yet.</div>`; return; }
      const ov = P.overrides || {};
      const ks = c.keys.filter(k => keep(filt, M[k], ov[k]));
      const row = k => { const e = M[k];
        return `<tr data-k="${h(k)}" class="${(e.problems || []).length ? "prob" : ""}"><th scope="row"><button type="button" class="vp-mk" data-k="${h(k)}" aria-haspopup="dialog">${h(e.label || k)}</button>${ov[k] ? ico("admin", "ov", "overridden by an admin") : ""}</th>
          ${TYPES.map(([t, l]) => `<td data-l="${l}">${cellHtml(e, t)}</td>`).join("")}<td data-l="Live clip">${liveCell(e)}</td><td class="vp-uc">${usesHtml(e)}</td></tr>`; };
      const qs = ks.filter(k => /_qm\d+$/.test(k)), po = ks.filter(k => !/_qm\d+$/.test(k));
      const sec = (t, L) => L.length ? `<tbody><tr class="vp-sec"><th colspan="6" scope="rowgroup">${t} <span class="muted">${L.length}</span></th></tr>${L.map(row).join("")}</tbody>` : "";
      g.innerHTML = ks.length ? `<div class="vp-gw"><table class="vp-grid"><thead><tr><th scope="col">Match</th>${TYPES.map(([t, l, s]) => `<th scope="col" title="${h(TYPE_TIP[t])}"><span class="vp-hl">${l}</span><span class="vp-hs" aria-hidden="true">${s}</span></th>`).join("")}
          <th scope="col"><span class="vp-hl">Live clip</span><span class="vp-hs" aria-hidden="true">Live</span></th><th scope="col" class="vp-uc">Tracker uses</th></tr></thead>${sec("Qualifications", qs)}${sec("Playoffs", po)}</table></div>`
        : `<div class="empty small">No matches ${filt === "all" ? "with videos yet" : "in this filter"}.</div>`;
    };
    drawGrid();
    document.getElementById("vpfilt").onclick = e => { const b = e.target.closest("[data-f]"); if (!b) return; filt = b.dataset.f; try { sessionStorage.setItem("cs:vpfilt", filt); } catch (x) { } drawGrid(); };
    document.getElementById("vpgrid").onclick = e => { const tr = e.target.closest("tr[data-k]"); if (tr) openSheet(tr.dataset.k, e.target.closest("button")); };
    // ---- livestream recorder (card + summary), refreshed while this tab is open
    const lsEl = document.getElementById("lsrec"), vmEl = document.getElementById("vmode");
    const vmDraw = async (fetch) => {
      if (fetch) { try { V = await A("GET", `events/${ev}/video-mode`); } catch (x) { return; } }
      if (!V || !vmEl.isConnected) return;
      if (fetch && vmEl.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;   // don't eat a URL being typed
      vmEl.innerHTML = vmCard(ev, V); vmWire(ev, vmEl, V, () => vmDraw(true));
    };
    vmDraw(false);
    const lsDraw = async () => { const st = await liveRec.load(ev); lsSt = st; if (lsEl.isConnected) { lsEl.innerHTML = liveRec.card(st, ev); drawSum(); } };
    lsDraw(); const lsIv = setInterval(() => { lsDraw(); if (liveOnly) vmDraw(true); }, 15000); cleanup.push(() => clearInterval(lsIv));
    // ---- actions
    const dst = t => { const s = document.getElementById("dst"); if (s) s.textContent = t; };
    document.getElementById("vpcheck").onclick = async e => {
      e.target.disabled = true;
      try { dst("checking videos…"); const r = await job(ev, A("POST", `events/${ev}/video-plan/check`, {}), dst); dst(`${r.checked} video${r.checked === 1 ? "" : "s"} checked`); route(false); }
      catch (x) { dst(errMsg(x)); e.target.disabled = false; }
    };
    document.getElementById("disc").onclick = async () => {
      try { dst("asking TBA…"); await job(ev, A("POST", `events/${ev}/sources/discover`), dst); route(false); }
      catch (x) { dst(errMsg(x)); }
    };
    // ---- the per-match sheet: candidates, why, pin / exclude
    const dlg = document.getElementById("vpsheet");
    let opener = null;
    function openSheet(k, from) {
      opener = from || null; drawSheet(k);
      try { dlg.showModal(); } catch (x) { dlg.setAttribute("open", ""); }
      const f = dlg.querySelector(".vp-x"); if (f) f.focus();
    }
    dlg.addEventListener("close", () => { if (opener && opener.isConnected) opener.focus(); });
    dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); });
    function drawSheet(k) {
      const e = M[k] || { candidates: [] }, ov = (P.overrides || {})[k] || null, L = e.live;
      const cs = (e.candidates || []).slice().sort((a, b) => (b.id === e.track) - (a.id === e.track) || (b.id === e.stations) - (a.id === e.stations)
        || ["wide", "stations", "broadcast", undefined, null].indexOf(a.type) - ["wide", "stations", "broadcast", undefined, null].indexOf(b.type));
      const tr = cs.find(c => c.id === e.track);
      const head = tr ? `Tracker uses <b>${h(tr.title || tr.id)}</b> <span class="muted">(${h(TYPE_L[tr.type] || "?")}${e.pinned ? ", pinned" : ", chosen automatically"})</span>${e.limited ? `<div class="vp-limn">${ico("warn", "lim")} Limited tracking: ${h(e.limited)}.</div>` : ""}`
        : L && L.trackable ? "Tracked from the livestream clip." : e.pending ? "Videos are still being checked." : "No usable video for the tracker yet.";
      const card = c => {
        const flags = [];
        if (c.id === e.track) flags.push(`<span class="vp-f on">${ico("starOn", "on")} tracking${e.pinned ? " (pinned)" : ""}</span>`);
        if (c.id === e.stations) flags.push(`<span class="vp-f on">${ico("starOn", "on")} station cams${e.pinned_stations ? " (pinned)" : ""}</span>`);
        if (c.dup_of) flags.push(`<span class="vp-f">duplicate of ${h(c.dup_of)}</span>`);
        if (c.excluded) flags.push(`<span class="vp-f bad">${ico("ban")} ${h(c.excluded)}</span>`);
        if (c.title_ok === false && !c.excluded) flags.push(`<span class="vp-f bad">title names another match</span>`);
        if (c.pending) flags.push(`<span class="vp-f">not checked yet</span>`);
        const excl = ov && (ov.exclude || []).includes(c.id), url = `https://youtu.be/${encodeURIComponent(c.id)}`;
        return `<article class="vp-cand ${c.id === e.track ? "chosen" : ""} ${c.excluded ? "excl" : ""}">
          <a class="vp-th" href="${h(url)}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true"><img src="https://i.ytimg.com/vi/${h(c.id)}/mqdefault.jpg" alt="" loading="lazy" width="160" height="90"></a>
          <div class="vp-ci"><div class="vp-ct"><a href="${h(url)}" target="_blank" rel="noopener noreferrer">${h(c.title || c.id)}<span class="vh"> (opens YouTube)</span></a></div>
            <div class="muted small">${h(c.channel || "?")} · ${mmss(c.duration)}${c.height ? ` · ${h(c.height)}p` : ""} · ${(c.via || []).includes("tba") ? "linked on TBA" : (c.via || []).includes("admin") ? "added by an admin" : "from a channel / playlist"}</div>
            ${c.type ? `<div class="vp-ty"><span class="pill vp-p ${h(c.type)}" title="${h(TYPE_TIP[c.type] || "")}">${h(TYPE_L[c.type] || c.type)}</span> <span class="muted small">${c.conf != null ? Math.round(c.conf * 100) + "% · " : ""}${h(c.why || "")}</span></div>` : ""}
            ${flags.length ? `<div class="vp-fl">${flags.join("")}</div>` : ""}
            <div class="vp-ab">${c.id !== e.track ? `<button type="button" class="small" data-act="pin" data-v="${h(c.id)}">Use for tracking</button>` : ""}
              ${c.type === "stations" && c.id !== e.stations ? `<button type="button" class="small" data-act="pst" data-v="${h(c.id)}">Use for station cams</button>` : ""}
              <button type="button" class="small" data-act="${excl ? "inc" : "exc"}" data-v="${h(c.id)}">${excl ? "Include again" : "Exclude"}</button></div></div></article>`;
      };
      dlg.innerHTML = `<div class="vp-shh"><h2 id="vpsh">${h(e.label || k)} <span class="muted">videos</span></h2><button type="button" class="tb-btn vp-x" aria-label="Close">${CS.icon("close")}</button></div>
        <div class="vp-shb"><div class="vp-head">${head}</div>
          ${(e.problems || []).length ? `<div class="vp-prob">${e.problems.map(p => `<div>${ico("warn", "lim")} ${h(p)}</div>`).join("")}</div>` : ""}
          ${L ? `<div class="vp-live small"><b>Livestream</b> ${L.clip && CS.mediaUrl && CS.mediaUrl(L.clip) ? `<a href="${h(CS.mediaUrl(L.clip))}" target="_blank" rel="noopener">clip</a>` : "match found, no clip"} · ${L.trackable ? "trackable (the stream camera is calibrated)" : h(L.note || "not trackable")}</div>` : ""}
          ${cs.length ? cs.map(card).join("") : `<div class="empty small">No video linked to this match yet.</div>`}
          <form class="vp-add vs-form" id="vpadd"><input name="u" placeholder="Add a YouTube link for this match…" aria-label="YouTube link to add" required><button>Add</button></form>
          <div class="vs-row vp-shf">${ov ? `<button type="button" id="vpauto">Back to automatic</button>` : ""}<button type="button" id="vprck">Check these videos again</button><span id="vpst" class="muted small" role="status"></span></div>
          ${ov ? `<p class="muted small">Overridden by ${h(ov.by || "an admin")} ${h(when(ov.at))}.</p>` : ""}</div>`;
      const say = t => { const s = document.getElementById("vpst"); if (s) s.textContent = t; };
      const put = async next => {
        say("saving…");
        try {
          const r = await A("PUT", `events/${ev}/video-plan/matches/${encodeURIComponent(k)}`, next);
          P.overrides = P.overrides || {}; if (r.override) P.overrides[k] = r.override; else delete P.overrides[k];
          if (r.entry) M[k] = Object.assign(r.entry, { label: (M[k] || {}).label || r.entry.label, played: (M[k] || {}).played });
          drawSheet(k); drawGrid(); drawSum();
          const f = dlg.querySelector(".vp-x"); if (f) f.focus();
        } catch (x) { say(errMsg(x)); }
      };
      const cur = () => ({ pin: (ov && ov.pin) || null, pin_stations: (ov && ov.pin_stations) || null, exclude: ((ov && ov.exclude) || []).slice(), add: ((ov && ov.add) || []).slice() });
      dlg.querySelector(".vp-x").onclick = () => dlg.close();
      dlg.querySelectorAll("[data-act]").forEach(b => b.onclick = () => {
        const n = cur(), v = b.dataset.v;
        if (b.dataset.act === "pin") { n.pin = v; n.exclude = n.exclude.filter(x => x !== v); }
        if (b.dataset.act === "pst") n.pin_stations = v;
        if (b.dataset.act === "exc") { n.exclude.push(v); if (n.pin === v) n.pin = null; if (n.pin_stations === v) n.pin_stations = null; }
        if (b.dataset.act === "inc") n.exclude = n.exclude.filter(x => x !== v);
        put(n);
      });
      const au = document.getElementById("vpauto"); if (au) au.onclick = () => put({ pin: null, pin_stations: null, exclude: [], add: [] });
      document.getElementById("vpadd").onsubmit = ev2 => { ev2.preventDefault(); const n = cur(); n.add.push(ev2.target.u.value.trim()); put(n); };
      document.getElementById("vprck").onclick = async () => {
        const ids = (e.candidates || []).map(c => c.id).filter(ytId);
        if (!ids.length) return;
        try { say("checking…"); await job(ev, A("POST", `events/${ev}/video-plan/check`, { only: ids }), say); route(false); } catch (x) { say(errMsg(x)); }
      };
    }
    // ---- advanced: the raw sources (title families, webcasts, pasted URLs) -- the old Sources tab
    advancedSources(ev, document.getElementById("vpadvb"), d);
  }

  function advancedSources(ev, el, d) {
    const S = d.sources || [];
    const cov = s => {
      const c = s.coverage || {};
      if (s.kind === "per_match") return `${s.n_videos || 0} match video${s.n_videos === 1 ? "" : "s"}`;
      if (c.mode === "range") return `${h(c.from || "start")} – ${h(c.to || "end")}`;
      if (c.mode === "matches" && (c.matches || []).length) return `${c.matches.length} matches`;
      return "whole event";
    };
    el.innerHTML = `
      <p class="muted small vp-advn">Where candidate videos come from: TBA webcasts (recorded automatically on their day), upload families TBA links (grouped by title) and anything pasted below.
        Switching a family <b>off</b> excludes its videos from every match. The per-match choice above does not need roles; <i>Tracked</i> only matters for matches the plan has not checked yet.</p>
      <div class="vs-tw">
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
      <div style="margin-top:12px">
        <h3 style="margin:0 0 8px">Add a source</h3>
        <form id="add" class="vs-form">
          <input name="url" required placeholder="YouTube / Twitch URL (livestream, video, playlist, channel), .m3u8/.mp4 URL, or /media/… file" style="flex:3 1 380px">
          <select name="role">${d.roles.map(r => `<option title="${h(ROLE_TIP[r])}">${r}</option>`).join("")}</select>
          <input name="label" placeholder="Label (optional)…" style="flex:1 1 160px">
          <label class="small"><input type="checkbox" name="primary"> tracked video</label>
          <button>Add</button>${CS.help("Alternate angles (e.g. a team's own overhead recording) can be added the same way; mark the one the tracker should use as <i>tracked</i>.", "About alternate angles")}<span id="ast" class="muted small"></span>
        </form>
      </div>
      <div class="muted small" style="margin-top:12px">
        Fallback (matches not checked yet): prefer <code>${h((d.video_config || {}).prefer || "^Wide Match (default)")}</code>${(d.video_config || {}).reject ? `, skip <code>${h(d.video_config.reject)}</code>` : ""}${d.station_pattern ? ` · station-cam uploads <code>${h(d.station_pattern)}</code>` : ""}${d.livestream ? ` · livestream ${urlHtml(d.livestream, true)}` : ""}.
        ${d.jobs_inline ? "" : "Frame grabs and calibration run in the worker (its video job runner must be on)."}
        Next: <a href="#/${ev}/sources/layout">draw the layout</a>, then <a href="#/${ev}/sources/calib">calibrate the cameras</a>.
      </div>`;
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
    const [d, L, P] = await Promise.all([A("GET", `events/${ev}/sources`), A("GET", `events/${ev}/layout`),
                                         A("GET", `events/${ev}/video-plan`).catch(() => ({}))]);
    // boxes are drawn once per detected video type + channel (the plan's groups), not per title family;
    // the raw sources stay selectable for livestreams / pasted URLs
    const G = (P.groups || []).map(g => ({ ...g, label: `${g.label} (${g.n} match${g.n === 1 ? "" : "es"})` }))
      .sort((a, b) => ["wide", "stations", "broadcast"].indexOf(a.type) - ["wide", "stations", "broadcast"].indexOf(b.type) || b.n - a.n);
    const S0 = (d.sources || []).filter(s => !G.some(g => g.id === s.id));
    const S = [...G, ...S0];
    if (!S.length) { el.innerHTML = `<div class="empty">No videos yet: discover them on the <a href="#/${ev}/sources/sources">Videos</a> tab.</div>`; return; }
    const src = S.find(s => s.id === sid) || G.find(g => !g.has_layout) || G[0] || S.find(s => s.primary) || S[0];
    const optList = (list, sel) => list.map(s => `<option value="${h(s.id)}" ${s.id === sel ? "selected" : ""}>${h(s.label || s.id)}${s.origin === "auto" ? "" : ` (${h(s.role)})`}</option>`).join("");
    const opts = sel => (G.length ? `<optgroup label="Detected video types">${optList(G, sel)}</optgroup>` : "") +
      (S0.length ? `<optgroup label="Sources (advanced)">${optList(S0, sel)}</optgroup>` : "");
    const saved = ((L.layout || {}).sources || {})[src.id];
    let regions = saved ? saved.regions.map(r => ({ ...r })) : [];
    let sugg = [], sel = -1, kind = "field", W = saved ? saved.W : 1920, H = saved ? saved.H : 1080, dirty = false;
    el.innerHTML = `
      <div class="vs-row">
        <label class="small">Video <select id="srcsel">${opts(src.id)}</select></label>
        <label class="small">at <input id="ft" class="vs-num" type="number" step="1" min="0" placeholder="0" aria-label="Frame time in seconds"> s</label>
        <button id="grab">Grab frame</button>
        <button id="sug" title="graphics that never change, the 1 Hz clock, picture-in-picture rectangles, the field">Suggest boxes</button>
        <label class="small">compare with <select id="other"><option value="">(none)</option>${S.filter(s => s.id !== src.id).map(s => `<option value="${h(s.id)}">${h(s.label || s.id)}</option>`).join("")}</select></label>
        ${src.origin === "auto" ? CS.help(`<span class="hl">These boxes apply to every match whose chosen video is <b>${h(TYPE_L[src.type] || src.type)}</b> from ${h(src.channel || "this channel")}: draw them once per type.</span>`, "Which videos these boxes apply to") : ""}
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
