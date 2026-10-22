"use strict";
// admin.js: platform plugin -- accounts + roles, team settings, TBA key, worker options, followed events.
// Route #/admin[/users|settings|events|server|updates] (admin only). Uses CS.app / CS.form / CS.eventPicker from setup.js.
(function () {
  CS.nav({ label: "Admin", key: "admin", href: () => "#/admin", order: 95, global: true, group: "manage", icon: "admin", roles: ["admin"] });

  const when = ts => ts ? CS.fmtWhen(ts) : "never";
  const TABS = [["users", "Teammates"], ["settings", "Settings"], ["events", "Competitions"], ["server", "Server"], ["updates", "Updates"]];
  const css = document.createElement("style");
  css.textContent = `
  .uacts { display: flex; flex-wrap: wrap; gap: 6px; } .uacts button { white-space: nowrap; }
  .rowerr:empty { display: none; } .rowerr { margin-top: 4px; white-space: normal; }
  .admtabs { flex-wrap: nowrap; overflow-x: auto; }
  .evacts .cmp-btn { min-height: 36px; }
  .wk { display: flex; gap: 10px; align-items: flex-start; margin: 0 0 var(--sp-3); padding: var(--sp-2) var(--sp-3); border-radius: var(--r-s); background: var(--surface-2); font-size: var(--fs-foot); }
  .wk.paused { background: var(--caution-tint); border: 1px solid var(--caution-line); } .wk b { color: var(--ink); }
  .envgrp { border: 0; padding: 0; margin: 0 0 var(--sp-4); display: grid; gap: var(--sp-2); }
  .envgrp legend { font-weight: 600; color: var(--ink); margin-bottom: 4px; }
  .envrow { display: grid; grid-template-columns: minmax(0, 1fr) minmax(9rem, 14rem); gap: 4px 12px; align-items: center; }
  .envrow .k { font-family: var(--mono); font-size: var(--fs-foot); color: var(--ink-3); }
  .envrow.dirty { background: var(--caution-tint); border-radius: var(--r-s); margin: 0 -6px; padding: 2px 6px; }
  .envrow input, .envrow select { width: 100%; }
  @media (max-width: 560px) { .envrow { grid-template-columns: 1fr; } }
  .wk .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ink-3); margin-top: 5px; flex: none; } .wk.ok .dot { background: var(--good); } .wk.paused .dot { background: var(--caution); }
  @media (pointer: coarse) { .uacts button, .evacts .cmp-btn, .evacts button { min-height: 44px; } }
  /* connection key sheet */
  .pill.ck-pill { color: var(--ink); border-color: var(--accent-line); background: var(--accent-tint); } .pill.ck-pill .ico { width: 11px; height: 11px; vertical-align: -1px; margin-right: 3px; color: var(--accent); }
  .ck { display: grid; gap: var(--sp-4); } .ck > .cserr:empty { display: none; } .ck > p, .ck-sec > p { margin: 0; line-height: 1.55; text-wrap: pretty; }
  .ck-who { display: inline-flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
  .ck-note { display: flex; gap: 10px; align-items: flex-start; padding: var(--sp-3); border-radius: var(--r-p); background: var(--surface-2); font-size: var(--fs-foot); }
  .ck-note > .ico { width: 16px; height: 16px; flex: none; margin-top: 1px; color: var(--ink-2); } .ck-note > span { flex: 1; min-width: 0; } .ck-note b { color: var(--ink); }
  .ck-note.off { background: var(--caution-tint); border: 1px solid var(--caution-line); } .ck-note.off > .ico { color: var(--caution); }
  .ck-note.live { align-items: center; flex-wrap: wrap; } .ck-note.live > .ico { color: var(--accent); }
  .ck-note.done > .ico { color: var(--good); }
  .ck-form { display: grid; gap: var(--sp-3); }
  .ck-f { display: grid; gap: 6px; font-size: var(--fs-foot); font-weight: 500; color: var(--ink-2); border: 0; padding: 0; margin: 0; min-width: 0; }
  .ck-f legend { padding: 0; margin-bottom: 6px; }
  .ck-f .csin { width: 100%; min-height: 40px; font-size: var(--fs-body); }
  .ck-f .hint { font-weight: 400; font-size: var(--fs-cap); color: var(--ink-2); } .ck-f .hint.warn { color: var(--caution); font-weight: 500; } .ck-f .hint.bad { color: var(--bad); }
  .ck-segs { display: inline-flex; width: max-content; max-width: 100%; border: 1px solid var(--line-strong); border-radius: var(--r-s); overflow: hidden; background: var(--surface); }
  .ck-segs label { position: relative; display: block; }
  .ck-segs input { position: absolute; inset: 0; opacity: 0; margin: 0; cursor: pointer; }
  .ck-segs span { display: block; padding: 8px 16px; font-size: var(--fs-body); font-weight: 500; color: var(--ink); border-left: 1px solid var(--line); white-space: nowrap; }
  .ck-segs label:first-child span { border-left: 0; }
  .ck-segs label:hover span { background: var(--surface-2); }
  .ck-segs input:checked + span { background: var(--accent-fill); color: var(--on-accent); font-weight: 600; }
  .ck-segs input:focus-visible + span { outline: 2px solid var(--focus, var(--accent)); outline-offset: -3px; }
  .ck-go { display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; }
  .ck-go button, .ck-acts button { min-height: 40px; font-size: var(--fs-body); }
  .ck-box { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--sp-3); align-items: center; padding: var(--sp-4); border-radius: var(--r-m);
    background: var(--accent-tint); border: 1px solid var(--accent-line); }
  .ck-str { font-family: var(--mono); line-height: 1.45; overflow-wrap: anywhere; word-break: break-all; user-select: all; -webkit-user-select: all; cursor: text; }
  .ck-str .ck-a { display: block; font-size: var(--fs-foot); color: var(--ink-2); }
  .ck-str .ck-k { display: block; font-size: clamp(19px, 3.2vw, 26px); font-weight: 700; letter-spacing: .04em; color: var(--ink); font-variant-numeric: tabular-nums; }
  .ck-str ::selection, .ck-str::selection { background: var(--accent-fill); color: var(--on-accent); }
  .ck-box button { min-height: 40px; font-size: var(--fs-body); } .ck-box button .ico, .ck-cmd button .ico { width: 15px; height: 15px; vertical-align: -3px; margin-right: 6px; }
  .ck-meta { font-size: var(--fs-foot); color: var(--ink-2); } .ck-meta b { color: var(--ink); } .ck-meta b.ck-adm { color: var(--caution); }
  .cksheet .cmp-h h2 { display: flex; align-items: center; gap: 8px; } .cksheet .cmp-h h2 .ico { width: 20px; height: 20px; color: var(--accent); flex: none; }
  .ck-sec { display: grid; gap: var(--sp-2); } .ck-sec h3 { margin: 0; font-size: var(--fs-body); }
  .ck-steps { margin: 0; padding: 0; list-style: none; display: grid; gap: var(--sp-3); counter-reset: ck; }
  .ck-steps > li { counter-increment: ck; display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 4px var(--sp-3); align-items: start; font-size: var(--fs-foot); color: var(--ink-2); line-height: 1.5; }
  .ck-steps > li::before { content: counter(ck); width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 700; background: var(--surface-3); color: var(--ink); }
  .ck-steps > li > div { display: grid; gap: var(--sp-2); min-width: 0; } .ck-steps b { color: var(--ink); font-weight: 600; }
  .ck-cmd { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px var(--sp-2); align-items: center; padding: var(--sp-2) var(--sp-2) var(--sp-2) var(--sp-3); border-radius: var(--r-p); background: var(--surface-2); }
  .ck-cmd .ck-os { grid-column: 1 / -1; font-size: var(--fs-cap); font-weight: 600; color: var(--ink-2); }
  .ck-cmd code { font-family: var(--mono); font-size: 12.5px; color: var(--ink); overflow-wrap: anywhere; word-break: break-all; line-height: 1.5; }
  .ck-cmd button { min-height: 34px; align-self: end; }
  .ck-ph { color: var(--caution); font-style: italic; text-decoration: underline dashed; text-underline-offset: 3px; }
  .ck-acts { display: flex; flex-wrap: wrap; gap: var(--sp-2); justify-content: flex-end; align-items: center; padding-top: var(--sp-3); border-top: 1px solid var(--line); }
  .ck-acts .grow { flex: 1; }
  @media (max-width: 520px) { .ck-box { grid-template-columns: minmax(0, 1fr); } .ck-box button { justify-self: start; } .ck-acts button { flex: 1 1 auto; } .ck-acts .grow { display: none; } }
  @media (pointer: coarse) { .ck-segs span { padding: 11px 16px; } .ck-cmd button { min-height: 44px; } }`;
  document.head.appendChild(css);

  CS.globalRoute("admin", async tab => {
    crumbs([["Admin", "#/admin"], [(TABS.find(t => t[0] === tab) || TABS[0])[1]]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">No Cyber-Scout server here (static site): nothing to administer.</div>`; return; }
    if (!CS.can("admin")) { $app.innerHTML = `<div class="empty">Admins only.</div>`; return; }
    tab = TABS.some(t => t[0] === tab) ? tab : "users";
    $app.innerHTML = `<h1>Admin</h1><div class="sub"><a href="#/setup">Re-run the setup wizard</a></div>
      <div class="tabs admtabs" role="navigation" aria-label="Admin sections">${TABS.map(([k, t]) => `<a class="tab ${k === tab ? "on" : ""}" href="#/admin/${k}"${k === tab ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</div><div id="adm"></div>`;
    const el = document.getElementById("adm");
    const on = $app.querySelector(".admtabs .tab.on"), row = on && on.parentElement;      // phones: the tab row scrolls; show the current one
    if (row && row.scrollWidth > row.clientWidth) row.scrollLeft = on.offsetLeft - row.offsetLeft - 16;
    await ({ users, settings, events, server, updates })[tab](el);
  });

  // ---------------------------------------------------------------- users
  // The table is full width so Reset password / Disable / Delete show on a desktop; on a narrow screen they fold into the
  // row's "›" detail (app.js moves the real buttons there, so they keep working). One delegated handler per table.
  const MIN_ADMIN = 10, MIN_MEMBER = 4;   // server: PASSWORD_MIN / MEMBER_PASSWORD_MIN

  // ---------------------------------------------------------------- connection keys
  // A one-time key that connects a laptop's Cyber-Scout Client to this server and signs it in as one teammate, no password
  // (POST / DELETE users/<id>/connect-key; it works once, until it expires, and a new one replaces the old). The admin
  // hands over the connection string "cyberscout:<server>#<KEY>"; the Client's Connect page (sync.js parseConn) and the
  // Client installer (--key / CS_KEY) take it. The key is shown only here, right after it's made: it's never stored in
  // this browser, and the server keeps only its expiry (users list: connect_key_expires).
  const KEY_HOURS = [[24, "1 day"], [168, "7 days"], [720, "30 days"]];
  const secs = v => v == null || v === "" ? null : typeof v === "number" ? (v > 1e11 ? v / 1000 : v) : (Date.parse(v) / 1000 || null);
  const keyLive = u => { const t = secs(u.connect_key_expires); return t && t > Date.now() / 1000 ? t : null; };
  const keyPill = u => { const t = keyLive(u); return t ? ` <span class="pill ck-pill" title="A connection key for ${esc(u.username)} works once, until ${esc(CS.fmtWhen(t, { day: true }))}">${CS.icon("key", "ico")}key until ${esc(CS.fmtWhen(t, { day: true }))}</span>` : ""; };
  const lsGet3 = k => { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } };
  const lsSet3 = (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) { } };
  const normSrv = v => CS.sync && CS.sync.normServer ? CS.sync.normServer(v) : (/^https?:\/\/[^\s]+$/.test(String(v || "").trim()) ? String(v).trim().replace(/\/+$/, "") : null);
  /** the address laptops should use: the Client's server when this page is the Client, else this page's own address */
  function keyServer() {
    const sy = CS.sync;
    if (sy && sy.mode === "client" && sy.status && sy.status.server) return sy.status.server;
    try { return new URL(".", location.href).href.replace(/\/+$/, ""); } catch (e) { return location.origin; }
  }
  const hostOf = s => { try { return new URL(s).hostname.toLowerCase(); } catch (e) { return ""; } };
  const loopback = s => { const h = hostOf(s); return h === "localhost" || h.endsWith(".localhost") || /^127\./.test(h) || h === "[::1]" || h === "0.0.0.0"; };
  // shell / PowerShell single quotes: the string never has one (normServer'd address + key alphabet), but be sure
  const q1 = s => String(s).replace(/'/g, "%27");

  function keySheet(u, after) {
    const name = esc(u.display_name || u.username), live0 = keyLive(u);
    let hours = 168, srv = keyServer(), made = null, done = "", changed = false, busy = false;
    const s = CS.sheet({ title: `${CS.icon("key", "ico")}<span>Connection key <span class="muted">for ${name}</span></span>`, wide: true, cls: "cksheet", onClose: () => { made = null; if (changed && after) after(done); } });
    const body = s.body;
    const dlOf = () => { const v = lsGet3("cs:dlsite"); const n = v ? normSrv(v) : null; return n ? n.replace(/\/downloads(\/.*)?$/, "") : null; };
    function render() {
      const live = keyLive(u);
      const who = `<span class="ck-who"><b>${name}</b>${u.display_name && u.display_name !== u.username ? ` <span class="muted small">${esc(u.username)}</span>` : ""} <span class="pill role">${esc(u.role)}</span></span>`;
      let html = `<div class="ck">`;
      if (!made) html += `<p>One key connects a laptop's Cyber-Scout Client to this server and signs it in as ${who}. Nobody types a password.</p>`;
      if (u.role === "admin" && !u.disabled && !made) html += `<div class="ck-note off">${CS.icon("warn", "ico")}<span><b>This key signs in as an admin</b> (everything: accounts, settings, the server). Hand it over in person.</span></div>`;
      if (u.disabled) html += `<div class="ck-note off">${CS.icon("ban", "ico")}<span><b>${name}'s account is disabled,</b> so a key couldn't sign it in. Enable it first (the Enable button in its row), then make a key.</span></div>`;
      if (done) html += `<div class="ck-note done" role="status">${CS.icon("check", "ico")}<span>${esc(done)}</span></div>`;
      if (!made && live) html += `<div class="ck-note live">${CS.icon("key", "ico")}<span><b>A key is active</b> until ${esc(CS.fmtWhen(live, { day: true }))}. It can't be shown again: make a new one to get a line to hand over.</span><button type="button" class="danger" data-k="revoke">Revoke</button></div>`;
      if (made) html += resultHTML();
      else if (!u.disabled) html += `<div class="ck-form">
          <fieldset class="ck-f"><legend>Works once, for</legend><div class="ck-segs">${KEY_HOURS.map(([h, t]) => `<label><input type="radio" name="ckh" value="${h}"${h === hours ? " checked" : ""}><span>${t}</span></label>`).join("")}</div></fieldset>
          <label class="ck-f">Server address laptops use<input class="csin" name="cksrv" value="${esc(srv)}" spellcheck="false" autocapitalize="none" autocomplete="off" inputmode="url"><span class="hint" data-part="srvhint"></span></label>
          <div class="ck-go"><button type="button" class="primary" data-k="make">${live ? "Make a new key" : "Make key"}</button>${live ? `<span class="muted small">This cancels the active key.</span>` : ""}</div></div>`;
      html += `<div class="cserr" role="alert" data-part="err"></div></div>`;
      body.innerHTML = html;
      srvHint();
    }
    function srvHint() {
      const h = body.querySelector("[data-part=srvhint]"); if (!h) return;
      const v = body.querySelector("[name=cksrv]").value, n = normSrv(v);
      h.className = "hint" + (!n ? " bad" : loopback(n) ? " warn" : "");
      h.textContent = !n ? "That isn't a server address (like 10.0.0.58:7560 or https://scout.example.org)."
        : loopback(n) ? "That's this computer's own address: other laptops can't reach it. Put the server's address on the team network or the internet here."
        : "Goes into the key. Laptops must reach it: the team network address, or the internet one if laptops connect from anywhere.";
    }
    function resultHTML() {
      const str = `cyberscout:${q1(made.server)}#${made.key}`, dl = dlOf(), d = dl ? esc(dl) : `<span class="ck-ph">downloads page</span>`;
      const sh = `curl -fsSL ${d}/downloads/get-client.sh | bash -s -- --key '${esc(str)}'`, ps = `$env:CS_KEY='${esc(str)}'; irm ${d}/downloads/get-client.ps1 | iex`;
      return `<div class="ck-box"><div class="ck-str" id="ckstr" aria-label="Connection key for ${name}"><span class="ck-a">cyberscout:${esc(q1(made.server))}</span><span class="ck-k">#${esc(made.key)}</span></div>
          <button type="button" class="primary" data-copy="${esc(str)}">${CS.icon("copy", "ico")}<span>Copy</span></button></div>
        <p class="ck-meta">Works once, until <b>${esc(CS.fmtWhen(made.exp, { day: true }))}</b>. Making a new one cancels this one. ${u.role === "admin" ? `<b class="ck-adm">This key signs in as an admin: hand it over in person.</b>` : `Anyone who has it can sign in as ${name} once, so hand it to ${name} directly, not in a shared channel.`}</p>
        <section class="ck-sec"><h3>On ${name}'s laptop</h3><ol class="ck-steps">
          <li><div><span><b>The Client is installed:</b> open it and paste the line on its Connect page, under “Have a connection key?” (the sign-in page has a link: Use a connection key).</span></div></li>
          <li><div><span><b>Not installed yet:</b> one line installs the Client, connects it and signs in. It's the get-client line from your team's downloads page, with the key added:</span>
            <label class="ck-f">Downloads page address<input class="csin" name="ckdl" value="${esc(lsGet3("cs:dlsite"))}" placeholder="e.g. http://10.0.0.58:7580" spellcheck="false" autocapitalize="none" autocomplete="off" inputmode="url">
              <span class="hint">Where your team got the installers. The app doesn't know it; this browser remembers what you type.</span></label>
            <div data-part="cmds">${cmdsHTML(sh, ps, !!dl)}</div></div></li></ol></section>
        <div class="ck-acts"><button type="button" class="danger" data-k="revoke">Revoke</button><span class="grow"></span><button type="button" data-k="again">Make a new key</button><button type="button" class="primary" data-x>Done</button></div>`;
    }
    const cmdsHTML = (sh, ps, ok) => `<div class="ck-cmd"><span class="ck-os">macOS, Linux (Terminal)</span><code>${sh}</code><button type="button" data-copy="${ok ? esc(strip(sh)) : ""}"${ok ? "" : ` disabled title="Add the downloads page address first"`}>${CS.icon("copy", "ico")}<span>Copy</span></button></div>
      <div class="ck-cmd" style="margin-top:6px"><span class="ck-os">Windows (PowerShell)</span><code>${ps}</code><button type="button" data-copy="${ok ? esc(strip(ps)) : ""}"${ok ? "" : ` disabled title="Add the downloads page address first"`}>${CS.icon("copy", "ico")}<span>Copy</span></button></div>`;
    const strip = h => { const t = document.createElement("textarea"); t.innerHTML = h.replace(/<[^>]+>/g, ""); return t.value; };
    function redrawCmds() {
      const box = body.querySelector("[data-part=cmds]"); if (!box || !made) return;
      const str = `cyberscout:${q1(made.server)}#${made.key}`, dl = dlOf(), d = dl ? esc(dl) : `<span class="ck-ph">downloads page</span>`;
      box.innerHTML = cmdsHTML(`curl -fsSL ${d}/downloads/get-client.sh | bash -s -- --key '${esc(str)}'`, `$env:CS_KEY='${esc(str)}'; irm ${d}/downloads/get-client.ps1 | iex`, !!dl);
    }
    const err = t => { const e = body.querySelector("[data-part=err]"); if (e) e.textContent = t ? CS.sentence(t) : ""; };
    async function make(b) {
      const inp = body.querySelector("[name=cksrv]"), n = normSrv(inp.value);
      if (!n) { srvHint(); inp.setAttribute("aria-invalid", "true"); inp.focus(); return; }
      busy = true; b.disabled = true; b.textContent = "Making…"; err("");
      try {
        const r = await CS.app("POST", `users/${u.id}/connect-key`, { hours });
        const exp = secs(r.expires_at) || Date.now() / 1000 + hours * 3600;
        made = { key: String(r.key || ""), server: n, exp }; srv = n; u.connect_key_expires = exp; done = ""; changed = true;
        render();
        const c = body.querySelector(".ck-box [data-copy]"); if (c) c.focus();
      } catch (x) { if (b.isConnected) { b.disabled = false; b.textContent = keyLive(u) ? "Make a new key" : "Make key"; } err(x.message); }
      finally { busy = false; }
    }
    async function revoke(b) {
      b.disabled = true; err("");
      try {
        await CS.app("DELETE", `users/${u.id}/connect-key`);
        made = null; u.connect_key_expires = null; changed = true;
        done = `Revoked: ${u.display_name || u.username}'s key can't be used any more.`; render();
      } catch (x) { if (b.isConnected) b.disabled = false; err(x.message); }
    }
    async function copy(b) {
      const txt = b.dataset.copy; if (!txt) return;
      const ok = typeof plCopy === "function" ? await plCopy(txt) : false, lab = b.querySelector("span") || b;
      if (ok) { lab.textContent = "Copied"; setTimeout(() => { if (b.isConnected) lab.textContent = "Copy"; }, 2000); }
      else {           // no clipboard (plain http on the team network): select the text to copy by hand
        const t = b.parentElement.querySelector(".ck-str, code"); if (t) { const r = document.createRange(); r.selectNodeContents(t); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }
        lab.textContent = "Selected: press Ctrl+C / ⌘C";
      }
    }
    body.addEventListener("click", e => {
      const b = e.target.closest("button"); if (!b || busy) return;
      if (b.dataset.copy != null) copy(b);
      else if (b.dataset.k === "make") make(b);
      else if (b.dataset.k === "revoke") revoke(b);
      else if (b.dataset.k === "again") { made = null; done = ""; render(); const m = body.querySelector("[data-k=make]"); if (m) m.focus(); }
    });
    body.addEventListener("change", e => { if (e.target.name === "ckh") hours = +e.target.value; });
    body.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.name === "cksrv") { e.preventDefault(); const m = body.querySelector("[data-k=make]"); if (m && !busy) make(m); } });
    body.addEventListener("input", e => {
      if (e.target.name === "cksrv") { srv = e.target.value; e.target.removeAttribute("aria-invalid"); srvHint(); }
      else if (e.target.name === "ckdl") { const v = e.target.value.trim(); lsSet3("cs:dlsite", v && normSrv(v) ? v : ""); redrawCmds(); }
    });
    render();
    const f = body.querySelector(live0 && !u.disabled ? "[data-k=make]" : "input[name=ckh]:checked, [data-k=make], [data-x]"); if (f) f.focus();
  }
  async function users(el, flash) {
    const [list, sec] = await Promise.all([CS.app("GET", "users"), CS.app("GET", "admin/security").catch(() => null)]);
    const me = CS.user.id, pol = sec ? sec.passwordless_login : null;        // never | lan | everywhere
    const nopwOk = pol === "everywhere" || pol === "lan";
    const nopwPill = u => u.has_password === false ? (nopwOk ? ` <span class="pill" title="signs in with just the username${pol === "lan" ? " (on the team network only)" : ""}">no password</span>`
      : ` <span class="pill failed" title="passwordless sign-in is off (Activity → Sign-in rules): this account can't sign in until it has a password">no password: can't sign in</span>`) : "";
    el.innerHTML = `<div class="card"><h2>Teammates ${CS.help(CS.ROLES.map(r => `<span class="hl"><b>${r}</b>: ${esc(CS.ROLE_TIP[r])}</span>`).join(""), "What each role can do")}</h2>
      <div class="tablewrap"><table class="utab"><thead><tr><th>User</th><th>Role</th><th data-pri="300">Last sign-in</th><th data-pri="850"><span class="vh">Actions</span></th></tr></thead><tbody>
      ${list.map(u => `<tr data-id="${u.id}" class="${u.disabled ? "dnp" : ""}"><td><b>${esc(u.username)}</b>${u.id === me ? ' <span class="pill">you</span>' : ""}${nopwPill(u)}${keyPill(u)}<div class="muted small">${esc(u.display_name)}</div></td>
        <td><select data-role aria-label="Role of ${esc(u.username)}" ${u.id === me ? `disabled title="you can't change your own role"` : ""}>${CS.ROLES.map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}</select>${u.id === me ? `<div class="muted small" style="white-space:normal">You can't change your own role.</div>` : ""}<div class="cserr rowerr" role="alert"></div></td>
        <td class="small">${u.disabled ? '<span class="pill failed">disabled</span>' : esc(u.last_login ? CS.fmtWhen(u.last_login) : "never")}${u.active_sessions ? `<div class="muted small">${u.active_sessions} session${u.active_sessions > 1 ? "s" : ""}</div>` : ""}</td>
        <td><div class="uacts"><button type="button" data-act="key" title="A one-time key that connects a laptop's Cyber-Scout Client and signs it in as ${esc(u.username)}">Connection key</button>${u.id === me ? `<button type="button" data-act="pw">Change my password</button>` : `<button type="button" data-act="reset" title="Set a new password (signs them out)">Reset password</button>
          <button type="button" data-act="toggle">${u.disabled ? "Enable" : "Disable"}</button> <button type="button" data-act="delete" class="danger" title="Delete the account (their notes stay)" aria-label="Delete ${esc(u.username)}">Delete</button>`}</div></td></tr>`).join("")}
      </tbody></table></div><div class="cserr" aria-live="polite" id="uerr"></div><div class="csok" role="status" aria-live="polite" id="uok"></div></div>
      <div class="card" id="uadd" style="margin-top:16px;max-width:620px"><h2>Add a teammate ${CS.help(`Admins need a password of ${MIN_ADMIN}+ characters. ${nopwOk ? `Everyone else may have none: they sign in with just the username${pol === "lan" ? " on the team network" : ""} (Activity → Sign-in rules).` : "Passwordless sign-in is off (Activity → Sign-in rules), so everyone needs a password: Generate makes one to share."} Share the username and password with them in person; they can change it under My account.`, "About passwords")}</h2>
        <form class="csform" style="max-width:none"><label>Username <span class="hint">2–32 letters, digits, “.”, “_” or “-”</span><input name="username" required minlength="2" maxlength="32" pattern="${CS.USER_PATTERN}" title="2 to 32 letters, digits, '.', '_' or '-', starting with a letter or digit" autocapitalize="none" spellcheck="false" autocomplete="off"></label>
          <label>Name <span class="hint">optional, shown to teammates</span><input name="display_name" maxlength="64" autocomplete="off"></label>
          <label>Role<select name="role">${CS.ROLES.map(r => `<option value="${r}" ${r === "viewer" ? "selected" : ""}>${r}</option>`).join("")}</select><span class="hint" id="roletip"></span></label>
          <label><span id="pwlabel"></span><div class="row"><input name="password" autocomplete="new-password" spellcheck="false"><button type="button" id="gen" style="flex:none">Generate</button></div><span class="hint" id="pwhint"></span></label>
          <button type="submit" class="primary">Create account</button><div><div class="cserr" aria-live="polite" style="min-height:0"></div><div class="csok" role="status" aria-live="polite" id="addok"></div></div></form>
      </div>`;
    const f = el.querySelector("#uadd");
    const roleSel = f.querySelector("[name=role]"), pwIn = f.querySelector("[name=password]");
    const pwRule = () => {                    // admins: required, 10+; everyone else: required unless passwordless sign-in is on
      const adm = roleSel.value === "admin", need = adm || !nopwOk;
      pwIn.required = need; pwIn.minLength = adm ? MIN_ADMIN : MIN_MEMBER;
      f.querySelector("#pwlabel").textContent = adm ? `Password (${MIN_ADMIN}+ characters)` : need ? `Password (${MIN_MEMBER}+ characters)` : "Password (optional)";
      f.querySelector("#pwhint").textContent = need && !adm ? "Needed: passwordless sign-in is off (Activity → Sign-in rules)." : !need ? `Blank = they sign in with just the username${pol === "lan" ? " on the team network" : ""}.` : "";
      f.querySelector("#roletip").textContent = CS.ROLE_TIP[roleSel.value] || "";
    };
    roleSel.onchange = pwRule; pwRule();
    f.querySelector("#gen").onclick = () => { pwIn.value = CS.genPassword(); pwIn.dispatchEvent(new Event("input")); };
    if (!nopwOk && !pwIn.value) pwIn.value = CS.genPassword();
    const msg = (t, ok) => { el.querySelector("#uerr").textContent = ok ? "" : CS.sentence(t); el.querySelector("#uok").textContent = ok ? t : ""; };
    CS.form(f, async (d) => {
      msg("", true);
      const u = await CS.app("POST", "users", { username: d.username.trim(), display_name: d.display_name || "", role: d.role, password: d.password });
      await users(el, `Created ${u.username} (${u.role}). ` + (d.password ? `Password: ${d.password}` : `No password: they sign in with just the username${pol === "lan" ? " on the team network" : ""}.`));
    });
    if (flash) el.querySelector("#addok").textContent = flash;
    const tb = el.querySelector(".utab");
    const rowOf = x => { const tr = x.closest("tr[data-id]"); return tr ? { tr, id: +tr.dataset.id, u: list.find(v => v.id === +tr.dataset.id) } : null; };
    const rowErr = (r, t) => { const e = el.querySelector(`tr[data-id="${r.id}"]:not(.fx-d) .rowerr`); if (e) e.textContent = t ? CS.sentence(t) : ""; else msg(t); };
    tb.onchange = async e => {
      const sel = e.target.closest("[data-role]"), r = sel && rowOf(sel); if (!r) return;
      rowErr(r, ""); msg("", true);
      try { await CS.app("PATCH", "users/" + r.id, { role: sel.value }); r.u.role = sel.value; msg(`${r.u.username} is now ${sel.value}.`, true); }
      catch (x) { sel.value = r.u.role; rowErr(r, x.status === 422 && /password/i.test(x.message) ? `${x.message} (Reset password, in this row)` : x.message); }
    };
    tb.onclick = async e => {
      const b = e.target.closest("button[data-act]"), r = b && rowOf(b); if (!r) return;
      rowErr(r, ""); msg("", true);
      const u = r.u;
      try {
        if (b.dataset.act === "pw") { location.hash = "#/account"; return; }
        if (b.dataset.act === "key") {
          keySheet(u, m => users(el).then(() => { if (m) msg(m, true); const k = el.querySelector(`tr[data-id="${u.id}"]:not(.fx-d) button[data-act=key]`); if (k && k.getClientRects().length) k.focus(); }));
          return;
        }
        if (b.dataset.act === "toggle") {
          if (!u.disabled && !confirm(`Disable ${u.username}? They're signed out and can't sign in until you enable the account again.`)) return;
          await CS.app("PATCH", "users/" + r.id, { disabled: !u.disabled }); await users(el); msg(`${u.username} is ${u.disabled ? "enabled again" : "disabled"}.`, true);
        } else if (b.dataset.act === "delete") {
          if (!confirm(`Delete ${u.username}? Their notes are kept. (Disable instead to keep the account.)`)) return;
          await CS.app("DELETE", "users/" + r.id); await users(el); msg(`Deleted ${u.username}.`, true);
        } else if (b.dataset.act === "reset") {
          const need = u.role === "admin" || !nopwOk;
          const pw = prompt(u.role === "admin" ? `New password for ${u.username} (${MIN_ADMIN}+ characters). They'll be signed out.`
            : need ? `New password for ${u.username} (${MIN_MEMBER}+ characters; passwordless sign-in is off). They'll be signed out.`
            : `New password for ${u.username} (or leave blank for none). They'll be signed out.`, need ? CS.genPassword() : "");
          if (pw === null) return;
          await CS.app("PATCH", "users/" + r.id, { password: pw }); await users(el);
          msg(pw ? `New password for ${u.username}: ${pw}` : `${u.username} now signs in with just the username.`, true);
        }
      } catch (x) { if (el.querySelector(`tr[data-id="${r.id}"]`)) rowErr(r, x.message); else msg(x.message); }
    };
  }

  // ---------------------------------------------------------------- settings
  /** what the worker is doing, from the session's processing state (setup.js polls it) */
  function workerLine() {
    const pr = CS.processing;
    if (!pr || (pr.paused == null && !pr.worker_seen)) return `<div class="wk"><span class="dot"></span><span><b>Worker status unknown.</b> It reports here after its next pass (a worker older than this version doesn't).</span></div>`;
    const seen = pr.worker_seen ? `last check ${esc(CS.fmtWhen(pr.worker_seen))}` : "";
    if (pr.paused) return `<div class="wk paused"><span class="dot"></span><span><b>Worker: paused</b> (${pr.paused_by === "app" ? "by the switch below" : "PAUSE_PROCESSING=1 in the server's .env"}) · ${seen}. Nothing new is built or tracked; Video page jobs still run.
      <div style="margin-top:6px"><button type="button" class="primary" data-resume>Resume processing</button></div></span></div>`;
    if (pr.paused == null) return `<div class="wk"><span class="dot"></span><span><b>Worker: not heard from</b> · ${seen}. Check that it's running on the server.</span></div>`;
    return `<div class="wk ok"><span class="dot"></span><span><b>Worker: ${pr.reason === "no_key" ? "waiting for a TBA key" : pr.reason === "no_events" ? "idle (no competition followed)" : "running"}</b> · ${seen}.</span></div>`;
  }
  async function settings(el) {
    const s = await CS.app("GET", "settings"), o = s.options || {}, goalKnown = CS.GOALS.some(g => g[0] === s.season_goal);
    const tri = (name, v, on, off) => `<select name="${name}"><option value="" ${v == null ? "selected" : ""}>default (from .env)</option>
      <option value="1" ${v === true ? "selected" : ""}>${on}</option><option value="0" ${v === false ? "selected" : ""}>${off}</option></select>`;
    el.innerHTML = `<div class="grid2" style="align-items:start"><div class="stack">
      <div class="card"><h2>Team</h2><form class="csform" style="max-width:none" id="fteam">
        <label>FRC team number<input name="team_number" type="number" min="1" max="99999" value="${esc(s.team_number || "")}"></label>
        ${s.team_name ? `<div class="muted small">${esc(s.team_name)}</div>` : ""}
        <fieldset class="opts" style="border:0;padding:0;margin:0;display:grid;gap:2px"><legend style="font-size:var(--fs-foot);font-weight:500;color:var(--ink-2);margin-bottom:4px">Season goal</legend>
          ${CS.GOALS.map(([v, t]) => `<label class="opt"><input type="radio" name="goal" value="${esc(v)}" ${s.season_goal === v ? "checked" : ""}> ${esc(t)}</label>`).join("")}
          <label class="opt"><input type="radio" name="goal" value="__other" ${s.season_goal && !goalKnown ? "checked" : ""}> Other: <input name="other" maxlength="40" value="${goalKnown ? "" : esc(s.season_goal || "")}" aria-label="Other goal"></label>
          <label class="opt"><input type="radio" name="goal" value="" ${s.season_goal ? "" : "checked"}> None</label></fieldset>
        <button type="submit" class="primary">Save</button><div><div class="cserr" aria-live="polite" style="min-height:0"></div><div class="csok" role="status" aria-live="polite"></div></div></form></div>
      <div class="card"><h2>The Blue Alliance key</h2>
        <div class="note" style="margin:0 0 10px">${s.tba_key_set ? `Saved: <span class="code">${esc(s.tba_key_hint)}</span>. ` : "<b>Not set: nothing gets processed.</b> "}
          Keys are made at <a href="https://www.thebluealliance.com/account" target="_blank" rel="noopener">thebluealliance.com/account</a> (Read API Keys). Stored on the server only.</div>
        <form class="csform" style="max-width:none" id="fkey"><label>${s.tba_key_set ? "Replace with" : "Key"}<input name="key" autocomplete="off" spellcheck="false" required minlength="20"></label>
          <button type="submit" class="primary">Check and save</button><div><div class="cserr" aria-live="polite" style="min-height:0"></div><div class="csok" role="status" aria-live="polite"></div></div></form></div></div>
      <div class="card"><h2>Processing ${CS.help("Applied by the worker on its next check for matches, no restart needed. A GPU device change applies from the next match.", "When changes apply")}</h2><div id="wkline">${workerLine()}</div><form class="csform" style="max-width:none" id="fopt">
        <label>Check TBA for new matches every (seconds)<input name="poll_seconds" type="number" min="30" max="3600" placeholder="default (120)" value="${o.poll_seconds ?? ""}"></label>
        <label>GPU<select name="gpu_mode"><option value="" ${o.cpu_only == null ? "selected" : ""}>default (from .env)</option>
          <option value="gpu" ${o.cpu_only === false ? "selected" : ""}>use the GPU</option><option value="cpu" ${o.cpu_only === true ? "selected" : ""}>CPU only (~4x slower)</option></select></label>
        <label>GPU device number (if there are several)<input name="gpu_device" type="number" min="0" max="15" placeholder="default (0)" value="${o.gpu_device ?? ""}"></label>
        <label>Match processing <span class="hint">paused: nothing new is tracked or built (schedules, results, rankings); Video page jobs still run</span><select name="paused"><option value="" ${o.paused == null ? "selected" : ""}>default (from .env)</option>
          <option value="0" ${o.paused === false ? "selected" : ""}>running</option><option value="1" ${o.paused === true ? "selected" : ""}>paused</option></select></label>
        <label>Driver-station cameras ("hub cams": camera switcher + shot checks, ~1-2 min extra per match)${tri("hubcam", o.hubcam, "on", "off")}</label>
        <button type="submit" class="primary">Save</button><div><div class="cserr" aria-live="polite" style="min-height:0"></div><div class="csok" role="status" aria-live="polite"></div></div></form>
</div></div>`;
    const ok = (f, t) => { f.querySelector(".csok").textContent = t; setTimeout(() => { f.querySelector(".csok").textContent = ""; }, 4000); };
    const ft = el.querySelector("#fteam").parentElement, fk = el.querySelector("#fkey").parentElement, fo = el.querySelector("#fopt").parentElement;
    const oth = ft.querySelector("input[name=other]"); oth.oninput = () => { if (oth.value) ft.querySelector("input[value=__other]").checked = true; };
    CS.form(ft, async (d, f) => {
      const g = d.goal === "__other" ? (d.other || "").trim() : d.goal;
      if (d.goal === "__other" && !g) throw CS.fieldErr("other", "Type the goal next to Other (or pick one)");
      let r; try { r = await CS.app("PATCH", "settings", { team_number: d.team_number ? +d.team_number : null, season_goal: g || null }); }
      catch (e) { if (/team/i.test(e.message)) e.field = "team_number"; throw e; }
      ok(f, `Saved${r.team_name ? ": " + r.team_name : ""}.`);
    });
    const wk = () => { const w = el.querySelector("#wkline"); if (w && w.isConnected) w.innerHTML = workerLine(); else removeEventListener("cs:processing", wk); };
    addEventListener("cs:processing", wk); if (typeof cleanup !== "undefined") cleanup.push(() => removeEventListener("cs:processing", wk));
    CS.form(fk, async () => { try { await CS.app("PUT", "settings/tba-key", { key: fk.querySelector("[name=key]").value.trim() }); } catch (e) { e.field = "key"; throw e; } await settings(el); }, { busy: "Checking with The Blue Alliance…" });
    CS.form(fo, async (d, f) => {
      const num = v => v === "" ? null : +v;
      await CS.app("PATCH", "settings", { options: { poll_seconds: num(d.poll_seconds), gpu_device: num(d.gpu_device),
        cpu_only: d.gpu_mode === "" ? null : d.gpu_mode === "cpu", hubcam: d.hubcam === "" ? null : d.hubcam === "1",
        paused: d.paused === "" ? null : d.paused === "1" } });
      ok(f, "Saved.");
    });
    // "Resume processing" on the paused worker line: the same option, set to running (wins over PAUSE_PROCESSING=1)
    el.querySelector("#wkline").onclick = async ev => {
      const b = ev.target.closest("button[data-resume]"); if (!b) return;
      b.disabled = true; b.textContent = "Resuming…";
      try {
        await CS.app("PATCH", "settings", { options: { paused: false } });
        const sel = fo.querySelector("select[name=paused]"); if (sel) sel.value = "0";
        b.textContent = "Resumed: the worker picks it up within ~30 s";
      } catch (e) { b.disabled = false; b.textContent = "Resume processing"; fo.querySelector(".cserr").textContent = CS.sentence(e.message); }
    };
    // the generic CS.form focuses the first input; don't jump the page on the settings tab
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- competitions (followed events)
  // Each one is separate (teams, matches, scoring, picklists, notes, identity fixes, video setup); the worker tracks the one
  // on now first. The list/status helpers come from app.js (CS.comps; same rules as the competition switcher).
  async function events(el) {
    const evs = await CS.app("GET", "events");
    CS.comps && CS.comps(true).catch(() => { });
    const today = new Date(), iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    const st = e => !e.start_date ? ["", "dates unknown"] : iso < e.start_date ? ["upcoming", "upcoming"] : iso > (e.end_date || e.start_date) ? ["", "done"] : ["live", "live now"];
    const dates = e => compDates(e);
    el.innerHTML = `<div class="card"><h2>Followed competitions ${CS.help("Every competition is kept separate: its teams, matches, scoring, picklists, notes, identity fixes and video setup (sources, layout boxes, camera calibration). Teammates' accounts and roles, your team number, the TBA key and settings are shared. The worker tracks the competition that's on now first, then the others.", "How competitions work")}</h2>
      ${evs.length ? `<div class="evpick" style="max-height:none">${evs.map(e => { const [cls, txt] = st(e);
        return `<div class="evrow"><div><b>${esc(e.name || e.key)}</b> <span class="pill cmp-pill ${cls}">${esc(txt)}</span>${e.needs_setup ? ` <span class="pill cmp-pill no_video" title="no camera calibration yet: matches aren't tracked">needs video setup</span>` : ""}
          <div class="muted">${esc(e.key)} · ${esc(dates(e))}${e.city ? " · " + esc([e.city, e.state_prov].map(x => String(x || "").replace(/[\s,]+$/, "")).filter(Boolean).join(", ")) : ""} · ${e.n_total ? `${e.n_done ?? 0} of ${e.n_total} matches tracked` : "not built yet"}</div></div>
        <div class="evacts" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><a class="cmp-btn" href="#/${esc(e.key)}">Open</a><a class="cmp-btn" href="#/${esc(e.key)}/sources">Video setup</a>
        <button type="button" data-k="${esc(e.key)}" class="danger" title="Stop following (processed data is kept)">Unfollow</button></div></div>`; }).join("")}</div>`
        : `<div class="muted">Not following any competition: the worker is idle.</div>`}
      <div class="cserr" aria-live="polite"></div></div>
      <div class="card" style="margin-top:16px"><h2>Follow another competition</h2><div class="note" style="margin:0 0 10px">After following one, set up its video (its own sources, layout boxes and camera calibration): matches are tracked only after that.</div><div id="picker"></div></div>`;
    el.querySelectorAll("button[data-k]").forEach(b => b.onclick = async () => {
      const e0 = evs.find(x => x.key === b.dataset.k) || {};
      if (!confirm(`Unfollow ${e0.name || b.dataset.k}? Already processed matches, picklists and notes are kept.`)) return;
      try { await CS.app("DELETE", "events/" + b.dataset.k); await events(el); }
      catch (e) { el.querySelector(".cserr").textContent = CS.sentence(e.message); }
    });
    CS.eventPicker(el.querySelector("#picker"), { showFollowed: false, onFollow: () => events(el) });
  }

  // ---------------------------------------------------------------- server (.env)
  // A few infrastructure settings from the server's .env. The api can't read or write .env: it asks the host-side updater
  // (same request file as Updates, action "env"), which writes .env, recreates the containers that use it, waits for
  // them to be healthy and puts the old .env back if they aren't. The keys and their kinds come from the updater
  // (ENV_KEYS in deploy/updater/cs-updater.py); labels and help live here. Keys without a label still show (raw name).
  const ENV_UI = [
    ["Tracking", [
      ["GPU_IDS", "Graphics cards for the worker + recorder", "Host card numbers (nvidia-smi -L): 0, 1 or 0,1. Inside the containers they're renumbered from 0. With GPU_DRIVER=cdi in .env (set by the installer when a card works only that way): one card."],
      ["PROCESSORS_GPU0", "Extra tracking jobs on card 0", "Each job tracks one match at a time: ~300 MB of graphics memory and 8-10 CPU cores. 0 = none."],
      ["PROCESSORS_GPU1", "Extra tracking jobs on card 1", "Same, on card 1 (on atomosserver card 1 is also used for experiments and training)."],
      ["WORKER_TRACK", "Main worker tracks matches too", "auto: only while no extra job is running. 1: always. 0: never (leave it to the extra jobs)."],
      ["WORKER_MEM_LIMIT", "Worker memory cap", "e.g. 12g. The container is stopped and restarted if it goes over."],
      ["PROCESSOR_MEM_LIMIT", "Memory cap per extra job", "e.g. 6g."]]],
    ["Videos", [
      ["MEDIA_PROXIES", "Small preview copies for offline clients", "480p copies of kept match videos that the Cyber-Scout Client and app download."],
      ["PROXY_ENCODER", "Preview encoder", "auto: the graphics card's video encoder (NVENC) if it works, else the CPU (slower, 2 threads)."],
      ["PROXY_GPU", "Card for preview encoding", "As the worker container numbers them: with one card in GPU_IDS that is 0."],
      ["YT_FORCE_IPV4", "Download YouTube over IPv4 only", "Keep on where YouTube bot-checks the server's IPv6 address."],
      ["CS_PROBE_LINKS", "Check links pasted on the Video page", "One quick request to see that the address answers."],
      ["CS_URL_ALLOW", "Private hosts allowed as video sources", "Comma separated host names, IPs or CIDRs, e.g. an OBS server on the team network (10.0.0.20)."]]],
    ["Livestream recorder", [
      ["LIVESTREAM", "Record competition livestreams", "Records each followed event's stream during its dates and cuts matches out of it."],
      ["LIVE_BUDGET_GB", "Disk budget per stream (GB)", "Old segments are deleted past this."],
      ["LIVE_MIN_FREE_GB", "Keep this much disk free (GB)", "Old segments of finished streams are deleted to keep it."],
      ["RECORDER_MEM_LIMIT", "Recorder memory cap", "e.g. 8g."]]],
    ["Server", [
      ["TZ", "Time zone", "e.g. America/Los_Angeles. Used for logs and the recorder's event dates."],
      ["SESSION_IDLE_DAYS", "Sign out after this many days unused", ""],
      ["SESSION_MAX_DAYS", "Sign out after this many days in any case", ""]]],
  ];
  let srvTimer = 0;
  async function server(el) {
    clearTimeout(srvTimer);
    el.innerHTML = `<div class="card" id="srv" style="max-width:820px"><h2>Server settings ${CS.help("These come from the <span class=\"code\">.env</span> file in the Cyber-Scout folder on the server. The web app can't edit that file itself: it asks the updater (a small program on the server), which saves the change, restarts the containers that use it, checks that everything comes back healthy, and puts the old file back by itself if not. The previous file is kept in <span class=\"code\">updater/env-backups/</span>.", "How server settings work")}</h2>
      <div class="note" style="margin:0 0 12px">To pause or resume match processing, use <a href="#/admin/settings">Settings → Processing</a>: that applies right away, without a restart.</div>
      <div id="srvbody"><div class="muted">Loading…</div></div>
      <div class="cserr" aria-live="polite" id="srverr"></div></div>`;
    const body = el.querySelector("#srvbody"), card = el.querySelector("#srv");
    const err = t => { el.querySelector("#srverr").textContent = t ? CS.sentence(t) : ""; };
    let st = null, offline = 0, draft = {}, sent = false, showProg = false;   // sent: waiting for our change to finish

    const label = k => { for (const [, rows] of ENV_UI) for (const r of rows) if (r[0] === k) return r; return [k, k, ""]; };
    const groups = () => {
      const known = new Set(ENV_UI.flatMap(g => g[1].map(r => r[0])));
      const extra = Object.keys(st.schema).filter(k => !known.has(k));
      return [...ENV_UI.map(([g, rows]) => [g, rows.map(r => r[0]).filter(k => st.schema[k])]), ["Other", extra]].filter(g => g[1].length);
    };
    const val = k => k in draft ? draft[k] : (st.values[k] ?? "");
    function field(k) {
      const sp = st.schema[k], v = val(k), dflt = `default (${sp.default === "" ? "none" : sp.default})`, id = "env_" + k;
      const opts = (list, names) => `<option value="" ${v === "" ? "selected" : ""}>${esc(dflt)}</option>` + list.map((o, i) => `<option value="${esc(o)}" ${v === o ? "selected" : ""}>${esc(names ? names[i] : o)}</option>`).join("");
      if (sp.kind === "bool") return `<select class="csin" id="${id}" data-k="${esc(k)}">${opts(["1", "0"], ["on", "off"])}</select>`;
      if (sp.kind === "choice") return `<select class="csin" id="${id}" data-k="${esc(k)}">${opts(sp.choices)}</select>`;
      if (sp.kind === "int") return `<input class="csin" id="${id}" data-k="${esc(k)}" type="number" inputmode="numeric" min="${sp.min}" max="${sp.max}" step="1" placeholder="${esc(dflt)}" value="${esc(v)}">`;
      return `<input class="csin" id="${id}" data-k="${esc(k)}" spellcheck="false" autocapitalize="none" autocomplete="off" placeholder="${esc(dflt)}" value="${esc(v)}">`;
    }
    const changed = () => Object.keys(draft).filter(k => (draft[k] || "") !== (st.values[k] ?? ""));

    function render() {
      if (!st) { body.innerHTML = `<div class="muted">The server isn't answering${offline ? " (restarting?)" : ""}. Retrying…</div>`; return; }
      if (!st.installed) {
        body.innerHTML = `<div class="note" style="margin:0 0 10px"><b>The updater isn't installed on this server</b> (or isn't running), so these settings can only be changed by hand in <span class="code">.env</span>.</div>
          <div class="small">Install it once, on the server, in the Cyber-Scout folder (e.g. <span class="code">/opt/cyber-scout</span>):</div>
          <div style="margin:6px 0"><span class="code">sudo ./app/deploy/updater/install-updater.sh</span></div>
          <div class="muted small">It only needs the docker group, not root, to run. Details: app/deploy/updater/README.md.</div>`;
        return;
      }
      if (!st.supported) {
        body.innerHTML = `<div class="note" style="margin:0"><b>The updater on the server is an older version</b> that can't change settings. Re-run <span class="code">sudo ./app/deploy/updater/install-updater.sh</span> in the Cyber-Scout folder on the server.</div>`;
        return;
      }
      const r = st.result, dirty = changed(), busy = st.busy;
      const keepFocus = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.k;
      body.innerHTML = `${st.error ? `<div class="cserr" style="margin-bottom:8px">${esc(CS.sentence(st.error))}</div>` : ""}
        <form id="envf" novalidate>${groups().map(([g, keys]) => `<fieldset class="envgrp"><legend>${esc(g)}</legend>${keys.map(k => { const [, t, h] = label(k);
          return `<div class="envrow${dirty.includes(k) ? " dirty" : ""}"><label for="env_${esc(k)}"><div>${esc(t)} ${h ? CS.help(esc(h), t) : ""}</div><div class="k">${esc(k)}${st.schema[k].api ? " · also restarts the api" : ""}</div></label>${field(k)}</div>`; }).join("")}</fieldset>`).join("")}
          <div class="row" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
            <button type="submit" class="primary" ${busy || !dirty.length ? "disabled" : ""}>${dirty.length ? `Save ${dirty.length} change${dirty.length > 1 ? "s" : ""} and restart` : "No changes"}</button>
            ${dirty.length && !busy ? `<button type="button" data-undo>Undo</button>` : ""}
            ${st.other_task ? `<span class="muted small">The updater is busy with an update; try again when it's done.</span>` : ""}</div></form>
        ${st.running ? `<div style="margin-top:12px"><b>Saving and restarting…</b>${offline ? ` <span class="muted small">the site is restarting; this page reconnects by itself</span>` : ""}</div>` : ""}
        ${(st.running || (r && showProg)) && st.progress.length ? `<div class="small" style="margin-top:8px;max-height:14em;overflow:auto;font-family:var(--mono);background:var(--surface-2);border-radius:var(--r-tag);padding:6px 8px" id="srvprog">${st.progress.map(l => `<div>${esc(l)}</div>`).join("")}</div>` : ""}
        ${!st.running && r && r.message ? `<div class="${r.ok ? "csok" : "cserr"}" style="margin-top:8px">${esc(r.message)} <span class="muted small">${esc(when(r.at))}</span></div>` : ""}`;
      const pr = body.querySelector("#srvprog"); if (pr) pr.scrollTop = pr.scrollHeight;
      if (keepFocus) { const f = body.querySelector(`[data-k="${window.CSS.escape(keepFocus)}"]`); if (f) f.focus(); }
    }

    async function poll() {
      clearTimeout(srvTimer);
      if (!card.isConnected) return;
      try { st = await CS.app("GET", "admin/server-env"); offline = 0; err(""); }
      catch (e) {
        if (e.status === 401 || e.status === 403) { err(e.message); return; }
        offline++;
      }
      if (!card.isConnected) return;
      if (st && sent && !st.running) { draft = {}; sent = false; }     // done: the values now come from the new .env
      render();
      if ((st && st.running) || offline) srvTimer = setTimeout(poll, offline ? 3000 : 2500);
    }

    body.addEventListener("input", ev => {
      const k = ev.target.dataset && ev.target.dataset.k; if (!k || !st) return;
      draft[k] = ev.target.value.trim();
      const row = ev.target.closest(".envrow"); if (row) row.classList.toggle("dirty", (draft[k] || "") !== (st.values[k] ?? ""));
      const n = changed().length, b = body.querySelector("#envf button[type=submit]");
      if (b) { b.disabled = st.busy || !n; b.textContent = n ? `Save ${n} change${n > 1 ? "s" : ""} and restart` : "No changes"; }
    });
    body.addEventListener("click", ev => { if (ev.target.closest("[data-undo]")) { draft = {}; err(""); render(); } });
    body.addEventListener("submit", async ev => {
      ev.preventDefault(); if (!st) return;
      const keys = changed(); if (!keys.length) return;
      const api = keys.some(k => st.schema[k].api);
      const lines = keys.map(k => `  ${k}: ${st.values[k] ?? "(default)"} → ${draft[k] || "(default)"}`).join("\n");
      if (!confirm(`Change these server settings?\n\n${lines}\n\nRestarts the worker, the recorder${api ? ", the api" : ""} and any extra tracking jobs: a match being tracked is redone, and a livestream recording has a gap of a few seconds${api ? "; the site blinks while the api restarts" : ""}.\nIf the app doesn't come back healthy, the previous .env is put back by itself.`)) return;
      const b = body.querySelector("#envf button[type=submit]"); if (b) { b.disabled = true; b.textContent = "Asking the updater…"; }
      err("");
      try {
        await CS.app("POST", "admin/server-env", { changes: Object.fromEntries(keys.map(k => [k, draft[k] || null])) });
        sent = showProg = true;
      } catch (e) {
        err(e.message);
        const m = /^([A-Z][A-Z0-9_]+) /.exec(e.message || ""), f = m && body.querySelector(`[data-k="${m[1]}"]`); if (f) f.focus();
      }
      await poll();
    });
    await poll();
  }

  // ---------------------------------------------------------------- updates
  // The api only *asks* a host-side updater (deploy/updater/) to check / apply / roll back and reports its status
  // file; the updater does git + docker. Polls while it works, and keeps polling through the restart (the page
  // stays loaded while the web container restarts; after a reload the state comes back from the server).
  const day = ts => ts ? new Date(ts * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) : "";
  const ver = c => c ? `<span class="code">${esc(c.short)}</span>${c.date ? ` · ${esc(day(c.date))}` : ""}` : `<span class="muted">unknown</span>`;
  const restarts = t => {
    if (!t) return "";
    const p = [];
    if (t.worker === "heavy") p.push("worker + recorder (big rebuild of the worker image)");
    else if (t.worker === "light") p.push("worker + recorder");
    if (t.api) p.push("api");
    if (t.web) p.push("web");
    return p.join(", ");
  };
  let updTimer = 0, updBase = null;
  async function updates(el) {
    clearTimeout(updTimer);
    el.innerHTML = `<div class="card" id="upd" style="max-width:760px"><h2>Updates ${CS.help("Updates are done by a small program on the server (the updater), not by this web app: the app can only ask it to <b>check</b>, <b>update</b> or <b>roll back</b>. It fetches the code, rebuilds and restarts the containers, checks that everything is healthy, and goes back to the previous version by itself if anything fails. Settings and data are kept; the database is backed up first.", "How updates work")}</h2>
      <div id="updbody"><div class="muted">Loading…</div></div>
      <div class="cserr" aria-live="polite" id="upderr"></div><div class="csok" role="status" aria-live="polite" id="updok"></div></div>`;
    const body = el.querySelector("#updbody"), card = el.querySelector("#upd");
    const err = t => { el.querySelector("#upderr").textContent = t || ""; };
    let st = null, offline = 0, ytdlp = false;

    function render() {
      if (!st) { body.innerHTML = `<div class="muted">The server isn't answering${offline ? " (restarting?)" : ""}. Retrying…</div>`; return; }
      const busy = st.state === "checking" || st.state === "applying" || !!st.pending;
      if (!st.installed) {
        body.innerHTML = `<div class="note" style="margin:0 0 10px">${st.current ? `Version ${ver(st.current)}. ` : ""}<b>The updater isn't installed on this server</b> (or isn't running), so updates are done by hand.</div>
          <div class="small">Install it once, on the server, in the Cyber-Scout folder (e.g. <span class="code">/opt/cyber-scout</span>):</div>
          <div style="margin:6px 0"><span class="code">sudo ./app/deploy/updater/install-updater.sh</span></div>
          <div class="muted small">Or re-run install.sh and answer yes to the updater. Details: app/deploy/updater/README.md.</div>`;
        return;
      }
      const cur = st.current, n = st.changelog.length + (st.changelog_more || 0), r = st.result;
      const doing = st.pending ? `Waiting for the updater to start (${esc(st.pending.action)})…`
        : st.state === "checking" ? "Checking for updates…" : st.state === "applying" ? (st.action === "rollback" ? "Rolling back…" : st.action === "env" ? "Changing server settings (Admin → Server)…" : "Updating…") : "";
      body.innerHTML = `<div style="margin-bottom:8px">Version ${ver(cur)}${cur && cur.local ? ` <span class="pill" title="app/ on the server has changes that aren't in the published code">local changes</span>` : ""}
          ${cur && cur.subject ? `<div class="muted small">${esc(cur.subject)}</div>` : ""}</div>
        ${CS.processing && CS.processing.paused ? `<div class="note" style="margin:0 0 8px">Match processing is paused; it stays paused after an update.</div>` : ""}
        ${n ? `<div style="margin:12px 0 6px"><b>${n} update${n === 1 ? "" : "s"} available</b>${st.latest ? ` → ${ver(st.latest)}` : ""} <span class="muted small">checked ${esc(when(st.checked_at))}</span></div>
          <ul class="small" style="margin:0 0 8px;padding-left:1.2em;max-height:14em;overflow:auto">${st.changelog.map(c => `<li><span class="code">${esc(c.short)}</span> ${esc(c.subject || "")} <span class="muted">${esc(day(c.date))}</span></li>`).join("")}
          ${st.changelog_more ? `<li class="muted">… and ${st.changelog_more} older</li>` : ""}</ul>
          ${st.touches ? `<div class="muted small">Restarts: ${esc(restarts(st.touches) || "containers")}${st.disk ? ` · needs ~${st.disk.need_gb} GB free disk, ${st.disk.free_gb} GB free` : ""}</div>` : ""}
          ${st.disk && !st.disk.ok ? `<div class="cserr">Not enough free disk on the server for this update: free some space first (e.g. old Docker images).</div>` : ""}`
        : st.checked_at ? `<div class="muted" style="margin:12px 0 6px">Up to date (checked ${esc(when(st.checked_at))}).</div>` : ""}
        <div class="row" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px">
          <button type="button" data-u="check" ${busy ? "disabled" : ""}>Check for updates</button>
          ${n || ytdlp ? `<button type="button" class="primary" data-u="apply" ${busy ? "disabled" : ""}>Update now</button>` : ""}
          ${st.previous ? `<button type="button" class="danger" data-u="rollback" ${busy ? "disabled" : ""} title="${esc(st.previous.subject || "")}">Roll back to ${esc(st.previous.short)}</button>` : ""}
          ${st.ytdlp_allowed ? `<label class="small"><input type="checkbox" id="updyt" ${ytdlp ? "checked" : ""} ${busy ? "disabled" : ""}> also update yt-dlp (YouTube downloader${st.ytdlp_pinned ? `, now ${esc(st.ytdlp_pinned)}` : ""}) ${CS.help("Use this when match videos stop downloading: YouTube changes often break older yt-dlp versions. Rebuilds a small part of the worker image.", "About yt-dlp")}</label>` : ""}
        </div>
        ${doing ? `<div style="margin-top:12px"><b>${doing}</b>${offline ? ` <span class="muted small">the site is restarting; this page reconnects by itself</span>` : ""}</div>` : ""}
        ${(busy || r) && st.progress.length ? `<div class="small" style="margin-top:8px;max-height:16em;overflow:auto;font-family:var(--mono);background:var(--surface-2);border-radius:var(--r-tag);padding:6px 8px" id="updprog">${st.progress.map(l => `<div>${esc(l)}</div>`).join("")}</div>` : ""}
        ${!busy && r && r.message ? `<div class="${r.ok ? "csok" : "cserr"}" style="margin-top:8px">${esc(r.message)}</div>` : ""}
        ${!busy && r && r.ok && r.action !== "check" && updBase && cur && cur.sha !== updBase ? `<div style="margin-top:8px"><button type="button" class="primary" data-u="reload">Reload the page</button> <span class="muted small">to use the new version</span></div>` : ""}
        ${st.rejected && Date.now() / 1000 - st.rejected.at < 600 ? `<div class="muted small" style="margin-top:8px">The updater ignored a request: ${esc(st.rejected.reason || "")}</div>` : ""}`;
      const pr = body.querySelector("#updprog"); if (pr) pr.scrollTop = pr.scrollHeight;
      const cb = body.querySelector("#updyt"); if (cb) cb.onchange = () => { ytdlp = cb.checked; render(); };
    }

    async function poll() {
      clearTimeout(updTimer);
      if (!card.isConnected) return;
      try { st = await CS.app("GET", "admin/update"); offline = 0; err(""); if (!updBase && st.current) updBase = st.current.sha; }
      catch (e) {
        if (e.status === 401 || e.status === 403) { err(e.message); return; }
        offline++;                                  // api / web restarting: keep the last status, try again
      }
      if (!card.isConnected) return;
      render();
      const busy = st && (st.state === "checking" || st.state === "applying" || st.pending);
      if (busy || offline) updTimer = setTimeout(poll, offline ? 3000 : 2500);
    }

    body.onclick = async ev => {
      const b = ev.target.closest("button[data-u]"); if (!b || !st) return;
      const act = b.dataset.u; err(""); el.querySelector("#updok").textContent = "";
      if (act === "reload") { location.reload(); return; }
      if (act === "apply") {
        const what = restarts(st.touches) || (ytdlp ? "worker + recorder" : "the containers");
        const heavy = st.touches && st.touches.worker === "heavy";
        if (!confirm(`Update Cyber-Scout${st.latest ? ` to ${st.latest.short}` : ""}${ytdlp ? " (and yt-dlp)" : ""}?\n\nRestarts: ${what}.\nIt takes a few minutes${heavy ? " (the worker image is rebuilt from scratch: maybe 15+ minutes)" : ""}. The site keeps working while it builds, then blinks for a few seconds while the containers restart. A match being processed right now is redone afterwards.\n\nThe database is backed up first; if anything fails, it goes back to the current version by itself.`)) return;
      }
      if (act === "rollback" && !confirm(`Roll back to ${st.previous.short}${st.previous.subject ? ` (${st.previous.subject})` : ""}?\n\nThe containers restart (the site blinks for a few seconds). Use this if the last update broke something.`)) return;
      b.disabled = true;
      try {
        await CS.app("POST", "admin/update/" + act, act === "apply" ? { ytdlp } : undefined);
        if (act === "apply") ytdlp = false;
      } catch (e) { err(e.message); }
      await poll();
    };
    await poll();
  }
})();
