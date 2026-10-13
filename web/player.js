"use strict";
/* Match viewer: real match video + live canvas analysis layers. Routes #/<ev>/m/<match> and #/<ev>/a/<match>[?t=<video s>].
 * Everything is drawn in the browser from the match JSON (+ <match>.fuel.json); nothing is baked into the video.
 * One "View" menu: Video (raw), Field (top-down), Overlay (video with drawings; up to three views, the first one large).
 * Scoreboard, per-team tallies and legends live beside the video (Teams panel, "?"), not on it. */

// ------------------------------------------------------------------ projection (OpenCV projectPoints)
function makeCamera(cam) {
  const [rx, ry, rz] = cam.rvec, th = Math.hypot(rx, ry, rz);
  let R = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  if (th > 1e-12) {
    const x = rx / th, y = ry / th, z = rz / th, c = Math.cos(th), s = Math.sin(th), C = 1 - c;
    R = [c + x * x * C, x * y * C - z * s, x * z * C + y * s,
         y * x * C + z * s, c + y * y * C, y * z * C - x * s,
         z * x * C - y * s, z * y * C + x * s, c + z * z * C];
  }
  const t = cam.tvec, K = cam.K, d = cam.dist || [0, 0, 0, 0, 0];
  const fx = K[0][0], fy = K[1][1], cx = K[0][2], cy = K[1][2], k1 = d[0] || 0, k2 = d[1] || 0, p1 = d[2] || 0, p2 = d[3] || 0, k3 = d[4] || 0;
  // returns [u,v,depth]; depth<=0 means behind the camera
  function project(X, Y, Z) {
    const xc = R[0] * X + R[1] * Y + R[2] * Z + t[0], yc = R[3] * X + R[4] * Y + R[5] * Z + t[1], zc = R[6] * X + R[7] * Y + R[8] * Z + t[2];
    const x = xc / zc, y = yc / zc, r2 = x * x + y * y, rad = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2;
    const xd = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x), yd = y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y;
    return [fx * xd + cx, fy * yd + cy, zc];
  }
  return { project, W: cam.W, H: cam.H };
}
if (typeof module !== "undefined") module.exports = { makeCamera };

// ------------------------------------------------------------------ shot physics (live/shots.py traj)
function shotPos(s, tau, out) {
  const c = s.drag == null ? 0.15 : s.drag, a = s.acc || [0, 0, 0];
  const p1 = c > 1e-9 ? (1 - Math.exp(-c * tau)) / c : tau, p2 = c > 1e-9 ? (tau - p1) / c : tau * tau / 2;
  out[0] = s.origin[0] + s.v0[0] * p1 + a[0] * p2;
  out[1] = s.origin[1] + s.v0[1] * p1 + a[1] * p2;
  out[2] = s.origin[2] + s.v0[2] * p1 + (a[2] - 9.81) * p2;
  return out;
}

const PL = {
  LEN: 16.541, WID: 8.069, HUB_X: 4.03, HUB_Y: 4.035, HUB_Z: 1.83, HUB_R: 0.53, FOOT: 0.91, FPS: 30,
  // [key, label, shortcut key, default on, sources it applies to]
  layers: [
    ["robots", "Robots", "r", 1, "*"], ["labels", "Team numbers", "l", 1, "*"], ["trails", "Trails", "t", 1, "*"], ["arcs", "Shot arcs", "a", 1, "*"],
    ["history", "Shot history", "h", 0, "*"], ["identity", "Identity fixes", "i", 1, "*"], ["score", "Scoreboard", "s", 0, "*"],
    ["fuel", "Raw fuel tracks", "f", 0, "ov"], ["grid", "Calibration grid", "g", 0, "ov red blue"],
  ],
  SRC: { ov: "Overhead", red: "Red station", blue: "Blue station", field: "Field" },
  MODES: [["video", "Video", "The broadcast, nothing drawn on it"], ["field", "Field", "Top-down replay of the tracking"], ["overlay", "Overlay", "Video with tracking drawn on it, up to three views"]],
};
const plLS = {
  get(k, d) { try { const v = localStorage.getItem("pl." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("pl." + k, JSON.stringify(v)); } catch (e) { } },
};
const plLayersFor = src => PL.layers.filter(l => l[4] === "*" || l[4].split(" ").includes(src));
const plDefLayers = src => Object.fromEntries(plLayersFor(src).map(l => [l[0], !!l[3]]));
const PLAY_I = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4.5 2.8v10.4L13 8z" fill="currentColor"/></svg>';
const PAUSE_I = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4 3h3v10H4zM9 3h3v10H9z" fill="currentColor"/></svg>';

async function analyzePage(ev, key) {
  // optional start time: #/<ev>/a/<match>?t=<video seconds> (used by the defense library)
  let seekT = null;
  { const qi = String(key || "").indexOf("?");
    if (qi >= 0) { const q = new URLSearchParams(key.slice(qi + 1)); key = key.slice(0, qi); const v = parseFloat(q.get("t")); if (Number.isFinite(v)) seekT = v; } }
  const [d, evj] = await Promise.all([api(`${ev}/m/${key}.json`), api(`${ev}/event.json`).catch(() => null)]);
  CS.markActive && CS.markActive("matches");
  crumbs([[evj ? evj.name : ev, `#/${ev}`], ["Matches", `#/${ev}/matches`], [d.label]]); $updated.textContent = "";
  const hz = d.hz || 10, v0 = d.t_start - 0.5, ts0 = d.t_teleop_start;
  let hasVideo = !!d.video_local;
  const cam = d.camera ? makeCamera(d.camera) : null;
  const N = d.tracks.length ? d.tracks[0].xy.length : 0;
  const tracks = d.tracks.map((t, i) => {
    const k = d.tracks.filter(x => x.alliance === t.alliance).indexOf(t);
    return { ...t, i, color: COLORS[t.alliance][k % 3], label: t.team ? num(t.team) : `${t.alliance[0].toUpperCase()}${k + 1}`, tc: tconf(t) };
  });
  const ID = d.identity || null, COR = ((ID && ID.corrections) || []).map((c, i) => ({ ...c, i })).sort((a, b) => a.t_video - b.t_video);
  const confAt = (tr, T) => { if (!tr.conf) return null; const i = Math.max(0, Math.min(tr.conf.length - 1, Math.round((T - v0) * hz))); return tr.conf[i]; };
  const UNK = "rgba(160,170,185,.9)";
  const mt2vt = mt => mt < 20 ? d.t_start + mt : v0 + ts0 + (mt - 20);
  const PER = (d.periods && d.periods.length ? d.periods : [["auto", 0, 20], ["transition", 20, 30], ["shift1", 30, 55], ["shift2", 55, 80], ["shift3", 80, 105], ["shift4", 105, 130], ["endgame", 130, 160]]
    .map(([name, t0, t1]) => ({ name, t0, t1, active: { red: name === "shift1" || name === "shift3" || !/^shift/.test(name), blue: name === "shift2" || name === "shift4" || !/^shift/.test(name) } })))
    .map(p => ({ ...p, v0: mt2vt(p.t0), v1: mt2vt(p.t1) }));
  const shots = (d.shots || []).filter(s => s.origin && s.v0).map((s, i) => {
    const out = [0, 0, 0];
    let te = s.outcome === "made" && s.t_entry != null ? s.t_entry : (s.t_obs ? s.t_obs[1] + 0.1 : s.t + 1.5);
    if (!(te > s.t)) te = s.t + 1.2;
    let tauEnd = te - s.t;     // stop at ground contact
    for (let tau = 0.05; tau <= tauEnd; tau += 0.05) { shotPos(s, tau, out); if (out[2] < 0) { tauEnd = tau; break; } }
    const e = shotPos(s, tauEnd, [0, 0, 0]);
    const tr = s.shooter && s.shooter.track_idx != null ? tracks[s.shooter.track_idx] : null;
    const al = (s.shooter && s.shooter.alliance) || (tr && tr.alliance) || "red";
    return { ...s, i, te: s.t + tauEnd, tauEnd, end: e, tr, al, color: tr ? tr.color : COLORS[al][0], unattr: !!s.unattributed, team: s.unattributed ? null : (s.team || (tr && tr.team)) };
  }).sort((a, b) => a.t - b.t);
  const sIdx = shots.map(s => s.t);
  const an = d.analytics || null;

  // ---- configuration: mode + per-view layers (remembered per browser)
  const ST = hasVideo && d.stations && d.stations.video && d.stations.cams ? d.stations : null;
  const srcOK = s => s === "field" || (hasVideo && (s === "ov" || (ST && (s === "red" || s === "blue"))));
  const fixL = (src, L) => { const def = plDefLayers(src), o = {}; for (const k in def) o[k] = L && k in L ? !!L[k] : def[k]; return o; };
  let cfg = plLS.get("cfg", null) || {};
  cfg = { mode: ["video", "field", "overlay"].includes(cfg.mode) ? cfg.mode : "overlay", cam: srcOK(cfg.cam) && cfg.cam !== "field" ? cfg.cam : "ov",
    field: fixL("field", cfg.field), panes: (Array.isArray(cfg.panes) ? cfg.panes : []).filter(p => p && srcOK(p.src)).slice(0, 3).map(p => ({ src: p.src, L: fixL(p.src, p.L) })) };
  // keep each source once
  cfg.panes = cfg.panes.filter((p, i, a) => a.findIndex(q => q.src === p.src) === i);
  if (!cfg.panes.length) cfg.panes = [{ src: hasVideo ? "ov" : "field", L: plDefLayers(hasVideo ? "ov" : "field") }];
  if (!hasVideo) cfg.mode = "field";
  const saveCfg = () => plLS.set("cfg", cfg);
  const S = { on: tracks.map(() => true), outcome: "", trailS: plLS.get("trailS", 4), offset: plLS.get("offset", 0), speed: 1, fuel: null, fuelOn: 0.5 };
  const views = () => cfg.mode === "video" ? [{ src: cfg.cam, L: {} }] : cfg.mode === "field" ? [{ src: "field", L: cfg.field }] : cfg.panes;
  const t0m = seekT != null ? seekT : d.t_start - 3;

  // ---- page
  const W = d.winner, sr = d.red && d.red.score, sb = d.blue && d.blue.score;
  const helpLegend = `<span class="hl"><b>Arcs</b> are coloured by shooter; the ball is yellow in flight. Landing: <b style="color:#51cf66">✓ made</b>, <b style="color:#ff6b6b">✗ missed</b>, <b style="color:#e0b020">→ pass</b>.</span>
    <span class="hl"><b>Team numbers</b>: solid = identity confidence ≥ 80%, "?" = 50–80%, grey "unknown" = under 50%, hidden when the robot is lost. Dashed boxes: position interpolated, not seen.</span>
    <span class="hl"><b>Timeline</b>: the two thin bars under each period show when the red and blue hubs are live. Ticks are made shots; triangles are identity fixes.</span>
    <span class="hl"><b>Keys</b>: space play/pause · ←/→ one frame · shift+←/→ 5 s · v next view · [ ] nudge data sync · layer keys ${PL.layers.map(l => `${l[2]} ${l[1].toLowerCase()}`).join(", ")} (main view).</span>`;
  $app.innerHTML = `<div class="mv">
    <h1 class="vh">${esc(d.label)}</h1>
    <div class="mv-head">
      <span class="mv-score"><span class="score r${W === "red" ? " win" : ""}">${sr ?? "–"}</span><span class="muted"> – </span><span class="score b${W === "blue" ? " win" : ""}">${sb ?? "–"}</span>${W ? ` <span class="muted small">${W === "red" ? "Red" : "Blue"} won</span>` : ""}</span>
      ${d.start_detected === false ? `<span class="pill no_video" title="match start not detected from motion; using a default offset">start estimated</span>` : ""}
      ${d.feed_ok != null && d.feed_ok < 0.98 ? `<span class="pill no_video" title="the field camera was missing from the broadcast for part of this match; stats use only the filmed part">camera footage ${Math.round(d.feed_ok * 100)}%</span>` : ""}
      <span class="pill no_video" id="mvNote" hidden>match video unavailable here · showing the field replay</span>
      ${d.video && d.video.id ? `<a class="mv-yt" href="https://youtu.be/${esc(d.video.id)}?t=${Math.floor(v0)}" target="_blank" rel="noopener">YouTube<span class="vh"> (opens a new tab)</span> ↗</a>` : ""}
    </div>
    <div class="mv-grid">
      <div class="mv-main">
        <div class="mv-stage" id="mvStage">
          <div class="mv-pane" data-src="ov">${hasVideo ? `<video id="anVid" playsinline muted preload="auto" src="${esc(d.video_local)}"></video>` : ""}<canvas></canvas><div class="mv-msg"></div></div>
          ${ST ? ["red", "blue"].map(al => `<div class="mv-pane" data-src="${al}"><canvas></canvas><div class="mv-msg"></div></div>`).join("") : ""}
          <div class="mv-pane" data-src="field"><canvas></canvas><div class="mv-msg"></div></div>
          <div class="mv-id" id="mvId" hidden></div>
        </div>
        ${ST ? `<video id="anIns" playsinline muted preload="auto" src="${esc(ST.video)}" style="display:none"></video>` : ""}
        <div class="an-scrub" id="anScrub" role="slider" tabindex="0" aria-label="Match timeline" aria-valuemin="0"></div>
        <div class="mv-ctl" role="toolbar" aria-label="Playback">
          <button type="button" class="mv-play" id="bPlay" aria-label="Play">${PLAY_I}</button>
          <button type="button" class="mv-ib" id="bM5" aria-label="Back 5 seconds" title="Back 5 s (shift+←)">−5</button>
          <button type="button" class="mv-ib" id="bP5" aria-label="Forward 5 seconds" title="Forward 5 s (shift+→)">+5</button>
          <button type="button" class="mv-ib" id="bBack" aria-label="Previous frame" title="Previous frame (←)">‹</button>
          <button type="button" class="mv-ib" id="bFwd" aria-label="Next frame" title="Next frame (→)">›</button>
          <span class="mv-clock" aria-live="off"><b id="mvClk">–</b><span id="mvPer"></span></span>
          <span class="mv-sp"></span>
          <select id="mvSpeed" aria-label="Playback speed">${[0.25, 0.5, 1, 2].map(s => `<option value="${s}"${s === 1 ? " selected" : ""}>${s}×</option>`).join("")}</select>
          <button type="button" class="mv-viewb" id="mvViewB" aria-haspopup="dialog" aria-expanded="false" aria-controls="mvViewP"></button>
          ${CS.help(helpLegend, "Legend and keyboard shortcuts")}
        </div>
        <div class="mv-pop" id="mvViewP" role="dialog" aria-label="View" hidden></div>
      </div>
      <aside class="mv-side card" aria-labelledby="mvTeamsH">
        <div class="mv-sh"><h2 id="mvTeamsH">Teams</h2>${CS.help(`<span class="hl">Counts follow the video: each robot's made / attempted shots up to the current moment (auto + teleop; passes not counted as attempts). Hover a count for the auto/teleop/pass split.</span><span class="hl">Untick a team to hide it from every view and from the shot list.</span><span class="hl">${esc(UNATTR_TIP)}</span><span class="hl">Fuel stats are reconstructed from the broadcast video; they can differ from the official hub counts (the TBA line below compares them period by period).</span>`, "About these counts")}<span class="mv-cap">FUEL made / shots</span></div>
        ${["red", "blue"].map(al => `<div class="mv-al ${al}">
          <div class="mv-alh"><b>${al === "red" ? "Red" : "Blue"}</b><span id="mvA-${al}" class="mv-n"></span></div>
          ${tracks.filter(t => t.alliance === al).map(t => `<label class="mv-tm"><input type="checkbox" data-i="${t.i}" checked><span class="sw" style="background:${t.color}"></span><span class="team ${al[0]}">${esc(t.label)}</span>${t.tc != null && t.tc < IDC.hi ? idPill(t.tc) : ""}<span class="mv-n" id="mvT-${t.i}"></span></label>`).join("")}
          ${shots.some(s => s.unattr) ? `<div class="mv-tm mv-un"><span class="mv-unl">unattributed</span><span class="mv-n" id="mvU-${al}"></span></div>` : ""}
        </div>`).join("")}
        ${d.reconcile ? `<div class="mv-tba" id="mvTba"></div>` : ""}
      </aside>
    </div>
    ${robotsCard()}
    <details class="mv-sec" id="mvShots"><summary><span class="mv-st">Shots</span><span class="muted" id="anShotN"></span></summary>
      <div class="mv-sb"><div class="mv-fl"><select id="fOut" aria-label="Outcome"><option value="">All outcomes</option><option value="made">Made</option><option value="missed">Missed</option><option value="pass">Passes</option></select>
        <span class="muted small">Teams follow the Teams panel. Select a shot to jump to it.</span></div><div class="an-list" id="anList"></div></div></details>
    ${COR.length || ID ? `<details class="mv-sec"><summary><span class="mv-st">Identity</span><span class="muted">${COR.length} correction${COR.length === 1 ? "" : "s"}${ID && ID.method ? ` · ${esc(ID.method)}` : ""}</span></summary><div class="mv-sb"><div class="an-list" id="anId"></div></div></details>` : ""}
  </div>`;

  function robotsCard() {
    const reads = ID && ID.per_team_reads;
    const ma = (m, a) => m == null && a == null ? "–" : `${m ?? "–"}/${a ?? "–"}`;
    const climb = e => !e ? "–" : e.level ? "L" + e.level : e.attempted ? "failed" : "–";
    const rows = tracks.map(t => {
      const a = (an && an[t.i]) || {}, sc = a.scoring || {}, co = a.collection || {}, au = a.auto || {}, df = a.defense || {}, st = t.stats || {};
      const dead = a.reliability && a.reliability.dead;
      return `<tr><td><span class="sw" style="background:${t.color}"></span> ${t.team ? `<a class="team ${t.alliance[0]}" href="#/${ev}/t/${t.team}">${esc(t.label)}</a>` : `<span class="team muted">${esc(t.label)}</span>`}${dead ? ' <span class="pill failed">dead</span>' : ""}</td>
        <td>${esc(a.role || "–")}</td><td class="num">${ma(sc.made, sc.attempts)}</td><td class="num">${pct(sc.accuracy)}</td>
        <td class="num">${ma(au.made, au.shots)}</td><td>${climb(a.endgame)}</td><td class="num">${pct(st.observed)}</td>
        <td>${t.manual ? '<span class="pill">manual</span>' : idPill(tconf(t)) || "–"}</td>
        <td class="num">${fx(sc.fire_rate)}</td><td class="num">${sc.volley_size ?? "–"}</td><td class="num">${fx(sc.cycle_s)}</td><td class="num">${co.intakes ?? "–"}</td>
        <td>${au.routine ? esc(au.routine) : "–"}</td><td class="num">${fx(df.time_s)}</td><td class="num">${df.contacts ?? "–"}</td><td class="num">${df.pins ?? "–"}</td>
        <td class="num">${fx(st.distance_m, 0)}</td><td class="num">${fx(st.auto_distance_m)}</td><td class="num">${fx(st.max_speed)}</td>
        <td class="num">${pct(st.time_own_zone)} / ${pct(st.time_neutral)} / ${pct(st.time_opp_zone)}</td>
        <td class="num">${reads && t.team ? (reads[t.team] || 0) : "–"}</td></tr>`;
    }).join("");
    const P0 = ' data-pri="0"';
    const note = `<span class="hl">Fuel stats are reconstructed from the broadcast video; counts can differ from the official hub counts. Accuracy = made / attempted (passes excluded).</span>
      <span class="hl"><b>Seen</b> is the share of the match the robot was directly detected; elsewhere its path is interpolated (drawn faded). Team labels are inferred across the event; low-confidence ones may be swapped within an alliance.</span>
      ${ID ? `<span class="hl"><b>Identity</b>: ${esc(ID.method || "")}, ${ID.n_reads ?? "?"} bumper-number reads, ${COR.length} identity correction${COR.length === 1 ? "" : "s"} (${COR.filter(c => c.anchored).length} anchored by a read); see Identity below.</span>` : ""}
      <span class="hl">Use the arrow at the end of a row for fire rate, cycles, intakes, defense and movement.</span>`;
    return `<section class="card mv-robots" aria-labelledby="mvRobH"><div class="mv-sh"><h2 id="mvRobH">Robots</h2><span class="muted small">whole match</span>${CS.help(note, "How these robot stats are measured")}</div>
      <div class="tablewrap"><table class="mv-rt"><thead><tr><th data-pri="1000">Team</th><th data-pri="60">Role</th><th class="num" data-pri="95" title="FUEL made / attempted">Made/Att</th><th class="num" data-pri="85">Acc</th>
        <th class="num" data-pri="70" title="auto FUEL made / attempted">Auto</th><th data-pri="65">Climb</th><th class="num" data-pri="55" title="share of the match the robot was directly detected">Seen</th><th data-pri="75" title="team label (identity) confidence">ID</th>
        <th class="num"${P0}>Fire /s</th><th class="num"${P0}>Volley</th><th class="num"${P0}>Cycle s</th><th class="num"${P0}>Intakes</th><th${P0}>Auto routine</th><th class="num"${P0}>Defense s</th><th class="num"${P0}>Contacts</th><th class="num"${P0}>Pins</th>
        <th class="num"${P0}>Distance m</th><th class="num"${P0}>Auto distance m</th><th class="num"${P0}>Top speed m/s</th><th class="num"${P0}>Own / neutral / opp. zone</th><th class="num"${P0} title="bumper-number OCR reads of this team in this match">Number reads</th></tr></thead>
      <tbody>${rows}</tbody></table></div></section>`;
  }

  // ---- elements
  const $ = id => document.getElementById(id), stageEl = $("mvStage"), ins = $("anIns");
  let vid = $("anVid");
  const pane = {};
  stageEl.querySelectorAll(".mv-pane").forEach(el => {
    const src = el.dataset.src, cv = el.querySelector("canvas");
    const c = src === "red" || src === "blue" ? ST.cams[src] : null;
    const crop = c && c.crop || [0, 0, 1920, 1080];
    pane[src] = { src, el, cv, g: cv.getContext("2d"), msg: el.querySelector(".mv-msg"), w: 0, h: 0, dpr: 1, r: { x: 0, y: 0, w: 0, h: 0 },
      c, crop, cam: src === "ov" ? cam : c && c.usable !== false && c.K ? makeCamera(c) : null,
      camW: src === "ov" ? 1920 : (c && c.W) || (crop[2] - crop[0]) };
  });
  const idbar = $("mvId");
  let ctx = null, tcam = cam, K = 1, curL = {};    // current draw target while drawing one view

  // ---- clock abstraction (video, or free-running when no video)
  const clock = { t: t0m, playing: false, last: 0, dur: d.t_start + 170 };
  const curT = () => vid ? vid.currentTime : clock.t;
  function seek(t) { t = Math.max(0, Math.min(clock.dur, t)); if (vid) vid.currentTime = t; else clock.t = t; draw(); }
  const vt = () => curT() - S.offset;   // data time

  // ---- layout of the views
  function sizePane(p) {
    const r = p.el.getBoundingClientRect(); if (!r.width) { p.w = 0; return; }
    p.dpr = window.devicePixelRatio || 1; p.w = r.width; p.h = r.height;
    p.cv.width = Math.round(p.w * p.dpr); p.cv.height = Math.round(p.h * p.dpr);
    const ar = p.src === "field" ? PL.LEN / PL.WID : p.src === "ov" ? p.w / p.h : (p.crop[2] - p.crop[0]) / (p.crop[3] - p.crop[1]);
    const pad = p.src === "field" ? Math.min(p.w, p.h) * 0.03 : 0;
    let w = p.w - 2 * pad, h = w / ar; if (h > p.h - 2 * pad) { h = p.h - 2 * pad; w = h * ar; }
    p.r = { x: (p.w - w) / 2, y: (p.h - h) / 2, w, h };
  }
  function layout() {
    const vs = views();
    stageEl.dataset.n = vs.length;
    for (const src in pane) {
      const p = pane[src], k = vs.findIndex(v => v.src === src);
      p.el.classList.toggle("off", k < 0);
      p.el.classList.remove("p1", "p2", "p3"); if (k >= 0) p.el.classList.add("p" + (k + 1));
      let tag = p.el.querySelector(".mv-tag");
      if (vs.length > 1 && k >= 0) {
        if (!tag) { tag = document.createElement("button"); tag.type = "button"; tag.className = "mv-tag"; p.el.appendChild(tag); }
        tag.textContent = PL.SRC[src]; tag.disabled = k === 0;
        tag.setAttribute("aria-label", k === 0 ? `${PL.SRC[src]} (main view)` : `Make ${PL.SRC[src]} the main view`); tag.title = k === 0 ? "Main view" : "Make this the main view";
        tag.onclick = e => { e.stopPropagation(); promote(src); };
      } else if (tag) tag.remove();
    }
    for (const src in pane) sizePane(pane[src]);
    $("mvViewB").innerHTML = `<span class="muted">View</span> ${esc(PL.MODES.find(m => m[0] === cfg.mode)[1])}${cfg.mode === "overlay" && vs.length > 1 ? ` <span class="muted">· ${vs.length}</span>` : ""} <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2.5 4.5l3.5 3.5 3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
    syncIns(true); draw();
  }
  function promote(src) {
    if (cfg.mode !== "overlay") return;
    const k = cfg.panes.findIndex(p => p.src === src); if (k <= 0) return;
    const [p] = cfg.panes.splice(k, 1); cfg.panes.unshift(p); saveCfg(); layout(); if (!popEl.hidden) renderPop();
  }
  const ro = new ResizeObserver(es => { for (const e of es) { const p = Object.values(pane).find(x => x.el === e.target); if (p) sizePane(p); } draw(); });
  Object.values(pane).forEach(p => ro.observe(p.el));

  // ---- fuel
  async function loadFuel() {
    if (S.fuel !== null || !d.fuel_url) return;
    S.fuel = [];
    try {
      const f = await api(`${ev}/m/${d.fuel_url}`);
      S.fuel = (f.tracks || []).map(t => ({ t: t.t, uv: t.uv, s: t.s == null ? 1 : t.s, a: t.t[0], b: t.t[t.t.length - 1] }));
      draw();
    } catch (e) { S.fuel = []; pane.ov.msg.textContent = "fuel tracks unavailable"; }
  }

  // ---- data lookups
  function robotAt(tr, t) {   // metres, interpolated; obs of nearest earlier frame
    const f = (t - v0) * hz, i = Math.floor(f);
    if (i < 0 || i > N) return null;
    const i0 = Math.max(0, Math.min(N - 1, i)), i1 = Math.min(N - 1, i0 + 1), a = Math.max(0, Math.min(1, f - i0));
    const p = tr.xy[i0], q = tr.xy[i1];
    if (!p || !q || p[0] == null) return null;
    return [(p[0] + (q[0] - p[0]) * a) / 100, (p[1] + (q[1] - p[1]) * a) / 100, tr.obs ? tr.obs[i0] : 1];
  }
  const PJ = (x, y, z) => { const p = tcam.project(x, y, z); return p[2] > 0.1 ? [p[0] * K, p[1] * K, p[2]] : null; };
  function poly(pts, close) {     // projected polyline in field space; breaks where behind camera
    ctx.beginPath(); let pen = false, first = null;
    for (const q of pts) {
      const p = PJ(q[0], q[1], q[2]);
      if (!p) { pen = false; continue; }
      if (!pen) { ctx.moveTo(p[0], p[1]); pen = true; } else ctx.lineTo(p[0], p[1]);
      if (!first) first = p;
    }
    if (close && first) ctx.closePath();
  }
  const seg = (a, b, n, z) => Array.from({ length: n + 1 }, (_, i) => [a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n, z || 0]);
  const OUTC = { made: "#51cf66", missed: "#ff6b6b", pass: "#ffd43b", unknown: "#adb5bd" };
  const MARK = { made: "✓", missed: "✗", pass: "→", unknown: "?" };
  const hubs = [[PL.HUB_X, PL.HUB_Y, "blue"], [PL.LEN - PL.HUB_X, PL.HUB_Y, "red"]];
  const trackVisible = t => S.on[t.i];
  const shotVisible = s => s.tr ? S.on[s.tr.i] : tracks.some(t => t.alliance === s.al && S.on[t.i]);
  function periodAt(t) { let cur = null; for (const p of PER) if (t >= p.v0) cur = p; return cur; }
  function counts(t) {  // per track + alliance made/attempts split by auto|teleop, plus unattributed
    const c = tracks.map(() => ({ am: 0, aa: 0, tm: 0, ta: 0, p: 0 })), al = { red: { m: 0, a: 0 }, blue: { m: 0, a: 0 } }, un = { red: { n: 0, m: 0, a: 0 }, blue: { n: 0, m: 0, a: 0 } };
    for (const s of shots) {
      if (s.t > t) break;
      const A = al[s.al] || al.red, att = s.outcome === "made" || s.outcome === "missed";
      if (s.outcome === "made") A.m++; if (att) A.a++;
      if (s.unattr) { const u = un[s.al] || un.red; u.n++; if (s.outcome === "made") u.m++; if (att) u.a++; continue; }
      if (!s.tr) continue;
      const x = c[s.tr.i], au = s.period === "auto";
      if (s.outcome === "pass") { x.p++; continue; }
      if (au) { if (att) x.aa++; if (s.outcome === "made") x.am++; } else { if (att) x.ta++; if (s.outcome === "made") x.tm++; }
    }
    return { c, al, un };
  }
  const matchT = T => T < d.t_start ? -1 : (T - d.t_start < 20.5 && T < v0 + ts0 ? T - d.t_start : 20 + Math.max(0, T - (v0 + ts0)));
  const mmss = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  function gameClock(T) {    // what the field display shows
    const mt = matchT(T), p = periodAt(T);
    if (mt < 0) return ["0:20", "Pre-match"];
    if (mt < 20) return [mmss(Math.max(0, 20 - mt)), "Auto"];
    if (mt >= 160) return ["0:00", "Match over"];
    return [mmss(Math.max(0, 140 - (mt - 20))), p ? (PLABEL[p.name] || p.name) : "Teleop"];
  }

  // ---- drawing
  function draw() {
    const T = vt(), vs = views();
    for (const v of vs) drawView(v, T);
    const [clk, per] = gameClock(T); $("mvClk").textContent = clk; $("mvPer").textContent = per;
    moveHead(); updTeams(T); updIdCard(T, vs);
  }
  function drawView(v, T) {
    const p = pane[v.src]; if (!p || !p.w) return;
    const g = p.g; g.setTransform(p.dpr, 0, 0, p.dpr, 0, 0); g.clearRect(0, 0, p.w, p.h);
    g.lineJoin = "round"; g.lineCap = "round";
    ctx = g; curL = v.L || {};
    if (v.src === "field") { drawField(p, T); if (curL.score) drawHud(p, T); return; }
    if (v.src === "red" || v.src === "blue") {
      g.fillStyle = "#000"; g.fillRect(0, 0, p.w, p.h);
      try { if (ins && ins.readyState >= 2) g.drawImage(ins, p.crop[0], p.crop[1], p.crop[2] - p.crop[0], p.crop[3] - p.crop[1], p.r.x, p.r.y, p.r.w, p.r.h); } catch (e) { }
    }
    const anyL = Object.values(curL).some(Boolean);
    p.msg.textContent = !anyL ? "" : !p.cam ? (v.src === "ov" ? "no camera calibration: nothing can be drawn on the video" : !p.c ? "no calibration for this station camera" : `${v.src} station calibration is not usable for this match: nothing drawn`) : "";
    if (p.cam && anyL) {
      tcam = p.cam; K = p.r.w / p.camW;
      g.save(); g.translate(p.r.x, p.r.y); g.beginPath(); g.rect(0, 0, p.r.w, p.r.h); g.clip();
      if (curL.grid) drawGrid();
      if (curL.history) drawHistory(T);
      if (v.src === "ov" && curL.fuel) drawFuel(T);
      if (curL.trails) drawTrails(T);
      if (curL.robots) drawRobots(T);
      if (curL.arcs) drawArcs(T);
      if (curL.identity) drawEncounter(T);
      g.restore();
    }
    if (curL.score) drawHud(p, T);
  }
  function drawGrid() {
    ctx.lineWidth = 1; ctx.strokeStyle = "rgba(255,255,255,.35)";
    for (let x = 0; x <= PL.LEN + 1e-6; x += 1) { poly(seg([x, 0], [x, PL.WID], 16)); ctx.stroke(); }
    for (let y = 0; y <= PL.WID + 1e-6; y += 1) { poly(seg([0, y], [PL.LEN, y], 32)); ctx.stroke(); }
    poly(seg([PL.LEN / 2, 0], [PL.LEN / 2, PL.WID], 16)); ctx.strokeStyle = "rgba(255,255,255,.7)"; ctx.stroke();
    ctx.lineWidth = 2; ctx.strokeStyle = "#00e5ff";
    poly([...seg([0, 0], [PL.LEN, 0], 32), ...seg([PL.LEN, 0], [PL.LEN, PL.WID], 16), ...seg([PL.LEN, PL.WID], [0, PL.WID], 32), ...seg([0, PL.WID], [0, 0], 16)]); ctx.stroke();
    for (const h of hubs) {
      ctx.strokeStyle = h[2] === "red" ? "#ff5a5f" : "#3e8bff"; ctx.lineWidth = 2;
      const ring = z => Array.from({ length: 49 }, (_, i) => [h[0] + PL.HUB_R * Math.cos(i / 48 * 6.2832), h[1] + PL.HUB_R * Math.sin(i / 48 * 6.2832), z]);
      poly(ring(PL.HUB_Z), true); ctx.stroke();
      const sq = z => { const r = 0.53; return [[h[0] - r, h[1] - r, z], [h[0] + r, h[1] - r, z], [h[0] + r, h[1] + r, z], [h[0] - r, h[1] + r, z], [h[0] - r, h[1] - r, z]]; };
      ctx.lineWidth = 1; poly(sq(PL.HUB_Z)); ctx.stroke(); poly(sq(0)); ctx.stroke();
    }
  }
  const labelText = (tr, cf) => cf == null ? tr.label : cf >= IDC.hi ? tr.label : cf >= IDC.mid ? tr.label + " ?" : "unknown";
  function drawRobots(T) {
    const h = PL.FOOT / 2, zt = tcam === cam ? 0.5 : 0.6, fs = Math.max(10, Math.min(13, K * 1920 / 150));
    for (const tr of tracks) {
      if (!trackVisible(tr)) continue;
      const p = robotAt(tr, T); if (!p) continue;
      const cor = [[p[0] - h, p[1] - h], [p[0] + h, p[1] - h], [p[0] + h, p[1] + h], [p[0] - h, p[1] + h]];
      const cf = confAt(tr, T), unk = cf != null && cf < IDC.mid;
      ctx.globalAlpha = cf == null ? 1 : cf <= 0 ? 0.18 : unk ? 0.5 : 1;
      ctx.strokeStyle = unk ? UNK : tr.color; ctx.fillStyle = (unk ? UNK : tr.color) + "30"; ctx.lineWidth = 2.5;
      ctx.setLineDash(p[2] && !unk ? [] : [7, 5]);
      poly(cor.map(c => [c[0], c[1], 0]), true); ctx.fill(); ctx.stroke();
      ctx.lineWidth = 1.2; poly(cor.map(c => [c[0], c[1], zt]), true); ctx.stroke();
      ctx.setLineDash([]);
      for (const c of cor) { poly([[c[0], c[1], 0], [c[0], c[1], zt]]); ctx.stroke(); }
      if (curL.labels && !(cf != null && cf <= 0)) {   // conf 0 = identity/position unknown: no label at all
        const q = PJ(p[0], p[1], zt + 0.15); if (!q) { ctx.globalAlpha = 1; continue; }
        const txt = labelText(tr, cf);
        ctx.font = (unk ? `italic ${fs - 1}px` : `bold ${fs}px`) + " system-ui,sans-serif"; const w = ctx.measureText(txt).width + 8, bh = fs + 4;
        ctx.fillStyle = "rgba(0,0,0,.65)"; ctx.fillRect(q[0] - w / 2, q[1] - bh, w, bh);
        if (cf != null && cf >= IDC.mid && cf < IDC.hi) { ctx.strokeStyle = "#ffd43b"; ctx.lineWidth = 1; ctx.setLineDash([3, 2]); ctx.strokeRect(q[0] - w / 2, q[1] - bh, w, bh); ctx.setLineDash([]); }
        ctx.fillStyle = unk ? UNK : tr.color; ctx.textAlign = "center"; ctx.fillText(txt, q[0], q[1] - 4); ctx.textAlign = "left";
      }
      ctx.globalAlpha = 1;
    }
  }
  function drawTrails(T) {
    ctx.lineWidth = 2;
    for (const tr of tracks) {
      if (!trackVisible(tr)) continue;
      const n = Math.round(S.trailS * hz), f = (T - v0) * hz, i1 = Math.min(N - 1, Math.floor(f));
      if (i1 < 1) continue;
      const pts = []; for (let i = Math.max(0, i1 - n); i <= i1; i++) { const p = tr.xy[i]; if (p && p[0] != null) pts.push([p[0] / 100, p[1] / 100, 0.02]); }
      const m = pts.length; if (m < 2) continue;
      for (let a = 0; a < m - 1; a += 3) { ctx.globalAlpha = 0.15 + 0.7 * a / m; ctx.strokeStyle = tr.color; poly(pts.slice(a, Math.min(m, a + 4))); ctx.stroke(); }
    }
    ctx.globalAlpha = 1;
  }
  function firstShot(T0) { let lo = 0, hi = shots.length; while (lo < hi) { const m = (lo + hi) >> 1; if (sIdx[m] < T0) lo = m + 1; else hi = m; } return lo; }
  function drawArcs(T) {
    for (let k = firstShot(T - 8); k < shots.length && shots[k].t <= T; k++) {
      const s = shots[k]; if (!shotVisible(s)) continue;
      const tau = Math.min(T - s.t, s.tauEnd), done = T >= s.te, fade = done ? Math.max(0, 1 - (T - s.te) / 1.5) : 1;
      if (fade <= 0) continue;
      ctx.globalAlpha = fade; ctx.strokeStyle = s.color; ctx.lineWidth = 3;
      const pts = []; for (let u = 0; u <= tau + 1e-6; u += 0.04) pts.push(shotPos(s, u, [0, 0, 0])); pts.push(shotPos(s, tau, [0, 0, 0]));
      poly(pts); ctx.stroke();
      const head = pts[pts.length - 1], q = PJ(head[0], head[1], head[2]);
      if (q && !done) { ctx.fillStyle = "#ffd43b"; ctx.beginPath(); ctx.arc(q[0], q[1], 5, 0, 6.3); ctx.fill(); ctx.strokeStyle = "#000"; ctx.lineWidth = 1; ctx.stroke(); }
      if (done && q) marker(q[0], q[1], s.outcome, 16);
    }
    ctx.globalAlpha = 1;
  }
  function marker(x, y, o, sz) {
    ctx.fillStyle = "rgba(0,0,0,.6)"; ctx.beginPath(); ctx.arc(x, y, sz * 0.75, 0, 6.3); ctx.fill();
    ctx.fillStyle = OUTC[o] || "#fff"; ctx.font = `bold ${sz}px system-ui,sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(MARK[o] || "?", x, y + 1); ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  }
  function drawHistory(T) {
    ctx.globalAlpha = 0.4;
    for (let k = 0; k < shots.length && shots[k].t <= T; k++) {
      const s = shots[k]; if (T < s.te || !shotVisible(s)) continue;
      const q = PJ(s.end[0], s.end[1], Math.max(0, s.end[2])); if (!q) continue;
      ctx.fillStyle = OUTC[s.outcome] || "#fff"; ctx.beginPath(); ctx.arc(q[0], q[1], 3.5, 0, 6.3); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  function drawFuel(T) {
    if (!S.fuel) { loadFuel(); return; }
    for (const f of S.fuel) {
      if (f.s < S.fuelOn || T < f.a - 0.05 || T > f.b + 0.3) continue;
      ctx.strokeStyle = "rgba(255,230,0,.7)"; ctx.fillStyle = "#ffe600"; ctx.lineWidth = 1.5;
      ctx.beginPath(); let started = false, last = null;
      for (let i = 0; i < f.t.length; i++) {
        if (f.t[i] > T + 1e-3) break; if (f.t[i] < T - 0.5) continue;
        const x = f.uv[i][0] * K, y = f.uv[i][1] * K; if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); last = [x, y, f.t[i]];
      }
      ctx.stroke();
      if (last && T - last[2] < 0.12) { ctx.beginPath(); ctx.arc(last[0], last[1], 5, 0, 6.3); ctx.fill(); }
    }
  }
  // top-down field: broadcast orientation (red end on the left), field.jpg underneath
  function drawField(p, T) {
    const g = ctx, r = p.r, sc = r.w / PL.LEN, FP = (x, y) => [r.x + (PL.LEN - x) * sc, r.y + y * sc];
    g.fillStyle = "#14181d"; g.fillRect(0, 0, p.w, p.h);
    if (FIELD.complete && FIELD.naturalWidth) { g.globalAlpha = .92; g.drawImage(FIELD, r.x, r.y, r.w, r.h); g.globalAlpha = 1; }
    else { g.fillStyle = "#3b4a3f"; g.fillRect(r.x, r.y, r.w, r.h); }
    const L = curL, per = periodAt(T);
    for (const al of ["red", "blue"]) {     // hub rings: solid = live this period
      const [hx, hy] = FP(HUBS[al][0], HUBS[al][1]), act = per ? per.active[al] : null;
      g.lineWidth = Math.max(2, r.w / 300); g.strokeStyle = act ? (al === "red" ? "#ff5a5f" : "#3e8bff") : "#8b98a8"; g.globalAlpha = act ? .95 : .45;
      g.setLineDash(act ? [] : [4, 5]); g.beginPath(); g.arc(hx, hy, 0.75 * sc, 0, 7); g.stroke(); g.setLineDash([]);
      if (act) { g.fillStyle = al === "red" ? "rgba(255,90,95,.18)" : "rgba(62,139,255,.18)"; g.fill(); }
      g.globalAlpha = 1;
    }
    if (L.history) {
      g.globalAlpha = .45;
      for (let k = 0; k < shots.length && shots[k].t <= T; k++) { const s = shots[k]; if (T < s.te || !shotVisible(s)) continue; const [x, y] = FP(s.end[0], s.end[1]); g.fillStyle = OUTC[s.outcome] || "#fff"; g.beginPath(); g.arc(x, y, Math.max(2, sc * .1), 0, 7); g.fill(); }
      g.globalAlpha = 1;
    }
    if (L.trails) {
      g.lineWidth = Math.max(1.5, r.w / 450);
      for (const tr of tracks) {
        if (!trackVisible(tr)) continue;
        const n = Math.round(S.trailS * hz), i1 = Math.min(N - 1, Math.floor((T - v0) * hz)); if (i1 < 1) continue;
        const a0 = Math.max(1, i1 - n);
        for (let i = a0; i <= i1; i++) {
          const q0 = tr.xy[i - 1], q1 = tr.xy[i]; if (!q0 || !q1 || q0[0] == null || q1[0] == null) continue;
          const [x0, y0] = FP(q0[0] / 100, q0[1] / 100), [x1, y1] = FP(q1[0] / 100, q1[1] / 100);
          g.strokeStyle = tr.color; g.globalAlpha = (0.15 + 0.75 * (i - a0) / Math.max(1, i1 - a0)) * (tr.obs && !tr.obs[i] ? 0.4 : 1);
          g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke();
        }
      }
      g.globalAlpha = 1;
    }
    if (L.robots) {
      const s = PL.FOOT * sc, fs = Math.max(10, Math.min(14, r.w / 70));
      for (const tr of tracks) {
        if (!trackVisible(tr)) continue;
        const q = robotAt(tr, T); if (!q) continue;
        const cf = confAt(tr, T), unk = cf != null && cf < IDC.mid, [x, y] = FP(q[0], q[1]);
        g.globalAlpha = cf != null && cf <= 0 ? 0.2 : q[2] ? 0.95 : 0.45;
        g.fillStyle = unk ? "#a0aab9" : tr.color; g.fillRect(x - s / 2, y - s / 2, s, s);
        g.globalAlpha = 1; g.strokeStyle = "#0009"; g.lineWidth = 1; g.strokeRect(x - s / 2, y - s / 2, s, s);
        if (L.labels && !(cf != null && cf <= 0)) {
          const txt = labelText(tr, cf);
          g.font = `${unk ? "italic " : ""}700 ${fs}px system-ui`; g.textAlign = "center"; g.textBaseline = "middle";
          g.fillStyle = "#000c"; g.fillText(txt, x + 1, y - s * .95 + 1); g.fillStyle = unk ? "#d0d6de" : "#fff"; g.fillText(txt, x, y - s * .95);
          g.textAlign = "left"; g.textBaseline = "alphabetic";
        }
      }
    }
    if (L.arcs) {
      const lw = Math.max(2, r.w / 400), tmp = [0, 0, 0];
      for (let k = firstShot(T - 8); k < shots.length && shots[k].t <= T; k++) {
        const s = shots[k]; if (!shotVisible(s)) continue;
        const tau = Math.min(T - s.t, s.tauEnd), done = T >= s.te, fade = done ? Math.max(0, 1 - (T - s.te) / 1.5) : 1; if (fade <= 0) continue;
        const [x0, y0] = FP(s.origin[0], s.origin[1]); shotPos(s, tau, tmp); const [x1, y1] = FP(tmp[0], tmp[1]);
        g.globalAlpha = fade; g.strokeStyle = s.outcome === "pass" ? "#facc15" : s.color; g.lineWidth = lw; g.setLineDash(s.outcome === "missed" && done ? [lw * 3, lw * 2] : []);
        g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke(); g.setLineDash([]);
        if (!done) { g.fillStyle = "#ffd43b"; g.beginPath(); g.arc(x1, y1, Math.max(3, r.w / 220), 0, 7); g.fill(); }
        else marker(x1, y1, s.outcome, Math.max(11, Math.min(15, r.w / 60)));
      }
      g.globalAlpha = 1;
    }
    if (L.identity) {
      const c = corrNear(T);
      if (c && c.encounter && c.encounter.xy && Math.abs(c.t_video - T) <= 1.5) {
        const [x, y] = FP(c.encounter.xy[0], c.encounter.xy[1]);
        g.strokeStyle = c.anchored ? "#ffd43b" : "#8896a8"; g.lineWidth = 2; g.setLineDash([6, 4]); g.beginPath(); g.arc(x, y, 1.6 * sc, 0, 7); g.stroke(); g.setLineDash([]);
      }
    }
    const gap = (d.feed_gaps || []).find(([a, b]) => T - v0 >= a && T - v0 < b);
    if (gap) {
      g.fillStyle = "rgba(13,17,23,.72)"; g.fillRect(r.x, r.y, r.w, r.h);
      g.fillStyle = "#e6edf3"; g.font = `600 ${Math.max(13, r.w / 40)}px system-ui`; g.textAlign = "center";
      g.fillText("No camera feed in the broadcast here", r.x + r.w / 2, r.y + r.h / 2);
      g.font = `${Math.max(11, r.w / 60)}px system-ui`; g.fillStyle = "#8b98a8"; g.fillText("positions are not known for this stretch", r.x + r.w / 2, r.y + r.h / 2 + r.w / 30);
      g.textAlign = "left";
    }
  }
  // optional slim scoreboard on a view: alliance FUEL made so far + game clock, one line at the top
  let cnCache = null;
  function drawHud(p, T) {
    const cn = cnCache || counts(T), [clk, per] = gameClock(T), g = ctx;
    g.setTransform(p.dpr, 0, 0, p.dpr, 0, 0);
    const fs = Math.max(11, Math.min(14, p.w / 60)), h = fs + 10, w = Math.min(p.w - 12, fs * 22), x = (p.w - w) / 2, y = 6;
    g.fillStyle = "rgba(10,14,20,.72)"; g.beginPath(); g.roundRect ? g.roundRect(x, y, w, h, 6) : g.rect(x, y, w, h); g.fill();
    g.font = `700 ${fs}px system-ui,sans-serif`; g.textBaseline = "middle";
    g.textAlign = "left"; g.fillStyle = "#ff8589"; g.fillText(`RED ${cn.al.red.m}`, x + 10, y + h / 2);
    g.textAlign = "right"; g.fillStyle = "#86b6ff"; g.fillText(`${cn.al.blue.m} BLUE`, x + w - 10, y + h / 2);
    g.textAlign = "center"; g.fillStyle = "#fff"; g.font = `600 ${fs - 1}px system-ui,sans-serif`; g.fillText(`${per} ${clk}`, x + w / 2, y + h / 2);
    g.textAlign = "left"; g.textBaseline = "alphabetic";
  }

  // ---- Teams panel (live tally) and the TBA line
  let teamKey = "";
  function updTeams(T) {
    const cn = counts(T); cnCache = cn;
    const p = periodAt(T), rc = d.reconcile && p ? ["red", "blue"].map(al => d.reconcile[al] && d.reconcile[al][p.name]) : null;
    const k = JSON.stringify([cn.c, cn.al, cn.un, p && p.name]); if (k === teamKey) return; teamKey = k;
    for (const al of ["red", "blue"]) { const e = $("mvA-" + al); if (e) e.textContent = `${cn.al[al].m} / ${cn.al[al].a}`; const u = $("mvU-" + al); if (u) u.textContent = `${cn.un[al].m} / ${cn.un[al].a}`; }
    tracks.forEach(t => { const e = $("mvT-" + t.i), c = cn.c[t.i]; if (!e) return; e.textContent = `${c.am + c.tm} / ${c.aa + c.ta}`; e.title = `auto ${c.am}/${c.aa} · teleop ${c.tm}/${c.ta}${c.p ? ` · ${c.p} pass${c.p === 1 ? "" : "es"}` : ""}`; });
    const tb = $("mvTba");
    if (tb) tb.innerHTML = rc && (rc[0] || rc[1]) ? `<span class="muted">${esc(PLABEL[p.name] || p.name)}, whole period:</span> traced vs TBA <span class="r">${rc[0] ? `${rc[0].traced_made}/${rc[0].tba}` : "–"}</span> · <span class="b">${rc[1] ? `${rc[1].traced_made}/${rc[1].tba}` : "–"}</span> ${CS.help(`<span class="hl">FUEL made in the current period as traced from the video (all shooters, including human players) next to the official TBA count for that period, red then blue. Large gaps mean the video missed or double-counted shots in this period.</span>`, "Traced vs TBA")}` : "";
  }
  $app.querySelectorAll(".mv-tm input[type=checkbox]").forEach(cb => cb.onchange = () => { S.on[+cb.dataset.i] = cb.checked; refreshList(); draw(); });

  // ---- identity: corrections (notice on the stage, encounter ring, list)
  const vtOf = f => v0 + f / hz;
  let corrAll = false;   // un-anchored relinks with conf < 0.2 are hidden by default
  const corrWeak = c => c.kind === "relink" && (c.conf || 0) < 0.2;
  const corrVis = () => corrAll ? COR : COR.filter(c => !corrWeak(c));
  const corrClose = c => c.encounter && c.encounter.dist_m != null && c.encounter.dist_m < 1.5;
  const corrNear = T => { let best = null, bd = 3.0001; for (const c of corrVis()) { const dd = Math.abs(c.t_video - T); if (dd < bd) { bd = dd; best = c; } } return best; };
  function bestEvidence(c) { return (c.evidence || []).slice().sort((a, b) => (b[3] - a[3]) || (Math.abs(a[0] - c.frame) - Math.abs(b[0] - c.frame)))[0] || null; }
  const lab = tm => esc(num(tm));
  function corrText(c) {
    const [a, b] = (c.teams || []).map(lab), when = mmss(c.t_video);
    const head = c.kind === "relink" ? `Re-linked: ${a} / ${b} tracks (OCR overrode motion) at ${when}`
      : corrClose(c) ? `Swap fixed: ${a} &harr; ${b} at ${when}` : `Identity cut: ${a} / ${b} at ${when}`;
    const evs = (c.evidence || []).slice().sort((x, y) => (y[3] - x[3]) || (Math.abs(x[0] - c.frame) - Math.abs(y[0] - c.frame))).slice(0, 3);
    return { head, ev: evs.map(e => `read ${lab(e[1])} at ${mmss(vtOf(e[0]))}${e[3] != null && e[3] < 1 ? ` (weight ${e[3]})` : ""}`).join("; "),
      tail: `${c.anchored ? "anchored by a read" : "not anchored by a read (lower trust)"}, confidence ${Math.round((c.conf || 0) * 100)}%${c.unresolved ? `, unresolved ${mmss(vtOf(c.unresolved[0]))}-${mmss(vtOf(c.unresolved[1]))}` : ""}` };
  }
  const seekC = (c, lead) => seek(c.t_video - lead + S.offset);
  let cardFor = null;
  function updIdCard(T, vs) {
    const c = cfg.mode !== "video" && vs.some(v => v.L && v.L.identity) ? corrNear(T) : null;
    if (!c) { if (!idbar.hidden) { idbar.hidden = true; CS.closeHelp && idbar.contains(document.activeElement) && CS.closeHelp(); } cardFor = null; return; }
    idbar.hidden = false;
    if (cardFor !== c.i) {
      cardFor = c.i; const x = corrText(c), e = bestEvidence(c);
      idbar.className = "mv-id" + (c.anchored ? "" : " un");
      idbar.innerHTML = `<b>${x.head}</b> ${CS.help(`<span class="hl">${x.ev ? "Evidence: " + esc(x.ev) + ". " : ""}${esc(x.tail)}.${c.encounter && c.encounter.dist_m != null && (c.kind !== "relink" || c.encounter.dist_m < 3) ? ` Robots ${fx(c.encounter.dist_m, 1)} m apart.` : ""}${c.kind === "relink" && c.motion_link ? ` Motion alone would have linked them (${c.motion_link.frames} frames, ${fx(c.motion_link.jump_m, 1)} m jump).` : ""}</span>`, "Why the identity changed")}
        <button type="button" data-a="enc">Go to encounter</button>${e ? `<button type="button" data-a="ev">Evidence read</button>` : ""}`;
      idbar.onclick = ev2 => { const b = ev2.target.closest("button[data-a]"); if (!b) return; ev2.stopPropagation(); if (vid) vid.pause();
        if (b.dataset.a === "enc") seekC(c, 0); else { const e2 = bestEvidence(c); if (e2) seek(vtOf(e2[0]) + S.offset); } };
    }
  }
  function drawEncounter(T) {
    const c = corrNear(T); if (!c || !c.encounter || !c.encounter.xy) return;
    if (Math.abs(c.t_video - T) > 1.5) return;
    const q = PJ(c.encounter.xy[0], c.encounter.xy[1], 0.02); if (!q) return;
    ctx.strokeStyle = c.anchored ? "#ffd43b" : "#8896a8"; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.ellipse(q[0], q[1], 70 * K, 38 * K, 0, 0, 6.3); ctx.stroke(); ctx.setLineDash([]);
  }
  function buildIdSide() {
    const box = $("anId"); if (!box) return;
    const reads = ID && ID.per_team_reads;
    const vis = corrVis(), hid = COR.length - vis.length;
    box.innerHTML = `${vis.length ? vis.map(c => { const x = corrText(c); return `<button type="button" class="an-idrow ${c.anchored ? "" : "un"}" data-i="${c.i}" title="${esc(x.ev + ". " + x.tail)}"><b>${mmss(c.t_video)}</b> ${c.kind === "relink" ? "re-linked" : corrClose(c) ? "swap fixed" : "cut"} ${(c.teams || []).map(lab).join(" / ")}
        <span class="muted small">${c.anchored ? "anchored" : "unanchored"} ${Math.round((c.conf || 0) * 100)}%</span></button>`; }).join("") : '<div class="muted small">No identity corrections in this match.</div>'}
      ${hid || corrAll && COR.some(corrWeak) ? `<div class="an-reads"><button type="button" class="linkish" id="anIdAll">${corrAll ? "Hide low-confidence relinks" : `Show all (${hid} hidden)`}</button></div>` : ""}
      ${reads ? `<div class="an-reads" title="bumper-number OCR reads per team">Number reads (${ID.n_reads ?? "?"}): ${tracks.map(t => t.team ? `${esc(num(t.team))}&times;${reads[t.team] || 0}` : "").filter(Boolean).join(" &middot; ")}</div>` : ""}`;
    box.onclick = e => { const b = e.target.closest("button"); if (!b) return;
      if (b.id === "anIdAll") { corrAll = !corrAll; cardFor = null; buildIdSide(); buildScrub(); return; }
      if (b.classList.contains("an-idrow")) seekC(COR.find(c => c.i === +b.dataset.i), 2); };
  }

  // ---- scrubber: the match only (not the whole upload); per period a red and a blue lane, tinted while that hub is live,
  // made FUEL as density blocks, identity fixes as small marks
  const scrub = $("anScrub");
  const dom = () => { const lo = Math.max(0, d.t_start - 4), hi = Math.min(clock.dur, v0 + ts0 + 146); return hi > lo + 20 ? [lo, hi] : [0, clock.dur]; };
  const X = t => { const [lo, hi] = dom(); return Math.max(0, Math.min(100, (t - lo) / (hi - lo) * 100)); };
  function buildScrub() {
    const [lo, hi] = dom(), BIN = 1.5, bins = { red: {}, blue: {} };
    for (const s of shots) if (s.outcome === "made" && s.t >= lo && s.t <= hi) { const b = Math.floor((s.t - lo) / BIN), B = bins[s.al] || bins.red; B[b] = (B[b] || 0) + 1; }
    const mx = Math.max(1, ...["red", "blue"].flatMap(a => Object.values(bins[a])));
    scrub.setAttribute("aria-valuemin", Math.round(lo)); scrub.setAttribute("aria-valuemax", Math.round(hi));
    scrub.innerHTML = PER.map(p => `<div class="seg" style="left:${X(p.v0)}%;width:${X(p.v1) - X(p.v0)}%" title="${esc(PLABEL[p.name] || p.name)}"><span>${esc(PLABEL[p.name] || p.name)}</span>
      <i class="r ${p.active.red ? "on" : ""}"></i><i class="b ${p.active.blue ? "on" : ""}"></i></div>`).join("") +
      ["red", "blue"].map(al => Object.entries(bins[al]).map(([b, n]) => `<u class="${al}" style="left:${X(lo + b * BIN)}%;width:${X(lo + (+b + 1) * BIN) - X(lo + b * BIN)}%;opacity:${(0.25 + 0.75 * n / mx).toFixed(2)}"></u>`).join("")).join("") +
      corrVis().map(c => `<b class="idm ${c.anchored ? "" : "un"}" style="left:${X(c.t_video)}%" title="${esc(corrText(c).head.replace("&harr;", "<->"))}"></b>`).join("") + `<div class="head" id="anHead"></div>`;
  }
  function moveHead() { const h = $("anHead"); if (h) h.style.left = X(curT()) + "%"; scrub.setAttribute("aria-valuenow", Math.round(curT())); scrub.setAttribute("aria-valuetext", `${$("mvPer").textContent} ${$("mvClk").textContent}`); }
  function scrubTo(e) { const r = scrub.getBoundingClientRect(), [lo, hi] = dom(); seek(lo + Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (hi - lo)); }
  let drag = false;
  scrub.onpointerdown = e => { drag = true; scrub.setPointerCapture(e.pointerId); scrubTo(e); };
  scrub.onpointermove = e => { if (drag) scrubTo(e); };
  scrub.onpointerup = () => { drag = false; };
  scrub.onkeydown = e => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); seek(curT() + (e.key === "ArrowLeft" ? -5 : 5)); } };

  // ---- shot list
  const fmtT = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;
  function refreshList() {
    const rows = shots.filter(s => shotVisible(s) && (!S.outcome || s.outcome === S.outcome));
    $("anShotN").textContent = String(rows.length);
    const box = $("mvShots"); if (!box.open) return;
    $("anList").innerHTML = rows.map(s => `<button type="button" class="an-row" data-i="${s.i}"><span class="sw" style="background:${s.color}"></span>
      <b>${fmtT(s.t)}</b> <span style="color:${OUTC[s.outcome]}">${MARK[s.outcome] || "?"} ${esc(s.outcome)}</span> ${s.unattr ? `<i class="muted" title="${esc(UNATTR_TIP)}">unattributed</i>` : s.team ? esc(num(s.team)) : esc(s.shooter && s.shooter.kind || "")}
      <span class="muted small">${esc(PLABEL[s.period] || s.period || "")}${s.conf != null ? " · " + Math.round(s.conf * 100) + "%" : ""}</span></button>`).join("") || '<div class="muted small">No shots for these teams.</div>';
  }
  $("mvShots").addEventListener("toggle", refreshList);
  $("anList").onclick = e => { const r = e.target.closest(".an-row"); if (!r) return; const s = shots.find(x => x.i === +r.dataset.i); seek(s.t - 1 + S.offset); stageEl.scrollIntoView({ block: "nearest", behavior: "smooth" }); };
  $("fOut").onchange = e => { S.outcome = e.target.value; refreshList(); };

  // ---- driver-station cameras (second video = full broadcast inset; each station is a crop of it)
  const SYNC = ST && ST.sync || { offset: 0, knots: [] };
  const KN = (SYNC.knots && SYNC.knots.length ? SYNC.knots : [[0, SYNC.offset || 0]]).slice().sort((a, b) => a[0] - b[0]);
  function insOff(ti) {   // overhead time - inset time, piecewise-linear over knots, linear extrapolation
    const n = KN.length; if (n === 1) return KN[0][1];
    let i = 0; while (i < n - 2 && ti > KN[i + 1][0]) i++;
    const a = KN[i], b = KN[i + 1], dt = b[0] - a[0];
    return dt > 1e-9 ? a[1] + (b[1] - a[1]) * (ti - a[0]) / dt : a[1];
  }
  const ov2ins = ov => { let ti = ov - insOff(ov); for (let k = 0; k < 4; k++) ti = ov - insOff(ti); return ti; };
  const needIns = () => !!ins && views().some(v => v.src === "red" || v.src === "blue");
  let lastIns = 0;
  function syncIns(force) {
    if (!ins || !vid || !needIns()) { if (ins && !ins.paused) ins.pause(); return; }
    const tgt = Math.max(0, ov2ins(vid.currentTime));
    if (ins.playbackRate !== vid.playbackRate) ins.playbackRate = vid.playbackRate;
    if (vid.paused) { if (!ins.paused) ins.pause(); }
    else if (ins.paused && ins.readyState >= 2) ins.play().catch(() => { });
    const err = Math.abs(ins.currentTime - tgt), now = performance.now();
    if (err > (vid.paused ? 0.4 / PL.FPS : 1.2 / PL.FPS) && (force || vid.paused || now - lastIns > 300 || err > 0.5)) { lastIns = now; ins.currentTime = tgt; }
  }
  if (ins) {
    ins.addEventListener("seeked", () => draw()); ins.addEventListener("loadeddata", () => draw());
    ins.addEventListener("error", () => { for (const al of ["red", "blue"]) if (pane[al]) pane[al].msg.textContent = "station video failed to load"; });
    const id = setInterval(() => { if (!dead && needIns()) syncIns(); }, 250); cleanup.push(() => clearInterval(id));
  }

  // ---- View menu (one place for every view and its layers)
  const popEl = $("mvViewP"), viewB = $("mvViewB");
  let openRow = -1;
  const chk = (id, on, label, dis, data) => `<label class="mv-ck${dis ? " dis" : ""}"><input type="checkbox" ${data}${on ? " checked" : ""}${dis ? " disabled" : ""}> ${esc(label)}</label>`;
  const layerChecks = (src, L, path) => plLayersFor(src).map(l => chk("", L[l[0]], l[1], false, `data-path="${path}" data-l="${l[0]}"`)).join("");
  function renderPop() {
    const noSt = !ST, m = cfg.mode;
    const stNote = !hasVideo ? "No local video for this match: only the field replay is available." : noSt ? `Overhead only · no station cams for this match ${CS.help(`Station views come from the event's separate "Match N" upload with the two driver-station cameras. This match has none on TBA (at this event, day 2 and playoffs only have the broadcast).`, "Why there are no station cams")}` : "";
    const modeRow = `<div class="mv-seg" role="radiogroup" aria-label="View">${PL.MODES.map(([k, t, sub]) => { const dis = !hasVideo && k !== "field";
      return `<button type="button" role="radio" aria-checked="${m === k}" data-mode="${k}"${dis ? " disabled" : ""} title="${esc(sub)}">${t}</button>`; }).join("")}</div>
      <div class="mv-psub">${esc(PL.MODES.find(x => x[0] === m)[2])}</div>`;
    let body = "";
    if (m === "video") {
      body = `<div class="mv-ph">Camera</div><div class="mv-rad" role="radiogroup" aria-label="Camera">${["ov", "red", "blue"].map(s => { const dis = !srcOK(s);
        return `<label class="mv-ck${dis ? " dis" : ""}"><input type="radio" name="mvcam" value="${s}"${cfg.cam === s ? " checked" : ""}${dis ? " disabled" : ""}> ${PL.SRC[s]}</label>`; }).join("")}</div>`;
    } else if (m === "field") {
      body = `<div class="mv-ph">Show</div><div class="mv-cks">${layerChecks("field", cfg.field, "field")}</div>`;
    } else {
      const used = new Set(cfg.panes.map(p => p.src)), free = ["ov", "red", "blue", "field"].filter(s => !used.has(s) && srcOK(s));
      body = `<div class="mv-ph">Views <span class="muted">· up to 3, the first is the large one</span></div>
        <ol class="mv-vl">${cfg.panes.map((p, i) => `<li class="${openRow === i ? "open" : ""}"><div class="mv-vr">
          <span class="mv-vn">${i + 1}</span><span class="mv-vt">${PL.SRC[p.src]}</span>
          <button type="button" class="mv-sm" data-act="lay" data-i="${i}" aria-expanded="${openRow === i}">Layers</button>
          ${i ? `<button type="button" class="mv-sm" data-act="up" data-i="${i}" title="Make this the large view" aria-label="Make ${PL.SRC[p.src]} the large view">↑</button>` : ""}
          ${cfg.panes.length > 1 ? `<button type="button" class="mv-sm" data-act="rm" data-i="${i}" aria-label="Remove ${PL.SRC[p.src]}">✕</button>` : ""}</div>
          ${openRow === i ? `<div class="mv-cks">${layerChecks(p.src, p.L, "p" + i)}</div>` : ""}</li>`).join("")}</ol>
        ${cfg.panes.length < 3 && free.length ? `<div class="mv-add"><span class="muted">Add</span>${free.map(s => `<button type="button" class="mv-sm" data-act="add" data-src="${s}">+ ${PL.SRC[s]}</button>`).join("")}</div>` : ""}`;
    }
    const sync = Math.round(S.offset * PL.FPS);
    popEl.innerHTML = `${modeRow}${body}${stNote ? `<div class="mv-pnote">${stNote}</div>` : ""}
      <div class="mv-pfoot"><span>Trail <select data-x="trail" aria-label="Trail length">${[2, 4, 6, 8, 12].map(s => `<option value="${s}"${S.trailS === s ? " selected" : ""}>${s} s</option>`).join("")}</select></span>
        <span>Data sync <button type="button" class="mv-sm" data-x="s-" aria-label="Data one frame earlier">−</button><b>${sync > 0 ? "+" : ""}${sync}</b> fr<button type="button" class="mv-sm" data-x="s+" aria-label="Data one frame later">+</button></span></div>`;
  }
  function openPop() {
    renderPop(); popEl.hidden = false; viewB.setAttribute("aria-expanded", "true"); placePop();
    const f = popEl.querySelector("[aria-checked=true]") || popEl.querySelector("input,button"); f && f.focus();
  }
  function closePop(focus) { if (popEl.hidden) return; popEl.hidden = true; viewB.setAttribute("aria-expanded", "false"); if (focus) viewB.focus(); }
  function placePop() {
    if (popEl.hidden) return;
    const r = viewB.getBoundingClientRect(), vw = document.documentElement.clientWidth, pw = popEl.offsetWidth, ph = popEl.offsetHeight;
    let y = r.bottom + 6; if (y + ph > innerHeight - 8 && r.top - ph - 6 > 8) y = r.top - ph - 6;
    popEl.style.left = Math.max(8, Math.min(r.right - pw, vw - pw - 8)) + "px"; popEl.style.top = Math.max(8, y) + "px";
  }
  viewB.onclick = () => popEl.hidden ? openPop() : closePop();
  const onDocDown = e => { if (!popEl.hidden && !popEl.contains(e.target) && !viewB.contains(e.target) && !(e.target.closest && e.target.closest(".hlp-p"))) closePop(); };
  document.addEventListener("pointerdown", onDocDown, true);
  addEventListener("scroll", placePop, true); addEventListener("resize", placePop);
  popEl.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); closePop(true); } });
  const reopen = sel => { renderPop(); placePop(); const f = sel && popEl.querySelector(sel); if (f) f.focus(); };
  popEl.onclick = e => {
    const b = e.target.closest("button"); if (!b || b.classList.contains("hlp-b")) return;
    if (b.dataset.mode) { cfg.mode = b.dataset.mode; saveCfg(); layout(); return reopen(`[data-mode="${cfg.mode}"]`); }
    const i = +b.dataset.i, act = b.dataset.act;
    if (act === "lay") { openRow = openRow === i ? -1 : i; return reopen(`[data-act="lay"][data-i="${i}"]`); }
    if (act === "up") { const [p] = cfg.panes.splice(i, 1); cfg.panes.unshift(p); openRow = -1; saveCfg(); layout(); return reopen(`[data-act="lay"][data-i="0"]`); }
    if (act === "rm") { cfg.panes.splice(i, 1); openRow = -1; saveCfg(); layout(); return reopen(".mv-vl button"); }
    if (act === "add") { const src = b.dataset.src; cfg.panes.push({ src, L: plDefLayers(src) }); openRow = cfg.panes.length - 1; saveCfg(); layout(); return reopen(`[data-act="lay"][data-i="${openRow}"]`); }
    if (b.dataset.x === "s-" || b.dataset.x === "s+") { setOff(Math.round(S.offset * PL.FPS) + (b.dataset.x === "s+" ? 1 : -1)); return reopen(`[data-x="${b.dataset.x}"]`); }
  };
  popEl.onchange = e => {
    const t = e.target;
    if (t.name === "mvcam") { cfg.cam = t.value; saveCfg(); layout(); return; }
    if (t.dataset.x === "trail") { S.trailS = +t.value; plLS.set("trailS", S.trailS); draw(); return; }
    if (t.dataset.l) {
      const L = t.dataset.path === "field" ? cfg.field : cfg.panes[+t.dataset.path.slice(1)].L;
      L[t.dataset.l] = t.checked; saveCfg(); if (t.dataset.l === "fuel" && t.checked) loadFuel(); draw();
    }
  };
  function toggleLayer(k) {     // keyboard: layer on the main view
    const v = views()[0]; if (!v || cfg.mode === "video" || !(k in v.L)) return;
    v.L[k] = !v.L[k]; saveCfg(); if (k === "fuel" && v.L[k]) loadFuel(); if (!popEl.hidden) renderPop(); draw();
  }
  function cycleMode() { const ms = hasVideo ? ["video", "field", "overlay"] : ["field"]; cfg.mode = ms[(ms.indexOf(cfg.mode) + 1) % ms.length]; saveCfg(); layout(); if (!popEl.hidden) renderPop(); }

  // ---- transport
  const step = n => { if (vid) vid.pause(); seek((Math.round(curT() * PL.FPS) + n) / PL.FPS + 0.002); };
  const togglePlay = () => { if (vid) { vid.paused ? vid.play().catch(() => { }) : vid.pause(); } else { clock.playing = !clock.playing; clock.last = performance.now(); loop(); } updPlay(); };
  const updPlay = () => { const pl = vid ? !vid.paused : clock.playing, b = $("bPlay"); if (!b) return; b.innerHTML = pl ? PAUSE_I : PLAY_I; b.setAttribute("aria-label", pl ? "Pause" : "Play"); };
  $("bPlay").onclick = togglePlay; $("bBack").onclick = () => step(-1); $("bFwd").onclick = () => step(1);
  $("bM5").onclick = () => seek(curT() - 5); $("bP5").onclick = () => seek(curT() + 5);
  $("mvSpeed").onchange = e => { S.speed = +e.target.value; if (vid) vid.playbackRate = S.speed; syncIns(); };
  const setOff = fr => { S.offset = fr / PL.FPS; plLS.set("offset", S.offset); draw(); };
  stageEl.addEventListener("click", e => { if (e.target.closest("button,a,.hlp-p,.mv-id")) return; togglePlay(); });
  function onKey(e) {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) && e.target.type !== "range") return;
    if (e.target.closest && e.target.closest(".mv-pop")) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key;
    if ((k === " " || k === "Enter") && /^(BUTTON|A|SUMMARY)$/.test(e.target.tagName)) return;
    if (k === " ") togglePlay();
    else if (k === "ArrowLeft") e.shiftKey ? seek(curT() - 5) : step(-1);
    else if (k === "ArrowRight") e.shiftKey ? seek(curT() + 5) : step(1);
    else if (k === "v") cycleMode();
    else if (k === "[") setOff(Math.round(S.offset * PL.FPS) - 1);
    else if (k === "]") setOff(Math.round(S.offset * PL.FPS) + 1);
    else { const l = PL.layers.find(x => x[2] === k.toLowerCase()); if (!l) return; toggleLayer(l[0]); }
    e.preventDefault();
  }
  document.addEventListener("keydown", onKey);

  // ---- frame-accurate draw loop
  let rv = 0, raf = 0, dead = false;
  function loop() {
    if (dead) return;
    if (!vid && clock.playing) { const n = performance.now(); clock.t += (n - clock.last) / 1000 * S.speed; clock.last = n; if (clock.t >= clock.dur) { clock.t = clock.dur; clock.playing = false; updPlay(); } draw(); raf = requestAnimationFrame(loop); }
  }
  if (vid) {
    const onFrame = () => { if (dead) return; syncIns(); draw(); rv = vid.requestVideoFrameCallback(onFrame); };
    if ("requestVideoFrameCallback" in HTMLVideoElement.prototype) rv = vid.requestVideoFrameCallback(onFrame);
    else { const tick = () => { if (dead) return; if (!vid.paused) { syncIns(); draw(); } raf = requestAnimationFrame(tick); }; raf = requestAnimationFrame(tick); }
    for (const ev2 of ["seeked", "timeupdate", "loadeddata", "pause", "play", "ratechange"]) vid.addEventListener(ev2, () => { syncIns(ev2 === "seeked"); draw(); updPlay(); });
    vid.addEventListener("loadedmetadata", () => { clock.dur = vid.duration; buildScrub(); if (!vid.currentTime) vid.currentTime = Math.max(0, t0m); });
    vid.addEventListener("error", () => {   // never loaded (missing file): fall back to the field replay; a later hiccup only says so
      if (!vid) return;
      if (vid.readyState === 0 && vid.error && vid.error.code === 4) failVideo(); else pane.ov.msg.textContent = "the video could not be played here; reload the page to retry (the field view still works)";
    });
  } else clock.t = t0m;
  function failVideo() {     // no playable video: run the data on its own clock and show the field replay
    const v = vid; vid = null; hasVideo = false;
    try { v.removeAttribute("src"); v.load(); } catch (e) { }
    clock.t = Math.max(0, t0m); clock.dur = d.t_start + 170; cfg.mode = "field";
    $("mvNote").hidden = false; buildScrub(); layout(); updPlay(); if (!popEl.hidden) renderPop();
  }
  if (FIELD.complete) draw(); else FIELD.addEventListener("load", () => draw(), { once: true });
  if (views().some(v => v.L && v.L.fuel)) loadFuel();
  buildScrub(); buildIdSide(); refreshList(); layout(); updPlay();
  window.__an = { ins, ov2ins, S, cfg, seek, draw, vid, shots, tracks, cam, step, toggleLayer, layout, setMode: m => { cfg.mode = m; layout(); }, setPanes: ps => { cfg.panes = ps.map(s => ({ src: s, L: plDefLayers(s) })); cfg.mode = "overlay"; layout(); } };
  cleanup.push(() => { dead = true; closePop(); document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDocDown, true); removeEventListener("scroll", placePop, true); removeEventListener("resize", placePop);
    ro.disconnect(); cancelAnimationFrame(raf); if (vid && rv && vid.cancelVideoFrameCallback) vid.cancelVideoFrameCallback(rv);
    if (ins) { ins.pause(); ins.removeAttribute("src"); ins.load(); } if (vid) { vid.pause(); vid.removeAttribute("src"); vid.load(); } delete window.__an; });
}
