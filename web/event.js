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

// fold order when the table is narrower than its columns: higher = kept longer (app.js fitTable)
const TPRI = { rank: 1000, number: 990, blend: 800, made: 700, acc: 600, video: 450, flags: 400, climb: 300, automade: 260, cycle: 220, epa: 200, opr: 150, nickname: 60 };
const TEAMS_HELP = `<span class="hl">Tap a header to sort. <b>Blend</b> ± sd and <b>EPA</b> are points per match.</span>
  <span class="hl">Video columns use verified matches only (v2 tracker, identity ≥ 60%); v1 tracks feed nothing but the replay. Values are shrunk toward the event; hover a value (or open its team page) for the raw number and n. <i>Grey italic</i> = fewer than 3 verified matches.</span>
  <span class="hl"><b>≥</b> marks a floor: more than 20% of that alliance's FUEL was not credited to any robot, so low video numbers are not evidence of a weak robot.</span>
  <span class="hl">Climb is from TBA for every played qual.</span>`;
function teamsTable(el, d, ev) {
  const vis = lsGet("cyberscout:cols2", null) || Object.fromEntries(QCOLS.map(c => [c[0], !!c[5]]));
  const picks = lsGet("cyberscout:picks:" + ev, {}), stars = new Set(picks.stars || []);
  let sk = "blend", dir = -1, data = null, showCfg = false;
  const base = [["rank", "Rank", r => r.t.rank ?? "–", true, r => r.t.rank], ["number", "Team", r => `<a class="team" href="#/${ev}/t/${r.t.key}">${r.t.number}</a>${stars.has(r.t.key) ? ` <span class="starm" title="starred">${CS.icon("starOn")}<span class="vh">starred</span></span>` : ""}`, false, r => r.t.number],
    ["nickname", "Name", r => esc(r.t.nickname), false, r => r.t.nickname || ""], ["opr", "OPR", r => fx(r.t.opr), true, r => r.t.opr, "TBA OPR (end of event)"]];
  const cols = () => [...base, ...QCOLS.filter(c => vis[c[0]]).map(c => [c[0], c[1], r => qcell(c, r), true, r => qv(c[2](r)), c[6]]),
    ["flags", "", r => flagChips(r.s, ["auto_not_tracked", "video_higher", "video_lower"]), false, () => null]];
  const render = () => {
    const cs = cols(), arr = [...(data ? data.rows : d.teams.map(t => ({ t, s: null })))];
    const sc = cs.find(c => c[0] === sk) || cs[0];
    arr.sort((a, b) => { const x = sc[4](a), y = sc[4](b); if (x == null && y == null) return a.t.number - b.t.number; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir; });
    el.innerHTML = `${dataBanner(data && data.sd)}<div class="controls"><button id="cfg" class="${showCfg ? "on" : ""}" aria-expanded="${showCfg}">Columns</button>${data ? "" : '<span class="muted small">loading…</span>'}${CS.help(TEAMS_HELP, "How to read this table")}</div>
      ${showCfg ? `<div class="cfg">${QCOLS.map(c => `<label title="${esc(c[6])}"><input type="checkbox" data-c="${c[0]}" ${vis[c[0]] ? "checked" : ""}> ${c[1]}</label>`).join("")}</div>` : ""}
      <div class="tablewrap"><table><thead><tr>${cs.map(([k, t, , n, , tip]) => `<th data-k="${k}" data-pri="${TPRI[k] ?? 100}" title="${esc(tip || "")}" class="${n ? "num" : ""}">${t}${sk === k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${arr.map(r => `<tr>${cs.map(([, , f, n]) => `<td class="${n ? "num" : ""}">${f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    el.querySelector("#cfg").onclick = () => { showCfg = !showCfg; render(); };
    el.querySelectorAll(".cfg input").forEach(i => i.onchange = () => { vis[i.dataset.c] = i.checked; lsSet("cyberscout:cols2", vis); render(); });
    el.querySelectorAll("th").forEach(th => th.onclick = () => {
      const k = th.dataset.k; if (!k || k === "flags") return; if (sk === k) dir = -dir; else { sk = k; const q = QCOLS.find(c => c[0] === k); dir = ["rank", "number", "nickname"].includes(k) ? 1 : -1; if (q && q[4] === -1) dir = 1; }
      render();
    });
  };
  render();
  scoutRows(d, ev).then(r => { data = r; if (el.isConnected) render(); });
}

// ------------------------------------------------------------------ compare
const CMP_EPA_HELP = `<span class="hl"><b>EPA</b> = Expected Points Added (Statbotics-style): a running estimate of how many points a team adds to its alliance per match, updated after every match from official scores.</span><span class="hl"><b>Local as-of</b> = computed here from this event's results up to now.</span>`;
const CMP_OPR_HELP = `<span class="hl"><b>OPR</b> = Offensive Power Rating: a least-squares solve over all qual alliance scores at this event, giving each team's average point contribution.</span><span class="hl">Noisy with few matches, and it can't separate teammates who always play together.</span>`;
async function comparePane(el, d, ev, arg) {
  let sel = (arg ? arg.split(",") : lsGet("cyberscout:cmp:" + ev, [])).filter(k => d.teams.some(t => t.key === k || t.key === "frc" + k)).map(k => k.startsWith("frc") ? k : "frc" + k).slice(0, 3);
  let show = lsGet("cyberscout:cmpshow", "all");
  const opts = d.teams.map(t => `<option value="${esc(t.key)}">${esc(t.number)} ${esc(t.nickname || "")}</option>`).join("");
  el.innerHTML = `<div class="controls">${[0, 1, 2].map(i => `<select data-i="${i}"><option value="">${i < 2 ? "choose team" : "(optional) third team"}</option>${opts}</select>`).join("")}</div>
    <div id="cmpshow"></div><div id="cmpbody" class="cmp"></div>`;
  const body = el.querySelector("#cmpbody"), showEl = el.querySelector("#cmpshow");
  const selects = [...el.querySelectorAll("select")];
  selects.forEach((s, i) => { s.value = sel[i] || ""; s.onchange = () => { sel = selects.map(x => x.value).filter(Boolean); lsSet("cyberscout:cmp:" + ev, sel); build(); }; });
  // one {v,n,conf} leaf -> value + n (grey italic under 3 matches), "–" when missing
  const leaf = (x, f = "1", unit = "", tip = "") => { const v = val(x), n = mn(x);
    return has(v) ? `<span class="sc-v ${n != null && n < 3 ? "grey" : ""}" title="${esc(tip)}">${fmtV(v, f)}${unit}</span>${n != null ? `<span class="sc-sd">n=${n}</span>` : ""}` : "–"; };
  // hub active / inactive: a full-width row (label + verdict on one line, the supporting numbers wrap below)
  const hubCell = (t, k, l) => { const h = hubPhaseSummary(t.analytics?.hub), x = h && h[k];
    return `<div style="display:flex;justify-content:space-between;gap:8px"><span style="white-space:nowrap">${l}</span>${x ? `<b class="${x.thin ? "muted" : ""}" style="text-align:right">${esc(x.label)}</b>` : "–"}</div>${x ? `<div class="small muted hp-d" style="width:0;min-width:100%">${x.html}</div>` : ""}`; };     // width 0 / min 100%: the long details wrap instead of widening the table
  async function build() {
    if (!sel.length) { showEl.innerHTML = ""; body.innerHTML = `<div class="empty">Pick 2–3 teams to compare shot charts, autos and key metrics side by side.</div>`; return; }
    const [ts, sd] = await Promise.all([Promise.all(sel.map(k => getTeam(ev, k))), scoutData(ev)]);
    body.style.setProperty("--n", ts.length);
    const S = t => (sd && sd.by[t.key]) || {}, V = (t, k) => (S(t).video || {})[k];
    const DF = t => t.analytics?.defense || {}, DD = t => t.analytics?.defended || {};
    // [label (string, or a function so each column gets its own "?" ids), cell(t, label), kind: 1 = full-width row, 2 = value may wrap]
    const rows = [[() => `EPA (local <span style="white-space:nowrap">as-of)&nbsp;${CS.help(CMP_EPA_HELP, "What EPA means")}</span>`, t => fx(S(t).rating?.epa)],
      [() => `OPR ${CS.help(CMP_OPR_HELP, "What OPR means")}`, t => fx(t.opr)], ["Video", t => covPill(S(t).coverage)],
      ["Made / match", t => svHtml(V(t, "made_pm"))], ["Accuracy", t => svHtml(V(t, "acc"), "p")], ["Auto FUEL", t => V(t, "auto_made") ? svHtml(V(t, "auto_made")) : flagChips(S(t), ["auto_not_tracked"]) || "–"],
      [() => `Hub active ${CS.help(hubHelp(), "About hub active / inactive")}`, (t, l) => hubCell(t, "on", l), 1], ["Hub inactive", (t, l) => hubCell(t, "off", l), 1],
      ["Climb (TBA)", t => S(t).climb ? `${pct(S(t).climb.rate)}<span class="sc-sd">${S(t).climb.n} m</span>` : "–"],
      ["Defense played", t => { const n = S(t).coverage?.n; if (!n) return "–";
        return `<span title="${esc(`${fx(V(t, "def_secs") || 0, 0)} s defending in total; verified video matches only`)}"><span style="white-space:nowrap">${V(t, "def_matches") || 0} of ${n} matches</span> <span style="white-space:nowrap">· ${V(t, "def_played") || 0} episode${V(t, "def_played") === 1 ? "" : "s"}</span></span>`; }, 2],
      ["Defending /match", t => leaf(DF(t).time_s, "1", " s", "time spent defending per match")], ["Contacts /match", t => leaf(DF(t).contacts)], ["Pins /match", t => leaf(DF(t).pins, "2", "", "sustained contact, opponent nearly stopped")],
      ["Defense effect", t => leaf(DF(t).effect, "p", "", "drop in the defended opponent's fire rate")],
      ["Defended against", t => leaf(DD(t).time_s, "1", " s", has(val(DD(t).rate_retained)) ? `keeps ${fmtV(val(DD(t).rate_retained), "p")} of its scoring rate when defended (n=${mn(DD(t).rate_retained)})` : "")]];
    const vids = new Set(sd ? sd.verified_matches.map(shortKey) : []);
    const chips = SHOW_CHIPS;
    if (chips && !chips.some(c => c[0] === show)) show = "all";
    showEl.innerHTML = chips ? `<div class="chipbar" style="align-items:center;margin-top:var(--sp-2)"><span class="small muted">Shot charts</span><div class="chips" id="cmpsc"></div>${shotLegend()}</div>` : "";
    body.innerHTML = ts.map((t, i) => `<div class="cmpcol card"><h2><a href="#/${ev}/t/${t.key}">${t.number}</a> <span class="muted" style="font-weight:500">${esc(t.nickname || "")}</span></h2>
      <table class="mini">${rows.map(([l, f, kind]) => { l = typeof l === "function" ? l() : l;
        return kind === 1 ? `<tr><td colspan="2" style="white-space:normal">${f(t, l)}</td></tr>`
          : `<tr><td style="white-space:normal;min-width:6.5em">${l}</td><td class="num"${kind ? ' style="white-space:normal"' : ""}>${f(t)}</td></tr>`; }).join("")}</table>
      <h3>Shot chart</h3><div id="cs${i}"></div><h3>Autos</h3><div id="ca${i}"></div>
      <div class="small muted">verified matches only: ${esc((S(t).coverage?.keys || []).map(shortKey).join(", ") || "none")}</div></div>`).join("");
    const charts = ts.map((t, i) => liveCanvas(body.querySelector("#cs" + i), F => drawShotChart(F, (t.shots_map || []).filter(p => vids.has(p.m)), "both", "all", show)));
    if (chips) chipRow(showEl.querySelector("#cmpsc"), chips, show, k => { show = k; lsSet("cyberscout:cmpshow", k); charts.forEach(F => F.redraw()); });
    ts.forEach((t, i) => {
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
      if (r && r.queued) return;       // offline: kept in the outbox (sync.js), sent in order when the server is back
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
// sync.js: a queued save was sent (new version), or the user picked "take theirs" on a conflict
window.CS = window.CS || { routes: {}, globalRoutes: {}, navItems: [] };     // event.js loads before app.js
CS.picklist = {
  adopt(ev, cur) { plMeta[ev] = { version: cur.version, by: cur.updated_by, at: cur.updated_at }; lsSet(plKey(ev), cur.data); },
};
document.addEventListener("cs:sent", e => {
  const d = e.detail || {}, m = /^events\/([^/]+)\/picklists\/main$/.exec(d.path || "");
  if (m && d.data && d.data.version != null) plMeta[decodeURIComponent(m[1])] = { version: d.data.version, by: d.data.updated_by, at: d.data.updated_at };
});
const PL_DEFAULT = { blend: 4, made: 0, acc: 0, automade: 0, cycle: 0, climb: 0, defs: 0 };
// weights come from the shared server picklist or localStorage: only the known keys, each a number in 0..5 (security
// audit: never render a stored value as markup, in value="" or as the shown number)
const plWeight = v => { const n = Number(v); return Number.isFinite(n) ? Math.max(0, Math.min(5, n)) : 0; };
const plWeights = w => Object.fromEntries(Object.keys(PL_DEFAULT).map(k => [k, plWeight(w && k in w ? w[k] : PL_DEFAULT[k])]));
const PL_KEYS = [["blend", "Blend", r => r.s?.rating?.blend, 1], ["made", "Video made/m", r => qv(VID(r, "made_pm")), 1], ["acc", "Video acc", r => qv(VID(r, "acc")), 1],
  ["automade", "Video auto", r => qv(VID(r, "auto_made")), 1], ["cycle", "Cycle s ↓", r => qv(VID(r, "cycle_s")), -1], ["climb", "Climb (TBA)", r => r.s?.climb?.rate, 1],
  ["defs", "Defense", r => VID(r, "def_matches"), 1]];
async function picklistPane(el, d, ev) {
  el.innerHTML = `<div class="loading">Loading team stats…</div>`;
  const hm = location.hash.match(/\/picklist\/(\d{9,})/);          // deep link #/<ev>/picklist/<ts>
  const ts = hm ? hm[1] : "";                                      // as-of time lives in the toolbar (URL)
  const { sd, rows } = await scoutRows(d, ev, ts);
  const fmtTs = t => { const x = new Date(t * 1000); return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][x.getDay()] + " " + x.toTimeString().slice(0, 5); };
  const st = Object.assign({ stars: [], dnp: [], order: [], weights: { ...PL_DEFAULT } }, await plLoad(ev));
  st.weights = plWeights(st.weights && "blend" in st.weights ? st.weights : {});
  const climbOn = !!(sd && sd.event_stats && sd.event_stats.climb_matters);
  const save = () => plSave(ev, st, cur => {
    Object.assign(st, { stars: [], dnp: [], order: [] }, cur); st.weights = plWeights(cur.weights);
    if (el.isConnected) render();
  });
  // z-scores across the event (missing = event mean); video metrics are already shrunk toward the event
  const zs = {};
  PL_KEYS.forEach(([k, , g, sg]) => { const v = rows.map(g).filter(has), m = v.reduce((a, b) => a + b, 0) / Math.max(1, v.length);
    const s_ = Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, v.length - 1)) || 1; zs[k] = r => { const x = g(r); return has(x) ? sg * (x - m) / s_ : 0; }; });
  let only = "all", mode = lsGet("cyberscout:plmode", "blend");
  const W = k => (k === "climb" && !climbOn) ? 0 : plWeight(st.weights[k]);
  const custom = r => { let s_ = 0, w = 0; for (const [k] of PL_KEYS) { const wt = W(k); if (!wt) continue; s_ += wt * zs[k](r); w += wt; } return w ? s_ / w : 0; };
  const key = { blend: r => r.s?.rating?.blend, epa: r => r.s?.rating?.epa, opr: r => r.t.opr, custom };
  // "My order": the strategist's dragged ranking (st.order, best first); teams not placed yet follow by blend
  const sorted = m => { if (m === "manual") { const ix = new Map(st.order.map((k, i) => [k, i])), b = key.blend;
      return [...rows].sort((x, y) => (ix.get(x.t.key) ?? 1e6) - (ix.get(y.t.key) ?? 1e6) || (b(y) ?? -1e9) - (b(x) ?? -1e9)).map(r => ({ r, s: ix.has(r.t.key) ? ix.get(r.t.key) + 1 : null })); }
    const kf = key[m] || key.blend; return rows.map(r => ({ r, s: kf(r) })).sort((a, b) => (b.s ?? -1e9) - (a.s ?? -1e9)); };
  // move team k to just before (after = false) or after team t in the full list; dragging in a computed order starts
  // "My order" from what's on screen
  const move = (k, t, after) => {
    const full = sorted(mode).map(x => x.r.t.key).filter(x => x !== k);
    full.splice(full.indexOf(t) + (after ? 1 : 0), 0, k);
    st.order = full; if (mode !== "manual") { mode = "manual"; lsSet("cyberscout:plmode", mode); }
    save(); render();
  };
  const maxB = Math.max(1, ...rows.map(r => (r.s?.rating?.blend || 0) + (r.s?.rating?.sd || 0)));
  const bbar = r => { const b = r.s?.rating; if (!b || b.blend == null) return "–"; const X = v => Math.max(0, Math.min(100, v / maxB * 100)).toFixed(1);
    return `<span class="sc-prb" title="blend ${fx(b.blend)} ± ${fx(b.sd)} (1 sd)"><i class="rg" style="left:${X(b.blend - b.sd)}%;width:${(X(b.blend + b.sd) - X(b.blend - b.sd)).toFixed(1)}%"></i><i class="mn" style="left:${X(b.blend)}%"></i></span><b>${fx(b.blend)}</b><span class="sc-sd">±${fx(b.sd, 0)}</span>`; };
  const render = () => {
    const list = sorted(mode);
    const shown = list.filter(x => only === "all" || (only === "star" && st.stars.includes(x.r.t.key)) || (only === "avail" && !st.dnp.includes(x.r.t.key)));
    const dis = x => x && x.r.s ? flagChips(x.r.s, ["video_higher", "video_lower", "auto_not_tracked"]) : "";
    el.innerHTML = `${ts ? `<div class="sc-banner"><b>Replay as of ${fmtTs(+ts)}</b>: ratings, ranks, OPR and video only from data posted before then.</div>` : dataBanner(sd, "ratings: end-of-event dashboard")}<div class="plwrap"><div class="plw card"><h2>Order by ${CS.help(`<span class="hl">Default: <b>blend</b> rating ± sd (expected points/match; best predictor here: rank-order agreement with the day-2 result .92 vs .65 for video-only stats).</span><span class="hl"><b>Custom</b> = weighted z-scores; video metrics are shrunk and missing values count as event average.</span><span class="hl"><b>My order</b> = your own ranking: drag a row by its handle (or focus the handle and press ↑ ↓). Dragging in any other order starts My order from it. Shared with the team like stars.</span>`, "How the order works")}</h2><div class="chips" id="plm"></div>
      ${mode === "custom" ? PL_KEYS.map(([k, l]) => `<label class="wrow ${k === "climb" && !climbOn ? "sc-dis" : ""}" title="${k === "climb" && !climbOn ? `off: the best robot here averages ${fx(sd?.event_stats?.tower_pts_max)} tower pts/match (≤ 10)` : ""}"><span>${l}</span><input type="range" min="0" max="5" step="0.5" value="${plWeight(st.weights[k])}" data-k="${k}" ${k === "climb" && !climbOn ? "disabled" : ""}><b>${W(k)}</b></label>`).join("") : ""}
      <div class="controls">${mode === "manual" ? '<button id="plro">Reset my order</button>' : ""}<button id="plreset">Reset weights</button><button id="plcopy">Copy list</button></div></div>
      <div class="plt card"><div class="pl-bar"><div class="chips" id="plf"></div>${CS.help(`<span class="hl">Video columns: verified matches only, shrunk (hover for raw and n); <i>grey italic</i> = n &lt; 3.</span><span class="hl">"video higher" = reconciled video FUEL ≥ 1.15× EPA FUEL + 8. Video FUEL numbers use only alliance results that reconcile with TBA within 20% (hover for n).</span><span class="hl">"video lower?" appears only with ≥ 3 reconciled matches: under-attribution made every "video says worse" call wrong.</span>${climbOn ? "" : `<span class="hl">Climb column hidden: no robot here averages more than 10 tower pts/match (max ${fx(sd?.event_stats?.tower_pts_max)}).</span>`}`, "How to read the picklist")}</div><div class="tablewrap"><table><thead><tr><th class="num" data-pri="1000">#</th><th data-pri="960"><span class="vh">Star / do not pick</span></th><th data-pri="990">Team</th><th class="num" data-pri="300">Rank</th><th data-pri="800">Blend ± sd</th><th class="num" data-pri="200">EPA</th><th class="num" data-pri="150">OPR</th>
        <th title="verified robot-video matches / played" data-pri="450">Video</th><th class="num" title="FUEL made per match on verified video (≥ = floor)" data-pri="600">Made/m</th><th class="num" data-pri="550">Acc</th><th class="num" data-pri="250">Auto</th>${climbOn ? '<th class="num" data-pri="240">Climb</th>' : ""}<th class="num" title="matches with defense played (episodes) on verified video: third-robot / defender value" data-pri="230">Def.</th><th class="num" title="stopped ≥ 30 s AND the alliance scored under 85% of expectation (TBA); ≥ 3 verified matches" data-pri="220">Dead</th>${mode === "custom" ? '<th class="num" data-pri="850">Score</th>' : ""}<th data-pri="400">Notes</th></tr></thead>
      <tbody>${shown.map((x, i) => { const r = x.r, k = r.t.key, star = st.stars.includes(k), no = st.dnp.includes(k), dd = VID(r, "dead"); return `<tr data-k="${k}" class="${no ? "dnp" : ""} ${k === (d.team || "frc4911") ? "usrow" : ""}"><td class="num"><span class="pl-rk"><button class="grip" data-g="${k}" title="Drag to rank" aria-label="Move ${r.t.number}: drag, or arrow keys">${CS.icon("grip")}</button>${i + 1}</span></td>
        <td style="white-space:nowrap"><button class="star ${star ? "on" : ""}" data-s="${k}" title="star" aria-label="Star ${r.t.number}" aria-pressed="${star}">${CS.icon(star ? "starOn" : "star")}</button><button class="star ${no ? "on" : ""}" data-n="${k}" title="do not pick" aria-label="Do not pick ${r.t.number}" aria-pressed="${no}">${CS.icon("ban")}</button></td><td><a class="team" href="#/${ev}/t/${k}">${r.t.number}</a> <span class="muted small pl-nick" title="${esc(r.t.nickname || "")}">${esc((r.t.nickname || "").slice(0, 12))}</span></td>
        <td class="num">${r.t.rank ?? "–"}</td><td>${bbar(r)}</td><td class="num">${fx(r.s?.rating?.epa)}</td><td class="num">${fx(r.t.opr)}</td><td>${covPill(r.s?.coverage, { short: true })}</td>
        <td class="num">${svHtml(VID(r, "made_pm"))}</td><td class="num">${svHtml(VID(r, "acc"), "p")}</td><td class="num">${VID(r, "auto_made") ? svHtml(VID(r, "auto_made")) : r.s?.coverage?.n ? '<span class="muted" title="video auto contradicts TBA: not tracked">n/t</span>' : "–"}</td>
        ${climbOn ? `<td class="num">${pct(r.s?.climb?.rate)}</td>` : ""}<td class="num" title="${VID(r, "def_played") || 0} defense episodes in ${VID(r, "def_matches") || 0} matches">${VID(r, "def_matches") || "–"}</td><td class="num">${dd && dd.n >= 3 ? pct(dd.v) : "–"}</td>${mode === "custom" ? `<td class="num"><b>${fx(x.s, 2)}</b></td>` : ""}
        <td>${dis(x)}</td></tr>`; }).join("")}</tbody></table></div></div></div>`;
    chipRow(el.querySelector("#plm"), [["blend", "Blend"], ["epa", "EPA"], ["opr", "OPR"], ["custom", "Custom"], ["manual", "My order"]], mode, k => { mode = k; lsSet("cyberscout:plmode", k); render(); });
    chipRow(el.querySelector("#plf"), [["all", "All"], ["star", "Starred"], ["avail", "Hide do-not-pick"]], only, k => { only = k; render(); });
    el.querySelectorAll(".wrow input").forEach(i => i.oninput = () => { st.weights[i.dataset.k] = plWeight(i.value); save(); i.nextElementSibling.textContent = String(plWeight(i.value)); clearTimeout(render.t); render.t = setTimeout(render, 120); });
    el.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { const k = b.dataset.s, i = st.stars.indexOf(k); i >= 0 ? st.stars.splice(i, 1) : st.stars.push(k); save(); render(); });
    el.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { const k = b.dataset.n, i = st.dnp.indexOf(k); i >= 0 ? st.dnp.splice(i, 1) : st.dnp.push(k); save(); render(); });
    el.querySelector("#plreset").onclick = () => { st.weights = { ...PL_DEFAULT }; save(); render(); };
    const ro = el.querySelector("#plro"); if (ro) ro.onclick = () => { st.order = []; save(); render(); };
    plDrag(el.querySelector(".plt tbody"), shown.map(x => x.r.t.key), move);
    el.querySelector("#plcopy").onclick = e => { const txt = shown.map((x, i) => `${i + 1}. ${x.r.t.number} ${x.r.t.nickname || ""} ${mode === "manual" ? "" : ` (${mode} ${fx(x.s, mode === "custom" ? 2 : 0)})`}${st.stars.includes(x.r.t.key) ? " *" : ""}`).join("\n"); try { navigator.clipboard.writeText(txt); e.target.textContent = "Copied"; } catch (x) { e.target.textContent = "Copy failed"; } };
  };
  render();
}
// Drag-to-rank on the picklist rows: pointer events (mouse, touch, pen) on the grip; arrow keys on a focused grip.
// keys = the rows on screen, in order; move(k, target, after) re-renders.
function plDrag(tb, keys, move) {
  if (!tb) return;
  tb.querySelectorAll("[data-g]").forEach(g => {
    g.onkeydown = e => {
      const k = g.dataset.g, i = keys.indexOf(k), j = e.key === "ArrowUp" ? i - 1 : e.key === "ArrowDown" ? i + 1 : -1;
      if (j < 0 || j >= keys.length) return;
      e.preventDefault(); move(k, keys[j], j > i);
      const f = document.querySelector(`.plt [data-g="${k}"]`); f && f.focus();
    };
    g.onpointerdown = e => {
      if (e.button) return;
      e.preventDefault(); try { g.setPointerCapture(e.pointerId); } catch (_) { /* synthetic events */ }
      const k = g.dataset.g, src = g.closest("tr"); let tgt = null, after = false;
      src.classList.add("pl-drag");
      const mark = () => tb.querySelectorAll(".pl-before,.pl-after").forEach(r => r.classList.remove("pl-before", "pl-after"));
      g.onpointermove = ev => {
        if (ev.clientY < 70) scrollBy(0, -14); else if (ev.clientY > innerHeight - 70) scrollBy(0, 14);
        tgt = null; mark();
        for (const tr of tb.rows) {
          const b = tr.getBoundingClientRect();
          if (ev.clientY < b.top || ev.clientY > b.bottom) continue;
          if (tr !== src) { tgt = tr.dataset.k; after = ev.clientY > b.top + b.height / 2; tr.classList.add(after ? "pl-after" : "pl-before"); }
          break;
        }
      };
      g.onpointerup = g.onpointercancel = ev => {
        g.onpointermove = g.onpointerup = g.onpointercancel = null; mark(); src.classList.remove("pl-drag");
        if (ev.type === "pointerup" && tgt) move(k, tgt, after);
      };
    };
  });
}
