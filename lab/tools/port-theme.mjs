#!/usr/bin/env node
// Print a lab theme as a drop-in for web/: token blocks for web/style.css + theme rules (+ @font-face).
//   node lab/tools/port-theme.mjs <theme-id> [--inline] [--style path/to/style.css] > theme.css
//   --inline   embed the woff2 fonts as base64 data: URLs (how web/fonts.css ships Archivo, so it works offline)
//   ids: harbor earth jet fieldday pine anodized sandstone
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url)), lab = join(here, "..");
const L = createRequire(import.meta.url)(join(lab, "themes.js"));
const args = process.argv.slice(2), id = args.find(a => !a.startsWith("--"));
const t = L.THEMES.find(x => x.id === id);
if (!t) { console.error(`usage: port-theme.mjs <${L.THEMES.map(x => x.id).join("|")}> [--inline] [--style web/style.css]`); process.exit(2); }
const si = args.indexOf("--style"), stylePath = si >= 0 ? args[si + 1] : join(lab, "..", "web", "style.css");
let style = ""; try { style = readFileSync(stylePath, "utf8"); } catch (e) { console.error(`(no ${stylePath}: emitting a bare :root block)`); }
const inline = args.includes("--inline");
const fontSrc = inline ? f => "data:font/woff2;base64," + readFileSync(join(lab, "fonts", f.file)).toString("base64") : null;
process.stdout.write(L.port(t, style, fontSrc));
