"use strict";
/* schedule.js: #/<ev>/schedule. All matches: upcoming (with predicted win % and, for the perspective team's matches, an expandable
 * per-period "where you'll be beaten" breakdown) and played (score, link to the Analyze view #/<ev>/a/<key>).
 * Data: api/<ev>/schedule.json (contract: live/strategy/README.md). */
(function () {
  const H = s => esc(s);
  const n0 = (v, d = 0) => v == null ? "–" : Number(v).toFixed(d);
  const P = v => v == null ? "–" : Math.round(v * 100) + "%";
  const tn = k => (k || "").replace("frc", "");
  const sg = (v, d = 0) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d);
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const fmtT = ts => { if (!ts) return "–"; const d = new Date(ts * 1000); return DAYS[d.getDay()] + " " + d.toTimeString().slice(0, 5); };
  let S = null;

  function css() {
    if (document.getElementById("sc-css")) return;
    const st = document.createElement("style"); st.id = "sc-css";
    st.textContent = `
.sc,.sc>*,.sc .card{min-width:0;max-width:100%}.sc .card{overflow:hidden}.sc .tablewrap{max-width:100%;overflow-x:auto}.sc .vrow>*,.sc .detbox>*{min-width:0}
.sc .filters{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
.sc .fchip{padding:4px 12px;min-height:32px;border-radius:99px;border:1px solid var(--line-strong);background:var(--surface);color:var(--ink-2);font-size:var(--fs-foot);cursor:pointer}.sc .fchip:hover{color:var(--ink);background:var(--surface-2)}
.sc td.tms{white-space:normal;min-width:64px} .sc td.tms .t{margin:1px 2px 1px 0} .sc .mt{display:block;font-size:var(--fs-cap);color:var(--ink-2);font-weight:400}
.sc .fchip.on{color:var(--ink);border-color:var(--accent);background:var(--accent-tint);font-weight:600}
.sc table.sct td{padding:5px 8px;vertical-align:middle}
.sc tr.mrow{cursor:default} .sc tr.mrow.exp{cursor:pointer}
.sc tr.mrow.next td{background:var(--sel)}
.sc .t{display:inline-block;padding:0 4px;border-radius:3px;font-variant-numeric:tabular-nums;margin-right:2px}
@media (pointer:coarse){.sc a.t{display:inline-flex;align-items:center;justify-content:center;min-height:36px;min-width:44px;margin:2px 3px 2px 0}}
.sc .t.r{background:var(--red-soft)} .sc .t.b{background:var(--blue-soft)} .sc .t.us{outline:2px solid var(--accent);outline-offset:-1px;font-weight:700}
.sc .pbar{display:flex;height:8px;width:80px;border-radius:4px;overflow:hidden;background:var(--surface-3);gap:1px}
.sc .pbar i.r{background:var(--red-fill)} .sc .pbar i.b{background:var(--blue-fill)}
.sc .sc2{font-variant-numeric:tabular-nums}
.sc .win{font-weight:700}
.sc tr.det td{padding:0;background:var(--panel2);white-space:normal}
.sc .detbox{padding:12px 14px;display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:16px}
.sc .sum{font-size:14px;line-height:1.5;margin-bottom:8px}
.sc .per{display:grid;grid-template-columns:62px 1fr 56px 56px 56px;gap:8px;align-items:center;font-size:12.5px;padding:3px 0;font-variant-numeric:tabular-nums}
.sc .per.hd{color:var(--ink-2);font-size:var(--fs-cap);font-weight:600}
.sc .dvb{position:relative;height:14px;background:var(--surface);border-radius:3px}
.sc .dvb .mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
.sc .dvb i{position:absolute;top:2px;bottom:2px;border-radius:2px} .sc .dvb i.up{background:var(--good)} .sc .dvb i.dn{background:var(--bad)}
.sc .act{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;vertical-align:middle;background:var(--muted);opacity:.5}
.sc .act.on{background:#ffd43b;opacity:1}
.sc .kvs{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);margin-top:8px}
.sc .kvs b{color:var(--text)}
.sc .chev{display:inline-block;width:16px;color:var(--ink-2);transition:transform .15s var(--ease);vertical-align:-3px}.sc .chev svg{width:14px;height:14px} .sc tr.open .chev{transform:rotate(90deg)}
@media (max-width:900px){.sc .detbox{grid-template-columns:1fr}.sc .hide-s{display:none}.sc .per{grid-template-columns:54px 1fr 46px 46px 46px}}
`;
    document.head.appendChild(st);
  }

  function teams(ts, c, us) { return ts.map(k => `<a class="t ${c} ${k === us ? "us" : ""}" href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(""); }

  function breakdown(m, team) {
    const b = m.breakdown; if (!b) return "";
    const us = m.us, them = us === "red" ? "blue" : "red";
    const per = b.periods || [];
    const mx = Math.max(5, ...per.map(p => Math.abs(p.delta || 0)));
    const rows = per.map(p => {
      const w = Math.abs(p.delta || 0) / mx * 50, up = (p.delta || 0) >= 0;
      const act = ["shift1", "shift2", "shift3", "shift4"].includes(p.period)
        ? `<span class="act ${p.our_active_p >= 0.5 ? "on" : ""}" title="our hub active in ${Math.round(p.our_active_p * 100)}% of sims"></span>` : "";
      const tw = p.tower, lab = tw ? `${p.label}${(tw.ours || 0) + (tw.theirs || 0) >= 1 ? "" : " FUEL"}` : p.label;
      const tip = tw ? `${p.label}: FUEL us ${n0((p.ours ?? 0) - (tw.ours || 0), 1)} / them ${n0((p.theirs ?? 0) - (tw.theirs || 0), 1)}; tower (climb) us ${n0(tw.ours, 1)} / them ${n0(tw.theirs, 1)}` : "";
      return `<div class="per" title="${H(tip)}"><span>${act}${H(lab)}</span>
        <div class="dvb"><span class="mid"></span><i class="${up ? "up" : "dn"}" style="${up ? `left:50%;width:${w}%` : `right:50%;width:${w}%`}"></i></div>
        <span class="${us === "red" ? "t r" : "t b"}" style="text-align:right">${n0(p.ours, 1)}</span><span class="t ${them === "red" ? "r" : "b"}" style="text-align:right">${n0(p.theirs, 1)}</span>
        <b class="${up ? "pos" : "neg"}" style="text-align:right">${sg(p.delta, 1)}</b></div>`;
    }).join("");
    const swing = per.find(p => p.period === "auto");
    return `<div class="detbox"><div><div class="sum">${H(b.summary || "")}</div>
        <div class="kvs"><span>Win <b>${P(b.win_prob)}</b></span><span>Win auto <b>${P(b.p_win_auto)}</b></span>
          <span>Score <b>${n0(b.score?.ours)}</b> – ${n0(b.score?.theirs)}</span><span>Margin <b>${sg(b.score?.margin)}</b> ± ${n0(b.score?.margin_sd)}</span>
          <span>Fouls to us/them <b>${n0(b.fouls?.ours)}</b>/${n0(b.fouls?.theirs)}</span></div></div>
      <div><div class="per hd"><span>Period ${CS.help('<span class="act on"></span>= our hub likely active (the auto loser has shifts 1 and 3). Points per period, incl. human-player FUEL; Auto and Endgame also include tower (climb) points, split out on hover.', "How to read the period breakdown")}</span><span></span><span style="text-align:right">Us</span><span style="text-align:right">Them</span><span style="text-align:right">Δ</span></div>${rows}
</div></div>`;
  }

  function rowHtml(m, team) {
    const hasB = !!m.breakdown;
    const us = m.us ? team : null;
    let mid;
    const L = !m.played && S.live && S.live[m.key];            // livestream result, TBA not posted yet (event.json row)
    const ls = L && L.red && L.blue && L.red.score != null && L.blue.score != null ? { red: L.red.score, blue: L.blue.score, winner: L.winner || (L.red.score === L.blue.score ? "tie" : null) } : null;
    if (ls) {
      mid = `<td class="num sc2"><span class="t r ${ls.winner === "red" ? "win" : ""}">${ls.red}</span> – <span class="t b ${ls.winner === "blue" ? "win" : ""}">${ls.blue}</span>${CS.liveTag(L, "blk")}</td>
        <td class="hide-s small muted">${H(CS.liveTracking(L.live, L.state === "done"))}</td>`;
    } else if (m.played) {
      const r = m.result, rw = r.winner === "red", bw = r.winner === "blue";
      mid = `<td class="num sc2"><span class="t r ${rw ? "win" : ""}">${r.red}</span> – <span class="t b ${bw ? "win" : ""}">${r.blue}</span></td>
        <td class="hide-s small muted">${r.rp?.red != null ? `${r.rp.red}/${r.rp.blue} RP` : ""}</td>`;
    } else {
      const p = m.pred;
      mid = p ? `<td class="num"><span class="pbar" title="red ${P(p.p_red)} · blue ${P(1 - p.p_red)}"><i class="r" style="width:${p.p_red * 100}%"></i><i class="b" style="width:${(1 - p.p_red) * 100}%"></i></span>
          <span class="small muted">${P(p.p_red)}</span></td><td class="hide-s small muted sc2">${n0(p.score?.red)}–${n0(p.score?.blue)}</td>`
        : `<td class="muted">–</td><td class="hide-s"></td>`;
    }
    const ours = m.us ? ` <span class="small ${m.us === "red" ? "" : ""}" title="${tn(team)} is on ${m.us}"><span class="adot ${m.us}"></span></span>` : "";
    const wp = ls && m.us ? `<span class="res ${ls.winner === m.us ? "w" : ls.winner === "tie" ? "" : "l"}">${ls.winner === m.us ? "W" : ls.winner === "tie" ? "T" : "L"}</span>` : !m.played && hasB ? `<b class="${m.breakdown.win_prob >= .5 ? "pos" : "neg"}">${P(m.breakdown.win_prob)}</b>` : (m.played && m.us ? `<span class="res ${m.result.winner === m.us ? "w" : m.result.winner === "tie" ? "" : "l"}">${m.result.winner === m.us ? "W" : m.result.winner === "tie" ? "T" : "L"}</span>` : "");
    return `<tr class="mrow ${hasB ? "exp" : ""} ${S.nextKey === m.key ? "next" : ""}" id="r-${m.key}" data-k="${m.key}">
      <td><span class="chev">${hasB ? CS.icon("chevR") : ""}</span>${m.played && m.analyze_url ? `<a href="${m.analyze_url}" title="open the match video and replay"><b>${H(m.short)}</b></a>` : ls ? `<a href="#/${S.ev}/m/${m.key}" title="open the match (livestream clip)"><b>${H(m.short)}</b></a>` : `<b>${H(m.short)}</b>`}${ours}<span class="mt">${fmtT(m.time)}</span></td>
      <td class="tms">${teams(m.red, "r", us)}</td><td class="tms">${teams(m.blue, "b", us)}</td>${mid}<td class="num">${wp}</td></tr>
      ${hasB ? `<tr class="det" id="d-${m.key}" style="display:none"><td colspan="6">${breakdown(m, team)}</td></tr>` : ""}`;
  }

  function list(d) {
    const f = S.filter, team = d.team;
    let ms = d.matches;
    if (f === "ours") ms = ms.filter(m => m.us);
    else if (f === "up") ms = ms.filter(m => !m.played);
    else if (f === "played") ms = ms.filter(m => m.played);
    return `<table class="sct"><thead><tr><th>Match</th><th data-pri="950">Red</th><th data-pri="940">Blue</th><th class="num" data-pri="800">Result / P(red)</th><th class="hide-s" data-pri="200">RP / score</th><th class="num" data-pri="700">${tn(team)}</th></tr></thead><tbody>${ms.map(m => rowHtml(m, team)).join("")}</tbody></table>`;
  }

  function wire(d) {
    document.querySelectorAll(".fchip").forEach(c => c.onclick = () => { S.filter = c.dataset.f; draw(d); });
    document.querySelectorAll("tr.mrow.exp").forEach(r => r.onclick = e => {
      if (e.target.closest("a")) return;
      const det = document.getElementById("d-" + r.dataset.k), open = det.style.display === "none";
      det.style.display = open ? "" : "none"; r.classList.toggle("open", open);
      if (open) S.open.add(r.dataset.k); else S.open.delete(r.dataset.k);
    });
    S.open.forEach(k => { const r = document.getElementById("r-" + k), det = document.getElementById("d-" + k); if (r && det) { det.style.display = ""; r.classList.add("open"); } });
  }
  function draw(d) {
    const cnt = { all: d.matches.length, ours: d.matches.filter(m => m.us).length, up: d.matches.filter(m => !m.played).length, played: d.matches.filter(m => m.played).length };
    const lab = { all: "All", ours: tn(d.team) + " only", up: "Upcoming", played: "Played" };
    document.getElementById("sc-body").innerHTML = `<div class="filters">${Object.keys(lab).map(k => `<button type="button" class="fchip ${S.filter === k ? "on" : ""}" data-f="${k}" aria-pressed="${S.filter === k}">${lab[k]} <span class="muted">${cnt[k]}</span></button>`).join("")}</div>
      <div class="tablewrap">${list(d)}</div>`;
    wire(d);
  }

  CS.route("schedule", async function (ev, key) {
    css();
    crumbs([[ev, `#/${ev}`], ["Schedule"]]);             // this page's title even if its data fails to load (MATCH-8)
    const [d, evj] = await Promise.all([api(`${ev}/schedule.json`), api(`${ev}/event.json`).catch(() => null)]);
    const live = !d.asof && evj ? Object.fromEntries((evj.matches || []).filter(m => CS.isStream(m)).map(m => [m.key, m])) : null;
    const nextOurs = d.matches.find(m => !m.played && m.us && !(live && live[m.key]));
    S = { ev, filter: S && S.ev === ev ? S.filter : (d.matches.some(m => m.us) ? "ours" : "all"), open: new Set(S && S.ev === ev ? S.open : []), nextKey: nextOurs?.key };
    if (nextOurs && !S.open.size) S.open.add(nextOurs.key);
    S.live = live;
    // asof: built with data up to that time (a finished event), not a replay; "Latest data" until the competition is over (MATCH-12)
    const name = evj && evj.name || ev, asofTxt = d.asof ? (CS.fmtWhen ? CS.fmtWhen(d.asof, { day: true }) : new Date(d.asof * 1000).toLocaleString()) : "";
    const lab = CS.latestLabel ? CS.latestLabel(ev) : "Latest data";
    crumbs([[name, `#/${ev}`], ["Schedule"]]); $updated.textContent = d.asof ? "data as of " + asofTxt : ago(d.generated);
    $app.innerHTML = `<div class="dash sc"><div class="dtop"><div><h1>Schedule</h1><div class="sub">${H(name)} · us = ${tn(d.team)} ${CS.help(`Predictions for <b>${tn(d.team)}</b>'s matches break down where each alliance scores: tap a row with ▸ to expand it. Tap a played match to open its video and replay.`, "How the schedule works")}</div></div></div>
      <div class="sc-banner"><b>${H(lab)}</b>${d.asof ? ` as of ${H(asofTxt)}` : ""}, not replay time ${CS.help("Predictions for unplayed matches use everything posted so far. For a replay view, pick a time on the Dashboard.", "About this data")}</div>
      <section class="card" id="sc-body"></section></div>`;
    draw(d);
    const target = key;                              // deep link #/<ev>/schedule/<matchkey>
    if (target) setTimeout(() => document.getElementById("r-" + target)?.scrollIntoView({ block: "center" }), 50);
    const iv = setInterval(async () => { try { const n = await api(`${ev}/schedule.json`); if (n.generated !== d.generated) route(false); } catch (e) { } }, 90000);
    cleanup.push(() => clearInterval(iv));
  });
  CS.nav({ label: "Schedule", key: "schedule", href: ev => `#/${ev}/schedule`, order: 20, group: "day", icon: "schedule", tab: true });
})();
