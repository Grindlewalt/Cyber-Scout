"use strict";
// Quantitative helpers, charts and the team page. Loaded before app.js (shares its globals:
// api, fieldCanvas, esc, fx, pct, num, crumbs, FIELD, COLORS, PALETTE, cleanup, $app, $updated, confBar).

// ------------------------------------------------------------------ small helpers
const val = x => (x && typeof x === "object" && "v" in x) ? x.v : x;                 // metric: number | {v,n,conf}
const mn = x => (x && typeof x === "object") ? x.n : null;
const mc = x => (x && typeof x === "object") ? x.conf : null;
const has = v => v != null && !Number.isNaN(v);
const C_MADE = "#4ade80", C_MISS = "#fb7185", C_PASS = "#facc15";
const PERIODS = ["auto", "transition", "shift1", "shift2", "shift3", "shift4", "endgame"];
const PLABEL = { auto: "Auto", transition: "Trans.", shift1: "Shift 1", shift2: "Shift 2", shift3: "Shift 3", shift4: "Shift 4", endgame: "End game" };
const HUBS = { red: [11.915, 4.035], blue: [4.626, 4.035] };
const fmtV = (v, f) => !has(v) ? "–" : f === "p" ? Math.round(v * 100) + "%" : f === "0" ? String(Math.round(v)) : f === "2" ? Number(v).toFixed(2) : Number(v).toFixed(1);

function lsGet(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { } }

const teamCache = {};
function getTeam(ev, tk) { return teamCache[ev + tk] || (teamCache[ev + tk] = api(`${ev}/t/${tk}.json`)); }

/** metric tile: x is a number or {v,n,conf}; shows n and a confidence bar when present */
function tile(label, x, f = "1", unit = "", tip = "") {
  const v = val(x), n = mn(x), c = mc(x);
  return `<div class="tile" title="${esc(tip)}"><b>${fmtV(v, f)}${has(v) && unit ? `<small>${unit}</small>` : ""}</b><span>${label}</span>
    ${n != null ? `<em>n=${n}${c != null ? ` ${confBar(c, "metric confidence")}` : ""}</em>` : ""}</div>`;
}
function spark(vals, opts = {}) {
  const w = opts.w || 110, h = opts.h || 26, ok = vals.filter(has);
  if (ok.length < 2) return `<span class="muted small">–</span>`;
  const lo = Math.min(...ok), hi = Math.max(...ok), rg = hi - lo || 1;
  const pts = vals.map((v, i) => has(v) ? [2 + i * (w - 4) / (vals.length - 1), h - 3 - (v - lo) / rg * (h - 6)] : null).filter(Boolean);
  const last = pts[pts.length - 1];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${opts.color || "#7cb4ff"}" stroke-width="1.6" points="${pts.map(p => p.map(x => x.toFixed(1)).join(",")).join(" ")}"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="2.4" fill="${opts.color || "#7cb4ff"}"/></svg>`;
}
function bars(rows, opts = {}) {      // rows: [[label, value, max, text, color]]
  return `<div class="bars">${rows.map(([l, v, mx, t, col]) => `<div class="barrow"><span class="bl">${l}</span>
    <span class="bt"><i style="width:${mx ? Math.max(0, Math.min(100, 100 * v / mx)) : 0}%;background:${col || "var(--accent)"}"></i></span><span class="bv">${t ?? fmtV(v)}</span></div>`).join("")}</div>`;
}
function periodBars(byp, n = 1) {     // {period:{a,m}} -> attempts bar with made overlay
  const mx = Math.max(1, ...PERIODS.map(p => (byp[p] || {}).a || 0));
  return `<div class="bars">${PERIODS.map(p => { const b = byp[p] || { a: 0, m: 0 };
    return `<div class="barrow"><span class="bl">${PLABEL[p]}</span><span class="bt"><i style="width:${100 * b.a / mx}%;background:#3a4a62"></i>
    <i style="width:${100 * b.m / mx}%;background:${C_MADE};margin-top:-10px"></i></span><span class="bv">${fx(b.m, 1)}/${fx(b.a, 1)}</span></div>`; }).join("")}</div>`;
}

// ------------------------------------------------------------------ field drawings (team frame: own end on the LEFT, coords in cm)
function drawHubMark(F, x, y, col = "#ffffff") {
  const { ctx, P, S } = F, [px, py] = P(x, y);
  ctx.strokeStyle = col; ctx.globalAlpha = .7; ctx.lineWidth = Math.max(1.5, F.c.width / 500);
  ctx.beginPath(); ctx.arc(px, py, S(.6), 0, 7); ctx.stroke(); ctx.globalAlpha = 1;
}
function densityLayer(F, pts, cell = .5, hue = 50) {          // pts: [[x_m,y_m]]
  const { ctx, P } = F, nx = Math.ceil(LEN / cell), ny = Math.ceil(WID / cell), g = new Float32Array(nx * ny);
  for (const [x, y] of pts) { const i = Math.min(nx - 1, Math.floor(x / cell)), j = Math.min(ny - 1, Math.floor(y / cell)); if (i >= 0 && j >= 0) g[i * ny + j]++; }
  const mx = Math.max(...g) || 1;
  for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) {
    const v = g[i * ny + j] / mx; if (v <= 0) continue;
    const [x0, y0] = P(i * cell, j * cell), [x1, y1] = P((i + 1) * cell, (j + 1) * cell);
    ctx.fillStyle = `hsla(${Math.round(hue - hue * v)},100%,55%,${0.2 + 0.6 * Math.sqrt(v)})`;
    ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0) + .5, Math.abs(y1 - y0) + .5);
  }
}
/** shots: team's shots_map entries. mode: "pts" | "heat" | "both" */
function drawShotChart(F, shots, mode = "both", pf = "all") {
  const { ctx, P, c, bg } = F; bg(.55);
  const use = shots.filter(s => pf === "all" || (pf === "auto" ? s.p === "auto" : s.p !== "auto"));
  if (mode !== "pts") densityLayer(F, use.filter(s => s.o !== "pass").map(s => [s.xy[0] / 100, s.xy[1] / 100]), .5, 55);
  drawHubMark(F, HUBS.red[0], HUBS.red[1]);
  if (mode !== "heat") {
    const r = Math.max(2, c.width / 330);
    for (const s of use) {
      const [x, y] = P(s.xy[0] / 100, s.xy[1] / 100);
      ctx.fillStyle = s.o === "made" ? C_MADE : s.o === "pass" ? C_PASS : C_MISS; ctx.globalAlpha = .75;
      ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  return use.length;
}
function drawIntakeMap(F, pts, mode = "both") {
  const { ctx, P, c, bg } = F; bg(.55);
  if (mode !== "pts") densityLayer(F, pts.map(p => [p[0] / 100, p[1] / 100]), .6, 200);
  if (mode !== "heat") { ctx.fillStyle = "#22d3ee"; ctx.globalAlpha = .8; for (const p of pts) { const [x, y] = P(p[0] / 100, p[1] / 100); ctx.beginPath(); ctx.arc(x, y, Math.max(2, c.width / 330), 0, 7); ctx.fill(); } ctx.globalAlpha = 1; }
}
function drawPath(F, xy, col, { w = 1, alpha = .9, arrow = true, obs = null, dot = true } = {}) {
  const { ctx, P, c } = F; if (!xy || xy.length < 2) return;
  ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = Math.max(1.5, c.width / 380) * w; ctx.lineJoin = ctx.lineCap = "round";
  for (let j = 1; j < xy.length; j++) {
    const [x0, y0] = P(xy[j - 1][0] / 100, xy[j - 1][1] / 100), [x1, y1] = P(xy[j][0] / 100, xy[j][1] / 100);
    ctx.globalAlpha = alpha * (obs && !obs[j] ? .4 : 1); ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  }
  ctx.globalAlpha = alpha;
  if (dot) { const [sx, sy] = P(xy[0][0] / 100, xy[0][1] / 100); ctx.beginPath(); ctx.arc(sx, sy, c.width / 160 * Math.sqrt(w), 0, 7); ctx.fill(); }
  if (arrow) {
    const n = xy.length - 1, [ex, ey] = P(xy[n][0] / 100, xy[n][1] / 100), [px, py] = P(xy[Math.max(0, n - 2)][0] / 100, xy[Math.max(0, n - 2)][1] / 100);
    const ang = Math.atan2(ey - py, ex - px), r = c.width / 90 * Math.sqrt(w);
    ctx.beginPath(); ctx.moveTo(ex + r * Math.cos(ang), ey + r * Math.sin(ang));
    ctx.lineTo(ex + r * Math.cos(ang + 2.5), ey + r * Math.sin(ang + 2.5)); ctx.lineTo(ex + r * Math.cos(ang - 2.5), ey + r * Math.sin(ang - 2.5)); ctx.fill();
  }
  ctx.globalAlpha = 1;
}
/** attach a canvas to el, and call draw(F) now, on image load and on resize */
function liveCanvas(el, draw) {
  const F = fieldCanvas(el);
  const go = () => { F.size(); draw(F); };
  FIELD.complete ? go() : FIELD.addEventListener("load", go, { once: true });
  window.addEventListener("resize", go); cleanup.push(() => window.removeEventListener("resize", go));
  go.F = F; F.redraw = go; return F;
}
function chipRow(el, opts, cur, cb) {       // single-select chips
  el.innerHTML = opts.map(([k, l]) => `<span class="chip ${k === cur ? "on" : ""}" data-k="${k}">${l}</span>`).join("");
  el.querySelectorAll(".chip").forEach(ch => ch.onclick = () => { el.querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === ch)); cb(ch.dataset.k); });
}

// ------------------------------------------------------------------ team page
const ROLE_TXT = { scorer: "Scorer", feeder: "Feeder / passer", collector: "Collector", defender: "Defender", hybrid: "Hybrid" };
function section(id, title, body, note = "") {
  return `<section class="card sect" id="s-${id}"><h2>${title}</h2>${body}${note ? `<div class="note">${note}</div>` : ""}</section>`;
}

async function teamPage(ev, tk) {
  const [d, e] = await Promise.all([getTeam(ev, tk), api(`${ev}/event.json`).catch(() => null)]);
  crumbs([[e ? e.name : ev, `#/${ev}`], [`${d.number}${d.nickname ? " · " + d.nickname : ""}`]]); $updated.textContent = "";
  const ag = d.analytics, S = ag && ag.scoring || {}, MV = ag && ag.movement || {}, DF = ag && ag.defense || {}, EG = ag && ag.endgame || {}, RL = ag && ag.reliability || {};
  const ms = d.matches || [], an = ms.map(m => m.analytics || null);
  const tr = fn => an.map(a => { try { return a ? fn(a) : null; } catch (x) { return null; } });
  const starred = (lsGet("autoscout:picks:" + ev, {}).stars || []).includes(tk);
  const role = ag && ag.role;
  const navs = [["scoring", "Scoring"], ["shots", "Shot chart"], ["intake", "Intake"], ["auto", "Auto"], ["move", "Movement"], ["def", "Defense"], ["end", "Endgame"], ["rel", "Reliability"], ["matches", "Matches"]];

  const levels = EG.levels || {}, nL = Object.values(levels).reduce((a, b) => a + b, 0) || 1;
  const trend = (label, vals, f, color) => `<div class="trend"><span>${label}</span>${spark(vals, { color })}<em>${fmtV(vals.filter(has).slice(-1)[0], f)}</em></div>`;

  $app.innerHTML = `<div class="titlebar"><div><h1>${d.number} <span class="muted" style="font-weight:500">${esc(d.nickname || "")}</span></h1>
    <div class="sub">${d.rank ? `Rank ${d.rank} · ` : ""}${d.opr != null ? `OPR ${fx(d.opr)} · ` : ""}${d.tracked} matches tracked
      ${role ? ` · <span class="pill role">${ROLE_TXT[val(role)] || val(role)}</span>` : ""}
      · <a href="https://www.thebluealliance.com/team/${d.number}/${(e && e.year) || ""}" target="_blank" rel="noopener">TBA</a></div></div>
    <button id="star" class="${starred ? "on" : ""}">${starred ? "★ Starred" : "☆ Star"}</button></div>
    <nav class="jump">${navs.map(([k, l]) => `<a href="#" data-j="${k}">${l}</a>`).join("")}</nav>
    ${ag ? "" : `<div class="card empty">No quantitative analytics for this team yet.</div>`}
    <div class="trends">${trend("Accuracy", tr(a => a.scoring.accuracy), "p", C_MADE)}${trend("Made", tr(a => a.scoring.made), "0", "#7cb4ff")}${trend("Attempts", tr(a => a.scoring.attempts), "0", "#7cb4ff")}
      ${trend("Cycle s", tr(a => a.scoring.cycle_s), "1", "#fbbf24")}${trend("Dist m", tr(a => a.movement.distance_m), "0", "#a78bfa")}${trend("Defense s", tr(a => a.defense.time_s), "1", "#fb7185")}</div>
    <div class="stackv">
    ${section("scoring", "Scoring", `<div class="tiles">
        ${tile("Accuracy", has(S.accuracy_pooled) ? { ...(S.accuracy || {}), v: S.accuracy_pooled } : S.accuracy, "p", "", "made / attempted fuel (pooled over matches)")}
        ${tile("Made / match", S.made, "1")}${tile("Attempts / match", S.attempts, "1")}${tile("Passes / match", S.passes, "1")}
        ${tile("Points / match", S.points, "1", "", "made into an ACTIVE hub")}${tile("Uncounted / match", S.uncounted, "1", "", "made into an inactive hub")}
        ${tile("Fire rate", S.fire_rate, "1", " /s", "balls per second inside a volley")}${tile("Volley size", S.volley_size, "0", "", "max balls in a volley ≈ hopper capacity")}
        ${tile("Volleys / match", S.volleys, "1")}${tile("Cycle time", S.cycle_s, "1", " s", "time between volleys")}
        ${tile("Shot distance", S.dist_mean, "1", " m")}${tile("Shoot on move", S.on_move_rate, "p", "", "share of shots launched while moving")}
        ${tile("1st shot (auto)", S.first_shot_auto, "1", " s")}${tile("1st shot (teleop)", S.first_shot_teleop, "1", " s")}</div>
      <h3>By period <span class="muted small">(made / attempts per match)</span></h3>${S.by_period ? periodBars(S.by_period) : ""}`,
      "Aggregates are per-match means over tracked matches. n = matches the metric was available in; the bar is confidence (coverage × sample size).")}
    ${section("shots", "Shot chart", `<div class="chips" id="sc-p"></div><div class="chips" id="sc-m"></div><div id="shotmap"></div>
      <div class="legendline"><i style="background:${C_MADE}"></i>made <i style="background:${C_MISS}"></i>missed <i style="background:${C_PASS}"></i>pass <span class="muted small" id="sc-n"></span></div>`,
      "Launch positions from the team's own end (own alliance wall on the left, hub ringed). Density shows where it shoots from, all attempts.")}
    ${section("intake", "Intake map", `<div id="intakemap"></div>
      <div class="tiles" style="margin-top:10px">${tile("Intakes / match", ag && ag.collection && ag.collection.intakes, "1")}${tile("Per minute", ag && ag.collection && ag.collection.per_min, "1")}
      ${["neutral", "depot", "outpost", "opp"].map(k => tile("From " + k, ag && ag.collection && ag.collection.by_loc && ag.collection.by_loc[k], "1")).join("")}</div>`, "Where floor fuel was picked up, from the team's own end.")}
    ${section("auto", "Autonomous", `<div class="grid2"><div><div id="autocv"></div><div class="chips" id="autochips"></div><label class="small muted"><input type="checkbox" id="allautos"> show all auto paths</label></div>
      <div id="routines"></div></div>`, "Highlighted path = representative of the selected routine. Dot = start, arrow = end of auto. Routines are clustered by start spot and path shape.")}
    ${section("move", "Movement", `<div class="grid2"><div><div class="tiles">
        ${tile("Distance / match", MV.distance_m, "0", " m")}${tile("Mean speed", MV.mean_speed, "2", " m/s", "while moving")}${tile("p95 speed", MV.p95_speed, "2", " m/s")}
        ${tile("Max accel", MV.max_accel, "1", " m/s²")}${tile("Time moving", MV.pct_moving, "p")}${tile("Auto distance", ag && ag.auto && ag.auto.distance_m, "1", " m")}</div>
        <h3>Zone time</h3>${MV.zone ? bars([["Own", val(MV.zone.own), 1, pct(val(MV.zone.own)), "#7cb4ff"], ["Neutral", val(MV.zone.neutral), 1, pct(val(MV.zone.neutral)), "#94a3b8"], ["Opp.", val(MV.zone.opp), 1, pct(val(MV.zone.opp)), "#fb7185"]]) : ""}
        <h3>Mid-field crossings <span class="muted small">(total)</span></h3>${MV.lanes_total ? bars(["bump", "trench", "open"].map(k => [k[0].toUpperCase() + k.slice(1), MV.lanes_total[k], Math.max(1, ...Object.values(MV.lanes_total)), String(MV.lanes_total[k])])) : ""}
        <div class="small muted">${MV.trench_capable != null ? (MV.trench_capable ? "Trench-capable (has driven through a trench)" : "No trench run seen") : ""}</div></div>
      <div><h3>Teleop heatmap</h3><div id="heat"></div></div></div>`, "Heatmap: where the robot spent teleop, summed over tracked matches.")}
    ${section("def", "Defense", `<div class="tiles">${tile("Time defending", DF.time_s, "1", " s", "in opponent half, close to and shadowing an opponent")}${tile("Contacts", DF.contacts, "1", "", "opposing robots within ~1 m")}
        ${tile("Pins", DF.pins, "1", "", "sustained contact, victim nearly stopped")}${tile("Effect", DF.effect, "p", "", "drop in defended opponent fire rate")}
        ${tile("Defended: time", ag && ag.defended && ag.defended.time_s, "1", " s")}${tile("Rate retained", ag && ag.defended && ag.defended.rate_retained, "p", "", "own scoring rate when defended")}
        ${tile("Escape time", ag && ag.defended && ag.defended.escape_s, "1", " s")}${tile("Congestion", ag && ag.congestion, "p", "", "time within 1.5 m of a teammate")}</div>`)}
    ${section("end", "Endgame", `<div class="grid2"><div><h3>Climb level</h3>${bars([0, 1, 2, 3].map(l => ["Level " + l || "none", levels[l] || 0, nL, `${levels[l] || 0}/${nL}`, l ? C_MADE : "#475569"]).map(r => (r[0] = r[0] === "Level 0" ? "None" : r[0], r)))}</div>
      <div class="tiles">${tile("Climb rate", has(EG.climb_rate) ? { v: EG.climb_rate, n: EG.n } : null, "p")}${tile("Attempt rate", has(EG.attempt_rate) ? { v: EG.attempt_rate, n: EG.n } : null, "p")}
        ${tile("Arrives at tower", EG.arrive_t, "0", " s", "match time")}${tile("Climb duration", EG.duration_s, "1", " s")}${tile("Stops scoring", EG.stop_scoring_t, "0", " s", "match time")}</div></div>`)}
    ${section("rel", "Reliability & role", `<div class="grid2"><div class="tiles">${tile("Dead matches", has(RL.dead_matches) ? { v: RL.dead_matches, n: RL.n } : null, "0", "", "no movement for 15 s or more mid-match, not climbing")}
        ${tile("Avg dead time", RL.mean_dead_s, "1", " s")}${tile("Low-coverage matches", has(RL.low_coverage_matches) ? RL.low_coverage_matches : null, "0")}</div>
      <div>${role ? `<h3>Role <span class="pill role">${ROLE_TXT[val(role)] || val(role)}</span> ${confBar(mc(role), "role confidence")} <span class="muted small">n=${mn(role)}</span></h3>${role.scores ? bars(Object.entries(role.scores).sort((a, b) => b[1] - a[1]).map(([k, v]) => [ROLE_TXT[k] || k, v, 1, pct(v)])) : ""}` : ""}</div></div>`)}
    ${section("matches", "Matches", `<div class="tablewrap"><table><thead><tr><th>Match</th><th>All.</th><th class="num">Made/Att</th><th class="num">Acc</th><th class="num">Fire /s</th><th class="num">Volley</th><th class="num">Cycle s</th>
      <th class="num">Intakes</th><th>Auto</th><th class="num">Auto made</th><th>Climb</th><th class="num">Def s</th><th class="num">Cont.</th><th class="num">Pins</th><th class="num">Dist m</th><th>Flags</th></tr></thead>
      <tbody>${ms.map((m, i) => { const a = an[i]; return `<tr><td><a href="#/${ev}/m/${m.match}">${esc(m.short)}</a></td><td class="${m.alliance[0]}">${m.alliance[0].toUpperCase()}</td>` + (a ? `
        <td class="num">${a.scoring.made}/${a.scoring.attempts}</td><td class="num">${fmtV(a.scoring.accuracy, "p")}</td><td class="num">${fmtV(a.scoring.fire_rate)}</td><td class="num">${fmtV(a.scoring.volley_size, "0")}</td><td class="num">${fmtV(a.scoring.cycle_s)}</td>
        <td class="num">${a.collection.intakes}</td><td>${esc(a.auto.routine || "–")}</td><td class="num">${a.auto.made}/${a.auto.shots}</td><td>${a.endgame.level ? "L" + a.endgame.level : a.endgame.attempted ? "failed" : "–"}</td>
        <td class="num">${fmtV(a.defense.time_s)}</td><td class="num">${a.defense.contacts}</td><td class="num">${a.defense.pins}</td><td class="num">${fmtV(a.movement.distance_m, "0")}</td>
        <td>${a.reliability.dead ? '<span class="pill failed">dead</span>' : ""}${a.reliability.low_coverage ? '<span class="pill no_video">low cov.</span>' : ""}</td>` : `<td colspan="14" class="muted">no analytics</td>`) + `</tr>`; }).join("")
        || `<tr><td colspan="16" class="empty">No tracked matches yet</td></tr>`}</tbody></table></div>`)}
    </div>`;

  document.querySelectorAll(".jump a").forEach(a => a.onclick = ev_ => { ev_.preventDefault(); const s = document.getElementById("s-" + a.dataset.j); s && s.scrollIntoView({ behavior: "smooth", block: "start" }); });
  document.getElementById("star").onclick = e_ => {
    const p = lsGet("autoscout:picks:" + ev, {}); p.stars = p.stars || [];
    const i = p.stars.indexOf(tk); i >= 0 ? p.stars.splice(i, 1) : p.stars.push(tk); lsSet("autoscout:picks:" + ev, p);
    e_.target.classList.toggle("on", i < 0); e_.target.textContent = i < 0 ? "★ Starred" : "☆ Star";
  };

  // shot chart
  let smode = "both", sper = "all";
  const shots = d.shots_map || [];
  chipRow(document.getElementById("sc-p"), [["all", "All"], ["auto", "Auto"], ["tele", "Teleop"]], sper, k => { sper = k; SC.redraw(); });
  chipRow(document.getElementById("sc-m"), [["both", "Points + density"], ["pts", "Points"], ["heat", "Density"]], smode, k => { smode = k; SC.redraw(); });
  const SC = liveCanvas(document.getElementById("shotmap"), F => { const n = drawShotChart(F, shots, smode, sper); document.getElementById("sc-n").textContent = `${n} shots`; });
  liveCanvas(document.getElementById("intakemap"), F => drawIntakeMap(F, d.intakes_map || []));

  // autos / routines
  const routines = d.routines || [], autos = d.autos || [];
  let selR = routines.length ? routines[0].id : null, showAll = false;
  const on = autos.map(() => true), chips = document.getElementById("autochips");
  chips.innerHTML = autos.map((a, i) => `<span class="chip on" data-i="${i}" style="color:${PALETTE[i % PALETTE.length]}">${esc(a.short)}${a.routine ? " · " + esc(a.routine) : ""}</span>`).join("");
  chips.querySelectorAll(".chip").forEach(ch => ch.onclick = () => { const i = +ch.dataset.i; on[i] = !on[i]; ch.classList.toggle("on", on[i]); AC.redraw(); });
  const AC = liveCanvas(document.getElementById("autocv"), F => {
    F.bg(.6);
    if (showAll || !routines.length) autos.forEach((a, i) => { if (on[i]) drawPath(F, a.xy, PALETTE[i % PALETTE.length], { alpha: routines.length ? .35 : .9, obs: a.obs }); });
    routines.forEach((r, i) => { if (r.id === selR) drawPath(F, r.xy, "#ffffff", { w: 1.8, alpha: .95 }); });
  });
  document.getElementById("allautos").onchange = e_ => { showAll = e_.target.checked; AC.redraw(); };
  const rdiv = document.getElementById("routines");
  const drawR = () => {
    rdiv.innerHTML = routines.length ? `<div class="tablewrap"><table><thead><tr><th>Routine</th><th class="num">Used</th><th class="num">Shots</th><th class="num">Made</th><th class="num">Neutral</th><th class="num">Consist.</th></tr></thead><tbody>
      ${routines.map(r => `<tr class="${r.id === selR ? "sel" : ""} clk" data-r="${r.id}"><td><b>${esc(r.name || r.id)}</b><div class="small muted">start ${r.start_xy ? `(${fx(r.start_xy[0] / 100)}, ${fx(r.start_xy[1] / 100)})` : "–"} · ${esc((r.matches || []).join(", "))}</div></td>
      <td class="num">${r.n}/${r.of}<div class="bartiny"><i style="width:${100 * (r.freq ?? r.n / r.of)}%"></i></div></td><td class="num">${fx(r.shots_avg)}</td><td class="num">${fx(r.made_avg)}</td><td class="num">${pct(r.neutral_cross_rate)}</td><td class="num">${pct(r.consistency)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">No routines clustered yet.</div>`;
    rdiv.querySelectorAll("tr.clk").forEach(tr => tr.onclick = () => { selR = tr.dataset.r; drawR(); AC.redraw(); });
  };
  drawR();

  liveCanvas(document.getElementById("heat"), F => {
    const { ctx, P, bg } = F; bg(.6); if (!d.heat) return;
    const g = d.grid, mx = Math.max(...d.heat.flat()) || 1;
    for (let i = 0; i < d.heat.length; i++) for (let j = 0; j < d.heat[i].length; j++) {
      const v = d.heat[i][j] / mx; if (v < .02) continue;
      const [x0, y0] = P(i * g, j * g), [x1, y1] = P((i + 1) * g, (j + 1) * g);
      ctx.fillStyle = `hsla(${Math.round(55 - 55 * v)},100%,55%,${.25 + .6 * Math.sqrt(v)})`;
      ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0) + .5, Math.abs(y1 - y0) + .5);
    }
  });
}
