/* Design lab extension slot "c": push extra themes / layouts / fonts into the lab registry (themes.js).
 *   LAB.THEMES.push({ id, name, blurb, why, fonts: [...], base: {...}, light: {...}, dark: {...}, rules: "..." });
 *   LAB.LAYOUTS.push({ id, name, blurb });   // its CSS goes in ext/c.css, scoped to html[data-theme][data-layout="<id>"]
 *   LAB.FONTS["key"] = [{ family, file, weight, stretch }];   // woff2 in lab/fonts/
 * Same token keys and rules conventions as the built-in themes in themes.js.
 *
 * Slot c = the wildcards: three directions that are neither earthy green nor black.
 *   knight  Knight         oxblood banner + plate steel; 'us' is a steel shield, never red        (Saira, width axis)
 *   frost   Nordic Frost   frost paper + heather plum; a light, low-glare day theme with a dusk    (Familjen Grotesk)
 *   chalk   Chalk & Slate  the strategy whiteboard: graph paper / slate board, 'us' is hatched     (Gabarito)
 * plus one layout: "mobile" (Mobile-first), a single column with a sticky match switcher at the bottom. */
(function (L) {
  "use strict";
  if (!L) return;
  const SANS = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  // fonts: Latin subsets from Google Fonts, SIL OFL 1.1 (Saira: Omnibus-Type; Familjen Grotesk: Familjen STHLM; Gabarito: Naipe Foundry)
  L.FONTS["c-saira"] = [{ family: "Saira", file: "c-saira.woff2", weight: "100 900", stretch: "50% 125%" }];
  L.FONTS["c-familjen"] = [{ family: "Familjen Grotesk", file: "c-familjen.woff2", weight: "400 700" }];
  L.FONTS["c-gabarito"] = [{ family: "Gabarito", file: "c-gabarito.woff2", weight: "400 900" }];

  // shared 'us' tag recipe (filled chip, like Jet/Sandstone) — each theme then adds its own mark
  const US_FILL = `
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 50%, transparent); }`;

  L.THEMES.push(
    // ------------------------------------------------------------------------------------------ knight
    {
      id: "knight", name: "Knight", defaultMode: "dark",
      blurb: "CyberKnights heraldry: an oxblood banner for the sidebar, plate-steel greys, chamfered corners like armour. Light = pewter.",
      why: "Oxblood stays in the chrome and never touches data; 'us' is a steel shield, so the only true red on screen is the red alliance.",
      fonts: ["c-saira"],
      base: { font: `"Saira", ${SANS}`, "font-num": `"Saira", ${SANS}`, "num-stretch": "72%", "r-tag": "2px", "r-s": "3px", "r-p": "4px", "r-m": "4px" },
      light: {
        bg: "#e9e7e6", surface: "#fbfafa", "surface-2": "#f3f1f0", "surface-3": "#e5e2e0", line: "#dbd7d5", "line-strong": "#aea7a4",
        ink: "#1e1a1b", "ink-2": "#4a4345", "ink-3": "#5e5759",
        accent: "#343c44", "accent-fill": "#343c44", "on-accent": "#ffffff", "accent-tint": "#e1e5e9", "accent-line": "#8c97a2", focus: "#343c44",
        plate: "#4a1824", "plate-2": "#5f2331", "plate-ink": "#ffffff", "plate-ink-2": "#edd5d9", "plate-ink-3": "#d6b3b9", "plate-accent": "#dfe5ea",
        red: "#c2161f", blue: "#1a56c4", "red-fill": "#cf1d26", "blue-fill": "#1f61d4", "red-tint": "#f8e0df", "blue-tint": "#e0e8f7",
        caution: "#855100", "caution-tint": "#f4e8d2", "caution-line": "#cfa55a", good: "#17733a", bad: "#c2161f",
        spark: "#343c44", "field-bg": "#36383c",
      },
      dark: {
        bg: "#1a1517", surface: "#241d20", "surface-2": "#2c2428", "surface-3": "#362d31", line: "#3d3337", "line-strong": "#5d5055",
        ink: "#f2eef0", "ink-2": "#c9c0c4", "ink-3": "#aba1a5",
        accent: "#d5dce2", "accent-fill": "#c9d1d8", "on-accent": "#15181b", "accent-tint": "#30363c", "accent-line": "#76818b", focus: "#d5dce2",
        plate: "#2c1017", "plate-2": "#461a24", "plate-ink": "#f7eff1", "plate-ink-2": "#dcc4c9", "plate-ink-3": "#bf9ca3", "plate-accent": "#d5dce2",
        red: "#ff7a72", blue: "#80b0ff", "red-fill": "#d6262d", "blue-fill": "#2a66dc", "red-tint": "#4b1f1f", "blue-tint": "#1c2a45",
        caution: "#f0b44c", "caution-tint": "#382c1b", "caution-line": "#7d5f27", good: "#6dd295", bad: "#ff7a72",
        spark: "#d5dce2", "field-bg": "#2d2b2e",
      },
      rules: `
/* the banner: oxblood plate edged in steel; the current section gets a steel bar, like a sword on the standard */
:T .sidebar { border-right: 1px solid color-mix(in srgb, var(--plate-accent) 22%, transparent); }
:T .sb-brand { font-stretch: 88%; font-weight: 700; letter-spacing: .03em; }
:T .sb-h { letter-spacing: .04em; }
:T .sb-item[aria-current="page"] { background: var(--plate-2); box-shadow: inset 3px 0 0 var(--plate-accent); border-radius: 0 var(--r-s) var(--r-s) 0; }
:T h1, :T .qid h2 { font-weight: 700; letter-spacing: .01em; }
:T .qid h2 { font-size: 36px; }
:T .qb-n { font-weight: 600; font-size: 78px; letter-spacing: -.01em; }
:T .cd > span { font-weight: 500; font-size: 46px; letter-spacing: .02em; }
:T .oe dd, :T .rl-r b, :T .dchip b { font-weight: 600; }
/* armour plate: the queue card and the 'us' marks are chamfered (top-left / bottom-right), nothing else is */
:T .qcard { --ch: 18px; border-color: transparent; clip-path: polygon(var(--ch) 0, 100% 0, 100% calc(100% - var(--ch)), calc(100% - var(--ch)) 100%, 0 100%, 0 var(--ch));
  box-shadow: inset 0 2px 0 var(--accent-line); }
:T .weare { clip-path: polygon(5px 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%, 0 5px); border-radius: 0; padding: 2px 9px; letter-spacing: .03em; }
${US_FILL}
:T .dchip.us { --ch: 6px; border-radius: 0; clip-path: polygon(var(--ch) 0, 100% 0, 100% calc(100% - var(--ch)), calc(100% - var(--ch)) 100%, 0 100%, 0 var(--ch)); }
:T .dchip.us::before { content: ""; flex: none; width: 9px; height: 11px; background: currentColor; clip-path: polygon(0 0, 100% 0, 100% 52%, 50% 100%, 0 52%); }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
:T .ltrack .we, :T .lk .we { border-radius: 1px; transform: rotate(45deg); box-shadow: 0 0 0 2px var(--surface); }
:T .dsec, :T .side { border-color: var(--line); }
:T .oe-spark, :T .trends2 { color: var(--spark); }`,
    },

    // ------------------------------------------------------------------------------------------ nordic frost
    {
      id: "frost", name: "Nordic Frost",
      blurb: "Frost-white paper with a lilac chill, a pale frosted sidebar and heather plum for 'us'. Evening = dusk heather, not black.",
      why: "A cool light theme that isn't blue: heather plum sits far from both alliances, and the soft frost ground keeps glare down in bright venues.",
      fonts: ["c-familjen"],
      base: { font: `"Familjen Grotesk", ${SANS}`, "font-num": `"Familjen Grotesk", ${SANS}`, "num-stretch": "100%", "r-tag": "6px", "r-s": "8px", "r-p": "12px", "r-m": "18px" },
      light: {
        bg: "#edecf1", surface: "#fcfbfd", "surface-2": "#f4f3f7", "surface-3": "#e7e5ec", line: "#dedbe5", "line-strong": "#b2adbd",
        ink: "#1e1a26", "ink-2": "#4a4557", "ink-3": "#5d586b",
        accent: "#7b3a6d", "accent-fill": "#7b3a6d", "on-accent": "#ffffff", "accent-tint": "#f1e3ee", "accent-line": "#c99bbd", focus: "#7b3a6d",
        plate: "#e2dfe8", "plate-2": "#d4cfdd", "plate-ink": "#1e1a26", "plate-ink-2": "#433d50", "plate-ink-3": "#554f63", "plate-accent": "#7b3a6d",
        red: "#b51f2b", blue: "#1d55b9", "red-fill": "#c32532", "blue-fill": "#2361ca", "red-tint": "#f7e2e4", "blue-tint": "#e2e8f6",
        caution: "#845300", "caution-tint": "#f5ebd5", "caution-line": "#cfa85e", good: "#1d713f", bad: "#b51f2b",
        spark: "#7b3a6d", "field-bg": "#403c48",
      },
      dark: {
        bg: "#201c25", surface: "#28232e", "surface-2": "#302a37", "surface-3": "#393241", line: "#3f3848", "line-strong": "#5c5368",
        ink: "#f4f1f6", "ink-2": "#cdc6d4", "ink-3": "#b4abbd",
        accent: "#dcb0e0", "accent-fill": "#d4a2d9", "on-accent": "#2a1230", "accent-tint": "#42324a", "accent-line": "#8c6492", focus: "#dcb0e0",
        plate: "#1c1920", "plate-2": "#302b36", "plate-ink": "#f4f1f6", "plate-ink-2": "#c9c2d1", "plate-ink-3": "#a79fb1", "plate-accent": "#dcb0e0",
        red: "#ff8a7a", blue: "#8cb6ff", "red-fill": "#dc2f38", "blue-fill": "#3a6fe0", "red-tint": "#4b2a2f", "blue-tint": "#253150",
        caution: "#f2be6a", "caution-tint": "#433829", "caution-line": "#85683a", good: "#85d9a5", bad: "#ff8a7a",
        spark: "#dcb0e0", "field-bg": "#3a3540",
      },
      rules: `
/* frosted plate: same paper family as the page, set apart by a hairline; the current section is a raised pill */
:T .sidebar { border-right: 1px solid var(--line); }
:T .sb-brand { font-weight: 700; letter-spacing: -.01em; }
:T .sb-item:hover { background: color-mix(in srgb, var(--plate-ink) 6%, transparent); }
:T .sb-item[aria-current="page"] { background: var(--surface); color: var(--plate-ink); box-shadow: 0 1px 2px color-mix(in srgb, var(--ink) 10%, transparent); }
:T[data-mode="dark"] .sb-item[aria-current="page"] { background: var(--plate-2); }
:T .sb-item[aria-current="page"] svg { color: var(--plate-accent); }
:T h1, :T .qid h2, :T .qb-n, :T .cd > span, :T .oe dd { letter-spacing: -.025em; }
:T .qid h2 { font-weight: 700; font-size: 34px; }
:T .qb-n { font-weight: 700; font-size: 74px; }
:T .cd > span { font-weight: 500; }
:T .qcard { border-color: transparent; box-shadow: 0 0 0 1px color-mix(in srgb, var(--line) 70%, transparent), 0 24px 48px -36px color-mix(in srgb, var(--accent) 55%, transparent); }
/* heather marks 'us' twice: the filled chip, and a plum edge on our alliance row */
${US_FILL}
:T .qteams > .aline:first-child { box-shadow: inset 4px 0 0 var(--accent); }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent-line); box-shadow: inset 0 0 0 1px var(--accent-line); }
:T .weare { border-radius: 999px; padding: 2px 10px; }
:T .dsec, :T .side { border-color: var(--line); }
:T .oe-spark, :T .trends2 { color: var(--spark); }`,
    },

    // ------------------------------------------------------------------------------------------ chalk & slate
    {
      id: "chalk", name: "Chalk & Slate", defaultMode: "light",
      blurb: "The drive team's whiteboard: graph-paper page, a slate board for the sidebar, graphite ink. Dark = the slate itself, written in chalk.",
      why: "Pencil-and-chalk greys leave red and blue as the only marker colours; 'us' is hatched like a shaded box on a play sheet, so it never needs a hue.",
      fonts: ["c-gabarito"],
      base: { font: `"Gabarito", ${SANS}`, "font-num": `"Gabarito", ${SANS}`, "num-stretch": "100%", "r-tag": "3px", "r-s": "5px", "r-p": "7px", "r-m": "10px" },
      light: {
        bg: "#ebeae6", surface: "#fbfbf9", "surface-2": "#f3f2ee", "surface-3": "#e5e4df", line: "#d9d8d2", "line-strong": "#abaaa3",
        ink: "#23272b", "ink-2": "#4b5056", "ink-3": "#5e6369",
        accent: "#2f353b", "accent-fill": "#2f353b", "on-accent": "#fbfbf9", "accent-tint": "#e2e4e5", "accent-line": "#868c92", focus: "#2f353b",
        plate: "#33393d", "plate-2": "#41484d", "plate-ink": "#f4f3ef", "plate-ink-2": "#d0d2cf", "plate-ink-3": "#acb0ae", "plate-accent": "#f4f3ef",
        red: "#c0232c", blue: "#1b58c0", "red-fill": "#cb2a33", "blue-fill": "#2062cf", "red-tint": "#f7e1df", "blue-tint": "#e0e7f4",
        caution: "#835300", "caution-tint": "#f3e9d3", "caution-line": "#cba65e", good: "#1c703c", bad: "#c0232c",
        spark: "#2f353b", "field-bg": "#3a3f42",
      },
      dark: {
        bg: "#212528", surface: "#272c2e", "surface-2": "#2e3436", "surface-3": "#373d40", line: "#3d4447", "line-strong": "#5a6266",
        ink: "#f1f0ec", "ink-2": "#cacbc7", "ink-3": "#b0b2af",
        accent: "#f1f0ec", "accent-fill": "#ecebe6", "on-accent": "#23272a", "accent-tint": "#3b4245", "accent-line": "#868d90", focus: "#f1f0ec",
        plate: "#191c1e", "plate-2": "#2b3134", "plate-ink": "#f1f0ec", "plate-ink-2": "#c6c8c4", "plate-ink-3": "#a2a6a4", "plate-accent": "#f1f0ec",
        red: "#ff8279", blue: "#89b4ff", "red-fill": "#dc2f38", "blue-fill": "#3a6fe0", "red-tint": "#4a2c2f", "blue-tint": "#24344c",
        caution: "#f2c063", "caution-tint": "#403a2b", "caution-line": "#84703d", good: "#8cd79f", bad: "#ff8279",
        spark: "#f1f0ec", "field-bg": "#30373a",
      },
      rules: `
/* graph paper under everything (5 mm grid, 2 % ink); cards sit on it as clean sheets */
:T body { background-color: var(--bg);
  background-image: linear-gradient(color-mix(in srgb, var(--ink) 6%, transparent) 1px, transparent 1px), linear-gradient(90deg, color-mix(in srgb, var(--ink) 6%, transparent) 1px, transparent 1px);
  background-size: 20px 20px; background-position: -1px -1px; }
:T .toolbar { background: color-mix(in srgb, var(--bg) 92%, transparent); }
/* the slate: a chalk ledge along the bottom edge of the board */
:T .sidebar { box-shadow: inset -1px 0 0 color-mix(in srgb, var(--plate-ink) 10%, transparent); }
:T .sb-brand { font-weight: 800; letter-spacing: -.01em; }
:T .sb-item[aria-current="page"] { background: transparent; box-shadow: none; }
:T .sb-item[aria-current="page"] span { text-decoration: underline 2px var(--plate-accent); text-underline-offset: 5px; text-decoration-skip-ink: none; }
:T h1, :T .qid h2 { font-weight: 800; letter-spacing: -.02em; }
:T .qb-n { font-weight: 800; font-size: 72px; letter-spacing: -.03em; }
:T .cd > span { font-weight: 600; letter-spacing: -.01em; }
:T .qcard, :T .side, :T .dsec { border-color: var(--line-strong); }
/* 'us' is hatched: diagonal pencil shading inside an inked outline (reads without colour, prints in greyscale) */
:T { --hatch: repeating-linear-gradient(-45deg, var(--accent) 0 1.5px, transparent 1.5px 4.5px); }
/* the shading sits in a band beside the text, never under it */
:T .dchip.us { background: var(--hatch) left top / 9px 100% no-repeat, var(--surface); border: 2px solid var(--accent); box-shadow: none; padding-left: 15px; }
:T .dchip.us .ustag { color: var(--ink); }
:T .rl-m.upc.on { background: var(--hatch) left bottom / 100% 7px no-repeat, var(--surface); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); padding-bottom: 12px; }
:T .ltrack .we, :T .lk .we { background: var(--hatch), var(--surface); box-shadow: 0 0 0 2px var(--accent); }
:T .weare { border-radius: var(--r-tag); }
:T .oe-spark, :T .trends2 { color: var(--spark); }`,
    }
  );

  L.LAYOUTS.push({ id: "mobile", name: "Mobile-first", blurb: "One column of stacked cards with the match switcher pinned to the bottom, above the tab bar. Phone first; on a laptop it's a centred column." });
})(window.LAB);
