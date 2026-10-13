/* Design lab extension slot "b": push extra themes / layouts / fonts into the lab registry (themes.js).
 *   LAB.THEMES.push({ id, name, blurb, why, fonts: [...], base: {...}, light: {...}, dark: {...}, rules: "..." });
 *   LAB.LAYOUTS.push({ id, name, blurb });   // its CSS goes in ext/b.css, scoped to html[data-theme][data-layout="<id>"]
 *   LAB.FONTS["key"] = [{ family, file, weight, stretch }];   // woff2 in lab/fonts/
 * Same token keys and rules conventions as the built-in themes in themes.js.
 *
 * Slot b = the modern-black family: Carbon, Onyx & Gold, Smoke (all dark-only) + the "Command" layout.
 * Fonts (Latin subsets from Google Fonts, SIL OFL 1.1): Saira, Manrope, Atkinson Hyperlegible Next. */
(function (L) {
  "use strict";
  if (!L) return;
  const SANS = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  Object.assign(L.FONTS, {
    "b-saira": [{ family: "Saira", file: "b-saira.woff2", weight: "100 900", stretch: "50% 125%" }],
    "b-manrope": [{ family: "Manrope", file: "b-manrope.woff2", weight: "200 800" }],
    "b-atkinson": [{ family: "Atkinson Hyperlegible Next", file: "b-atkinson-next.woff2", weight: "200 800" }],
  });

  L.THEMES.push(
    // ------------------------------------------------------------------------------------------ carbon
    {
      id: "carbon", name: "Carbon", defaultMode: "dark",
      blurb: "Dark only: true black, carbon-graphite panels with a machined top edge, Saira's motorsport numerals. 'Us' is timing-screen magenta.",
      why: "Magenta is the 'fastest overall' colour of race timing screens and sits on neither alliance's side of the wheel, so 'us' pops without ever reading red or blue.",
      fonts: ["b-saira"],
      base: { font: `"Saira", ${SANS}`, "font-num": `"Saira", ${SANS}`, "num-stretch": "72%", "r-tag": "2px", "r-s": "4px", "r-p": "5px", "r-m": "8px" },
      light: null,
      dark: {
        bg: "#000000", surface: "#0e0e10", "surface-2": "#16161a", "surface-3": "#202026", line: "#24242a", "line-strong": "#3d3d45",
        ink: "#f3f3f5", "ink-2": "#b3b3bc", "ink-3": "#93939d",
        accent: "#f46bff", "accent-fill": "#ec5cff", "on-accent": "#14000f", "accent-tint": "#260d2c", "accent-line": "#8c3a9c", focus: "#f46bff",
        plate: "#000000", "plate-2": "#1f1024", "plate-ink": "#f3f3f5", "plate-ink-2": "#aaaab3", "plate-ink-3": "#8c8c96", "plate-accent": "#f46bff",
        red: "#ff6b5e", blue: "#5ea4ff", "red-fill": "#dc2a2f", "blue-fill": "#2463eb", "red-tint": "#2a0f0e", "blue-tint": "#0c1a33",
        caution: "#f5b83d", "caution-tint": "#251d0d", "caution-line": "#6b511d", good: "#55d98e", bad: "#ff6b5e",
        spark: "#f46bff", "field-bg": "#17171a",
      },
      rules: `
:T .sidebar { border-right: 1px solid var(--line); }
:T .toolbar { background: color-mix(in srgb, var(--bg) 84%, transparent); }
:T .sb-brand { font-weight: 600; font-stretch: 85%; letter-spacing: .01em; }
:T .sb-item[aria-current="page"] { box-shadow: none; }
:T .qcard, :T .card, :T .dsec { border-color: transparent; box-shadow: inset 0 1px 0 color-mix(in srgb, var(--ink) 9%, transparent); }
:T h1, :T .qid h2 { font-weight: 600; letter-spacing: -.01em; }
:T .qb-n { font-weight: 300; font-size: 80px; letter-spacing: -.03em; }
:T .cd > span { font-weight: 300; letter-spacing: -.01em; font-size: 46px; }
:T .oe dd, :T .rl-r b, :T .dchip b { font-weight: 600; }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
:T .rl-m.upc.on.red, :T .rl-m.upc.on.blue { border-top-color: var(--accent); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 50%, transparent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface); }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T .weare { font-weight: 600; }
:T ::selection { background: color-mix(in srgb, var(--accent) 34%, transparent); }`,
    },

    // ------------------------------------------------------------------------------------------ onyx & gold
    {
      id: "onyx", name: "Onyx & Gold", defaultMode: "dark",
      blurb: "Dark only: warm onyx black, ivory type, champagne-gold 'us' plaque, Manrope's fine numerals like a watch dial.",
      why: "Gold is the trophy colour and a warm metal never reads as an alliance; replay moves to a clearly orange chip so the two warm accents never blur.",
      fonts: ["b-manrope"],
      base: { font: `"Manrope", ${SANS}`, "font-num": `"Manrope", ${SANS}`, "num-stretch": "100%", "r-tag": "3px", "r-s": "5px", "r-p": "7px", "r-m": "10px" },
      light: null,
      dark: {
        bg: "#0a0907", surface: "#13110e", "surface-2": "#1b1814", "surface-3": "#25211b", line: "#2a251e", "line-strong": "#4a4235",
        ink: "#f4eee2", "ink-2": "#c7bca8", "ink-3": "#a59a85",
        accent: "#e4c47e", "accent-fill": "#d9b56c", "on-accent": "#17110a", "accent-tint": "#2b2416", "accent-line": "#7d6838", focus: "#e4c47e",
        plate: "#070605", "plate-2": "#1b1712", "plate-ink": "#f4eee2", "plate-ink-2": "#c2b69f", "plate-ink-3": "#9f937c", "plate-accent": "#e4c47e",
        red: "#ff7470", blue: "#7aabff", "red-fill": "#cf2a33", "blue-fill": "#2a63d8", "red-tint": "#2d1414", "blue-tint": "#121c31",
        caution: "#ff9c5a", "caution-tint": "#2f1c10", "caution-line": "#7f4a26", good: "#93d6a0", bad: "#ff7470",
        spark: "#e4c47e", "field-bg": "#1f1c17",
      },
      rules: `
:T body { font-optical-sizing: auto; }
:T .sidebar { border-right: 1px solid color-mix(in srgb, var(--plate-accent) 16%, transparent); }
:T .sb-brand { font-weight: 700; letter-spacing: -.01em; }
:T .sb-item[aria-current="page"] { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--plate-accent) 28%, transparent); }
:T .qcard { box-shadow: 0 1px 0 color-mix(in srgb, var(--accent) 22%, transparent) inset, 0 24px 48px -32px #000; }
:T .qid h2, :T h1 { font-weight: 700; letter-spacing: -.02em; }
:T .qb-n { font-weight: 400; font-size: 72px; letter-spacing: -.04em; }
:T .cd > span { font-weight: 400; letter-spacing: -.03em; }
:T .oe dd { font-weight: 500; letter-spacing: -.02em; }
:T .rl-r b, :T .dchip b, :T .rl-now b { font-weight: 700; letter-spacing: -.01em; }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent-line); box-shadow: inset 0 0 0 1px var(--accent-line); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 45%, transparent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface); }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T ::selection { background: color-mix(in srgb, var(--accent) 30%, transparent); }`,
    },

    // ------------------------------------------------------------------------------------------ smoke
    {
      id: "smoke", name: "Smoke", defaultMode: "dark",
      blurb: "Dark only: soft charcoal instead of black, off-white ink that doesn't bloom, Atkinson Hyperlegible Next. 'Us' is a warm apricot.",
      why: "For four-hour sessions in dim stands: lower contrast swing than pure black, a typeface built for legibility, and an apricot 'us' that is lighter and yellower than red.",
      fonts: ["b-atkinson"],
      base: { font: `"Atkinson Hyperlegible Next", ${SANS}`, "font-num": `"Atkinson Hyperlegible Next", ${SANS}`, "num-stretch": "100%", "r-tag": "5px", "r-s": "7px", "r-p": "10px", "r-m": "14px", "fs-body": "14.5px" },
      light: null,
      dark: {
        bg: "#1b1c1f", surface: "#232428", "surface-2": "#2a2b30", "surface-3": "#33353a", line: "#36383d", "line-strong": "#55585f",
        ink: "#e9e7e2", "ink-2": "#bdbab3", "ink-3": "#a4a19a",
        accent: "#ffbd85", "accent-fill": "#f7ad72", "on-accent": "#2a1608", "accent-tint": "#3c2e23", "accent-line": "#94653f", focus: "#ffbd85",
        plate: "#151619", "plate-2": "#2a2b30", "plate-ink": "#e9e7e2", "plate-ink-2": "#b8b5ae", "plate-ink-3": "#9c9992", "plate-accent": "#ffbd85",
        red: "#ff8a8f", blue: "#8cb6ff", "red-fill": "#d23342", "blue-fill": "#2f6be0", "red-tint": "#3d2427", "blue-tint": "#22304a",
        caution: "#e8d066", "caution-tint": "#34311e", "caution-line": "#776a2c", good: "#8fd6a0", bad: "#ff8a8f",
        spark: "#ffbd85", "field-bg": "#2b2d31",
      },
      rules: `
:T .sidebar { border-right: 1px solid var(--line); }
:T .sb-item[aria-current="page"] { box-shadow: none; }
:T .qcard, :T .card, :T .dsec { border-color: transparent; }
:T .qcard { box-shadow: 0 1px 2px rgba(0, 0, 0, .25), 0 18px 36px -28px rgba(0, 0, 0, .7); }
:T h1, :T .qid h2 { font-weight: 700; letter-spacing: -.01em; }
:T .qb-n { font-weight: 700; letter-spacing: -.03em; }
:T .cd > span { font-weight: 600; letter-spacing: -.01em; }
:T .rl-m.upc.on { background: var(--accent-tint); border-color: var(--accent-line); box-shadow: inset 0 0 0 1px var(--accent-line); }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); border-color: var(--accent-fill); box-shadow: none; }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 45%, transparent); }
:T .ltrack .we, :T .lk .we { box-shadow: 0 0 0 2px var(--surface); }
:T .oe-spark, :T .trends2 { color: var(--spark); }
:T ::selection { background: color-mix(in srgb, var(--accent) 30%, transparent); }`,
    },
  );

  L.LAYOUTS.push({ id: "command", name: "Command", blurb: "Command centre for a big laptop: icon rail, queue card on the left, our event, schedule and ratings stacked live on the right." });
})(window.LAB);
