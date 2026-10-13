"use strict";
/* Strategy dashboard (#/<ev>/dash[/<ts|live>[/<q51>]]). Contract: live/strategy/DASHBOARD.md. Everything is optional-chained;
 * missing blocks hide. Screen job: "what do I tell the drive team for our next match, and how is our event going".
 * Order = importance: our match rail (pick a match) -> queue card (six lines) + our event -> schedule, levers, ratings
 * behind disclosures -> honest footer. The as-of time lives in the toolbar (app.js renderTimebar), not on this page. */
(function () {
  const H = s => esc(s);
  const n0 = (v, d = 0) => v == null ? "–" : Number(v).toFixed(d);
  const tn = k => (k || "").replace("frc", "");
  let US = "frc4911";     // our team: dashboard.json "team" (the app's team number), set per page load
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const fmtT = ts => { if (ts == null) return "–"; const d = new Date(ts * 1000); return DAYS[d.getDay()] + " " + d.toTimeString().slice(0, 5); };
  const hhmm = ts => ts == null ? "–" : new Date(ts * 1000).toTimeString().slice(0, 5);
  const dur = s => { const a = Math.abs(Math.round(s)), h = Math.floor(a / 3600), m = Math.floor(a % 3600 / 60), x = a % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
  const SH = w => String(w).replace("shift", "S").replace("auto", "Auto").replace("endgame", "End").replace("transition", "Trans");
  const OPEN = id => { try { return localStorage.getItem("cs:dash:" + id) === "1"; } catch (e) { return false; } };
  const SETOPEN = (id, v) => { try { v ? localStorage.setItem("cs:dash:" + id, "1") : localStorage.removeItem("cs:dash:" + id); } catch (e) { } };
  let S = null;   // page state

  async function load(ev, ts) {
    return ts ? api(`${ev}/dash/${ts}.json`) : api(`${ev}/dashboard.json`);
  }

  // ---------------------------------------------------------------- display policy (docs/STRATEGIST_REVIEW.md)
  // win % / bonus odds: round to 5%, shown as "<5%" / ">95%" at the ends (tabs, card, schedule and tables all use this one function)
  const wp5 = p => p == null ? "–" : p < 0.05 ? "<5%" : p > 0.95 ? ">95%" : Math.round(p * 20) * 5 + "%";
  const SHS = ws => (ws || []).map(w => SH(w)).join("+");
  const shp = ws => `<em class="sh">${SHS(ws) || "–"}</em>`;
  const covTag = c => !c ? "" : `<span class="mcov ${c.tier}" title="verified robot video: ${H(c.label)}">${c.n ? "vid " + c.n : "no vid"}</span>`;
  const plansOf = d => d.plans || (d.next_match ? [d.next_match] : []);
  const ALLY = a => a === "red" ? "Red" : "Blue";
  // livestream fast result for a match TBA has not posted (S.live: event.json rows with score_source "livestream"; live view only)
  const streamRes = s => {
    const L = !s.played && S.live && S.live[s.key]; if (!L || !s.alliance) return null;
    const us = s.alliance, op = us === "red" ? "blue" : "red", a = L[us] && L[us].score, b = L[op] && L[op].score;
    if (a == null || b == null) return null;
    return { [us]: a, [op]: b, won: L.winner === us, tie: L.winner === "tie" || (!L.winner && a === b), row: L };
  };

  function mchip(k, R) {
    const r = R[k];
    return `<a class="dchip${k === US ? " us" : ""}" href="#/${S.ev}/t/${k}" title="${r ? `${tn(k)} ${H(r.nickname || "")}: model rating ${n0(r.blend?.total)} ± ${n0(r.blend?.sd)} pts/match; ${H(r.coverage?.label || "")}` : ""}">
      <b>${tn(k)}</b>${k === US ? `<span class="ustag">us</span>` : ""}<span class="rt">${r ? n0(r.blend?.total) : "–"}<small> pts</small></span>${covTag(r?.coverage)}</a>`;
  }
  function stamp(pl) {
    const a = pl.as_of; if (!a) return "";
    return `plan as of ${fmtT(a.ts)} · ${a.quals_played} quals posted${a.last_result ? ` (through ${H(a.last_result)})` : ""}`;
  }
  // mini field from OUR driver station: our wall at the bottom; the target is an opponent, so it wears their colour
  function miniField(pl, q) {
    const f = q.defend?.where?.field; const W = 64, L = 131, al = pl.alliance, op = al === "red" ? "blue" : "red";
    const y = v => (L - v * L).toFixed(1), x = u => (u * W).toFixed(1);
    const zone = q.defend?.where?.zone_ours;
    return `<svg class="mfield" viewBox="-4 -16 ${W + 8} ${L + 32}" role="img" aria-label="Field seen from our driver station${zone ? `: defend at ${H(zone)}` : ""}">
      <rect x="0" y="0" width="${W}" height="${L}" rx="4" class="mf-bg"/>
      <rect x="0" y="${y(0.2435)}" width="${W}" height="${(0.2435 * L).toFixed(1)}" class="mf-${al}"/>
      <rect x="0" y="0" width="${W}" height="${(0.2435 * L).toFixed(1)}" class="mf-${op}"/>
      <line x1="0" x2="${W}" y1="${y(0.5)}" y2="${y(0.5)}" class="mf-mid"/>
      <text x="${W / 2}" y="${L + 12}" class="mf-t">OUR WALL</text><text x="${W / 2}" y="-5" class="mf-t">THEIR WALL</text>
      ${f ? `<circle cx="${x(f[0])}" cy="${y(f[1])}" r="9" class="mf-tgt ${op}"/><circle cx="${x(f[0])}" cy="${y(f[1])}" r="3" class="mf-dot ${op}"/>` : `<text x="${W / 2}" y="${L / 2 + 4}" class="mf-t">no shot data</text>`}
    </svg>`;
  }

  // ---------------------------------------------------------------- signature: our matches as one wrapping strip (played -> now -> plans)
  // Never scrolls sideways: played matches are compact result pills, upcoming plans are the prominent buttons.
  // On a phone only the last three results show, behind a "+N earlier" toggle, so the next match is always on screen.
  function rail(d) {
    const sc = d.schedule || [], ps = plansOf(d);
    if (!sc.length && !ps.length) return "";
    const items = sc.length ? sc : ps.map(p => ({ key: p.key, short: p.short, time: p.time, alliance: p.alliance, played: false, win_prob: p.prediction?.win_prob }));
    const played = items.filter(s => s.played && s.result), nOld = Math.max(0, played.length - 3);
    const streamPill = (s, r) => {
      const us = s.alliance, op = us === "red" ? "blue" : "red", wl = r.won ? "W" : r.tie ? "T" : "L";
      const desc = `${s.label || s.short}: ${wl === "W" ? "won" : wl === "T" ? "tied" : "lost"} ${r[us]}–${r[op]} as ${us}, score from the livestream (TBA hasn't posted it yet)`;
      return `<li><a class="rl-p ${us} rl-live" href="#/${S.ev}/m/${s.key}" title="${H(desc)}. Open the match." aria-label="${H(desc)}">
        <span class="rl-k">${H(s.short)}</span><span class="res ${wl === "W" ? "w" : wl === "L" ? "l" : ""}">${wl}</span><span class="rl-sc">${r[us]}–${r[op]}</span>${CS.liveTag(r.row)}</a></li>`;
    };
    let nowDone = false, k = 0;
    const nowMark = `<li class="rl-now"><span>${d.event?.asof ? "replay" : "now"}<b>${hhmm(d.event?.now)}</b></span></li>`;
    const more = nOld ? `<li class="rl-more"><button type="button" class="rl-mbtn" aria-expanded="false">+${nOld} earlier</button></li>` : "";
    const cells = items.map(s => {
      const pi = ps.findIndex(p => p.key === s.key);
      let pre = "";
      const sr = streamRes(s);
      if (sr) return streamPill(s, sr);
      if (!s.played && !nowDone) { nowDone = true; pre = nowMark; }
      if (s.played && s.result) {
        const r = s.result, us = s.alliance, op = us === "red" ? "blue" : "red", wl = r.won ? "W" : r.tie ? "T" : "L", i = k++;
        const desc = `${s.label || s.short}: ${wl === "W" ? "won" : wl === "T" ? "tied" : "lost"} ${r[us]}–${r[op]} as ${us}, ${r.rp ?? "–"} RP`;
        return (i === nOld ? more : "") + pre + `<li class="${i < nOld ? "old" : ""}"><a class="rl-p ${us}" href="#/${S.ev}/m/${s.key}" title="${H(desc)}. Open the match." aria-label="${H(desc)}">
          <span class="rl-k">${H(s.short)}</span><span class="res ${wl === "W" ? "w" : wl === "L" ? "l" : ""}">${wl}</span><span class="rl-sc">${r[us]}–${r[op]}</span></a></li>`;
      }
      if (pi >= 0) {
        const p = ps[pi], g = p.queue?.mode === "goal";
        return pre + `<li><button type="button" id="rt-${pi}" aria-pressed="${pi === S.tab}" aria-controls="plans" data-i="${pi}" class="rl-m upc ${p.alliance}${pi === S.tab ? " on" : ""}" title="${H(p.label)} at ${fmtT(p.time)}, we are ${p.alliance}: model win ${wp5(p.prediction?.win_prob)}${g ? "; goal mode: play for the bonus RP" : ""}. Show its plan.">
          <span class="rl-k">${H(p.short)}<small>${hhmm(p.time)}</small></span><span class="rl-r"><b>${wp5(p.prediction?.win_prob)}</b>${g ? `<span class="rpg">RP</span>` : ""}</span></button></li>`;
      }
      return pre + `<li><span class="rl-m later ${s.alliance || ""}" title="${H(s.label || s.short)}: no plan yet"><span class="rl-k">${H(s.short)}<small>${hhmm(s.time)}</small></span><span class="rl-r">${wp5(s.win_prob)}</span></span></li>`;
    }).join("") + (nowDone ? "" : nowMark);
    const w = played.filter(s => s.result.won).length, t = played.filter(s => s.result.tie).length;
    return `<nav class="rail" aria-label="${tn(d.team)} matches">
      <div class="rl-h"><span>${tn(d.team)}'s matches</span>${played.length ? `<span class="muted">${w}–${played.length - w - t}–${t}</span>` : ""}</div>
      <ol class="rl">${cells}</ol></nav>`;
  }

  // ---------------------------------------------------------------- queue card (six lines, evidence quieter than the instruction)
  function line(tag, main, ev, cls = "") {
    return `<li class="ql ${cls}"><div class="ql-tag">${tag}</div><div class="ql-body"><div class="ql-main">${main}</div>${ev ? `<div class="ql-ev">${ev}</div>` : ""}</div></li>`;
  }
  function rpRow(who, o, shown, cls) {
    const lab = { energized: "ENERGIZED", supercharged: "SUPERCHARGED", traversal: "TRAVERSAL" };
    return `<tr class="${cls}"><th scope="row">${who}</th><td>Win <b>${wp5(o.win)}</b></td>${shown.map(b => `<td>${lab[b]} <b>${wp5(o[b])}</b></td>`).join("")}<td class="muted">exp. RP <b>${n0(o.rp_mean, 1)}</b></td></tr>`;
  }
  function queueCard(pl, d, R, first) {
    const q = pl.queue || {}, p = pl.prediction || {}, al = pl.alliance, op = al === "red" ? "blue" : "red";
    const usTeams = pl.teams?.[al] || [], themTeams = pl.teams?.[op] || [];
    const wp = p.win_prob, goal = q.mode === "goal" && q.lever, epa = q.win_prob_epa;
    const disagree = q.models_disagree ? `<span class="mchipw" title="model and EPA differ by 15 points or more (as shown) — treat the number with care">models disagree</span>` : "";
    const bar = wp == null ? "" : `<div class="wbar" role="img" aria-label="model: ${ALLY(al)} wins ${wp5(wp)}"><i class="${al}" style="width:${(wp * 100).toFixed(1)}%"></i><i class="${op}" style="width:${(100 - wp * 100).toFixed(1)}%"></i></div>`;
    const scores = `expected score us <b>${n0(p.score?.[al]?.mean)}</b> <span class="rng">(${n0(p.score?.[al]?.lo)}–${n0(p.score?.[al]?.hi)})</span> vs them <b>${n0(p.score?.[op]?.mean)}</b> <span class="rng">(${n0(p.score?.[op]?.lo)}–${n0(p.score?.[op]?.hi)})</span>`;
    const head = goal
      ? `<div class="qwin goal"><div class="qbig"><span class="qb-n">${H(q.lever.bonus.toUpperCase())}</span><span class="qb-l">goal mode · play for this RP · ${wp5(q.lever.p)} to get it</span></div>
          <div class="qsub"><div class="qs-main">${H(q.lever.text)}</div><div class="muted">Win ${wp5(wp)} (model) · EPA (TBA only) says ${wp5(epa)} ${disagree}</div>${bar}</div></div>`
      : `<div class="qwin"><div class="qbig ${al}"><span class="qb-n">${wp5(wp)}</span><span class="qb-l">we win · model</span></div>
          <div class="qsub"><div class="qs-main">EPA (TBA only) says <b>${wp5(epa)}</b> ${disagree}</div>${bar}<div class="muted">${scores}</div></div></div>`;
    const au = q.auto || {}, df = q.defend, dd = q.defenders || {}, rp = q.rp || {}, fl = q.fouls || {};
    const pa = au.p_win_auto ?? .5, weWin = pa >= .5;
    const L1 = line("Auto",
      weWin ? `Win auto → our hub live ${shp(au.if_won?.our_active)} <span class="muted">(win ${wp5(au.if_won?.win_prob)})</span>`
            : `Likely lose auto → our hub live ${shp(au.if_lost?.our_active)} <span class="muted">(win ${wp5(au.if_lost?.win_prob)})</span>`,
      `<div class="swap">${weWin ? `<b>Lose it → swap</b>: live ${shp(au.if_lost?.our_active)}, defend ${shp(au.if_lost?.defend_in)} (win ${wp5(au.if_lost?.win_prob)})`
                                 : `<b>If we win it → swap</b>: live ${shp(au.if_won?.our_active)}, defend ${shp(au.if_won?.defend_in)} (win ${wp5(au.if_won?.win_prob)})`}</div>
       We win auto <b>${wp5(pa)}</b> · exp. ${n0(au.our_expected)} vs ${n0(au.their_expected)} FUEL${au.history_alliance_avg?.us != null ? ` · TBA last 4 matches avg ${n0(au.history_alliance_avg?.us)} vs ${n0(au.history_alliance_avg?.them)}` : ""}
       ${au.caution ? `<div class="warnt">${H(au.caution)}</div>` : ""}`);
    const L2 = df ? line("Defend",
      `${(df.goal_note || df.skip_note) ? `<div class="goaln">${H(df.goal_note || df.skip_note)}</div><span class="muted">If you defend anyway:</span> ` : ""}<b>${tn(df.defender)}</b> defends <a class="tgt ${op}" href="#/${S.ev}/t/${df.target}">${tn(df.target)}</a> in ${shp(df.when)}
        <div class="ql-where">${df.where ? `Where: <b>${H(df.where.zone_ours)}</b>` : `<span class="muted">Where: no verified shot locations</span>`}</div>`,
      `${SHS(df.when_alt) ? `Their live shifts; ${SHS(df.when_alt)} if auto flips. ` : ""}${df.where ? `${Math.round((df.where.share || 0) * 100)}% of its made FUEL there (${H(df.coverage || "")}). ` : ""}
       ≈${df.denied_fuel} FUEL denied <span class="basis ${df.basis}">${df.basis === "measured" ? "measured" : "assumption"}</span>${df.alt ? ` · ${H(df.alt.text)}` : ""}${pl.plan?.suggested_defender?.why ? ` · ${H(pl.plan.suggested_defender.why)}` : ""}`,
      "hasfield") : "";
    const L3 = line("Their D", `${H(dd.text || "–")}`,
      `${(dd.teams || []).map(o => `<span class="mcov ${o.tier}" title="${H(o.label)}">${tn(o.team)} ${o.n_video ? (o.n_defended + "/" + o.n_video) : "n=0"}</span>`).join(" ")}
       ${dd.ignores ? `<div class="warnt">${H(dd.ignores)}</div>` : ""}${dd.effect ? `<div>If defended: ${H(dd.effect.text)}.</div>` : ""}`);
    const L4 = line("RP", `<table class="rpt"><tbody>${rpRow("Us", rp.us || {}, rp.shown || [], "")}${rpRow("Them", rp.them || {}, rp.shown || [], "them")}</tbody></table>`,
      rp.hidden_why ? `TRAVERSAL hidden: ${H(rp.hidden_why)}.` : "");
    const L5 = line("Fouls", `${H(fl.text || "–")}`, `<span title="${H(fl.note || "")}">alliance-level${fl.note ? ": " + H(fl.note) : ""}</span>`, (fl.flag || []).length ? "warn" : "");
    const later = !first && d.event?.now ? `in ${dur(pl.time - d.event.now)}` : "";
    return `<section class="qcard ${al}" id="plans" aria-labelledby="rt-${S.tab}">
      <header class="qhead">
        <div class="qid"><h2>${H(pl.label)}</h2><span class="weare ${al}">we are ${al.toUpperCase()}</span>
          <div class="qmeta">${fmtT(pl.time)} · ${stamp(pl)}</div></div>
        ${first ? `<div class="cd"><span id="cd">–</span><small id="cdsub"></small></div>` : `<div class="cd later"><span>${later}</span><small>after the earlier match${(d.plans || []).findIndex(x => x.key === pl.key) > 1 ? "es" : ""}</small></div>`}
        ${pl.preview_url ? `<a class="dbtn" href="#/${S.ev}/m/${pl.key}">Full preview</a>` : ""}
      </header>
      <div class="qteams"><div class="aline ${al}"><span class="atag">Us</span>${usTeams.map(k => mchip(k, R)).join("")}</div>
        <div class="aline ${op}"><span class="atag">Them</span>${themTeams.map(k => mchip(k, R)).join("")}</div></div>
      ${head}
      <ol class="qlines">${L1}${L2 ? L2.replace('<div class="ql-body">', `<div class="ql-body"><div class="ql-fieldwrap">${miniField(pl, q)}</div>`) : ""}${L3}${L4}${L5}</ol>
      <details class="qdetails"><summary>Details: every defense option, auto routines, TBA auto history, key factors</summary>${planDetails(pl, R)}</details>
    </section>`;
  }
  function planDetails(pl, R) {
    const pd = pl.plan || {}, q = pl.queue || {};
    const small = (pd.defend || []).filter(x => (x.expected_gain_fuel ?? 0) < 2);
    const defs = (pd.defend || []).filter(x => (x.expected_gain_fuel ?? 0) >= 2).map((x, i) => `<div class="drow"><div class="dn">${i + 1}</div><div class="dbody"><div><a href="#/${S.ev}/t/${x.target}"><b>Defend ${tn(x.target)}</b></a>
        <span class="shifts">${(x.when || []).map(w => `<em>${SH(w)}</em>`).join("")}</span>${x.where?.zone_ours ? ` <span class="muted small">${H(x.where.zone_ours)}</span>` : ""}</div>
        <div class="small muted">${H(x.why)}</div></div><div class="dgain">≈${n0(x.expected_gain_fuel)}<small>FUEL denied${x.basis === "measured" ? "" : " (assumed)"}</small></div></div>`).join("");
    const mu = (pd.auto?.matchups || []).map(x => `<div class="small"><b>${tn(x.team)}</b> <span class="muted">${x.alliance === pl.alliance ? "us" : "them"}</span> ${H(x.note)}</div>`).join("");
    const hist = q.auto?.history || {};
    const ht = Object.entries(hist).map(([t, h]) => `<div class="aht"><b>${tn(t)}</b> <span class="muted small">${(pl.teams?.[pl.alliance] || []).includes(t) ? "us" : "them"}</span> ${h.map(x => `<span title="${H(x.match)}">${x.alliance_auto}<small class="muted"> ${H(x.match)}</small></span>`).join(" ")}</div>`).join("");
    const eg = (pd.endgame || []).filter(x => (x.p_any || 0) >= 0.05);
    const egt = eg.length ? `<table class="mini"><thead><tr><th>Team</th><th class="num">L1</th><th class="num">L2</th><th class="num">L3</th></tr></thead><tbody>${eg.map(x => `<tr><td>${tn(x.team)}</td>${["L1", "L2", "L3"].map(l => `<td class="num">${pct(x.p_climb?.[l])}</td>`).join("")}</tr>`).join("")}</tbody></table>`
      : `<div class="muted small">Nobody in this match climbs in ≥5% of matches — climb odds hidden.</div>`;
    const kf = (pd.key_factors || []).map(f => `<div class="kf"><span>${H(f.text)}</span></div>`).join("");
    return `<div class="plan">
      <div><h3>Defense options ${CS.help("FUEL denied ≈ live-shift FUEL × (1 − shot rate kept) × 70% engagement.", "How FUEL denied is estimated")}</h3>${defs || '<div class="muted small">No target worth a defender (all under 2 FUEL).</div>'}${small.length ? `<div class="muted small">Not worth defending (&lt;2 FUEL): ${small.map(x => tn(x.target)).join(", ")}</div>` : ""}</div>
      <div><h3>Auto</h3>${mu || '<div class="muted small">No routine data.</div>'}
        <h3>TBA alliance auto FUEL, last 4 matches ${CS.help(`The whole alliance's counted auto FUEL in each team's recent matches (TBA). Compare with the model's ${n0(q.auto?.our_expected)} (us) / ${n0(q.auto?.their_expected)} (them).`, "What this table shows")}</h3>
        <div class="ahts">${ht}</div>
        <h3>Endgame climb</h3>${egt}</div>
      <div><h3>Key factors</h3>${kf || '<div class="muted small">–</div>'}</div></div>`;
  }
  function plansSection(d, R) {
    const ps = plansOf(d);
    if (!ps.length) return `<section class="qcard empty-q" id="plans"><h2>Next match</h2><p>No unplayed matches for ${tn(d.team)} ${d.event?.asof ? "at this replay time" : "right now"}.</p>
      <p class="muted">${d.event?.asof ? "Pick an earlier time in the toolbar to see the plans as they stood." : "Results, rankings and selection odds are below and in the sidebar."}</p></section>`;
    if (S.tab >= ps.length) S.tab = 0;
    return queueCard(ps[S.tab], d, R, S.tab === 0);
  }
  function startCountdown(d) {
    const m = d.next_match; if (!m) return;
    const el = document.getElementById("cd"), sub = document.getElementById("cdsub"); if (!el) return;
    const replay = d.event?.asof != null;
    const stale = !replay && Math.abs(m.time - Date.now() / 1000) > 172800;
    const tick = () => {
      const ref = replay || stale ? d.event.now : Date.now() / 1000, diff = m.time - ref;
      el.textContent = (diff < 0 ? "+" : "T−") + dur(diff);
      el.className = diff < 0 ? "late" : diff < 600 ? "soon" : "";
      sub.textContent = replay ? "relative to replay time" : stale ? "relative to data time (stale)" : diff < 0 ? "past predicted time" : "to predicted start";
    };
    tick(); const iv = setInterval(tick, 1000); cleanup.push(() => clearInterval(iv));
  }

  // ---------------------------------------------------------------- how our event is going (side panel)
  function ourEvent(d, R) {
    const r = R[d.team] || {}, pr = r.projection || d.our_team?.projection || {}, N = (d.ratings || []).length;
    const tr = (d.our_team?.trend || []).filter(x => x.blend_total != null);
    const lv = [...(d.our_team?.levers || [])].filter(l => l.gap_rank != null).sort((a, b) => a.gap_rank - b.gap_rank)[0];
    const dis = r.disagree?.flag ? `<span class="mchipw" title="Model and EPA differ by ${n0(Math.abs(r.disagree.diff))} pts (more than 15 pts and 1.5 sd)">models disagree</span>` : "";
    return `<section class="side card" aria-labelledby="oe-h"><h2 id="oe-h">${tn(d.team)} at this event</h2>
      <dl class="oe">
        <div><dt>Rank now</dt><dd>${r.rank ?? "–"}<small>${N ? ` of ${N}` : ""}</small></dd></div>
        <div><dt>Record</dt><dd>${H(r.record || "–")}<small>${r.rp != null ? ` ${n0(r.rp)} RP` : ""}</small></dd></div>
        <div title="final qualification rank, mean and 10–90% range of the simulation"><dt>Projected rank</dt><dd>${n0(pr.rank_mean)}<small>${pr.rank_p10 != null ? ` (${pr.rank_p10}–${pr.rank_p90})` : ""}</small></dd></div>
        <div><dt>Top 8 odds</dt><dd>${wp5(pr.p_top8)}</dd></div>
        <div title="expected final ranking points (RP) after the remaining quals"><dt>Expected final RP</dt><dd>${n0(pr.rp_mean, 1)}</dd></div>
        <div title="ranking score = RP per match, expected at the end of quals"><dt>Expected RS</dt><dd>${n0(pr.rs_mean, 2)}</dd></div>
      </dl>
      <div class="oe-row"><span class="muted">Model rating</span> <b>${n0(r.blend?.total)}</b><small class="muted"> ± ${n0(r.blend?.sd)} pts/match</small> · <span class="muted">EPA</span> ${n0(r.epa?.total)} ${dis}</div>
      ${tr.length >= 2 ? `<div class="oe-spark">${spark(tr, "blend_total")}<div class="small muted">rating after each match: ${n0(tr[0].blend_total, 1)} → <b>${n0(tr[tr.length - 1].blend_total, 1)}</b></div></div>` : ""}
      <div class="oe-row">${covPill(r.coverage || null)} <span class="small muted">verified robot video</span></div>
      ${lv ? `<div class="oe-lever"><span class="muted small">Biggest lever (pit)</span><div><b>${H(LAB[lv.name] || lv.name)}</b> ${fmtLev(lv, lv.ours)} <span class="muted">vs best ${fmtLev(lv, lv.event_best)} (${tn(lv.event_best_team)})</span></div>
        <button type="button" class="linkbtn" data-open="levers">All levers</button></div>` : ""}
    </section>`;
  }

  // ---------------------------------------------------------------- levers (pit material)
  const LOW = new Set(["cycle_s", "dead_rate"]);
  const LAB = { cycle_s: "Cycle time (s)", accuracy: "Accuracy", fire_rate: "Fire rate (balls/s)", auto_fuel: "Auto FUEL (model)", teleop_fuel: "Teleop FUEL (model)",
    volley_size: "Balls per volley", on_move_rate: "Shots on the move", climb: "Climb rate" };
  const PCTS = new Set(["accuracy", "climb", "on_move_rate"]);
  const fmtLev = (l, v) => PCTS.has(l.name) ? pct(v) : n0(v, 1);
  function lever(l) {
    const low = LOW.has(l.name), f = v => fmtLev(l, v);
    const vals = [l.ours, l.event_best, l.event_median].filter(v => v != null), lo = Math.min(...vals) * 0.9, hi = Math.max(...vals) * 1.05;
    const x = v => ((v - lo) / (hi - lo || 1) * 100).toFixed(1) + "%";
    const gap = l.event_best ? (low ? (l.ours - l.event_best) : (l.event_best - l.ours)) / l.event_best : 0;
    const ahead = low ? l.ours <= l.event_median : l.ours >= l.event_median;
    return `<div class="lev"><div class="lh"><span class="gr">${l.gap_rank ?? ""}</span><b>${H(LAB[l.name] || l.name)}</b>
      <span class="lv-ours">${f(l.ours)}</span><span class="small muted">${ahead ? "above" : "below"} median</span>${l.n_eff != null ? `<span class="mcov ${l.n_eff >= 3 ? "fact" : "grey"}" title="effective verified matches behind our value">n≈${n0(l.n_eff, 1)}</span>` : ""}<span class="muted small">med ${f(l.event_median)} · best ${f(l.event_best)} <a href="#/${S.ev}/t/${l.event_best_team}">${tn(l.event_best_team)}</a></span>
      <span class="gap">${gap > 0.005 ? "gap " + n0(gap * 100) + "%" : "at best"}</span></div>
      <div class="ltrack" aria-hidden="true"><i class="med" style="left:${x(l.event_median)}" title="event median"></i><i class="best" style="left:${x(l.event_best)}" title="event best"></i><i class="we" style="left:${x(l.ours)}" title="ours"></i></div></div>`;
  }
  function spark(pts, key, w = 260, h = 48) {
    pts = pts.filter(p => p[key] != null); if (pts.length < 2) return '<div class="muted small">not enough matches</div>';
    const v = pts.map(p => p[key]), lo = Math.min(...v), hi = Math.max(...v), sp = hi - lo || 1;
    const X = i => 4 + i / (pts.length - 1) * (w - 8), Y = y => h - 4 - (y - lo) / sp * (h - 8);
    return `<svg viewBox="0 0 ${w} ${h}" class="spark" preserveAspectRatio="none" role="img" aria-label="${n0(v[0], 1)} to ${n0(v[v.length - 1], 1)}"><polyline fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke" points="${v.map((y, i) => X(i).toFixed(1) + "," + Y(y).toFixed(1)).join(" ")}"/>
      ${v.map((y, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="2.5" fill="currentColor"><title>${H(pts[i].short || pts[i].match)}: ${n0(y, 1)}</title></circle>`).join("")}</svg>`;
  }
  function levers(d) {
    const o = d.our_team; if (!o) return "";
    const lv = [...(o.levers || [])].sort((a, b) => (a.gap_rank ?? 99) - (b.gap_rank ?? 99));
    const tr = o.trend || [];
    return `<div class="small muted lv-legend"><span class="lk"><i class="we"></i> ours</span> <span class="lk"><i class="med"></i> median</span> <span class="lk"><i class="best"></i> best</span></div>
      ${lv.map(lever).join("") || '<div class="muted small">No lever data.</div>'}
      <div class="trends2"><div><h3>Model rating after each match <span class="muted small">pts/match</span></h3>${spark(tr, "blend_total")}${sparkCap(tr, "blend_total")}</div>
        <div><h3>Our counted FUEL <span class="muted small">tracked video only</span></h3>${spark(tr, "fuel_made")}${sparkCap(tr, "fuel_made")}</div></div>`;
  }
  const sparkCap = (pts, key) => { const v = pts.map(p => p[key]).filter(x => x != null); return v.length < 2 ? "" : `<div class="small muted">${n0(v[0], 1)} → <b>${n0(v[v.length - 1], 1)}</b> (min ${n0(Math.min(...v), 1)}, max ${n0(Math.max(...v), 1)})</div>`; };

  // ---------------------------------------------------------------- schedule
  function schedule(d) {
    const sc = d.schedule || [];
    const rpc = s => {
      if (s.result) return `<b>${s.result.rp ?? "–"}</b><span class="muted">–${s.result.rp_them ?? "–"}</span>`;
      if (s.bonus_probs) return `<span class="muted" title="expected RP us–them">${n0(s.bonus_probs.rp_mean, 1)}–${n0(s.bonus_probs_them?.rp_mean, 1)}</span>`;
      return "";
    };
    return `<div class="tablewrap"><table class="sched"><thead><tr><th>Match</th><th>Time</th><th>Side</th><th>With</th><th>Against</th><th class="num">Win%</th><th>Result</th><th class="num">RP</th><th>Video</th></tr></thead><tbody>
      ${sc.map(s => { const isNext = d.next_match && d.next_match.key === s.key; const us = s.alliance, op = us === "red" ? "blue" : "red";
        const pi = (d.plans || []).findIndex(p => p.key === s.key);
        return `<tr class="${isNext ? "nextrow" : ""}">
        <td>${pi >= 0 ? `<a href="#" data-plan="${pi}"><b>${H(s.short)}</b></a>` : `<a href="#/${S.ev}/m/${s.key}">${H(s.short)}</a>`}</td><td>${fmtT(s.time)}</td>
        <td><span class="adot ${s.alliance}" aria-hidden="true"></span> ${H(s.alliance || "")}</td>
        <td>${(s.partners || []).map(k => `<a href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(" ")}</td>
        <td>${(s.opponents || []).map(k => `<a href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(" ")}</td>
        <td class="num">${wp5(s.win_prob)}</td>
        <td>${s.result ? `<span class="res ${s.result.won ? "w" : s.result.tie ? "" : "l"}">${s.result.won ? "W" : s.result.tie ? "T" : "L"}</span> ${s.result[us]}–${s.result[op]}` : streamRes(s) ? (r => `<span class="res ${r.won ? "w" : r.tie ? "" : "l"}">${r.won ? "W" : r.tie ? "T" : "L"}</span> ${r[us]}–${r[op]}${CS.liveTag(r.row)}`)(streamRes(s)) : pi >= 0 ? `<a href="#" data-plan="${pi}" class="small">plan</a>` : '<span class="muted">–</span>'}</td>
        <td class="num">${rpc(s)}</td>
        <td class="small">${s.played && s.analyze_url ? `<a href="${s.analyze_url}" title="analyze the match video">Analyze</a>` : ""}</td></tr>`; }).join("")}
      </tbody></table></div>`;
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
    const th = c => `<th data-k="${c[0]}" class="${c[3]}${c[0] === key ? " sorted" : ""}" aria-sort="${c[0] === key ? (S.sort.dir > 0 ? "ascending" : "descending") : "none"}" tabindex="0">${c[1]}${c[0] === key ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
    const prbar = r => { const p = r.projection; if (!p) return ""; const L = v => ((v - 1) / Math.max(1, N - 1) * 100).toFixed(1);
      return `<div class="prb" title="mean ${n0(p.rank_mean, 1)}, 80% range ${p.rank_p10}-${p.rank_p90}"><i class="rg" style="left:${L(p.rank_p10)}%;width:${Math.max(1, L(p.rank_p90) - L(p.rank_p10))}%"></i><i class="mn" style="left:${L(p.rank_mean)}%"></i></div><span class="small">${p.rank_p10}–${p.rank_p90}</span>`; };
    const vcell = r => { const c = r.coverage; if (!c) return "–"; return `<span class="mcov ${c.tier}" title="verified robot video (v2 identity, team conf ≥ 0.6): ${H(c.label)}">${c.n ? `${c.n}${c.of ? "/" + c.of : ""}` : "none"}</span>${c.last ? ` <span class="muted small">last ${H(c.last)}</span>` : ""}`; };
    const dcell = r => { const x = r.disagree; if (!x || x.diff == null) return ""; return x.flag ? `<span class="mchipw" title="Model and EPA differ by ${n0(Math.abs(x.diff))} pts (more than 15 pts and 1.5 sd)">${x.diff > 0 ? "+" : "−"}${n0(Math.abs(x.diff))}</span>` : `<span class="muted small">${x.diff > 0 ? "+" : "−"}${n0(Math.abs(x.diff))}</span>`; };
    return `
      <div class="tablewrap"><table class="rate"><thead>
        <tr class="grp"><th colspan="6"></th><th colspan="3" class="num">Model by period</th><th colspan="3"></th></tr>
        <tr>${COLS.map(th).join("")}</tr></thead><tbody>
      ${sorted.map(r => `<tr class="${r.team === d.team ? "usrow" : ""}" data-t="${r.team}">
        <td class="num">${r.rank ?? "–"}</td><td class="tm"><a href="#/${S.ev}/t/${r.team}"><b>${tn(r.team)}</b> <span class="muted nick">${H(r.nickname)}</span></a></td><td class="num">${H(r.record)}</td>
        <td class="num bld">${n0(r.blend?.total)}<small class="muted"> ±${n0(r.blend?.sd)}</small></td><td class="num">${n0(r.epa?.total)}</td><td class="num">${dcell(r)}</td>
        <td class="num g2">${n0(r.blend?.auto)}</td><td class="num g2">${n0(r.blend?.teleop)}</td><td class="num g2">${n0(r.blend?.endgame)}</td>
        <td>${vcell(r)}</td><td><div class="prcell">${prbar(r)}</div></td><td class="num">${wp5(r.projection?.p_top8)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function ratingsHelp(d) {
    const rows = d.ratings || []; if (!rows.length) return "";
    const src = d.model?.epa_source?.display === "statbotics" ? "Statbotics EPA" : "EPA computed here from TBA results as of this time (Statbotics offline)";
    const vw = rows.map(r => r.video_weight || 0), vwm = vw.reduce((a, b) => a + b, 0) / Math.max(1, vw.length);
    return CS.help(`<b>Model</b> = each team's pre-event EPA updated by this event's TBA results and, where available, verified robot video (video is on average ${Math.round(vwm * 100)}% of the information). ± = 1 sd.
        <b>EPA</b> = ${H(src)}. <b>Δ</b> = Model − EPA; highlighted when they disagree by &gt;15 pts. <b>Video</b> = verified robot-video matches / matches played; dashed = fewer than 3 (treat as a hint).`, "How the ratings are made");
  }
  // model track record: one honest line; the method and caveats sit behind "?"
  function model(d) {
    const b = d.model?.backtest_asof;
    if (!b) return "";
    const f3 = v => v == null ? "–" : Number(v).toFixed(3);
    const asof = d.event?.asof ? fmtT(d.event.asof) : "now";
    if (!b.n || b.n < (b.min_n || 8)) return `<section class="dmodel"><b>Can you trust the win %?</b> Only ${b.n || 0} quals posted as of ${asof}: too few to grade the model yet, treat every number as a rough guide.</section>`;
    const br = b.brier || {}, dx = b.blend_minus_epa, vi = b.video_increment;
    const ci = x => x ? `90% interval ${f3(-x.ci90[1])} to ${f3(-x.ci90[0])}` : "";
    const sure = x => x && x.ci90[1] < 0 ? "better" : x && x.ci90[0] > 0 ? "worse" : "not distinguishable";
    const help = `<span class="hl">As of ${asof}: the ${b.n} quals already posted (through ${H(b.through)}), each predicted only from the matches before it.</span>
      <span class="hl">Brier score (0 = perfect, 0.250 = coin flip): Model ${f3(br.blend)} · EPA ${f3(br.epa)} · Model without video ${f3(br.no_video)}.</span>
      <span class="hl">Model vs EPA: ${f3(dx ? -dx.mean : null)} better (${ci(dx)}): ${sure(dx)}. Video's extra help: ${f3(vi ? -vi.mean : null)} (${ci(vi)}): ${sure(vi) === "better" ? "real" : "not yet distinguishable from zero"}.</span>
      ${b.auto_bias_recent ? `<span class="hl">Alliance auto FUEL over ${H(b.auto_bias_recent.from)}–${H(b.auto_bias_recent.to)}: actual minus predicted ${b.auto_bias_recent.mean > 0 ? "+" : ""}${n0(b.auto_bias_recent.mean, 1)} on average (${b.auto_bias_recent.n_alliances} alliances).</span>` : ""}
      <span class="hl">Not modelled: defense and congestion between robots, per-team fouls. Defense FUEL-denied numbers marked "assumption" are not measured.</span>`;
    return `<section class="dmodel"><b>Can you trust the win %?</b> Over ${b.n} posted quals the model picked the winner ${pct(b.accuracy?.blend)} of the time (EPA ${pct(b.accuracy?.epa)}); Brier ${f3(br.blend)} vs ${f3(br.epa)}, ${sure(dx) === "better" ? "measurably better than EPA" : sure(dx) === "worse" ? "worse than EPA" : "not distinguishable from EPA"}. ${CS.help(help, "How the win % is graded")}</section>`;
  }

  // ---------------------------------------------------------------- page
  function disclosure(id, title, sum, body, help) {
    if (!body) return "";
    return `<details class="dsec" id="sec-${id}" data-sec="${id}"${OPEN(id) ? " open" : ""}><summary><span class="ds-t">${title}</span>${help || ""}<span class="ds-s">${sum}</span><span class="ds-c" aria-hidden="true"></span></summary><div class="ds-b" id="body-${id}">${body}</div></details>`;
  }
  function render(d) {
    const R = Object.fromEntries((d.ratings || []).map(r => [r.team, r]));
    const sc = d.schedule || [], up = sc.filter(s => !s.played).length, lv = (d.our_team?.levers || []).filter(l => l.gap_rank != null);
    const top = [...lv].sort((a, b) => a.gap_rank - b.gap_rank)[0];
    $app.innerHTML = `<div class="dash db${d.event?.asof ? " isreplay" : ""}">
      <h1 class="vh">Strategy for ${tn(d.team)}, ${H(d.event?.name || S.ev)}</h1>
      ${!d.event?.asof && S.lsrec && CS.liveRecorder ? CS.liveRecorder.line(S.lsrec, S.ev) : ""}
      ${rail(d)}
      <div class="db-grid"><div class="db-main">${plansSection(d, R)}</div><div class="db-side">${ourEvent(d, R)}</div></div>
      <div class="dsecs">
      ${disclosure("sched", "Schedule and results", `${sc.length - up} played · ${up} to play`, sc.length ? schedule(d) : "", CS.help("Scores and RP are us–them. Win% = model win probability before the match (5% steps).", "How to read the schedule"))}
      ${disclosure("levers", "Levers", top ? `pit material · biggest gap: ${H(LAB[top.name] || top.name)}` : "pit material", d.our_team ? levers(d) : "", CS.help("Biggest gap to the event best first. n≈ = verified matches behind our value. On each track: the filled dot is ours, the thin tick the event median, the square the event best.", "How to read the levers"))}
      ${disclosure("ratings", "Ratings", `${(d.ratings || []).length} teams · points per match · model and EPA side by side`, `<div id="rt">${ratings(d)}</div>`, ratingsHelp(d))}
      </div>
      ${model(d)}</div>`;
    startCountdown(d); wirePlans(d, R); wireSecs();
    const rt = () => { document.getElementById("rt").innerHTML = ratings(d); wireSort(rt); };
    wireSort(rt);
    const mb = document.querySelector(".rl-mbtn");
    if (mb) mb.onclick = () => { const r = mb.closest(".rail"), all = r.classList.toggle("all"); mb.setAttribute("aria-expanded", all); mb.textContent = all ? "Show fewer" : `+${r.querySelectorAll("li.old").length} earlier`; };
  }
  function wireSecs() {
    document.querySelectorAll("details.dsec").forEach(x => x.ontoggle = () => SETOPEN(x.dataset.sec, x.open));
    document.querySelectorAll("[data-open]").forEach(b => b.onclick = () => { const x = document.getElementById("sec-" + b.dataset.open); if (x) { x.open = true; x.scrollIntoView({ block: "start" }); } });
  }
  function wirePlans(d, R) {
    const show = (i, focus) => { S.tab = i;
      const p = (d.plans || [])[i];
      if (p) try { history.replaceState(null, "", `#/${S.ev}/dash/${S.ts || "live"}/${(p.short || i).toString().toLowerCase()}`); } catch (e) { }
      const el = document.getElementById("plans"); if (!el) return; el.outerHTML = plansSection(d, R);
      document.querySelectorAll(".rail .rl-m.upc").forEach(b => { const on = +b.dataset.i === i; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
      if (focus) document.getElementById("rt-" + i)?.focus();
      wirePlans(d, R); startCountdown(d); };
    const tabs = [...document.querySelectorAll(".rail .rl-m.upc")];
    tabs.forEach(b => { b.onclick = () => show(+b.dataset.i);
      b.onkeydown = e => { const k = tabs.indexOf(b), n = e.key === "ArrowRight" ? k + 1 : e.key === "ArrowLeft" ? k - 1 : null; if (n != null && tabs[n]) { e.preventDefault(); show(+tabs[n].dataset.i, true); } }; });
    document.querySelectorAll("a[data-plan]").forEach(a => a.onclick = e => { e.preventDefault(); show(+a.dataset.plan); document.querySelector(".rail")?.scrollIntoView({ block: "start" }); });
  }
  function wireSort(rt) {
    document.querySelectorAll("table.rate th[data-k]").forEach(th => { const go = () => {
      const k = th.dataset.k; S.sort = S.sort.k === k ? { k, dir: -S.sort.dir } : { k, dir: ["rank", "team", "pr"].includes(k) ? 1 : -1 }; rt(); };
      th.onclick = go; th.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }; });
  }

  function injectStyle() {
    if (document.getElementById("dash-m-style")) return;
    const st = document.createElement("style"); st.id = "dash-m-style";
    st.textContent = `
/* ---- rail: our matches, played -> now -> plans (the screen's signature). Wraps; never scrolls sideways. */
.db { gap: 16px; }
.rail { display: grid; gap: 6px; }
.rl-h { display: flex; gap: 10px; align-items: baseline; font-size: var(--fs-foot); font-weight: 650; }
.rl { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: stretch; }
.rl > li { display: flex; }
.rl-p { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: var(--r-p); border: 1px solid var(--line); border-top: 3px solid var(--line-strong);
  color: var(--ink); text-decoration: none; font-size: var(--fs-foot); min-height: 36px; align-self: center; }
.rl-p.red { border-top-color: var(--red-fill); } .rl-p.blue { border-top-color: var(--blue-fill); }
.rl-p:hover { background: var(--surface); border-color: var(--ink-3); }
.rl-p .rl-k { font-weight: 650; } .rl-p .rl-sc { display: none; }
.rl-p.rl-live .rl-sc { display: inline; font-variant-numeric: tabular-nums; } .rl-p .livetag { margin-left: 0; }
.db > .lsline { margin-bottom: -6px; }
.rl-m { display: flex; flex-direction: column; justify-content: space-between; gap: 4px; min-width: 64px; min-height: 52px; padding: 6px 10px 8px; border-radius: var(--r-p);
  border: 1px solid var(--line); border-top: 3px solid var(--line-strong); background: var(--surface); color: var(--ink); text-decoration: none; font: inherit; text-align: left; cursor: pointer; }
.rl-m.red { border-top-color: var(--red-fill); } .rl-m.blue { border-top-color: var(--blue-fill); }
.rl-m.later { cursor: default; color: var(--ink-2); background: transparent; border-style: dashed; border-top-style: solid; }
.rl-k { font-size: var(--fs-foot); font-weight: 650; display: flex; gap: 6px; align-items: baseline; } .rl-k small { font-weight: 400; color: var(--ink-2); font-size: var(--fs-cap); }
.rl-r { display: flex; gap: 6px; align-items: center; font-size: var(--fs-cap); color: var(--ink-2); } .rl-r b { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 18px; font-weight: 700; line-height: 1; color: var(--ink); font-variant-numeric: tabular-nums; }
.rl-m.upc { min-width: 92px; transition: background-color .15s var(--ease), border-color .15s var(--ease); } .rl-m.upc:hover { border-color: var(--ink-3); }
.rl-m.upc.on { background: var(--accent-tint); color: var(--ink); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); } .rl-m.upc.on .rl-k small, .rl-m.upc.on .rl-r { color: var(--ink-2); } .rl-m.upc.on .rl-r b { color: var(--ink); }
.rl-m.upc.on.red { border-top-color: var(--red-fill); } .rl-m.upc.on.blue { border-top-color: var(--blue-fill); }
.rl-now { align-self: stretch; display: flex; align-items: stretch; padding: 0 4px; }
.rl-now span { border-left: 2px solid var(--accent); padding-left: 8px; display: flex; flex-direction: column; justify-content: center; font-size: var(--fs-cap); font-weight: 600; color: var(--ink-2); }
.rl-now b { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: var(--fs-callout); font-weight: 700; color: var(--ink); letter-spacing: 0; font-variant-numeric: tabular-nums; }
.isreplay .rl-now span { border-color: var(--caution); color: var(--caution); }
.rpg { font-size: 11px; font-weight: 700; color: var(--caution); border: 1px solid var(--caution-line); border-radius: var(--r-tag); padding: 0 4px; line-height: 15px; }
.rl-m.upc.on .rpg { background: var(--surface); }
.rl > li.rl-more { display: none; }
.rl-mbtn { min-height: 36px; align-self: center; padding: 4px 10px; font-size: var(--fs-foot); color: var(--ink-2); background: transparent; border-style: dashed; }
@media (max-width: 760px) {
  .rail:not(.all) .rl > li.old { display: none; }
  .rl > li.rl-more { display: flex; }
}

/* ---- layout */
.db-grid { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: var(--sp-4); align-items: start; }
.db-side { position: sticky; top: calc(var(--tb-h) + 16px); }
@media (max-width: 1100px) { .db-grid { grid-template-columns: minmax(0, 1fr); } .db-side { position: static; } }

/* ---- queue card */
.qcard { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-m); padding: var(--sp-5) var(--sp-5) var(--sp-4); }
.qcard.empty-q p { margin: 6px 0; font-size: var(--fs-callout); }
.qhead { display: flex; align-items: flex-start; gap: var(--sp-4); flex-wrap: wrap; margin-bottom: var(--sp-4); }
.qid { flex: 1; min-width: 220px; } .qid h2 { display: inline; font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 32px; line-height: 1.05; font-weight: 700; letter-spacing: -.01em; margin: 0; color: var(--ink); }
.qmeta { color: var(--ink-2); font-size: var(--fs-foot); margin-top: 2px; }
.weare { font-size: var(--fs-cap); font-weight: 700; padding: 2px 8px; border-radius: var(--r-tag); vertical-align: 6px; margin-left: var(--sp-2); color: #fff; }
.weare.red { background: var(--red-fill); } .weare.blue { background: var(--blue-fill); }
.cd { text-align: right; } .cd > span { display: block; font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 40px; font-weight: 700; line-height: 1; letter-spacing: .01em; font-variant-numeric: tabular-nums; }
.cd small { display: block; font-size: var(--fs-cap); color: var(--ink-2); margin-top: 4px; }
.cd .soon { color: var(--caution); } .cd .late { color: var(--bad); } .cd.later > span { font-size: 22px; color: var(--ink-2); font-family: var(--font); }
.qteams { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-2); }
.aline { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: var(--sp-2); border-radius: var(--r-p); }
.aline.red { background: var(--red-tint); } .aline.blue { background: var(--blue-tint); }
.atag { font-size: var(--fs-foot); font-weight: 600; width: 44px; padding-left: 4px; color: var(--ink-2); }
.dchip { display: flex; align-items: center; gap: 6px; padding: 4px 8px; background: var(--surface); border: 1px solid transparent; border-radius: 6px; color: var(--ink); text-decoration: none; min-height: 32px; }
.dchip:hover { border-color: var(--ink-3); } .dchip.us { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
.dchip b { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; } .rt { font-size: var(--fs-cap); color: var(--ink-2); } .rt small { font-size: var(--fs-cap); }
.ustag { font-size: 11px; font-weight: 700; color: var(--accent); }
.mcov { font-size: 11px; font-weight: 600; padding: 0 5px; border-radius: var(--r-tag); color: var(--ink-2); border: 1px solid var(--line-strong); white-space: nowrap; line-height: 17px; }
.mcov.fact { color: var(--ink); } .mcov.grey { border-style: dashed; } .mcov.none { color: var(--ink-3); border-color: var(--line); }
.qwin { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--sp-5); align-items: center; margin: var(--sp-5) 0 var(--sp-2); }
.qbig { display: flex; flex-direction: column; } .qb-n { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 64px; font-weight: 700; line-height: .9; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }
.qb-l { font-size: var(--fs-foot); color: var(--ink-2); margin-top: var(--sp-2); font-weight: 500; }
.qwin.goal .qb-n { font-size: 36px; letter-spacing: 0; }
.qsub { display: flex; flex-direction: column; gap: 6px; font-size: var(--fs-foot); } .qs-main { font-size: var(--fs-callout); }
.rng { color: var(--ink-2); font-size: var(--fs-cap); }
.wbar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; max-width: 420px; gap: 2px; } .wbar i { display: block; } .wbar .red { background: var(--red-fill); } .wbar .blue { background: var(--blue-fill); }
.mchipw { display: inline-block; background: var(--caution-tint); color: var(--caution); border: 1px solid var(--caution-line); border-radius: var(--r-tag); padding: 0 8px; font-size: var(--fs-cap); font-weight: 700; line-height: 18px; white-space: nowrap; }
.qlines { list-style: none; margin: var(--sp-2) 0 0; padding: 0; border-top: 1px solid var(--line); }
.ql { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: var(--sp-3); padding: var(--sp-3) 0; border-bottom: 1px solid var(--line); }
.ql-tag { font-size: var(--fs-foot); font-weight: 600; color: var(--ink-2); padding-top: 2px; }
.ql.warn .ql-tag { color: var(--caution); }
.ql-main { font-size: var(--fs-callout); line-height: 1.45; }
.ql-ev { font-size: var(--fs-foot); color: var(--ink-2); margin-top: 4px; line-height: 1.5; } .ql-ev b { color: var(--ink); font-weight: 600; }
.ql-where { margin-top: 2px; }
.ql.hasfield .ql-body { display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "main field" "ev field"; grid-template-rows: auto 1fr; column-gap: 16px; align-items: start; }
.ql.hasfield .ql-main { grid-area: main; } .ql.hasfield .ql-ev { grid-area: ev; } .ql-fieldwrap { grid-area: field; }
em.sh { font-style: normal; font-weight: 700; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-tag); padding: 0 6px; white-space: nowrap; }
.swap { color: var(--ink); margin-bottom: 2px; } .warnt { color: var(--caution); margin-top: 2px; }
.goaln { color: var(--caution); font-weight: 650; margin-bottom: 2px; }
a.tgt { font-weight: 750; text-decoration-color: currentColor; } a.tgt.red { color: var(--red); } a.tgt.blue { color: var(--blue); }
.basis { font-size: 11px; padding: 0 5px; border-radius: var(--r-tag); border: 1px dashed var(--ink-2); white-space: nowrap; } .basis.measured { border-style: solid; color: var(--ink); }
.mfield { width: 76px; height: auto; display: block; }
.mf-bg { fill: var(--surface-2); stroke: var(--line-strong); } .mf-red { fill: var(--red-fill); fill-opacity: .2; } .mf-blue { fill: var(--blue-fill); fill-opacity: .2; }
.mf-mid { stroke: var(--line-strong); stroke-dasharray: 3 3; } .mf-t { fill: var(--ink-2); font-size: 10px; font-weight: 600; text-anchor: middle; letter-spacing: .04em; }
.mf-tgt { fill-opacity: .22; stroke-width: 2; } .mf-tgt.red { fill: var(--red-fill); stroke: var(--red); } .mf-tgt.blue { fill: var(--blue-fill); stroke: var(--blue); }
.mf-dot.red { fill: var(--red); } .mf-dot.blue { fill: var(--blue); }
.rpt { width: auto; font-size: var(--fs-body); } .rpt th, .rpt td { border: 0; padding: 1px 14px 1px 0; background: none !important; position: static; cursor: default; }
.rpt tbody { display: block; } .rpt tr { display: flex; flex-wrap: wrap; padding: 1px 0; } .rpt td, .rpt th { display: block; white-space: nowrap; }
.rpt th { color: var(--ink); font-size: var(--fs-body); width: 48px; } .rpt tr.them, .rpt tr.them th { color: var(--ink-2); font-weight: 500; }
.qdetails { margin-top: 10px; } .qdetails summary { cursor: pointer; color: var(--ink); font-size: var(--fs-foot); font-weight: 600; padding: 6px 0; }
.plan { display: grid; grid-template-columns: 1.4fr 1.1fr 1fr; gap: 18px; margin-top: 8px; padding-top: 4px; border-top: 1px solid var(--line); }
.plan > * { min-width: 0; } .plan .mini { width: 100%; }
.drow { display: flex; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--line); align-items: flex-start; }
.dn { width: 20px; height: 20px; border-radius: 50%; background: var(--surface-3); text-align: center; line-height: 20px; font-size: var(--fs-cap); font-weight: 700; flex: none; }
.dbody { flex: 1; min-width: 0; } .dgain { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: var(--fs-t2); font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; } .dgain small { display: block; font-size: 11px; color: var(--ink-2); font-weight: 400; }
.shifts em { font-style: normal; font-size: var(--fs-cap); background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-tag); padding: 0 5px; margin-left: 4px; }
.ahts { font-size: var(--fs-foot); font-variant-numeric: tabular-nums; } .aht { padding: 3px 0; border-bottom: 1px solid var(--line); } .aht span { margin-right: 8px; white-space: nowrap; }
.kf { padding: 5px 0; border-bottom: 1px solid var(--line); font-size: var(--fs-foot); }

/* ---- our event (side) */
.side h2 { font-size: var(--fs-callout); margin-bottom: 10px; }
.oe { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 12px; margin: 0 0 12px; }
.oe div { min-width: 0; } .oe dt { font-size: var(--fs-cap); color: var(--ink-2); } .oe dd { margin: 0; font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 26px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.oe dd small { font-size: var(--fs-cap); font-weight: 400; color: var(--ink-2); }
.oe-row { font-size: var(--fs-foot); padding: 8px 0; border-top: 1px solid var(--line); }
.oe-spark { padding: 2px 0 8px; color: var(--ink); } .oe-spark .spark { height: 40px; }
.oe-lever { font-size: var(--fs-foot); padding-top: 8px; border-top: 1px solid var(--line); display: grid; gap: 2px; }
.linkbtn { justify-self: start; background: none; border: 0; padding: 4px 0; min-height: 28px; color: var(--ink); font-weight: 600; text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 2px; }
.linkbtn:hover { text-decoration-color: currentColor; }

/* ---- secondary sections */
.dsecs { display: flex; flex-direction: column; gap: 10px; }
.dsec { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-m); scroll-margin-top: calc(var(--tb-h) + 12px); }
.dsec > summary { list-style: none; display: flex; align-items: baseline; gap: 12px; padding: 14px 16px; cursor: pointer; min-height: 48px; flex-wrap: wrap; }
.dsec > summary::-webkit-details-marker { display: none; }
.ds-t { font-size: var(--fs-callout); font-weight: 600; } .ds-s { color: var(--ink-2); font-size: var(--fs-foot); flex: 1; }
.ds-c { width: 9px; height: 9px; border-right: 2px solid var(--ink-2); border-bottom: 2px solid var(--ink-2); transform: rotate(45deg); align-self: center; margin-right: 4px; }
.dsec[open] .ds-c { transform: rotate(-135deg); }
.ds-b { padding: 0 16px 16px; }
.lev { padding: 7px 0; } .lh { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: var(--fs-foot); } .lh .gap { margin-left: auto; color: var(--ink); font-weight: 600; font-size: var(--fs-cap); }
.lv-ours { font-weight: 700; }
.gr { width: 20px; height: 20px; line-height: 20px; text-align: center; border-radius: 50%; background: var(--surface-2); font-size: 11px; font-weight: 700; }
.ltrack { position: relative; height: 6px; background: var(--surface-3); border-radius: 3px; margin: 8px 0 2px; }
.ltrack i, .lk i { position: absolute; top: -4px; width: 2px; height: 14px; margin-left: -1px; border-radius: 1px; }
.ltrack .med, .lk .med { background: var(--ink-3); } .ltrack .best, .lk .best { background: transparent; border: 2px solid var(--ink); width: 8px; margin-left: -4px; border-radius: 2px; }
.ltrack .we, .lk .we { width: 12px; height: 12px; top: -3px; border-radius: 50%; margin-left: -6px; background: var(--accent-fill); box-shadow: 0 0 0 2px var(--surface); }
.lv-legend { margin-bottom: 4px; } .lk { position: relative; display: inline-block; padding-left: 16px; margin-left: 8px; } .lk i { left: 6px; top: 1px; }
.trends2 { margin-top: 14px; color: var(--ink); } .trends2 .spark { width: 100%; height: 48px; background: var(--surface-2); border-radius: 4px; }
.sched td { padding: 6px 8px; } .rlegend { margin: 0 0 10px; line-height: 1.55; }
.dmodel { padding: 10px 14px; background: transparent; border: 1px dashed var(--line-strong); border-radius: var(--r-m); font-size: var(--fs-foot); line-height: 1.6; color: var(--ink-2); }
.dmodel b { color: var(--ink); }

@media (max-width: 760px) {
  .dchip .rt { display: none; } .qhead { margin-bottom: var(--sp-3); } .qid h2 { font-size: 28px; }
  .qcard { padding: var(--sp-4) var(--sp-4) var(--sp-3); } .qid { min-width: 0; flex-basis: 100%; } .cd { text-align: left; } .cd > span { font-size: 32px; } .qb-n { font-size: 56px; }
  .qteams { grid-template-columns: 1fr; } .qwin { grid-template-columns: 1fr; gap: 10px; }
  .ql { grid-template-columns: 1fr; gap: 2px; } .ql.hasfield .ql-body { grid-template-columns: minmax(0, 1fr) 74px; } .mfield { width: 74px; }
  .plan { grid-template-columns: 1fr; } .oe dd { font-size: 20px; }
}`;
    document.head.appendChild(st);
  }

  window.dashPage = async function (ev, ts) {
    if (ts === "live") ts = null;          // #/<ev>/dash/live/<plan> = live view with a plan tab open
    S = { ev, ts, tab: 0, sort: S && S.ev === ev ? S.sort : { k: "bt", dir: -1 } };
    injectStyle();
    const liveP = ts ? Promise.resolve(null) : api(`${ev}/event.json`).catch(() => null);
    // recorder status in parallel with the page data (its only fallback is event.json, which api() shares)
    const lsP = !ts && CS.liveRecorder ? CS.liveRecorder.load(ev).catch(() => null) : Promise.resolve(null);
    const [d, evj] = await Promise.all([load(ev, ts), liveP]);
    if (d && d.team) US = d.team;
    if (evj) {
      S.live = Object.fromEntries((evj.matches || []).filter(m => CS.isStream(m)).map(m => [m.key, m]));
      // wait briefly for the recorder line (instant on the LAN); over a slow link render first and add it when it lands
      if (CS.liveRecorder) {
        const late = {}, st = await Promise.race([lsP, new Promise(r => setTimeout(() => r(late), 250))]);
        if (st !== late) S.lsrec = st;
        else { const me = S; lsP.then(x => { if (S !== me || !x) return; S.lsrec = x;
          const db = document.querySelector(".db"), h = db && db.querySelector(":scope > h1.vh");
          if (h && !db.querySelector(":scope > .lsline") && !d.event?.asof) h.insertAdjacentHTML("afterend", CS.liveRecorder.line(x, ev)); }); }
      }
    }
    // linkable plan tabs: #/<ev>/dash/<ts|live>/<q51 | plan index>
    const want = (location.hash.split("/")[4] || "").toLowerCase(), ps = d.plans || [];
    const byKey = ps.findIndex(p => (p.short || "").toLowerCase() === want);
    S.tab = byKey >= 0 ? byKey : (/^\d+$/.test(want) && +want < ps.length ? +want : 0);
    if (byKey < 0 && !/^\d+$/.test(want) && S.live) { const i = ps.findIndex(p => !S.live[p.key]); if (i > 0) S.tab = i; }   // a plan whose match the stream already scored is history
    CS.markActive && CS.markActive("dash");
    crumbs([[d.event?.name || ev, `#/${ev}`], ["Dashboard"]]);
    $updated.textContent = `Strategy for ${tn(d.team)} · ${d.event?.asof ? "replay at " : ""}${fmtT(d.event?.now)} · ${d.event?.quals_played ?? "?"}/${d.event?.quals_total ?? "?"} quals played`;
    render(d);
    if (!ts) {   // live: refresh every 60s without losing scroll
      const iv = setInterval(async () => { try { const n = await load(ev, null); if (JSON.stringify(n.event) !== JSON.stringify(d.event) || n.next_match?.key !== d.next_match?.key) return route(false);
        const e2 = evj && await api(`${ev}/event.json`); if (e2 && e2.updated !== evj.updated) return route(false);
        if (CS.liveRecorder && S.lsrec) { const st = await CS.liveRecorder.load(ev, e2); const el = document.querySelector(".db > .lsline"); if (el && st) el.outerHTML = CS.liveRecorder.line(st, ev) || "<span hidden></span>"; } } catch (e) { } }, 60000);
      cleanup.push(() => clearInterval(iv));
    }
  };
})();
