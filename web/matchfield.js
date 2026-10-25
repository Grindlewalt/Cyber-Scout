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
  // an estimated spot (yellow outline): the robot is not-enough-info, or its start is a stand-in (station's usual / default spot)
  const estSpot = r => !!(r && (r.enough === false || (r.start && r.start.stand_in)));
  const SRC = { statbotics: "Statbotics EPA", event_average: "event avg" };
  // driver station of a robot on its alliance wall: from the plan (ds.xy), else 3 stations 2 m apart centred on the wall
  const dsXY = r => r.ds && r.ds.xy ? r.ds.xy : [r.alliance === "red" ? LEN : 0, WID / 2 + ((r.alliance === "red" ? 1 : -1) * ((r.station || 2) - 2) * 2)];
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
.mf-leg i.e { width: 18px; height: 10px; border: 2px dashed var(--nei); border-radius: 3px; margin-right: 4px; }
.mf-leg i.ds { width: 12px; height: 12px; border: 0; border-radius: 2px; background: var(--ink-2); }
.mf-r .mf-st { font-size: var(--fs-cap); color: var(--nei-ink); background: var(--nei); border-radius: var(--r-tag); padding: 1px 6px; width: fit-content; }
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

  // ---------------------------------------------------------------- the field: the real field picture, a calm start-spot view, and a play-through
  const PHN = { auto: "Auto", transition: "Transition", shift1: "Shift 1", shift2: "Shift 2", shift3: "Shift 3", shift4: "Shift 4", endgame: "Endgame" };
  const PHC = { auto: "#ff922b", transition: "#fcc419", shift1: "#51cf66", shift2: "#22b8cf", shift3: "#5c7cfa", shift4: "#cc5de8", endgame: "#ff6b6b" };
  const PH0 = [["auto", 0, 20], ["transition", 20, 30], ["shift1", 30, 55], ["shift2", 55, 80], ["shift3", 80, 105], ["shift4", 105, 130], ["endgame", 130, 160]];
  const CYS = ["transition", "shift1", "shift2", "shift3", "shift4", "endgame"], CYL = { transition: "Trans", shift1: "S1", shift2: "S2", shift3: "S3", shift4: "S4", endgame: "End" };
  const clock = t => { t = Math.max(0, Math.floor(t)); return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0"); };
  const FIELD_IMG = typeof FIELD !== "undefined" ? FIELD : Object.assign(new Image(), { src: "field.jpg" });
  const EVT = { first_score: "first score", first_defense: "defends", first_pickup: "first pickup", cross_neutral: "crosses neutral", climb: "climbs" };

  function css2() {
    if (document.getElementById("mf2-css")) return;
    const st = document.createElement("style"); st.id = "mf2-css";
    st.textContent = `
.mf-cv { background: #1b2430; }
.mf-hud { position: absolute; left: 10px; top: 8px; display: flex; gap: 8px; align-items: center; pointer-events: none; z-index: 2; }
.mf-clk { font-family: var(--font-num); font-stretch: var(--num-stretch); font-variant-numeric: tabular-nums; font-weight: 750; font-size: clamp(18px, 3.4vw, 30px); color: #fff; background: rgba(12,16,24,.72); border-radius: 10px; padding: 1px 12px; backdrop-filter: blur(3px); }
.mf-ph { font-size: var(--fs-foot); font-weight: 700; color: #1b1b1b; border-radius: 99px; padding: 2px 12px; background: var(--phc, #ddd); box-shadow: 0 1px 4px rgba(0,0,0,.35); }
.mf-big { position: absolute; inset: 0; margin: auto; width: fit-content; height: fit-content; z-index: 3; display: inline-flex; align-items: center; gap: 12px; border: 0; border-radius: 99px; cursor: pointer;
  padding: clamp(10px, 2vw, 18px) clamp(22px, 4vw, 40px); font: 750 clamp(17px, 3vw, 26px)/1 var(--font-ui, system-ui); color: var(--on-accent, #fff); background: var(--accent-fill, var(--accent)); box-shadow: 0 6px 28px rgba(0,0,0,.45), 0 0 0 6px rgba(255,255,255,.18); }
.mf-big:hover { filter: brightness(1.08); } .mf-big:focus-visible { outline: 3px solid #fff; outline-offset: 3px; }
.mf-big svg { width: 1.1em; height: 1.1em; }
.mf-ctl { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px 12px; align-items: center; }
.mf-ppw { display: inline-flex; align-items: center; gap: 4px; } .mf-pp { width: 44px; height: 44px; border-radius: 50%; border: 0; cursor: pointer; background: var(--accent-fill, var(--accent)); color: var(--on-accent, #fff); display: grid; place-items: center; }
.mf-pp svg { width: 20px; height: 20px; } .mf-pp:focus-visible, .mf-seg button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.mf-pp:disabled { opacity: .4; cursor: default; }
.mf-scr { position: relative; height: 44px; min-width: 0; }
.mf-bands { position: absolute; left: 0; right: 0; top: 12px; height: 22px; display: flex; border-radius: 6px; overflow: hidden; }
.mf-bands i { display: grid; place-items: center; font-style: normal; font-size: 10px; font-weight: 700; color: #1b1b1b; background: var(--c); overflow: hidden; white-space: nowrap; border-right: 1px solid rgba(0,0,0,.25); opacity: .85; }
.mf-bands i.on { opacity: 1; box-shadow: inset 0 -3px 0 rgba(0,0,0,.45); }
.mf-ticks { position: absolute; left: 0; right: 0; top: 34px; height: 8px; pointer-events: none; }
.mf-ticks b { position: absolute; width: 6px; height: 6px; margin-left: -3px; border-radius: 50%; background: var(--c); border: 1px solid var(--surface); }
.mf-scr input { position: absolute; inset: 0; width: 100%; margin: 0; background: transparent; -webkit-appearance: none; appearance: none; cursor: pointer; }
.mf-scr input::-webkit-slider-runnable-track { height: 44px; background: transparent; } .mf-scr input::-moz-range-track { height: 44px; background: transparent; }
.mf-scr input::-webkit-slider-thumb { -webkit-appearance: none; width: 6px; height: 40px; margin-top: 2px; border-radius: 3px; background: var(--ink); box-shadow: 0 0 0 2px var(--surface); }
.mf-scr input::-moz-range-thumb { width: 6px; height: 40px; border: 0; border-radius: 3px; background: var(--ink); box-shadow: 0 0 0 2px var(--surface); }
.mf-scr input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }
.mf-cyc { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 2px; margin-top: 2px; }
.mf-cyc span { text-align: center; padding: 1px 0; border-radius: 4px; background: var(--surface-2); line-height: 1.15; font-size: var(--fs-cap); color: var(--ink-2); }
.mf-cyc span b { display: block; font-size: 12px; color: var(--ink); font-variant-numeric: tabular-nums; } .mf-cyc span small { font-size: 9.5px; }
.mf-cyc span.on { background: var(--c); outline: 2px solid var(--ink); } .mf-cyc span.on, .mf-cyc span.on b { color: #1b1b1b; }
.mf-cyc-h { font-size: var(--fs-cap); color: var(--ink-2); margin-top: 3px; display: flex; gap: 4px; align-items: center; }
.mf-gl { border: 1px solid var(--accent); border-radius: var(--r-m); padding: 8px 10px 4px; background: var(--surface); }
.mf-gl-h { display: flex; align-items: center; gap: 8px; font-weight: 700; } .mf-gl-h .mf-sp { flex: 1; }
.mf-gl-h button { border: 1px solid var(--line-strong); background: var(--surface); color: var(--ink); border-radius: 99px; min-height: 32px; padding: 2px 12px; cursor: pointer; }
.mf-leg i.g { width: 14px; height: 14px; border: 2px solid var(--ink-2); border-radius: 50%; opacity: .5; } .mf-leg i.a { width: 14px; height: 14px; border: 0; border-radius: 50%; background: radial-gradient(circle, var(--ink-2), transparent 70%); opacity: .6; }
@media (max-width: 560px) { .mf-rob { grid-template-columns: 1fr; } .mf-ctl { grid-template-columns: auto minmax(0, 1fr); } .mf-ctl .mf-seg { grid-column: 1 / -1; justify-self: start; } .mf-cyc span b { font-size: 11px; } }
`;
    document.head.appendChild(st);
  }

  // The predicted play-through is off until it's tuned (owner, 2026-10-24): an unplayed match shows the field with "Match not
  // played yet", a played one replays only what the tracking saw. The code stays; on for one device:
  // localStorage.setItem("cs:field:predict", "1") (and removeItem to turn it off again).
  const predictOn = () => { try { return localStorage.getItem("cs:field:predict") === "1"; } catch (e) { return false; } };
  CS.matchField = (el, plan, opts = {}) => {
    css(); css2();
    const PREDICT = predictOn();
    const mini = !!opts.mini, US = opts.us || "frc4911", ev = String(plan.key || "").split("_")[0];
    const robots = (plan.robots || []).map(r => PREDICT ? r : Object.assign({}, r, { timeline: null }));
    const hasAct = !!(plan.played && !(opts.asof && plan.time > opts.asof) && robots.some(r => r.actual && r.actual.timeline));
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const tlOf = (r, v) => v === "actual" ? r.actual && r.actual.timeline : r.timeline;
    const anyPred = robots.some(r => r.timeline && r.timeline.xy), phases = ((robots.find(r => r.timeline && r.timeline.phases) || {}).timeline || {}).phases || PH0.map(([phase, t0, t1]) => ({ phase, t0, t1 }));
    const END = Math.max(...phases.map(p => p.t1), ...robots.flatMap(r => ["pred", "actual"].map(v => { const t = tlOf(r, v); return t && t.xy ? t.xy.length * (t.dt || 1) : 0; })), 1);
    const S = { view: hasAct ? "actual" : "pred", t: 0, playing: false, speed: 1, sel: null, routes: false };
    let raf = 0, dead = false, last = 0;
    el.innerHTML = "";
    const root = document.createElement("div"); root.className = "mf" + (mini ? " mini" : ""); el.appendChild(root);
    const phaseAt = t => phases.find(p => t >= p.t0 && t < p.t1) || phases[phases.length - 1];

    // ---- positions: 1 s samples, Catmull-Rom between them (smooth at 60 fps); null samples are skipped (hold the last known)
    function posAt(tl, t) {
      const xy = tl && tl.xy; if (!xy || !xy.length) return null;
      const dt = tl.dt || 1, f = Math.max(0, Math.min(xy.length - 1, t / dt)), i = Math.floor(f), u = f - i;
      const g = k => { for (let j = Math.max(0, Math.min(xy.length - 1, k)); j >= 0; j--) if (xy[j]) return xy[j]; for (let j = k; j < xy.length; j++) if (xy[j]) return xy[j]; return null; };
      if (!xy[i] && !xy[Math.min(xy.length - 1, i + 1)]) return null;     // no track here (not tracked at that moment)
      const p0 = g(i - 1), p1 = g(i), p2 = g(i + 1), p3 = g(i + 2); if (!p1 || !p2) return null;
      const c = k => .5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * u + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * u * u + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * u * u * u);
      return [c(0), c(1)];
    }
    const spreadAt = (tl, t) => { const s = tl && tl.spread; if (!s || !s.length) return 0; return s[Math.max(0, Math.min(s.length - 1, Math.round(t / (tl.dt || 1))))] || 0; };
    const calmXY = r => (r.start && r.start.xy) || (r.actual && r.actual.start && r.actual.start.xy) || dsXY(r);
    const cl = p => p && [Math.max(.35, Math.min(LEN - .35, p[0])), Math.max(.3, Math.min(WID - .3, p[1]))];   // tracking noise can sit outside the field: keep the bumper on the picture
    const nowXY = (r, v) => { const tl = tlOf(r, v); return cl((tl && posAt(tl, S.t)) || (S.t === 0 && v === "pred" ? calmXY(r) : null)); };

    function evLabel(r, e) {
      const t = tn(r.team);
      const base = e.kind === "first_defense" ? `${t} defends ${tn(e.target || "")}`.trim() : `${t} ${EVT[e.kind] || String(e.kind || "").replace(/_/g, " ")}`;
      return base + (e.p != null && e.p < .95 ? ` ${Math.round(e.p * 100)}%` : "");
    }

    function ui() {
      const A = plan.alliances || {}, nei = al => S.view === "pred" && A[al] && A[al].enough === false;
      const tot = ["red", "blue"].filter(al => A[al] && A[al].pred && A[al].pred.score).map(al => { const v = S.view === "actual" && A[al].actual ? A[al].actual.score : A[al].pred.score.mean;
        return `<div class="mf-at ${al}${nei(al) ? " nei" : ""}" title="${H(nei(al) ? CS.matchNote(plan, al) : "")}"><span>${al === "red" ? "Red" : "Blue"} ${S.view === "actual" ? "final" : "predicted"}</span><b>${v == null ? "–" : n0(v, S.view === "actual" ? 0 : 0)}</b>${nei(al) ? `<small>not enough info</small>` : ""}</div>`; }).join("");
      const seg = (lab, key, arr) => `<div class="mf-seg" role="group" aria-label="${lab}">${arr.map(([k, l]) => `<button type="button" data-${key}="${k}" aria-pressed="${S[key === "sp" ? "speed" : key] == k}">${l}</button>`).join("")}</div>`;
      const pa = hasAct && PREDICT ? seg("Predicted or actual", "view", [["pred", "Predicted"], ["actual", "Actual"], ["both", "Both"]]) : "";
      const rt = anyPred ? `<button type="button" class="mf-tg" data-routes aria-pressed="${S.routes}" title="Where the robots usually drive while cycling (path density)">Most-travelled routes</button>` : "";
      const col = al => `<div class="mf-col">${robots.filter(r => r.alliance === al).map(rowHtml).join("")}</div>`;
      const help = !PREDICT ? CS.help(`${hasAct ? "<b>Play</b> replays where the video tracking saw each robot in this match (real tracking, not a prediction). " : ""}The tabs on the walls are the driver stations with their teams. Tap a team for its at-a-glance view.`, "How to read the field")
        : CS.help(`<b>Play</b> moves the six robots along where the model expects each of them to be over the match: auto, transition, shifts 1 to 4, endgame. The soft halo is how unsure it is at that moment (tap a team for its dashed ring), never an exact position; a yellow dashed outline is an estimate for a team we have little video of. Small labels pop up when the model expects a first score, a first defense or a climb (with its chance).<br><b>Most-travelled routes</b> shades the paths a team uses most while cycling. After the match, <b>Actual</b> replays where the video tracking saw each robot; <b>Both</b> draws actual solid and the prediction faint.${calib ? CS.matchCalib(calib, "start_side_hit") : ""}${calib && calib.timeline ? `<br><b>How far to trust it:</b> ${calib.timeline.route_err_m != null ? `the predicted route is usually right (within ~${n0(calib.timeline.route_err_m, 1)} m of where the team drives)` : "the route a robot drives is the better part of the prediction"}; <b>where</b> it is in its cycle at a given second is not: half the time it is over ~${n0(calib.timeline.pos_err_m && calib.timeline.pos_err_m.median, 1)} m off at this event (${calib.timeline.n_matches} matches checked). Read the halo: the robot may be anywhere inside it.${calib.timeline.first_score_t_err_s != null ? ` First-score times are off by about ${n0(calib.timeline.first_score_t_err_s, 0)} s.` : ""}` : ""}`, "How to read the field");
      const can = anyPred || (hasAct && S.view !== "pred");
      root.innerHTML = `<div class="mf-bar">${pa}${rt}<span class="mf-sp"></span>${help}</div>
        ${tot ? `<div class="mf-tot">${tot}</div>` : ""}
        <div class="mf-cv" tabindex="0" aria-label="Field with the six robots. Space plays or pauses, left and right arrows step one second."><canvas role="img" aria-label="Top-down field with the six robots at their start spots${can ? " and their expected play-through" : ""}"></canvas>
          <div class="mf-hud"><span class="mf-clk">0:00</span><span class="mf-ph">Auto</span></div>
          ${can ? `<button type="button" class="mf-big" data-play>${ICO.play}<span>${reduce ? "Step" : "Play"} the match</span></button>` : ""}</div>
        ${can ? `<div class="mf-ctl"><span class="mf-ppw"><button type="button" class="mf-pp" data-pp aria-label="Play">${ICO.play}</button>${help}</span>
          <div class="mf-scr"><div class="mf-bands">${phases.map(p => `<i data-ph="${p.phase}" style="flex:${p.t1 - p.t0};--c:${PHC[p.phase] || "#bbb"}">${(p.t1 - p.t0) / END > .09 ? PHN[p.phase] || p.phase : ""}</i>`).join("")}</div><div class="mf-ticks"></div>
            <input type="range" min="0" max="${END}" step="1" value="0" aria-label="Match time, seconds"></div>
          ${seg("Speed", "sp", [[1, "1×"], [2, "2×"], [4, "4×"]])}</div>` : PREDICT ? `<div class="mf-note">No tracked play-through for these robots yet: the field shows each robot's start spot.</div>` : ""}
        <div class="mf-leg"><span><i class="ds"></i>driver station</span>${PREDICT ? `<span><i class="e"></i>estimate (little video)</span><span><i class="a"></i>unsure (halo)</span>` : ""}${S.view === "both" ? `<span><i class="g"></i>prediction (faint)</span>` : ""}${S.routes ? `<span><i class="h"></i>most-travelled route</span>` : ""}</div>
        <div class="mf-rob">${col("red")}${col("blue")}</div><div class="mf-gl" hidden></div>`;
      canvas = root.querySelector("canvas"); size(); ticks(); hud(); draw();
      root.querySelectorAll("[data-view]").forEach(b => b.onclick = () => { S.view = b.dataset.view; ui(); });
      root.querySelectorAll("[data-sp]").forEach(b => b.onclick = () => { S.speed = +b.dataset.sp; root.querySelectorAll("[data-sp]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); });
      root.querySelectorAll("[data-routes]").forEach(b => b.onclick = () => { S.routes = !S.routes; b.setAttribute("aria-pressed", String(S.routes)); draw(); });
      root.querySelectorAll("[data-team]").forEach(b => b.onclick = () => pick(b.dataset.team));
      root.querySelectorAll("[data-play],[data-pp]").forEach(b => b.onclick = () => toggle());
      const rg = root.querySelector(".mf-scr input"); if (rg) rg.oninput = () => { S.t = +rg.value; hud(); draw(); };
      canvas.onclick = e => { const r = canvas.getBoundingClientRect(), best = hit((e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * Hh); if (best) pick(best); };
      glance();
      if (opts.onChange) opts.onChange({ view: S.view === "actual" ? "actual" : "pred", sel: S.sel });
    }
    const ICO = { play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l13-7.5z" fill="currentColor"/></svg>', pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4h4v16H6zM14 4h4v16h-4z" fill="currentColor"/></svg>' };
    function ticks() {   // event dots under the scrubber: when the model expects something to happen
      colors(); const box = root.querySelector(".mf-ticks"); if (!box) return;
      box.innerHTML = robots.filter(r => !S.sel || r.team === S.sel).flatMap(r => ((r.timeline && r.timeline.events) || []).map(e => `<b style="left:${(e.t / END * 100).toFixed(2)}%;--c:${r.alliance === "red" ? C.red : C.blue}" title="${H(evLabel(r, e))} at ${clock(e.t)}"></b>`)).join("");
    }
    function hud() {
      const p = phaseAt(S.t), c = root.querySelector(".mf-clk"); if (!c) return;
      c.textContent = clock(S.t); const ph = root.querySelector(".mf-ph"); ph.textContent = PHN[p.phase] || p.phase; ph.style.setProperty("--phc", PHC[p.phase] || "#ddd");
      const rg = root.querySelector(".mf-scr input"); if (rg && document.activeElement !== rg) rg.value = Math.round(S.t); else if (rg) rg.value = Math.round(S.t);
      root.querySelectorAll(".mf-bands i").forEach(i => i.classList.toggle("on", i.dataset.ph === p.phase));
      root.querySelectorAll(".mf-cyc span").forEach(s => s.classList.toggle("on", s.dataset.cy === p.phase));
      const pp = root.querySelector("[data-pp]"); if (pp) { pp.innerHTML = S.playing ? ICO.pause : ICO.play; pp.setAttribute("aria-label", S.playing ? "Pause" : reduce ? "Step forward" : "Play"); }
      const big = root.querySelector(".mf-big"); if (big) big.hidden = S.playing || S.t > 0;
    }
    function toggle() {
      if (reduce) { S.t = S.t >= END ? 0 : Math.min(END, S.t + 5); hud(); draw(); return; }
      S.playing = !S.playing; if (S.playing && S.t >= END) S.t = 0;
      last = 0; cancelAnimationFrame(raf); if (S.playing) raf = requestAnimationFrame(tick); hud();
    }
    function tick(now) {
      if (dead || !S.playing) return;
      if (!root.isConnected) { S.playing = false; return; }
      if (last) S.t = Math.min(END, S.t + (now - last) / 1000 * S.speed); last = now;
      if (S.t >= END) S.playing = false;
      hud(); draw(); if (S.playing) raf = requestAnimationFrame(tick);
    }
    function step(d) { S.playing = false; cancelAnimationFrame(raf); S.t = Math.max(0, Math.min(END, Math.round(S.t) + d)); hud(); draw(); }
    function pick(tm) { S.sel = S.sel === tm ? null : tm; root.querySelectorAll(".mf-r").forEach(r => { const b = r.querySelector("[data-team]"), on = b.dataset.team === S.sel; r.classList.toggle("on", on); r.classList.toggle("dim", !!S.sel && !on); b.setAttribute("aria-pressed", String(on)); });
      ticks(); glance(); draw(); if (opts.onChange) opts.onChange({ view: S.view === "actual" ? "actual" : "pred", sel: S.sel }); }
    function glance() {
      const box = root.querySelector(".mf-gl"); if (!box) return;
      if (!S.sel) { box.hidden = true; box.innerHTML = ""; return; }
      box.hidden = false; const tm = S.sel; box.innerHTML = `<div class="mf-gl-h"><b>${tn(tm)}</b> at a glance<span class="mf-sp"></span><button type="button" data-x>Close</button></div><div class="mf-gl-b"><span class="muted">Loading…</span></div>`;
      box.querySelector("[data-x]").onclick = () => pick(tm);
      if (CS.teamGlance) CS.teamGlance(box.querySelector(".mf-gl-b"), ev, tm).catch(() => { const b = box.querySelector(".mf-gl-b"); if (b) b.textContent = "Could not load this team."; });
    }

    function rowHtml(r) {
      const us = r.team === US, on = S.sel === r.team, dim = S.sel && !on, ok = r.enough !== false || S.view !== "pred";
      const cy = r.tele && r.tele.cycles_per_shift, inact = r.tele && r.tele.inactive, ar = r.actual && r.actual.inactive && r.actual.inactive.role, role = S.view === "pred" ? inact && inact.role : ar || (inact && inact.role);
      const help = inact ? CS.help(`<b>Predicted job while its hub is inactive</b>: ${H(ROLE_WHAT[inact.role] || inact.role)}. ${H(inact.basis || "")}<br>${Object.entries(inact.p || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${H(ROLE[k] || k)} ${n0(v * 100)}%`).join(" · ")}<br>A pattern from past matches, not a certainty.`, "Predicted job") : "";
      const cyc = cy ? `<div class="mf-cyc-h">Cycles per shift ${CS.help("How many scoring cycles (collect, then shoot) this team does in each period: the average over its tracked matches, plus or minus one standard deviation. Intake recall is about 45 %, so counts are a floor.", "Cycles per shift")}</div>
        <div class="mf-cyc">${CYS.map(k => { const c = cy[k]; return `<span data-cy="${k}" style="--c:${PHC[k]}"${c ? ` title="${H(PHN[k])}: ${n0(c.mean, 1)} cycles, give or take ${n0(c.sd, 1)}"` : ""}>${CYL[k]}<b>${c ? n0(c.mean, 1) : "–"}</b><small>${c && c.sd != null ? "±" + n0(c.sd, 1) : ""}</small></span>`; }).join("")}</div>` : "";
      const sn = S.view !== "actual" && r.start && r.start.stand_in ? `<span class="mf-st" title="${H(r.start.basis || "")}">${r.start.default ? "default spot" : "usual spot"}: estimate</span>` : "";
      const tl = r.timeline;
      return `<div class="mf-r ${r.alliance}${on ? " on" : ""}${dim ? " dim" : ""}${ok ? "" : " no"}">
        <button type="button" class="mf-sel" data-team="${r.team}" aria-pressed="${on}" title="${on ? "close the team view" : "open this team's at-a-glance view"}"><b>${tn(r.team)}</b><small>${us ? "us · " : ""}station ${r.station}${r.n_video != null ? ` · ${r.n_video} tracked` : ""}</small></button>
        ${ok ? "" : `<span class="mf-why">not enough info: ${H(r.why || "")}</span>`}${sn}
        ${role ? `<span style="display:flex;gap:4px;align-items:center;flex-wrap:wrap"><span class="mf-role">${H(ROLE[role] || role)}</span>${S.view === "pred" ? help : ""}</span>` : ""}${cyc}
        ${on && tl && tl.basis ? `<span class="mf-sub">${H(tl.basis)}</span>` : ""}</div>`;
    }

    // ---- canvas
    let canvas, dpr = 1, W = 0, Hh = 0, C = {}, calib = opts.calib || null;
    const colors = () => {
      const cs = getComputedStyle(root), g = (n, d) => (cs.getPropertyValue(n) || "").trim() || d;
      C = { red: g("--red-fill", "#c33"), blue: g("--blue-fill", "#33c"), ink: g("--ink", "#111"), acc: g("--accent", "#06c"), nei: g("--nei", "#f5c518") };
    };
    function size() {
      if (!canvas) return;
      const w = Math.max(220, canvas.parentElement.clientWidth || root.clientWidth || 360);
      dpr = Math.min(3, window.devicePixelRatio || 1); W = w; Hh = Math.round(w * WID / LEN);
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(Hh * dpr); heatC = {};
    }
    const sc = () => W / LEN, X = x => (LEN - x) * sc(), Y = y => y * sc();   // the match player's mapping: red end on the left, y down
    const alc = a => a === "red" ? C.red : C.blue;
    const nrm = document.createElement("canvas").getContext("2d");
    const al = (c, a) => { nrm.fillStyle = c; const v = nrm.fillStyle; return v[0] === "#" ? `rgba(${parseInt(v.slice(1, 3), 16)},${parseInt(v.slice(3, 5), 16)},${parseInt(v.slice(5, 7), 16)},${a})` : v; };
    let heatC = {};
    function heatImg(r) {   // sparse 0.25 m cells -> a smooth density in the alliance colour (soft blobs summed offscreen, then coloured)
      const h = r.tele && r.tele.path_heat; if (!h || !h.cells || !h.cells.length) return null;
      const k = r.team + ":" + W; if (heatC[k]) return heatC[k];
      const off = document.createElement("canvas"); off.width = Math.round(W); off.height = Math.round(Hh); const o = off.getContext("2d"), rad = Math.max(8, h.cell * sc() * 2.6);
      h.cells.forEach(([ix, iy, w]) => { const x = X((ix + .5) * h.cell), y = Y((iy + .5) * h.cell), g = o.createRadialGradient(x, y, 0, x, y, rad);
        g.addColorStop(0, `rgba(0,0,0,${Math.min(.45, .08 + w * .35)})`); g.addColorStop(1, "rgba(0,0,0,0)"); o.fillStyle = g; o.fillRect(x - rad, y - rad, rad * 2, rad * 2); });
      o.globalCompositeOperation = "source-in"; o.fillStyle = alc(r.alliance); o.fillRect(0, 0, off.width, off.height);
      return (heatC[k] = off);
    }
    function stations(ctx) {
      const fs = Math.max(8, Math.min(12, W / 40)), w = Math.max(26, Math.min(40, W * .085)), h = Math.max(24, Math.min(46, 1.55 * sc()));
      robots.forEach(r => { const xy = dsXY(r), x = X(xy[0]), y = Y(xy[1]), left = r.alliance === "red", x0 = left ? x : x - w, t = tn(r.team);
        ctx.globalAlpha = S.sel && S.sel !== r.team ? .35 : .95;
        ctx.beginPath(); ctx.roundRect(x0, y - h / 2, w, h, left ? [0, 5, 5, 0] : [5, 0, 0, 5]); ctx.fillStyle = alc(r.alliance); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.font = `600 ${Math.max(7, fs - 3)}px system-ui,sans-serif`; ctx.fillText(r.station != null ? String(r.station) : "", x0 + w / 2, y - h / 2 + Math.max(5, fs - 3) * .9 + 1);
        ctx.font = `700 ${fs * (t.length > 4 ? .85 : 1)}px system-ui,sans-serif`; ctx.fillText(t, x0 + w / 2, y + h * .14); });
      ctx.globalAlpha = 1; ctx.textBaseline = "alphabetic";
    }
    function bot(ctx, r, xy, o) {   // a robot: team-number bumper tab; estimate = yellow dashed outline, no fill; ghost = faint
      const x = X(xy[0]), y = Y(xy[1]), fs = Math.max(8, Math.min(12, W / 38)), t = tn(r.team), w = Math.max(24, t.length * fs * .66 + 8), h = fs + 7, est = estSpot(r) && o.view !== "actual";
      ctx.globalAlpha = (o.ghost ? .38 : 1) * (S.sel && S.sel !== r.team ? .3 : 1);
      ctx.save(); ctx.shadowColor = "rgba(0,0,0,.55)"; ctx.shadowBlur = o.ghost ? 0 : 6; ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 4);
      if (est) { ctx.fillStyle = "rgba(20,24,32,.72)"; ctx.fill(); ctx.restore(); ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 4); ctx.setLineDash([4, 3]); ctx.strokeStyle = C.nei; ctx.lineWidth = 2.5; ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = "#fff"; }
      else { ctx.fillStyle = alc(r.alliance); ctx.fill(); ctx.restore(); if (!o.ghost) { ctx.strokeStyle = "rgba(255,255,255,.85)"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 4); ctx.stroke(); } ctx.fillStyle = "#fff"; }
      if (r.team === US && !o.ghost) { ctx.strokeStyle = C.acc; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.roundRect(x - w / 2 - 3, y - h / 2 - 3, w + 6, h + 6, 7); ctx.stroke(); }
      ctx.font = `700 ${fs}px system-ui,sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(t, x, y + .5); ctx.textBaseline = "alphabetic"; ctx.globalAlpha = 1;
    }
    function trail(ctx, tl, r, solid) {   // a short fading tail: the last ~5 s
      const a = alc(r.alliance); let prev = null;
      for (let k = 20; k >= 0; k--) { const t = S.t - k * .25; if (t < 0) continue; const p = posAt(tl, t); if (!p) { prev = null; continue; }
        if (prev) { ctx.strokeStyle = al(a, (1 - k / 21) * (solid ? .85 : .4) * (S.sel && S.sel !== r.team ? .3 : 1)); ctx.lineWidth = 1 + (1 - k / 21) * (solid ? 3.5 : 2); ctx.lineCap = "round"; ctx.beginPath(); ctx.moveTo(X(prev[0]), Y(prev[1])); ctx.lineTo(X(p[0]), Y(p[1])); ctx.stroke(); }
        prev = p; }
    }
    function pops(ctx) {   // timeline events pop up as small labelled markers for ~5 s after they happen (predicted view only: they are the model's expectation); labels are stacked so none overlap
      if (S.view === "actual") return;
      const fs = Math.max(9, Math.min(12, W / 44)), h = fs + 9, placed = [], list = [];
      robots.forEach(r => ((r.timeline && r.timeline.events) || []).forEach(e => {
        const age = S.t - e.t; if (age < 0 || age > 6 || (S.sel && S.sel !== r.team)) return;
        const xy = cl(e.xy || posAt(r.timeline, e.t)); if (xy) list.push({ r, e, age, xy }); }));
      list.sort((a, b) => b.age - a.age);   // oldest first: the newest label gets the free spot
      list.forEach(({ r, e, age, xy }) => {
        const k = age < .3 ? age / .3 : age > 4.5 ? 1 - (age - 4.5) / 1.5 : 1, lab = evLabel(r, e);
        ctx.font = `700 ${fs}px system-ui,sans-serif`; const w = ctx.measureText(lab).width + 14, x = Math.max(w / 2 + 2, Math.min(W - w / 2 - 2, X(xy[0])));
        let y = Math.max(h, Y(xy[1]) - 22), tries = 0; const hits = yy => placed.some(q => Math.abs(q.x - x) < (q.w + w) / 2 + 2 && Math.abs(q.y - yy) < h + 2);
        while (hits(y) && tries++ < 8) y += (tries % 2 ? -1 : 1) * tries * (h + 2);   // alternately above and below
        y = Math.max(h / 2 + 1, Math.min(Hh - h / 2 - 1, y)); placed.push({ x, y, w });
        ctx.globalAlpha = Math.max(0, k); ctx.beginPath(); ctx.arc(X(xy[0]), Y(xy[1]), 3 + 6 * (1 - Math.min(1, age / 1.2)), 0, 7); ctx.strokeStyle = alc(r.alliance); ctx.lineWidth = 2; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(X(xy[0]), Y(xy[1])); ctx.lineTo(x, y); ctx.strokeStyle = al(alc(r.alliance), .7); ctx.lineWidth = 1; ctx.stroke();   // leader to the spot
        ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, h / 2); ctx.fillStyle = "rgba(255,248,214,.97)"; ctx.fill(); ctx.strokeStyle = alc(r.alliance); ctx.lineWidth = 2; ctx.stroke();
        ctx.fillStyle = "#1b1b1b"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(lab, x, y + .5); ctx.textBaseline = "alphabetic"; ctx.globalAlpha = 1; });
    }
    function draw() {
      if (!canvas || dead) return;
      colors(); const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, Hh);
      if (FIELD_IMG.complete && FIELD_IMG.naturalWidth) ctx.drawImage(FIELD_IMG, 0, 0, W, Hh); else { ctx.fillStyle = "#3a3f46"; ctx.fillRect(0, 0, W, Hh); }
      ctx.fillStyle = "rgba(10,14,22,.22)"; ctx.fillRect(0, 0, W, Hh);   // calm the picture so the robots read
      if (S.routes) robots.forEach(r => { const im = heatImg(r); if (!im) return; ctx.globalAlpha = S.sel ? (S.sel === r.team ? 1 : 0) : .55; if (ctx.globalAlpha) ctx.drawImage(im, 0, 0, W, Hh); ctx.globalAlpha = 1; });
      stations(ctx);
      const views = S.view === "both" ? ["pred", "actual"] : [S.view];
      views.forEach(v => robots.forEach(r => {
        const tl = tlOf(r, v), ghost = S.view === "both" && v === "pred", xy = nowXY(r, v); if (!xy) return;
        if (v === "pred" && tl && tl.spread && !ghost) { const sp = Math.max(.8, Math.min(3.2, spreadAt(tl, S.t))) * sc(), cx = X(xy[0]), cy = Y(xy[1]); const c = estSpot(r) ? C.nei : alc(r.alliance);   // the honest part: where it may really be
          // six ~3 m halos at full strength wash the field over: faint for everyone, the dashed ring for the selected team only
          const on = S.sel === r.team, k = S.sel && !on ? .35 : 1;
          const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, sp); g.addColorStop(0, al(c, (on ? .34 : .16) * k)); g.addColorStop(.6, al(c, (on ? .16 : .07) * k)); g.addColorStop(1, al(c, 0)); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, sp, 0, 7); ctx.fill();
          if (on) { ctx.setLineDash([5, 4]); ctx.lineWidth = 1.5; ctx.strokeStyle = al(c, .85); ctx.stroke(); ctx.setLineDash([]); } }
        if (tl && S.t > 0) trail(ctx, tl, r, !ghost);
      }));
      if (!PREDICT && S.view === "pred") {     // prediction off: the field only, and why there's nothing on it
        const ahead = !plan.played || (opts.asof && plan.time > opts.asof);
        const t = ahead ? "Match not played yet" : "Not tracked yet", sub = ahead ? "" : "the replay appears once its video is tracked";
        ctx.fillStyle = "rgba(10,14,22,.62)"; const bw = Math.min(W - 24, mini ? 260 : 380), bh = sub ? (mini ? 52 : 64) : (mini ? 38 : 48);
        ctx.beginPath(); ctx.roundRect(W / 2 - bw / 2, Hh / 2 - bh / 2, bw, bh, 10); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.font = `700 ${mini ? 15 : 20}px system-ui`;
        ctx.fillText(t, W / 2, Hh / 2 - (sub ? (mini ? 8 : 10) : 0));
        if (sub) { ctx.font = `500 ${mini ? 11 : 13}px system-ui`; ctx.fillStyle = "rgba(255,255,255,.8)"; ctx.fillText(sub, W / 2, Hh / 2 + (mini ? 12 : 16)); }
        ctx.textBaseline = "alphabetic";
        return;
      }
      views.forEach(v => robots.forEach(r => { const xy = nowXY(r, v); if (xy) bot(ctx, r, xy, { view: v, ghost: S.view === "both" && v === "pred" }); }));
      pops(ctx);
      if (PREDICT && S.t === 0 && !robots.some(r => r.timeline && r.timeline.xy)) { ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.font = "600 13px system-ui"; ctx.textAlign = "center"; ctx.fillText("start spots (no tracked play-through yet)", W / 2, Hh - 10); }
    }
    function hit(px, py) {   // nearest robot (by its drawn position) within 30 px
      let best = null, bd = 30;
      robots.forEach(r => { const xy = nowXY(r, S.view === "both" ? "actual" : S.view) || nowXY(r, "pred"); if (!xy) return; const d = Math.hypot(X(xy[0]) - px, Y(xy[1]) - py); if (d < bd) { bd = d; best = r.team; } });
      return best;
    }

    root.addEventListener("keydown", e => {
      const t = e.target, tag = t && t.tagName;
      if (e.key === " " && tag !== "BUTTON" && tag !== "SELECT" && tag !== "A") { e.preventDefault(); toggle(); }
      else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && tag !== "INPUT" && tag !== "SELECT") { e.preventDefault(); step(e.key === "ArrowLeft" ? -1 : 1); }
    });
    ui();
    if (!calib && ev && CS.mpCalib) CS.mpCalib(ev).then(c => { if (c && !dead && !calib) { calib = c; const keep = S.t; ui(); S.t = keep; hud(); draw(); } }).catch(() => { });
    const ro = new ResizeObserver(() => { if (!root.isConnected) return; const w = canvas && canvas.parentElement.clientWidth; if (w && Math.abs(w - W) > 1) { size(); draw(); } });
    ro.observe(root);
    const mo = new MutationObserver(() => { if (root.isConnected) draw(); }); mo.observe(document.documentElement, { attributes: true });
    if (!(FIELD_IMG.complete && FIELD_IMG.naturalWidth)) FIELD_IMG.addEventListener("load", () => draw(), { once: true });
    const destroy = () => { dead = true; S.playing = false; cancelAnimationFrame(raf); ro.disconnect(); mo.disconnect(); };
    if (typeof cleanup !== "undefined") cleanup.push(destroy);
    return { destroy, state: S, seek: t => { S.t = t; hud(); draw(); } };
  };
})();
