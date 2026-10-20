"use strict";
// audit.js: platform plugin -- admin "Activity" page (#/activity: audit log with filters + revert, safety switches:
// lockdown, passwordless sign-in policy, scout identity-fix review, sign everyone out) and the strategist
// "Identity fixes" queue (#/fixes: approve / reject scouts' robot->team fixes). Everything user-written is escaped.
(function () {
  CS.nav({ label: "Activity", key: "activity", href: () => "#/activity", order: 96, global: true, group: "manage", icon: "activity", roles: ["admin"] });
  // identity fixes belong to one competition: #/<ev>/fixes (the old #/fixes link opens the current competition's queue).
  // The nav item carries how many wait at the competition (checked at most once a minute).
  const PEND = {};
  function pendingBadge(ev) {
    const c = PEND[ev];
    if ((!c || Date.now() - c.at > 60000) && !(c && c.busy) && CS.can("strategist")) {
      PEND[ev] = Object.assign(c || { n: 0 }, { busy: true, at: Date.now() });
      CS.app("GET", `events/${encodeURIComponent(ev)}/overrides`).then(o => setPending(ev, (o.pending || []).length), () => { }).finally(() => { PEND[ev].busy = false; });
    }
    return c ? c.n : 0;
  }
  function setPending(ev, n) {
    const c = PEND[ev] = Object.assign(PEND[ev] || {}, { n, at: Date.now() });
    // update the nav in place (sidebar, tab bar, More sheet) instead of redrawing it
    document.querySelectorAll('[data-navkey="fixes"]').forEach(a => {
      if (!a.getAttribute("href").startsWith(`#/${ev}/`)) return;
      let b = a.querySelector(".nav-badge");
      if (!c.n) { if (b) b.remove(); a.title = "Identity fixes"; return; }
      if (!b) { b = document.createElement("span"); b.className = "nav-badge"; a.appendChild(b); }
      b.textContent = c.n; b.setAttribute("aria-label", `${c.n} waiting`); a.title = `Identity fixes (${c.n} waiting)`;
    });
  }
  CS.nav({ label: "Identity fixes", key: "fixes", href: ev => `#/${ev}/fixes`, order: 94, group: "manage", icon: "check", minRole: "strategist", badge: pendingBadge });

  const when = ts => ts ? CS.fmtWhen(ts, { sec: true }) : "";
  const css = document.createElement("style");
  css.textContent = `
  .aacts { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
  .amore { position: relative; } .amore > summary { list-style: none; cursor: pointer; min-width: 32px; min-height: 32px; display: grid; place-items: center;
    border: 1px solid var(--line-strong); border-radius: var(--r-s); color: var(--ink-2); font-weight: 700; }
  .amore > summary::-webkit-details-marker { display: none; }
  .amore[open] > summary { color: var(--ink); background: var(--surface-2); }
  .amore > button { position: absolute; right: 0; top: calc(100% + 4px); z-index: 5; white-space: nowrap; box-shadow: var(--shadow-pop); background: var(--surface); }
  .fxq a.mlink { display: inline-flex; align-items: center; gap: 4px; font-weight: 600; } .fxq a.mlink svg { width: 14px; height: 14px; }
  .fxq .dec { display: flex; gap: 6px; flex-wrap: wrap; }
  @media (pointer: coarse) { .aacts button, .amore > summary, .fxq .dec button, #lockbtn, #revokebtn { min-height: 44px; } }`;
  document.head.appendChild(css);
  const POLICY = [["never", "Off: every account needs a password"], ["everywhere", "On (testing only): anyone who knows a no-password username can sign in"]];
  const FAMILIES = [["changes", "changes (no sign-ins)"], ["", "everything"], ["picklist", "picklists"], ["note", "notes"], ["override", "identity fixes"], ["settings", "settings"],
    ["events", "competitions"], ["sources", "video setup"], ["users", "accounts"], ["auth", "sign-ins"], ["sessions", "sign-outs"], ["http", "other requests"]];
  // the log in sentences: "Sam saved the picklist", "Coach changed the season goal: — → DCMP"
  const ACT = {
    "auth.login": "signed in", "auth.login_failed": "tried to sign in (wrong password)", "auth.logout": "signed out", "auth.password": "changed their password",
    "auth.setup": "created the first admin account", "events.follow": "followed a competition", "events.unfollow": "unfollowed a competition",
    "note.create": "wrote a note", "note.edit": "edited a note", "note.delete": "deleted a note", "note.hide": "hid a note", "note.restore": "restored a note",
    "override.request": "asked for a robot identity fix", "override.reject": "rejected a robot identity fix", "override.withdraw": "withdrew a robot identity fix",
    "override.approve": "approved a robot identity fix", "override.set": "fixed a robot's team", "picklist.save": "saved the picklist",
    "sessions.revoke_all": "signed everyone out", "settings.security": "changed the safety switches", "settings.tba_key": "changed the TBA key",
    "settings.update": "changed the settings", "sources.auto_setup_accept": "accepted an automatic video set-up", "sources.dynamic": "changed a video's camera setup",
    "sources.video_mode": "changed the match-video mode", "users.create": "added a teammate", "users.delete": "deleted a teammate",
    "users.profile": "changed their display name", "users.update": "changed a teammate's account",
  };
  const KEYN = { team_number: "team number", team_name: "team name", season_goal: "season goal", lockdown: "lockdown", passwordless_login: "passwordless sign-in",
    scout_overrides: "scouts' identity fixes", options: "processing options", display_name: "name", has_password: "password", password_reset: "password reset", disabled: "disabled", role: "role" };
  const val = v => v == null || v === "" ? "—" : v === true ? "on" : v === false ? "off" : typeof v === "object" ? (Array.isArray(v) ? v.join(", ") : Object.entries(v).map(([k, x]) => `${k} ${val(x)}`).join(", ")) : String(v);
  const clip = (t, n = 90) => t.length > n ? t.slice(0, n) + "…" : t;
  /** before / after -> "season goal: — → DCMP; team number: 4911" (only what changed) */
  function changeTxt(e) {
    const b = e.before, a = e.after, fam = e.action.split(".")[0];
    if (e.action.startsWith("http.")) return "";
    if (fam === "picklist") return `${b && b.version != null ? `version ${b.version}` : "none"} → ${a && a.version != null ? `version ${a.version}` : "none"}`;
    if (fam === "note") return clip((a && a.text) || (b && b.text) || "");
    const B = b && typeof b === "object" && !Array.isArray(b) ? b : {}, A = a && typeof a === "object" && !Array.isArray(a) ? a : {};
    if (fam === "events") return A.row ? "now followed" : B.row ? "no longer followed" : "";
    // an account made or deleted: who it is, not a field-by-field diff
    if (e.action === "users.create" || e.action === "users.delete" || e.action === "auth.setup") { const u = e.action === "users.delete" ? B : A;
      return [u.username, u.display_name && u.display_name !== u.username ? `“${u.display_name}”` : "", u.role, u.has_password === false ? "no password" : u.has_password ? "with a password" : ""].filter(Boolean).join(", "); }
    const keys = [...new Set([...Object.keys(B), ...Object.keys(A)])].filter(k => val(B[k]) !== val(A[k]) && k !== "password");
    if (!keys.length) return b == null && a == null ? "" : clip(val(a != null ? a : b), 120);
    return keys.map(k => `${KEYN[k] || k.replace(/_/g, " ")}: ${clip(val(B[k]), 40)} → ${clip(val(A[k]), 40)}`).join("; ");
  }

  // ---------------------------------------------------------------- admin: activity + switches
  CS.globalRoute("activity", async () => {
    crumbs([["Activity", "#/activity"]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">No Cyber-Scout server here (static site).</div>`; return; }
    if (!CS.can("admin")) { $app.innerHTML = `<div class="empty">Admins only.</div>`; return; }
    const [sec, users, evs] = await Promise.all([CS.app("GET", "admin/security"), CS.app("GET", "users"), CS.app("GET", "events").catch(() => [])]);
    const evName = Object.fromEntries(evs.map(e => [e.key, e.short_name || e.name || e.key]));
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
      <div class="card" style="margin-top:16px"><h2>Change log ${CS.help("Undo puts back what was there before that change (it is logged too, and can itself be undone). “Undo all their changes since…” (under ⋯ on another teammate's change) reverts every change that account made from that moment on: use it when a login leaked. Sign-ins and other requests are under What.", "About undo")}</h2>
        <div class="row" style="flex-wrap:wrap;gap:8px;margin-bottom:10px">
          <label class="small">Who <select id="fuser"><option value="">anyone</option>${users.map(u => `<option value="${u.id}">${esc(u.display_name || u.username)} (${esc(u.username)})</option>`).join("")}</select></label>
          <label class="small">What <select id="ffam">${FAMILIES.map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("")}</select></label>
          <label class="small">Competition <select id="fev"><option value="">all</option>${evs.map(e => `<option value="${esc(e.key)}">${esc(e.name || e.key)}</option>`).join("")}<option value="none">none (accounts, settings)</option></select></label>
          <label class="small">From <select id="fvia"><option value="">anywhere</option><option value="lan">team network</option><option value="public">internet</option></select></label>
        </div>
        <div class="tablewrap"><table class="alog"><thead><tr><th>When</th><th>What happened</th><th data-pri="600">Change</th><th data-pri="300">From</th><th data-pri="850"><span class="vh">Undo</span></th></tr></thead><tbody id="alog"></tbody></table></div>
        <div style="margin-top:8px"><button type="button" id="amore" hidden>Older…</button></div>
        <div class="cserr" aria-live="polite" id="aerr"></div><div class="csok" role="status" aria-live="polite" id="aok"></div></div>`;

    const msg = (id, t) => { const e = document.getElementById(id); if (e) e.textContent = t; };
    document.getElementById("lockbtn").onclick = async () => {
      if (!lk && !confirm("Lock the site? Only admins will be able to change anything until you end the lockdown.")) return;
      try { await CS.app("PATCH", "admin/security", { lockdown: !lk }); if (CS.session) CS.session.lockdown = !lk; route(false); } catch (e) { msg("swerr", CS.sentence(e.message)); }
    };
    document.getElementById("revokebtn").onclick = async () => {
      const inc = document.getElementById("incadm").checked;
      if (!confirm(`Sign out everyone${inc ? " (including other admins)" : " except admins"}? You stay signed in.`)) return;
      try { const r = await CS.app("POST", "admin/sessions/revoke", { include_admins: inc }); msg("swok", `Signed out ${r.signed_out} session${r.signed_out === 1 ? "" : "s"}.`); }
      catch (e) { msg("swerr", CS.sentence(e.message)); }
    };
    const fs = document.getElementById("fsec").parentElement;
    CS.form(fs, async (d, f) => {
      await CS.app("PATCH", "admin/security", { passwordless_login: d.passwordless_login, scout_overrides: d.scout_overrides });
      f.querySelector(".csok").textContent = "Saved.";
    });
    window.scrollTo(0, 0);

    const tb = document.getElementById("alog"), more = document.getElementById("amore");
    let before = null, rows = [];
    const uname = Object.fromEntries(users.map(u => [String(u.id), u.display_name || u.username]));
    const nm = u => u && (u.display_name || u.username) ? u.display_name || u.username : "Someone";
    // what the entry was about, in words: a teammate, a match, a picklist, a note, a page of the API
    const about = e => {
      const t = e.target || "", m = /^user:(\d+)$/.exec(t);
      const snap = (e.after && e.after.username) || (e.before && e.before.username);
      if (m) return e.user && String(e.user.id) === m[1] && /^(auth|users\.profile)/.test(e.action) ? "" : uname[m[1]] || snap || `account #${m[1]}`;
      if (e.action.startsWith("override.")) return shortKey(t.split("/").pop());
      if (e.action.startsWith("picklist.")) return t.split("/").slice(1).join("/") === "main" ? "" : t.split("/").slice(1).join("/");
      if (e.action.startsWith("note.")) return `note #${t}`;
      if (e.action.startsWith("http.")) return t.replace(/^\/app\/v1\//, "").replace(/^events\/[^/]+\//, "");
      if (e.action.startsWith("settings.") || e.action.startsWith("events.")) return "";
      return t;
    };
    const what = e => {
      const rv = /\.revert$/.test(e.action), base = ACT[e.action] || (rv ? `undid a ${e.action.split(".")[0]} change` : e.action.startsWith("http.") ? `ran ${e.action.slice(5).toUpperCase()}` : e.action.replace(/[._]/g, " "));
      const a = about(e);
      return `<b>${esc(nm(e.user))}</b> ${esc(base)}${a ? ` <span class="muted">· ${esc(a)}</span>` : ""}`;
    };
    const ownWrite = e => e.user && e.user.id && !/^(auth|http|sessions)\./.test(e.action);
    const row = e => { const ch = changeTxt(e), other = ownWrite(e) && e.user.id !== CS.user.id;
      return `<tr data-id="${e.id}"><td class="small" style="white-space:nowrap">${esc(when(e.at))}</td>
      <td class="wrapc">${what(e)}${e.event ? ` <span class="pill" title="competition">${esc(evName[e.event] || e.event)}</span>` : ""}${e.user && e.user.role ? `<div class="muted small">${esc(e.user.username || "")} · ${esc(e.user.role)}</div>` : ""}</td>
      <td class="small wrapc">${ch ? esc(ch) : '<span class="muted">—</span>'}</td>
      <td class="small" title="${esc(e.ua || "")}">${e.via === "public" ? '<span class="pill failed">internet</span>' : e.via ? '<span class="pill">team network</span>' : ""}<div class="muted small">${esc(e.ip || "")}</div></td>
      <td><div class="aacts">${e.reverted_by ? `<span class="muted small">undone</span>` : e.revertible ? `<button type="button" data-undo="${e.id}">Undo</button>` : ""}
        ${other ? `<details class="amore"><summary aria-label="More undo options for this change">⋯</summary><button type="button" class="danger" data-undoall="${e.id}">Undo all ${esc(nm(e.user))}'s changes since this…</button></details>` : ""}</div></td></tr>`; };
    async function load(reset) {
      if (reset) { before = null; rows = []; tb.innerHTML = ""; }
      const q = new URLSearchParams({ limit: "100" });
      const u = document.getElementById("fuser").value, f = document.getElementById("ffam").value, v = document.getElementById("fvia").value, ev = document.getElementById("fev").value;
      if (u) q.set("user_id", u); if (f === "changes") q.set("changes", "true"); else if (f) q.set("action", f); if (v) q.set("via", v); if (ev) q.set("event", ev); if (before) q.set("before_id", before);
      try {
        const r = await CS.app("GET", "audit?" + q);
        rows = rows.concat(r.entries); before = r.next_before_id; more.hidden = !before;
        tb.insertAdjacentHTML("beforeend", r.entries.map(row).join("") || (reset ? `<tr><td colspan="5" class="muted">Nothing here yet.</td></tr>` : ""));
      } catch (e) { msg("aerr", CS.sentence(e.message)); }
    }
    ["fuser", "ffam", "fvia", "fev"].forEach(id => { document.getElementById(id).onchange = () => load(true); });
    more.onclick = () => load(false);
    // the table is in #app: its "›" detail rows carry the row's data-id, and the buttons keep their data-*
    document.querySelector("table.alog").onclick = async ev => {
      const b = ev.target.closest("button[data-undo], button[data-undoall]"); if (!b) return;
      const e = rows.find(x => String(x.id) === (b.dataset.undo || b.dataset.undoall)); if (!e) return;
      msg("aerr", ""); msg("aok", "");
      const plain = s0 => s0.replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      try {
        if (b.dataset.undo) {
          const ch = changeTxt(e);
          if (!confirm(`Undo this change from ${when(e.at)}?\n\n${plain(what(e))}${ch ? `\n${ch}` : ""}\n\nWhat was there before comes back (the undo is logged and can be undone too).`)) return;
          await CS.app("POST", `audit/${e.id}/revert`); msg("aok", "Undone.");
        } else {
          const name = nm(e.user), uname0 = e.user.username || "";
          const dry = await CS.app("POST", "audit/revert-user", { user_id: e.user.id, since: e.at, dry_run: true });
          const n = dry.reverted.length;
          if (!n) { msg("aok", `Nothing to undo: ${name} made no undoable changes since ${when(e.at)}.`); return; }
          const typed = prompt(`Undo ALL of ${name}'s changes since ${when(e.at)}: ${n} change${n === 1 ? "" : "s"}${dry.skipped.length ? ` (${dry.skipped.length} more can't be undone)` : ""}.\n\nIf their login leaked, disable the account first (Admin → Teammates).\n\nType their username (${uname0}) to go ahead.`);
          if (typed === null) return;
          if (typed.trim().toLowerCase() !== uname0.toLowerCase()) { msg("aerr", "Nothing undone: the username didn't match."); return; }
          const r = await CS.app("POST", "audit/revert-user", { user_id: e.user.id, since: e.at });
          msg("aok", `Undid ${r.reverted.length} change${r.reverted.length === 1 ? "" : "s"}${r.skipped.length ? ` (${r.skipped.length} not undoable or already undone)` : ""}.`);
        }
        await load(true);
      } catch (x) { msg("aerr", CS.sentence(x.message)); }
    };
    await load(true);
  });

  // ---------------------------------------------------------------- strategist+: identity fixes waiting for approval
  // One queue per competition (#/<ev>/fixes); other competitions' waiting fixes are only counted, with a link.
  CS.globalRoute("fixes", () => {
    let ev = null; try { ev = localStorage.getItem("cs:lastev"); } catch (e) { }
    location.replace(/^\d{4}[a-z0-9]{1,16}$/.test(ev || "") ? `#/${ev}/fixes` : "#/");
  });
  CS.route("fixes", async ev => {
    CS.markActive && CS.markActive("fixes");
    crumbs([["Identity fixes", `#/${ev}/fixes`]]); $updated.textContent = "";
    if (!CS.platform) { $app.innerHTML = `<div class="empty">No Cyber-Scout server here (static site).</div>`; return; }
    if (!CS.can("strategist")) { $app.innerHTML = `<div class="empty">Strategists and admins only.</div>`; return; }
    const evs = await CS.app("GET", "events");
    const cur = evs.find(e => e.key === ev) || { key: ev, name: ev };
    const [o, others] = await Promise.all([CS.app("GET", `events/${encodeURIComponent(ev)}/overrides`),
      Promise.all(evs.filter(e => e.key !== ev).map(e => CS.app("GET", `events/${encodeURIComponent(e.key)}/overrides`).then(x => ({ e, n: (x.pending || []).length })).catch(() => ({ e, n: 0 }))))]);
    const pend = o.pending || [], elsewhere = others.filter(x => x.n);
    // fixes name robots by track idx: show them as the match page labels them (its m/<match>.json), else "#idx"
    const shown = {};
    await Promise.all([...new Set(pend.map(p => p.match))].map(m => api(`${ev}/m/${m}.json`)
      .then(d => { shown[m] = Object.fromEntries((d.tracks || []).map((t, i) => [String(t.idx != null ? t.idx : i), t.team ? num(t.team) : null])); }).catch(() => { })));
    const side = (d, m) => ["red", "blue"].filter(a => d && d[a] && Object.keys(d[a]).length)
      .map(a => `${a}: ` + Object.entries(d[a]).map(([i, t]) => { const l = (shown[m] || {})[i]; return `${l ? `robot shown as ${esc(l)}` : `#${esc(i)}`} → ${esc(String(t).replace(/^frc/, ""))}`; }).join(", ")).join("; ") || "(no fixes)";
    setPending(ev, pend.length);
    const paused = CS.processing && CS.processing.paused;
    $app.innerHTML = `<h1>Identity fixes</h1><div class="sub">Scouts' robot → team corrections at <b>${esc(cur.name || ev)}</b> wait here until a strategist or admin approves them. Open the match to check the robots before you decide; approved fixes are used on the worker's next pass${paused ? " (processing is paused on the server right now, so not until it's turned back on)" : ""}.</div>
      <div class="card fxq">${pend.length ? `<div class="tablewrap"><table><thead><tr><th>Match</th><th>Requested</th><th data-pri="300">Now</th><th data-pri="400">By</th><th data-pri="850"><span class="vh">Decide</span></th></tr></thead><tbody>
        ${pend.map(p => `<tr data-id="${esc(p.id)}"><td data-label="${esc(shortKey(p.match))}"><a class="mlink" href="#/${esc(ev)}/m/${esc(p.match)}" title="Open the match to check">${esc(shortKey(p.match))}${CS.icon("chevR")}</a></td>
          <td class="wrapc">${side(p.data, p.match)}</td><td class="small wrapc">${side(p.current, p.match)}${p.stale ? ' <span class="pill failed" title="changed since this was requested">changed</span>' : ""}</td>
          <td>${esc(p.by ? p.by.display_name : "?")}<div class="muted small">${esc(CS.fmtWhen(p.at))}</div></td>
          <td><div class="dec"><button type="button" class="primary" data-act="approve">Approve</button><button type="button" data-act="reject">Reject</button></div></td></tr>`).join("")}
        </tbody></table></div>` : `<div class="muted">Nothing waiting at this competition.</div>`}<div class="cserr" aria-live="polite"></div></div>
      ${elsewhere.length ? `<div class="note" style="margin-top:12px">Also waiting: ${elsewhere.map(x => `<a href="#/${esc(x.e.key)}/fixes">${esc(x.e.name || x.e.key)}</a> (${x.n})`).join(", ")}.</div>` : ""}`;
    // one handler for the table: the "›" detail row of a folded Decide column carries the row's data-id too
    const card = $app.querySelector(".fxq");
    card.onclick = async e => {
      const b = e.target.closest("button[data-act]"), tr = b && b.closest("tr[data-id]"); if (!tr) return;
      b.disabled = true;
      try { await CS.app("POST", `events/${encodeURIComponent(ev)}/override-requests/${tr.dataset.id}/${b.dataset.act}`); route(false); }
      catch (x) { b.disabled = false; card.querySelector(".cserr").textContent = CS.sentence(x.message); }
    };
  });
})();
