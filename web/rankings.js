"use strict";
/* rankings.js: #/<ev>/rankings. Live rankings + RP, projected rank range, district points (now / projected / P(DCMP)) and the
 * season-goal panel (odds, points needed, cutoff projection, value of each remaining match).
 * Data: api/<ev>/rankings.json (contract: live/strategy/README.md, "District points, season goals ..."). Missing blocks hide. */
(function () {
  const H = s => esc(s);
  const n0 = (v, d = 0) => v == null ? "–" : Number(v).toFixed(d);
  // one display policy for simulated probabilities (Rankings and Selection): whole percent, "<1%" / ">99%" at the ends (a
  // simulation that never saw it isn't proof it can't happen)
  const P = v => v == null ? "–" : v >= 0.995 ? ">99%" : v < 0.005 ? "<1%" : Math.round(v * 100) + "%";
  const tn = k => (k || "").replace("frc", "");
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const fmtT = ts => { if (!ts) return ""; const d = new Date(ts * 1000); return DAYS[d.getDay()] + " " + d.toTimeString().slice(0, 5); };
  const fmtD = ts => { const d = new Date(ts * 1000); return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + ", " + d.toTimeString().slice(0, 5); };
  const sgn = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v * 100).toFixed(1) + " pts";
  let S = null;

  function css() {
    if (document.getElementById("rk-css")) return;
    const st = document.createElement("style"); st.id = "rk-css";
    st.textContent = `
.rk,.rk>*,.rk .card{min-width:0;max-width:100%}.rk .card{overflow:hidden}.rk .tablewrap{max-width:100%;overflow-x:auto}.rk .vrow>*,.rk .detbox>*{min-width:0}
.rk .dtop{margin-bottom:12px}
.rk .goalcard{display:grid;grid-template-columns:minmax(190px,240px) 1fr;gap:18px;align-items:start}
.rk .bigp{font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:52px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
.rk .bigp small{display:block;font-family:var(--font);font-stretch:100%;font-size:var(--fs-foot);font-weight:500;color:var(--ink-2);margin-top:var(--sp-2)}
.rk .gbar{position:relative;height:10px;background:var(--surface-3);border-radius:5px;margin:12px 0 4px;overflow:hidden}
.rk .gbar i{position:absolute;top:0;bottom:0;background:var(--good);opacity:.85}
.rk .gbar b{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--ink)}
.rk .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:var(--sp-4) var(--sp-5)}
.rk .tile{background:none;border:0;border-top:1px solid var(--line);border-radius:0;padding:var(--sp-2) 0 0}
.rk .tile .k{font-size:var(--fs-cap);color:var(--ink-2)}
.rk .tile .v{font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:var(--fs-t2);font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px}
.rk .tile .v small{font-size:12px;font-weight:400;color:var(--muted);margin-left:4px}
.rk h3{font-size:var(--fs-foot);color:var(--ink-2);margin:var(--sp-4) 0 var(--sp-2);font-weight:600}
.rk .vrow{display:grid;grid-template-columns:92px 1fr 230px;gap:10px;align-items:center;padding:4px 0;font-size:13px}
.rk .vbar{position:relative;height:14px;background:var(--surface-2);border-radius:3px}
.rk .vbar .mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
.rk .vbar i{position:absolute;top:2px;bottom:2px;border-radius:2px}
.rk .vbar i.up{background:var(--good)} .rk .vbar i.dn{background:var(--bad)}
.rk .evrow{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
.rk .evrow:last-child{border:0}
.rk .evrow .nm{min-width:130px}
.rk .tag{font-size:11px;padding:0 6px;border-radius:var(--r-tag);border:1px solid var(--line);color:var(--muted)}
.rk .tag.live{color:var(--good);border-color:currentColor} .rk .tag.future{color:var(--ink);border-style:dashed} .rk .tag.off{opacity:.55}
.rk .prb{position:relative;height:8px;width:90px;background:var(--surface-3);border-radius:4px;display:inline-block;vertical-align:middle;margin-right:6px}
.rk .prb .rg{position:absolute;top:1px;bottom:1px;background:var(--line-strong);border-radius:3px}
.rk .prb .mn{position:absolute;top:-1px;bottom:-1px;width:2px;background:var(--ink)}
.rk .pcell{display:inline-block;min-width:34px}
.rk .dp{position:relative;display:inline-block;width:70px;height:8px;background:var(--surface-3);border-radius:4px;vertical-align:middle;margin-right:5px}
.rk .dp i{position:absolute;top:1px;bottom:1px;background:color-mix(in srgb,var(--good) 55%,transparent);border-radius:3px} .rk .dp b{position:absolute;top:-1px;bottom:-1px;width:2px;background:var(--ink)}
.rk .note{font-size:var(--fs-foot);color:var(--ink-2);margin-top:var(--sp-3);line-height:1.5}
.rk th.sorted{color:var(--text)}
.rk .pitch-l{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:4px 0 6px}.rk .pitch-l dt{font-size:var(--fs-cap);color:var(--ink-2);font-weight:600;padding-top:2px}.rk .pitch-l dd{margin:0;font-weight:500}
.rk .pitch-d summary{cursor:pointer;font-size:var(--fs-cap);color:var(--ink-2);min-height:28px;display:flex;align-items:center;gap:6px;list-style:none}.rk .pitch-d summary::-webkit-details-marker{display:none}.rk .pitch-d summary::before{content:"";width:5px;height:5px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg)}.rk .pitch-d[open] summary::before{transform:rotate(45deg)}.rk .pitch-d[open] summary{margin-bottom:4px}.rk .pitch-d ul{margin:4px 0 0;padding-left:18px}
.rk .sel-empty{text-align:left;display:grid;gap:8px;max-width:70ch}
.rk th[tabindex]:focus-visible{outline-offset:-2px}
@media (max-width:900px){.rk .goalcard{grid-template-columns:1fr}.rk .nick{display:none}.rk .vrow{grid-template-columns:62px 1fr 150px}.rk .prb{width:60px}.rk .dp{width:50px}}
`;
    document.head.appendChild(st);
  }


  function goalPanel(d) {
    const g = d.goal; if (!g) return "";
    const p = g.p_goal, ci = g.p_goal_ci || [p, p], cut = g.cutoff || {}, cp = cut.projected || {}, pp = g.projected_points || {};
    const pn = g.points_needed || {};
    const col = p >= 0.75 ? "pos" : p <= 0.25 ? "neg" : "";
    const goalName = { dcmp: "District Championship", worlds: "World Championship" }[String(g.goal).toLowerCase()] || (String(g.goal).startsWith("rank") ? "District rank ≤ " + String(g.goal).split(":")[1] : g.goal);
    const scaleMax = Math.max(cp.p90 || 0, pp.p90 || 0, g.current_points || 0, 1) * 1.1;
    const X = v => (Math.min(v, scaleMax) / scaleMax * 100).toFixed(1) + "%";
    const bar = `<div class="gbar" title="your projected points (green range) vs projected cutoff (yellow)"><i style="left:${X(pp.p10 || 0)};width:${(parseFloat(X(pp.p90 || 0)) - parseFloat(X(pp.p10 || 0))).toFixed(1)}%"></i><b style="left:${X(cp.mean || 0)}"></b></div>`;
    const evs = (g.events || []).map(e => {
      const pt = e.points;
      const val = pt == null ? "–" : typeof pt === "number" ? `<b>${pt}</b> pts` : `<b>${n0(pt.mean)}</b> pts <small class="muted">${n0(pt.p10)}–${n0(pt.p90)}${pt.rank_mean ? " · rank ~" + n0(pt.rank_mean) : ""}</small>`;
      return `<div class="evrow"><span class="nm"><b>${H(e.name || e.event)}</b></span><span class="tag ${e.status === "done" ? "" : e.status}">${e.status === "done" ? "played" : e.status}</span>${e.counts ? "" : '<span class="tag off" title="only a team\'s first two district events count">not counted</span>'}<span>${val}</span></div>`;
    }).join("");
    const v = g.value || {};
    const maxd = Math.max(0.02, ...(v.matches || []).map(m => Math.abs(m.delta || 0)));
    const vm = (v.matches || []).map(m => {
      const w = Math.abs(m.delta || 0) / maxd * 50;
      return `<div class="vrow"><span><a href="#/${S.ev}/schedule/${m.key}">${H(m.label)}</a><div class="muted small">${fmtT(m.time)}</div></span>
        <div class="vbar"><span class="mid"></span><i class="${(m.delta || 0) >= 0 ? "up" : "dn"}" style="${(m.delta || 0) >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`}"></i></div>
        <span class="small"><b class="${(m.delta || 0) >= 0 ? "pos" : "neg"}">${sgn(m.delta)}</b> <span class="muted">win ${P(m.p_if_win)} · loss ${P(m.p_if_loss)}</span></span></div>`;
    }).join("");
    const vev = (v.events || []).map(e => {
      const evn = (g.events || []).find(x => x.event === e.event);
      const rows = (e.by_rank || []).map(r => `<tr><td>${H(r.outcome)}</td><td class="num">${P(r.share)}</td><td class="num"><b>${P(r.p_goal)}</b></td></tr>`).join("");
      const rows2 = (e.by_points || []).map(r => `<tr><td>${H(r.outcome)}</td><td class="num">${n0(r.points)} pts</td><td class="num"><b>${P(r.p_goal)}</b></td></tr>`).join("");
      return `<div class="tablewrap"><div class="small"><b>${H(evn?.name || e.event)}</b> ${CS.help("Goal odds given how this event goes: conditional, not causal.", "How to read these odds")}</div>
        <table class="mini"><thead><tr><th>Outcome</th><th class="num">Chance</th><th class="num">P(goal)</th></tr></thead><tbody>${rows}${rows2}</tbody></table></div>`;
    }).join("");
    return `<section class="card goalcard">
      <div><div class="muted small">Season goal: ${H(goalName)} · ${tn(g.team)} ${CS.help(`${g.dcmp ? `<span class="hl">DCMP capacity ${g.dcmp.capacity} (about ${n0(g.dcmp.points_slots_mean)} places left for points after award pre-qualifiers).</span>` : ""}<span class="hl">Model: ${H(g.model?.theta || "")}; ${g.model?.sims} simulated seasons, alliance score sd ${n0(g.model?.sigma_alliance)}. District points follow the game manual (first two events + DCMP).</span><span class="hl">Bar: green = your projected points (10–90%), yellow = projected cutoff.</span>`, "How the season goal is projected")}</div>
        <div class="bigp ${col}">${P(p)}<small>${g.awards ? H(g.awards.headline) + " · " : ""}90% range ${P(ci[0])}–${P(ci[1])}${g.p_dcmp != null ? ` · DCMP ${P(g.p_dcmp)}` : ""}${g.dcmp?.is_prequalified ? " · pre-qualified by award" : ""}</small></div>
        ${g.awards ? `<div class="small" style="margin-top:6px" title="${H(g.awards.note)}">${g.awards.contender ? `without awards ${P(g.awards.p_no_awards)}` : `+ random awards: <b>${P(g.awards.p_with_awards)}</b>`} · if we win Impact/EI: ${P(g.awards.p_if_impact_or_ei)}</div>` : ""}${bar}</div>
      <div>
        <div class="tiles">
          <div class="tile"><div class="k">Points now</div><div class="v">${n0(g.current_points)}</div></div>
          <div class="tile"><div class="k">Projected final</div><div class="v">${n0(pp.mean)}<small>${n0(pp.p10)}–${n0(pp.p90)}</small></div></div>
          <div class="tile"><div class="k">Projected cutoff</div><div class="v">${n0(cp.mean)}<small>${n0(cp.p10)}–${n0(cp.p90)}</small></div></div>
          <div class="tile"><div class="k">Cutoff today</div><div class="v">${n0(cut.now)}</div></div>
          <div class="tile"><div class="k">Still needed</div><div class="v">${n0(g.points_still_needed)}<small>pts for 50%</small></div></div>
          <div class="tile"><div class="k">For 80% / 95%</div><div class="v">${n0(pn.p80?.still_needed)}<small>/ ${n0(pn.p95?.still_needed)} pts</small></div></div>
        </div>
        <h3>Events</h3>${evs || '<div class="muted small">No registered events.</div>'}
        ${vm ? `<h3>Value of each remaining qual ${CS.help("Change in goal odds if we win vs lose that qual.", "What this value means")}</h3>${vm}` : ""}
        ${vev ? `<h3>What each event is worth</h3><div class="grid2">${vev}</div>` : ""}
      </div></section>`;
  }

  const COLS = [
    ["rank", "Rk", r => r.rank, "num"], ["team", "Team", r => r.number, ""], ["rec", "W-L-T", r => r.record ? parseInt(r.record) : null, "num"],
    ["rp", "RP", r => r.rp, "num"], ["rs", "RS", r => r.rs, "num"], ["pr", "Proj. rank", r => r.projection?.rank_mean, ""],
    ["t8", "Top 8", r => r.projection?.p_top8, "num"], ["dn", "Dist. pts", r => r.district?.current, "num"],
    ["dp", "Projected pts", r => r.district?.projected, ""], ["dc", "DCMP", r => r.district?.p_dcmp, "num"]];

  function table(d) {
    const rows = d.teams || [];
    // district columns only where there are district points (offseason / regional events have none: MATCH-36)
    const cols = rows.some(r => r.district) ? COLS : COLS.filter(c => !["dn", "dp", "dc"].includes(c[0]));
    const N = rows.length, key = S.sort.k, col = cols.find(c => c[0] === key) || cols[0];
    // once every qual is played, top 8 is decided: say so instead of a simulated percentage (MATCH-21)
    const T8 = v => d.quals_total && d.quals_played >= d.quals_total && (v === 0 || v === 1) ? (v ? "yes" : "no") : P(v);
    const sorted = [...rows].sort((a, b) => {
      const x = col[2](a), y = col[2](b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (x - y) * S.sort.dir;
    });
    const maxDp = Math.max(1, ...rows.map(r => r.district?.p90 || 0));
    const RPRI = { rank: 1000, team: 990, rs: 800, rp: 700, pr: 600, t8: 500, dc: 450, dn: 400, dp: 350, rec: 300 };
    // sortable headers are reachable by keyboard and say how the table is sorted (MATCH-27)
    const th = c => `<th data-k="${c[0]}" data-pri="${RPRI[c[0]] ?? 100}" class="${c[3]}${c[0] === key ? " sorted" : ""}" tabindex="0" aria-sort="${c[0] === key ? (S.sort.dir > 0 ? "ascending" : "descending") : "none"}">${c[1]}${c[0] === key ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
    const prbar = r => { const p = r.projection; if (!p) return ""; const L = v => ((v - 1) / Math.max(1, N - 1) * 100).toFixed(1);
      return `<span class="prb" title="mean ${n0(p.rank_mean, 1)}, 10–90%: ${p.rank_p10}–${p.rank_p90}"><i class="rg" style="left:${L(p.rank_p10)}%;width:${Math.max(2, L(p.rank_p90) - L(p.rank_p10))}%"></i><i class="mn" style="left:${L(p.rank_mean)}%"></i></span><span class="small">${n0(p.rank_mean, 1)} <span class="muted">${p.rank_p10}–${p.rank_p90}</span></span>`; };
    const dpbar = r => { const q = r.district; if (!q || q.projected == null) return ""; const X = v => (v / maxDp * 100).toFixed(1);
      return `<span class="dp" title="projected ${n0(q.projected)}, 10–90%: ${n0(q.p10)}–${n0(q.p90)}"><i style="left:${X(q.p10)}%;width:${Math.max(2, X(q.p90) - X(q.p10))}%"></i><b style="left:${X(q.projected)}%"></b></span><span class="small">${n0(q.projected)} <span class="muted">${n0(q.p10)}–${n0(q.p90)}</span></span>`; };
    return `<section class="card"><h2>Qualification rankings <span class="muted small">${d.quals_played}/${d.quals_total} quals played</span> ${CS.help(`<span class="hl">RS = RP per match. Projection = Monte Carlo of the remaining quals (mean and 10–90% range). Tap a header to sort.</span>${d.district ? `<span class="hl">Current district points count a team's first two district events (all points from finished events: qualification, alliance selection, playoffs, awards) plus rookie bonus. The playing event contributes once it finishes; its projection uses the simulated qualification, alliance selection, playoffs and awards.</span>` : ""}`, "How rankings are projected")}</h2>
      <div class="tablewrap"><table class="rate rkt"><thead><tr>${cols.map(th).join("")}</tr></thead><tbody>
      ${sorted.map(r => `<tr class="${r.team === d.team ? "usrow" : ""}">
        <td class="num">${r.rank ?? "–"}</td><td class="tm"><a href="#/${S.ev}/t/${r.team}"><b>${r.number}</b> <span class="muted nick">${H(r.nickname || "")}</span></a></td>
        <td class="num">${H(r.record || "–")}</td><td class="num">${n0(r.rp)}</td><td class="num">${n0(r.rs, 2)}</td>
        <td>${prbar(r)}</td><td class="num">${T8(r.projection?.p_top8)}</td>
        ${cols === COLS ? `<td class="num">${r.district ? n0(r.district.current) : "–"}</td><td>${dpbar(r)}</td>
        <td class="num">${r.district ? P(r.district.p_dcmp) : "–"}</td>` : ""}</tr>`).join("")}
      </tbody></table></div></section>`;
  }

  function wire(d) {
    document.querySelectorAll("table.rkt th[data-k]").forEach(th => {
      th.onclick = () => {
        const k = th.dataset.k; S.sort = S.sort.k === k ? { k, dir: -S.sort.dir } : { k, dir: ["rank", "team", "pr"].includes(k) ? 1 : -1 };
        document.getElementById("rk-table").innerHTML = table(d); wire(d);
        const f = document.querySelector(`table.rkt th[data-k="${k}"]`); if (f) f.focus();
      };
      th.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); th.onclick(); } };
    });
  }

  CS.route("rankings", async function (ev) {
    css();
    S = { ev, sort: S && S.ev === ev ? S.sort : { k: "rank", dir: 1 } };
    crumbs([[ev, `#/${ev}`], ["Rankings"]]);             // this page's title even if its data fails to load (MATCH-8)
    const [d, evj] = await Promise.all([api(`${ev}/rankings.json`), api(`${ev}/event.json`).catch(() => null)]);
    const name = evj && evj.name || ev, allQ = d.quals_total && d.quals_played >= d.quals_total;
    // asof: the worker built these with data up to that time (a finished event), not a replay of this page
    crumbs([[name, `#/${ev}`], ["Rankings"]]); $updated.textContent = d.asof ? "data as of " + fmtD(d.asof) : ago(d.generated);
    // "Latest data" while quals are still being played; it is only final once they're all in (MATCH-12)
    $app.innerHTML = `<div class="dash rk"><div class="dtop"><div><h1>Rankings &amp; season goal</h1><div class="sub">${H(name)} · us = ${tn(d.team)}${d.district ? " · district " + H(d.district) : ""}</div></div></div>
      ${`<div class="sc-banner"><b>${allQ ? "All quals played" : "Latest data"}</b>${d.asof ? ` as of ${fmtD(d.asof)}` : ""}, not replay time${d.quals_played != null ? ` · ${d.quals_played}/${d.quals_total} quals played` : ""}. Replay: pick a time on <a href="#/${ev}/selection">Selection</a> or the <a href="#/${ev}/dash">Dashboard</a>.</div>`}
      ${goalPanel(d)}<div style="height:16px"></div><div id="rk-table">${table(d)}</div></div>`;
    wire(d);
    const iv = setInterval(async () => { try { const n = await api(`${ev}/rankings.json`); if (n.generated !== d.generated) route(false); } catch (e) { } }, 90000);
    cleanup.push(() => clearInterval(iv));
  });
  CS.nav({ label: "Rankings", key: "rankings", href: ev => `#/${ev}/rankings`, order: 30, group: "day", icon: "rankings", tab: true });

  // ================================================================== Selection tab (#/<ev>/selection[/<ts>])
  // Data: api/<ev>/selection.json (live) or selection/<ts>.json (replay snapshot, goals --asof <ts>), index in
  // selection/index.json. Contract: live/strategy/README.md "selection.json".
  const ROLE_SHORT = { captain: "captain", "first pick": "R1 pick", "second pick": "R2 pick", "not selected": "not picked" };
  const fmtDT = ts => { if (!ts) return "end of data"; const d = new Date(ts * 1000); return DAYS[d.getDay()] + " " + d.toTimeString().slice(0, 5); };
  const P5 = P;
  // difference of two shown percentages, from the rounded values so it always matches what's printed next to it
  const dpp = (a, b) => { if (a == null || b == null) return "–"; const d = Math.round(a * 100) - Math.round(b * 100);
    return d ? `<b class="${d > 0 ? "pos" : "neg"}">${d > 0 ? "+" : "−"}${Math.abs(d)} pp</b>` : `<b class="muted">0 pp</b>`; };

  function selPage(ev, d, times, ts, name) {
    const u = d.us, tm = tn(d.team);
    const head = `<div class="dtop"><div><h1>Alliance selection</h1><div class="sub">${H(name || ev)} · us = ${tm} · projection ${d.asof ? "as of " + fmtDT(d.asof) : "on all data"} · ${d.quals_played ?? "?"}/${d.quals_total ?? "?"} quals played${d.asof ? " · replay snapshot: only data posted before that time" : ""}</div></div></div>`;
    if (d.status !== "projected" && d.status !== "fixed" || !u) {
      if (d.actual) return head + `<section class="card"><h3 style="margin-top:0">Actual alliances (event finished)</h3>
        ${d.actual_us ? `<div class="need">${tm}: ${d.actual_us.seed ? `A${d.actual_us.seed} ${ROLE_SHORT[d.actual_us.role] || d.actual_us.role}` : "not selected"} · ${n0(d.actual_us.event_points)} event pts · DCMP ${P5(d.actual_us.p_dcmp)}</div>` : ""}
        <div class="tablewrap"><table class="mini"><thead><tr><th>Seed</th><th>Captain</th><th data-pri="800">R1</th><th data-pri="700">R2</th><th data-pri="300">Backup</th><th data-pri="600">Result</th></tr></thead><tbody>
        ${d.actual.map(a => `<tr><td>A${a.seed}</td>${[0, 1, 2, 3].map(i => { const t = a.picks[i]; return `<td class="${t === d.team ? "pos" : ""}">${t ? `<b>${tn(t)}</b> <span class="muted small">${n0(a.points[t])} pts</span>` : "–"}</td>`; }).join("")}<td>${H(a.status || "")}</td></tr>`).join("")}</tbody></table></div>
        <div class="small muted">Pick an earlier time in the toolbar to see the projection as it stood before selection.</div></section>`;
      return head + `<section class="card"><div class="empty">${d.status === "done" ? "This event is finished: district points are final, so there is no draft left to project. Pick an earlier time in the toolbar to see the projection as it stood." : "No selection projection for this event (not a district event, or the simulation was unavailable)."}</div></section>`;
    }
    const nd = u.need;
    const who = (u.picked_by || []).map(x => `<tr><td><a href="#/${S.ev}/t/${x.captain}"><b>${tn(x.captain)}</b></a> <span class="muted small">${H((d.teams.find(t => t.team === x.captain) || {}).nickname || "").slice(0, 22)}</span></td><td class="small wrapc">${(x.slots || [x]).map(z => `A${z.seed} ${ROLE_SHORT[z.role]}`).join(", ")}</td><td class="num"><span class="hbar" style="width:${Math.round(x.p * 90)}px"></span>${P5(x.p)}</td></tr>`).join("");
    const outs = (u.outcomes || []).filter(o => o.share >= 0.01).map(o => {
      const pl = o.playoffs || {};
      return `<tr><td><b>${o.seed ? "A" + o.seed + " " : ""}${ROLE_SHORT[o.role]}</b></td><td class="num">${P5(o.share)}</td><td class="num">${n0(o.event_points)}</td>
        <td class="num">${o.p_dcmp == null ? `<span class="muted" title="fewer than 15 simulated seasons">thin</span>` : `<b>${P5(o.p_dcmp)}</b>`}</td><td class="small">${o.seed ? (o.needs ? (o.needs === "out early" ? "any" : H(o.needs)) : "✕") : ""}</td>
        <td class="small muted">${o.playoffs ? `win ${P5(pl.win)} · final ${P5(pl.finalist)} · top-4 ${P5(pl["top 4 (13)"])}` : ""}</td></tr>`; }).join("");
    const cell = c => !c || !c.result ? `<td class="cell-no" title="even winning gives ${c ? c.points : "?"} pts">✕</td>`
      : `<td class="${c.result === "out early" ? "cell-ok" : (c.p_result ?? 0) >= 0.25 ? "cell-ok" : "cell-hard"}" title="${c.points} pts; P(alliance does at least this) ${P5(c.p_result)}">${c.result === "out early" ? "any" : H(c.result)}<span class="sc-sd">${P5(c.p_result)}</span></td>`;
    const grid = nd ? `<table class="mini"><thead><tr><th>Alliance</th><th>as captain</th><th>as R1 pick</th><th>as R2 pick</th></tr></thead><tbody>
      ${nd.grid.map(g => `<tr><td>A${g.seed}</td>${cell(g.captain)}${cell(g["first pick"])}${cell(g["second pick"])}</tr>`).join("")}</tbody></table>` : "";
    // pitch cards (MATCH-33): the argument first, in two short lines; the simulation numbers and fit facts folded below
    const pitches = (u.pitches || []).map(p => { const R = p.resim, alt = R && R.alt && R.alt[0] ? tn(R.alt[0].team) : "their next best";
      return `<div class="pitch"><h4>Pitch to ${tn(p.captain)} <span class="muted small">${H((p.nickname || "").slice(0, 28))} · A${p.seed} ${ROLE_SHORT[p.role]} · ${P5(p.p)} of sims</span></h4>
      <dl class="pitch-l"><dt>We bring</dt><dd>${H(p.we_bring)}</dd><dt>They lack</dt><dd>${H(p.lacks)}</dd></dl>
      <details class="pitch-d"><summary>Simulation and fit</summary>
        ${R ? `<div class="small">If ${tn(p.captain)} takes ${tm} instead of ${alt}: alliance wins the event ${P5(R.take.p_win)} vs ${P5(R.next.p_win)} (${dpp(R.take.p_win, R.next.p_win)}); our DCMP ${P5(R.take.p_dcmp)} vs ${P5(R.next.p_dcmp)} (${dpp(R.take.p_dcmp, R.next.p_dcmp)}).</div>
          <div class="small muted">${R.n} simulated events where they captain and ${tm} is still available.</div>` : `<div class="small">${H(p.pitch)}</div>`}
        ${(p.fit || []).length ? `<ul class="small muted">${p.fit.map(f => `<li>${H(f)}</li>`).join("")}</ul>` : ""}</details></div>`; }).join("") || `<div class="muted small">No likely captain.</div>`;
    const seeds = (d.seeds || []).map(sd => `<tr><td>A${sd.seed}</td><td>${sd.captain.map(c => `<span class="${c.team === d.team ? "pos" : ""}">${tn(c.team)}</span> <span class="muted small">${P5(c.p)}</span>`).join(" · ")}</td>
      <td>${sd.first_pick.map(c => `<span class="${c.team === d.team ? "pos" : ""}">${tn(c.team)}</span> <span class="muted small">${P5(c.p)}</span>`).join(" · ")}</td><td class="num">${P5(sd.playoffs.win)}</td><td class="num">${P5(sd.playoffs.finalist)}</td></tr>`).join("");
    const trows = (d.teams || []).slice(0, 32).sort((a, b) => a.rank_mean - b.rank_mean).map(t => `<tr class="${t.team === d.team ? "usrow" : ""}"><td><a href="#/${S.ev}/t/${t.team}"><b>${t.number}</b></a> <span class="muted small nick">${H((t.nickname || "").slice(0, 20))}</span></td>
      <td class="num">${n0(t.rank_mean, 1)} <span class="muted small">${t.rank_p10}–${t.rank_p90}</span></td><td class="num">${n0(t.rating)}<span class="sc-sd">±${n0(t.rating_sd)}</span></td>
      <td class="num">${P5(t.p_captain)}</td><td class="num">${P5(t.p_r1)}</td><td class="num">${P5(t.p_r2)}</td><td>${t.likely_captain ? `${tn(t.likely_captain.team)} <span class="muted small">${P5(t.likely_captain.p)}</span>` : "–"}</td><td class="num">${P5(t.p_dcmp)}</td></tr>`).join("");
    return head + `<div class="sc-sel">
      <section class="card"><div class="grid"><div>
        <div class="muted small">${tm} at selection · ${d.sims} simulated events</div>
        <div class="kpis" style="margin-top:6px"><div class="kpi"><div class="k">Captain</div><div class="v">${P5(u.p_captain)}</div></div><div class="kpi"><div class="k">R1 pick</div><div class="v">${P5(u.p_r1)}</div></div>
          <div class="kpi"><div class="k">R2 pick</div><div class="v">${P5(u.p_r2)}</div></div><div class="kpi"><div class="k">Not picked</div><div class="v">${P5(u.p_unpicked)}</div></div>
          <div class="kpi" title="headline: no award for us at the remaining events; + awards = with random awards"><div class="k">DCMP (no awards)</div><div class="v">${P5(u.p_dcmp)}</div><div class="small muted">+awards ${P5(u.p_dcmp_with_awards)}</div></div><div class="kpi"><div class="k">Proj. rank</div><div class="v">${n0(u.rank_mean, 1)}</div></div></div>
        ${nd ? `<h3>DCMP need, in pick terms ${CS.help(`<span class="hl">Season so far ${nd.current_points} pts; median projected cutoff ${n0(nd.final_points)} → ${n0(nd.need_here)} needed here, qual points ~${n0(nd.qual_points_median)} (median rank). ${H(nd.note)}.</span><span class="hl">Grid cell = least playoff result that reaches the need from that slot (✕ = not even a win); % = chance that alliance seed gets at least that far in the simulation.</span>`, "How the need is computed")}</h3><div class="need"><b>${H(nd.text)}</b></div>${grid}` : ""}
      </div><div>
        <h3>Who picks ${tm}</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Captain</th><th data-pri="500">As (most likely first)</th><th class="num" data-pri="950">Chance</th></tr></thead><tbody>${who || '<tr><td colspan="3" class="muted">nobody in the simulation</td></tr>'}</tbody></table></div>
        <h3>Pitch cards</h3>${pitches}
      </div></div></section>
      <div style="height:14px"></div>
      <section class="card"><div class="grid"><div><h3 style="margin-top:0">DCMP odds by outcome ${CS.help("Conditional, not causal: share of simulated seasons with that outcome (and no award for us) that reached DCMP (thin = fewer than 15 sims). Needs = least playoff result from that slot that reaches the need (grid above).", "How to read DCMP odds by outcome")}</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Outcome</th><th class="num">Chance</th><th class="num" data-pri="400">Event pts</th><th class="num" data-pri="800">P(DCMP)</th><th data-pri="300">Needs</th><th data-pri="200">Playoffs from that slot</th></tr></thead><tbody>${outs}</tbody></table></div></div>
        <div><h3 style="margin-top:0">If this captain takes ${tm} vs their next best ${CS.help(`Full re-simulation (draft + playoffs + district points) with the pick forced vs forbidden, same random numbers; only seasons where that team captains and ${tm} is still available. Δ win in percentage points.`, "How this comparison is simulated")}</h3>
        <div class="tablewrap"><table class="mini"><thead><tr><th>Captain</th><th>Next best</th><th class="num">Alliance wins<br>take / next</th><th class="num">Δ win</th><th class="num">Our DCMP<br>take / next</th></tr></thead><tbody>
        ${(u.captains || []).map(c => `<tr><td><b>${tn(c.captain)}</b> <span class="muted small">A${c.seed_mode}</span></td><td>${c.alt.map(a => tn(a.team)).join(" / ")}</td>
          <td class="num">${P5(c.take.p_win)} / ${P5(c.next.p_win)}</td><td class="num">${dpp(c.take.p_win, c.next.p_win)}</td>
          <td class="num">${P5(c.take.p_dcmp)} / ${P5(c.next.p_dcmp)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">no likely captain</td></tr>'}</tbody></table></div></div></div></section>
      <div style="height:14px"></div>
      <section class="card"><h3 style="margin-top:0">Projected seeds</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Seed</th><th>Captain (most likely)</th><th data-pri="800">R1 pick (most likely)</th><th class="num" data-pri="700">Win</th><th class="num" data-pri="600">Finalist</th></tr></thead><tbody>${seeds}</tbody></table></div></section>
      <div style="height:14px"></div>
      <section class="card"><h3 style="margin-top:0">Draft odds by team ${CS.help(`${H(d.model?.draft || "")}. Real captains decline, scout for fit and pick defenders; treat these as base rates. Ratings in points per match (incl. foul share).`, "How draft odds are simulated")}</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Team</th><th class="num">Final rank (10–90%)</th><th class="num" data-pri="300">Rating ± sd</th><th class="num" data-pri="700">Captain</th><th class="num" data-pri="650">R1</th><th class="num" data-pri="600">R2</th><th data-pri="200">Most likely captain picking them</th><th class="num" data-pri="800">DCMP</th></tr></thead><tbody>${trows}</tbody></table></div></section></div>`;
  }

  // no selection file (not a district event, the simulation hasn't run, or no snapshot at that replay time): say why
  // instead of an error page (MATCH-7)
  function selEmpty(ev, name, times, ts) {
    const near = ts ? [...times].sort((a, b) => Math.abs(a - ts) - Math.abs(b - ts)).slice(0, 3).sort((a, b) => a - b) : [];
    const paused = window.CS && CS.processing && CS.processing.paused;
    return `<div class="dtop"><div><h1>Alliance selection</h1><div class="sub">${H(name)}</div></div></div>
      <section class="card"><div class="empty sel-empty">${ts
        ? `<b>No selection projection at ${H(fmtDT(+ts))}.</b>${near.length ? ` Snapshots near that time: ${near.map(t => `<a href="#/${ev}/selection/${t}">${H(fmtDT(t))}</a>`).join(" · ")}.` : " This competition has no selection snapshots."}
          <span class="muted">The latest projection (if there is one) is on <a href="#/${ev}/selection">Selection without a replay time</a>.</span>`
        : `<b>No alliance-selection projection for this competition.</b> It's built with the strategy data for district events and needs the
          simulation to have run. This competition may not be a district event, or the worker hasn't built it yet.${paused ? " Processing is paused on the server right now, so nothing new is built until an admin turns it back on." : ""}
          <span class="muted">Rankings and the Picklist still work: <a href="#/${ev}/rankings">Rankings</a> · <a href="#/${ev}/picklist">Picklist</a>.</span>`}</div></section>`;
  }

  CS.route("selection", async function (ev, ts) {
    css();
    S = { ev, sort: S && S.ev === ev ? S.sort : { k: "rank", dir: 1 } };
    crumbs([[ev, `#/${ev}`], ["Selection"]]);             // this page's title even if its data fails to load (MATCH-8)
    const evP = api(`${ev}/event.json`).catch(() => null), idxP = api(`${ev}/selection/index.json`).catch(() => ({ times: [] }));
    let d = null;
    try { d = await api(ts ? `${ev}/selection/${ts}.json` : `${ev}/selection.json`); }
    catch (e) { if (!/404/.test(e.message)) throw e; }
    const [evj, idx] = await Promise.all([evP, idxP]), name = evj && evj.name || ev;
    crumbs([[name, `#/${ev}`], ["Selection"]]);
    if (!d) { $updated.textContent = ""; $app.innerHTML = `<div class="dash rk">${selEmpty(ev, name, idx.times || [], ts ? +ts : 0)}</div>`; return; }
    $updated.textContent = d.asof ? "replay " + fmtDT(d.asof) : ago(d.generated);
    $app.innerHTML = `<div class="dash rk">${selPage(ev, d, idx.times || [], ts, name)}</div>`;
  });
  CS.nav({ label: "Selection", key: "selection", href: (ev, ts) => `#/${ev}/selection` + (ts ? "/" + ts : ""), order: 40, group: "picks", icon: "selection", replay: true });
})();
