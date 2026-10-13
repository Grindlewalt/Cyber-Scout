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

  CS.globalRoute("settings", async () => {
    crumbs([["Settings"]]); $updated.textContent = "";
    if (!S) { $app.innerHTML = `<div class="empty">Settings need theme.js, which didn't load. Reload the page.</div>`; return; }
    if (CS.sync) await CS.sync.ready;
    const client = !!(CS.sync && CS.sync.mode === "client");
    const draw = (focusSel) => {
      const cur = S.get(), t = S.themes.find(x => x.id === cur.style), single = t.modes.length === 1;
      const def = S.defaults;
      $app.innerHTML = `<div class="st">
        <h1>Settings</h1><div class="sub">${client ? "This computer's connection to your team's server, and how Cyber-Scout looks for you." : "How Cyber-Scout looks for you, and what this device keeps for offline use. Saved in this browser only, so each device can have its own look."}</div>
        ${client ? '<div id="st-sync"></div>' : ""}
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
        ${client ? "" : '<div id="st-sync" class="st-after"></div>'}
      </div>`;
      // sync.js: Connection (Cyber-Scout Client) first, or Offline (browser) after the look
      if (CS.sync && CS.sync.mountSettings) CS.sync.mountSettings($app.querySelector("#st-sync"));
      $app.querySelectorAll("input[name=st-style]").forEach(i => i.onchange = () => { S.set({ style: i.value }); draw(`input[name=st-style][value="${i.value}"]`); });
      $app.querySelectorAll("input[name=st-mode]").forEach(i => i.onchange = () => { S.set({ mode: i.value }); draw(`input[name=st-mode][value="${i.value}"]`); });
      $app.querySelectorAll("input[name=st-layout]").forEach(i => i.onchange = () => { S.set({ layout: i.value }); draw(`input[name=st-layout][value="${i.value}"]`); });
      $app.querySelector("#st-reset").onclick = () => { S.reset(); draw("#st-reset"); const ok = $app.querySelector(".st-ok"); if (ok) ok.textContent = "Back to Sand & Earth, Auto, Sidebar."; };
      if (focusSel) { const f = $app.querySelector(focusSel); if (f && !f.disabled) f.focus(); else { const r = $app.querySelector("input[name=st-style]:checked"); r && r.focus(); } }
    };
    draw();
    // another tab (or the reset) changed the look: redraw, unless the user is mid-interaction here
    const onChange = () => { if (location.hash.replace(/^#\/?/, "").split("/")[0] === "settings" && !$app.contains(document.activeElement)) draw(); };
    document.addEventListener("cs:style", onChange);
    if (typeof cleanup !== "undefined") cleanup.push(() => document.removeEventListener("cs:style", onChange));
  });
})();
