/* Design lab extension slot "d": the user's picks, tuned for the real site:
 *   "Sand & Earth" (light = Sandstone + a few green accents, dark = Earth evening with less green) and "Jet (dark only)".
 * Same API as ext/a.js: LAB.THEMES.push({...}), LAB.LAYOUTS.push({...}), LAB.FONTS[key] = [...].
 *
 * Sand & Earth  id "sandearth", light + dark, follows the system setting. One family across both: Sofia Sans
 *               (Sofia Sans Condensed numerals), Sandstone's face. Green is reserved for "us / you are here":
 *               the 4911 tag, the current nav item, the selected match, focus ring, primary buttons, the logo.
 *   light = built-in Sandstone; only the accent group moves from espresso to moss (#4a6a40):
 *     accent, accent-fill #3a2a1e -> #4a6a40   accent-tint #e9dcc7 -> #e2e3cb   accent-line #9c8467 -> #93a27f
 *     focus #3a2a1e -> #4a6a40                 plate-accent #3a2a1e -> #4a6a40   (sidebar, ink, paper, spark: unchanged)
 *   dark  = built-in Earth "evening" with the green taken out of the room:
 *     plate (sidebar) moss-black #16211a -> bark #171410, plate-2 #223127 -> #2a241d, plate inks warm instead of grey-green;
 *     surfaces nudged from olive-umber toward bark; accent lime #aed08f -> sage #aabf9b (sat 43% -> 22%);
 *     sparkline green -> warm cream; "good" desaturated; the current nav item is a bark plate with a sage icon
 *     (not a green slab) and the selected match is a sage-ringed tint (not a green fill).
 * Jet           id "jetdark", dark only: the built-in Jet dark set, verbatim, with the "Paper" light variant removed.
 *
 * Mode-dependent treatments use a few extra tokens (cur-*, pick-*) so one rules block serves both appearances. */
(function (L) {
  "use strict";
  if (!L) return;
  const byId = id => L.THEMES.find(t => t.id === id);
  const sand = byId("sandstone"), earth = byId("earth"), jet = byId("jet");
  if (!sand || !earth || !jet) return;

  const MOSS = "#4a6a40", SAGE = "#aabf9b";

  L.THEMES.push(
    // ------------------------------------------------------------------------------------------ sand & earth
    {
      id: "sandearth", name: "Sand & Earth",
      blurb: "Day = Sandstone: warm desert paper, a pale sandstone sidebar, espresso ink, moss green only where you are. Evening = Earth in bark and umber, sage as the one accent.",
      why: "Green marks 'us / you are here' and nothing else, so it reads as a signal, never as the room; red and blue stay the only saturated colours on screen.",
      fonts: ["sofia"],
      base: Object.assign({}, sand.base),
      light: Object.assign({}, sand.light, {
        accent: MOSS, "accent-fill": MOSS, "on-accent": "#fbf7f0", "accent-tint": "#e2e3cb", "accent-line": "#93a27f", focus: MOSS,
        "plate-accent": MOSS,
        // current nav item / selected match: filled moss, like Sandstone's espresso fill
        "cur-bg": MOSS, "cur-ink": "#fbf7f0", "cur-icon": "#fbf7f0", "cur-ring": "transparent",
        "pick-bg": MOSS, "pick-ink": "#fbf7f0", "pick-ink-2": "#fbf7f0", "pick-line": MOSS, "pick-chip": "transparent",
      }),
      dark: Object.assign({}, earth.dark, {
        bg: "#1d1915", surface: "#26211b", "surface-2": "#2f2922", "surface-3": "#39322a", line: "#3e372e", "line-strong": "#5d5446",
        ink: "#efe8d6", "ink-2": "#cdc3b0", "ink-3": "#b0a592",
        accent: SAGE, "accent-fill": "#a0b691", "on-accent": "#1c2016", "accent-tint": "#31332a", "accent-line": "#6b7560", focus: SAGE,
        plate: "#171410", "plate-2": "#2a241d", "plate-ink": "#efe8d6", "plate-ink-2": "#cdc3b0", "plate-ink-3": "#a99e8b", "plate-accent": SAGE,
        good: "#b7cd8f", spark: "#d8ccb4", "field-bg": "#3a352c",
        // current nav item: bark plate + sage icon; selected match: sage ring on a warm tint
        "cur-bg": "#2a241d", "cur-ink": "#efe8d6", "cur-icon": SAGE, "cur-ring": "rgba(170, 191, 155, .32)",
        "pick-bg": "#31332a", "pick-ink": "#efe8d6", "pick-ink-2": "#cdc3b0", "pick-line": SAGE, "pick-chip": "var(--surface)",
      }),
      rules: `
:T .sidebar { border-right: 1px solid var(--line-strong); }
:T .sb-item:hover { background: color-mix(in srgb, var(--plate-ink) 7%, transparent); }
:T .sb-item[aria-current="page"] { background: var(--cur-bg); color: var(--cur-ink); box-shadow: inset 0 0 0 1px var(--cur-ring); }
:T .sb-item[aria-current="page"] svg { color: var(--cur-icon); }
:T .sb-h { letter-spacing: .02em; }
:T h1, :T .qid h2 { letter-spacing: -.01em; font-weight: 800; }
:T .qb-n { font-weight: 800; font-size: 72px; }
:T .cd > span { font-weight: 700; }
:T .rl-m.upc.on { background: var(--pick-bg); color: var(--pick-ink); border-color: var(--pick-line); box-shadow: inset 0 0 0 1px var(--pick-line); }
:T .rl-m.upc.on.red { border-top-color: var(--red-fill); }
:T .rl-m.upc.on.blue { border-top-color: var(--blue-fill); }
:T .rl-m.upc.on .rl-k small, :T .rl-m.upc.on .rl-r { color: var(--pick-ink-2); }
:T .rl-m.upc.on .rl-r b { color: var(--pick-ink); }
:T .rl-m.upc.on .rpg { background: var(--pick-chip); color: var(--pick-ink); border-color: currentColor; }
:T .dchip.us { background: var(--accent-fill); color: var(--on-accent); box-shadow: none; border-color: var(--accent-fill); }
:T .dchip.us .rt, :T .dchip.us .ustag { color: var(--on-accent); }
:T .dchip.us .mcov { color: var(--on-accent); border-color: color-mix(in srgb, var(--on-accent) 50%, transparent); }
:T .oe-spark, :T .trends2 { color: var(--spark); }`,
    },

    // ------------------------------------------------------------------------------------------ jet (dark only)
    {
      id: "jetdark", name: "Jet (dark only)", defaultMode: "dark",
      blurb: "True black, graphite hairlines, colour only for data. 'Us' is the one inverse white tag. Dark only.",
      why: jet.why,
      fonts: jet.fonts.slice(),
      base: Object.assign({}, jet.base),
      light: null,
      dark: Object.assign({}, jet.dark),
      rules: jet.rules,
    }
  );
})(window.LAB);
