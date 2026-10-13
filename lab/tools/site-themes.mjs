#!/usr/bin/env node
// Build the real site's theme files from the design lab registry (themes.js + ext/a-d.js) and layout CSS.
//   node lab/tools/site-themes.mjs            -> web/themes.css, web/theme.js, web/fonts/*.woff2
// web/themes.css  every theme scoped to html[data-style="<id>"] (+ data-mode / prefers-color-scheme), the default
//                 (Sand & Earth) also on plain :root so the site works without JS; @font-face per family (a browser
//                 downloads only the faces the active theme uses); the Top bar + Command layouts.
// web/theme.js    the boot script (in <head>, before CSS paints): reads cs:style / cs:mode / cs:layout from
//                 localStorage and sets <html data-style data-mode data-layout> + theme-color/color-scheme metas.
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const lab = join(dirname(fileURLToPath(import.meta.url)), ".."), web = join(lab, "..", "web");
globalThis.window = globalThis;
for (const f of ["themes.js", "ext/a.js", "ext/b.js", "ext/c.js", "ext/d.js"]) (0, eval)(readFileSync(join(lab, f), "utf8"));
const L = globalThis.LAB;

const DEFAULT = "sandearth";
// site order + names (the lab names some themes for comparison; users see these)
const SITE = [
  ["sandearth", "Sand & Earth"], ["jetdark", "Jet"], ["earth", "Earth"], ["sandstone", "Sandstone"], ["jet", "Jet & Paper"],
  ["fieldday", "Field Day"], ["pine", "Pine & Brass"], ["anodized", "Anodized"], ["meadow", "Meadow"], ["forestfloor", "Forest Floor"],
  ["olivelinen", "Olive & Linen"], ["carbon", "Carbon"], ["onyx", "Onyx & Gold"], ["smoke", "Smoke"], ["knight", "Knight"],
  ["frost", "Nordic Frost"], ["chalk", "Chalk & Slate"], ["harbor", "Harbor"],
];
const BLURB = { sandearth: "Warm sandstone paper by day, bark and umber at night, with moss green only where you are.",
  harbor: "The original look: slate-teal paper, a deep teal sidebar, Archivo.",
  jet: "True black at night, clean white Paper by day. 'Us' is the one inverse tag.",
  jetdark: "True black, graphite hairlines, colour only for match data. Always dark." };
const themes = SITE.map(([id, name]) => { const t = L.THEMES.find(x => x.id === id); if (!t) throw new Error("missing theme " + id); return { ...t, siteName: name }; });
const LAYOUTS = [
  { id: "sidebar", name: "Sidebar", blurb: "Navigation down the left, toolbar on top." },
  { id: "topbar", name: "Top bar", blurb: "Navigation across the top, wider pages below." },
  { id: "command", name: "Command", blurb: "Icon rail and a denser dashboard grid for a big laptop. Phones and small screens keep the stacked layout." },
];

// ------------------------------------------------------------------ fonts (one file per family; Saira is shared)
const faces = new Map();
for (const t of themes) for (const k of t.fonts || []) for (const f of L.FONTS[k]) if (!faces.has(f.family)) faces.set(f.family, f);
mkdirSync(join(web, "fonts"), { recursive: true });
let fontBytes = 0;
for (const f of faces.values()) { copyFileSync(join(lab, "fonts", f.file), join(web, "fonts", f.file)); fontBytes += readFileSync(join(lab, "fonts", f.file)).length; }
const fontCSS = [...faces.values()].map(f => `@font-face { font-family: "${f.family}"; font-style: normal; font-weight: ${f.weight};${f.stretch ? ` font-stretch: ${f.stretch};` : ""} font-display: swap; src: url("fonts/${f.file}") format("woff2"); }`).join("\n");

// ------------------------------------------------------------------ tokens
const decl = (o, pad = "  ") => Object.entries(o || {}).map(([k, v]) => `${pad}--${k}: ${v};`).join("\n");
const LIGHT_D = { sel: "color-mix(in srgb, var(--accent) 9%, transparent)", hover: "color-mix(in srgb, var(--ink) 5%, transparent)" };
const DARK_D = { sel: "color-mix(in srgb, var(--accent) 12%, transparent)", hover: "color-mix(in srgb, var(--ink) 6%, transparent)",
  "shadow-pop": "0 2px 6px rgba(0, 0, 0, .25), 0 16px 40px rgba(0, 0, 0, .4)" };
const block = (sel, scheme, ...sets) => `${sel} {\n  color-scheme: ${scheme};\n${sets.filter(s => s && Object.keys(s).length).map(s => decl(s)).join("\n")}\n}`;
function themeCSS(t) {
  const id = t.id, isDef = id === DEFAULT, S = `html[data-style="${id}"]`;
  const SC = isDef ? `html:is([data-style="${id}"], :not([data-style]))` : S;     // the default also styles a page without JS
  const first = t.light ? "light" : "dark", both = !!(t.light && t.dark), out = [`/* ---- ${t.siteName}: ${(BLURB[id] || t.blurb).replace(/\*\//g, "")} */`];
  if (isDef) {
    out.push(block(":root", "light", t.light, LIGHT_D));
    out.push(`${SC} {\n${decl(t.base)}\n}`);
  } else out.push(block(S, first, t.base, t[first], first === "light" ? LIGHT_D : DARK_D));
  if (both) {
    out.push(block(`${S}[data-mode="dark"]`, "dark", t.dark, DARK_D));
    out.push(`@media (prefers-color-scheme: dark) {\n  ${isDef ? `html:is([data-style="${id}"][data-mode="auto"], :not([data-style]))` : `${S}[data-mode="auto"]`} {\n    color-scheme: dark;\n${decl(t.dark, "    ")}\n${decl(DARK_D, "    ")}\n  }\n}`);
  }
  if (t.rules) {
    const lines = t.rules.trim().split("\n"), plain = [], darkOnly = [];
    for (const ln of lines) (ln.includes(':T[data-mode="dark"]') ? darkOnly : plain).push(ln);
    out.push(plain.join("\n").replace(/:T/g, SC));
    if (darkOnly.length) {   // lab rules for an explicit dark choice also apply to "auto" when the system is dark
      out.push(darkOnly.join("\n").replace(/:T\[data-mode="dark"\]/g, `${S}[data-mode="dark"]`));
      out.push(`@media (prefers-color-scheme: dark) {\n${darkOnly.join("\n").replace(/:T\[data-mode="dark"\]/g, `${S}[data-mode="auto"]`)}\n}`);
    }
  }
  return out.join("\n");
}

// ------------------------------------------------------------------ layouts (lab.css TOP BAR, ext/b.css COMMAND)
const labCSS = readFileSync(join(lab, "lab.css"), "utf8"), bCSS = readFileSync(join(lab, "ext/b.css"), "utf8");
const topbar = labCSS.slice(labCSS.indexOf("/* ------------------------------------------------------------------ layout: TOP BAR"), labCSS.indexOf("/* ------------------------------------------------------------------ layout: COMPACT"));
const command = bCSS.slice(bCSS.indexOf("/* ------------------------------------------------------------------ layout: COMMAND"));
const layoutCSS = (topbar + "\n" + command).replace(/html\[data-theme\]/g, "html[data-style]").trim();

const css = `/* =============================================================================================
 * Cyber-Scout themes + layouts. GENERATED by lab/tools/site-themes.mjs from the design lab: edit the lab, then rebuild.
 * Picked per browser on the Settings page (#/settings); theme.js sets <html data-style data-mode data-layout>
 * before first paint. Default = "Sand & Earth" (also on plain :root, so the site is styled without JS).
 * Every theme keeps red / blue for the alliances only; text pairs >= 4.5:1 (lab/gallery.html has the tables).
 * Fonts: SIL Open Font License 1.1, self-hosted in fonts/ (Latin subsets). Only the active theme's faces download.
 * ============================================================================================= */
${fontCSS}

${themes.map(themeCSS).join("\n\n")}

@media (prefers-contrast: more) {
  :root:is([data-style][data-mode][data-mode], :not([data-style])) { --ink-2: var(--ink); --ink-3: var(--ink); --line: var(--line-strong); --plate-ink-2: var(--plate-ink); --plate-ink-3: var(--plate-ink); }
}
:focus-visible { outline-color: var(--focus, var(--accent)); }
.sidebar :focus-visible { outline-color: var(--plate-ink); }

/* =============================================================================================
 * Layouts: html[data-layout="topbar" | "command"] ("sidebar" = style.css as is). A layout decides size and placement;
 * its selectors carry [data-style] so they outrank theme rules. Phones (<= 760px) always get the stock layout.
 * ============================================================================================= */
${layoutCSS}
`;
writeFileSync(join(web, "themes.css"), css);

// ------------------------------------------------------------------ boot script + catalog
const pick = (k, m) => k && { bg: k.bg, surface: k.surface, plate: k.plate, ink: k.ink, accent: k["accent-fill"], red: k["red-fill"], blue: k["blue-fill"] };
const catalog = themes.map(t => ({ id: t.id, name: t.siteName, blurb: BLURB[t.id] || t.blurb, modes: [t.light && "light", t.dark && "dark"].filter(Boolean),
  sw: { light: pick(t.light), dark: pick(t.dark) } }));
const js = `/* theme.js: GENERATED by lab/tools/site-themes.mjs (edit the template there). Runs in <head> before the CSS paints:
 * reads the per-browser look from localStorage (cs:style, cs:mode, cs:layout) and sets <html data-style data-mode
 * data-layout> plus the theme-color / color-scheme metas, so a reload never flashes the default look.
 * External file because the CSP is script-src 'self'. Storage can throw (private windows, blocked site data): then the
 * defaults apply. window.CSStyle is the API the Settings page uses. */
(function () {
  "use strict";
  var THEMES = ${JSON.stringify(catalog)};
  var LAYOUTS = ${JSON.stringify(LAYOUTS)};
  var DEF = { style: "${DEFAULT}", mode: "auto", layout: "sidebar" };
  var MODES = ["auto", "light", "dark"];
  var K = { style: "cs:style", mode: "cs:mode", layout: "cs:layout" };
  var find = function (list, id) { for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i]; return null; };
  var get = function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } };
  var put = function (k, v, dflt) { try { if (v == null || v === dflt) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { } };
  var state = {};
  function load() {
    state.style = find(THEMES, get(K.style)) ? get(K.style) : DEF.style;
    state.mode = MODES.indexOf(get(K.mode)) >= 0 ? get(K.mode) : DEF.mode;
    state.layout = find(LAYOUTS, get(K.layout)) ? get(K.layout) : DEF.layout;
  }
  // the appearance actually shown: single-appearance themes are locked to theirs
  function effMode() { var t = find(THEMES, state.style); return t.modes.length === 1 ? t.modes[0] : state.mode; }
  function apply() {
    var t = find(THEMES, state.style), m = effMode(), h = document.documentElement;
    h.setAttribute("data-style", t.id); h.setAttribute("data-mode", m); h.setAttribute("data-layout", state.layout);
    var cs = document.querySelector('meta[name="color-scheme"]');
    if (cs) cs.setAttribute("content", m === "auto" ? "light dark" : m);
    var bgL = (t.sw[m === "dark" ? "dark" : "light"] || t.sw.dark).bg, bgD = (t.sw[m === "light" ? "light" : "dark"] || t.sw.light).bg;
    var tc = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < tc.length; i++) tc[i].setAttribute("content", /dark/.test(tc[i].getAttribute("media") || "") ? bgD : bgL);
  }
  function changed() { try { document.dispatchEvent(new CustomEvent("cs:style", { detail: api.get() })); } catch (e) { } }
  load(); apply();

  var api = window.CSStyle = {
    themes: THEMES, layouts: LAYOUTS, defaults: DEF,
    get: function () { return { style: state.style, mode: state.mode, layout: state.layout, effectiveMode: effMode() }; },
    set: function (patch) {
      patch = patch || {};
      if (patch.style && find(THEMES, patch.style)) { state.style = patch.style; put(K.style, state.style, DEF.style); }
      if (patch.mode && MODES.indexOf(patch.mode) >= 0) { state.mode = patch.mode; put(K.mode, state.mode, DEF.mode); }
      if (patch.layout && find(LAYOUTS, patch.layout)) { state.layout = patch.layout; put(K.layout, state.layout, DEF.layout); }
      apply(); changed();
    },
    reset: function () { put(K.style, null); put(K.mode, null); put(K.layout, null); load(); apply(); changed(); }
  };
  // another tab changed the look: follow along
  window.addEventListener("storage", function (e) { if (e.key === null || e.key === K.style || e.key === K.mode || e.key === K.layout) { load(); apply(); changed(); } });
})();
`;
writeFileSync(join(web, "theme.js"), js);
console.log(`web/themes.css ${(Buffer.byteLength(css) / 1024).toFixed(1)} KB, web/theme.js ${(Buffer.byteLength(js) / 1024).toFixed(1)} KB, ${faces.size} font files ${(fontBytes / 1024).toFixed(0)} KB in web/fonts/`);
