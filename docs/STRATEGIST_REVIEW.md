# Strategist review → rework plan (2026wasam replayed at Sun 09:00)

Three critics (match prep, alliance selection, skeptical quant) used the tool as of day-2 09:00, wrote blind calls,
then graded against what happened. Full notes: `.work/strategist/{match,picks,quant}/` (REPORT.md + artifacts).

## What held up
- Blended win prob beats EPA, honestly measured: live replay 58 quals Brier **.119 vs .147** (fair `epa_pre`), gap
  −.028 [−.048, −.009]; blind 09:00 → Q47–58: .140 vs .208. Rank projection 27/29 inside p10–p90; top-8 7/8.
- Defend-timing (opponents' active shifts) right 3/3; ENERGIZED odds good; district points exact; climb from TBA.
- Video helps only modestly (−.014 Brier, n.s.) and **only as positive evidence** (9317 underrated ✓; every
  "video says worse" call was wrong — under-attribution, e.g. Q12 blue TBA 297 vs credited 164).

## What's broken (consensus)
1. **Identity/coverage lies.** "ID 27%" averages v1 placeholder 0.2 with real OCR conf → red on every team. v1 tracks
   still feed team pages; Q28 (v1, no per-frame conf) leaks 392 credited shots into spec/defense (4180 56.7 → 28).
   Robot video = 11 quals (Q1, Q10–19, Sat ≤14:50), 1–4 per team, and no page says so.
2. **Inconsistent numbers.** 4911 cycle 9.7/9.3/16.2, made/match 99.5/93.5, 2046 auto 30/0.0/–; win% 33/30/31 for
   one match; raw counts not reconciled to TBA (2522 credited 175 > alliance 171).
3. **Picklist page** ranks by min-max of video-only stats (n 1–4) → champion's 1st pick 15th. Spearman .65 vs .92 blend.
4. **Plans only for the next match**; snapshot `preview_url` null → no Q51/Q55 plan at 09:00.
5. **Credibility footer** shows a constant 58-match backtest (future matches) vs a strawman EPA (.189).
6. **No alliance-selection view** though goals.py simulates the draft.
7. Labels: "EPA = public prior" (it's local, as-of), "Blend = EPA + video" (video is 10–36%), factor weights as "+100",
   "Endgame/climb +23" is FUEL, "−5.6 fuel" green, xy in metres, RP shown red/blue not us/them, unlabeled chips,
   "4911 kept 60%… defending it pays" (opponent advice in our plan), 4 different defense-effect numbers.

## Trust policy (encode everywhere) — `live/coverage.py`
- Verified video match = v2 (identity.method `ocr*`) AND team_conf ≥ 0.6. v1 tracks feed nothing but replays.
- n ≥ 3 → fact; 1–2 → grey + "n=2 (Q12, Q17)"; 0 → hidden. Always "as of <last verified match>".
- Counts reconciled to TBA alliance totals; >20% unattributed → show "≥X", never list as under-rated.
- Win prob: blend headline, EPA beside it, "models disagree" chip at >15 pts; round to 5%, clamp 3–97%.
- Bonus odds clamp 3–97%; hide TRAVERSAL while nobody at the event has achieved it.
- Defense gains with basis "prior" → "assumption", no decimals; key factors need ≥ 2 effective matches.

## Work split
**Agent M — match day** (owns `live/strategy/dashboard.py`, `preview.py`, `sim.py`, `model.py`, `web/dash.js`,
`live/strategy/DASHBOARD.md`):
- Plans for **every** remaining match of ours (tabs Q47 | Q51 | Q55), kept in snapshots (stamped as-of).
- **Queue card** (six lines, prose behind "details"): auto win % → our active shifts, and the swap if we lose auto;
  defend who/when/where on a mini field (not metres); expected opposing defenders (defense library: matches
  defended, effect ± range); RP line (win, ENERGIZED, SUPERCHARGED; TRAVERSAL hidden per policy); foul-risk line.
- **Goal mode**: win < 10% or > 90% → headline is the RP lever (e.g. ENERGIZED: expected 125 vs 100 needed).
- One win % per match everywhere; RP us/them; label fixes above; remove self-defense advice from our plan;
  suggested defender not just "lowest scorer" (account for passing / feeding value).
- Ratings table: drop ID%, add "video n (last Qx)" from coverage.py; EPA (labelled "EPA, local as-of" or
  Statbotics when up) beside Blend; disagreement chip; relabel Blend.
- Honest footer: as-of backtest (only matches before the snapshot) vs `epa_pre`, with interval and the video increment.
- Investigate the low-biased auto prediction (our alliance auto under-predicted 3/3; levers say 13.9 vs alliance
  autos 37–58): show TBA auto history beside the prediction; fix the shrinkage if it's a bug.

**Agent S — selection & scouting truth** (owns `live/strategy/goals.py`, `district.py`, `live/scouting.py`,
`live/coverage.py`, `web/rankings.js`, `web/teams2.js`, `web/quant.js`, `web/event.js`, `web/defense.js`,
`web/app.js` (ID pill only), `web/schedule.js`):
- Apply the trust policy to team pages, spec sheets, compare, picklist, defense library: v1 tracks out, Q28-style
  leak closed, one canonical (shrunk) value per metric with "raw (n)" tooltip, TBA-reconciled counts, "auto not
  tracked" when it contradicts TBA (2046), climb from TBA per robot, coverage banner.
- **Picklist**: default sort by blend ± sd; columns EPA, OPR, video made/match with n and reconciled flag,
  "video disagrees" chip; grey n < 3; drop "Dead %" at n ≤ 2; climb weight off when event tower OPR ≤ 10.
- **Selection tab** (replay-aware via `goals --asof`): keep goals.py draft sims → projected seeds with P(captain),
  per team P(round-1 pick), P(round-2 pick), most likely captain; for 4911 one card per likely captain (what they
  lack vs what 4911 brings, one-line pitch); DCMP odds per outcome (A1–A8 × R1/R2 × playoff round) and the DCMP need
  in pick terms ("need ~44: R1 pick of A1–A6 + finalist, or win"); manual "captain picks X" scenario.
- Trends: last-N TBA trend + EPA change with CI. Banner on non-replay pages: "end-of-event data, not replay time"
  (or make them replay-aware if cheap).
- Kill: ID pill, video-only picklist default, Dead% n ≤ 2, ⚠ on every rank, climb lever when tower OPR ≤ 10, blank
  auto columns, per-shot accuracy CIs, 2-point sparklines, v1 heatmaps/autos on team pages.
