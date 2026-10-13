/* Design lab extension slot "a": push extra themes / layouts / fonts into the lab registry (themes.js).
 *   LAB.THEMES.push({ id, name, blurb, why, fonts: [...], base: {...}, light: {...}, dark: {...}, rules: "..." });
 *   LAB.LAYOUTS.push({ id, name, blurb });   // its CSS goes in ext/a.css, scoped to html[data-theme][data-layout="<id>"]
 *   LAB.FONTS["key"] = [{ family, file, weight, stretch }];   // woff2 in lab/fonts/
 * Same token keys and rules conventions as the built-in themes in themes.js.
 *
 * Slot a = the earthy family (green + cream, three ways), plus the "Field notebook" layout.
 *   Meadow        light sage paper, a pale sage sidebar (no dark plate), fern 'us'.      Lexend.
 *   Forest Floor  dark only: olive-umber humus, a moss sidebar, lichen 'us'.              Geologica.
 *   Olive & Linen light only: linen paper inside one olive-drab frame (sidebar + toolbar). Fraunces numerals, Hanken Grotesk text.
 * Fonts: Latin subsets from Google Fonts, SIL Open Font License 1.1 (Lexend: Bonnie Shaver-Troup & Thomas Jockin;
 * Geologica: Monokrom; Fraunces: Undercase Type; Hanken Grotesk: Hanken Design Co.). Variable woff2, no CDN at runtime. */
(function (L) {
  "use strict";
  if (!L) return;
  const SANS = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  const SERIF = 'ui-serif, Georgia, "Times New Roman", serif';

  Object.assign(L.FONTS, {
    "a-lexend": [{ family: "Lexend", file: "a-lexend.woff2", weight: "100 900" }],
    "a-geologica": [{ family: "Geologica", file: "a-geologica.woff2", weight: "100 900" }],
    "a-fraunces": [{ family: "Fraunces", file: "a-fraunces.woff2", weight: "100 900" }],
    "a-hanken": [{ family: "Hanken Grotesk", file: "a-hanken-grotesk.woff2", weight: "100 900" }],
  });

  L.THEMES.push(
    // ------------------------------------------------------------------------------------------ meadow
    {
      id: "meadow", name: "Meadow",
      blurb: "Sage and oat in daylight: the sidebar is a pale sage page, not a dark plate. Fern green marks 'us'. Dusk = grey-green, never black.",
      why: "The lightest earthy option for bright pits and phones in the sun; Lexend's open, evenly spaced letters were drawn for reading ease at a glance.",
      fonts: ["a-lexend"],
      base: { font: `"Lexend", ${SANS}`, "font-num": `"Lexend", ${SANS}`, "num-stretch": "100%",
        "r-tag": "6px", "r-s": "8px", "r-p": "11px", "r-m": "16px" },
      light: {
        bg: "#e7ebdc", surface: "#fbfcf5", "surface-2": "#f1f4e8", "surface-3": "#e2e7d4", line: "#d5dbc5", "line-strong": "#a6ae93",
        ink: "#1c281e", "ink-2": "#455244", "ink-3": "#566252",
        accent: "#2a6638", "accent-fill": "#2a6638", "on-accent": "#fbfcf5", "accent-tint": "#dbe8d0", "accent-line": "#8db38a", focus: "#2a6638",
        plate: "#d8dfca", "plate-2": "#c6d1b3", "plate-ink": "#1c281e", "plate-ink-2": "#384536", "plate-ink-3": "#4a5747", "plate-accent": "#2a6638",
        red: "#b0252b", blue: "#1c55b6", "red-fill": "#bf2e2e", "blue-fill": "#2360c6", "red-tint": "#f4e0da", "blue-tint": "#dde6f1",
        caution: "#7a5100", "caution-tint": "#f1e5c3", "caution-line": "#c6a356", good: "#2f6a1d", bad: "#b0252b",
        spark: "#2a6638", "field-bg": "#3d4b3b",
        "sb-cur": "#fbfcf5", "sb-cur-shadow": "0 1px 2px rgba(28, 40, 30, .14)",
      },
      dark: {
        bg: "#181e1a", surface: "#1f2621", "surface-2": "#27302a", "surface-3": "#303a33", line: "#333d36", "line-strong": "#515d55",
        ink: "#edf1ea", "ink-2": "#c3ccc2", "ink-3": "#a5afa5",
        accent: "#a3d9a5", "accent-fill": "#95cf98", "on-accent": "#0f2013", "accent-tint": "#2a3d2e", "accent-line": "#55805b", focus: "#a3d9a5",
        plate: "#242d27", "plate-2": "#313c34", "plate-ink": "#edf1ea", "plate-ink-2": "#c3ccc2", "plate-ink-3": "#a1aca2", "plate-accent": "#a3d9a5",
        red: "#fb8a80", blue: "#93b6f7", "red-fill": "#cd3a33", "blue-fill": "#2f68dc", "red-tint": "#472a26", "blue-tint": "#24324a",
        caution: "#e8b95c", "caution-tint": "#3a3322", "caution-line": "#7c6532", good: "#bcd77e", bad: "#fb8a80",
        spark: "#a3d9a5", "field-bg": "#3d4b3b",
        "sb-cur": "#313c34", "sb-cur-shadow": "none",
      },
      rules: `
:T .sidebar { border-right: 1px solid var(--line-strong); }
:T .sb-brand { letter-spacing: -.01em; }
:T .sb-item:hover { background: color-mix(in srgb, var(--plate-ink) 7%, transparent); }
:T .sb-item[aria-current="page"] { background: var(--sb-cur); color: var(--plate-ink); box-shadow: var(--sb-cur-shadow); }
:T h1, :T .qid h2 { font-weight: 700; letter-spacing: -.015em; }
:T .qb-n { font-weight: 800; letter-spacing: -.03em; }
:T .cd > span { font-weight: 700; letter-spacing: -.01em; }
:T .qcard { border-color: var(--line); box-shadow: 0 1px 1px color-mix(in srgb, var(--ink) 6%, transparent), 0 10px 24px -18px color-mix(in srgb, var(--ink) 40%, transparent); }
:T .dsec { border-color: var(--line); }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 55%, transparent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface); }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T ::selection { background: color-mix(in srgb, var(--accent) 24%, transparent); }`,
    },

    // ------------------------------------------------------------------------------------------ forest floor
    {
      id: "forestfloor", name: "Forest Floor", defaultMode: "dark",
      blurb: "Dark only: humus and moss under a canopy. Olive-umber ground, a deeper moss sidebar, birch-cream type, lichen marks 'us'.",
      why: "A dark earthy theme that is green-brown rather than black or pine: low glare in dim stands, and lichen can't be read as an alliance.",
      fonts: ["a-geologica"],
      base: { font: `"Geologica", ${SANS}`, "font-num": `"Geologica", ${SANS}`, "num-stretch": "100%",
        "r-tag": "4px", "r-s": "6px", "r-p": "8px", "r-m": "12px" },
      light: null,
      dark: {
        bg: "#1e1f15", surface: "#26281c", "surface-2": "#2d2f22", "surface-3": "#36382a", line: "#3a3c2d", "line-strong": "#585a44",
        ink: "#ede7d3", "ink-2": "#c8c3aa", "ink-3": "#aaa78e",
        accent: "#d3dc8c", "accent-fill": "#c6d27c", "on-accent": "#1a1d0c", "accent-tint": "#363c20", "accent-line": "#7b8440", focus: "#d3dc8c",
        plate: "#172015", "plate-2": "#253021", "plate-ink": "#ede7d3", "plate-ink-2": "#c2c8ad", "plate-ink-3": "#9ea78b", "plate-accent": "#d3dc8c",
        red: "#ff8b7c", blue: "#8fb5ff", "red-fill": "#d23f33", "blue-fill": "#3570e0", "red-tint": "#44261e", "blue-tint": "#202b42",
        caution: "#f2a95e", "caution-tint": "#3a2a19", "caution-line": "#87592b", good: "#a6d27e", bad: "#ff8b7c",
        spark: "#d3dc8c", "field-bg": "#2f3627",
      },
      rules: `
:T .sidebar { border-right: 1px solid var(--line); }
:T .sb-brand { font-weight: 600; letter-spacing: -.01em; }
:T .sb-item[aria-current="page"] { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--plate-accent) 28%, transparent); }
:T .toolbar { background: color-mix(in srgb, var(--bg) 86%, transparent); }
:T h1, :T .qid h2 { font-weight: 600; letter-spacing: -.02em; }
:T .qb-n { font-weight: 700; letter-spacing: -.035em; }
:T .cd > span { font-weight: 300; letter-spacing: -.02em; }
:T .oe dd { font-weight: 600; }
:T .qcard { border-color: var(--line); box-shadow: inset 0 1px 0 color-mix(in srgb, var(--ink) 7%, transparent), 0 20px 40px -28px #000; }
:T .dsec { border-color: var(--line); }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent-line); box-shadow: inset 0 0 0 1px var(--accent-line); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 50%, transparent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface); }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T ::selection { background: color-mix(in srgb, var(--accent) 30%, transparent); }`,
    },

    // ------------------------------------------------------------------------------------------ olive & linen
    {
      id: "olivelinen", name: "Olive & Linen", defaultMode: "light",
      blurb: "Light only: linen paper inside one olive-drab frame (sidebar and toolbar together). Soft Fraunces numerals like a seed almanac.",
      why: "Olive is the yellow side of green, so 'us' sits far from red, blue and teal; the serif numerals make the plan read like printed notes.",
      fonts: ["a-fraunces", "a-hanken"],
      base: { font: `"Hanken Grotesk", ${SANS}`, "font-num": `"Fraunces", ${SERIF}`, "num-stretch": "100%",
        "r-tag": "3px", "r-s": "5px", "r-p": "7px", "r-m": "10px" },
      light: {
        bg: "#f1ece0", surface: "#fcfaf3", "surface-2": "#f5f1e6", "surface-3": "#e9e3d3", line: "#e0d9c7", "line-strong": "#b8af94",
        ink: "#24241a", "ink-2": "#4e4d3b", "ink-3": "#5f5e4a",
        accent: "#56621c", "accent-fill": "#56621c", "on-accent": "#fcfaf3", "accent-tint": "#e7e9cb", "accent-line": "#a9b16a", focus: "#56621c",
        plate: "#3c401f", "plate-2": "#4b5029", "plate-ink": "#f7f2e3", "plate-ink-2": "#dcd9c0", "plate-ink-3": "#bfbd9f", "plate-accent": "#e3e69c",
        red: "#b0262a", blue: "#1f55b3", "red-fill": "#bd2e2b", "blue-fill": "#2560c4", "red-tint": "#f4ded6", "blue-tint": "#dfe5f0",
        caution: "#7a5000", "caution-tint": "#f2e3c0", "caution-line": "#c8a259", good: "#4a6a12", bad: "#b0262a",
        spark: "#56621c", "field-bg": "#4a4a35",
      },
      dark: null,
      rules: `
/* one olive frame: the toolbar takes the plate's tokens, like a linen page set into an olive cover */
:T .toolbar { --ink: var(--plate-ink); --ink-2: var(--plate-ink-2); --ink-3: var(--plate-ink-3); --line: var(--plate-2); --surface: var(--plate-2);
  --hover: color-mix(in srgb, var(--plate-ink) 10%, transparent); --focus: var(--plate-accent);
  background: var(--plate); color: var(--plate-ink); border-bottom-color: var(--plate-2); -webkit-backdrop-filter: none; backdrop-filter: none; }
:T .toolbar .timectl.replay { --ink: #24241a; --ink-2: #4e4d3b; }
:T .toolbar :focus-visible { outline-color: var(--plate-accent); }
:T .sidebar { border-right: 1px solid var(--plate-2); }
:T .sb-brand { font-weight: 600; font-size: 20px; letter-spacing: -.01em; font-variation-settings: "SOFT" 100; }
:T .sb-item[aria-current="page"] { box-shadow: none; }
:T .qid h2, :T .qb-n, :T .cd > span, :T .oe dd, :T .dchip b, :T .rl-r b, :T .rl-now b, :T .dgain, :T h1 { font-variation-settings: "SOFT" 100; }
:T .qid h2 { font-weight: 600; font-size: 36px; letter-spacing: -.015em; }
:T .qb-n { font-weight: 600; font-size: 72px; letter-spacing: -.03em; }
:T .cd > span { font-weight: 500; letter-spacing: -.01em; }
:T .oe dd { font-weight: 600; }
:T .dchip b, :T .rl-r b { font-weight: 650; }
:T .qcard { border-color: var(--line); box-shadow: 0 1px 0 var(--line), 0 14px 30px -26px color-mix(in srgb, var(--ink) 60%, transparent); }
:T .qhead { padding-bottom: var(--sp-3); border-bottom: 1px solid var(--line); }
:T .dsec { border-color: var(--line); }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 55%, transparent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface); }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T ::selection { background: color-mix(in srgb, var(--accent) 22%, transparent); }`,
    },
  );

  L.LAYOUTS.push({ id: "notebook", name: "Notebook",
    blurb: "Field notebook: one centred column with room to breathe, a slim nav strip, the plan's notes hung on a margin rule." });
})(window.LAB);
