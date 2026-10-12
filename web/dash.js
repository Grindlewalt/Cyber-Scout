"use strict";
/* Strategy dashboard (#/<ev>/dash). Contract: live/strategy/DASHBOARD.md. Everything is optional-chained; missing blocks hide. */
(function () {
  const H = s => esc(s);
  const n0 = (v, d = 0) => v == null ? "–" : Number(v).toFixed(d);
  const tn = k => (k || "").replace("frc", "");
  const US = "frc4911";
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const fmtT = ts => { if (ts == null) return "–"; const d = new Date(ts * 1000); return DAYS[d.getDay()] + " " + d.toTimeString().slice(0, 5); };
  const dur = s => { const a = Math.abs(Math.round(s)), h = Math.floor(a / 3600), m = Math.floor(a % 3600 / 60), x = a % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
  const SH = w => String(w).replace("shift", "S").replace("auto", "Auto").replace("endgame", "End").replace("transition", "Trans");
  let S = null;   // page state

  async function load(ev, ts) {
    return ts ? api(`${ev}/dash/${ts}.json`) : api(`${ev}/dashboard.json`);
  }

  // ---------------------------------------------------------------- small pieces
  // ---------------------------------------------------------------- replay control
  function replayBar(d, idx) {
    const asof = d.event?.asof;
    const opts = idx.map(x => `<option value="${x.ts}"${asof && Math.abs(x.ts - asof) < 1 ? " selected" : ""}>${H(x.label)}</option>`).join("");
    const pos = asof ? Math.max(0, idx.findIndex(x => x.ts === asof)) : idx.length;
    return `<div class="dreplay${asof ? " on" : ""}">
      ${asof ? `<span class="rtag">REPLAY</span><span>as of <b>${fmtT(asof)}</b></span>` : `<span class="ltag">LIVE</span><span>${d.event?.quals_played ?? "?"}/${d.event?.quals_total ?? "?"} quals played</span>`}
      ${idx.length ? `<input type="range" id="dslide" min="0" max="${idx.length}" value="${pos}" step="1" aria-label="replay time">
      <select id="dsel"><option value="">Live</option>${opts}</select>` : ""}
      ${asof ? `<button id="dlive" class="dbtn">Back to live</button>` : ""}
    </div>`;
  }
  function wireReplay(idx) {
    const go = ts => { location.hash = `#/${S.ev}/dash` + (ts ? "/" + ts : ""); };
    const sl = document.getElementById("dslide"), se = document.getElementById("dsel");
    if (sl) {
      sl.oninput = () => { const i = +sl.value; document.getElementById("dslabel")?.remove(); sl.title = i >= idx.length ? "Live" : idx[i].label; };
      sl.onchange = () => { const i = +sl.value; go(i >= idx.length ? null : idx[i].ts); };
    }
    if (se) se.onchange = () => go(se.value || null);
    const b = document.getElementById("dlive"); if (b) b.onclick = () => go(null);
  }

  // ---------------------------------------------------------------- display policy (docs/STRATEGIST_REVIEW.md)
  // win % / bonus odds: round to 5%, clamp 3–97% (no false precision)
  // shown as "<5%" / ">95%" at the ends (tabs, card, schedule and tables all use this one function)
  const wp5 = p => p == null ? "–" : p < 0.05 ? "<5%" : p > 0.95 ? ">95%" : Math.round(p * 20) * 5 + "%";
  const SHS = ws => (ws || []).map(w => SH(w)).join("+");
  const covTag = c => !c ? "" : `<span class="mcov ${c.tier}" title="verified robot video: ${H(c.label)}">${c.n ? "vid " + c.n : "no vid"}</span>`;
  function mchip(k, R, us) {
    const r = R[k];
    return `<a class="dchip${k === US ? " us" : ""}" href="#/${S.ev}/t/${k}" title="${r ? `model rating ${n0(r.blend?.total)} ± ${n0(r.blend?.sd)} pts/match; ${H(r.coverage?.label || "")}` : ""}">
      <b>${tn(k)}</b><span class="rt">${r ? n0(r.blend?.total) : "–"}<small> pts</small></span>${covTag(r?.coverage)}</a>`;
  }
  function stamp(pl, d) {
    const a = pl.as_of; if (!a) return "";
    return `plan as of ${fmtT(a.ts)} · ${a.quals_played} quals posted${a.last_result ? ` (through ${H(a.last_result)})` : ""}`;
  }
  // mini field from OUR driver station: our wall at the bottom
  function miniField(pl, q) {
    const f = q.defend?.where?.field; const W = 64, L = 131, al = pl.alliance, op = al === "red" ? "blue" : "red";
    const y = v => (L - v * L).toFixed(1), x = u => (u * W).toFixed(1);
    return `<svg class="mfield" viewBox="-2 -14 ${W + 4} ${L + 28}" width="${W + 4}" height="${L + 28}" aria-label="field from our driver station">
      <rect x="0" y="0" width="${W}" height="${L}" rx="4" class="mf-bg"/>
      <rect x="0" y="${y(0.2435)}" width="${W}" height="${(0.2435 * L).toFixed(1)}" class="mf-${al}"/>
      <rect x="0" y="0" width="${W}" height="${(0.2435 * L).toFixed(1)}" class="mf-${op}"/>
      <line x1="0" x2="${W}" y1="${y(0.5)}" y2="${y(0.5)}" class="mf-mid"/>
      <text x="${W / 2}" y="${L + 11}" class="mf-t">OUR WALL</text><text x="${W / 2}" y="-4" class="mf-t">THEIR WALL</text>
      ${f ? `<circle cx="${x(f[0])}" cy="${y(f[1])}" r="9" class="mf-tgt"/><circle cx="${x(f[0])}" cy="${y(f[1])}" r="3" class="mf-dot"/>` : `<text x="${W / 2}" y="${L / 2 + 4}" class="mf-t">no shot data</text>`}
    </svg>`;
  }

  // ---------------------------------------------------------------- plans (every remaining match) + queue card
  function planTabs(d) {
    const ps = d.plans || (d.next_match ? [d.next_match] : []);
    if (!ps.length) return "";
    return `<div class="ptabs" role="tablist">${ps.map((p, i) => {
      const g = p.queue?.mode === "goal";
      return `<button class="ptab ${p.alliance}${i === S.tab ? " on" : ""}" data-i="${i}" role="tab">
        <b>${H(p.short)}</b><span>${fmtT(p.time).slice(4)}</span><em>${wp5(p.prediction?.win_prob)}</em>${g ? `<small class="rpg" title="goal mode: play for the bonus RP">RP</small>` : ""}</button>`; }).join("")}</div>`;
  }
  function line(tag, body, cls = "") { return `<div class="qline ${cls}"><div class="qtag">${tag}</div><div class="qbody">${body}</div></div>`; }
  function rpCells(o, shown) {
    const lab = { energized: "ENERGIZED", supercharged: "SUPERCHARGED", traversal: "TRAVERSAL" };
    return `<span>Win <b>${wp5(o.win)}</b></span>` + shown.map(b => `<span>${lab[b]} <b>${wp5(o[b])}</b></span>`).join("") +
      `<span class="muted">exp. RP <b>${n0(o.rp_mean, 1)}</b></span>`;
  }
  function queueCard(pl, d, R, first) {
    const q = pl.queue || {}, p = pl.prediction || {}, al = pl.alliance, op = al === "red" ? "blue" : "red";
    const usTeams = pl.teams?.[al] || [], themTeams = pl.teams?.[op] || [];
    const wp = p.win_prob, goal = q.mode === "goal" && q.lever;
    const epa = q.win_prob_epa;
    const head = goal ? `<div class="qhead goal"><div class="qbig">${H(q.lever.bonus.toUpperCase())}<small>play for this RP · ${wp5(q.lever.p)} to get it</small></div>
        <div class="qsub">${H(q.lever.text)}<div class="muted small">Win ${wp5(wp)} · EPA says ${wp5(epa)}</div></div></div>`
      : `<div class="qhead"><div class="qbig ${wp >= .5 ? "pos" : "neg"}">${wp5(wp)}<small>we win (model)</small></div>
        <div class="qsub">EPA (TBA only) says <b>${wp5(epa)}</b>${q.models_disagree ? ` <span class="mchipw" title="model and EPA differ by 15 points or more (as shown) — treat the number with care">models disagree</span>` : ""}
        <div class="muted small">expected score us ${n0(p.score?.[al]?.mean)} <small>(${n0(p.score?.[al]?.lo)}–${n0(p.score?.[al]?.hi)})</small> vs them ${n0(p.score?.[op]?.mean)} <small>(${n0(p.score?.[op]?.lo)}–${n0(p.score?.[op]?.hi)})</small></div></div></div>`;
    const au = q.auto || {}, df = q.defend, dd = q.defenders || {}, rp = q.rp || {}, fl = q.fouls || {};
    const pa = au.p_win_auto ?? .5, weWin = pa >= .5;
    const L1 = line("AUTO", `We win auto <b>${wp5(pa)}</b> <span class="muted small">(exp. ${n0(au.our_expected)} vs ${n0(au.their_expected)} FUEL${au.history_alliance_avg?.us != null ? `; TBA last 4 matches avg ${n0(au.history_alliance_avg?.us)} vs ${n0(au.history_alliance_avg?.them)}` : ""})</span><br>
      ${weWin ? `<b>Win it →</b> our hub live <em class="sh">${SHS(au.if_won?.our_active)}</em> (win ${wp5(au.if_won?.win_prob)}). <span class="swap"><b>Lose it → swap</b>: live <em class="sh">${SHS(au.if_lost?.our_active)}</em>, defend <em class="sh">${SHS(au.if_lost?.defend_in)}</em> (win ${wp5(au.if_lost?.win_prob)})</span>`
        : `<b>Likely lose it →</b> our hub live <em class="sh">${SHS(au.if_lost?.our_active)}</em> (win ${wp5(au.if_lost?.win_prob)}). <span class="swap"><b>If we win it → swap</b>: live <em class="sh">${SHS(au.if_won?.our_active)}</em>, defend <em class="sh">${SHS(au.if_won?.defend_in)}</em> (win ${wp5(au.if_won?.win_prob)})</span>`}
      ${au.caution ? `<div class="small warnt">${H(au.caution)}</div>` : ""}`);
    const L2 = df ? line("DEFEND", `<div class="qdef"><div>${(df.goal_note || df.skip_note) ? `<div class="goaln"><b>${H(df.goal_note || df.skip_note)}</b></div><span class="muted small">If you defend anyway:</span> ` : ""}<b>${tn(df.defender)}</b> defends <a href="#/${S.ev}/t/${df.target}"><b>${tn(df.target)}</b></a> in <em class="sh">${SHS(df.when)}</em>
        <span class="muted small">(their live shifts; ${SHS(df.when_alt)} if auto flips)</span><br>${df.where ? `Where: <b>${H(df.where.zone_ours)}</b> <span class="muted small">(${Math.round((df.where.share || 0) * 100)}% of its made FUEL, ${H(df.coverage || "")})</span>` : `<span class="muted">Where: no verified shot locations</span>`}
        <div class="small muted">≈${df.denied_fuel} FUEL denied <span class="basis ${df.basis}">${df.basis === "measured" ? "measured" : "assumption"}</span>${df.alt ? ` · ${H(df.alt.text)}` : ""} · ${H(pl.plan?.suggested_defender?.why || "")}</div></div>${miniField(pl, q)}</div>`) : "";
    const L3 = line("THEIR D", `${H(dd.text || "–")} ${(dd.teams || []).map(o => `<span class="mcov ${o.tier}" title="${H(o.label)}">${tn(o.team)} ${o.n_video ? (o.n_defended + "/" + o.n_video) : "n=0"}</span>`).join(" ")}
      ${dd.ignores ? `<div class="small warnt">${H(dd.ignores)}</div>` : ""}
      ${dd.effect ? `<div class="small muted">If defended: ${H(dd.effect.text)}.</div>` : ""}`);
    const L4 = line("RP", `<div class="rprow"><span class="who">Us</span>${rpCells(rp.us || {}, rp.shown || [])}</div><div class="rprow them"><span class="who">Them</span>${rpCells(rp.them || {}, rp.shown || [])}</div>
      ${rp.hidden_why ? `<div class="small muted">TRAVERSAL hidden: ${H(rp.hidden_why)}.</div>` : ""}`);
    const L5 = line("FOULS", `${H(fl.text || "–")} <span class="muted small" title="${H(fl.note || "")}">(alliance-level)</span>`, (fl.flag || []).length ? "warn" : "");
    return `<section class="card qcard ${al}">
      <div class="hhead">
        <div><div class="hl">${H(pl.label)} <span class="weare ${al}">we are ${al.toUpperCase()}</span></div><div class="muted small">${fmtT(pl.time)} · ${stamp(pl, d)}</div></div>
        ${first ? `<div class="cd"><span id="cd">–</span><small id="cdsub"></small></div>` : `<div class="cd later">${d.event?.now ? "in " + dur(pl.time - d.event.now) : ""}<small>after the earlier match${(d.plans || []).findIndex(x => x.key === pl.key) > 1 ? "es" : ""}</small></div>`}
        <div class="hlinks">${pl.preview_url ? `<a class="dbtn" href="#/${S.ev}/m/${pl.key}">Full preview</a>` : ""}</div>
      </div>
      <div class="qteams"><div class="aline ${al}"><span class="atag">US</span>${usTeams.map(k => mchip(k, R)).join("")}</div>
        <div class="aline ${op}"><span class="atag">THEM</span>${themTeams.map(k => mchip(k, R)).join("")}</div></div>
      ${head}
      <div class="qlines">${L1}${L2}${L3}${L4}${L5}</div>
      <details class="qdetails"><summary>Details: every defense option, auto routines, TBA auto history, key factors</summary>${planDetails(pl, R)}</details>
    </section>`;
  }
  function planDetails(pl, R) {
    const pd = pl.plan || {}, q = pl.queue || {};
    const small = (pd.defend || []).filter(x => (x.expected_gain_fuel ?? 0) < 2);
    const defs = (pd.defend || []).filter(x => (x.expected_gain_fuel ?? 0) >= 2).map((x, i) => `<div class="drow"><div class="dn">${i + 1}</div><div class="dbody"><div><a href="#/${S.ev}/t/${x.target}"><b>Defend ${tn(x.target)}</b></a>
        <span class="shifts">${(x.when || []).map(w => `<em>${SH(w)}</em>`).join("")}</span>${x.where?.zone_ours ? ` <span class="muted small">${H(x.where.zone_ours)}</span>` : ""}</div>
        <div class="small muted">${H(x.why)}</div></div><div class="dgain n">≈${n0(x.expected_gain_fuel)}<small>FUEL denied${x.basis === "measured" ? "" : " (assumed)"}</small></div></div>`).join("");
    const mu = (pd.auto?.matchups || []).map(x => `<div class="small"><b>${tn(x.team)}</b> <span class="muted">${x.alliance === pl.alliance ? "us" : "them"}</span> ${H(x.note)}</div>`).join("");
    const hist = q.auto?.history || {};
    const ht = Object.entries(hist).map(([t, h]) => `<tr><td>${tn(t)} <span class="muted small">${(pl.teams?.[pl.alliance] || []).includes(t) ? "us" : "them"}</span></td>${h.map(x => `<td class="num" title="${H(x.match)}">${x.alliance_auto}<small class="muted"> ${H(x.match)}</small></td>`).join("")}</tr>`).join("");
    const eg = (pd.endgame || []).filter(x => (x.p_any || 0) >= 0.05);
    const egt = eg.length ? `<table class="mini"><thead><tr><th>Team</th><th class="num">L1</th><th class="num">L2</th><th class="num">L3</th></tr></thead><tbody>${eg.map(x => `<tr><td>${tn(x.team)}</td>${["L1", "L2", "L3"].map(l => `<td class="num">${pct(x.p_climb?.[l])}</td>`).join("")}</tr>`).join("")}</tbody></table>`
      : `<div class="muted small">Nobody in this match climbs in ≥5% of matches — climb odds hidden.</div>`;
    const kf = (pd.key_factors || []).map(f => `<div class="kf"><span>${H(f.text)}</span></div>`).join("");
    return `<div class="plan">
      <div><h3>Defense options <span class="muted small">FUEL denied ≈ live-shift FUEL × (1 − shot rate kept) × 70% engagement</span></h3>${defs || '<div class="muted small">No target worth a defender (all under 2 FUEL).</div>'}${small.length ? `<div class="muted small">Not worth defending (&lt;2 FUEL): ${small.map(x => tn(x.target)).join(", ")}</div>` : ""}</div>
      <div><h3>Auto</h3>${mu || '<div class="muted small">No routine data.</div>'}
        <h3>TBA alliance auto FUEL, last 4 matches</h3><div class="muted small">the whole alliance's counted auto FUEL in each team's recent matches (TBA) — compare with the model's ${n0(q.auto?.our_expected)} (us) / ${n0(q.auto?.their_expected)} (them)</div>
        <table class="mini"><tbody>${ht}</tbody></table>
        <h3>Endgame climb</h3>${egt}</div>
      <div><h3>Key factors</h3>${kf || '<div class="muted small">–</div>'}</div></div>`;
  }
  function plansSection(d, R) {
    const ps = d.plans || (d.next_match ? [d.next_match] : []);
    if (!ps.length) return `<section class="card dhero"><h2>Next match</h2><div class="empty">No unplayed matches for ${tn(d.team)} at this point.</div></section>`;
    if (S.tab >= ps.length) S.tab = 0;
    return `<div id="plans">${planTabs(d)}${queueCard(ps[S.tab], d, R, S.tab === 0)}</div>`;
  }
  function startCountdown(d) {
    const m = d.next_match; if (!m) return;
    const el = document.getElementById("cd"), sub = document.getElementById("cdsub"); if (!el) return;
    const replay = d.event?.asof != null;
    const stale = !replay && Math.abs(m.time - Date.now() / 1000) > 172800;
    const tick = () => {
      const ref = replay || stale ? d.event.now : Date.now() / 1000, diff = m.time - ref;
      el.textContent = (diff < 0 ? "+" : "T-") + dur(diff);
      el.className = diff < 0 ? "late" : diff < 600 ? "soon" : "";
      sub.textContent = replay ? "relative to replay time" : stale ? "relative to data time (stale)" : diff < 0 ? "past predicted time" : "to predicted start";
    };
    tick(); const iv = setInterval(tick, 1000); cleanup.push(() => clearInterval(iv));
  }

  // ---------------------------------------------------------------- our team
  const LOW = new Set(["cycle_s", "dead_rate"]);
  const LAB = { cycle_s: "Cycle time (s)", accuracy: "Accuracy", fire_rate: "Fire rate (balls/s)", auto_fuel: "Auto FUEL (model)", teleop_fuel: "Teleop FUEL (model)",
    volley_size: "Balls per volley", on_move_rate: "Shots on the move", climb: "Climb rate" };
  const PCT2 = new Set(["on_move_rate"]);
  const PCT = new Set(["accuracy", "climb"]);
  function lever(l) {
    const low = LOW.has(l.name), f = v => (PCT.has(l.name) || PCT2.has(l.name)) ? pct(v) : n0(v, 1);
    const vals = [l.ours, l.event_best, l.event_median].filter(v => v != null), lo = Math.min(...vals) * 0.9, hi = Math.max(...vals) * 1.05;
    const x = v => ((v - lo) / (hi - lo) * 100).toFixed(1) + "%";
    const gap = l.event_best ? (low ? (l.ours - l.event_best) : (l.event_best - l.ours)) / l.event_best : 0;
    const ahead = low ? l.ours <= l.event_median : l.ours >= l.event_median;
    return `<div class="lev"><div class="lh"><span class="gr">${l.gap_rank ?? ""}</span><b>${H(LAB[l.name] || l.name)}</b>
      <span class="${ahead ? "pos" : "neg"}">${f(l.ours)}</span>${l.n_eff != null ? `<span class="mcov ${l.n_eff >= 3 ? "fact" : "grey"}" title="effective verified matches behind our value">n≈${n0(l.n_eff, 1)}</span>` : ""}<span class="muted small">med ${f(l.event_median)} · best ${f(l.event_best)} <a href="#/${S.ev}/t/${l.event_best_team}">${tn(l.event_best_team)}</a></span>
      <span class="gap">${gap > 0.005 ? "gap " + n0(gap * 100) + "%" : "at best"}</span></div>
      <div class="ltrack"><i class="med" style="left:${x(l.event_median)}" title="event median"></i><i class="best" style="left:${x(l.event_best)}" title="event best"></i><i class="we ${ahead ? "ok" : "bad"}" style="left:${x(l.ours)}" title="ours"></i></div></div>`;
  }
  function spark(pts, key, w = 260, h = 54, color = "#3e8bff") {
    pts = pts.filter(p => p[key] != null); if (pts.length < 2) return '<div class="muted small">not enough matches</div>';
    const v = pts.map(p => p[key]), lo = Math.min(...v), hi = Math.max(...v), sp = hi - lo || 1;
    const X = i => 4 + i / (pts.length - 1) * (w - 8), Y = y => h - 4 - (y - lo) / sp * (h - 8);
    return `<svg viewBox="0 0 ${w} ${h}" class="spark" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="2" points="${v.map((y, i) => X(i).toFixed(1) + "," + Y(y).toFixed(1)).join(" ")}"/>
      ${v.map((y, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="2.5" fill="${color}"><title>${H(pts[i].match)}: ${n0(y, 1)}</title></circle>`).join("")}</svg>
      <div class="small muted">${n0(v[0], 1)} → <b>${n0(v[v.length - 1], 1)}</b> (min ${n0(lo, 1)}, max ${n0(hi, 1)})</div>`;
  }
  function ourTeam(d) {
    const o = d.our_team; if (!o) return "";
    const lv = [...(o.levers || [])].sort((a, b) => (a.gap_rank ?? 99) - (b.gap_rank ?? 99));
    return `<section class="card"><h2>${tn(d.team)} levers <span class="muted small">biggest gap to the event best first · n≈ = verified matches behind our value</span></h2>${lv.map(lever).join("") || '<div class="muted small">No lever data.</div>'}
      <div class="trends"><div><h3>Model rating after each match <span class="muted small">pts/match</span></h3>${spark(o.trend || [], "blend_total")}</div><div><h3>Our counted FUEL <span class="muted small">tracked video only</span></h3>${spark(o.trend || [], "fuel_made", 260, 54, "#ffd43b")}</div></div></section>`;
  }

  // ---------------------------------------------------------------- schedule
  function schedule(d, R) {
    const sc = d.schedule || [];
    const rpc = s => {
      if (s.result) return `<b>${s.result.rp ?? "–"}</b><span class="muted">–${s.result.rp_them ?? "–"}</span>`;
      if (s.bonus_probs) return `<span class="muted" title="expected RP us–them">${n0(s.bonus_probs.rp_mean, 1)}–${n0(s.bonus_probs_them?.rp_mean, 1)}</span>`;
      return "";
    };
    return `<section class="card"><h2>${tn(d.team)} schedule <span class="muted small">scores and RP are us–them</span></h2><div class="tablewrap"><table class="sched"><thead><tr><th>Match</th><th>Time</th><th></th><th>With</th><th>Against</th><th class="num" title="model win probability before the match (5% steps)">Win%</th><th>Result</th><th class="num">RP</th><th></th></tr></thead><tbody>
      ${sc.map(s => { const isNext = d.next_match && d.next_match.key === s.key; const us = s.alliance, op = us === "red" ? "blue" : "red";
        const pi = (d.plans || []).findIndex(p => p.key === s.key);
        return `<tr class="${isNext ? "nextrow" : ""}">
        <td>${pi >= 0 ? `<a href="#" data-plan="${pi}">${H(s.short)}</a>` : `<a href="#/${S.ev}/m/${s.key}">${H(s.short)}</a>`}</td><td>${fmtT(s.time)}</td>
        <td><span class="adot ${s.alliance}" title="${s.alliance}"></span></td>
        <td>${(s.partners || []).map(k => `<a href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(" ")}</td>
        <td class="muted">${(s.opponents || []).map(k => `<a href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(" ")}</td>
        <td class="num">${wp5(s.win_prob)}</td>
        <td>${s.result ? `<span class="res ${s.result.won ? "w" : s.result.tie ? "" : "l"}">${s.result.won ? "W" : s.result.tie ? "T" : "L"}</span> ${s.result[us]}–${s.result[op]}` : pi >= 0 ? `<a href="#" data-plan="${pi}" class="small">plan</a>` : '<span class="muted">–</span>'}</td>
        <td class="num">${rpc(s)}</td>
        <td class="small">${s.played && s.analyze_url ? `<a href="${s.analyze_url}" title="analyze the match video">video</a>` : ""}</td></tr>`; }).join("")}
      </tbody></table></div></section>`;
  }

  // ---------------------------------------------------------------- ratings
  const COLS = [
    ["rank", "Rk", r => r.rank, "num"], ["team", "Team", r => +tn(r.team), ""], ["rec", "W-L", r => parseInt(r.record) || 0, "num"],
    ["bt", "Model", r => r.blend?.total, "num bld"], ["et", "EPA", r => r.epa?.total, "num"], ["df", "Δ", r => r.disagree?.diff, "num"],
    ["ba", "Auto", r => r.blend?.auto, "num g2"], ["bo", "Teleop", r => r.blend?.teleop, "num g2"], ["be", "Climb", r => r.blend?.endgame, "num g2"],
    ["vn", "Video", r => r.coverage?.n, ""],
    ["pr", "Proj. rank", r => r.projection?.rank_mean, ""], ["t8", "Top 8", r => r.projection?.p_top8, "num"]];
  function ratings(d) {
    const rows = d.ratings || []; if (!rows.length) return "";
    const N = rows.length;
    const key = S.sort.k, col = COLS.find(c => c[0] === key) || COLS[0];
    const sorted = [...rows].sort((a, b) => ((col[2](a) ?? -1e9) - (col[2](b) ?? -1e9)) * S.sort.dir);
    const th = c => `<th data-k="${c[0]}" class="${c[3]}${c[0] === key ? " sorted" : ""}">${c[1]}${c[0] === key ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
    const prbar = r => { const p = r.projection; if (!p) return ""; const L = v => ((v - 1) / Math.max(1, N - 1) * 100).toFixed(1);
      return `<div class="prb" title="mean ${n0(p.rank_mean, 1)}, 80% range ${p.rank_p10}-${p.rank_p90}"><i class="rg" style="left:${L(p.rank_p10)}%;width:${Math.max(1, L(p.rank_p90) - L(p.rank_p10))}%"></i><i class="mn" style="left:${L(p.rank_mean)}%"></i></div><span class="small">${p.rank_p10}–${p.rank_p90}</span>`; };
    const src = d.model?.epa_source?.display === "statbotics" ? "Statbotics EPA" : "EPA computed here from TBA results as of this time (Statbotics offline)";
    const vw = rows.map(r => r.video_weight || 0), vwm = vw.reduce((a, b) => a + b, 0) / Math.max(1, vw.length);
    const vcell = r => { const c = r.coverage; if (!c) return "–"; return `<span class="mcov ${c.tier}" title="verified robot video (v2 identity, team conf ≥ 0.6): ${H(c.label)}">${c.n ? `${c.n}${c.of ? "/" + c.of : ""}` : "none"}</span>${c.last ? ` <span class="muted small">last ${H(c.last)}</span>` : ""}`; };
    const dcell = r => { const x = r.disagree; if (!x || x.diff == null) return ""; return x.flag ? `<span class="mchipw" title="Model and EPA differ by ${n0(Math.abs(x.diff))} pts (more than 15 pts and 1.5 sd)">${x.diff > 0 ? "+" : "−"}${n0(Math.abs(x.diff))}</span>` : `<span class="muted small">${x.diff > 0 ? "+" : "−"}${n0(Math.abs(x.diff))}</span>`; };
    return `<section class="card"><h2>Ratings <span class="muted small">points per match</span></h2>
      <div class="small muted rlegend"><b>Model</b> = each team's pre-event EPA updated by this event's TBA results and, where available, verified robot video (video is on average ${Math.round(vwm * 100)}% of the information). ± = 1 sd.
        <b>EPA</b> = ${H(src)}. <b>Δ</b> = Model − EPA; highlighted when they disagree by &gt;15 pts. <b>Video</b> = verified robot-video matches / matches played; grey = fewer than 3 (treat as a hint).</div>
      <div class="tablewrap"><table class="rate"><thead>
        <tr class="grp"><th colspan="6"></th><th colspan="3" class="num">Model by period</th><th colspan="3"></th></tr>
        <tr>${COLS.map(th).join("")}</tr></thead><tbody>
      ${sorted.map(r => `<tr class="${r.team === d.team ? "usrow" : ""}" data-t="${r.team}">
        <td class="num">${r.rank ?? "–"}</td><td class="tm"><a href="#/${S.ev}/t/${r.team}"><b>${tn(r.team)}</b> <span class="muted nick">${H(r.nickname)}</span></a></td><td class="num">${H(r.record)}</td>
        <td class="num bld">${n0(r.blend?.total)}<small class="muted"> ±${n0(r.blend?.sd)}</small></td><td class="num">${n0(r.epa?.total)}</td><td class="num">${dcell(r)}</td>
        <td class="num g2">${n0(r.blend?.auto)}</td><td class="num g2">${n0(r.blend?.teleop)}</td><td class="num g2">${n0(r.blend?.endgame)}</td>
        <td>${vcell(r)}</td><td><div class="prcell">${prbar(r)}</div></td><td class="num">${wp5(r.projection?.p_top8)}</td></tr>`).join("")}
      </tbody></table></div></section>`;
  }

  function model(d) {
    const b = d.model?.backtest_asof;
    if (!b) return "";
    const f3 = v => v == null ? "–" : Number(v).toFixed(3);
    const asof = d.event?.asof ? fmtT(d.event.asof) : "now";
    if (!b.n || b.n < (b.min_n || 8)) return `<section class="dmodel muted small"><b>Can you trust the win %?</b> Only ${b.n || 0} quals have been posted as of ${asof} — too few to grade the model yet. Until then, treat every number as a rough guide.</section>`;
    const br = b.brier || {}, dx = b.blend_minus_epa, vi = b.video_increment;
    const ci = x => x ? `90% interval ${f3(-x.ci90[1])} to ${f3(-x.ci90[0])}` : "";
    const sure = x => x && x.ci90[1] < 0 ? "better" : x && x.ci90[0] > 0 ? "worse" : "not distinguishable";
    return `<section class="dmodel small"><b>Can you trust the win %?</b> As of ${asof}: the ${b.n} quals already posted (through ${H(b.through)}), each predicted only from the matches before it.
      Brier score (0 = perfect, 0.250 = coin flip): <b>Model ${f3(br.blend)}</b> · EPA ${f3(br.epa)} · Model without video ${f3(br.no_video)}.
      <div>Model vs EPA: ${f3(dx ? -dx.mean : null)} better (${ci(dx)}) — ${sure(dx)}. Video's extra help: ${f3(vi ? -vi.mean : null)} (${ci(vi)}) — ${sure(vi) === "better" ? "real" : "not yet distinguishable from zero"}.
      Picked the winner: model ${pct(b.accuracy?.blend)}, EPA ${pct(b.accuracy?.epa)}.</div>
      ${b.auto_bias_recent ? `<div class="muted">Alliance auto FUEL over ${H(b.auto_bias_recent.from)}–${H(b.auto_bias_recent.to)}: actual minus predicted ${b.auto_bias_recent.mean > 0 ? "+" : ""}${n0(b.auto_bias_recent.mean, 1)} on average (${b.auto_bias_recent.n_alliances} alliances).</div>` : ""}
      <div class="muted">Not modelled: defense and congestion between robots, per-team fouls. Defense FUEL-denied numbers marked "assumption" are not measured.</div></section>`;
  }

  // ---------------------------------------------------------------- page
  function render(d, idx) {
    const R = Object.fromEntries((d.ratings || []).map(r => [r.team, r]));
    $app.innerHTML = `<div class="dash${d.event?.asof ? " isreplay" : ""}">
      <div class="dtop"><div><h1>${H(d.event?.name || S.ev)}</h1><div class="sub">Strategy for <b>${tn(d.team)}</b> · ${fmtT(d.event?.now)}</div></div>
        <div class="evtabs"><a class="tablink on" href="#/${S.ev}/dash">Dashboard</a><a class="tablink" href="#/${S.ev}/teams">Teams</a><a class="tablink" href="#/${S.ev}/compare">Compare</a><a class="tablink" href="#/${S.ev}/picklist">Picklist</a><a class="tablink" href="#/${S.ev}/matches">Matches</a></div></div>
      ${replayBar(d, idx)}
      ${plansSection(d, R)}
      <div class="grid2 dmid">${schedule(d, R)}${ourTeam(d)}</div>
      <div id="rt">${ratings(d)}</div>
      ${model(d)}</div>`;
    wireReplay(idx); startCountdown(d); wirePlans(d, R);
    const rt = () => { document.getElementById("rt").innerHTML = ratings(d); wireSort(rt); };
    wireSort(rt);
  }
  function wirePlans(d, R) {
    const show = i => { S.tab = i;
      const p = (d.plans || [])[i];
      if (p) try { history.replaceState(null, "", `#/${S.ev}/dash/${S.ts || "live"}/${(p.short || i).toString().toLowerCase()}`); } catch (e) { }
      const el = document.getElementById("plans"); if (!el) return; el.outerHTML = plansSection(d, R); wirePlans(d, R); startCountdown(d); };
    document.querySelectorAll("#plans .ptab").forEach(b => b.onclick = () => show(+b.dataset.i));
    document.querySelectorAll("a[data-plan]").forEach(a => a.onclick = e => { e.preventDefault(); show(+a.dataset.plan); document.getElementById("plans")?.scrollIntoView({ behavior: "smooth" }); });
  }
  function wireSort(rt) {
    document.querySelectorAll("table.rate th[data-k]").forEach(th => th.onclick = () => {
      const k = th.dataset.k; S.sort = S.sort.k === k ? { k, dir: -S.sort.dir } : { k, dir: ["rank", "team", "pr"].includes(k) ? 1 : -1 }; rt(); });
  }

  function injectStyle() {
    if (document.getElementById("dash-m-style")) return;
    const st = document.createElement("style"); st.id = "dash-m-style";
    st.textContent = `
.ptabs { display: flex; gap: 6px; margin: 0 0 -1px; flex-wrap: wrap; }
.ptab { display: flex; gap: 8px; align-items: baseline; background: var(--panel2); color: var(--muted); border: 1px solid var(--line); border-bottom: 0; border-radius: 8px 8px 0 0; padding: 7px 14px; font: inherit; cursor: pointer; border-top: 3px solid var(--line); }
.ptab.red { border-top-color: var(--red); } .ptab.blue { border-top-color: var(--blue); }
.rpg { font-size: 10px; font-weight: 800; color: #1a1200; background: #ffd43b; border-radius: 3px; padding: 0 4px; }
.ptab.on { background: var(--panel); color: var(--text); } .ptab b { font-size: 15px; } .ptab span { font-size: 12px; } .ptab em { font-style: normal; font-weight: 700; color: var(--text); }
.qcard { border-left: 4px solid var(--line); border-top-left-radius: 0; } .qcard.red { border-left-color: var(--red); } .qcard.blue { border-left-color: var(--blue); }
.weare { font-size: 11px; font-weight: 800; letter-spacing: .06em; padding: 2px 7px; border-radius: 4px; vertical-align: middle; margin-left: 6px; }
.weare.red { background: var(--red); color: #fff; } .weare.blue { background: var(--blue); color: #fff; }
.cd.later { font-size: 22px; color: var(--muted); }
.qteams { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; } .qteams .aline { margin: 0; } .qteams .atag { width: 40px; }
.mcov { font-size: 10.5px; font-weight: 600; padding: 1px 5px; border-radius: 3px; background: var(--panel2); color: var(--muted); border: 1px solid var(--line); white-space: nowrap; }
.mcov.fact { color: var(--good); border-color: rgba(63,185,80,.4); } .mcov.none { opacity: .6; }
.qhead { display: flex; gap: 22px; align-items: center; margin: 14px 0 6px; }
.qbig { font-size: 56px; font-weight: 800; line-height: 1; } .qbig.pos { color: var(--good); } .qbig.neg { color: var(--bad); }
.qbig small { display: block; font-size: 11px; font-weight: 500; color: var(--muted); letter-spacing: .06em; text-transform: uppercase; margin-top: 4px; }
.qhead.goal .qbig { font-size: 40px; color: #ffd43b; } .qsub { font-size: 15px; }
.mchipw { background: rgba(210,153,34,.18); color: var(--warn); border: 1px solid rgba(210,153,34,.5); border-radius: 10px; padding: 1px 8px; font-size: 11.5px; font-weight: 700; }
.qlines { border-top: 1px solid var(--line); margin-top: 8px; }
.qline { display: grid; grid-template-columns: 74px 1fr; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--line); font-size: 14.5px; line-height: 1.5; }
.qline.warn .qtag { color: var(--warn); }
.qtag { font-size: 11px; font-weight: 800; letter-spacing: .08em; color: var(--muted); padding-top: 3px; }
em.sh { font-style: normal; font-weight: 700; background: var(--panel2); border: 1px solid var(--line); border-radius: 4px; padding: 0 6px; }
.swap { color: var(--muted); } .swap b { color: var(--text); } .warnt { color: var(--warn); margin-top: 2px; }
.qdef { display: flex; gap: 14px; justify-content: space-between; align-items: flex-start; }
.basis { font-size: 10.5px; padding: 0 5px; border-radius: 3px; border: 1px dashed var(--muted); } .basis.measured { border-style: solid; color: var(--good); border-color: var(--good); }
.mfield { flex: none; } .mf-bg { fill: #1f2a20; stroke: var(--line); } .mf-red { fill: rgba(229,72,77,.22); } .mf-blue { fill: rgba(62,139,255,.22); }
.mf-mid { stroke: #ffffff33; stroke-dasharray: 3 3; } .mf-t { fill: var(--muted); font-size: 8px; text-anchor: middle; letter-spacing: .05em; }
.mf-tgt { fill: rgba(255,212,59,.25); stroke: #ffd43b; stroke-width: 1.5; } .mf-dot { fill: #ffd43b; }
.rprow { display: flex; gap: 14px; flex-wrap: wrap; align-items: baseline; } .rprow .who { width: 44px; font-weight: 700; } .rprow.them { color: var(--muted); } .rprow.them b { color: var(--text); font-weight: 600; }
.qdetails { margin-top: 10px; } .qdetails summary { cursor: pointer; color: #7cb4ff; font-size: 13px; padding: 4px 0; }
.dgain.n { color: var(--text); } .goaln { color: #ffd43b; margin-bottom: 2px; }
.rlegend { margin: -4px 0 10px; line-height: 1.5; }
.dmodel { padding: 12px 14px; margin-bottom: 24px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; line-height: 1.6; }
@media (max-width: 900px) { .qteams { grid-template-columns: 1fr; } .qline { grid-template-columns: 1fr; gap: 2px; } .qbig { font-size: 44px; } .qdef { flex-direction: column; } }`;
    document.head.appendChild(st);
  }

  window.dashPage = async function (ev, ts) {
    if (ts === "live") ts = null;          // #/<ev>/dash/live/<plan> = live view with a plan tab open
    S = { ev, ts, tab: 0, sort: S && S.ev === ev ? S.sort : { k: "bt", dir: -1 } };
    injectStyle();
    let idx = []; try { idx = await api(`${ev}/dash/index.json`); } catch (e) { }
    const d = await load(ev, ts);
    // linkable plan tabs: #/<ev>/dash/<ts|live>/<q51 | plan index>
    const want = (location.hash.split("/")[4] || "").toLowerCase(), ps = d.plans || [];
    const byKey = ps.findIndex(p => (p.short || "").toLowerCase() === want);
    S.tab = byKey >= 0 ? byKey : (/^\d+$/.test(want) && +want < ps.length ? +want : 0);
    crumbs([[d.event?.name || ev, `#/${ev}`], ["Dashboard"]]); $updated.textContent = d.event?.asof ? "replay" : "";
    render(d, idx);
    if (!ts) {   // live: refresh every 60s without losing scroll
      const iv = setInterval(async () => { try { const n = await load(ev, null); if (JSON.stringify(n.event) !== JSON.stringify(d.event) || n.next_match?.key !== d.next_match?.key) route(false); } catch (e) { } }, 60000);
      cleanup.push(() => clearInterval(iv));
    }
  };
})();
