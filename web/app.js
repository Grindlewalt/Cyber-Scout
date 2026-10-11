"use strict";
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
function confBar(c) {
  if (c == null) return "";
  const col = c > 0.7 ? "var(--good)" : c > 0.35 ? "var(--warn)" : "var(--bad)";
  return `<span class="conf" title="team label confidence ${Math.round(c * 100)}%"><i style="width:${Math.round(c * 100)}%;background:${col}"></i></span>`;
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

async function eventPage(ev, tab) {
  const d = await api(`${ev}/event.json`);
  crumbs([[d.name, `#/${ev}`]]); $updated.textContent = ago(d.updated);
  tab = tab || sessionStorageGet("tab:" + ev) || "teams";
  $app.innerHTML = `<h1>${esc(d.name)}</h1>
    <div class="sub">${esc([d.city, d.state_prov].filter(Boolean).join(", "))} · ${esc(d.start || "")}${d.end && d.end !== d.start ? " – " + esc(d.end) : ""}
      · ${d.n_done}/${d.n_total} matches tracked${d.calibrated ? "" : ' · <span class="pill failed">camera not calibrated</span>'}
      <div class="progress"><div style="width:${d.n_total ? 100 * d.n_done / d.n_total : 0}%"></div></div></div>
    <div class="tabs"><div class="tab" data-t="teams">Teams</div><div class="tab" data-t="matches">Matches</div></div>
    <div id="tabbody" class="card"></div>`;
  const body = document.getElementById("tabbody");
  const show = t => {
    sessionStorageSet("tab:" + ev, t);
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x.dataset.t === t));
    if (t === "teams") teamsTable(body, d, ev); else matchesTable(body, d, ev);
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

function teamsTable(el, d, ev) {
  const cols = [
    ["rank", "Rank", r => r.rank ?? "–", true], ["number", "Team", r => `<a class="team" href="#/${ev}/t/${r.key}">${r.number}</a>`, false],
    ["nickname", "Name", r => esc(r.nickname), false], ["opr", "OPR", r => fx(r.opr), true],
    ["tracked", "Tracked", r => r.tracked, true], ["distance_m", "Dist / match (m)", r => fx(r.distance_m, 0), true],
    ["auto_distance_m", "Auto dist (m)", r => fx(r.auto_distance_m), true], ["max_speed", "Top speed (m/s)", r => fx(r.max_speed), true],
    ["time_own_zone", "Own zone", r => pct(r.time_own_zone), true], ["time_neutral", "Neutral", r => pct(r.time_neutral), true],
    ["time_opp_zone", "Opp. zone", r => pct(r.time_opp_zone), true], ["id_conf", "Label conf.", r => confBar(r.id_conf), true]];
  let sk = "rank", dir = 1;
  const render = () => {
    const rows = [...d.teams].sort((a, b) => {
      const x = a[sk], y = b[sk];
      if (x == null && y == null) return a.number - b.number;
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
    });
    el.innerHTML = `<div class="tablewrap"><table><thead><tr>${cols.map(([k, t, , n]) =>
      `<th data-k="${k}" class="${n ? "num" : ""}">${t}${sk === k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr>${cols.map(([, , f, n]) => `<td class="${n ? "num" : ""}">${f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="note">Per-match averages over the matches where the robot was tracked. "Own zone" is the alliance zone
      on the robot's own side of its hub; "Opp. zone" the other alliance's. Label confidence is how sure the team
      assignment is (robots are matched to teams by appearance and starting spot across the event).</div>`;
    el.querySelectorAll("th").forEach(th => th.onclick = () => {
      const k = th.dataset.k; if (sk === k) dir = -dir; else { sk = k; dir = ["rank", "number", "nickname"].includes(k) ? 1 : -1; }
      render();
    });
  };
  render();
}

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
  $app.innerHTML = `<h1>${esc(d.label)}</h1>
    <div class="sub"><span class="score r ${d.winner === "red" ? "win" : ""}">${d.red.score}</span> – <span class="score b ${d.winner === "blue" ? "win" : ""}">${d.blue.score}</span>
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
      </div>
      <div><div class="card"><div class="fieldwrap" id="fw"></div>
        <div class="controls"><button id="play">▶ Play</button><span class="clock" id="clock">–</span><span class="phase" id="phase"></span>
          <input type="range" id="scrub" min="0" max="${Math.max(N - 1, 0)}" value="0" step="1"></div>
        <div class="controls"><button id="trail" class="on">Trails</button><button id="full">Full paths</button>
          <button id="speed">1×</button><button id="sync" class="on" title="follow the video">Sync video</button></div>
        <div class="legend">${tracks.map((t, i) => `<label><input type="checkbox" data-i="${i}" checked>
          <span class="sw" style="background:${t.color}"></span><span class="team ${t.alliance[0]}">${t.label}</span>
          ${t.team ? confBar(t.id_conf) : ""}</label>`).join("")}</div></div></div>
    </div>`;
  const F = fieldCanvas(document.getElementById("fw"));
  let f = 0, playing = false, last = 0, trail = true, full = false, speed = 1, sync = true, player = null, alive = true;
  const scrub = document.getElementById("scrub");
  const draw = () => {
    const { ctx, P, S, bg, c } = F; bg(0.95);
    const fi = Math.max(0, Math.min(N - 1, Math.round(f)));
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

// ------------------------------------------------------------------ team page
async function teamPage(ev, tk) {
  const [d, e] = await Promise.all([api(`${ev}/t/${tk}.json`), api(`${ev}/event.json`).catch(() => null)]);
  crumbs([[e ? e.name : ev, `#/${ev}`], [`${d.number}${d.nickname ? " · " + d.nickname : ""}`]]); $updated.textContent = "";
  $app.innerHTML = `<h1>${d.number} <span class="muted" style="font-weight:500">${esc(d.nickname || "")}</span></h1>
    <div class="sub">${d.rank ? `Rank ${d.rank} · ` : ""}${d.opr != null ? `OPR ${fx(d.opr)} · ` : ""}${d.tracked} matches tracked
      · <a href="https://www.thebluealliance.com/team/${d.number}/${(e && e.year) || ""}" target="_blank" rel="noopener">TBA</a></div>
    <div class="kv" style="margin-bottom:16px">
      <div><b>${fx(d.distance_m, 0)} m</b><span>distance / match</span></div>
      <div><b>${fx(d.auto_distance_m)} m</b><span>auto distance</span></div>
      <div><b>${fx(d.max_speed)} m/s</b><span>top speed</span></div>
      <div><b>${pct(d.time_own_zone)}</b><span>time in own zone</span></div>
      <div><b>${pct(d.time_neutral)}</b><span>time in neutral zone</span></div>
      <div><b>${pct(d.time_opp_zone)}</b><span>time in opponent zone</span></div></div>
    <div class="grid2">
      <div class="card"><h2>Auto paths</h2><div id="autos"></div><div class="chips" id="chips"></div>
        <div class="note">First 20 s of every tracked match, from the team's own end (own alliance wall on the left).
        Dot = start, arrow = where it was at the end of auto. Faded segments are interpolated.</div></div>
      <div class="card"><h2>Teleop heatmap</h2><div id="heat"></div>
        <div class="note">Where the robot spent teleop, summed over all tracked matches, from the team's own end.</div></div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Matches</h2><div class="tablewrap"><table><thead><tr><th>Match</th><th>Alliance</th>
      <th class="num">Dist (m)</th><th class="num">Auto (m)</th><th class="num">Top (m/s)</th><th class="num">Own</th><th class="num">Neutral</th><th class="num">Opp.</th><th class="num">Seen</th><th>Label</th></tr></thead>
      <tbody>${d.matches.map(m => `<tr><td><a href="#/${ev}/m/${m.match}">${esc(m.short)}</a></td><td class="${m.alliance[0]}">${m.alliance}</td>
        <td class="num">${fx(m.distance_m, 0)}</td><td class="num">${fx(m.auto_distance_m)}</td><td class="num">${fx(m.max_speed)}</td>
        <td class="num">${pct(m.time_own_zone)}</td><td class="num">${pct(m.time_neutral)}</td><td class="num">${pct(m.time_opp_zone)}</td>
        <td class="num">${pct(m.observed)}</td><td>${confBar(m.conf)}</td></tr>`).join("") || `<tr><td colspan="10" class="empty">No tracked matches yet</td></tr>`}</tbody></table></div></div>`;

  const A = fieldCanvas(document.getElementById("autos"));
  const on = d.autos.map(() => true);
  const chips = document.getElementById("chips");
  chips.innerHTML = d.autos.map((a, i) => `<span class="chip on" data-i="${i}" style="color:${PALETTE[i % PALETTE.length]}">${esc(a.short)}</span>`).join("");
  chips.querySelectorAll(".chip").forEach(ch => ch.onclick = () => { const i = +ch.dataset.i; on[i] = !on[i]; ch.classList.toggle("on", on[i]); drawAutos(); });
  function drawAutos() {
    const { ctx, P, c, bg } = A; bg(0.85);
    d.autos.forEach((a, i) => {
      if (!on[i] || !a.xy.length) return;
      const col = PALETTE[i % PALETTE.length]; ctx.strokeStyle = col; ctx.fillStyle = col;
      ctx.lineWidth = Math.max(2, c.width / 380); ctx.lineJoin = "round"; ctx.lineCap = "round";
      for (let j = 1; j < a.xy.length; j++) {
        const [x0, y0] = P(a.xy[j - 1][0] / 100, a.xy[j - 1][1] / 100), [x1, y1] = P(a.xy[j][0] / 100, a.xy[j][1] / 100);
        ctx.globalAlpha = a.obs && !a.obs[j] ? 0.35 : 0.9;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      const [sx, sy] = P(a.xy[0][0] / 100, a.xy[0][1] / 100); ctx.beginPath(); ctx.arc(sx, sy, c.width / 160, 0, 7); ctx.fill();
      const n = a.xy.length - 1, [ex, ey] = P(a.xy[n][0] / 100, a.xy[n][1] / 100), [px, py] = P(a.xy[Math.max(0, n - 2)][0] / 100, a.xy[Math.max(0, n - 2)][1] / 100);
      const ang = Math.atan2(ey - py, ex - px), r = c.width / 90;
      ctx.beginPath(); ctx.moveTo(ex + r * Math.cos(ang), ey + r * Math.sin(ang));
      ctx.lineTo(ex + r * Math.cos(ang + 2.5), ey + r * Math.sin(ang + 2.5)); ctx.lineTo(ex + r * Math.cos(ang - 2.5), ey + r * Math.sin(ang - 2.5)); ctx.fill();
    });
  }
  const H = fieldCanvas(document.getElementById("heat"));
  function drawHeat() {
    const { ctx, P, c, bg } = H; bg(0.6);
    if (!d.heat) return;
    const g = d.grid, mx = Math.max(...d.heat.flat()) || 1;
    for (let i = 0; i < d.heat.length; i++) for (let j = 0; j < d.heat[i].length; j++) {
      const v = d.heat[i][j] / mx; if (v < 0.02) continue;
      const [x0, y0] = P(i * g, j * g), [x1, y1] = P((i + 1) * g, (j + 1) * g);
      ctx.fillStyle = `hsla(${Math.round(55 - 55 * v)}, 100%, 55%, ${0.25 + 0.6 * Math.sqrt(v)})`;
      ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0) + 0.5, Math.abs(y1 - y0) + 0.5);
    }
  }
  const redraw = () => { A.size(); H.size(); drawAutos(); drawHeat(); };
  FIELD.complete ? redraw() : FIELD.addEventListener("load", redraw, { once: true });
  window.addEventListener("resize", redraw); cleanup.push(() => window.removeEventListener("resize", redraw));
}

// ------------------------------------------------------------------ router
async function route(scroll = true) {
  cleanup.forEach(f => f()); cleanup = [];
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (!p.length) await home();
    else if (p.length === 1) await eventPage(p[0]);
    else if (p[1] === "m") await matchPage(p[0], p[2]);
    else if (p[1] === "t") await teamPage(p[0], p[2]);
    else await eventPage(p[0]);
  } catch (e) {
    console.error(e);
    $app.innerHTML = `<div class="empty">Couldn't load this page (${esc(e.message)}). It may not be processed yet.</div>`;
  }
  if (scroll) window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => route());
FIELD.onload = () => { };
route();
