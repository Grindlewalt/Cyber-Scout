"use strict";
/* schedule.js: #/<ev>/schedule. All matches; every row opens (whole row, Enter/Space) to the match plan: the field (Auto | Tele,
 * predicted vs actual once played), the period bars (red and blue both) and the match video. Status pills + header from the queue
 * backend (event.json rows' wait/state_note, event.json queue). Data: schedule.json, event.json, mp/<match>.json (matchfield.js). */
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
.sc tr.mrow{cursor:pointer} .sc tr.mrow:hover td{background:var(--surface-2)}
.sc .rowbtn{display:inline-flex;align-items:center;gap:2px;border:0;background:none;color:var(--ink);font:inherit;padding:4px 6px 4px 0;min-height:36px;cursor:pointer}.sc .rowbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.sc .spill{display:inline-block;margin-left:6px;padding:0 7px;border-radius:99px;font-size:11px;line-height:17px;border:1px solid var(--line-strong);color:var(--ink-2);white-space:nowrap;vertical-align:1px}
.sc .spill.idle{border-style:dashed;color:var(--ink-3)}.sc .spill.warn{background:var(--caution-tint);color:var(--caution);border-color:var(--caution-line)}.sc .spill.bad{color:var(--bad);border-color:var(--bad)}.sc .spill.ok{color:var(--ink)}
.sc .sc-qh{margin:0 0 12px;padding:8px 12px;border-radius:var(--r-p);background:var(--surface-2);font-size:var(--fs-foot);color:var(--ink-2)}.sc .sc-qh b{color:var(--ink)}
.sc .mx{padding:14px;display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:18px}.sc .mx-r{display:grid;gap:12px;align-content:start;min-width:0}.sc .mx>*{min-width:0}
.sc .vid{display:grid;gap:8px}.sc .vid video,.sc .vid iframe{width:100%;aspect-ratio:16/9;border:0;border-radius:var(--r-p);background:#000}
.sc .vplay{display:flex;align-items:center;justify-content:center;gap:8px;aspect-ratio:16/9;border:1px solid var(--line-strong);border-radius:var(--r-p);background:var(--surface-3);color:var(--ink);cursor:pointer;font-weight:600}.sc .vplay svg{width:22px;height:22px}
.sc .mpnote{padding:12px 14px;border:1px dashed var(--line-strong);border-radius:var(--r-p);color:var(--ink-2);font-size:var(--fs-foot)}
.sc tr.open .chev{transform:rotate(90deg)}
.sc tr.mrow.next td{background:var(--sel)}
.sc .t{display:inline-block;padding:0 4px;border-radius:3px;font-variant-numeric:tabular-nums;margin-right:2px}
@media (pointer:coarse){.sc a.t{display:inline-flex;align-items:center;justify-content:center;min-height:36px;min-width:44px;margin:2px 3px 2px 0}}
.sc .t.r{background:var(--red-soft)} .sc .t.b{background:var(--blue-soft)} .sc .t.us{outline:2px solid var(--accent);outline-offset:-1px;font-weight:700}
.sc .pbar{display:flex;height:8px;width:80px;border-radius:4px;overflow:hidden;background:var(--surface-3);gap:1px}
.sc .pbar i.r{background:var(--red-fill)} .sc .pbar i.b{background:var(--blue-fill)}
.sc .sc2{font-variant-numeric:tabular-nums}
.sc .win{font-weight:700}
.sc tr.det td{padding:0;background:var(--surface-2);white-space:normal}
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
@media (max-width:900px){.sc .mx{grid-template-columns:1fr}.sc .hide-s{display:none}.sc .per{grid-template-columns:54px 1fr 46px 46px 46px}}
`;
    document.head.appendChild(st);
  }

  function teams(ts, c, us) { return ts.map(k => `<a class="t ${c} ${k === us ? "us" : ""}" href="#/${S.ev}/t/${k}">${tn(k)}</a>`).join(""); }

  const WAIT = { not_played: ["not played yet", "idle"], setup: ["needs video setup", "warn"], queued: ["queued", ""], video: ["no video yet", "warn"],
    stream: ["waiting for stream clip", ""], retry: ["retrying", ""], model: ["waiting for the detector", ""], failed: ["failed", "bad"] };
  const scoreOf = v => v == null || v < 0 ? "–" : v;
  // status pill from the queue backend; paused processing: every waiting code except not_played reads "not tracked (paused)"
  function pill(m) {
    const r = S.rows[m.key], w = r && r.wait, pr = CS.processing;
    if (!w) return m.analyze_url && m.played ? `<span class="spill ok" title="tracked from video">tracked</span>` : "";
    let [t, c] = WAIT[w] || [w, ""];
    if (pr && pr.paused && w !== "not_played") { t = "not tracked (paused)"; c = "warn"; }
    return `<span class="spill ${c}"${r.state_note ? ` title="${H(r.state_note)}"` : ""}>${H(t)}</span>`;
  }
  function header(evj) {
    const q = evj && evj.queue; if (!q && !evj) return "";
    const parts = [], pr = CS.processing, paused = !!(pr && pr.paused);
    if (evj.n_total) parts.push(`<b>${evj.n_done ?? 0} of ${evj.n_total}</b> matches tracked`);
    const t = q && q.tracking; if (t) parts.push(typeof t === "object" ? `tracking ${H(t.short || t.key || "")} now` : t > 0 ? `tracking ${t} now` : "");
    const W = (q && q.waiting) || {}, lab = { setup: "need video setup", queued: "queued", video: "waiting for video", stream: "waiting for the stream clip", model: "waiting for the detector", retry: "retrying", failed: "failed" };
    if (paused) { const n = Object.entries(W).filter(([k]) => k !== "not_played").reduce((a, [, v]) => a + (v || 0), 0); if (n) parts.push(`${n} not tracked (paused)`); }
    else Object.keys(lab).forEach(k => { if (W[k]) parts.push(`${W[k]} ${lab[k]}`); });
    if (W.not_played) parts.push(`${W.not_played} not played yet`);
    if (q && q.next && q.next.short) parts.push(`next: ${H(q.next.short)}${q.next.time ? " at " + H(CS.fmtWhen ? CS.fmtWhen(q.next.time) : fmtT(q.next.time)) : ""}`);
    return parts.filter(Boolean).length ? `<div class="sc-qh" role="status">${parts.filter(Boolean).join(" · ")}</div>` : "";
  }
  // the video: local file, else the YouTube embed (loaded on tap, the CSP allows youtube.com frames), always the link to the full replay
  function videoHtml(m, mp) {
    const v = (mp && mp.video) || m.video || {}, local = CS.mediaUrl && (CS.mediaUrl(v.local) || CS.mediaUrl(v.clip)), yt = typeof v.youtube === "string" && /^[A-Za-z0-9_-]{11}$/.test(v.youtube) ? v.youtube : null;
    const an = m.analyze_url || (mp && mp.tracked ? `#/${S.ev}/a/${m.key}` : null);
    const link = an ? `<a class="dbtn" href="${H(an)}">Full analysis and replay</a>` : "";
    if (local) return `<div class="vid"><button type="button" class="vplay" data-local="${H(local)}" aria-label="Play the match video ${H(m.short)}">${CS.icon("play")}<span>Play match video</span></button>${link}</div>`;
    if (yt) return `<div class="vid"><button type="button" class="vplay" data-yt="${yt}" aria-label="Play the match video ${H(m.short)}">${CS.icon("play")}<span>Play match video</span></button>${link}</div>`;
    return `<div class="vid"><div class="muted small">${m.played ? "No video for this match yet." : "Video appears here after the match."}</div>${link}</div>`;
  }
  async function fill(m, box) {
    let mp = S.mp[m.key];
    if (mp === undefined) mp = S.mp[m.key] = m.mp === false ? null : await api(`${S.ev}/mp/${m.key}.json`).catch(() => null);
    const calib = await CS.mpCalib(S.ev);
    if (!box.isConnected) return;
    if (!mp || !mp.robots) {
      const b = m.breakdown;
      box.innerHTML = `<div class="mpnote">No field plan for this match yet (older data, or this event has no tracked matches).</div>${b ? `<div class="sum">${H(b.summary || "")}</div>` : ""}${videoHtml(m, null)}`; return; }
    box.innerHTML = `<div class="mx-f"></div><div class="mx-r"><div class="mx-p"></div>${videoHtml(m, mp)}</div>`;
    const pe = box.querySelector(".mx-p");
    CS.matchField(box.querySelector(".mx-f"), mp, { mini: true, us: S.team, asof: S.asof, calib, onChange: st => { pe.innerHTML = CS.matchPeriods(mp, { asof: S.asof, calib, view: st.view }); } });
    const lb = box.querySelector("[data-local]");
    if (lb) lb.onclick = () => { const v = document.createElement("video"); v.controls = true; v.autoplay = true; v.playsInline = true; v.src = lb.dataset.local; v.setAttribute("aria-label", "Match video"); lb.replaceWith(v); };
    const b = box.querySelector("[data-yt]");
    if (b) b.onclick = () => { b.closest(".vid").insertAdjacentHTML("afterbegin", `<iframe src="https://www.youtube.com/embed/${b.dataset.yt}?autoplay=1" title="Match video" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`); b.remove(); };
  }

  function rowHtml(m, team) {
    const hasB = true;
    const us = m.us ? team : null;
    let mid;
    const L = !m.played && S.live && S.live[m.key];            // livestream result, TBA not posted yet (event.json row)
    const ls = L && L.red && L.blue && L.red.score != null && L.blue.score != null ? { red: L.red.score, blue: L.blue.score, winner: L.winner || (L.red.score === L.blue.score ? "tie" : null) } : null;
    if (ls) {
      mid = `<td class="num sc2"><span class="t r ${ls.winner === "red" ? "win" : ""}">${scoreOf(ls.red)}</span> – <span class="t b ${ls.winner === "blue" ? "win" : ""}">${scoreOf(ls.blue)}</span>${CS.liveTag(L, "blk")}</td>
        <td class="hide-s small muted">${H(CS.liveTracking(L.live, L.state === "done"))}</td>`;
    } else if (m.played) {
      const r = m.result, rw = r.winner === "red", bw = r.winner === "blue";
      mid = `<td class="num sc2"><span class="t r ${rw ? "win" : ""}">${scoreOf(r.red)}</span> – <span class="t b ${bw ? "win" : ""}">${scoreOf(r.blue)}</span></td>
        <td class="hide-s small muted">${r.rp?.red != null ? `${r.rp.red}/${r.rp.blue} RP` : ""}</td>`;
    } else {
      const p = m.pred;
      mid = p ? `<td class="num"><span class="pbar" title="red ${P(p.p_red)} · blue ${P(1 - p.p_red)}"><i class="r" style="width:${p.p_red * 100}%"></i><i class="b" style="width:${(1 - p.p_red) * 100}%"></i></span>
          <span class="small muted">${P(p.p_red)}</span></td><td class="hide-s small muted sc2">${n0(p.score?.red)}–${n0(p.score?.blue)}</td>`
        : `<td class="muted">–</td><td class="hide-s"></td>`;
    }
    const ours = m.us ? ` <span class="small ${m.us === "red" ? "" : ""}" title="${tn(team)} is on ${m.us}"><span class="adot ${m.us}"></span></span>` : "";
    const wp = ls && m.us ? `<span class="res ${ls.winner === m.us ? "w" : ls.winner === "tie" ? "" : "l"}">${ls.winner === m.us ? "W" : ls.winner === "tie" ? "T" : "L"}</span>` : !m.played && m.breakdown ? `<b class="${m.breakdown.win_prob >= .5 ? "pos" : "neg"}">${P(m.breakdown.win_prob)}</b>` : (m.played && m.us ? `<span class="res ${m.result.winner === m.us ? "w" : m.result.winner === "tie" ? "" : "l"}">${m.result.winner === m.us ? "W" : m.result.winner === "tie" ? "T" : "L"}</span>` : "");
    const open = S.open.has(m.key), id = "d-" + m.key;
    return `<tr class="mrow exp ${open ? "open" : ""} ${S.nextKey === m.key ? "next" : ""}" id="r-${m.key}" data-k="${m.key}">
      <td><button type="button" class="rowbtn" aria-expanded="${open}" aria-controls="${id}" aria-label="${H(m.label || m.short)}, ${open ? "hide" : "show"} the match plan"><span class="chev">${CS.icon("chevR")}</span><b>${H(m.short)}</b></button>${ours}<span class="mt">${fmtT(m.time)}</span>${pill(m)}</td>
      <td class="tms">${teams(m.red, "r", us)}</td><td class="tms">${teams(m.blue, "b", us)}</td>${mid}<td class="num">${wp}</td></tr>
      <tr class="det" id="${id}" ${open ? "" : "hidden"}><td colspan="6"><div class="mx" data-k="${m.key}"></div></td></tr>`;
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
    const toggle = r => {
      const det = document.getElementById("d-" + r.dataset.k), open = det.hidden, btn = r.querySelector(".rowbtn");
      det.hidden = !open; r.classList.toggle("open", open); btn.setAttribute("aria-expanded", open);
      if (open) { S.open.add(r.dataset.k); const box = det.querySelector(".mx"); if (!box.dataset.f) { box.dataset.f = 1; fill(d.matches.find(x => x.key === r.dataset.k), box); } }
      else S.open.delete(r.dataset.k);
    };
    document.querySelectorAll("tr.mrow").forEach(r => r.onclick = e => { if (e.target.closest("a")) return; if (e.target.closest(".rowbtn")) return toggle(r); toggle(r); });
    document.querySelectorAll("tr.mrow.open").forEach(r => { const box = document.getElementById("d-" + r.dataset.k).querySelector(".mx"); if (!box.dataset.f) { box.dataset.f = 1; fill(d.matches.find(x => x.key === r.dataset.k), box); } });
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
    S = { ev, team: d.team, asof: d.asof || null, mp: {}, rows: Object.fromEntries(((evj && evj.matches) || []).map(m => [m.key, m])), filter: S && S.ev === ev ? S.filter : (d.matches.some(m => m.us) ? "ours" : "all"), open: new Set(S && S.ev === ev ? S.open : []), nextKey: nextOurs?.key };
    if (nextOurs && !S.open.size) S.open.add(nextOurs.key);
    S.live = live;
    // asof: built with data up to that time (a finished event), not a replay; "Latest data" until the competition is over (MATCH-12)
    const name = evj && evj.name || ev, asofTxt = d.asof ? (CS.fmtWhen ? CS.fmtWhen(d.asof, { day: true }) : new Date(d.asof * 1000).toLocaleString()) : "";
    const lab = CS.latestLabel ? CS.latestLabel(ev) : "Latest data";
    crumbs([[name, `#/${ev}`], ["Schedule"]]); $updated.textContent = d.asof ? "data as of " + asofTxt : ago(d.generated);
    $app.innerHTML = `<div class="dash sc"><div class="dtop"><div><h1>Schedule</h1><div class="sub">${H(name)} · us = ${tn(d.team)} ${CS.help(`Predictions for <b>${tn(d.team)}</b>'s matches break down where each alliance scores: tap any row to open its match plan: the field, the points per period and the video.`, "How the schedule works")}</div></div></div>
      <div class="sc-banner"><b>${H(lab)}</b>${d.asof ? ` as of ${H(asofTxt)}` : ""}, not replay time ${CS.help("Predictions for unplayed matches use everything posted so far. For a replay view, pick a time on the Dashboard.", "About this data")}</div>
      ${header(evj)}<section class="card" id="sc-body"></section></div>`;
    draw(d);
    const target = key;                              // deep link #/<ev>/schedule/<matchkey>
    if (target) setTimeout(() => document.getElementById("r-" + target)?.scrollIntoView({ block: "center" }), 50);
    const iv = setInterval(async () => { try { const n = await api(`${ev}/schedule.json`); if (n.generated !== d.generated) route(false); } catch (e) { } }, 90000);
    cleanup.push(() => clearInterval(iv));
  });
  CS.nav({ label: "Schedule", key: "schedule", href: ev => `#/${ev}/schedule`, order: 20, group: "day", icon: "schedule", tab: true });
})();
