"use strict";
// notes.js: scouting notes on the team page (#/<ev>/t/<team>) and the match page (#/<ev>/m/<match>), and proposing a
// robot identity fix on the match page. Both talk to server/routers/scouting.py through CS.app (session + CSRF):
//   notes   GET/POST events/<ev>/notes, PATCH/DELETE notes/<id>, POST notes/<id>/restore
//   fixes   GET events/<ev>/overrides, PUT/DELETE events/<ev>/overrides/<match>, POST events/<ev>/override-requests/<id>/approve|reject
// Roles (the server enforces them; these checks only hide controls): viewers read; scouts add notes, edit / delete their
// own and propose fixes (queued for approval unless Activity says "apply right away"); strategists and admins also hide /
// restore anyone's note and apply fixes directly. Notes are plain text: always esc() + white-space: pre-wrap, never HTML.
(function () {
  const NOTE_MAX = 4000;
  const H = s => esc(s);
  const css = `
  .nt-card h2 { margin: 0; }
  .nt-form { display: grid; gap: var(--sp-2); margin: var(--sp-2) 0 var(--sp-3); }
  .nt-ta { width: 100%; min-height: 76px; resize: vertical; background: var(--surface); color: var(--ink); border: 1px solid var(--line-strong);
    border-radius: var(--r-s); padding: var(--sp-2) 10px; font: inherit; font-size: 16px; line-height: 1.45; }
  .nt-ta:focus-visible, .idfx select:focus-visible { outline: 2px solid var(--accent); outline-offset: 0; border-color: var(--accent); }
  .nt-row { display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; }
  .nt-row .nt-grow { flex: 1; min-width: 0; }
  .nt-cnt { font-size: var(--fs-cap); color: var(--ink-3); font-variant-numeric: tabular-nums; }
  .nt-cnt.warn { color: var(--caution); } .nt-cnt.over { color: var(--bad); font-weight: 600; }
  .nt-row select { min-height: 32px; max-width: 100%; }
  .nt-list { list-style: none; margin: 0; padding: 0; display: grid; }
  .nt-i { padding: var(--sp-3) 0; border-top: 1px solid var(--line); min-width: 0; }
  .nt-i:first-child { border-top: 0; padding-top: var(--sp-1); }
  .nt-m { font-size: var(--fs-foot); color: var(--ink-2); display: flex; gap: 6px; flex-wrap: wrap; align-items: baseline; }
  .nt-m b { color: var(--ink); font-weight: 600; }
  .nt-t { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 2px; line-height: 1.5; }
  .nt-a { display: flex; gap: var(--sp-1); margin-top: var(--sp-1); flex-wrap: wrap; }
  .nt-a button, .idfx .acts button, .idfx-p button { padding: 2px 10px; font-size: var(--fs-foot); }
  .nt-q .nt-t { color: var(--ink-2); }
  .nt-rm .nt-t { color: var(--ink-3); text-decoration: line-through; text-decoration-color: var(--line-strong); }
  .nt-tag { font-size: var(--fs-cap); padding: 0 6px; border-radius: var(--r-tag); border: 1px dashed var(--line-strong); color: var(--ink-2); white-space: nowrap; }
  .nt-tag.warn { border-style: solid; color: var(--caution); border-color: var(--caution-line); background: var(--caution-tint); }
  .nt-chip { font-size: var(--fs-cap); }
  .nt-msg { display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; font-size: var(--fs-foot); min-height: 0; margin-bottom: var(--sp-2); }
  .nt-msg:empty { display: none; }
  .nt-msg.err { color: var(--bad); } .nt-msg.ok { color: var(--ink-2); }
  .nt-msg button { padding: 2px 10px; font-size: var(--fs-foot); }
  .nt-ro { font-size: var(--fs-foot); color: var(--ink-2); margin: var(--sp-1) 0 var(--sp-3); }
  .nt-tools { margin-left: auto; font-size: var(--fs-foot); color: var(--ink-2); display: inline-flex; gap: 6px; align-items: center; }
  .mv-fixb { margin-left: auto; padding: 2px 10px; font-size: var(--fs-foot); }
  .idfx { border-top: 1px solid var(--line); margin-top: var(--sp-3); padding-top: var(--sp-3); display: grid; gap: var(--sp-3); }
  .idfx h3 { margin: 0; font-size: var(--fs-callout); }
  .idfx-g { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--sp-3); }
  .idfx fieldset { border: 1px solid var(--line); border-radius: var(--r-s); padding: var(--sp-2) var(--sp-3) var(--sp-3); margin: 0; min-width: 0; display: grid; gap: var(--sp-2); }
  .idfx legend { font-weight: 600; font-size: var(--fs-foot); padding: 0 4px; }
  .idfx fieldset.red legend { color: var(--red); } .idfx fieldset.blue legend { color: var(--blue); }
  .idfx-r { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--sp-2); align-items: center; font-size: var(--fs-foot); }
  .idfx-r .sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: 0; }
  .idfx-r select { min-height: 32px; font-size: var(--fs-body); }
  .idfx-r.chg select { border-color: var(--accent); background: var(--accent-tint); }
  .idfx-p { font-size: var(--fs-foot); display: grid; gap: var(--sp-2); }
  .idfx-p > div { display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; }
  .idfx .acts { display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; }
  .idfx .acts a { font-size: var(--fs-foot); margin-left: auto; }
  @media (max-width: 760px) { .idfx-g { grid-template-columns: 1fr; } .nt-tools { margin-left: 0; } }`;
  function style() {
    if (document.getElementById("nt-css")) return;
    const s = document.createElement("style"); s.id = "nt-css"; s.textContent = css; document.head.appendChild(s);
  }
  const on = () => CS.platform && !!CS.user;
  const locked = () => !!(CS.session && CS.session.lockdown) && !CS.can("admin");
  const me = () => (CS.user && CS.user.id) || null;
  /** API error -> one friendly sentence (lockdown 423, write budget 429, the server's text otherwise) */
  function errMsg(e) {
    if (e && e.status === 423) return "Read-only right now: an admin turned on lockdown, so nobody but admins can change anything.";
    if (e && e.status === 429) {
      if (/waiting for approval/.test(e.message || "")) return e.message;
      const w = (e.message || "").match(/in (\d+)s/);
      return `Slow down: too many changes in a short time. Try again in ${w ? w[1] + " s" : "a minute"}.`;
    }
    if (e && e.status === 403) return "Your role can't do that.";
    if (e && (!e.status || e.status >= 502 || (e.data && e.data.offline))) return "Not connected to the server. Try again once the connection is back.";
    const m = (e && e.message) || "Something went wrong.";
    return m.charAt(0).toUpperCase() + m.slice(1);
  }
  CS.writeErr = errMsg;
  const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  function when(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000), s = Date.now() / 1000 - ts;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    const hm = d.toTimeString().slice(0, 5);
    if (new Date().toDateString() === d.toDateString()) return hm;
    if (s < 6 * 86400) return `${DAY[d.getDay()]} ${hm}`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + hm;
  }
  const full = ts => ts ? new Date(ts * 1000).toLocaleString() : "";
  const name = u => (u && u.display_name) || "someone";
  const counter = (n) => `${n} / ${NOTE_MAX}`;
  // one phrasing per mode for "your change waits in the outbox" (sync.js); the inline message replaces sync.js's toast
  const QMSG = () => (CS.sync && CS.sync.queuedMsg) ? CS.sync.queuedMsg() : "Saved on this device. It's sent when the connection to the server is back.";
  const QUIET = { quiet: true };
  // processing paused on the server (app.js CS.processing): the worker won't re-label anything until it's back on
  const paused = () => !!(CS.processing && CS.processing.paused);
  const relabel = () => paused() ? "Processing is paused on the server, so this match is re-labelled only once an admin turns processing back on."
    : "The worker re-labels this match on its next pass (usually within a few minutes).";
  const cntCls = n => n > NOTE_MAX ? "nt-cnt over" : n > NOTE_MAX - 200 ? "nt-cnt warn" : "nt-cnt";

  // ================================================================ notes
  /** CS.notes.mount(el, ev, {team} | {match, teams: [teamkey...]})  el: an empty <section> on the page */
  function mount(el, ev, opt) {
    if (!el) return;
    if (!on()) { el.remove(); return; }                       // static site / signed out: no notes
    style();
    el.classList.add("card", "nt-card");
    const team = opt.team || null, match = opt.match || null, teams = opt.teams || [];
    const S = { list: [], showRemoved: false, editing: null, draft: "", about: "", busy: false };
    const hid = "ntH" + Math.random().toString(36).slice(2, 7);
    el.setAttribute("aria-labelledby", hid);
    const q = () => `events/${encodeURIComponent(ev)}/notes?` + (team ? `team=${encodeURIComponent(team)}` : `match=${encodeURIComponent(match)}`)
      + (S.showRemoved ? "&include_removed=true" : "");
    const help = `<span class="hl">Plain-text notes for everyone on the team: what you saw that the numbers don't show.</span>`
      + `<span class="hl">${team ? "Notes about this team, including the ones written on its match pages." : "Notes about this match; pick a team to make a note also show on that team's page."}</span>`
      + `<span class="hl">Scouts and up can write; you can edit or delete your own. Strategists and admins can hide anyone's note and bring hidden or deleted notes back. Every change is in the Activity log.</span>`;
    // notes written offline wait in the outbox (sync.js): shown here until they're sent
    const queued = () => CS.sync ? CS.sync.pending(it => it.method === "POST" && it.path === `events/${encodeURIComponent(ev)}/notes` && !!it.body
      && (team ? it.body.team === team : it.body.match === match)) : [];
    // ... and so do edits / deletes of existing notes: the newest one per note is drawn over the saved copy (CLIENT-4)
    const queuedEdits = () => { const m = new Map();
      for (const it of CS.sync ? CS.sync.pending(x => /^notes\/\d+$/.test(x.path || "") && (x.method === "PATCH" || x.method === "DELETE")) : []) m.set(+it.path.split("/")[1], it);
      return m; };
    const qItem = it => `<li class="nt-i nt-q"><div class="nt-m"><b>${H(name(CS.user))}</b><span class="nt-tag warn" title="${H(QMSG())}">waiting to send</span>
        <time datetime="${H(new Date(it.at).toISOString())}">${H(when(it.at / 1000))}</time></div><div class="nt-t">${H(it.body.text)}</div></li>`;
    // (not while typing: a redraw would take the text box away)
    const typing = () => { const a = document.activeElement; return !!a && el.contains(a) && /^(TEXTAREA|INPUT|SELECT)$/.test(a.tagName); };
    const onOutbox = () => { if (!el.isConnected) document.removeEventListener("cs:outbox", onOutbox); else if (!S.editing && !typing()) load(); };
    document.addEventListener("cs:outbox", onOutbox);
    async function load() {
      try { S.list = await CS.app("GET", q()) || []; S.err = null; }
      catch (e) { S.list = []; S.err = e.status === 404 ? "Notes aren't available on this server." : !e.status || (e.data && e.data.offline) || e.status >= 502
        ? "Not connected: everyone's notes show here once the server can be reached." : errMsg(e); }
      draw();
    }
    function msg(kind, text, undo) {
      const m = el.querySelector(".nt-msg"); if (!m) return;
      m.className = "nt-msg " + kind; m.innerHTML = text ? `<span>${H(text)}</span>${undo ? `<button type="button" data-undo="${undo}">Undo</button>` : ""}` : "";
      const b = m.querySelector("[data-undo]");
      if (b) b.onclick = () => act("restore", +b.dataset.undo, "Restored.");
    }
    function item(n, qe) {
      const mine = n.author && n.author.id === me(), rm = n.removed;
      const chips = [];
      if (qe) { n = Object.assign({}, n, qe.method === "PATCH" && qe.body && typeof qe.body.text === "string" ? { text: qe.body.text } : {});
        chips.push(`<span class="nt-tag warn" title="${H(QMSG())}">${qe.method === "DELETE" ? "delete" : "edit"} waiting to send</span>`); }
      if (team && n.match) chips.push(`<a class="nt-chip" href="#/${H(ev)}/m/${H(n.match)}">${H(shortKey(n.match))}</a>`);
      if (match && n.team) chips.push(`<a class="nt-chip team" href="#/${H(ev)}/t/${H(n.team)}">${H(num(n.team))}</a>`);
      if (n.updated_at && n.updated_at - n.created_at > 5 && !rm) chips.push(`<span class="nt-tag" title="edited ${H(full(n.updated_at))}${n.edited_by ? " by " + H(name(n.edited_by)) : ""}">edited${n.edited_by ? " by " + H(name(n.edited_by)) : ""}</span>`);
      if (rm) chips.push(`<span class="nt-tag warn" title="${H(full(n.removed_at))}">${rm === "hidden" ? `hidden by ${H(name(n.removed_by))}` : rm === "deleted" ? "deleted by its author" : "removed (undone)"}</span>`);
      const acts = [];
      if (S.editing === n.id) {
        return `<li class="nt-i" data-id="${n.id}"><div class="nt-m"><b>${H(name(n.author))}</b><span>editing</span></div>
          <form class="nt-form" data-edit="${n.id}"><label class="vh" for="nted${n.id}">Edit note</label>
          <textarea class="nt-ta" id="nted${n.id}" maxlength="${NOTE_MAX}" required>${H(n.text)}</textarea>
          <div class="nt-row"><span class="${cntCls(n.text.length)} nt-grow" aria-live="polite">${counter(n.text.length)}</span>
          <button type="button" data-cancel>Cancel</button><button type="submit" class="primary">Save</button></div></form></li>`;
      }
      if (!rm && !locked() && !qe) {
        if (mine) acts.push(`<button type="button" data-act="edit">Edit</button>`, `<button type="button" data-act="delete" class="danger">Delete</button>`);
        else if (CS.can("strategist")) acts.push(`<button type="button" data-act="hide">Hide</button>`);
      }
      if (rm && !locked() && CS.can("strategist")) acts.push(`<button type="button" data-act="restore">Restore</button>`);
      return `<li class="nt-i${rm || (qe && qe.method === "DELETE") ? " nt-rm" : ""}${qe ? " nt-q" : ""}" data-id="${n.id}"><div class="nt-m"><b>${H(name(n.author))}</b>
          <time datetime="${H(new Date(n.created_at * 1000).toISOString())}" title="${H(full(n.created_at))}">${H(when(n.created_at))}</time>${chips.join("")}</div>
        <div class="nt-t">${H(n.text)}</div>${acts.length ? `<div class="nt-a">${acts.join("")}</div>` : ""}</li>`;
    }
    function draw() {
      const live = S.list.filter(n => !n.removed), canWrite = CS.can("scout"), qd = queued(), qe = queuedEdits();
      // offline: say so, and say how old the list is, instead of "none yet" over a list that may be missing notes (CLIENT-12)
      const ri = CS.sync && CS.sync.readInfo ? CS.sync.readInfo(q()) : null, saved = !S.err && ri && ri.saved;
      const head = S.err ? (qd.length ? `${qd.length} waiting to send · not connected` : "not connected")
        : [live.length || (qd.length ? "" : "none yet"), qd.length ? `${qd.length} waiting to send` : ""].filter(Boolean).join(" · ");
      const keepFocus = document.activeElement && el.contains(document.activeElement) ? document.activeElement.id : null;
      const about = match && teams.length ? `<label class="nt-row"><span class="muted small">About</span><select id="ntAbout"><option value="">the whole match</option>${teams.map(t => `<option value="${H(t)}"${S.about === t ? " selected" : ""}>${H(num(t))}</option>`).join("")}</select></label>` : "";
      el.innerHTML = `<div class="mv-sh"><h2 id="${hid}">Notes</h2><span class="muted small">${H(String(head))}</span>${CS.help(help, "About notes")}
          ${CS.can("strategist") ? `<label class="nt-tools"><input type="checkbox" id="ntRm"${S.showRemoved ? " checked" : ""}> show hidden and deleted</label>` : ""}</div>
        ${!canWrite ? `<div class="nt-ro">Read-only: scouts, strategists and admins can write notes.</div>`
          : locked() ? `<div class="nt-ro">Read-only right now: an admin turned on lockdown.</div>`
          : `<form class="nt-form" id="ntNew"><label class="vh" for="ntText">New note</label>
            <textarea class="nt-ta" id="ntText" maxlength="${NOTE_MAX}" placeholder="${team ? "What did you see this team do? Drive, intake, defense, breakdowns…" : "What happened in this match that the numbers don't show?"}">${H(S.draft)}</textarea>
            <div class="nt-row">${about}<span class="${cntCls(S.draft.length)} nt-grow" id="ntCnt" aria-live="polite">${counter(S.draft.length)}</span>
            <button type="submit" class="primary"${S.draft.trim() ? "" : " disabled"}>Add note</button></div></form>`}
        <div class="nt-msg" role="status" aria-live="polite"></div>
        ${S.err ? `<div class="nt-ro">${H(S.err)}</div>` : ""}${saved ? `<div class="nt-ro">Saved copy from ${H(when(ri.at / 1000))}: notes written since then show once the server can be reached.</div>` : ""}
        ${qd.length || (!S.err && S.list.length) ? `<ul class="nt-list">${qd.map(qItem).join("")}${S.err ? "" : S.list.map(n => item(n, qe.get(n.id))).join("")}</ul>` : S.err ? "" : `<div class="muted small">No notes${saved ? " in the saved copy" : " yet"}.</div>`}`;
      wire();
      if (keepFocus) { const f = document.getElementById(keepFocus); if (f) f.focus(); }
    }
    function wire() {
      const f = el.querySelector("#ntNew");
      if (f) {
        const ta = f.querySelector("textarea"), cnt = f.querySelector("#ntCnt"), b = f.querySelector("button[type=submit]");
        ta.oninput = () => { S.draft = ta.value; cnt.textContent = counter(ta.value.length); cnt.className = cntCls(ta.value.length) + " nt-grow"; b.disabled = !ta.value.trim() || ta.value.length > NOTE_MAX; };
        const ab = f.querySelector("#ntAbout"); if (ab) ab.onchange = () => { S.about = ab.value; };
        f.onsubmit = async e => {
          e.preventDefault(); if (S.busy || !ta.value.trim()) return;
          S.busy = true; b.disabled = true;
          const body = { text: ta.value };
          if (team) body.team = team; else { body.match = match; if (S.about) body.team = S.about; }
          try { const r = await CS.app("POST", `events/${encodeURIComponent(ev)}/notes`, body, QUIET); S.draft = "";
            if (r && r.queued) { draw(); msg("ok", QMSG()); } else { await load(); msg("ok", "Note added."); } }
          catch (x) { msg("err", errMsg(x)); b.disabled = false; }
          finally { S.busy = false; }
        };
      }
      const rm = el.querySelector("#ntRm"); if (rm) rm.onchange = () => { S.showRemoved = rm.checked; load(); };
      el.querySelectorAll(".nt-i [data-act]").forEach(b => b.onclick = () => {
        const id = +b.closest(".nt-i").dataset.id, a = b.dataset.act;
        if (a === "edit") { S.editing = id; draw(); const t = el.querySelector(`#nted${id}`); if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); } return; }
        if (a === "delete" && !confirm("Delete this note? You can undo it right after.")) return;
        act(a, id);
      });
      el.querySelectorAll("form[data-edit]").forEach(fe => {
        const id = +fe.dataset.edit, ta = fe.querySelector("textarea"), cnt = fe.querySelector(".nt-cnt");
        ta.oninput = () => { cnt.textContent = counter(ta.value.length); cnt.className = cntCls(ta.value.length) + " nt-grow"; };
        fe.querySelector("[data-cancel]").onclick = () => { S.editing = null; draw(); };
        fe.onsubmit = async e => {
          e.preventDefault();
          try { const r = await CS.app("PATCH", `notes/${id}`, { text: ta.value }, QUIET); S.editing = null;
            if (r && r.queued) { draw(); msg("ok", "Edit " + QMSG().charAt(0).toLowerCase() + QMSG().slice(1)); } else { await load(); msg("ok", "Saved."); } }
          catch (x) { msg("err", errMsg(x)); }
        };
      });
    }
    async function act(a, id, okText) {
      try {
        if (a === "delete" || a === "hide") {
          const r = await CS.app("DELETE", `notes/${id}`, undefined, QUIET);
          if (r && r.queued) { draw(); msg("ok", QMSG()); return; }
          await load(); msg("ok", a === "hide" ? "Note hidden: only strategists and admins can still see it." : "Note deleted.", id);
        } else if (a === "restore") {
          const r = await CS.app("POST", `notes/${id}/restore`, undefined, QUIET); if (r && r.queued) { msg("ok", QMSG()); return; }
          await load(); msg("ok", okText || "Note restored.");
        }
      } catch (x) { msg("err", errMsg(x)); }
    }
    draw(); load();
  }

  // ================================================================ identity fixes (match page)
  /** CS.idFix.mount(el, ev, match, tracks, teams)  tracks: [{idx, alliance, team, label, color, tc}] as drawn on the page;
   *  teams: {red: [teamkey x3], blue: [...]} (TBA). Renders a "Wrong team?" form; returns {open()} or null. */
  function mountFix(el, ev, match, tracks, teams) {
    if (!el || !on() || !CS.can("scout")) return null;
    style();
    const base = `events/${encodeURIComponent(ev)}`;
    const S = { open: false, ov: null, sel: {}, msg: null, busy: false };
    tracks.forEach(t => { S.sel[t.idx] = t.team || ""; });
    const orig = Object.fromEntries(tracks.map(t => [t.idx, t.team || ""]));
    // a fix names robots by track idx; show them the way the page labels them ("robot shown as 9442 → 1318")
    const shown = Object.fromEntries(tracks.map(t => [String(t.idx), t.label]));
    const sideTxt = d => ["red", "blue"].filter(a => d && d[a] && Object.keys(d[a]).length)
      .map(a => `${a}: ` + Object.entries(d[a]).map(([i, t]) => `${shown[i] != null ? `robot shown as ${H(shown[i])}` : `robot #${H(i)}`} → ${H(num(t))}`).join(", ")).join("; ") || "no fixes";
    async function load() {
      try { S.ov = await CS.app("GET", `${base}/overrides`); S.loadErr = null; }
      catch (e) { S.ov = null; S.loadErr = e.status === 404 ? null : !e.status || e.status >= 502 || (e.data && e.data.offline)
        ? "Not connected: the fixes already on the server show here once it can be reached." : errMsg(e); }
      draw();
    }
    // fixes for this match still in the outbox (saved offline): listed as "waiting to send" (CLIENT-13)
    const queuedFix = () => CS.sync ? CS.sync.pending(it => it.path === `${base}/overrides/${encodeURIComponent(match)}`) : [];
    const onQ = () => { if (!el.isConnected) { document.removeEventListener("cs:outbox", onQ); removeEventListener("cs:processing", onQ); }
      else if (S.open && !(el.contains(document.activeElement) && document.activeElement.tagName === "SELECT")) {
        if (!queuedFix().length && S.msg && S.msg[2] === "queued") { S.msg = ["ok", "Sent to the server."]; load(); }      // (it went out)
        else if (!queuedFix().length && S.loadErr) load(); else draw(); } };
    document.addEventListener("cs:outbox", onQ); addEventListener("cs:processing", onQ);
    const direct = () => CS.can("strategist") || (S.ov && S.ov.mode === "direct");
    const changed = () => tracks.filter(t => S.sel[t.idx] && S.sel[t.idx] !== orig[t.idx]);
    function draw() {
      if (!S.open) { el.innerHTML = ""; el.hidden = true; return; }
      el.hidden = false;
      const cur = (S.ov && S.ov.overrides && S.ov.overrides[match]) || null;
      const pend = ((S.ov && S.ov.pending) || []).filter(p => p.match === match);
      const lk = locked();
      const row = t => {
        const opts = (teams[t.alliance] && teams[t.alliance].length ? teams[t.alliance] : tracks.filter(x => x.alliance === t.alliance).map(x => x.team).filter(Boolean));
        return `<label class="idfx-r${S.sel[t.idx] !== orig[t.idx] ? " chg" : ""}"><span><span class="sw" style="background:${H(t.color)}"></span>Robot shown as <b>${H(t.label)}</b>${t.tc != null ? ` <span class="muted">${Math.round(t.tc * 100)}%</span>` : ""}</span>
          <select data-idx="${H(t.idx)}" aria-label="Robot shown as ${H(t.label)} is really"${lk ? " disabled" : ""}>${t.team ? "" : `<option value="">unknown</option>`}${opts.map(k => `<option value="${H(k)}"${S.sel[t.idx] === k ? " selected" : ""}>${H(num(k))}</option>`).join("")}</select></label>`;
      };
      const n = changed().length, qf = queuedFix();
      // an applied fix the page doesn't show yet: the worker hasn't re-labelled the match (MATCH-13)
      const lag = cur ? ["red", "blue"].flatMap(a => Object.entries(cur[a] || {}).map(([i, k]) => ({ t: tracks.find(x => String(x.idx) === i), k })))
        .filter(x => x.t && x.k && x.t.team !== x.k) : [];
      el.innerHTML = `<div class="idfx" role="group" aria-labelledby="idfxH"><h3 id="idfxH">Fix a robot's team ${CS.help(`<span class="hl">If a robot is labelled with the wrong team, pick the team it really is. Choosing a team that another robot of the alliance has swaps the two.</span><span class="hl">${direct() ? `Your fix is applied right away. ${paused() ? "Processing is paused on the server, so the worker re-labels this match (and re-checks the rest of the event) once an admin turns it back on." : "The worker re-labels this match (and re-checks the rest of the event) on its next pass, usually within a few minutes."}` : `A strategist or admin approves scouts' fixes in Identity fixes; then the worker re-labels this match on its next pass${paused() ? " (processing is paused on the server right now)" : ""}.`}</span><span class="hl">Use the video: the team number is on the bumpers. Fixed robots show a "manual" tag in Robots.</span>`, "How identity fixes work")}</h3>
        ${lk ? `<div class="nt-ro">Read-only right now: an admin turned on lockdown.</div>` : ""}
        <div class="idfx-g">${["red", "blue"].map(a => `<fieldset class="${a}"><legend>${a === "red" ? "Red" : "Blue"}</legend>${tracks.filter(t => t.alliance === a).map(row).join("")}</fieldset>`).join("")}</div>
        ${cur || pend.length || qf.length ? `<div class="idfx-p">
          ${qf.map(it => `<div><span><b>Waiting to send:</b> ${it.method === "DELETE" ? "clear this match's fixes" : sideTxt(it.body)} <span class="nt-tag warn" title="${H(QMSG())}">saved here</span></span></div>`).join("")}
          ${cur ? `<div><span><b>Applied for this match:</b> ${sideTxt(cur)}${lag.length ? ` <span class="nt-tag warn" title="${H(relabel())}">not re-labelled yet</span>` : ""}</span>${lk ? "" : `<button type="button" data-clear>${direct() ? "Clear fixes" : "Ask to clear"}</button>`}</div>
            ${lag.length ? `<div class="muted">This page still shows the old labels (${lag.map(x => `${H(x.t.label)} will show as ${H(num(x.k))}`).join(", ")}). ${H(relabel())}</div>` : ""}` : ""}
          ${pend.map(p => `<div data-rid="${H(p.id)}"><span><b>Waiting for review:</b> ${sideTxt(p.data)} · ${H(name(p.by))}, <span title="${H(full(p.at))}">${H(when(p.at))}</span>${p.stale ? ' <span class="nt-tag warn" title="the applied fixes changed since this was requested">changed since</span>' : ""}</span>
            ${lk ? "" : CS.can("strategist") ? `<button type="button" class="primary" data-dec="approve">Approve</button><button type="button" data-dec="reject">Reject</button>`
              : p.by && p.by.id === me() ? `<button type="button" data-dec="reject">Withdraw</button>` : ""}</div>`).join("")}</div>` : ""}
        <div class="acts"><button type="button" class="primary" data-send${n && !lk && !S.busy ? "" : " disabled"}>${S.busy ? "Sending…" : direct() ? "Apply fix" : "Send for review"}${n && !S.busy ? ` (${n})` : ""}</button>
          <button type="button" data-close>Close</button>${CS.can("strategist") ? `<a href="#/${H(ev)}/fixes">Identity fixes queue</a>` : ""}</div>
        <div class="nt-msg${S.msg ? " " + S.msg[0] : ""}" role="status" aria-live="polite">${S.msg ? `<span>${S.msg[1]}</span>` : ""}</div>
        ${S.loadErr ? `<div class="nt-ro">${H(S.loadErr)}</div>` : ""}</div>`;
      wire();
    }
    function wire() {
      el.querySelectorAll("select[data-idx]").forEach(s => s.onchange = () => {
        const idx = s.dataset.idx, t = tracks.find(x => String(x.idx) === idx), was = S.sel[idx], nv = s.value;
        // keep each team on one robot: the robot that had the chosen team takes this one's old team
        const other = tracks.find(x => x.alliance === t.alliance && String(x.idx) !== idx && S.sel[x.idx] === nv);
        if (other) S.sel[other.idx] = was;
        S.sel[idx] = nv; S.msg = null; draw();
        const f = el.querySelector(`select[data-idx="${CSS.escape(idx)}"]`); if (f) f.focus();
      });
      const send = el.querySelector("[data-send]"); if (send) send.onclick = () => submit();
      const clr = el.querySelector("[data-clear]"); if (clr) clr.onclick = () => submit(true);
      el.querySelector("[data-close]").onclick = () => { S.open = false; draw(); if (S.opener) S.opener.focus(); };
      el.querySelectorAll("[data-dec]").forEach(b => b.onclick = async () => {
        const rid = b.closest("[data-rid]").dataset.rid, d = b.dataset.dec;
        try {
          const r = await CS.app("POST", `${base}/override-requests/${encodeURIComponent(rid)}/${d}`, undefined, QUIET);
          S.msg = r && r.queued ? ["ok", H(QMSG())] : ["ok", d === "approve" ? `Approved. ${H(relabel())}` : CS.can("strategist") ? "Rejected." : "Withdrawn."];
        } catch (x) { S.msg = ["err", H(errMsg(x))]; }
        load();
      });
    }
    async function submit(clear) {
      if (S.busy) return;
      if (clear && !confirm(direct() ? "Remove every identity fix for this match?" : "Ask a strategist to remove every identity fix for this match?")) return;
      S.busy = true; draw();          // (no second tap while it's being sent, MATCH-25)
      try {
        let r;
        if (clear) r = await CS.app("DELETE", `${base}/overrides/${encodeURIComponent(match)}`, undefined, QUIET);
        else {
          const cur = (S.ov && S.ov.overrides && S.ov.overrides[match]) || {}, entry = { red: { ...(cur.red || {}) }, blue: { ...(cur.blue || {}) } };
          changed().forEach(t => { entry[t.alliance][String(t.idx)] = S.sel[t.idx]; });
          r = await CS.app("PUT", `${base}/overrides/${encodeURIComponent(match)}`, entry, QUIET);
        }
        const queued = r && (r.status === "pending" || r.queued);
        S.msg = r && r.queued ? ["ok", `${H(QMSG())} Until then this match keeps its current labels.`, "queued"]
          : queued ? ["ok", `Sent for review: a strategist or admin approves it in Identity fixes. Until then this match keeps its current labels.`]
          : ["ok", clear ? `Fixes cleared. ${H(relabel())}` : `Fix applied. ${H(relabel())} Reload this page after that to see the new labels.`];
        // applied: the new teams are the baseline. Sent for review / waiting to send: back to the labels the page shows
        // (the request is listed above), so the button is off until something changes and a second tap can't file it
        // twice (MATCH-25, CLIENT-13)
        if (!queued) tracks.forEach(t => { orig[t.idx] = S.sel[t.idx]; });
        else tracks.forEach(t => { S.sel[t.idx] = orig[t.idx]; });
      } catch (x) { S.msg = ["err", H(errMsg(x))]; }
      S.busy = false;
      await load();
    }
    return {
      open(opener) { S.open = true; S.opener = opener || null; S.msg = null; if (S.ov) draw(); else load(); setTimeout(() => { el.scrollIntoView({ block: "nearest" }); const s = el.querySelector("select"); if (s) s.focus(); }, 0); },
      toggle(opener) { if (S.open) { S.open = false; draw(); } else this.open(opener); },
    };
  }

  CS.notes = { mount };
  CS.idFix = { mount: mountFix };
})();
