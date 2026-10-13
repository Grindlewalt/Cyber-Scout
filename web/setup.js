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
  .csform button.primary { min-height: 40px; font-size: var(--fs-body); }
  .cserr { color: var(--bad); font-size: var(--fs-foot); min-height: 1em; } .csok { color: var(--good); font-size: var(--fs-foot); }
  .authcard { max-width: 440px; margin: var(--sp-7) auto; padding: var(--sp-5); }
  .authcard h1 { margin-bottom: var(--sp-4); }
  .steps { display: flex; gap: var(--sp-1); flex-wrap: wrap; margin: var(--sp-1) 0 var(--sp-5); counter-reset: st; }
  .steps a, .steps span { font-size: var(--fs-foot); padding: var(--sp-1) 10px; border-radius: var(--r-s); border: 1px solid var(--line); color: var(--ink-2); text-decoration: none; }
  .steps .cur { border-color: var(--accent); background: var(--accent-tint); color: var(--ink); font-weight: 600; } .steps .done { color: var(--good); }
  .evpick { display: grid; gap: var(--sp-1); max-height: 420px; overflow-y: auto; margin-top: var(--sp-2); overscroll-behavior: contain; }
  .evrow { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--line); font-size: 13.5px; }
  .evrow b { font-weight: 600; } .evrow .muted { font-size: var(--fs-cap); }
  .code { font-family: var(--mono); background: var(--surface-2); padding: 1px 6px; border-radius: var(--r-tag); white-space: nowrap; }
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
      else { CS.platform = true; CS.session = await r.json(); CS.user = CS.session.user; if (sy) { if (CS.user) sy.rememberUser(CS.user); else sy.forget(); } }
    } catch (e) { apiDown = true; }
    const off = apiDown && sy && sy.offlineSession();
    if (off) { apiDown = false; CS.platform = true; CS.session = off; CS.user = off.user; if (sy.mode === "browser") sy.lost(); }
    loaded = true;
    return CS.session;
  };
  /** sign-in state changed outside the login form (client signed out, back online): check the session again and re-route */
  CS.reauth = () => { loaded = false; route(false); };
  CS.navAllowed = i => !CS.platform || ((!i.roles || (CS.user && i.roles.includes(CS.user.role))) && (!i.minRole || CS.can(i.minRole)));

  // ---------------------------------------------------------------- hooks into app.js (route / nav / api)
  const hashParts = () => location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  function goLogin() {
    const p = hashParts();
    if (p[0] !== "login" && p[0] !== "setup") { try { sessionStorage.setItem("cs:next", location.hash); } catch (e) { } }
    location.replace("#/login");
  }
  function afterAuth() {
    let next = "#/";
    try { next = sessionStorage.getItem("cs:next") || "#/"; sessionStorage.removeItem("cs:next"); } catch (e) { }
    if (/^#\/(login|setup)/.test(next)) next = "#/";
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

  function renderUserBox() {
    const el = document.getElementById("userbox"); if (!el) return;
    if (!CS.platform || !CS.user) { el.innerHTML = ""; return; }
    const u = CS.user;
    el.innerHTML = `<a href="#/account" class="who" title="${esc(ROLE_TIP[u.role])}">${esc(u.display_name || u.username)} · ${esc(u.role)}</a>
      <a href="#" id="signout">Sign out</a>`;
    el.querySelector("#signout").onclick = async e => {
      e.preventDefault();
      if (CS.sync && CS.sync.mode === "client") { try { await CS.sync.clientLogout(); } catch (x) { } }
      else { try { await CS.app("POST", "auth/logout"); } catch (x) { } if (CS.sync) CS.sync.signedOut(); }
      CS.user = null; loaded = false; location.hash = "#/login";
    };
  }

  // ---------------------------------------------------------------- helpers
  function form(el, onSubmit) {
    const f = el.querySelector("form"), err = el.querySelector(".cserr"), btn = f.querySelector("button[type=submit]");
    f.onsubmit = async e => {
      e.preventDefault(); err.textContent = ""; btn.disabled = true;
      try { await onSubmit(Object.fromEntries(new FormData(f).entries()), f); }
      catch (x) { err.textContent = x.message || String(x); }
      finally { btn.disabled = false; }
    };
    const first = f.querySelector("input:not([type=hidden])");
    if (first && f.closest(".authcard, [data-autofocus]")) first.focus();
  }
  CS.form = form;
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
        <button type="submit" class="primary">Sign in</button><div class="cserr" aria-live="polite"></div></form></div>`;
    form($app, async d => {
      if (cl) {
        await CS.sync.clientLogin(d.username.trim(), d.password || "");
        loaded = false; await CS.refreshSession();
        if (!CS.user) throw new Error(apiDown ? "Signed in, but the server stopped answering. Try again." : "Signed in, but the server didn't confirm it. Try again.");
        afterAuth(); return;
      }
      CS.session = await CS.app("POST", "auth/login", { username: d.username.trim(), password: d.password || "" });
      CS.user = CS.session.user; if (CS.sync) CS.sync.rememberUser(CS.user);
      afterAuth();
    });
  });

  // ---------------------------------------------------------------- my account
  CS.globalRoute("account", async () => {
    crumbs([["My account"]]); $updated.textContent = "";
    if (!CS.user) { goLogin(); return; }
    const u = CS.user;
    $app.innerHTML = `<h1>${esc(u.display_name || u.username)}</h1><div class="sub">Signed in as <b>${esc(u.username)}</b> · role <b>${esc(u.role)}</b> (${esc(ROLE_TIP[u.role])})</div>
      <div class="card acctcard" style="max-width:480px"><h2>Change password</h2>
      <form class="csform"><label>Current password<input name="current_password" type="password" autocomplete="current-password" placeholder="Leave blank if you don't have one"></label>
        <label>New password${u.role === "admin" ? " (10+ characters)" : " (optional: 4+ characters, blank = none)"}<input name="new_password" type="password" autocomplete="new-password" ${u.role === "admin" ? 'minlength="10" required' : 'minlength="4"'}></label>
        <label>Repeat new password<input name="again" type="password" autocomplete="new-password" required></label>
        <button type="submit" class="primary">Change password</button><div class="cserr" aria-live="polite"></div><div class="csok" role="status" aria-live="polite"></div></form>
      <div class="note">Changing your password signs you out everywhere else.</div></div>`;
    form($app, async (d, f) => {
      if (d.new_password !== d.again) throw new Error("the new passwords don't match");
      await CS.app("POST", "auth/password", { current_password: d.current_password || "", new_password: d.new_password || "" });
      f.reset(); $app.querySelector(".csok").textContent = "Password changed.";
    });
  });

  // ---------------------------------------------------------------- TBA event picker (setup + admin)
  CS.eventPicker = async function (el, opts = {}) {
    const thisYear = new Date().getFullYear();
    let year = opts.year || thisYear, q = "", data = null, followed = new Set();
    async function load() {
      el.innerHTML = `<div class="loading">Loading events from The Blue Alliance…</div>`;
      try {
        [data, followed] = await Promise.all([CS.app("GET", "tba/events?year=" + year),
          CS.app("GET", "events").then(l => new Set(l.map(e => e.key)))]);
      } catch (e) { el.innerHTML = `<div class="cserr" aria-live="polite">${esc(e.message)}</div>`; return; }
      draw();
    }
    const row = e => `<div class="evrow"><div><b>${esc(e.name || e.key)}</b><div class="muted">${esc(e.key)} · ${esc(e.start_date || "")}
        ${e.end_date && e.end_date !== e.start_date ? "– " + esc(e.end_date) : ""} · ${esc([e.city, e.state_prov, e.country].filter(Boolean).join(", "))}${e.district ? " · " + esc(e.district.toUpperCase()) : ""}</div></div>
        ${followed.has(e.key) ? opts.allowUnfollow === false ? `<span class="muted small">following</span>` : `<span><span class="pill done">following</span> <button data-u="${esc(e.key)}" title="stop following (its data is kept)">Unfollow</button></span>` : `<button data-k="${esc(e.key)}">Follow</button>`}</div>`;
    function draw() {
      const needle = q.trim().toLowerCase();
      const match = e => !needle || [e.key, e.name, e.city, e.state_prov, e.country, e.district].some(v => (v || "").toLowerCase().includes(needle));
      const mine = data.team_events.filter(match), all = data.events.filter(match);
      el.innerHTML = `<div class="controls evctl" style="margin-top:0"><select id="evyear" aria-label="Season">${[thisYear + 1, thisYear, thisYear - 1, thisYear - 2].map(y => `<option ${y === year ? "selected" : ""}>${y}</option>`).join("")}</select>
          <input class="csin" id="evq" placeholder="Search name, city, key…" aria-label="Search events" value="${esc(q)}" style="flex:1;min-width:180px"></div>
        ${opts.showFollowed !== false && followed.size ? `<h3>Following</h3><div class="evpick">${[...followed].map(k => row(data.events.concat(data.team_events).find(e => e.key === k) || { key: k })).join("")}</div>` : ""}
        ${data.team_number ? `<h3>Team ${esc(data.team_number)}'s events</h3>${mine.length ? `<div class="evpick">${mine.map(row).join("")}</div>` : `<div class="muted small">None registered for ${year}${needle ? " matching the search" : ""}.</div>`}` : ""}
        <h3>All ${year} events</h3><div class="evpick">${all.slice(0, 80).map(row).join("") || `<div class="muted small">No events.</div>`}</div>
        ${all.length > 80 ? `<div class="note">Showing 80 of ${all.length}; search to narrow down.</div>` : ""}
        <div class="controls evctl"><input class="csin" id="evkey" placeholder="Or an event key, e.g. 2026wasam…" aria-label="Event key" autocapitalize="none" spellcheck="false"><button id="evadd">Follow</button></div>
        <div class="cserr" aria-live="polite"></div>`;
      el.querySelector("#evyear").onchange = e => { year = +e.target.value; load(); };
      const qi = el.querySelector("#evq");
      qi.oninput = () => { q = qi.value; clearTimeout(draw.t); draw.t = setTimeout(() => { draw(); const n = el.querySelector("#evq"); n.focus(); n.setSelectionRange(q.length, q.length); }, 200); };
      el.querySelectorAll("button[data-k]").forEach(b => b.onclick = () => follow(b.dataset.k, b));
      el.querySelectorAll("button[data-u]").forEach(b => b.onclick = async () => {
        b.disabled = true;
        try { await CS.app("DELETE", "events/" + b.dataset.u); followed.delete(b.dataset.u); draw(); opts.onFollow && opts.onFollow(null); }
        catch (e) { b.disabled = false; el.querySelector(".cserr").textContent = e.message; }
      });
      el.querySelector("#evadd").onclick = e => follow(el.querySelector("#evkey").value.trim().toLowerCase(), e.target);
    }
    async function follow(key, btn) {
      if (!key) return;
      btn.disabled = true; el.querySelector(".cserr").textContent = "";
      try { const ev = await CS.app("POST", "events", { key }); followed.add(ev.key); draw(); opts.onFollow && opts.onFollow(ev); }
      catch (e) { btn.disabled = false; el.querySelector(".cserr").textContent = e.message; }
    }
    await load();
  };

  // ---------------------------------------------------------------- first-run wizard
  const STEPS = [["account", "Admin account"], ["tba", "TBA key"], ["team", "Your team"], ["goal", "Season goal"], ["event", "Event"], ["video", "Video"], ["done", "Done"]];
  const KEYS = STEPS.map(x => x[0]);

  // Until the admin finishes the wizard once (settings.setup_complete), it walks every step in order, even ones that
  // already have a value (e.g. a TBA key / event imported from an old .env), so nothing is silently auto-picked.
  async function wizardState(cur) {
    const [s, evs] = await Promise.all([CS.app("GET", "settings"), CS.app("GET", "events")]);
    const done = { account: true, tba: s.tba_key_set, team: !!s.team_number, goal: !!s.season_goal, event: evs.length > 0, video: false };
    const firstMissing = KEYS.find(k => k !== "done" && k !== "video" && !done[k]) || "done";
    let next = firstMissing;
    if (!s.setup_complete) {
      const after = cur ? KEYS[KEYS.indexOf(cur) + 1] : "tba";
      next = KEYS.indexOf(firstMissing) < KEYS.indexOf(after) ? firstMissing : after;
    }
    return { s, evs, done, next };
  }

  CS.globalRoute("setup", async step => {
    crumbs([["Setup"]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">This site runs without the Cyber-Scout server (no setup needed).</div>`; return; }
    if (CS.session.setup_required) return stepAccount();
    if (!CS.user) { goLogin(); return; }
    if (!CS.can("admin")) { $app.innerHTML = `<div class="empty">Only an admin can change the setup.</div>`; return; }
    const st = await wizardState();
    step = KEYS.includes(step) && step !== "account" ? step : st.next;
    const head = `<h1>Set up Cyber-Scout</h1><div class="steps">${STEPS.map(([k, t]) => {
      const cls = k === step ? "cur" : st.done[k] ? "done" : "";
      return k === "account" || k === step ? `<span class="${cls}">${st.done[k] && k !== step ? "✓ " : ""}${t}</span>` : `<a class="${cls}" href="#/setup/${k}">${st.done[k] ? "✓ " : ""}${t}</a>`;
    }).join("")}</div>`;
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
        catch (e) { if (e.status === 422 && Array.isArray(e.data && e.data.detail)) throw new Error("That doesn't look like a TBA Read API key: it is letters and digits only. Copy it again from thebluealliance.com/account."); throw e; }
        next();
      });
      const sk = $app.querySelector("#skip"); if (sk) sk.onclick = next;
    } else if (step === "team") {
      $app.innerHTML = head + `<div class="card" data-autofocus style="max-width:620px"><h2>Your team</h2>
        <div class="note" style="margin:0 0 12px">Used to put your matches, your events and your season goal first.</div>
        <form class="csform"><label>FRC team number<input name="team" type="number" min="1" max="99999" required value="${esc(st.s.team_number || "")}" inputmode="numeric" autocomplete="off"></label>
        <button type="submit" class="primary">Save</button><div class="cserr" aria-live="polite"></div></form>
        ${st.s.team_name ? `<div class="note">Saved: team ${esc(st.s.team_number)} <b>${esc(st.s.team_name)}</b>.</div>` : ""}</div>`;
      form($app, async d => { await CS.app("PATCH", "settings", { team_number: +d.team }); next(); });
    } else if (step === "goal") {
      const cur = st.s.season_goal || "", known = GOALS.some(g => g[0] === cur);
      $app.innerHTML = head + `<div class="card" style="max-width:620px"><h2>Season goal ${CS.help("The Rankings &amp; goals page tracks how close you are to this (points still needed, odds, what each match is worth).", "What the season goal is for")}</h2>
        <form class="csform opts" aria-label="Season goal">${GOALS.map(([v, t]) => `<label class="opt"><input type="radio" name="goal" value="${esc(v)}" ${cur === v ? "checked" : ""}> ${esc(t)}</label>`).join("")}
          <label class="opt"><input type="radio" name="goal" value="__other" ${cur && !known ? "checked" : ""}> Other:
            <input name="other" maxlength="40" value="${known ? "" : esc(cur)}" aria-label="Other goal"></label>
          <button type="submit" class="primary">Save</button><div class="cserr" aria-live="polite"></div></form></div>`;
      form($app, async d => {
        const g = d.goal === "__other" ? (d.other || "").trim() : d.goal;
        if (!g) throw new Error("pick a goal");
        await CS.app("PATCH", "settings", { season_goal: g }); next();
      });
      const oth = $app.querySelector("input[name=other]"); oth.oninput = () => { if (oth.value) $app.querySelector("input[value=__other]").checked = true; };
    } else if (step === "event") {
      $app.innerHTML = head + `<div class="card"><h2>Which event(s) should Cyber-Scout follow? ${CS.help("It processes every match of a followed event as soon as the video is up. You can follow several and change this later in Admin.", "What following an event does")}</h2>
        <div id="picker"></div><div class="controls"><button class="primary" id="cont" ${st.evs.length ? "" : "disabled"}>Continue</button></div></div>`;
      CS.eventPicker($app.querySelector("#picker"), { onFollow: () => CS.app("GET", "events").then(l => { $app.querySelector("#cont").disabled = !l.length; }) });
      $app.querySelector("#cont").onclick = next;
    } else if (step === "video") {
      const srcs = await Promise.all(st.evs.map(e => CS.app("GET", `events/${e.key}/sources`).then(d => d.sources || [], () => null)));
      $app.innerHTML = head + `<div class="card" style="max-width:760px"><h2>Match video</h2>
        <div class="note" style="margin:0 0 12px">For each event, tell Cyber-Scout where the video comes from: the livestream (found on TBA, or paste the
          YouTube link), any extra camera angles, and what kind each one is (<b>wide overhead</b>, <b>dynamic broadcast</b> that switches cameras,
          or <b>alliance-side</b> station cams). Then draw boxes on a sample frame (field view, red / blue alliance views, scoreboard) and
          calibrate the camera. Without this, matches can't be tracked.</div>
        <div class="vsl">${st.evs.map((e, i) => { const S = srcs[i];
          return `<div class="vsr"><div><b>${esc(e.name || e.key)}</b><div class="muted small">${esc(e.key)}</div></div>
            <div class="vsm"><span>${S == null ? "Video sources: not available yet" : S.length ? `${S.length} source${S.length === 1 ? "" : "s"}: ${S.map(x => esc(x.label || x.kind || x.id)).join(", ")}` : "No video sources yet"}</span>
              <span>${S && S.some(x => x.has_layout) ? `<span class="ok">${CS.icon("check", "ico")} boxes drawn</span>` : "Layout boxes not drawn"}</span></div>
            <a class="vsgo" href="#/${esc(e.key)}/sources">Set up video${CS.icon("chevR")}</a></div>`; }).join("") || `<div class="muted small" style="padding:12px 0">Follow an event first.</div>`}</div>
        <div class="controls"><button class="primary" id="cont">Continue</button> <span class="muted small">You can come back to this any time from the event's <b>Video</b> tab.</span></div></div>`;
      $app.querySelector("#cont").onclick = next;
    } else {
      const ev = st.evs[0];
      if (!st.s.setup_complete) await CS.app("PATCH", "settings", { setup_complete: true });
      CS.session = await CS.refreshSession();
      $app.innerHTML = head + `<div class="card" style="max-width:720px"><h2>All set</h2>
        <div class="note" style="margin:0 0 12px">Team ${esc(st.s.team_number || "?")}${st.s.team_name ? " " + esc(st.s.team_name) : ""} · goal: ${esc(st.s.season_goal || "none")} ·
          following ${st.evs.map(e => esc(e.name || e.key)).join(", ") || "no events"}.</div>
        <ol style="line-height:1.9;margin:0;padding-left:20px">
          ${st.evs.map(e => `<li><a href="#/${esc(e.key)}/sources"><b>Set up the video for ${esc(e.name || e.key)}</b></a>: the broadcast stream, which camera is which, and the field layout.</li>`).join("")}
          <li><a href="#/admin/users">Add teammates</a>: give each one their own login and a role.</li>
          ${ev ? `<li><a href="#/${esc(ev.key)}">Open ${esc(ev.name || ev.key)}</a>: matches show up as they're played and processed.</li>` : ""}
        </ol></div>`;
    }
  });

  async function stepAccount() {
    const needCode = CS.session.setup_code_required;
    $app.innerHTML = `<div class="card authcard" style="max-width:520px"><h1>Welcome to Cyber-Scout</h1>
      <div class="sub">First-time setup. Create the admin account: it manages teammates' logins, the TBA key, events and video.</div>
      <form class="csform">
        ${needCode ? `<label>Setup code<input name="setup_code" required autocomplete="off" spellcheck="false" placeholder="xxxx-xxxx-xxxx">
          <span class="small">Printed by the installer, or on the server: <span class="code">cat data/setup-code.txt</span> in the install folder.</span></label>` : ""}
        <label>Username<input name="username" required minlength="2" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9._-]+" autocomplete="username" autocapitalize="none" spellcheck="false"></label>
        <label>Your name<input name="display_name" maxlength="64" autocomplete="name"></label>
        <label>Password (10+ characters)<input name="password" type="password" minlength="10" required autocomplete="new-password"></label>
        <label>Repeat password<input name="again" type="password" required autocomplete="new-password"></label>
        <button type="submit" class="primary">Create admin account</button><div class="cserr" aria-live="polite"></div></form></div>`;
    form($app, async d => {
      if (d.password !== d.again) throw new Error("the passwords don't match");
      CS.session = await CS.app("POST", "setup", { setup_code: d.setup_code || null, username: d.username.trim(), display_name: d.display_name || "", password: d.password });
      CS.user = CS.session.user;
      location.hash = "#/setup/tba";
    });
  }
})();
