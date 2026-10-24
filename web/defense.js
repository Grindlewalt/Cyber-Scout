"use strict";
// defense.js: DEFENSE, the last tab of Selection (#/<ev>/selection/defense[/<target team>|/defenders]; the old #/<ev>/defense links
// redirect there). Default view = "Best defenders": a ranked list (points denied per match, episodes, contact time, the share of
// its output a defended team kept), each defender expandable to its episodes as top-down clips. The library below it: "Defend a
// team" answers and the strongest episodes. CS.defensePane(ev, target, host) draws it (rankings.js). Data: api/<ev>/defense.json from
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
  .dx{display:flex;flex-direction:column;gap:14px}
  .dx .titlebar{align-items:center}.dx h1{margin:0}
  .dx-ev{font-size:var(--fs-body);color:var(--ink);line-height:1.5}
  .dx .sc-banner{margin:0}
  .dx-q{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
  .dx-seg{width:auto;grid-template-columns:auto auto}.dx-seg button{padding:4px 14px}
  .dx-pick{flex:1 1 240px;max-width:380px;min-width:0}.dx-pick select{min-height:36px;width:100%;font-size:var(--fs-body);padding:6px 8px}
  .dx-ans{padding:16px 18px}
  .dx-ah{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}.dx-ah h2{margin:0}
  .dx-nick{font-weight:400;font-size:var(--fs-body)}.dx-small{margin-left:auto;font-size:var(--fs-foot);color:var(--ink-2)}
  .dx-ret{display:grid;grid-template-columns:auto minmax(0,1fr);gap:20px;align-items:center;margin:4px 0 12px}
  .dx-retn b{display:block;font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:52px;font-weight:700;line-height:1;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
  .dx-retn span{display:block;margin-top:var(--sp-1);font-size:var(--fs-foot);color:var(--ink-2);font-weight:500}
  .dx-retb{max-width:420px}
  .dx-rb{position:relative;height:10px;background:var(--surface-2);border-radius:5px}
  .dx-rb .rg{position:absolute;top:0;bottom:0;background:var(--line-strong);border-radius:5px}
  .dx-rb .one{position:absolute;top:-4px;bottom:-4px;width:2px;margin-left:-1px;background:var(--ink-3)}
  .dx-rb .mn{position:absolute;top:-3px;width:16px;height:16px;margin-left:-8px;border-radius:50%;background:var(--ink);border:2px solid var(--surface)}
  .dx-rb.sm{height:6px;width:120px;flex:none}.dx-rb.sm .mn{width:10px;height:10px;margin-left:-5px;top:-2px;border-width:1px}.dx-rb.sm .one{top:-3px;bottom:-3px}
  .dx-rbl{position:relative;height:16px;font-size:11px;color:var(--ink-3);margin-top:4px}.dx-rbl span{position:absolute}.dx-rbl span:first-child{left:0}.dx-rbl span:last-child{right:0}.dx-rbl span:nth-child(2){transform:translateX(-50%)}
  .dx-rett{font-size:var(--fs-foot);color:var(--ink-2);margin-top:4px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  .dx-tag{font-size:11px;padding:0 6px;border-radius:var(--r-tag);border:1px dashed var(--ink-2);white-space:nowrap;color:var(--ink-2)}.dx-tag.measured{border-style:solid;color:var(--ink)}
  .dx-adv{margin:0 0 10px;padding-left:18px;font-size:var(--fs-body);line-height:1.6}
  .dx-defs{font-size:var(--fs-foot);margin-bottom:8px}
  .dx-more2 summary{cursor:pointer;font-size:var(--fs-foot);font-weight:600;padding:6px 0;color:var(--ink)}
  .dx-3{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:6px}
  .dx-rank{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr));gap:2px 16px}.dx-rank li{min-width:0}
  .dx-rank button{display:flex;align-items:center;gap:10px;width:100%;min-height:40px;background:none;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:6px 4px;text-align:left;color:var(--ink);font-size:var(--fs-body)}
  .dx-rank button:hover{background:var(--hover)}
  .dx-rk{font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:var(--fs-callout);font-weight:700;min-width:44px;font-variant-numeric:tabular-nums}.dx-rank .dx-nick{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:var(--fs-foot)}
  .dx-rv{min-width:96px;text-align:right;font-size:var(--fs-foot)}
  .dx-clips{display:flex;flex-direction:column;gap:10px}
  .dx-ch{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.dx-ch h2{margin:0}.dx-sp{flex:1}
  .dx-all{margin-left:auto}
  .dx-vt{width:auto;grid-template-columns:auto auto}.dx-vt button{padding:2px 12px;min-height:28px}
  .dx-fp{width:min(300px,calc(100vw - 16px))}
  .dx-fr{display:grid;grid-template-columns:80px minmax(0,1fr);align-items:center;gap:8px;margin-bottom:6px}.dx-fr span{color:var(--ink-2)}.dx-fr select{width:100%}
  .dx-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
  .dx-clip{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-m);overflow:hidden;min-width:0}
  .dx-clip.success{border-color:color-mix(in srgb,var(--good) 55%,transparent)}.dx-clip.focus{box-shadow:0 0 0 2px var(--accent)}
  .dx-clip canvas{display:block;width:100%;aspect-ratio:16/10;background:#2b3038;cursor:pointer}
  .dx-cb{padding:var(--sp-2) var(--sp-3) var(--sp-3);font-size:12.5px;display:flex;flex-direction:column;gap:4px}
  .dx-ct{display:flex;justify-content:space-between;gap:6px;align-items:center}
  .dx-ct b{font-variant-numeric:tabular-nums}
  .dx-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 2px;vertical-align:0;box-shadow:0 0 0 1px color-mix(in srgb,var(--ink) 25%,transparent)}
  .dx-cs{color:var(--muted);font-size:12px}
  .dx-ce{display:flex;gap:8px;align-items:center;font-variant-numeric:tabular-nums;font-size:12px;flex-wrap:wrap}
  .dx-cl{display:flex;gap:8px;align-items:center;font-size:12.5px}
  .dx-v{display:inline-block;padding:0 6px;border-radius:var(--r-tag);font-size:11px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
  .dx-v.good{color:var(--good);border-color:currentColor}.dx-v.warn{color:var(--warn);border-color:var(--caution-line)}.dx-v.bad{color:var(--bad);border-color:currentColor}
  .dx-v.sig{background:color-mix(in srgb,var(--good) 15%,transparent)}
  .dx-tbl td,.dx-tbl th{padding:5px 6px;font-size:12.5px}
  .dx-tbl tr.clk{cursor:pointer}
  .dx-more{display:block;margin:4px auto;padding:6px 16px}.dx-defall{margin-top:8px}
  .dxd-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
  .dxd{position:relative;border:1px solid var(--line);border-radius:var(--r-m);background:var(--surface);overflow:hidden;transition:border-color .15s var(--ease)}
  .dxd:hover{border-color:var(--line-strong)}.dxd.open{border-color:var(--accent)}.dxd.thin .dxd-main,.dxd.thin .dxd-s{opacity:.72}
  .dxd-row{position:relative;display:grid;grid-template-columns:34px minmax(150px,1.3fr) minmax(170px,1.5fr) 82px 92px 118px auto 22px;align-items:center;gap:0 12px;padding:10px 14px;min-height:56px}
  .dxd-rk{font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:var(--fs-t3);font-weight:700;color:var(--ink-2);font-variant-numeric:tabular-nums}
  .dxd-tog{all:unset;cursor:pointer;display:inline-flex;align-items:baseline;gap:8px;min-width:0;max-width:100%}
  .dxd-tog::after{content:"";position:absolute;inset:0;border-radius:var(--r-m)}.dxd-tog:focus-visible::after{outline:2px solid var(--accent);outline-offset:-2px}
  .dxd-tog b{font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:21px;font-weight:700;font-variant-numeric:tabular-nums}
  .dxd-tog .dx-nick{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:var(--fs-foot)}
  .dxd-us{font-size:10.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;padding:1px 6px;border-radius:var(--r-tag);background:var(--accent-fill);color:var(--on-accent)}
  .dxd-main{display:flex;flex-direction:column;gap:5px;min-width:0}.dxd-v b{font-family:var(--font-num);font-stretch:var(--num-stretch);font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}
  .dxd-v small{margin-left:6px;font-size:var(--fs-cap);color:var(--ink-2)}
  .dxd-bar{display:block;height:6px;border-radius:3px;background:var(--surface-3);overflow:hidden}.dxd-bar i{display:block;height:100%;background:var(--g-def,color-mix(in srgb,var(--caution) 55%,var(--bad)));border-radius:3px}
  .dxd-s{display:flex;flex-direction:column;font-variant-numeric:tabular-nums;font-size:var(--fs-body)}.dxd-s b{font-weight:600}.dxd-s b small,.dxd-s>small{font-size:var(--fs-cap);color:var(--ink-2);font-weight:400}
  .dxd-keep .dx-rb.sm{width:100%;margin-top:6px}.dxd-t{justify-self:end;position:relative;z-index:1}
  .dxd-ch{color:var(--ink-3);display:grid;place-items:center;transition:transform .2s var(--ease)}.dxd-ch svg{width:18px;height:18px}.dxd.open .dxd-ch{transform:rotate(90deg);color:var(--accent)}
  .dxd-p{padding:6px 14px 14px;border-top:1px dashed var(--line-strong)}
  .dxd-facts{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:var(--fs-foot);color:var(--ink-2);padding:8px 0 12px}.dxd-facts a{font-weight:600}
  @media (max-width:1000px){.dxd-row{grid-template-columns:30px minmax(0,1fr) minmax(130px,1.2fr) 80px 22px;}.dxd-ct,.dxd-keep,.dxd-t{display:none}}
  @media (max-width:640px){.dxd-row{grid-template-columns:28px minmax(0,1fr) auto 22px;grid-template-areas:"rk who ep ch" "rk main main main";gap:6px 10px}.dxd-rk{grid-area:rk;align-self:start;padding-top:2px}.dxd-who{grid-area:who}.dxd-main{grid-area:main}.dxd-ct,.dxd-keep,.dxd-t{display:none}.dxd-ep{grid-area:ep;text-align:right}.dxd-ch{grid-area:ch}}
  @media (max-width:640px){.dx-ret{grid-template-columns:1fr;gap:8px}.dx-retn b{font-size:38px}.dx-rank{grid-template-columns:minmax(0,1fr)}.dx-rb.sm{width:70px}.dx-grid{grid-template-columns:1fr}.dx-all{margin-left:0}}
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
  // Built around the strategist's two questions: "how do I defend team X?" (pick a team, get the answer + best clips) and
  // "who defends well?". Event-level statistics are one honest line with the details behind "?"; filters live in one popover.
  async function page(ev, target, host) {
    css();
    const root = host || $app, base = `#/${ev}/selection/defense`;
    const [d, e, scd] = await Promise.all([api(`${ev}/defense.json`), api(`${ev}/event.json`).catch(() => null), scoutData(ev)]);
    const mode = !target || target === "defenders" ? "defenders" : "team";
    if (target === "defenders" || target === "team") target = "";
    const nick = {}; ((e && e.teams) || []).forEach(t => nick[t.key] = t.nickname);
    const eps = d.episodes || [], G = d.global || {}, PL = G.placebo || {}, TM = d.teams_meta || {}, EVR = TM.event || null;
    const RET = Object.fromEntries((d.teams || []).filter(t => t.retention).map(t => [t.team, t.retention]));
    const uniq = k => [...new Set(eps.map(x => x[k]))].sort((a, b) => (+tn(a) || 0) - (+tn(b) || 0) || String(a).localeCompare(String(b)));
    const zones = [...new Set(eps.map(x => x.zone + " · " + x.side))].sort();
    const F = { target: target || "", defender: "", zone: "", shift: "", method: "", verdict: "", sort: "denied", view: "clips" };
    const targets = Object.entries(d.targets || {}).filter(([, t]) => t.received && t.received.n).map(([k]) => k).sort((a, b) => +tn(a) - +tn(b));
    const nm = k => `${tn(k)}${nick[k] ? " " + nick[k] : ""}`;

    // one honest line for the whole event; every number and the method behind "?"
    const evHelp = `<span class="hl"><b>What counts as defense:</b> an opponent stayed within ${d.params?.radius_m ?? 1.5} m of a robot in its own scoring area for ≥ ${d.params?.min_s ?? 2} s. Each episode is scored against that robot's own output in comparable undefended time (same zone mix and hub state). Both robots must have verified identity (frames with identity confidence ≥ ${d.params?.id_conf_min ?? 0.5}).</span>
      <span class="hl"><b>This event:</b> ${G.n ?? 0} episodes detected · ${G.tested ?? 0} with ≥ 2 pts at stake (testable) · ${G.succ ?? 0} clearly reduced output · ${G.sig ?? 0} significant after multiple testing · defended output ${pc(G.ratio)} of expected (${G.ratio_ci ? pc(G.ratio_ci[0]) + "–" + pc(G.ratio_ci[1]) : "–"}, bootstrap 90%)${EVR ? ` · placebo-controlled retention ${pc(EVR.est)} (${pc(EVR.lo)}–${pc(EVR.hi)}) over ${TM.placebo_windows} placebo windows` : ""} · placebo windows score ${pc(PL.pooled_ratio)} with ${pc(PL.rate)} false "clearly reduced" calls (calibration).</span>
      <span class="hl"><b>Read per-team numbers as anecdotes until n ≥ 3.</b> ${TM.discoveries ?? 0} of ${TM.tests ?? 0} team estimates are significant after multiple testing.</span>
      <span class="hl"><b>Matches used (${(d.matches || []).length} verified):</b> ${esc((d.matches || []).map(shortKey).join(", "))}.</span>`;
    // the event-wide claim needs evidence behind it: below 3 testable episodes there is no headline number (MATCH-17, SETUP-11);
    // an interval that still includes "no effect" says so; the interval is only printed when there is one
    const nT = G.tested ?? 0, eps_ = n => `${n} testable episode${n === 1 ? "" : "s"}`;
    const ci = EVR ? [EVR.lo, EVR.hi] : G.ratio_ci || null, est = EVR ? EVR.est : G.ratio;
    const evLine = nT < 3 || est == null
      ? `<b>Too few defended episodes to say how much defense slows scorers here</b> <span class="muted">(${eps_(nT)}${nT ? "; at least 3 are needed for an estimate" : ""})</span>. The clips below are single examples, not a measurement. ${CS.help(evHelp, "How the defense numbers are measured")}`
      : `Defended scorers kept <b>${pc(est)}</b> of their expected output across the event <span class="muted">(${ci ? `${pc(ci[0])}–${pc(ci[1])}${EVR ? ", placebo-controlled" : ""}; ` : ""}${eps_(nT)}${ci && ci[1] >= 1 ? "; not significant: the range includes no effect" : ""})</span>${TM.discoveries === 0 ? `; no single team's effect is significant yet` : ""}. ${CS.help(evHelp, "How the defense numbers are measured")}`;
    const effHelp = `<span class="hl">Each clip is a top-down replay of the episode: the <i class="dx-dot" style="background:${C_D}" aria-hidden="true"></i><b>defender</b> and the <i class="dx-dot" style="background:${C_T}" aria-hidden="true"></i><b>target</b> are circled, the yellow strip on the time bar is the episode. Click a clip to pause.</span>
      <span class="hl"><b>Effect bar:</b> the grey band is the 90% range of the target's points in comparable undefended windows of the same length, the tick is their mean (expected), the dot is what it actually scored while defended (green = below the band).</span>
      <span class="hl"><b>Clearly reduced</b> = ≥ 2 points expected, ≤ 50% scored, one-sided p ≤ 0.10; <b>✓ significant</b> = p ≤ 0.05 and Benjamini-Hochberg q ≤ 0.2 across all testable episodes. With ~2 matches per team single episodes are noisy: on placebo windows the "clearly reduced" rule fires ${pc(PL.rate)} of the time. Inactive-hub episodes are judged on shot attempts.</span>`;

    root.innerHTML = `<div class="dx">
      <div class="dx-ev">${evLine}</div>
      ${dataBanner(scd)}
      <div class="dx-q">
        <div class="mv-seg dx-seg" role="radiogroup" aria-label="Question">
          <button type="button" role="radio" aria-checked="${mode === "defenders"}" data-mode="defenders">Best defenders</button>
          <button type="button" role="radio" aria-checked="${mode === "team"}" data-mode="team">Defend a team</button>
        </div>
        ${mode === "team" ? `<label class="dx-pick"><span class="vh">Team to defend</span><select id="dxTeam"><option value="">Choose a team…</option>${targets.map(k => `<option value="${H(k)}"${F.target === k ? " selected" : ""}>${H(nm(k))}</option>`).join("")}</select></label>` : ""}
      </div>
      <div id="dxAnswer"></div>
      <section class="dx-clips" id="dxClips"></section>
    </div>`;

    let succOnly = false, shown = 3, focus = null, expanded = false, defAll = false;
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
        <div class="dx-cb"><div class="dx-ct"><span><i class="dx-dot" style="background:${C_D}" aria-hidden="true"></i><b>${tn(x.defender)}</b> on <i class="dx-dot" style="background:${C_T}" aria-hidden="true"></i><b>${tn(x.target)}</b></span>${vpill(x)}</div>
          <div class="dx-cs">${x.m} · ${SH(x.shift)}${x.active ? "" : " (hub inactive)"} · ${f1(x.dur)} s · ${x.method} · ${H(x.zone)} ${x.side}${x.double ? " · double-team" : ""}${x.maybe_collecting ? ` · <span class="dx-v warn" title="defender spent most of the episode in the target's outpost corner with little contact: it may have been collecting spilled fuel rather than defending">maybe collecting</span>` : ""}</div>
          <div class="dx-ce">${effGlyph(x.effect)}<span>${effText(x.effect)}</span></div>
          <div class="dx-cl"><a href="#/${ev}/a/${x.key}?t=${f1(x.clip.v0)}">Watch at ${f1(x.t_match, 0)} s</a>
            ${CS.help(`p ${f1(pOf(x.effect), 2)}${x.effect.q != null ? ` · q ${f1(x.effect.q, 2)}` : ""} · contact ${f1(x.feat.contact_s)} s${x.disp.pushed_m ? ` · pushed ${f1(x.disp.pushed_m)} m` : ""}${x.cycle.delta != null ? ` · cycle ${x.cycle.delta > 0 ? "+" : ""}${f1(x.cycle.delta)} s` : ""}`, "Episode statistics")}</div></div></div>`;
    }
    async function mount(cv) {
      const x = eps.find(y => y.id === cv.dataset.id); if (!x) return;
      try { const c = await getClip(ev, x); if (!cv.isConnected) return; cv.__player = clipPlayer(cv, c, x); cv.__player.once(); obs.observe(cv); }
      catch (err) { const g = cv.getContext("2d"); cv.width = 320; cv.height = 200; g.fillStyle = "#8b98a8"; g.font = "12px system-ui"; g.fillText("clip unavailable", 12, 20); }
    }

    // ---- the answer for one team
    const retBar = (r, est) => {   // 0..150% scale, 100% = defense made no difference
      const X = v => Math.max(0, Math.min(100, v / 1.5 * 100)).toFixed(1);
      return `<div class="dx-rb" role="img" aria-label="keeps ${pc(r ? r.est : est)}${r ? `, 90% range ${pc(r.lo)} to ${pc(r.hi)}` : ""}">
        ${r ? `<i class="rg" style="left:${X(r.lo)}%;width:${Math.max(1, X(r.hi) - X(r.lo))}%"></i>` : ""}<i class="one" style="left:${X(1)}%"></i><i class="mn" style="left:${X(r ? r.est : est)}%"></i></div>
        <div class="dx-rbl"><span>0%</span><span style="left:${X(1)}%">no effect</span><span>150%</span></div>`;
    };
    function answer() {
      const el = document.getElementById("dxAnswer");
      if (mode === "defenders") { el.innerHTML = defendersPanel(); wireDefenders(); return; }
      if (!F.target) { el.innerHTML = overview(); el.querySelectorAll("[data-pick]").forEach(b => b.onclick = () => pick(b.dataset.pick)); return; }
      const t = (d.targets || {})[F.target];
      if (!t) { el.innerHTML = `<div class="card dx-ans"><h2>How to defend ${tn(F.target)}</h2><div class="muted">No tracked defense data for this team.</div></div>`; return; }
      const R = t.received || {}, B = t.baseline || {}, rr = RET[F.target];
      const meas = rr && rr.significant;
      const tag = rr ? (meas ? `<span class="dx-tag measured" title="significant after adjusting for ${TM.tests} team tests">measured</span>` : `<span class="dx-tag" title="not significant: plan with the event-wide ${EVR ? pc(EVR.est) : ""} instead">not significant</span>`)
        : `<span class="dx-tag" title="fewer than 3 testable episodes">too few episodes</span>`;
      const retHelp = rr ? `<span class="hl">Placebo-controlled retention ${pc(rr.est)} (90% CI ${pc(rr.lo)}–${pc(rr.hi)}; adjusted for ${TM.tests} team tests ${pc(rr.lo_adj)}–${pc(rr.hi_adj)}; n_eff ${rr.n_eff}; ${rr.n_testable} testable of ${rr.n_episodes} episodes).</span>
          <span class="hl">${meas ? "Measured: defense reliably cuts this team's output." : `Not significant: use the event-wide ${EVR ? pc(EVR.est) : ""} (an assumption) rather than this team's own number.`}</span>`
        : `<span class="hl">Fewer than 3 testable episodes: no team-specific estimate${EVR ? `; plan with the event-wide placebo-controlled retention ${pc(EVR.est)} (${pc(EVR.lo)}–${pc(EVR.hi)}), an assumption` : ""}.</span>`;
      const base = `<span class="hl">Baseline: ${B.pts_per_active_s != null ? f1(B.pts_per_active_s * 60) : "–"} pts per active-hub minute over ${f1(B.active_s, 0)} s of free active time (${f1(B.free_s, 0)} s free teleop). Defended: ${R.n} episodes, output ${pc(R.ratio)} of expected${R.ci ? ` (90% CI ${pc(R.ci[0])}–${pc(R.ci[1])})` : ""}.</span>`;
      const defs = (t.defenders || []).filter(x => x.tested >= 3).sort((a, b) => (a.ratio ?? 9) - (b.ratio ?? 9)).slice(0, 3);
      const thin = (t.defenders || []).filter(x => x.tested && x.tested < 3);
      const row = (k, p) => `<tr><td>${H(k)}</td><td class="num">${p.n}</td><td class="num">${p.tested}</td><td class="num">${p.succ}${p.sig ? ` (${p.sig}✓)` : ""}</td><td class="num">${p.obs}/${f1(p.exp)}</td><td class="num">${pc(p.ratio)}</td></tr>`;
      const tbl = (title, obj) => { const e2 = Object.entries(obj || {}).filter(([, p]) => p.n); return e2.length ? `<div class="tablewrap"><table class="dx-tbl"><thead><tr><th>${title}</th><th class="num" title="episodes">Ep.</th><th class="num" title="testable episodes">Test.</th><th class="num" title="clearly reduced">Clear</th><th class="num">Pts obs/exp</th><th class="num">Output</th></tr></thead><tbody>${e2.map(([k, p]) => row(k, p)).join("")}</tbody></table></div>` : ""; };
      el.innerHTML = `<section class="card dx-ans" aria-labelledby="dxAnsH">
        <div class="dx-ah"><h2 id="dxAnsH">How to defend ${tn(F.target)} <span class="muted dx-nick">${H(nick[F.target] || "")}</span></h2>
          <a class="dx-small" href="#/${ev}/teams/${F.target}">All fields of ${tn(F.target)}</a></div>
        <div class="dx-ret"><div class="dx-retn"><b>${rr ? pc(rr.est) : pc(R.ratio)}</b><span>of its scoring kept when defended</span></div>
          <div class="dx-retb">${retBar(rr, R.ratio ?? 1)}<div class="dx-rett">${rr ? `90% range ${pc(rr.lo)}–${pc(rr.hi)}` : `raw, ${R.tested ?? 0} testable episode${R.tested === 1 ? "" : "s"}`} ${tag} ${CS.help(retHelp + base, "How sure is this?")}</div></div></div>
        ${(t.advice || []).length ? `<ul class="dx-adv">${t.advice.map(a => `<li>${H(a)}</li>`).join("")}</ul>` : ""}
        ${defs.length ? `<div class="dx-defs"><span class="muted">Defenders that slowed it most:</span> ${defs.map(x => `<b>${tn(x.team)}</b> <span class="muted">${pc(x.ratio)} kept, ${x.tested} testable</span>`).join(" · ")}</div>`
          : thin.length ? `<div class="dx-defs"><span class="muted">Defended by</span> ${thin.map(x => `<b>${tn(x.team)}</b>`).join(", ")} <span class="muted">(${thin.length === 1 ? "only" : "each"} fewer than 3 testable episodes: too few to rank who slowed it most)</span></div>` : ""}
        <details class="dx-more2"><summary>By method, place and hub state</summary><div class="dx-3">${tbl("Method", t.by_method)}${tbl("Where", t.by_zone)}${tbl("Hub state", t.by_state)}</div></details>
      </section>`;
    }
    function overview() {
      const rows = Object.entries(RET).filter(([k]) => (d.targets || {})[k]).sort((a, b) => a[1].est - b[1].est);
      const X = v => Math.max(0, Math.min(100, v / 1.5 * 100)).toFixed(1);
      if (!rows.length) return `<section class="card dx-ans"><div class="dx-ah"><h2>Who is easiest to slow down</h2></div>
        <p class="muted">No team has 3 or more testable defended episodes yet, so there's no per-team estimate.${targets.length ? " Pick a team above to see the episodes that were found." : ""}</p></section>`;
      return `<section class="card dx-ans"><div class="dx-ah"><h2>Who is easiest to slow down</h2>${CS.help("Teams with at least 3 testable defended episodes, lowest placebo-controlled retention first. The bar is the 90% range; the tick at 100% means defense made no difference. Wide bars are honest: most teams have only a few episodes.", "How to read this list")}</div>
        <ol class="dx-rank">${rows.slice(0, 8).map(([k, r]) => `<li><button type="button" data-pick="${H(k)}"><span class="dx-rk">${tn(k)}</span><span class="dx-nick muted">${H(nick[k] || "")}</span>
          <span class="dx-rb sm" aria-hidden="true"><i class="rg" style="left:${X(r.lo)}%;width:${Math.max(1, X(r.hi) - X(r.lo))}%"></i><i class="one" style="left:${X(1)}%"></i><i class="mn" style="left:${X(r.est)}%"></i></span>
          <span class="dx-rv"><b>${pc(r.est)}</b> kept${r.significant ? "" : ' <span class="muted">· n.s.</span>'}</span></button></li>`).join("")}</ol>
        ${rows.length > 8 ? `<div class="muted small">Pick any team above for the rest (${rows.length} with an estimate).</div>` : ""}</section>`;
    }
    function pick(k) {
      F.target = k || ""; F.defender = ""; shown = 3; expanded = false; focus = null;
      history.replaceState(null, "", `${base}${F.target ? "/" + F.target : ""}`);
      const s = document.getElementById("dxTeam"); if (s) s.value = F.target;
      answer(); body();
    }
    // ---- Best defenders: ranked by points denied per match; each row opens onto that defender's episodes as clips.
    // Honesty (CLAUDE.md): no team's own effect clears the multiple-testing bar, so every number here is labelled "assumption"
    // (a "measured" tag appears only when episodes of that defender are individually significant); thin rows say so.
    const nOf = k => { const c = scd && scd.by && scd.by[k] && scd.by[k].coverage; return c && c.n ? c.n : null; };
    const DEF = Object.entries(d.defenders || {}).map(([k, p]) => {
      const mine = eps.filter(x => x.defender === k), contact = mine.reduce((a, x) => a + (x.feat && x.feat.contact_s || 0), 0), nm_ = nOf(k) || (p.matches || []).length || null;
      return { k, p, mine, contact, perMatch: nm_ && p.denied != null ? p.denied / nm_ : null, nMatch: nm_ };
    }).filter(r => r.p.n).sort((a, b) => (b.perMatch ?? -9) - (a.perMatch ?? -9) || (b.p.denied ?? 0) - (a.p.denied ?? 0));
    const open = new Set(F.defender ? [F.defender] : []), shownBy = {};
    function defTag(r) {
      const sig = r.p.sig || 0;
      return sig ? `<span class="dx-tag measured" title="${sig} of its episodes are individually significant (p ≤ 0.05 and q ≤ 0.2), but no per-team effect has been tested as significant, so the size is still an assumption">${sig} episode${sig === 1 ? "" : "s"} significant</span>`
        : `<span class="dx-tag" title="Assumption, not measured: per-team defense effects need at least 3 testable episodes and to survive a multiple-testing correction; none do yet. Read it as a pointer to the clips, not a measured effect.">assumption</span>`;
    }
    function defRow(r, i, mx) {
      const { k, p } = r, isOpen = open.has(k), thin = (p.tested || 0) < 3, kept = p.ratio, X = v => Math.max(0, Math.min(100, v / 1.5 * 100)).toFixed(1);
      const bar = r.perMatch != null ? Math.max(3, Math.round(Math.max(0, r.perMatch) / mx * 100)) : 0;
      return `<li class="dxd${isOpen ? " open" : ""}${thin ? " thin" : ""}" data-k="${H(k)}"><div class="dxd-row">
        <span class="dxd-rk">${i + 1}</span>
        <span class="dxd-who"><button type="button" class="dxd-tog" aria-expanded="${isOpen}"${isOpen ? ` aria-controls="dxd-${H(k)}"` : ""}><b>${tn(k)}</b><span class="dx-nick muted">${H(nick[k] || "")}</span></button>${k === (e && e.team) ? ' <span class="dxd-us">us</span>' : ""}</span>
        <span class="dxd-main"><span class="dxd-v"><b>${r.perMatch != null ? f1(r.perMatch) : "–"}</b><small>pts denied / match</small></span><span class="dxd-bar" aria-hidden="true"><i style="width:${bar}%"></i></span></span>
        <span class="dxd-s dxd-ep" data-l="Episodes"><b>${p.n}</b><small>${p.tested} testable</small></span>
        <span class="dxd-s dxd-ct" data-l="Contact"><b>${f1(r.contact, 0)}<small> s</small></b><small>${r.nMatch ? `over ${r.nMatch} matches` : "total"}</small></span>
        <span class="dxd-s dxd-keep" data-l="Victims kept"><b>${pc(kept)}</b>${p.ci && !thin ? `<span class="dx-rb sm" aria-hidden="true"><i class="rg" style="left:${X(p.ci[0])}%;width:${Math.max(1, X(p.ci[1]) - X(p.ci[0]))}%"></i><i class="one" style="left:${X(1)}%"></i><i class="mn" style="left:${X(kept)}%"></i></span>` : `<small>${thin ? "too few" : ""}</small>`}</span>
        <span class="dxd-t">${defTag(r)}</span><span class="dxd-ch" aria-hidden="true">${CS.icon("chevR")}</span></div>${isOpen ? defPanel(r) : ""}</li>`;
    }
    function defPanel(r) {
      const { k, p, mine } = r, sh = shownBy[k] || (shownBy[k] = 3), list = mine.slice().sort((a, b) => (b.effect.denied ?? -1e9) - (a.effect.denied ?? -1e9));
      const tg = Object.entries(p.targets || {}).sort((a, b) => b[1] - a[1]);
      return `<div class="dxd-p" id="dxd-${H(k)}" role="region" aria-label="Episodes of ${tn(k)}">
        <div class="dxd-facts"><span><b>${p.succ}</b> of ${p.tested} testable episodes clearly reduced output</span><span>methods: ${Object.entries(p.methods || {}).map(([m, c]) => `${m} ${c}`).join(", ") || "–"}</span>
          <span>against ${tg.map(([t, c]) => `<a href="${base}/${H(t)}">${tn(t)}</a>${c > 1 ? "×" + c : ""}`).join(" · ") || "–"}</span>
          <span><a href="#/${ev}/teams/${H(k)}">All fields of ${tn(k)}</a></span></div>
        ${list.length ? `<div class="dx-grid">${list.slice(0, sh).map(cardHTML).join("")}</div>${list.length > sh ? `<button type="button" class="dx-more" data-more="${H(k)}">Show ${Math.min(6, list.length - sh)} more (${list.length - sh} left)</button>` : ""}` : `<div class="empty">No episodes found for this defender.</div>`}</div>`;
    }
    function defendersPanel() {
      const rows = defAll ? DEF : DEF.slice(0, 12), mx = Math.max(1, ...DEF.map(r => r.perMatch || 0));
      if (!DEF.length) return `<section class="card dx-ans"><div class="dx-ah"><h2>Best defenders</h2></div><p class="muted">No defended episodes have been found yet at this event.</p></section>`;
      return `<section class="card dx-ans"><div class="dx-ah"><h2>Best defenders ${CS.help(`<span class="hl">Ranked by <b>points denied per match</b>: for every testable episode, the points the defended robot was expected to score in that window minus what it did score, summed per defender and divided by its verified matches.</span>
        <span class="hl"><b>Victims kept</b> = the share of expected output the defended teams still scored (lower = better defense; the bar is the 90% range, the tick at 100% means no effect). <b>Contact</b> = seconds of robot-to-robot contact over its episodes.</span>
        <span class="hl"><b>These are assumptions, not measurements.</b> Expected output comes from each target's own undefended time; with about two matches per team no individual defender's effect survives a multiple-testing correction. Use the list to find clips worth watching.</span>`, "How to read the defender ranking")}</h2>
        <span class="muted small">${DEF.length} defender${DEF.length === 1 ? "" : "s"} with episodes</span></div>
        <ol class="dxd-list">${rows.map((r, i) => defRow(r, i, mx)).join("")}</ol>
        ${DEF.length > 12 ? `<button type="button" class="mv-sm dx-defall" id="dxDefAll">${defAll ? "Show top 12" : `Show all ${DEF.length}`}</button>` : ""}</section>`;
    }
    function wireDefenders() {
      const da = document.getElementById("dxDefAll"); if (da) da.onclick = () => { defAll = !defAll; answer(); };
      document.querySelectorAll(".dxd-tog").forEach(b => b.onclick = () => {
        const li = b.closest(".dxd"), k = li.dataset.k;
        li.querySelectorAll("canvas").forEach(c => { c.__player && c.__player.stop(); obs.unobserve(c); });
        if (open.has(k)) open.delete(k); else open.add(k);
        const r = DEF.find(x => x.k === k), i = DEF.indexOf(r);
        li.outerHTML = defRow(r, i, Math.max(1, ...DEF.map(x => x.perMatch || 0)));
        wireDefenders(); mountIn(document.querySelector(`.dxd[data-k="${k}"]`));
      });
      document.querySelectorAll("[data-more]").forEach(b => b.onclick = () => { const k = b.dataset.more; shownBy[k] = (shownBy[k] || 3) + 6; const li = b.closest(".dxd"); li.querySelectorAll("canvas").forEach(c => { c.__player && c.__player.stop(); obs.unobserve(c); });
        const r = DEF.find(x => x.k === k); li.outerHTML = defRow(r, DEF.indexOf(r), Math.max(1, ...DEF.map(x => x.perMatch || 0))); wireDefenders(); mountIn(document.querySelector(`.dxd[data-k="${k}"]`)); });
      mountIn(document.querySelector("#dxAnswer"));
    }
    const mountIn = el => el && el.querySelectorAll(".dx-clip canvas").forEach(c => { if (!c.__player) mount(c); });

    // ---- clips: a few best ones up front; everything else behind "All episodes" with one Filter popover
    const FL = [["defender", "Defender", () => uniq("defender"), tn], ["zone", "Place", () => zones, x => x], ["shift", "Period", () => ["transition", "shift1", "shift2", "shift3", "shift4", "endgame"], SH],
      ["method", "Method", () => METHODS, x => x], ["verdict", "Verdict", () => Object.keys(VERD), v => VERD[v][0]]];
    const nAct = () => FL.filter(f => F[f[0]] && !(f[0] === "defender" && mode === "defenders")).length + (succOnly ? 1 : 0);
    function body() {
      const r = filtered(), el = document.getElementById("dxClips");
      document.querySelectorAll(".dx-clip canvas").forEach(c => { c.__player && c.__player.stop(); obs.unobserve(c); });
      const who = F.defender ? `${tn(F.defender)}'s defense${F.target ? ` on ${tn(F.target)}` : ""}` : F.target ? `Defense on ${tn(F.target)}` : "Strongest episodes";
      if (!expanded) {
        el.innerHTML = `<div class="dx-ch"><h2>${who}</h2>${CS.help(effHelp, "How to read a clip")}<span class="muted small">${r.length} episode${r.length === 1 ? "" : "s"}</span>
            ${r.length > shown ? `<button type="button" class="dx-all" id="dxAll">All ${r.length} episodes</button>` : ""}</div>
          ${r.length ? `<div class="dx-grid">${r.slice(0, shown).map(cardHTML).join("")}</div>` : `<div class="empty">No defended episodes${F.target ? ` against ${tn(F.target)}` : ""}.</div>`}`;
        el.querySelectorAll(".dx-clip canvas").forEach(mount);
        const all = document.getElementById("dxAll"); if (all) all.onclick = () => { expanded = true; shown = 12; body(); };
        return;
      }
      const fpop = `<div class="dx-fw"><button type="button" class="dx-fb" id="dxFB" aria-haspopup="dialog" aria-expanded="false" aria-controls="dxFP">Filter${nAct() ? ` · ${nAct()}` : ""}</button>
        <div class="mv-pop dx-fp" id="dxFP" role="dialog" aria-label="Filter episodes" hidden>
          ${FL.filter(f => !(f[0] === "defender" && mode === "defenders")).map(([k, lab, vals, fmt]) => `<label class="dx-fr"><span>${lab}</span><select data-f="${k}"><option value="">Any</option>${vals().map(v => `<option value="${H(v)}"${F[k] === v ? " selected" : ""}>${H(fmt(v))}</option>`).join("")}</select></label>`).join("")}
          <label class="dx-fr"><span>Sort</span><select data-f="sort">${[["denied", "Points denied"], ["rate", "Δ rate"], ["p", "p-value"], ["match", "Match"], ["dur", "Duration"]].map(([v, t]) => `<option value="${v}"${F.sort === v ? " selected" : ""}>${t}</option>`).join("")}</select></label>
          <label class="mv-ck"><input type="checkbox" id="dxSucc"${succOnly ? " checked" : ""}> Clearly reduced only</label>
          ${nAct() ? `<button type="button" class="mv-sm" id="dxClr">Clear filters</button>` : ""}</div></div>`;
      el.innerHTML = `<div class="dx-ch"><h2>${who}</h2>${CS.help(effHelp, "How to read a clip")}<span class="muted small">${r.length} episode${r.length === 1 ? "" : "s"}</span>
          <span class="dx-sp"></span>${fpop}
          <div class="mv-seg dx-vt" role="radiogroup" aria-label="Show as"><button type="button" role="radio" aria-checked="${F.view === "clips"}" data-v="clips">Clips</button><button type="button" role="radio" aria-checked="${F.view === "table"}" data-v="table">Table</button></div>
          <button type="button" class="mv-sm" id="dxLess">Show fewer</button></div>
        <div id="dxBody"></div>`;
      const bodyEl = document.getElementById("dxBody");
      if (F.view === "clips") {
        bodyEl.innerHTML = r.length ? `<div class="dx-grid">${r.slice(0, shown).map(cardHTML).join("")}</div>${r.length > shown ? `<button class="dx-more" id="dxMore">Show ${Math.min(12, r.length - shown)} more (${r.length - shown} left)</button>` : ""}`
          : `<div class="empty">No episodes match these filters.</div>`;
        bodyEl.querySelectorAll(".dx-clip canvas").forEach(mount);
        const mb = document.getElementById("dxMore"); if (mb) mb.onclick = () => { shown += 12; body(); };
        if (focus) { const fc = document.getElementById("dx-" + focus); fc && fc.scrollIntoView({ block: "center" }); }
      } else {
        bodyEl.innerHTML = `<div class="tablewrap"><table class="dx-tbl"><thead><tr><th>Defender</th><th>Target</th><th class="num" data-pri="80" title="observed / expected points (or attempts when the hub was inactive)">Obs/Exp</th><th data-pri="70">Verdict</th><th data-pri="75">Match</th><th class="num" data-pri="60">Clock</th><th data-pri="55">Period</th><th class="num" data-pri="50">Dur</th><th data-pri="45">Method</th><th data-pri="40">Place</th>
          <th class="num" data-pri="0">Δ /s</th><th class="num" data-pri="0">p</th><th class="num" data-pri="0">q</th><th class="num" data-pri="0">Contact s</th><th class="num" data-pri="0">Pushed m</th><th class="num" data-pri="0">Cycle Δ</th><th data-pri="0">Video</th></tr></thead>
          <tbody>${r.map(x => { const ef = x.effect, m = ef.metric === "attempts" ? "att" : "pts";
            return `<tr class="clk" data-id="${x.id}"><td>${tn(x.defender)}</td><td>${tn(x.target)}</td><td class="num">${ef[m + "_obs"]}/${f1(ef[m + "_exp"])}</td><td>${vpill(x)}</td><td>${x.m}</td><td class="num">${f1(x.t_match, 0)}</td>
            <td>${SH(x.shift)}${x.active ? "" : " (inact.)"}</td><td class="num">${f1(x.dur)}</td><td>${x.method}</td><td>${H(x.zone)} ${x.side}</td>
            <td class="num">${f1(ef.d_rate, 2)}</td><td class="num">${f1(pOf(ef), 2)}</td><td class="num">${f1(ef.q, 2)}</td><td class="num">${f1(x.feat.contact_s)}</td><td class="num">${f1(x.disp.pushed_m)}</td><td class="num">${f1(x.cycle.delta)}</td>
            <td><a href="#/${ev}/a/${x.key}?t=${f1(x.clip.v0)}">video</a></td></tr>`; }).join("")}</tbody></table></div>`;
        bodyEl.querySelectorAll("tr.clk").forEach(tr => tr.onclick = ev_ => { if (ev_.target.closest("a,button")) return; focus = tr.dataset.id; F.view = "clips";
          const r2 = filtered(), i = r2.findIndex(y => y.id === focus); if (i >= shown) shown = i + 1; body(); });
      }
      wireFilter();
      el.querySelectorAll(".dx-vt button").forEach(b => b.onclick = () => { F.view = b.dataset.v; body(); });
      document.getElementById("dxLess").onclick = () => { expanded = false; shown = 3; body(); el.scrollIntoView({ block: "nearest" }); };
    }
    let fpOpen = false;
    function wireFilter() {
      const b = document.getElementById("dxFB"), p = document.getElementById("dxFP"); if (!b) return;
      const place = () => { if (p.hidden) return; const r = b.getBoundingClientRect(), vw = document.documentElement.clientWidth, pw = p.offsetWidth;
        p.style.left = Math.max(8, Math.min(r.left, vw - pw - 8)) + "px"; p.style.top = (r.bottom + 6) + "px"; };
      const open = on => { fpOpen = on; p.hidden = !on; b.setAttribute("aria-expanded", on); if (on) { place(); const f = p.querySelector("select"); f && f.focus(); } };
      if (fpOpen) open(true);
      b.onclick = () => open(p.hidden);
      p.onkeydown = e => { if (e.key === "Escape") { e.stopPropagation(); open(false); b.focus(); } };
      p.onchange = e => { const s = e.target;
        if (s.id === "dxSucc") succOnly = s.checked; else if (s.dataset.f) F[s.dataset.f] = s.value || (s.dataset.f === "sort" ? "denied" : "");
        shown = 12; focus = null; body(); };
      const clr = document.getElementById("dxClr"); if (clr) clr.onclick = () => { FL.forEach(f => { if (!(f[0] === "defender" && mode === "defenders")) F[f[0]] = ""; }); succOnly = false; body(); };
      fpPlace = place; fpClose = () => open(false);
    }
    let fpPlace = () => { }, fpClose = () => { };
    const fpAway = e => { const p = document.getElementById("dxFP"), b = document.getElementById("dxFB"); if (p && !p.hidden && !p.contains(e.target) && !b.contains(e.target)) fpClose(); };
    const fpScroll = () => fpPlace();
    document.addEventListener("pointerdown", fpAway, true); addEventListener("scroll", fpScroll, true);
    cleanup.push(() => { document.removeEventListener("pointerdown", fpAway, true); removeEventListener("scroll", fpScroll, true); });

    document.querySelectorAll(".dx-seg button").forEach(b => b.onclick = () => { location.hash = b.dataset.mode === "defenders" ? base : `${base}/${F.target || "team"}`; });
    const sel = document.getElementById("dxTeam"); if (sel) sel.onchange = () => pick(sel.value);
    const onR = () => document.querySelectorAll(".dx-clip canvas").forEach(c => c.__player && c.__player.once());
    window.addEventListener("resize", onR); cleanup.push(() => window.removeEventListener("resize", onR));
    FIELD.complete || FIELD.addEventListener("load", onR, { once: true });
    answer(); body();
  }

  CS.defensePane = (ev, target, host) => page(ev, target, host);
})();
