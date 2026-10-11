"use strict";
// setup.js: platform plugin -- sign-in, session + role-aware nav, first-run setup wizard, "my account".
// Talks to the API at app/v1/* (server/README.md). Exposes for other plugins:
//   CS.app(method, path, body?)  fetch app/v1/<path> with the session cookie + CSRF header; throws Error{status,data}
//   CS.user {id, username, display_name, role} | null      CS.can(role) -> bool (viewer < scout < strategist < admin)
//   CS.platform  true when the API is present (false = legacy static site: no sign-in, localStorage only)
//   CS.eventPicker(el, opts)  TBA event list (team's events first) with Follow buttons (admin)
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
  .csform { display: grid; gap: 12px; max-width: 420px; }
  .csform label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  .csform input, .csform select, .csin { background: var(--panel2); color: var(--text); border: 1px solid var(--line); border-radius: 6px;
    padding: 8px 10px; font: inherit; font-size: 14px; width: 100%; }
  .csform input:focus, .csin:focus { outline: none; border-color: var(--accent); }
  .csform .row { display: flex; gap: 8px; align-items: center; } .csform .row > * { flex: 1; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  button.danger { color: var(--bad); } button:disabled { opacity: .5; cursor: default; }
  .cserr { color: var(--bad); font-size: 13px; min-height: 1em; } .csok { color: var(--good); font-size: 13px; }
  .authcard { max-width: 460px; margin: 40px auto; }
  .steps { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 18px; }
  .steps a, .steps span { font-size: 12.5px; padding: 3px 10px; border-radius: 99px; border: 1px solid var(--line); color: var(--muted); }
  .steps .cur { border-color: var(--accent); color: var(--text); } .steps .done { color: var(--good); }
  #userbox { display: flex; gap: 10px; align-items: center; font-size: 13px; white-space: nowrap; }
  #userbox a { color: var(--muted); } #userbox a:hover { color: var(--text); }
  .evpick { display: grid; gap: 4px; max-height: 420px; overflow-y: auto; margin-top: 8px; }
  .evrow { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 8px 10px;
    background: var(--panel2); border-radius: 8px; font-size: 13.5px; }
  .evrow b { font-weight: 600; } .evrow .muted { font-size: 12px; }
  .code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--panel2); padding: 1px 6px; border-radius: 4px; }
  @media (max-width: 640px) { #userbox .who { display: none; } }`;
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
  CS.app = async function (method, path, body) {
    const opt = { method, credentials: "same-origin", cache: "no-store", headers: {} };
    if (method !== "GET") opt.headers["X-CSRF-Token"] = cookie("cs_csrf") || (CS.session && CS.session.csrf) || "";
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch("app/v1/" + path, opt);
    let d = null;
    if (r.status !== 204) { try { d = await r.json(); } catch (e) { } }
    if (!r.ok) {
      const err = new Error(errText(d, r.status)); err.status = r.status; err.data = d;
      if (r.status === 401 && CS.platform && CS.user) { CS.user = null; setTimeout(() => goLogin(), 0); }
      throw err;
    }
    return d;
  };
  CS.can = role => !CS.platform || (!!CS.user && ROLES.indexOf(CS.user.role) >= ROLES.indexOf(role));

  let loaded = false, apiDown = false;
  CS.refreshSession = async function () {
    apiDown = false;
    try {
      const r = await fetch("app/v1/session", { credentials: "same-origin", cache: "no-store" });
      const json = (r.headers.get("content-type") || "").includes("json");
      if (r.status === 404 || (r.ok && !json)) { CS.platform = false; CS.session = null; CS.user = null; }  // legacy static site / dev server
      else if (!r.ok) { apiDown = true; }
      else { CS.platform = true; CS.session = await r.json(); CS.user = CS.session.user; }
    } catch (e) { apiDown = true; }
    loaded = true;
    return CS.session;
  };
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
      $app.innerHTML = `<div class="empty">Can't reach the Cyber-Scout server right now. <button id="retry">Retry</button></div>`;
      document.getElementById("retry").onclick = () => { loaded = false; route(); };
      return;
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
      if (CS.platform && / 401$|: 401$/.test(e.message)) { await CS.refreshSession(); if (!CS.user) goLogin(); }
      throw e;
    }
  };

  function renderUserBox() {
    let el = document.getElementById("userbox");
    if (!el) {
      el = document.createElement("span"); el.id = "userbox";
      const top = document.querySelector("header.top"); if (!top) return;
      top.appendChild(el);
    }
    if (!CS.platform || !CS.user) { el.innerHTML = ""; return; }
    const u = CS.user;
    el.innerHTML = `<a href="#/account" class="who" title="${esc(ROLE_TIP[u.role])}">${esc(u.display_name || u.username)} · ${esc(u.role)}</a>
      <a href="#" id="signout">Sign out</a>`;
    el.querySelector("#signout").onclick = async e => {
      e.preventDefault();
      try { await CS.app("POST", "auth/logout"); } catch (x) { }
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
    const first = f.querySelector("input:not([type=hidden])"); if (first) first.focus();
  }
  CS.form = form;
  CS.genPassword = () => {
    const a = "abcdefghjkmnpqrstuvwxyz23456789", b = new Uint32Array(14); crypto.getRandomValues(b);
    return Array.from(b, x => a[x % a.length]).join("").replace(/(.{4})(?=.)/g, "$1-");
  };

  // ---------------------------------------------------------------- login
  CS.globalRoute("login", async () => {
    crumbs([["Sign in"]]); $updated.textContent = "";
    $app.innerHTML = `<div class="card authcard"><h1>Sign in</h1><div class="sub">Cyber-Scout. Ask your team's admin for an account.</div>
      <form class="csform" autocomplete="on">
        <label>Username<input name="username" autocomplete="username" required maxlength="32" autocapitalize="none" spellcheck="false"></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" placeholder="blank if you don't have one"></label>
        <button type="submit" class="primary">Sign in</button><div class="cserr"></div></form></div>`;
    form($app, async d => {
      CS.session = await CS.app("POST", "auth/login", { username: d.username.trim(), password: d.password || "" });
      CS.user = CS.session.user;
      afterAuth();
    });
  });

  // ---------------------------------------------------------------- my account
  CS.globalRoute("account", async () => {
    crumbs([["My account"]]); $updated.textContent = "";
    if (!CS.user) { goLogin(); return; }
    const u = CS.user;
    $app.innerHTML = `<h1>${esc(u.display_name || u.username)}</h1><div class="sub">Signed in as <b>${esc(u.username)}</b> · role <b>${esc(u.role)}</b> (${esc(ROLE_TIP[u.role])})</div>
      <div class="card" style="max-width:480px"><h2>Change password</h2>
      <form class="csform"><label>Current password<input name="current_password" type="password" autocomplete="current-password" placeholder="blank if you don't have one"></label>
        <label>New password${u.role === "admin" ? " (10+ characters)" : " (optional: 4+ characters, blank = none)"}<input name="new_password" type="password" autocomplete="new-password" ${u.role === "admin" ? 'minlength="10" required' : 'minlength="4"'}></label>
        <label>Repeat new password<input name="again" type="password" autocomplete="new-password" required></label>
        <button type="submit" class="primary">Change password</button><div class="cserr"></div><div class="csok"></div></form>
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
      } catch (e) { el.innerHTML = `<div class="cserr">${esc(e.message)}</div>`; return; }
      draw();
    }
    const row = e => `<div class="evrow"><div><b>${esc(e.name || e.key)}</b><div class="muted">${esc(e.key)} · ${esc(e.start_date || "")}
        ${e.end_date && e.end_date !== e.start_date ? "– " + esc(e.end_date) : ""} · ${esc([e.city, e.state_prov, e.country].filter(Boolean).join(", "))}${e.district ? " · " + esc(e.district.toUpperCase()) : ""}</div></div>
        ${followed.has(e.key) ? `<span><span class="pill done">following</span> <button data-u="${esc(e.key)}" title="stop following (its data is kept)">Unfollow</button></span>` : `<button data-k="${esc(e.key)}">Follow</button>`}</div>`;
    function draw() {
      const needle = q.trim().toLowerCase();
      const match = e => !needle || [e.key, e.name, e.city, e.state_prov, e.country, e.district].some(v => (v || "").toLowerCase().includes(needle));
      const mine = data.team_events.filter(match), all = data.events.filter(match);
      el.innerHTML = `<div class="controls" style="margin-top:0"><select id="evyear">${[thisYear + 1, thisYear, thisYear - 1, thisYear - 2].map(y => `<option ${y === year ? "selected" : ""}>${y}</option>`).join("")}</select>
          <input class="csin" id="evq" placeholder="Search name, city, key…" value="${esc(q)}" style="flex:1;min-width:180px"></div>
        ${opts.showFollowed !== false && followed.size ? `<h3>Following</h3><div class="evpick">${[...followed].map(k => row(data.events.concat(data.team_events).find(e => e.key === k) || { key: k })).join("")}</div>` : ""}
        ${data.team_number ? `<h3>Team ${esc(data.team_number)}'s events</h3>${mine.length ? `<div class="evpick">${mine.map(row).join("")}</div>` : `<div class="muted small">None registered for ${year}${needle ? " matching the search" : ""}.</div>`}` : ""}
        <h3>All ${year} events</h3><div class="evpick">${all.slice(0, 80).map(row).join("") || `<div class="muted small">No events.</div>`}</div>
        ${all.length > 80 ? `<div class="note">Showing 80 of ${all.length}; search to narrow down.</div>` : ""}
        <div class="controls"><input class="csin" id="evkey" placeholder="or type an event key, e.g. 2026wasam" style="max-width:260px"><button id="evadd">Follow</button></div>
        <div class="cserr"></div>`;
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
      $app.innerHTML = head + `<div class="card" style="max-width:620px"><h2>The Blue Alliance API key</h2>
        <div class="note" style="margin:0 0 12px">Cyber-Scout gets schedules, results and match videos from The Blue Alliance. Sign in at
        <a href="https://www.thebluealliance.com/account" target="_blank" rel="noopener">thebluealliance.com/account</a>, add a <b>Read API Key</b>
        (any description) and paste it here. It is checked with TBA and stored only on this server; browsers never see it again.</div>
        ${st.s.tba_key_set ? `<div class="csok" style="margin-bottom:10px">A key is saved (${esc(st.s.tba_key_hint || "")}). Paste a new one to replace it.</div>` : ""}
        <form class="csform"><label>Read API key<input name="key" autocomplete="off" spellcheck="false" required minlength="20"></label>
        <div class="row"><button type="submit" class="primary">Check and save</button>${st.s.tba_key_set ? `<button type="button" id="skip">Keep current key</button>` : ""}</div>
        <div class="cserr"></div></form></div>`;
      form($app, async d => { await CS.app("PUT", "settings/tba-key", { key: d.key.trim() }); next(); });
      const sk = $app.querySelector("#skip"); if (sk) sk.onclick = next;
    } else if (step === "team") {
      $app.innerHTML = head + `<div class="card" style="max-width:620px"><h2>Your team</h2>
        <div class="note" style="margin:0 0 12px">Used to put your matches, your events and your season goal first.</div>
        <form class="csform"><label>FRC team number<input name="team" type="number" min="1" max="99999" required value="${esc(st.s.team_number || "")}" inputmode="numeric"></label>
        <button type="submit" class="primary">Save</button><div class="cserr"></div></form>
        ${st.s.team_name ? `<div class="note">Saved: team ${esc(st.s.team_number)} <b>${esc(st.s.team_name)}</b>.</div>` : ""}</div>`;
      form($app, async d => { await CS.app("PATCH", "settings", { team_number: +d.team }); next(); });
    } else if (step === "goal") {
      const cur = st.s.season_goal || "", known = GOALS.some(g => g[0] === cur);
      $app.innerHTML = head + `<div class="card" style="max-width:620px"><h2>Season goal</h2>
        <div class="note" style="margin:0 0 12px">The Rankings &amp; goals page tracks how close you are to this (points still needed, odds, what each match is worth).</div>
        <form class="csform">${GOALS.map(([v, t]) => `<label style="display:flex;gap:8px;align-items:center;color:var(--text)"><input type="radio" name="goal" value="${esc(v)}" style="width:auto" ${cur === v ? "checked" : ""}> ${esc(t)}</label>`).join("")}
          <label style="display:flex;gap:8px;align-items:center;color:var(--text)"><input type="radio" name="goal" value="__other" style="width:auto" ${cur && !known ? "checked" : ""}> Other:
            <input name="other" maxlength="40" value="${known ? "" : esc(cur)}" style="flex:1"></label>
          <button type="submit" class="primary">Save</button><div class="cserr"></div></form></div>`;
      form($app, async d => {
        const g = d.goal === "__other" ? (d.other || "").trim() : d.goal;
        if (!g) throw new Error("pick a goal");
        await CS.app("PATCH", "settings", { season_goal: g }); next();
      });
    } else if (step === "event") {
      $app.innerHTML = head + `<div class="card"><h2>Which event(s) should Cyber-Scout follow?</h2>
        <div class="note" style="margin:0 0 4px">It processes every match of a followed event as soon as the video is up. You can follow several and change this later in Admin.</div>
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
        <div class="tablewrap"><table><thead><tr><th>Event</th><th>Sources</th><th>Layout</th><th></th></tr></thead><tbody>
        ${st.evs.map((e, i) => { const S = srcs[i];
          return `<tr><td><b>${esc(e.name || e.key)}</b><div class="muted small">${esc(e.key)}</div></td>
            <td>${S == null ? '<span class="muted">not available yet</span>' : S.length ? S.map(x => esc(x.label || x.kind || x.id)).join(", ") : '<span class="muted">none yet</span>'}</td>
            <td>${S && S.some(x => x.has_layout) ? "✓ boxes drawn" : '<span class="muted">not drawn</span>'}</td>
            <td><a href="#/${esc(e.key)}/sources"><b>Set up video →</b></a></td></tr>`; }).join("")}
        </tbody></table></div>
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
        <button type="submit" class="primary">Create admin account</button><div class="cserr"></div></form></div>`;
    form($app, async d => {
      if (d.password !== d.again) throw new Error("the passwords don't match");
      CS.session = await CS.app("POST", "setup", { setup_code: d.setup_code || null, username: d.username.trim(), display_name: d.display_name || "", password: d.password });
      CS.user = CS.session.user;
      location.hash = "#/setup/tba";
    });
  }
})();
