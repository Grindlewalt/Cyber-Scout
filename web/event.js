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

// Canonical team metrics come from scout.json (one value per metric, same on every page). key, label, getter(row)
// -> metric {v, raw, n, tier, lower_bound} | number, format, higher-is-better (1 / -1 / 0), default visible, tooltip.
const VID = (r, k) => r.s && r.s.video ? r.s.video[k] : null;
const QCOLS = [
  ["blend", "Blend", r => r.s?.rating?.blend, "1", 1, 1, "model rating: expected points/match (TBA results + earlier events + verified video), schedule-neutral"],
  ["epa", "EPA", r => r.s?.rating?.epa, "1", 1, 1, "EPA, local as-of (TBA results only)"],
  ["video", "Video", r => r.s?.coverage?.n ?? 0, "cov", 1, 1, "verified robot-video matches / matches played (v2 tracker, identity ≥ 60%)"],
  ["made", "Made/m", r => VID(r, "made_pm"), "1", 1, 1, "FUEL made per match on verified video (shrunk; ≥ = alliance FUEL under-credited, a floor)"],
  ["acc", "Acc", r => VID(r, "acc"), "p", 1, 1, "accuracy made / (made+missed), shrunk toward the event (20 pseudo-shots)"],
  ["automade", "Auto", r => VID(r, "auto_made"), "1", 1, 1, "auto FUEL per match on verified video (hidden when it contradicts TBA)"],
  ["cycle", "Cycle s", r => VID(r, "cycle_s"), "1", -1, 1, "median time between volley starts (lower is better)"],
  ["hopper", "Volley p90", r => VID(r, "hopper"), "0", 1, 0, "90th percentile volley size ≈ hopper"],
  ["fire", "Fire /s", r => VID(r, "fire"), "1", 1, 0, "balls per second within a volley"],
  ["climb", "Climb", r => r.s?.climb?.rate, "p", 1, 1, "endgame climb rate from TBA per robot (every played qual)"],
  ["dead", "Dead", r => VID(r, "dead"), "pd", -1, 0, "share of verified matches disabled (shown only with ≥ 3 verified matches)"],
  ["speed", "p95 m/s", r => VID(r, "speed_p95"), "2", 1, 0, "95th percentile moving speed"],
  ["defs", "Def. played", r => VID(r, "def_played"), "0", 1, 0, "defense episodes played (verified matches)"]];
const qv = x => x && typeof x === "object" ? (x.n ? x.v : null) : x;
function qcell(c, r) {
  const x = c[2](r);
  if (c[3] === "cov") return covPill(r.s?.coverage, { short: true });
  if (c[3] === "pd") return x && x.n >= 3 ? fmtV(x.v, "p") + `<span class="sc-sd">n=${x.n}</span>` : `<span class="muted">–</span>`;
  if (x && typeof x === "object") return svHtml(x, c[3] === "p" ? "p" : c[3]);
  if (c[0] === "blend" && has(x)) return `${fmtV(x, "1")}<span class="sc-sd">±${fmtV(r.s.rating.sd, "0")}</span>`;
  return fmtV(x, c[3]);
}
async function scoutRows(d, ev, ts) {
  let sd;
  if (ts) { try { sd = await api(`${ev}/scout/${ts}.json`); sd.by = Object.fromEntries(sd.teams.map(t => [t.team, t])); } catch (e) { sd = null; } }
  else sd = await scoutData(ev);
  // replay: rank / OPR as of then (scout snapshot), never the end-of-event values from event.json
  return { sd, rows: d.teams.map(t => { const s = sd && sd.by[t.key] || null; return { t: ts ? { ...t, rank: s?.rank ?? null, opr: s?.rating?.opr ?? null } : t, s }; }) };
}

function teamsTable(el, d, ev) {
  const vis = lsGet("cyberscout:cols2", null) || Object.fromEntries(QCOLS.map(c => [c[0], !!c[5]]));
  const picks = lsGet("cyberscout:picks:" + ev, {}), stars = new Set(picks.stars || []);
  let sk = "blend", dir = -1, data = null, showCfg = false;
  const base = [["rank", "Rank", r => r.t.rank ?? "–", true, r => r.t.rank], ["number", "Team", r => `<a class="team" href="#/${ev}/t/${r.t.key}">${r.t.number}</a>${stars.has(r.t.key) ? ' <span class="starm">★</span>' : ""}`, false, r => r.t.number],
    ["nickname", "Name", r => esc(r.t.nickname), false, r => r.t.nickname || ""], ["opr", "OPR", r => fx(r.t.opr), true, r => r.t.opr, "TBA OPR (end of event)"]];
  const cols = () => [...base, ...QCOLS.filter(c => vis[c[0]]).map(c => [c[0], c[1], r => qcell(c, r), true, r => qv(c[2](r)), c[6]]),
    ["flags", "", r => flagChips(r.s, ["auto_not_tracked", "video_higher", "video_lower"]), false, () => null]];
  const render = () => {
    const cs = cols(), arr = [...(data ? data.rows : d.teams.map(t => ({ t, s: null })))];
    const sc = cs.find(c => c[0] === sk) || cs[0];
    arr.sort((a, b) => { const x = sc[4](a), y = sc[4](b); if (x == null && y == null) return a.t.number - b.t.number; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir; });
    el.innerHTML = `${dataBanner(data && data.sd)}<div class="controls"><button id="cfg" class="${showCfg ? "on" : ""}">Columns</button><span class="muted small">${data ? "" : "loading… "}tap a header to sort · grey italic = fewer than 3 verified matches · hover a value for raw (n)</span></div>
      ${showCfg ? `<div class="cfg">${QCOLS.map(c => `<label title="${esc(c[6])}"><input type="checkbox" data-c="${c[0]}" ${vis[c[0]] ? "checked" : ""}> ${c[1]}</label>`).join("")}</div>` : ""}
      <div class="tablewrap"><table><thead><tr>${cs.map(([k, t, , n, , tip]) => `<th data-k="${k}" title="${esc(tip || "")}" class="${n ? "num" : ""}">${t}${sk === k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${arr.map(r => `<tr>${cs.map(([, , f, n]) => `<td class="${n ? "num" : ""}">${f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="note">Blend ± sd and EPA: points per match. Video columns use verified matches only (v2 tracker, identity ≥ 60%); v1 tracks feed nothing but the replay.
      Values are shrunk toward the event (hover for the raw value and n). ≥ marks a floor: more than 20% of that alliance's FUEL was not credited to any robot, so low video numbers are not evidence of a weak robot.
      Climb is from TBA for every played qual.</div>`;
    el.querySelector("#cfg").onclick = () => { showCfg = !showCfg; render(); };
    el.querySelectorAll(".cfg input").forEach(i => i.onchange = () => { vis[i.dataset.c] = i.checked; lsSet("cyberscout:cols2", vis); render(); });
    el.querySelectorAll("th").forEach(th => th.onclick = () => {
      const k = th.dataset.k; if (k === "flags") return; if (sk === k) dir = -dir; else { sk = k; const q = QCOLS.find(c => c[0] === k); dir = ["rank", "number", "nickname"].includes(k) ? 1 : -1; if (q && q[4] === -1) dir = 1; }
      render();
    });
  };
  render();
  scoutRows(d, ev).then(r => { data = r; if (el.isConnected) render(); });
}

// ------------------------------------------------------------------ compare
async function comparePane(el, d, ev, arg) {
  let sel = (arg ? arg.split(",") : lsGet("cyberscout:cmp:" + ev, [])).filter(k => d.teams.some(t => t.key === k || t.key === "frc" + k)).map(k => k.startsWith("frc") ? k : "frc" + k).slice(0, 3);
  const opts = d.teams.map(t => `<option value="${t.key}">${t.number} ${esc(t.nickname || "")}</option>`).join("");
  el.innerHTML = `<div class="controls">${[0, 1, 2].map(i => `<select data-i="${i}"><option value="">${i < 2 ? "choose team" : "(optional) third team"}</option>${opts}</select>`).join("")}</div>
    <div id="cmpbody" class="cmp"></div>`;
  const body = el.querySelector("#cmpbody");
  const selects = [...el.querySelectorAll("select")];
  selects.forEach((s, i) => { s.value = sel[i] || ""; s.onchange = () => { sel = selects.map(x => x.value).filter(Boolean); lsSet("cyberscout:cmp:" + ev, sel); build(); }; });
  async function build() {
    if (!sel.length) { body.innerHTML = `<div class="empty">Pick 2–3 teams to compare shot charts, autos and key metrics side by side.</div>`; return; }
    const [ts, sd] = await Promise.all([Promise.all(sel.map(k => getTeam(ev, k))), scoutData(ev)]);
    body.style.setProperty("--n", ts.length);
    const S = t => (sd && sd.by[t.key]) || {}, V = (t, k) => (S(t).video || {})[k];
    const rows = [["Blend ± sd", t => S(t).rating?.blend != null ? `${fx(S(t).rating.blend)}<span class="sc-sd">±${fx(S(t).rating.sd, 0)}</span>` : "–"],
      ["EPA (local as-of)", t => fx(S(t).rating?.epa)], ["OPR", t => fx(t.opr)], ["Video", t => covPill(S(t).coverage)],
      ["Made / match", t => svHtml(V(t, "made_pm"))], ["Accuracy", t => svHtml(V(t, "acc"), "p")], ["Auto FUEL", t => V(t, "auto_made") ? svHtml(V(t, "auto_made")) : flagChips(S(t), ["auto_not_tracked"]) || "–"],
      ["Cycle s", t => svHtml(V(t, "cycle_s"))], ["Volley p90", t => svHtml(V(t, "hopper"), "0")], ["Climb (TBA)", t => S(t).climb ? `${pct(S(t).climb.rate)}<span class="sc-sd">${S(t).climb.n} m</span>` : "–"],
      ["Flags", t => flagChips(S(t)) || "–"]];
    const vids = new Set(sd ? sd.verified_matches.map(shortKey) : []);
    body.innerHTML = ts.map((t, i) => `<div class="cmpcol card"><h2><a href="#/${ev}/t/${t.key}">${t.number}</a> <span class="muted" style="font-weight:500">${esc(t.nickname || "")}</span></h2>
      <table class="mini">${rows.map(([l, f]) => `<tr><td>${l}</td><td class="num">${f(t)}</td></tr>`).join("")}</table>
      <h3>Shot chart</h3><div id="cs${i}"></div><h3>Autos</h3><div id="ca${i}"></div>
      <div class="small muted">verified matches only: ${esc((S(t).coverage?.keys || []).map(shortKey).join(", ") || "none")}</div></div>`).join("");
    ts.forEach((t, i) => {
      liveCanvas(body.querySelector("#cs" + i), F => drawShotChart(F, (t.shots_map || []).filter(p => vids.has(p.m)), "both", "all"));
      liveCanvas(body.querySelector("#ca" + i), F => { F.bg(.6); (t.autos || []).filter(a => vids.has(a.short) && (S(t).coverage?.keys || []).map(shortKey).includes(a.short)).forEach((a, j) => drawPath(F, a.xy, PALETTE[j % PALETTE.length], { alpha: .8, obs: a.obs })); });
    });
  }
  build();
}

// ------------------------------------------------------------------ picklist
// Storage: one shared picklist per event on the server (app/v1/events/<ev>/picklists/main; strategists and admins
// edit, everyone signed in reads), cached in localStorage under the old key (teamsTable / quant.js read the stars
// from there). No server (legacy static site) or not allowed to edit -> localStorage only, as before.
const PL_NAME = "main", plMeta = {}, plTimers = {};
const plKey = ev => "cyberscout:picks:" + ev;
const plShared = () => !!(window.CS && CS.platform && CS.user);
async function plLoad(ev) {
  if (!plShared()) return lsGet(plKey(ev), {});
  try {
    const r = await CS.app("GET", `events/${ev}/picklists/${PL_NAME}`);
    plMeta[ev] = { version: r.version, by: r.updated_by, at: r.updated_at };
    lsSet(plKey(ev), r.data);
    return r.data;
  } catch (e) {
    if (e.status === 404) plMeta[ev] = { version: null };      // none yet: the first save creates it from this browser's list
    return lsGet(plKey(ev), {});
  }
}
function plSave(ev, st, onConflict) {
  lsSet(plKey(ev), st);
  if (!plShared() || !CS.can("strategist")) return;
  clearTimeout(plTimers[ev]);
  plTimers[ev] = setTimeout(async () => {
    const m = plMeta[ev] || {};
    try {
      const r = await CS.app("PUT", `events/${ev}/picklists/${PL_NAME}`, { data: st, base_version: m.version ?? undefined });
      plMeta[ev] = { version: r.version, by: r.updated_by, at: r.updated_at };
    } catch (e) {
      const cur = e.status === 409 && e.data && e.data.detail && e.data.detail.current;
      if (cur) {        // someone else saved first: take theirs (re-apply your change if you still want it)
        plMeta[ev] = { version: cur.version, by: cur.updated_by, at: cur.updated_at };
        lsSet(plKey(ev), cur.data);
        onConflict && onConflict(cur.data, cur.updated_by);
      } else console.warn("picklist not saved to the server:", e.message);
    }
  }, 500);
}
const PL_DEFAULT = { blend: 4, made: 0, acc: 0, automade: 0, cycle: 0, climb: 0, defs: 0 };
const PL_KEYS = [["blend", "Blend", r => r.s?.rating?.blend, 1], ["made", "Video made/m", r => qv(VID(r, "made_pm")), 1], ["acc", "Video acc", r => qv(VID(r, "acc")), 1],
  ["automade", "Video auto", r => qv(VID(r, "auto_made")), 1], ["cycle", "Cycle s ↓", r => qv(VID(r, "cycle_s")), -1], ["climb", "Climb (TBA)", r => r.s?.climb?.rate, 1],
  ["defs", "Defense", r => VID(r, "def_matches"), 1]];
async function picklistPane(el, d, ev) {
  el.innerHTML = `<div class="loading">Loading team stats…</div>`;
  const hm = location.hash.match(/\/picklist\/(\d{9,})/);          // deep link #/<ev>/picklist/<ts>
  const ts = hm ? hm[1] : "";                                      // as-of time lives in the toolbar (URL)
  const { sd, rows } = await scoutRows(d, ev, ts);
  const fmtTs = t => { const x = new Date(t * 1000); return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][x.getDay()] + " " + x.toTimeString().slice(0, 5); };
  const st = Object.assign({ stars: [], dnp: [], weights: { ...PL_DEFAULT } }, await plLoad(ev));
  st.weights = { ...PL_DEFAULT, ...(st.weights && "blend" in st.weights ? st.weights : {}) };
  const climbOn = !!(sd && sd.event_stats && sd.event_stats.climb_matters);
  const save = () => plSave(ev, st, cur => {
    Object.assign(st, { stars: [], dnp: [] }, cur); st.weights = { ...PL_DEFAULT, ...cur.weights };
    if (el.isConnected) render();
  });
  // z-scores across the event (missing = event mean); video metrics are already shrunk toward the event
  const zs = {};
  PL_KEYS.forEach(([k, , g, sg]) => { const v = rows.map(g).filter(has), m = v.reduce((a, b) => a + b, 0) / Math.max(1, v.length);
    const s_ = Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, v.length - 1)) || 1; zs[k] = r => { const x = g(r); return has(x) ? sg * (x - m) / s_ : 0; }; });
  let only = "all", mode = lsGet("cyberscout:plmode", "blend");
  const W = k => (k === "climb" && !climbOn) ? 0 : (st.weights[k] || 0);
  const custom = r => { let s_ = 0, w = 0; for (const [k] of PL_KEYS) { const wt = W(k); if (!wt) continue; s_ += wt * zs[k](r); w += wt; } return w ? s_ / w : 0; };
  const key = { blend: r => r.s?.rating?.blend, epa: r => r.s?.rating?.epa, opr: r => r.t.opr, custom };
  const maxB = Math.max(1, ...rows.map(r => (r.s?.rating?.blend || 0) + (r.s?.rating?.sd || 0)));
  const bbar = r => { const b = r.s?.rating; if (!b || b.blend == null) return "–"; const X = v => Math.max(0, Math.min(100, v / maxB * 100)).toFixed(1);
    return `<span class="sc-prb" title="blend ${fx(b.blend)} ± ${fx(b.sd)} (1 sd)"><i class="rg" style="left:${X(b.blend - b.sd)}%;width:${(X(b.blend + b.sd) - X(b.blend - b.sd)).toFixed(1)}%"></i><i class="mn" style="left:${X(b.blend)}%"></i></span><b>${fx(b.blend)}</b><span class="sc-sd">±${fx(b.sd, 0)}</span>`; };
  const render = () => {
    const kf = key[mode] || key.blend;
    const list = rows.map(r => ({ r, s: kf(r) })).sort((a, b) => (b.s ?? -1e9) - (a.s ?? -1e9));
    const shown = list.filter(x => only === "all" || (only === "star" && st.stars.includes(x.r.t.key)) || (only === "avail" && !st.dnp.includes(x.r.t.key)));
    const dis = x => x && x.r.s ? flagChips(x.r.s, ["video_higher", "video_lower", "auto_not_tracked"]) : "";
    el.innerHTML = `${ts ? `<div class="sc-banner"><b>Replay as of ${fmtTs(+ts)}</b>: ratings, ranks, OPR and video only from data posted before then.</div>` : dataBanner(sd, "ratings: end-of-event dashboard")}<div class="plwrap"><div class="plw card"><h2>Order by</h2><div class="chips" id="plm"></div>
      <div class="note" style="margin:6px 0 8px">Default: blend rating ± sd (expected points/match; best predictor here: rank-order agreement with the day-2 result .92 vs .65 for video-only stats).
      Custom = weighted z-scores; video metrics are shrunk and missing values count as event average.</div>
      ${mode === "custom" ? PL_KEYS.map(([k, l]) => `<label class="wrow ${k === "climb" && !climbOn ? "sc-dis" : ""}" title="${k === "climb" && !climbOn ? `off: the best robot here averages ${fx(sd?.event_stats?.tower_pts_max)} tower pts/match (≤ 10)` : ""}"><span>${l}</span><input type="range" min="0" max="5" step="0.5" value="${st.weights[k] || 0}" data-k="${k}" ${k === "climb" && !climbOn ? "disabled" : ""}><b>${W(k)}</b></label>`).join("") : ""}
      <div class="controls"><button id="plreset">Reset weights</button><button id="plcopy">Copy list</button></div></div>
      <div class="plt card"><div class="chips" id="plf"></div><div class="tablewrap"><table><thead><tr><th class="num">#</th><th></th><th>Team</th><th class="num">Rank</th><th>Blend ± sd</th><th class="num">EPA</th><th class="num">OPR</th>
        <th title="verified robot-video matches / played">Video</th><th class="num" title="FUEL made per match on verified video (≥ = floor)">Made/m</th><th class="num">Acc</th><th class="num">Auto</th>${climbOn ? '<th class="num">Climb</th>' : ""}<th class="num" title="matches with defense played (episodes) on verified video: third-robot / defender value">Def.</th><th class="num" title="stopped ≥ 30 s AND the alliance scored under 85% of expectation (TBA); ≥ 3 verified matches">Dead</th>${mode === "custom" ? '<th class="num">Score</th>' : ""}<th>Notes</th></tr></thead>
      <tbody>${shown.map((x, i) => { const r = x.r, k = r.t.key, star = st.stars.includes(k), no = st.dnp.includes(k), dd = VID(r, "dead"); return `<tr class="${no ? "dnp" : ""} ${k === (d.team || "frc4911") ? "usrow" : ""}"><td class="num">${i + 1}</td>
        <td style="white-space:nowrap"><button class="star ${star ? "on" : ""}" data-s="${k}" title="star">${star ? "★" : "☆"}</button><button class="star ${no ? "on" : ""}" data-n="${k}" title="do not pick">⊘</button></td><td><a class="team" href="#/${ev}/t/${k}">${r.t.number}</a> <span class="muted small" title="${esc(r.t.nickname || "")}">${esc((r.t.nickname || "").slice(0, 12))}</span></td>
        <td class="num">${r.t.rank ?? "–"}</td><td>${bbar(r)}</td><td class="num">${fx(r.s?.rating?.epa)}</td><td class="num">${fx(r.t.opr)}</td><td>${covPill(r.s?.coverage, { short: true })}</td>
        <td class="num">${svHtml(VID(r, "made_pm"))}</td><td class="num">${svHtml(VID(r, "acc"), "p")}</td><td class="num">${VID(r, "auto_made") ? svHtml(VID(r, "auto_made")) : r.s?.coverage?.n ? '<span class="muted" title="video auto contradicts TBA: not tracked">n/t</span>' : "–"}</td>
        ${climbOn ? `<td class="num">${pct(r.s?.climb?.rate)}</td>` : ""}<td class="num" title="${VID(r, "def_played") || 0} defense episodes in ${VID(r, "def_matches") || 0} matches">${VID(r, "def_matches") || "–"}</td><td class="num">${dd && dd.n >= 3 ? pct(dd.v) : "–"}</td>${mode === "custom" ? `<td class="num"><b>${fx(x.s, 2)}</b></td>` : ""}
        <td>${dis(x)}</td></tr>`; }).join("")}</tbody></table></div>
        <div class="note">Video columns: verified matches only, shrunk (hover for raw and n); grey italic = n &lt; 3. "video higher" = reconciled video FUEL ≥ 1.15× EPA FUEL + 8.
        Video FUEL numbers use only alliance results that reconcile with TBA within 20% (hover for n). "video lower?" appears only with ≥ 3 reconciled matches: under-attribution made every "video says worse" call wrong. ${climbOn ? "" : `Climb column hidden: no robot here averages more than 10 tower pts/match (max ${fx(sd?.event_stats?.tower_pts_max)}).`}</div></div></div>`;
    chipRow(el.querySelector("#plm"), [["blend", "Blend"], ["epa", "EPA"], ["opr", "OPR"], ["custom", "Custom"]], mode, k => { mode = k; lsSet("cyberscout:plmode", k); render(); });
    chipRow(el.querySelector("#plf"), [["all", "All"], ["star", "Starred"], ["avail", "Hide ⊘"]], only, k => { only = k; render(); });
    el.querySelectorAll(".wrow input").forEach(i => i.oninput = () => { st.weights[i.dataset.k] = +i.value; save(); i.nextElementSibling.textContent = i.value; clearTimeout(render.t); render.t = setTimeout(render, 120); });
    el.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { const k = b.dataset.s, i = st.stars.indexOf(k); i >= 0 ? st.stars.splice(i, 1) : st.stars.push(k); save(); render(); });
    el.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { const k = b.dataset.n, i = st.dnp.indexOf(k); i >= 0 ? st.dnp.splice(i, 1) : st.dnp.push(k); save(); render(); });
    el.querySelector("#plreset").onclick = () => { st.weights = { ...PL_DEFAULT }; save(); render(); };
    el.querySelector("#plcopy").onclick = e => { const txt = shown.map((x, i) => `${i + 1}. ${x.r.t.number} ${x.r.t.nickname || ""} (${mode} ${fx(x.s, mode === "custom" ? 2 : 0)})${st.stars.includes(x.r.t.key) ? " *" : ""}`).join("\n"); try { navigator.clipboard.writeText(txt); e.target.textContent = "Copied"; } catch (x) { e.target.textContent = "Copy failed"; } };
  };
  render();
}
