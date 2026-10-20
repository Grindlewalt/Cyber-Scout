"use strict";
// setup.js: platform plugin -- sign-in, session + role-aware nav, first-run setup wizard, "my account".
// Talks to the API at app/v1/* (server/README.md). Exposes for other plugins:
//   CS.app(method, path, body?)  fetch app/v1/<path> with the session cookie + CSRF header; throws Error{status,data}
//   CS.user {id, username, display_name, role} | null      CS.can(role) -> bool (viewer < scout < strategist < admin)
//   CS.platform  true when the API is present (false = legacy static site: no sign-in, localStorage only)
//   CS.eventPicker(el, opts)  TBA event list (team's events first) with Follow buttons (admin)
//                             opts: {year, showFollowed, allowUnfollow (default true), onFollow(ev | null)}
//   CS.nav({..., roles: ["admin"]})  or  {minRole: "strategist"}: only shown to those roles
(function () {
  const ROLES = ["viewer", "scout", "strategist", "admin"];
  // the server's USERNAME_RE as an HTML pattern (the "v" flag: a literal "-" must be escaped in a class)
  const USER_PATTERN = "[A-Za-z0-9][A-Za-z0-9._\\-]{1,31}";
  CS.USER_PATTERN = USER_PATTERN;
  const ROLE_TIP = {
    viewer: "read-only: all pages, picklists and notes",
    scout: "viewer + write notes, fix robot identities",
    strategist: "scout + edit the shared picklist, edit anyone's notes",
    admin: "everything: accounts, TBA key, events, video setup",
  };
  const GOALS = [["Win an event", "Win an event"], ["DCMP", "Qualify for District Championship (DCMP)"],
    ["Worlds", "Qualify for the Championship (Worlds)"], ["Einstein", "Make Einstein"], ["Scouting", "Just scouting"]];
  CS.ROLES = ROLES; CS.ROLE_TIP = ROLE_TIP; CS.GOALS = GOALS;
  CS.user = null; CS.session = null; CS.platform = false;

  // ---------------------------------------------------------------- styles (forms; keeps style.css untouched)
  const css = document.createElement("style");
  css.textContent = `
  .csform { display: grid; gap: var(--sp-3); max-width: 420px; }
  .csform label { display: grid; gap: var(--sp-1); font-size: var(--fs-foot); font-weight: 500; color: var(--ink-2); }
  .csform input, .csform select, .csin { background: var(--surface); color: var(--ink); border: 1px solid var(--line-strong); border-radius: var(--r-s);
    padding: var(--sp-2) 10px; font: inherit; font-size: 16px; width: 100%; min-height: 40px; }
  .csform input:focus-visible, .csform select:focus-visible, .csin:focus-visible { outline: 2px solid var(--accent); outline-offset: 0; border-color: var(--accent); }
  .csform .row { display: flex; gap: var(--sp-2); align-items: center; } .csform .row > * { flex: 1; }
  .csform button.primary, .csform .row button { min-height: 40px; font-size: var(--fs-body); }
  /* a field's own error, right under it (CS.form fieldFor) */
  .csform .ferr { color: var(--bad); font-size: var(--fs-foot); font-weight: 500; }
  .csform [aria-invalid=true] { border-color: var(--bad); }
  .csform .pwrow { display: flex; gap: var(--sp-2); } .csform .pwrow input { flex: 1; min-width: 0; }
  .csform .pwrow .pwshow { flex: none; min-height: 40px; font-size: var(--fs-foot); }
  .csform .hint { font-weight: 400; color: var(--ink-2); font-size: var(--fs-cap); }
  #userbox .tb-lock { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 99px; border: 1px solid var(--caution-line);
    background: var(--caution-tint); color: var(--ink); font-weight: 600; text-decoration: none; }
  #userbox .tb-lock svg { width: 13px; height: 13px; color: var(--caution); }
  .steps .later { color: var(--ink-2); font-style: italic; } .steps .need { border-style: dashed; }
  .cserr { color: var(--bad); font-size: var(--fs-foot); font-weight: 500; min-height: 1em; } .csok { color: var(--good); font-size: var(--fs-foot); }
  .authcard { max-width: 440px; margin: var(--sp-7) auto; padding: var(--sp-5); }
  .authcard h1 { margin-bottom: var(--sp-4); }
  .steps { display: flex; gap: var(--sp-1); flex-wrap: wrap; margin: var(--sp-1) 0 var(--sp-5); counter-reset: st; }
  .steps a, .steps span { font-size: var(--fs-foot); padding: var(--sp-1) 10px; border-radius: var(--r-s); border: 1px solid var(--line); color: var(--ink-2); text-decoration: none; }
  .steps .cur { border-color: var(--accent); background: var(--accent-tint); color: var(--ink); font-weight: 600; } .steps .done { color: var(--good); }
  .evpick { display: grid; gap: var(--sp-1); max-height: 420px; overflow-y: auto; margin-top: var(--sp-2); overscroll-behavior: contain; }
  .evrow { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--line); font-size: 13.5px; }
  .evrow b { font-weight: 600; } .evrow .muted { font-size: var(--fs-cap); }
  .code { font-family: var(--mono); background: var(--surface-2); padding: 1px 6px; border-radius: var(--r-tag); overflow-wrap: anywhere; }
  @media (max-width: 640px) { #userbox .who { display: none; } }
  /* radios / checkboxes inside forms are not text fields */
  .csform input[type=radio], .csform input[type=checkbox] { width: auto; min-height: 0; padding: 0; margin: 0; border: 0; flex: none; }
  .csform input[type=radio]:focus-visible, .csform input[type=checkbox]:focus-visible { outline-offset: 2px; }
  .csform input[type=number] { -moz-appearance: textfield; appearance: textfield; }
  .csform input[type=number]::-webkit-inner-spin-button, .csform input[type=number]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
  .csform .opt { display: flex; gap: 10px; align-items: center; min-height: 36px; color: var(--ink); font-size: var(--fs-body); font-weight: 400; cursor: pointer; }
  .csform .opt input[type=text], .csform .opt input:not([type]) { flex: 1; min-width: 0; }
  .csform.opts { gap: var(--sp-1); } .csform.opts button { margin-top: var(--sp-2); }
  /* empty status lines take no room (they stay in the DOM for aria-live) */
  .csform .cserr:empty, .csform .csok:empty { position: absolute; min-height: 0; }   /* out of the grid flow: no gap */
  .authcard .csform, .acctcard .csform { max-width: none; }
  .acctcard .note { margin-top: var(--sp-3); }
  .evctl select { min-height: 40px; font-size: var(--fs-body); }
  .evctl #evkey { flex: 1 1 220px; max-width: 340px; }
  @media (max-width: 480px) { .evrow .pill { display: none; } }
  @media (pointer: coarse) { .cmp-hit .cmp-btn, .evrow button, .csform .pwrow .pwshow { min-height: 44px; } }
  /* wizard: video step */
  .vsl { display: grid; margin: var(--sp-2) 0 0; border-top: 1px solid var(--line); }
  .vsr { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) auto; gap: var(--sp-1) var(--sp-4); align-items: center; padding: var(--sp-3) 0; border-bottom: 1px solid var(--line); }
  .vsr b { font-weight: 600; } .vsr .vsm { display: grid; gap: 2px; font-size: var(--fs-foot); color: var(--ink-2); min-width: 0; overflow-wrap: anywhere; }
  .vsr .vsm .ok { color: var(--good); } .vsr .vsm .ico { width: 14px; height: 14px; vertical-align: -2px; }
  .vsr a.vsgo { display: inline-flex; align-items: center; gap: 4px; min-height: 36px; padding: 6px var(--sp-3); border: 1px solid var(--line-strong); border-radius: var(--r-s);
    color: var(--ink); font-weight: 600; font-size: var(--fs-foot); text-decoration: none; white-space: nowrap; background: var(--surface); }
  .vsr a.vsgo:hover { background: var(--surface-2); border-color: var(--ink-3); } .vsr a.vsgo svg { width: 14px; height: 14px; }
  @media (max-width: 640px) { .vsr { grid-template-columns: minmax(0, 1fr); } .vsr a.vsgo { justify-self: start; } }`;
  document.head.appendChild(css);

  // ---------------------------------------------------------------- API client
  function cookie(name) {
    const m = document.cookie.match(new RegExp("(?:^|;\\s*)" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : "";
  }
  function errText(d, status) {
    const x = d && d.detail;
    if (!x) return "HTTP " + status;
    if (typeof x === "string") return x;
    if (Array.isArray(x)) return x.map(e => (e.loc ? e.loc.slice(-1)[0] + ": " : "") + e.msg).join("; ");
    return x.message || JSON.stringify(x);
  }
  CS.csrf = () => cookie("cs_csrf") || (CS.session && CS.session.csrf) || "";
  // opts (sync.js): {headers} extra request headers (Idempotency-Key), {timeout} ms before giving up (AbortError, no .status)
  CS.app = async function (method, path, body, opts) {
    const opt = { method, credentials: "same-origin", cache: "no-store", headers: {} };
    if (method !== "GET") opt.headers["X-CSRF-Token"] = CS.csrf();
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    if (opts && opts.headers) Object.assign(opt.headers, opts.headers);
    let ac = null, tm = 0;
    if (opts && opts.timeout && window.AbortController) { ac = new AbortController(); opt.signal = ac.signal; tm = setTimeout(() => ac.abort(), opts.timeout); }
    let r;
    try { r = await fetch("app/v1/" + path, opt); } finally { clearTimeout(tm); }
    if (CS.sync && CS.sync.seenApp) CS.sync.seenApp(path, r);       // sync.js: the Client's cached answers get "Saved copy · as of"
    let d = null;
    if (r.status !== 204) { try { d = await r.json(); } catch (e) { } }
    if (!r.ok) {
      const err = new Error(errText(d, r.status)); err.status = r.status; err.data = d;
      if (r.status === 401 && CS.platform && CS.user) { CS.user = null; if (CS.sync) CS.sync.forget(); setTimeout(() => goLogin(), 0); }
      throw err;
    }
    return d;
  };
  CS.can = role => !CS.platform || (!!CS.user && ROLES.indexOf(CS.user.role) >= ROLES.indexOf(role));

  let loaded = false, apiDown = false;
  // sync.js (CS.sync): client mode asks the local Cyber-Scout Client first (no server set yet -> #/connect, not signed in ->
  // #/login); when the server can't be reached, the last signed-in user on this device keeps reading the saved data offline.
  CS.refreshSession = async function () {
    apiDown = false;
    const sy = CS.sync;
    if (sy) await sy.ready;
    if (sy && sy.mode === "client") {
      const st = await sy.refresh();
      if (!st) { apiDown = true; loaded = true; return CS.session; }
      CS.platform = true;
      if (!st.server || !st.signed_in) { CS.session = { client: true, setup_required: false, configured: true, user: null }; CS.user = null; loaded = true; return CS.session; }
    }
    try {
      const r = await fetch("app/v1/session", { credentials: "same-origin", cache: "no-store" });
      const json = (r.headers.get("content-type") || "").includes("json");
      if (r.status === 404 || (r.ok && !json)) { CS.platform = false; CS.session = null; CS.user = null; }  // legacy static site / dev server
      else if (!r.ok) { apiDown = true; }
      else { CS.platform = true; CS.session = await r.json(); CS.user = CS.session.user; if (sy) { if (CS.user) sy.rememberUser(CS.user); else sy.forget(); }
        if (CS.setProcessing) CS.setProcessing(CS.user ? CS.session.processing : null); }
    } catch (e) { apiDown = true; }
    const off = apiDown && sy && sy.offlineSession();
    if (off) { apiDown = false; CS.platform = true; CS.session = off; CS.user = off.user; if (sy.mode === "browser") sy.lost(); }
    loaded = true;
    return CS.session;
  };
  /** sign-in state changed outside the login form (client signed out, back online): check the session again and re-route */
  CS.reauth = () => { loaded = false; route(false); };
  // every minute (while the page is visible): lockdown and the worker's processing state can change under an open page
  let pollT = 0;
  async function pollSession() {
    clearTimeout(pollT); pollT = setTimeout(pollSession, 60000);
    if (document.hidden || !CS.platform || !CS.user || apiDown) return;
    try {
      const r = await fetch("app/v1/session", { credentials: "same-origin", cache: "no-store" });
      if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) return;
      const s2 = await r.json();
      if (!s2.user || !CS.user || s2.user.id !== CS.user.id) return;            // signed out / switched: the next request handles it
      const lock = !!(CS.session && CS.session.lockdown) !== !!s2.lockdown;
      CS.session = Object.assign({}, CS.session, { lockdown: s2.lockdown, processing: s2.processing });
      if (lock) renderUserBox();
      if (CS.setProcessing) CS.setProcessing(s2.processing);
    } catch (e) { }
  }
  pollT = setTimeout(pollSession, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollSession(); });
  CS.navAllowed = i => !CS.platform || ((!i.roles || (CS.user && i.roles.includes(CS.user.role))) && (!i.minRole || CS.can(i.minRole)));

  // ---------------------------------------------------------------- hooks into app.js (route / nav / api)
  const hashParts = () => location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  function goLogin() {
    const p = hashParts();
    if (p[0] !== "login" && p[0] !== "setup" && p[0] !== "connect") { try { sessionStorage.setItem("cs:next", location.hash); } catch (e) { } }
    location.replace("#/login");
  }
  function afterAuth() {
    let next = "#/";
    try { next = sessionStorage.getItem("cs:next") || "#/"; sessionStorage.removeItem("cs:next"); } catch (e) { }
    if (/^#\/(login|setup|connect)/.test(next)) next = "#/";      // (the Client's "which server?" page is done once signed in)
    if (CS.user && CS.user.role === "admin" && CS.session && !CS.session.configured) next = "#/setup";
    if (location.hash === next) route(); else location.hash = next;
  }

  const appRoute = route;
  route = async function (scroll) {
    if (!loaded) await CS.refreshSession();
    const p = hashParts();
    if (apiDown) {
      renderUserBox();
      const cl = CS.sync && CS.sync.mode === "client";
      $app.innerHTML = cl ? `<div class="empty err" role="alert"><b>The Cyber-Scout Client isn't answering</b>This page is served by the Client on this computer. Check that it's still running, then try again.<div class="acts"><button type="button" id="retry">Try again</button></div></div>`
        : `<div class="empty err" role="alert"><b>Can't reach the Cyber-Scout server</b>Check that the server is running and this device is on its network. Once you've signed in here, saved data stays readable without a connection.<div class="acts"><button type="button" id="retry">Try again</button></div></div>`;
      document.getElementById("retry").onclick = () => { loaded = false; route(); };
      return;
    }
    if (CS.sync && CS.sync.needsServer()) {          // Cyber-Scout Client, first run: which server?
      if (p[0] !== "connect" && p[0] !== "settings") { location.replace("#/connect"); return; }
      return appRoute(scroll);
    }
    if (CS.platform) {
      if (CS.session.setup_required && p[0] !== "setup") { location.replace("#/setup"); return; }
      if (!CS.session.setup_required && !CS.user && p[0] !== "login") { goLogin(); return; }
      if (CS.user && p[0] === "login") { afterAuth(); return; }
      if (CS.user && CS.user.role === "admin" && !CS.session.configured && !p.length) { location.replace("#/setup"); return; }
    }
    return appRoute(scroll);
  };

  const appTopNav = renderTopNav;
  renderTopNav = function (p) {
    const all = CS.navItems;
    CS.navItems = all.filter(CS.navAllowed);
    try { appTopNav(p); } finally { CS.navItems = all; }
    renderUserBox();
  };

  const appApi = api;
  api = async function (p) {
    try { return await appApi(p); }
    catch (e) {
      if (CS.platform && / 401$|: 401$/.test(e.message)) { await CS.refreshSession(); if (!CS.user) { if (CS.sync) CS.sync.forget(); goLogin(); } }
      throw e;
    }
  };

  /** Sign out (toolbar, the phone's More sheet, My account). The Client asks first, as its Settings button does. */
  CS.signOut = async () => {
    const cl = CS.sync && CS.sync.mode === "client";
    if (cl && !confirm("Sign this computer out of the server? Saved data stays; syncing stops until you sign in again.")) return;
    if (cl) { try { await CS.sync.clientLogout(); } catch (x) { } }
    else { try { await CS.app("POST", "auth/logout"); } catch (x) { } if (CS.sync) CS.sync.signedOut(); }
    CS.user = null; loaded = false; if (CS.setProcessing) CS.setProcessing(null); location.hash = "#/login";
  };
  function renderUserBox() {
    const el = document.getElementById("userbox"); if (!el) return;
    if (!CS.platform || !CS.user) { el.innerHTML = ""; return; }
    const u = CS.user, lock = !!(CS.session && CS.session.lockdown), admin = CS.can("admin");
    // lockdown: everyone sees it (read-only for all but admins); admins get the way out
    const lk = lock ? `<${admin ? 'a href="#/activity"' : "span"} class="tb-lock" title="${admin ? "Lockdown is on: only admins can change anything. Open Activity to end it." : "Lockdown: the site is read-only right now (an admin turned it on)."}">${CS.icon("ban")}<span>Read-only${admin ? " (lockdown)" : ""}</span></${admin ? "a" : "span"}>` : "";
    el.innerHTML = `${lk}<a href="#/account" class="who" title="${esc(ROLE_TIP[u.role])}">${esc(u.display_name || u.username)} · ${esc(u.role)}</a>
      <a href="#" id="signout">Sign out</a>`;
    el.querySelector("#signout").onclick = e => { e.preventDefault(); CS.signOut(); };
  }

  // ---------------------------------------------------------------- helpers
  // CS.form(el, onSubmit, opts): submit -> onSubmit(values, form). An error lands under the field it is about (Error.field,
  // or guessed from the server's sentence: "setup code", "username", "password", "name"), marked aria-invalid and
  // focused; anything else in the form's .cserr. Messages are shown as sentences. Old success lines are cleared on submit.
  // opts: {busy: "Checking…"} button text while it runs.
  const FIELD_GUESS = [[/setup code/i, ["setup_code"]], [/don't match|do not match/i, ["again"]], [/current password/i, ["current_password"]],
    [/password/i, ["new_password", "password"]], [/username/i, ["username"]], [/display name|teammate's name/i, ["display_name"]], [/goal/i, ["goal"]]];
  function fieldOf(f, x) {
    const names = x.field ? [x.field] : ((FIELD_GUESS.find(([re]) => re.test(x.message || "")) || [])[1] || []);
    for (const n of names) { const i = f.querySelector(`[name="${n}"]:not([type=hidden])`); if (i && !i.disabled) return i; }
    return null;
  }
  function clearErrs(f) {
    f.querySelectorAll(".ferr").forEach(x => x.remove());
    f.querySelectorAll("[aria-invalid]").forEach(i => { i.removeAttribute("aria-invalid"); i.removeAttribute("aria-describedby"); });
  }
  let ferrN = 0;
  function showErr(f, err, x) {
    const m = CS.sentence(x.message || String(x)), inp = fieldOf(f, x);
    if (!inp) { if (err) err.textContent = m; return; }
    const id = "ferr" + (++ferrN), sp = document.createElement("span"); sp.className = "ferr"; sp.id = id; sp.setAttribute("role", "alert"); sp.textContent = m;
    const host = inp.closest(".pwrow, .row") || inp; host.after(sp);
    inp.setAttribute("aria-invalid", "true"); inp.setAttribute("aria-describedby", id);
    inp.addEventListener("input", () => { sp.remove(); inp.removeAttribute("aria-invalid"); inp.removeAttribute("aria-describedby"); }, { once: true });
    if (inp.type !== "radio") { inp.focus(); if (inp.select && inp.type !== "number") inp.select(); } else inp.focus();
  }
  function form(el, onSubmit, opts = {}) {
    const f = el.querySelector("form"), err = f.querySelector(".cserr") || el.querySelector(".cserr"), btn = f.querySelector("button[type=submit]");
    const label = btn ? btn.textContent : "";
    f.onsubmit = async e => {
      e.preventDefault(); if (err) err.textContent = ""; clearErrs(f);
      f.querySelectorAll(".csok").forEach(x => { x.textContent = ""; });
      if (btn) { btn.disabled = true; if (opts.busy) btn.textContent = opts.busy; }
      try { await onSubmit(Object.fromEntries(new FormData(f).entries()), f); }
      catch (x) { showErr(f, err, x); }
      finally { if (btn) { btn.disabled = false; if (opts.busy) btn.textContent = label; } }
    };
    // a password field gets a Show / Hide toggle
    f.querySelectorAll("input[type=password]").forEach(i => {
      if (i.closest(".pwrow")) return;
      const w = document.createElement("span"); w.className = "pwrow"; i.before(w); w.appendChild(i);
      const b = document.createElement("button"); b.type = "button"; b.className = "pwshow"; b.textContent = "Show"; b.setAttribute("aria-pressed", "false");
      b.setAttribute("aria-label", "Show password");
      b.onclick = () => { const on = i.type === "password"; i.type = on ? "text" : "password"; b.textContent = on ? "Hide" : "Show"; b.setAttribute("aria-pressed", String(on)); };
      w.appendChild(b);
    });
    const first = f.querySelector("input[type=radio]:checked") || f.querySelector("input:not([type=hidden]), select");
    if (first && f.closest(".authcard, [data-autofocus]")) first.focus();
  }
  CS.form = form;
  /** throw from a form handler: the message lands under that field */
  const fieldErr = (field, message) => Object.assign(new Error(message), { field });
  CS.fieldErr = fieldErr;
  CS.genPassword = () => {
    const a = "abcdefghjkmnpqrstuvwxyz23456789", b = new Uint32Array(14); crypto.getRandomValues(b);
    return Array.from(b, x => a[x % a.length]).join("").replace(/(.{4})(?=.)/g, "$1-");
  };

  // ---------------------------------------------------------------- login
  CS.globalRoute("login", async () => {
    crumbs([["Sign in"]]); $updated.textContent = "";
    const cl = CS.sync && CS.sync.mode === "client" ? CS.sync.status || {} : null;   // Cyber-Scout Client: it signs in, keeps the session
    $app.innerHTML = `<div class="card authcard"><h1>Sign in</h1><div class="sub">${cl ? `This computer's Cyber-Scout Client signs in to <b>${esc(CS.sync.host(cl.server))}</b> and keeps you signed in. <a href="#/connect">Change server</a>`
      : "Cyber-Scout. Ask your team's admin for an account."}</div>
      <form class="csform" autocomplete="on">
        <label>Username<input name="username" autocomplete="username" required maxlength="32" autocapitalize="none" spellcheck="false"></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" placeholder="Leave blank if you don't have one"></label>
        <button type="submit" class="primary">Sign in</button><div class="cserr" aria-live="polite"></div>
        <div class="note" style="margin:0">Forgot your password? Your team's admin can reset it (Admin → Teammates).</div></form></div>`;
    // a wrong password: say so under the password, and clear it for the next try
    const bad = x => {
      if (x && x.status === 401 && /username or password/i.test(x.message || "")) { const pw = $app.querySelector("input[name=password]"); if (pw) pw.value = ""; x.field = "password"; }
      return x;
    };
    form($app, async d => {
      if (cl) {
        try { await CS.sync.clientLogin(d.username.trim(), d.password || ""); } catch (x) { throw bad(x); }
        loaded = false; await CS.refreshSession();
        if (!CS.user) throw new Error(apiDown ? "Signed in, but the server stopped answering. Try again." : "Signed in, but the server didn't confirm it. Try again.");
        afterAuth(); return;
      }
      try { CS.session = await CS.app("POST", "auth/login", { username: d.username.trim(), password: d.password || "" }); } catch (x) { throw bad(x); }
      CS.user = CS.session.user; if (CS.sync) CS.sync.rememberUser(CS.user);
      if (CS.setProcessing) CS.setProcessing(CS.session.processing);
      afterAuth();
    });
  });

  // ---------------------------------------------------------------- my account
  CS.globalRoute("account", async () => {
    crumbs([["My account"]]); $updated.textContent = "";
    if (!CS.user) { goLogin(); return; }
    const u = CS.user, adm = u.role === "admin";
    $app.innerHTML = `<h1>${esc(u.display_name || u.username)}</h1><div class="sub">Signed in as <b>${esc(u.username)}</b> · role <b>${esc(u.role)}</b> (${esc(ROLE_TIP[u.role])})</div>
      <div class="grid2" style="align-items:start;max-width:980px">
      <div class="card acctcard" id="acpw"><h2>${adm ? "Change password" : "Password"}</h2>
      <form class="csform"><label>Current password<input name="current_password" type="password" autocomplete="current-password" placeholder="Leave blank if you don't have one"></label>
        <label>New password <span class="hint">${adm ? "10+ characters" : "optional: 4+ characters, or leave both blank to sign in with just your username"}</span><input name="new_password" type="password" autocomplete="new-password" ${adm ? 'minlength="10" required' : 'minlength="4"'}></label>
        <label>Repeat new password<input name="again" type="password" autocomplete="new-password"${adm ? " required" : ""}></label>
        <button type="submit" class="primary">${adm ? "Change password" : "Save password"}</button><div class="cserr" aria-live="polite"></div><div class="csok" role="status" aria-live="polite"></div></form>
      <div class="note">Changing your password signs you out everywhere else.${adm ? "" : " Without a password you can sign in only where your admin allows it (Activity → Sign-in rules)."}</div></div>
      <div class="stack">
      ${CS.can("scout") ? `<div class="card acctcard" id="acname"><h2>Your name</h2>
        <form class="csform"><label>Shown to teammates (notes, picklist edits, Activity)<input name="display_name" maxlength="64" minlength="2" required autocomplete="name" value="${esc(u.display_name || "")}"></label>
        <button type="submit" class="primary">Save name</button><div class="cserr" aria-live="polite"></div><div class="csok" role="status" aria-live="polite"></div></form></div>` : ""}
      <div class="card acctcard"><h2>Sign out</h2><div class="note" style="margin:0 0 10px">${CS.sync && CS.sync.mode === "client" ? "Signs this computer's Cyber-Scout Client out of the server. Saved data stays." : "Signs this browser out. Saved copies for offline use stay on this device."}</div>
        <button type="button" id="acout">Sign out</button></div></div></div>`;
    form($app.querySelector("#acpw"), async (d, f) => {
      if ((d.new_password || d.again) && d.new_password !== d.again) throw fieldErr("again", "the new passwords don't match");
      await CS.app("POST", "auth/password", { current_password: d.current_password || "", new_password: d.new_password || "" });
      f.reset(); $app.querySelector("#acpw .csok").textContent = d.new_password ? "Password changed." : "Password removed: you sign in with just your username.";
    });
    if ($app.querySelector("#acname")) form($app.querySelector("#acname"), async (d, f) => {
      const r = await CS.app("PATCH", "auth/profile", { display_name: d.display_name.trim() });
      CS.user = Object.assign({}, CS.user, r && r.user ? r.user : { display_name: d.display_name.trim() });
      renderTopNav(hashParts()); $app.querySelector("h1").textContent = CS.user.display_name || CS.user.username;
      f.querySelector(".csok").textContent = "Saved.";
    });
    $app.querySelector("#acout").onclick = () => CS.signOut();
  });

  // ---------------------------------------------------------------- TBA event picker (setup + admin)
  // The same ranked type-ahead as the competition switcher's "Add" (GET events/search: on now / soon / your team first,
  // a full key from any season looked up directly); the followed ones on top with Unfollow.
  const WHEN_T = { now: "On now", soon: "Soon", recent: "Just ended" };
  CS.eventPicker = async function (el, opts = {}) {
    const y0 = new Date().getFullYear();
    let seq = 0, t = 0, followed = [];
    el.innerHTML = `${opts.showFollowed !== false ? `<div id="evfol"></div>` : ""}
      <form class="cmp-find evfind" role="search"><label class="vh" for="evq">Find an event on The Blue Alliance</label>
        <input id="evq" class="csin" type="search" placeholder="Event name, city or key (2026wasam)…" autocomplete="off" autocapitalize="none" spellcheck="false" enterkeyhint="search">
        <select id="evyr" aria-label="Season">${[y0 + 1, y0, y0 - 1, y0 - 2].map(y => `<option ${y === (opts.year || y0) ? "selected" : ""}>${y}</option>`).join("")}</select></form>
      <div class="cmp-res" id="evres" aria-live="polite"><div class="cmp-none">Loading events from The Blue Alliance…</div></div>
      <div class="cserr" aria-live="polite"></div>`;
    const q = el.querySelector("#evq"), yr = el.querySelector("#evyr"), res = el.querySelector("#evres"), fol = el.querySelector("#evfol"), errEl = el.querySelector(".cserr");
    const place = e => [e.city, e.state_prov].map(x => String(x || "").replace(/[\s,]+$/, "")).filter(Boolean).join(", ");
    const drawFollowed = () => {
      if (!fol) return;
      fol.innerHTML = followed.length ? `<h3>Following</h3><div class="evpick" style="max-height:none">${followed.map(e => `<div class="evrow"><div><b>${esc(e.name || e.key)}</b><div class="muted">${esc([e.key, compDates(e), place(e)].filter(Boolean).join(" · "))}</div></div>
          ${opts.allowUnfollow === false ? `<span class="muted small">following</span>` : `<button type="button" data-u="${esc(e.key)}" title="Stop following (its data is kept)">Unfollow</button>`}</div>`).join("")}</div><h3>Add an event</h3>` : "";
    };
    const hit = e => { const tags = [WHEN_T[e.when] ? `<span class="pill cmp-when ${e.when}">${WHEN_T[e.when]}</span>` : "", e.offseason ? `<span class="pill">Offseason</span>` : "",
        e.your_team ? `<span class="pill cmp-mine">Your team</span>` : ""].join("");
      return `<div class="cmp-hit"><span class="cmp-main"><b>${esc(e.name || e.key)}</b><span class="cmp-sub">${esc([e.key, compDates(e), place(e)].filter(Boolean).join(" · "))}</span>${tags ? `<span class="cmp-tags">${tags}</span>` : ""}</span>
        ${e.followed ? `<span class="pill done">following</span>` : `<button type="button" class="cmp-btn" data-k="${esc(e.key)}" aria-label="Follow ${esc(e.name || e.key)}">Follow</button>`}</div>`; };
    async function loadFollowed() { try { followed = await CS.app("GET", "events"); } catch (e) { followed = []; } drawFollowed(); }
    async function find() {
      const my = ++seq, v = q.value.trim();
      try {
        const r = await CS.app("GET", `events/search?q=${encodeURIComponent(v)}&year=${/^\d{4}/.test(v) ? v.slice(0, 4) : yr.value}&limit=12`);
        if (my !== seq) return;
        res.innerHTML = r.results.length ? `${v ? "" : `<div class="cmp-rh">${r.team_number ? `On now, soon and team ${esc(r.team_number)}'s events first` : "On now and soon first"}</div>`}${r.results.map(hit).join("")}
          ${r.n_matched > r.results.length ? `<div class="cmp-none">${r.n_matched - r.results.length} more: type more of the name.</div>` : ""}`
          : `<div class="cmp-none">No ${esc(r.year)} event matches “${esc(v)}”. Try the city, or the event key from its TBA link (thebluealliance.com/event/<b>2026wasam</b>).</div>`;
      } catch (e) {
        if (my !== seq) return;
        res.innerHTML = `<div class="cmp-none cserr" role="alert">${esc(e.status === 409 ? "Set your TBA Read API key first (the TBA key step / Admin → Settings): event lists come from The Blue Alliance." : CS.sentence(e.message))}</div>`;
      }
    }
    q.oninput = () => { clearTimeout(t); t = setTimeout(find, 220); };
    yr.onchange = find;
    el.querySelector(".evfind").onsubmit = e => { e.preventDefault(); clearTimeout(t); find(); };
    el.onclick = async e => {
      const f = e.target.closest("button[data-k]"), u = e.target.closest("button[data-u]"); if (!f && !u) return;
      const b = f || u; b.disabled = true; errEl.textContent = "";
      try {
        if (f) { b.textContent = "Following…"; const ev = await CS.app("POST", "events", { key: f.dataset.k }); await loadFollowed(); await find(); opts.onFollow && opts.onFollow(ev); }
        else { await CS.app("DELETE", "events/" + u.dataset.u); await loadFollowed(); await find(); opts.onFollow && opts.onFollow(null); }
      } catch (x) { b.disabled = false; if (f) b.textContent = "Follow"; errEl.textContent = CS.sentence(x.message); }
    };
    await Promise.all([loadFollowed(), find()]);
    if (opts.focus) q.focus();
  };

  // ---------------------------------------------------------------- first-run wizard
  const STEPS = [["account", "Admin account"], ["tba", "TBA key"], ["team", "Your team"], ["goal", "Season goal"], ["event", "Event"], ["video", "Video"], ["done", "Done"]];
  const KEYS = STEPS.map(x => x[0]);

  // Until the admin finishes the wizard once (settings.setup_complete), it walks every step in order, even ones that
  // already have a value (e.g. a TBA key / event imported from an old .env), so nothing is silently auto-picked.
  // Video is optional here (it can be done later): ✓ once every followed event has its video set up, "later" once the
  // admin pressed Continue on it (this browser remembers), otherwise open.
  const lsWiz = (v) => { try { if (v === undefined) return localStorage.getItem("cs:wiz-video") === "1"; localStorage.setItem("cs:wiz-video", v ? "1" : "0"); } catch (e) { } return false; };
  async function wizardState(cur) {
    const [s, evs] = await Promise.all([CS.app("GET", "settings"), CS.app("GET", "events")]);
    const done = { account: true, tba: s.tba_key_set, team: !!s.team_number, goal: !!s.season_goal, event: evs.length > 0,
      video: evs.length > 0 && evs.every(e => !e.needs_setup) };
    done.later = !done.video && lsWiz();
    const firstMissing = KEYS.find(k => k !== "done" && k !== "video" && !done[k]) || "done";
    let next = firstMissing;
    if (!s.setup_complete) {
      const after = cur ? KEYS[KEYS.indexOf(cur) + 1] : "tba";
      next = KEYS.indexOf(firstMissing) < KEYS.indexOf(after) ? firstMissing : after;
    }
    return { s, evs, done, next };
  }

  /** the step list (also on the first page, so it's clear what follows): ✓ done, "later" for a skipped Video, links only to
   *  steps you can reach */
  function wizHead(step, done = {}, reach = () => false) {
    const n = STEPS.findIndex(([k]) => k === step) + 1;
    return `<h1>Set up Cyber-Scout</h1><div class="sub" style="margin-bottom:var(--sp-2)">Step ${n} of ${STEPS.length}</div><ol class="steps" aria-label="Setup steps" style="list-style:none;padding:0">${STEPS.map(([k, t]) => {
      const later = k === "video" && done.later && k !== step, ok = done[k] && k !== step;
      const cls = k === step ? "cur" : ok ? "done" : later ? "later" : "";
      const lab = `${ok ? "✓ " : ""}${t}${later ? " · later" : ""}`;
      return `<li style="display:contents">${k === "account" || k === step || !reach(k) ? `<span class="${cls}"${k === step ? ' aria-current="step"' : ""}>${lab}</span>` : `<a class="${cls}" href="#/setup/${k}">${lab}</a>`}</li>`;
    }).join("")}</ol>`;
  }
  CS.globalRoute("setup", async step => {
    crumbs([["Setup"]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">This site runs without the Cyber-Scout server (no setup needed).</div>`; return; }
    if (CS.session.setup_required) return stepAccount();
    if (!CS.user) { goLogin(); return; }
    if (!CS.can("admin")) { $app.innerHTML = `<div class="empty">Only an admin can change the setup.</div>`; return; }
    const st = await wizardState();
    // required steps can't be skipped: a step is reachable when every required step before it is done
    const REQ = ["tba", "team", "event"];
    const reach = k => KEYS.slice(1, KEYS.indexOf(k)).every(x => !REQ.includes(x) || st.done[x]);
    if (KEYS.includes(step) && step !== "account" && !reach(step)) { location.replace("#/setup/" + st.next); return; }   // a required step first
    step = KEYS.includes(step) && step !== "account" ? step : st.next;
    const head = wizHead(step, st.done, reach);
    const next = () => wizardState(step).then(s2 => { location.hash = "#/setup/" + s2.next; });
    if (step === "tba") {
      $app.innerHTML = head + `<div class="card" data-autofocus style="max-width:620px"><h2>The Blue Alliance API key</h2>
        <div class="note" style="margin:0 0 12px">Cyber-Scout gets schedules, results and match videos from The Blue Alliance. Sign in at
        <a href="https://www.thebluealliance.com/account" target="_blank" rel="noopener">thebluealliance.com/account</a>, add a <b>Read API Key</b>
        (any description) and paste it here. It is checked with TBA and stored only on this server; browsers never see it again.</div>
        ${st.s.tba_key_set ? `<div class="csok" role="status" aria-live="polite" style="margin-bottom:10px">A key is saved (${esc(st.s.tba_key_hint || "")}). Paste a new one to replace it.</div>` : ""}
        <form class="csform"><label>Read API key<input name="key" autocomplete="off" spellcheck="false" required minlength="20"></label>
        <div class="row"><button type="submit" class="primary">Check and save</button>${st.s.tba_key_set ? `<button type="button" id="skip">Keep current key</button>` : ""}</div>
        <div class="cserr" aria-live="polite"></div></form></div>`;
      form($app, async d => {
        try { await CS.app("PUT", "settings/tba-key", { key: d.key.trim() }); }
        catch (e) { if (e.status === 422 && Array.isArray(e.data && e.data.detail)) throw fieldErr("key", "That doesn't look like a TBA Read API key: it is letters and digits only. Copy it again from thebluealliance.com/account."); e.field = "key"; throw e; }
        next();
      }, { busy: "Checking with The Blue Alliance…" });
      const sk = $app.querySelector("#skip"); if (sk) sk.onclick = next;
    } else if (step === "team") {
      $app.innerHTML = head + `<div class="card" data-autofocus style="max-width:620px"><h2>Your team</h2>
        <div class="note" style="margin:0 0 12px">Used to put your matches, your events and your season goal first.</div>
        <form class="csform"><label>FRC team number<input name="team" type="number" min="1" max="99999" required value="${esc(st.s.team_number || "")}" inputmode="numeric" autocomplete="off"></label>
        <button type="submit" class="primary">Save</button><div class="cserr" aria-live="polite"></div></form>
        ${st.s.team_name ? `<div class="note">Saved: team ${esc(st.s.team_number)} <b>${esc(st.s.team_name)}</b>.</div>` : ""}</div>`;
      form($app, async d => { try { await CS.app("PATCH", "settings", { team_number: +d.team }); } catch (e) { e.field = "team"; throw e; } next(); }, { busy: "Looking it up…" });
    } else if (step === "goal") {
      const cur = st.s.season_goal || "", known = GOALS.some(g => g[0] === cur);
      $app.innerHTML = head + `<div class="card" data-autofocus style="max-width:620px"><h2>Season goal ${CS.help("The Rankings &amp; goals page tracks how close you are to this (points still needed, odds, what each match is worth).", "What the season goal is for")}</h2>
        <form class="csform opts" aria-label="Season goal">${GOALS.map(([v, t]) => `<label class="opt"><input type="radio" name="goal" value="${esc(v)}" ${cur === v ? "checked" : ""}> ${esc(t)}</label>`).join("")}
          <label class="opt"><input type="radio" name="goal" value="__other" ${cur && !known ? "checked" : ""}> Other:
            <input name="other" maxlength="40" value="${known ? "" : esc(cur)}" aria-label="Other goal"></label>
          <button type="submit" class="primary">Save</button><div class="cserr" aria-live="polite"></div></form></div>`;
      form($app, async d => {
        const g = d.goal === "__other" ? (d.other || "").trim() : d.goal;
        if (!g) throw fieldErr(d.goal === "__other" ? "other" : "goal", "Pick a goal (or type one next to Other)");
        await CS.app("PATCH", "settings", { season_goal: g }); next();
      });
      const oth = $app.querySelector("input[name=other]"); oth.oninput = () => { if (oth.value) $app.querySelector("input[value=__other]").checked = true; };
    } else if (step === "event") {
      $app.innerHTML = head + `<div class="card" style="max-width:760px"><h2>Which event(s) should Cyber-Scout follow? ${CS.help("It processes every match of a followed event as soon as the video is up. You can follow several and change this later in Admin → Competitions.", "What following an event does")}</h2>
        <div id="picker"></div><div class="controls"><button class="primary" id="cont" ${st.evs.length ? "" : "disabled"} aria-describedby="conthint">Continue</button>
          <span class="muted small" id="conthint">${st.evs.length ? "" : "Follow at least one event to continue."}</span></div></div>`;
      const cont = $app.querySelector("#cont"), hint = $app.querySelector("#conthint");
      CS.eventPicker($app.querySelector("#picker"), { focus: true, onFollow: () => {
        CS.app("GET", "events").then(l => { cont.disabled = !l.length; hint.textContent = l.length ? "" : "Follow at least one event to continue."; });
        if (CS.comps) CS.comps(true).then(() => renderTopNav(hashParts()));     // the sidebar shows the new competition right away
      } });
      cont.onclick = next;
    } else if (step === "video") {
      const srcs = await Promise.all(st.evs.map(e => CS.app("GET", `events/${e.key}/sources`).then(d => d.sources || [], () => null)));
      $app.innerHTML = head + `<div class="card" data-autofocus style="max-width:760px"><h2>Match video <span class="muted small" style="font-weight:400">(optional now)</span></h2>
        <div class="note" style="margin:0 0 12px">For each event, tell Cyber-Scout where its video comes from: the event's stream (TBA lists it on the
          Video page, or paste a YouTube or Twitch link) and any extra cameras, and what each one is: <b>Main / wide view</b>, <b>Broadcast</b>
          (cuts between cameras), or a <b>Red</b> / <b>Blue station cam</b>. Then <b>Automatic set-up</b> on the stream's card finds the field box and
          calibrates the camera (or draw the field box yourself). Until that's done, schedules and results still work, but robots aren't tracked.</div>
        <div class="vsl">${st.evs.map((e, i) => { const S = srcs[i];
          return `<div class="vsr"><div><b>${esc(e.name || e.key)}</b><div class="muted small">${esc(e.key)}</div></div>
            <div class="vsm"><span>${S == null ? "Video sources: not available yet" : S.length ? `${S.length} source${S.length === 1 ? "" : "s"}: ${S.map(x => esc(x.label || x.kind || x.id)).join(", ")}` : "No video sources yet"}</span>
              <span>${!e.needs_setup ? `<span class="ok">${CS.icon("check", "ico")} set up</span>` : S && S.some(x => x.has_layout) ? `<span class="ok">${CS.icon("check", "ico")} field box drawn</span> · camera not calibrated` : "Field box not set up yet"}</span></div>
            <a class="vsgo" href="#/${esc(e.key)}/sources">Set up video${CS.icon("chevR")}</a></div>`; }).join("") || `<div class="muted small" style="padding:12px 0">Follow an event first.</div>`}</div>
        <div class="controls"><button class="primary" id="cont">${st.done.video ? "Continue" : "Continue, set up video later"}</button> <span class="muted small">You can come back to this any time from the competition's <b>Video</b> page.</span></div></div>`;
      $app.querySelector("#cont").onclick = () => { lsWiz(true); next(); };
      ($app.querySelector(".vsgo") || $app.querySelector("#cont")).focus();
    } else {
      const ev = st.evs[0];
      if (!st.s.setup_complete) await CS.app("PATCH", "settings", { setup_complete: true });
      CS.session = await CS.refreshSession();
      if (CS.comps) CS.comps(true).then(() => renderTopNav(hashParts()));
      $app.innerHTML = head + `<div class="card" style="max-width:720px"><h2>All set</h2>
        <div class="note" style="margin:0 0 12px">Team ${esc(st.s.team_number || "?")}${st.s.team_name ? " " + esc(st.s.team_name) : ""} · goal: ${esc(st.s.season_goal || "not set")} ·
          following ${st.evs.map(e => esc(e.name || e.key)).join(", ") || "no events"}.</div>
        <ol style="line-height:1.9;margin:0;padding-left:20px">
          ${st.evs.map(e => `<li><a href="#/${esc(e.key)}/sources"><b>Set up the video for ${esc(e.name || e.key)}</b></a>: the event's stream, what each video is, and its field box (Automatic set-up on the stream's card does most of it).</li>`).join("")}
          <li><a href="#/admin/users">Add teammates</a>: give each one their own login and a role.</li>
          ${ev ? `<li><a href="#/${esc(ev.key)}">Open ${esc(ev.name || ev.key)}</a>: matches show up as they're played and processed.</li>` : ""}
        </ol></div>`;
    }
  });

  async function stepAccount() {
    const needCode = CS.session.setup_code_required;
    $app.innerHTML = `<div class="card authcard" style="max-width:560px"><h1>Welcome to Cyber-Scout</h1>
      <div class="sub" style="margin-bottom:var(--sp-3)">First-time setup. Create the admin account: it manages teammates' logins, the TBA key, events and video.</div>
      <ol class="steps" aria-label="Setup steps" style="list-style:none;padding:0">${STEPS.map(([k, t], i) => `<li style="display:contents"><span class="${i ? "" : "cur"}"${i ? "" : ' aria-current="step"'}>${t}</span></li>`).join("")}</ol>
      <form class="csform">
        ${needCode ? `<label>Setup code<input name="setup_code" required autocomplete="off" spellcheck="false" placeholder="xxxx-xxxx-xxxx">
          <span class="hint">Printed by the installer, or on the server: <span class="code">cat data/setup-code.txt</span> in the install folder.</span></label>` : ""}
        <label>Username <span class="hint">2–32 letters, digits, “.”, “_” or “-”</span><input name="username" required minlength="2" maxlength="32" pattern="${USER_PATTERN}" title="2 to 32 letters, digits, '.', '_' or '-', starting with a letter or digit" autocomplete="username" autocapitalize="none" spellcheck="false"></label>
        <label>Your name <span class="hint">optional, shown to teammates</span><input name="display_name" maxlength="64" autocomplete="name"></label>
        <label>Password <span class="hint">10+ characters; a few words work well</span><input name="password" type="password" minlength="10" required autocomplete="new-password"></label>
        <label>Repeat password<input name="again" type="password" required autocomplete="new-password"></label>
        <button type="submit" class="primary">Create admin account</button><div class="cserr" aria-live="polite"></div></form></div>`;
    form($app, async d => {
      if (d.password !== d.again) throw fieldErr("again", "the passwords don't match");
      CS.session = await CS.app("POST", "setup", { setup_code: d.setup_code || null, username: d.username.trim(), display_name: d.display_name || "", password: d.password });
      CS.user = CS.session.user;
      location.hash = "#/setup/tba";
    });
  }
})();
