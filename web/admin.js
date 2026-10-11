"use strict";
// admin.js: platform plugin -- accounts + roles, team settings, TBA key, worker options, followed events.
// Route #/admin[/users|settings|events] (admin only). Uses CS.app / CS.form / CS.eventPicker from setup.js.
(function () {
  CS.nav({ label: "Admin", href: () => "#/admin", order: 95, global: true, roles: ["admin"] });

  const when = ts => ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "never";
  const TABS = [["users", "Teammates"], ["settings", "Settings"], ["events", "Events"]];

  CS.globalRoute("admin", async tab => {
    crumbs([["Admin", "#/admin"]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">No Cyber-Scout server here (static site): nothing to administer.</div>`; return; }
    if (!CS.can("admin")) { $app.innerHTML = `<div class="empty">Admins only.</div>`; return; }
    tab = TABS.some(t => t[0] === tab) ? tab : "users";
    $app.innerHTML = `<h1>Admin</h1><div class="sub">Accounts, settings and the events Cyber-Scout follows. <a href="#/setup">Re-run the setup wizard</a></div>
      <div class="tabs">${TABS.map(([k, t]) => `<a class="tab ${k === tab ? "on" : ""}" href="#/admin/${k}">${t}</a>`).join("")}</div><div id="adm"></div>`;
    const el = document.getElementById("adm");
    await ({ users, settings, events })[tab](el);
  });

  // ---------------------------------------------------------------- users
  async function users(el) {
    const list = await CS.app("GET", "users");
    const me = CS.user.id;
    el.innerHTML = `<div class="grid2" style="align-items:start">
      <div class="card"><h2>Teammates</h2><div class="tablewrap"><table><thead><tr><th>User</th><th>Role</th><th>Last sign-in</th><th></th></tr></thead><tbody>
      ${list.map(u => `<tr data-id="${u.id}" class="${u.disabled ? "dnp" : ""}"><td><b>${esc(u.username)}</b>${u.id === me ? ' <span class="pill">you</span>' : ""}<div class="muted small">${esc(u.display_name)}</div></td>
        <td><select data-role ${u.id === me ? "disabled" : ""}>${CS.ROLES.map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
        <td class="small">${u.disabled ? '<span class="pill failed">disabled</span>' : when(u.last_login)}${u.active_sessions ? `<div class="muted small">${u.active_sessions} session${u.active_sessions > 1 ? "s" : ""}</div>` : ""}</td>
        <td style="white-space:nowrap">${u.id === me ? `<a href="#/account" class="small">change password</a>` : `<button data-act="reset" title="set a new password (signs them out)">Reset password</button>
          <button data-act="toggle">${u.disabled ? "Enable" : "Disable"}</button> <button data-act="delete" class="danger" title="delete account (their notes stay)">✕</button>`}</td></tr>`).join("")}
      </tbody></table></div><div class="cserr" id="uerr"></div><div class="csok" id="uok"></div>
      <div class="note">${CS.ROLES.map(r => `<b>${r}</b>: ${esc(CS.ROLE_TIP[r])}`).join("<br>")}</div></div>
      <div class="card"><h2>Add a teammate</h2>
        <form class="csform"><label>Username<input name="username" required minlength="2" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9._-]+" autocapitalize="none" spellcheck="false" autocomplete="off"></label>
          <label>Name<input name="display_name" maxlength="64" autocomplete="off"></label>
          <label>Role<select name="role">${CS.ROLES.map(r => `<option value="${r}" ${r === "scout" ? "selected" : ""}>${r}: ${esc(CS.ROLE_TIP[r])}</option>`).join("")}</select></label>
          <label>Temporary password<div class="row"><input name="password" required minlength="10" autocomplete="new-password" spellcheck="false"><button type="button" id="gen" style="flex:none">Generate</button></div></label>
          <button type="submit" class="primary">Create account</button><div class="cserr"></div><div class="csok"></div></form>
        <div class="note">Share the username + password with them in person; they can change the password under their name (top right).</div></div></div>`;
    const f = el.querySelectorAll(".card")[1];
    f.querySelector("#gen").onclick = () => { f.querySelector("[name=password]").value = CS.genPassword(); };
    CS.form(f, async (d, fm) => {
      const u = await CS.app("POST", "users", { username: d.username.trim(), display_name: d.display_name || "", role: d.role, password: d.password });
      await users(el);
      el.querySelector("#uok").textContent = `Created ${u.username} (${u.role}). Password: ${d.password}`;
    });
    const msg = (t, ok) => { el.querySelector("#uerr").textContent = ok ? "" : t; el.querySelector("#uok").textContent = ok ? t : ""; };
    el.querySelectorAll("tr[data-id]").forEach(tr => {
      const id = +tr.dataset.id, u = list.find(x => x.id === id);
      const sel = tr.querySelector("[data-role]");
      sel.onchange = async () => {
        try { await CS.app("PATCH", "users/" + id, { role: sel.value }); msg(`${u.username} is now ${sel.value}.`, true); }
        catch (e) { sel.value = u.role; msg(e.message); }
      };
      tr.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
        try {
          if (b.dataset.act === "toggle") { await CS.app("PATCH", "users/" + id, { disabled: !u.disabled }); await users(el); }
          else if (b.dataset.act === "delete") {
            if (!confirm(`Delete ${u.username}? Their notes are kept. (Disable instead to keep the account.)`)) return;
            await CS.app("DELETE", "users/" + id); await users(el);
          } else if (b.dataset.act === "reset") {
            const pw = prompt(`New password for ${u.username} (10+ characters). They'll be signed out.`, CS.genPassword());
            if (!pw) return;
            await CS.app("PATCH", "users/" + id, { password: pw }); await users(el); msg(`New password for ${u.username}: ${pw}`, true);
          }
        } catch (e) { msg(e.message); }
      });
    });
  }

  // ---------------------------------------------------------------- settings
  async function settings(el) {
    const s = await CS.app("GET", "settings"), o = s.options || {};
    const tri = (name, v, on, off) => `<select name="${name}"><option value="" ${v == null ? "selected" : ""}>default (from .env)</option>
      <option value="1" ${v === true ? "selected" : ""}>${on}</option><option value="0" ${v === false ? "selected" : ""}>${off}</option></select>`;
    el.innerHTML = `<div class="grid2" style="align-items:start"><div class="stack">
      <div class="card"><h2>Team</h2><form class="csform" id="fteam">
        <label>FRC team number<input name="team_number" type="number" min="1" max="99999" value="${esc(s.team_number || "")}"></label>
        ${s.team_name ? `<div class="muted small">${esc(s.team_name)}</div>` : ""}
        <label>Season goal<input name="season_goal" maxlength="40" list="goals" value="${esc(s.season_goal || "")}"></label>
        <datalist id="goals">${CS.GOALS.map(g => `<option value="${esc(g[0])}">${esc(g[1])}</option>`).join("")}</datalist>
        <button type="submit" class="primary">Save</button><div class="cserr"></div><div class="csok"></div></form></div>
      <div class="card"><h2>The Blue Alliance key</h2>
        <div class="note" style="margin:0 0 10px">${s.tba_key_set ? `Saved: <span class="code">${esc(s.tba_key_hint)}</span>. ` : "<b>Not set: nothing gets processed.</b> "}
          Keys are made at <a href="https://www.thebluealliance.com/account" target="_blank" rel="noopener">thebluealliance.com/account</a> (Read API Keys). Stored on the server only.</div>
        <form class="csform" id="fkey"><label>${s.tba_key_set ? "Replace with" : "Key"}<input name="key" autocomplete="off" spellcheck="false" required minlength="20"></label>
          <button type="submit" class="primary">Check and save</button><div class="cserr"></div><div class="csok"></div></form></div></div>
      <div class="card"><h2>Processing</h2><form class="csform" id="fopt">
        <label>Check TBA for new matches every (seconds)<input name="poll_seconds" type="number" min="30" max="3600" placeholder="default (120)" value="${o.poll_seconds ?? ""}"></label>
        <label>GPU<select name="gpu_mode"><option value="" ${o.cpu_only == null ? "selected" : ""}>default (from .env)</option>
          <option value="gpu" ${o.cpu_only === false ? "selected" : ""}>use the GPU</option><option value="cpu" ${o.cpu_only === true ? "selected" : ""}>CPU only (~4x slower)</option></select></label>
        <label>GPU device number (if there are several)<input name="gpu_device" type="number" min="0" max="15" placeholder="default (0)" value="${o.gpu_device ?? ""}"></label>
        <label>Driver-station cameras ("hub cams": camera switcher + shot checks, ~1-2 min extra per match)${tri("hubcam", o.hubcam, "on", "off")}</label>
        <button type="submit" class="primary">Save</button><div class="cserr"></div><div class="csok"></div></form>
        <div class="note">Applied by the worker on its next check for matches, no restart needed. A GPU device change applies from the next match.</div></div></div>`;
    const ok = (f, t) => { f.querySelector(".csok").textContent = t; setTimeout(() => { f.querySelector(".csok").textContent = ""; }, 4000); };
    const ft = el.querySelector("#fteam").parentElement, fk = el.querySelector("#fkey").parentElement, fo = el.querySelector("#fopt").parentElement;
    CS.form(ft, async (d, f) => {
      const r = await CS.app("PATCH", "settings", { team_number: d.team_number ? +d.team_number : null, season_goal: d.season_goal.trim() || null });
      ok(f, `Saved${r.team_name ? ": " + r.team_name : ""}.`);
    });
    CS.form(fk, async () => { await CS.app("PUT", "settings/tba-key", { key: fk.querySelector("[name=key]").value.trim() }); await settings(el); });
    CS.form(fo, async (d, f) => {
      const num = v => v === "" ? null : +v;
      await CS.app("PATCH", "settings", { options: { poll_seconds: num(d.poll_seconds), gpu_device: num(d.gpu_device),
        cpu_only: d.gpu_mode === "" ? null : d.gpu_mode === "cpu", hubcam: d.hubcam === "" ? null : d.hubcam === "1" } });
      ok(f, "Saved.");
    });
    // the generic CS.form focuses the first input; don't jump the page on the settings tab
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- events
  async function events(el) {
    const evs = await CS.app("GET", "events");
    el.innerHTML = `<div class="card"><h2>Followed events</h2>
      ${evs.length ? `<div class="evpick" style="max-height:none">${evs.map(e => `<div class="evrow"><div><b>${esc(e.name || e.key)}</b><div class="muted">${esc(e.key)} · ${esc(e.start_date || "")}</div></div>
        <div style="display:flex;gap:6px"><a href="#/${esc(e.key)}">open</a> · <a href="#/${esc(e.key)}/sources">video setup</a>
        <button data-k="${esc(e.key)}" class="danger" title="stop following (processed data is kept)">Stop following</button></div></div>`).join("")}</div>`
        : `<div class="muted">Not following any event: the worker is idle.</div>`}
      <div class="cserr"></div></div>
      <div class="card" style="margin-top:16px"><h2>Follow another event</h2><div id="picker"></div></div>`;
    el.querySelectorAll("button[data-k]").forEach(b => b.onclick = async () => {
      if (!confirm(`Stop following ${b.dataset.k}? Already processed matches are kept.`)) return;
      try { await CS.app("DELETE", "events/" + b.dataset.k); await events(el); }
      catch (e) { el.querySelector(".cserr").textContent = e.message; }
    });
    CS.eventPicker(el.querySelector("#picker"), { onFollow: () => events(el) });
  }
})();
