"use strict";
// teams2.js: team SPEC SHEETS (#/<ev>/specs, #/<ev>/specs/<team>). Data: api/<ev>/spec/{index,<team>}.json written by
// live/scouting.py. Team frame = own alliance wall on the LEFT (blue matches point-reflected); driver's left = top.
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

  // ------------------------------------------------------------------ list
  const COLS = [
    ["number", "Team", v => v, ""], ["rank", "Rank", v => v ?? "–", ""], ["n", "Video", (v, x) => covPill(x.cov, { short: true }), "verified matches (v2 tracker, identity ≥ 60%) / played"],
    ["made_pm", "Made/m", (v, x) => x.made_v ? svHtml(x.made_v) : f1(v), "FUEL made per match: shrunk, TBA-reconciled (≥ = floor, alliance under-credited); same number as the Teams table"], ["acc", "Acc", pc, "made / (made+missed), raw"],
    ["pts_min", "Pts/active min", v => f1(v), "made fuel into an ACTIVE hub per minute of own-hub-active teleop time"],
    ["fire", "Fire /s", v => f1(v), "balls per second inside a volley"], ["hopper", "Volley p90", v => v ?? "–", "90th percentile volley size ≈ hopper capacity"],
    ["cycle", "Cycle s", v => f1(v), "median time between volley starts (teleop)"], ["speed", "p95 m/s", v => f1(v, 2), "95th percentile moving speed"],
    ["trench", "Trench", pc, "share of hub-line crossings through a trench"], ["left", "Left", pc, "share of crossings on the driver's left"],
    ["on_move", "On move", pc, "share of shots launched while moving > 0.5 m/s"], ["climb", "Climb", pc, "endgame climb rate (TBA, every played qual)"], ["flags", "", (v, x) => FLAGS(x), ""],
    ["def_recv", "Def. recv", v => v ?? 0, "defense episodes received"], ["def_play", "Def. played", v => v ?? 0, "defense episodes played"],
  ];
  const SPRI = { number: 1000, made_pm: 800, acc: 700, n: 500, flags: 450, pts_min: 400, climb: 360, cycle: 350, rank: 300, fire: 200, hopper: 190, speed: 150, trench: 140, left: 130, on_move: 120, def_recv: 110, def_play: 100 };
  const FLAGS = x => flagChips({ flags: (x.flags || []).map(k => ({ k, text: k.replace(/_/g, " ") })) }, ["auto_not_tracked", "video_higher", "video_lower"]);
  async function listPage(ev) {
    css();
    crumbs([[ev, `#/${ev}`], ["Spec sheets"]]);           // this page's title even if its data fails to load (MATCH-8)
    const [ix, e, scd] = await Promise.all([api(`${ev}/spec/index.json`), api(`${ev}/event.json`).catch(() => null), scoutData(ev)]);
    crumbs([[e ? e.name : ev, `#/${ev}`], ["Spec sheets"]]); $updated.textContent = "";
    let sk = "made_pm", sd = -1;
    const rows = ix.teams || [];
    $app.innerHTML = `<div class="sx-head"><div><h1>Spec sheets</h1>
      <div class="sub">${rows.length} teams · ${(ix.matches || []).length} verified matches ${CS.help(`<span class="hl">Verified = v2 tracker; a team counts only where its identity is ≥ 60%, frames and shots ≥ 50%.</span><span class="hl"><i>Grey italic</i> = fewer than 3 verified matches. Tap a header to sort; tap a row for the spec sheet.</span>`, "Which matches count")}</div></div>
      <div class="sx-links"><a href="#/${ev}/defense">Defense library</a></div></div>
      ${dataBanner(scd)}<div class="sx-card"><div class="tablewrap"><table class="sx-list sx-tbl" id="sxl"></table></div></div>`;
    const tb = document.getElementById("sxl");
    const draw = () => {
      const kv = (r, k) => k === "made_pm" && r.made_v ? r.made_v.v : r[k];
      const r = rows.slice().sort((a, b) => { const x = kv(a, sk), y = kv(b, sk); if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sd; });
      tb.innerHTML = `<thead><tr>${COLS.map(([k, l, , t]) => `<th class="${k === "number" ? "" : "num"}" data-k="${k}" data-pri="${SPRI[k] ?? 100}" title="${H(t)}"${k === "flags" ? "" : ` tabindex="0" aria-sort="${k === sk ? (sd < 0 ? "descending" : "ascending") : "none"}"`}>${l}${k === sk ? (sd < 0 ? " ▾" : " ▴") : ""}</th>`).join("")}</tr></thead>
      <tbody>${r.map(x => `<tr class="clk" data-t="${x.team}">${COLS.map(([k, , f]) => k === "number"
        ? `<td><a href="#/${ev}/specs/${x.team}"><b>${x.number}</b></a> <span class="sx-mini">${H((x.nickname || "").slice(0, 22))}</span></td>`
        : `<td class="${k === "flags" ? "" : "num"}">${f(x[k], x)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      // sortable from the keyboard too (Enter / Space), focus stays on the header (MATCH-27)
      tb.querySelectorAll("th").forEach(th => { th.onclick = () => { const k = th.dataset.k; if (!k || k === "flags") return; if (k === sk) sd = -sd; else { sk = k; sd = k === "rank" || k === "number" ? 1 : -1; } draw(); const f = tb.querySelector(`th[data-k="${k}"]`); if (f) f.focus(); };
        th.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); th.onclick(); } }; });
      tb.querySelectorAll("tr.clk").forEach(tr => tr.onclick = ev_ => { if (ev_.target.tagName !== "A") location.hash = `#/${ev}/specs/${tr.dataset.t}`; });
    };
    draw();
  }

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
  async function specPage(ev, team) {
    crumbs([[ev, `#/${ev}`], ["Spec sheets", `#/${ev}/specs`], [num(team)]]);       // title before the data (MATCH-8)
    css();
    const [sp, e, sd] = await Promise.all([api(`${ev}/spec/${team}.json`), api(`${ev}/event.json`).catch(() => null), scoutData(ev)]);
    const TR = sp.truth || {}, TV = TR.video || {}, autoBad = (TR.flags || []).some(f => f.k === "auto_not_tracked");
    crumbs([[e ? e.name : ev, `#/${ev}`], ["Spec sheets", `#/${ev}/specs`], [String(sp.number)]]); $updated.textContent = "";
    const D = sp.drive || {}, S = sp.scoring || {}, A = sp.auto || {}, CL = sp.climb || {}, LN = sp.lanes || {}, DF = sp.defense || {};
    const st = (v, l, u = "", tip = "") => `<div${!tip ? "" : typeof tipA === "function" ? tipA(tip) : ` title="${H(tip)}"`}><b>${v}${u && v !== "–" ? `<small> ${u}</small>` : ""}</b><span>${l}</span></div>`;
    const matchTxt = (sp.matches || []).map(m => `<span title="identity-valid ${pc(m.valid)} of the match, mean conf ${pc(m.conf)}">${m.m}<span class="sx-mini">(${m.al[0]}, ${pc(m.valid)})</span></span>`).join(" ");
    const recv = DF.received || {}, play = DF.played || null;
    const lvls = CL.levels || {}, nL = Object.values(lvls).reduce((a, b) => a + b, 0);
    const ownN = (LN.own || {}).n || 0;
    $app.innerHTML = `<div class="sx-head"><div><h1>${sp.number} <small>${H(sp.nickname || "")}</small></h1>
      <div class="sub">${sp.rank ? `Rank ${sp.rank} · ` : ""}${covPill(sp.coverage)} ${(sp.matches || []).length ? CS.help(`<span class="hl">Verified matches (alliance, identity-valid share of the match):</span><span class="hl">${matchTxt}</span>`, "Which matches are verified") : ""} ${flagChips(TR)}</div></div>
      <div class="sx-links"><a href="#/${ev}/t/${team}">Team page</a><a href="#/${ev}/defense/${team}">How to defend ${sp.number}</a><a href="#/${ev}/specs">All specs</a></div></div>
    ${dataBanner(sd)}${(sp.coverage?.n || 0) < 3 ? `<div class="sc-banner"><b>n=${sp.coverage?.n || 0}</b>: fewer than 3 verified matches. Every video number on this page is an anecdote, not a rate.</div>` : ""}
    <div class="sx-grid3">
      <div class="sx-card"><h2>Drive <small>${f1(D.seconds, 0)} s tracked</small></h2><div class="sx-stat">
        ${st(f1(D.top_speed, 2), "Top speed", "m/s", "99th percentile of speed")}${st(f1(D.p95_speed, 2), "p95 moving", "m/s")}${st(f1(D.mean_speed, 2), "Mean moving", "m/s")}
        ${st(f1(D.accel_p90), "Accel p90", "m/s²")}${st(f1(D.accel_max), "Accel max", "m/s²", "99th percentile")}${st(pc(D.pct_moving), "Time moving")}${st(f1(D.dist_per_match, 0), "Dist / match", "m")}</div></div>
      <div class="sx-card"><h2>Scoring <small>${S.made}/${S.att} made${S.passes ? ` · ${S.passes} passes` : ""} · ${S.n_fuel ?? 0} reconciled match${S.n_fuel === 1 ? "" : "es"}</small> ${CS.help(`<span class="hl">Scoring counts FUEL only in matches whose video total reconciles with the TBA alliance score (${S.n_fuel ?? 0} of ${(sp.matches || []).length} verified here), so the totals can be lower than the shot map, which shows every verified match.</span>`, "Which matches these totals cover")}</h2><div class="sx-stat">
        ${st(TV.made_pm ? (TV.made_pm.lower_bound ? "≥" : "") + f1(TV.made_pm.v) : f1(S.made_pm), "Made / match", "", TV.made_pm ? `canonical (shrunk, TBA-reconciled); raw ${f1(TV.made_pm.raw)} over n=${TV.made_pm.n}` : "")}${st(pc(S.acc), "Accuracy", "", `${S.made}/${S.made + S.missed} shots`)}
        ${st(f1(S.pts_per_active_min), "Pts / active min", "", "made into an active hub per minute of own-hub-active teleop")}
        ${st(f1(S.fire_rate), "Fire rate", "/s", "balls per second inside a volley of 3+")}${st(f1(S.volley_mean), "Volley mean")}
        ${st(S.hopper_est ?? S.volley_max ?? "–", "Volley p90 ≈ hopper", "", `max volley ${S.volley_max ?? "–"}, ${S.n_volleys} teleop volleys`)}
        ${st(f1(S.cycle_s), "Cycle", "s", S.cycle_iqr ? `IQR ${S.cycle_iqr[0]}–${S.cycle_iqr[1]} s` : "")}${st(pc(S.on_move), "Shots on move")}${st(f1(S.dist_med), "Shot dist", "m", "median distance to hub")}</div></div>
      <div class="sx-card"><h2>Auto &amp; climb</h2><div class="sx-stat">
        ${autoBad ? st("n/t", "Auto made", "", (TR.flags.find(f => f.k === "auto_not_tracked") || {}).text) + st("n/t", "Auto shots") : st(TV.auto_made ? f1(TV.auto_made.v) : f1(A.made_avg), "Auto made", "", TV.auto_made ? `the same number as the team page and Teams (shrunk toward the event, n=${TV.auto_made.n}); the plain mean of the rows below is ${f1(A.made_avg)}` : "mean over the reconciled matches below") + st(f1(A.att_avg), "Auto shots", "", "mean over the reconciled matches below")}${st(pc(A.climb_rate), "Auto L1 (TBA)")}
        ${st(pc(CL.rate), "Endgame climb (TBA)", "", `${CL.n ?? nL} quals · levels: ${Object.entries(lvls).map(([k, v]) => `L${k}×${v}`).join(", ")}`)}</div>
        ${autoBad ? `<div class="sx-mini" style="margin-top:6px;color:var(--caution)">${H((TR.flags.find(f => f.k === "auto_not_tracked") || {}).text)}</div>` : ""}
        ${autoBad ? "" : `<div class="sx-mini" style="margin-top:6px">${(A.rows || []).map(r => `${r.m}: ${r.made}/${r.att}${r.climb ? " + climb" : ""}`).join(" · ")}</div>`}
        ${(A.routines || []).length ? `<div class="sx-mini" style="margin-top:4px">Routines: ${A.routines.map(r => `${H(r.name)} ${r.n}/${r.of}`).join(", ")} — <a href="#/${ev}/t/${team}">paths on team page</a></div>` : ""}</div>
    </div>
    <div class="sx-grid" style="margin-top:14px">
      <div class="sx-card"><h2>Lanes <small>${ownN} own-side crossings · trench ${pc(LN.trench_share)} · left ${pc(LN.left_share)}</small></h2>
        <div id="sxLane"></div>
        <div class="sx-leg"><span><i style="background:${LANE_COL.trench}"></i>trench</span><span><i style="background:${LANE_COL.bump}"></i>bump</span>
          ${LN.trench_capable === false ? "<span>no trench run seen</span>" : ""}${CS.help("Numbers = crossings per lane. Arrow ← = toward own wall, → = outbound.", "How to read the lanes")}</div></div>
      <div class="sx-card"><h2>Shooting locations <small id="sxShN"></small></h2>
        <div class="sx-ctl"><span class="chips" id="sxShP" style="margin:0"></span><span class="chips" id="sxShM" style="margin:0"></span></div>
        <div id="sxShot"></div>
        <div class="sx-leg"><span><i class="dot" style="background:${C_MADE}"></i>made</span><span><i class="dot" style="background:${C_MISS}"></i>missed</span>${CS.help("■ = launched on the move, ● = stopped. Own end on the left, hub ringed.", "How to read the shot map")}</div></div>
    </div>
    <div class="sx-card" style="margin-top:14px"><h2>Shooting paths ${CS.help(`<span class="hl">The drive leading into each volley, aligned on the first shot.</span><span class="hl">Lead-in starts at the latest of: previous volley, last floor intake, leaving the neutral zone (crossing back over the own hub line), a tracking hole, or 10 s. Moving = robot speed &gt; 0.5 m/s at the first shot.</span><span class="hl">Tap a row to highlight one path.</span>`, "How shooting paths work")}</h2>
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
    <div class="sx-card" style="margin-top:14px"><h2>Defense <small><a href="#/${ev}/defense/${team}">open in defense library</a></small></h2>
      <div class="sx-grid"><div><dl class="sx-kv">
        <dt>Defended (episodes)</dt><dd>${recv.n ?? 0} <small>(${recv.tested ?? 0} testable, ${recv.succ ?? 0} clearly reduced)</small></dd>
        <dt>Output when defended</dt><dd>${recv.ratio != null ? pc(recv.ratio) + " of own baseline" : "–"} ${recv.ci ? `<small>90% CI ${pc(recv.ci[0])}–${pc(recv.ci[1])}</small>` : ""}</dd>
        <dt>Baseline (free)</dt><dd>${DF.baseline && DF.baseline.pts_per_active_s != null ? f1(DF.baseline.pts_per_active_s * 60) + " pts / active min" : "–"} <small>${DF.baseline ? f1(DF.baseline.free_s, 0) + " s free" : ""}</small></dd>
        <dt>Played defense</dt><dd>${play ? `${play.n} episodes, ${f1(play.secs, 0)} s` : "none seen"}</dd>
        ${play ? `<dt>Victims' output</dt><dd>${play.ratio != null ? pc(play.ratio) + " of their baseline" : "–"} <small>${play.tested} testable, ${play.succ} clear, ${f1(play.denied)} pts denied</small></dd>` : ""}
      </dl></div><div><ul class="sx-adv">${(DF.advice || []).map(a => `<li>${H(a)}</li>`).join("") || "<li class='muted'>No defense data.</li>"}</ul></div></div></div>`;

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

  CS.route("specs", (ev, team) => team ? specPage(ev, team) : listPage(ev));
  CS.nav({ label: "Specs", key: "specs", href: ev => `#/${ev}/specs`, order: 65, group: "scout", icon: "specs" });
})();
