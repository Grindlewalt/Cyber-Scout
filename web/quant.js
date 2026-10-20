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

// an explanation that opens on a tap or click too (app.js tipA: data-tip + title), not only on hover (MATCH-15)
const tipX = (t, key) => typeof tipA === "function" ? tipA(t, key) : ` title="${esc(t)}"`;
/** metric tile: x is a number or {v,n,conf}; shows n and a confidence bar when present */
function tile(label, x, f = "1", unit = "", tip = "") {
  const v = val(x), n = mn(x), c = mc(x);
  // the explanation opens on a tap too (app.js tipA), not only on hover (MATCH-15)
  return `<div class="tile"${tip ? tipX(tip) : ""}><b>${fmtV(v, f)}${has(v) && unit ? `<small>${unit}</small>` : ""}</b><span>${label}</span>
    ${n != null ? `<em>n=${n}${c != null ? ` ${confBar(c, "metric confidence")}` : ""}</em>` : ""}</div>`;
}
function spark(vals, opts = {}) {
  const w = opts.w || 110, h = opts.h || 26, ok = vals.filter(has);
  if (ok.length < 3) return `<span class="muted small" title="fewer than 3 points: no trend">–</span>`;
  const lo = Math.min(...ok), hi = Math.max(...ok), rg = hi - lo || 1;
  const pts = vals.map((v, i) => has(v) ? [2 + i * (w - 4) / (vals.length - 1), h - 3 - (v - lo) / rg * (h - 6)] : null).filter(Boolean);
  const last = pts[pts.length - 1];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${opts.color || "currentColor"}" stroke-width="1.6" points="${pts.map(p => p.map(x => x.toFixed(1)).join(",")).join(" ")}"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="2.4" fill="${opts.color || "currentColor"}"/></svg>`;
}
function bars(rows, opts = {}) {      // rows: [[label, value, max, text, color]]
  return `<div class="bars">${rows.map(([l, v, mx, t, col]) => `<div class="barrow"><span class="bl">${l}</span>
    <span class="bt"><i style="width:${mx ? Math.max(0, Math.min(100, 100 * v / mx)) : 0}%;background:${col || "var(--ink-3)"}"></i></span><span class="bv">${t ?? fmtV(v)}</span></div>`).join("")}</div>`;
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
// shot classes. "out" = not aimed at our hub: passes, feeds and clears (lobbed out of the far end). Old data has no
// pass_kind: a "missed" shot that LANDED nearer the opponent's hub (HUBS.blue, team frame) is a clear, not a miss.
const shotIsClear = s => s.o === "pass" ? s.k === "clear"
  : s.o === "missed" && !!s.land && Math.hypot(s.land[0] / 100 - HUBS.blue[0], s.land[1] / 100 - HUBS.blue[1]) < Math.hypot(s.land[0] / 100 - HUBS.red[0], s.land[1] / 100 - HUBS.red[1]);
const shotIsOut = s => s.o === "pass" || shotIsClear(s);
const shotIsHub = s => (s.o === "made" || s.o === "missed") && !shotIsClear(s);
const SHOW_CHIPS = [["all", "All"], ["hub", "Hub shots"], ["out", "Passes & clears"]];
const shotsIn = (shots, pf = "all") => (shots || []).filter(s => pf === "all" || (pf === "auto" ? s.p === "auto" : s.p !== "auto"));
/** {all, hub, out} counts after the period filter (for chip labels) */
function shotCounts(shots, pf = "all") { const u = shotsIn(shots, pf), h = u.filter(shotIsHub).length, o = u.filter(shotIsOut).length; return { all: u.length, hub: h, out: o }; }
/** inline legend for drawShotChart's dot colours */
function shotLegend() {
  return `<span class="shotlegend"><span><i style="background:${C_MADE}"></i>made</span><span><i style="background:${C_MISS}"></i>missed</span><span title="passes, feeds and clears: FUEL sent somewhere other than our hub"><i style="background:${C_PASS}"></i>pass / clear <span class="muted">(not toward hub)</span></span></span>`;
}
/** shots: team's shots_map entries. mode: "pts" | "heat" | "both"; pf: "all" | "auto" | "tele";
 *  show: "all" | "hub" (made/missed toward our hub) | "out" (passes, feeds, clears). Density uses the shown class
 *  (hub shots for "all"/"hub"). Returns the number of points shown. */
function drawShotChart(F, shots, mode = "both", pf = "all", show = "all") {
  const { ctx, P, c, bg } = F; bg(.55);
  const use = shotsIn(shots, pf), hubS = use.filter(shotIsHub), outS = use.filter(shotIsOut);
  const shown = show === "hub" ? hubS : show === "out" ? outS : use;
  if (mode !== "pts") densityLayer(F, (show === "out" ? outS : hubS).map(s => [s.xy[0] / 100, s.xy[1] / 100]), .5, 55);
  drawHubMark(F, HUBS.red[0], HUBS.red[1]);
  if (mode !== "heat") {
    const r = Math.max(2, c.width / 330);
    for (const s of shown) {
      const [x, y] = P(s.xy[0] / 100, s.xy[1] / 100);
      ctx.fillStyle = s.o === "made" ? C_MADE : shotIsOut(s) ? C_PASS : C_MISS; ctx.globalAlpha = .75;
      ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  return shown.length;
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
function chipRow(el, opts, cur, cb) {       // single-select chips: buttons, so they work from the keyboard (aria-pressed = selected)
  el.innerHTML = opts.map(([k, l]) => `<button type="button" class="chip ${k === cur ? "on" : ""}" data-k="${k}" aria-pressed="${k === cur}">${l}</button>`).join("");
  el.querySelectorAll(".chip").forEach(ch => ch.onclick = () => { el.querySelectorAll(".chip").forEach(x => { x.classList.toggle("on", x === ch); x.setAttribute("aria-pressed", x === ch); }); cb(ch.dataset.k); });
}

// ------------------------------------------------------------------ hub active / inactive behaviour (team analytics.hub)
// Teleop shifts split by whether our own hub is active (FUEL scored while it is inactive does not count). Each phase is
// {observed_s, shots_pm, made_pm, passes_pm, intakes_pm, defending_frac, zone:{own,neutral,opp}} of {v,n,conf,trend} leaves.
// Activity labels: score = value / threshold; an activity applies at score >= 1 and the top two that apply are combined
// ("Passing + collecting"). Thresholds tuned on 2026wasam (hub active: scorers 30-110 shots/min, others 2-17; hub inactive:
// scorers pass/stockpile 10-57/min and collect 6-13/min; defending_frac tops out near 0.23):
//   Scoring    shots at our hub        >= 8 / min
//   Defending  defending_frac          >= 15 %
//   Passing    passes + feeds + clears >= 5 / min
//   Collecting intakes                 >= 5 / min, or >= 55 % of the time in the neutral zone
// None applies: "Holding in own zone" (own >= 60 %), "Waiting in opponent zone" (opp >= 50 %), else "Mostly idle";
// with "(some <activity>)" when the strongest activity is at least half its threshold.
const HUB_T = { shots_pm: 8, defending_frac: .15, passes_pm: 5, intakes_pm: 5, neutral: .55, own: .6, opp: .5 };
const HUB_ACT = [
  [h => h.shots_pm / HUB_T.shots_pm, "Scoring", "scoring"],
  [h => h.defending_frac / HUB_T.defending_frac, "Defending", "defending"],
  [h => h.passes_pm / HUB_T.passes_pm, "Passing / feeding", "passing"],
  [h => Math.max(h.intakes_pm / HUB_T.intakes_pm, h.neutral / HUB_T.neutral), "Collecting in neutral zone", "collecting"],
];
const HUB_THIN = 3;             // fewer matches than this: shown greyed as thin data
const per = (v, one, many = one + "s") => `${rate_(v)} ${rate_(v) === "1" ? one : many}/min`;
const rate_ = v => !has(v) ? "–" : v >= 9.95 ? String(Math.round(v)) : Number(v).toFixed(1).replace(/\.0$/, "");
/** analytics.hub -> null | {on:{label, html, n, thin}, off:{...}} (label: plain text; html: supporting numbers) */
function hubPhaseSummary(hub) {
  if (!hub || (!hub.on && !hub.off)) return null;
  const one = ph => {
    const z = (ph && ph.zone) || {};
    const g = ph ? { shots_pm: val(ph.shots_pm), made_pm: val(ph.made_pm), passes_pm: val(ph.passes_pm), intakes_pm: val(ph.intakes_pm),
      defending_frac: val(ph.defending_frac), own: val(z.own), neutral: val(z.neutral), opp: val(z.opp), obs: val(ph.observed_s) } : {};
    if (!ph || !Object.values(g).some(has)) return { label: "Not observed", html: `<span class="muted">under 5 s of video in this state</span>`, n: 0, thin: true };
    const n = Math.max(0, ...[ph.observed_s, ph.shots_pm, ph.defending_frac, z.own].map(mn).filter(has));
    const sc = HUB_ACT.map(([f, l, sh]) => [f(g) || 0, l, sh]).sort((a, b) => b[0] - a[0]), hits = sc.filter(x => x[0] >= 1);
    const label = hits.length > 1 ? hits[0][2][0].toUpperCase() + hits[0][2].slice(1) + " + " + hits[1][2] : hits.length ? hits[0][1]
      : ((g.own || 0) >= HUB_T.own ? "Holding in own zone" : (g.opp || 0) >= HUB_T.opp ? "Waiting in opponent zone" : "Mostly idle") + (sc[0][0] >= .5 ? ` (some ${sc[0][2]})` : "");
    const bits = [];
    if (has(g.shots_pm)) bits.push(`${per(g.shots_pm, "shot")}${has(g.made_pm) && g.shots_pm > 0 ? ` (${rate_(g.made_pm)} made)` : ""}`, per(g.passes_pm, "pass", "passes"), per(g.intakes_pm, "intake"));
    else bits.push(`<span class="muted">no shot data</span>`);
    if (has(g.defending_frac)) bits.push(`${pct(g.defending_frac)} defending`);
    if ([g.own, g.neutral, g.opp].some(has)) bits.push(`own ${pct(g.own)} / mid ${pct(g.neutral)} / opp ${pct(g.opp)}`);
    const thin = n < HUB_THIN;
    const html = bits.map(b => `<span>${b}</span>`).join(" · ") + ` <span class="hp-n">n=${n}${has(g.obs) ? ` · ~${Math.round(g.obs)} s/match seen` : ""}</span>${thin ? ' <span class="pill thin" title="fewer than 3 matches observed in this state: anecdotal">thin data</span>' : ""}`;
    return { label, html, n, thin };
  };
  return { on: one(hub.on), off: one(hub.off) };
}
/** the two-line "When hub is active: … / When hub is inactive: …" block; "" when there is no hub data */
function hubPhaseBlock(hub) {
  const HP = hubPhaseSummary(hub); if (!HP) return "";
  return `<div class="hubph">${[["on", "When hub is active"], ["off", "When hub is inactive"]].map(([k, t]) => `<div class="hp-row ${k}${HP[k].thin ? " thin" : ""}"><div class="hp-h"><span class="hp-k">${t}:</span> <b>${esc(HP[k].label)}</b></div><div class="hp-d">${HP[k].html}</div></div>`).join("")}</div>`;
}
const hubHelp = () => `<span class="hl"><b>Teleop shifts split by whether our own hub is active.</b> FUEL scored into an inactive hub does not count, so strong teams switch jobs then: collect, pass/feed, or defend.</span>`
  + `<span class="hl">Rates are per minute of video actually seen in that state, averaged over n verified matches. Grey = fewer than ${HUB_THIN} matches: anecdotal.</span>`
  + `<span class="hl">Labels: Scoring ≥ ${HUB_T.shots_pm} shots/min at our hub · Defending ≥ ${pct(HUB_T.defending_frac)} of the time · Passing ≥ ${HUB_T.passes_pm} passes, feeds or clears/min · Collecting ≥ ${HUB_T.intakes_pm} intakes/min or ≥ ${pct(HUB_T.neutral)} of the time in the neutral zone. When two apply, the stronger comes first. Otherwise the zone it waits in, or "Mostly idle".</span>`;

// ------------------------------------------------------------------ team page
function section(id, title, body, note = "") {
  return `<section class="card sect" id="s-${id}"><h2>${title}${note ? " " + CS.help(note, "About " + String(title).replace(/<[^>]*>/g, "").toLowerCase()) : ""}</h2>${body}</section>`;
}

// start spot in words (team frame: own wall at x = 16.54 m, driver's left = low y), not raw metres
const spotTxt = xy => { if (!xy) return "–"; const x = xy[0] / 100, y = xy[1] / 100, d = 16.541 - x; return `${y < 2.7 ? "driver's left" : y > 5.4 ? "driver's right" : "centre"}, ${d < 0 ? 0 : d.toFixed(1)} m off own wall`; };
const label_ = c => `n=${c.n}${c.of ? "/" + c.of : ""} (${(c.keys || []).map(shortKey).join(", ")})`;
async function teamPage(ev, tk) {
  const [d, e, scd] = await Promise.all([getTeam(ev, tk), api(`${ev}/event.json`).catch(() => null), scoutData(ev)]);
  const SR = (scd && scd.by[tk]) || {}, SV = SR.video || {}, RT = SR.rating || {}, CV = SR.coverage || { n: 0, keys: [] };
  const vkeys = new Set(CV.keys || []), vshort = new Set((CV.keys || []).map(shortKey));
  d.matches = (d.matches || []).map(m => ({ ...m, verified: vkeys.has(m.match) }));
  const tainted = !d.verified_only && d.matches.some(m => !m.verified);   // t-file not yet rebuilt by sitev2.verified_team_files
  d.shots_map = (d.shots_map || []).filter(p => vshort.has(p.m));
  d.autos = (d.autos || []).filter(a => vshort.has(a.short));
  if (tainted) { d.routines = []; d.heat = null; d.intakes_map = []; }
  crumbs([[e ? e.name : ev, `#/${ev}`], [`${d.number}${d.nickname ? " · " + d.nickname : ""}`]]); $updated.textContent = "";
  const ag = d.analytics, S = ag && ag.scoring || {}, MV = ag && ag.movement || {}, DF = ag && ag.defense || {}, EG = ag && ag.endgame || {}, RL = ag && ag.reliability || {};
  const ms = d.matches || [], an = ms.map(m => m.analytics || null);
  const hasRoutine = an.some((a, i) => a && ms[i].verified && a.auto && a.auto.routine), RC = hasRoutine ? 0 : 1;     // the Auto routine column only when some match has one
  const ma = (m, x) => m == null && x == null ? "–" : `${m ?? "–"}/${x ?? "–"}`;                                     // made / attempts; "–" instead of "null/null" (MATCH-19)
  const tr = fn => an.map((a, i) => { try { return a && ms[i].verified ? fn(a) : null; } catch (x) { return null; } });
  const starred = (lsGet("cyberscout:picks:" + ev, {}).stars || []).includes(tk);
  const HP = tainted ? null : hubPhaseSummary(ag && ag.hub);
  const mcf = m => m.team_conf != null ? m.team_conf : m.conf;
  const cfs = ms.map(mcf).filter(x => x != null), idMean = d.team_conf != null ? d.team_conf : cfs.length ? cfs.reduce((a, b) => a + b, 0) / cfs.length : null;
  const nLow = cfs.filter(x => x < IDC.low).length;
  const idHead = idMean == null ? "" : `<span class="idhead ${idMean >= IDC.hi ? "good" : idMean >= IDC.mid ? "warn" : "bad"}" title="Mean identity confidence over this team's tracked matches: how sure we are the tracked robot really is this team. ${ID_CAL}">Identity ${Math.round(idMean * 100)}%${nLow ? ` · ${nLow} shaky match${nLow > 1 ? "es" : ""}` : ""}</span>`;
  const navs = [["scoring", "Scoring"], ["shots", "Shot chart"], ["intake", "Intake"], ["auto", "Auto"], ["move", "Movement"], ["def", "Defense"], ["end", "Endgame"], ["rel", "Reliability"], ["matches", "Matches"]];

  const levels = EG.levels || {}, nL = Object.values(levels).reduce((a, b) => a + b, 0) || 1;
  const trend = (label, vals, f, color) => `<div class="trend"><span>${label}</span>${spark(vals, { color })}<em>${fmtV(vals.filter(has).slice(-1)[0], f)}</em></div>`;

  $app.innerHTML = `<div class="titlebar"><div><h1>${d.number} <span class="muted" style="font-weight:500">${esc(d.nickname || "")}</span></h1>
    <div class="sub">${d.rank ? `Rank ${d.rank} · ` : ""}${d.opr != null ? `OPR ${fx(d.opr)} · ` : ""}${CV.n} verified match${CV.n === 1 ? "" : "es"}${CV.of ? ` of ${CV.of} played` : ""}${d.tracked > CV.n ? ` <span class="muted small">(${d.tracked - CV.n} replay-only)</span>` : ""}
      ${HP ? ` · <span class="hp-pills"><span class="pill hp on${HP.on.thin ? " thin" : ""}"${tipX(`Teleop, while our hub is active (n=${HP.on.n} matches).`, true)}>Hub active: ${esc(HP.on.label)}</span> <span class="pill hp off${HP.off.thin ? " thin" : ""}"${tipX(`Teleop, while our hub is inactive (n=${HP.off.n} matches).`, true)}>Inactive: ${esc(HP.off.label)}</span></span>` : ""}
      · ${covPill(CV)} ${flagChips(SR)} · <a href="https://www.thebluealliance.com/team/${d.number}/${(e && e.year) || ""}" target="_blank" rel="noopener">TBA</a></div></div>
    ${typeof plCanEdit !== "function" || plCanEdit() ? `<button id="star" type="button" class="${starred ? "on" : ""}" aria-pressed="${starred}">${CS.icon(starred ? "starOn" : "star")}<span>${starred ? "Starred" : "Star"}</span></button>`
      : starred ? `<span id="star" class="on starmark" title="Starred on the team picklist (strategists change it)">${CS.icon("starOn")}<span>Starred</span></span>` : ""}</div>
    <nav class="jump">${navs.map(([k, l]) => `<a href="#" data-j="${k}">${l}</a>`).join("")}</nav>
    ${ag ? "" : `<div class="card empty">No quantitative analytics for this team yet.</div>`}
    ${dataBanner(scd)}
    <section class="card sect"><h2>Scouting truth ${CS.help(`<span class="hl">The same canonical numbers as Teams, Specs, Compare and Picklist.</span><span class="hl">Video: ${CV.n ? esc(label_(CV)) : "no verified video"}.</span>${(SR.flags || []).map(f => `<span class="hl">${esc(f.text)}</span>`).join("")}`, "Where these numbers come from")}</h2>
      <div class="sc-truth">
        <div><b>${has(RT.blend) ? fx(RT.blend) : "–"}<span class="sc-sd">±${fx(RT.sd, 0)}</span></b><span>Blend rating (pts/match)</span></div>
        <div><b>${fx(RT.epa)}</b><span>EPA, local as-of (TBA only)</span></div><div><b>${fx(d.opr)}</b><span>OPR (TBA)</span></div>
        <div><b>${svHtml(SV.made_pm)}</b><span>Made / match (video)</span></div><div><b>${svHtml(SV.acc, "p")}</b><span>Accuracy (video, shrunk)</span></div>
        <div><b>${SV.auto_made ? svHtml(SV.auto_made) : CV.n ? '<span class="muted">n/t</span>' : "–"}</b><span>Auto FUEL (video)</span></div>
        <div><b>${svHtml(SV.cycle_s)}</b><span>Cycle s (video)</span></div>
        <div><b>${SR.climb ? pct(SR.climb.rate) : "–"}</b><span>Climb, TBA (${SR.climb ? SR.climb.n : 0} quals)</span></div>
        <div><b>${!(SR.trend && SR.trend.tba) ? "–" : SR.trend.tba.verdict === "flat" ? `<span class="muted">no clear trend</span>` : `${SR.trend.tba.diff > 0 ? "+" : ""}${SR.trend.tba.diff}<span class="sc-sd">${SR.trend.tba.ci[0]}…${SR.trend.tba.ci[1]}</span>`}</b><span title="alliance score minus fouls drawn minus partners' blend, last 4 quals vs earlier; shown only when the 90% interval excludes 0${SR.trend && SR.trend.tba ? ` (now ${SR.trend.tba.diff}, ${SR.trend.tba.ci[0]}…${SR.trend.tba.ci[1]})` : ""}">TBA trend, last 4</span></div>
        <div><b>${SR.trend && SR.trend.blend ? spark(SR.trend.blend.map(x => x[1])) : "–"}</b><span>Blend over the event${SR.trend && SR.trend.blend ? ` (${fx(SR.trend.blend[0][1], 0)} → ${fx(SR.trend.blend.slice(-1)[0][1], 0)})` : ""}</span></div>
      </div>
      ${d.matches.some(m => !m.verified) ? `<div class="note">Verified matches only (${CV.n}) · ${esc(d.matches.filter(m => !m.verified).map(m => m.short).join(", "))} replay-only ${CS.help(tainted
        ? "Matches tracked without verified identity are replay-only: excluded from the shot chart, autos and trends; the intake map, heatmap and routine clusters are hidden because they mix them in."
        : "Every section below uses verified matches only. The listed matches were tracked without verified identity (v1 tracks or confidence under 60%) and are replay-only.", "Why some matches are replay-only")}</div>` : ""}</section>
    <section id="tpNotes" class="sect"></section>
    <div class="trends" style="${ms.filter(m => m.verified).length < 3 ? "display:none" : ""}">${trend("Accuracy", tr(a => a.scoring.accuracy), "p")}${trend("Made", tr(a => a.scoring.made), "0")}${trend("Attempts", tr(a => a.scoring.attempts), "0")}
      ${trend("Cycle s", tr(a => a.scoring.cycle_s), "1")}${trend("Dist m", tr(a => a.movement.distance_m), "0")}${trend("Defense s", tr(a => a.defense.time_s), "1")}</div>
    <div class="stackv">
    ${tainted ? section("scoring", "Scoring", `<div class="muted small">Per-team aggregates from the tracker file mix in unverified matches, so they are not shown; the canonical values are in Scouting truth above and the per-match rows (verified only) are in Matches below.</div>`) : ""}
    ${tainted ? "" : section("scoring", "Scoring", `<div class="tiles">
        ${SV.acc ? tile("Accuracy", { v: SV.acc.v, n: SV.acc.n }, "p", "", "canonical (same as every page): made / (made+missed), shrunk toward the event; raw " + fmtV(SV.acc.raw, "p")) : tile("Accuracy", has(S.accuracy_pooled) ? { ...(S.accuracy || {}), v: S.accuracy_pooled } : S.accuracy, "p", "", "made / attempted fuel (pooled over matches)")}
        ${SV.made_pm ? tile("Made / match" + (SV.made_pm.lower_bound ? " (≥)" : ""), { v: SV.made_pm.v, n: SV.made_pm.n }, "1", "", "canonical (same as every page): TBA-reconciled, shrunk; raw " + fmtV(SV.made_pm.raw)) : tile("Made / match", S.made, "1")}${tile("Attempts / match", S.attempts, "1")}${tile("Passes / match", S.passes, "1")}
        ${tile("Points / match", S.points, "1", "", "made into an ACTIVE hub")}${tile("Uncounted / match", S.uncounted, "1", "", "made into an inactive hub")}
        ${tile("Fire rate", S.fire_rate, "1", " /s", "balls per second inside a volley")}${tile("Volley size", S.volley_size, "0", "", "max balls in a volley ≈ hopper capacity")}
        ${tile("Volleys / match", S.volleys, "1")}${SV.cycle_s ? tile("Cycle time", { v: SV.cycle_s.v, n: SV.cycle_s.n }, "1", " s", "canonical (same as every page): median time between teleop volley starts") : tile("Cycle time", S.cycle_s, "1", " s", "time between volleys")}
        ${tile("Shot distance", S.dist_mean, "1", " m")}${tile("Shoot on move", S.on_move_rate, "p", "", "share of shots launched while moving")}
        ${tile("1st shot (auto)", S.first_shot_auto, "1", " s")}${tile("1st shot (teleop)", S.first_shot_teleop, "1", " s")}</div>
      <h3>By period <span class="muted small">(made / attempts per match)</span></h3>${S.by_period ? periodBars(S.by_period) : ""}`,
      "Aggregates are per-match means over tracked matches. n = matches the metric was available in; the bar is confidence (coverage × sample size).")}
    ${section("shots", "Shot chart", `<div class="chipbar sep"><div class="chips" id="sc-s" role="group" aria-label="Which shots"></div><div class="chips" id="sc-p" role="group" aria-label="Match period"></div><div class="chips" id="sc-m" role="group" aria-label="Display"></div></div><div id="shotmap"></div>
      <div class="legendline">${shotLegend()} <span class="muted small" id="sc-n"></span></div>`,
      `<span class="hl">Launch positions from the team's own end (own alliance wall on the left, our hub ringed).</span><span class="hl"><b>Hub shots</b> = made or missed at our hub. <b>Passes &amp; clears</b> = FUEL sent anywhere else: passes and feeds to a partner or the floor, and clears lobbed out of the far end near the opponent's hub (older data: misses that landed nearer the opponent's hub).</span><span class="hl">Density shows where the selected set is launched from (hub shots unless Passes &amp; clears is picked).</span>`)}
    ${section("intake", "Intake map", `<div id="intakemap"></div>
      <div class="tiles" style="margin-top:10px">${tile("Intakes / match", ag && ag.collection && ag.collection.intakes, "1")}${tile("Per minute", ag && ag.collection && ag.collection.per_min, "1")}
      ${["neutral", "depot", "outpost", "opp"].map(k => [k, ag && ag.collection && ag.collection.by_loc && ag.collection.by_loc[k]]).filter(([, x]) => has(val(x))).map(([k, x]) => tile("From " + k, x, "1")).join("")}</div>`, "Where floor fuel was picked up, from the team's own end.")}
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
    ${section("rel", "Reliability & hub phases", `<div class="grid2"><div class="tiles">${tile("Dead matches", has(RL.dead_matches) ? { v: RL.dead_matches, n: RL.n } : null, "0", "", "no movement for 15 s or more mid-match, not climbing")}
        ${tile("Avg dead time", RL.mean_dead_s, "1", " s")}${tile("Low-coverage matches", has(RL.low_coverage_matches) ? RL.low_coverage_matches : null, "0")}</div>
      <div><h3 class="hp-h3">Hub active vs inactive ${CS.help(hubHelp(), "About hub active / inactive")}</h3>${HP ? hubPhaseBlock(ag.hub) : `<div class="muted small">${tainted ? "Hidden: this team's aggregates still mix in unverified matches." : "No hub active / inactive split for this team yet: it needs verified video of its teleop."}</div>`}</div></div>`)}
    ${section("matches", "Matches", `<div class="tablewrap"><table><thead><tr><th>Match</th><th>All.</th><th class="num" data-pri="800">Made/Att</th><th class="num" data-pri="700">Acc</th><th class="num" data-pri="200">Fire /s</th><th class="num" data-pri="190">Volley</th><th class="num" data-pri="300">Cycle s</th>
      <th class="num" data-pri="180">Intakes</th>${hasRoutine ? '<th data-pri="120">Auto</th>' : ""}<th class="num" data-pri="500">Auto made</th><th data-pri="450">Climb</th><th class="num" data-pri="170">Def s</th><th class="num" data-pri="160">Cont.</th><th class="num" data-pri="150">Pins</th><th class="num" data-pri="140">Dist m</th><th title="verified identity (v2, ≥ 60%)" data-pri="250">Verified</th><th data-pri="400">Flags</th></tr></thead>
      <tbody>${ms.map((m, i) => { const a = an[i]; const lo = mcf(m) != null && mcf(m) < IDC.low; if (!m.verified) return `<tr class="sc-dis"><td><a href="#/${ev}/m/${m.match}">${esc(m.short)}</a></td><td class="${m.alliance[0]}">${m.alliance[0].toUpperCase()}</td><td colspan="${15 - RC}" class="muted wrapc" title="identity not verified (v1 tracks or confidence &lt; 60%): replay only, feeds no numbers">not verified · replay only</td></tr>`;
        return `<tr><td><a href="#/${ev}/m/${m.match}">${esc(m.short)}</a></td><td class="${m.alliance[0]}">${m.alliance[0].toUpperCase()}</td>` + (a ? `
        <td class="num">${ma(a.scoring.made, a.scoring.attempts)}</td><td class="num">${fmtV(a.scoring.accuracy, "p")}</td><td class="num">${fmtV(a.scoring.fire_rate)}</td><td class="num">${fmtV(a.scoring.volley_size, "0")}</td><td class="num">${fmtV(a.scoring.cycle_s)}</td>
        <td class="num">${a.collection.intakes ?? "–"}</td>${hasRoutine ? `<td>${esc(a.auto.routine || "–")}</td>` : ""}<td class="num">${ma(a.auto.made, a.auto.shots)}</td><td>${a.endgame.level ? "L" + a.endgame.level : a.endgame.attempted ? "failed" : "–"}</td>
        <td class="num">${fmtV(a.defense.time_s)}</td><td class="num">${a.defense.contacts}</td><td class="num">${a.defense.pins}</td><td class="num">${fmtV(a.movement.distance_m, "0")}</td><td>✓</td>
        <td>${a.reliability.dead ? '<span class="pill failed">dead</span>' : ""}${a.reliability.low_coverage ? '<span class="pill no_video">low cov.</span>' : ""}</td>` : `<td colspan="${13 - RC}" class="muted wrapc">no analytics</td><td>✓</td>`) + `</tr>`; }).join("")
        || `<tr><td colspan="16" class="empty">No tracked matches yet</td></tr>`}</tbody></table></div>`)}
    </div>`;

  if (CS.notes) CS.notes.mount(document.getElementById("tpNotes"), ev, { team: tk });      // notes.js
  document.querySelectorAll(".jump a").forEach(a => a.onclick = ev_ => { ev_.preventDefault(); const s = document.getElementById("s-" + a.dataset.j); s && s.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }); });
  const starB = document.querySelector("button#star");
  if (starB) starB.onclick = async e_ => {     // shared picklist (event.js) when signed in; read-only users get a plain mark instead
    const p = typeof plLoad === "function" ? await plLoad(ev) : lsGet("cyberscout:picks:" + ev, {}); p.stars = p.stars || [];
    const i = p.stars.indexOf(tk); i >= 0 ? p.stars.splice(i, 1) : p.stars.push(tk);
    // a star that doesn't reach the server says so (the Picklist shows the details and keeps the change until then)
    const told = {}, open = () => { location.hash = `#/${ev}/picklist`; };
    const status = s => { if (told[s.k] || !CS.toast || !document.body.contains(starB)) return; told[s.k] = 1;
      if (s.k === "error") CS.toast(`Star not saved: ${s.why}`, { kind: "bad", action: "Open picklist", fn: open });
      else if (s.k === "conflict") CS.toast(`Star not saved: ${s.cur && s.cur.by && s.cur.by.display_name || "someone"} changed the picklist after this page loaded. Pick which list to keep on the Picklist.`, { kind: "warn", action: "Open picklist", fn: open }); };
    typeof plSave === "function" ? plSave(ev, p, { status }) : lsSet("cyberscout:picks:" + ev, p);
    const b = e_.currentTarget; b.classList.toggle("on", i < 0); b.setAttribute("aria-pressed", i < 0); b.innerHTML = `${CS.icon(i < 0 ? "starOn" : "star")}<span>${i < 0 ? "Starred" : "Star"}</span>`;
  };

  // shot chart
  let smode = "both", sper = "all", sshow = "all";
  const shots = d.shots_map || [];
  const showChips = () => { const c = shotCounts(shots, sper); chipRow(document.getElementById("sc-s"), SHOW_CHIPS.map(([k, l]) => [k, `${l} <span class="chipn">${c[k]}</span>`]), sshow, k => { sshow = k; SC.redraw(); }); };
  chipRow(document.getElementById("sc-p"), [["all", "All"], ["auto", "Auto"], ["tele", "Teleop"]], sper, k => { sper = k; showChips(); SC.redraw(); });
  showChips();
  chipRow(document.getElementById("sc-m"), [["both", "Points + density"], ["pts", "Points"], ["heat", "Density"]], smode, k => { smode = k; SC.redraw(); });
  const SC = liveCanvas(document.getElementById("shotmap"), F => { const n = drawShotChart(F, shots, smode, sper, sshow); document.getElementById("sc-n").textContent = `${n} ${sshow === "out" ? (n === 1 ? "pass / clear" : "passes & clears") : n === 1 ? "shot" : "shots"}`; });
  liveCanvas(document.getElementById("intakemap"), F => drawIntakeMap(F, d.intakes_map || []));

  // autos / routines
  const routines = d.routines || [], autos = d.autos || [];
  let selR = routines.length ? routines[0].id : null, showAll = false;
  const on = autos.map(() => true), chips = document.getElementById("autochips");
  chips.innerHTML = autos.map((a, i) => `<button type="button" class="chip on" data-i="${i}" aria-pressed="true"><i class="sw" style="background:${PALETTE[i % PALETTE.length]}" aria-hidden="true"></i>${esc(a.short)}${a.routine ? " · " + esc(a.routine) : ""}</button>`).join("");
  chips.querySelectorAll(".chip").forEach(ch => ch.onclick = () => { const i = +ch.dataset.i; on[i] = !on[i]; ch.classList.toggle("on", on[i]); ch.setAttribute("aria-pressed", on[i]); AC.redraw(); });
  const AC = liveCanvas(document.getElementById("autocv"), F => {
    F.bg(.6);
    if (showAll || !routines.length) autos.forEach((a, i) => { if (on[i]) drawPath(F, a.xy, PALETTE[i % PALETTE.length], { alpha: routines.length ? .35 : .9, obs: a.obs }); });
    routines.forEach((r, i) => { if (r.id === selR) drawPath(F, r.xy, "#ffffff", { w: 1.8, alpha: .95 }); });
  });
  document.getElementById("allautos").onchange = e_ => { showAll = e_.target.checked; AC.redraw(); };
  const rdiv = document.getElementById("routines");
  const drawR = () => {
    rdiv.innerHTML = routines.length ? `<div class="tablewrap"><table><thead><tr><th>Routine</th><th class="num">Used</th><th class="num" data-pri="300">Shots</th><th class="num" data-pri="400">Made</th><th class="num" data-pri="200">Neutral</th><th class="num" data-pri="100">Consist.</th></tr></thead><tbody>
      ${routines.map(r => `<tr class="${r.id === selR ? "sel" : ""} clk" data-r="${r.id}"><td><b>${esc(r.name || r.id)}</b><div class="small muted">${r.start_xy ? `start ${spotTxt(r.start_xy)} · ` : ""}${esc((r.matches || []).join(", "))}</div></td>
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
