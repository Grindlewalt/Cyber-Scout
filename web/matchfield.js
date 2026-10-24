"use strict";
/* matchfield.js: the shared match-plan field (Match Day + Schedule). Data: api/<ev>/mp/<match>.json (contract in
 * .work/redesign/CONTRACT.md). Everything drawn comes from that file; nothing is invented. Field frame: metres, blue wall x=0,
 * red wall x=16.541, y 0..8.069; drawn red end on the right, y up (the app's broadcast picture turned 180 degrees).
 *   CS.matchField(el, plan, {mini, mode, us}) -> {destroy}   Auto | Tele toggle, Predicted | Actual, team isolate, legend
 *   CS.matchPeriods(plan, {view})            -> html          totals + period bars (red and blue both), yellow when not enough info
 *   CS.matchNote(plan, al)                   -> "not enough info (team 1234: why)" or "" */
(function () {
  const H = s => esc(s);
  const LEN = 16.541, WID = 8.069, HUB_X = 4.03, HUB_Y = WID / 2;
  const tn = k => (k || "").replace("frc", "");
  const n0 = (v, d = 0) => v == null ? "–" : Number(v).toFixed(d);
  const ROLE = { defense: "Defense", ferry_neutral: "Ferry from neutral", robber: "Robber", collect: "Collect" };
  const ROLE_WHAT = { defense: "plays defense on the opponents' scorers", ferry_neutral: "ferries FUEL from the neutral zone to its side",
    robber: "takes FUEL from the opponents' side", collect: "keeps collecting for the next active shift" };

  CS.matchNote = (plan, al) => {
    const a = plan && plan.alliances && plan.alliances[al];
    if (!a || a.enough !== false) return "";
    const ms = (a.missing || []).map(m => `team ${tn(m.team)}: ${m.why}`).join("; ");
    return "not enough info" + (ms ? ` (${ms})` : "");
  };

  // hit rates measured so far at this event (mp/index.json calib), as a plain sentence for the "?" popovers; "" when not there
  const cal = {};
  CS.mpCalib = ev => cal[ev] || (cal[ev] = api(`${ev}/mp/index.json`).then(j => j && j.calib || null).catch(() => null));
  const pc = v => v == null ? null : Math.round((v <= 1 ? v * 100 : v));
  const calSay = (c, k) => { if (!c || c[k] == null) return "";
    const v = c[k], t = { role_hit: `the job guess was right ${pc(v)}% of the time at this event so far`, start_side_hit: `the start side was right ${pc(v)}% of the time at this event so far`,
      auto_fuel_mae: `auto FUEL was off by ${n0(v, 1)} on average at this event so far`, score_range_cover: `the real score fell inside the predicted range ${pc(v)}% of the time at this event so far`,
      win_hit: `the predicted winner was right ${pc(v)}% of the time at this event so far` }[k]; return t ? `<br><b>Track record:</b> ${t}.` : ""; };
  CS.matchCalib = calSay;
  const standIn = r => !!(r && (r.stand_in || (r.auto && r.auto.stand_in)));
  function css() {
    if (document.getElementById("mf-css")) return;
    const st = document.createElement("style"); st.id = "mf-css";
    st.textContent = `
:root { --nei: #f5c518; --nei-ink: #2b2100; }
.mf { display: grid; gap: 10px; min-width: 0; max-width: 100%; }
.mf-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.mf-seg { display: inline-flex; border: 1px solid var(--line-strong); border-radius: 99px; overflow: hidden; background: var(--surface); }
.mf-seg button { border: 0; background: transparent; color: var(--ink-2); padding: 5px 16px; min-height: 36px; font-size: var(--fs-foot); font-weight: 600; cursor: pointer; }
.mf-seg button[aria-pressed="true"] { background: var(--accent-tint); color: var(--ink); box-shadow: inset 0 -3px 0 var(--accent); }
.mf-sel:focus-visible { outline: none; }
.mf-r:has(.mf-sel:focus-visible):not(.on) { box-shadow: 0 0 0 2px var(--accent); }
.mf-seg button:focus-visible, .mf-tg:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.mf-tg { border: 1px solid var(--line-strong); background: var(--surface); color: var(--ink-2); border-radius: 99px; padding: 4px 12px; min-height: 36px; font-size: var(--fs-foot); cursor: pointer; }
.mf-tg[aria-pressed="true"] { color: var(--ink); border-color: var(--accent); background: var(--accent-tint); }
.mf-sp { flex: 1; }
.mf-tot { display: flex; flex-wrap: wrap; gap: 8px; }
.mf-at { display: flex; gap: 8px; align-items: baseline; padding: 4px 12px; border-radius: var(--r-p); font-size: var(--fs-foot); color: #fff; }
.mf-at.red { background: var(--red-fill); } .mf-at.blue { background: var(--blue-fill); }
.mf-at b { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 20px; font-variant-numeric: tabular-nums; }
.mf-at.nei, .mf-nei { background: var(--nei); color: var(--nei-ink); }
.mf-at small { opacity: .85; }
.mf-cv { position: relative; border-radius: var(--r-m); overflow: hidden; border: 1px solid var(--line); background: var(--surface-2); }
.mf-cv canvas { display: block; width: 100%; height: auto; touch-action: manipulation; }
.mf-rob { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.mf-col { display: grid; gap: 6px; align-content: start; min-width: 0; }
.mf-r { display: grid; gap: 2px; padding: 6px 8px; border-radius: var(--r-p); border: 1px solid var(--line); border-left: 4px solid var(--line-strong); background: var(--surface); min-width: 0; }
.mf-r.red { border-left-color: var(--red-fill); } .mf-r.blue { border-left-color: var(--blue-fill); }
.mf-r.on { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
.mf-r.dim { opacity: .5 }
.mf-r.no { border-style: dashed; border-left-style: solid; background: transparent; }
.mf-sel { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; border: 0; background: none; padding: 0; min-height: 28px; font: inherit; color: var(--ink); cursor: pointer; text-align: left; }
.mf-sel b { font-family: var(--font-num); font-stretch: var(--num-stretch); font-size: 17px; font-variant-numeric: tabular-nums; }
.mf-sel small { color: var(--ink-2); font-size: var(--fs-cap); }
.mf-sub.si { opacity: .65; font-style: italic; } .mf-sub { font-size: var(--fs-cap); color: var(--ink-2); line-height: 1.4; }
.mf-why { font-size: var(--fs-cap); color: var(--ink); background: var(--nei); color: var(--nei-ink); border-radius: var(--r-tag); padding: 1px 6px; }
.mf-role { display: inline-flex; gap: 4px; align-items: center; font-size: var(--fs-cap); font-weight: 650; padding: 1px 8px; border-radius: 99px; background: var(--surface-3); color: var(--ink); width: fit-content; }
.mf-role.ok::after { content: "as predicted"; font-weight: 400; color: var(--ink-2); }
.mf-leg { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: var(--fs-cap); color: var(--ink-2); align-items: center; }
.mf-leg i { display: inline-block; width: 22px; height: 0; border-top: 3px solid var(--ink-2); vertical-align: middle; margin-right: 4px; }
.mf-leg i.d { border-top-style: dotted; } .mf-leg i.h { height: 10px; width: 14px; border: 0; background: color-mix(in srgb, var(--ink-2) 40%, transparent); border-radius: 2px; }
.mf-leg i.x { width: 10px; height: 10px; border: 2px solid var(--ink-2); border-radius: 50%; }
.mf-note { font-size: var(--fs-foot); color: var(--ink-2); }
.mini .mf-rob { grid-template-columns: 1fr 1fr; }
@media (max-width: 560px) { .mf-r { padding: 5px 6px; } .mf-sel b { font-size: 15px; } }
/* period bars */
.mpp { display: grid; gap: 4px; min-width: 0; }
.mpp-hd, .mpp-row { display: grid; grid-template-columns: 74px minmax(0, 1fr); gap: 8px; align-items: center; }
.mpp-hd { font-size: var(--fs-cap); font-weight: 650; color: var(--ink-2); }
.mpp-hd .two, .mpp-bars { display: grid; grid-template-columns: 1fr 1fr; gap: 3px; }
.mpp-hd .two span:first-child { text-align: right; padding-right: 4px; } .mpp-hd .two span:last-child { padding-left: 4px; }
.mpp-l { font-size: var(--fs-foot); color: var(--ink-2); }
.mpp-side { position: relative; height: 24px; display: flex; background: var(--surface-2); border-radius: 3px; }
.mpp-side.r { justify-content: flex-end; border-radius: 4px 0 0 4px; } .mpp-side.b { justify-content: flex-start; border-radius: 0 4px 4px 0; }
.mpp-side i { display: flex; align-items: center; height: 100%; min-width: 30px; border-radius: inherit; color: #fff; font-style: normal; font-size: var(--fs-foot); font-weight: 700; font-variant-numeric: tabular-nums; padding: 0 7px; }
.mpp-side.r i { background: var(--red-fill); justify-content: flex-start; } .mpp-side.b i { background: var(--blue-fill); justify-content: flex-end; }
.mpp-side.r.nei i { background: color-mix(in srgb, var(--nei) 68%, var(--red-fill)); color: var(--nei-ink); } .mpp-side.b.nei i { background: color-mix(in srgb, var(--nei) 68%, var(--blue-fill)); color: var(--nei-ink); }
.mpp-side.nei u { background: var(--nei-ink); } .mpp-chip { display: inline-block; background: var(--nei); color: var(--nei-ink); border-radius: var(--r-tag); padding: 0 6px; font-weight: 700; margin-left: 6px; }
.mpp-side u { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--ink); opacity: .75; text-decoration: none; }
.mpp-row.tot .mpp-side { height: 30px; } .mpp-row.tot .mpp-side i { font-size: var(--fs-callout); }
.mpp-row.tot .mpp-l { font-weight: 700; color: var(--ink); }
.mpp-neirow { font-size: var(--fs-foot); background: var(--nei); color: var(--nei-ink); padding: 5px 10px; border-radius: var(--r-p); font-weight: 600; }
.mpp-key { font-size: var(--fs-cap); color: var(--ink-2); display: flex; gap: 12px; flex-wrap: wrap; } .mpp-key u { display: inline-block; width: 2px; height: 12px; background: var(--ink); vertical-align: -2px; margin-right: 4px; }
`;
    document.head.appendChild(st);
  }

  // ---------------------------------------------------------------- period bars (also used for the scoreboard)
  CS.matchPeriods = (plan, o = {}) => {
    css();
    const A = plan && plan.alliances; if (!A) return "";
    const R = A.red, B = A.blue;
    const hasAct = !!(plan.played && !(o.asof && plan.time > o.asof) && R.actual && B.actual && (R.actual.periods || []).some(p => p.pts != null) && (B.actual.periods || []).some(p => p.pts != null));
    const view = o.view || (hasAct ? "actual" : "pred");
    const act = view === "actual" && hasAct;
    const pm = al => Object.fromEntries((A[al].pred.periods || []).map(p => [p.period, p]));
    const am = al => Object.fromEntries(((A[al].actual && A[al].actual.periods) || []).map(p => [p.period, p.pts]));
    const P = { red: pm("red"), blue: pm("blue") }, X = { red: am("red"), blue: am("blue") };
    const order = (R.pred.periods || []).map(p => ({ period: p.period, label: p.label }));
    const val = (al, p) => act ? X[al][p] : P[al][p] && P[al][p].mean;
    const tot = al => act ? A[al].actual.score : A[al].pred.score && A[al].pred.score.mean;
    const mx = Math.max(5, ...order.flatMap(p => ["red", "blue"].map(al => Math.max(val(al, p.period) || 0, act ? (P[al][p.period] || {}).mean || 0 : 0))));
    const mt = Math.max(5, tot("red") || 0, tot("blue") || 0);
    const nei = al => !act && A[al].enough === false;
    const side = (al, v, max, tick, label) => {
      const w = v == null ? 0 : Math.max(0, v / max * 100);
      return `<div class="mpp-side ${al === "red" ? "r" : "b"}${nei(al) ? " nei" : ""}" ${label ? `title="${H(label)}"` : ""}>${v == null ? "" : `<i style="width:${w.toFixed(1)}%">${n0(v, v >= 100 || act ? 0 : 1)}</i>`}${tick != null ? `<u style="${al === "red" ? "right" : "left"}:${Math.min(100, tick / max * 100).toFixed(1)}%" aria-hidden="true"></u>` : ""}</div>`;
    };
    const row = (cls, label, rv, bv, max, tr, tb, tip) =>
      `<div class="mpp-row ${cls}"><span class="mpp-l">${H(label)}</span><div class="mpp-bars">${side("red", rv, max, tr, tip)}${side("blue", bv, max, tb, tip)}</div></div>`;
    const rows = order.map(p => {
      const sh = /shift/.test(p.period), ap = al => P[al][p.period] && P[al][p.period].active_p;
      const tip = sh ? `hub active in ${n0((ap("red") || 0) * 100)}% (red) / ${n0((ap("blue") || 0) * 100)}% (blue) of simulations` : "";
      return row("", p.label, val("red", p.period), val("blue", p.period), mx, act ? (P.red[p.period] || {}).mean : null, act ? (P.blue[p.period] || {}).mean : null, tip);
    }).join("");
    const totRow = row("tot", act ? "Final" : "Predicted", tot("red"), tot("blue"), mt, act ? A.red.pred.score.mean : null, act ? A.blue.pred.score.mean : null,
      act ? "" : `range red ${n0(A.red.pred.score.lo)}–${n0(A.red.pred.score.hi)}, blue ${n0(A.blue.pred.score.lo)}–${n0(A.blue.pred.score.hi)}`);
    const neis = ["red", "blue"].filter(nei).map(al => `<div class="mpp-neirow">${al === "red" ? "Red" : "Blue"} prediction: ${H(CS.matchNote(plan, al))}</div>`).join("");
    const cs = o.calib ? CS.matchCalib(o.calib, "score_range_cover") : "";
    const wp = !act && A.red.pred.win_p != null ? `<div class="mpp-key">win chance: red ${n0(A.red.pred.win_p * 100)}% · blue ${n0(A.blue.pred.win_p * 100)}%</div>` : "";
    return `<div class="mpp" role="group" aria-label="Points per period, red and blue">${neis}
      <div class="mpp-hd"><span>${act ? "Actual" : "Predicted"}</span><div class="two"><span>Red${nei("red") ? '<em class="mpp-chip">not enough info</em>' : ""}</span><span>Blue${nei("blue") ? '<em class="mpp-chip">not enough info</em>' : ""}</span></div></div>
      ${totRow}${rows}
      <div class="mpp-key">${act ? `<span><u></u>predicted</span><span>actual FUEL points counted from video; tower and fouls are in the final</span>` : `<span>points per period, FUEL plus tower</span>`}${wp}${cs ? `<span>${cs.replace("<br>", "")}</span>` : ""}</div></div>`;
  };

  // ---------------------------------------------------------------- the field
  CS.matchField = (el, plan, opts = {}) => {
    css();
    const mini = !!opts.mini, US = opts.us || "frc4911";
    const robots = (plan.robots || []);
    const hasAct = !!(plan.played && !(opts.asof && plan.time > opts.asof) && robots.some(r => r.actual));
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const S = { mode: opts.mode || "auto", view: hasAct ? "actual" : "pred", sel: null, heat: true, prog: 1 };
    let raf = 0, dead = false;
    el.innerHTML = "";
    const root = document.createElement("div"); root.className = "mf" + (mini ? " mini" : ""); el.appendChild(root);

    const rOf = tm => robots.find(r => r.team === tm);
    const view = r => S.view === "actual" && r.actual ? "actual" : "pred";
    // robot path/point accessors per view
    const startXY = r => S.view === "actual" ? (r.actual && r.actual.start && r.actual.start.xy) : (r.start && r.start.xy);
    const autoPath = r => S.view === "actual" ? (r.actual && r.actual.auto && r.actual.auto.path) : (r.auto && r.auto.path);
    const autoPts = r => S.view === "actual" ? (r.actual && r.actual.auto && r.actual.auto.fuel) : (r.auto && r.auto.pts && r.auto.pts.mean);
    const trPath = r => S.view === "actual" ? (r.actual && r.actual.transition && r.actual.transition.path) : (r.tele && r.tele.transition && r.tele.transition.path);
    const cyPath = r => S.view === "actual" ? null : (r.tele && r.tele.cycle && r.tele.cycle.path);
    const colXY = r => S.view === "actual" ? (r.actual && r.actual.collect && r.actual.collect.xy) : (r.tele && r.tele.cycle && r.tele.cycle.collect && r.tele.cycle.collect.xy);
    const roleOf = r => S.view === "actual" ? (r.actual && r.actual.inactive && r.actual.inactive.role) : (r.tele && r.tele.inactive && r.tele.inactive.role);

    function ui() {
      const A = plan.alliances, nei = al => S.view !== "actual" && A[al].enough === false;
      const sumA = al => S.view === "actual" ? (A[al].actual && A[al].actual.auto) : A[al].pred.auto && A[al].pred.auto.mean;
      const tot = ["red", "blue"].map(al => {
        const v = sumA(al), p = A[al].pred.auto;
        return `<div class="mf-at ${al}${nei(al) ? " nei" : ""}" title="${H(nei(al) ? CS.matchNote(plan, al) : "")}"><span>${al === "red" ? "Red" : "Blue"} auto</span>
          <b>${v == null ? "–" : n0(v, S.view === "actual" ? 0 : 1)}</b>${S.view !== "actual" && p && p.lo != null ? `<small>${n0(p.lo)}–${n0(p.hi)}</small>` : ""}${nei(al) ? `<small>not enough info</small>` : ""}</div>`;
      }).join("");
      const seg = `<div class="mf-seg" role="group" aria-label="Period shown">${[["auto", "Auto"], ["tele", "Tele"]].map(([k, l]) => `<button type="button" data-mode="${k}" aria-pressed="${S.mode === k}">${l}</button>`).join("")}</div>`;
      const pa = hasAct ? `<div class="mf-seg" role="group" aria-label="Predicted or actual">${[["pred", "Predicted"], ["actual", "Actual"]].map(([k, l]) => `<button type="button" data-view="${k}" aria-pressed="${S.view === k}">${l}</button>`).join("")}</div>` : "";
      const heatBtn = S.mode === "tele" ? `<button type="button" class="mf-tg" data-heat aria-pressed="${S.heat}">Heat</button>` : "";
      const col = al => `<div class="mf-col">${robots.filter(r => r.alliance === al).map(rowHtml).join("")}</div>`;
      root.innerHTML = `<div class="mf-bar">${seg}${pa}${heatBtn}<span class="mf-sp"></span>${CS.help(
        `Each path is that robot's own past route (the data behind it is named in each robot's note), not a simulation. <b>Auto</b>: where it starts and drives; <b>Tele</b>: a typical scoring cycle, where it collects (heat = where it spends its time), its transition path, and its likely job while its hub is inactive. A robot with too little video is dashed and grey with the reason. After the match, <b>Actual</b> shows what really happened with the prediction as ghost lines.${CS.matchCalib(opts.calib, "start_side_hit")}${CS.matchCalib(opts.calib, "auto_fuel_mae")}`, "How to read the field")}</div>
        ${S.mode === "auto" ? `<div class="mf-tot">${tot}</div>` : ""}
        <div class="mf-cv"><canvas role="img" aria-label="Top-down field with the six robots' ${S.mode === "auto" ? "start spots and auto paths" : "typical cycles and transition paths"}"></canvas></div>
        <div class="mf-leg">${S.mode === "auto" ? `<span><i></i>auto path (arrow = end)</span><span><i class="x"></i>start spot</span>` : `<span><i></i>typical cycle</span><span><i class="d"></i>transition path</span><span><i class="x"></i>collect point</span><span><i class="h"></i>${S.sel ? "heat: where it goes" : "tap a team to see its heat"}</span>`}<span>tap a team to isolate it</span></div>
        <div class="mf-rob">${col("red")}${col("blue")}</div>`;
      canvas = root.querySelector("canvas"); size(); draw();
      root.querySelectorAll("[data-mode]").forEach(b => b.onclick = () => { S.mode = b.dataset.mode; anim(); ui(); });
      root.querySelectorAll("[data-view]").forEach(b => b.onclick = () => { S.view = b.dataset.view; anim(); ui(); });
      root.querySelectorAll("[data-heat]").forEach(b => b.onclick = () => { S.heat = !S.heat; ui(); });
      root.querySelectorAll("[data-team]").forEach(b => b.onclick = () => pick(b.dataset.team));
      canvas.onclick = e => { const r = canvas.getBoundingClientRect(), best = hit((e.clientX - r.left) / r.width * canvas.width / dpr, (e.clientY - r.top) / r.height * canvas.height / dpr); if (best) pick(best); };
      if (opts.onChange) opts.onChange(S);
    }
    function pick(tm) { S.sel = S.sel === tm ? null : tm; ui(); const b = root.querySelector(`[data-team="${tm}"]`); if (b) b.focus({ preventScroll: true }); }

    function rowHtml(r) {
      const us = r.team === US, on = S.sel === r.team, dim = S.sel && !on;
      const ok = r.enough !== false || S.view === "actual", si = standIn(r) && S.view !== "actual";
      let sub = "";
      if (S.mode === "auto") {
        const pts = autoPts(r), b = r.auto && r.auto.basis;
        sub = ok || S.view === "actual" || pts != null ? `<span class="mf-sub${si ? " si" : ""}"><b>${pts == null ? "–" : (si ? "≈" : "") + n0(pts, S.view === "actual" ? 0 : 1)}</b> ${S.view === "actual" ? "FUEL in auto" : si ? "auto pts (event avg)" : "auto pts"}${r.auto && r.auto.routine && S.view !== "actual" ? ` · ${H(r.auto.routine)}` : ""}</span>${on && S.view !== "actual" ? `<span class="mf-sub">${b ? "Path: " + H(b) + ". " : ""}${r.start ? "Start: " + H(r.start.basis || "") : ""}</span>` : ""}` : "";
      } else {
        const inact = r.tele && r.tele.inactive, pr = r.tele && r.tele.transition, ar = r.actual && r.actual.inactive && r.actual.inactive.role;
        const role = S.view === "actual" ? ar : inact && inact.role;
        const help = inact ? CS.help(`<b>Predicted job while its hub is inactive</b>: ${H(ROLE_WHAT[inact.role] || inact.role)}. ${H(inact.basis || "")}<br>${Object.entries(inact.p || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${H(ROLE[k] || k)} ${n0(v * 100)}%`).join(" · ")}<br>This is a pattern from past matches, an assumption about this match.${CS.matchCalib(opts.calib, "role_hit")}`, `Why ${tn(r.team)} is predicted to ${ROLE[inact.role] || "play"}`) : "";
        const mid = [];
        if (role) mid.push(`<span class="mf-role${S.view === "actual" && inact && ar === inact.role ? " ok" : ""}">${H(ROLE[role] || role)}</span>${S.view === "actual" ? "" : help}`);
        else if (ok || S.view === "actual") mid.push(`<span class="mf-sub">job: no data</span>`);
        if (S.view === "actual" && inact && ar && ar !== inact.role) mid.push(`<span class="mf-sub">predicted ${H(ROLE[inact.role] || inact.role)}</span>`);
        const tr = S.view === "actual" ? (r.actual && r.actual.transition && r.actual.transition.scored != null ? (r.actual.transition.scored ? "scored in transition" : "did not score in transition") : "")
          : pr && pr.scores_p != null ? `scores in transition ${n0(pr.scores_p * 100)}%` : "";
        sub = `<span style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">${mid.join("")}</span>${tr ? `<span class="mf-sub">${H(tr)}${S.view !== "actual" && r.tele && r.tele.cycle && r.tele.cycle.per_min != null && r.tele.cycle.per_min <= 15 ? ` · ${n0(r.tele.cycle.per_min, 1)} cycles/min` : ""}</span>` : ""}`;
        if (on && S.view !== "actual" && r.tele && r.tele.cycle) sub += `<span class="mf-sub">${H(r.tele.cycle.basis || "")}</span>`;
      }
      return `<div class="mf-r ${r.alliance}${on ? " on" : ""}${dim ? " dim" : ""}${ok ? "" : " no"}">
        <button type="button" class="mf-sel" data-team="${r.team}" aria-pressed="${on}" title="${on ? "show all teams" : "show only this team"}"><b>${tn(r.team)}</b><small>${us ? "us · " : ""}station ${r.station}${r.n_video != null ? ` · ${r.n_video} tracked` : ""}</small></button>
        ${ok ? "" : `<span class="mf-why">not enough info: ${H(r.why || "")}</span>`}${sub}</div>`;
    }

    // ---- canvas
    let canvas, dpr = 1, W = 0, Hh = 0, C = {};
    const colors = () => {
      const cs = getComputedStyle(root), g = (n, d) => (cs.getPropertyValue(n) || "").trim() || d;
      C = { bg: g("--surface-2", "#eee"), line: g("--line-strong", "#999"), red: g("--red-fill", "#c33"), blue: g("--blue-fill", "#33c"), ink: g("--ink", "#111"), ink2: g("--ink-2", "#555"),
        ink3: g("--ink-3", "#888"), surf: g("--surface", "#fff"), acc: g("--accent", "#06c"), nei: "#f5c518" };
    };
    function size() {
      if (!canvas) return;
      const w = Math.max(220, canvas.parentElement.clientWidth || root.clientWidth || 360);
      dpr = Math.min(3, window.devicePixelRatio || 1);
      W = w; Hh = Math.round(w * WID / LEN);
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(Hh * dpr);
    }
    const sc = () => W / LEN;
    const X = x => x * sc(), Y = y => (WID - y) * sc();
    const alc = a => a === "red" ? C.red : C.blue;
    const alpha = (c, a) => { const t = document.createElement("canvas").getContext("2d"); t.fillStyle = c; const v = t.fillStyle; // normalise any css colour to #rrggbb
      if (v[0] === "#") return `rgba(${parseInt(v.slice(1, 3), 16)},${parseInt(v.slice(3, 5), 16)},${parseInt(v.slice(5, 7), 16)},${a})`; return v; };
    const alCache = {}; const al = (c, a) => alCache[c + a] || (alCache[c + a] = alpha(c, a));

    function field(ctx) {
      ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, Hh);
      const zone = (x0, x1, c) => { ctx.fillStyle = al(c, .13); ctx.fillRect(X(x0), 0, X(x1) - X(x0), Hh); };
      zone(0, 5.2, C.blue); zone(LEN - 5.2, LEN, C.red);
      ctx.fillStyle = al(C.nei, .22); ctx.fillRect(X(LEN / 2 - .9), Y(6.3), X(1.8), Y(1.7) - Y(6.3));      // the FUEL pile in the middle
      ctx.strokeStyle = C.line; ctx.lineWidth = 1; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(X(LEN / 2), 0); ctx.lineTo(X(LEN / 2), Hh); ctx.stroke(); ctx.setLineDash([]);
      [[HUB_X, C.blue], [LEN - HUB_X, C.red]].forEach(([x, c]) => {   // hubs
        ctx.beginPath(); for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i + Math.PI / 6; ctx.lineTo(X(x) + Math.cos(a) * .75 * sc(), Y(HUB_Y) + Math.sin(a) * .75 * sc()); }
        ctx.closePath(); ctx.fillStyle = al(c, .35); ctx.fill(); ctx.strokeStyle = c; ctx.lineWidth = 1.5; ctx.stroke(); });
      ctx.lineWidth = 3; ctx.strokeStyle = C.blue; ctx.beginPath(); ctx.moveTo(1, 0); ctx.lineTo(1, Hh); ctx.stroke();
      ctx.strokeStyle = C.red; ctx.beginPath(); ctx.moveTo(W - 1, 0); ctx.lineTo(W - 1, Hh); ctx.stroke();
      ctx.font = `700 ${Math.max(9, W / 60)}px system-ui,sans-serif`; ctx.fillStyle = al(C.ink2, .8); ctx.textAlign = "left"; ctx.fillText("BLUE", 8, Hh - 8);
      ctx.textAlign = "right"; ctx.fillText("RED", W - 8, Hh - 8);
    }
    const visible = r => !S.sel || S.sel === r.team;
    const colorOf = r => r.enough === false && S.view !== "actual" ? C.ink3 : alc(r.alliance);
    function line(ctx, pts, c, w, dash, p, arrow) {
      if (!pts || pts.length < 2) return;
      const n = Math.max(2, Math.ceil(pts.length * p)), q = pts.slice(0, n);
      ctx.strokeStyle = c; ctx.lineWidth = w; ctx.setLineDash(dash || []); ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.beginPath(); q.forEach((pt, i) => i ? ctx.lineTo(X(pt[0]), Y(pt[1])) : ctx.moveTo(X(pt[0]), Y(pt[1]))); ctx.stroke(); ctx.setLineDash([]);
      if (arrow && p >= 1) { const a = q[q.length - 1], b = q[Math.max(0, q.length - 4)]; let ang = Math.atan2(Y(a[1]) - Y(b[1]), X(a[0]) - X(b[0]));
        if (!isFinite(ang)) ang = 0; const s = 5 + w; ctx.fillStyle = c; ctx.beginPath(); ctx.moveTo(X(a[0]), Y(a[1]));
        ctx.lineTo(X(a[0]) - Math.cos(ang - .45) * s, Y(a[1]) - Math.sin(ang - .45) * s); ctx.lineTo(X(a[0]) - Math.cos(ang + .45) * s, Y(a[1]) - Math.sin(ang + .45) * s); ctx.closePath(); ctx.fill(); }
    }
    function marker(ctx, r, xy, label) {
      const x = X(xy[0]), y = Y(xy[1]), fs = Math.max(8, Math.min(12, W / 38)), t = tn(r.team), w = Math.max(24, t.length * fs * .66 + 8), h = fs + 7;
      ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 4);
      if (r.enough === false && S.view !== "actual") { ctx.fillStyle = C.surf; ctx.fill(); ctx.setLineDash([3, 2]); ctx.strokeStyle = C.ink3; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = C.ink2; }
      else { ctx.fillStyle = alc(r.alliance); ctx.fill(); ctx.fillStyle = "#fff"; }
      if (r.team === US) { ctx.strokeStyle = C.acc; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.roundRect(x - w / 2 - 2, y - h / 2 - 2, w + 4, h + 4, 6); ctx.stroke(); }
      ctx.font = `700 ${fs}px system-ui,sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(t, x, y + .5);
      if (label) { ctx.font = `600 ${Math.max(8, fs - 1)}px system-ui,sans-serif`; ctx.fillStyle = C.ink; ctx.textBaseline = "top"; ctx.fillText(label, x, y + h / 2 + 2); }
      ctx.textBaseline = "alphabetic";
    }
    function draw() {
      if (!canvas || dead) return;
      colors(); const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, Hh);
      field(ctx); const p = S.prog, act = S.view === "actual";
      const rs = robots, ghost = act, dimA = r => ctx.globalAlpha = S.sel && S.sel !== r.team ? .2 : 1;
      if (S.mode === "tele" && S.heat && S.sel) rs.forEach(r => { const h = r.team === S.sel && !act && r.tele && r.tele.cycle && r.tele.cycle.heat; if (!h) return;
        // sparse 0.5 m cells -> a smooth density: soft radial blobs summed on an offscreen canvas, then the alliance colour through its alpha
        const off = document.createElement("canvas"); off.width = Math.round(W); off.height = Math.round(Hh); const o = off.getContext("2d"), rad = h.cell * sc() * 1.6;
        h.cells.forEach(([ix, iy, w]) => { const x = X((ix + .5) * h.cell), y = Y((iy + .5) * h.cell), g = o.createRadialGradient(x, y, 0, x, y, rad);
          g.addColorStop(0, `rgba(0,0,0,${Math.min(.5, .12 + w * .38)})`); g.addColorStop(1, "rgba(0,0,0,0)"); o.fillStyle = g; o.fillRect(x - rad, y - rad, rad * 2, rad * 2); });
        o.globalCompositeOperation = "source-in"; o.fillStyle = colorOf(r); o.fillRect(0, 0, off.width, off.height);
        ctx.drawImage(off, 0, 0, W, Hh); });
      rs.forEach(r => {
        dimA(r); const c = colorOf(r), dash = r.enough === false && S.view !== "actual" ? [5, 4] : null;
        if (S.mode === "auto") {
          if (ghost) line(ctx, r.auto && r.auto.path, al(c, .3), 2, [4, 4], 1, false);
          line(ctx, autoPath(r), c, S.sel ? 3.5 : 2.6, dash, p, true);
        } else {
          if (ghost) { line(ctx, cyPath(r) || (r.tele && r.tele.cycle && r.tele.cycle.path), al(c, .3), 2, [4, 4], 1, false); }
          line(ctx, cyPath(r), c, S.sel ? 3.5 : 2.6, dash, p, true);
          line(ctx, trPath(r), c, 2.4, [1.5, 4], p, true);
          const cx = colXY(r); if (cx) { const x = X(cx[0]), y = Y(cx[1]), sp = r.tele && r.tele.cycle && r.tele.cycle.collect && r.tele.cycle.collect.spread_m;
            if (sp && !act) { ctx.beginPath(); ctx.arc(x, y, Math.min(sp, 1.2) * sc(), 0, 7); ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.strokeStyle = al(c, .6); ctx.stroke(); ctx.setLineDash([]); }
            ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.lineWidth = 2; ctx.strokeStyle = c; ctx.fillStyle = C.surf; ctx.fill(); ctx.stroke(); }
        }
      });
      rs.forEach(r => { dimA(r); const xy = startXY(r) || (r.start && r.start.xy); if (!xy) return;
        const pts = autoPts(r); marker(ctx, r, xy, S.mode === "auto" && pts != null && (r.enough !== false || act || standIn(r)) ? `${standIn(r) && !act ? "≈" : ""}${n0(pts, act ? 0 : 1)}${act ? " FUEL" : " pts"}` : ""); });
      ctx.globalAlpha = 1;
      if (!rs.some(r => startXY(r) || (r.start && r.start.xy))) { ctx.fillStyle = C.ink2; ctx.font = "600 13px system-ui"; ctx.textAlign = "center"; ctx.fillText("no tracked starts for these robots yet", W / 2, Hh / 2); }
    }
    function hit(px, py) {
      let best = null, bd = 28;
      robots.forEach(r => { const xy = startXY(r) || (r.start && r.start.xy); if (!xy) return; const d = Math.hypot(X(xy[0]) - px, Y(xy[1]) - py); if (d < bd) { bd = d; best = r.team; } });
      return best;
    }
    function anim() {
      cancelAnimationFrame(raf);
      if (reduce) { S.prog = 1; return; }
      S.prog = 0; const t0 = performance.now();
      const step = t => { S.prog = Math.min(1, (t - t0) / 450); draw(); if (S.prog < 1 && !dead) raf = requestAnimationFrame(step); };
      raf = requestAnimationFrame(step);
    }

    ui();
    const ro = new ResizeObserver(() => { if (!root.isConnected) return; const w = canvas && canvas.parentElement.clientWidth; if (w && Math.abs(w - W) > 1) { size(); draw(); } });
    ro.observe(root);
    const mo = new MutationObserver(() => { if (root.isConnected) draw(); }); mo.observe(document.documentElement, { attributes: true });
    const mq = matchMedia("(prefers-color-scheme: dark)"), onMq = () => draw(); mq.addEventListener && mq.addEventListener("change", onMq);
    const destroy = () => { dead = true; cancelAnimationFrame(raf); ro.disconnect(); mo.disconnect(); mq.removeEventListener && mq.removeEventListener("change", onMq); };
    if (typeof cleanup !== "undefined") cleanup.push(destroy);
    return { destroy, state: S };
  };
})();
