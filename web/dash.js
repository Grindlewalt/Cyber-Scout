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
  function vbadge(r) {
    if (!r) return "";
    const v = r.video_weight || 0;
    return `<span class="vbadge" title="${Math.round(v * 100)}% of the blended rating comes from video data (${r.video_matches ?? 0} matches); 0% = pure EPA"><i style="width:${Math.round(v * 100)}%"></i><b>${Math.round(v * 100)}</b></span>`;
  }
  function chip(k, R) {
    const r = R[k];
    return `<a class="dchip${k === US ? " us" : ""}" href="#/${S.ev}/t/${k}">
      <b>${tn(k)}</b><span class="rt">${r ? n0(r.blend?.total) : "–"}<small>±${r ? n0(r.blend?.sd) : "–"}</small></span>
      ${vbadge(r)}${r ? idPill(r.id_conf) : ""}</a>`;
  }
  const hbar = (p, a, b) => `<div class="wbar"><i class="r" style="width:${p * 100}%"></i><i class="b" style="width:${(1 - p) * 100}%"></i></div>`;
  const rng = s => s ? `<b>${n0(s.mean)}</b> <small>${n0(s.lo)}–${n0(s.hi)}</small>` : "–";

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

  // ---------------------------------------------------------------- hero
  function hero(d, R) {
    const m = d.next_match;
    if (!m) return `<section class="card dhero"><h2>Next match</h2><div class="empty">No unplayed matches for ${tn(d.team)} at this point.</div></section>`;
    const p = m.prediction || {}, pl = m.plan || {};
    const al = m.alliance, opp = al === "red" ? "blue" : "red";
    const wp = p.win_prob, redP = al === "red" ? wp : 1 - wp;
    const sc = p.score || {};
    const defs = (pl.defend || []).map((x, i) => `<div class="drow">
        <div class="dn">${i + 1}</div><div class="dbody"><div><a href="#/${S.ev}/t/${x.target}"><b>Defend ${tn(x.target)}</b></a>
        <span class="shifts">${(x.when || []).map(w => `<em>${SH(w)}</em>`).join("")}</span>
        ${x.where ? `<span class="muted small">@ ${H(x.where.zone || "")}${x.where.xy ? ` (${n0(x.where.xy[0], 1)}, ${n0(x.where.xy[1], 1)})` : ""}</span>` : ""}</div>
        <div class="small muted">${H(x.why)}</div></div><div class="dgain">-${n0(x.expected_gain_fuel, 1)}<small>fuel</small></div></div>`).join("");
    const au = pl.auto;
    const matchups = (au?.matchups || []).map(x => `<div class="small"><b>${tn(x.team)}</b> ${H(x.likely_routine)} <span class="muted">(${pct(x.p)})</span> ${x.note ? `<span class="muted">- ${H(x.note)}</span>` : ""}</div>`).join("");
    const eg = (pl.endgame || []).map(x => { const c = x.p_climb || {};
      return `<tr class="${x.team === US ? "usrow" : ""}"><td><a href="#/${S.ev}/t/${x.team}">${tn(x.team)}</a></td>${["L1", "L2", "L3"].map(l => `<td class="num">${pct(c[l])}</td>`).join("")}<td class="num">${pct(((c.L1 || 0) + (c.L2 || 0) + (c.L3 || 0)))}</td></tr>`; }).join("");
    const kf = (pl.key_factors || []).map(f => `<div class="kf"><span class="kw ${f.weight >= 0 ? "pos" : "neg"}">${f.weight >= 0 ? "+" : ""}${n0(f.weight * 100)}</span><span>${H(f.text)}</span><span class="muted small">${H(f.kind)}</span></div>`).join("");
    return `<section class="card dhero ${al}">
      <div class="hhead">
        <div><div class="hl">${H(m.label)}</div><div class="muted small">${fmtT(m.time)} · ${H(m.key)}</div></div>
        <div class="cd"><span id="cd">–</span><small id="cdsub"></small></div>
        <div class="hlinks">${m.preview_url ? `<a class="dbtn" href="#/${S.ev}/m/${m.key}">Full preview</a>` : ""}</div>
      </div>
      <div class="hgrid">
        <div class="halls">
          <div class="aline ${al}"><span class="atag">${al.toUpperCase()} (us)</span>${[...m.partners.slice(0, 1), US, ...m.partners.slice(1)].map(k => chip(k, R)).join("")}</div>
          <div class="aline ${opp}"><span class="atag">${opp.toUpperCase()}</span>${(m.opponents || []).map(k => chip(k, R)).join("")}</div>
        </div>
        <div class="hwin">
          <div class="bigp ${wp >= .5 ? "pos" : "neg"}">${pct(wp)}<small>win probability</small></div>
          ${hbar(redP)}
          <div class="scores"><span class="red">${al === "red" ? "Us" : "Opp"} ${rng(sc.red)}</span><span class="blue">${rng(sc.blue)} ${al === "blue" ? "Us" : "Opp"}</span></div>
          <div class="autob"><div class="small muted">AUTO BATTLE · we win auto <b>${pct(p.p_win_auto)}</b></div>
            <div class="ab"><span>if won <b class="pos">${pct(p.win_prob_if_auto_won)}</b></span><span>if lost <b class="neg">${pct(p.win_prob_if_auto_lost)}</b></span>
            <span class="muted small">swing ${n0(((p.win_prob_if_auto_won ?? 0) - (p.win_prob_if_auto_lost ?? 0)) * 100)} pts</span></div></div>
        </div>
      </div>
      <div class="plan">
        <div><h3>Defense plan</h3>${defs || '<div class="muted small">No defensive targets recommended.</div>'}</div>
        <div><h3>Auto</h3>${au ? `<div class="small">Expected fuel: us <b>${n0(au.our_expected, 1)}</b> vs them <b>${n0(au.their_expected, 1)}</b></div>` : ""}${matchups || '<div class="muted small">No routine data.</div>'}
          <h3>Endgame climb odds</h3>${eg ? `<table class="mini"><thead><tr><th>Team</th><th class="num">L1</th><th class="num">L2</th><th class="num">L3</th><th class="num">any</th></tr></thead><tbody>${eg}</tbody></table>` : '<div class="muted small">–</div>'}</div>
        <div><h3>Key factors</h3>${kf || '<div class="muted small">–</div>'}</div>
      </div></section>`;
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
  const LAB = { cycle_s: "Cycle time (s)", accuracy: "Accuracy", fire_rate: "Fire rate (/s)", auto_fuel: "Auto fuel", climb: "Climb rate" };
  const PCT = new Set(["accuracy", "climb"]);
  function lever(l) {
    const low = LOW.has(l.name), f = v => PCT.has(l.name) ? pct(v) : n0(v, 1);
    const vals = [l.ours, l.event_best, l.event_median].filter(v => v != null), lo = Math.min(...vals) * 0.9, hi = Math.max(...vals) * 1.05;
    const x = v => ((v - lo) / (hi - lo) * 100).toFixed(1) + "%";
    const gap = l.event_best ? (low ? (l.ours - l.event_best) : (l.event_best - l.ours)) / l.event_best : 0;
    const ahead = low ? l.ours <= l.event_median : l.ours >= l.event_median;
    return `<div class="lev"><div class="lh"><span class="gr">${l.gap_rank ?? ""}</span><b>${H(LAB[l.name] || l.name)}</b>
      <span class="${ahead ? "pos" : "neg"}">${f(l.ours)}</span><span class="muted small">med ${f(l.event_median)} · best ${f(l.event_best)} <a href="#/${S.ev}/t/${l.event_best_team}">${tn(l.event_best_team)}</a></span>
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
    return `<section class="card"><h2>${tn(d.team)} levers <span class="muted small">biggest gap first</span></h2>${lv.map(lever).join("") || '<div class="muted small">No lever data.</div>'}
      <div class="trends"><div><h3>Blended rating</h3>${spark(o.trend || [], "blend_total")}</div><div><h3>Fuel made</h3>${spark(o.trend || [], "fuel_made", 260, 54, "#ffd43b")}</div></div></section>`;
  }

  // ---------------------------------------------------------------- schedule
  function schedule(d, R) {
    const sc = d.schedule || [];
    return `<section class="card"><h2>${tn(d.team)} schedule</h2><div class="tablewrap"><table class="sched"><thead><tr><th>Match</th><th>Time</th><th></th><th>With</th><th>Against</th><th class="num">Win%</th><th>Result</th><th></th></tr></thead><tbody>
      ${sc.map(s => { const isNext = d.next_match && d.next_match.key === s.key; return `<tr class="${isNext ? "nextrow" : ""}">
        <td><a href="#/${S.ev}/m/${s.key}">${H(s.label.replace("Qualification ", "Q"))}</a></td><td>${fmtT(s.time)}</td>
        <td><span class="adot ${s.alliance}"></span></td>
        <td>${(s.partners || []).map(k => `<a href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(" ")}</td>
        <td class="muted">${(s.opponents || []).map(k => `<a href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(" ")}</td>
        <td class="num">${pct(s.win_prob)}</td>
        <td>${s.result ? `<span class="res ${s.result.won ? "w" : "l"}">${s.result.won ? "W" : "L"}</span> ${s.result.red}-${s.result.blue}` : isNext ? '<span class="muted">next</span>' : '<span class="muted">–</span>'}</td>
        <td class="small">${s.played && s.analyze_url ? `<a href="${s.analyze_url}">Analyze</a>` : ""}</td></tr>`; }).join("")}
      </tbody></table></div></section>`;
  }

  // ---------------------------------------------------------------- ratings
  const COLS = [
    ["rank", "Rk", r => r.rank, "num"], ["team", "Team", r => +tn(r.team), ""], ["rec", "W-L", r => parseInt(r.record) || 0, "num"],
    ["et", "EPA", r => r.epa?.total, "num g1"], ["bt", "Blend", r => r.blend?.total, "num g1 bld"],
    ["ea", "EPA", r => r.epa?.auto, "num g2"], ["ba", "Blend", r => r.blend?.auto, "num g2 bld"],
    ["eo", "EPA", r => r.epa?.teleop, "num g1"], ["bo", "Blend", r => r.blend?.teleop, "num g1 bld"],
    ["ee", "EPA", r => r.epa?.endgame, "num g2"], ["be", "Blend", r => r.blend?.endgame, "num g2 bld"],
    ["vw", "Video", r => r.video_weight, ""], ["id", "ID", r => r.id_conf, "num"],
    ["pr", "Proj. rank", r => r.projection?.rank_mean, ""], ["t8", "Top8", r => r.projection?.p_top8, "num"]];
  function ratings(d) {
    const rows = d.ratings || []; if (!rows.length) return "";
    const N = rows.length;
    const key = S.sort.k, col = COLS.find(c => c[0] === key) || COLS[0];
    const sorted = [...rows].sort((a, b) => ((col[2](a) ?? -1e9) - (col[2](b) ?? -1e9)) * S.sort.dir);
    const th = c => `<th data-k="${c[0]}" class="${c[3]}${c[0] === key ? " sorted" : ""}">${c[1]}${c[0] === key ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
    const prbar = r => { const p = r.projection; if (!p) return ""; const L = v => ((v - 1) / Math.max(1, N - 1) * 100).toFixed(1);
      return `<div class="prb" title="mean ${n0(p.rank_mean, 1)}, p10 ${p.rank_p10}, p90 ${p.rank_p90}"><i class="rg" style="left:${L(p.rank_p10)}%;width:${Math.max(1, L(p.rank_p90) - L(p.rank_p10))}%"></i><i class="mn" style="left:${L(p.rank_mean)}%"></i></div><span class="small">${n0(p.rank_mean, 1)} <span class="muted">${p.rank_p10}-${p.rank_p90}</span></span>`; };
    return `<section class="card"><h2>Ratings <span class="muted small">EPA = public prior; Blend = EPA updated with Cyber-Scout video. Video bar = share of Blend from video. ± = 1 sd</span></h2>
      <div class="tablewrap"><table class="rate"><thead>
        <tr class="grp"><th colspan="3"></th><th colspan="2" class="num">Total</th><th colspan="2" class="num">Auto</th><th colspan="2" class="num">Teleop</th><th colspan="2" class="num">Endgame</th><th colspan="2"></th><th colspan="2">Projection</th></tr>
        <tr>${COLS.map(th).join("")}</tr></thead><tbody>
      ${sorted.map(r => `<tr class="${r.team === d.team ? "usrow" : ""}" data-t="${r.team}">
        <td class="num">${r.rank}</td><td class="tm"><a href="#/${S.ev}/t/${r.team}"><b>${tn(r.team)}</b> <span class="muted nick">${H(r.nickname)}</span></a></td><td class="num">${H(r.record)}</td>
        <td class="num g1">${n0(r.epa?.total)}</td><td class="num g1 bld">${n0(r.blend?.total)}<small class="muted"> ±${n0(r.blend?.sd)}</small></td>
        <td class="num g2">${n0(r.epa?.auto)}</td><td class="num g2 bld">${n0(r.blend?.auto)}</td>
        <td class="num g1">${n0(r.epa?.teleop)}</td><td class="num g1 bld">${n0(r.blend?.teleop)}</td>
        <td class="num g2">${n0(r.epa?.endgame)}</td><td class="num g2 bld">${n0(r.blend?.endgame)}</td>
        <td>${vbadge(r)}</td><td class="num">${idPill(r.id_conf)}</td><td><div class="prcell">${prbar(r)}</div></td><td class="num">${pct(r.projection?.p_top8)}</td></tr>`).join("")}
      </tbody></table></div></section>`;
  }

  function model(d) {
    const b = d.model?.backtest; if (!b) return "";
    const br = b.brier || {}, items = [["Cyber-Scout sim", br.sim_full], ["EPA", br.epa], ["OPR", br.opr]];
    const best = Math.min(...items.map(i => i[1]).filter(v => v != null));
    return `<section class="dmodel muted small"><b>Model credibility</b> · Backtest Brier score (lower is better; coin flip = 0.250) over ${b.n ?? "?"} matches:
      ${items.map(([n, v]) => `<span class="${v === best ? "pos" : ""}">${n} <b>${v == null ? "–" : Number(v).toFixed(3)}</b></span>`).join(" · ")}
      ${(d.model.notes || []).map(n => `<div>${H(n)}</div>`).join("")}</section>`;
  }

  // ---------------------------------------------------------------- page
  function render(d, idx) {
    const R = Object.fromEntries((d.ratings || []).map(r => [r.team, r]));
    $app.innerHTML = `<div class="dash${d.event?.asof ? " isreplay" : ""}">
      <div class="dtop"><div><h1>${H(d.event?.name || S.ev)}</h1><div class="sub">Strategy for <b>${tn(d.team)}</b> · ${fmtT(d.event?.now)}</div></div>
        <div class="evtabs"><a class="tablink on" href="#/${S.ev}/dash">Dashboard</a><a class="tablink" href="#/${S.ev}/teams">Teams</a><a class="tablink" href="#/${S.ev}/compare">Compare</a><a class="tablink" href="#/${S.ev}/picklist">Picklist</a><a class="tablink" href="#/${S.ev}/matches">Matches</a></div></div>
      ${replayBar(d, idx)}
      ${hero(d, R)}
      <div class="grid2 dmid">${ourTeam(d)}${schedule(d, R)}</div>
      <div id="rt">${ratings(d)}</div>
      ${model(d)}</div>`;
    wireReplay(idx); startCountdown(d);
    const rt = () => { document.getElementById("rt").innerHTML = ratings(d); wireSort(rt); };
    wireSort(rt);
  }
  function wireSort(rt) {
    document.querySelectorAll("table.rate th[data-k]").forEach(th => th.onclick = () => {
      const k = th.dataset.k; S.sort = S.sort.k === k ? { k, dir: -S.sort.dir } : { k, dir: ["rank", "team", "pr"].includes(k) ? 1 : -1 }; rt(); });
  }

  window.dashPage = async function (ev, ts) {
    S = { ev, sort: S && S.ev === ev ? S.sort : { k: "bt", dir: -1 } };
    let idx = []; try { idx = await api(`${ev}/dash/index.json`); } catch (e) { }
    const d = await load(ev, ts);
    crumbs([[d.event?.name || ev, `#/${ev}`], ["Dashboard"]]); $updated.textContent = d.event?.asof ? "replay" : "";
    render(d, idx);
    if (!ts) {   // live: refresh every 60s without losing scroll
      const iv = setInterval(async () => { try { const n = await load(ev, null); if (JSON.stringify(n.event) !== JSON.stringify(d.event) || n.next_match?.key !== d.next_match?.key) route(false); } catch (e) { } }, 60000);
      cleanup.push(() => clearInterval(iv));
    }
  };
})();
