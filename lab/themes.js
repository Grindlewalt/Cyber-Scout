/* Cyber-Scout design lab: theme definitions (single source of truth).
 * Read by lab.js (switcher, live CSS), gallery.html (contrast tables) and tools/port-theme.mjs (porting into web/).
 * Each theme = base (mode-independent: type, shape) + light and/or dark token sets + optional rules.
 * Rules use ":T" as the theme scope; the lab expands it to html[data-theme="<id>"], the port tool to "html". */
(function (root) {
  "use strict";

  // fonts live in lab/fonts/*.woff2 (Latin subsets, SIL OFL 1.1). Archivo is the existing web/fonts.css face.
  const FONTS = {
    bricolage: [{ family: "Bricolage Grotesque", file: "bricolage.woff2", weight: "200 800", stretch: "75% 100%" }],
    instrument: [{ family: "Instrument Sans", file: "instrument-sans.woff2", weight: "400 700", stretch: "75% 100%" }],
    mona: [{ family: "Mona Sans", file: "mona-sans.woff2", weight: "200 900", stretch: "75% 125%" }],
    chivo: [{ family: "Chivo", file: "chivo.woff2", weight: "100 900" }],
    plex: [{ family: "IBM Plex Sans", file: "plex-sans.woff2", weight: "100 700", stretch: "75% 100%" }],
    sofia: [{ family: "Sofia Sans", file: "sofia-sans.woff2", weight: "1 1000" }, { family: "Sofia Sans Condensed", file: "sofia-sans-condensed.woff2", weight: "1 1000" }],
  };
  const SANS = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  const THEMES = [
    // ------------------------------------------------------------------------------------------ current
    {
      id: "harbor", name: "Current (harbor teal)",
      blurb: "Today's web/style.css, unchanged: slate-teal paper, teal plate, Archivo.",
      why: "Baseline for comparison.",
      fonts: [], base: {},
      light: {
        bg: "#eef4f4", surface: "#ffffff", "surface-2": "#ebf2f2", "surface-3": "#dee8e9", line: "#d6e0e1", "line-strong": "#afbebe",
        ink: "#122229", "ink-2": "#435359", "ink-3": "#57666b",
        accent: "#007471", "accent-fill": "#007471", "on-accent": "#ffffff", "accent-tint": "#dcf2f0", "accent-line": "#7cc4bf", focus: "#007471",
        plate: "#0f3b3e", "plate-2": "#16494c", "plate-ink": "#ffffff", "plate-ink-2": "#c2dedd", "plate-ink-3": "#9ac0bf", "plate-accent": "#6ee0d5",
        red: "#c42b31", blue: "#1f5fd1", "red-fill": "#d6343a", "blue-fill": "#2f6fe4", "red-tint": "#fbe7e7", "blue-tint": "#e4ecfb",
        caution: "#8a5800", "caution-tint": "#f8eedb", "caution-line": "#d9b56d", good: "#1d7a3a", bad: "#c42b31",
        spark: "#122229", "field-bg": "#3b4a3f",
      },
      dark: {
        bg: "#122126", surface: "#1a2a2f", "surface-2": "#233339", "surface-3": "#2c3e44", line: "#314045", "line-strong": "#4c5e65",
        ink: "#edf3f5", "ink-2": "#bdcacf", "ink-3": "#a5b4b9",
        accent: "#61dbd1", "accent-fill": "#4acfc5", "on-accent": "#0b2023", "accent-tint": "#164545", "accent-line": "#2f8a85", focus: "#61dbd1",
        plate: "#0b282b", "plate-2": "#133639", "plate-ink": "#edf3f5", "plate-ink-2": "#bdd3d3", "plate-ink-3": "#94b1b1", "plate-accent": "#61dbd1",
        red: "#fb817f", blue: "#7daff9", "red-fill": "#d6343a", "blue-fill": "#2f6fe4", "red-tint": "#4a2a2c", "blue-tint": "#213654",
        caution: "#f2b84b", "caution-tint": "#3a3220", "caution-line": "#7a5c22", good: "#6bd68a", bad: "#fb817f",
        spark: "#edf3f5", "field-bg": "#3b4a3f",
      },
      rules: "",
    },

    // ------------------------------------------------------------------------------------------ earth
    {
      id: "earth", name: "Earth",
      blurb: "Moss and linen. Cream paper, a deep moss sidebar, ochre for replay. Evening = bark and forest, never black.",
      why: "The requested earthy green and cream, kept calm: green is the only accent, so 'us' and red/blue never compete.",
      fonts: ["bricolage"],
      base: { font: `"Bricolage Grotesque", ${SANS}`, "font-num": `"Bricolage Grotesque", ${SANS}`, "num-stretch": "78%", "r-tag": "5px", "r-s": "7px", "r-p": "10px", "r-m": "14px" },
      light: {
        bg: "#ece7d8", surface: "#faf7ee", "surface-2": "#f3eee1", "surface-3": "#e5dfcd", line: "#dbd3bf", "line-strong": "#b4ab92",
        ink: "#1f281b", "ink-2": "#4a5243", "ink-3": "#5d6455",
        accent: "#36633a", "accent-fill": "#36633a", "on-accent": "#faf7ee", "accent-tint": "#dfe8d1", "accent-line": "#97b487", focus: "#36633a",
        plate: "#24382a", "plate-2": "#304835", "plate-ink": "#f5f0e1", "plate-ink-2": "#d2d8c4", "plate-ink-3": "#aeb9a1", "plate-accent": "#cfe39a",
        red: "#b3261e", blue: "#1d55b5", "red-fill": "#bf2f25", "blue-fill": "#2560c4", "red-tint": "#f5ded5", "blue-tint": "#dde5f2",
        caution: "#83560a", "caution-tint": "#f1e3bf", "caution-line": "#c9a65a", good: "#4a7419", bad: "#b3261e",
        spark: "#36633a", "field-bg": "#3b4a3f",
      },
      dark: {
        bg: "#1c1a14", surface: "#25221b", "surface-2": "#2e2a22", "surface-3": "#38332a", line: "#3d382d", "line-strong": "#5b5444",
        ink: "#efe8d6", "ink-2": "#ccc3ad", "ink-3": "#aea58f",
        accent: "#aed08f", "accent-fill": "#9fc47f", "on-accent": "#18210f", "accent-tint": "#2f3a24", "accent-line": "#5f7b48", focus: "#aed08f",
        plate: "#16211a", "plate-2": "#223127", "plate-ink": "#efe8d6", "plate-ink-2": "#c6cdb9", "plate-ink-3": "#9fa995", "plate-accent": "#bfdc93",
        red: "#f28b7f", blue: "#8fb3f5", "red-fill": "#cf3a30", "blue-fill": "#2f6be0", "red-tint": "#46271f", "blue-tint": "#223049",
        caution: "#e3b75b", "caution-tint": "#3a3020", "caution-line": "#7a6230", good: "#b5d675", bad: "#f28b7f",
        spark: "#aed08f", "field-bg": "#3b4a3f",
      },
      rules: `
:T body { font-optical-sizing: auto; }
:T .sb-brand { letter-spacing: -.01em; }
:T .sidebar { border-right: 1px solid color-mix(in srgb, var(--plate-ink) 6%, transparent); }
:T .qid h2, :T .qb-n, :T .cd > span { letter-spacing: -.02em; }
:T .qb-n { font-weight: 800; }
:T .qcard { border-color: transparent; box-shadow: 0 1px 2px color-mix(in srgb, var(--ink) 10%, transparent), 0 12px 28px -20px color-mix(in srgb, var(--ink) 45%, transparent); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 55%, transparent); }
:T .rl-m.upc.on { box-shadow: inset 0 0 0 1px var(--accent), 0 6px 16px -10px var(--accent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface); }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T .dsec, :T .side { border-color: var(--line); }`,
    },

    // ------------------------------------------------------------------------------------------ jet
    {
      id: "jet", name: "Jet", defaultMode: "dark",
      blurb: "True black, graphite hairlines, colour only for data. 'Us' is the one inverse white tag. Light variant = Paper.",
      why: "A monochrome frame makes red/blue and the replay amber the only colour on screen, so they read instantly.",
      fonts: ["instrument"],
      base: { font: `"Instrument Sans", ${SANS}`, "font-num": `"Instrument Sans", ${SANS}`, "num-stretch": "75%", "r-tag": "3px", "r-s": "5px", "r-p": "6px", "r-m": "10px" },
      light: {
        bg: "#f3f3f1", surface: "#ffffff", "surface-2": "#f4f4f2", "surface-3": "#e7e7e4", line: "#e0e0dc", "line-strong": "#b3b3ae",
        ink: "#0b0b0b", "ink-2": "#45454a", "ink-3": "#5c5c60",
        accent: "#0b0b0b", "accent-fill": "#0b0b0b", "on-accent": "#ffffff", "accent-tint": "#ebebe8", "accent-line": "#8c8c88", focus: "#0b0b0b",
        plate: "#0b0b0b", "plate-2": "#202022", "plate-ink": "#ffffff", "plate-ink-2": "#c4c4c6", "plate-ink-3": "#9a9a9e", "plate-accent": "#ffffff",
        red: "#cc1f2d", blue: "#1554d1", "red-fill": "#d42433", "blue-fill": "#1b5fe0", "red-tint": "#fbe6e7", "blue-tint": "#e5ecfb",
        caution: "#8a5200", "caution-tint": "#f7ecd6", "caution-line": "#d4ab5f", good: "#127a3a", bad: "#cc1f2d",
        spark: "#0b0b0b", "field-bg": "#2a2c2e",
      },
      dark: {
        bg: "#050505", surface: "#0f0f10", "surface-2": "#161617", "surface-3": "#202022", line: "#232325", "line-strong": "#3b3b3e",
        ink: "#f4f4f2", "ink-2": "#b4b4b6", "ink-3": "#909094",
        accent: "#f4f4f2", "accent-fill": "#f4f4f2", "on-accent": "#050505", "accent-tint": "#232325", "accent-line": "#77777b", focus: "#f4f4f2",
        plate: "#050505", "plate-2": "#18181a", "plate-ink": "#f4f4f2", "plate-ink-2": "#a9a9ac", "plate-ink-3": "#88888c", "plate-accent": "#ffffff",
        red: "#ff6464", blue: "#62a0ff", "red-fill": "#d42a33", "blue-fill": "#2563eb", "red-tint": "#2b1213", "blue-tint": "#111d33",
        caution: "#f5b83d", "caution-tint": "#251d0e", "caution-line": "#6b511d", good: "#5fd690", bad: "#ff6464",
        spark: "#f4f4f2", "field-bg": "#1c1d1f",
      },
      rules: `
:T .sidebar { border-right: 1px solid var(--line); }
:T .toolbar { background: color-mix(in srgb, var(--bg) 82%, transparent); }
:T .sb-item[aria-current="page"] { box-shadow: none; }
:T h1, :T .qid h2, :T .qb-n, :T .cd > span, :T .oe dd, :T .bigp { letter-spacing: -.03em; }
:T .oe dd small, :T .bigp small { letter-spacing: normal; }
:T .qb-n { font-weight: 600; font-size: 72px; }
:T .qid h2 { font-weight: 600; }
:T .qcard, :T .dsec, :T .card { border-color: var(--line); }
:T .rl-m.upc.on { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .rl-m.upc.on .rl-k small, :T .rl-m.upc.on .rl-r, :T .rl-m.upc.on .rl-r b { color: var(--on-accent); }
:T .rl-m.upc.on .rpg { background: transparent; color: var(--on-accent); border-color: currentColor; }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); box-shadow: none; border-color: var(--accent-fill); }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 50%, transparent); }
:T .tab.on { border-bottom-width: 1px; }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T .weare { letter-spacing: .02em; }`,
    },

    // ------------------------------------------------------------------------------------------ field day
    {
      id: "fieldday", name: "Field Day",
      blurb: "The venue itself: grey arena carpet, aluminium, hi-vis safety yellow, scoreboard numerals. Dark = under the lights.",
      why: "Borrowed from the FRC field, not a brand kit: yellow marks 'us' like safety tape, and it can't be mistaken for an alliance.",
      fonts: ["mona"],
      base: { font: `"Mona Sans", ${SANS}`, "font-num": `"Mona Sans", ${SANS}`, "num-stretch": "78%", "r-tag": "2px", "r-s": "3px", "r-p": "4px", "r-m": "6px" },
      light: {
        bg: "#e7e9ec", surface: "#ffffff", "surface-2": "#f1f3f5", "surface-3": "#e1e4e8", line: "#d2d6dc", "line-strong": "#a6aeb8",
        ink: "#13161b", "ink-2": "#3d444e", "ink-3": "#545c67",
        accent: "#13161b", "accent-fill": "#ffcc00", "on-accent": "#13161b", "accent-tint": "#fff3bf", "accent-line": "#d9ad00", focus: "#13161b",
        plate: "#22262c", "plate-2": "#30353d", "plate-ink": "#ffffff", "plate-ink-2": "#cbd0d7", "plate-ink-3": "#a1a8b2", "plate-accent": "#ffcc00",
        red: "#c8102e", blue: "#0055b8", "red-fill": "#d0142f", "blue-fill": "#0a62cc", "red-tint": "#fbe2e5", "blue-tint": "#dde9f8",
        caution: "#a14600", "caution-tint": "#fde6d3", "caution-line": "#e39a5d", good: "#14783a", bad: "#c8102e",
        spark: "#13161b", "field-bg": "#3a3f46", cap: "#22262c",
      },
      dark: {
        bg: "#101317", surface: "#191d23", "surface-2": "#21262d", "surface-3": "#2a3038", line: "#2e343c", "line-strong": "#4a525d",
        ink: "#f3f5f7", "ink-2": "#bcc3cc", "ink-3": "#9aa2ad",
        accent: "#ffd23f", "accent-fill": "#ffcc00", "on-accent": "#13161b", "accent-tint": "#383114", "accent-line": "#8a7420", focus: "#ffd23f",
        plate: "#0b0d10", "plate-2": "#1d2128", "plate-ink": "#f3f5f7", "plate-ink-2": "#bec5ce", "plate-ink-3": "#959eaa", "plate-accent": "#ffcc00",
        red: "#ff7a85", blue: "#6fa8ff", "red-fill": "#d0142f", "blue-fill": "#1764d0", "red-tint": "#3d1d23", "blue-tint": "#172a45",
        caution: "#ff9f4a", "caution-tint": "#382517", "caution-line": "#8a5326", good: "#5ed38a", bad: "#ff7a85",
        spark: "#ffd23f", "field-bg": "#2b3036", cap: "#ffcc00",
      },
      rules: `
:T :focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; box-shadow: 0 0 0 5px var(--accent-fill); }
:T .sidebar :focus-visible { outline-color: var(--plate-ink); }
:T .sb-brand { font-family: var(--font-num); font-size: 22px; letter-spacing: .01em; text-transform: uppercase; }
:T .sb-item[aria-current="page"] { background: var(--plate-2); box-shadow: none; }
:T .sb-item[aria-current="page"] span { text-decoration: underline 2px var(--plate-accent); text-underline-offset: 5px; }
:T h1, :T .qid h2, :T .cd > span, :T .qb-n, :T .oe dd, :T .dchip b, :T .rl-r b, :T .rl-now b, :T .dgain, :T .kv b, :T .tile b { font-weight: 700; letter-spacing: 0; }
:T .qid h2 { font-size: 38px; text-transform: uppercase; }
:T .qb-n { font-size: 76px; }
:T .cd > span { font-size: 46px; }
:T .qcard { border-top: 4px solid var(--cap); }
:T .weare { text-transform: uppercase; letter-spacing: .04em; }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent-line); box-shadow: inset 0 0 0 1px var(--accent-line); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 45%, transparent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface), 0 0 0 3px var(--on-accent); }
:T .progress > div, :T .sc-sel .hbar { background: var(--ink); }
:T .rl-now span { border-left-width: 3px; }
:T .tab.on, :T .tablink.on { border-color: var(--accent-fill); }
:T .oe-spark, :T .trends2 { color: var(--spark); }`,
    },

    // ------------------------------------------------------------------------------------------ pine & brass
    {
      id: "pine", name: "Pine & Brass",
      blurb: "Dark only: lacquered pine green, brass fittings, parchment type. The trophy case at the end of the pits.",
      why: "A dark theme that is green rather than grey: warm, low-glare for dim stands, with brass as a quiet 'us'.",
      fonts: ["chivo"],
      base: { font: `"Chivo", ${SANS}`, "font-num": `"Chivo", ${SANS}`, "num-stretch": "100%", "r-tag": "4px", "r-s": "6px", "r-p": "8px", "r-m": "12px" },
      light: null,
      dark: {
        bg: "#0e1712", surface: "#15211a", "surface-2": "#1b2a22", "surface-3": "#23352b", line: "#2a3c31", "line-strong": "#46594d",
        ink: "#efe9d8", "ink-2": "#c6c2ae", "ink-3": "#a6a692",
        accent: "#dcb75c", "accent-fill": "#cfa94e", "on-accent": "#1a1407", "accent-tint": "#323019", "accent-line": "#7f6b33", focus: "#dcb75c",
        plate: "#0a120d", "plate-2": "#18271e", "plate-ink": "#efe9d8", "plate-ink-2": "#c1c6b4", "plate-ink-3": "#99a291", "plate-accent": "#e2bf66",
        red: "#ff7a72", blue: "#86afff", "red-fill": "#c8352c", "blue-fill": "#2c65cf", "red-tint": "#3c201c", "blue-tint": "#1b2b44",
        caution: "#f39a52", "caution-tint": "#36241a", "caution-line": "#8a5532", good: "#9ad38c", bad: "#ff7a72",
        spark: "#dcb75c", "field-bg": "#2d3a32",
      },
      rules: `
:T .sidebar { border-right: 1px solid color-mix(in srgb, var(--plate-accent) 18%, transparent); }
:T .sb-item[aria-current="page"] { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--plate-accent) 35%, transparent); }
:T .qcard { box-shadow: inset 0 1px 0 color-mix(in srgb, var(--accent) 40%, transparent), 0 18px 40px -24px #000; }
:T .qb-n { font-weight: 800; letter-spacing: -.03em; }
:T .qid h2 { font-weight: 800; letter-spacing: -.02em; }
:T .cd > span { font-weight: 300; letter-spacing: -.01em; }
:T .rl-m.upc.on { background: var(--accent-tint); }
:T .dchip.us { box-shadow: inset 0 0 0 1px var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
:T .weare { color: #fff; }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T ::selection { background: color-mix(in srgb, var(--accent) 35%, transparent); }`,
    },

    // ------------------------------------------------------------------------------------------ anodized
    {
      id: "anodized", name: "Anodized",
      blurb: "Machine shop: bead-blasted aluminium greys, a graphite plate, anodized-violet 'us', IBM Plex like a drawing title block.",
      why: "Violet is the robot-parts colour and sits between red and blue on no one's side; the cool neutrals keep data crisp.",
      fonts: ["plex"],
      base: { font: `"IBM Plex Sans", ${SANS}`, "font-num": `"IBM Plex Sans", ${SANS}`, "num-stretch": "85%", "r-tag": "2px", "r-s": "4px", "r-p": "5px", "r-m": "8px" },
      light: {
        bg: "#ececf0", surface: "#ffffff", "surface-2": "#f3f3f6", "surface-3": "#e4e4ea", line: "#d8d8e0", "line-strong": "#aaaab8",
        ink: "#16151d", "ink-2": "#44425a", "ink-3": "#595769",
        accent: "#7330c0", "accent-fill": "#7330c0", "on-accent": "#ffffff", "accent-tint": "#efe5fa", "accent-line": "#bf9fe6", focus: "#7330c0",
        plate: "#1d1b23", "plate-2": "#2b2833", "plate-ink": "#ffffff", "plate-ink-2": "#cbc8d6", "plate-ink-3": "#a3a0b1", "plate-accent": "#c9a6ff",
        red: "#c42029", blue: "#0a5cbf", "red-fill": "#cd2730", "blue-fill": "#0f66d0", "red-tint": "#fae4e5", "blue-tint": "#dfeaf8",
        caution: "#875400", "caution-tint": "#f6ecd6", "caution-line": "#d1aa5e", good: "#17773b", bad: "#c42029",
        spark: "#7330c0", "field-bg": "#3a3d45",
      },
      dark: {
        bg: "#121116", surface: "#1a1920", "surface-2": "#222129", "surface-3": "#2b2a34", line: "#302f39", "line-strong": "#4b4a57",
        ink: "#eeedf3", "ink-2": "#bdbbcb", "ink-3": "#9d9bad",
        accent: "#c6a6ff", "accent-fill": "#b48cff", "on-accent": "#1a1028", "accent-tint": "#2e2443", "accent-line": "#6b50a6", focus: "#c6a6ff",
        plate: "#0c0b10", "plate-2": "#1e1c26", "plate-ink": "#eeedf3", "plate-ink-2": "#bdbbcb", "plate-ink-3": "#9593a6", "plate-accent": "#c6a6ff",
        red: "#ff7b80", blue: "#64a8ff", "red-fill": "#cd2730", "blue-fill": "#1a68d2", "red-tint": "#3c1f24", "blue-tint": "#152a46",
        caution: "#f2b84b", "caution-tint": "#332b1b", "caution-line": "#7a5c22", good: "#66d18f", bad: "#ff7b80",
        spark: "#c6a6ff", "field-bg": "#2b2d33",
      },
      rules: `
/* the chrome is one graphite frame: sidebar + toolbar, with the toolbar's tokens re-scoped to the plate */
:T .toolbar { --ink: var(--plate-ink); --ink-2: var(--plate-ink-2); --ink-3: var(--plate-ink-3); --line: var(--plate-2); --surface: var(--plate-2);
  --hover: color-mix(in srgb, var(--plate-ink) 10%, transparent); --focus: var(--plate-accent);
  background: var(--plate); color: var(--plate-ink); border-bottom-color: var(--plate-2); -webkit-backdrop-filter: none; backdrop-filter: none; }
:T .toolbar .timectl.replay { --ink: #16151d; --ink-2: #44425a; }
:T .sidebar { border-right: 1px solid var(--plate-2); }
:T h1, :T .qid h2 { font-weight: 600; letter-spacing: -.01em; }
:T .qb-n { font-weight: 600; letter-spacing: -.02em; }
:T .cd > span { font-weight: 500; }
:T .qcard { box-shadow: 0 1px 2px color-mix(in srgb, var(--ink) 8%, transparent); }
:T .qhead { padding-bottom: var(--sp-3); border-bottom: 1px solid var(--line); }
:T .qmeta, :T .rl-k small, :T .cd small, :T .tb-title .s { font-variant-numeric: tabular-nums slashed-zero; }
:T .dchip.us { box-shadow: inset 0 0 0 2px var(--accent); }
:T .sb-item[aria-current="page"] { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--plate-accent) 30%, transparent); }
:T .oe-spark, :T .trends2 { color: var(--spark); }`,
    },

    // ------------------------------------------------------------------------------------------ sandstone
    {
      id: "sandstone", name: "Sandstone",
      blurb: "Light only: sun-warmed sandstone and limestone, a pale sandstone sidebar, espresso ink. 'Us' is an espresso tag.",
      why: "Low-glare warm neutrals for bright pits; the only hues on the page are the alliances and ochre replay.",
      fonts: ["sofia"],
      base: { font: `"Sofia Sans", ${SANS}`, "font-num": `"Sofia Sans Condensed", "Sofia Sans", ${SANS}`, "num-stretch": "100%", "r-tag": "4px", "r-s": "6px", "r-p": "9px", "r-m": "13px", "fs-body": "15px", "fs-foot": "14px" },
      light: {
        bg: "#eee5d6", surface: "#fbf7f0", "surface-2": "#f4ede1", "surface-3": "#e8dece", line: "#ddd1bd", "line-strong": "#b6a68d",
        ink: "#2a1f17", "ink-2": "#57483b", "ink-3": "#68594a",
        accent: "#3a2a1e", "accent-fill": "#3a2a1e", "on-accent": "#fbf7f0", "accent-tint": "#e9dcc7", "accent-line": "#9c8467", focus: "#3a2a1e",
        plate: "#e2d4bd", "plate-2": "#d3c2a5", "plate-ink": "#2a1f17", "plate-ink-2": "#4b3d30", "plate-ink-3": "#5e4f3f", "plate-accent": "#3a2a1e",
        red: "#b3261f", blue: "#1f57b6", "red-fill": "#bd2c24", "blue-fill": "#2361c6", "red-tint": "#f5dfd6", "blue-tint": "#dfe6f2",
        caution: "#7f5100", "caution-tint": "#f2e3c2", "caution-line": "#c9a560", good: "#3d6b26", bad: "#b3261f",
        spark: "#3a2a1e", "field-bg": "#4a4237",
      },
      dark: null,
      rules: `
:T .sidebar { border-right: 1px solid var(--line-strong); }
:T .sb-item:hover { background: color-mix(in srgb, var(--plate-ink) 7%, transparent); }
:T .sb-item[aria-current="page"] { background: var(--accent-fill); color: var(--on-accent); box-shadow: none; }
:T .sb-item[aria-current="page"] svg { color: var(--on-accent); }
:T .sb-h { letter-spacing: .02em; }
:T h1, :T .qid h2 { letter-spacing: -.01em; font-weight: 800; }
:T .qb-n { font-weight: 800; font-size: 72px; }
:T .cd > span { font-weight: 700; }
:T .rl-m.upc.on { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .rl-m.upc.on .rl-k small, :T .rl-m.upc.on .rl-r, :T .rl-m.upc.on .rl-r b { color: var(--on-accent); }
:T .rl-m.upc.on .rpg { background: transparent; color: var(--on-accent); border-color: currentColor; }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); box-shadow: none; border-color: var(--accent-fill); }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 50%, transparent); }
:T .oe-spark, :T .trends2 { color: var(--spark); }`,
    },
  ];

  // ---------------------------------------------------------------------------------------- contrast (WCAG 2.x)
  const hex = h => { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map(c => c + c).join(""); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255); };
  const lum = h => { const [r, g, b] = hex(h).map(c => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  // [foreground token, background token, where it appears]
  const PAIRS = [
    ["ink", "bg", "page text"], ["ink", "surface", "card text"], ["ink", "surface-2", "inset text"], ["ink-2", "surface", "secondary"], ["ink-2", "surface-2", "secondary inset"], ["ink-2", "bg", "secondary on page"],
    ["ink-3", "surface", "tertiary"], ["ink-3", "surface-2", "tertiary inset"],
    ["accent", "surface", "'us' text / focus"], ["accent", "bg", "'us' on page"], ["on-accent", "accent-fill", "text on 'us' fill"], ["ink", "accent-tint", "selected rail"], ["ink-2", "accent-tint", "selected rail meta"],
    ["plate-ink", "plate", "sidebar title"], ["plate-ink-2", "plate", "sidebar item"], ["plate-ink-3", "plate", "sidebar group"], ["plate-ink", "plate-2", "current item"], ["plate-accent", "plate-2", "current icon (3:1)", 3],
    ["red", "surface", "red alliance text"], ["blue", "surface", "blue alliance text"], ["red", "surface-2", "red on inset"], ["blue", "surface-2", "blue on inset"],
    ["#ffffff", "red-fill", "'we are RED' tag"], ["#ffffff", "blue-fill", "'we are BLUE' tag"], ["ink", "red-tint", "team row (red)"], ["ink", "blue-tint", "team row (blue)"], ["ink-2", "red-tint", "'Us/Them' label (red)"], ["ink-2", "blue-tint", "'Us/Them' label (blue)"],
    ["caution", "surface", "replay / caution"], ["caution", "caution-tint", "replay chip"], ["ink", "caution-tint", "replay time"], ["good", "surface", "good"], ["bad", "surface", "bad"],
    ["focus", "bg", "focus ring (3:1)", 3], ["spark", "surface", "sparkline (3:1)", 3], ["red-fill", "surface", "red bar (3:1)", 3], ["blue-fill", "surface", "blue bar (3:1)", 3],
  ];
  function contrast(tokens) {
    const v = k => k.startsWith("#") ? k : tokens[k];
    return PAIRS.map(([f, b, what, min = 4.5]) => { const r = ratio(v(f), v(b)); return { fg: f, bg: b, what, min, ratio: Math.round(r * 100) / 100, ok: r >= min }; });
  }

  // ---------------------------------------------------------------------------------------- porting
  // Drop-in replacement for the token blocks of web/style.css + the theme's rules. Used by the lab's "Copy theme CSS"
  // and by tools/port-theme.mjs (which can pass fontSrc to inline the fonts as base64).
  function port(t, styleText = "", fontSrc) {
    const L = { FONTS };
    const m = styleText.match(/:root \{[\s\S]*?\n\}/);
    const light = t.light || t.dark, onlyDark = !t.light;
    const set = Object.assign({}, t.base, light);
    let rootBlock;
    if (m) {
      const seen = new Set();
      const NOTE = ["ink", "ink-2", "ink-3", "accent", "red", "blue", "caution", "good"];
      rootBlock = m[0].split("\n").map(line => {
        let hit = false;
        line = line.replace(/--([a-z0-9-]+):\s*([^;]+);/g, (all, k) => { if (k in set) { seen.add(k); hit = true; return `--${k}: ${set[k]};`; } return all; });
        if (!hit) return line.replace(/\/\* surfaces:[^*]*\*\//, "/* surfaces */");
        line = line.replace(/\s*\/\*.*?\*\/\s*$/, "");
        const k = (line.match(/--([a-z0-9-]+):/) || [])[1];
        return NOTE.includes(k) ? `${line}   /* ${ratio(set[k], set.surface).toFixed(1)}:1 on --surface */` : line;
      }).join("\n")
        .replace(/color-scheme:[^;]+;/, `color-scheme: ${onlyDark ? "dark" : "light dark"};`);
      const extra = Object.keys(set).filter(k => !seen.has(k));
      if (extra.length) rootBlock = rootBlock.replace(/\n\}$/, `\n  /* added by the ${t.name} theme */\n${extra.map(k => `  --${k}: ${set[k]};`).join("\n")}\n}`);
      if (onlyDark) rootBlock = rootBlock.replace(/\n\}$/, `\n  --sel: color-mix(in srgb, var(--accent) 12%, transparent);\n  --hover: color-mix(in srgb, var(--ink) 6%, transparent);\n  --shadow-pop: 0 2px 6px rgba(0, 0, 0, .25), 0 16px 40px rgba(0, 0, 0, .4);\n}`);
    } else rootBlock = `:root {\n${Object.entries(set).map(([k, v]) => `  --${k}: ${v};`).join("\n")}\n}`;
    const darkBlock = t.light && t.dark ? `@media (prefers-color-scheme: dark) {\n  :root {\n${Object.entries(t.dark).map(([k, v]) => `    --${k}: ${v};`).join("\n")}\n    --sel: color-mix(in srgb, var(--accent) 12%, transparent);\n    --hover: color-mix(in srgb, var(--ink) 6%, transparent);\n    --shadow-pop: 0 2px 6px rgba(0, 0, 0, .25), 0 16px 40px rgba(0, 0, 0, .4);\n  }\n}` : "";
    const faces = (t.fonts || []).flatMap(k => L.FONTS[k]).map(f => `@font-face { font-family: "${f.family}"; font-style: normal; font-weight: ${f.weight};${f.stretch ? ` font-stretch: ${f.stretch};` : ""} font-display: swap; src: url("${fontSrc ? fontSrc(f) : "fonts/" + f.file}") format("woff2"); }`).join("\n");
    const rules = (t.rules || "").trim().replace(/:T /g, "").replace(/:T/g, "html");
    const lt = t.light ? t.light.bg : null, dk = t.dark ? t.dark.bg : null;
    return `/* ===== Cyber-Scout theme "${t.name}" — exported from the design lab (lab/themes.js) =====
 * ${t.blurb}
 * How to port into web/:
 * 1. Fonts: ${faces ? `copy ${[...new Set((t.fonts || []).flatMap(k => L.FONTS[k]).map(f => "lab/fonts/" + f.file))].join(", ")} into web/fonts/ and add the @font-face lines below to web/fonts.css
 *    (or run: node lab/tools/port-theme.mjs ${t.id} --inline  to get them base64-inlined like Archivo).` : "none (keeps Archivo from web/fonts.css)."}
 * 2. In web/style.css ${t.light && t.dark ? `replace the first ":root { ... }" block and the "@media (prefers-color-scheme: dark) { :root { ... } }" block with the two blocks below.` : `replace the first ":root { ... }" block with the one below, and delete the "@media (prefers-color-scheme: dark) { :root { ... } }" block (this theme has one appearance).`}
 * 3. Append the "theme rules" section to the end of web/style.css.
 * 4. web/index.html: <meta name="theme-color"> ${lt ? `light ${lt}` : ""}${lt && dk ? ", " : ""}${dk ? `dark ${dk}` : ""}${onlyDark ? '; set <meta name="color-scheme" content="dark">' : ""}${!t.dark ? '; set <meta name="color-scheme" content="light">' : ""}.
 */
${faces ? faces + "\n\n" : ""}${rootBlock}
${darkBlock ? "\n" + darkBlock + "\n" : ""}
/* ---- theme rules (append to the end of web/style.css) ---- */
:focus-visible { outline-color: var(--focus); }
.sidebar :focus-visible { outline-color: var(--plate-ink); }
${rules}
`;
  }

  const LAYOUTS = [
    { id: "sidebar", name: "Sidebar", blurb: "Today's layout: brand plate on the left, toolbar on top." },
    { id: "topbar", name: "Top bar", blurb: "Navigation across the top, wider content below." },
    { id: "compact", name: "Compact", blurb: "Icon rail and tighter spacing for a laptop in the stands." },
    { id: "focus", name: "Focus", blurb: "No sidebar; the queue card runs full width with our event as a strip below." },
  ];

  const api = { THEMES, LAYOUTS, FONTS, contrast, ratio, PAIRS, port };
  root.LAB = Object.assign(root.LAB || {}, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
