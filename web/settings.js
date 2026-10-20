"use strict";
// settings.js: #/settings -- each person picks their own look (theme, appearance, layout) for this browser.
// The choices live in theme.js (window.CSStyle: localStorage cs:style / cs:mode / cs:layout, applied before first paint).
(function () {
  const S = window.CSStyle;
  CS.nav({ label: "Settings", key: "settings", href: () => "#/settings", order: 99, global: true, group: "manage", icon: "settings" });

  const MODE = { auto: "Auto", light: "Light", dark: "Dark" };
  const mini = (k, label) => k ? `<span class="st-mini" role="img" aria-label="${label}" style="--b:${k.bg};--s:${k.surface};--p:${k.plate};--i:${k.ink};--a:${k.accent};--r:${k.red};--u:${k.blue}">
      <span class="pl"></span><span class="cd"><span class="ln"></span><span class="ac"></span><span class="al"><span></span><span></span></span></span></span>` : "";
  const LAYOUT_ART = {
    sidebar: '<span class="lo-side"></span><span class="lo-top"></span><span class="lo-body"></span>',
    topbar: '<span class="lo-bar"></span><span class="lo-body wide"></span>',
    command: '<span class="lo-rail"></span><span class="lo-top"></span><span class="lo-grid"><span></span><span></span><span></span><span></span></span>',
  };

  // Cyber-Scout Client only: "Help process matches" (the client's processor mode) -- its status line, the GPU /
  // slow-Wi-Fi notices and a link to its own page (client/csclient/procui.py at /client/processing)
  let procT = 0;
  function procSection(el) {
    if (!el || !CS.sync || !CS.sync.ctl) return;
    clearInterval(procT);
    el.innerHTML = `<fieldset class="st-sec" id="processing"><legend><h2>Help process matches</h2></legend>
      <p class="st-why">This computer can track match video on its GPU for the server, or record the stream and send it match clips (Clip only).</p>
      <div class="cx-grid"><div class="cx-row"><div class="cx-k">Status</div><div class="cx-v" data-part="proc" aria-live="polite"><p class="cx-note">Asking the Cyber-Scout Client\u2026</p></div></div></div></fieldset>`;
    const box = el.querySelector("[data-part=proc]");
    const mbs = v => v >= 10e6 ? Math.round(v / 1e6) : (v / 1e6).toFixed(1);
    async function load() {
      if (!document.body.contains(el)) { clearInterval(procT); return; }
      let p;
      try { p = await CS.sync.ctl("GET", "processing", undefined, 6000); }
      catch (x) { box.innerHTML = `<p class="cx-note">${esc(x.status === 404 ? "This version of the Cyber-Scout Client can't process matches." : x.message)}</p>`; return; }
      const on = p.enabled, n = p.net || {}, g = p.gpu, sp = p.speed || {};
      const mode = p.mode === "clip" ? "Clip only" : "Track matches";
      const facts = [g ? (g.ok ? `GPU: ${g.name}` : "No GPU the tracker can use") : null,
        sp.per_match_min != null ? `about ${sp.per_match_min < 10 ? sp.per_match_min.toFixed(1) : Math.round(sp.per_match_min)} min per match (${sp.from === "measured" ? "measured" : "benchmark"})` : null,
        n.down_bps ? `${mbs(n.down_bps)} MB/s from the server` : null].filter(Boolean);
      const notes = (p.notices || []).filter(x => x.level !== "info" && !(x.kind === "no_gpu" && p.mode === "clip"));
      box.innerHTML = `<div class="cx-res${on && /^(error|no_gpu|needs_setup)/.test(p.state) ? " bad" : on ? " good" : ""}">${CS.icon(on ? "check" : "more", "ico")}<span><b>${on ? esc(mode) : "Off"}</b>${on ? `: ${esc(p.line)}` : ""}</span></div>
        ${facts.length ? `<p class="cx-note">${esc(facts.join(", "))}.</p>` : ""}
        ${notes.map(x => `<p class="cx-note warn">${esc(x.text)}</p>`).join("")}
        <p class="cx-note"><a href="/client/processing">${on ? "Open processing" : CS.can("admin") ? "Set up and turn on processing" : "See processing (setting it up needs an admin account)"}</a></p>`;
    }
    load();
    procT = setInterval(load, 5000);
    if (typeof cleanup !== "undefined") cleanup.push(() => clearInterval(procT));
  }

  CS.globalRoute("settings", async () => {
    crumbs([["Settings"]]); $updated.textContent = "";
    if (!S) { $app.innerHTML = `<div class="empty">Settings need theme.js, which didn't load. Reload the page.</div>`; return; }
    if (CS.sync) await CS.sync.ready;
    const client = !!(CS.sync && CS.sync.mode === "client");
    const draw = (focusSel) => {
      const cur = S.get(), t = S.themes.find(x => x.id === cur.style), single = t.modes.length === 1;
      const def = S.defaults;
      $app.innerHTML = `<div class="st">
        <h1>Settings</h1><div class="sub">${client ? "This computer's connection to your team's server, and how Cyber-Scout looks for you." : "What this device keeps for offline use, and how Cyber-Scout looks for you. Saved in this browser only, so each device can have its own look."}</div>
        <div id="st-sync"></div>${client ? '<div id="st-proc"></div>' : ""}
        <figure class="st-note">
          <blockquote>I like having a colorful UI and I'm sure others do too. I couldn't land on a specific design, so I went a little ham.</blockquote>
          <figcaption><span class="st-av" aria-hidden="true">G</span>Grant</figcaption>
        </figure>

        <fieldset class="st-sec"><legend><h2>Theme</h2></legend>
          <div class="st-grid">${S.themes.map(x => `<label class="st-theme">
            <input type="radio" name="st-style" value="${esc(x.id)}"${x.id === cur.style ? " checked" : ""}>
            <span class="st-prevs">${mini(x.sw.light, x.name + ", light")}${mini(x.sw.dark, x.name + ", dark")}</span>
            <span class="st-name"><b>${esc(x.name)}</b><span class="st-badges">${x.id === def.style ? '<span class="st-b def">Default</span>' : ""}${x.modes.map(m => `<span class="st-b">${MODE[m]}</span>`).join("")}</span></span>
          </label>`).join("")}</div>
          <p class="st-why" aria-live="polite"><b>${esc(t.name)}.</b> ${esc(t.blurb)}</p>
        </fieldset>

        <fieldset class="st-sec"${single ? ' aria-describedby="st-modenote"' : ""}><legend><h2>Appearance</h2></legend>
          <div class="st-seg">${["auto", "light", "dark"].map(m => { const dis = single && m !== t.modes[0], on = single ? m === t.modes[0] : m === cur.mode;
            return `<label class="${dis ? "dis" : ""}"><input type="radio" name="st-mode" value="${m}"${on ? " checked" : ""}${dis ? " disabled" : ""}><span>${MODE[m]}</span></label>`; }).join("")}</div>
          <p class="st-why" id="st-modenote">${single ? `${esc(t.name)} is ${t.modes[0]} only.` : cur.mode === "auto" ? "Follows your device's light or dark setting." : `Always ${cur.mode}, whatever your device is set to.`}</p>
        </fieldset>

        <fieldset class="st-sec"><legend><h2>Layout</h2></legend>
          <div class="st-layouts">${S.layouts.map(l => `<label class="st-layout">
            <input type="radio" name="st-layout" value="${esc(l.id)}"${l.id === cur.layout ? " checked" : ""}>
            <span class="st-lo lo-${esc(l.id)}" aria-hidden="true">${LAYOUT_ART[l.id] || ""}</span>
            <span class="st-name"><b>${esc(l.name)}</b>${l.id === def.layout ? '<span class="st-badges"><span class="st-b def">Default</span></span>' : ""}</span>
            <small>${esc(l.blurb)}</small>
          </label>`).join("")}</div>
        </fieldset>

        <div class="st-acts"><button type="button" id="st-reset"${cur.style === def.style && cur.mode === def.mode && cur.layout === def.layout ? " disabled" : ""}>Reset to default</button>
          <span class="st-ok" role="status" aria-live="polite"></span></div>
      </div>`;
      // sync.js: Connection (Cyber-Scout Client) or Offline (browser) first, above the look (on a phone the 18 themes are
      // several screens long)
      if (CS.sync && CS.sync.mountSettings) CS.sync.mountSettings($app.querySelector("#st-sync"));
      if (client) procSection($app.querySelector("#st-proc"));
      $app.querySelectorAll("input[name=st-style]").forEach(i => i.onchange = () => { S.set({ style: i.value }); draw(`input[name=st-style][value="${i.value}"]`); });
      $app.querySelectorAll("input[name=st-mode]").forEach(i => i.onchange = () => { S.set({ mode: i.value }); draw(`input[name=st-mode][value="${i.value}"]`); });
      $app.querySelectorAll("input[name=st-layout]").forEach(i => i.onchange = () => { S.set({ layout: i.value }); draw(`input[name=st-layout][value="${i.value}"]`); });
      $app.querySelector("#st-reset").onclick = () => {
        S.reset(); draw("#st-reset");
        const th = S.themes.find(x => x.id === def.style), lo = S.layouts.find(x => x.id === def.layout);
        const ok = $app.querySelector(".st-ok"); if (ok) ok.textContent = `Back to ${th ? th.name : def.style}, ${MODE[def.mode] || def.mode}, ${lo ? lo.name : def.layout}.`;
      };
      if (focusSel) { const f = $app.querySelector(focusSel); if (f && !f.disabled) f.focus(); else { const r = $app.querySelector("input[name=st-style]:checked"); r && r.focus(); } }
    };
    draw();
    // another tab (or the reset) changed the look: redraw, unless the user is mid-interaction here
    const onChange = () => { if (location.hash.replace(/^#\/?/, "").split("/")[0] === "settings" && !$app.contains(document.activeElement)) draw(); };
    document.addEventListener("cs:style", onChange);
    if (typeof cleanup !== "undefined") cleanup.push(() => document.removeEventListener("cs:style", onChange));
  });
})();
