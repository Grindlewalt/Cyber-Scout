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
  // the same names as the video cards' "What it is" (SETUP-7): camera roles are never shown raw
  const ROLE_NAME = { wide: "Main / wide view", dynamic: "Broadcast", "alliance-side": "Station cam" };
  const TARGETS = [["overhead", "Overhead camera"], ["station_red", "Red station cam"], ["station_blue", "Blue station cam"]];
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
  // tool output (yt-dlp, ffmpeg) -> one plain sentence; the raw text stays behind a "Details" disclosure (SETUP-25).
  // First match wins: "could not resolve …: The channel is not currently live" is "not live", not "unreachable".
  const PLAIN = [
    [/not (currently )?live|is offline|is_upcoming|will begin in|premieres in|live event will begin|this live event has ended|stream (is )?over/i,
      "The stream isn't live right now: try again during the event, or use a recording of it."],
    [/\b(404|410)\b|not found|HTTP Error 4\d\d|Server returned 4\d\d|does not exist/i,
      "Couldn't open the video: its address answers “not found”. Check the link (Replace on its card)."],
    [/not a bot/i, "YouTube is refusing the server's downloads for now (its bot check): try again later, or update yt-dlp (Admin → Updates)."],
    [/private video|sign in to confirm|members-only|age-restricted|requires (a )?log ?in|HTTP Error 403|Server returned 403/i,
      "The video can't be read without a sign-in (private, members-only or age-restricted): use a public stream or recording."],
    [/unsupported url|no video formats|unable to extract|is not a valid url|invalid data found|could not find codec/i,
      "Cyber-Scout can't read this link as a video: use a YouTube or Twitch link of the stream, or a recording."],
    [/cannot resolve|name or service not known|name resolution|getaddrinfo|timed? ?out|connection (refused|reset)|unreachable|network is/i,
      "Couldn't reach the video from the server: check the link and the server's internet connection."],
    [/frame grabs? failed|could not read enough frames|could not grab|no frames?\b/i, "Couldn't read frames from this video."],
  ];
  const TECH = /ERROR:|\[[\w#:@ .-]+\]|Traceback|Errno|0x[0-9a-f]{6,}|\bffmpeg\b|yt-dlp|HTTP Error/i;
  function plainErr(e) {
    const raw = typeof e === "string" ? e : errMsg(e), hit = PLAIN.find(([re]) => re.test(raw));
    if (hit) return { text: hit[1], raw };
    return TECH.test(raw) ? { text: "Something went wrong reading the video.", raw } : { text: raw, raw: null };
  }
  const errHtml = e => { const p = plainErr(e);
    return `<span class="vw-et">${h(p.text)}</span>${p.raw ? ` <details class="vw-det"><summary>Details</summary><code>${h(p.raw)}</code></details>` : ""}`; };
  // ---- calibration in plain words.  New fits carry their own verdict + reasons (live/calibrate.py report()); these
  // translate the raw texts that still arrive: older wizard states and job errors, the job runner's hints.
  // calib-plain:start (tests/test_calib_robust.py runs this block in node)
  const CAL_PLAIN = [
    [/need more correspondences \((\d+) points?\)/i, m => `Not enough to fit the camera (${m[1]} tag corners and points): it needs at least 2 AprilTags, or field lines or points clicked by hand.`],
    [/pose initiali[sz]ation failed/i, "Couldn't find a camera position that fits these AprilTags and points together: one of them is probably on the wrong spot or has the wrong name. Leave one out, or click field lines."],
    [/unknown source (None|null|undefined)?\s*$/i, "No video is set as the main (wide) view of this event yet: add the event's stream or video on the Video page first."],
    [/unknown source (\S+)/i, m => `This video (${m[1]}) isn't one of the event's videos any more: reload the Video page.`],
    [/expired \(no job runner picked it up\)/i, "The worker isn't running video jobs: is it running?"],
    [/waiting for the worker's video job runner|sources\.job_loop/i, "waiting for the worker to pick it up (is it running?)"],
    [/^run detect \+ fit first|no fitted camera yet/i, "Fit the camera first, then save it."],
    [/^run detect first/i, "Read the video first (Detect tags)."],
    [/large residuals: some point is probably mislabelled/i, "Some AprilTags or points don't line up with the fit (over 4 px): one is probably on the wrong spot, has moved, or has the wrong name."],
    [/few correspondences/i, "Few AprilTags or points to fit: check the grid over the whole field."],
    [/all tags are on one flat face/i, "All the AprilTags found are on one flat face, so the camera's distance and lens can't be worked out: click field lines or points elsewhere on the field."],
    [/camera below 0\.5 m/i, "The camera comes out below 0.5 m: a clicked point is probably on the wrong spot."],
    [/^could not read (enough )?frames/i, "Couldn't read any frames from this video."],
    [/^no video for this source yet/i, "This video has nothing to read yet (no match upload found for it)."],
  ];
  function calPlain(t) {
    const s = String(t == null ? "" : t);
    for (const [re, out] of CAL_PLAIN) { const m = s.match(re); if (m) return typeof out === "function" ? out(m) : out; }
    return s;
  }
  // calib-plain:end
  const calErrHtml = e => { const raw = typeof e === "string" ? e : errMsg(e), t = calPlain(raw);
    return t !== raw ? `<span class="vw-et">${h(t)}</span>` : errHtml(raw); };
  // the first check of a pasted link, before any request (mirrors live/sources.py _checked / link_problem; the
  // server stays the judge): YouTube / Twitch links, a direct .mp4 / .m3u8 address, or a file on the server
  const LINK_EG = "e.g. youtube.com/@FIRSTWashington/live or twitch.tv/firstwa_red1";
  const MEDIA_EXT = /\.(m3u8|mp4|m4v|mov|mkv|webm|ts|flv)$/i;
  function linkProblem(v) {
    const u = (v || "").trim();
    if (!u) return "Paste a link first.";
    if (u.startsWith("/") || /^[A-Za-z0-9_-]{11}$/.test(u)) return "";
    if (/\s/.test(u) && !/^https?:\/\//i.test(u)) return `That isn't a link. Paste the event's stream or video link, ${LINK_EG}.`;
    let x;
    try { x = new URL(/^[a-z][a-z0-9+.-]*:/i.test(u) ? u : "https://" + u); } catch (e) { return "That link isn't valid: copy it again from the browser's address bar."; }
    const host = x.hostname.replace(/^(www|m)\./, "");
    if (/(^|\.)(youtube\.com|youtu\.be)$/.test(host)) return x.pathname.length > 1 || x.searchParams.get("v") || x.searchParams.get("list") ? ""
      : "That YouTube link doesn't point at a stream, video, playlist or channel. Open the stream on YouTube and copy the address from there, e.g. youtube.com/@FIRSTWashington/live or a youtube.com/watch?v=… link.";
    if (/(^|\.)twitch\.tv$/.test(host)) return x.pathname.length > 1 ? ""
      : "That Twitch link doesn't name a channel or a video: use twitch.tv/<channel> (e.g. twitch.tv/firstwa_red1) or twitch.tv/videos/<number>.";
    if (!/^https?:$/.test(x.protocol) || !/^[a-z]+:\/\//i.test(u)) return `${host && host.includes(".") ? host + " isn't YouTube or Twitch" : "That isn't a YouTube or Twitch link"}. Paste the event's stream or video link, ${LINK_EG}.`;
    return MEDIA_EXT.test(x.pathname) ? "" : `${host} isn't YouTube or Twitch. Paste the event's stream or video link from YouTube or Twitch (a direct video address ending in .mp4 or .m3u8 works too).`;
  }
  /** a form's field error: under the field, in the error colour, the field marked invalid (SETUP-6) */
  function fieldErr(f, t) {
    const inp = f.querySelector("input[name=u]"), m = f.querySelector(".vw-ferr"); if (!m) return;
    m.innerHTML = t ? (typeof t === "string" ? h(t) : t.status && t.status < 500 ? h(errMsg(t)) : errHtml(t)) : ""; m.hidden = !t;
    if (t) { inp.setAttribute("aria-invalid", "true"); inp.focus(); } else inp.removeAttribute("aria-invalid");
  }
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
  const LS_LABEL = { recording: "Recording", waiting: "Waiting for the stream", starting: "Starting the recorder", stream_over: "Stream over", idle: "Idle",
                     error: "Error", no_source: "No livestream source", disabled: "Off", stopped: "Stopped", not_running: "Not running" };
  const LS_ACTIVE = ["recording", "waiting", "starting", "error", "not_running"];
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
    // "start" pulses: starting up, or connected but no video yet (recording with a message)
    dot: st => st.state === "error" || st.state === "not_running" ? "bad" : liveRec.warn(st) ? "warn"
      : st.state === "starting" || (st.state === "recording" && st.message) ? "warn start" : st.state === "recording" ? "on" : "",
    /** one quiet line (Dashboard) */
    line(st, ev) {
      if (!liveRec.active(st)) return "";
      const w = liveRec.warn(st, true), lm = st.last_match;
      return `<div class="lsline" role="status"><span class="ls-dot ${liveRec.dot(st)}" aria-hidden="true"></span><span><b>Livestream</b> ${h((LS_LABEL[st.state] || st.state).toLowerCase())}</span>
        ${(st.state === "recording" || st.state === "starting") && st.message && !w ? `<span>${h(st.message)}</span>` : st.behind_live_s != null && st.state === "recording" && !w ? `<span>${dur(st.behind_live_s)} behind live</span>` : ""}
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
    $app.innerHTML = `<style>${CSS}</style><h1>Video setup ${CS.help("The event's videos and streams: what each one is, what was found on it, and its field setup (a field box and a camera calibration, once per camera). The pipeline reads it for every match.", "What video setup is for")}</h1>
      <div id="vs"></div>`;
    const el = document.getElementById("vs");
    try {
      await sourcesTab(ev, el, { tab, id: id && decodeURIComponent(id) });      // old tab links (layout / calib) open that video's Field setup
    } catch (e) {
      el.innerHTML = `<div class="empty">${e.status === 503 ? h(errMsg(e)) : e.status === 404 ? "No video-setup API on this server (static site)." : "Couldn't load: " + h(errMsg(e))}</div>`;
    }
  });

  // ---------------------------------------------------------------- 1. videos: the per-match plan (live/videoplan.py)
  // Every candidate upload of a match is checked once (a few low-res frames) and classified wide / broadcast / station cams;
  // the worker picks per match. This tab shows the coverage, explains each choice and takes the rare override.
  const TYPES = [["wide", "Main / wide", "Wide"], ["broadcast", "Broadcast", "Bcast"], ["stations", "Station cams", "Stn"]];
  const TYPE_L = { wide: "Main / wide", broadcast: "Broadcast", stations: "Station cams" };
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
  // The server suggests one (suggested / suggested_why): livestream only for an offseason event with no match videos on TBA.
  const VM_HELP = `<span class="hl"><b>Uploads + livestream</b> (normal events): each match is tracked from its per-match YouTube upload (a wide camera upload first). The livestream gives fast results and is tracked only when its camera is calibrated.</span>
    <span class="hl"><b>Livestream only</b> (offseason events without uploads): TBA only gives the match list, lineups and results. Every match is tracked from the recorded livestream, which has to show a fixed, wide view of the field: add the stream, then its field box and calibration (Field setup on the stream's card). No upload is ever looked up.</span>
    <span class="hl">A match the stream analyser can't read off the broadcast overlay is cut from the recording by TBA's match start time instead.</span>`;
  const VM_ICON = { ok: ["check", "ok", "done"], warn: ["warn", "lim", "check this"], bad: ["ban", "bad", "missing"] };
  const VM_NAME = { auto: "Uploads + livestream", livestream: "Livestream only" };
  function vmBar(V) {
    if (!V) return "";
    const sug = !V.chosen && V.suggested ? V.suggested : null;
    const b = m => `<button type="button" class="chip ${V.video_mode === m ? "on" : ""}" data-vm="${m}" aria-pressed="${V.video_mode === m}">${VM_NAME[m]}${sug === m ? ` <span class="vw-sug">suggested</span>` : ""}</button>`;
    return `<div class="vw-mode"><span class="vw-ml">Match video</span><div class="vm-seg" role="group" aria-label="Match video mode">${b("livestream")}${b("auto")}</div>
      ${CS.help(VM_HELP, "About the match video mode")}<span id="vmst" class="muted small" role="status"></span>
      ${V.video_mode === "livestream" ? (() => { const bad = (V.checks || []).filter(c => c.state === "bad");
        return `<span class="vw-sugw small">${bad.length ? `<b>${bad.length} step${bad.length > 1 ? "s" : ""} left</b> before matches are tracked from the stream: ${h(bad.map(c => c.label.replace(/^Layout: /, "")).join(", ").toLowerCase())}`
          : "<b>Ready:</b> each match is tracked from its livestream clip as it is cut"} · <span class="muted">uploads are not looked up</span></span>`; })() : ""}
      ${sug && V.video_mode !== "livestream" ? `<span class="vw-sugw small">${sug !== V.video_mode ? `Suggested: <b>${VM_NAME[sug]}</b> · ${h(V.suggested_why || "")}` : `${h(V.suggested_why || "")}`}${V.chosen ? "" : " · <span class=\"muted\">not chosen yet</span>"}</span>` : ""}</div>`;
  }

  // ---------------------------------------------------------------- the event's videos, in plain words
  // One card per source: what it is (main / broadcast / station cam), its link, the recorder state, the matches found on it,
  // Replace / Remove, and a collapsed "Field setup" holding the layout editor + calibration wizard for that video.
  const WHAT = [["main", "Main / wide view"], ["broadcast", "Broadcast (cuts between cameras)"], ["red", "Red station cam"], ["blue", "Blue station cam"]];
  const WHAT_SHORT = { main: "Main / wide", broadcast: "Broadcast", red: "Red station", blue: "Blue station", stations: "Station cams" };
  const WHAT_TIP = { main: "the fixed, wide view of the whole field: what robots are tracked from",
                     broadcast: "the program feed that cuts between cameras: results and clips, tracking only while the wide camera is on air",
                     red: "fixed camera on the red driver stations", blue: "fixed camera on the blue driver stations" };
  const whatOf = s => s.role === "alliance-side" ? (s.station === "red" || s.station === "blue" ? s.station : "stations") : s.role === "wide" ? "main" : "broadcast";
  const whatBody = w => w === "main" ? { role: "wide", primary: true, station: "" } : w === "red" || w === "blue" ? { role: "alliance-side", station: w, primary: false }
    : { role: "dynamic", primary: false, station: "" };
  const whatOpts = cur => WHAT.concat(cur === "stations" ? [["stations", "Station cams (both)"]] : []).map(([k, t]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${t}</option>`).join("");
  const KIND_W = { livestream: "Live stream", video: "Recording", per_match: "Per-match uploads", file: "File" };
  const ytVideo = u => /(?:youtu\.be\/|[?&]v=|\/live\/|\/shorts\/)[A-Za-z0-9_-]{11}/.test(u || "") || /^[A-Za-z0-9_-]{11}$/.test((u || "").trim());
  const fitQ = c => c ? [c.tag_rms_px != null ? `tags ${c.tag_rms_px} px` : "", c.point_rms_px != null ? `points ${c.point_rms_px} px` : "",
                         c.line_med_px != null ? `centre line ${c.line_med_px} px` : ""].filter(Boolean).join(", ") : "";
  /** recorder status -> {c: on | warn | bad | "", t: plain state, why}; evj (event.json) for "Q1 is scheduled for …" */
  function recPlain(st, evj) {
    if (!st) return { c: "", t: "No recorder status", why: "the recorder hasn't reported for this event yet" };
    if (st.stale && LS_ACTIVE.includes(st.state)) return { c: "bad", t: "Recorder not reporting", why: `no update for ${dur(st.age_s)}: is the recorder service running?` };
    const nx = st.next_check_in_s ? ` · checks again in ${dur(st.next_check_in_s)}` : "";
    switch (st.state) {
      case "starting": return { c: "warn start", t: "Starting the recorder", why: st.message || "" };
      case "recording": {
        if (st.message) return { c: "warn start", t: "Recording", why: st.message };       // connected, no video yet
        const nq = !st.last_match && evj && evj.queue && evj.queue.next;
        return { c: "on", t: "Recording", why: [st.behind_live_s != null ? `${dur(st.behind_live_s)} behind live` : "",
          nq ? `no match on the stream yet; ${nq.short} is scheduled for ${CS.fmtWhen(nq.time)}` : ""].filter(Boolean).join(" · ") };
      }
      case "waiting": return { c: "warn", t: "Waiting for the stream to go live", why: (st.message || "not live yet") + nx };
      case "stream_over": return { c: "warn", t: "Not live", why: (st.message || "the stream ended") + nx };
      case "error": return { c: "bad", t: "Problem", why: st.message || "the recorder failed" };
      case "not_running": return { c: "bad", t: "Recorder not running", why: st.message || "" };
      case "no_source": return { c: "", t: "Nothing to record", why: "add a live stream link" };
      default: return { c: "", t: LS_LABEL[st.state] || st.state, why: st.message || "" };
    }
  }
  /** the matches the recorder found on a stream: processed (site match done) / cut (clip) / found */
  function foundHtml(st, site) {
    const M = st.matches || {}, R = (M.recent || []).filter(r => r.key || r.name), n = M.n_matches != null ? M.n_matches : R.length;
    if (!R.length) return `<div class="vw-found muted small">No match found on this stream yet${st.state === "recording" ? ": each match is cut about 20 s after its final buzzer" : ""}.</div>`;
    const stOf = r => site[r.key] === "done" ? ["done", "processed"] : r.clip ? ["cut", "cut, waiting to be processed"] : r.usable === false ? ["bad", "found, not usable"] : ["found", "found, no clip yet"];
    const chips = R.map(r => { const [c, t] = stOf(r);
      return `<span class="vw-m ${c}" title="${h(t)}">${h(r.key ? shortKey(r.key) : r.name)}${c === "done" ? CS.icon("check", "ico") : ""}<span class="vh"> ${h(t)}</span></span>`; }).join("");
    const nd = R.filter(r => site[r.key] === "done").length, nc = R.filter(r => r.clip && site[r.key] !== "done").length;
    return `<div class="vw-found"><div class="small"><b>${n} match${n === 1 ? "" : "es"} found on this stream</b>${nd ? ` · ${nd} processed` : ""}${nc ? ` · ${nc} cut, waiting to be processed` : ""}${M.n > n ? ` · ${M.n - n} skipped` : ""}
      ${R.length < n ? ` <span class="muted">(latest ${R.length})</span>` : ""}</div><div class="vw-ms" aria-label="Matches found">${chips}</div>
      <div class="vw-leg muted small"><span class="vw-m done">${CS.icon("check", "ico")}</span> processed · <span class="vw-m cut"></span> cut, waiting · <span class="vw-m found"></span> found, no clip</div></div>`;
  }
  /** overlay lines of a camera fit (job result "overlay") on a frame canvas */
  function paintOverlay(ctx, overlay, s, show) {
    if (!overlay) return;
    const lw = 1.6 / s;
    for (const L of overlay) {
      const isG = L.kind === "grid", isT = L.kind === "tag";
      if ((isG && !show.grid) || (isT && !show.tags) || (!isG && !isT && !show.lines)) continue;
      ctx.strokeStyle = isG ? "rgba(255,255,255,.35)" : isT ? "#51cf66" : L.kind === "field" ? "#ffd43b" : L.kind === "hub_red" ? "#ff5a5f" : L.kind === "hub_blue" ? "#3e8bff" : "#fff";
      ctx.lineWidth = isG ? lw * 0.7 : lw * 1.3; ctx.beginPath(); let pen = false;
      for (const p of L.pts) { if (!p) { pen = false; continue; } if (!pen) { ctx.moveTo(p[0], p[1]); pen = true; } else ctx.lineTo(p[0], p[1]); }
      ctx.stroke();
    }
  }
  // ---- a fit's verdict, per-tag errors and what Detect read (Auto-calibrate, the auto set-up card, the wizard)
  const VLABEL = { good: "The fit looks right", check: "Check this fit", bad: "This fit can't be trusted" };
  const BAD_SHORT = { tag_error: "the AprilTags don't line up", lens_centre: "the lens centre is far off the picture",
                      height: "the camera height is impossible", lens: "the lens shape is impossible", one_face: "all the tags are on one flat face",
                      point_error: "your clicked points don't line up" };
  // a report without a verdict (saved before the verdict existed): warnings make it "check"
  const verdictOf = r => !r ? null : r.verdict || ((r.warnings || []).length ? "check" : "good");
  const shortBad = r => { const x = (r.verdict_reasons || []).find(y => y.level === "bad"); return x ? BAD_SHORT[x.code] || x.text : "the checks failed"; };
  /** verdict box: the bad / check / note reasons as a list, the passed checks in one muted line */
  function verdictHtml(r) {
    const v = verdictOf(r);
    if (!v) return "";
    const R = r.verdict_reasons || (r.warnings || []).map(w => ({ level: "check", text: calPlain(w) }));
    const top = R.filter(x => x.level !== "ok"), ok = R.filter(x => x.level === "ok");
    return `<div class="vc-v ${v}"><b class="vc-vt">${CS.icon(v === "good" ? "check" : "warn", "ico")} ${VLABEL[v]}</b>
      ${top.length ? `<ul class="vc-why">${top.map(x => `<li class="${h(x.level)}">${h(x.text)}</li>`).join("")}</ul>` : ""}
      ${ok.length ? `<p class="vc-ok">${ok.map(x => h(x.text)).join(" ")}</p>` : ""}</div>`;
  }
  /** each AprilTag's distance from where the fit puts it; a tag the fit left out says so */
  function tagErrs(items) {
    const T = (items || []).filter(i => i.kind === "tag" || i.kind == null).sort((a, b) => (!!a.dropped - !!b.dropped) || a.id - b.id);
    if (!T.length) return "";
    const c = e => e == null ? "" : e > 3 ? "bad" : e > 1.5 ? "warn" : "good";
    return `<div class="vc-tags small" role="list" aria-label="How far each AprilTag is from the fit">${T.map(i => i.dropped
      ? `<span role="listitem" class="vc-t out" title="Left out of the fit: it is ${h(i.err_px)} px from where the other tags put it">AprilTag ${h(i.id)} · left out, ${h(i.err_px)} px off</span>`
      : `<span role="listitem" class="vc-t ${c(i.err_px)}">AprilTag ${h(i.id)} · ${h(i.err_px)} px</span>`).join("")}</div>`;
  }
  /** a left-out tag on the frame: where it was seen (red ring) and where the other tags put it (dashed) */
  function paintDropped(ctx, items, s) {
    for (const i of (items || []).filter(x => x.dropped && x.uv)) {
      const [u, v] = i.uv;
      ctx.strokeStyle = "#ff5a5f"; ctx.lineWidth = 2.2 / s; ctx.beginPath(); ctx.arc(u, v, 13 / s, 0, 7); ctx.stroke();
      if (i.pred) { ctx.setLineDash([6 / s, 4 / s]); ctx.beginPath(); ctx.moveTo(u, v); ctx.lineTo(i.pred[0], i.pred[1]); ctx.moveTo(i.pred[0] + 13 / s, i.pred[1]); ctx.arc(i.pred[0], i.pred[1], 13 / s, 0, 7); ctx.stroke(); ctx.setLineDash([]); }
      ctx.fillStyle = "#ff5a5f"; ctx.font = `bold ${13 / s}px system-ui, sans-serif`; ctx.fillText(`AprilTag ${i.id}: left out`, u + 16 / s, v - 8 / s);
    }
  }
  /** detected tags (no fit yet) on the frame */
  function paintTags(ctx, tags, s) {
    for (const [id, t] of Object.entries(tags || {})) {
      const c = t.corners; if (!c) continue;
      ctx.strokeStyle = "#51cf66"; ctx.lineWidth = 2 / s; ctx.beginPath(); c.forEach((p, k) => k ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.stroke();
      ctx.fillStyle = "#51cf66"; ctx.font = `bold ${12 / s}px system-ui, sans-serif`; ctx.fillText(id, c[2][0] + 6 / s, c[2][1] - 6 / s);
    }
  }
  const clock = t => { t = Math.max(0, Math.round(t || 0)); const hh = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = String(t % 60).padStart(2, "0");
    return hh ? `${hh}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`; };
  /** what Detect read: "10 frames at 0:12–1:53 of the recorded stream (6 cut-away frames skipped): none had an AprilTag." */
  function readLine(r) {
    const R = r && r.read, F = (r && r.frame_tags) || [];
    if (!R) return "";
    const n = R.n || F.length, span = R.t1 > R.t0 ? `${clock(R.t0)}–${clock(R.t1)}` : clock(R.t0);
    const W = R.when || "", ago = /^ago:(\d+)$/.exec(W);
    const rec = W === "latest" ? "the latest minutes of the recorded stream" : ago ? `the recorded stream ${ago[1]} minutes ago`
      : W.startsWith("match:") ? `${W.slice(6).split("_").pop().toUpperCase()}'s clip` : "the recorded stream";
    const from = R.kind === "live" ? `read live over ${dur(R.t1 - R.t0)}` : `at ${span} of ${R.kind === "recording" ? rec
      : R.finished_stream ? "the stream's recording (the stream has ended)" : "the video"}`;
    const withT = F.filter(x => x.n > 0).length, mx = F.reduce((a, x) => Math.max(a, x.n), 0);
    const got = !F.length ? "" : !withT ? "none had an AprilTag" : withT === F.length ? `each had AprilTags (up to ${mx})`
      : `${withT} had AprilTags (up to ${mx}), ${F.length - withT} had none`;
    return `${n} frame${n === 1 ? "" : "s"} ${from}${R.cutaways ? ` (${R.cutaways} cut-away frame${R.cutaways === 1 ? "" : "s"} skipped)` : ""}${got ? `: ${got}` : ""}.`;
  }
  /** what to do when the frames had too few AprilTags, by where they came from */
  const noTagsNext = R => `If the frame below isn't the whole field (a break, a results screen, a close-up), ${R && R.kind === "live" ? "try again while a match is on"
    : R && R.kind === "recording" ? "try again once a match has been recorded" : "this video may not show the field from a fixed camera"}. If it is the field, the AprilTags are too small or blurry to read: calibrate by hand by clicking 2 or more points along a few field lines.`;

  /** Auto-calibrate the overhead camera of a video: detect tags -> fit (robust, with a verdict) -> the admin checks the
   * overlay -> accept, then the layout's field box from the fitted camera's field_roi (only when the video has none).
   * A fit that can't be trusted makes "Calibrate by hand" the main button.  done(saved, message). */
  async function autoCal(ev, box, sid, done, when = "latest") {
    const shell = inner => { box.innerHTML = `<div class="vw-ac">${inner}</div>`; };
    // which part of a recorded stream to read (live/sources.py recorded_media): the field can change during the day
    // (2026wamil: a raised bar moved AprilTag 1 during set-up), so the latest minutes by default, or an earlier time
    const WHEN = [["latest", "the latest 3 minutes"], ["ago:10", "10 minutes ago"], ["ago:20", "20 minutes ago"], ["ago:30", "30 minutes ago"], ["ago:60", "an hour ago"]];
    const whenSel = () => `<label class="small vw-when">Read <select id="acwhen" aria-label="Which part of the recording to read">${WHEN.map(([v, t]) => `<option value="${v}" ${v === when ? "selected" : ""}>${h(t)}</option>`).join("")}</select></label>`;
    const showFrame = async (sel, W, H, draw) => {
      const FV = FrameView(box.querySelector(sel), { bin: box._bin });
      FV.draw = ctx => draw && draw(ctx, FV.s); FV.empty = "frame not available";
      try { FV.setImage(await loadImg(imgUrl(`events/${ev}/calibration/overhead/frame.jpg`)), W, H); } catch (e) { FV.W = W || 1920; FV.H = H || 1080; FV.size(); }
    };
    const wire = () => {
      const no = box.querySelector("#acno"), re = box.querySelector("#acre"), ws = box.querySelector("#acwhen");
      if (no) no.onclick = () => done(false);
      if (re) re.onclick = () => autoCal(ev, box, sid, done, ws ? ws.value : when);
    };
    // a failure: what happened, what was read and the frame itself (when there is one), and the next step
    const fail = (t, r, next) => {
      const down = /worker isn't running/.test(calPlain(t));          // by hand needs the worker too: Try again first
      shell(`<p class="small">${CS.icon("warn", "ico")} <b>Couldn't calibrate automatically.</b> ${calErrHtml(t)}</p>
        ${r && r.read ? `<p class="small muted">${h(readLine(r))}</p>` : ""}${next ? `<p class="small">${h(next)}</p>` : ""}
        ${r && r.frame ? `<figure class="vc-fig"><div class="vs-wrap" id="acfv"></div><figcaption class="muted small">A frame that was read${Object.keys(r.tags || {}).length ? " (green: the AprilTags found)" : ""}.</figcaption></figure>` : ""}
        <div class="vs-row">${down ? `<button type="button" class="primary" id="acre">Try again</button><button type="button" data-sub="calib">Calibrate by hand</button>`
          : `<button type="button" class="primary" data-sub="calib">Calibrate by hand</button><button type="button" id="acre">Try again</button>`}${whenSel()}<button type="button" id="acno">Close</button></div>`);
      wire();
      if (r && r.frame) showFrame("#acfv", r.W, r.H, (ctx, s) => paintTags(ctx, r.tags, s));
    };
    shell(`<p class="small" role="status"><b>Auto-calibrate</b> <span id="acst">reading frames and AprilTags from the video (about a minute)…</span></p>`);
    const say = t => { const s = box.querySelector("#acst"); if (s) s.textContent = t; };
    let r = null;
    try {
      r = await job(ev, A("POST", `events/${ev}/calibration/overhead/detect`, { source: sid, when }), t => t && say("reading frames and AprilTags: " + calPlain(t)));
      const ids = Object.keys(r.tags || {}), n = ids.length;
      if (n < 2) return fail(n ? `Only AprilTag ${ids[0]} was seen in enough frames: an automatic fit needs at least 2.` : "No AprilTags were found in the frames read.", r, noTagsNext(r.read));
      say(`${n} AprilTags found; fitting the camera…`);
      const f = await job(ev, A("POST", `events/${ev}/calibration/overhead/fit`, { points: {}, line_clicks: {}, drop_tags: [], auto_lines: true, more_tags: true }), t => t && say("fitting: " + calPlain(t)));
      if (f.error) return fail(f.error, r);
      const rp = f.report || {}, bad = verdictOf(rp) === "bad";
      shell(`<p class="small"><b>${bad ? "This fit can't be trusted." : "Check the fit."}</b> ${bad ? "Robots and shots would land in the wrong places. The reasons are below; the grid on the frame shows the problem." : "Does the yellow outline sit on the carpet edges, the grid on the field and the hub boxes on the hubs?"} <span class="muted">${h(fitQ(rp))}${rp.n_tags ? ` · ${rp.n_tags} tags` : ""}</span></p>
        ${verdictHtml(rp)}${tagErrs(rp.items)}${r.read ? `<p class="muted small">${h(readLine(r))}</p>` : ""}
        <div class="vs-wrap" id="accv"></div>
        <div class="vs-row vw-acb">${bad ? `<button type="button" class="primary" data-sub="calib">Calibrate by hand</button><button type="button" id="acok" aria-describedby="acwhy">Save anyway</button><span id="acwhy" class="vc-whyshort small">not recommended: ${h(shortBad(rp))}</span>`
          : `<button type="button" class="primary" id="acok">Looks right: save</button><button type="button" data-sub="calib">Fix it by hand</button>`}<button type="button" id="acre">Read another time</button>${whenSel()}<button type="button" id="acno">Cancel</button><span id="acst" class="muted small" role="status"></span></div>`);
      wire();
      showFrame("#accv", f.W, f.H, (ctx, s) => { paintOverlay(ctx, f.overlay, s, { grid: true, lines: true, tags: true }); paintDropped(ctx, rp.items, s); });
      box.querySelector("#acok").onclick = async e => {
        if (bad && !confirm(`This fit can't be trusted: ${shortBad(rp)}. Robots and shots would be placed wrongly on the field. Save it anyway?`)) return;
        e.target.disabled = true; say("saving…");
        try {
          await A("POST", `events/${ev}/calibration/overhead/accept`, bad ? { force: true } : undefined);
          let note = "";
          if (f.field_roi) {
            const L = await A("GET", `events/${ev}/layout`), cur = ((L.layout || {}).sources || {})[sid];
            if (!cur || !(cur.regions || []).some(r => r.kind === "field" || r.kind === "overhead")) {
              await A("PUT", `events/${ev}/layout/${encodeURIComponent(sid)}`, { W: cur ? cur.W : f.W, H: cur ? cur.H : f.H,
                regions: [...((cur && cur.regions) || []), { kind: "field", label: "Field area", box: f.field_roi.map(Math.round) }] });
              note = " The field box was set from it.";
            } else note = " Your field box is kept.";
          }
          done(true, "Calibration saved." + note);
        } catch (x) { say(calPlain(errMsg(x))); e.target.disabled = false; }
      };
    } catch (x) { fail(errMsg(x), r); }
  }

  async function sourcesTab(ev, el, focus) {
    const [P, d, V0, cal, evj, LS0] = await Promise.all([A("GET", `events/${ev}/video-plan`).catch(e => ({ error: e })), A("GET", `events/${ev}/sources`),
      A("GET", `events/${ev}/video-mode`).catch(() => null), A("GET", `events/${ev}/calibration`).catch(() => null), api(`${ev}/event.json`).catch(() => null), liveRec.load(ev).catch(() => null)]);
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
    el.innerHTML = `<div id="vmode">${vmBar(V)}</div>
      <div id="vwflash" role="status"></div>
      <section class="vw-list" id="vwlist" aria-label="Videos and streams"></section>
      <section class="card vp-sum" aria-labelledby="vpsumh" ${liveOnly ? "hidden" : ""}><h2 class="vw-h2" id="vpsumh">Match uploads</h2><div id="vpsum"></div>
        <div class="vs-row vp-acts0"><button id="vpcheck" type="button">Check videos</button><button id="disc" type="button">Discover from TBA</button>
          ${CS.help(help, "How match videos are chosen")}<span id="dst" class="muted small" role="status"></span></div>
        <details class="vw-fs" data-sid="__uploads"><summary><span>Field setup for uploads</span> <span class="muted small">field boxes per video type · overhead calibration</span></summary><div class="vw-fsb">
          <div class="vw-sub" role="group" aria-label="Field setup step"><button type="button" class="chip" data-sub="layout">Field boxes</button><button type="button" class="chip" data-sub="calib">Calibration</button></div>
          <div class="vw-mount"></div></div></details></section>
      <section class="card vp-cov" aria-labelledby="vpcovh" ${liveOnly ? "hidden" : ""}><div class="vp-covh"><h2 id="vpcovh">Matches</h2><div class="vp-filt" role="group" aria-label="Show matches" id="vpfilt"></div></div>
        <div id="vpgrid"></div>
        <p class="vp-legend muted small">${ico("starOn", "on")} chosen · ${ico("check", "ok")} available · ${ico("warn", "lim")} limited tracking · <span class="vp-n">2<i>dup 1</i></span> uploads, duplicates · <span class="vp-pend">…</span> being checked</p></section>
      <details class="card vp-adv" id="vpadv"><summary>Advanced: sources <span class="muted small">${S.length} · title families, webcasts, pasted URLs</span></summary><div id="vpadvb"></div></details>
      <dialog class="vp-sheet" id="vpsheet" aria-labelledby="vpsh"></dialog>`;
    // ---- summary line (+ livestream state)
    let lsSt = LS0;
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
    // ---- the videos: one card each (what it is, link, recorder, matches found, Replace / Remove, Field setup)
    const listEl = document.getElementById("vwlist"), vmEl = document.getElementById("vmode");
    const site = {}; ((evj && evj.matches) || []).forEach(m => { if (m && m.key) site[m.key] = m.state; });
    const calT = k => ((cal && cal.targets) || []).find(t => t.target === k) || {};
    const chk = () => Object.fromEntries(((V && V.checks) || []).map(c => [c.key, c]));
    const ORD = ["main", "red", "blue", "stations", "broadcast"], rank = s => [s.kind === "per_match", s.origin === "tba", ORD.indexOf(whatOf(s))];
    const shown = S.filter(s => !s.dismissed).sort((a, b) => { const x = rank(a), y = rank(b); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]; }), gone = S.filter(s => s.dismissed);
    const mainSrc = shown.find(s => s.id === (V && V.stream_source)) || shown.find(s => s.kind !== "per_match" && whatOf(s) === "main");
    const recFor = s => !!(lsSt && lsSt.source && (lsSt.source.id === s.id || (s.url && lsSt.source.url === s.url)));
    // the recorder reports its source as it read it (an old TBA webcast name has no channel): this page's name for it
    const recName = src => { const m = S.find(x => x.id === src.id || (x.url && x.url === src.url));
      return (m && m.label) || src.url || src.label || "another stream"; };
    function fsOf(s) {                      // field setup of a video: field (or station) box + its camera calibration
      const w = whatOf(s), stream = V && s.id === V.stream_source, c = chk();
      // a broadcast stream can carry a fixed wide camera too (program feed: tracked from every camera): optional setup
      const optional = w === "broadcast" && s.kind !== "per_match";
      const target = w === "main" || optional ? "overhead" : w === "red" ? "station_red" : w === "blue" ? "station_blue" : null;
      const box = stream && c.layout ? c.layout.state === "ok" : !!s.has_layout;
      const calOk = !target ? null : target === "overhead" && s.calibration !== undefined ? !!s.calibration
        : stream && target === "overhead" && c.calibration ? c.calibration.state === "ok" : !!calT(target).saved;
      return { w, target, box, calOk, optional, need: [!box && (target && target !== "overhead" ? "station box" : "field box"), calOk === false && "calibration"].filter(Boolean) };
    }
    const fsNote = fs => fs.optional ? `For tracking from this broadcast: the field box and the calibration of its fixed wide camera (the automatic set-up above does both). <b>Auto-calibrate</b> finds the AprilTags itself; you check the result before it's saved.`
      : fs.w === "main" ? `So robots can be tracked from this video: a field box and a camera calibration, once per camera. <b>Auto-calibrate</b> finds the AprilTags itself and sets the field box too; you check the result before it's saved.`
      : fs.target ? `Draw the ${fs.w} station box on a frame of this video, then calibrate that camera.`
      : "Optional: boxes for the clock, score bar and picture-in-picture insets of this feed.";
    const liveChoice = () => `<span class="vw-live" hidden role="radiogroup" aria-label="Live stream or recording"><label class="small"><input type="radio" name="live" value="1" checked> Live / upcoming stream</label>
      <label class="small"><input type="radio" name="live" value="0"> Recording (VOD)</label></span>`;
    function dynHtml(s) {                  // the parts the 15 s refresh redraws: recorder state + matches found
      const live = s.kind === "livestream", mine = live && recFor(s);
      let out = "";
      if (live) {
        const r = mine || !(lsSt && lsSt.source) ? recPlain(lsSt, evj) : { c: "", t: "Not being recorded", why: `the recorder records ${recName(lsSt.source)} first` };
        out += `<div class="vw-rec"><span class="ls-dot ${r.c}" aria-hidden="true"></span><span><b>${h(r.t)}</b>${r.why ? ` <span class="muted">· ${h(r.why)}</span>` : ""}</span></div>`;
        const tr = mine && lsSt.tracking;
        if (tr) out += `<div class="vw-tr small">${tr.ok ? `${CS.icon("check", "ico")} Robots are tracked from this stream` : `Robots aren't tracked from this stream yet <span class="muted">· ${h(tr.note || "")}</span>`}</div>`;
        if (mine) out += foundHtml(lsSt, site) + `<details class="vw-recd"><summary class="small">Recorder details</summary>${liveRec.card(lsSt, ev)}</details>`;
      } else if (s.kind === "per_match") {
        out += `<div class="vw-found small">${s.n_videos ? `<b>${s.n_videos} match upload${s.n_videos === 1 ? "" : "s"} linked</b> <span class="muted">· the per-match plan below picks one per match</span>` : `<span class="muted">No match uploads found yet</span>`}</div>`;
      } else {
        out += `<div class="vw-found muted small">A recording: used for frame grabs, the field box and calibration. Matches are cut from live streams and found in per-match uploads.</div>`;
      }
      return out;
    }
    // ---- automatic set-up of a stream (GET/POST events/<ev>/sources/<sid>/auto-setup[/accept], live/sources.py):
    // the worker runs dynpose.auto_setup on the recording; the card shows the projected field and one-click Accept
    const AS_HELP = `<span class="hl">Once a stream is recorded, Cyber-Scout looks at a few minutes of it: the broadcast graphics (the tracker ignores them), the match clock, and the <b>fixed wide camera</b> if the broadcast has one. That camera is found from the field's AprilTags and fitted like the calibration wizard.</span>
      <span class="hl">Nothing is used until you <b>Accept</b>: check that the yellow outline sits on the carpet edges and the hub squares on the hubs.</span>
      <span class="hl">A <b>program feed</b> (cuts between cameras) is tracked from every camera: each frame gets its own camera position from the AprilTags. Fuel and shots are measured only while the fixed camera is on air.</span>`;
    const pct = x => x == null ? "–" : `${Math.round(x * 100)}%`;
    const LAYOUT_W = { top: "2026 FIRST scoreboard (top)", bottom: "2026 FIRST score bar (bottom)" };
    function feedLine(a, s) {
      const f = (a && a.feed) || {}, pf = s.program_feed != null ? s.program_feed : a && a.program_feed;
      if (pf == null) return f.why ? `<span class="muted">Feed: not decided · ${h(f.why)}</span>` : "";
      return pf ? `<b>Program feed</b> <span class="muted">· ${h(f.why || s.program_feed_why || "cuts between cameras")}</span>`
        : `<b>One fixed camera</b> <span class="muted">· ${h(f.why || s.program_feed_why || "")}</span>`;
    }
    function autoHtml(s) {
      const a = s.auto_setup, rd = s.auto_ready || {}, sid = encodeURIComponent(s.id);
      const btn = (act, t, cls) => `<button type="button" class="small ${cls || ""}" data-as-act="${act}">${t}</button>`;
      const hand = btn("hand", "Calibrate by hand");
      const head = t => `<div class="vw-ash"><span class="vw-asi" aria-hidden="true">${CS.icon("starOn")}</span><b>Automatic set-up</b> ${t || ""}${CS.help(AS_HELP, "About the automatic set-up")}</div>`;
      if (a && a.running) {
        const q = a.job && a.job.state === "queued", t0 = (a.job && (q ? a.job.created : a.job.started || a.job.created)) || a.at;
        return head(`<span class="muted small" role="status">· ${q ? "queued" : "working"}${t0 ? ` ${dur(Date.now() / 1000 - t0)} ago` : ""}${q ? ", waiting for the worker to pick it up (is it running?)" : ""}</span>`) +
          `<p class="small vw-asn"><span class="vw-spin" aria-hidden="true"></span>Looking at the recording: graphics, clock, the cameras and their AprilTags. This takes a few minutes.</p>`;
      }
      if (!a || !a.status) {
        return head() + `<p class="small vw-asn muted">${rd.media ? "Ready to run on the recording: it starts on its own within a minute." : h(rd.why ? `Waiting: ${rd.why}.` : "Waiting for the first minutes of the recording.")} It sets itself up once the recorder has a match (or a few minutes) of this stream.</p>
          <div class="vs-row vw-asb">${rd.media ? btn("run", "Set up now", "primary") : ""}${hand}</div>`;
      }
      const isrc = `app/v1/events/${h(ev)}/sources/${h(sid)}/auto-setup/overlay.jpg?v=${h(Math.round(a.at || 0))}`;
      const img = a.has_overlay ? `<figure class="vw-asf"><a href="${isrc}" target="_blank" rel="noopener" title="Open full size"><img src="${isrc}" alt="${h(`The ${a.status === "ok" ? "found camera's view with the projected field" : "recorded frame with the graphics found"} (${s.label || s.id})`)}" width="${h(a.W || 1920)}" height="${h(a.H || 1080)}" loading="lazy" decoding="async"></a>
        <figcaption class="muted small">${a.status === "ok" ? `<span class="vw-k y">carpet edge</span> <span class="vw-k w">grid · lines</span> <span class="vw-k r">hubs</span> ` : ""}<span class="vw-k o">graphics (ignored)</span>${a.clock ? ` <span class="vw-k g">clock</span>` : ""}${a.field ? ` <span class="vw-k c">field box</span>` : ""}</figcaption></figure>` : "";
      const fx = a.fixed || {}, vs = a.vs_saved, sb = a.scoreboard || {};
      // the fixed camera's fit judged like the wizard's (verdict + reasons, per-tag errors; older states: warnings)
      const fv = a.status === "ok" ? verdictOf(fx) : null, fbad = fv === "bad";
      const facts = `<dl class="vw-askv small">
          ${a.status === "ok" ? `<div><dt>Fixed camera</dt><dd>on air ${pct(fx.on_air)} of the clip · ${h(fx.tags)} AprilTags · fit ${h(fx.tag_rms_px)} px${fx.line_med_px != null ? ` · centre line ${h(fx.line_med_px)} px` : ""}${tagErrs(fx.items)}</dd></div>` : ""}
          <div><dt>Feed</dt><dd>${feedLine(a, s) || "–"}</dd></div>
          <div><dt>Scoreboard</dt><dd>${sb.layout ? h(LAYOUT_W[sb.layout] || sb.layout) + (a.clock ? " · clock found" : "") : a.clock ? "clock found (layout not recognised)" : "<span class=\"muted\">not recognised: the stream analyser still tries both 2026 FIRST layouts</span>"}</dd></div>
          ${vs && vs.median_px != null ? `<div><dt>vs saved</dt><dd>${h(vs.median_px)} px median, ${h(vs.max_px)} px max from ${h(vs.file)}${vs.reaimed_median_px != null ? ` <span class="muted">(${h(vs.reaimed_median_px)} px once re-aimed: the lens fits differ)</span>` : ""}</dd></div>` : ""}
          <div><dt>From</dt><dd class="muted">${a.media_kind === "clip" ? "a recorded match" : a.media_kind === "sample" ? "the last minutes recorded" : "the file"} · ${h(when(a.at))}${a.seconds ? ` · ${dur(a.seconds)}` : ""}</dd></div></dl>`;
      if (a.status === "ok" && a.accepted_at) {
        return head(`<span class="pill vw-ok">${CS.icon("check", "ico")} accepted</span>`) +
          `<p class="small vw-asn muted">Accepted by ${h(a.accepted_by || "an admin")} ${h(when(a.accepted_at))}${a.accepted_file ? ` → ${h(a.accepted_file)}` : ""}.</p>
          ${fbad ? verdictHtml(fx) : ""}<details class="vw-asd"><summary class="small">What was found</summary>${fbad ? "" : verdictHtml(fx)}${img}${facts}</details>
          <div class="vs-row vw-asb">${btn("run", "Run again")}${hand}</div>`;
      }
      if (a.status === "ok" && fbad) {         // found the camera, but its fit can't be trusted: by hand comes first
        return head(`<span class="pill vw-need">can't be trusted</span>`) +
          `<p class="small vw-asn">Found the fixed wide camera, but its fit can't be trusted: robots and shots would land in the wrong places.</p>${verdictHtml(fx)}${img}${facts}
          <div class="vs-row vw-asb">${btn("hand", "Calibrate by hand", "primary")}${btn("accept", "Accept anyway")}<span class="vc-whyshort small">not recommended: ${h(shortBad(fx))}</span>${btn("run", "Run again")}<span class="muted small" data-as-st role="status"></span></div>`;
      }
      if (a.status === "ok") {
        return head(`<span class="pill vw-ready">ready to accept</span>`) +
          `<p class="small vw-asn">Found the fixed wide camera. Does the yellow outline sit on the carpet edges and the hub squares on the hubs?</p>${verdictHtml(fx)}${img}${facts}
          <div class="vs-row vw-asb">${btn("accept", "Looks right: accept", "primary")}${btn("run", "Run again")}${hand}<span class="muted small" data-as-st role="status"></span></div>`;
      }
      const bad = a.status === "error", msg = calPlain(a.message || "");
      return head(`<span class="pill vw-need">${bad ? "failed" : "no fixed camera"}</span>`) +
        `<p class="small vw-asn">${CS.icon("warn", "ico")} ${bad ? `The automatic set-up failed: ${calErrHtml(a.message || "")}` : `Couldn't find the fixed wide camera: <span class="muted">${h(msg)}</span>`}</p>
        <p class="small vw-asn muted">${bad ? "" : "Robots can still be located from every camera, but fuel and shots need the fixed camera: "}Calibrate the camera by hand (pick frames where it is on air), or run it again on a later match.</p>
        ${a.has_overlay ? `<details class="vw-asd"><summary class="small">What was found</summary>${img}${facts}</details>` : ""}
        <div class="vs-row vw-asb">${hand}${btn("run", "Run again")}<span class="muted small" data-as-st role="status"></span></div>`;
    }
    const DYN_OPTS = [["auto", null], ["on", true], ["off", false]];
    function dynSelect(s) {
      if (s.kind === "per_match" || s.role === "alliance-side") return "";
      const cur = s.dynamic === true ? "on" : s.dynamic === false ? "off" : "auto", pf = s.program_feed;
      const autoT = pf === true ? "Automatic: every camera (program feed)" : pf === false ? "Automatic: the fixed camera (one camera)" : "Automatic (decided by the set-up)";
      const t = { auto: autoT, on: "Every camera", off: "The fixed camera only" };
      return `<label class="vw-wl small">Track robots from <select data-dyn aria-label="Which cameras robots are tracked from (${h(s.label || s.id)})">${DYN_OPTS.map(([k]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${t[k]}</option>`).join("")}</select>
        ${CS.help(`<span class="hl"><b>Every camera</b>: each frame gets its own camera position from the field's AprilTags, so robots are tracked through the close-ups too (a program feed). <b>The fixed camera only</b>: robots are tracked while the calibrated wide camera is on air; other shots are gaps.</span><span class="hl"><b>Automatic</b> follows what the set-up detected: a program feed (cuts between cameras) is tracked from every camera.</span>`, "About tracking from every camera")}</label>`;
    }
    function card(s) {
      const fs = fsOf(s), w = fs.w, tba = s.origin === "tba", per = s.kind === "per_match", nm = h(s.label || s.id);
      const pill = !fs.target || (fs.optional && fs.need.length) ? `<span class="pill">optional</span>` : fs.need.length ? `<span class="pill vw-need">needs ${h(fs.need.join(" + "))}</span>`
        : `<span class="pill vw-ok">${CS.icon("check", "ico")} done</span>`;
      return `<article class="card vw-card ${w}${s.enabled === false ? " off" : ""}" data-id="${h(s.id)}">
        <div class="vw-hd"><span class="vw-ic ${w}" aria-hidden="true">${CS.icon("video")}</span>
          <div class="vw-ti"><h2>${nm}</h2>
            <div class="vw-meta"><span class="pill vw-what ${w}" title="${h(WHAT_TIP[w] || "")}">${h(WHAT_SHORT[w] || w)}</span><span class="pill">${h(KIND_W[s.kind] || s.kind)}</span>
              ${tba ? `<span class="pill vw-tba" title="Listed on The Blue Alliance for this event: Cyber-Scout added it on its own. Remove it if it's wrong; it won't come back.">from TBA</span>` : ""}
              ${s.enabled === false ? `<span class="pill">off</span>` : ""}</div>
            <div class="vw-url small">${s.url ? urlHtml(s.url) : s.title_pattern ? `title ~ <code>${h(s.title_pattern)}</code>${s.example_title ? ` · e.g. “${h(s.example_title)}”` : ""}` : ""}</div></div>
          <div class="vw-acts">${tba && per ? "" : `<button type="button" class="small" data-rep aria-label="Replace the link of ${nm}">Replace</button>`}<button type="button" class="small" data-rm aria-label="Remove ${nm}">Remove</button></div></div>
        <form class="vw-repf vs-form" hidden novalidate><input name="u" required aria-label="New link for ${nm}" aria-describedby="vwrerr-${h(s.id)}" placeholder="New YouTube / Twitch link…" autocomplete="off" autocapitalize="none" spellcheck="false">
          ${liveChoice()}<button class="primary">Save link</button><button type="button" data-cancel>Cancel</button>
          <p class="vw-ferr" id="vwrerr-${h(s.id)}" role="alert" hidden></p>
          <span class="muted small vw-repn">${tba ? "Adds your link and removes the TBA one." : "Keeps this video's field box and calibration: redo them if the camera moved."}</span></form>
        <div class="vw-body"><div class="vw-sets"><label class="vw-wl small">What it is <select data-what aria-label="What ${nm} is">${whatOpts(w)}</select></label>${dynSelect(s)}</div>
          <div class="vw-dyn">${dynHtml(s)}</div>
          ${s.auto_setup_ok && s.enabled !== false ? `<section class="vw-as" data-as aria-label="Automatic set-up of ${nm}">${autoHtml(s)}</section>` : ""}</div>
        ${per ? "" : `<details class="vw-fs" data-sid="${h(s.id)}"><summary><span>Field setup</span> ${pill}</summary><div class="vw-fsb">
          <div class="vw-sub" role="group" aria-label="Field setup step">${fs.target === "overhead" ? `<button type="button" class="chip vw-auto" data-sub="auto">Auto-calibrate</button>` : ""}
            <button type="button" class="chip" data-sub="layout">${fs.target && fs.target !== "overhead" ? "Station box" : "Field box"}${fs.box ? " ✓" : ""}</button>${fs.target ? `<button type="button" class="chip" data-sub="calib">Calibration${fs.calOk ? " ✓" : ""}</button>` : ""}</div>
          <p class="vw-fsn muted small">${fsNote(fs)}</p><div class="vw-mount"></div></div></details>`}
      </article>`;
    }
    // the first video is the main (wide) one; every later link is a Broadcast until the admin says otherwise: a
    // station cam is never picked for them (SETUP-7)
    const used = new Set(shown.filter(s => s.kind !== "per_match").map(whatOf));
    const nextWhat = used.has("main") ? "broadcast" : "main";
    listEl.innerHTML = shown.map(card).join("") + `
      <form class="card vw-add vs-form" id="vwadd" novalidate><h2 class="vw-h2">${shown.length ? "Add another video or stream" : "Add the event's video or stream"}</h2>
        <div class="vw-tbaw" id="vwtba" hidden></div>
        <input name="u" required aria-label="Video or stream link" aria-describedby="vwerr" placeholder="Paste a YouTube or Twitch link: live stream or recording" autocomplete="off" autocapitalize="none" spellcheck="false" inputmode="url">
        <label class="small">What it is <select name="what">${whatOpts(nextWhat)}</select></label>${liveChoice()}
        <button class="primary">Add</button><span id="vwst" class="muted small" role="status"></span>
        <p class="vw-ferr" id="vwerr" role="alert" hidden></p>
        <p class="muted small vw-tip">A channel's live link (youtube.com/@channel/live or twitch.tv/channel) follows each day's new stream; a watch link is one stream or one recording.</p></form>
      ${gone.length ? `<p class="vw-gone small muted">Removed from TBA's list (kept off): ${gone.map(s => `<span>${h(s.label || s.id)} <button type="button" class="small" data-restore="${h(s.id)}">Restore</button></span>`).join(" · ")}</p>` : ""}`;
    try { const f = sessionStorage.getItem("cs:vwflash"); if (f) { sessionStorage.removeItem("cs:vwflash"); document.getElementById("vwflash").innerHTML = `<div class="vw-flash">${CS.icon("check", "ico")} ${h(f)}</div>`; } } catch (e) { }
    const flash = t => { try { sessionStorage.setItem("cs:vwflash", t); } catch (e) { } };
    const say = t => { const s = document.getElementById("vwst"); if (s) s.textContent = t; };
    const live = f => { const r = f.querySelector("input[name=live]:checked"); return ytVideo(f.u.value) && r ? r.value === "1" : null; };
    listEl.querySelectorAll("form").forEach(f => { const lv = f.querySelector(".vw-live"); if (lv) f.u.addEventListener("input", () => { lv.hidden = !ytVideo(f.u.value); }); });
    const addBody = (url, w, lv) => { const b = whatBody(w); return { url, role: b.role, primary: !!b.primary, station: b.station || null, live: lv }; };
    // the match video mode never changes as a side effect of adding a link (SETUP-6): after the first main live
    // stream the flash only points at the suggestion, which stays a choice at the top
    const modeHint = (w, s) => w === "main" && s.kind === "livestream" && V && !V.chosen && V.suggested && V.suggested !== V.video_mode
      ? ` Suggested match video: ${VM_NAME[V.suggested]} (${V.suggested_why}): choose it at the top if that's right.` : "";
    const addForm = document.getElementById("vwadd");
    addForm.u.addEventListener("input", () => { if (addForm.u.getAttribute("aria-invalid")) fieldErr(addForm, ""); });
    async function addLink(url, w, lv) {
      const s = await A("POST", `events/${ev}/sources`, addBody(url, w, lv));
      if (s.job) { say("listing the playlist…"); await job(ev, Promise.resolve(s.job), say); }
      flash(`Added ${s.label || s.url}.` + modeHint(w, s) + (w === "main" ? " Next: Field setup on its card." : "")); route(false);
    }
    addForm.onsubmit = async e => {
      e.preventDefault(); const f = e.target, w = f.what.value, u = f.u.value.trim(), bad = linkProblem(u);
      if (bad) { fieldErr(f, bad); return; }
      fieldErr(f, ""); say("adding…");
      try { await addLink(u, w, live(f)); }
      catch (x) { say(""); fieldErr(f, x); }
    };
    // TBA's webcasts for this event, offered above the paste box in every match video mode (SETUP-12)
    A("GET", `events/${ev}/sources/webcasts`).then(r => {
      const box = document.getElementById("vwtba"), L = ((r && r.webcasts) || []).filter(w => !w.source || w.dismissed);
      if (!box || !L.length) return;
      box.innerHTML = `<p class="small"><b>The Blue Alliance lists ${L.length === 1 ? "a stream" : L.length + " streams"} for this event.</b>
          <span class="muted">Add uses your “What it is” choice below.</span></p>
        <ul class="vw-tbal">${L.map(w => `<li><b>${h(w.label.replace(/^Event webcast · /, ""))}</b> <span class="muted small">${urlHtml(w.url)}</span>
          ${w.dismissed ? `<button type="button" class="small" data-tbares="${h(w.source)}">Restore</button>` : `<button type="button" class="small" data-tbaadd="${h(w.url)}">Add</button>`}</li>`).join("")}</ul>`;
      box.hidden = false;
      box.onclick = async e => {
        const b = e.target.closest("button[data-tbaadd], button[data-tbares]"); if (!b) return;
        b.disabled = true; fieldErr(addForm, ""); say("adding…");
        try {
          if (b.dataset.tbares) { await A("PATCH", `events/${ev}/sources/${encodeURIComponent(b.dataset.tbares)}`, { dismissed: false, enabled: true }); flash("Restored."); route(false); }
          else await addLink(b.dataset.tbaadd, addForm.what.value, null);
        } catch (x) { b.disabled = false; say(""); fieldErr(addForm, x); }
      };
    }).catch(() => { });
    listEl.querySelectorAll("[data-restore]").forEach(b => b.onclick = async () => {
      try { await A("PATCH", `events/${ev}/sources/${encodeURIComponent(b.dataset.restore)}`, { dismissed: false, enabled: true }); route(false); } catch (x) { say(errMsg(x)); }
    });
    listEl.querySelectorAll(".vw-card").forEach(c => {
      const s = S.find(x => x.id === c.dataset.id), sid = encodeURIComponent(s.id), rf = c.querySelector(".vw-repf");
      const csay = t => { let m = c.querySelector(".vw-err"); if (!m) { m = document.createElement("div"); m.className = "vw-err small"; m.setAttribute("role", "status"); c.querySelector(".vw-body").prepend(m); } m.textContent = t; };
      c.querySelector("[data-what]").onchange = async e => {
        try { await A("PATCH", `events/${ev}/sources/${sid}`, whatBody(e.target.value)); route(false); } catch (x) { csay(errMsg(x)); }
      };
      c.querySelector("[data-rm]").onclick = async () => {
        if (s.origin === "admin" && !confirm(`Remove ${s.label || s.url}? (its field box and calibration files are kept)`)) return;
        try { await A("DELETE", `events/${ev}/sources/${sid}`); flash(s.origin === "admin" ? `Removed ${s.label || s.id}.` : `Removed ${s.label || s.id}: TBA discovery keeps it off (Restore below).`); route(false); }
        catch (x) { csay(errMsg(x)); }
      };
      const dy = c.querySelector("[data-dyn]");
      if (dy) dy.onchange = async e => {
        const v = { auto: null, on: true, off: false }[e.target.value];
        try { const r = await A("PATCH", `events/${ev}/sources/${sid}`, { dynamic: v }); s.dynamic = r.dynamic; s.dynamic_on = r.dynamic_on; csay(""); }
        catch (x) { csay(errMsg(x)); }
      };
      wireAuto(c, s);
      const rb = c.querySelector("[data-rep]");
      if (rb) rb.onclick = () => { rf.hidden = !rf.hidden; if (!rf.hidden) rf.u.focus(); };
      rf.querySelector("[data-cancel]").onclick = () => { rf.hidden = true; fieldErr(rf, ""); };
      rf.u.addEventListener("input", () => { if (rf.u.getAttribute("aria-invalid")) fieldErr(rf, ""); });
      rf.onsubmit = async e => {
        e.preventDefault();
        const u = rf.u.value.trim(), bad = linkProblem(u);
        if (bad) { fieldErr(rf, bad); return; }
        fieldErr(rf, ""); csay("saving…");
        try {
          if (s.origin === "admin") await A("PATCH", `events/${ev}/sources/${sid}`, { url: u, live: live(rf) });
          else { await A("POST", `events/${ev}/sources`, addBody(u, whatOf(s) === "stations" ? "red" : whatOf(s), live(rf))); await A("DELETE", `events/${ev}/sources/${sid}`); }
          flash("Link replaced."); route(false);
        } catch (x) { csay(""); fieldErr(rf, x); }
      };
    });
    // ---- automatic set-up: actions + a poll while one runs
    function drawAuto(c, s) {
      const box = c.querySelector("[data-as]"); if (!box) return;
      const op = [...box.querySelectorAll("details")].map(d => d.open);
      box.innerHTML = autoHtml(s);
      box.querySelectorAll("details").forEach((d, i) => { if (op[i]) d.open = true; });
      const ds = c.querySelector("[data-dyn]");
      if (ds && !ds.matches(":focus")) { const o = document.createElement("div"); o.innerHTML = dynSelect(s); const n = o.querySelector("[data-dyn]"); if (n) ds.querySelector("option[value=auto]").textContent = n.querySelector("option[value=auto]").textContent; }
    }
    function wireAuto(c, s) {
      const box = c.querySelector("[data-as]"); if (!box) return;
      box.addEventListener("click", async e => {
        const b = e.target.closest("[data-as-act]"); if (!b || !box.contains(b)) return;
        const st = t => { const x = box.querySelector("[data-as-st]"); if (x) x.textContent = t; };
        const act = b.dataset.asAct, sid = encodeURIComponent(s.id);
        if (act === "hand") {
          const det = dets.find(x => x.dataset.sid === s.id);
          if (det) { det.open = true; mount(det, "calib"); requestAnimationFrame(() => det.scrollIntoView({ block: "start", behavior: "smooth" })); }
          return;
        }
        b.disabled = true;
        try {
          if (act === "run") {
            const j = await A("POST", `events/${ev}/sources/${sid}/auto-setup`);
            s.auto_setup = Object.assign({}, s.auto_setup || {}, { running: true, job: { id: j.id, state: j.state, created: j.created } });
            drawAuto(c, s); pollAuto();
          } else if (act === "accept") {
            const fx = (s.auto_setup && s.auto_setup.fixed) || {};
            if (verdictOf(fx) === "bad" && !confirm(`This fit can't be trusted: ${shortBad(fx)}. Robots and shots would be placed wrongly on the field. Accept it anyway?`)) { b.disabled = false; return; }
            st("saving…");
            const r = await A("POST", `events/${ev}/sources/${sid}/auto-setup/accept`);
            flash(`Automatic set-up of ${s.label || s.id} accepted: camera saved to ${r.camera}${r.layout ? ", field box, graphics and clock boxes set" : ""}.`);
            route(false);
          }
        } catch (x) {
          if (act === "run" && x.status === 409 && /already running/.test(errMsg(x))) {      // started elsewhere: follow it
            s.auto_setup = Object.assign({}, s.auto_setup || {}, { running: true }); drawAuto(c, s); pollAuto(); return;
          }
          b.disabled = false; st(errMsg(x)); if (!box.querySelector("[data-as-st]")) csayIn(c, errMsg(x));
        }
      });
    }
    const csayIn = (c, t) => { let m = c.querySelector(".vw-err"); if (!m) { m = document.createElement("div"); m.className = "vw-err small"; m.setAttribute("role", "status"); c.querySelector(".vw-body").prepend(m); } m.textContent = t; };
    let asIv = null;
    function pollAuto() {
      if (asIv) return;
      asIv = setInterval(async () => {
        const run = S.filter(s => s.auto_setup && s.auto_setup.running);
        if (!run.length || !listEl.isConnected) { clearInterval(asIv); asIv = null; return; }
        for (const s of run) {
          try {
            const r = await A("GET", `events/${ev}/sources/${encodeURIComponent(s.id)}/auto-setup`);
            s.auto_setup = r.auto_setup; s.auto_ready = r.ready;
            if (r.auto_setup && !r.auto_setup.running && r.auto_setup.program_feed != null) s.program_feed = r.auto_setup.program_feed;
            const c = listEl.querySelector(`.vw-card[data-id="${window.CSS.escape(s.id)}"]`); if (c) drawAuto(c, s);
          } catch (x) { }
        }
      }, 4000);
      cleanup.push(() => { if (asIv) { clearInterval(asIv); asIv = null; } });
    }
    if (S.some(s => s.auto_setup && s.auto_setup.running)) pollAuto();
    // ---- match video mode (top)
    const vmWire = () => vmEl.querySelectorAll("[data-vm]").forEach(b => b.onclick = async () => {
      if (b.dataset.vm === V.video_mode && V.chosen) return;
      const s = vmEl.querySelector("#vmst"); if (s) s.textContent = "saving…";
      try { await A("PUT", `events/${ev}/video-mode`, { video_mode: b.dataset.vm }); route(false); } catch (x) { if (s) s.textContent = errMsg(x); }
    });
    vmWire();
    // ---- Field setup dropdowns: one editor mounted at a time (layoutTab / calibTab / autoCal into the open <details>);
    // each mount gets its own cleanup bin, emptied when it closes or the route changes
    let cur = null;
    const unmount = () => { if (!cur) return; cur.bin.dead = true; cur.bin.fns.forEach(f => { try { f(); } catch (e) { } }); cur.box.innerHTML = ""; cur = null; };
    cleanup.push(unmount);
    const dets = [...el.querySelectorAll("details.vw-fs")];
    async function mount(det, sub, arg) {
      const sid = det.dataset.sid, box = det.querySelector(".vw-mount"), s = S.find(x => x.id === sid);
      dets.forEach(o => { if (o !== det && o.open) o.open = false; });
      unmount();
      det.querySelectorAll("[data-sub]").forEach(b => { b.classList.toggle("on", b.dataset.sub === sub); b.setAttribute("aria-pressed", String(b.dataset.sub === sub)); });
      if (!sub) return;
      const target = arg && sub === "calib" ? arg : sid === "__uploads" ? "overhead" : fsOf(s).target || "overhead";
      const hash = sub === "calib" ? `#/${ev}/sources/calib/${target}` : sub === "layout" ? `#/${ev}/sources/layout${sid === "__uploads" ? (arg ? "/" + encodeURIComponent(arg) : "") : "/" + encodeURIComponent(sid)}` : null;
      if (hash) try { history.replaceState(null, "", hash); } catch (e) { }
      const bin = box._bin = { fns: [], dead: false, push(f) { if (this.dead) f(); else this.fns.push(f); } };
      cur = { box, bin, det };
      box.innerHTML = `<div class="loading small">Loading…</div>`;
      try {
        if (sub === "auto") await autoCal(ev, box, sid, (saved, msg) => {
          if (saved) { flash(msg); try { history.replaceState(null, "", `#/${ev}/sources/calib/overhead`); } catch (e) { } route(false); } else mount(det, "calib");
        });
        else if (sub === "calib") await calibTab(ev, box, target, sid !== "__uploads" ? sid : null);
        else await layoutTab(ev, box, sid === "__uploads" ? arg : sid);
      } catch (e) { if (!bin.dead) box.innerHTML = `<div class="empty small">Couldn't load: ${h(errMsg(e))}</div>`; }
    }
    dets.forEach(det => {
      det.addEventListener("click", e => { const b = e.target.closest("[data-sub]"); if (b && det.contains(b)) { e.preventDefault(); mount(det, b.dataset.sub); } });
      det.addEventListener("toggle", () => {
        const box = det.querySelector(".vw-mount");
        if (det.open) { if (cur && cur.box === box) return;
          const s = S.find(x => x.id === det.dataset.sid), fs = s ? fsOf(s) : null;
          mount(det, !fs ? null : fs.w === "main" && !fs.box && !fs.calOk ? null : !fs.box ? "layout" : fs.calOk === false ? "calib" : "layout"); }
        else if (cur && cur.box === box) { unmount(); try { history.replaceState(null, "", `#/${ev}/sources`); } catch (e) { } }
      });
    });
    // the old tab routes: #/<ev>/sources/layout[/<sid>] and /calib[/<target>] open that video's Field setup
    if (focus && (focus.tab === "layout" || focus.tab === "calib")) {
      const byId = sid => dets.find(x => x.dataset.sid === sid), up = byId("__uploads"), mainDet = mainSrc && byId(mainSrc.id);
      let det = null, arg = null;
      if (focus.tab === "layout") {
        det = focus.id ? byId(focus.id) : mainDet;
        if (!det) { det = up; arg = focus.id || null; }
      } else {
        const tg = TARGETS.some(t => t[0] === focus.id) ? focus.id : "overhead", side = tg.replace("station_", "");
        const st = tg === "overhead" ? mainSrc : shown.find(x => x.kind !== "per_match" && whatOf(x) === side);
        det = st ? byId(st.id) : null;
        if (!det) { det = tg === "overhead" ? up : mainDet || up; }
        arg = tg;
      }
      if (det) {
        if (det === up && up.closest("[hidden]")) up.closest("section").hidden = false;
        det.open = true; mount(det, focus.tab, arg);
        requestAnimationFrame(() => det.scrollIntoView({ block: "start" }));
      }
    }
    // ---- refreshed while the page is open: recorder state, matches found, mode checks
    const redrawDyn = () => listEl.querySelectorAll(".vw-card").forEach(c => { const s = S.find(x => x.id === c.dataset.id), dy = c.querySelector(".vw-dyn");
      if (s && dy && !dy.contains(document.activeElement)) { const o = dy.querySelector(".vw-recd"); const was = o && o.open; dy.innerHTML = dynHtml(s); const n = dy.querySelector(".vw-recd"); if (n && was) n.open = true; } });
    const lsDraw = async () => { lsSt = await liveRec.load(ev, evj); if (listEl.isConnected) { redrawDyn(); drawSum(); } };
    lsDraw(); const lsIv = setInterval(lsDraw, 15000); cleanup.push(() => clearInterval(lsIv));
    // ---- actions
    const dst = t => { const s = document.getElementById("dst"); if (s) s.textContent = t; }, dstE = e => { const s = document.getElementById("dst"); if (s) s.innerHTML = errHtml(e); };
    document.getElementById("vpcheck").onclick = async e => {
      e.target.disabled = true;
      try { dst("checking videos…"); const r = await job(ev, A("POST", `events/${ev}/video-plan/check`, {}), dst); dst(`${r.checked} video${r.checked === 1 ? "" : "s"} checked`); route(false); }
      catch (x) { dstE(x); e.target.disabled = false; }
    };
    document.getElementById("disc").onclick = async () => {
      try { dst("asking TBA…"); await job(ev, A("POST", `events/${ev}/sources/discover`), dst); route(false); }
      catch (x) { dstE(x); }
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
        <table class="mini vs-src"><thead><tr><th>Source</th><th>Type</th><th title="${h(Object.entries(ROLE_TIP).map(([k, v]) => ROLE_NAME[k] + ": " + v).join("\n"))}">What it is</th>
          <th title="the overhead video the tracker processes">Tracked</th><th>Covers</th><th>Field box</th><th>On</th><th></th></tr></thead>
        <tbody>${S.length ? S.map(s => `<tr data-id="${h(s.id)}">
          <td><b>${h(s.label || s.id)}</b><div class="muted small vs-url">${s.url ? urlHtml(s.url) :
            s.title_pattern ? `title ~ <code>${h(s.title_pattern)}</code>${s.example_title ? ` · e.g. “${h(s.example_title)}”` : ""}` : ""}</div></td>
          <td data-l="Type"><span><span class="pill">${h(KIND_W[s.kind] || s.kind)}</span> <span class="muted small">${h(s.provider || "")}${s.origin === "tba" ? " · TBA" : ""}</span></span></td>
          <td data-l="What it is"><select data-f="role" aria-label="What ${h(s.label || s.id)} is">${d.roles.map(r => `<option value="${h(r)}" ${r === s.role ? "selected" : ""} title="${h(ROLE_TIP[r])}">${h(ROLE_NAME[r] || r)}</option>`).join("")}</select></td>
          <td data-l="Tracked" class="vs-c"><input type="radio" name="prim" data-f="primary" aria-label="Track ${h(s.label || s.id)}" ${s.primary ? "checked" : ""}></td>
          <td data-l="Covers" class="small">${cov(s)}</td>
          <td data-l="Field box"><a href="#/${ev}/sources/layout/${encodeURIComponent(s.id)}">${s.has_layout ? "edit" : "draw"}</a></td>
          <td data-l="On" class="vs-c"><input type="checkbox" data-f="enabled" aria-label="Use ${h(s.label || s.id)}" ${s.enabled !== false ? "checked" : ""}></td>
          <td class="vs-x"><button class="small" data-del>Remove</button></td></tr>`).join("") :
          `<tr><td colspan="8" class="muted">No sources yet: discover them from TBA or paste a URL below.</td></tr>`}</tbody></table>
      </div>
      <div style="margin-top:12px">
        <h3 style="margin:0 0 8px">Add a source</h3>
        <form id="add" class="vs-form">
          <input name="url" required placeholder="YouTube / Twitch URL (livestream, video, playlist, channel), .m3u8/.mp4 URL, or /media/… file" style="flex:3 1 380px">
          <select name="role" aria-label="What it is">${d.roles.map(r => `<option value="${h(r)}" title="${h(ROLE_TIP[r])}">${h(ROLE_NAME[r] || r)}</option>`).join("")}</select>
          <input name="label" placeholder="Label (optional)…" style="flex:1 1 160px">
          <label class="small"><input type="checkbox" name="primary"> tracked video</label>
          <button>Add</button>${CS.help("Alternate angles (e.g. a team's own overhead recording) can be added the same way; mark the one the tracker should use as <i>tracked</i>.", "About alternate angles")}<span id="ast" class="muted small" role="status"></span>
        </form>
      </div>
      <div class="muted small" style="margin-top:12px">
        Fallback (matches not checked yet): prefer <code>${h((d.video_config || {}).prefer || "^Wide Match (default)")}</code>${(d.video_config || {}).reject ? `, skip <code>${h(d.video_config.reject)}</code>` : ""}${d.station_pattern ? ` · station-cam uploads <code>${h(d.station_pattern)}</code>` : ""}${d.livestream ? ` · livestream ${urlHtml(d.livestream, true)}` : ""}.
        ${d.jobs_inline ? "" : "Frame grabs and calibration run in the worker (its video job runner must be on)."}
        Next: <a href="#/${ev}/sources/layout">draw the field box</a>, then <a href="#/${ev}/sources/calib">calibrate the cameras</a>.
      </div>`;
    el.querySelectorAll("tr[data-id]").forEach(tr => {
      const id = tr.dataset.id;
      tr.querySelectorAll("[data-f]").forEach(inp => inp.onchange = async () => {
        const f = inp.dataset.f, v = inp.type === "checkbox" || inp.type === "radio" ? inp.checked : inp.value;
        try { await A("PATCH", `events/${ev}/sources/${encodeURIComponent(id)}`, { [f]: v }); if (f === "primary") route(false); }
        catch (e) { document.getElementById("ast").innerHTML = errHtml(e); }
      });
      tr.querySelector("[data-del]").onclick = async () => {
        if (!confirm("Remove this source? (its field box is kept)")) return;
        try { await A("DELETE", `events/${ev}/sources/${encodeURIComponent(id)}`); route(false); } catch (e) { document.getElementById("ast").innerHTML = errHtml(e); }
      };
    });
    document.getElementById("add").onsubmit = async e => {
      e.preventDefault();
      const f = e.target, st = document.getElementById("ast");
      try {
        const s = await A("POST", `events/${ev}/sources`, { url: f.url.value.trim(), role: f.role.value, label: f.label.value.trim() || null, primary: f.primary.checked });
        if (s.job) { st.textContent = "indexing the playlist…"; await job(ev, Promise.resolve(s.job), t => st.textContent = t); }
        route(false);
      } catch (x) { st.innerHTML = errHtml(x); }
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
    if (opts.edit) { c.classList.add("vs-draw"); c.tabIndex = 0; c.setAttribute("role", "application"); c.setAttribute("aria-label", opts.edit); }
    // the magnifier: follows a mouse; a finger or pen shows it only while pressed (touch has no hover)
    c.addEventListener("pointermove", e => { if (opts.lens && (e.pointerType === "mouse" || e.buttons)) { V.mouse = V.toImg(e); V.render(); } });
    c.addEventListener("pointerleave", () => { V.mouse = null; V.render(); });
    c.addEventListener("pointerup", e => { if (e.pointerType !== "mouse" && V.mouse) { V.mouse = null; V.render(); } });
    c.addEventListener("blur", () => { if (V.mouse) { V.mouse = null; V.render(); } });
    const onR = () => V.size(); window.addEventListener("resize", onR); (opts.bin || cleanup).push(() => window.removeEventListener("resize", onR));
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
    if (!S.length) { el.innerHTML = `<div class="empty small">No videos yet: add one above, or discover the uploads from TBA.</div>`; return; }
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
          <div class="small muted">Draw boxes ${CS.help("Pick a kind, then drag on the frame with the mouse, a finger or a pen (or press <b>Add box</b>). Tap or click a box to select it; drag it to move, its corners to resize. <b>Keyboard</b>: pick a box with its swatch in the list (or Add box), then the arrow keys move it (Shift: faster), Alt / Option + arrows resize it, Delete removes it.", "How to draw boxes")}</div>
          <div class="chips" id="kinds" role="group" aria-label="Kind of box">${Object.keys(L.kinds).map(k => `<button type="button" class="chip ${k === kind ? "on" : ""}" data-kind="${k}" aria-pressed="${k === kind}" title="${h(L.kinds[k])}"><i class="vs-sw" style="background:${KCOL[k]}"></i>${KSHORT[k] || k}</button>`).join("")}
            <button type="button" id="addbox" class="small" title="A box of the chosen kind in the middle of the frame: move it with the arrow keys, resize with Alt / Option + arrows">Add box</button></div>
          <div id="sugbox"></div>
          <div id="rlist"></div>
          <div class="vs-row" style="margin-top:10px"><button id="save" class="primary">Save layout</button>
            <button id="dflt" title="the 2026 FIRST broadcast boxes the pipeline uses when there is no layout">Load 2026 FIRST defaults</button></div>
          <div class="small muted" id="savest">${saved ? `saved ${when(saved.updated_at)} by ${h(saved.updated_by || "?")}` : "not saved yet — the pipeline uses the 2026 FIRST defaults"}</div>
        </div>
      </div>`;
    const st = t => el.querySelector("#lst").textContent = t, stE = e => { el.querySelector("#lst").innerHTML = errHtml(e); };
    const V = FrameView(el.querySelector("#cv"), { lens: true, zoom: 3, bin: el._bin,
      edit: "Video frame with the boxes. Drag to draw a box; arrow keys move the selected box (Shift: faster), Alt + arrows resize it, Delete removes it." });
    V.empty = "No frame yet: Grab frame"; V.W = W; V.H = H; V.size();
    if (src.has_frame === false) st("no frame yet: Grab frame");      // (a plan group doesn't say: try it)
    else try { V.setImage(await loadImg(imgUrl(`events/${ev}/sources/${encodeURIComponent(src.id)}/frame.jpg`))); W = V.W; H = V.H; }
    catch (e) { st("no frame yet: Grab frame"); }
    el.querySelector("#srcsel").onchange = e => { location.hash = `#/${ev}/sources/layout/${encodeURIComponent(e.target.value)}`; };
    el.querySelector("#kinds").onclick = e => { const k = e.target.closest("[data-kind]"); if (!k) return; kind = k.dataset.kind;
      el.querySelectorAll("#kinds [data-kind]").forEach(c => { c.classList.toggle("on", c === k); c.setAttribute("aria-pressed", String(c === k)); }); };
    const SINGLE = ["station_red", "station_blue", "clock", "field", "overhead"];      // one box of these per video
    el.querySelector("#addbox").onclick = () => {
      const w = W * 0.3, hh = H * 0.3, x0 = (W - w) / 2, y0 = (H - hh) / 2;
      if (SINGLE.includes(kind)) regions = regions.filter(r => r.kind !== kind);
      regions.push({ id: "r" + Date.now().toString(36), kind, label: KSHORT[kind], box: [x0, y0, x0 + w, y0 + hh] });
      sel = regions.length - 1; dirty = true; paint(); V.c.focus();
      st(`${KSHORT[kind] || kind} box added in the middle: move it with the arrow keys or drag it.`);
    };
    el.querySelector("#grab").onclick = async () => {
      const t = el.querySelector("#ft").value;
      try {
        await job(ev, A("POST", `events/${ev}/sources/${encodeURIComponent(src.id)}/frame`, t !== "" ? { t: +t } : {}), st);
        V.setImage(await loadImg(imgUrl(`events/${ev}/sources/${encodeURIComponent(src.id)}/frame.jpg`))); W = V.W; H = V.H; st("");
      } catch (e) { stE(e); }
    };
    el.querySelector("#sug").onclick = async () => {
      const other = el.querySelector("#other").value;
      try {
        const r = await job(ev, A("POST", `events/${ev}/layout/${encodeURIComponent(src.id)}/suggest`, other ? { other } : {}), st);
        const sx = W / r.W, sy = H / r.H;
        sugg = r.suggestions.map(s => ({ ...s, box: [s.box[0] * sx, s.box[1] * sy, s.box[2] * sx, s.box[3] * sy] }));
        st(`${sugg.length} suggestions: accept the ones that fit`); paint();
      } catch (e) { stE(e); }
    };
    el.querySelector("#dflt").onclick = () => {
      const hasInsets = regions.some(r => r.kind.startsWith("station_")) || src.role !== "wide";
      regions = (hasInsets ? L.defaults.program : L.defaults.wide).map(r => ({ ...r, box: [r.box[0] * W / L.defaults.W, r.box[1] * H / L.defaults.H, r.box[2] * W / L.defaults.W, r.box[3] * H / L.defaults.H] }));
      dirty = true; paint();
    };
    el.querySelector("#save").onclick = async () => {
      try {
        await A("PUT", `events/${ev}/layout/${encodeURIComponent(src.id)}`, { W, H, regions: regions.map(r => ({ id: r.id, kind: r.kind, label: r.label, box: r.box.map(Math.round), light_on_dark: r.light_on_dark })) });
        dirty = false; el.querySelector("#savest").textContent = "saved just now"; paint();
      } catch (e) { el.querySelector("#savest").innerHTML = `<span class="vw-ferr">${errHtml(e)}</span>`; }
    };
    const leave = e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", leave); (el._bin || cleanup).push(() => window.removeEventListener("beforeunload", leave));

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
      el.querySelector("#sugbox").innerHTML = sugg.length ? `<div class="vs-sug"><b class="small">Suggestions</b> <button class="small" id="accall">accept all</button> <button class="small" id="dropall">dismiss</button>
        ${sugg.map((s, i) => `<div class="vs-ri"><i class="vs-sw" style="background:${KCOL[s.kind]}"></i>${KSHORT[s.kind] || s.kind} <span class="muted small">${h(s.label || "")} ${s.score != null ? Math.round(s.score * 100) + "%" : ""}</span>
          <button class="small" data-acc="${i}">accept</button></div>`).join("")}</div>` : "";
      el.querySelector("#rlist").innerHTML = regions.map((r, i) => `<div class="vs-ri ${i === sel ? "on" : ""}" data-i="${i}">
        <button type="button" class="vs-pick" data-sel="${i}" aria-pressed="${i === sel}" aria-label="Select box ${i + 1} (${h(r.label || KSHORT[r.kind] || r.kind)}): then the arrow keys move it" title="Select (arrow keys move it)"><i class="vs-sw" style="background:${KCOL[r.kind]}"></i></button>
        <select data-k="${i}">${Object.keys(L.kinds).map(k => `<option value="${k}" ${k === r.kind ? "selected" : ""}>${KSHORT[k] || k}</option>`).join("")}</select>
        <input data-l="${i}" value="${h(r.label || "")}" placeholder="Label…" aria-label="Box label" style="width:110px">
        <span class="muted small vs-xy">${r.box.map(v => Math.round(v)).join(",")}</span>
        ${r.kind === "clock" ? `<label class="small" title="digits lighter than their box"><input type="checkbox" data-ld="${i}" ${r.light_on_dark ? "checked" : ""}>light</label>` : ""}
        <button class="small icon" data-x="${i}" aria-label="Remove box">${CS.icon("close")}</button></div>`).join("") || `<div class="muted small" style="margin-top:8px">No boxes yet.</div>`;
      const acc = i => { const s = sugg[i]; regions.push({ id: "r" + Date.now().toString(36) + i, kind: s.kind, label: s.label, box: s.box.slice() }); dirty = true; };
      el.querySelectorAll("[data-acc]").forEach(b => b.onclick = () => { acc(+b.dataset.acc); sugg.splice(+b.dataset.acc, 1); paint(); });
      const aa = el.querySelector("#accall"); if (aa) aa.onclick = () => { sugg.forEach((_, i) => acc(i)); sugg = []; paint(); };
      const da = el.querySelector("#dropall"); if (da) da.onclick = () => { sugg = []; paint(); };
      el.querySelectorAll("[data-k]").forEach(s => s.onchange = () => { regions[+s.dataset.k].kind = s.value; dirty = true; paint(); });
      el.querySelectorAll("[data-l]").forEach(s => s.oninput = () => { regions[+s.dataset.l].label = s.value; dirty = true; V.render(); });
      el.querySelectorAll("[data-ld]").forEach(s => s.onchange = () => { regions[+s.dataset.ld].light_on_dark = s.checked; dirty = true; });
      el.querySelectorAll("[data-x]").forEach(b => b.onclick = () => { regions.splice(+b.dataset.x, 1); sel = -1; dirty = true; paint(); });
      el.querySelectorAll(".vs-ri[data-i]").forEach(d => d.onclick = e => { if (e.target.closest("select,input,button")) return; sel = +d.dataset.i; paint(); });
      el.querySelectorAll("[data-sel]").forEach(b => b.onclick = () => { const i = +b.dataset.sel; sel = sel === i ? -1 : i; paint(); const n = el.querySelector(`[data-sel="${i}"]`); if (n) n.focus(); });
    }
    // pointer (mouse, finger, pen): draw / move / resize; a finger gets bigger corner targets
    let drag = null;
    const hit = (x, y, touch) => {
      const tol = (touch ? 22 : 9) / V.s;
      for (let i = regions.length - 1; i >= 0; i--) {
        const [x0, y0, x1, y1] = regions[i].box;
        const cs = [[0, 0], [1, 0], [0, 1], [1, 1]].find(([a, b]) => Math.abs(x - (a ? x1 : x0)) < tol && Math.abs(y - (b ? y1 : y0)) < tol);
        if (cs) return { i, mode: "resize", corner: cs };
        if (x > x0 && x < x1 && y > y0 && y < y1) return { i, mode: "move" };
      }
      return null;
    };
    V.c.addEventListener("pointerdown", e => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault(); try { V.c.setPointerCapture(e.pointerId); } catch (x) { } V.c.focus({ preventScroll: true });
      const [x, y] = V.toImg(e), t = hit(x, y, e.pointerType !== "mouse");
      if (t) { sel = t.i; drag = { ...t, x, y, box0: regions[t.i].box.slice() }; }
      else { sel = -1; drag = { mode: "new", x, y, box: [x, y, x, y] }; }
      paint();
    });
    V.c.addEventListener("pointermove", onMove); V.c.addEventListener("pointerup", onUp); V.c.addEventListener("pointercancel", onUp);
    (el._bin || cleanup).push(() => { window.removeEventListener("keydown", onKey); });
    function onMove(e) {
      if (!drag) { if (e.pointerType === "mouse") { const t = hit(...V.toImg(e)); V.c.style.cursor = t ? (t.mode === "move" ? "move" : "nwse-resize") : "crosshair"; } return; }
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
          if (SINGLE.includes(kind)) regions = regions.filter(r => r.kind !== kind);
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
    // keyboard: arrow keys move the selected box (Shift: 10x), Alt / Option + arrows resize it -- on the frame or on the
    // box's swatch button in the list (only the frame redraws, so focus stays where it is)
    el.addEventListener("keydown", e => {
      if (sel < 0 || !regions[sel] || !(e.target === V.c || (e.target.closest && e.target.closest("[data-sel]")))) return;
      if (e.key === "Escape" && e.target === V.c) { sel = -1; paint(); return; }
      const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]; if (!d) return;
      e.preventDefault();
      const step = (e.shiftKey ? 10 : 1) * Math.max(1, Math.round(W / 480)), b = regions[sel].box;
      if (e.altKey) { b[2] = Math.max(b[0] + 8, Math.min(W, b[2] + d[0] * step)); b[3] = Math.max(b[1] + 8, Math.min(H, b[3] + d[1] * step)); }
      else { const dx = Math.max(-b[0], Math.min(W - b[2], d[0] * step)), dy = Math.max(-b[1], Math.min(H - b[3], d[1] * step)); b[0] += dx; b[2] += dx; b[1] += dy; b[3] += dy; }
      dirty = true; V.render();
      const xy = el.querySelector(`.vs-ri[data-i="${sel}"] .vs-xy`); if (xy) xy.textContent = b.map(v => Math.round(v)).join(",");
    });
    paint();
  }

  // ---------------------------------------------------------------- 3. calibration wizard
  async function calibTab(ev, el, target, srcId) {
    const cal = await A("GET", `events/${ev}/calibration`);
    target = TARGETS.some(t => t[0] === target) ? target : "overhead";
    const stat = cal.targets.find(t => t.target === target) || {};
    const fmt = fitQ;
    el.innerHTML = `
      <div class="vs-row">${TARGETS.map(([k, t]) => {
        const s = cal.targets.find(x => x.target === k) || {};
        return `<a class="chip ${k === target ? "on" : ""}" href="#/${ev}/sources/calib/${k}">${t} ${s.saved ? `<span class="pill done">${s.where === "repo" ? "shipped" : "saved"}</span>` : `<span class="pill">none</span>`}</a>`;
      }).join("")}</div>
      <div class="muted small" style="margin:6px 0 10px">${stat.saved ? `Saved calibration: ${h(stat.file)}${stat.calib ? " (" + h(fmt(stat.calib)) + ", " + when(stat.calib.accepted_at || stat.calib.at) + ")" : stat.tag_rms != null ? ` (tag RMS ${stat.tag_rms.toFixed(2)} px)` : ""}. <button class="small" id="showcur">show it on the frame</button>` : "Not calibrated yet."}
        Steps: <b>1</b> detect AprilTags → <b>2</b> click a few field points the tags don't cover → <b>3</b> fit and check the grid sits on the field → <b>4</b> accept.</div>
      <div class="vs-row"><button id="det">1. Detect tags</button><button id="fit" disabled>3. Fit</button><button id="acc" disabled class="on">4. Accept &amp; save</button>
        <span class="small">show: <label><input type="checkbox" id="sg" checked> grid</label> <label><input type="checkbox" id="sh" checked> lines + hubs</label> <label><input type="checkbox" id="st" checked> tags</label></span>
        <span id="cst" class="muted small" role="status"></span></div>
      <p id="cread" class="small muted vc-read" hidden></p>
      <div class="vs-edit">
        <div class="vs-wrap" id="ccv"></div>
        <div class="vs-side">
          <div id="rep"></div>
          <div class="small muted" style="margin-top:8px">2. Pick a line or point, then click or tap on the frame ${CS.help("The magnifier follows the mouse (with a finger or pen it shows while you press); drag a marker to adjust it; the × next to it in the list removes it (right-click works too). Lines are easiest: any 2+ points along a visible edge. <b>Keyboard</b>: pick a line or point in the list, Tab to the frame, Enter places it in the middle, the arrow keys move it (Shift: faster), Delete removes it. Field frame: blue wall X = 0, the blue outpost / chute is on the Y = 0 long side.", "How to place field points")}</div>
          <div id="plist"></div>
        </div>
      </div>`;
    const st = t => el.querySelector("#cst").textContent = t, stE = e => { el.querySelector("#cst").innerHTML = calErrHtml(e); };
    // what Detect read + what to do when it found too few tags (in plain words, above the frame)
    const readNote = (r, n) => { const p = el.querySelector("#cread"), t = r && r.read ? readLine(r) + (n < 2 ? " " + noTagsNext(r.read) : "") : "";
      p.textContent = t; p.hidden = !t; };
    // a fit that can't be trusted: Accept becomes "Save anyway", no longer the highlighted step
    const accState = () => { const b = el.querySelector("#acc"), bad = verdictOf(report) === "bad";
      b.classList.toggle("on", !bad); b.textContent = bad ? "4. Save anyway" : "4. Accept & save";
      b.title = bad ? `Not recommended: ${shortBad(report)}` : ""; };
    const V = FrameView(el.querySelector("#ccv"), { lens: true, zoom: 4, bin: el._bin,
      edit: "Video frame for the calibration. Pick a line or point in the list, then click or tap here; with the keyboard Enter places it, arrow keys move it, Delete removes it." });
    V.empty = "No frame yet: 1. Detect tags grabs one";
    let W = 1920, H = 1080, tags = {}, points = {}, NP = {}, NL = DEFAULT_LINES, lineClicks = {}, overlay = null, pick = null, report = null, drop = new Set();
    const show = { grid: true, lines: true, tags: true };
    ["sg", "sh", "st"].forEach((id, i) => document.getElementById(id).onchange = e => { show[["grid", "lines", "tags"][i]] = e.target.checked; V.render(); });
    let hasFrame = stat.has_frame !== false;          // no wizard frame yet: don't ask for one (a 404 in the console)
    async function loadFrame() {
      if (!hasFrame) return;
      try { V.setImage(await loadImg(imgUrl(`events/${ev}/calibration/${target}/frame.jpg`)), W, H); } catch (e) { }
    }
    async function loadState() {
      if (stat.has_frame !== undefined && !stat.candidate) { st("start with step 1"); V.size(); return; }   // no wizard state yet
      try {
        const w = await A("GET", `events/${ev}/calibration/${target}/wizard`);
        W = w.W; H = w.H; tags = w.fit_tags || w.tags || {}; points = w.points || {}; NP = w.points_catalog || NP;
        NL = w.lines_catalog || NL; lineClicks = w.line_clicks || {};
        report = w.report || null; await loadFrame(); readNote(w, Object.keys(tags).length);
        el.querySelector("#fit").disabled = false; el.querySelector("#acc").disabled = !w.camera; accState();
        if (w.camera) st(w.accepted_at && w.accepted_at > (w.fitted_at || 0) ? "this fit is saved" : "last fit not accepted yet: Fit again to see its overlay");
      } catch (e) { st("start with step 1"); await loadFrame(); V.size(); }
    }
    V.draw = ctx => {
      const lw = 1.6 / V.s;
      paintOverlay(ctx, overlay, V.s, show);
      if (show.tags) paintDropped(ctx, report && report.items, V.s);
      const out = new Set(((report && report.dropped_tags) || []).map(d => +d.id));     // left out by the fit itself
      if (show.tags) for (const [id, t] of Object.entries(tags)) {
        const c = t.corners, off = drop.has(+id);
        ctx.strokeStyle = off ? "#888" : out.has(+id) ? "#ff5a5f" : t.predicted ? "#22b8cf" : "#51cf66"; ctx.lineWidth = 2 / V.s;
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
    const rmX = (k, label) => `<button type="button" class="vs-ptx" data-rmp="${h(k)}" aria-label="${h(label)}" title="${h(label)}">${CS.icon("close")}</button>`;
    function plist() {
      const groups = {};
      Object.entries(NP).forEach(([k, v]) => { if (v.group !== "tags") (groups[v.group] = groups[v.group] || []).push([k, v]); });
      const tagIds = Object.keys(tags);
      el.querySelector("#plist").innerHTML = `<div class="vs-pg"><b class="small">field lines — click 2+ points anywhere along one (easiest)</b>${Object.entries(NL).map(([k, v]) => { const n = (lineClicks[k] || []).length;
        return `<div class="vs-ptr"><button type="button" class="vs-pt ${"line:" + k === pick ? "on" : ""} ${n ? "set" : ""}" data-p="line:${k}" aria-pressed="${"line:" + k === pick}">${n ? "●" + n : "○"} ${h(v.label)}</button>${n ? rmX("line:" + k, `Remove the ${n} point${n > 1 ? "s" : ""} on ${v.label}`) : ""}</div>`; }).join("")}</div>` +
        Object.entries(groups).map(([g, l]) => `<div class="vs-pg"><b class="small">${h(g)}</b>${l.map(([k, v]) =>
        `<div class="vs-ptr"><button type="button" class="vs-pt ${k === pick ? "on" : ""} ${points[k] ? "set" : ""}" data-p="${k}" aria-pressed="${k === pick}">${points[k] ? "●" : "○"} ${h(v.label)}</button>${points[k] ? rmX(k, `Remove ${v.label}`) : ""}</div>`).join("")}</div>`).join("") +
        `<div class="vs-pg"><b class="small">AprilTag centres (when not detected)</b><div class="chips">${Object.entries(NP).filter(([k, v]) => v.group === "tags" && !tagIds.includes(k.slice(4)))
          .map(([k]) => `<span class="vs-tg"><button type="button" class="chip ${k === pick ? "on" : ""} ${points[k] ? "set" : ""}" data-p="${k}" aria-pressed="${k === pick}" aria-label="AprilTag ${k.slice(4)} centre${points[k] ? " (placed)" : ""}">${k.slice(4)}${points[k] ? "●" : ""}</button>${points[k] ? rmX(k, `Remove AprilTag ${k.slice(4)} centre`) : ""}</span>`).join("")}</div></div>`;
      el.querySelectorAll("[data-p]").forEach(d => d.onclick = () => { const k = d.dataset.p; pick = k === pick ? null : k; plist(); V.render();
        const n = el.querySelector(`#plist [data-p="${window.CSS.escape(k)}"]`); if (n) n.focus(); });
      el.querySelectorAll("[data-rmp]").forEach(b => b.onclick = () => { const k = b.dataset.rmp;
        if (k.startsWith("line:")) delete lineClicks[k.slice(5)]; else delete points[k];
        plist(); V.render(); const n = el.querySelector(`#plist [data-p="${window.CSS.escape(k)}"]`); if (n) n.focus(); });
    }
    function rep() {
      const r = report, out = new Map(((r && r.dropped_tags) || []).map(d => [+d.id, d]));
      const tl = Object.entries(tags).map(([id, t]) => { const o = out.get(+id);
        return `<label class="chip ${drop.has(+id) ? "" : "on"} ${o ? "vc-out" : ""}" title="${o ? `left out by the fit: ${h(o.off_px)} px from where the other tags put it` : t.predicted ? "found from the fit" : "detected"}${t.n ? `, ${t.n} frames` : ""}"><input type="checkbox" class="vh" data-t="${id}" ${drop.has(+id) ? "" : "checked"} aria-label="Use AprilTag ${id}${o ? " (left out by the fit: it doesn't line up)" : ""}">${id}${t.predicted ? "*" : ""}${o ? " ✕" : ""}</label>`; }).join("");
      el.querySelector("#rep").innerHTML = `<div class="small"><b>Tags</b> (click to ignore one): <div class="chips">${tl || `<span class="muted">none found — click field lines or points</span>`}</div></div>` +
        (r ? `<div class="vs-kv small">${verdictHtml(r)}${tagErrs(r.items)}<b>Fit</b>: ${h(fmt(r)) || "–"}<br>focal ${h(r.f)} px · k1 ${h(r.k1)} · camera at (${(r.C || []).map(v => (+v).toFixed(1)).join(", ")}) m
          ${Object.entries(r.lines || {}).map(([k, v]) => `<div>${h((NL[k.replace(" (auto)", "")] || {}).label || k)}${/auto/.test(k) ? " (found automatically)" : ""}: <b style="color:${v > 3 ? "var(--warn)" : "var(--good)"}">${v} px</b></div>`).join("")}
          ${(r.items || []).filter(i => i.kind === "point").map(i => `<div>${h((NP[i.id] || {}).label || i.id)}: <b style="color:${i.err_px > 6 ? "var(--bad)" : i.err_px > 3 ? "var(--warn)" : "var(--good)"}">${i.err_px} px</b></div>`).join("")}
          <div class="muted">${verdictOf(r) === "bad" ? "Fix it before saving: click 2 or more points along a few field lines (or field points), untick a tag that doesn't line up, and Fit again." : "Does the yellow outline / grid sit on the carpet everywhere, and the hub boxes on the hubs? Then accept."}</div></div>` : "");
      el.querySelectorAll("[data-t]").forEach(c => c.onchange = () => { c.checked ? drop.delete(+c.dataset.t) : drop.add(+c.dataset.t); rep(); V.render(); });
    }
    // pointer (mouse, finger, pen): place / drag named points; right-click or the list's × removes one
    let dragK = null;
    V.c.addEventListener("pointerdown", e => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault(); try { V.c.setPointerCapture(e.pointerId); } catch (x) { } V.c.focus({ preventScroll: true });
      const [x, y] = V.toImg(e), tol = e.pointerType === "mouse" ? 1 : 2.4;
      const near = Object.entries(points).find(([k, p]) => p && Math.hypot(p[0] - x, p[1] - y) < 10 * tol / V.s);
      if (near) { dragK = near[0]; pick = near[0]; plist(); return; }
      for (const [k, pts] of Object.entries(lineClicks)) {
        const i = (pts || []).findIndex(p => Math.hypot(p[0] - x, p[1] - y) < 8 * tol / V.s);
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
    V.c.addEventListener("pointermove", mv); V.c.addEventListener("pointerup", up); V.c.addEventListener("pointercancel", up);
    // keyboard: Enter places the picked point (or adds a point to the picked line) in the middle of the frame, the
    // arrow keys move it (Shift: 10 px), Delete removes it; the magnifier shows where it is
    V.c.addEventListener("keydown", e => {
      if (!pick) return;
      const line = pick.startsWith("line:"), k = line ? pick.slice(5) : pick;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        const at = [W / 2, H / 2];
        if (line) (lineClicks[k] = lineClicks[k] || []).push(at); else points[k] = at;
        V.mouse = at.slice(); plist(); V.render(); return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        if (line) { (lineClicks[k] || []).pop(); if (!(lineClicks[k] || []).length) delete lineClicks[k]; } else delete points[k];
        V.mouse = null; plist(); V.render(); return;
      }
      const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]; if (!d) return;
      const p = line ? (lineClicks[k] || []).slice(-1)[0] : points[k]; if (!p) return;
      e.preventDefault();
      const s = e.shiftKey ? 10 : 1; p[0] = Math.max(0, Math.min(W, p[0] + d[0] * s)); p[1] = Math.max(0, Math.min(H, p[1] + d[1] * s));
      V.mouse = p.slice(); V.render();
    });

    el.querySelector("#det").onclick = async () => {
      try {
        const r = await job(ev, A("POST", `events/${ev}/calibration/${target}/detect`, srcId ? { source: srcId } : {}), t => st(calPlain(t)));
        W = r.W; H = r.H; tags = r.tags; NP = r.points || NP; NL = r.lines || NL; report = r.report || null; overlay = r.overlay || null; drop = new Set();
        hasFrame = true; await loadFrame(); el.querySelector("#fit").disabled = false;
        const n = Object.keys(tags).length, v = verdictOf(report);
        readNote(r, n);
        st(!n ? "No AprilTags found: click 2 or more points along a few field lines (or field points), then Fit"
          : `${n} AprilTag${n === 1 ? "" : "s"} found${r.report ? ` — a first fit is drawn: ${v === "bad" ? "it can't be trusted (see why on the right)" : v === "check" ? "check it" : "it looks right; check the grid"}`
          : r.fit_error ? `: ${calPlain(r.fit_error)}` : " — click field lines or points (4–6 spread over the field), then Fit"}`);
        plist(); rep(); V.render();
      } catch (e) { stE(e); }
    };
    el.querySelector("#fit").onclick = async () => {
      try {
        const r = await job(ev, A("POST", `events/${ev}/calibration/${target}/fit`, { points, line_clicks: lineClicks, drop_tags: [...drop] }), t => st(calPlain(t)));
        if (r.error) { stE(r.error); return; }
        report = r.report; overlay = r.overlay; tags = r.tags || tags; lineClicks = r.line_clicks || lineClicks;
        const v = verdictOf(report);
        el.querySelector("#acc").disabled = false; accState();
        st(v === "bad" ? `fitted, but it can't be trusted: ${shortBad(report)}` : v === "check" ? "fitted — check the overlay carefully (see the notes)" : "fitted — check the overlay");
        rep(); plist(); V.render();
      } catch (e) { stE(e); }
    };
    el.querySelector("#acc").onclick = async () => {
      if (!confirm(verdictOf(report) === "bad" ? `This fit can't be trusted: ${shortBad(report)}. Robots and shots would be placed wrongly on the field. Save it anyway? (the old file is kept as a backup)`
        : "Save this calibration? The pipeline uses it for the next matches (the old file is kept as a backup).")) return;
      try { await A("POST", `events/${ev}/calibration/${target}/accept`, verdictOf(report) === "bad" ? { force: true } : undefined); st("saved"); route(false); } catch (e) { stE(e); }
    };
    const sc = el.querySelector("#showcur");
    if (sc) sc.onclick = async () => {
      try { const r = await job(ev, A("POST", `events/${ev}/calibration/${target}/overlay`), st); overlay = r.overlay; hasFrame = true; await loadFrame(); V.render(); st(`showing the saved ${r.file}`); }
      catch (e) { stE(e); }
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
    .vc-v { display:grid; gap:4px; margin:6px 0; padding:8px 10px; border-radius:var(--r-s); border:1px solid var(--line); font-size:var(--fs-foot); line-height:1.45; }
    .vc-v.good { border-color:color-mix(in srgb, var(--good) 45%, transparent); background:color-mix(in srgb, var(--good) 8%, transparent); }
    .vc-v.check { border-color:var(--caution-line); background:var(--caution-tint); }
    .vc-v.bad { border-color:color-mix(in srgb, var(--bad) 55%, transparent); background:color-mix(in srgb, var(--bad) 9%, transparent); }
    .vc-vt { display:flex; gap:6px; align-items:center; } .vc-vt .ico { width:15px; height:15px; flex:none; }
    .vc-v.good .vc-vt { color:var(--good); } .vc-v.check .vc-vt { color:var(--caution); } .vc-v.bad .vc-vt { color:var(--bad); }
    .vc-why { margin:0; padding-left:18px; display:grid; gap:3px; color:var(--ink); }
    .vc-why li.bad::marker { color:var(--bad); } .vc-why li.check::marker { color:var(--caution); } .vc-why li.note::marker { color:var(--ink-2); }
    .vc-ok { margin:0; color:var(--ink-2); }
    .vc-tags { display:flex; flex-wrap:wrap; gap:4px; margin:4px 0; }
    .vc-t { padding:1px 8px; border-radius:999px; border:1px solid var(--line); font-variant-numeric:tabular-nums; white-space:nowrap; }
    .vc-t.good { border-color:color-mix(in srgb, var(--good) 45%, transparent); } .vc-t.warn { border-color:var(--caution-line); color:var(--caution); }
    .vc-t.bad { border-color:color-mix(in srgb, var(--bad) 55%, transparent); color:var(--bad); }
    .vc-t.out { border-style:dashed; border-color:color-mix(in srgb, var(--bad) 55%, transparent); color:var(--bad); }
    .vc-whyshort { color:var(--bad); } .vc-read { margin:6px 0 0; } .vc-fig { margin:0; display:grid; gap:4px; min-width:0; }
    .chip.vc-out { border-style:dashed; color:var(--bad); }
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
    .vw-ferr { flex-basis:100%; margin:0; color:var(--bad); font-size:var(--fs-foot); font-weight:500; line-height:1.45; }
    .vs-form input[aria-invalid=true], .vs-form input[aria-invalid=true]:focus-visible { border-color:var(--bad); outline-color:var(--bad); box-shadow:0 0 0 1px var(--bad); }
    .vw-det { display:inline; font-weight:400; } .vw-det summary { display:inline; cursor:pointer; color:var(--ink-2); font-size:var(--fs-cap); text-decoration:underline; }
    .vw-det[open] { display:block; } .vw-det code { display:block; margin-top:4px; white-space:pre-wrap; overflow-wrap:anywhere; font-size:11.5px; color:var(--ink-2); max-height:10em; overflow:auto; }
    .vw-tbaw { flex-basis:100%; display:grid; gap:6px; padding:10px 12px; border:1px dashed var(--line-strong); border-radius:var(--r-s); background:var(--surface-2); }
    .vw-tbaw p { margin:0; } .vw-tbal { list-style:none; margin:0; padding:0; display:grid; gap:6px; }
    .vw-tbal li { display:flex; flex-wrap:wrap; align-items:center; gap:4px 10px; font-size:var(--fs-foot); } .vw-tbal li > span { min-width:0; overflow-wrap:anywhere; }
    .vs-draw { touch-action:none; } .vs-canvas:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
    #kinds .chip { display:inline-flex; align-items:center; min-height:30px; } #addbox { min-height:30px; }
    .vs-pick { display:inline-grid; place-items:center; width:28px; height:28px; padding:0; border-radius:6px; }
    .vs-pick .vs-sw { margin:0; width:12px; height:12px; } .vs-pick[aria-pressed=true] { border-color:var(--accent); background:var(--accent-tint); }
    .vs-ptr { display:flex; align-items:center; gap:2px; } .vs-ptr .vs-pt { flex:1; }
    button.vs-pt { display:block; width:100%; text-align:left; background:none; border:0; color:inherit; min-height:0; }
    .vs-ptx, .vs-tg .vs-ptx { flex:none; display:inline-grid; place-items:center; width:26px; height:26px; padding:0; background:none; border:1px solid transparent; color:var(--ink-2); }
    .vs-ptx svg { width:14px; height:14px; } .vs-ptx:hover { border-color:var(--line-strong); color:var(--bad); }
    .vs-tg { display:inline-flex; align-items:center; }
    .chip:has(> input.vh:focus-visible) { outline:2px solid var(--accent); outline-offset:1px; }
    @media (pointer: coarse) { #kinds .chip, #addbox, .vs-pick, button.vs-pt, .vs-ptx, .vs-tg .chip { min-height:40px; } .vs-pick, .vs-ptx { width:40px; } }
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
