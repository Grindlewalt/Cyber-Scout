"use strict";
// Plugin registry: feature files (web/setup.js, admin.js, sources.js, rankings.js, schedule.js, teams2.js,
// defense.js, ...) call CS.route()/CS.globalRoute()/CS.nav() at load time instead of editing this router.
window.CS = window.CS || { routes: {}, globalRoutes: {}, navItems: [] };
CS.route = (name, fn) => { CS.routes[name] = fn; };            // #/<ev>/<name>/...  -> fn(ev, ...rest)
CS.globalRoute = (name, fn) => { CS.globalRoutes[name] = fn; }; // #/<name>/...      -> fn(...rest)
CS.nav = item => { CS.navItems.push(item); };                   // {label, href: ev => "#/..", order, roles?}

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
 * IDENTITY (tracker v2, all optional; absent => falls back to id_conf / v1 behaviour)
 *   tracks[]  team, team_conf (overall 0..1), conf[] (per replay frame @hz, 0 = unknown/"limbo")
 *   identity  {method, n_reads, per_team_reads:{team:n}, corrections:[{kind:"cut"|"relink", frame, t, t_video, teams[], tracks[],
 *              evidence:[[frame,team,lp,weight]], encounter:{dist_m,xy?}, motion_link?:{frames,jump_m}, conf, anchored, unresolved?:[f0,f1]}]}
 *   shots[]   team:null + unattributed:"low_identity_conf" => quarantined (shooter identity conf < 0.5 at launch), credited to no team
 *   t/<team>.json  team_conf (optional mean) and matches[].team_conf (else matches[].conf); event.json teams[].team_conf|id_conf
 * Calibration: conf 0.8-0.95 ~97% right, >=0.95 ~99%, 0.5-0.8 ~85%, <0.5 unreliable.
 *
 * event.json  teams[].q (recommended, avoids fetching every team file for the table; otherwise derived from t/*.json analytics):
 *   {n,acc,made,att,fire,volley,cycle,intakes,climb,defs,pins,automade,dead (share of matches),role,speed,dist,conf}
 * ============================================================================================ */
// Cyber-Scout front end: hash-routed, reads the JSON the worker writes under api/.
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
// api(): one request per file at a time, and an answer is reused for a few seconds, so a page that asks for the same
// file from several places (router + page + sidebar, or the boot prefetch at the end of this file) fetches it once.
// Every caller gets its own parsed copy (pages add fields to what they get). Polls (60-90 s apart) always refetch;
// each fetch still revalidates with the server (no-cache + ETag -> 304 when unchanged).
// Offline (sync.js): in browser mode the service worker answers from its cache when the server is slow or unreachable
// (headers x-cs-src / x-cs-at), in client mode the local Cyber-Scout Client serves its mirror; CS.sync.seen() turns that
// into the connection pill and the toolbar's "Saved copy · as of …".
const API_MEMO = new Map(), API_TTL = 5000;
async function api(p) {
  const now = Date.now();
  let e = API_MEMO.get(p);
  if (!e || (e.t && now - e.t >= API_TTL)) {
    for (const [k, v] of API_MEMO) if (v.t && now - v.t >= API_TTL) API_MEMO.delete(k);
    const me = e = { t: 0 };
    e.text = fetch("api/" + p, { cache: "no-cache" })
      .then(r => { if (CS.sync) CS.sync.seen(p, r); if (!r.ok) throw new Error(p + ": " + r.status); return r.text(); },
        err => { if (CS.sync) CS.sync.lost(err); throw err; })
      .then(x => { me.t = Date.now(); return x; }, err => { if (API_MEMO.get(p) === me) API_MEMO.delete(p); throw err; });
    API_MEMO.set(p, e);
  }
  return JSON.parse(await e.text);
}
// Honesty labels (coverage, n=, "≥" floors, flags, ID %, confidence bars) explain themselves on a tap or click, not only on
// hover (phones and tablets have no hover): data-tip holds the plain-text explanation and opens in the "?" popover
// (wireHelp below); title keeps the mouse hover. The chips that stand alone (coverage, flags, ID %) are also buttons for
// the keyboard (Tab, Enter); numbers in table cells stay out of the Tab order (the page's "?" explains them).
const tipA = (tip, key) => ` data-tip="${esc(tip)}" title="${esc(tip)}"${key ? ' role="button" tabindex="0" aria-expanded="false"' : ""}`;
function confBar(c, what = "team label confidence") {
  if (c == null) return "";
  const col = c > 0.7 ? "var(--good)" : c > 0.35 ? "var(--warn)" : "var(--bad)";
  return `<span class="conf"${tipA(`${what[0].toUpperCase() + what.slice(1)} ${Math.round(c * 100)}%.${what === "team label confidence" ? " " + ID_CAL : ""}`)}><i style="width:${Math.round(c * 100)}%;background:${col}"></i></span>`;
}
// ---- identity helpers (tracker v2)
const IDC = { hi: 0.8, mid: 0.5, low: 0.6 };      // label tiers; low = team-level "flag this match" threshold
const tconf = t => t.team_conf != null ? t.team_conf : t.id_conf;
const UNATTR_TIP = "Shots whose shooter identity confidence was below 50% at launch. The robot could not be identified reliably, so these are not credited to any team (they still count toward the alliance).";
const ID_CAL = "Calibration: 80-95% confidence is right about 97% of the time, 95%+ about 99%, 50-80% about 85%; under 50% is unreliable.";
function idPill(c, n) {
  if (c == null) return "";
  const cls = c >= IDC.hi ? "good" : c >= IDC.mid ? "warn" : "bad";
  return `<span class="idp ${cls}"${tipA(`Identity confidence ${Math.round(c * 100)}%${n != null ? `, ${n} bumper-number reads` : ""}. ${ID_CAL}`, true)}>${Math.round(c * 100)}%</span>`;
}
// ---- scouting truth (api/<ev>/scout.json, live/scouting.py build_truth; trust rule live/coverage.py)
// One canonical value per team metric for every page. Video numbers: verified (v2, team_conf >= 0.6) matches only,
// n >= 3 -> fact, 1-2 -> grey "n=2 (Q12, Q17)", 0 -> hidden; "≥" when > 20% of the alliance FUEL was not credited.
const SCOUT = {};
function scoutData(ev) {
  return SCOUT[ev] || (SCOUT[ev] = api(`${ev}/scout.json`).then(d => { d.by = Object.fromEntries((d.teams || []).map(t => [t.team, t])); return d; }).catch(() => null));
}
const shortKey = k => { const m = String(k || "").split("_").pop().match(/^([a-z]+)(\d+)(?:m(\d+))?$/); return m ? ({ qm: "Q", sf: "SF", f: "F", qf: "QF" }[m[1]] || m[1].toUpperCase()) + m[2] + (m[3] && m[1] !== "qm" ? "-" + m[3] : "") : k; };
function covPill(c, opts = {}) {
  if (!c || !c.n) return `<span class="sc-cov none"${tipA("No verified robot video for this team: video stats hidden, ratings come from TBA results only.", true)}>no video</span>`;
  const ks = (c.keys || []).map(shortKey).join(", ");
  return `<span class="sc-cov ${c.tier}"${tipA(`Verified video matches (v2 tracker, identity ≥ 60%): ${ks}${c.of ? ` of ${c.of} played` : ""}.${c.tier === "grey" ? " Fewer than 3: treat video numbers as anecdotes." : ""}`, true)}>${opts.short ? `${c.n}${c.of ? "/" + c.of : ""}` : `video ${c.n}${c.of ? "/" + c.of : ""} · last ${shortKey(c.last)}`}</span>`;
}
/** canonical metric {v, raw, n, tier, lower_bound?} -> html.  f: "1" | "0" | "p" | "2" */
function svHtml(m, f = "1", unit = "") {
  if (!m || m.v == null || !m.n) return `<span class="muted">–</span>`;
  const fm = v => v == null ? "–" : f === "p" ? Math.round(v * 100) + "%" : f === "0" ? String(Math.round(v)) : f === "2" ? Number(v).toFixed(2) : Number(v).toFixed(1);
  const tip = `shrunk value ${fm(m.v)}; raw ${fm(m.raw)} over n=${m.n} verified match${m.n === 1 ? "" : "es"}${m.gates ? ` (FUEL from alliance results within 20% of TBA: ${Object.entries(m.gates).map(([k, v]) => `${v} ${k}`).join(", ")})` : ""}${m.lower_bound ? "; ≥ = more than 20% of the alliance FUEL was not credited to any robot, so this is a floor" : ""}${m.over ? "; credited FUEL exceeded the TBA count and was scaled down" : ""}`;
  return `<span class="sc-v ${m.tier || ""}"${tipA(tip[0].toUpperCase() + tip.slice(1) + ".")}>${m.lower_bound ? "≥" : ""}${fm(m.v)}${unit}<small>n=${m.n}</small></span>`;
}
const FLAG_TXT = { auto_not_tracked: ["auto not tracked", "warn"], video_higher: ["video higher", "good"], video_lower: ["video lower?", "grey"], lower_bound: ["≥ floor", "grey"], over: ["scaled to TBA", "grey"], no_reconciled: ["no reconciled video", "grey"] };
function flagChips(row, only) {
  return (row && row.flags || []).filter(f => !only || only.includes(f.k)).map(f => `<span class="sc-flag ${(FLAG_TXT[f.k] || [])[1] || ""}"${tipA(f.text || (FLAG_TXT[f.k] || [f.k])[0], true)}>${esc((FLAG_TXT[f.k] || [f.k])[0])}</span>`).join(" ");
}
/** "End-of-event data" once the competition is over, else "Latest data": everything posted so far (never the replay time) */
function latestLabel(ev) {
  ev = ev || location.hash.replace(/^#\/?/, "").split("/")[0];
  const c = EVK.test(ev || "") ? CS.compInfo(ev) : null;
  return c && compStatus(c) === "done" ? "End-of-event data" : "Latest data";
}
CS.latestLabel = latestLabel;
/** banner for pages that show the latest data (everything posted so far, not the replay time the dashboard may be set to) */
function dataBanner(sd, what = "") {
  const lv = sd && sd.last_verified ? shortKey(sd.last_verified) : null, vm = sd ? (sd.verified_matches || []) : [];
  const help = `<span class="hl">These numbers use everything posted so far, not the replay time the Dashboard may be set to.</span>${sd ? `<span class="hl">Robot video: ${vm.length} verified match${vm.length === 1 ? "" : "es"}${lv ? `, last ${lv}` : ""} · ${sd.quals_played} qual${sd.quals_played === 1 ? "" : "s"} played.</span><span class="hl">Which matches: ${esc(vm.map(shortKey).join(", ") || "none yet")}</span>` : ""}`;
  return `<div class="sc-banner"><b>${latestLabel()}</b>, not replay time${what ? " · " + esc(what) : ""}${sd ? ` · ${vm.length} verified video match${vm.length === 1 ? "" : "es"}${lv ? `, last ${lv}` : ""}` : ""} ${CS.help(help, "About this data")}</div>`;
}
/** toolbar title: the last crumb is the page title, earlier ones are a back path. The event itself lives in the sidebar. */
function crumbs(parts) {
  const ev = (location.hash.replace(/^#\/?/, "").split("/")[0]) || "";
  const ps = parts.filter(([, h], i) => !(i === 0 && h && (h === `#/${ev}` || h === "#/")) || parts.length === 1);
  const last = ps[ps.length - 1];
  $crumbs.innerHTML = ps.map(([t, h], i) => i < ps.length - 1 && h ? `<a href="${h}">${esc(t)}</a><span class="sep" aria-hidden="true">/</span>` : `<span>${esc(t)}</span>`).join("");
  pageTitle = last ? last[0] : ""; applyTitle();
}
/** one date format for every page: "11:03 PM" today, "Oct 12, 11:03 PM" this year, "Oct 12, 2025, 11:03 PM" before.
 *  opts.sec adds seconds (logs), opts.day forces the date even for today. */
CS.fmtWhen = (ts, opts = {}) => {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date(), today = d.toDateString() === now.toDateString();
  const t = { hour: "numeric", minute: "2-digit" }; if (opts.sec) t.second = "2-digit";
  if (today && !opts.day) return d.toLocaleTimeString([], t);
  return d.toLocaleString([], Object.assign({ month: "short", day: "numeric" }, d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}, t));
};
function ago(ts) {
  if (!ts) return "";
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 90) return "updated just now";
  if (s < 5400) return `updated ${Math.round(s / 60)} min ago`;
  return `updated ${new Date(ts * 1000).toDateString() === new Date().toDateString() ? "today " : ""}${CS.fmtWhen(ts)}`;
}
/** a server message as a sentence: capital first letter, full stop ("invalid username or password" -> "Invalid ...") */
CS.sentence = m => { const t = String(m || "").trim(); return !t ? "" : t[0].toUpperCase() + t.slice(1) + (/[.!?…)]$/.test(t) ? "" : "."); };

// ------------------------------------------------------------------ livestream fast results (live/liveingest.py LiveView.decorate_row)
// Match rows (event.json matches[], m/<match>.json) may carry live {clip, tracking, score, ...} and score_source: "livestream" while
// the score comes from the stream's results screen (TBA has not posted yet), "tba" after.
CS.isStream = m => !!m && m.score_source === "livestream";
CS.liveTag = (m, cls = "") => CS.isStream(m) ? `<span class="livetag ${cls}" title="Score read off the livestream's results screen. TBA hasn't posted this match yet.">LIVE · stream</span>` : "";
/** a media file the site serves (relative "media/...", as the worker writes it), else null: never another scheme or host */
CS.mediaUrl = u => typeof u === "string" && /^\/?media\/[\w\-./]+$/.test(u) && !/\.\./.test(u) ? u.replace(/^\//, "") : null;
/** only http(s) URLs become links (security audit); everything else is shown as text */
CS.safeHref = u => typeof u === "string" && /^https?:\/\/[^\s]+$/i.test(u.trim()) ? u.trim() : null;
/** quiet status of a stream-result match: tracked = it already has tracking data */
CS.liveTracking = (live, tracked) => !live || !live.tracking ? "" : tracked ? (live.tracking === "from_stream" ? "Tracked from stream" : "")
  : live.tracking === "from_stream" ? "Tracking from stream" : "Waiting for uploads";

// ------------------------------------------------------------------ "?" help: explanations stay out of the way until asked for
// CS.help(html, label) -> a real <button aria-expanded> followed by its popover (inline content only: span/b/br/a, no <p>/<div>,
// so it is valid inside paragraphs, table headers and summaries). One open at a time; click outside or Escape closes it.
let hlpN = 0;
CS.help = (html, label = "What this means") => {
  const id = "hlp" + (++hlpN);
  return `<span class="hlp"><button type="button" class="hlp-b" aria-expanded="false" aria-controls="${id}" aria-label="${esc(label)}" title="${esc(label)}">?</button><span class="hlp-p" id="${id}" role="note" hidden>${html}</span></span>`;
};
(function wireHelp() {
  let open = null, tipP = null;
  const close = focus => { if (!open) return; open.p.hidden = true; if (open.b.hasAttribute("aria-expanded")) open.b.setAttribute("aria-expanded", "false"); if (focus) open.b.focus(); open = null; };
  // a [data-tip] label (tipA above): its text in one shared popover
  const openTip = b => {
    if (open && open.b === b) return close();
    close();
    if (!tipP) { tipP = document.createElement("span"); tipP.className = "hlp-p"; tipP.id = "cstip"; tipP.setAttribute("role", "note"); tipP.hidden = true; document.body.appendChild(tipP); }
    tipP.textContent = b.dataset.tip; tipP.hidden = false;
    if (b.hasAttribute("aria-expanded")) { b.setAttribute("aria-expanded", "true"); b.setAttribute("aria-controls", "cstip"); }
    open = { b, p: tipP }; place();
  };
  const place = () => {
    if (!open) return;
    const r = open.b.getBoundingClientRect(), p = open.p; if (!r.width || !open.b.isConnected) return close();
    const vw = document.documentElement.clientWidth, vh = innerHeight, pw = p.offsetWidth, ph = p.offsetHeight;
    let y = r.bottom + 6; if (y + ph > vh - 8 && r.top - ph - 6 > 8) y = r.top - ph - 6;
    p.style.left = Math.max(8, Math.min(r.left - 10, vw - pw - 8)) + "px"; p.style.top = Math.max(8, y) + "px";
  };
  CS.closeHelp = close;
  document.addEventListener("click", e => {
    const b = e.target.closest && e.target.closest(".hlp-b");
    if (b) {
      e.preventDefault(); e.stopPropagation();
      if (open && open.b === b) return close();
      close(); const p = document.getElementById(b.getAttribute("aria-controls")); if (!p) return;
      p.hidden = false; b.setAttribute("aria-expanded", "true"); open = { b, p }; place(); return;
    }
    const t = e.target.closest && !e.target.closest(".hlp-p") && e.target.closest("[data-tip]");
    if (t) { e.preventDefault(); e.stopPropagation(); openTip(t); return; }
    if (open && !open.p.contains(e.target)) close();
  }, true);
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && open) { e.stopPropagation(); close(true); return; }
    const t = (e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("[data-tip][role=button]") ? e.target : null;
    if (t) { e.preventDefault(); e.stopPropagation(); openTip(t); }
  }, true);
  addEventListener("scroll", place, true); addEventListener("resize", place);
})();

// ------------------------------------------------------------------ tables never scroll sideways
// Every <table> with a <thead> inside .tablewrap is fitted to its width: columns that do not fit (lowest priority first) fold into a
// per-row "more" disclosure. Priority: <th data-pri="N"> (0 = details only, always folded); default keeps the first two columns
// and folds from the right. Re-fits on render (MutationObserver), resize and <details> toggle.
const CHEV = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>';
function fitTable(t) {
  const wrap = t.parentElement; if (!wrap || !t.tHead || !t.tHead.rows.length) return;
  // reset (an open detail row gives its cells' contents back first)
  t.querySelectorAll(".fx-h").forEach(c => c.classList.remove("fx-h"));
  t.querySelectorAll("tr.fx-d").forEach(fitRestore);
  t.querySelectorAll(".fx-t").forEach(c => c.remove()); t.classList.remove("fx");
  const avail = wrap.clientWidth; if (!avail) return;
  const head = t.tHead.rows[t.tHead.rows.length - 1], hs = [...head.cells];
  if (hs.some(c => c.colSpan > 1)) return;
  const n = hs.length, rows = [...t.tBodies].flatMap(b => [...b.rows]).filter(r => r.cells.length === n && ![...r.cells].some(c => c.colSpan > 1));
  const pri = hs.map((h, j) => h.dataset.pri != null ? +h.dataset.pri : j === 0 ? 1000 : j === 1 ? 900 : 50 + (n - j));
  const order = hs.map((_, j) => j).filter(j => pri[j] < 900).sort((a, b) => pri[a] - pri[b] || b - a);
  const hidden = [];
  const hide = j => { hidden.push(j); hs[j].classList.add("fx-h"); rows.forEach(r => r.cells[j].classList.add("fx-h")); };
  const over = () => t.offsetWidth > avail + 1;
  order.filter(j => pri[j] === 0).forEach(hide);
  if (!hidden.length && !over()) { t.__fitW = avail; return; }
  // the toggle column
  t.classList.add("fx");
  const th = document.createElement("th"); th.className = "fx-t"; th.innerHTML = '<span class="vh">More</span>'; head.appendChild(th);
  [...t.tHead.rows].slice(0, -1).forEach(r => r.classList.add("fx-h"));
  rows.forEach(r => { const td = document.createElement("td"); td.className = "fx-t";
    const c0 = r.cells[0], lab = (c0.dataset.label || (c0.querySelector("b, a, strong") || c0).textContent || "").trim().slice(0, 40);
    td.innerHTML = `<button type="button" class="fx-x" aria-expanded="false" aria-label="More for ${esc(lab)}">${CHEV}</button>`; r.appendChild(td); });
  for (const j of order) { if (!over()) break; if (!hidden.includes(j)) hide(j); }
  // give back what fits again (a wide early column may have been the only problem), most important first
  const unhide = j => { hidden.splice(hidden.indexOf(j), 1); hs[j].classList.remove("fx-h"); rows.forEach(r => r.cells[j].classList.remove("fx-h")); };
  for (const j of hidden.slice().filter(j => pri[j] > 0).sort((a, b) => pri[b] - pri[a] || a - b)) { unhide(j); if (over()) hide(j); }
  if (!hidden.length) { t.querySelectorAll(".fx-t").forEach(c => c.remove()); t.classList.remove("fx"); [...t.tHead.rows].forEach(r => r.classList.remove("fx-h")); }
  t.__fit = { hidden: hidden.slice().sort((a, b) => a - b), hs };
  t.__fitW = avail;
}
// The detail row shows the folded cells' real nodes (moved in, moved back on close / re-fit), not copies: buttons, selects
// and links keep their handlers and state (copying innerHTML left Admin's Reset password / Disable / Delete dead).
// It carries the row's data-* attributes, so delegated handlers that read tr.dataset still find the row.
function fitRestore(d) {
  (d.__src || []).forEach(([cell, dd]) => { if (dd) cell.append(...dd.childNodes); });
  d.remove();
}
function fitDetail(btn) {
  const tr = btn.closest("tr"), t = tr.closest("table"), f = t.__fit; if (!f) return;
  const nx = tr.nextElementSibling;
  if (nx && nx.classList.contains("fx-d")) { fitRestore(nx); btn.setAttribute("aria-expanded", "false"); return; }
  const vis = [...tr.cells].filter(c => !c.classList.contains("fx-h")).length;
  const lab = h => { const c = h.cloneNode(true); c.querySelectorAll(".hlp").forEach(x => x.remove()); return esc((c.textContent || "").replace(/[▲▼▾▴]/g, "").trim()); };
  const d = document.createElement("tr"); d.className = "fx-d";
  for (const a of tr.attributes) if (a.name.startsWith("data-")) d.setAttribute(a.name, a.value);
  d.innerHTML = `<td colspan="${vis}"><dl class="fx-dl">${f.hidden.map(j => `<div><dt${f.hs[j].title ? ` title="${esc(f.hs[j].title)}"` : ""}>${lab(f.hs[j])}</dt><dd></dd></div>`).join("")}</dl></td>`;
  const dds = d.querySelectorAll("dd");
  d.__src = f.hidden.map((j, i) => {
    const cell = tr.cells[j], dd = dds[i];
    if (!cell.childNodes.length) { dd.textContent = "–"; return [cell, null]; }
    dd.append(...cell.childNodes); return [cell, dd];
  });
  tr.after(d); btn.setAttribute("aria-expanded", "true");
}
function fitAll(force) {
  for (const t of $app.querySelectorAll(".tablewrap > table")) {
    const w = t.parentElement.clientWidth;
    const stale = t.classList.contains("fx") && [...t.tBodies].some(b => [...b.rows].some(r => !r.classList.contains("fx-d") && !r.querySelector(":scope > .fx-t") && r.cells.length > 1 && ![...r.cells].some(c => c.colSpan > 1)));
    if (force || stale || t.__fitW !== w) fitTable(t);
  }
}
CS.fit = fitAll;
// sortable headers (Teams, Specs, Rankings: a <th data-k> with a click handler) work from the keyboard and say how the
// column is sorted (aria-sort from the page's ▲ / ▼ mark); Enter / Space sorts and keeps focus on that header
function a11ySort() {
  for (const th of $app.querySelectorAll("th[data-k]")) {
    if (!th.onclick || th.dataset.sorta) continue;
    th.dataset.sorta = "1";
    if (!th.hasAttribute("tabindex")) th.tabIndex = 0;
    if (!th.hasAttribute("aria-sort")) { const tx = th.textContent; th.setAttribute("aria-sort", /[▲▴]/.test(tx) ? "ascending" : /[▼▾]/.test(tx) ? "descending" : "none"); }
    th.addEventListener("keydown", e => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      const k = th.dataset.k, ti = [...$app.querySelectorAll("table")].indexOf(th.closest("table"));
      th.click();
      requestAnimationFrame(() => { const t = $app.querySelectorAll("table")[ti], n = t && [...t.querySelectorAll("th[data-k]")].find(x => x.dataset.k === k); if (n) n.focus(); });
    });
  }
}
(function wireFit() {
  let q = 0;
  const later = force => { cancelAnimationFrame(q); q = requestAnimationFrame(() => fitAll(force)); };
  new MutationObserver(() => { fitAll(); a11ySort(); }).observe($app, { childList: true, subtree: true });
  addEventListener("resize", () => later(true));
  document.addEventListener("toggle", () => later(), true);
  document.addEventListener("click", e => { const b = e.target.closest && e.target.closest(".fx-x"); if (b) { e.stopPropagation(); fitDetail(b); } }, true);
})();

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
  crumbs([["Competitions"]]); $updated.textContent = "";
  const l = await loadComps(true);
  const sv = lsGet2(localStorage, "cs:lastev"), pick = l.find(c => c.key === sv) || defaultComp(l);
  if (pick) { location.replace("#/" + pick.key); return; }
  const admin = CS.platform && CS.user && CS.can("admin");
  $app.innerHTML = `<div class="empty"><b>No competitions yet</b>${admin ? "Add the competition you want to scout: Cyber-Scout tracks its matches as they're played."
    : "A competition appears here once an admin adds it (and the worker has synced it)."}${admin ? `<div class="acts"><button type="button" class="primary" id="addcomp">Add competition…</button></div>` : ""}</div>`;
  const b = document.getElementById("addcomp"); if (b) b.onclick = () => openComps(null, true);
}

// The old tab links (#/<ev>/compare, picklist, matches, specs, defense) are redirects (see OLD_LINKS below); an unknown
// #/<ev>/<word> lands on Teams. eventPage is only the fallback for those.
async function eventPage(ev, tab, arg) {
  location.replace(`#/${ev}/teams`);
}
function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { } }

const STATE_LABEL = { done: "tracked", pending: "waiting", no_video: "no video yet", processing: "processing", error: "retrying", failed: "failed", live: "stream result",
                      waiting_live: "waiting for stream clip" };
const WAITING = new Set(["pending", "no_video", "waiting_live", "error"]);
function statusPill(m) {
  if (m.state === "done" && m.feed_ok != null && m.feed_ok < 0.9)
    return `<span class="pill no_video"${tipA("The field camera was missing from the broadcast for part of this match.", true)}>partial footage (${Math.round(m.feed_ok * 100)}%)</span>`;
  if (m.state === "live" && m.live) return `<span class="pill live"${m.live.tracking_note ? tipA(m.live.tracking_note, true) : ""}>${CS.liveTracking(m.live, false).toLowerCase() || "stream result"}</span>`;
  // while processing is paused nothing is "waiting": it won't be tracked until an admin turns processing back on
  if (WAITING.has(m.state) && CS.processing && CS.processing.paused)
    return `<span class="pill no_video"${tipA("Not tracked: processing is paused on the server. " + (m.state_note || ""), true)}>not tracked (paused)</span>`;
  return `<span class="pill ${m.state}"${m.state_note ? tipA(m.state_note, true) : ""}>${STATE_LABEL[m.state] || esc(m.state)}</span>`;
}
// Matches: every match with its tracking state; "ours only" and a search (team number or match, as on Schedule)
const MT = { ev: null, ours: null, q: "" };
function matchesTable(el, d, ev) {
  if (MT.ev !== ev) Object.assign(MT, { ev, ours: null, q: "" });
  const tl = (ts, al, us) => ts.map(t => `<a class="team ${al[0]}${t === us ? " us" : ""}" href="#/${ev}/t/${t}">${num(t)}</a>`).join(" ");
  const short = m => m.short || shortKey(m.key);
  el.innerHTML = `<div class="mt-bar"><div class="chips" role="group" aria-label="Show"></div>
      <label class="mt-q"><span class="vh">Find a match or team</span><input class="csin" type="search" id="mtq" placeholder="Team or match (4911, Q12)…" autocomplete="off" spellcheck="false" enterkeyhint="search" value="${esc(MT.q)}"></label></div>
    <div class="tablewrap"><table><thead><tr><th>Match</th><th data-pri="800">Red</th><th class="num" data-pri="850">Score</th><th data-pri="790">Blue</th><th data-pri="40">Tracking</th><th data-pri="30">ID confidence ${CS.help(`How sure the tracker is about which robot is which team in this match (bumper numbers read off the video). ${ID_CAL}`, "What ID confidence means")}</th></tr></thead><tbody></tbody></table></div>
    <div class="muted small mt-none" hidden></div>`;
  const tb = el.querySelector("tbody"), chips = el.querySelector(".chips"), none = el.querySelector(".mt-none");
  const draw = us => {
    const q = MT.q.trim().toLowerCase().replace(/^frc/, ""), ours = !!(us && MT.ours);
    const ms = d.matches.filter(m => (!ours || m.red.teams.includes(us) || m.blue.teams.includes(us))
      && (!q || short(m).toLowerCase() === q || (m.label || "").toLowerCase().includes(q) || m.red.teams.concat(m.blue.teams).some(t => num(t) === q)));
    chips.innerHTML = us ? [["0", "All", d.matches.length], ["1", `${num(us)} only`, d.matches.filter(m => m.red.teams.includes(us) || m.blue.teams.includes(us)).length]]
      .map(([k, t, n]) => `<button type="button" class="chip${(k === "1") === ours ? " on" : ""}" data-ours="${k}" aria-pressed="${(k === "1") === ours}">${esc(t)} <span class="chipn">${n}</span></button>`).join("") : "";
    tb.innerHTML = ms.map(m => `<tr>
      <td data-label="${esc(short(m))}">${m.state === "done" || (m.live && CS.mediaUrl(m.live.clip)) ? `<a href="#/${ev}/m/${m.key}" title="${esc(m.label)}"><b>${esc(short(m))}</b></a>` : `<b title="${esc(m.label)}">${esc(short(m))}</b>`}</td>
      <td>${tl(m.red.teams, "red", us)}</td>
      <td class="num"><span class="score r ${m.winner === "red" ? "win" : ""}">${m.red.score ?? "–"}</span>
        <span class="muted">–</span> <span class="score b ${m.winner === "blue" ? "win" : ""}">${m.blue.score ?? "–"}</span>${CS.liveTag(m, "blk")}</td>
      <td>${tl(m.blue.teams, "blue", us)}</td>
      <td>${statusPill(m)}</td>
      <td>${confBar(m.team_conf ?? m.id_conf) || '<span class="muted">–</span>'}</td></tr>`).join("");
    none.hidden = !!ms.length; none.textContent = ms.length ? "" : `No match matches “${MT.q.trim()}”${ours ? ` among ${num(us)}'s` : ""}.`;
  };
  let us = null;
  draw(null);
  api(`${ev}/schedule.json`).then(x => {
    us = x && /^frc\d+$/.test(x.team || "") ? x.team : null;
    if (MT.ours == null) MT.ours = !!us && d.matches.some(m => m.red.teams.includes(us) || m.blue.teams.includes(us)) && compStatus(CS.compInfo(ev) || {}) === "live";
    if (us && el.isConnected) draw(us);
  }).catch(() => { });
  chips.onclick = e => { const b = e.target.closest("[data-ours]"); if (b) { MT.ours = b.dataset.ours === "1"; draw(us); } };
  let t = 0;
  el.querySelector("#mtq").oninput = e => { MT.q = e.target.value; clearTimeout(t); t = setTimeout(() => draw(us), 120); };
  const re = () => { if (el.isConnected) draw(us); else removeEventListener("cs:processing", re); };
  addEventListener("cs:processing", re); cleanup.push(() => removeEventListener("cs:processing", re));
}

// ------------------------------------------------------------------ match page
// One match page: the viewer in player.js (video / field / overlay, one View menu) with the Teams panel and Robots summary.
// #/<ev>/a/<key> is the same page (kept for old links and the defense library's ?t=<video s>).
const matchPage = (ev, key) => analyzePage(ev, key);

// ------------------------------------------------------------------ shell: one navigation that never moves
// Sidebar (desktop) / bottom tab bar + "More" sheet (phone) built from CS.nav items. Every section is always listed in
// the same place; the current one is marked (aria-current). Items: {label, key, href(ev, ts), order, group, icon,
// tab (phone tab bar), replay (href takes the shared as-of time), global, roles|minRole}.
const NAV_GROUPS = [["day", "Match day"], ["picks", "Picks"], ["scout", "Scouting"], ["manage", "Manage"]];
const ICON = {
  dash: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3.5h6v3H9zM8.5 11.5h7M8.5 15.5h5"/>',
  schedule: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  rankings: '<path d="M5 20v-8M12 20V5M19 20v-5"/>',
  selection: '<circle cx="12" cy="7.5" r="2.6"/><circle cx="5.5" cy="10" r="2.1"/><circle cx="18.5" cy="10" r="2.1"/><path d="M7.5 19.5c.6-3.3 2.3-5 4.5-5s3.9 1.7 4.5 5M2.5 18.5c.4-2.2 1.5-3.4 3-3.6M21.5 18.5c-.4-2.2-1.5-3.4-3-3.6"/>',
  picklist: '<path d="M11 6.5h9M11 12h9M11 17.5h9"/><path d="M5.5 4l1.1 2.2 2.4.35-1.75 1.7.4 2.4-2.15-1.13-2.15 1.13.4-2.4L2 6.55l2.4-.35z"/>',
  teams: '<circle cx="9" cy="8.5" r="3"/><path d="M3.5 19.5c.7-3.5 2.8-5.5 5.5-5.5s4.8 2 5.5 5.5M15.5 5.8a3 3 0 0 1 0 5.4M17.5 14c1.6.6 2.6 2.4 3 5.2"/>',
  compare: '<rect x="3.5" y="4.5" width="7" height="15" rx="1.5"/><rect x="13.5" y="4.5" width="7" height="15" rx="1.5"/>',
  matches: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M10 9.2v5.6l4.6-2.8z"/>',
  play: '<path d="M7.5 4.8v14.4a.8.8 0 0 0 1.2.7l11.3-7.2a.8.8 0 0 0 0-1.4L8.7 4.1a.8.8 0 0 0-1.2.7z"/>',
  specs: '<path d="M7 3.5h7l4 4v13H7z"/><path d="M14 3.5v4h4M9.5 12h6M9.5 15.5h6"/>',
  defense: '<path d="M12 3.5l7 2.5v5.5c0 4.3-2.9 7.7-7 9-4.1-1.3-7-4.7-7-9V6z"/>',
  video: '<rect x="3" y="6.5" width="13" height="11" rx="2"/><path d="M16 10.5l5-3v9l-5-3z"/>',
  admin: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  activity: '<path d="M4.2 13.5A8 8 0 1 0 6.3 6.3"/><path d="M3.8 4.5v4.2H8"/><path d="M12 8v4.4l3 1.8"/>',
  account: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-4 3.5-6 7-6s6.2 2 7 6"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/><circle cx="12" cy="12" r="6.6"/>',
  more: '<circle cx="6" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="18" cy="12" r="1.3" fill="currentColor"/>',
  // inline UI glyphs (same 24-grid, 1.8 stroke): used instead of unicode stars/arrows/crosses
  star: '<path d="M12 3.8l2.5 5.1 5.6.8-4 3.9.9 5.6-5-2.6-5 2.6.9-5.6-4-3.9 5.6-.8z"/>',
  grip: '<circle cx="9" cy="6.5" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="6.5" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="17.5" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="17.5" r="1.4" fill="currentColor" stroke="none"/>',
  starOn: '<path d="M12 3.8l2.5 5.1 5.6.8-4 3.9.9 5.6-5-2.6-5 2.6.9-5.6-4-3.9 5.6-.8z" fill="currentColor"/>',
  external: '<path d="M14 4.5h5.5V10M19.5 4.5l-8 8M17 14v4.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1H10"/>',
  chevL: '<path d="M14.5 6l-6 6 6 6"/>',
  chevR: '<path d="M9.5 6l6 6-6 6"/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  up: '<path d="M12 19V5.5M6.5 11L12 5.5l5.5 5.5"/>',
  warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.2v.1"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  ban: '<circle cx="12" cy="12" r="8"/><path d="M6.5 6.5l11 11"/>',
  updown: '<path d="M8 9.5l4-4 4 4M8 14.5l4 4 4-4"/>',
  down: '<path d="M6.5 9.5l5.5 5.5 5.5-5.5"/>',
  plus: '<path d="M12 5.5v13M5.5 12h13"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M10.9 12.1L19 4M15.5 7.5l2.5 2.5M17.5 5.5l2 2"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>',
  power: '<path d="M12 3.5v8"/><path d="M7.2 6.6a7 7 0 1 0 9.6 0"/>',
};
CS.icon = (k, cls) => `<svg${cls ? ` class="${cls}"` : ""} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k] || ICON.more}</svg>`;
const withTs = (base, ts) => base + (ts ? "/" + ts : "");
CS.nav({ label: "Dashboard", key: "dash", href: (ev, ts) => withTs(`#/${ev}/dash`, ts), order: 10, group: "day", icon: "dash", tab: true, replay: true });
CS.nav({ label: "Teams", key: "teams", href: ev => `#/${ev}/teams`, order: 35, group: "day", icon: "teams", tab: true });
// Matches, Specs, Defense, Compare and Picklist are not nav items any more: Matches became the Schedule, Specs became each
// team's fields (Teams), and Compare / Picklist / Defense are tabs of Selection (rankings.js). Their old links redirect.

const lsGet2 = (s, k) => { try { return s.getItem(k); } catch (e) { return null; } };
const lsSet2 = (s, k, v) => { try { v == null ? s.removeItem(k) : s.setItem(k, v); } catch (e) { } };
CS.asofGet = ev => lsGet2(sessionStorage, "cs:asof:" + ev) || null;
CS.asofSet = (ev, ts) => lsSet2(sessionStorage, "cs:asof:" + ev, ts || null);
// ------------------------------------------------------------------ competitions: the switcher
// Every followed event is a separate competition: its own teams, matches, scoring, picklists, notes, identity fixes and
// video setup (all under #/<ev>/... and app/v1/events/<ev>/...). Accounts, roles, team number and theme are shared.
// List: app/v1/events when signed in (dates, place, progress, needs_setup), else the site's api/events.json.
// The last one picked is remembered per device (localStorage cs:lastev); with none: live now > next upcoming > most recent.
const EVK = /^\d{4}[a-z0-9]{1,16}$/;
let comps = null, compsAt = 0, compsFor = null, compsP = null;
const pad2 = n => String(n).padStart(2, "0");
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
function compStatus(c) {
  const s = c && c.start_date, e = (c && c.end_date) || s, t = todayIso();
  return !s ? "unknown" : t < s ? "upcoming" : t > e ? "done" : "live";
}
const ST_ORDER = { live: 0, upcoming: 1, done: 2, unknown: 3 };
/** live (earliest start first), upcoming (soonest first), done (most recent first), dates unknown */
function sortComps(l) {
  return l.slice().sort((a, b) => {
    const sa = compStatus(a), sb = compStatus(b);
    if (sa !== sb) return ST_ORDER[sa] - ST_ORDER[sb];
    if (sa === "done") return (b.end_date || b.start_date || "").localeCompare(a.end_date || a.start_date || "");
    return (a.start_date || "").localeCompare(b.start_date || "") || a.key.localeCompare(b.key);
  });
}
const defaultComp = l => sortComps(l)[0] || null;
function loadComps(force) {
  const who = CS.platform ? (CS.user ? CS.user.id : 0) : -1;
  if (!force && comps && compsFor === who && Date.now() - compsAt < 60000) return Promise.resolve(comps);
  if (compsP) return compsP;
  const norm = e => ({ key: e.key, name: e.name || e.key, short_name: e.short_name || null, start_date: e.start_date || e.start || null,
    end_date: e.end_date || e.end || null, city: e.city || null, state_prov: e.state_prov || null, n_done: e.n_done ?? null, n_total: e.n_total ?? null,
    needs_setup: e.needs_setup != null ? !!e.needs_setup : e.calibrated === false, updated: e.updated || null });
  const src = CS.platform && CS.user ? CS.app("GET", "events") : api("events.json");
  compsP = src.then(l => { comps = (Array.isArray(l) ? l : []).filter(e => e && EVK.test(e.key || "")).map(norm); compsAt = Date.now(); compsFor = who; return comps; },
    () => (comps = comps || []))
    .finally(() => { compsP = null; });
  return compsP;
}
CS.comps = loadComps;
CS.compInfo = ev => (comps || []).find(c => c.key === ev) || null;
const compName = ev => (CS.compInfo(ev) || {}).name || ev;
const compShort = ev => { const c = CS.compInfo(ev); return c ? c.short_name || c.name : ev; };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function compDates(c) {
  if (!c || !c.start_date) return "";
  const [y0, m0, d0] = c.start_date.split("-").map(Number), [y1, m1, d1] = (c.end_date || c.start_date).split("-").map(Number);
  const yr = y1 !== new Date().getFullYear() ? `, ${y1}` : "";
  if (c.end_date === c.start_date || !c.end_date) return `${MON[m0 - 1]} ${d0}${yr}`;
  return m0 === m1 && y0 === y1 ? `${MON[m0 - 1]} ${d0}–${d1}${yr}` : `${MON[m0 - 1]} ${d0} – ${MON[m1 - 1]} ${d1}${yr}`;
}
/** "Elk Grove, CA" (TBA's city sometimes ends with a comma: "Elk Grove,") */
const placeOf = c => [c && c.city, c && c.state_prov].map(x => String(x || "").replace(/[\s,]+$/, "")).filter(Boolean).join(", ");
function compStatusText(c) {
  const st = compStatus(c);
  if (st === "live") return "Live now";
  if (st === "done") return "Done";
  if (st === "upcoming") { const days = Math.round((new Date(c.start_date + "T00:00:00") - new Date(todayIso() + "T00:00:00")) / 864e5);
    return days <= 1 ? "Starts tomorrow" : days <= 14 ? `In ${days} days` : "Upcoming"; }
  return "Dates unknown";
}
const compProgress = c => c && c.n_total ? `${c.n_done ?? 0} of ${c.n_total} tracked` : c && compStatus(c) === "upcoming" ? "no schedule yet" : "not built yet";
/** where switching to ev2 lands: the same page type (Teams -> Teams, Picklist -> Picklist); a team or match page falls back to
 * the competition's team list / dashboard; pages outside a competition -> its dashboard */
function compHref(ev2) {
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (!p.length || CS.globalRoutes[p[0]] || !EVK.test(p[0])) return `#/${ev2}`;
  const s = p[1];
  if (s === "t" || s === "teams") return `#/${ev2}/teams`;
  if (s === "selection") return `#/${ev2}/selection` + (p[2] && SEL_TABS.has(p[2]) ? "/" + p[2] : "");
  if (!s || s === "dash" || s === "m" || s === "a") return `#/${ev2}`;
  const it = CS.navItems.find(i => !i.global && i.key === s);
  return it ? it.href(ev2, null) : `#/${ev2}`;
}
CS.compHref = compHref;
/** which nav key a hash belongs to (team page -> Teams, match/analyze -> Schedule, Compare / Picklist / Defense -> Selection) */
function activeKey(p) {
  if (!p.length) return null;
  if (CS.globalRoutes[p[0]]) return p[0];
  const s = p[1];
  if (!s || s === "dash") return "dash";
  if (s === "t" || s === "specs") return "teams";
  if (s === "m" || s === "a" || s === "matches") return "schedule";
  if (s === "compare" || s === "picklist" || s === "defense") return "selection";
  return s;
}
// Selection's tabs: #/<ev>/selection[/<ts>] (Odds), /picklist[/<ts>], /compare[/<teams>], /defense[/<team|defenders>]
const SEL_TABS = new Set(["odds", "picklist", "compare", "defense"]);
/** the replay time in a Selection url: odds = selection/<ts> or selection/odds/<ts>, picklist = selection/picklist/<ts>; else null */
const selTs = p => /^\d{9,}$/.test(p[2] || "") ? p[2] : (p[2] === "odds" || p[2] === "picklist") && /^\d{9,}$/.test(p[3] || "") ? p[3] : null;
/** an old link (#/<ev>/matches, specs[/team], defense[/x], compare[/x], picklist[/ts]) -> where it lives now, else null */
function oldLink(p) {
  if (p.length < 2 || CS.globalRoutes[p[0]] || !EVK.test(p[0])) return null;
  const [ev, s, a] = p, tail = a ? "/" + a : "";
  if (s === "matches") return `#/${ev}/schedule`;
  if (s === "specs") return `#/${ev}/teams${tail}`;
  if (s === "defense" || s === "compare" || s === "picklist") return `#/${ev}/selection/${s}${tail}`;
  return null;
}
/** the as-of time in the current URL for pages that can replay, else undefined (page can't replay) */
function urlTs(p) {
  const k = activeKey(p);
  if (k === "dash") return p[2] && p[2] !== "live" ? p[2] : null;
  if (k === "selection") return !p[2] || p[2] === "odds" || p[2] === "picklist" || /^\d{9,}$/.test(p[2]) ? selTs(p) : undefined;     // Compare / Defense: latest data only
  return undefined;
}
let shellEv = null, shellKey = null;
CS.markActive = k => { if (k === "matches") k = "schedule"; shellKey = k; document.querySelectorAll("[data-navkey]").forEach(a => { if (a.dataset.navkey === k) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }); };

// ---- shell styles that belong to app.js (the global notice, honesty-label tips, nav badges, Matches filter)
(function shellCss() {
  const st = document.createElement("style");
  st.textContent = `
  #gnotice { max-width: calc(1320px - 2 * var(--gut)); margin: var(--sp-3) var(--gut) 0; }
  #gnotice[hidden] { display: none; }
  #gnotice .gn { display: flex; align-items: center; gap: var(--sp-2) var(--sp-3); flex-wrap: wrap; padding: var(--sp-2) var(--sp-2) var(--sp-2) var(--sp-4);
    border: 1px solid var(--caution-line); background: var(--caution-tint); border-radius: var(--r-m); font-size: var(--fs-foot); color: var(--ink-2); line-height: 1.45; }
  #gnotice .gn > svg { width: 18px; height: 18px; flex: none; color: var(--caution); }
  #gnotice .gn > div { flex: 1; min-width: 220px; } #gnotice b { color: var(--ink); font-weight: 600; }
  #gnotice .gn + .gn { margin-top: var(--sp-2); }
  #gnotice .gn-why { margin-top: 4px; } #gnotice .gn-raw { margin-top: 4px; } #gnotice .gn-raw summary { cursor: pointer; width: fit-content; }
  #gnotice .code { white-space: normal; overflow-wrap: anywhere; }
  @media (max-width: 760px) { #gnotice .gn { align-items: flex-start; } #gnotice .gn > div { flex-basis: calc(100% - 40px); } #gnotice .gn-x { margin-left: auto; } }
  @media (min-width: 761px) { #gnotice .gn.lk { display: none; } #gnotice:not(:has(.gn:not(.lk))) { display: none; } }
  #app:focus { outline: none; }
  #gnotice .gn-x { min-height: 32px; padding: 4px 10px; font-size: var(--fs-foot); background: transparent; border-color: var(--caution-line); }
  [data-tip] { cursor: help; }
  [data-tip][role=button]:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  a.team.us { font-weight: 700; text-decoration: underline; text-underline-offset: 3px; }
  .nav-badge { margin-left: auto; min-width: 20px; height: 20px; padding: 0 6px; border-radius: 10px; background: var(--accent-fill); color: var(--on-accent);
    font-size: 11.5px; font-weight: 700; line-height: 20px; text-align: center; font-variant-numeric: tabular-nums; flex: none; }
  .evh { margin-bottom: var(--sp-1); } .evsub { margin-bottom: var(--sp-4); }
  .mt-bar { display: flex; gap: var(--sp-2) var(--sp-3); align-items: center; flex-wrap: wrap; margin-bottom: var(--sp-3); } .mt-bar .chips { margin: 0; }
  .mt-bar .chip { min-height: 36px; font-size: var(--fs-foot); }
  .mt-q { flex: 1 1 200px; max-width: 320px; margin-left: auto; } .mt-q .csin { min-height: 36px; }
  .mt-none { padding: var(--sp-4) 0 var(--sp-2); }
  .timectl .tc-short { display: none; }
  @media (max-width: 760px) { .timectl.static .tc-long { display: none; } .timectl.static .tc-short { display: inline; } .timectl.static { padding-right: 4px; } }
  @media (pointer: coarse) { .mt-bar .chip, .mt-q .csin, #gnotice .gn-x { min-height: 44px; } }`;
  document.head.appendChild(st);
})();

// ---- processing state (server: GET app/v1/session -> processing, from the worker's live/worker.py status_beat)
// CS.processing = {paused: bool|null, paused_by: "app"|"env"|null, worker_seen: unix|null, reason, why, worker_down} |
// undefined before the first session answer. worker_down (server/workerstatus.py): null while the worker checks in, else
// {never, since, quiet_s, docker?}; docker (admins, host updater installed) = its container {state, error, problem: {why, fix}}.
// setup.js sets it from every session check (and a 60 s poll); pages read it (guard for undefined) and listen to the
// window event "cs:processing" (fired when paused / the reason / worker-down changes). The notice above #app says it once.
// An older api (no worker_down): quiet for an hour, as before.
const workerDown = p => !p ? null : p.worker_down !== undefined ? p.worker_down
  : p.worker_seen && p.paused !== true && Date.now() / 1000 - p.worker_seen > 3600 ? { never: false, since: p.worker_seen } : null;
CS.workerDown = workerDown;
const sinceTxt = ts => { const s = Date.now() / 1000 - ts; return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : CS.fmtWhen(ts); };
const downSig = x => { const d = workerDown(x); return d ? (d.never ? "never" : "down") + (d.docker ? ":" + d.docker.state : "") : ""; };
/** "last checked in 12 min ago" / "hasn't checked in since the server started, 15 min ago" */
const downWhen = d => d.never ? `hasn't checked in since the server started ${esc(sinceTxt(Date.now() / 1000 - (d.quiet_s || 0)))}`
  : `last checked in ${esc(sinceTxt(d.since))}`;
const IN_FOLDER = `on the server, in the Cyber-Scout folder (where <span class="code">docker-compose.yml</span> is, e.g. <span class="code">/opt/cyber-scout</span>)`;
/** what to check when the worker isn't running and the updater can't tell us why (no updater, or it runs normally) */
const DOWN_HOW = `<span class="hl"><b>What to check</b>, ${IN_FOLDER}:</span>
  <span class="hl"><span class="code">docker compose ps -a</span>: the worker should say <i>Up</i>. <i>Created</i> or <i>Exited</i> means it isn't running.</span>
  <span class="hl"><span class="code">docker compose up -d worker</span> starts it; if it can't, Docker prints why.</span>
  <span class="hl"><span class="code">docker compose logs --tail 80 worker</span>: why it stopped, if it started and then quit.</span>
  <span class="hl"><b>Common cause:</b> Docker can't pass the GPU in (<span class="code">nvidia-container-cli</span> or <span class="code">CDI</span> errors), often after a kernel, driver or NVIDIA toolkit update and a reboot. Re-running the installer tests every card and picks ones that work; setting <span class="code">GPU_IDS</span> to one card that works (e.g. <span class="code">0</span>) also does it.</span>
  <span class="hl">With the host updater installed (<span class="code">sudo ./app/deploy/updater/install-updater.sh</span>), this notice shows Docker's own error and the fix.</span>`;
CS.setProcessing = p => {
  const v = p && typeof p === "object" ? { paused: p.paused ?? null, paused_by: p.paused_by ?? null, worker_seen: p.worker_seen ?? null, reason: p.reason ?? null, why: p.why ?? null, worker_down: p.worker_down } : null;
  const sig = x => x ? [x.paused, x.reason, downSig(x)].join("|") : "";
  const ch = sig(v) !== sig(CS.processing);
  CS.processing = v;
  renderNotice();
  if (ch) dispatchEvent(new CustomEvent("cs:processing", { detail: v }));
};
const PAUSE_HOW = "To turn it back on: <a href=\"#/admin/settings\">Admin → Settings</a> → Processing → <b>Resume processing</b>. The worker picks it up within about 30 seconds, no restart needed. Video page jobs still run while it's paused.";
CS.PAUSE_HOW = PAUSE_HOW;
function renderNotice() {
  let el = document.getElementById("gnotice");
  if (!el) { el = document.createElement("div"); el.id = "gnotice"; el.setAttribute("role", "status"); el.hidden = true; $app.before(el); }
  const pr = CS.processing, p0 = location.hash.replace(/^#\/?/, "").split("/")[0], admin = CS.platform && CS.user && CS.can("admin");
  let html = "", sig = "";
  if (CS.platform && CS.user && pr && !["login", "setup", "connect"].includes(p0)) {
    const seen = pr.worker_seen ? `<span class="hl">The worker last checked in ${esc(sinceTxt(pr.worker_seen))}.</span>` : "";
    const down = workerDown(pr);
    if (down) { sig = downSig(pr); html = downNotice(down, admin); }
    else if (pr.paused) { sig = "paused";
      html = `<b>Processing is paused on the server.</b> Nothing new is built or tracked until an admin turns it back on. ${CS.help(`<span class="hl">Schedules, results, rankings, predictions and tracked matches stay as of the last build until processing is turned back on.</span>${seen}${admin ? `<span class="hl">${PAUSE_HOW}</span>` : ""}`, "About paused processing")}`; }
    else if (pr.reason === "no_key" && admin) { sig = "no_key";
      html = `<b>Nothing is processed yet: the server has no TBA key.</b> <a href="#/admin/settings">Set it in Admin → Settings</a>.`; }
  }
  const hide = sig && sessionStorageGet("cs:gnotice") === sig;
  // lockdown: the toolbar chip says it on wider screens (setup.js renderUserBox); phones hide the user box, so it's here
  const lock = CS.platform && CS.user && CS.session && CS.session.lockdown && !["login", "setup", "connect"].includes(p0)
    ? `<div class="gn lk">${CS.icon("ban")}<div><b>Read-only (lockdown).</b> Only admins can change anything.${admin ? ' <a href="#/activity">End it</a>' : ""}</div></div>` : "";
  const proc = html && !hide ? `<div class="gn">${CS.icon("warn")}<div>${html}</div><button type="button" class="gn-x" aria-label="Hide this notice until the next visit">Hide</button></div>` : "";
  el.hidden = !proc && !lock;
  el.innerHTML = lock + proc;
  const x = el.querySelector(".gn-x"); if (x) x.onclick = () => { sessionStorageSet("cs:gnotice", sig); renderNotice(); };
}
CS.renderNotice = renderNotice;
/** the "worker isn't running" notice: what it means, and for admins Docker's reason + the fix (or what to check) */
function downNotice(d, admin) {
  if (!admin) return `<b>The server isn't taking in new data right now:</b> its worker ${d.never ? "hasn't started" : `stopped ${esc(sinceTxt(d.since))}`}, so schedules, results and video stay as they are. Ask an admin to check the server.`;
  const dk = d.docker, pb = dk && dk.problem;
  const lead = `<b>The worker isn't running</b> (it ${downWhen(d)}). Nothing new comes in until it does: no schedules or results from TBA, no video, no tracking.`;
  if (pb) return `${lead}<div class="gn-why"><b>Why:</b> ${esc(pb.why)}</div><div class="gn-why"><b>Fix:</b> ${esc(pb.fix)}</div>${dk.error || dk.log ? `<details class="gn-raw"><summary>What Docker said</summary><span class="code">${esc(dk.error || dk.log)}</span></details>` : ""}`;
  if (dk && dk.state === "running") return `${lead} Docker says its container is running, so it's stuck or can't write to the data folder. ${CS.help(`<span class="hl">${IN_FOLDER}: <span class="code">docker compose logs --tail 80 worker</span> shows what it's doing; <span class="code">docker compose restart worker</span> restarts it.</span>`, "What to check")}`;
  return `${lead} Start it ${IN_FOLDER}: <span class="code">docker compose up -d worker</span>. If it can't start, Docker prints why. ${CS.help(DOWN_HOW, "What to check")}`;
}

function renderTopNav(p) {
  const el = document.getElementById("topnav"); if (!el) return;
  const bare = CS.platform && !CS.user;
  document.body.classList.toggle("bare", !!bare);
  // only well-formed event keys from the URL or storage reach the nav hrefs (security audit: fragment XSS)
  const sv = lsGet2(localStorage, "cs:lastev");
  const hev = p.length && !CS.globalRoutes[p[0]] && EVK.test(p[0]) ? p[0] : null;
  const known = comps && comps.length ? comps : null;
  const last = EVK.test(sv || "") && (!known || known.some(c => c.key === sv)) ? sv : null;   // a competition no longer followed: default
  const dc = known ? defaultComp(known) : null;
  const ev = hev || last || (dc ? dc.key : null);
  if (!bare) loadComps().then(l => {
    if (location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).join("/") !== p.join("/")) return;
    if (!ev && l.length) renderTopNav(p); else renderComp(shellEv);
  });
  if (hev) lsSet2(localStorage, "cs:lastev", hev);
  shellEv = ev; shellKey = activeKey(p);
  const ts = ev ? CS.asofGet(ev) : null;
  const items = CS.navItems.filter(i => ev || i.global).sort((a, b) => (a.order || 50) - (b.order || 50));
  const key = i => i.key || (i.global ? i.href(ev).replace(/^#\//, "").split("/")[0] : i.href(ev).split("/")[2]);
  const badge = i => { const n = i.badge && ev && CS.platform && CS.user ? i.badge(ev) : 0; return n ? `<span class="nav-badge" aria-label="${n} waiting">${n}</span>` : ""; };
  const link = (i, cls) => { const k = key(i), b = badge(i); return `<a class="${cls}" data-navkey="${esc(k)}" href="${esc(i.href(ev, i.replay ? ts : null))}" title="${esc(i.label)}${b ? ` (${i.badge(ev)} waiting)` : ""}"${k === shellKey ? ' aria-current="page"' : ""}>${CS.icon(i.icon)}<span>${esc(i.label)}</span>${b}</a>`; };
  const groups = NAV_GROUPS.map(([g, t]) => { const its = items.filter(i => (i.group || (i.global ? "manage" : "scout")) === g); return its.length ? `<div class="sb-group" role="group" aria-label="${t}"><div class="sb-h">${t}</div>${its.map(i => link(i, "sb-item")).join("")}</div>` : ""; }).join("");
  const acct = CS.platform && CS.user ? `<div class="sb-group" role="group" aria-label="Account"><div class="sb-h">Account</div><a class="sb-item" data-navkey="account" href="#/account">${CS.icon("account")}<span>${esc(CS.user.display_name || CS.user.username)}</span></a>
    <button type="button" class="sb-item ms-signout">${CS.icon("close")}<span>Sign out</span></button></div>` : "";
  el.innerHTML = groups;
  renderComp(ev);
  // phone: four tabs + More
  const tb = document.getElementById("tabbar");
  if (tb) {
    const tabs = items.filter(i => i.tab), inTabs = tabs.some(i => key(i) === shellKey);
    tb.innerHTML = tabs.map(i => link(i, "")).join("") + `<button id="moretab" type="button"${!inTabs && shellKey ? ' aria-current="page"' : ""} aria-haspopup="dialog">${CS.icon("more")}<span>More</span></button>`;
    const ms = document.getElementById("moresheet"), body = document.getElementById("msbody");
    body.innerHTML = (ev && !bare ? `<div class="sb-group" role="group" aria-label="Competition"><div class="sb-h">Competition</div><button type="button" class="sb-item ms-comp" aria-haspopup="dialog">${CS.icon("updown")}<span>${esc(compName(ev))}</span></button></div>` : "") + groups + acct;
    const mc = body.querySelector(".ms-comp"); if (mc) mc.onclick = () => { ms.close(); openComps(document.getElementById("tbcomp")); };
    const so = body.querySelector(".ms-signout"); if (so) so.onclick = () => { ms.close(); if (CS.signOut) CS.signOut(); };
    document.getElementById("moretab").onclick = () => { try { ms.showModal(); } catch (e) { ms.setAttribute("open", ""); } };
    document.getElementById("msclose").onclick = () => ms.close();
    ms.onclick = e => { if (e.target === ms || e.target.closest("a")) ms.close(); };
  }
  renderTimebar(p, ev);
  renderNotice();
}

// ---- the competition switcher: sidebar header (desktop), toolbar subtitle (phone / Command / top bar / hidden sidebar), More sheet
const compDot = c => `<i class="cmp-dot ${compStatus(c)}" aria-hidden="true"></i>`;
function compMeta(c) {
  if (!c) return "Switch competition";
  return `${compDot(c)}<span>${esc(compStatusText(c))}${c.needs_setup ? " · needs video setup" : c.n_total ? " · " + esc(compProgress(c)) : ""}</span>`;
}
function tbChip() {
  let b = document.getElementById("tbcomp"); if (b) return b;
  const sub = document.getElementById("tbsub"); if (!sub) return null;
  b = document.createElement("button"); b.type = "button"; b.id = "tbcomp"; b.hidden = true; b.setAttribute("aria-haspopup", "dialog"); b.setAttribute("aria-expanded", "false");
  b.onclick = () => openComps(b);
  sub.insertBefore(b, sub.firstChild);
  return b;
}
function renderComp(ev) {
  const bare = CS.platform && !CS.user, sbe = document.getElementById("sbevent"), chip = tbChip();
  const admin = CS.platform && CS.user && CS.can("admin"), c = ev ? CS.compInfo(ev) : null;
  if (sbe) {
    sbe.innerHTML = bare ? "" : ev ? `<button type="button" class="cmp-trig" aria-haspopup="dialog" aria-expanded="false" title="Switch competition">
        <span class="cmp-tx"><span class="vh">Competition: </span><b>${esc(compName(ev))}</b><span class="cmp-meta">${compMeta(c)}</span></span>${CS.icon("updown")}</button>`
      : comps && !comps.length && admin ? `<button type="button" class="cmp-trig cmp-new" aria-haspopup="dialog">${CS.icon("plus")}<span class="cmp-tx"><b>Add competition</b></span></button>` : "";
    const t = sbe.querySelector(".cmp-trig"); if (t) t.onclick = () => openComps(t, !ev);
  }
  if (chip) {
    chip.innerHTML = ev ? `<span class="vh">Competition: </span><span class="cmp-n">${esc(compShort(ev))}</span>${CS.icon("down")}` : "";
    chip.title = ev ? `${compName(ev)}: switch competition` : "";
  }
  placeComp(); applyTitle();
}
/** the toolbar chip shows only where the sidebar's switcher can't be seen */
function placeComp() {
  const chip = document.getElementById("tbcomp"); if (!chip) return;
  const trig = document.querySelector("#sbevent .cmp-trig"), sb = document.getElementById("sidebar");
  const sbShows = !!trig && trig.getClientRects().length > 0 && !document.body.classList.contains("sb-off") && (!sb || getComputedStyle(sb).visibility !== "hidden");
  chip.hidden = !shellEv || sbShows || !!(CS.platform && !CS.user);
}
(function wireComp() {
  addEventListener("resize", placeComp);
  new MutationObserver(placeComp).observe(document.documentElement, { attributes: true, attributeFilter: ["data-layout", "data-style"] });
  const b = document.getElementById("sbtoggle"); if (b) b.addEventListener("click", () => setTimeout(placeComp));
})();

let compDlg = null, compTrig = null;
function compDialog() {
  if (compDlg) return compDlg;
  const d = compDlg = document.createElement("dialog"); d.className = "compsheet"; d.setAttribute("aria-labelledby", "cmph");
  document.body.appendChild(d);
  d.addEventListener("click", e => {
    if (e.target === d) return d.close();                                   // backdrop
    const x = e.target.closest("[data-x]"), back = e.target.closest("[data-back]"), add = e.target.closest("[data-add]"), a = e.target.closest("a[href]");
    if (x) d.close(); else if (back) drawCompList(); else if (add) drawCompAdd();
    else if (a) { if (a.dataset.ev && EVK.test(a.dataset.ev)) lsSet2(localStorage, "cs:lastev", a.dataset.ev); d.dataset.nav = "1"; d.close(); }
  });
  d.addEventListener("keydown", e => {                                       // arrow keys move through the list
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const its = [...d.querySelectorAll(".cmp-row, .cmp-act")]; if (!its.length) return;
    const i = its.indexOf(document.activeElement); e.preventDefault();
    its[(i < 0 ? 0 : i + (e.key === "ArrowDown" ? 1 : its.length - 1)) % its.length].focus();
  });
  d.addEventListener("close", () => {
    if (compTrig) compTrig.setAttribute("aria-expanded", "false"); d.classList.remove("wide");
    // back to what opened it (the sidebar trigger may have been redrawn meanwhile); a link that navigated hands focus to the page
    const nav = d.dataset.nav === "1"; delete d.dataset.nav;
    if (nav) return;
    const vis = b => b && b.isConnected && b.getClientRects().length ? b : null;
    const t = vis(compTrig) || vis(document.querySelector("#sbevent .cmp-trig")) || vis(document.getElementById("tbcomp"));
    if (t) t.focus();
  });
  addEventListener("resize", () => { if (d.open) placeDlg(); });
  return d;
}
function placeDlg() {
  const d = compDlg; if (!d || !d.open) return;
  const anchor = compTrig && compTrig.isConnected && compTrig.getClientRects().length ? compTrig : null;
  if (matchMedia("(max-width: 760px)").matches || d.classList.contains("wide") || !anchor) { d.classList.remove("anchored"); d.style.left = d.style.top = d.style.maxHeight = ""; return; }
  const r = anchor.getBoundingClientRect(), vw = document.documentElement.clientWidth, w = Math.min(420, vw - 32);
  const left = Math.max(16, Math.min(r.left, vw - w - 16)), top = Math.round(r.bottom + 6);
  d.classList.add("anchored"); d.style.left = left + "px"; d.style.top = top + "px"; d.style.maxHeight = `calc(100vh - ${top + 16}px)`;
}
function openComps(trigger, addMode) {
  const d = compDialog();
  compTrig = trigger && trigger.isConnected && trigger.getClientRects().length ? trigger : null;
  if (addMode) drawCompAdd(true); else drawCompList(true);
  if (!d.open) { try { d.showModal(); } catch (e) { d.setAttribute("open", ""); } }
  if (compTrig) compTrig.setAttribute("aria-expanded", "true");
  placeDlg();
  const cur = d.querySelector(".cmp-row.on") || d.querySelector(".cmp-row"); if (cur) cur.focus();
  if (!addMode) loadComps(true).then(() => { if (d.open && d.dataset.mode === "list") { const f = document.activeElement && document.activeElement.dataset.ev; drawCompList(); const n = f && d.querySelector(`.cmp-row[data-ev="${f}"]`); if (n) n.focus(); } renderComp(shellEv); });
}
CS.openComps = openComps;
const DLG_HEAD = (title, back) => `<div class="cmp-h">${back ? `<button type="button" class="tb-btn" data-back aria-label="Back to competitions" title="Back">${CS.icon("chevL")}</button>` : ""}<h2 id="cmph">${title}</h2>
  <button type="button" class="tb-btn" data-x aria-label="Close" title="Close">${CS.icon("close")}</button></div>`;
function drawCompList() {
  const d = compDialog(), cur = shellEv, admin = CS.platform && CS.user && CS.can("admin");
  d.dataset.mode = "list"; d.classList.remove("wide");
  const l = sortComps(comps || []);
  if (cur && !l.some(c => c.key === cur)) l.unshift({ key: cur, name: cur, other: true });
  const row = c => { const on = c.key === cur, place = placeOf(c);
    return `<a class="cmp-row${on ? " on" : ""}" href="${esc(compHref(c.key))}" data-ev="${esc(c.key)}"${on ? ' aria-current="true"' : ""}>
      <span class="cmp-ck">${on ? CS.icon("check") : ""}</span>
      <span class="cmp-main"><b>${esc(c.name)}</b><span class="cmp-sub">${c.other ? "not followed (data kept)" : esc([compDates(c), place].filter(Boolean).join(" · ") || c.key)}</span></span>
      ${c.other ? "" : `<span class="cmp-side"><span class="cmp-st ${compStatus(c)}">${compDot(c)}${esc(compStatusText(c))}</span>
        <span class="cmp-prog${c.needs_setup ? " warn" : ""}">${c.needs_setup ? "Needs video setup" : esc(compProgress(c))}</span></span>`}</a>`; };
  d.innerHTML = `<div class="cmp-in">${DLG_HEAD("Competitions")}
    <div class="cmp-list" role="list">${l.map(row).join("") || `<div class="cmp-none">No competitions followed yet.${admin ? "" : " An admin adds them."}</div>`}</div>
    ${admin ? `<div class="cmp-foot"><button type="button" class="cmp-act" data-add>${CS.icon("plus")}<span>Add competition…</span></button>
      <a class="cmp-act" href="#/admin/events">${CS.icon("admin")}<span>Manage competitions</span></a></div>` : ""}</div>`;
  placeDlg();
}
// Add: type a name, city or key -> TBA events of the season (GET app/v1/events/search, ranked on the server, which keeps the TBA
// key); with nothing typed, events on now / soon / just ended and your team's first. One click follows.
const WHEN_T = { now: "On now", soon: "Soon", recent: "Just ended" };
function drawCompAdd() {
  const d = compDialog(), y0 = new Date().getFullYear();
  d.dataset.mode = "add"; d.classList.add("wide"); placeDlg();
  d.innerHTML = `<div class="cmp-in">${DLG_HEAD("Add a competition", true)}<div class="cmp-body">
    <form class="cmp-find" role="search" id="cmpfind"><label class="vh" for="cmpq">Find an event on The Blue Alliance</label>
      <input id="cmpq" class="csin" type="search" placeholder="Event name, city or key (2026wasam)…" autocomplete="off" autocapitalize="none" spellcheck="false" enterkeyhint="search">
      <select id="cmpyr" aria-label="Season">${[y0 + 1, y0, y0 - 1, y0 - 2].map(y => `<option ${y === y0 ? "selected" : ""}>${y}</option>`).join("")}</select></form>
    <div class="cmp-res" id="cmpres" aria-live="polite"><div class="cmp-none">Loading events from The Blue Alliance…</div></div>
    <p class="cmp-note">Each competition gets its own teams, matches, scoring, picklists, notes and video setup. Accounts, roles, your team number and settings are shared.</p></div></div>`;
  const q = d.querySelector("#cmpq"), yr = d.querySelector("#cmpyr"), res = d.querySelector("#cmpres");
  let seq = 0, t = 0;
  const hit = e => { const place = placeOf(e);
    const tags = [WHEN_T[e.when] ? `<span class="pill cmp-when ${e.when}">${WHEN_T[e.when]}</span>` : "", e.offseason ? `<span class="pill">Offseason</span>` : "",
      e.your_team ? `<span class="pill cmp-mine">Your team</span>` : ""].join("");
    return `<div class="cmp-hit"><span class="cmp-main"><b>${esc(e.name || e.key)}</b><span class="cmp-sub">${esc([e.key, compDates(e), place].filter(Boolean).join(" · "))}</span>${tags ? `<span class="cmp-tags">${tags}</span>` : ""}</span>
      ${e.followed ? `<a class="cmp-btn" href="#/${esc(e.key)}" data-ev="${esc(e.key)}">Open</a>` : `<button type="button" class="cmp-btn" data-follow="${esc(e.key)}" aria-label="Follow ${esc(e.name || e.key)}">Follow</button>`}</div>`; };
  async function find() {
    const my = ++seq, v = q.value.trim();
    try {
      const r = await CS.app("GET", `events/search?q=${encodeURIComponent(v)}&year=${/^\d{4}/.test(v) ? v.slice(0, 4) : yr.value}&limit=12`);
      if (my !== seq || !d.open) return;
      res.innerHTML = r.results.length ? `${v ? "" : `<div class="cmp-rh">${r.team_number ? `On now, soon and team ${esc(r.team_number)}'s events first` : "On now and soon first"}</div>`}${r.results.map(hit).join("")}
        ${r.n_matched > r.results.length ? `<div class="cmp-none">${r.n_matched - r.results.length} more: type more of the name.</div>` : ""}`
        : `<div class="cmp-none">No ${esc(r.year)} event matches “${esc(v)}”. Try the city, or the event key from its TBA link (thebluealliance.com/event/<b>2026wasam</b>).</div>`;
    } catch (e) {
      if (my !== seq) return;
      res.innerHTML = `<div class="cmp-none cserr" role="alert">${esc(e.status === 409 ? "Set your TBA Read API key first (Admin → Settings): event lists come from The Blue Alliance." : e.message)}</div>`;
    }
  }
  q.oninput = () => { clearTimeout(t); t = setTimeout(find, 220); };
  yr.onchange = find;
  d.querySelector("#cmpfind").onsubmit = e => { e.preventDefault(); clearTimeout(t); find(); };
  res.onclick = async e => {
    const b = e.target.closest("[data-follow]"); if (!b) return;
    b.disabled = true; b.textContent = "Following…";
    try { const ev = await CS.app("POST", "events", { key: b.dataset.follow }); if (ev && EVK.test(ev.key || "")) drawCompAdded(ev); }
    catch (x) { b.disabled = false; b.textContent = "Follow"; res.insertAdjacentHTML("afterbegin", `<div class="cmp-none cserr" role="alert">${esc(x.message)}</div>`); }
  };
  find(); setTimeout(() => q.focus(), 0);
}
function drawCompAdded(ev) {
  const d = compDialog(), k = esc(ev.key), n = esc(ev.name || ev.key);
  d.dataset.mode = "added";
  loadComps(true).then(() => renderComp(shellEv));
  d.innerHTML = `<div class="cmp-in">${DLG_HEAD("Competition added", true)}<div class="cmp-body">
    <p><b>Now following ${n}.</b> ${CS.processing && CS.processing.paused ? `Processing is paused on the server, so its schedule, rankings and predictions appear once an admin turns processing back on. ${CS.help(PAUSE_HOW, "How to turn processing back on")}`
      : "Its schedule, rankings and predictions appear within a few minutes (the worker's next pass, once The Blue Alliance lists its teams)."}</p>
    <p class="cmp-note"><b>Next: its video.</b> Paste the event's stream or video link (YouTube or Twitch), then its field setup on the stream's card. Robots are tracked once that's done; each competition has its own setup.</p>
    <div class="cmp-go"><a class="cmp-btn primary" href="#/${k}/sources" data-ev="${k}">${CS.icon("video")}Set up video</a>
      <a class="cmp-btn" href="#/${k}" data-ev="${k}">Open its dashboard</a><button type="button" class="cmp-btn" data-add>Add another</button></div></div></div>`;
  const f = d.querySelector(".cmp-btn.primary"); if (f) f.focus();
}

/** shown on a competition's dashboard while it has no camera calibration */
function compSetupBanner(ev) {
  const at = location.hash;
  loadComps().then(() => {
    const c = CS.compInfo(ev); if (!c || !c.needs_setup || location.hash !== at || $app.querySelector(".cmp-setup")) return;
    const admin = CS.platform && CS.user && CS.can("admin");
    $app.insertAdjacentHTML("afterbegin", `<div class="cmp-setup" role="status">${CS.icon("warn")}<div><b>${esc(c.name)} needs video setup</b>
      Robots aren't tracked here until its video is set up (stream or uploads, layout boxes, camera calibration: each competition has its own).
      Schedule, rankings and predictions from TBA still work.</div>${admin ? `<a class="cmp-btn" href="#/${esc(ev)}/sources">Set up video</a>` : `<span class="muted small">Ask an admin to set it up.</span>`}</div>`);
  });
}
/** why nothing new is being built right now, as a sentence for empty states ("" when the worker is running normally) */
function buildStall(admin) {
  const pr = CS.processing;
  if (pr && pr.paused) return `Processing is paused on the server, so the worker isn't building pages right now. ${admin ? `Turn it back on to build them. ${CS.help(PAUSE_HOW, "How to turn processing back on")}` : "Ask an admin to turn processing back on."}`;
  if (pr && pr.reason === "no_key") return admin ? `The server has no TBA key yet: <a href="#/admin/settings">set it in Admin → Settings</a>.` : "The server isn't set up yet (no TBA key): ask an admin.";
  const down = workerDown(pr);
  if (down) return `The worker isn't running (it ${downWhen(down)}), so nothing new is built. ${admin ? (down.docker && down.docker.problem ? esc(down.docker.problem.why) + " " + esc(down.docker.problem.fix) : `Start it ${IN_FOLDER}: <span class="code">docker compose up -d worker</span>. ${CS.help(DOWN_HOW, "What to check")}`) : "Ask an admin to check the server."}`;
  return "";
}
CS.buildStall = buildStall;
/** the page a hash points at, for titles when its data can't be shown: "Selection", "Match Q12", "Team 4911", ... */
function pageLabel(p) {
  if (!p.length) return "Competitions";
  if (CS.globalRoutes[p[0]]) { const it = CS.navItems.find(i => i.global && (i.key || "") === p[0]); return it ? it.label : ""; }
  const s = p[1], a = (p[2] || "").split("?")[0];
  if (s === "m" || s === "a") return a ? `Match ${shortKey(a)}` : "Match";
  if (s === "t") return a ? `Team ${num(a)}` : "Team";
  const k = activeKey(p), it = CS.navItems.find(i => !i.global && i.key === k);
  return it ? it.label : EV_TABS[k] || "";
}
/** a followed competition the worker hasn't built yet (no api/<ev>/event.json): say so instead of an error */
async function compNotBuilt(ev, p) {
  try { await api(`${ev}/event.json`); return false; } catch (e) { if (!/\b404\b/.test(e.message)) return false; }
  await loadComps();
  const c = CS.compInfo(ev), admin = CS.platform && CS.user && CS.can("admin"), lab = p ? pageLabel(p) : "";
  crumbs(lab && lab !== "Dashboard" ? [[c ? c.name : ev, `#/${ev}`], [lab]] : [[c ? c.name : ev]]); $updated.textContent = "";
  const stall = c ? buildStall(admin) : "";
  $app.innerHTML = `<div class="empty cs-notbuilt"><b>${esc(c ? c.name : ev)} isn't ready yet</b>${c ? `${stall || "The worker builds a competition's pages on its next pass (every few minutes) once The Blue Alliance lists its teams."}${c.needs_setup ? " Robots are tracked once its video is set up." : ""}`
    : "This competition isn't followed, so there's nothing to show. Pick another one from the competition menu."}
    <div class="acts">${c && c.needs_setup && admin ? `<a class="cmp-btn primary" href="#/${esc(ev)}/sources">Set up video</a>` : ""}<button type="button" id="routeretry">Try again</button></div></div>`;
  document.getElementById("routeretry").onclick = () => route(false);
  const re = () => { if ($app.querySelector(".cs-notbuilt")) route(false); };
  addEventListener("cs:processing", re); cleanup.push(() => removeEventListener("cs:processing", re));
  return true;
}
/** page title: "<page> · <competition> · Cyber-Scout" */
let pageTitle = "";
function applyTitle() {
  const ev = location.hash.replace(/^#\/?/, "").split("/")[0] || "";
  const c = EVK.test(ev) && !CS.globalRoutes[ev] ? compShort(ev) : "";
  document.title = [pageTitle, c !== pageTitle ? c : "", "Cyber-Scout"].filter(Boolean).join(" · ");
}

// ---- the time control: one place, in the toolbar, on every event page with data
// Replayable pages (Dashboard, Picklist: dash/index.json snapshots; Selection: selection/index.json) get the full control,
// with only that page's snapshot times; other data pages get a "Latest data" label with a "?"; admin / setup pages and
// competitions that aren't built get nothing.
const TIMES = {};
function eventTimes(ev, kind = "dash") {
  const k = ev + ":" + kind;
  const sel = () => api(`${ev}/selection/index.json`).then(x => (x.times || []).map(t => ({ ts: String(t), label: fmtDay(t) })));
  return TIMES[k] || (TIMES[k] = (kind === "selection" ? sel().catch(() => [])
    : api(`${ev}/dash/index.json`).then(x => (x || []).map(t => ({ ts: String(t.ts), label: t.label, q: t.quals_played }))).catch(() => sel().catch(() => [])))
    .then(l => { setTimeout(() => { delete TIMES[k]; }, 120000); return l; }));
}
const fmtDay = ts => { const d = new Date(ts * 1000); return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()] + " " + d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };
const DATA_PAGES = new Set(["teams", "selection", "schedule", "rankings"]);
let tbSeq = 0;
async function renderTimebar(p, ev) {
  const el = document.getElementById("timebar"); if (!el) return;
  const seq = ++tbSeq;
  if (!ev || !p.length || CS.globalRoutes[p[0]]) { el.innerHTML = ""; return; }
  const ts = urlTs(p), k = activeKey(p);
  if (ts === undefined && !DATA_PAGES.has(k)) { el.innerHTML = ""; return; }      // video setup, identity fixes, match pages
  let evj = null; try { evj = await api(`${ev}/event.json`); } catch (e) { }
  if (seq !== tbSeq) return;
  if (!evj) { el.innerHTML = ""; return; }                                        // not built yet: its page says so
  const lab = latestLabel(ev);
  const latestTag = () => `<div class="timectl static"><span class="tc-tag"><span class="tc-long">${lab}</span><span class="tc-short">${lab === "Latest data" ? "Latest" : "Final"}</span></span>${CS.help(`<span class="hl">This page uses everything posted so far${lab === "Latest data" ? "" : " (the competition is over)"}, not a replay time.</span><span class="hl">To see the event as it was at a moment, use the time control on the Dashboard, Selection or Picklist.</span>${CS.processing && CS.processing.paused ? `<span class="hl">Processing is paused on the server: nothing new is added until it's turned back on.</span>` : ""}`, "About the data on this page")}</div>`;
  if (ts === undefined) { el.innerHTML = latestTag(); return; }
  CS.asofSet(ev, ts);
  const onPl = k === "selection" && p[2] === "picklist";
  const times = await eventTimes(ev, k === "selection" && !onPl ? "selection" : "dash"); if (seq !== tbSeq) return;
  if (!times.length && !ts) { el.innerHTML = latestTag(); return; }                // nothing to replay here
  const item = CS.navItems.find(i => (i.key || "") === k) || CS.navItems.find(i => i.key === "dash");
  const go = t => { CS.asofSet(ev, t); location.hash = onPl ? `#/${ev}/selection/picklist${t ? "/" + t : ""}` : item.href(ev, t || null); };
  const i = ts ? times.findIndex(t => t.ts === ts) : times.length;
  const narrow = matchMedia("(max-width: 760px)").matches;
  // "Live" only while the competition is on, processing runs and the data is fresh; otherwise "Latest"
  const c = CS.compInfo(ev), pr = CS.processing;
  const live = !!c && compStatus(c) === "live" && !(pr && pr.paused) && !workerDown(pr) && evj.updated && Date.now() / 1000 - evj.updated < 1800;
  const opt = t => `<option value="${t.ts}"${t.ts === ts ? " selected" : ""}>${esc(t.label)}${t.q != null && !narrow ? ` · ${t.q} qual${t.q === 1 ? "" : "s"}` : ""}</option>`;
  el.innerHTML = `<div class="timectl ${ts ? "replay" : live ? "live" : "latest"}" role="group" aria-label="Data time">
    <span class="tc-tag"${!ts && !live ? tipA(`Latest data: the newest build${evj.updated ? `, ${CS.fmtWhen(evj.updated, { day: true })}` : ""}.${pr && pr.paused ? " Processing is paused on the server, so it isn't updating." : ""}`) : ""}><i aria-hidden="true"></i>${ts ? "Replay" : live ? "Live" : "Latest"}</span>
    <button class="tc-step" id="tcprev" type="button" aria-label="Earlier snapshot" title="Earlier snapshot"${i <= 0 ? " disabled" : ""}>${CS.icon("chevL")}</button>
    <select id="tcsel" aria-label="Show data as of"><option value="">${ts ? "Latest data" : "Now"}</option>${times.map(opt).join("")}</select>
    <button class="tc-step" id="tcnext" type="button" aria-label="Later snapshot" title="Later snapshot"${!ts ? " disabled" : ""}>${CS.icon("chevR")}</button>
    ${ts ? `<button class="tc-live" id="tclive" type="button">${live ? "Go live" : "Latest"}</button>` : ""}</div>`;
  document.getElementById("tcsel").onchange = e => go(e.target.value || null);
  document.getElementById("tcprev").onclick = () => { const j = (i < 0 ? times.length : i) - 1; if (j >= 0) go(times[j].ts); };
  document.getElementById("tcnext").onclick = () => { const j = i + 1; go(j >= times.length ? null : times[j].ts); };
  const gl = document.getElementById("tclive"); if (gl) gl.onclick = () => go(null);
}
(function wireShell() {
  const b = document.getElementById("sbtoggle"); if (!b) return;
  const set = off => { document.body.classList.toggle("sb-off", off); b.setAttribute("aria-label", off ? "Show sidebar" : "Hide sidebar"); b.title = b.getAttribute("aria-label"); };
  set(lsGet2(localStorage, "cs:sboff") === "1");
  b.onclick = () => { const off = !document.body.classList.contains("sb-off"); lsSet2(localStorage, "cs:sboff", off ? "1" : null); set(off); };
})();

// app-wide full screen (pit TVs / projectors): a toolbar button, Fullscreen API on the whole page. Hidden where the browser can't
// (iPhone Safari has no element full screen; the match viewer has its own fallback).
(function wireFullscreen() {
  const de = document.documentElement, tb = document.getElementById("toolbar"), ub = document.getElementById("userbox");
  if (!tb || !(de.requestFullscreen || de.webkitRequestFullscreen) || !(document.fullscreenEnabled || document.webkitFullscreenEnabled)) return;
  const ON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/></svg>';
  const OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/></svg>';
  const b = document.createElement("button");
  b.type = "button"; b.className = "tb-btn tb-fs"; b.id = "tbfs";
  const cur = () => document.fullscreenElement || document.webkitFullscreenElement;
  const sync = () => { const on = cur() === de; b.innerHTML = on ? OFF : ON; b.setAttribute("aria-pressed", on ? "true" : "false");
    b.setAttribute("aria-label", on ? "Leave full screen" : "Full screen"); b.title = on ? "Leave full screen (Esc)" : "Full screen (pit TV / projector)"; };
  b.onclick = () => {
    if (cur()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else { try { const r = de.requestFullscreen ? de.requestFullscreen({ navigationUI: "hide" }) : de.webkitRequestFullscreen(); if (r && r.catch) r.catch(() => { }); } catch (e) { } }
  };
  sync(); tb.insertBefore(b, ub || null);
  document.addEventListener("fullscreenchange", sync); document.addEventListener("webkitfullscreenchange", sync);
})();

// ------------------------------------------------------------------ sheets
// CS.sheet({title, html, wide, cls, onClose}): a modal in the competition list's look (.compsheet): centred on a desktop, a
// bottom sheet on phones. Closes on Esc, [data-x] and a click on the backdrop (not a text selection that ends there);
// removed when closed, focus goes back to what opened it, and a page change closes it. -> {dlg, body, close}
// CS.ask({title, html, ok, cancel, danger, mount(body), value(body)}): the same with Cancel / OK; resolves to false
// (cancelled) or value(body) (default true). The app's quick yes/no questions still use confirm(); this is for the ones
// that need choices or a list in them.
CS.sheet = (o = {}) => {
  const opener = document.activeElement, d = document.createElement("dialog"), id = "sh" + Math.random().toString(36).slice(2, 8);
  d.className = `compsheet cs-sheet${o.wide ? " wide" : ""}${o.cls ? " " + o.cls : ""}`; d.setAttribute("aria-labelledby", id);
  d.innerHTML = `<div class="cmp-in"><div class="cmp-h"><h2 id="${id}">${o.title || ""}</h2><button type="button" class="tb-btn" data-x aria-label="Close" title="Close">${CS.icon("close")}</button></div>
    <div class="cmp-body sh-body">${o.html || ""}</div></div>`;
  document.body.appendChild(d);
  const close = () => { if (d.open) d.close(); else if (d.isConnected) { d.remove(); if (o.onClose) o.onClose(); } };
  let downOnBackdrop = false;
  d.addEventListener("pointerdown", e => { downOnBackdrop = e.target === d; });
  d.addEventListener("click", e => { if ((e.target === d && downOnBackdrop) || e.target.closest("[data-x]")) close(); });
  d.addEventListener("close", () => {
    d.remove(); if (o.onClose) o.onClose();
    if (opener && opener.isConnected && opener.focus && !document.querySelector("dialog[open]")) opener.focus();
  });
  cleanup.push(close);
  try { d.showModal(); } catch (e) { d.setAttribute("open", ""); }
  const f = d.querySelector("[autofocus]"); if (f) f.focus();
  return { dlg: d, body: d.querySelector(".sh-body"), close };
};
CS.ask = o => new Promise(res => {
  let done = false;
  const s = CS.sheet({ title: o.title, cls: "cs-ask", onClose: () => { if (!done) res(false); },
    html: `${o.html || ""}<div class="sh-acts"><button type="button" data-x${o.danger ? " autofocus" : ""}>${o.cancel || "Cancel"}</button>
      <button type="button" class="${o.danger ? "danger sh-danger" : "primary"}" data-ok${o.danger ? "" : " autofocus"}>${o.ok || "OK"}</button></div>` });
  if (o.mount) o.mount(s.body);
  s.body.querySelector("[data-ok]").onclick = () => { done = true; res(o.value ? o.value(s.body) : true); s.close(); };
});

// ------------------------------------------------------------------ router
// Every hash segment is checked once here before any page sees it (security audit: fragment XSS into hrefs / innerHTML):
// a plain character set everywhere, a well-formed event key, and the exact shape of match / team keys and snapshot
// times. Pages may still check their own arguments; anything that fails shows "Page not found".
const SEG_OK = /^[A-Za-z0-9_.,=?&%:-]{1,160}$/, MKEY_OK = /^[A-Za-z0-9_]{1,40}$/, TEAM_OK = /^frc\d{1,5}$/, TS_OK = /^\d{9,12}$/;
function routeOk(p) {
  if (!p.every(x => SEG_OK.test(x))) return false;
  if (!p.length || CS.globalRoutes[p[0]]) return true;
  if (!EVK.test(p[0])) return false;
  const [, s, a, b] = p;
  switch (s) {
    case "m": case "a": return MKEY_OK.test((a || "").split("?")[0]);
    case "t": return TEAM_OK.test(a || "") && (!b || /^[a-z0-9_]{1,24}$/.test(b));
    case "teams": return !a || (TEAM_OK.test(a) && (!b || /^[a-z0-9_]{1,24}$/.test(b)));
    case "dash": return !a || ((a === "live" || TS_OK.test(a)) && (!b || MKEY_OK.test(b)));
    case "selection": return !a || TS_OK.test(a) || (SEL_TABS.has(a) && (!b || (a === "compare" ? /^(frc)?\d{1,5}(,(frc)?\d{1,5}){0,2}$/.test(b)
      : a === "defense" ? b === "defenders" || b === "team" || TEAM_OK.test(b) : TS_OK.test(b))));
    case "picklist": return !a || TS_OK.test(a);
    case "specs": return !a || TEAM_OK.test(a);
    case "defense": return !a || a === "defenders" || TEAM_OK.test(a);
    case "schedule": return !a || MKEY_OK.test(a);
    case "compare": return !a || /^(frc)?\d{1,5}(,(frc)?\d{1,5}){0,2}$/.test(a);
    default: return true;
  }
}
CS.routeOk = routeOk;
// Back / Forward keep your place: every history entry gets an id (history.state.csn); the scroll position of the page on
// screen is saved under it when you leave, and put back when you return to that entry (sessionStorage cs:scroll).
// A new entry (a link, a typed URL) starts at the top.
try { history.scrollRestoration = "manual"; } catch (e) { }
let navId = null;
const scrollMem = () => { try { return JSON.parse(sessionStorageGet("cs:scroll") || "{}") || {}; } catch (e) { return {}; } };
function saveScroll() {
  if (navId == null) return;
  const m = scrollMem(); m[navId] = Math.round(scrollY);
  const ks = Object.keys(m); if (ks.length > 60) ks.slice(0, ks.length - 60).forEach(k => delete m[k]);
  sessionStorageSet("cs:scroll", JSON.stringify(m));
}
addEventListener("pagehide", saveScroll);
/** -> the saved scrollY when this route returns to an entry seen before (back / forward / reload), else null */
function enterEntry() {
  const st = history.state;
  if (st && st.csn) { const back = st.csn !== navId || !document.querySelector("#app > :not(.loading)") ? scrollMem()[st.csn] : null; navId = st.csn; return back ?? null; }
  navId = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  try { history.replaceState(Object.assign({}, st || {}, { csn: navId }), ""); } catch (e) { }
  return null;
}
function restoreScroll(y) {
  let n = 0;
  const go = () => { window.scrollTo(0, y); if (Math.abs(scrollY - y) > 2 && ++n < 20) setTimeout(go, 50); };   // late content: try for ~1 s
  requestAnimationFrame(go);
}
// "Skip to content" moves focus to the page (its href="#app" isn't a route)
document.addEventListener("click", e => { const a = e.target.closest && e.target.closest("a.skip"); if (a) { e.preventDefault(); $app.focus(); } });

async function route(scroll = true) {
  cleanup.forEach(f => f()); cleanup = [];
  if (scroll) saveScroll();
  const back = scroll ? enterEntry() : null;
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const done = () => {
    if (back != null) restoreScroll(back); else if (scroll) window.scrollTo(0, 0);
    // keyboard / screen reader: a new page takes focus (unless the page put it somewhere, e.g. a form field)
    if (scroll && (!document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected)) $app.focus({ preventScroll: true });
  };
  if (!routeOk(p)) {
    renderTopNav([]); crumbs([["Page not found"]]); $updated.textContent = "";
    $app.innerHTML = `<div class="empty" role="alert"><b>Page not found</b>This link isn't a page of Cyber-Scout. <div class="acts"><a class="cmp-btn" href="#/">Go to the competition</a></div></div>`;
    done();
    return;
  }
  const moved = oldLink(p);
  if (moved) { location.replace(moved); return; }                 // replace, not push: Back doesn't land on the old link again
  try {
    renderTopNav(p);
    if (p.length && CS.globalRoutes[p[0]]) await CS.globalRoutes[p[0]](...p.slice(1));
    else if (!p.length) await home();
    else if (p[1] && CS.routes[p[1]]) await CS.routes[p[1]](p[0], ...p.slice(2));
    else if (p[1] === "dash") await dashPage(p[0], p[2]);
    else if (p.length === 1) { try { await api(`${p[0]}/dashboard.json`); await dashPage(p[0]); } catch (e) { if (!/404/.test(e.message)) throw e; await eventPage(p[0]); } }
    else if (p[1] === "m") await matchPage(p[0], p[2]);
    else if (p[1] === "a") await analyzePage(p[0], p[2]);
    else if (p[1] === "t" && p[3] && CS.fieldPage) await CS.fieldPage(p[0], p[2], p[3]);
    else if (p[1] === "t") await teamPage(p[0], p[2]);
    else await eventPage(p[0], p[1], p[2]);
    if (p.length && EVK.test(p[0]) && !CS.globalRoutes[p[0]] && (!p[1] || p[1] === "dash")) compSetupBanner(p[0]);
  } catch (e) {
    const ev = p.length && EVK.test(p[0]) && !CS.globalRoutes[p[0]] ? p[0] : null;
    const off = CS.sync && CS.sync.isOffline();
    if (!off && !(ev && /\b404\b/.test(e.message) && e.message.startsWith(ev + "/"))) console.error(e);     // offline or not built yet: not a bug
    if (ev && !off && /\b404\b/.test(e.message) && await compNotBuilt(ev, p)) { done(); return; }
    // the title is the page you asked for, whatever failed (not the previous page's)
    const lab = pageLabel(p);
    if (off) {
      const down = CS.sync.clientDown && CS.sync.clientDown();
      crumbs([[down ? "Client not running" : "Not saved on this device"]]); $updated.textContent = "";
      const ao = document.getElementById("asof"); if (ao) { ao.textContent = ""; ao.title = ""; }      // nothing "as of" is shown
      const hint = CS.sync.offlineHint && CS.sync.offlineHint();
      $app.innerHTML = `<div class="empty err" role="alert">${hint || `<b>This page isn't saved on this device</b>You're not connected to the Cyber-Scout server, and this page wasn't opened or saved while you were.${CS.sync.mode === "browser" ? " Next time you're online, use “Save this event for offline” in the connection menu." : ""}`}
        <div class="acts"><button type="button" id="routeretry">Try again</button></div></div>`;
    } else {
      if (ev) crumbs(lab && lab !== "Dashboard" ? [[compName(ev), `#/${ev}`], [lab]] : [[compName(ev)]]); else if (lab) crumbs([[lab]]);
      $updated.textContent = "";
      const missing = ev && /\b404\b/.test(e.message) && e.message.startsWith(ev + "/");
      const admin = CS.platform && CS.user && CS.can("admin"), stall = missing ? buildStall(admin) : "";
      const sched = ev ? `<a class="cmp-btn" href="#/${esc(ev)}/schedule">Open the schedule</a>` : "";
      // a page file the worker hasn't written (yet): an empty state for that page, not a network error
      $app.innerHTML = !missing ? `<div class="empty err" role="alert"><b>Couldn't load this page</b>The server didn't answer as expected (${esc(e.message)}). Check the connection, then try again.
          <div class="acts"><button type="button" id="routeretry">Try again</button></div></div>`
        : p[1] === "m" || p[1] === "a" ? `<div class="empty"><b>${esc(lab)} wasn't tracked</b>There's no robot tracking for this match${stall ? `: ${stall}` : " yet. A match is tracked once its video is in (minutes after it's played when processing runs)."} Its score and teams are on the Schedule.
          <div class="acts">${sched}<button type="button" id="routeretry">Try again</button></div></div>`
        : `<div class="empty"><b>${esc(lab || "This page")} isn't built for ${esc(compName(ev))} yet</b>${stall || (p[1] === "selection" ? "Alliance-selection odds appear once the worker has enough of the schedule and results to project the rankings." : "The worker writes this page on its next pass once there's data for it.")}
          <div class="acts"><button type="button" id="routeretry">Try again</button></div></div>`;
    }
    const rb = document.getElementById("routeretry"); if (rb) rb.onclick = () => route(false);
  }
  done();
}
window.addEventListener("hashchange", () => route());
FIELD.onload = () => { };
// boot as soon as the DOM and all page scripts are in (not window "load", which also waits for fonts / images), and
// start the URL's main JSON right away so it downloads while setup.js checks the session (saves a round trip).
function bootPrefetch() {
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean), ok = s => /^[A-Za-z0-9_]{1,40}$/.test(s || "");
  if (!p.length || CS.globalRoutes[p[0]] || !/^\d{4}[a-z0-9]{1,16}$/.test(p[0])) return;
  const ev = p[0], k = (p[2] || "").split("?")[0], want = [`${ev}/event.json`];
  if (!p[1] || p[1] === "dash") want.push(ok(k) && k !== "live" ? `${ev}/dash/${k}.json` : `${ev}/dashboard.json`);
  else if ((p[1] === "m" || p[1] === "a") && ok(k)) want.push(`${ev}/m/${k}.json`);
  else if (p[1] === "t" && ok(k)) want.push(`${ev}/t/${k}.json`, `${ev}/scout.json`);
  else if (p[1] === "teams") want.push(`${ev}/scout.json`, `${ev}/dashboard.json`);
  else if (p[1] === "selection") { const st = selTs(p); if (!SEL_TABS.has(p[2]) || p[2] === "odds") want.push(st ? `${ev}/selection/${st}.json` : `${ev}/selection.json`, `${ev}/selection/index.json`); }
  else if (p[1] === "rankings" || p[1] === "schedule") want.push(`${ev}/${p[1]}.json`);
  want.forEach(x => api(x).catch(() => { }));
}
function boot() { try { bootPrefetch(); } catch (e) { } route(); }
if (document.readyState === "complete") boot(); else document.addEventListener("DOMContentLoaded", boot, { once: true });
