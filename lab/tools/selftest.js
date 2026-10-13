/* In-page checks for the lab, run headless: index.html?selftest=1 then read <html data-selftest> from --dump-dom. */
(async function () {
  const out = [], ok = (name, cond, info = "") => out.push(`${cond ? "PASS" : "FAIL"} ${name}${info ? " (" + info + ")" : ""}`);
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const h = document.documentElement, L = window.LAB;
  await wait(2500);
  ok("dashboard rendered", !!document.querySelector(".qcard") && !!document.querySelector(".rail"));
  ok("nav icons rendered", document.querySelectorAll(".sb-item svg").length >= 10, document.querySelectorAll(".sb-item").length + " items");
  ok("route pinned", L.allowed(location.hash), location.hash);
  // open the panel with the button
  const fab = document.getElementById("labfab"); fab.click(); await wait(100);
  const panel = document.getElementById("labpanel");
  ok("panel opens", !panel.hidden && fab.getAttribute("aria-expanded") === "true");
  ok("focus moves into panel", panel.contains(document.activeElement), document.activeElement && document.activeElement.value);
  // pick each theme via the radios
  for (const t of L.THEMES) {
    const r = panel.querySelector(`input[name=labtheme][value="${t.id}"]`); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); await wait(30);
    const bg = getComputedStyle(document.body).backgroundColor;
    ok(`theme ${t.id} applies`, h.dataset.theme === t.id && new URL(location.href).searchParams.get("theme") === t.id, bg);
  }
  ok("jet opens dark", (() => { const r = panel.querySelector('input[name=labtheme][value="jet"]'); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); return h.dataset.mode === "dark"; })());
  // layouts
  for (const l of L.LAYOUTS) { const r = panel.querySelector(`input[name=lablayout][value="${l.id}"]`); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); await wait(30);
    ok(`layout ${l.id}`, h.dataset.layout === l.id && new URL(location.href).searchParams.get("layout") === l.id); }
  // appearance on a single-mode theme is fixed
  { const r = panel.querySelector('input[name=labtheme][value="pine"]'); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); await wait(30);
    ok("pine light disabled", panel.querySelector('input[name=labmode][value="light"]').disabled && h.dataset.mode === "dark"); }
  { const r = panel.querySelector('input[name=labtheme][value="earth"]'); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); await wait(30);
    const m = panel.querySelector('input[name=labmode][value="dark"]'); m.checked = true; m.dispatchEvent(new Event("change", { bubbles: true })); await wait(30);
    ok("earth dark", h.dataset.mode === "dark" && getComputedStyle(h).getPropertyValue("--bg").trim() === L.theme("earth").dark.bg, getComputedStyle(h).getPropertyValue("--bg")); }
  ok("persisted", localStorage.getItem("lab:theme") === "earth" && localStorage.getItem("lab:mode") === "dark");
  // copy settings
  panel.querySelector("#labcopy").click(); await wait(400);
  const ta = panel.querySelector("#labout");
  ok("copy shows CSS", !ta.hidden && /--accent: #aed08f|--accent: #36633a/.test(ta.value) && ta.value.includes("prefers-color-scheme: dark"), ta.value.length + " chars");
  // escape closes and returns focus
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await wait(50);
  ok("escape closes", panel.hidden && document.activeElement === fab);
  // keyboard shortcuts
  document.body.focus(); document.dispatchEvent(new KeyboardEvent("keydown", { key: "]", bubbles: true })); await wait(30);
  ok("] next theme", h.dataset.theme === "jet", h.dataset.theme);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "l", bubbles: true })); await wait(30);
  ok("L next layout", h.dataset.layout === "sidebar", h.dataset.layout);
  // inert navigation
  const before = location.hash, teams = [...document.querySelectorAll(".sb-item")].find(a => /Teams/.test(a.textContent));
  teams.click(); await wait(200);
  ok("nav link inert", location.hash === before && !!document.querySelector(".qcard") && /isn't wired/.test(document.querySelector(".lab-toast").textContent));
  location.hash = "#/2026wasam/rankings"; await wait(300);
  ok("hash guard", location.hash === before && !!document.querySelector(".qcard") && !document.querySelector(".empty.err"), location.hash);
  const tab = document.querySelector(".rail .rl-m.upc:not(.on)"); if (tab) { tab.click(); await wait(150); }
  ok("plan tabs still work", !!tab && tab.classList.contains("on") || !!document.querySelector(".rail .rl-m.upc.on"), location.hash);
  h.setAttribute("data-selftest", out.join(" | "));
})();
