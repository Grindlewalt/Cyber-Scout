/* Design lab UI: the floating "Design lab" button + panel, inert navigation, and a few layout helpers.
 * Everything visual comes from themes.js via lab-boot.js; this file only flips <html data-*> and builds the panel. */
(function () {
  "use strict";
  const L = window.LAB;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MODE_LABEL = { light: "Light", dark: "Dark", auto: "Auto" };

  // ---------------------------------------------------------------- toast (polite live region)
  const toastEl = document.createElement("div");
  toastEl.className = "lab-toast"; toastEl.setAttribute("role", "status"); toastEl.setAttribute("aria-live", "polite");
  document.body.appendChild(toastEl);
  let tt = 0;
  L.toast = msg => { toastEl.textContent = msg; toastEl.classList.add("on"); clearTimeout(tt); tt = setTimeout(() => toastEl.classList.remove("on"), 2600); };

  // ---------------------------------------------------------------- inert links: everything except in-page controls stays put
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || a.closest(".lab-panel")) return;
    const h = a.getAttribute("href");
    if (h === "#" || h.startsWith("#plans") || (h.startsWith("#/") && L.allowed(h))) return;
    e.preventDefault();
    const ms = document.getElementById("moresheet"); if (ms && ms.open && a.closest("#moresheet")) ms.close();
    const what = (a.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
    L.toast(`Lab preview: "${what || "this link"}" isn't wired. Only the Dashboard is.`);
  }, true);

  // nav labels as tooltips (the compact rail hides them visually) — set on every nav re-render
  const titleNav = () => document.querySelectorAll(".sb-item, .tabbar a").forEach(a => { const s = a.querySelector("span"); if (s && !a.title) a.title = s.textContent; });
  ["topnav", "tabbar"].forEach(id => { const el = document.getElementById(id); if (el) new MutationObserver(titleNav).observe(el, { childList: true }); });

  // Focus layout: the sidebar toggle opens the nav as a drawer instead of a column
  const sbt = document.getElementById("sbtoggle");
  if (sbt) sbt.addEventListener("click", e => {
    if (document.documentElement.dataset.layout !== "focus") return;
    e.stopImmediatePropagation();
    const on = document.body.classList.toggle("lab-drawer");
    sbt.setAttribute("aria-expanded", on);
  }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && document.body.classList.contains("lab-drawer")) { document.body.classList.remove("lab-drawer"); sbt && sbt.focus(); } });

  // ---------------------------------------------------------------- the panel
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 2-.9 2-1.8 0-1.3-1.1-1.6-1.1-2.9 0-1 .8-1.8 1.8-1.8h2.1c2.1 0 3.7-1.6 3.7-3.7C20.5 6.8 16.7 3.5 12 3.5z"/><circle cx="7.6" cy="11.2" r="1.2" fill="currentColor" stroke="none"/><circle cx="10.4" cy="7.4" r="1.2" fill="currentColor" stroke="none"/><circle cx="15.2" cy="7.6" r="1.2" fill="currentColor" stroke="none"/></svg>';
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "lab-fab"; btn.id = "labfab";
  btn.setAttribute("aria-label", "Design lab: theme and layout"); btn.title = "Design lab (theme, layout, appearance)";
  btn.setAttribute("aria-expanded", "false"); btn.setAttribute("aria-controls", "labpanel");
  btn.innerHTML = ICON;
  const panel = document.createElement("section");
  panel.className = "lab-panel"; panel.id = "labpanel"; panel.hidden = true;
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-labelledby", "labh");
  document.body.append(btn, panel);

  const sw = (t, m) => { const k = t[m]; return `<span class="lab-sw" aria-hidden="true"><i style="background:${k.bg}"></i><i style="background:${k.plate}"></i><i style="background:${k["accent-fill"]}"></i><i style="background:${k["red-fill"]}"></i><i style="background:${k["blue-fill"]}"></i></span>`; };
  function minText(t, m) {
    const r = L.contrast(t[m]); const txt = r.filter(x => x.min === 4.5);
    const lo = txt.reduce((a, x) => x.ratio < a.ratio ? x : a); return { lo, fails: r.filter(x => !x.ok) };
  }
  // a theme can ask for its signature appearance (Jet opens dark); otherwise the viewer's last explicit choice carries over
  const themePatch = id => ({ theme: id, mode: L.theme(id).defaultMode || L.state.userMode || "auto" });
  function render() {
    const s = L.state, t = L.theme(s.theme), ms = L.modes(t);
    const eff = document.documentElement.dataset.mode;
    const shownMode = ms.length === 1 ? ms[0] : (eff === "auto" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : eff);
    const ct = minText(t, shownMode);
    panel.innerHTML = `
      <header class="lab-h"><h2 id="labh">Design lab</h2>
        <button type="button" class="lab-x" id="labclose" aria-label="Close design lab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></svg></button></header>
      <p class="lab-sub">The real Dashboard at the Sunday 09:00 replay. Only this page is wired.</p>
      <fieldset class="lab-f"><legend>Theme</legend>
        <div class="lab-themes">${L.THEMES.map(x => `<label class="lab-t${x.id === s.theme ? " on" : ""}">
          <input type="radio" name="labtheme" value="${x.id}"${x.id === s.theme ? " checked" : ""}>
          ${sw(x, x.light ? "light" : "dark")}<span class="lab-tn"><b>${esc(x.name)}</b><small>${L.modes(x).map(m => MODE_LABEL[m]).join(" + ")}</small></span></label>`).join("")}</div>
        <p class="lab-why">${esc(t.blurb)} <span>${esc(t.why)}</span></p>
      </fieldset>
      <fieldset class="lab-f"><legend>Layout</legend>
        <div class="lab-seg lab-seg4">${L.LAYOUTS.map(l => `<label title="${esc(l.blurb)}"><input type="radio" name="lablayout" value="${l.id}"${l.id === s.layout ? " checked" : ""}><span>${esc(l.name)}</span></label>`).join("")}</div>
        <p class="lab-why">${esc(L.LAYOUTS.find(l => l.id === s.layout).blurb)}</p>
      </fieldset>
      <fieldset class="lab-f"${ms.length === 1 ? ` aria-describedby="labmodenote"` : ""}><legend>Appearance</legend>
        <div class="lab-seg">${["light", "dark", "auto"].map(m => { const dis = ms.length === 1 && m !== ms[0];
          const on = ms.length === 1 ? m === ms[0] : m === s.mode;
          return `<label class="${dis ? "dis" : ""}"><input type="radio" name="labmode" value="${m}"${on ? " checked" : ""}${dis ? " disabled" : ""}><span>${MODE_LABEL[m]}</span></label>`; }).join("")}</div>
        ${ms.length === 1 ? `<p class="lab-why" id="labmodenote">${esc(t.name)} is ${ms[0]} only.</p>` : ""}
      </fieldset>
      <p class="lab-ct ${ct.fails.length ? "bad" : ""}">${ct.fails.length ? `${ct.fails.length} contrast pair${ct.fails.length > 1 ? "s" : ""} below target` : "All text pairs pass 4.5:1"} in ${shownMode}. Lowest: <b>${ct.lo.ratio.toFixed(2)}:1</b> <span>(${esc(ct.lo.what)})</span> · <a href="gallery.html#ct-${t.id}">all ratios</a></p>
      <div class="lab-acts"><button type="button" id="labcopy">Copy theme CSS</button><button type="button" id="lablink">Copy link</button><a class="lab-gal" href="gallery.html">Gallery</a></div>
      <textarea class="lab-out" id="labout" readonly hidden aria-label="Theme CSS to paste into web/style.css" rows="7"></textarea>
      <p class="lab-keys">Keys: <kbd>[</kbd> <kbd>]</kbd> theme · <kbd>L</kbd> layout · <kbd>D</kbd> appearance</p>`;
    panel.querySelectorAll("input[name=labtheme]").forEach(i => i.onchange = () => { L.apply(themePatch(i.value)); render(); panel.querySelector(`input[value="${i.value}"]`).focus(); });
    panel.querySelectorAll("input[name=lablayout]").forEach(i => i.onchange = () => { L.apply({ layout: i.value }); render(); panel.querySelector(`input[name=lablayout][value="${i.value}"]`).focus(); });
    panel.querySelectorAll("input[name=labmode]").forEach(i => i.onchange = () => { L.apply({ mode: i.value, userMode: i.value }); render(); panel.querySelector(`input[name=labmode][value="${i.value}"]`).focus(); });
    panel.querySelector("#labclose").onclick = () => close(true);
    panel.querySelector("#labcopy").onclick = async () => { const css = await L.portCSS(L.theme(L.state.theme)); copyOut(css, "Theme CSS copied. Paste it into web/style.css (see the comment at the top)."); };
    panel.querySelector("#lablink").onclick = () => copyOut(location.href, "Link copied.", true);
  }
  async function copyOut(text, msg, short) {
    const ta = panel.querySelector("#labout");
    ta.value = text; ta.hidden = !!short && true; ta.rows = short ? 2 : 9;
    let ok = false;
    try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; } } catch (e) { }
    if (!ok) { ta.hidden = false; ta.select(); try { ok = document.execCommand("copy"); } catch (e) { } }
    if (!short) ta.hidden = false;
    L.toast(ok ? msg : "Select the text in the box and copy it.");
  }
  function open() { render(); panel.hidden = false; btn.setAttribute("aria-expanded", "true"); const c = panel.querySelector("input:checked"); (c || panel).focus(); }
  function close(focus) { panel.hidden = true; btn.setAttribute("aria-expanded", "false"); if (focus) btn.focus(); }
  btn.onclick = () => panel.hidden ? open() : close(true);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !panel.hidden) { e.preventDefault(); close(true); } });
  document.addEventListener("pointerdown", e => { if (!panel.hidden && !panel.contains(e.target) && !btn.contains(e.target)) close(false); });
  document.addEventListener("lab:change", () => { if (!panel.hidden) { /* keep panel in sync with keyboard shortcuts */ } });

  // quick keys for comparing: [ and ] cycle themes, L cycles layouts, D cycles appearance (ignored while typing)
  document.addEventListener("keydown", e => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tg = e.target; if (tg && (tg.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName) && tg.type !== "radio")) return;
    const ids = L.THEMES.map(t => t.id), ls = L.LAYOUTS.map(l => l.id), i = ids.indexOf(L.state.theme);
    if (e.key === "]" || e.key === "[") { L.apply(themePatch(ids[(i + (e.key === "]" ? 1 : ids.length - 1)) % ids.length])); L.toast(L.theme(L.state.theme).name); }
    else if (e.key === "l" || e.key === "L") { L.apply({ layout: ls[(ls.indexOf(L.state.layout) + 1) % ls.length] }); L.toast("Layout: " + L.LAYOUTS.find(l => l.id === L.state.layout).name); }
    else if (e.key === "d" || e.key === "D") { const ms = L.modes(L.theme(L.state.theme)); if (ms.length < 2) { L.toast(`${L.theme(L.state.theme).name} is ${ms[0]} only.`); return; }
      const order = ["light", "dark", "auto"], nm = order[(order.indexOf(L.state.mode) + 1) % 3]; L.apply({ mode: nm, userMode: nm }); L.toast("Appearance: " + MODE_LABEL[L.state.mode]); }
    else return;
    if (!panel.hidden) render();
  });

  // ---------------------------------------------------------------- porting: a drop-in replacement for the token blocks of web/style.css
  L.portCSS = async function (t) {
    let styleText = "";
    try { styleText = await (await fetch("style.css", { cache: "no-cache" })).text(); } catch (e) { }
    return L.port(t, styleText);
  };

  // ?probe=1: write layout metrics into the DOM for headless checks (no visual change)
  if (new URLSearchParams(location.search).has("probe")) {
    const run = () => { const d = document.documentElement; d.setAttribute("data-probe", JSON.stringify({ sw: d.scrollWidth, cw: d.clientWidth, over: [...document.querySelectorAll("body *")].filter(e => { const r = e.getBoundingClientRect(); return r.width && r.right > d.clientWidth + 1 && getComputedStyle(e).position !== "fixed"; }).slice(0, 8).map(e => e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (e.className && typeof e.className === "string" ? "." + e.className.split(" ").join(".") : "")) })); };
    setTimeout(run, 3000); setTimeout(run, 6000);
  }
})();

/* layout helpers that need a little DOM: the focus-layout drawer and deep-link flags */
(function () {
  "use strict";
  const L = window.LAB, sbt = document.getElementById("sbtoggle");
  const sync = () => {
    const focus = document.documentElement.dataset.layout === "focus";
    if (!focus) document.body.classList.remove("lab-drawer");
    if (sbt) { sbt.setAttribute("aria-label", focus ? "Open navigation" : document.body.classList.contains("sb-off") ? "Show sidebar" : "Hide sidebar"); sbt.title = sbt.getAttribute("aria-label");
      if (focus) sbt.setAttribute("aria-expanded", document.body.classList.contains("lab-drawer")); else sbt.removeAttribute("aria-expanded"); }
  };
  document.addEventListener("lab:change", sync); sync();
  document.addEventListener("pointerdown", e => {
    if (!document.body.classList.contains("lab-drawer")) return;
    if (e.target.closest(".sidebar") || (sbt && sbt.contains(e.target))) return;
    document.body.classList.remove("lab-drawer"); sync();
  });
  const q = new URLSearchParams(location.search);
  if (q.has("shot")) document.documentElement.setAttribute("data-shot", "");
  if (q.has("open")) setTimeout(() => document.getElementById("labfab").click(), 400);
  if (q.has("selftest")) { const sc = document.createElement("script"); sc.src = "tools/selftest.js"; document.body.appendChild(sc); }
})();
