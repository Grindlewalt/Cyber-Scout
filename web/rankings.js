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

  const nav = ev => `<div class="evtabs"><a class="tablink" href="#/${ev}/dash">Dashboard</a><a class="tablink on" href="#/${ev}/rankings">Rankings</a><a class="tablink" href="#/${ev}/schedule">Schedule</a><a class="tablink" href="#/${ev}/teams">Teams</a><a class="tablink" href="#/${ev}/matches">Matches</a></div>`;

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
        <div class="bigp ${col}">${P(p)}<small>chance (90% range ${P(ci[0])}–${P(ci[1])})${g.p_dcmp != null ? ` · DCMP ${P(g.p_dcmp)}` : ""}${g.dcmp?.is_prequalified ? " · pre-qualified by award" : ""}</small></div>${bar}
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
      ${goalPanel(d)}<div style="height:16px"></div><div id="rk-table">${table(d)}</div></div>`;
    wire(d);
    const iv = setInterval(async () => { try { const n = await api(`${ev}/rankings.json`); if (n.generated !== d.generated) route(false); } catch (e) { } }, 90000);
    cleanup.push(() => clearInterval(iv));
  });
  CS.nav({ label: "Rankings", href: ev => `#/${ev}/rankings`, order: 30 });
})();
