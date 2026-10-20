"use strict";
// admin.js: platform plugin -- accounts + roles, team settings, TBA key, worker options, followed events.
// Route #/admin[/users|settings|events|updates] (admin only). Uses CS.app / CS.form / CS.eventPicker from setup.js.
(function () {
  CS.nav({ label: "Admin", key: "admin", href: () => "#/admin", order: 95, global: true, group: "manage", icon: "admin", roles: ["admin"] });

  const when = ts => ts ? CS.fmtWhen(ts) : "never";
  const TABS = [["users", "Teammates"], ["settings", "Settings"], ["events", "Competitions"], ["updates", "Updates"]];
  const css = document.createElement("style");
  css.textContent = `
  .uacts { display: flex; flex-wrap: wrap; gap: 6px; } .uacts button { white-space: nowrap; }
  .rowerr:empty { display: none; } .rowerr { margin-top: 4px; white-space: normal; }
  .admtabs { flex-wrap: nowrap; overflow-x: auto; }
  .evacts .cmp-btn { min-height: 36px; }
  .wk { display: flex; gap: 10px; align-items: flex-start; margin: 0 0 var(--sp-3); padding: var(--sp-2) var(--sp-3); border-radius: var(--r-s); background: var(--surface-2); font-size: var(--fs-foot); }
  .wk.paused { background: var(--caution-tint); border: 1px solid var(--caution-line); } .wk b { color: var(--ink); }
  .wk .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ink-3); margin-top: 5px; flex: none; } .wk.ok .dot { background: var(--good); } .wk.paused .dot { background: var(--caution); }
  @media (pointer: coarse) { .uacts button, .evacts .cmp-btn, .evacts button { min-height: 44px; } }`;
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
    await ({ users, settings, events, updates })[tab](el);
  });

  // ---------------------------------------------------------------- users
  // The table is full width so Reset password / Disable / Delete show on a desktop; on a narrow screen they fold into the
  // row's "›" detail (app.js moves the real buttons there, so they keep working). One delegated handler per table.
  const MIN_ADMIN = 10, MIN_MEMBER = 4;   // server: PASSWORD_MIN / MEMBER_PASSWORD_MIN
  async function users(el, flash) {
    const [list, sec] = await Promise.all([CS.app("GET", "users"), CS.app("GET", "admin/security").catch(() => null)]);
    const me = CS.user.id, pol = sec ? sec.passwordless_login : null;        // never | lan | everywhere
    const nopwOk = pol === "everywhere" || pol === "lan";
    const nopwPill = u => u.has_password === false ? (nopwOk ? ` <span class="pill" title="signs in with just the username${pol === "lan" ? " (on the team network only)" : ""}">no password</span>`
      : ` <span class="pill failed" title="passwordless sign-in is off (Activity → Sign-in rules): this account can't sign in until it has a password">no password: can't sign in</span>`) : "";
    el.innerHTML = `<div class="card"><h2>Teammates ${CS.help(CS.ROLES.map(r => `<span class="hl"><b>${r}</b>: ${esc(CS.ROLE_TIP[r])}</span>`).join(""), "What each role can do")}</h2>
      <div class="tablewrap"><table class="utab"><thead><tr><th>User</th><th>Role</th><th data-pri="300">Last sign-in</th><th data-pri="850"><span class="vh">Actions</span></th></tr></thead><tbody>
      ${list.map(u => `<tr data-id="${u.id}" class="${u.disabled ? "dnp" : ""}"><td><b>${esc(u.username)}</b>${u.id === me ? ' <span class="pill">you</span>' : ""}${nopwPill(u)}<div class="muted small">${esc(u.display_name)}</div></td>
        <td><select data-role aria-label="Role of ${esc(u.username)}" ${u.id === me ? `disabled title="you can't change your own role"` : ""}>${CS.ROLES.map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}</select>${u.id === me ? `<div class="muted small" style="white-space:normal">You can't change your own role.</div>` : ""}<div class="cserr rowerr" role="alert"></div></td>
        <td class="small">${u.disabled ? '<span class="pill failed">disabled</span>' : esc(u.last_login ? CS.fmtWhen(u.last_login) : "never")}${u.active_sessions ? `<div class="muted small">${u.active_sessions} session${u.active_sessions > 1 ? "s" : ""}</div>` : ""}</td>
        <td><div class="uacts">${u.id === me ? `<button type="button" data-act="pw">Change my password</button>` : `<button type="button" data-act="reset" title="Set a new password (signs them out)">Reset password</button>
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
    if (pr.paused) return `<div class="wk paused"><span class="dot"></span><span><b>Worker: paused</b> (PAUSE_PROCESSING=1) · ${seen}. Nothing new is built or tracked; Video page jobs still run. ${CS.help(CS.PAUSE_HOW || "", "How to turn processing back on")}</span></div>`;
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
        cpu_only: d.gpu_mode === "" ? null : d.gpu_mode === "cpu", hubcam: d.hubcam === "" ? null : d.hubcam === "1" } });
      ok(f, "Saved.");
    });
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
        : st.state === "checking" ? "Checking for updates…" : st.state === "applying" ? (st.action === "rollback" ? "Rolling back…" : "Updating…") : "";
      body.innerHTML = `<div style="margin-bottom:8px">Version ${ver(cur)}${cur && cur.local ? ` <span class="pill" title="app/ on the server has changes that aren't in the published code">local changes</span>` : ""}
          ${cur && cur.subject ? `<div class="muted small">${esc(cur.subject)}</div>` : ""}</div>
        ${st.paused ? `<div class="note" style="margin:0 0 8px">Match processing is paused on the server (PAUSE_PROCESSING=1); it stays paused after an update.</div>` : ""}
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
