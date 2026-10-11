"use strict";
/* ============================================================================================
 * DATA CONTRACT (JSON under api/<ev>/...; cm for xy in t/*.json and m/*.json tracks, metres for shots/intakes in m/*.json)
 * Everything v2 is optional: missing fields simply hide the corresponding widget.
 *
 * m/<match>.json  (v1 fields unchanged, plus)
 *   shots[]    {t (video s), match_t, period, shooter:{kind,alliance,track_idx}, origin:[x,y,z], v0:[vx,vy,vz],
 *               landing:[x,y], target, outcome:"made"|"missed"|"pass"|"unknown", hub_active:bool|null, conf, team}
 *              period in auto|transition|shift1..shift4|endgame. Field frame in metres, absolute (red end at x=16.541).
 *   intakes[]  {t, match_t, track_idx, xy:[x,y], conf}
 *   analytics[track_idx] = {                      // one per tracks[] entry, same order
 *     scoring:{attempts,made,missed,passes,accuracy,points,uncounted,by_period:{<period>:{a,m}},fire_rate,volley_size,volleys,
 *              cycle_s,dist_mean,on_move_rate,first_shot_auto,first_shot_teleop},
 *     collection:{intakes,per_min,by_loc:{neutral,depot,outpost,opp}},
 *     movement:{distance_m,mean_speed,p95_speed,max_accel,pct_moving,zone:{own,neutral,opp},lanes:{bump,trench,open},near_side_pct,trench_capable},
 *     auto:{routine,start_xy,end_xy,distance_m,neutral_cross,shots,made,climb},
 *     defense:{time_s,contacts,pins,effect}, defended:{time_s,rate_retained,escape_s},
 *     endgame:{level:0-3,attempted,arrive_t,duration_s,stop_scoring_t}, reliability:{dead_s,dead,stalls,low_coverage},
 *     congestion, role:"scorer"|"feeder"|"collector"|"defender"|"hybrid", coverage:{feed_ok,observed} }
 *   periods[]  optional {name,t0,t1,active:{red,blue}}  match_t seconds (t0 = auto start); default 0/20/30/55/80/105/130/160,
 *              shifts 1,3 red-active, 2,4 blue-active. Replay-frame time of match_t: <20 -> t+0.5, else t_teleop_start+(t-20).
 *
 * t/<team>.json  (v1 fields unchanged, plus)
 *   analytics  aggregate: same tree as a match analytics but numeric leaves are {v,n,conf} (mean, matches, 0..1 confidence), plus
 *              scoring.made_total/attempts_total/accuracy_pooled, movement.lanes_total{bump,trench,open}, movement.trench_capable,
 *              endgame:{levels:{"0":n,..},climb_rate,attempt_rate,arrive_t,duration_s,stop_scoring_t,n},
 *              reliability:{dead_matches,n,mean_dead_s,low_coverage_matches}, role:{v,scores:{role:share},n,conf}, n
 *   matches[].analytics   that match's analytics (so the team page needs no extra fetches)
 *   shots_map[]  {xy:[x_cm,y_cm] launch point, land:[x,y], o:"made"|"missed"|"pass", p:period, m:"Q12"}  team frame (own end on the
 *                left like autos/heat: blue matches are point-reflected)
 *   intakes_map[] [[x_cm,y_cm],...] same frame
 *   autos[].routine  routine id;  routines[] {id,name,n,of,freq,matches[short],xy (representative, cm),start_xy,end_xy,shots_avg,
 *                made_avg,neutral_cross_rate,climb_rate,consistency}
 *
 * event.json  teams[].q (recommended, avoids fetching every team file for the table; otherwise derived from t/*.json analytics):
 *   {n,acc,made,att,fire,volley,cycle,intakes,climb,defs,pins,automade,dead (share of matches),role,speed,dist,conf}
 * ============================================================================================ */
// autoscout-live front end: hash-routed, reads the JSON the worker writes under api/.
const $app = document.getElementById("app");
const $crumbs = document.getElementById("crumbs");
const $updated = document.getElementById("updated");
const LEN = 16.541, WID = 8.069;
const FIELD = new Image(); FIELD.src = "field.jpg";
const COLORS = { red: ["#ff5a5f", "#ffa94d", "#ff7ad9"], blue: ["#3e8bff", "#2dd4ee", "#a78bfa"] };
const PALETTE = ["#ff5a5f", "#3e8bff", "#ffd43b", "#51cf66", "#cc5de8", "#ff922b", "#22b8cf", "#f06595",
                 "#94d82d", "#845ef7", "#fab005", "#20c997"];
let cleanup = [];

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = k => (k || "").replace("frc", "");
const pct = v => v == null ? "–" : Math.round(v * 100) + "%";
const fx = (v, d = 1) => v == null ? "–" : Number(v).toFixed(d);
async function api(p) {
  const r = await fetch("api/" + p, { cache: "no-cache" });
  if (!r.ok) throw new Error(p + ": " + r.status);
  return r.json();
}
function confBar(c, what = "team label confidence") {
  if (c == null) return "";
  const col = c > 0.7 ? "var(--good)" : c > 0.35 ? "var(--warn)" : "var(--bad)";
  return `<span class="conf" title="${what} ${Math.round(c * 100)}%"><i style="width:${Math.round(c * 100)}%;background:${col}"></i></span>`;
}
function crumbs(parts) {
  $crumbs.innerHTML = parts.map(([t, h]) => h ? `<a href="${h}">${esc(t)}</a>` : `<span>${esc(t)}</span>`).join(" / ");
}
function ago(ts) {
  if (!ts) return "";
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 90) return "updated just now";
  if (s < 5400) return `updated ${Math.round(s / 60)} min ago`;
  return `updated ${new Date(ts * 1000).toLocaleString()}`;
}

// ------------------------------------------------------------------ field canvas
function fieldCanvas(el, opts = {}) {
  const c = document.createElement("canvas"); c.className = "field";
  el.appendChild(c);
  const ctx = c.getContext("2d");
  function size() {
    const w = c.clientWidth || el.clientWidth || 800, dpr = window.devicePixelRatio || 1;
    c.width = Math.round(w * dpr); c.height = Math.round(w * WID / LEN * dpr);
    c.style.height = (w * WID / LEN) + "px";
  }
  size();
  const P = (x, y) => [(LEN - x) / LEN * c.width, y / WID * c.height];   // broadcast view: red left
  const S = m => m / LEN * c.width;
  function bg(alpha = 1) {
    ctx.globalAlpha = alpha;
    if (FIELD.complete && FIELD.naturalWidth) ctx.drawImage(FIELD, 0, 0, c.width, c.height);
    else { ctx.fillStyle = "#3a3f46"; ctx.fillRect(0, 0, c.width, c.height); }
    ctx.globalAlpha = 1;
  }
  return { c, ctx, P, S, bg, size };
}

// ------------------------------------------------------------------ pages
async function home() {
  crumbs([]); $updated.textContent = "";
  let evs = [];
  try { evs = await api("events.json"); } catch (e) { }
  if (evs.length === 1) { location.replace("#/" + evs[0].key); return; }
  $app.innerHTML = `<h1>Events</h1><div class="sub">Automatically tracked FRC events</div>
    <div class="evcards">${evs.map(e => `<a class="card" href="#/${e.key}"><h2>${esc(e.name)}</h2>
      <div class="muted small">${esc(e.start || "")} · ${e.n_done}/${e.n_total} matches tracked</div>
      <div class="progress"><div style="width:${e.n_total ? 100 * e.n_done / e.n_total : 0}%"></div></div></a>`).join("")
      || `<div class="empty">No events yet. The worker publishes here as soon as it has synced one.</div>`}</div>`;
}

async function eventPage(ev, tab, arg) {
  const d = await api(`${ev}/event.json`);
  crumbs([[d.name, `#/${ev}`]]); $updated.textContent = ago(d.updated);
  tab = tab || sessionStorageGet("tab:" + ev) || "teams";
  $app.innerHTML = `<h1>${esc(d.name)}</h1>
    <div class="sub">${esc([d.city, d.state_prov].filter(Boolean).join(", "))} · ${esc(d.start || "")}${d.end && d.end !== d.start ? " – " + esc(d.end) : ""}
      · ${d.n_done}/${d.n_total} matches tracked${d.calibrated ? "" : ' · <span class="pill failed">camera not calibrated</span>'}
      <div class="progress"><div style="width:${d.n_total ? 100 * d.n_done / d.n_total : 0}%"></div></div></div>
    <div class="tabs"><div class="tab" data-t="teams">Teams</div><div class="tab" data-t="compare">Compare</div><div class="tab" data-t="picklist">Picklist</div><div class="tab" data-t="matches">Matches</div></div>
    <div id="tabbody" class="card"></div>`;
  const body = document.getElementById("tabbody");
  const show = t => {
    sessionStorageSet("tab:" + ev, t);
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x.dataset.t === t));
    if (t === "teams") teamsTable(body, d, ev); else if (t === "compare") comparePane(body, d, ev, arg); else if (t === "picklist") picklistPane(body, d, ev); else matchesTable(body, d, ev);
  };
  document.querySelectorAll(".tab").forEach(x => x.onclick = () => show(x.dataset.t));
  show(tab);
  const iv = setInterval(async () => {
    try { const n = await api(`${ev}/event.json`); if (n.updated !== d.updated) route(false); } catch (e) { }
  }, 60000);
  cleanup.push(() => clearInterval(iv));
}
function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { } }

const STATE_LABEL = { done: "tracked", pending: "waiting", no_video: "no video yet", processing: "processing", error: "retrying", failed: "failed" };
function statusPill(m) {
  if (m.state === "done" && m.feed_ok != null && m.feed_ok < 0.9)
    return `<span class="pill no_video" title="the field camera was missing from the broadcast for part of this match">partial footage (${Math.round(m.feed_ok * 100)}%)</span>`;
  return `<span class="pill ${m.state}">${STATE_LABEL[m.state] || m.state}</span>`;
}
function matchesTable(el, d, ev) {
  const tl = (ts, al) => ts.map(t => `<a class="team ${al[0]}" href="#/${ev}/t/${t}">${num(t)}</a>`).join(" ");
  el.innerHTML = `<div class="tablewrap"><table><thead><tr><th>Match</th><th>Red</th><th class="num">Score</th><th>Blue</th><th>Tracking</th><th>Labels</th></tr></thead><tbody>
    ${d.matches.map(m => `<tr>
      <td>${m.state === "done" ? `<a href="#/${ev}/m/${m.key}">${esc(m.label)}</a>` : esc(m.label)}</td>
      <td>${tl(m.red.teams, "red")}</td>
      <td class="num"><span class="score r ${m.winner === "red" ? "win" : ""}">${m.red.score ?? "–"}</span>
        <span class="muted">–</span> <span class="score b ${m.winner === "blue" ? "win" : ""}">${m.blue.score ?? "–"}</span></td>
      <td>${tl(m.blue.teams, "blue")}</td>
      <td>${statusPill(m)}</td>
      <td>${confBar(m.id_conf)}</td></tr>`).join("")}</tbody></table></div>`;
}

// ------------------------------------------------------------------ match replay
let ytReady = null;
function loadYT() {
  if (ytReady) return ytReady;
  ytReady = new Promise(res => {
    window.onYouTubeIframeAPIReady = () => res(true);
    const s = document.createElement("script"); s.src = "https://www.youtube.com/iframe_api";
    s.onerror = () => res(false); document.head.appendChild(s);
    setTimeout(() => res(!!window.YT && !!window.YT.Player), 8000);
  });
  return ytReady;
}

async function matchPage(ev, key) {
  const [d, e] = await Promise.all([api(`${ev}/m/${key}.json`), api(`${ev}/event.json`).catch(() => null)]);
  crumbs([[e ? e.name : ev, `#/${ev}`], [d.label]]); $updated.textContent = "";
  const N = d.tracks.length ? d.tracks[0].xy.length : 0, hz = d.hz, T = N / hz;
  const v0 = d.t_start - 0.5;     // video time of replay frame 0
  const tracks = d.tracks.map(t => {
    const al = t.alliance, k = d.tracks.filter(x => x.alliance === al).indexOf(t);
    return { ...t, color: COLORS[al][k % 3], on: true, label: t.team ? num(t.team) : `${al[0].toUpperCase()}${k + 1}` };
  });
  // ---- v2: shots, periods
  const ts0 = d.t_teleop_start, ae0 = d.t_auto_end;
  const mt2ft = mt => mt < 20 ? mt + 0.5 : ts0 + (mt - 20);
  const PER = (d.periods && d.periods.length ? d.periods : [["auto", 0, 20], ["transition", 20, 30], ["shift1", 30, 55], ["shift2", 55, 80], ["shift3", 80, 105], ["shift4", 105, 130], ["endgame", 130, 160]]
    .map(([name, t0, t1]) => ({ name, t0, t1, active: { red: name === "shift1" || name === "shift3" || !/^shift/.test(name), blue: name === "shift2" || name === "shift4" || !/^shift/.test(name) } })))
    .map(p => ({ ...p, f0: mt2ft(p.t0), f1: p.t1 >= 160 ? Math.min(T, mt2ft(p.t1)) : mt2ft(p.t1) }));
  const SH = (d.shots || []).map(s => {
    const o = s.origin, l = s.landing, ft = s.t - v0, dist = Math.hypot(l[0] - o[0], l[1] - o[1]);
    const vh = s.v0 ? Math.hypot(s.v0[0], s.v0[1]) : 0, tf = Math.max(.35, Math.min(2.5, vh > .5 ? dist / vh : .3 + dist * .12));
    const ti = s.shooter && s.shooter.track_idx;
    return { ...s, ft, tf, tr: tracks[ti], ti };
  }).filter(s => s.tr).sort((a, b) => a.ft - b.ft);
  const hasShots = SH.length > 0, an = d.analytics || null;
  const $ = id => document.getElementById(id);

  $app.innerHTML = `<h1>${esc(d.label)}</h1>
    <div class="sub"><a class="btn" href="#/${ev}/a/${key}" style="font-weight:600">Analyze (video + layers)</a> <span class="score r ${d.winner === "red" ? "win" : ""}">${d.red.score}</span> – <span class="score b ${d.winner === "blue" ? "win" : ""}">${d.blue.score}</span>
     · video: <a href="https://youtu.be/${d.video.id}?t=${Math.floor(v0)}" target="_blank" rel="noopener">${esc(d.video.title)}</a>
     ${d.start_detected ? "" : ' · <span class="pill no_video" title="match start not detected from motion; using a default offset">start estimated</span>'}
     ${d.feed_ok < 0.98 ? ` · <span class="pill no_video" title="the field camera was missing from the broadcast for part of this match; stats use only the filmed part">camera footage ${Math.round(d.feed_ok * 100)}%</span>` : ""}</div>
    <div class="match-layout">
      <div class="stack"><div class="video"><div id="yt"></div></div>
        <div class="card"><h2>Robots</h2><div class="tablewrap"><table><thead><tr><th></th><th>Team</th><th class="num">Dist (m)</th><th class="num">Auto (m)</th><th class="num">Top (m/s)</th><th class="num">Own</th><th class="num">Neutral</th><th class="num">Opp.</th><th class="num">Seen</th><th>Label</th></tr></thead>
          <tbody>${tracks.map(t => `<tr><td><span class="sw" style="background:${t.color}"></span></td>
            <td>${t.team ? `<a class="team ${t.alliance[0]}" href="#/${ev}/t/${t.team}">${num(t.team)}</a>` : `<span class="muted">${t.label}</span>`}</td>
            <td class="num">${fx(t.stats.distance_m, 0)}</td><td class="num">${fx(t.stats.auto_distance_m)}</td><td class="num">${fx(t.stats.max_speed)}</td>
            <td class="num">${pct(t.stats.time_own_zone)}</td><td class="num">${pct(t.stats.time_neutral)}</td><td class="num">${pct(t.stats.time_opp_zone)}</td>
            <td class="num">${pct(t.stats.observed)}</td><td>${t.manual ? '<span class="pill">manual</span>' : confBar(t.id_conf)}</td></tr>`).join("")}</tbody></table></div>
          <div class="note">"Seen" is the share of the match the robot was directly detected; elsewhere its path is
          interpolated (drawn faded). Team labels are inferred across the event; low-confidence ones may be swapped
          within an alliance.</div></div>
        ${an ? `<div class="card"><h2>Robot analytics</h2><div class="tablewrap"><table><thead><tr><th></th><th>Team</th><th>Role</th><th class="num">Made/Att</th><th class="num">Acc</th><th class="num">Fire /s</th><th class="num">Volley</th><th class="num">Cycle s</th>
          <th class="num">Intakes</th><th class="num">Auto</th><th>Climb</th><th class="num">Def s</th><th class="num">Cont.</th><th class="num">Pins</th><th>Flags</th></tr></thead><tbody>
          ${tracks.map((t, i) => { const a = an[i]; return a ? `<tr><td><span class="sw" style="background:${t.color}"></span></td><td><span class="team ${t.alliance[0]}">${t.label}</span></td><td>${esc(a.role || "")}</td>
            <td class="num">${a.scoring.made}/${a.scoring.attempts}</td><td class="num">${pct(a.scoring.accuracy)}</td><td class="num">${fx(a.scoring.fire_rate)}</td><td class="num">${a.scoring.volley_size ?? "–"}</td><td class="num">${fx(a.scoring.cycle_s)}</td>
            <td class="num">${a.collection.intakes}</td><td class="num">${a.auto.made}/${a.auto.shots}${a.auto.routine ? ` <span class="muted">${esc(a.auto.routine)}</span>` : ""}</td>
            <td>${a.endgame.level ? "L" + a.endgame.level : a.endgame.attempted ? "failed" : "–"}</td><td class="num">${fx(a.defense.time_s)}</td><td class="num">${a.defense.contacts}</td><td class="num">${a.defense.pins}</td>
            <td>${a.reliability.dead ? '<span class="pill failed">dead</span>' : ""}</td></tr>` : ""; }).join("")}</tbody></table></div>
          <div class="note">Fuel stats are reconstructed from the broadcast video; counts can differ from the official hub counts. Accuracy = made / attempted (passes excluded).</div></div>` : ""}
      </div>
      <div><div class="card"><div class="fieldwrap" id="fw"></div>
        <div class="controls"><button id="play">▶ Play</button><span class="clock" id="clock">–</span><span class="phase" id="phase"></span>
          <input type="range" id="scrub" min="0" max="${Math.max(N - 1, 0)}" value="0" step="1"></div>
        ${hasShots ? `<div class="tl" id="tl"><div class="tlseg" id="tlseg"></div><div class="tlact"><i class="r"></i><i class="b"></i></div><div class="tlhead" id="tlhead"></div></div>` : ""}
        <div class="controls"><button id="trail" class="on">Trails</button>${hasShots ? '<button id="shots" class="on">Shots</button><button id="allshots">Shot history</button>' : ""}<button id="full">Full paths</button>
          <button id="speed">1×</button><button id="sync" class="on" title="follow the video">Sync video</button></div>
        ${hasShots ? `<div class="legendline"><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" stroke="#fff" stroke-width="2.4"/></svg>made
          <svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" stroke="#fff" stroke-width="2.4" stroke-dasharray="5 4"/></svg>missed
          <svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" stroke="#facc15" stroke-width="1.6" stroke-dasharray="1 4" stroke-linecap="round"/></svg>pass
          <span class="muted small">arc colour = shooter</span></div><div id="sb" class="sb"></div>` : ""}
        <div class="legend">${tracks.map((t, i) => `<label><input type="checkbox" data-i="${i}" checked>
          <span class="sw" style="background:${t.color}"></span><span class="team ${t.alliance[0]}">${t.label}</span>
          ${t.team ? confBar(t.id_conf) : ""}</label>`).join("")}</div></div></div>
    </div>`;
  const F = fieldCanvas(document.getElementById("fw"));
  let f = 0, playing = false, last = 0, trail = true, full = false, speed = 1, sync = true, player = null, alive = true;
  const scrub = document.getElementById("scrub");
  let showShots = true, histShots = false, sbKey = "";
  function drawShots(F, ft) {
    const { ctx, P, S, c } = F, lw = Math.max(2, c.width / 400);
    for (const s of SH) {
      if (s.ft > ft) break;
      const age = ft - (s.ft + s.tf), landed = age >= 0;
      if (landed && age > 2.5 && !histShots) continue;
      const [x0, y0] = P(s.origin[0], s.origin[1]), [x1, y1] = P(s.landing[0], s.landing[1]);
      const dx = x1 - x0, dy = y1 - y0, L = Math.hypot(dx, dy), cx = (x0 + x1) / 2 - dy * .12, cy = (y0 + y1) / 2 + dx * .12 - L * .12;
      const u = Math.min(1, (ft - s.ft) / s.tf), col = s.tr.color, pass = s.outcome === "pass", made = s.outcome === "made";
      const alpha = landed ? (histShots ? Math.max(.18, 1 - age / 2.5 * .8) : 1 - age / 2.5) : 1;
      ctx.globalAlpha = Math.max(0, alpha) * (s.outcome === "unknown" ? .5 : 1);
      ctx.strokeStyle = pass ? "#facc15" : col; ctx.lineWidth = pass ? lw * .8 : lw * (made ? 1.15 : 1);
      ctx.setLineDash(pass ? [1, c.width / 150] : made ? [] : [c.width / 120, c.width / 160]); ctx.lineCap = pass ? "round" : "butt";
      ctx.beginPath(); ctx.moveTo(x0, y0);
      const n = Math.max(2, Math.ceil(24 * u));
      for (let k = 1; k <= n; k++) { const w = u * k / n, a = (1 - w) * (1 - w), b = 2 * (1 - w) * w, e = w * w; ctx.lineTo(a * x0 + b * cx + e * x1, a * y0 + b * cy + e * y1); }
      ctx.stroke(); ctx.setLineDash([]);
      if (!landed) {         // ball in flight
        const w = u, a = (1 - w) * (1 - w), b = 2 * (1 - w) * w, e = w * w;
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(a * x0 + b * cx + e * x1, a * y0 + b * cy + e * y1, Math.max(2.5, c.width / 260), 0, 7); ctx.fill();
      } else {               // landing marker
        const r = Math.max(3, c.width / 200);
        ctx.lineWidth = lw; ctx.strokeStyle = pass ? "#facc15" : made ? col : "#fff";
        if (made) { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x1, y1, r, 0, 7); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke(); }
        else if (pass) { ctx.beginPath(); ctx.arc(x1, y1, r, 0, 7); ctx.stroke(); }
        else { ctx.beginPath(); ctx.moveTo(x1 - r, y1 - r); ctx.lineTo(x1 + r, y1 + r); ctx.moveTo(x1 + r, y1 - r); ctx.lineTo(x1 - r, y1 + r); ctx.stroke(); }
        if (s.hub_active === false && made) { ctx.globalAlpha *= .9; ctx.fillStyle = "#000"; ctx.font = `700 ${Math.max(9, c.width / 110)}px system-ui`; ctx.textAlign = "center"; ctx.fillText("0", x1, y1 - r * 1.8); }
      }
      ctx.globalAlpha = 1;
    }
  }
  function updateBoard(ft) {
    if (!hasShots) return;
    const cnt = tracks.map(() => ({ am: 0, aa: 0, tm: 0, ta: 0, p: 0 }));
    for (const s of SH) {
      if (s.ft > ft) break; const k = cnt[s.ti];
      if (s.outcome === "pass") { k.p++; continue; }
      const au = s.period === "auto"; if (s.outcome === "made") au ? k.am++ : k.tm++; if (s.outcome === "made" || s.outcome === "missed") au ? k.aa++ : k.ta++;
    }
    const key = JSON.stringify(cnt); if (key === sbKey) return; sbKey = key;
    const col = al => { const idx = tracks.map((t, i) => t.alliance === al ? i : -1).filter(i => i >= 0), sum = f => idx.reduce((a, i) => a + cnt[i][f], 0);
      return `<div class="sbcol ${al}"><div class="sbh"><b>${al.toUpperCase()}</b><span>fuel made <b>${sum("am") + sum("tm")}</b>/${sum("aa") + sum("ta")}</span></div>
        <table><thead><tr><th></th><th class="num">Auto</th><th class="num">Teleop</th><th class="num">Total</th><th class="num">Pass</th></tr></thead><tbody>
        ${idx.map(i => { const k = cnt[i], t = tracks[i]; return `<tr><td><span class="sw" style="background:${t.color}"></span> <span class="team ${al[0]}">${t.label}</span></td><td class="num">${k.am}/${k.aa}</td><td class="num">${k.tm}/${k.ta}</td><td class="num"><b>${k.am + k.tm}</b>/${k.aa + k.ta}</td><td class="num">${k.p || ""}</td></tr>`; }).join("")}
        </tbody></table></div>`; };
    $("sb").innerHTML = col("red") + col("blue");
  }
  function buildTimeline() {
    if (!hasShots) return;
    $("tlseg").innerHTML = PER.map(p => `<div class="seg" style="left:${100 * p.f0 / T}%;width:${100 * (p.f1 - p.f0) / T}%" title="${esc(p.name)} ${p.t0}–${p.t1}s"><span>${esc((PLABEL[p.name] || p.name))}</span></div>`).join("");
    $("tl").querySelector(".tlact").innerHTML = ["r", "b"].map(a => `<div class="row ${a}">${PER.map(p => `<i class="${p.active[a === "r" ? "red" : "blue"] ? "on" : ""}" style="left:${100 * p.f0 / T}%;width:${100 * (p.f1 - p.f0) / T}%"></i>`).join("")}</div>`).join("");
    $("tl").onclick = e => { const r = $("tl").getBoundingClientRect(); f = Math.max(0, Math.min(N - 1, (e.clientX - r.left) / r.width * N)); if (sync && ytState() != null) player.seekTo(v0 + f / hz, true); draw(); };
  }
  const draw = () => {
    const { ctx, P, S, bg, c } = F; bg(0.95);
    const fi = Math.max(0, Math.min(N - 1, Math.round(f))), ftNow = f / hz;
    if (hasShots) {
      const cur = PER.find(p => ftNow >= p.f0 && ftNow < p.f1);
      for (const al of ["red", "blue"]) {
        const [hx, hy] = P(HUBS[al][0], HUBS[al][1]), act = cur ? cur.active[al] : null;
        ctx.lineWidth = Math.max(2, c.width / 300); ctx.strokeStyle = act ? (al === "red" ? "#ff5a5f" : "#3e8bff") : "#8b98a8"; ctx.globalAlpha = act ? .95 : .45;
        ctx.setLineDash(act ? [] : [4, 5]); ctx.beginPath(); ctx.arc(hx, hy, S(.75), 0, 7); ctx.stroke(); ctx.setLineDash([]);
        if (act) { ctx.fillStyle = al === "red" ? "rgba(255,90,95,.18)" : "rgba(62,139,255,.18)"; ctx.fill(); }
        ctx.globalAlpha = 1;
      }
    }
    for (const t of tracks) {
      if (!t.on) continue;
      const a = full ? 0 : trail ? Math.max(0, fi - 3 * hz) : fi;
      ctx.lineWidth = Math.max(2, c.width / 450); ctx.lineCap = "round"; ctx.lineJoin = "round";
      for (let i = a + 1; i <= (full ? N - 1 : fi); i++) {
        const [x0, y0] = P(t.xy[i - 1][0] / 100, t.xy[i - 1][1] / 100), [x1, y1] = P(t.xy[i][0] / 100, t.xy[i][1] / 100);
        ctx.strokeStyle = t.color;
        ctx.globalAlpha = (full ? 0.55 : 0.15 + 0.75 * (i - a) / Math.max(1, fi - a)) * (t.obs[i] ? 1 : 0.4);
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      const [x, y] = P(t.xy[fi][0] / 100, t.xy[fi][1] / 100), s = S(0.91);
      ctx.fillStyle = t.color; ctx.globalAlpha = t.obs[fi] ? 0.95 : 0.45;
      ctx.fillRect(x - s / 2, y - s / 2, s, s);
      ctx.globalAlpha = 1; ctx.strokeStyle = "#0008"; ctx.lineWidth = Math.max(1, c.width / 800); ctx.strokeRect(x - s / 2, y - s / 2, s, s);
      ctx.font = `700 ${Math.max(11, c.width / 75)}px system-ui`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillStyle = "#000c"; ctx.fillText(t.label, x + 1, y - s * 0.95 + 1); ctx.fillStyle = "#fff"; ctx.fillText(t.label, x, y - s * 0.95);
    }
    if (hasShots && showShots) drawShots(F, ftNow);
    const gap = (d.feed_gaps || []).find(([a, b]) => fi / hz >= a && fi / hz < b);
    if (gap) {
      ctx.fillStyle = "rgba(13,17,23,.72)"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.fillStyle = "#e6edf3"; ctx.font = `600 ${Math.max(13, c.width / 40)}px system-ui`; ctx.textAlign = "center";
      ctx.fillText("No camera feed in the broadcast here", c.width / 2, c.height / 2);
      ctx.font = `${Math.max(11, c.width / 60)}px system-ui`; ctx.fillStyle = "#8b98a8";
      ctx.fillText("positions are not known for this stretch", c.width / 2, c.height / 2 + c.width / 30);
    }
    const tt = fi / hz, ae = d.t_auto_end, ts = d.t_teleop_start;
    let clk, ph;
    if (tt < 0.5) { clk = "0:20"; ph = "pre-match"; }
    else if (tt < ae) { clk = fmt(20 - (tt - 0.5)); ph = "AUTO"; }
    else if (tt < ts) { clk = "2:20"; ph = "transition"; }
    else { clk = fmt(Math.max(0, 140 - (tt - ts))); ph = "TELEOP"; }
    document.getElementById("clock").textContent = clk; document.getElementById("phase").textContent = ph;
    scrub.value = fi;
    if (hasShots) { updateBoard(ftNow); $("tlhead").style.left = (100 * Math.min(1, ftNow / T)) + "%"; }
  };
  const fmt = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const setPlaying = p => { playing = p; document.getElementById("play").textContent = p ? "❚❚ Pause" : "▶ Play"; };
  const loop = ts => {
    if (!alive) return;
    const st = ytState();
    if (sync && (st === 1 || st === 2 || st === 3)) {
      f = (player.getCurrentTime() - v0) * hz; setPlaying(st === 1);
    } else if (playing) {
      f += (ts - last) / 1000 * hz * speed;
      if (f >= N - 1) { f = N - 1; setPlaying(false); }
    }
    last = ts; draw(); requestAnimationFrame(loop);
  };
  requestAnimationFrame(t => { last = t; loop(t); });
  const ytState = () => { try { return player && player.getPlayerState ? player.getPlayerState() : null; } catch (e) { return null; } };
  const standalone = () => { if (f >= N - 1) f = 0; setPlaying(!playing); };
  document.getElementById("play").onclick = () => {
    const st = ytState();
    if (!sync || st == null) return standalone();
    if (st === 1) { player.pauseVideo(); return; }
    if (playing) { setPlaying(false); return; }
    player.seekTo(v0 + f / hz, true); player.playVideo();
    setTimeout(() => {           // video blocked / unavailable: run the replay on its own
      if (alive && ![1, 3].includes(ytState())) { sync = false; document.getElementById("sync").classList.remove("on"); setPlaying(true); }
    }, 2500);
  };
  scrub.oninput = () => { f = +scrub.value; if (sync && ytState() != null) player.seekTo(v0 + f / hz, true); draw(); };
  const tog = (id, fn) => { const b = document.getElementById(id); b.onclick = () => b.classList.toggle("on", fn()); };
  if (hasShots) { tog("shots", () => (showShots = !showShots)); tog("allshots", () => (histShots = !histShots)); buildTimeline(); }
  tog("trail", () => (trail = !trail)); tog("full", () => (full = !full));
  tog("sync", () => { sync = !sync; if (!sync && player && player.pauseVideo) player.pauseVideo(); return sync; });
  document.getElementById("speed").onclick = e => { speed = speed === 1 ? 2 : speed === 2 ? 4 : speed === 4 ? 0.5 : 1; e.target.textContent = speed + "×"; };
  document.querySelectorAll(".legend input").forEach(cb => cb.onchange = () => { tracks[+cb.dataset.i].on = cb.checked; });
  const onResize = () => F.size(); window.addEventListener("resize", onResize);
  cleanup.push(() => { alive = false; window.removeEventListener("resize", onResize); try { player && player.destroy(); } catch (e) { } });
  if (await loadYT() && alive) {
    player = new YT.Player("yt", { videoId: d.video.id, playerVars: { start: Math.floor(v0), rel: 0, modestbranding: 1, playsinline: 1 } });
  } else {
    document.getElementById("yt").innerHTML = `<div class="empty">Video unavailable here. <a href="https://youtu.be/${d.video.id}" target="_blank">Open on YouTube</a></div>`;
    sync = false; document.getElementById("sync").classList.remove("on");
  }
}

// ------------------------------------------------------------------ router
async function route(scroll = true) {
  cleanup.forEach(f => f()); cleanup = [];
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (!p.length) await home();
    else if (p.length === 1) await eventPage(p[0]);
    else if (p[1] === "m") await matchPage(p[0], p[2]);
    else if (p[1] === "a") await analyzePage(p[0], p[2]);
    else if (p[1] === "t") await teamPage(p[0], p[2]);
    else await eventPage(p[0], p[1], p[2]);
  } catch (e) {
    console.error(e);
    $app.innerHTML = `<div class="empty">Couldn't load this page (${esc(e.message)}). It may not be processed yet.</div>`;
  }
  if (scroll) window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => route());
FIELD.onload = () => { };
route();
