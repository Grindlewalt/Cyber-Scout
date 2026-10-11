"use strict";
/* Analyze view: real match video + live canvas analysis layers. Route #/<ev>/a/<match>.
 * Everything is drawn in the browser from the match JSON (+ <match>.fuel.json); nothing is baked into the video. */

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
  layers: [
    ["robots", "Robots", "r", 1], ["labels", "Labels", "l", 1], ["trails", "Trails", "t", 1], ["arcs", "Shot arcs", "a", 1],
    ["history", "Shot history", "h", 1], ["fuel", "Raw fuel tracks", "f", 0], ["minimap", "Minimap", "m", 1],
    ["identity", "Identity", "i", 1], ["score", "Scoreboard", "s", 1], ["tba", "Traced vs TBA", "b", 0], ["grid", "Calibration grid", "g", 0], ["clock", "Period bar", "c", 1],
  ],
};
const plLS = {
  get(k, d) { try { const v = localStorage.getItem("pl." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("pl." + k, JSON.stringify(v)); } catch (e) { } },
};

async function analyzePage(ev, key) {
  const d = await api(`${ev}/m/${key}.json`);
  crumbs([[ev, `#/${ev}`], [d.label, `#/${ev}/m/${key}`], ["Analyze"]]); $updated.textContent = "";
  const hz = d.hz || 10, v0 = d.t_start - 0.5, ts0 = d.t_teleop_start;
  const hasVideo = !!d.video_local, cam = d.camera ? makeCamera(d.camera) : null;
  const N = d.tracks.length ? d.tracks[0].xy.length : 0;
  const tracks = d.tracks.map((t, i) => {
    const k = d.tracks.filter(x => x.alliance === t.alliance).indexOf(t);
    return { ...t, i, color: COLORS[t.alliance][k % 3], label: t.team ? num(t.team) : `${t.alliance[0].toUpperCase()}${k + 1}`, tc: tconf(t) };
  });
  const ID = d.identity || null, COR = ((ID && ID.corrections) || []).map((c, i) => ({ ...c, i })).sort((a, b) => a.t_video - b.t_video);
  const confAt = (tr, T) => { if (!tr.conf) return null; const i = Math.max(0, Math.min(tr.conf.length - 1, Math.round((T - v0) * hz))); return tr.conf[i]; };
  const UNK = "rgba(160,170,185,.9)";
  const mt2vt = mt => mt < 20 ? d.t_start + mt : v0 + ts0 + (mt - 20);
  const PER = (d.periods || []).map(p => ({ ...p, v0: mt2vt(p.t0), v1: mt2vt(p.t1) }));
  const shots = (d.shots || []).map((s, i) => {
    const out = [0, 0, 0];
    let te = s.outcome === "made" && s.t_entry != null ? s.t_entry : (s.t_obs ? s.t_obs[1] + 0.1 : s.t + 1.5);
    if (!(te > s.t)) te = s.t + 1.2;
    // stop at ground contact
    let tauEnd = te - s.t;
    for (let tau = 0.05; tau <= tauEnd; tau += 0.05) { shotPos(s, tau, out); if (out[2] < 0) { tauEnd = tau; break; } }
    const e = shotPos(s, tauEnd, [0, 0, 0]);
    const tr = s.shooter && s.shooter.track_idx != null ? tracks[s.shooter.track_idx] : null;
    const al = (s.shooter && s.shooter.alliance) || (tr && tr.alliance) || "red";
    return { ...s, i, te: s.t + tauEnd, tauEnd, end: e, tr, al, color: tr ? tr.color : COLORS[al][0], unattr: !!s.unattributed, team: s.unattributed ? null : (s.team || (tr && tr.team)) };
  }).sort((a, b) => a.t - b.t);
  const sIdx = shots.map(s => s.t);

  const S = {  // state
    layers: Object.fromEntries(PL.layers.map(l => [l[0], plLS.get("layer." + l[0], !!l[3])])),
    team: "", alliance: "", outcome: "", trailS: plLS.get("trailS", 4), offset: plLS.get("offset", 0), speed: 1, fuel: null, fuelOn: 0.5,
  };
  const t0m = d.t_start - 3;

  $app.innerHTML = `<div class="an">
   <div class="an-main">
    <div class="an-stage" id="anStage">${hasVideo ? `<video id="anVid" playsinline muted preload="auto" src="${esc(d.video_local)}"></video>` : `<div class="an-field"></div>`}
      <canvas id="anCv"></canvas><div class="an-msg" id="anMsg"></div></div>
    <div class="an-scrub" id="anScrub"></div>
    <div class="an-ctl">
      <button id="bPlay" title="space">Play</button><button id="bBack" title="left arrow">&#9664; frame</button><button id="bFwd" title="right arrow">frame &#9654;</button>
      <button id="bM5">-5 s</button><button id="bP5">+5 s</button>
      <span class="an-speed" id="anSpeed">${[0.25, 0.5, 1, 2].map(s => `<button data-s="${s}" class="${s === 1 ? "on" : ""}">${s}&times;</button>`).join("")}</span>
      <span class="muted small" id="anTime"></span>
      <label class="small muted" title="Shift data relative to video, in frames ( [ and ] keys )">sync <input id="anOff" type="number" step="1" value="${Math.round(S.offset * PL.FPS)}" style="width:54px"> fr</label>
    </div>
    <div class="muted small">space play/pause, left/right frame, shift+arrows 5 s, [ ] nudge sync. ${hasVideo ? "" : "No local video for this match: layers are drawn on a plain field only."}</div>
   </div>
   <div class="an-side">
    <div class="card"><h2>Layers</h2><div class="an-lay" id="anLay"></div>
      <label class="small muted">trail <input id="anTrail" type="range" min="1" max="12" step="1" value="${S.trailS}" style="width:90px"> <span id="anTrailV"></span> s</label></div>
    <div class="card"><h2>Filters</h2><div class="cfg">
      <select id="fTeam"><option value="">all teams</option>${tracks.map(t => `<option value="${t.i}">${esc(t.label)} (${t.alliance[0]})</option>`).join("")}</select>
      <select id="fAl"><option value="">both alliances</option><option value="red">red</option><option value="blue">blue</option></select>
      <select id="fOut"><option value="">all outcomes</option><option>made</option><option>missed</option><option>pass</option></select></div></div>
    ${COR.length || ID ? `<div class="card"><h2>Identity <span class="muted small">(${COR.length} correction${COR.length === 1 ? "" : "s"})</span></h2><div class="an-list" id="anId" style="max-height:200px"></div></div>` : ""}
    <div class="card"><h2>Shots <span class="muted small" id="anShotN"></span></h2><div class="an-list" id="anList"></div></div>
   </div></div>`;
  const $ = id => document.getElementById(id), stage = $("anStage"), cv = $("anCv"), ctx = cv.getContext("2d"), vid = $("anVid");
  const msg = $("anMsg");
  const unhot = {};
  for (const al of ["red", "blue"]) { const h = document.createElement("div"); h.className = "an-unhot"; h.style.display = "none"; h.title = UNATTR_TIP; stage.appendChild(h); unhot[al] = h; }
  const idcard = document.createElement("div"); idcard.className = "an-idcard"; idcard.style.display = "none"; stage.appendChild(idcard);

  // ---- clock abstraction (video, or free-running when no video)
  const clock = { t: t0m, playing: false, last: 0, dur: d.t_start + 170 };
  const curT = () => vid ? vid.currentTime : clock.t;
  function seek(t) {
    t = Math.max(0, Math.min(clock.dur, t));
    if (vid) vid.currentTime = t; else { clock.t = t; draw(); }
  }
  const vt = () => curT() - S.offset;   // data time

  // ---- layer buttons
  const lay = $("anLay");
  lay.innerHTML = PL.layers.map(l => `<button data-l="${l[0]}" title="key ${l[2]}">${l[1]} <kbd>${l[2]}</kbd></button>`).join("");
  function syncLayerBtns() { lay.querySelectorAll("button").forEach(b => b.classList.toggle("on", !!S.layers[b.dataset.l])); }
  function toggleLayer(k) { S.layers[k] = !S.layers[k]; plLS.set("layer." + k, S.layers[k]); syncLayerBtns(); if (k === "identity") scrub.classList.toggle("noid", !S.layers.identity); if (k === "fuel") loadFuel(); draw(); }
  lay.onclick = e => { const b = e.target.closest("button"); if (b) toggleLayer(b.dataset.l); };
  syncLayerBtns();
  $("anTrailV").textContent = S.trailS;
  $("anTrail").oninput = e => { S.trailS = +e.target.value; $("anTrailV").textContent = S.trailS; plLS.set("trailS", S.trailS); draw(); };
  $("fTeam").onchange = e => { S.team = e.target.value; refreshList(); draw(); };
  $("fAl").onchange = e => { S.alliance = e.target.value; refreshList(); draw(); };
  $("fOut").onchange = e => { S.outcome = e.target.value; refreshList(); draw(); };
  const shotVisible = s => (!S.team || (s.tr && s.tr.i === +S.team)) && (!S.alliance || s.al === S.alliance) && (!S.outcome || s.outcome === S.outcome);
  const trackVisible = t => (!S.team || t.i === +S.team) && (!S.alliance || t.alliance === S.alliance);

  // ---- fuel
  async function loadFuel() {
    if (S.fuel !== null || !d.fuel_url) return;
    S.fuel = [];
    try {
      const f = await api(`${ev}/m/${d.fuel_url}`);
      S.fuel = (f.tracks || []).map(t => ({ t: t.t, uv: t.uv, s: t.s == null ? 1 : t.s, a: t.t[0], b: t.t[t.t.length - 1] }));
      draw();
    } catch (e) { S.fuel = []; msg.textContent = "fuel tracks unavailable"; }
  }
  if (S.layers.fuel) loadFuel();

  // ---- canvas sizing
  let CW = 0, CH = 0, K = 1, DPR = 1;
  function resize() {
    const r = stage.getBoundingClientRect(); DPR = window.devicePixelRatio || 1;
    CW = r.width; CH = r.height; cv.width = Math.round(CW * DPR); cv.height = Math.round(CH * DPR);
    cv.style.width = CW + "px"; cv.style.height = CH + "px"; K = CW / 1920; draw();
  }
  const ro = new ResizeObserver(resize); ro.observe(stage);

  // ---- data lookups
  function robotAt(tr, t) {   // metres, interpolated; obs of nearest earlier frame
    const f = (t - v0) * hz, i = Math.floor(f);
    if (i < 0 || i >= N - 1) { if (i < 0 || i > N) return null; }
    const i0 = Math.max(0, Math.min(N - 1, i)), i1 = Math.min(N - 1, i0 + 1), a = Math.max(0, Math.min(1, f - i0));
    const p = tr.xy[i0], q = tr.xy[i1];
    if (!p || !q || p[0] == null) return null;
    return [(p[0] + (q[0] - p[0]) * a) / 100, (p[1] + (q[1] - p[1]) * a) / 100, tr.obs ? tr.obs[i0] : 1];
  }
  const PJ = (x, y, z) => { const p = cam.project(x, y, z); return p[2] > 0.1 ? [p[0] * K, p[1] * K, p[2]] : null; };
  // projected polyline in field space; breaks where behind camera
  function poly(pts, close) {
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

  // ---- minimap helper
  function field2mini(x, y, m) { return [m.x + x / PL.LEN * m.w, m.y + (1 - y / PL.WID) * m.h]; }
  const OUTC = { made: "#51cf66", missed: "#ff6b6b", pass: "#ffd43b", unknown: "#adb5bd" };
  const MARK = { made: "✓", missed: "✗", pass: "→", unknown: "?" };
  const hubs = [[PL.HUB_X, PL.HUB_Y, "blue"], [PL.LEN - PL.HUB_X, PL.HUB_Y, "red"]];

  function periodAt(t) {
    let cur = null;
    for (const p of PER) if (t >= p.v0) cur = p;
    return cur;
  }
  function counts(t) {  // per track + alliance made/attempts split by auto|teleop, plus per-alliance per-period made
    const c = tracks.map(() => ({ am: 0, aa: 0, tm: 0, ta: 0 })), al = { red: { m: 0 }, blue: { m: 0 } }, per = { red: {}, blue: {} }, un = { red: { n: 0, m: 0, a: 0 }, blue: { n: 0, m: 0, a: 0 } };
    for (const s of shots) {
      if (s.t > t) break;
      if (s.outcome === "made") { al[s.al].m++; per[s.al][s.period] = (per[s.al][s.period] || 0) + 1; }
      if (s.unattr) { const u = un[s.al]; u.n++; if (s.outcome === "made") u.m++; if (s.outcome === "made" || s.outcome === "missed") u.a++; continue; }
      if (!s.tr) continue;
      const x = c[s.tr.i], a = s.period === "auto";
      if (a) { x.aa++; if (s.outcome === "made") x.am++; } else { x.ta++; if (s.outcome === "made") x.tm++; }
    }
    return { c, al, per, un };
  }

  // ---- drawing
  const tmp = [0, 0, 0];
  function draw() {
    if (!CW) return;
    const T = vt();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0); ctx.clearRect(0, 0, CW, CH);
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    const L = S.layers;
    if (!cam) { msg.textContent = "no camera calibration: video layers unavailable (minimap only)"; }
    else if (!vid) { /* plain-field mode handled via minimap below */ }
    if (cam && vid) {
      if (L.grid) drawGrid();
      if (L.history) drawHistory(T);
      if (L.fuel) drawFuel(T);
      if (L.trails) drawTrails(T);
      if (L.robots) drawRobots(T);
      if (L.arcs) drawArcs(T);
    }
    const cn = (L.score || L.tba) ? counts(T) : null;
    if (L.minimap || !vid) drawMini(T, !vid);
    for (const al in unhot) if (!L.score) unhot[al].style.display = "none";
    if (L.score) drawScore(cn);
    if (L.tba) drawTBA(T, cn);
    if (L.clock) drawClock(T);
    if (cam && vid && L.identity) drawEncounter(T);
    updIdCard(T);
    $("anTime").textContent = fmtT(curT()) + " / " + fmtT(clock.dur) + "  frame " + Math.round(curT() * PL.FPS) + (periodAt(T) ? "  " + periodAt(T).name : "");
    moveHead();
  }
  const fmtT = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;

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
  function drawRobots(T) {
    const h = PL.FOOT / 2, zt = 0.5;
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
      if (S.layers.labels && !(cf != null && cf <= 0)) {   // conf 0 = identity/position unknown: no label at all
        const q = PJ(p[0], p[1], zt + 0.15); if (!q) { ctx.globalAlpha = 1; continue; }
        const txt = cf == null ? tr.label : cf >= IDC.hi ? tr.label : cf >= IDC.mid ? tr.label + " ?" : "unknown";
        ctx.font = (unk ? "italic 12px" : "bold 13px") + " system-ui,sans-serif"; const w = ctx.measureText(txt).width + 8;
        ctx.fillStyle = "rgba(0,0,0,.65)"; ctx.fillRect(q[0] - w / 2, q[1] - 17, w, 17);
        if (cf != null && cf >= IDC.mid && cf < IDC.hi) { ctx.strokeStyle = "#ffd43b"; ctx.lineWidth = 1; ctx.setLineDash([3, 2]); ctx.strokeRect(q[0] - w / 2, q[1] - 17, w, 17); ctx.setLineDash([]); }
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
      for (let a = 0; a < m - 1; a += 3) {   // fading segments
        ctx.globalAlpha = 0.15 + 0.7 * a / m; ctx.strokeStyle = tr.color; poly(pts.slice(a, Math.min(m, a + 4))); ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }
  function firstShot(T0) { let lo = 0, hi = shots.length; while (lo < hi) { const m = (lo + hi) >> 1; if (sIdx[m] < T0) lo = m + 1; else hi = m; } return lo; }
  function drawArcs(T) {
    const from = firstShot(T - 8);
    for (let k = from; k < shots.length && shots[k].t <= T; k++) {
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
  function drawMini(T, big) {
    const w = big ? CW - 20 : Math.min(CW * 0.3, 340), m = { w, h: w * PL.WID / PL.LEN, x: 0, y: 0 };
    m.x = big ? 10 : CW - w - 10; m.y = big ? 10 : CH - m.h - 56;
    ctx.fillStyle = "rgba(10,14,20,.78)"; ctx.fillRect(m.x, m.y, m.w, m.h); ctx.strokeStyle = "#8896a8"; ctx.lineWidth = 1; ctx.strokeRect(m.x, m.y, m.w, m.h);
    ctx.beginPath(); ctx.moveTo(m.x + m.w / 2, m.y); ctx.lineTo(m.x + m.w / 2, m.y + m.h); ctx.stroke();
    const sc = m.w / PL.LEN;
    for (const h of hubs) { const q = field2mini(h[0], h[1], m); ctx.strokeStyle = h[2] === "red" ? "#ff5a5f" : "#3e8bff"; ctx.beginPath(); ctx.arc(q[0], q[1], PL.HUB_R * sc, 0, 6.3); ctx.stroke(); }
    for (let k = firstShot(T - 3); k < shots.length && shots[k].t <= T; k++) {
      const s = shots[k]; if (!shotVisible(s)) continue;
      const done = T >= s.te, tau = Math.min(T - s.t, s.tauEnd), p = shotPos(s, tau, [0, 0, 0]), q = field2mini(p[0], p[1], m);
      if (!done) { ctx.fillStyle = s.color; ctx.beginPath(); ctx.arc(q[0], q[1], Math.max(2.5, sc * 0.1), 0, 6.3); ctx.fill(); }
      else { ctx.globalAlpha = Math.max(0, 1 - (T - s.te) / 2); ctx.fillStyle = OUTC[s.outcome]; ctx.beginPath(); ctx.arc(q[0], q[1], 3, 0, 6.3); ctx.fill(); ctx.globalAlpha = 1; }
    }
    for (const tr of tracks) {
      if (!trackVisible(tr)) continue;
      const p = robotAt(tr, T); if (!p) continue;
      const q = field2mini(p[0], p[1], m), r = PL.FOOT * sc;
      const cf = confAt(tr, T), unk = cf != null && cf < IDC.mid;
      ctx.globalAlpha = cf != null && cf <= 0 ? 0.2 : 1;
      ctx.fillStyle = (unk ? "#a0aab9" : tr.color) + (p[2] && !unk ? "cc" : "55"); ctx.strokeStyle = unk ? "#a0aab9" : tr.color; ctx.fillRect(q[0] - r / 2, q[1] - r / 2, r, r); ctx.strokeRect(q[0] - r / 2, q[1] - r / 2, r, r);
      if (!(cf != null && cf <= 0)) { ctx.fillStyle = "#fff"; ctx.font = "9px system-ui"; ctx.textAlign = "center"; ctx.fillText(unk ? "?" : tr.label + (cf != null && cf < IDC.hi ? "?" : ""), q[0], q[1] + 3); ctx.textAlign = "left"; }
      ctx.globalAlpha = 1;
    }
  }
  function drawScore(cn) {
    ctx.font = "11px ui-monospace,Menlo,monospace";
    for (const al of ["red", "blue"]) {
      const rows = tracks.filter(t => t.alliance === al), x = al === "red" ? 8 : CW - 198, y = 130 * K + 6, hasU = shots.some(s => s.unattr);
      ctx.fillStyle = "rgba(10,14,20,.78)"; ctx.fillRect(x, y, 190, 18 + (rows.length + (hasU ? 1 : 0)) * 14);
      ctx.fillStyle = COLORS[al][0]; ctx.fillText(`${al.toUpperCase()} made ${cn.al[al].m}  auto|tele`, x + 5, y + 12);
      rows.forEach((t, i) => {
        const c = cn.c[t.i]; ctx.fillStyle = t.color;
        ctx.fillText(`${t.label.padEnd(5)} ${c.am}/${c.aa} | ${c.tm}/${c.ta}`, x + 5, y + 26 + i * 14);
      });
      if (hasU) {
        const u = cn.un[al], yy = y + 26 + rows.length * 14; ctx.fillStyle = "rgba(160,170,185,.95)"; ctx.font = "italic 11px ui-monospace,Menlo,monospace";
        ctx.fillText(`unattrib. ${u.n} shots ${u.m}/${u.a}`, x + 5, yy); ctx.font = "11px ui-monospace,Menlo,monospace";
        const h = unhot[al]; h.style.display = ""; h.style.left = x + "px"; h.style.top = yy - 11 + "px";
      }
    }
  }
  function drawTBA(T, cn) {
    const p = periodAt(T); if (!p || !d.reconcile) return;
    const x = 8, y = CH - 150, w = 190;
    ctx.fillStyle = "rgba(10,14,20,.8)"; ctx.fillRect(x, y, w, 76); ctx.font = "11px system-ui"; ctx.fillStyle = "#cfd6e0"; ctx.fillText(`traced vs TBA, ${p.name} (made)`, x + 6, y + 13);
    ["red", "blue"].forEach((al, i) => {
      const r = d.reconcile[al] && d.reconcile[al][p.name]; if (!r) return;
      const mx = Math.max(r.tba, r.traced_made, cn.per[al][p.name] || 0, 1), by = y + 22 + i * 26, bw = w - 70;
      ctx.fillStyle = "#273142"; ctx.fillRect(x + 62, by, bw, 8); ctx.fillStyle = COLORS[al][0] + "55"; ctx.fillRect(x + 62, by, bw * r.traced_made / mx, 8);
      ctx.fillStyle = COLORS[al][0]; ctx.fillRect(x + 62, by, bw * (cn.per[al][p.name] || 0) / mx, 8);
      ctx.fillStyle = "#fff"; ctx.fillRect(x + 62 + bw * r.tba / mx - 1, by - 2, 2, 12);
      ctx.fillStyle = COLORS[al][0]; ctx.fillText(`${al} ${cn.per[al][p.name] || 0}/${r.traced_made}`, x + 6, by + 8);
      ctx.fillStyle = "#cfd6e0"; ctx.fillText(`TBA ${r.tba}`, x + 62, by + 20);
    });
  }
  function drawClock(T) {
    const p = periodAt(T); if (!p) return;
    const mt = T < d.t_start ? 0 : (T - d.t_start < 20.5 && T < v0 + ts0 ? T - d.t_start : 20 + Math.max(0, T - (v0 + ts0)));
    const txt = `${p.name}   match ${fmtT(Math.min(160, mt))}`, w = 220, x = (CW - w) / 2, y = CH - 28;
    ctx.fillStyle = "rgba(10,14,20,.78)"; ctx.fillRect(x, y, w, 22); ctx.font = "12px system-ui"; ctx.textAlign = "center"; ctx.fillStyle = "#fff"; ctx.fillText(txt, CW / 2, y + 15);
    ctx.fillStyle = p.active.red ? COLORS.red[0] : "#444"; ctx.fillRect(x, y + 20, w / 2, 2); ctx.fillStyle = p.active.blue ? COLORS.blue[0] : "#444"; ctx.fillRect(x + w / 2, y + 20, w / 2, 2);
    ctx.textAlign = "left";
  }

  // ---- identity: corrections (card, encounter ring, side list)
  const mmss = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const vtOf = f => v0 + f / hz;                                  // video time of replay frame
  let corrAll = false;   // "show all" toggle: un-anchored relinks with conf < 0.2 are hidden by default
  const corrWeak = c => c.kind === "relink" && (c.conf || 0) < 0.2;
  const corrVis = () => corrAll ? COR : COR.filter(c => !corrWeak(c));
  const corrClose = c => c.encounter && c.encounter.dist_m != null && c.encounter.dist_m < 1.5;
  const corrNear = T => { let best = null, bd = 3.0001; for (const c of corrVis()) { const dd = Math.abs(c.t_video - T); if (dd < bd) { bd = dd; best = c; } } return best; };
  function bestEvidence(c) {   // strongest read, ties -> nearest to the correction
    return (c.evidence || []).slice().sort((a, b) => (b[3] - a[3]) || (Math.abs(a[0] - c.frame) - Math.abs(b[0] - c.frame)))[0] || null;
  }
  const lab = tm => num(tm);
  function corrText(c) {
    const [a, b] = (c.teams || []).map(lab), when = mmss(c.t_video);
    const head = c.kind === "relink" ? `Re-linked: ${a} / ${b} tracks (OCR overrode motion) at ${when}`
      : corrClose(c) ? `Swap fixed: ${a} &harr; ${b} at ${when}` : `Identity cut: ${a} / ${b} at ${when}`;
    const ev = (c.evidence || []).slice().sort((x, y) => (y[3] - x[3]) || (Math.abs(x[0] - c.frame) - Math.abs(y[0] - c.frame))).slice(0, 3);
    return { head, ev: ev.map(e => `read ${lab(e[1])} at ${mmss(vtOf(e[0]))}${e[3] != null && e[3] < 1 ? ` (weight ${e[3]})` : ""}`).join("; "),
      tail: `${c.anchored ? "anchored by a read" : "not anchored by a read (lower trust)"}, confidence ${Math.round(c.conf * 100)}%${c.unresolved ? `, unresolved ${mmss(vtOf(c.unresolved[0]))}-${mmss(vtOf(c.unresolved[1]))}` : ""}` };
  }
  const seekC = (c, lead) => seek(c.t_video - lead + S.offset);
  let cardFor = null;
  function updIdCard(T) {
    const c = S.layers.identity ? corrNear(T) : null;
    if (!c) { idcard.style.display = "none"; cardFor = null; return; }
    idcard.style.display = ""; idcard.className = "an-idcard" + (c.anchored ? "" : " un");
    if (cardFor !== c.i) {
      cardFor = c.i; const x = corrText(c), e = bestEvidence(c);
      idcard.innerHTML = `<b>${x.head}</b><div class="small" style="color:#cfd6e0">${x.ev ? "evidence: " + x.ev + ". " : ""}${x.tail}.${c.encounter && c.encounter.dist_m != null && (c.kind !== "relink" || c.encounter.dist_m < 3) ? ` Robots ${fx(c.encounter.dist_m, 1)} m apart.` : ""}${c.kind === "relink" && c.motion_link ? ` Motion alone would have linked them (${c.motion_link.frames} frames, ${fx(c.motion_link.jump_m, 1)} m jump).` : ""}</div>
        <button data-a="enc">Go to encounter</button>${e ? `<button data-a="ev">Go to evidence read</button>` : ""}`;
      idcard.onclick = ev2 => { const b = ev2.target.closest("button"); if (!b) return; ev2.stopPropagation(); if (vid) vid.pause();
        if (b.dataset.a === "enc") seekC(c, 0); else { const e2 = bestEvidence(c); if (e2) seek(vtOf(e2[0]) + S.offset); } };
    }
  }
  idcard.addEventListener("pointerdown", e => e.stopPropagation());
  idcard.style.pointerEvents = "auto";
  function drawEncounter(T) {
    const c = corrNear(T); if (!c || !c.encounter || !c.encounter.xy) return;
    const dd = Math.abs(c.t_video - T); if (dd > 1.5) return;
    const q = PJ(c.encounter.xy[0], c.encounter.xy[1], 0.02); if (!q) return;
    ctx.strokeStyle = c.anchored ? "#ffd43b" : "#8896a8"; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.ellipse(q[0], q[1], 70 * K, 38 * K, 0, 0, 6.3); ctx.stroke(); ctx.setLineDash([]);
  }
  function buildIdSide() {
    const box = $("anId"); if (!box) return;
    const reads = ID && ID.per_team_reads;
    const vis = corrVis(), hid = COR.length - vis.length;
    box.innerHTML = `${vis.length ? vis.map(c => { const x = corrText(c); return `<div class="an-idrow ${c.anchored ? "" : "un"}" data-i="${c.i}" title="${esc(x.ev + ". " + x.tail)}"><b>${mmss(c.t_video)}</b> ${c.kind === "relink" ? "re-linked" : corrClose(c) ? "swap fixed" : "cut"} ${c.teams.map(lab).join(" / ")}
        <span class="muted small">${c.anchored ? "anchored" : "unanchored"} ${Math.round(c.conf * 100)}%</span></div>`; }).join("") : '<div class="muted small">No identity corrections in this match.</div>'}
      ${hid || corrAll && COR.some(corrWeak) ? `<div class="an-reads"><a href="#" id="anIdAll">${corrAll ? "hide low-confidence relinks" : `show all (${hid} hidden)`}</a></div>` : ""}
      ${reads ? `<div class="an-reads" title="bumper-number OCR reads per team">reads (${ID.n_reads ?? "?"}): ${tracks.map(t => t.team ? `${num(t.team)}&times;${reads[t.team] || 0}` : "").filter(Boolean).join(" &middot; ")}</div>` : ""}
      <div class="an-reads">labels: solid &ge;80% &middot; "?" 50-80% &middot; grey "unknown" &lt;50% &middot; hidden when unknown</div>`;
    box.onclick = e => { if (e.target.id === "anIdAll") { e.preventDefault(); corrAll = !corrAll; cardFor = null; buildIdSide(); buildScrub(); return; }
      const r = e.target.closest(".an-idrow"); if (!r) return; seekC(COR.find(c => c.i === +r.dataset.i), 2); };
  }

  // ---- scrubber: period timeline + shot ticks
  const scrub = $("anScrub");
  function buildScrub() {
    const D = clock.dur;
    scrub.innerHTML = PER.map(p => `<div class="seg" style="left:${p.v0 / D * 100}%;width:${(p.v1 - p.v0) / D * 100}%" title="${p.name}"><span>${p.name}</span>
      <i class="r ${p.active.red ? "on" : ""}"></i><i class="b ${p.active.blue ? "on" : ""}"></i></div>`).join("") +
      shots.map(s => `<u class="${s.al[0]} ${s.outcome}" style="left:${s.t / D * 100}%"></u>`).join("") +
      corrVis().map(c => `<b class="idm ${c.anchored ? "" : "un"}" data-i="${c.i}" style="left:${c.t_video / D * 100}%" title="${esc(corrText(c).head.replace("&harr;", "<->"))}"></b>`).join("") + `<div class="head" id="anHead"></div>`;
    scrub.classList.toggle("noid", !S.layers.identity);
  }
  function moveHead() { const h = $("anHead"); if (h) h.style.left = curT() / clock.dur * 100 + "%"; }
  function scrubTo(e) { const r = scrub.getBoundingClientRect(); seek((e.clientX - r.left) / r.width * clock.dur); }
  let drag = false;
  scrub.onpointerdown = e => { drag = true; scrub.setPointerCapture(e.pointerId); scrubTo(e); };
  scrub.onpointermove = e => { if (drag) scrubTo(e); };
  scrub.onpointerup = () => { drag = false; };

  // ---- shot list
  function refreshList() {
    const rows = shots.filter(shotVisible);
    $("anShotN").textContent = `(${rows.length})`;
    $("anList").innerHTML = rows.map(s => `<div class="an-row" data-i="${s.i}"><span class="sw" style="background:${s.color}"></span>
      <b>${fmtT(s.t)}</b> <span style="color:${OUTC[s.outcome]}">${MARK[s.outcome]} ${s.outcome}</span> ${s.unattr ? `<i class="muted" title="${esc(UNATTR_TIP)}">unattributed</i>` : s.team ? num(s.team) : s.shooter && s.shooter.kind}
      <span class="muted small">${s.period}${s.conf != null ? " " + Math.round(s.conf * 100) + "%" : ""}</span></div>`).join("");
  }
  $("anList").onclick = e => { const r = e.target.closest(".an-row"); if (!r) return; const s = shots.find(x => x.i === +r.dataset.i); seek(s.t - 1 + S.offset); };

  // ---- transport
  const step = n => { if (vid) vid.pause(); seek((Math.round(curT() * PL.FPS) + n) / PL.FPS + 0.002); };
  const togglePlay = () => { if (vid) { vid.paused ? vid.play() : vid.pause(); } else { clock.playing = !clock.playing; clock.last = performance.now(); loop(); } updPlay(); };
  const updPlay = () => { $("bPlay").textContent = (vid ? !vid.paused : clock.playing) ? "Pause" : "Play"; };
  $("bPlay").onclick = togglePlay; $("bBack").onclick = () => step(-1); $("bFwd").onclick = () => step(1);
  $("bM5").onclick = () => seek(curT() - 5); $("bP5").onclick = () => seek(curT() + 5);
  $("anSpeed").onclick = e => {
    const b = e.target.closest("button"); if (!b) return; S.speed = +b.dataset.s; if (vid) vid.playbackRate = S.speed;
    $("anSpeed").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  };
  const setOff = fr => { S.offset = fr / PL.FPS; plLS.set("offset", S.offset); $("anOff").value = fr; draw(); };
  $("anOff").onchange = e => setOff(+e.target.value || 0);
  function onKey(e) {
    if (/INPUT|SELECT|TEXTAREA/.test(e.target.tagName) && e.target.type !== "range") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key;
    if (k === " ") togglePlay();
    else if (k === "ArrowLeft") e.shiftKey ? seek(curT() - 5) : step(-1);
    else if (k === "ArrowRight") e.shiftKey ? seek(curT() + 5) : step(1);
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
    if (!vid) { if (clock.playing) { const n = performance.now(); clock.t += (n - clock.last) / 1000 * S.speed; clock.last = n; draw(); raf = requestAnimationFrame(loop); } return; }
  }
  if (vid) {
    const onFrame = (now, meta) => { if (dead) return; draw(); rv = vid.requestVideoFrameCallback(onFrame); };
    const hasRVFC = "requestVideoFrameCallback" in HTMLVideoElement.prototype;
    if (hasRVFC) rv = vid.requestVideoFrameCallback(onFrame);
    else { const tick = () => { if (dead) return; if (!vid.paused) draw(); raf = requestAnimationFrame(tick); }; raf = requestAnimationFrame(tick); }
    for (const ev2 of ["seeked", "timeupdate", "loadeddata", "pause", "play"]) vid.addEventListener(ev2, () => { draw(); updPlay(); });
    vid.addEventListener("loadedmetadata", () => { clock.dur = vid.duration; buildScrub(); resize(); if (!vid.currentTime) vid.currentTime = Math.max(0, t0m); });
    vid.addEventListener("error", () => { msg.textContent = "video failed to load: " + d.video_local; });
    vid.addEventListener("click", togglePlay);
    stage.style.aspectRatio = "16 / 9";
  } else { stage.style.aspectRatio = "2.05 / 1"; clock.t = t0m; }
  buildScrub(); buildIdSide(); refreshList(); resize(); updPlay();
  window.__an = { S, seek, draw, vid, shots, tracks, cam, step, toggleLayer };
  cleanup.push(() => { dead = true; document.removeEventListener("keydown", onKey); ro.disconnect(); cancelAnimationFrame(raf); if (vid) { vid.pause(); vid.removeAttribute("src"); vid.load(); } delete window.__an; });
}
