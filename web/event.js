"use strict";
// Event page panes: quantitative team table, Compare, Picklist. Uses globals from app.js / quant.js.

/** derive the compact per-team summary (event.json teams[].q) from a team file's aggregate analytics */
function summarize(t) {
  const a = t.analytics; if (!a) return null;
  const S = a.scoring || {}, n = a.n;
  return { n, acc: S.accuracy_pooled ?? val(S.accuracy), made: val(S.made), att: val(S.attempts), fire: val(S.fire_rate), volley: val(S.volley_size), cycle: val(S.cycle_s),
    intakes: val(a.collection && a.collection.intakes), climb: a.endgame && a.endgame.climb_rate, defs: val(a.defense && a.defense.time_s), pins: val(a.defense && a.defense.pins),
    automade: val(a.auto && a.auto.made), dead: a.reliability ? a.reliability.dead_matches / (a.reliability.n || n || 1) : null, role: a.role && val(a.role),
    speed: val(a.movement && a.movement.mean_speed), dist: val(a.movement && a.movement.distance_m), conf: mc(S.accuracy) };
}
const eventRows = {};
function loadRows(d, ev, onProgress) {
  if (eventRows[ev] && eventRows[ev].n === d.updated) return eventRows[ev].p;
  const p = Promise.all(d.teams.map(async t => {
    let q = t.q || null;
    if (!q) { try { q = summarize(await getTeam(ev, t.key)); } catch (e) { } }
    onProgress && onProgress();
    return { t, q };
  }));
  eventRows[ev] = { n: d.updated, p }; return p;
}

// key, label, getter, format, higher-is-better (1 / -1), default visible, tooltip
const QCOLS = [
  ["acc", "Acc", q => q.acc, "p", 1, 1, "shot accuracy (made / attempted)"], ["made", "Made/m", q => q.made, "1", 1, 1, "fuel made per match"],
  ["att", "Att/m", q => q.att, "1", 1, 0, "shots attempted per match"], ["fire", "Fire /s", q => q.fire, "1", 1, 1, "balls per second within a volley"],
  ["volley", "Volley", q => q.volley, "0", 1, 1, "max balls per volley (hopper)"], ["cycle", "Cycle s", q => q.cycle, "1", -1, 1, "time between volleys (lower is better)"],
  ["intakes", "Intakes/m", q => q.intakes, "1", 1, 0, "ground pickups per match"], ["automade", "Auto made", q => q.automade, "1", 1, 1, "fuel made in auto per match"],
  ["climb", "Climb", q => q.climb, "p", 1, 1, "share of matches with a climb"], ["defs", "Def s", q => q.defs, "1", 1, 1, "seconds spent defending per match"],
  ["pins", "Pins/m", q => q.pins, "1", 1, 0, "pins per match"], ["dead", "Dead", q => q.dead, "p", -1, 1, "share of matches disabled (lower is better)"],
  ["speed", "Speed", q => q.speed, "2", 1, 0, "mean moving speed m/s"], ["dist", "Dist m", q => q.dist, "0", 1, 0, "distance per match"],
  ["role", "Role", q => q.role, "s", 0, 1, "classified role"], ["n", "n", q => q.n, "0", 1, 1, "matches the stats are based on"]];

function teamsTable(el, d, ev) {
  const vis = lsGet("autoscout:cols", null) || Object.fromEntries(QCOLS.map(c => [c[0], !!c[5]]));
  const picks = lsGet("autoscout:picks:" + ev, {}), stars = new Set(picks.stars || []);
  let sk = "rank", dir = 1, rows = null, showCfg = false;
  const base = [["rank", "Rank", r => r.t.rank ?? "–", true, r => r.t.rank], ["number", "Team", r => `<a class="team" href="#/${ev}/t/${r.t.key}">${r.t.number}</a>${stars.has(r.t.key) ? ' <span class="starm">★</span>' : ""}`, false, r => r.t.number],
    ["nickname", "Name", r => esc(r.t.nickname), false, r => r.t.nickname || ""], ["opr", "OPR", r => fx(r.t.opr), true, r => r.t.opr]];
  const cols = () => [...base, ...QCOLS.filter(c => vis[c[0]]).map(c => [c[0], c[1], r => r.q ? (c[3] === "s" ? esc(r.q.role || "–") : fmtV(c[2](r.q), c[3])) : "–", c[3] !== "s", r => r.q ? c[2](r.q) : null, c[6]])];
  const render = () => {
    const cs = cols(), arr = [...(rows || d.teams.map(t => ({ t, q: t.q || null })))];
    const sc = cs.find(c => c[0] === sk) || cs[0];
    arr.sort((a, b) => { const x = sc[4](a), y = sc[4](b); if (x == null && y == null) return a.t.number - b.t.number; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir; });
    el.innerHTML = `<div class="controls"><button id="cfg" class="${showCfg ? "on" : ""}">Columns</button><span class="muted small">${rows ? "" : "loading team stats… "}tap a header to sort</span></div>
      ${showCfg ? `<div class="cfg">${QCOLS.map(c => `<label title="${esc(c[6])}"><input type="checkbox" data-c="${c[0]}" ${vis[c[0]] ? "checked" : ""}> ${c[1]}</label>`).join("")}</div>` : ""}
      <div class="tablewrap"><table><thead><tr>${cs.map(([k, t, , n, , tip]) => `<th data-k="${k}" title="${esc(tip || "")}" class="${n ? "num" : ""}">${t}${sk === k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${arr.map(r => `<tr>${cs.map(([, , f, n]) => `<td class="${n ? "num" : ""}">${f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="note">Per-match means over tracked matches (n). Accuracy is pooled made / attempted; Cycle is time between volleys; Dead is the share of matches the robot stopped
      mid-match. Hover a header for its definition. Open a team for confidence and detail.</div>`;
    el.querySelector("#cfg").onclick = () => { showCfg = !showCfg; render(); };
    el.querySelectorAll(".cfg input").forEach(i => i.onchange = () => { vis[i.dataset.c] = i.checked; lsSet("autoscout:cols", vis); render(); });
    el.querySelectorAll("th").forEach(th => th.onclick = () => {
      const k = th.dataset.k; if (sk === k) dir = -dir; else { sk = k; const q = QCOLS.find(c => c[0] === k); dir = ["rank", "number", "nickname", "cycle", "dead"].includes(k) ? 1 : -1; if (q && q[4] === -1) dir = 1; }
      render();
    });
  };
  render();
  loadRows(d, ev).then(r => { rows = r; if (el.isConnected) render(); });
}

// ------------------------------------------------------------------ compare
async function comparePane(el, d, ev, arg) {
  let sel = (arg ? arg.split(",") : lsGet("autoscout:cmp:" + ev, [])).filter(k => d.teams.some(t => t.key === k || t.key === "frc" + k)).map(k => k.startsWith("frc") ? k : "frc" + k).slice(0, 3);
  const opts = d.teams.map(t => `<option value="${t.key}">${t.number} ${esc(t.nickname || "")}</option>`).join("");
  el.innerHTML = `<div class="controls">${[0, 1, 2].map(i => `<select data-i="${i}"><option value="">${i < 2 ? "choose team" : "(optional) third team"}</option>${opts}</select>`).join("")}</div>
    <div id="cmpbody" class="cmp"></div>`;
  const body = el.querySelector("#cmpbody");
  const selects = [...el.querySelectorAll("select")];
  selects.forEach((s, i) => { s.value = sel[i] || ""; s.onchange = () => { sel = selects.map(x => x.value).filter(Boolean); lsSet("autoscout:cmp:" + ev, sel); build(); }; });
  async function build() {
    if (!sel.length) { body.innerHTML = `<div class="empty">Pick 2–3 teams to compare shot charts, autos and key metrics side by side.</div>`; return; }
    const ts = await Promise.all(sel.map(k => getTeam(ev, k)));
    body.style.setProperty("--n", ts.length);
    const rows = [["Accuracy", t => t.analytics && t.analytics.scoring.accuracy_pooled, "p"], ["Made / match", t => val(t.analytics?.scoring.made), "1"], ["Fire rate /s", t => val(t.analytics?.scoring.fire_rate), "1"],
      ["Volley size", t => val(t.analytics?.scoring.volley_size), "0"], ["Cycle s", t => val(t.analytics?.scoring.cycle_s), "1"], ["Auto made", t => val(t.analytics?.auto.made), "1"],
      ["Climb rate", t => t.analytics?.endgame.climb_rate, "p"], ["Defense s", t => val(t.analytics?.defense.time_s), "1"], ["Dead", t => t.analytics ? t.analytics.reliability.dead_matches / t.analytics.n : null, "p"],
      ["Mean speed", t => val(t.analytics?.movement.mean_speed), "2"], ["Role", t => t.analytics && val(t.analytics.role), "s"]];
    body.innerHTML = ts.map((t, i) => `<div class="cmpcol card"><h2><a href="#/${ev}/t/${t.key}">${t.number}</a> <span class="muted" style="font-weight:500">${esc(t.nickname || "")}</span></h2>
      <table class="mini">${rows.map(([l, f, fm]) => `<tr><td>${l}</td><td class="num">${fm === "s" ? esc(f(t) || "–") : fmtV(f(t), fm)}</td></tr>`).join("")}</table>
      <h3>Shot chart</h3><div id="cs${i}"></div><h3>Autos</h3><div id="ca${i}"></div>
      <div class="small muted">${(t.routines || []).map(r => `${esc(r.id)} ${r.n}/${r.of}`).join(" · ")}</div></div>`).join("");
    ts.forEach((t, i) => {
      liveCanvas(body.querySelector("#cs" + i), F => drawShotChart(F, t.shots_map || [], "both", "all"));
      liveCanvas(body.querySelector("#ca" + i), F => { F.bg(.6); (t.routines || []).forEach((r, j) => drawPath(F, r.xy, PALETTE[j % PALETTE.length], { alpha: .5 + .5 * (r.freq ?? 1), w: 1 + (r.freq ?? 0) })); });
    });
  }
  build();
}

// ------------------------------------------------------------------ picklist
const PL_DEFAULT = { made: 3, acc: 2, climb: 1.5, automade: 1, cycle: 0.5, dead: 2, defs: 0, fire: 0, volley: 0, intakes: 0, speed: 0 };
async function picklistPane(el, d, ev) {
  el.innerHTML = `<div class="loading">Loading team stats…</div>`;
  const rows = (await loadRows(d, ev)).filter(r => r.q);
  const st = Object.assign({ stars: [], dnp: [], weights: { ...PL_DEFAULT } }, lsGet("autoscout:picks:" + ev, {}));
  st.weights = { ...PL_DEFAULT, ...st.weights };
  const save = () => lsSet("autoscout:picks:" + ev, st);
  const keys = QCOLS.filter(c => c[4] !== 0 && c[0] !== "n" && c[0] !== "att" && c[0] !== "pins" && c[0] !== "dist");
  const range = {}; keys.forEach(c => { const v = rows.map(r => c[2](r.q)).filter(has); range[c[0]] = [Math.min(...v), Math.max(...v)]; });
  let only = "all";
  function score(r) {
    let s = 0, w = 0;
    for (const c of keys) {
      const wt = st.weights[c[0]] || 0; if (!wt) continue; const v = c[2](r.q), [lo, hi] = range[c[0]];
      if (!has(v)) continue; let z = hi > lo ? (v - lo) / (hi - lo) : .5; if (c[4] < 0) z = 1 - z; s += wt * z; w += Math.abs(wt);
    }
    return w ? 100 * s / w : 0;
  }
  const render = () => {
    const list = rows.map(r => ({ r, s: score(r) })).sort((a, b) => b.s - a.s);
    const shown = list.filter(x => only === "all" || (only === "star" && st.stars.includes(x.r.t.key)) || (only === "hide" ? true : false) || (only === "avail" && !st.dnp.includes(x.r.t.key)));
    const show = cs => cs.filter(c => st.weights[c[0]]);
    el.innerHTML = `<div class="plwrap"><div class="plw card"><h2>Weights</h2><div class="note" style="margin:0 0 8px">Score = weighted, min-max-normalised blend of metrics (0–100). 0 = ignore. "Lower is better" metrics are inverted.</div>
      ${keys.map(c => `<label class="wrow" title="${esc(c[6])}"><span>${c[1]}${c[4] < 0 ? " ↓" : ""}</span><input type="range" min="0" max="5" step="0.5" value="${st.weights[c[0]] || 0}" data-k="${c[0]}"><b>${st.weights[c[0]] || 0}</b></label>`).join("")}
      <div class="controls"><button id="plreset">Reset</button><button id="plcopy">Copy list</button></div></div>
      <div class="plt card"><div class="chips" id="plf"></div><div class="tablewrap"><table><thead><tr><th class="num">#</th><th></th><th>Team</th><th>Name</th><th class="num">Score</th>${show(keys).map(c => `<th class="num">${c[1]}</th>`).join("")}<th>Role</th><th></th></tr></thead>
      <tbody>${shown.map((x, i) => { const k = x.r.t.key, star = st.stars.includes(k), no = st.dnp.includes(k); return `<tr class="${no ? "dnp" : ""}"><td class="num">${i + 1}</td>
        <td><button class="star ${star ? "on" : ""}" data-s="${k}" title="star">${star ? "★" : "☆"}</button></td><td><a class="team" href="#/${ev}/t/${k}">${x.r.t.number}</a></td><td>${esc(x.r.t.nickname || "")}</td>
        <td class="num"><b>${x.s.toFixed(0)}</b><div class="bartiny"><i style="width:${x.s}%"></i></div></td>${show(keys).map(c => `<td class="num">${fmtV(c[2](x.r.q), c[3])}</td>`).join("")}
        <td>${esc(x.r.q.role || "")}</td><td><button class="star ${no ? "on" : ""}" data-n="${k}" title="do not pick">⊘</button></td></tr>`; }).join("")}</tbody></table></div></div></div>`;
    chipRow(el.querySelector("#plf"), [["all", "All"], ["star", "Starred"], ["avail", "Hide ⊘"]], only, k => { only = k; render(); });
    el.querySelectorAll(".wrow input").forEach(i => i.oninput = () => { st.weights[i.dataset.k] = +i.value; save(); i.nextElementSibling.textContent = i.value; clearTimeout(render.t); render.t = setTimeout(render, 120); });
    el.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { const k = b.dataset.s, i = st.stars.indexOf(k); i >= 0 ? st.stars.splice(i, 1) : st.stars.push(k); save(); render(); });
    el.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { const k = b.dataset.n, i = st.dnp.indexOf(k); i >= 0 ? st.dnp.splice(i, 1) : st.dnp.push(k); save(); render(); });
    el.querySelector("#plreset").onclick = () => { st.weights = { ...PL_DEFAULT }; save(); render(); };
    el.querySelector("#plcopy").onclick = e => { const txt = shown.map((x, i) => `${i + 1}. ${x.r.t.number} ${x.r.t.nickname || ""} (${x.s.toFixed(0)})${st.stars.includes(x.r.t.key) ? " *" : ""}`).join("\n"); try { navigator.clipboard.writeText(txt); e.target.textContent = "Copied"; } catch (x) { e.target.textContent = "Copy failed"; } };
  };
  render();
}
