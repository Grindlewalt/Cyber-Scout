"use strict";
// audit.js: platform plugin -- admin "Activity" page (#/activity: audit log with filters + revert, safety switches:
// lockdown, passwordless sign-in policy, scout identity-fix review, sign everyone out) and the strategist
// "Identity fixes" queue (#/fixes: approve / reject scouts' robot->team fixes). Everything user-written is escaped.
(function () {
  CS.nav({ label: "Activity", key: "activity", href: () => "#/activity", order: 96, global: true, group: "manage", icon: "specs", roles: ["admin"] });
  CS.nav({ label: "Identity fixes", key: "fixes", href: () => "#/fixes", order: 94, global: true, group: "manage", icon: "check", minRole: "strategist" });

  const when = ts => ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" }) : "";
  const short = v => { if (v == null) return "—"; const s = JSON.stringify(v); return s.length > 140 ? s.slice(0, 140) + "…" : s; };
  const POLICY = [["lan", "Only on the team network (LAN / Tailscale)"], ["never", "Never: every account needs a password"], ["everywhere", "Everywhere, also through the internet link"]];
  const FAMILIES = [["", "everything"], ["picklist", "picklists"], ["note", "notes"], ["override", "identity fixes"], ["settings", "settings"],
    ["events", "events"], ["users", "accounts"], ["auth", "sign-ins"], ["sessions", "sign-outs"], ["http", "other"]];

  // ---------------------------------------------------------------- admin: activity + switches
  CS.globalRoute("activity", async () => {
    crumbs([["Activity", "#/activity"]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">No Cyber-Scout server here (static site).</div>`; return; }
    if (!CS.can("admin")) { $app.innerHTML = `<div class="empty">Admins only.</div>`; return; }
    const [sec, users] = await Promise.all([CS.app("GET", "admin/security"), CS.app("GET", "users")]);
    const lk = sec.lockdown;
    $app.innerHTML = `<h1>Activity</h1><div class="sub">Every change anyone made, and the switches for when something goes wrong.</div>
      <div class="grid2" style="align-items:start">
        <div class="card"><h2>Lockdown ${CS.help("Read-only mode: nobody but admins can change anything (picklists, notes, identity fixes). Use it during alliance selection or if someone starts messing around.", "About lockdown")}</h2>
          <div class="note" style="margin:0 0 10px">${lk ? `<b>On</b> since ${esc(when(lk.at))} (${esc(lk.by ? lk.by.display_name : "?")}).` : "Off: teammates can edit as their role allows."}</div>
          <button type="button" id="lockbtn" class="${lk ? "primary" : "danger"}">${lk ? "End lockdown" : "Lock the site (read-only)"}</button>
          <h2 style="margin-top:18px">Sign everyone out</h2>
          <label class="small"><input type="checkbox" id="incadm"> also the other admins</label>
          <div style="margin-top:6px"><button type="button" id="revokebtn" class="danger">Sign everyone out now</button></div>
          <div class="cserr" aria-live="polite" id="swerr"></div><div class="csok" role="status" aria-live="polite" id="swok"></div></div>
        <div class="card"><h2>Sign-in rules</h2><form class="csform" id="fsec">
          <label>Accounts without a password can sign in<select name="passwordless_login">${POLICY.map(([k, t]) => `<option value="${k}" ${k === sec.passwordless_login ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
          <label>Scouts' robot identity fixes<select name="scout_overrides"><option value="review" ${sec.scout_overrides === "review" ? "selected" : ""}>wait for a strategist to approve</option>
            <option value="direct" ${sec.scout_overrides === "direct" ? "selected" : ""}>apply right away</option></select></label>
          <button type="submit" class="primary">Save</button><div class="cserr" aria-live="polite"></div><div class="csok" role="status" aria-live="polite"></div></form></div>
      </div>
      <div class="card" style="margin-top:16px"><h2>Change log ${CS.help("Undo puts back what was there before that change (it is logged too, and can itself be undone). “Undo all since” reverts every change that account made from that moment on: use it when a login leaked.", "About undo")}</h2>
        <div class="row" style="flex-wrap:wrap;gap:8px;margin-bottom:10px">
          <label class="small">Who <select id="fuser"><option value="">anyone</option>${users.map(u => `<option value="${u.id}">${esc(u.display_name || u.username)} (${esc(u.username)})</option>`).join("")}</select></label>
          <label class="small">What <select id="ffam">${FAMILIES.map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("")}</select></label>
          <label class="small">From <select id="fvia"><option value="">anywhere</option><option value="lan">team network</option><option value="public">internet</option></select></label>
        </div>
        <div class="tablewrap"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th data-pri="300">Change</th><th data-pri="500">From</th><th><span class="vh">Undo</span></th></tr></thead><tbody id="alog"></tbody></table></div>
        <div style="margin-top:8px"><button type="button" id="amore" hidden>Older…</button></div>
        <div class="cserr" aria-live="polite" id="aerr"></div><div class="csok" role="status" aria-live="polite" id="aok"></div></div>`;

    const msg = (id, t) => { const e = document.getElementById(id); if (e) e.textContent = t; };
    document.getElementById("lockbtn").onclick = async () => {
      if (!lk && !confirm("Lock the site? Only admins will be able to change anything until you end the lockdown.")) return;
      try { await CS.app("PATCH", "admin/security", { lockdown: !lk }); route(false); } catch (e) { msg("swerr", e.message); }
    };
    document.getElementById("revokebtn").onclick = async () => {
      const inc = document.getElementById("incadm").checked;
      if (!confirm(`Sign out everyone${inc ? " (including other admins)" : " except admins"}? You stay signed in.`)) return;
      try { const r = await CS.app("POST", "admin/sessions/revoke", { include_admins: inc }); msg("swok", `Signed out ${r.signed_out} session${r.signed_out === 1 ? "" : "s"}.`); }
      catch (e) { msg("swerr", e.message); }
    };
    const fs = document.getElementById("fsec").parentElement;
    CS.form(fs, async (d, f) => {
      await CS.app("PATCH", "admin/security", { passwordless_login: d.passwordless_login, scout_overrides: d.scout_overrides });
      f.querySelector(".csok").textContent = "Saved.";
    });
    window.scrollTo(0, 0);

    const tb = document.getElementById("alog"), more = document.getElementById("amore");
    let before = null, rows = [];
    const who = u => u && (u.display_name || u.username) ? `${esc(u.display_name || u.username)}<div class="muted small">${esc(u.username || "")}${u.role ? " · " + esc(u.role) : ""}</div>` : `<span class="muted">—</span>`;
    const row = e => `<tr data-id="${e.id}"><td class="small" style="white-space:nowrap">${esc(when(e.at))}</td><td>${who(e.user)}</td>
      <td><span class="code">${esc(e.action)}</span><div class="muted small">${esc(e.target || "")}</div></td>
      <td class="small"><span class="muted">before</span> ${esc(short(e.before))}<br><span class="muted">after</span> ${esc(short(e.after))}</td>
      <td class="small" title="${esc(e.ua || "")}">${e.via === "public" ? '<span class="pill failed">internet</span>' : '<span class="pill">team net</span>'}<div class="muted small">${esc(e.ip || "")}</div></td>
      <td style="white-space:nowrap">${e.reverted_by ? `<span class="muted small">undone (#${e.reverted_by})</span>` : e.revertible ? `<button type="button" data-undo="${e.id}">Undo</button>` : ""}
        ${e.user && e.user.id ? `<button type="button" data-undoall="${e.id}" title="undo every change this account made from this moment on">Undo all since</button>` : ""}</td></tr>`;
    async function load(reset) {
      if (reset) { before = null; rows = []; tb.innerHTML = ""; }
      const q = new URLSearchParams({ limit: "100" });
      const u = document.getElementById("fuser").value, f = document.getElementById("ffam").value, v = document.getElementById("fvia").value;
      if (u) q.set("user_id", u); if (f) q.set("action", f); if (v) q.set("via", v); if (before) q.set("before_id", before);
      try {
        const r = await CS.app("GET", "audit?" + q);
        rows = rows.concat(r.entries); before = r.next_before_id; more.hidden = !before;
        tb.insertAdjacentHTML("beforeend", r.entries.map(row).join("") || (reset ? `<tr><td colspan="6" class="muted">Nothing yet.</td></tr>` : ""));
      } catch (e) { msg("aerr", e.message); }
    }
    ["fuser", "ffam", "fvia"].forEach(id => { document.getElementById(id).onchange = () => load(true); });
    more.onclick = () => load(false);
    tb.onclick = async ev => {
      const b = ev.target.closest("button"); if (!b) return;
      const e = rows.find(x => String(x.id) === (b.dataset.undo || b.dataset.undoall)); if (!e) return;
      msg("aerr", ""); msg("aok", "");
      try {
        if (b.dataset.undo) {
          if (!confirm(`Undo ${e.action} on ${e.target || "?"}? What was there before comes back.`)) return;
          await CS.app("POST", `audit/${e.id}/revert`); msg("aok", "Undone.");
        } else {
          const name = e.user.display_name || e.user.username;
          if (!confirm(`Undo EVERY change ${name} made since ${when(e.at)}? (Consider disabling the account first.)`)) return;
          const r = await CS.app("POST", "audit/revert-user", { user_id: e.user.id, since: e.at });
          msg("aok", `Undid ${r.reverted.length} change${r.reverted.length === 1 ? "" : "s"}${r.skipped.length ? ` (${r.skipped.length} not undoable or already undone)` : ""}.`);
        }
        await load(true);
      } catch (x) { msg("aerr", x.message); }
    };
    await load(true);
  });

  // ---------------------------------------------------------------- strategist+: identity fixes waiting for approval
  CS.globalRoute("fixes", async () => {
    crumbs([["Identity fixes", "#/fixes"]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">No Cyber-Scout server here (static site).</div>`; return; }
    if (!CS.can("strategist")) { $app.innerHTML = `<div class="empty">Strategists and admins only.</div>`; return; }
    const evs = await CS.app("GET", "events");
    const data = await Promise.all(evs.map(e => CS.app("GET", `events/${encodeURIComponent(e.key)}/overrides`).then(o => ({ e, o })).catch(() => ({ e, o: null }))));
    const side = d => ["red", "blue"].filter(a => d && d[a] && Object.keys(d[a]).length)
      .map(a => `${a}: ` + Object.entries(d[a]).map(([i, t]) => `#${esc(i)}→${esc(String(t).replace(/^frc/, ""))}`).join(", ")).join("; ") || "(no fixes)";
    const pend = data.flatMap(({ e, o }) => (o && o.pending || []).map(p => ({ ev: e.key, evname: e.name || e.key, p })));
    $app.innerHTML = `<h1>Identity fixes</h1><div class="sub">Scouts' robot → team corrections wait here until a strategist or admin approves them; approved fixes are used on the worker's next pass.</div>
      <div class="card">${pend.length ? `<div class="tablewrap"><table><thead><tr><th>Match</th><th>Requested</th><th data-pri="300">Now</th><th>By</th><th><span class="vh">Decide</span></th></tr></thead><tbody>
        ${pend.map(({ ev, evname, p }) => `<tr data-ev="${esc(ev)}" data-id="${p.id}"><td><a href="#/${esc(ev)}/m/${esc(p.match)}">${esc(p.match.split("_").pop())}</a><div class="muted small">${esc(evname)}</div></td>
          <td>${side(p.data)}</td><td class="small">${side(p.current)}${p.stale ? ' <span class="pill failed" title="changed since this was requested">changed</span>' : ""}</td>
          <td>${esc(p.by ? p.by.display_name : "?")}<div class="muted small">${esc(when(p.at))}</div></td>
          <td style="white-space:nowrap"><button type="button" class="primary" data-act="approve">Approve</button> <button type="button" data-act="reject">Reject</button></td></tr>`).join("")}
        </tbody></table></div>` : `<div class="muted">Nothing waiting.</div>`}<div class="cserr" aria-live="polite"></div></div>`;
    $app.querySelectorAll("button[data-act]").forEach(b => b.onclick = async () => {
      const tr = b.closest("tr");
      try { await CS.app("POST", `events/${encodeURIComponent(tr.dataset.ev)}/override-requests/${tr.dataset.id}/${b.dataset.act}`); route(false); }
      catch (e) { $app.querySelector(".cserr").textContent = e.message; }
    });
  });
})();
