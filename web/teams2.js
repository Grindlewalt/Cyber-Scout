"use strict";
// teams2.js: TEAMS (#/<ev>/teams, #/<ev>/teams/<team>, #/<ev>/teams/<team>/<field>) and the team SPEC SHEET renderer. The old
// Specs list became Teams; a spec sheet's cards are now fields of a team (lanes, shot locations, shooting paths, drive, ...),
// each opening as its own page (specPage(ev, team, {only, host}) draws just those cards). Data: api/<ev>/spec/<team>.json
// written by live/scouting.py. Team frame = own alliance wall on the LEFT (blue matches point-reflected); driver's left = top.
// Uses globals from app.js / quant.js: api, esc, fx, pct, crumbs, fieldCanvas, FIELD, LEN, WID, cleanup, $app, $updated,
// tile, bars, has, densityLayer, drawHubMark, chipRow, C_MADE, C_MISS, C_PASS, HUBS.
(function () {
  const H = s => esc(s);
  const tn = k => String(k || "").replace("frc", "");
  const LANE_COL = { trench: "#22d3ee", bump: "#f59e0b", open: "#94a3b8", neutral: "#a78bfa", zone: "#64748b" };
  const PER_TXT = { a: "Auto", t: "Trans", "1": "S1", "2": "S2", "3": "S3", "4": "S4", e: "End" };
  const BANDS = [["trench", "L", 0, 1.194], ["bump", "L", 1.584, 3.438], ["bump", "R", 4.631, 6.486], ["trench", "R", 6.876, 8.069]];
  const HUB_OWN = 11.915, HUB_OPP = 4.626;
  const CSS = `
  .sx-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
  .sx-head h1 small{font-weight:500;color:var(--muted);font-size:16px}
  .sx-links{display:flex;gap:6px;flex-wrap:wrap}.sx-links a{border:1px solid var(--line-strong);border-radius:var(--r-s);padding:4px 10px;font-size:var(--fs-foot);text-decoration:none;background:var(--surface)}.sx-links a:hover{background:var(--surface-2)}
  .sx-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:var(--sp-4)}
  .sx-grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--sp-4)}
  @media (max-width:1000px){.sx-grid3{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
  @media (max-width:820px){.sx-grid,.sx-grid3{grid-template-columns:minmax(0,1fr)}}
  .sx-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m);padding:var(--sp-4);min-width:0}
  .sx-card h2{font-size:var(--fs-callout);font-weight:600;margin:0 0 var(--sp-3);display:flex;justify-content:space-between;gap:8px;align-items:baseline;color:var(--ink)}
  .sx-card h2 small{font-weight:500;text-transform:none;letter-spacing:0;color:var(--muted);font-size:12px}
  .sx-kv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:13px}
  .sx-kv dt{color:var(--muted)}.sx-kv dd{margin:0;font-variant-numeric:tabular-nums;text-align:right}
  .sx-kv dd small{color:var(--muted)}
  .sx-stat{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:var(--sp-3) var(--sp-4)}
  .sx-stat div{border-top:1px solid var(--line);padding:var(--sp-2) 0 0;min-width:0}
  .sx-stat b{display:block;font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:20px;font-weight:600;line-height:1.15;font-variant-numeric:tabular-nums}.sx-stat b small{font-size:11px;color:var(--muted);font-weight:500}
  .sx-stat span{display:block;font-size:11.5px;color:var(--muted)}
  .sx-tbl td,.sx-tbl th{padding:4px 6px;font-size:12.5px}
  .sx-tbl tr.sel td{background:var(--sel)}
  .sx-tbl tr.clk{cursor:pointer}
  .sx-leg{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--muted);margin-top:6px}
  .sx-leg i{display:inline-block;width:12px;height:4px;border-radius:2px;margin-right:4px;vertical-align:middle}
  .sx-leg i.dot{width:9px;height:9px;border-radius:50%}
  .sx-ctl{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px;font-size:12px}
  .sx-ctl select,.sx-ctl button{background:var(--surface);color:var(--ink);border:1px solid var(--line-strong);border-radius:var(--r-s);padding:3px 8px;font-size:12px;min-height:28px}
  .sx-adv{margin:0;padding-left:18px;font-size:13px;line-height:1.5}
  .sx-mini{font-size:11.5px;color:var(--muted)}
  .sx-bar{height:6px;background:var(--surface-3);border-radius:3px;overflow:hidden;min-width:40px}.sx-bar i{display:block;height:100%}
  .sx-list th{cursor:pointer}
  .sx-pill{display:inline-block;padding:0 6px;border-radius:var(--r-tag);font-size:11px;border:1px solid var(--line);color:var(--muted)}
  `;
  function css() { if (!document.getElementById("sx-css")) { const s = document.createElement("style"); s.id = "sx-css"; s.textContent = CSS; document.head.appendChild(s); } }
  const f1 = (v, d = 1) => v == null ? "–" : Number(v).toFixed(d);
  const pc = v => v == null ? "–" : Math.round(v * 100) + "%";

  // ------------------------------------------------------------------ drawings (team frame)
  function laneDiagram(F, sp) {
    const { ctx, P, S, c, bg } = F; bg(.35);
    const cnt = { own: (sp.lanes.own || {}).counts || {}, opp: (sp.lanes.opp || {}).counts || {} };
    const mx = Math.max(1, ...Object.values(cnt.own), ...Object.values(cnt.opp));
    const fs = Math.max(10, c.width / 70);
    for (const [line, x] of [["own", HUB_OWN], ["opp", HUB_OPP]]) {
      for (const [kind, side, y0, y1] of BANDS) {
        const n = cnt[line][`${kind}_${side}`] || 0, a = n / mx;
        const [px0, py0] = P(x + .55, y0), [px1, py1] = P(x - .55, y1);
        ctx.fillStyle = LANE_COL[kind]; ctx.globalAlpha = n ? .18 + .62 * a : .06;
        ctx.fillRect(px0, py0, px1 - px0, py1 - py0); ctx.globalAlpha = 1;
        ctx.strokeStyle = LANE_COL[kind]; ctx.lineWidth = 1; ctx.strokeRect(px0, py0, px1 - px0, py1 - py0);
        ctx.fillStyle = "#fff"; ctx.font = `600 ${fs}px system-ui`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(String(n), (px0 + px1) / 2, (py0 + py1) / 2);
      }
    }
    // individual crossings as ticks (arrow = direction)
    for (const cr of sp.crossings || []) {
      if (cr.y == null) continue;
      const x = cr.line === "own" ? HUB_OWN : HUB_OPP, [px, py] = P(x, cr.y), d = (cr.dir === "in" ? -1 : 1) * S(.35);
      ctx.strokeStyle = cr.dir === "in" ? "#e2e8f0" : "#64748b"; ctx.lineWidth = 1.4; ctx.globalAlpha = .8;
      ctx.beginPath(); ctx.moveTo(px - d, py); ctx.lineTo(px + d, py); ctx.lineTo(px + d * .5, py - S(.12)); ctx.moveTo(px + d, py); ctx.lineTo(px + d * .5, py + S(.12)); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.font = `${fs}px system-ui`; ctx.textAlign = "left"; ctx.textBaseline = "top";
    const lab = (t, x, y, al = "left") => { ctx.textAlign = al; const w = ctx.measureText(t).width; ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.fillRect(al === "left" ? x - 3 : x - w - 3, y - 2, w + 6, fs + 5); ctx.fillStyle = "#e6edf3"; ctx.fillText(t, x, y); };
    lab("own wall ▸ driver's LEFT", 6, 4); lab("driver's RIGHT", 6, c.height - fs - 6);
    lab("own hub line", P(HUB_OWN, 0)[0] + S(.7), c.height / 2 - fs);
    lab("opp. hub line", P(HUB_OPP, 0)[0] - S(.7), c.height / 2 - fs, "right");
  }
  function shotMap(F, pts, mode) {
    const { ctx, P, c, bg } = F; bg(.5);
    if (mode !== "pts") densityLayer(F, pts.map(p => [p[0] / 100, p[1] / 100]), .5, 55);
    drawHubMark(F, HUBS.red[0], HUBS.red[1]);
    if (mode !== "heat") {
      const r = Math.max(2, c.width / 300);
      for (const p of pts) { const [x, y] = P(p[0] / 100, p[1] / 100); ctx.fillStyle = p[2] === "m" ? C_MADE : p[2] === "x" ? C_MISS : "#94a3b8"; ctx.globalAlpha = .75;
        ctx.beginPath(); if (p[5] === 1) { ctx.rect(x - r, y - r, 2 * r, 2 * r); } else ctx.arc(x, y, r, 0, 7); ctx.fill(); }
      ctx.globalAlpha = 1;
    }
  }
  const apprKey = p => p.approach && p.approach.lane !== "unknown" ? p.approach.lane : p.from === "neutral exit" ? "neutral" : "zone";
  const apprTxt = p => p.approach && p.approach.lane !== "unknown" ? `${p.approach.lane} ${p.approach.side === "L" ? "left" : "right"}` : p.from === "neutral exit" ? "from neutral" : "inside zone";

  // ------------------------------------------------------------------ animated shooting paths
  function pathsAnim(el, getPaths, getSel) {
    const F = fieldCanvas(el);
    const off = document.createElement("canvas");
    let T0 = performance.now(), span = [-5, 5], raf = 0, dead = false, speed = 1, stale = true;
    function rebuild() {
      F.size(); off.width = F.c.width; off.height = F.c.height;
      const ps = getPaths(), sel = getSel();
      span = [-Math.max(2, ...ps.map(p => p.i / p.hz), 0), Math.max(1, ...ps.map(p => (p.xy.length - 1 - p.i) / p.hz), 0) + 1.2];
      const o = off.getContext("2d");
      const G = { ...F, ctx: o };
      o.globalAlpha = .5; if (FIELD.complete && FIELD.naturalWidth) o.drawImage(FIELD, 0, 0, off.width, off.height); else { o.fillStyle = "#3a3f46"; o.fillRect(0, 0, off.width, off.height); } o.globalAlpha = 1;
      drawHubMark(G, HUBS.red[0], HUBS.red[1]);
      const lw = Math.max(1.2, off.width / 520);
      for (const p of ps) {
        const hi = sel == null || sel === p, col = LANE_COL[apprKey(p)];
        o.lineCap = o.lineJoin = "round";
        for (let j = 1; j < p.xy.length; j++) {
          const a = p.xy[j - 1], b = p.xy[j]; if (!a || !b) continue;
          const [x0, y0] = F.P(a[0] / 100, a[1] / 100), [x1, y1] = F.P(b[0] / 100, b[1] / 100);
          o.strokeStyle = j > p.i ? "#4ade80" : col; o.lineWidth = (j > p.i ? 2.2 : 1.2) * lw * (sel === p ? 1.8 : 1);
          o.globalAlpha = hi ? .85 : .12; o.beginPath(); o.moveTo(x0, y0); o.lineTo(x1, y1); o.stroke();
        }
        const s = p.xy[Math.min(p.i, p.xy.length - 1)];
        if (s) { const [x, y] = F.P(s[0] / 100, s[1] / 100), r = 3 * lw; o.globalAlpha = hi ? 1 : .2; o.strokeStyle = "#fff"; o.lineWidth = lw * 1.4;
          o.beginPath(); o.moveTo(x - r, y - r); o.lineTo(x + r, y + r); o.moveTo(x + r, y - r); o.lineTo(x - r, y + r); o.stroke(); }
      }
      o.globalAlpha = 1; stale = false;
    }
    function frame(now) {
      if (dead) return;
      if (stale) rebuild();
      const { ctx, c, P } = F, ps = getPaths(), sel = getSel();
      ctx.clearRect(0, 0, c.width, c.height); ctx.drawImage(off, 0, 0);
      const L = span[1] - span[0], tau = span[0] + ((now - T0) / 1000 * speed) % (L + 1);
      const r = Math.max(3, c.width / 150);
      for (const p of ps) {
        if (sel != null && sel !== p) continue;
        const k = p.i + tau * p.hz, j = Math.floor(k);
        if (j < 0 || j >= p.xy.length - 1) continue;
        const a = p.xy[j], b = p.xy[j + 1] || a; if (!a || !b) continue;
        const f = k - j, [x, y] = P((a[0] + (b[0] - a[0]) * f) / 100, (a[1] + (b[1] - a[1]) * f) / 100);
        const shooting = tau >= 0;
        ctx.fillStyle = shooting ? "#4ade80" : "#fff"; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
        if (shooting) { ctx.strokeStyle = "rgba(74,222,128,.6)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, r * (1.6 + (tau * 3) % 1.2), 0, 7); ctx.stroke(); }
      }
      const k_ = window.devicePixelRatio || 1;
      ctx.fillStyle = "rgba(0,0,0,.6)"; ctx.fillRect(6 * k_, 6 * k_, 124 * k_, 22 * k_); ctx.fillStyle = tau >= 0 ? "#4ade80" : "#e6edf3"; ctx.font = `600 ${12 * k_}px system-ui`; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText(tau < 0 ? `${(-tau).toFixed(1)} s to 1st shot` : `shooting +${tau.toFixed(1)} s`, 12 * k_, 17 * k_);
      raf = requestAnimationFrame(frame);
    }
    const onR = () => { stale = true; };
    window.addEventListener("resize", onR);
    FIELD.complete || FIELD.addEventListener("load", onR, { once: true });
    raf = requestAnimationFrame(frame);
    cleanup.push(() => { dead = true; cancelAnimationFrame(raf); window.removeEventListener("resize", onR); });
    return { refresh() { stale = true; T0 = performance.now(); }, setSpeed(s) { speed = s; } };
  }

  // ------------------------------------------------------------------ spec page
  // opt: {host: element to draw in (default the page), only: card ids to show (data-sx), quiet: leave the title alone}
  async function specPage(ev, team, opt = {}) {
    if (!opt.quiet) crumbs([[ev, `#/${ev}`], ["Teams", `#/${ev}/teams`], [num(team)]]);       // title before the data (MATCH-8)
    css();
    const [sp, e, sd] = await Promise.all([api(`${ev}/spec/${team}.json`), api(`${ev}/event.json`).catch(() => null), scoutData(ev)]);
    const TR = sp.truth || {}, TV = TR.video || {}, autoBad = (TR.flags || []).some(f => f.k === "auto_not_tracked");
    if (!opt.quiet) { crumbs([[e ? e.name : ev, `#/${ev}`], ["Teams", `#/${ev}/teams`], [String(sp.number)]]); $updated.textContent = ""; }
    const root = opt.host || $app;
    const D = sp.drive || {}, S = sp.scoring || {}, A = sp.auto || {}, CL = sp.climb || {}, LN = sp.lanes || {}, DF = sp.defense || {};
    const st = (v, l, u = "", tip = "") => `<div${!tip ? "" : typeof tipA === "function" ? tipA(tip) : ` title="${H(tip)}"`}><b>${v}${u && v !== "–" ? `<small> ${u}</small>` : ""}</b><span>${l}</span></div>`;
    const matchTxt = (sp.matches || []).map(m => `<span title="identity-valid ${pc(m.valid)} of the match, mean conf ${pc(m.conf)}">${m.m}<span class="sx-mini">(${m.al[0]}, ${pc(m.valid)})</span></span>`).join(" ");
    const recv = DF.received || {}, play = DF.played || null;
    const lvls = CL.levels || {}, nL = Object.values(lvls).reduce((a, b) => a + b, 0);
    const ownN = (LN.own || {}).n || 0;
    root.innerHTML = `<div class="sx-head"><div><h1>${sp.number} <small>${H(sp.nickname || "")}</small></h1>
      <div class="sub">${sp.rank ? `Rank ${sp.rank} · ` : ""}${covPill(sp.coverage)} ${(sp.matches || []).length ? CS.help(`<span class="hl">Verified matches (alliance, identity-valid share of the match):</span><span class="hl">${matchTxt}</span>`, "Which matches are verified") : ""} ${flagChips(TR)}</div></div>
      <div class="sx-links"><a href="#/${ev}/t/${team}">Team page</a><a href="#/${ev}/selection/defense/${team}">How to defend ${sp.number}</a><a href="#/${ev}/teams/${team}">All fields</a></div></div>
    ${dataBanner(sd)}${(sp.coverage?.n || 0) < 3 ? `<div class="sc-banner"><b>n=${sp.coverage?.n || 0}</b>: fewer than 3 verified matches. Every video number on this page is an anecdote, not a rate.</div>` : ""}
    <div class="sx-grid3">
      <div class="sx-card" data-sx="drive"><h2>Drive <small>${f1(D.seconds, 0)} s tracked</small></h2><div class="sx-stat">
        ${st(f1(D.top_speed, 2), "Top speed", "m/s", "99th percentile of speed")}${st(f1(D.p95_speed, 2), "p95 moving", "m/s")}${st(f1(D.mean_speed, 2), "Mean moving", "m/s")}
        ${st(f1(D.accel_p90), "Accel p90", "m/s²")}${st(f1(D.accel_max), "Accel max", "m/s²", "99th percentile")}${st(pc(D.pct_moving), "Time moving")}${st(f1(D.dist_per_match, 0), "Dist / match", "m")}</div></div>
      <div class="sx-card" data-sx="scoring"><h2>Scoring <small>${S.made}/${S.att} made${S.passes ? ` · ${S.passes} passes` : ""} · ${S.n_fuel ?? 0} reconciled match${S.n_fuel === 1 ? "" : "es"}</small> ${CS.help(`<span class="hl">Scoring counts FUEL only in matches whose video total reconciles with the TBA alliance score (${S.n_fuel ?? 0} of ${(sp.matches || []).length} verified here), so the totals can be lower than the shot map, which shows every verified match.</span>`, "Which matches these totals cover")}</h2><div class="sx-stat">
        ${st(TV.made_pm ? (TV.made_pm.lower_bound ? "≥" : "") + f1(TV.made_pm.v) : f1(S.made_pm), "Made / match", "", TV.made_pm ? `canonical (shrunk, TBA-reconciled); raw ${f1(TV.made_pm.raw)} over n=${TV.made_pm.n}` : "")}${st(pc(S.acc), "Accuracy", "", `${S.made}/${S.made + S.missed} shots`)}
        ${st(f1(S.pts_per_active_min), "Pts / active min", "", "made into an active hub per minute of own-hub-active teleop")}
        ${st(f1(S.fire_rate), "Fire rate", "/s", "balls per second inside a volley of 3+")}${st(f1(S.volley_mean), "Volley mean")}
        ${st(S.hopper_est ?? S.volley_max ?? "–", "Volley p90 ≈ hopper", "", `max volley ${S.volley_max ?? "–"}, ${S.n_volleys} teleop volleys`)}
        ${st(f1(S.cycle_s), "Cycle", "s", S.cycle_iqr ? `IQR ${S.cycle_iqr[0]}–${S.cycle_iqr[1]} s` : "")}${st(pc(S.on_move), "Shots on move")}${st(f1(S.dist_med), "Shot dist", "m", "median distance to hub")}</div></div>
      <div class="sx-card" data-sx="autoclimb"><h2>Auto &amp; climb</h2><div class="sx-stat">
        ${autoBad ? st("n/t", "Auto made", "", (TR.flags.find(f => f.k === "auto_not_tracked") || {}).text) + st("n/t", "Auto shots") : st(TV.auto_made ? f1(TV.auto_made.v) : f1(A.made_avg), "Auto made", "", TV.auto_made ? `the same number as the team page and Teams (shrunk toward the event, n=${TV.auto_made.n}); the plain mean of the rows below is ${f1(A.made_avg)}` : "mean over the reconciled matches below") + st(f1(A.att_avg), "Auto shots", "", "mean over the reconciled matches below")}${st(pc(A.climb_rate), "Auto L1 (TBA)")}
        ${st(pc(CL.rate), "Endgame climb (TBA)", "", `${CL.n ?? nL} quals · levels: ${Object.entries(lvls).map(([k, v]) => `L${k}×${v}`).join(", ")}`)}</div>
        ${autoBad ? `<div class="sx-mini" style="margin-top:6px;color:var(--caution)">${H((TR.flags.find(f => f.k === "auto_not_tracked") || {}).text)}</div>` : ""}
        ${autoBad ? "" : `<div class="sx-mini" style="margin-top:6px">${(A.rows || []).map(r => `${r.m}: ${r.made}/${r.att}${r.climb ? " + climb" : ""}`).join(" · ")}</div>`}
        ${(A.routines || []).length ? `<div class="sx-mini" style="margin-top:4px">Routines: ${A.routines.map(r => `${H(r.name)} ${r.n}/${r.of}`).join(", ")} — <a href="#/${ev}/t/${team}">paths on team page</a></div>` : ""}</div>
    </div>
    <div class="sx-grid" style="margin-top:14px">
      <div class="sx-card" data-sx="lanes"><h2>Lanes <small>${ownN} own-side crossings · trench ${pc(LN.trench_share)} · left ${pc(LN.left_share)}</small></h2>
        <div id="sxLane"></div>
        <div class="sx-leg"><span><i style="background:${LANE_COL.trench}"></i>trench</span><span><i style="background:${LANE_COL.bump}"></i>bump</span>
          ${LN.trench_capable === false ? "<span>no trench run seen</span>" : ""}${CS.help("Numbers = crossings per lane. Arrow ← = toward own wall, → = outbound.", "How to read the lanes")}</div></div>
      <div class="sx-card" data-sx="shots"><h2>Shooting locations <small id="sxShN"></small></h2>
        <div class="sx-ctl"><span class="chips" id="sxShP" style="margin:0"></span><span class="chips" id="sxShM" style="margin:0"></span></div>
        <div id="sxShot"></div>
        <div class="sx-leg"><span><i class="dot" style="background:${C_MADE}"></i>made</span><span><i class="dot" style="background:${C_MISS}"></i>missed</span>${CS.help("■ = launched on the move, ● = stopped. Own end on the left, hub ringed.", "How to read the shot map")}</div></div>
    </div>
    <div class="sx-card" data-sx="paths" style="margin-top:14px"><h2>Shooting paths ${CS.help(`<span class="hl">The drive leading into each volley, aligned on the first shot.</span><span class="hl">Lead-in starts at the latest of: previous volley, last floor intake, leaving the neutral zone (crossing back over the own hub line), a tracking hole, or 10 s. Moving = robot speed &gt; 0.5 m/s at the first shot.</span><span class="hl">Tap a row to highlight one path.</span>`, "How shooting paths work")}</h2>
      <div class="sx-ctl"><select id="sxPm" aria-label="Match"><option value="">all matches</option>${(sp.matches || []).map(m => `<option>${m.m}</option>`).join("")}</select>
        <select id="sxPp" aria-label="Period"><option value="tele">teleop</option><option value="auto">auto</option><option value="">all periods</option></select>
        <select id="sxPn" aria-label="Which launches"><option value="3">volleys ≥ 3</option><option value="1">all launches</option><option value="8">volleys ≥ 8</option></select>
        <select id="sxPs" aria-label="Playback speed"><option value="1">1×</option><option value="2">2×</option><option value="0.5">0.5×</option></select>
        <button id="sxPclr">clear selection</button></div>
      <div class="sx-grid"><div><div id="sxPath"></div>
        <div class="sx-leg"><span><i style="background:${LANE_COL.trench}"></i>came via trench</span><span><i style="background:${LANE_COL.bump}"></i>via bump</span>
          <span><i style="background:${LANE_COL.neutral}"></i>from neutral</span><span><i style="background:${LANE_COL.zone}"></i>inside zone</span><span><i style="background:#4ade80"></i>shooting</span><span>× first shot</span></div>
        <div id="sxAppr" style="margin-top:8px"></div></div>
        <div><div class="tablewrap" style="max-height:420px;overflow-y:auto"><table class="sx-tbl" id="sxPt"></table></div></div></div></div>
    <div class="sx-card" data-sx="defense" style="margin-top:14px"><h2>Defense <small><a href="#/${ev}/selection/defense/${team}">open in the defense tab</a></small></h2>
      <div class="sx-grid"><div><dl class="sx-kv">
        <dt>Defended (episodes)</dt><dd>${recv.n ?? 0} <small>(${recv.tested ?? 0} testable, ${recv.succ ?? 0} clearly reduced)</small></dd>
        <dt>Output when defended</dt><dd>${recv.ratio != null ? pc(recv.ratio) + " of own baseline" : "–"} ${recv.ci ? `<small>90% CI ${pc(recv.ci[0])}–${pc(recv.ci[1])}</small>` : ""}</dd>
        <dt>Baseline (free)</dt><dd>${DF.baseline && DF.baseline.pts_per_active_s != null ? f1(DF.baseline.pts_per_active_s * 60) + " pts / active min" : "–"} <small>${DF.baseline ? f1(DF.baseline.free_s, 0) + " s free" : ""}</small></dd>
        <dt>Played defense</dt><dd>${play ? `${play.n} episodes, ${f1(play.secs, 0)} s` : "none seen"}</dd>
        ${play ? `<dt>Victims' output</dt><dd>${play.ratio != null ? pc(play.ratio) + " of their baseline" : "–"} <small>${play.tested} testable, ${play.succ} clear, ${f1(play.denied)} pts denied</small></dd>` : ""}
      </dl></div><div><ul class="sx-adv">${(DF.advice || []).map(a => `<li>${H(a)}</li>`).join("") || "<li class='muted'>No defense data.</li>"}</ul></div></div></div>`;

    if (opt.only) {       // a field's page: just its cards (the rest stay in the DOM so the drawing code below finds its canvases)
      const keep = new Set(opt.only);
      root.querySelectorAll(".sx-head,.sc-banner").forEach(x => { x.style.display = "none"; });
      root.querySelectorAll("[data-sx]").forEach(x => { if (!keep.has(x.dataset.sx)) x.style.display = "none"; });
      root.querySelectorAll(".sx-grid,.sx-grid3").forEach(g => { if (![...g.children].some(c => c.style.display !== "none")) g.style.display = "none"; });
      root.querySelectorAll("[data-sx]").forEach(x => { x.style.marginTop = "0"; });
    }
    // lanes
    const LF = fieldCanvas(document.getElementById("sxLane"));
    const drawLane = () => { LF.size(); laneDiagram(LF, sp); };
    // shot map
    let sper = "all", smode = "both";
    const SF = fieldCanvas(document.getElementById("sxShot"));
    const drawShot = () => {
      SF.size();
      const pts = (sp.shots || []).filter(p => sper === "all" || (sper === "auto" ? p[3] === "a" : sper === "tele" ? p[3] !== "a" : sper === "move" ? p[5] === 1 : p[5] === 0));
      shotMap(SF, pts, smode);
      const mm = pts.filter(p => p[2] === "m").length, xx = pts.filter(p => p[2] === "x").length;
      document.getElementById("sxShN").textContent = `${pts.length} shots · ${mm} made · ${xx} missed · all ${(sp.matches || []).length} verified matches`;
    };
    chipRow(document.getElementById("sxShP"), [["all", "All"], ["auto", "Auto"], ["tele", "Teleop"], ["stop", "Stopped"], ["move", "Moving"]], sper, k => { sper = k; drawShot(); });
    chipRow(document.getElementById("sxShM"), [["both", "Points+density"], ["pts", "Points"], ["heat", "Density"]], smode, k => { smode = k; drawShot(); });
    const redraw = () => { drawLane(); drawShot(); };
    redraw(); FIELD.complete || FIELD.addEventListener("load", redraw, { once: true });
    window.addEventListener("resize", redraw); cleanup.push(() => window.removeEventListener("resize", redraw));

    // paths
    let sel = null;
    const allP = sp.paths || [];
    const filt = () => {
      const m = document.getElementById("sxPm").value, p = document.getElementById("sxPp").value, n = +document.getElementById("sxPn").value;
      return allP.filter(x => x.hub > 0 && x.n >= n && (!m || x.m === m) && (!p || (p === "auto" ? x.per === "auto" : x.per !== "auto")));
    };
    let cur = filt();
    const anim = pathsAnim(document.getElementById("sxPath"), () => cur, () => sel);
    const table = () => {
      const t = document.getElementById("sxPt");
      t.innerHTML = `<thead><tr><th>Match</th><th class="num">Clock</th><th class="num" data-pri="800">Made/n</th><th data-pri="300">From</th><th class="num" data-pri="500">Lead s</th><th data-pri="400">Start</th><th data-pri="450">Approach</th><th data-pri="600"><span class="vh">Video</span></th></tr></thead>
        <tbody>${cur.map((p, i) => `<tr class="clk${sel === p ? " sel" : ""}" data-i="${i}"><td>${p.m}</td><td class="num">${f1(p.t)}</td><td class="num">${p.made}/${p.hub}</td>
        <td>${H(p.from)}</td><td class="num">${f1(p.lead_s)}</td><td>${p.moving == null ? "–" : p.moving ? "moving" : "stopped"}</td>
        <td><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${LANE_COL[apprKey(p)]}"></i> ${apprTxt(p)}</td>
        <td><a href="#/${ev}/a/${p.key}?t=${Math.max(0, p.vt - p.lead_s - 1).toFixed(1)}" title="open the video at the start of the lead-in">video</a></td></tr>`).join("")
        || `<tr><td colspan="8" class="muted">No volleys for this filter.</td></tr>`}</tbody>`;
      t.querySelectorAll("tr.clk").forEach(tr => tr.onclick = ev_ => { if (ev_.target.tagName === "A") return; const p = cur[+tr.dataset.i]; sel = sel === p ? null : p; table(); anim.refresh(); });
      const ac = {}; cur.forEach(p => { const k = apprTxt(p); ac[k] = (ac[k] || 0) + 1; });
      const mv = cur.filter(p => p.moving != null), mvn = mv.filter(p => p.moving).length;
      const ent = Object.entries(ac).sort((a, b) => b[1] - a[1]), mx = Math.max(1, ...ent.map(x => x[1]));
      document.getElementById("sxAppr").innerHTML = bars(ent.map(([k, v]) => [k, v, mx, `${v}`, LANE_COL[k.startsWith("trench") ? "trench" : k.startsWith("bump") ? "bump" : k === "from neutral" ? "neutral" : "zone"]]))
        + `<div class="sx-mini" style="margin-top:6px">${cur.length} volleys · first shot on the move in ${mv.length ? pc(mvn / mv.length) : "–"} · median lead-in ${cur.length ? f1(cur.map(p => p.lead_s).sort((a, b) => a - b)[cur.length >> 1]) : "–"} s</div>`;
    };
    ["sxPm", "sxPp", "sxPn"].forEach(id => document.getElementById(id).onchange = () => { cur = filt(); sel = null; table(); anim.refresh(); });
    document.getElementById("sxPs").onchange = e_ => anim.setSpeed(+e_.target.value);
    document.getElementById("sxPclr").onclick = () => { sel = null; table(); anim.refresh(); };
    table();
  }

  CS.specPage = specPage;
})();

// ====================================================================================================================
// TEAMS (#/<ev>/teams[/<team>[/<field>]]): a Statbotics-style quick look per team. One row per team (rank, record, EPA and
// its auto / teleop / endgame parts, our model rating +- sd with its coverage label, four headline tracked stats). Click a
// row and it expands into every field we track for that team, grouped; each field opens as its own page
// (#/<ev>/teams/<team>/<field>) that reuses the team page's sections (quant.js teamPage) and the spec sheet's cards
// (specPage above) instead of re-drawing them. Data: scout.json (video metrics {v,raw,n,tier}, rating, climb), dashboard.json
// ratings[] (EPA parts, blend, projection), event.json teams[] (rank, q), rankings.json (fallback rank / record).
// ====================================================================================================================
(function () {
  const H = s => esc(s);
  const tn = k => String(k || "").replace("frc", "");
  const TEAM_RE = /^frc\d{1,5}$/;

  const CSS = `
  .tm { --g-rating: var(--accent); --g-scoring: var(--good); --g-auto: var(--caution); --g-collect: color-mix(in srgb, var(--accent) 55%, var(--ink-2)); --g-move: color-mix(in srgb, var(--good) 50%, var(--accent));
        --g-def: color-mix(in srgb, var(--caution) 55%, var(--bad)); --g-end: color-mix(in srgb, var(--accent) 40%, var(--caution)); --g-rel: var(--ink-3); }
  .tm-top { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--sp-3) var(--sp-5); flex-wrap: wrap; margin-bottom: var(--sp-3); }
  .tm-top h1 { margin: 0; } .tm-sub { margin-top: 2px; color: var(--ink-2); font-size: var(--fs-foot); }
  .tm-us { display: inline-flex; align-items: baseline; gap: 6px; flex-wrap: wrap; margin-top: var(--sp-2); padding: 6px 12px; border-radius: 99px; background: var(--accent-tint); border: 1px solid var(--accent-line);
    font-size: var(--fs-foot); color: var(--ink); } .tm-us b { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: var(--fs-callout); }
  .tm-ctl { display: flex; gap: var(--sp-2) var(--sp-3); align-items: center; flex-wrap: wrap; margin: var(--sp-3) 0; }
  .tm-ctl .tm-q { flex: 1 1 220px; max-width: 340px; } .tm-ctl .csin { min-height: 38px; width: 100%; }
  .tm-ctl .tm-sort { display: none; } .tm-ctl .tm-sort select { min-height: 38px; }
  .tm-n { color: var(--ink-2); font-size: var(--fs-foot); margin-left: auto; }
  .tm-list { display: flex; flex-direction: column; gap: 6px; }
  .tm-grid { display: grid; align-items: center; gap: 0 10px; grid-template-columns: 46px minmax(150px,1.5fr) 74px minmax(130px,1.25fr) minmax(130px,1.1fr) 66px 56px 58px 56px 26px; }
  .tm-head { position: sticky; top: var(--tb-h, 0px); z-index: 5; padding: 6px 14px; background: var(--bg); border-bottom: 1px solid var(--line-strong); font-size: var(--fs-cap); font-weight: 600; color: var(--ink-2); }
  html[data-layout="topbar"] .tm-head { top: calc(56px + var(--tb-h, 56px)); }
  .tm-head button { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 3px; padding: 4px 0; border-radius: var(--r-tag); }
  .tm-head button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .tm-head button:hover { color: var(--ink); } .tm-head [aria-sort] { color: var(--ink); }
  .tm-head .r, .tm .r { text-align: right; justify-content: flex-end; }
  .tm-head .arr { font-size: 9px; opacity: .8; }
  .tm-item { scroll-margin-top: 110px; position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-m); overflow: hidden; transition: border-color .15s var(--ease), box-shadow .15s var(--ease); }
  .tm-item::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: transparent; }
  .tm-item.us { border-color: var(--accent-line); background: color-mix(in srgb, var(--accent-tint) 55%, var(--surface)); } .tm-item.us::before { background: var(--accent-fill); }
  .tm-item:hover { border-color: var(--line-strong); } .tm-item.open { border-color: var(--accent); box-shadow: 0 6px 22px color-mix(in srgb, var(--ink) 9%, transparent); }
  .tm-row { position: relative; padding: 10px 14px; min-height: 52px; } .tm-row > .c { min-width: 0; }
  .tm-row .c-rank b { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: var(--fs-t3); font-variant-numeric: tabular-nums; color: var(--ink-2); }
  .tm-tog { all: unset; cursor: pointer; display: flex; align-items: baseline; gap: 8px; min-width: 0; max-width: 100%; }
  .tm-tog::after { content: ""; position: absolute; inset: 0; border-radius: var(--r-m); } .tm-tog:focus-visible::after { outline: 2px solid var(--accent); outline-offset: -2px; }
  .tm-num { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 21px; font-weight: 700; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }
  .tm-nick { color: var(--ink-2); font-size: var(--fs-foot); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .tm-tags { display: inline-flex; gap: 5px; margin-left: 6px; align-items: center; position: relative; z-index: 1; pointer-events: none; }
  .tm-tag { font-size: 10.5px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; padding: 1px 6px; border-radius: var(--r-tag); background: var(--accent-fill); color: var(--on-accent); }
  .tm-star { color: var(--caution); display: inline-flex; } .tm-star svg { width: 15px; height: 15px; }
  .tm-row .c-team { display: flex; align-items: center; min-width: 0; }
  .tm-row .num, .tm-row .c-rec, .tm-row .c-made, .tm-row .c-acc, .tm-row .c-cyc, .tm-row .c-clb { font-variant-numeric: tabular-nums; }
  .tm-row .c-made, .tm-row .c-acc, .tm-row .c-cyc, .tm-row .c-clb { text-align: right; font-size: var(--fs-body); }
  .tm-row .c-made .sc-v small, .tm-row .c-acc .sc-v small, .tm-row .c-cyc .sc-v small, .tm-row .c-clb .sc-v small { display: block; font-size: 10.5px; color: var(--ink-3); line-height: 1.1; }
  .tm-row .c-rec { color: var(--ink-2); font-size: var(--fs-foot); }
  .tm-big { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.05; }
  .tm-big small { font-size: var(--fs-cap); font-weight: 500; color: var(--ink-2); margin-left: 3px; }
  .tm-epa .tm-bar { display: flex; height: 6px; margin-top: 5px; border-radius: 3px; overflow: hidden; background: var(--surface-3); }
  .tm-epa .tm-bar i { display: block; min-width: 0; } .tm-bar .a { background: var(--caution); } .tm-bar .t { background: var(--accent); } .tm-bar .e { background: var(--good); }
  .tm-bar-w { display: block; } .tm-epa .tm-bar-w .tm-bar { transform-origin: left; }
  .tm-rate .sc-cov { position: relative; z-index: 1; margin-top: 3px; }
  .c-stats { display: contents; } .c-chev { color: var(--ink-3); display: grid; place-items: center; transition: transform .2s var(--ease); } .c-chev svg { width: 18px; height: 18px; }
  .tm-item.open .c-chev { transform: rotate(90deg); color: var(--accent); }
  .tm-panel { padding: 4px 14px 16px; border-top: 1px dashed var(--line-strong); }
  .tm-ph { display: flex; align-items: center; gap: var(--sp-2) var(--sp-3); flex-wrap: wrap; padding: 10px 0 4px; }
  .tm-ph .sp { flex: 1; } .tm-ph a.tm-all { font-weight: 600; text-decoration: none; padding: 6px 12px; border-radius: 99px; background: var(--accent-fill); color: var(--on-accent); font-size: var(--fs-foot); }
  .tm-ph a.tm-all { display: inline-flex; align-items: center; gap: 4px; } .tm-ph a.tm-all svg { width: 14px; height: 14px; }
  .tm-ph a.tm-all:hover { filter: brightness(1.08); } .tm-ph a.alt { background: none; color: var(--ink); border: 1px solid var(--line-strong); }
  .tm-ph .flags { display: inline-flex; gap: 6px; flex-wrap: wrap; }
  .tm-grp { margin-top: var(--sp-3); } .tm-gh { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; font-size: var(--fs-foot); font-weight: 700; letter-spacing: .02em; color: var(--ink); }
  .tm-gh i { width: 10px; height: 10px; border-radius: 3px; background: var(--gc, var(--accent)); flex: none; } .tm-gh .muted { font-weight: 500; }
  .tm-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 8px; }
  .tf { position: relative; display: flex; flex-direction: column; gap: 2px; padding: 9px 11px 11px; border-radius: var(--r-p); border: 1px solid var(--line); background: var(--surface); color: var(--ink); text-decoration: none; overflow: hidden;
    transition: border-color .15s var(--ease), transform .15s var(--ease), background-color .15s var(--ease); }
  .tf::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--gc, var(--accent)); }
  .tf:hover { border-color: var(--gc, var(--accent)); background: var(--surface-2); transform: translateY(-1px); } .tf:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .tf-l { font-size: var(--fs-cap); font-weight: 600; color: var(--ink-2); display: flex; align-items: center; gap: 4px; }
  .tf-l svg { width: 12px; height: 12px; margin-left: auto; opacity: .5; }
  .tf-v { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 24px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
  .tf-v small { font-size: var(--fs-cap); font-weight: 500; color: var(--ink-2); margin-left: 3px; } .tf-v .muted { font-size: 18px; }
  .tf-n { font-size: 11.5px; color: var(--ink-2); line-height: 1.3; } .tf-n.grey { color: var(--caution); }
  .tf-pc { height: 4px; border-radius: 2px; background: var(--surface-3); margin-top: 6px; overflow: hidden; } .tf-pc i { display: block; height: 100%; background: var(--gc, var(--accent)); border-radius: 2px; }
  .tf-pc.grey i { opacity: .4; background-image: repeating-linear-gradient(90deg, transparent 0 4px, var(--surface) 4px 6px); }
  .tm-note { margin-top: var(--sp-3); font-size: var(--fs-cap); color: var(--ink-2); }
  @media (max-width: 1060px) { .tm-grid { grid-template-columns: 46px minmax(140px,1.5fr) minmax(120px,1.2fr) minmax(120px,1.1fr) 64px 54px 56px 26px; } .tm .c-rec, .tm .c-cyc, .tm-head .h-rec, .tm-head .h-cyc { display: none; } }
  @media (max-width: 720px) {
    .tm-head { display: none; } .tm-ctl .tm-sort { display: flex; align-items: center; gap: 6px; font-size: var(--fs-foot); color: var(--ink-2); } .tm-n { margin-left: 0; flex-basis: 100%; }
    .tm-grid.tm-row { grid-template-columns: 34px minmax(0,1fr) auto 22px; grid-template-areas: "rank team rate chev" "rank epa epa epa" "rank stats stats stats"; gap: 4px 10px; padding: 10px 12px; }
    .tm .c-rec { display: none; } .tm .c-rank { grid-area: rank; align-self: start; padding-top: 3px; } .tm .c-team { grid-area: team; } .tm .c-rate { grid-area: rate; text-align: right; } .tm .c-chev { grid-area: chev; }
    .tm .c-epa { grid-area: epa; display: flex; align-items: center; gap: 10px; } .tm .c-epa::before { content: "EPA"; font-size: var(--fs-cap); color: var(--ink-2); font-weight: 600; } .tm-epa .tm-bar-w { flex: 1; } .tm-epa .tm-bar { margin-top: 0; }
    .c-stats { display: grid; grid-area: stats; grid-template-columns: repeat(4, 1fr); gap: 6px; } .tm .c-cyc { display: block; }
    .tm .c-stats > span { text-align: left; background: var(--surface-2); border-radius: var(--r-s); padding: 3px 7px 4px; font-size: var(--fs-foot); } .tm .c-stats > span::before { content: attr(data-l); display: block; font-size: 10.5px; color: var(--ink-3); font-weight: 600; }
    .tm-big { font-size: 20px; } .tm-nick { font-size: var(--fs-cap); } .tm-tags { margin-left: 4px; }
    .tm-panel { padding: 2px 12px 14px; } .tm-tiles { grid-template-columns: repeat(2, minmax(0,1fr)); } .tf-v { font-size: 22px; }
  }
  @media (pointer: coarse) { .tm-head button { min-height: 36px; } }
  @media (prefers-reduced-motion: reduce) { .tf, .c-chev, .tm-item { transition: none; } .tf:hover { transform: none; } }
  /* ---- a field's own page */
  .fp { display: flex; flex-direction: column; gap: var(--sp-4); }
  .fp-head { position: relative; padding: var(--sp-4) var(--sp-5); border-radius: var(--r-m); border: 1px solid var(--line); background: var(--surface); overflow: hidden; }
  .fp-head::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 6px; background: var(--gc, var(--accent)); }
  .fp-kick { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: var(--fs-foot); color: var(--ink-2); } .fp-kick a { color: var(--ink-2); font-weight: 600; text-decoration: none; } .fp-kick a:hover { color: var(--ink); text-decoration: underline; }
  .fp-kick .gp { font-weight: 700; color: var(--ink); display: inline-flex; align-items: center; gap: 6px; } .fp-kick .gp i { width: 10px; height: 10px; border-radius: 3px; background: var(--gc, var(--accent)); }
  .fp-title { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--sp-3) var(--sp-5); flex-wrap: wrap; margin-top: var(--sp-2); }
  .fp-title h1 { margin: 0; font-size: var(--fs-t1); line-height: 1.1; } .fp-title h1 small { display: block; font-size: var(--fs-foot); font-weight: 500; color: var(--ink-2); margin-top: 3px; }
  .fp-title h1 small { font-family: var(--font); font-stretch: 100%; letter-spacing: 0; }
  .fp-val { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: var(--fs-disp); font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
  .fp-val small { font-size: var(--fs-callout); font-weight: 500; color: var(--ink-2); margin-left: 5px; letter-spacing: 0; } .fp-val .muted { font-size: 32px; }
  .fp-meta { display: flex; align-items: center; gap: var(--sp-2) var(--sp-3); flex-wrap: wrap; margin-top: var(--sp-3); font-size: var(--fs-foot); color: var(--ink-2); }
  .fp-how { margin-top: var(--sp-2); font-size: var(--fs-foot); color: var(--ink-2); max-width: 78ch; line-height: 1.5; }
  .fp-acts { display: flex; gap: var(--sp-2); flex-wrap: wrap; margin-top: var(--sp-3); } .fp-acts a { font-size: var(--fs-foot); font-weight: 600; text-decoration: none; padding: 6px 12px; border-radius: 99px; border: 1px solid var(--line-strong); color: var(--ink); background: var(--surface); }
  .fp-acts a.main { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); } .fp-acts a:hover { filter: brightness(.97); border-color: var(--accent); }
  .fp-cols { display: grid; grid-template-columns: minmax(0,1.15fr) minmax(0,1fr); gap: var(--sp-4); } .fp-cols > * { min-width: 0; }
  .fp-cols > :only-child { grid-column: 1 / -1; }
  .fp-card { padding: var(--sp-4); border-radius: var(--r-m); border: 1px solid var(--line); background: var(--surface); } .fp-card h2 { margin: 0 0 var(--sp-2); font-size: var(--fs-callout); display: flex; align-items: center; gap: 6px; }
  .fp-pm { display: flex; align-items: flex-end; gap: 4px; height: 128px; overflow-x: auto; padding: 4px 2px 0; scrollbar-width: thin; }
  .fp-pm .b { flex: 1 0 22px; max-width: 54px; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; height: 100%; gap: 3px; font-size: 10.5px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .fp-pm .b i { display: block; width: 100%; border-radius: 4px 4px 0 0; background: var(--gc, var(--accent)); min-height: 2px; } .fp-pm .b em { font-style: normal; font-weight: 600; color: var(--ink); } .fp-pm .b span { white-space: nowrap; }
  .fp-strip { position: relative; height: 54px; margin: 18px 6px 4px; } .fp-strip .ln { position: absolute; left: 0; right: 0; top: 30px; height: 2px; background: var(--line-strong); border-radius: 1px; }
  .fp-strip .d { position: absolute; top: 25px; width: 10px; height: 10px; margin-left: -5px; border-radius: 50%; background: var(--ink-3); opacity: .45; }
  .fp-strip .me { position: absolute; top: 20px; width: 20px; height: 20px; margin-left: -10px; border-radius: 50%; background: var(--gc, var(--accent)); border: 3px solid var(--surface); box-shadow: 0 0 0 2px var(--gc, var(--accent)); }
  .fp-strip .lab { position: absolute; top: -6px; transform: translateX(-50%); font-size: var(--fs-cap); font-weight: 700; white-space: nowrap; color: var(--ink); background: var(--surface); padding: 0 4px; }
  .fp-strip .ax { position: absolute; top: 40px; font-size: 10.5px; color: var(--ink-3); } .fp-strip .ax.l { left: 0; } .fp-strip .ax.r { right: 0; }
  .fp-sib { display: flex; flex-direction: column; gap: 8px; } .fp-sib .row { display: flex; flex-wrap: wrap; gap: 5px 6px; align-items: center; } .fp-sib .gl { font-size: var(--fs-cap); font-weight: 700; color: var(--ink-2); min-width: 78px; }
  .fp-sib a { font-size: var(--fs-cap); padding: 3px 10px; border-radius: 99px; border: 1px solid var(--line-strong); color: var(--ink-2); text-decoration: none; background: var(--surface); } .fp-sib a:hover { color: var(--ink); background: var(--surface-2); }
  .fp-sib a[aria-current] { background: var(--accent-tint); border-color: var(--accent); color: var(--ink); font-weight: 700; }
  .fp-sec { display: flex; flex-direction: column; gap: var(--sp-3); } .fp-sec > .fp-cap { margin: 0; font-size: var(--fs-foot); font-weight: 700; color: var(--ink-2); display: flex; align-items: center; gap: 8px; }
  .fp-sec > .fp-cap::after { content: ""; flex: 1; height: 1px; background: var(--line); }
  .fp-kv { display: grid; grid-template-columns: auto minmax(0,1fr); gap: 6px 14px; margin: 0; font-size: var(--fs-body); } .fp-kv dt { color: var(--ink-2); } .fp-kv dd { margin: 0; font-variant-numeric: tabular-nums; }
  .fp-stack { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: var(--surface-3); margin: 6px 0 4px; } .fp-stack i { display: block; }
  .fp-leg { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: var(--fs-cap); color: var(--ink-2); } .fp-leg span { display: inline-flex; align-items: center; gap: 5px; } .fp-leg i { width: 9px; height: 9px; border-radius: 2px; }
  .fp .sc-v { font-variant-numeric: tabular-nums; }
  @media (max-width: 820px) { .fp-cols { grid-template-columns: minmax(0,1fr); } .fp-head { padding: var(--sp-4); } .fp-val { font-size: 44px; } .fp-sib .gl { flex-basis: 100%; } }
  `;
  function css() { if (!document.getElementById("tm-css")) { const s = document.createElement("style"); s.id = "tm-css"; s.textContent = CSS; document.head.appendChild(s); } }

  // ---- fields: everything we track for a team, grouped. get(R) -> {v, raw?, n?, tier?, lower_bound?} | null.
  // src says where it comes from (the label under the value): video (verified matches), tba (every played qual), model, ev (event data).
  // sec = team-page sections (quant.js) and sx = spec-sheet cards (specPage) the field's own page reuses; pm = the per-match value from a
  // match's analytics. how = what it is and how it is computed.
  const GROUPS = [["rating", "Rating", "--g-rating"], ["scoring", "Scoring", "--g-scoring"], ["auto", "Auto", "--g-auto"], ["collect", "Collection", "--g-collect"],
    ["move", "Movement", "--g-move"], ["def", "Defense", "--g-def"], ["end", "Endgame", "--g-end"], ["rel", "Reliability", "--g-rel"]];
  const V = k => R => R.sc && R.sc.video ? R.sc.video[k] : null;
  const FIELDS = [
    { id: "rating", g: "rating", label: "Our model rating", src: "model", f: "1", unit: " pts/match", get: R => R.blend ? { v: R.blend.total, sd: R.blend.sd } : null, custom: "rating",
      how: "Expected points per match for this team's alliance contribution: a blend of TBA results at this event, earlier events (prior) and verified robot video, schedule-neutral. The ± is one standard deviation; the weights of the three sources are on the field page." },
    { id: "epa", g: "rating", label: "EPA", src: "ev", f: "1", unit: " pts", get: R => R.epa ? { v: R.epa.total } : null, custom: "epa",
      how: "Expected Points Added (Statbotics-style): a running estimate of the points a team adds per match, updated after every match from official scores. Computed locally from this event's results up to now, split into auto, teleop and endgame." },
    { id: "opr", g: "rating", label: "OPR", src: "ev", f: "1", get: R => R.sc && R.sc.rating && R.sc.rating.opr != null ? { v: R.sc.rating.opr } : R.opr != null ? { v: R.opr } : null, custom: "opr",
      how: "Offensive Power Rating: a least-squares solve over every qual alliance score at this event, giving each team's average point contribution. Noisy with few matches, and it cannot separate teammates who always play together." },
    { id: "rank", g: "rating", label: "Rank & record", src: "ev", f: "0", lower: true, get: R => R.rank != null ? { v: R.rank } : null, custom: "rank",
      how: "Qualification rank, win-loss-tie record and ranking points from TBA, with the simulated final-rank range and chance of finishing in the top 8 / top 4." },
    { id: "made", g: "scoring", label: "Made / match", src: "video", f: "1", get: V("made_pm"), sec: ["scoring"], sx: ["scoring"], pm: a => a.scoring && a.scoring.made, pmLabel: "FUEL made",
      how: "FUEL scored per match on verified robot video, reconciled with TBA's alliance counts and shrunk toward the event average (a floor, marked ≥, when much of the alliance's FUEL was not credited to any robot)." },
    { id: "acc", g: "scoring", label: "Accuracy", src: "video", f: "p", get: V("acc"), sec: ["scoring", "shots"], sx: ["shots"], pm: a => a.scoring && a.scoring.accuracy, pmLabel: "accuracy",
      how: "Made / (made + missed) over verified matches, shrunk toward the event with 20 pseudo-shots." },
    { id: "pts_min", g: "scoring", label: "Pts / active minute", src: "video", f: "1", get: V("pts_active_min"), sec: ["scoring"], sx: ["scoring"],
      how: "FUEL made into an ACTIVE hub per minute of teleop time while its own hub was active." },
    { id: "cycle", g: "scoring", label: "Cycle time", src: "video", f: "1", unit: " s", lower: true, get: V("cycle_s"), sec: ["scoring"], sx: ["paths"], pm: a => a.scoring && a.scoring.cycle_s, pmLabel: "seconds",
      how: "Median time between the starts of its teleop volleys (lower is faster)." },
    { id: "fire", g: "scoring", label: "Fire rate", src: "video", f: "1", unit: " /s", get: V("fire"), sec: ["scoring"], sx: ["scoring"], pm: a => a.scoring && a.scoring.fire_rate, pmLabel: "balls / s",
      how: "FUEL per second inside a volley of 3 or more." },
    { id: "hopper", g: "scoring", label: "Volley size (hopper)", src: "video", f: "0", get: V("hopper"), sec: ["scoring"], sx: ["scoring"], pm: a => a.scoring && a.scoring.volley_size, pmLabel: "balls",
      how: "90th percentile volley size: roughly the hopper capacity." },
    { id: "shotmap", g: "scoring", label: "Shot map", src: "video", f: "0", unit: " shots", get: R => { const a = R.sc && R.sc.video && R.sc.video.acc; return a && a.shots != null ? { v: a.shots, n: a.n, tier: a.tier } : null; },
      sec: ["shots"], sx: ["shots", "paths"], how: "Where it launched from, in its own half of the field (own wall on the left, hub ringed), with made / missed / pass dots and density, plus the drive paths into each volley." },
    { id: "auto_made", g: "auto", label: "Auto FUEL", src: "video", f: "1", get: V("auto_made"), sec: ["auto"], sx: ["autoclimb"], pm: a => a.auto && a.auto.made, pmLabel: "auto FUEL",
      how: "FUEL made in the autonomous period, per match on verified video (hidden when it contradicts TBA), with its auto routines and paths." },
    { id: "intakes", g: "collect", label: "Intakes / match", src: "ev", f: "1", get: R => R.q && R.q.intakes != null ? { v: R.q.intakes } : null, sec: ["intake"], pm: a => a.collection && a.collection.intakes, pmLabel: "intakes",
      how: "Floor pick-ups per match from the tracker, with where on the field they happened." },
    { id: "speed", g: "move", label: "Top speed (p95)", src: "video", f: "1", unit: " m/s", get: V("speed_p95"), sec: ["move"], sx: ["drive"], pm: a => a.movement && a.movement.p95_speed, pmLabel: "m/s",
      how: "95th percentile of its moving speed on verified video." },
    { id: "trench", g: "move", label: "Trench share", src: "video", f: "p", get: R => { const t = R.sc && R.sc.video && R.sc.video.trench; return t != null ? (typeof t === "object" ? t : { v: t }) : null; }, sec: ["move"], sx: ["lanes"],
      how: "Share of its crossings of the hub line that went through a trench (rather than a bump or open ground)." },
    { id: "on_move", g: "move", label: "Shoots on the move", src: "video", f: "p", get: R => { const t = R.sc && R.sc.video && R.sc.video.on_move; return t != null ? (typeof t === "object" ? t : { v: t }) : null; }, sec: ["shots"], sx: ["shots"],
      how: "Share of its shots launched while moving faster than 0.5 m/s." },
    { id: "def_played", g: "def", label: "Defense played", src: "video", f: "0", unit: " episodes", get: R => { const t = R.sc && R.sc.video && R.sc.video.def_played; return t != null ? { v: t } : null; }, sec: ["def"], sx: ["defense"],
      defNote: true, how: "Episodes where it stayed within 1.5 m of an opponent in that opponent's scoring area for 2 s or more. How much that cost the opponent is an assumption, not a measurement (see Selection, Defense tab)." },
    { id: "def_recv", g: "def", label: "Defense received", src: "video", f: "0", unit: " episodes", get: R => { const t = R.sc && R.sc.video && R.sc.video.def_received; return t != null ? { v: t } : null; }, sec: ["def"], sx: ["defense"],
      defNote: true, how: "Episodes where an opponent defended this team, with the share of its output it kept (an assumption, not measured, until a team's own estimate is significant)." },
    { id: "climb", g: "end", label: "Climb rate", src: "tba", f: "p", get: R => { const c = R.sc && R.sc.climb; return c && c.rate != null ? { v: c.rate, raw: c.rate, n: c.n, tier: c.n >= 3 ? "fact" : "grey" } : null; }, sec: ["end"], sx: ["autoclimb"], pm: a => a.endgame && a.endgame.level, pmLabel: "climb level",
      how: "Share of played quals in which TBA credits the robot with a tower climb (every played qual, not only the tracked ones)." },
    { id: "dead", g: "rel", label: "Dead matches", src: "video", f: "p", lower: true, get: R => { const d = R.sc && R.sc.video && R.sc.video.dead; return d && d.n >= 3 ? d : null; }, sec: ["rel"], pm: a => a.reliability && a.reliability.dead ? 1 : 0, pmLabel: "dead (1) or not (0)",
      how: "Share of verified matches in which the robot did not move for 15 s or more mid-match while not climbing. Shown only with at least 3 verified matches." },
  ];
  const FBY = Object.fromEntries(FIELDS.map(f => [f.id, f]));
  const GBY = Object.fromEntries(GROUPS.map(g => [g[0], g]));
  CS.teamFields = FIELDS;

  // ---- data
  const mv = x => x == null ? null : typeof x === "object" ? x : { v: x };
  const fmtN = (v, f) => typeof fmtV === "function" ? fmtV(v, f) : String(v);
  const DATA = {};
  /** one row per team: event.json teams (all of them) + scout.json + dashboard ratings + rankings (fallbacks) */
  function teamsData(ev) {
    return DATA[ev] && Date.now() - DATA[ev].at < 4000 ? DATA[ev].p : (DATA[ev] = { at: Date.now(), p: (async () => {
      const [e, sd, db, rk] = await Promise.all([api(`${ev}/event.json`), scoutData(ev), api(`${ev}/dashboard.json`).catch(() => null), api(`${ev}/rankings.json`).catch(() => null)]);
      const dr = Object.fromEntries(((db && db.ratings) || []).map(r => [r.team, r])), rr = Object.fromEntries(((rk && rk.teams) || []).map(r => [r.team, r]));
      const rows = (e.teams || []).map(t => {
        const sc = sd && sd.by[t.key] || null, d = dr[t.key] || null, r = rr[t.key] || null, rt = sc && sc.rating || {};
        const epa = d && d.epa ? d.epa : rt.epa != null ? { total: rt.epa } : null;
        const blend = d && d.blend ? d.blend : rt.blend != null ? { total: rt.blend, auto: rt.auto, teleop: rt.teleop, endgame: rt.endgame, sd: rt.sd } : null;
        return { key: t.key, number: t.number, nickname: t.nickname || "", rank: (r && r.rank) ?? (d && d.rank) ?? sc?.rank ?? t.rank ?? null, record: (d && d.record) || (r && r.record) || rt.record || "",
          rp: (r && r.rp) ?? (d && d.rp) ?? null, played: (d && d.matches_played) ?? (r && r.matches_played) ?? null, epa, blend, d, r, sc, q: t.q || (sc && sc.q) || null, opr: t.opr ?? null,
          coverage: (d && d.coverage) || (sc && sc.coverage) || null, proj: (d && d.projection) || (r && r.projection) || null };
      });
      return { ev, e, sd, db, rows, by: Object.fromEntries(rows.map(r => [r.key, r])), team: (e && e.team) || (db && db.team) || "frc4911",
        maxEpa: Math.max(1, ...rows.map(r => r.epa ? r.epa.total || 0 : 0)) };
    })() }).p;
  }
  const cache = {};     // per-ev: field id -> sorted values (for percentiles and the strip chart)
  function dist(D, F) {
    const k = D.ev + ":" + F.id;
    if (cache[k] && cache[k].rows === D.rows) return cache[k];
    const pts = D.rows.map(r => { const m = mv(F.get(r)); return m && has(m.v) && (m.n == null || m.n > 0) ? { key: r.key, v: m.v, grey: m.tier === "grey" || (m.n != null && m.n < 3) } : null; }).filter(Boolean);
    return (cache[k] = { rows: D.rows, pts, sorted: pts.map(p => p.v).sort((a, b) => a - b) });
  }
  /** share of the event this team beats on the field (0..1; 1 = best, whichever way is better), null when it has no value */
  function pctile(D, F, R) {
    const m = mv(F.get(R)); if (!m || !has(m.v)) return null;
    const s = dist(D, F).sorted; if (s.length < 3) return null;
    const below = s.filter(x => x < m.v).length + s.filter(x => x === m.v).length / 2;
    const p = below / s.length; return F.lower ? 1 - p : p;
  }

  // ---- how a value reads
  function valHtml(F, m, big) {
    if (!m || !has(m.v)) return `<span class="muted">–</span>`;
    const unit = F.unit && !big ? F.unit : F.unit ? F.unit : "";
    if (F.id === "rating") return `${fmtN(m.v, "1")}${has(m.sd) ? `<small>±${fmtN(m.sd, "0")}</small>` : ""}`;
    if (m.n != null && m.n === 0) return `<span class="muted">–</span>`;
    const body = `${m.lower_bound ? "≥" : ""}${fmtN(m.v, F.f)}`;
    return `${body}${unit ? `<small>${H(unit.trim())}</small>` : ""}`;
  }
  /** the line under a value: where it comes from and how much it rests on (the honesty label; "grey" = under 3 matches) */
  function noteOf(F, R, m) {
    if (!m || !has(m.v)) return { t: F.src === "video" ? (R.sc && R.sc.coverage && R.sc.coverage.n ? "not enough in video" : "no verified video") : "no data", grey: true };
    if (F.id === "rating") return { t: R.coverage && R.coverage.label ? R.coverage.label : "model estimate", grey: !!(R.coverage && R.coverage.tier === "grey") };
    if (F.src === "video") { const n = m.n; return n == null ? { t: "verified video", grey: false } : n < 3 ? { t: `n=${n} verified, anecdote (under 3)`, grey: true } : { t: `n=${n} verified matches`, grey: false }; }
    if (F.src === "tba") return m.n != null ? { t: `TBA, ${m.n} qual${m.n === 1 ? "" : "s"}${m.n < 3 ? ", anecdote" : ""}`, grey: m.n < 3 } : { t: "TBA", grey: false };
    if (F.id === "epa") return { t: `${R.epa && R.epa.source === "local" ? "local, " : ""}TBA results only`, grey: false };
    if (F.id === "rank") return { t: R.record ? `${R.record}${R.rp != null ? `, ${fmtN(R.rp, "0")} RP` : ""}` : "TBA", grey: false };
    if (F.id === "opr") return { t: "TBA results, whole event", grey: false };
    return { t: F.src === "ev" ? "tracked matches" : "", grey: false };
  }

  // ---- the list
  const ST = {};        // per event: sort, search, open set
  const SORTS = [["rank", "Rank", 1], ["number", "Team", 1], ["rating", "Our rating", -1], ["epa", "EPA", -1], ["made", "Made/m", -1], ["acc", "Accuracy", -1], ["cycle", "Cycle", 1], ["climb", "Climb", -1]];
  const sortVal = (k, R) => k === "rank" ? R.rank : k === "number" ? R.number : k === "rating" ? (R.blend ? R.blend.total : null) : k === "epa" ? (R.epa ? R.epa.total : null)
    : (() => { const m = mv(FBY[k === "cycle" ? "cycle" : k].get(R)); return m && (m.n == null || m.n > 0) ? m.v : null; })();
  const cell = (id, R) => { const F = FBY[id], m = mv(F.get(R)); if (!m || !has(m.v) || (m.n === 0)) return `<span class="muted">–</span>`; const g = m.tier === "grey" || (m.n != null && m.n < 3);
    return `<span class="sc-v ${g ? "grey" : ""}">${m.lower_bound ? "≥" : ""}${fmtN(m.v, F.f)}${m.n != null ? `<small>n=${m.n}</small>` : ""}</span>`; };
  const epaBar = (D, R) => { const e = R.epa; if (!e) return ""; const a = Math.max(0, e.auto || 0), t = Math.max(0, e.teleop || 0), g = Math.max(0, e.endgame || 0), tot = a + t + g || 1;
    return `<span class="tm-bar-w"${tipA(`EPA ${fmtN(e.total, "1")}: auto ${fmtN(e.auto, "1")}, teleop ${fmtN(e.teleop, "1")}, endgame ${fmtN(e.endgame, "1")} points per match. The bar's length compares teams.`)}><span class="tm-bar" style="width:${Math.max(4, Math.round((e.total || 0) / D.maxEpa * 100))}%"><i class="a" style="flex:${a / tot}"></i><i class="t" style="flex:${t / tot}"></i><i class="e" style="flex:${g / tot}"></i></span></span>`; };

  function tilesFor(D, R, ev) {
    return GROUPS.map(([gid, gl, gv]) => {
      const fs = FIELDS.filter(f => f.g === gid); if (!fs.length) return "";
      return `<section class="tm-grp" style="--gc: var(${gv})" aria-label="${H(gl)}"><h4 class="tm-gh"><i aria-hidden="true"></i>${H(gl)}${gid === "def" ? ` <span class="muted">· effects are assumptions until measured</span>` : ""}</h4>
        <div class="tm-tiles">${fs.map(F => {
          const m = mv(F.get(R)), nt = noteOf(F, R, m), p = pctile(D, F, R);
          return `<a class="tf" href="#/${ev}/teams/${H(R.key)}/${F.id}"><span class="tf-l">${H(F.label)}${CS.icon("chevR")}</span><span class="tf-v">${valHtml(F, m)}</span>
            <span class="tf-n${nt.grey ? " grey" : ""}">${H(nt.t)}</span>${p != null ? `<span class="tf-pc${nt.grey ? " grey" : ""}" role="img" aria-label="better than ${Math.round(p * 100)}% of teams here"><i style="width:${Math.max(3, Math.round(p * 100))}%"></i></span>` : ""}</a>`; }).join("")}</div></section>`;
    }).join("");
  }
  function panelFor(D, R, ev, st) {
    const sc = R.sc, cov = R.coverage;
    return `<div class="tm-panel" id="tmp-${H(R.key)}" role="region" aria-label="All tracked fields for ${R.number}">
      <div class="tm-ph">${cov ? covPill(cov) : ""}<span class="flags">${sc ? flagChips(sc) : ""}</span><span class="sp"></span>
        <a class="tm-all alt" href="#/${ev}/selection/compare/${R.number}">Compare</a><a class="tm-all" href="#/${ev}/t/${H(R.key)}">All of ${R.number} ${CS.icon("chevR")}</a></div>
      ${tilesFor(D, R, ev)}
      <div class="tm-note">Each field opens as its own page. The thin bar under a value is where the team stands among this event's teams (faded and striped = under 3 verified matches).</div></div>`;
  }
  const TEAMS_HELP = `<span class="hl">Click a team to see every field we track for it; click a field to open it as its own page. Tap a column head to sort.</span>
    <span class="hl"><b>Our rating</b> and <b>EPA</b> are points per match. The rating blends TBA results, earlier events and verified robot video; &plusmn; is one standard deviation, and the pill shows how many verified video matches stand behind it.</span>
    <span class="hl">The EPA bar is split into <b>auto</b>, <b>teleop</b> and <b>endgame</b>. Made / match, accuracy and cycle time use verified video only (v2 tracker, identity at least 60%), shrunk toward the event; <i>grey</i> = fewer than 3 matches. Climb is from TBA for every played qual.</span>`;

  async function teamsPage(ev, openKey) {
    css();
    crumbs([[ev, `#/${ev}`], ["Teams"]]);
    const D = await teamsData(ev);
    crumbs([[D.e.name || ev, `#/${ev}`], ["Teams"]]); $updated.textContent = "";
    const st = ST[ev] || (ST[ev] = { sort: "rank", dir: 1, q: "", open: new Set() });
    if (openKey && TEAM_RE.test(openKey) && D.by[openKey]) st.open = new Set([openKey]);
    const us = D.by[D.team], picks = lsGet("cyberscout:picks:" + ev, {}), stars = new Set(picks.stars || []);
    const lbl = (k, t, cls) => `<button type="button" data-k="${k}" class="${cls || ""}" aria-sort="${st.sort === k ? (st.dir > 0 ? "ascending" : "descending") : "none"}">${t}${st.sort === k ? `<span class="arr" aria-hidden="true">${st.dir > 0 ? "▲" : "▼"}</span>` : ""}</button>`;
    $app.innerHTML = `<div class="tm"><div class="tm-top"><div><h1>Teams</h1>
        <div class="tm-sub">${H(D.e.short_name || D.e.name || ev)} · ${D.rows.length} teams · ${D.e.n_done ?? 0} of ${D.e.n_total ?? 0} matches tracked ${CS.help(TEAMS_HELP, "How to read this page")}</div>
        ${us ? `<div class="tm-us"><span>Us</span><b>${us.number}</b>${us.rank != null ? `<span>rank ${us.rank}</span>` : ""}${us.record ? `<span>${H(us.record)}</span>` : ""}${us.epa ? `<span>EPA ${fmtN(us.epa.total, "1")}</span>` : ""}${us.blend ? `<span>rating ${fmtN(us.blend.total, "1")}±${fmtN(us.blend.sd, "0")}</span>` : ""}</div>` : ""}</div></div>
      ${dataBanner(D.sd)}
      <div class="tm-ctl"><label class="tm-q"><span class="vh">Find a team</span><input class="csin" type="search" id="tmq" placeholder="Team number or name…" autocomplete="off" spellcheck="false" enterkeyhint="search" value="${H(st.q)}"></label>
        <label class="tm-sort"><span>Sort</span><select id="tmsort" aria-label="Sort teams by">${SORTS.map(([k, l]) => `<option value="${k}"${st.sort === k ? " selected" : ""}>${l}</option>`).join("")}</select></label><span class="tm-n" id="tmn" role="status"></span></div>
      <div class="tm-list"><div class="tm-head tm-grid" role="group" aria-label="Sort by">${lbl("rank", "Rank")}${lbl("number", "Team")}<span class="h-rec">Record</span>${lbl("epa", "EPA <span class=\"vh\">auto, teleop, endgame</span>")}${lbl("rating", "Our rating")}${lbl("made", "Made/m", "r")}${lbl("acc", "Acc", "r")}${lbl("cycle", "Cycle", "r h-cyc")}${lbl("climb", "Climb", "r")}<span></span></div>
        <div id="tmrows" class="tm-list"></div></div></div>`;
    const rowsEl = document.getElementById("tmrows");
    const draw = () => {
      const q = st.q.trim().toLowerCase(), dir = st.dir;
      const arr = D.rows.filter(R => !q || String(R.number).includes(q) || R.nickname.toLowerCase().includes(q)).sort((a, b) => {
        const x = sortVal(st.sort, a), y = sortVal(st.sort, b);
        if (x == null && y == null) return a.number - b.number; if (x == null) return 1; if (y == null) return -1; return (x - y) * dir || a.number - b.number; });
      document.getElementById("tmn").textContent = `${arr.length} of ${D.rows.length} teams`;
      rowsEl.innerHTML = arr.map(R => {
        const open = st.open.has(R.key), isUs = R.key === D.team;
        return `<article class="tm-item${isUs ? " us" : ""}${open ? " open" : ""}" id="tm-${H(R.key)}" data-t="${H(R.key)}"><div class="tm-row tm-grid">
          <span class="c c-rank" data-l="Rank"><b>${R.rank ?? "–"}</b></span>
          <span class="c c-team"><button type="button" class="tm-tog" aria-expanded="${open}"${open ? ` aria-controls="tmp-${H(R.key)}"` : ""}><span class="tm-num">${R.number}</span><span class="tm-nick">${H(R.nickname)}</span></button>
            <span class="tm-tags">${isUs ? `<span class="tm-tag">us</span>` : ""}${stars.has(R.key) ? `<span class="tm-star" title="starred on the picklist">${CS.icon("starOn")}<span class="vh">starred</span></span>` : ""}</span></span>
          <span class="c c-rec">${H(R.record || "–")}</span>
          <span class="c c-epa tm-epa" data-l="EPA">${R.epa ? `<b class="tm-big">${fmtN(R.epa.total, "1")}</b>${epaBar(D, R)}` : `<span class="muted">–</span>`}</span>
          <span class="c c-rate tm-rate" data-l="Our rating">${R.blend ? `<b class="tm-big">${fmtN(R.blend.total, "1")}${has(R.blend.sd) ? `<small>±${fmtN(R.blend.sd, "0")}</small>` : ""}</b>` : `<span class="muted">–</span>`}${R.coverage ? `<br>${covPill(R.coverage, { short: true })}` : ""}</span>
          <span class="c-stats"><span class="c c-made" data-l="Made/m">${cell("made", R)}</span><span class="c c-acc" data-l="Accuracy">${cell("acc", R)}</span><span class="c c-cyc" data-l="Cycle s">${cell("cycle", R)}</span><span class="c c-clb" data-l="Climb">${cell("climb", R)}</span></span>
          <span class="c c-chev" aria-hidden="true">${CS.icon("chevR")}</span></div>${open ? panelFor(D, R, ev, st) : ""}</article>`; }).join("") || `<div class="empty"><b>No team matches “${H(st.q)}”</b>Try a team number or part of a name.</div>`;
      rowsEl.querySelectorAll(".tm-tog").forEach(b => b.onclick = () => {
        const k = b.closest(".tm-item").dataset.t, was = st.open.has(k); if (was) st.open.delete(k); else st.open.add(k);
        const item = b.closest(".tm-item"), R = D.by[k];
        item.classList.toggle("open", !was); b.setAttribute("aria-expanded", String(!was));
        const old = item.querySelector(".tm-panel"); if (old) old.remove();
        if (was) b.removeAttribute("aria-controls"); else { item.insertAdjacentHTML("beforeend", panelFor(D, R, ev, st)); b.setAttribute("aria-controls", "tmp-" + k); }
        try { history.replaceState(history.state, "", `#/${ev}/teams${!was ? "/" + k : ""}`); } catch (e) { }
      });
    };
    const sortBy = k => { if (st.sort === k) st.dir = -st.dir; else { st.sort = k; st.dir = (SORTS.find(s => s[0] === k) || [0, 0, 1])[2]; } const f = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.k : null; head(); draw(); if (f) { const b = $app.querySelector(`.tm-head [data-k="${f}"]`); if (b) b.focus(); } document.getElementById("tmsort").value = st.sort; };
    const head = () => $app.querySelectorAll(".tm-head [data-k]").forEach(b => { const k = b.dataset.k, on = st.sort === k; b.setAttribute("aria-sort", on ? (st.dir > 0 ? "ascending" : "descending") : "none");
      const arr = b.querySelector(".arr"); if (arr) arr.remove(); if (on) b.insertAdjacentHTML("beforeend", `<span class="arr" aria-hidden="true">${st.dir > 0 ? "▲" : "▼"}</span>`); });
    $app.querySelectorAll(".tm-head [data-k]").forEach(b => b.onclick = () => sortBy(b.dataset.k));
    document.getElementById("tmsort").onchange = e => { st.sort = e.target.value; st.dir = (SORTS.find(s => s[0] === st.sort) || [0, 0, 1])[2]; head(); draw(); };
    document.getElementById("tmq").oninput = e => { st.q = e.target.value; draw(); };
    draw();
    if (openKey && st.open.has(openKey)) { const el = document.getElementById("tm-" + openKey); if (el) requestAnimationFrame(() => el.scrollIntoView({ block: "start" })); }
    const iv = setInterval(async () => { try { const n = await api(`${ev}/event.json`); if (n.updated !== D.e.updated) route(false); } catch (e) { } }, 60000);
    cleanup.push(() => clearInterval(iv));
  }

  // ---- a field's own page
  const ORD = n => { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
  function stripHtml(D, F, R) {
    const d = dist(D, F), me = d.pts.find(p => p.key === R.key); if (!me || d.pts.length < 3) return "";
    const lo = d.sorted[0], hi = d.sorted[d.sorted.length - 1], span = hi - lo || 1, X = v => (3 + (v - lo) / span * 94).toFixed(1);
    const rank = d.pts.filter(p => F.lower ? p.v < me.v : p.v > me.v).length + 1;
    return `<div class="fp-strip" role="img" aria-label="${R.number} ranks ${ORD(rank)} of ${d.pts.length} teams on ${H(F.label)}"><span class="ln"></span>${d.pts.filter(p => p.key !== R.key).map(p => `<i class="d" style="left:${X(p.v)}%"></i>`).join("")}
      <i class="me" style="left:${X(me.v)}%"></i><span class="lab" style="left:${Math.max(8, Math.min(92, +X(me.v)))}%">${R.number}</span><span class="ax l">${fmtN(lo, F.f)}</span><span class="ax r">${fmtN(hi, F.f)}</span></div>
      <div class="fp-leg"><span>${ORD(rank)} of ${d.pts.length} teams with a value${F.lower ? " (lower is better)" : ""}</span>${me.grey ? `<span class="tf-n grey">this one rests on under 3 matches</span>` : ""}</div>`;
  }
  function pmHtml(F, T, cov) {
    if (!F.pm || !T) return "";
    const keys = new Set((cov && cov.keys) || []), pts = (T.matches || []).filter(m => keys.has(m.match) && m.analytics).map(m => { let v = null; try { v = F.pm(m.analytics); } catch (e) { } return { m: m.short || shortKey(m.match), v: has(v) ? +v : null }; }).filter(p => p.v != null);
    if (!pts.length) return "";
    const mx = Math.max(1e-9, ...pts.map(p => Math.abs(p.v)));
    return `<section class="fp-card"><h2>Per match ${CS.help(`<span class="hl">${H(F.pmLabel || F.label)} in each verified match (video identity at least 60%). Bars share one scale starting at zero.</span>`, "About the per-match values")}<span class="muted small" style="font-weight:500">${pts.length} verified</span></h2>
      <div class="fp-pm" role="img" aria-label="${H(F.label)} per verified match">${pts.map(p => `<span class="b" title="${H(p.m)}: ${fmtN(p.v, F.f === "p" ? "p" : "1")}"><em>${fmtN(p.v, F.f === "p" ? "p" : p.v >= 10 ? "0" : "1")}</em><i style="height:${Math.max(2, Math.abs(p.v) / mx * 78).toFixed(0)}px"></i><span>${H(p.m)}</span></span>`).join("")}</div></section>`;
  }
  function ratingBody(F, D, R) {
    const e = R.epa, b = R.blend, w = R.d && R.d.weights, p = R.proj, sd = b && b.sd;
    const parts = (o) => o ? [["Auto", o.auto, "var(--caution)"], ["Teleop", o.teleop, "var(--accent)"], ["Endgame", o.endgame, "var(--good)"]] : [];
    const stack = o => { const ps = parts(o), tot = ps.reduce((s, x) => s + Math.max(0, x[1] || 0), 0) || 1;
      return `<div class="fp-stack" role="img" aria-label="${ps.map(x => `${x[0]} ${fmtN(x[1], "1")}`).join(", ")}">${ps.map(x => `<i style="flex:${Math.max(0, x[1] || 0) / tot};background:${x[2]}"></i>`).join("")}</div>
        <div class="fp-leg">${ps.map(x => `<span><i style="background:${x[2]}"></i>${x[0]} ${fmtN(x[1], "1")}</span>`).join("")}</div>`; };
    const trend = R.sc && R.sc.trend && R.sc.trend.blend;
    let h = "";
    if (F.custom === "rating" || F.custom === "epa") h += `<section class="fp-card"><h2>${F.custom === "rating" ? "Parts of the rating" : "Parts of the EPA"} ${CS.help(`<span class="hl">Points per match split by period. Auto + teleop + endgame add up to the total.</span>`, "About the parts")}</h2>${stack(F.custom === "rating" ? b : e)}</section>`;
    if (F.custom === "rating" && w) h += `<section class="fp-card"><h2>What it is built from ${CS.help(`<span class="hl">Weights of the three sources in this team's rating: earlier events (prior), this event's TBA results, and verified robot video. More verified matches shift weight to video.</span>`, "About the weights")}</h2>
      <div class="fp-stack" role="img" aria-label="prior ${Math.round(w.prior * 100)}%, TBA ${Math.round(w.tba * 100)}%, video ${Math.round(w.video * 100)}%"><i style="flex:${w.prior};background:var(--ink-3)"></i><i style="flex:${w.tba};background:var(--accent)"></i><i style="flex:${w.video};background:var(--good)"></i></div>
      <div class="fp-leg"><span><i style="background:var(--ink-3)"></i>earlier events ${Math.round(w.prior * 100)}%</span><span><i style="background:var(--accent)"></i>TBA results ${Math.round(w.tba * 100)}%</span><span><i style="background:var(--good)"></i>video ${Math.round(w.video * 100)}%</span></div></section>`;
    if (F.custom === "rank") h += `<section class="fp-card"><h2>Rank & projection</h2><dl class="fp-kv"><dt>Rank</dt><dd>${R.rank ?? "–"}</dd><dt>Record</dt><dd>${H(R.record || "–")}</dd><dt>Ranking points</dt><dd>${R.rp != null ? fmtN(R.rp, "1") : "–"}${R.played ? ` in ${R.played} matches` : ""}</dd>
      ${p ? `<dt>Simulated final rank</dt><dd>${fmtN(p.rank_mean, "1")} <span class="muted small">(${p.rank_p10}–${p.rank_p90}, 10–90%)</span></dd><dt>Top 8 / top 4</dt><dd>${pct(p.p_top8)} / ${pct(p.p_top4)}</dd><dt>Finishes first</dt><dd>${pct(p.p_first)}</dd>` : ""}</dl></section>`;
    if (F.custom === "opr") h += `<section class="fp-card"><h2>OPR next to the others</h2><dl class="fp-kv"><dt>OPR</dt><dd>${fmtN((mv(F.get(R)) || {}).v, "1")}</dd><dt>EPA</dt><dd>${e ? fmtN(e.total, "1") : "–"}</dd><dt>Our rating</dt><dd>${b ? fmtN(b.total, "1") + (has(sd) ? ` ±${fmtN(sd, "0")}` : "") : "–"}</dd></dl>
      <p class="muted small" style="margin:8px 0 0">OPR is the least noisy late in the event and the least reliable early on; the rating is the one the model uses.</p></section>`;
    if (trend && trend.length > 2 && F.custom !== "rank") h += `<section class="fp-card"><h2>Rating over the event ${CS.help(`<span class="hl">The blended rating after each batch of results. The first point is mostly the prior; later points lean on this event.</span>`, "About the trend")}</h2>${spark(trend.map(x => x[1]), { w: 260, h: 54, color: "var(--accent)" })}
      <div class="fp-leg"><span>${fmtN(trend[0][1], "0")} → ${fmtN(trend[trend.length - 1][1], "0")}</span></div></section>`;
    return h;
  }
  async function fieldPage(ev, tk, fid) {
    css();
    const F = FBY[fid];
    crumbs([[ev, `#/${ev}`], ["Teams", `#/${ev}/teams`], [num(tk), `#/${ev}/teams/${tk}`], [F ? F.label : "Field"]]);
    if (!F) { $app.innerHTML = `<div class="empty"><b>No such field</b>“${H(fid)}” isn't one of the fields we track. <div class="acts"><a class="cmp-btn" href="#/${H(ev)}/teams/${H(tk)}">All fields of ${H(num(tk))}</a></div></div>`; return; }
    const D = await teamsData(ev), R = D.by[tk];
    crumbs([[D.e.name || ev, `#/${ev}`], ["Teams", `#/${ev}/teams`], [`${num(tk)}${R && R.nickname ? " · " + R.nickname : ""}`, `#/${ev}/teams/${tk}`], [F.label]]); $updated.textContent = "";
    if (!R) { $app.innerHTML = `<div class="empty"><b>Team ${H(num(tk))} isn't at ${H(D.e.name || ev)}</b><div class="acts"><a class="cmp-btn" href="#/${H(ev)}/teams">All teams</a></div></div>`; return; }
    const [gid, gl, gv] = GBY[F.g] ? GBY[F.g] : GROUPS[0], m = mv(F.get(R)), nt = noteOf(F, R, m), T = F.pm ? await getTeam(ev, tk).catch(() => null) : null;
    const sib = GROUPS.map(([g, l]) => `<div class="row"><span class="gl">${H(l)}</span>${FIELDS.filter(f => f.g === g).map(f => `<a href="#/${ev}/teams/${H(tk)}/${f.id}"${f.id === fid ? ' aria-current="page"' : ""}>${H(f.label)}</a>`).join("")}</div>`).join("");
    const hasSec = !!(F.sec || F.sx);
    $app.innerHTML = `<div class="fp tm" style="--gc: var(${gv})"><header class="fp-head">
        <div class="fp-kick"><a href="#/${ev}/teams">Teams</a><span aria-hidden="true">/</span><a href="#/${ev}/teams/${H(tk)}">${R.number} ${H(R.nickname)}</a><span aria-hidden="true">/</span><span class="gp"><i aria-hidden="true"></i>${H(gl)}</span></div>
        <div class="fp-title"><h1>${H(F.label)}<small>${R.number} ${H(R.nickname)}${R.key === D.team ? " · us" : ""}</small></h1><div class="fp-val">${valHtml(F, m, true)}</div></div>
        <div class="fp-meta">${F.src === "video" ? covPill(R.sc && R.sc.coverage) : R.coverage && F.id === "rating" ? covPill(R.coverage) : ""}<span class="${nt.grey ? "tf-n grey" : ""}">${H(nt.t)}</span>${R.sc ? flagChips(R.sc) : ""}${F.defNote ? `<span class="tf-n grey">effect on opponents is an assumption, not measured</span>` : ""}</div>
        <p class="fp-how">${F.how}</p>
        <div class="fp-acts"><a class="main" href="#/${ev}/t/${H(tk)}">All of ${R.number} &rarr;</a>${F.defNote ? `<a href="#/${ev}/selection/defense/${F.id === "def_recv" ? H(tk) : "defenders"}">${F.id === "def_recv" ? "How to defend " + R.number : "Best defenders"}</a>` : ""}<a href="#/${ev}/selection/compare/${R.number}">Compare</a></div></header>
      <div class="fp-cols"><section class="fp-card"><h2>Among the teams here</h2>${stripHtml(D, F, R) || `<p class="muted small" style="margin:0">Not enough teams have a value yet to compare.</p>`}</section>${pmHtml(F, T, R.sc && R.sc.coverage)}</div>
      ${F.custom ? `<div class="fp-cols">${ratingBody(F, D, R)}</div>` : ""}
      ${hasSec ? `<div class="fp-sec"><h2 class="fp-cap">From the team page</h2><div id="fpTeam"></div>${F.sx ? `<h2 class="fp-cap">From the spec sheet</h2><div id="fpSpec"></div>` : ""}</div>` : ""}
      <section class="fp-card fp-sib" aria-label="Other fields"><h2>Other fields of ${R.number}</h2>${sib}</section></div>`;
    const jobs = [];
    if (F.sec) jobs.push(teamPage(ev, tk, { host: document.getElementById("fpTeam"), only: F.sec, quiet: true }).catch(e => { const h = document.getElementById("fpTeam"); if (h) h.innerHTML = `<div class="empty">No tracker data for this team yet.</div>`; }));
    if (F.sx) jobs.push(CS.specPage(ev, tk, { host: document.getElementById("fpSpec"), only: F.sx, quiet: true }).catch(e => { const h = document.getElementById("fpSpec"); if (h) h.innerHTML = `<div class="empty">No spec sheet for this team yet (it needs verified video).</div>`; }));
    await Promise.all(jobs);
  }
  CS.fieldPage = fieldPage;

  CS.route("teams", async (ev, a, b) => {
    if (a && b) return fieldPage(ev, a, b);
    return teamsPage(ev, a);
  });
})();
