"use strict";
// defense.js: DEFENSE LIBRARY (#/<ev>/defense, #/<ev>/defense/<target team>). Data: api/<ev>/defense.json from
// live/scouting.py. Clips are top-down tracking replays drawn in the browser from the match data (embedded slice, or
// fetched from m/<match>.json on demand), absolute field frame like the video (red end on the left).
// Uses globals from app.js: api, esc, crumbs, FIELD, LEN, WID, cleanup, $app, $updated.
(function () {
  const H = s => esc(s);
  const tn = k => String(k || "").replace("frc", "");
  const f1 = (v, d = 1) => v == null ? "–" : Number(v).toFixed(d);
  const pc = v => v == null ? "–" : Math.round(v * 100) + "%";
  const SH = s => ({ auto: "Auto", transition: "Trans", shift1: "S1", shift2: "S2", shift3: "S3", shift4: "S4", endgame: "End" }[s] || s);
  const VERD = { success: ["clearly reduced", "good"], weak: ["reduced, not clear", "warn"], "no effect": ["no effect", "none"], untestable: ["nothing at stake", "none"], tracking: ["tracking unsure", "bad"] };
  const METHODS = ["pin", "contact", "shadow", "zone-denial"];
  const C_D = "#22d3ee", C_T = "#facc15";
  const CSS = `
  .dx-sum{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:6px;margin-bottom:12px}
  .dx-sum div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
  .dx-sum b{display:block;font-size:19px;font-variant-numeric:tabular-nums}.dx-sum b small{font-size:11.5px;color:var(--muted);font-weight:500}
  .dx-sum span{font-size:11.5px;color:var(--muted)}
  .dx-flt{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 10px;font-size:12px}
  .dx-flt select,.dx-flt button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:4px 6px;font-size:12px;max-width:170px}
  .dx-flt button.on{border-color:var(--accent);color:#cfe1ff}
  .dx-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:10px}
  .dx-clip{background:var(--panel);border:1px solid var(--line);border-radius:9px;overflow:hidden;min-width:0}
  .dx-clip.success{border-color:rgba(63,185,80,.55)}.dx-clip.focus{box-shadow:0 0 0 2px var(--accent)}
  .dx-clip canvas{display:block;width:100%;aspect-ratio:16/10;background:#2b3038;cursor:pointer}
  .dx-cb{padding:7px 10px 9px;font-size:12.5px;display:flex;flex-direction:column;gap:3px}
  .dx-ct{display:flex;justify-content:space-between;gap:6px;align-items:center}
  .dx-ct b{font-variant-numeric:tabular-nums}.dx-ct a{color:var(--text)}
  .dx-cs{color:var(--muted);font-size:12px}
  .dx-ce{display:flex;gap:8px;align-items:center;font-variant-numeric:tabular-nums;font-size:12px}
  .dx-cl{display:flex;gap:10px;font-size:12px}
  .dx-v{display:inline-block;padding:0 7px;border-radius:99px;font-size:11px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
  .dx-v.good{color:var(--good);border-color:rgba(63,185,80,.45)}.dx-v.warn{color:var(--warn);border-color:rgba(210,153,34,.45)}.dx-v.bad{color:var(--bad);border-color:rgba(248,81,73,.45)}
  .dx-v.sig{background:rgba(63,185,80,.15)}
  .dx-panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:12px}
  .dx-panel h2{font-size:14px;margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em;color:#c9d4e2;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
  .dx-panel h2 small{text-transform:none;letter-spacing:0;font-weight:500;color:var(--muted);font-size:12px}
  .dx-2{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:14px}@media (max-width:860px){.dx-2{grid-template-columns:minmax(0,1fr)}}
  .dx-tbl td,.dx-tbl th{padding:4px 6px;font-size:12.5px}
  .dx-tbl tr.clk{cursor:pointer}
  .dx-adv{margin:0;padding-left:18px;font-size:13px;line-height:1.55}
  .dx-note{font-size:12px;color:var(--muted);line-height:1.5;margin-top:8px}
  .dx-more{display:block;margin:12px auto;background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:7px;padding:6px 16px}
  .dx-tabs{display:flex;gap:4px;margin:4px 0 10px;border-bottom:1px solid var(--line)}
  .dx-tabs span{padding:6px 12px;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;font-size:13px}
  .dx-tabs span.on{color:var(--text);border-color:var(--accent)}
  .dx-tchips{display:flex;flex-wrap:wrap;gap:6px}
  .dx-tchips a{font-size:12px;padding:3px 9px;border-radius:99px;background:var(--panel2);color:var(--text)}
  `;
  function css() { if (!document.getElementById("dx-css")) { const s = document.createElement("style"); s.id = "dx-css"; s.textContent = CSS; document.head.appendChild(s); } }

  // ------------------------------------------------------------------ effect glyph: null 90% band, expected tick, observed dot
  function effGlyph(ef) {
    const m = ef.metric === "attempts" ? "att" : "pts";
    const exp = ef[m + "_exp"], ci = ef[m + "_ci"], obs = ef[m + "_obs"];
    if (exp == null || !ci) return "";
    const mx = Math.max(1, ci[1], obs, exp) * 1.1, X = v => 2 + v / mx * 96;
    const col = obs < ci[0] ? "#3fb950" : obs > ci[1] ? "#f85149" : "#e6edf3";
    return `<svg width="100" height="14" viewBox="0 0 100 14" aria-label="observed vs expected"><rect x="0" y="6" width="100" height="2" fill="#263040"/>
      <rect x="${X(ci[0])}" y="3" width="${Math.max(1.5, X(ci[1]) - X(ci[0]))}" height="8" rx="2" fill="#3a4a62"/>
      <rect x="${X(exp) - 1}" y="1" width="2" height="12" fill="#8b98a8"/><circle cx="${X(obs)}" cy="7" r="3.6" fill="${col}"/></svg>`;
  }
  function effText(ef) {
    if (ef.metric === "points") return `${ef.pts_obs} pts vs ${f1(ef.pts_exp)} exp <span class="muted">(90% ${f1(ef.pts_ci[0], 0)}–${f1(ef.pts_ci[1], 0)})</span>`;
    if (ef.metric === "attempts") return `${ef.att_obs} shots vs ${f1(ef.att_exp)} exp <span class="muted">(hub inactive)</span>`;
    return `<span class="muted">${ef.pts_exp != null ? `${f1(ef.pts_exp)} pts expected — too little at stake` : "no baseline"}</span>`;
  }
  const pOf = ef => ef.metric === "points" ? ef.pts_p : ef.metric === "attempts" ? ef.att_p : null;
  function vpill(e) {
    const [t, c] = VERD[e.verdict] || [e.verdict, "none"];
    return `<span class="dx-v ${c}${e.sig ? " sig" : ""}" title="${e.sig ? "significant: p ≤ 0.05 and survives Benjamini-Hochberg q ≤ 0.2" : ""}">${t}${e.sig ? " ✓" : ""}</span>`;
  }

  // ------------------------------------------------------------------ clips
  const mCache = {};
  function clipFromMatch(m, e) {            // same slice live/scouting.py embeds
    const hz = m.hz || 10, v0 = m.t_start - 0.5, step = 2, N = m.tracks[0].xy.length;
    const f0 = Math.max(0, Math.round((e.clip.v0 - v0) * hz)), f1_ = Math.min(N - 1, Math.round((e.clip.v1 - v0) * hz));
    const fr = []; for (let i = f0; i <= f1_; i += step) fr.push(i);
    const tracks = m.tracks.map(t => ({ team: t.team, al: t.alliance,
      xy: fr.map(i => t.obs[i] || (t.conf && t.conf[i] >= .5) ? t.xy[i] : null), q: fr.map(i => (t.conf ? t.conf[i] : 0) >= .5 ? 1 : 0) }));
    const shots = (m.shots || []).filter(s => s.origin && s.t >= e.clip.v0 && s.t <= e.clip.v1).map(s => {
      const l = s.landing || s.origin, sh = s.shooter || {};
      return [+(s.t - e.clip.v0).toFixed(2), Math.round(s.origin[0] * 100), Math.round(s.origin[1] * 100), Math.round(l[0] * 100), Math.round(l[1] * 100),
        { made: "m", missed: "x", pass: "p" }[s.outcome] || "u", sh.kind === "robot" ? sh.track_idx : -1, s.team ? 1 : 0];
    });
    return { hz: hz / step, t0: v0 + f0 / hz, tracks, shots, a: (e.a - f0) / step, b: (e.b - f0) / step };
  }
  async function getClip(ev, e) {
    if (e.clip.data) return e.clip.data;
    const m = await (mCache[e.key] || (mCache[e.key] = api(`${ev}/m/${e.key}.json`)));
    return (e.clip.data = clipFromMatch(m, e));
  }

  function clipPlayer(cv, clip, e) {
    const ctx = cv.getContext("2d");
    const n = clip.tracks[0].xy.length, hz = clip.hz;
    const D = clip.tracks[e.d_k], T = clip.tracks[e.t_k];
    // viewport (broadcast orientation: u = LEN - x to the right, y down) around defender + target, 16:10
    let pts = [];
    for (const tr of [D, T]) for (let i = 0; i < n; i++) if (tr.xy[i]) pts.push([LEN - tr.xy[i][0] / 100, tr.xy[i][1] / 100]);
    if (!pts.length) pts = [[LEN / 2, WID / 2]];
    let u0 = Math.min(...pts.map(p => p[0])) - 2.2, u1 = Math.max(...pts.map(p => p[0])) + 2.2;
    let v0 = Math.min(...pts.map(p => p[1])) - 2.2, v1 = Math.max(...pts.map(p => p[1])) + 2.2;
    const AR = 1.6;
    let w = Math.max(u1 - u0, (v1 - v0) * AR, 7.5); let h = w / AR;
    if (h > WID) { h = WID; w = h * AR; }
    const cu = (u0 + u1) / 2, cvv = (v0 + v1) / 2;
    u0 = Math.min(Math.max(cu - w / 2, -0.3), LEN + 0.3 - w); v0 = Math.min(Math.max(cvv - h / 2, 0), WID - h);
    let W = 0, Hh = 0, sc = 1;
    const P = (x, y) => [((LEN - x) - u0) * sc, (y - v0) * sc];
    function size() {
      const dpr = window.devicePixelRatio || 1, cw = cv.clientWidth || 320;
      cv.width = Math.round(cw * dpr); cv.height = Math.round(cw / AR * dpr); W = cv.width; Hh = cv.height; sc = W / w;
    }
    size();
    let start = performance.now(), raf = 0, on = false, paused = false, pauseAt = 0;
    const loopN = n + Math.round(hz * 1.2);
    function draw(now) {
      if (cv.clientWidth && Math.abs(cv.clientWidth * (window.devicePixelRatio || 1) - W) > 2) size();
      const k = paused ? pauseAt : ((now - start) / 1000 * hz) % loopN, i = Math.min(n - 1, Math.floor(k)), fr = Math.min(1, k - i);
      ctx.fillStyle = "#2b3038"; ctx.fillRect(0, 0, W, Hh);
      if (FIELD.complete && FIELD.naturalWidth) {
        const iw = FIELD.naturalWidth, ih = FIELD.naturalHeight;
        ctx.globalAlpha = .55; ctx.drawImage(FIELD, u0 / LEN * iw, v0 / WID * ih, w / LEN * iw, h / WID * ih, 0, 0, W, Hh); ctx.globalAlpha = 1;
      }
      const pos = (tr, j) => { let a = tr.xy[j], h = j;           // hold the last known spot through short gaps (≤ 2 s)
        while (!a && h > 0 && j - h < 2 * hz) a = tr.xy[--h];
        if (!a) return null; if (h !== j) return [a[0] / 100, a[1] / 100]; const b = tr.xy[Math.min(n - 1, j + 1)] || a; return [(a[0] + (b[0] - a[0]) * fr) / 100, (a[1] + (b[1] - a[1]) * fr) / 100]; };
      const lw = Math.max(1, W / 320);
      // trails (3 s)
      clip.tracks.forEach((tr, ti) => {
        const hi = ti === e.d_k || ti === e.t_k;
        ctx.strokeStyle = ti === e.d_k ? C_D : ti === e.t_k ? C_T : (tr.al === "red" ? "#ff8589" : "#86b6ff");
        ctx.globalAlpha = hi ? .9 : .35; ctx.lineWidth = (hi ? 2 : 1.2) * lw; ctx.beginPath(); let pen = false;
        for (let j = Math.max(0, i - Math.round(3 * hz)); j <= i; j++) { const p = tr.xy[j]; if (!p) { pen = false; continue; } const [x, y] = P(p[0] / 100, p[1] / 100); pen ? ctx.lineTo(x, y) : ctx.moveTo(x, y); pen = true; }
        ctx.stroke(); ctx.globalAlpha = 1;
      });
      // shots in flight (1 s)
      const tnow = k / hz;
      for (const s of clip.shots) {
        const age = tnow - s[0]; if (age < 0 || age > 1.1) continue;
        const f = Math.min(1, age / 0.9), [ox, oy] = P(s[1] / 100, s[2] / 100), [lx, ly] = P(s[3] / 100, s[4] / 100);
        const isT = s[6] === e.t_k && s[7];
        ctx.strokeStyle = s[5] === "m" ? "#4ade80" : s[5] === "x" ? "#fb7185" : s[5] === "p" ? "#facc15" : "#94a3b8";
        ctx.globalAlpha = isT ? .95 : .35; ctx.lineWidth = (isT ? 1.8 : 1) * lw;
        ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + (lx - ox) * f, oy + (ly - oy) * f); ctx.stroke(); ctx.globalAlpha = 1;
      }
      // robots
      const rs = 0.91 * sc / 2;
      clip.tracks.forEach((tr, ti) => {
        const p = pos(tr, i); if (!p) return;
        const [x, y] = P(p[0], p[1]), q = tr.q[i];
        ctx.fillStyle = q ? (tr.al === "red" ? "rgba(229,72,77,.85)" : "rgba(62,139,255,.85)") : "rgba(140,150,165,.6)";
        ctx.fillRect(x - rs, y - rs, 2 * rs, 2 * rs);
        if (ti === e.d_k || ti === e.t_k) { ctx.strokeStyle = ti === e.d_k ? C_D : C_T; ctx.lineWidth = 2.4 * lw; ctx.beginPath(); ctx.arc(x, y, rs * 1.75, 0, 7); ctx.stroke(); }
        ctx.fillStyle = "#fff"; ctx.font = `600 ${Math.max(9, rs * .95)}px system-ui`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(q && tr.team ? tn(tr.team) : "?", x, y);
      });
      // time bar: episode window shaded
      const by = Hh - 7 * lw, bh = 4 * lw;
      ctx.fillStyle = "rgba(0,0,0,.5)"; ctx.fillRect(0, by - 2 * lw, W, bh + 4 * lw);
      ctx.fillStyle = "rgba(250,204,21,.55)"; ctx.fillRect(clip.a / n * W, by, (clip.b - clip.a) / n * W, bh);
      ctx.fillStyle = "#fff"; ctx.fillRect(Math.min(i, n - 1) / n * W - lw, by - 2 * lw, 2 * lw, bh + 4 * lw);
      const rel = (k - clip.a) / hz;
      ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.fillRect(4 * lw, 4 * lw, 74 * lw, 15 * lw);
      ctx.fillStyle = k >= clip.a && k <= clip.b ? C_T : "#e6edf3"; ctx.font = `600 ${10 * lw}px system-ui`; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText(`${rel >= 0 ? "+" : "−"}${Math.abs(rel).toFixed(1)} s${paused ? " ❚❚" : ""}`, 8 * lw, 11.5 * lw);
      if (on) raf = requestAnimationFrame(draw);
    }
    cv.onclick = () => { if (paused) { start = performance.now() - pauseAt / hz * 1000; paused = false; } else { pauseAt = ((performance.now() - start) / 1000 * hz) % loopN; paused = true; } if (!on) draw(performance.now()); };
    return {
      play() { if (!on) { on = true; raf = requestAnimationFrame(draw); } },
      stop() { on = false; cancelAnimationFrame(raf); },
      once() { draw(performance.now()); },
    };
  }

  // ------------------------------------------------------------------ page
  async function page(ev, target) {
    css();
    const [d, e, scd] = await Promise.all([api(`${ev}/defense.json`), api(`${ev}/event.json`).catch(() => null), scoutData(ev)]);
    crumbs([[e ? e.name : ev, `#/${ev}`], ["Defense library"]]); $updated.textContent = "";
    const nick = {}; ((e && e.teams) || []).forEach(t => nick[t.key] = t.nickname);
    const eps = d.episodes || [], G = d.global || {}, PL = G.placebo || {};
    const uniq = k => [...new Set(eps.map(x => x[k]))].sort((a, b) => (+tn(a) || 0) - (+tn(b) || 0) || String(a).localeCompare(String(b)));
    const zones = [...new Set(eps.map(x => x.zone + " · " + x.side))].sort();
    const F = { target: target || "", defender: "", zone: "", shift: "", method: "", verdict: "", sort: "denied", view: "clips" };
    const opt = (k, lab, vals, fmt = x => x) => `<select data-f="${k}"><option value="">${lab}</option>${vals.map(v => `<option value="${H(v)}"${F[k] === v ? " selected" : ""}>${H(fmt(v))}</option>`).join("")}</select>`;
    $app.innerHTML = `<div class="titlebar"><div><h1>Defense library</h1>
      <div class="sub">Every time an opponent stayed within ${d.params?.radius_m ?? 1.5} m of a robot in its own scoring area for ≥ ${d.params?.min_s ?? 2} s,
        scored against that robot's own output in comparable undefended time (same zone mix and hub state). ${(d.matches || []).length} verified matches
        (${H((d.matches || []).map(shortKey).join(", "))}); both robots must have verified identity. Per-team effects rest on a handful of episodes: read them as anecdotes until n ≥ 3.</div></div>
      <a class="dbtn" href="#/${ev}/specs" style="white-space:nowrap">Spec sheets</a></div>${dataBanner(scd)}
    <div class="dx-sum">
      <div><b>${G.n ?? 0}</b><span>episodes detected</span></div>
      <div><b>${G.tested ?? 0}</b><span>with ≥ 2 pts at stake (testable)</span></div>
      <div><b>${G.succ ?? 0}<small> / ${G.tested ?? 0}</small></b><span>clearly reduced output</span></div>
      <div><b>${G.sig ?? 0}</b><span>significant after multiple-testing</span></div>
      <div title="sum observed / sum expected over testable episodes, bootstrap 90% CI over episodes"><b>${pc(G.ratio)}<small> ${G.ratio_ci ? pc(G.ratio_ci[0]) + "–" + pc(G.ratio_ci[1]) : ""}</small></b><span>defended output vs expected</span></div>
      <div title="random undefended windows scored exactly like episodes"><b>${pc(PL.pooled_ratio)}<small> · ${pc(PL.rate)} false</small></b><span>placebo windows (calibration)</span></div>
    </div>
    <div id="dxTarget"></div>
    <div class="dx-flt">${opt("target", "any target", uniq("target"), tn)}${opt("defender", "any defender", uniq("defender"), tn)}
      ${opt("zone", "any zone", zones)}${opt("shift", "any period", ["transition", "shift1", "shift2", "shift3", "shift4", "endgame"], SH)}
      ${opt("method", "any method", METHODS)}${opt("verdict", "any verdict", Object.keys(VERD), v => VERD[v][0])}
      <select data-f="sort"><option value="denied">sort: points denied</option><option value="rate">sort: Δ rate</option><option value="p">sort: p-value</option><option value="match">sort: match</option><option value="dur">sort: duration</option></select>
      <button id="dxSucc">successful only</button><span class="muted" id="dxN"></span></div>
    <div class="dx-tabs"><span data-v="clips" class="on">Clips</span><span data-v="table">Table</span><span data-v="defenders">Defenders</span></div>
    <div id="dxBody"></div>
    <div class="dx-note">How to read an effect: the grey band is the 90% range of the target's points in comparable undefended windows of the same length (placebo windows),
      the tick is their mean (expected), the dot is what it actually scored while defended (green = below the band). <b>Clearly reduced</b> = ≥ 2 points expected,
      ≤ 50% scored, one-sided p ≤ 0.10; <b>✓ significant</b> = p ≤ 0.05 and Benjamini-Hochberg q ≤ 0.2 across all testable episodes. With ~2 matches per team,
      single episodes are noisy: on placebo windows the "clearly reduced" rule fires ${pc(PL.rate)} of the time. Inactive-hub episodes are judged on shot attempts.
      Only robot-frames with identity confidence ≥ ${d.params?.id_conf_min ?? 0.5} count.</div>`;

    let succOnly = false, shown = 24, focus = null;
    const obs = new IntersectionObserver(ents => ents.forEach(en => { const p = en.target.__player; if (p) en.isIntersecting ? p.play() : p.stop(); }), { rootMargin: "100px" });
    cleanup.push(() => { obs.disconnect(); document.querySelectorAll(".dx-clip canvas").forEach(c => c.__player && c.__player.stop()); });

    const filtered = () => {
      let r = eps.filter(x => (!F.target || x.target === F.target) && (!F.defender || x.defender === F.defender) && (!F.zone || x.zone + " · " + x.side === F.zone)
        && (!F.shift || x.shift === F.shift) && (!F.method || x.method === F.method) && (!F.verdict || x.verdict === F.verdict) && (!succOnly || x.verdict === "success"));
      const key = {
        denied: x => -(x.effect.denied ?? -1e9), rate: x => x.effect.d_rate ?? 1e9, p: x => pOf(x.effect) ?? 2,
        match: x => x.id, dur: x => -x.dur,
      }[F.sort];
      return r.sort((a, b) => { const p = key(a), q = key(b); return p < q ? -1 : p > q ? 1 : 0; });
    };
    function cardHTML(x) {
      return `<div class="dx-clip ${x.verdict === "success" ? "success" : ""}${focus === x.id ? " focus" : ""}" id="dx-${x.id}">
        <canvas data-id="${x.id}" title="click to pause"></canvas>
        <div class="dx-cb"><div class="dx-ct"><span><b style="color:${C_D}">${tn(x.defender)}</b> on <a href="#/${ev}/specs/${x.target}"><b style="color:${C_T}">${tn(x.target)}</b></a></span>${vpill(x)}</div>
          <div class="dx-cs">${x.m} · ${SH(x.shift)} (${x.active ? "hub active" : "hub inactive"}) · ${f1(x.dur)} s · <b>${x.method}</b> · ${H(x.zone)} ${x.side}${x.double ? " · double-team" : ""}${x.maybe_collecting ? ` · <span class="dx-v warn" title="defender spent most of the episode in the target's outpost corner with little contact: it may have been collecting spilled fuel rather than defending">maybe collecting</span>` : ""}</div>
          <div class="dx-ce">${effGlyph(x.effect)}<span>${effText(x.effect)}</span></div>
          <div class="dx-cs">p ${f1(pOf(x.effect), 2)}${x.effect.q != null ? ` · q ${f1(x.effect.q, 2)}` : ""} · contact ${f1(x.feat.contact_s)} s${x.disp.pushed_m ? ` · pushed ${f1(x.disp.pushed_m)} m` : ""}${x.cycle.delta != null ? ` · cycle ${x.cycle.delta > 0 ? "+" : ""}${f1(x.cycle.delta)} s` : ""}</div>
          <div class="dx-cl"><a href="#/${ev}/a/${x.key}?t=${f1(x.clip.v0)}">Open video at ${f1(x.t_match, 0)} s</a><a href="#/${ev}/defense/${x.target}">vs ${tn(x.target)}</a></div></div></div>`;
    }
    async function mount(cv) {
      const x = eps.find(y => y.id === cv.dataset.id); if (!x) return;
      try { const c = await getClip(ev, x); if (!cv.isConnected) return; cv.__player = clipPlayer(cv, c, x); cv.__player.once(); obs.observe(cv); }
      catch (err) { const g = cv.getContext("2d"); cv.width = 320; cv.height = 200; g.fillStyle = "#8b98a8"; g.font = "12px system-ui"; g.fillText("clip unavailable", 12, 20); }
    }
    function body() {
      const r = filtered(), el = document.getElementById("dxBody");
      document.getElementById("dxN").textContent = `${r.length} episode${r.length === 1 ? "" : "s"}`;
      document.querySelectorAll(".dx-clip canvas").forEach(c => { c.__player && c.__player.stop(); obs.unobserve(c); });
      if (F.view === "clips") {
        el.innerHTML = r.length ? `<div class="dx-grid">${r.slice(0, shown).map(cardHTML).join("")}</div>${r.length > shown ? `<button class="dx-more" id="dxMore">Show ${Math.min(24, r.length - shown)} more (${r.length - shown} left)</button>` : ""}`
          : `<div class="empty">No episodes match these filters.</div>`;
        el.querySelectorAll(".dx-clip canvas").forEach(mount);
        const mb = document.getElementById("dxMore"); if (mb) mb.onclick = () => { shown += 24; body(); };
        if (focus) { const fc = document.getElementById("dx-" + focus); fc && fc.scrollIntoView({ block: "center" }); }
      } else if (F.view === "table") {
        el.innerHTML = `<div class="tablewrap"><table class="dx-tbl"><thead><tr><th>Ep.</th><th>Defender</th><th>Target</th><th class="num">Clock</th><th>Period</th><th class="num">Dur</th><th>Method</th><th>Zone</th>
          <th class="num" title="observed / expected points (or attempts when the hub was inactive)">Obs/Exp</th><th class="num">Δ /s</th><th class="num">p</th><th class="num">q</th><th class="num">Contact s</th><th class="num">Pushed m</th><th class="num">Cycle Δ</th><th>Verdict</th><th></th></tr></thead>
          <tbody>${r.map(x => { const ef = x.effect, m = ef.metric === "attempts" ? "att" : "pts";
            return `<tr class="clk" data-id="${x.id}"><td>${x.id}</td><td>${tn(x.defender)}</td><td><a href="#/${ev}/specs/${x.target}">${tn(x.target)}</a></td><td class="num">${f1(x.t_match, 0)}</td>
            <td>${SH(x.shift)}${x.active ? "" : " (inact.)"}</td><td class="num">${f1(x.dur)}</td><td>${x.method}</td><td>${H(x.zone)} ${x.side}</td>
            <td class="num">${ef[m + "_obs"]}/${f1(ef[m + "_exp"])}</td><td class="num">${f1(ef.d_rate, 2)}</td><td class="num">${f1(pOf(ef), 2)}</td><td class="num">${f1(ef.q, 2)}</td>
            <td class="num">${f1(x.feat.contact_s)}</td><td class="num">${f1(x.disp.pushed_m)}</td><td class="num">${f1(x.cycle.delta)}</td><td>${vpill(x)}</td>
            <td><a href="#/${ev}/a/${x.key}?t=${f1(x.clip.v0)}">video</a></td></tr>`; }).join("")}</tbody></table></div>`;
        el.querySelectorAll("tr.clk").forEach(tr => tr.onclick = ev_ => { if (ev_.target.tagName === "A") return; focus = tr.dataset.id; F.view = "clips";
          const r2 = filtered(), i = r2.findIndex(y => y.id === focus); if (i >= shown) shown = i + 1; tabs(); body(); });
      } else {
        const rows = Object.entries(d.defenders || {}).filter(([k]) => !F.defender || k === F.defender).sort((a, b) => (b[1].denied ?? 0) - (a[1].denied ?? 0));
        el.innerHTML = `<div class="tablewrap"><table class="dx-tbl"><thead><tr><th>Defender</th><th class="num">Episodes</th><th class="num">Seconds</th><th class="num">Testable</th><th class="num">Clear</th><th class="num">Sig.</th>
          <th class="num" title="victims' points while defended / expected">Victims' output</th><th class="num">90% CI</th><th class="num">Pts denied</th><th>Methods</th><th>Targets</th></tr></thead>
          <tbody>${rows.map(([k, p]) => `<tr><td><a href="#" data-def="${k}"><b>${tn(k)}</b></a> <span class="muted small">${H((nick[k] || "").slice(0, 18))}</span></td><td class="num">${p.n}</td><td class="num">${f1(p.secs, 0)}</td>
            <td class="num">${p.tested}</td><td class="num">${p.succ}</td><td class="num">${p.sig}</td><td class="num">${pc(p.ratio)}</td><td class="num">${p.ci ? pc(p.ci[0]) + "–" + pc(p.ci[1]) : "–"}</td>
            <td class="num">${f1(p.denied)}</td><td>${Object.entries(p.methods || {}).map(([m, c]) => `${m} ${c}`).join(", ")}</td>
            <td>${Object.entries(p.targets || {}).map(([t, c]) => `<a href="#/${ev}/defense/${t}">${tn(t)}</a>${c > 1 ? "×" + c : ""}`).join(" ")}</td></tr>`).join("")}</tbody></table></div>
          <div class="dx-note">Victims' output pools all testable episodes of a defender: Σ observed / Σ expected points. CI = bootstrap over episodes (shown with ≥ 3 testable episodes).</div>`;
        el.querySelectorAll("a[data-def]").forEach(a => a.onclick = ev_ => { ev_.preventDefault(); F.defender = a.dataset.def; F.view = "clips"; syncSel(); tabs(); body(); });
      }
    }
    function targetPanel() {
      const el = document.getElementById("dxTarget");
      if (!F.target) {
        const top = Object.entries(d.targets || {}).filter(([, t]) => t.received && t.received.n).sort((a, b) => b[1].received.n - a[1].received.n);
        el.innerHTML = `<div class="dx-panel"><h2>How to defend… <small>pick a scorer</small></h2><div class="dx-tchips">${top.map(([k, t]) =>
          `<a href="#/${ev}/defense/${k}" title="${t.received.n} episodes, ${t.received.succ} clearly reduced">${tn(k)} <span class="muted">${t.received.n}</span></a>`).join("")}</div></div>`;
        return;
      }
      const t = (d.targets || {})[F.target];
      if (!t) { el.innerHTML = `<div class="dx-panel"><h2>How to defend ${tn(F.target)}</h2><div class="muted">No tracked data for this team.</div></div>`; return; }
      const R = t.received || {}, B = t.baseline || {};
      const row = (k, p) => `<tr><td>${H(k)}</td><td class="num">${p.n}</td><td class="num">${p.tested}</td><td class="num">${p.succ}${p.sig ? ` (${p.sig}✓)` : ""}</td><td class="num">${p.obs}/${f1(p.exp)}</td><td class="num">${pc(p.ratio)}</td></tr>`;
      const tbl = (title, obj) => { const e2 = Object.entries(obj).filter(([, p]) => p.n); return e2.length ? `<table class="dx-tbl"><thead><tr><th>${title}</th><th class="num">Ep.</th><th class="num">Test.</th><th class="num">Clear</th><th class="num">Pts obs/exp</th><th class="num">Output</th></tr></thead><tbody>${e2.map(([k, p]) => row(k, p)).join("")}</tbody></table>` : ""; };
      el.innerHTML = `<div class="dx-panel"><h2>How to defend ${tn(F.target)} <small>${H(nick[F.target] || "")} · <a href="#/${ev}/specs/${F.target}">spec sheet</a> · <a href="#/${ev}/defense">all targets</a></small></h2>
        <div class="dx-2"><div><ul class="dx-adv">${(t.advice || []).map(a => `<li>${H(a)}</li>`).join("")}</ul>
          <div class="dx-note">Baseline: ${B.pts_per_active_s != null ? f1(B.pts_per_active_s * 60) : "–"} pts per active-hub minute over ${f1(B.active_s, 0)} s of free active time
           (${f1(B.free_s, 0)} s free teleop). Defended: ${R.n} episodes, output ${pc(R.ratio)} of expected${R.ci ? ` (90% CI ${pc(R.ci[0])}–${pc(R.ci[1])})` : ""}.</div></div>
        <div>${tbl("Method", t.by_method || {})}${tbl("Where", t.by_zone || {})}${tbl("Hub state", t.by_state || {})}
          ${(t.defenders || []).length ? `<div class="dx-note">Defenders faced: ${t.defenders.map(x => `<b>${tn(x.team)}</b> ${x.n}× (${pc(x.ratio)})`).join(", ")}</div>` : ""}</div></div></div>`;
    }
    function syncSel() { document.querySelectorAll(".dx-flt select").forEach(s => s.value = F[s.dataset.f] || ""); }
    function tabs() { document.querySelectorAll(".dx-tabs span").forEach(s => s.classList.toggle("on", s.dataset.v === F.view)); }
    document.querySelectorAll(".dx-flt select").forEach(s => s.onchange = () => {
      F[s.dataset.f] = s.value || (s.dataset.f === "sort" ? "denied" : ""); shown = 24; focus = null;
      if (s.dataset.f === "target") { history.replaceState(null, "", `#/${ev}/defense${F.target ? "/" + F.target : ""}`); targetPanel(); }
      body();
    });
    document.getElementById("dxSucc").onclick = ev_ => { succOnly = !succOnly; ev_.target.classList.toggle("on", succOnly); shown = 24; body(); };
    document.querySelectorAll(".dx-tabs span").forEach(s => s.onclick = () => { F.view = s.dataset.v; tabs(); body(); });
    const onR = () => document.querySelectorAll(".dx-clip canvas").forEach(c => c.__player && c.__player.once());
    window.addEventListener("resize", onR); cleanup.push(() => window.removeEventListener("resize", onR));
    FIELD.complete || FIELD.addEventListener("load", onR, { once: true });
    targetPanel(); body();
  }

  CS.route("defense", (ev, target) => page(ev, target));
  CS.nav({ label: "Defense", href: ev => `#/${ev}/defense`, order: 45 });
})();
