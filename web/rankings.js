"use strict";
/* rankings.js: #/<ev>/rankings. Live rankings + RP, projected rank range, district points (now / projected / P(DCMP)) and the
 * season-goal panel (odds, points needed, cutoff projection, value of each remaining match).
 * Data: api/<ev>/rankings.json (contract: live/strategy/README.md, "District points, season goals ..."). Missing blocks hide. */
(function () {
  const H = s => esc(s);
  const n0 = (v, d = 0) => v == null ? "–" : Number(v).toFixed(d);
  const P = v => v == null ? "–" : (v >= 0.995 ? ">99%" : v <= 0.005 && v > 0 ? "<1%" : Math.round(v * 100) + "%");
  const tn = k => (k || "").replace("frc", "");
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const fmtT = ts => { if (!ts) return ""; const d = new Date(ts * 1000); return DAYS[d.getDay()] + " " + d.toTimeString().slice(0, 5); };
  const sgn = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v * 100).toFixed(1) + " pts";
  let S = null;

  function css() {
    if (document.getElementById("rk-css")) return;
    const st = document.createElement("style"); st.id = "rk-css";
    st.textContent = `
.rk,.rk>*,.rk .card{min-width:0;max-width:100%}.rk .card{overflow:hidden}.rk .tablewrap{max-width:100%;overflow-x:auto}.rk .vrow>*,.rk .detbox>*{min-width:0}
.rk .dtop{margin-bottom:12px}
.rk .goalcard{display:grid;grid-template-columns:minmax(190px,240px) 1fr;gap:18px;align-items:start}
.rk .bigp{font-size:44px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
.rk .bigp small{display:block;font-size:12px;font-weight:500;color:var(--muted);margin-top:6px;letter-spacing:.2px}
.rk .gbar{position:relative;height:10px;background:var(--panel2);border-radius:5px;margin:12px 0 4px;overflow:hidden}
.rk .gbar i{position:absolute;top:0;bottom:0;background:var(--good);opacity:.85}
.rk .gbar b{position:absolute;top:-2px;bottom:-2px;width:2px;background:#ffd43b}
.rk .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.rk .tile{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 11px}
.rk .tile .k{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.rk .tile .v{font-size:21px;font-weight:650;font-variant-numeric:tabular-nums;margin-top:2px}
.rk .tile .v small{font-size:12px;font-weight:400;color:var(--muted);margin-left:4px}
.rk h3{font-size:12.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin:16px 0 8px;font-weight:600}
.rk .vrow{display:grid;grid-template-columns:92px 1fr 230px;gap:10px;align-items:center;padding:4px 0;font-size:13px}
.rk .vbar{position:relative;height:14px;background:var(--panel2);border-radius:3px}
.rk .vbar .mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
.rk .vbar i{position:absolute;top:2px;bottom:2px;border-radius:2px}
.rk .vbar i.up{background:var(--good)} .rk .vbar i.dn{background:var(--bad)}
.rk .evrow{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
.rk .evrow:last-child{border:0}
.rk .evrow .nm{min-width:130px}
.rk .tag{font-size:11px;padding:0 6px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
.rk .tag.live{color:var(--good);border-color:rgba(63,185,80,.5)} .rk .tag.future{color:#a9cbff;border-color:rgba(62,139,255,.5)} .rk .tag.off{opacity:.55}
.rk .prb{position:relative;height:8px;width:90px;background:var(--panel2);border-radius:4px;display:inline-block;vertical-align:middle;margin-right:6px}
.rk .prb .rg{position:absolute;top:1px;bottom:1px;background:rgba(62,139,255,.55);border-radius:3px}
.rk .prb .mn{position:absolute;top:-1px;bottom:-1px;width:2px;background:#ffd43b}
.rk .pcell{display:inline-block;min-width:34px}
.rk .dp{position:relative;display:inline-block;width:70px;height:8px;background:var(--panel2);border-radius:4px;vertical-align:middle;margin-right:5px}
.rk .dp i{position:absolute;top:1px;bottom:1px;background:rgba(63,185,80,.5);border-radius:3px} .rk .dp b{position:absolute;top:-1px;bottom:-1px;width:2px;background:#ffd43b}
.rk .note{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.5}
.rk th.sorted{color:var(--text)}
@media (max-width:900px){.rk .goalcard{grid-template-columns:1fr}.rk .nick{display:none}.rk .vrow{grid-template-columns:62px 1fr 150px}.rk .prb{width:60px}.rk .dp{width:50px}}
`;
    document.head.appendChild(st);
  }

  const nav = ev => `<div class="evtabs"><a class="tablink" href="#/${ev}/dash">Dashboard</a><a class="tablink on" href="#/${ev}/rankings">Rankings</a><a class="tablink" href="#/${ev}/selection">Selection</a><a class="tablink" href="#/${ev}/schedule">Schedule</a><a class="tablink" href="#/${ev}/teams">Teams</a><a class="tablink" href="#/${ev}/matches">Matches</a></div>`;

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
      return `<div class="tablewrap"><div class="small"><b>${H(evn?.name || e.event)}</b> <span class="muted">odds given how the event goes (conditional, not causal)</span></div>
        <table class="mini"><thead><tr><th>Outcome</th><th class="num">Chance</th><th class="num">P(goal)</th></tr></thead><tbody>${rows}${rows2}</tbody></table></div>`;
    }).join("");
    return `<section class="card goalcard">
      <div><div class="muted small">SEASON GOAL · ${H(goalName)} · ${tn(g.team)}</div>
        <div class="bigp ${col}">${P(p)}<small>${g.awards ? H(g.awards.headline) + " · " : ""}90% range ${P(ci[0])}–${P(ci[1])}${g.p_dcmp != null ? ` · DCMP ${P(g.p_dcmp)}` : ""}${g.dcmp?.is_prequalified ? " · pre-qualified by award" : ""}</small></div>
        ${g.awards ? `<div class="small" style="margin-top:6px" title="${H(g.awards.note)}">${g.awards.contender ? `without awards ${P(g.awards.p_no_awards)}` : `+ random awards: <b>${P(g.awards.p_with_awards)}</b>`} · if we win Impact/EI: ${P(g.awards.p_if_impact_or_ei)}</div>` : ""}${bar}
        <div class="small muted">green = your projected points (10–90%), yellow = projected cutoff</div></div>
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
        ${vm ? `<h3>Value of each remaining qual <span class="muted" style="text-transform:none">change in goal odds, win vs loss</span></h3>${vm}` : ""}
        ${vev ? `<h3>What each event is worth</h3><div class="grid2">${vev}</div>` : ""}
        <div class="note">${g.dcmp ? `DCMP capacity ${g.dcmp.capacity} (about ${n0(g.dcmp.points_slots_mean)} places left for points after award pre-qualifiers). ` : ""}Model: ${H(g.model?.theta || "")}; ${g.model?.sims} simulated seasons, alliance score sd ${n0(g.model?.sigma_alliance)}. District points follow the game manual (first two events + DCMP).</div>
      </div></section>`;
  }

  const COLS = [
    ["rank", "Rk", r => r.rank, "num"], ["team", "Team", r => r.number, ""], ["rec", "W-L-T", r => r.record ? parseInt(r.record) : null, "num"],
    ["rp", "RP", r => r.rp, "num"], ["rs", "RS", r => r.rs, "num"], ["pr", "Proj. rank", r => r.projection?.rank_mean, ""],
    ["t8", "Top 8", r => r.projection?.p_top8, "num"], ["dn", "Dist. pts", r => r.district?.current, "num"],
    ["dp", "Projected pts", r => r.district?.projected, ""], ["dc", "DCMP", r => r.district?.p_dcmp, "num"]];

  function table(d) {
    const rows = d.teams || [];
    const N = rows.length, key = S.sort.k, col = COLS.find(c => c[0] === key) || COLS[0];
    const sorted = [...rows].sort((a, b) => {
      const x = col[2](a), y = col[2](b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (x - y) * S.sort.dir;
    });
    const maxDp = Math.max(1, ...rows.map(r => r.district?.p90 || 0));
    const th = c => `<th data-k="${c[0]}" class="${c[3]}${c[0] === key ? " sorted" : ""}">${c[1]}${c[0] === key ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
    const prbar = r => { const p = r.projection; if (!p) return ""; const L = v => ((v - 1) / Math.max(1, N - 1) * 100).toFixed(1);
      return `<span class="prb" title="mean ${n0(p.rank_mean, 1)}, 10–90%: ${p.rank_p10}–${p.rank_p90}"><i class="rg" style="left:${L(p.rank_p10)}%;width:${Math.max(2, L(p.rank_p90) - L(p.rank_p10))}%"></i><i class="mn" style="left:${L(p.rank_mean)}%"></i></span><span class="small">${n0(p.rank_mean, 1)} <span class="muted">${p.rank_p10}–${p.rank_p90}</span></span>`; };
    const dpbar = r => { const q = r.district; if (!q || q.projected == null) return ""; const X = v => (v / maxDp * 100).toFixed(1);
      return `<span class="dp" title="projected ${n0(q.projected)}, 10–90%: ${n0(q.p10)}–${n0(q.p90)}"><i style="left:${X(q.p10)}%;width:${Math.max(2, X(q.p90) - X(q.p10))}%"></i><b style="left:${X(q.projected)}%"></b></span><span class="small">${n0(q.projected)} <span class="muted">${n0(q.p10)}–${n0(q.p90)}</span></span>`; };
    return `<section class="card"><h2>Qualification rankings <span class="muted small">${d.quals_played}/${d.quals_total} quals played · RS = RP per match · projection = Monte Carlo of the remaining quals</span></h2>
      <div class="tablewrap"><table class="rate rkt"><thead><tr>${COLS.map(th).join("")}</tr></thead><tbody>
      ${sorted.map(r => `<tr class="${r.team === d.team ? "usrow" : ""}">
        <td class="num">${r.rank ?? "–"}</td><td class="tm"><a href="#/${S.ev}/t/${r.team}"><b>${r.number}</b> <span class="muted nick">${H(r.nickname || "")}</span></a></td>
        <td class="num">${H(r.record || "–")}</td><td class="num">${n0(r.rp)}</td><td class="num">${n0(r.rs, 2)}</td>
        <td>${prbar(r)}</td><td class="num">${P(r.projection?.p_top8)}</td>
        <td class="num">${r.district ? n0(r.district.current) : "–"}</td><td>${dpbar(r)}</td>
        <td class="num">${r.district ? P(r.district.p_dcmp) : "–"}</td></tr>`).join("")}
      </tbody></table></div>
      ${d.district ? '<div class="note">Current district points count a team\'s first two district events (all points from finished events: qualification, alliance selection, playoffs, awards) plus rookie bonus. The playing event contributes once it finishes; its projection uses the simulated qualification, alliance selection, playoffs and awards.</div>' : ""}</section>`;
  }

  function wire(d) {
    document.querySelectorAll("table.rkt th[data-k]").forEach(th => th.onclick = () => {
      const k = th.dataset.k; S.sort = S.sort.k === k ? { k, dir: -S.sort.dir } : { k, dir: ["rank", "team", "pr"].includes(k) ? 1 : -1 };
      document.getElementById("rk-table").innerHTML = table(d); wire(d);
    });
  }

  CS.route("rankings", async function (ev) {
    css();
    S = { ev, sort: S && S.ev === ev ? S.sort : { k: "rank", dir: 1 } };
    const d = await api(`${ev}/rankings.json`);
    crumbs([[ev, `#/${ev}`], ["Rankings"]]); $updated.textContent = d.asof ? "replay" : ago(d.generated);
    $app.innerHTML = `<div class="dash rk"><div class="dtop"><div><h1>Rankings &amp; season goal</h1><div class="sub">${H(ev)} · ${tn(d.team)}${d.district ? " · district " + H(d.district) : ""}</div></div>${nav(ev)}</div>
      ${d.asof ? "" : `<div class="sc-banner"><b>End-of-event data</b>, not replay time${d.quals_played ? ` · ${d.quals_played}/${d.quals_total} quals played` : ""}. Replay views: <a href="#/${ev}/selection">Selection</a> time picker, Dashboard time picker.</div>`}
      ${goalPanel(d)}<div style="height:16px"></div><div id="rk-table">${table(d)}</div></div>`;
    wire(d);
    const iv = setInterval(async () => { try { const n = await api(`${ev}/rankings.json`); if (n.generated !== d.generated) route(false); } catch (e) { } }, 90000);
    cleanup.push(() => clearInterval(iv));
  });
  CS.nav({ label: "Rankings", href: ev => `#/${ev}/rankings`, order: 30 });

  // ================================================================== Selection tab (#/<ev>/selection[/<ts>])
  // Data: api/<ev>/selection.json (live) or selection/<ts>.json (replay snapshot, goals --asof <ts>), index in
  // selection/index.json. Contract: live/strategy/README.md "selection.json".
  const ROLE_SHORT = { captain: "captain", "first pick": "R1 pick", "second pick": "R2 pick", "not selected": "not picked" };
  const fmtDT = ts => { if (!ts) return "end of data"; const d = new Date(ts * 1000); return DAYS[d.getDay()] + " " + d.toTimeString().slice(0, 5); };
  const P5 = v => v == null ? "–" : v >= 0.995 ? ">99%" : v < 0.005 ? "<1%" : Math.round(v * 100) + "%";
  const selNav = ev => nav(ev).replace('tablink on" href="#/' + ev + '/rankings"', 'tablink" href="#/' + ev + '/rankings"').replace('tablink" href="#/' + ev + '/selection"', 'tablink on" href="#/' + ev + '/selection"');

  function selPage(ev, d, times, ts) {
    const u = d.us, tm = tn(d.team);
    const opts = [`<option value="">live (${fmtDT(null)})</option>`, ...times.map(t => `<option value="${t}" ${String(t) === String(ts) ? "selected" : ""}>${fmtDT(t)}</option>`)].join("");
    const head = `<div class="dtop"><div><h1>Alliance selection</h1><div class="sub">${H(ev)} · us = ${tm} · projection ${d.asof ? "as of " + fmtDT(d.asof) : "on all data"} · ${d.quals_played ?? "?"}/${d.quals_total ?? "?"} quals played</div></div>${selNav(ev)}</div>
      <div class="scn small" style="margin:0 0 10px">As of <select id="selts">${opts}</select><span class="muted">replay snapshots use only data posted before that time (goals --asof)</span></div>`;
    if (d.status !== "projected" && d.status !== "fixed" || !u) {
      if (d.actual) return head + `<section class="card"><h3 style="margin-top:0">Actual alliances (event finished)</h3>
        ${d.actual_us ? `<div class="need">${tm}: ${d.actual_us.seed ? `A${d.actual_us.seed} ${ROLE_SHORT[d.actual_us.role] || d.actual_us.role}` : "not selected"} · ${n0(d.actual_us.event_points)} event pts · DCMP ${P5(d.actual_us.p_dcmp)}</div>` : ""}
        <div class="tablewrap"><table class="mini"><thead><tr><th>Seed</th><th>Captain</th><th>R1</th><th>R2</th><th>Backup</th><th>Result</th></tr></thead><tbody>
        ${d.actual.map(a => `<tr><td>A${a.seed}</td>${[0, 1, 2, 3].map(i => { const t = a.picks[i]; return `<td class="${t === d.team ? "pos" : ""}">${t ? `<b>${tn(t)}</b> <span class="muted small">${n0(a.points[t])} pts</span>` : "–"}</td>`; }).join("")}<td>${H(a.status || "")}</td></tr>`).join("")}</tbody></table></div>
        <div class="small muted">Pick an earlier time above to see the projection as it stood before selection.</div></section>`;
      return head + `<section class="card"><div class="empty">${d.status === "done" ? "This event is finished: district points are final, so there is no draft left to project. Pick an earlier time above to see the projection as it stood." : "No selection projection for this event (not a district event, or the simulation was unavailable)."}</div></section>`;
    }
    const nd = u.need;
    const who = (u.picked_by || []).map(x => `<tr><td><a href="#/${S.ev}/t/${x.captain}"><b>${tn(x.captain)}</b></a> <span class="muted small">${H((d.teams.find(t => t.team === x.captain) || {}).nickname || "").slice(0, 22)}</span></td><td colspan="2" class="small">${(x.slots || [x]).map(z => `A${z.seed} ${ROLE_SHORT[z.role]}`).join(", ")}</td><td class="num"><span class="hbar" style="width:${Math.round(x.p * 300)}px"></span>${P5(x.p)}</td></tr>`).join("");
    const outs = (u.outcomes || []).filter(o => o.share >= 0.01).map(o => {
      const pl = o.playoffs || {};
      return `<tr><td><b>${o.seed ? "A" + o.seed + " " : ""}${ROLE_SHORT[o.role]}</b></td><td class="num">${P5(o.share)}</td><td class="num">${n0(o.event_points)}</td>
        <td class="num">${o.p_dcmp == null ? `<span class="muted" title="fewer than 15 simulated seasons">thin</span>` : `<b>${P5(o.p_dcmp)}</b>`}</td><td class="small">${o.seed ? (o.needs ? (o.needs === "out early" ? "any" : H(o.needs)) : "✕") : ""}</td>
        <td class="small muted">${o.playoffs ? `win ${P5(pl.win)} · final ${P5(pl.finalist)} · top-4 ${P5(pl["top 4 (13)"])}` : ""}</td></tr>`; }).join("");
    const cell = c => !c || !c.result ? `<td class="cell-no" title="even winning gives ${c ? c.points : "?"} pts">✕</td>`
      : `<td class="${c.result === "out early" ? "cell-ok" : (c.p_result ?? 0) >= 0.25 ? "cell-ok" : "cell-hard"}" title="${c.points} pts; P(alliance does at least this) ${P5(c.p_result)}">${c.result === "out early" ? "any" : H(c.result)}<span class="sc-sd">${P5(c.p_result)}</span></td>`;
    const grid = nd ? `<table class="mini"><thead><tr><th>Alliance</th><th>as captain</th><th>as R1 pick</th><th>as R2 pick</th></tr></thead><tbody>
      ${nd.grid.map(g => `<tr><td>A${g.seed}</td>${cell(g.captain)}${cell(g["first pick"])}${cell(g["second pick"])}</tr>`).join("")}</tbody></table>` : "";
    const pitches = (u.pitches || []).map(p => `<div class="pitch"><h4>${tn(p.captain)} <span class="muted small">${H((p.nickname || "").slice(0, 28))} · A${p.seed} ${ROLE_SHORT[p.role]} · ${P5(p.p)} of sims</span></h4>
      <div><b>${H(p.pitch)}</b></div>${p.resim ? `<div class="small muted">our DCMP if they take us ${P5(p.resim.take.p_dcmp)} vs ${P5(p.resim.next.p_dcmp)} · ${p.resim.n} sims where they captain and we are available</div>` : ""}
      <div class="small muted">They lack: ${H(p.lacks)} · we bring: ${H(p.we_bring)}</div>
      ${(p.fit || []).length ? `<div class="small muted">Fit: ${H(p.fit.join(" · "))}</div>` : ""}</div>`).join("") || `<div class="muted small">No likely captain.</div>`;
    const seeds = (d.seeds || []).map(sd => `<tr><td>A${sd.seed}</td><td>${sd.captain.map(c => `<span class="${c.team === d.team ? "pos" : ""}">${tn(c.team)}</span> <span class="muted small">${P5(c.p)}</span>`).join(" · ")}</td>
      <td>${sd.first_pick.map(c => `<span class="${c.team === d.team ? "pos" : ""}">${tn(c.team)}</span> <span class="muted small">${P5(c.p)}</span>`).join(" · ")}</td><td class="num">${P5(sd.playoffs.win)}</td><td class="num">${P5(sd.playoffs.finalist)}</td></tr>`).join("");
    const trows = (d.teams || []).slice(0, 32).sort((a, b) => a.rank_mean - b.rank_mean).map(t => `<tr class="${t.team === d.team ? "usrow" : ""}"><td><a href="#/${S.ev}/t/${t.team}"><b>${t.number}</b></a> <span class="muted small nick">${H((t.nickname || "").slice(0, 20))}</span></td>
      <td class="num">${n0(t.rank_mean, 1)} <span class="muted small">${t.rank_p10}–${t.rank_p90}</span></td><td class="num">${n0(t.rating)}<span class="sc-sd">±${n0(t.rating_sd)}</span></td>
      <td class="num">${P5(t.p_captain)}</td><td class="num">${P5(t.p_r1)}</td><td class="num">${P5(t.p_r2)}</td><td>${t.likely_captain ? `${tn(t.likely_captain.team)} <span class="muted small">${P5(t.likely_captain.p)}</span>` : "–"}</td><td class="num">${P5(t.p_dcmp)}</td></tr>`).join("");
    return head + `<div class="sc-sel">
      <section class="card"><div class="grid"><div>
        <div class="muted small">${tm} AT SELECTION · ${d.sims} simulated events</div>
        <div class="kpis" style="margin-top:6px"><div class="kpi"><div class="k">Captain</div><div class="v">${P5(u.p_captain)}</div></div><div class="kpi"><div class="k">R1 pick</div><div class="v">${P5(u.p_r1)}</div></div>
          <div class="kpi"><div class="k">R2 pick</div><div class="v">${P5(u.p_r2)}</div></div><div class="kpi"><div class="k">Not picked</div><div class="v">${P5(u.p_unpicked)}</div></div>
          <div class="kpi" title="headline: no award for us at the remaining events; + awards = with random awards"><div class="k">DCMP (no awards)</div><div class="v">${P5(u.p_dcmp)}</div><div class="small muted">+awards ${P5(u.p_dcmp_with_awards)}</div></div><div class="kpi"><div class="k">Proj. rank</div><div class="v">${n0(u.rank_mean, 1)}</div></div></div>
        ${nd ? `<h3>DCMP need, in pick terms</h3><div class="need"><b>${H(nd.text)}</b></div><div class="small muted">Season so far ${nd.current_points} pts; median projected cutoff ${n0(nd.final_points)} → ${n0(nd.need_here)} needed here, qual points ~${n0(nd.qual_points_median)} (median rank). ${H(nd.note)}.</div>${grid}
          <div class="small muted">Cell = least playoff result that reaches the need from that slot (✕ = not even a win); % = chance that alliance seed gets at least that far in the simulation.</div>` : ""}
      </div><div>
        <h3>Who picks ${tm}</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Captain</th><th colspan="2">As (most likely first)</th><th class="num">Chance</th></tr></thead><tbody>${who || '<tr><td colspan="4" class="muted">nobody in the simulation</td></tr>'}</tbody></table></div>
        <h3>Pitch cards</h3>${pitches}
      </div></div></section>
      <div style="height:14px"></div>
      <section class="card"><div class="grid"><div><h3 style="margin-top:0">DCMP odds by outcome</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Outcome</th><th class="num">Chance</th><th class="num">Event pts</th><th class="num">P(DCMP)</th><th>Needs</th><th>Playoffs from that slot</th></tr></thead><tbody>${outs}</tbody></table></div>
        <div class="small muted">Conditional, not causal: share of simulated seasons with that outcome (and no award for us) that reached DCMP (thin = fewer than 15 sims). Needs = least playoff result from that slot that reaches the need (grid above).</div></div>
        <div><h3 style="margin-top:0">If this captain takes ${tm} vs their next best</h3>
        <div class="tablewrap"><table class="mini"><thead><tr><th>Captain</th><th>Next best</th><th class="num">Alliance wins<br>take / next</th><th class="num">Δ win</th><th class="num">Our DCMP<br>take / next</th></tr></thead><tbody>
        ${(u.captains || []).map(c => `<tr><td><b>${tn(c.captain)}</b> <span class="muted small">A${c.seed_mode}</span></td><td>${c.alt.map(a => tn(a.team)).join(" / ")}</td>
          <td class="num">${P5(c.take.p_win)} / ${P5(c.next.p_win)}</td><td class="num"><b class="${(c.delta_win || 0) >= 0 ? "pos" : "neg"}">${(c.delta_win || 0) >= 0 ? "+" : "−"}${Math.abs(Math.round((c.delta_win || 0) * 100))}</b></td>
          <td class="num">${P5(c.take.p_dcmp)} / ${P5(c.next.p_dcmp)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">no likely captain</td></tr>'}</tbody></table></div>
        <div class="small muted" style="margin-top:6px">Full re-simulation (draft + playoffs + district points) with the pick forced vs forbidden, same random numbers; only seasons where that team captains and ${tm} is still available. Δ win in percentage points.</div></div></div></section>
      <div style="height:14px"></div>
      <section class="card"><h3 style="margin-top:0">Projected seeds</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Seed</th><th>Captain (most likely)</th><th>R1 pick (most likely)</th><th class="num">Win</th><th class="num">Finalist</th></tr></thead><tbody>${seeds}</tbody></table></div></section>
      <div style="height:14px"></div>
      <section class="card"><h3 style="margin-top:0">Draft odds by team</h3><div class="tablewrap"><table class="mini"><thead><tr><th>Team</th><th class="num">Final rank (10–90%)</th><th class="num">Rating ± sd</th><th class="num">Captain</th><th class="num">R1</th><th class="num">R2</th><th>Most likely captain picking them</th><th class="num">DCMP</th></tr></thead><tbody>${trows}</tbody></table></div>
      <div class="note">${H(d.model?.draft || "")}. Real captains decline, scout for fit and pick defenders; treat these as base rates. Ratings in points per match (incl. foul share).</div></section></div>`;
  }

  CS.route("selection", async function (ev, ts) {
    css();
    S = { ev, sort: S && S.ev === ev ? S.sort : { k: "rank", dir: 1 } };
    const [d, idx] = await Promise.all([api(ts ? `${ev}/selection/${ts}.json` : `${ev}/selection.json`), api(`${ev}/selection/index.json`).catch(() => ({ times: [] }))]);
    crumbs([[ev, `#/${ev}`], ["Selection"]]); $updated.textContent = d.asof ? "replay " + fmtDT(d.asof) : ago(d.generated);
    $app.innerHTML = `<div class="dash rk">${selPage(ev, d, idx.times || [], ts)}</div>`;
    const sel = document.getElementById("selts"); if (sel) sel.onchange = () => { location.hash = `#/${ev}/selection${sel.value ? "/" + sel.value : ""}`; };
  });
  CS.nav({ label: "Selection", href: ev => `#/${ev}/selection`, order: 31 });
})();
