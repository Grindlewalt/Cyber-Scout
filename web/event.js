"use strict";
// Event page panes: Compare and Picklist (Selection tabs, rankings.js), scout row helpers. Uses globals from app.js / quant.js.

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

// Canonical team metrics come from scout.json (one value per metric, same on every page): {v, raw, n, tier, lower_bound}.
// (The Teams table that used to be here is the Teams page in teams2.js; the Picklist below still uses these helpers.)
const VID = (r, k) => r.s && r.s.video ? r.s.video[k] : null;
const qv = x => x && typeof x === "object" ? (x.n ? x.v : null) : x;
async function scoutRows(d, ev, ts) {
  let sd;
  if (ts) { try { sd = await api(`${ev}/scout/${ts}.json`); sd.by = Object.fromEntries(sd.teams.map(t => [t.team, t])); } catch (e) { sd = null; } }
  else sd = await scoutData(ev);
  // replay: rank / OPR as of then (scout snapshot), never the end-of-event values from event.json
  return { sd, rows: d.teams.map(t => { const s = sd && sd.by[t.key] || null; return { t: ts ? { ...t, rank: s?.rank ?? null, opr: s?.rating?.opr ?? null } : t, s }; }) };
}

// ------------------------------------------------------------------ compare
const CMP_EPA_HELP = `<span class="hl"><b>EPA</b> = Expected Points Added (Statbotics-style): a running estimate of how many points a team adds to its alliance per match, updated after every match from official scores.</span><span class="hl"><b>Local as-of</b> = computed here from this event's results up to now.</span>`;
const CMP_OPR_HELP = `<span class="hl"><b>OPR</b> = Offensive Power Rating: a least-squares solve over all qual alliance scores at this event, giving each team's average point contribution.</span><span class="hl">Noisy with few matches, and it can't separate teammates who always play together.</span>`;
async function comparePane(el, d, ev, arg) {
  let sel = (arg ? arg.split(",") : lsGet("cyberscout:cmp:" + ev, [])).filter(k => d.teams.some(t => t.key === k || t.key === "frc" + k)).map(k => k.startsWith("frc") ? k : "frc" + k).slice(0, 3);
  let show = lsGet("cyberscout:cmpshow", "all");
  const opts = d.teams.map(t => `<option value="${esc(t.key)}">${esc(t.number)} ${esc(t.nickname || "")}</option>`).join("");
  el.innerHTML = `<h2 class="vh">Compare teams</h2><div class="controls">${[0, 1, 2].map(i => `<select data-i="${i}" aria-label="Team ${i + 1}${i < 2 ? "" : " (optional)"}"><option value="">${i < 2 ? "choose team" : "(optional) third team"}</option>${opts}</select>`).join("")}</div>
    <div id="cmpshow"></div><div id="cmpbody" class="cmp"></div>`;
  const body = el.querySelector("#cmpbody"), showEl = el.querySelector("#cmpshow");
  const selects = [...el.querySelectorAll("select")];
  // the picked teams are also in the address (#/<ev>/selection/compare/4911,2046,948), so a comparison can be shared; replaceState
  // keeps the page as it is (no re-route)
  const toUrl = () => { try { history.replaceState(history.state, "", `#/${ev}/selection/compare${sel.length ? "/" + sel.map(num).join(",") : ""}`); } catch (e) { } };
  selects.forEach((s, i) => { s.value = sel[i] || ""; s.onchange = () => { sel = selects.map(x => x.value).filter(Boolean); lsSet("cyberscout:cmp:" + ev, sel); toUrl(); build(); }; });
  if (sel.length && !arg) toUrl();
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
    // [label (string, or a function so each column gets its own "?" ids), cell(t, label), kind: 1 = full-width row, 2 = value may wrap,
    //  shown(t): whether that team has data for the row (rows nobody has data for are left out; default: the cell isn't "–")]
    const rows = [[() => `Blend ± sd ${CS.help("Model rating: expected points per match (TBA results + earlier events + verified video), the order Picklist and Teams sort by by default.", "What Blend means")}`,
        t => has(S(t).rating?.blend) ? `<b>${fx(S(t).rating.blend)}</b><span class="sc-sd">±${fx(S(t).rating.sd, 0)}</span>` : "–"],
      [() => `EPA (local <span style="white-space:nowrap">as-of)&nbsp;${CS.help(CMP_EPA_HELP, "What EPA means")}</span>`, t => fx(S(t).rating?.epa)],
      [() => `OPR ${CS.help(CMP_OPR_HELP, "What OPR means")}`, t => fx(t.opr)], ["Video", t => covPill(S(t).coverage)],
      ["Made / match", t => svHtml(V(t, "made_pm"))], ["Accuracy", t => svHtml(V(t, "acc"), "p")], ["Auto FUEL", t => V(t, "auto_made") ? svHtml(V(t, "auto_made")) : flagChips(S(t), ["auto_not_tracked"]) || "–"],
      [() => `Hub active ${CS.help(hubHelp(), "About hub active / inactive")}`, (t, l) => hubCell(t, "on", l), 1, t => !!(hubPhaseSummary(t.analytics?.hub) || {}).on],
      ["Hub inactive", (t, l) => hubCell(t, "off", l), 1, t => !!(hubPhaseSummary(t.analytics?.hub) || {}).off],
      ["Climb (TBA)", t => S(t).climb ? `${pct(S(t).climb.rate)}<span class="sc-sd">${S(t).climb.n} m</span>` : "–"],
      ["Defense played", t => { const n = S(t).coverage?.n; if (!n) return "–";
        return `<span title="${esc(`${fx(V(t, "def_secs") || 0, 0)} s defending in total; verified video matches only`)}"><span style="white-space:nowrap">${V(t, "def_matches") || 0} of ${n} matches</span> <span style="white-space:nowrap">· ${V(t, "def_played") || 0} episode${V(t, "def_played") === 1 ? "" : "s"}</span></span>`; }, 2],
      ["Defending /match", t => leaf(DF(t).time_s, "1", " s", "time spent defending per match")], ["Contacts /match", t => leaf(DF(t).contacts)], ["Pins /match", t => leaf(DF(t).pins, "2", "", "sustained contact, opponent nearly stopped")],
      ["Defense effect", t => leaf(DF(t).effect, "p", "", "drop in the defended opponent's fire rate")],
      ["Defended against", t => leaf(DD(t).time_s, "1", " s", has(val(DD(t).rate_retained)) ? `keeps ${fmtV(val(DD(t).rate_retained), "p")} of its scoring rate when defended (n=${mn(DD(t).rate_retained)})` : "")]];
    const blank = x => String(x).replace(/<[^>]*>/g, "").trim();
    const shownRows = rows.filter(([, f, kind, shown]) => ts.some(t => shown ? shown(t) : !["", "–"].includes(blank(kind === 1 ? f(t, "") : f(t)))));
    const vids = new Set(sd ? sd.verified_matches.map(shortKey) : []);
    const chips = SHOW_CHIPS;
    if (chips && !chips.some(c => c[0] === show)) show = "all";
    showEl.innerHTML = chips ? `<div class="chipbar" style="align-items:center;margin-top:var(--sp-2)"><span class="small muted">Shot charts</span><div class="chips" id="cmpsc"></div>${shotLegend()}</div>` : "";
    body.innerHTML = ts.map((t, i) => `<div class="cmpcol card"><h2><a href="#/${ev}/t/${t.key}">${t.number}</a> <span class="muted" style="font-weight:500">${esc(t.nickname || "")}</span></h2>
      <table class="mini">${shownRows.map(([l, f, kind]) => { l = typeof l === "function" ? l() : l;
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
// edit, everyone signed in reads), cached in localStorage under the old key (the Teams page / quant.js read the stars
// from there). No server (legacy static site) -> localStorage only, as before.
// A change is never dropped silently: until the server has it, it is kept on this device as a draft
// (localStorage cyberscout:picks:unsaved:<ev>:<user>) and the page says why it isn't saved (an error, someone else
// saved first, waiting in the offline outbox). Edits are never merged automatically: on a conflict the strategist sees
// what differs and picks which list to keep. Every saved version stays restorable (History).
const PL_NAME = "main", plMeta = {}, plTimers = {}, plSt = {}, plChain = {};
const plKey = ev => "cyberscout:picks:" + ev;
const plPath = ev => `events/${ev}/picklists/${PL_NAME}`;
const plShared = () => !!(window.CS && CS.platform && CS.user);
const plDraftKey = ev => `cyberscout:picks:unsaved:${ev}:${CS.user ? CS.user.id ?? CS.user.username : ""}`;
const plDraft = ev => { const x = plShared() ? lsGet(plDraftKey(ev), null) : null; return x && x.data && typeof x.data === "object" ? x : null; };
const plDropDraft = ev => { try { localStorage.removeItem(plDraftKey(ev)); } catch (e) { } };
// who may change the shared list: strategists and admins; during lockdown only admins (the server checks both)
const plLocked = () => !!(CS.session && CS.session.lockdown) && !CS.can("admin");
const plCanEdit = () => !plShared() || (CS.can("strategist") && !plLocked());
// saves waiting in the offline outbox (sync.js: this browser's, or the Client's), oldest first
const plQueued = ev => CS.sync && CS.sync.pending ? CS.sync.pending(x => x.method === "PUT" && x.path === plPath(ev) && !!(x.body && x.body.data)) : [];
const plCur = r => ({ version: r.version, by: r.updated_by, at: r.updated_at, data: r.data });
const plMe = u => !!(u && CS.user && u.id != null && u.id === CS.user.id);
const plWho = u => plMe(u) ? "You" : (u && u.display_name) || "Someone";
const plStop = t => /[.!?]$/.test(t = String(t || "").trim()) ? t : t + ".";
function plAgo(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t, x = new Date(t * 1000);
  if (s < 45) return "just now";
  if (s < 5400) return `${Math.max(1, Math.round(s / 60))} min ago`;
  const day = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][x.getDay()] + " " + x.toTimeString().slice(0, 5);
  return s < 6 * 86400 ? day : x.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + x.toTimeString().slice(0, 5);
}
// state of the shared list: saved | saving | queued (offline outbox) | error {why} | conflict {cur} | loaderr {why};
// pages hear about changes through the "cs:picklist" event (and an optional per-call status callback)
function plTell(ev, s, on) {
  plSt[ev] = s;
  if (on && on.status) on.status(s);
  document.dispatchEvent(new CustomEvent("cs:picklist", { detail: { ev } }));
}
async function plLoad(ev) {
  if (!plShared()) return lsGet(plKey(ev), {});
  let data;
  try { const r = await CS.app("GET", plPath(ev)); plMeta[ev] = plCur(r); data = r.data; }
  catch (e) {
    if (e.status !== 404) {      // can't tell what the team's list is: show this device's copy, and send nothing until it loads
      plMeta[ev] = { version: null, data: null };
      plTell(ev, { k: "loaderr", why: CS.writeErr ? CS.writeErr(e) : e.message });
      return lsGet(plKey(ev), {});
    }
    plMeta[ev] = { version: null, data: null };       // none yet: the first save creates it from this browser's list
    data = lsGet(plKey(ev), {});
  }
  const q = plQueued(ev), dr = plDraft(ev), m = plMeta[ev];
  if (!plCanEdit()) plSt[ev] = { k: "saved" };                                   // read-only: the team's list as it is
  else if (plTimers[ev] || plChain[ev]) { if (dr) data = dr.data; }                   // a save from this page is on its way
  else if (q.length) { data = q[q.length - 1].body.data; plSt[ev] = { k: "queued" }; }       // made offline, not sent yet
  else if (dr) {        // an edit that never reached the server: an error, the page closed mid-save, or someone saved first
    data = dr.data;
    plSt[ev] = (dr.base ?? null) === (m.version ?? null) ? { k: "error", why: dr.why || "It wasn't sent before the page was closed." } : { k: "conflict", cur: m };
  } else plSt[ev] = { k: "saved" };
  lsSet(plKey(ev), data);
  return data;
}
// on = {status(s)}: called whenever the save state changes (the team page's star uses it for a toast)
function plSave(ev, st, on) {
  lsSet(plKey(ev), st);
  if (!plShared() || !plCanEdit()) return;
  const at = Date.now(), m = plMeta[ev] || {}, s = plSt[ev] || {};
  const dr = plDraft(ev);
  lsSet(plDraftKey(ev), { data: st, base: dr && (plTimers[ev] || plChain[ev] || s.k === "conflict") ? dr.base : m.version ?? null, at, why: dr && dr.why });
  if (s.k === "conflict" || s.k === "loaderr") return plTell(ev, s, on);      // held until the strategist picks a version / the list loads
  plTell(ev, { k: "saving" }, on);
  clearTimeout(plTimers[ev]);
  plTimers[ev] = setTimeout(() => { plTimers[ev] = 0; plSend(ev, st, at, on); }, 500);
}
// one PUT at a time per event: the next one is based on the version the previous one created. base: send on top of
// that version instead of the last one seen (Save mine, after a conflict)
function plSend(ev, st, at, on, base) {
  const run = async () => {
    const m = plMeta[ev] || {};
    try {
      const r = await CS.app("PUT", plPath(ev), { data: st, base_version: (base !== undefined ? base : m.version) ?? undefined });
      const dr = plDraft(ev);
      if (r && r.queued) {          // offline: kept in the outbox (sync.js), sent in order when the server is back
        if (dr && dr.at <= at) plDropDraft(ev);
        return plTell(ev, { k: "queued" }, on);
      }
      plMeta[ev] = plCur(r);
      if (dr && dr.at <= at) plDropDraft(ev); else if (dr) lsSet(plDraftKey(ev), Object.assign(dr, { base: r.version }));
      if (!plTimers[ev]) plTell(ev, { k: "saved" }, on);
    } catch (e) {
      const cur = e.status === 409 && e.data && e.data.detail && e.data.detail.current;
      if (cur) { clearTimeout(plTimers[ev]); plTimers[ev] = 0; return plTell(ev, { k: "conflict", cur: plCur(cur) }, on); }
      const why = CS.writeErr ? CS.writeErr(e) : e.message, dr = plDraft(ev);
      if (dr) lsSet(plDraftKey(ev), Object.assign(dr, { why }));
      plTell(ev, { k: "error", why, status: e.status }, on);
    }
  };
  const p = (plChain[ev] || Promise.resolve()).then(run);
  plChain[ev] = p; p.finally(() => { if (plChain[ev] === p) plChain[ev] = null; });
  return p;
}
// adopt a server copy as the list (take theirs, restore, a newer save seen by polling): drops this device's draft
function plAdopt(ev, cur) {
  plMeta[ev] = plCur(cur); lsSet(plKey(ev), cur.data); plDropDraft(ev);
  plTell(ev, { k: "saved" });
}
// sync.js: a queued save was sent (new version), or the user picked "take theirs" on a conflict
window.CS = window.CS || { routes: {}, globalRoutes: {}, navItems: [] };     // event.js loads before app.js
CS.picklist = {
  adopt(ev, cur) { plAdopt(ev, cur); },
};
document.addEventListener("cs:sent", e => {
  const d = e.detail || {}, m = /^events\/([^/]+)\/picklists\/main$/.exec(d.path || "");
  if (!m || !d.data || d.data.version == null) return;
  const ev = decodeURIComponent(m[1]);
  plMeta[ev] = plCur(d.data);
  if ((plSt[ev] || {}).k === "queued" && !plQueued(ev).length) plTell(ev, { k: "saved" });
});
// a vs b: the teams only a has starred / marked do-not-pick; whether the order / the weights differ
function plDiff(a, b) {
  a = a || {}; b = b || {};
  const only = k => (a[k] || []).filter(t => !(b[k] || []).includes(t)).map(num);
  return { stars: only("stars"), dnp: only("dnp"), order: JSON.stringify(a.order || []) !== JSON.stringify(b.order || []),
    weights: JSON.stringify(plWeights(a.weights)) !== JSON.stringify(plWeights(b.weights)) };
}
const plSame = (a, b) => { const d = plDiff(a, b), e = plDiff(b, a); return !d.stars.length && !d.dnp.length && !e.stars.length && !e.dnp.length && !d.order && !d.weights; };
// what changed from version `prev` to `cur`, in words (History)
function plChanges(prev, cur) {
  if (!prev) return [`started the list: ${(cur.stars || []).length} starred, ${(cur.dnp || []).length} do-not-pick`];
  const add = plDiff(cur, prev), rem = plDiff(prev, cur), out = [];
  if (add.stars.length) out.push("starred " + add.stars.join(", "));
  if (rem.stars.length) out.push("unstarred " + rem.stars.join(", "));
  if (add.dnp.length) out.push("do-not-pick " + add.dnp.join(", "));
  if (rem.dnp.length) out.push("cleared do-not-pick " + rem.dnp.join(", "));
  if (add.order) out.push((cur.order || []).length ? "changed our order" : "cleared our order");
  if (add.weights) out.push("changed the weights");
  return out.length ? out : ["no change"];
}
// clipboard: the async API needs https (or localhost); on the venue LAN (plain http) fall back to a selected textarea
async function plCopy(txt) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(txt); return true; } } catch (e) { }
  const ta = document.createElement("textarea"); ta.value = txt; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0";
  document.body.appendChild(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch (e) { }
  ta.remove(); return ok;
}
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
  const hm = location.hash.match(/\/picklist\/(\d{9,})/);          // deep link #/<ev>/selection/picklist/<ts>
  const ts = hm ? hm[1] : "";                                      // as-of time lives in the toolbar (URL)
  const { sd, rows } = await scoutRows(d, ev, ts);
  const fmtTs = t => { const x = new Date(t * 1000); return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][x.getDay()] + " " + x.toTimeString().slice(0, 5); };
  const EMPTY = () => ({ stars: [], dnp: [], order: [], weights: { ...PL_DEFAULT } });
  const st = EMPTY();
  const use = data => { Object.assign(st, EMPTY(), data || {}); st.weights = plWeights(st.weights && "blend" in st.weights ? st.weights : {}); };
  use(await plLoad(ev));
  if (!el.isConnected) return;
  const climbOn = !!(sd && sd.event_stats && sd.event_stats.climb_matters);
  const edit = plCanEdit(), us = d.team || "frc4911";
  let fresh = null, copyTxt = "";                // fresh: {by, at} after another strategist's save was picked up
  const save = () => plSave(ev, st);
  // z-scores across the event (missing = event mean); video metrics are already shrunk toward the event
  const zs = {};
  PL_KEYS.forEach(([k, , g, sg]) => { const v = rows.map(g).filter(has), m = v.reduce((a, b) => a + b, 0) / Math.max(1, v.length);
    const s_ = Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, v.length - 1)) || 1; zs[k] = r => { const x = g(r); return has(x) ? sg * (x - m) / s_ : 0; }; });
  let only = "all", mode = lsGet("cyberscout:plmode", "blend"), dragging = false;
  const W = k => (k === "climb" && !climbOn) ? 0 : plWeight(st.weights[k]);
  const custom = r => { let s_ = 0, w = 0; for (const [k] of PL_KEYS) { const wt = W(k); if (!wt) continue; s_ += wt * zs[k](r); w += wt; } return w ? s_ / w : 0; };
  const key = { blend: r => r.s?.rating?.blend, epa: r => r.s?.rating?.epa, opr: r => r.t.opr, custom };
  // "Our order": the strategists' dragged ranking (st.order, best first, shared like stars); teams not placed yet follow by blend
  const sorted = m => { if (m === "manual") { const ix = new Map(st.order.map((k, i) => [k, i])), b = key.blend;
      return [...rows].sort((x, y) => (ix.get(x.t.key) ?? 1e6) - (ix.get(y.t.key) ?? 1e6) || (b(y) ?? -1e9) - (b(x) ?? -1e9)).map(r => ({ r, s: ix.has(r.t.key) ? ix.get(r.t.key) + 1 : null })); }
    const kf = key[m] || key.blend; return rows.map(r => ({ r, s: kf(r) })).sort((a, b) => (b.s ?? -1e9) - (a.s ?? -1e9)); };
  // move team k to just before (after = false) or after team t in the full list; dragging in a computed order starts
  // "Our order" from what's on screen
  const move = (k, t, after) => {
    const full = sorted(mode).map(x => x.r.t.key).filter(x => x !== k);
    full.splice(full.indexOf(t) + (after ? 1 : 0), 0, k);
    st.order = full; if (mode !== "manual") { mode = "manual"; lsSet("cyberscout:plmode", mode); }
    save(); render();
  };
  const maxB = Math.max(1, ...rows.map(r => (r.s?.rating?.blend || 0) + (r.s?.rating?.sd || 0)));
  const bbar = r => { const b = r.s?.rating; if (!b || b.blend == null) return "–"; const X = v => Math.max(0, Math.min(100, v / maxB * 100)).toFixed(1);
    return `<span class="sc-prb" title="blend ${fx(b.blend)} ± ${fx(b.sd)} (1 sd)"><i class="rg" style="left:${X(b.blend - b.sd)}%;width:${(X(b.blend + b.sd) - X(b.blend - b.sd)).toFixed(1)}%"></i><i class="mn" style="left:${X(b.blend)}%"></i></span><b>${fx(b.blend)}</b><span class="sc-sd">±${fx(b.sd, 0)}</span>`; };

  // ---- save state: one status line beside the filters, and a banner above the table when something needs a decision
  const stHtml = () => {
    const s = plSt[ev] || { k: "saved" }, m = plMeta[ev] || {};
    if (!plShared()) return `<span class="pl-st">Kept in this browser only</span>`;
    if (s.k === "saving") return `<span class="pl-st">Saving…</span>`;
    if (s.k === "queued") return `<span class="pl-st warn" title="Kept on this device. It's sent when the connection to the server is back.">Waiting to send · offline</span>`;
    if (s.k === "error" || s.k === "conflict") return `<span class="pl-st bad">Not saved</span>`;
    if (s.k === "loaderr") return `<span class="pl-st bad">Not loaded</span>`;
    if (m.version == null) return `<span class="pl-st">${edit ? "Not started: your first change creates the team's list" : "No team list yet"}</span>`;
    const f = fresh && fresh.at === m.at;
    return `<span class="pl-st${f ? " good" : ""}">${f ? "Updated" : "Saved"} · ${esc(plWho(m.by))} · ${esc(plAgo(m.at))}</span>`;
  };
  const alertHtml = () => {
    const s = plSt[ev] || {};
    if (s.k === "loaderr") return `<div class="pl-alert" role="alert"><p><b>Couldn't load the team's picklist:</b> ${esc(plStop(s.why))} This is this device's last copy; nothing can be changed until it loads.</p>
      <div class="acts"><button type="button" class="primary" data-pa="reload">Try again</button></div></div>`;
    if (s.k === "error") return `<div class="pl-alert" role="alert"><p><b>Your change isn't saved.</b> ${esc(plStop(s.why))} It's kept on this device only, so the team doesn't see it yet.</p>
      <div class="acts"><button type="button" class="primary" data-pa="retry">Try again</button><button type="button" data-pa="discard">Discard my change</button></div></div>`;
    if (s.k === "conflict") {
      const c = s.cur || {}, a = plDiff(st, c.data), b = plDiff(c.data, st), who = plWho(c.by);
      const li = (l, x) => { const t = [x.stars.length ? `starred ${x.stars.join(", ")}` : "", x.dnp.length ? `do-not-pick ${x.dnp.join(", ")}` : ""].filter(Boolean).join(" · ");
        return t ? `<li><b>${l}</b> ${esc(t)}</li>` : ""; };
      return `<div class="pl-alert" role="alert"><p><b>Not saved: ${esc(who)} saved the picklist ${esc(plAgo(c.at))}${plMe(c.by) ? " on another device or tab" : ""}, after this page loaded.</b> Nothing is merged automatically: pick which list to keep. Either way the other one stays in History.</p>
        <ul class="pl-diff">${li("Only in yours:", a)}${li("Only in theirs:", b)}${a.order ? "<li>The order is different.</li>" : ""}${a.weights ? "<li>The weights are different.</li>" : ""}</ul>
        <div class="acts"><button type="button" data-pa="theirs">Use theirs (drop my change)</button><button type="button" class="primary" data-pa="mine">Save mine (replaces theirs)</button></div></div>`;
    }
    if (!plShared() || edit) return "";
    return `<p class="pl-ro">${plLocked() ? "Lockdown is on: an admin made everything read-only, so the picklist can't be changed right now." : "Read-only: only strategists and admins change the team picklist. You see their stars, do-not-picks and order."}</p>`;
  };
  const paintSt = () => {
    const a = el.querySelector("#plst"), b = el.querySelector("#plalert"); if (!a || !b) return;
    a.innerHTML = stHtml();
    const h = alertHtml(); if (b.__h !== h) { b.innerHTML = h; b.__h = h; }
    b.querySelectorAll("[data-pa]").forEach(x => x.onclick = () => act(x.dataset.pa, x));
  };
  async function act(a, btn) {
    const s = plSt[ev] || {};
    if (a === "reload") return route(false);
    if (a === "retry") { plTell(ev, { k: "saving" }); return plSend(ev, st, Date.now()); }
    if (a === "discard") {
      const m = plMeta[ev] || {}; plDropDraft(ev); use(m.data || {}); lsSet(plKey(ev), st); plTell(ev, { k: "saved" }); return render();
    }
    if (a === "theirs" && s.cur) { plAdopt(ev, { version: s.cur.version, updated_by: s.cur.by, updated_at: s.cur.at, data: s.cur.data }); use(s.cur.data); return render(); }
    if (a === "mine" && s.cur) { btn.disabled = true; plTell(ev, { k: "saving" }); plSend(ev, st, Date.now(), null, s.cur.version); }
  }
  // someone else saved and this page wasn't changed meanwhile: same edit as theirs = nothing to decide
  document.addEventListener("cs:picklist", onPl);
  function onPl(e) {
    if (!el.isConnected) return document.removeEventListener("cs:picklist", onPl);
    if (e.detail.ev !== ev) return;
    const s = plSt[ev] || {};
    if (s.k === "conflict" && s.cur && plSame(st, s.cur.data)) { plAdopt(ev, { version: s.cur.version, updated_by: s.cur.by, updated_at: s.cur.at, data: s.cur.data }); return; }
    paintSt();
  }
  // offline edits (CLIENT-4): the Client's outbox arrives a moment after the page; show what's waiting on top of the server copy
  const onOutbox = () => {
    if (!el.isConnected) return document.removeEventListener("cs:outbox", onOutbox);
    const q = plQueued(ev), s = plSt[ev] || {};
    if (q.length && (s.k === "saved" || s.k === "queued")) {
      const data = q[q.length - 1].body.data; plSt[ev] = { k: "queued" };
      if (!plSame(data, st)) { use(data); if (!busy()) render(); return; }
    } else if (!q.length && s.k === "queued") { plSt[ev] = { k: "saved" }; poll(); }
    paintSt();
  };
  document.addEventListener("cs:outbox", onOutbox);
  // another strategist's save shows up without a reload (every 20 s, and when the tab comes back)
  const busy = () => dragging || (el.contains(document.activeElement) && document.activeElement.matches("input, textarea"));
  async function poll() {
    if (!plShared() || document.hidden || !el.isConnected || busy()) return;
    if ((plSt[ev] || {}).k !== "saved" || plTimers[ev] || plChain[ev]) return;
    const m = plMeta[ev] || {};
    // no team list yet: ask the list endpoint (an empty list, not a 404 in the console every 20 s)
    let r; try { r = m.version == null ? ((await CS.app("GET", `events/${ev}/picklists`)) || []).find(x => x.name === PL_NAME) : await CS.app("GET", plPath(ev)); } catch (e) { return; }
    if (!r || r.version == null || r.version === m.version || (plSt[ev] || {}).k !== "saved" || plTimers[ev] || plChain[ev] || !el.isConnected) return;
    plAdopt(ev, r); use(r.data); fresh = { at: r.updated_at }; hist.items = null;
    render();
  }
  const iv = setInterval(poll, 20000), onVis = () => { if (!document.hidden) poll(); };
  document.addEventListener("visibilitychange", onVis); window.addEventListener("focus", onVis);
  cleanup.push(() => { clearInterval(iv); document.removeEventListener("visibilitychange", onVis); window.removeEventListener("focus", onVis);
    document.removeEventListener("cs:outbox", onOutbox); document.removeEventListener("cs:picklist", onPl); });

  // ---- History (MATCH-5): who changed what, newest first; strategists can restore any version (saved as a new one)
  const hist = { open: false, items: null, more: false, busy: false, err: "", n: 8 };
  async function loadHist() {
    hist.busy = true; hist.err = ""; paintHist();
    try {
      const vs = await CS.app("GET", `${plPath(ev)}/versions?limit=${hist.n + 1}`) || [];
      const full = await Promise.all(vs.map(v => CS.app("GET", `${plPath(ev)}/versions/${v.version}`).then(x => x.data, () => null)));
      hist.items = vs.map((v, i) => ({ ...v, data: full[i] }));
      hist.more = vs.length > hist.n;
    } catch (e) { hist.err = e.status === 404 ? "No saved versions yet." : (CS.writeErr ? CS.writeErr(e) : e.message); hist.items = []; }
    hist.busy = false; paintHist();
  }
  function histHtml() {
    if (hist.busy && !hist.items) return `<p class="small muted">Loading…</p>`;
    if (hist.err) return `<p class="small muted">${esc(hist.err)}</p>`;
    if (!hist.items || !hist.items.length) return `<p class="small muted">No saved versions yet.</p>`;
    const its = hist.items.slice(0, hist.n);
    return `<ol class="pl-hl">${its.map((v, i) => { const prev = hist.items[i + 1];
      const what = v.note ? [v.note.replace(/^restored v/, "restored version ")] : !v.data || (prev && !prev.data) ? [] : plChanges(prev ? prev.data : null, v.data);
      return `<li><div class="pl-hv"><b>v${v.version}</b> ${esc(plWho(v.updated_by))} · <span title="${esc(new Date(v.updated_at * 1000).toLocaleString())}">${esc(plAgo(v.updated_at))}</span>${v.current ? ' <span class="pill done">current</span>' : ""}</div>
        <div class="small muted">${esc(what.join(" · "))}</div>
        ${edit && plShared() && !v.current ? `<button type="button" class="small" data-rv="${v.version}">Restore</button>` : ""}</li>`; }).join("")}</ol>
      ${hist.more ? `<button type="button" class="small" data-hm="1">Show older</button>` : ""}`;
  }
  function paintHist() {
    const b = el.querySelector("#plhistb"); if (!b) return;
    b.innerHTML = histHtml();
    b.querySelectorAll("[data-rv]").forEach(x => x.onclick = () => restore(+x.dataset.rv, x));
    const more = b.querySelector("[data-hm]"); if (more) more.onclick = () => { hist.n += 8; loadHist(); };
  }
  async function restore(v, btn) {
    const it = (hist.items || []).find(x => x.version === v) || {};
    if (!confirm(`Restore version ${v} (${plWho(it.updated_by)}, ${plAgo(it.updated_at)})? It's saved as a new version, so the current list stays in History.${plDraft(ev) ? " Your unsaved change is dropped." : ""}`)) return;
    btn.disabled = true;
    try {
      const r = await CS.app("POST", `${plPath(ev)}/versions/${v}/restore`);
      plAdopt(ev, r); use(r.data); fresh = null; hist.items = null; render();
    } catch (e) { btn.disabled = false; hist.err = `Not restored: ${CS.writeErr ? CS.writeErr(e) : e.message}`; paintHist(); hist.err = ""; }
  }

  const render = () => {
    const list = sorted(mode);
    const shown = list.filter(x => only === "all" || (only === "star" && st.stars.includes(x.r.t.key)) || (only === "avail" && !st.dnp.includes(x.r.t.key)));
    const dis = x => x && x.r.s ? flagChips(x.r.s, ["video_higher", "video_lower", "auto_not_tracked"]) : "";
    const foc = document.activeElement && el.contains(document.activeElement) && document.activeElement.matches("[data-s],[data-n]") ? (document.activeElement.dataset.s ? `[data-s="${document.activeElement.dataset.s}"]` : `[data-n="${document.activeElement.dataset.n}"]`) : "";
    // read-only (viewers, scouts, lockdown): the marks show as plain icons, no grips or buttons
    const marks = (r, k, star, no) => edit
      ? `<button class="star ${star ? "on" : ""}" data-s="${k}" title="star" aria-label="Star ${r.t.number}" aria-pressed="${star}">${CS.icon(star ? "starOn" : "star")}</button><button class="star ${no ? "on" : ""}" data-n="${k}" title="do not pick" aria-label="Do not pick ${r.t.number}" aria-pressed="${no}">${CS.icon("ban")}</button>`
      : `${star ? `<span class="pl-mk on" title="starred">${CS.icon("starOn")}<span class="vh">starred</span></span>` : '<span class="pl-mk"></span>'}${no ? `<span class="pl-mk no" title="do not pick">${CS.icon("ban")}<span class="vh">do not pick</span></span>` : '<span class="pl-mk"></span>'}`;
    el.innerHTML = `${ts ? `<div class="sc-banner"><b>Replay as of ${fmtTs(+ts)}</b>: ratings, ranks, OPR and video only from data posted before then.</div>` : dataBanner(sd, "ratings: end-of-event dashboard")}<div class="plwrap"><div class="plw card"><h2>Order by ${CS.help(`<span class="hl">Default: <b>blend</b> rating ± sd (expected points/match; best predictor here: rank-order agreement with the day-2 result .92 vs .65 for video-only stats).</span><span class="hl"><b>Custom</b> = weighted z-scores; video metrics are shrunk and missing values count as event average.</span><span class="hl"><b>Our order</b> = the team's own ranking, shared like the stars${edit ? ": drag a row by its handle (or focus the handle and press ↑ ↓). Dragging in any other order starts Our order from it" : ". Strategists set it"}.</span><span class="hl">The order you look at (Blend, EPA, …) is your own view on this device; stars, do-not-picks, weights and Our order are the team's.</span>`, "How the order works")}</h2><div class="chips" id="plm"></div>
      ${mode === "custom" ? PL_KEYS.map(([k, l]) => `<label class="wrow ${k === "climb" && !climbOn ? "sc-dis" : ""}" title="${k === "climb" && !climbOn ? `off: the best robot here averages ${fx(sd?.event_stats?.tower_pts_max)} tower pts/match (≤ 10)` : ""}"><span>${l}</span><input type="range" min="0" max="5" step="0.5" value="${plWeight(st.weights[k])}" data-k="${k}" ${(k === "climb" && !climbOn) || !edit ? "disabled" : ""}><b>${W(k)}</b></label>`).join("") : ""}
      <div class="controls">${mode === "manual" && edit ? '<button id="plro">Clear our order</button>' : ""}${edit ? '<button id="plreset">Reset weights</button>' : ""}<button id="plcopy">Copy list</button></div>
      ${copyTxt ? `<label class="pl-copy"><span class="small muted">Copying isn't allowed on this connection: select the text and copy it.</span><textarea readonly rows="6">${esc(copyTxt)}</textarea></label>` : ""}
      ${plShared() ? `<details class="pl-hist" id="plhist"${hist.open ? " open" : ""}><summary>History</summary><div id="plhistb"></div></details>` : ""}</div>
      <div class="plt card"><h2 class="vh">Team picklist</h2><div class="pl-bar"><div class="chips" id="plf"></div>${CS.help(`<span class="hl">Video columns: verified matches only, shrunk (hover for raw and n); <i>grey italic</i> = n &lt; 3.</span><span class="hl">"video higher" = reconciled video FUEL ≥ 1.15× EPA FUEL + 8. Video FUEL numbers use only alliance results that reconcile with TBA within 20% (hover for n).</span><span class="hl">"video lower?" appears only with ≥ 3 reconciled matches: under-attribution made every "video says worse" call wrong.</span>${climbOn ? "" : `<span class="hl">Climb column hidden: no robot here averages more than 10 tower pts/match (max ${fx(sd?.event_stats?.tower_pts_max)}).</span>`}`, "How to read the picklist")}<span id="plst" role="status" aria-live="polite"></span></div>
      <div id="plalert"></div><div class="tablewrap"><table><thead><tr><th class="num" data-pri="1000">#</th><th data-pri="960"><span class="vh">Star / do not pick</span></th><th data-pri="990">Team</th><th class="num" data-pri="300">Rank</th><th data-pri="800">Blend ± sd</th><th class="num" data-pri="200">EPA</th><th class="num" data-pri="150">OPR</th>
        <th title="verified robot-video matches / played" data-pri="450">Video</th><th class="num" title="FUEL made per match on verified video (≥ = floor)" data-pri="600">Made/m</th><th class="num" data-pri="550">Acc</th><th class="num" data-pri="250">Auto</th>${climbOn ? '<th class="num" data-pri="240">Climb</th>' : ""}<th class="num" title="matches with defense played (episodes) on verified video: third-robot / defender value" data-pri="230">Def.</th><th class="num" title="stopped ≥ 30 s AND the alliance scored under 85% of expectation (TBA); ≥ 3 verified matches" data-pri="220">Dead</th>${mode === "custom" ? '<th class="num" data-pri="850">Score</th>' : ""}<th data-pri="400">Notes</th></tr></thead>
      <tbody>${shown.map((x, i) => { const r = x.r, k = r.t.key, star = st.stars.includes(k), no = st.dnp.includes(k), dd = VID(r, "dead"); return `<tr data-k="${k}" class="${no ? "dnp" : ""} ${k === us ? "usrow" : ""}"><td class="num"><span class="pl-rk">${edit ? `<button class="grip" data-g="${k}" title="Drag to rank" aria-label="Move ${r.t.number}: drag, or arrow keys">${CS.icon("grip")}</button>` : ""}${i + 1}</span></td>
        <td style="white-space:nowrap">${marks(r, k, star, no)}</td><td class="pl-tm"><a class="team" href="#/${ev}/t/${k}">${r.t.number}</a> <span class="muted small pl-nick" title="${esc(r.t.nickname || "")}">${esc(r.t.nickname || "")}</span>${no ? '<span class="vh"> (do not pick)</span>' : ""}</td>
        <td class="num">${r.t.rank ?? "–"}</td><td>${bbar(r)}</td><td class="num">${fx(r.s?.rating?.epa)}</td><td class="num">${fx(r.t.opr)}</td><td>${covPill(r.s?.coverage, { short: true })}</td>
        <td class="num">${svHtml(VID(r, "made_pm"))}</td><td class="num">${svHtml(VID(r, "acc"), "p")}</td><td class="num">${VID(r, "auto_made") ? svHtml(VID(r, "auto_made")) : r.s?.coverage?.n ? '<span class="muted" title="video auto contradicts TBA: not tracked">n/t</span>' : "–"}</td>
        ${climbOn ? `<td class="num">${pct(r.s?.climb?.rate)}</td>` : ""}<td class="num" title="${VID(r, "def_played") || 0} defense episodes in ${VID(r, "def_matches") || 0} matches">${VID(r, "def_matches") || "–"}</td><td class="num">${dd && dd.n >= 3 ? pct(dd.v) : "–"}</td>${mode === "custom" ? `<td class="num"><b>${fx(x.s, 2)}</b></td>` : ""}
        <td>${dis(x)}</td></tr>`; }).join("")}</tbody></table></div></div></div>`;
    chipRow(el.querySelector("#plm"), [["blend", "Blend"], ["epa", "EPA"], ["opr", "OPR"], ["custom", "Custom"], ["manual", "Our order"]], mode, k => { mode = k; lsSet("cyberscout:plmode", k); render(); });
    chipRow(el.querySelector("#plf"), [["all", "All"], ["star", "Starred"], ["avail", "Hide do-not-pick"]], only, k => { only = k; render(); });
    paintSt();
    const hd = el.querySelector("#plhist");
    if (hd) { hd.ontoggle = () => { hist.open = hd.open; if (hd.open && !hist.items && !hist.busy) loadHist(); }; if (hist.open) { if (!hist.items && !hist.busy) loadHist(); else paintHist(); } }
    const cp = el.querySelector(".pl-copy textarea"); if (cp) { cp.focus(); cp.select(); }
    el.querySelector("#plcopy").onclick = async e => {
      const b = e.currentTarget, txt = shown.map((x, i) => `${i + 1}. ${x.r.t.number} ${x.r.t.nickname || ""}${mode === "manual" ? "" : ` (${mode} ${fx(x.s, mode === "custom" ? 2 : 0)})`}${st.stars.includes(x.r.t.key) ? " *" : ""}`).join("\n");
      if (await plCopy(txt)) { copyTxt = ""; b.textContent = "Copied"; setTimeout(() => { if (b.isConnected) b.textContent = "Copy list"; }, 2000); }
      else { copyTxt = txt; render(); }
    };
    if (foc) { const f = el.querySelector(foc); if (f) f.focus(); }
    if (!edit) return;
    el.querySelectorAll(".wrow input").forEach(i => i.oninput = () => { st.weights[i.dataset.k] = plWeight(i.value); save(); i.nextElementSibling.textContent = String(plWeight(i.value)); clearTimeout(render.t); render.t = setTimeout(render, 120); });
    el.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { const k = b.dataset.s, i = st.stars.indexOf(k); i >= 0 ? st.stars.splice(i, 1) : st.stars.push(k); save(); render(); });
    el.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { const k = b.dataset.n, i = st.dnp.indexOf(k); i >= 0 ? st.dnp.splice(i, 1) : st.dnp.push(k); save(); render(); });
    el.querySelector("#plreset").onclick = () => { st.weights = { ...PL_DEFAULT }; save(); render(); };
    const ro = el.querySelector("#plro"); if (ro) ro.onclick = () => { if (!plShared() || confirm("Clear our order for the whole team? History can bring it back.")) { st.order = []; save(); render(); } };
    plDrag(el.querySelector(".plt tbody"), shown.map(x => x.r.t.key), move, on => { dragging = on; });
  };
  render();
}
// Drag-to-rank on the picklist rows: pointer events (mouse, touch, pen) on the grip; arrow keys on a focused grip.
// keys = the rows on screen, in order; move(k, target, after) re-renders; onDrag(true / false) while a row is held.
function plDrag(tb, keys, move, onDrag) {
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
      src.classList.add("pl-drag"); onDrag && onDrag(true);
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
        g.onpointermove = g.onpointerup = g.onpointercancel = null; mark(); src.classList.remove("pl-drag"); onDrag && onDrag(false);
        if (ev.type === "pointerup" && tgt) move(k, tgt, after);
      };
    };
  });
}
