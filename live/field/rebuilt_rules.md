# REBUILT 2026 rules facts (game-facts)

Machine-readable copy: `rebuilt_rules.json`. Frame: metres, X 0..16.541 (blue wall X=0), Y 0..8.069, point-symmetric.
Primary source: 2026 Game Manual **Version TU22** (final Team Update 22, 21 Apr 2026), plus the 2026 Field Dimension Drawings (FE-2026 and GE-26300 hub sheets).

## Sources
- Game Manual: https://firstfrc.blob.core.windows.net/frc2026/Manual/2026GameManual.pdf
- Team Updates (combined, TU22 final): https://firstfrc.blob.core.windows.net/frc2026/Manual/TeamUpdates/REBUILT_TeamUpdate-Combined.pdf
- Field Dimension Drawings: https://firstfrc.blob.core.windows.net/frc2026/FieldAssets/2026-field-dimension-dwgs.pdf
- WPILib tags: `live/field/2026-rebuilt-welded.json` / `.csv` (inches)

## Key numbers (manual section in brackets)
| item | value | source |
|---|---|---|
| FUEL diameter | 0.150 m (5.91 in) | 5.10.1 |
| FUEL mass | 0.203-0.227 kg (0.448-0.500 lb) | 5.10.1 |
| HUB footprint | 1.194 m square (47 in) | 5.4 |
| HUB centres | red (11.915, 4.035), blue (4.626, 4.035). Tags give 4.6255 / 11.915 / 4.0346. Confirmed | 5.4, tags 2-5,8-11,18-21,24-27 |
| HUB opening | hexagon, ~1.06 m across (41.7 in; drawing 41.9 +/-0.5 in) | 5.4 Fig 5-7 |
| HUB opening height | front edge 1.829 m (72 in) off carpet. Rear rim height **unverified** | 5.4, drawing sheet 4 (72.000 in) |
| Scoring | FUEL counts once it passes through the top opening and the sensor array | 6.5.1 |
| BUMP | 73 x 44.4 x 6.513 in (1.854 x 1.128 x 0.165 m), 15 deg ramps | 5.5 |
| TRENCH | 65.65 x 47 x 40.25 in. Clearance under arm **0.565 m (22.25 in)**, width 1.279 m | 5.6 |
| TOWER | 49.25 in wide, 45 in deep, 78.25 in tall. Rungs LOW 0.686 m (27 in), MID 1.143 m (45 in), HIGH 1.600 m (63 in) | 5.8 |
| TOWER levels | L1: no longer on carpet or tower base. L2: bumper above LOW rung. L3: bumper above MID rung | 6.5.2 |
| OUTPOST | opening 0.808 m wide x 0.178 m tall, bottom at 0.714 m. Chute holds 25 FUEL. Area 3.404 x 1.803 m | 5.9.2, 5.3 |
| DEPOT | 1.067 x 0.686 m (42 x 27 in), 1 per alliance, along the alliance wall. Y centres **unverified** | 5.7 |
| Alliance zone | 4.028 m deep (158.6 in), X 0..4.028 blue, 12.513..16.541 red | 5.3 |
| Neutral zone | X 4.028..12.513. Manual states 283 in (7.19 m) deep, which does not match 651.2 - 2*158.6 = 334 in. **Discrepancy, unverified** | 5.3 |
| Centre line | X = 8.2705 m | 5.3 |
| Human starting line | 0.610 m from blue wall (24 in) | 5.3 |
| Periods (match clock) | AUTO 0-20 s; TRANSITION 20-30 s; SHIFT 1 30-55; SHIFT 2 55-80; SHIFT 3 80-105; SHIFT 4 105-130; END GAME 130-160 s | 6.4 Table 6-2 |
| Pause AUTO to TELEOP | Manual says a **3 s scoring-assessment delay**, not a physical pause. Assumed 3 s, not stated | 6.4, 6.5 |
| Hub assessment | FUEL keeps scoring for 3 s after the hub deactivates, and 3 s after AUTO and TELEOP timer 0:00 | 6.5 |
| Hub status | AUTO, TRANSITION, END GAME: both active. Shifts 1-4 alternate; the AUTO winner's hub is inactive in SHIFT 1 (full table in JSON). Ties are random via FMS | 6.4.1 Table 6-3 |
| "uncounted" | Spec term for FUEL into an inactive hub. Manual gives no points for it. The term itself is from TBA, not the manual | 6.4.1 |
| Human player delivery | FUEL enters only via the CHUTE, the bottom OUTPOST opening, or thrown over the alliance wall from the OUTPOST AREA (violation otherwise, MAJOR FOUL). Direct HP throw into the hub: not addressed, **unverified** | G425 |
| Robot weight | 115 lb (52.16 kg), excl. bumpers and battery | R103 |
| Starting size | perimeter <= 2.794 m (110 in), height <= 0.762 m (30 in) | R104 |
| Extension | horizontal <= 0.305 m beyond perimeter (one direction only); total height <= 0.762 m | R105-R107 |
| Bumper extension | <= 0.102 m (4 in) | R403 |
| Points | FUEL in active hub: 1 (auto), 1 (teleop). Inactive: 0. TOWER auto L1 15 (max 2 robots). TOWER teleop L1 10, L2 20, L3 30 | 6.5.3 Table 6-4 |
| G403 (TU22) | In AUTO, a robot may not completely cross the CENTER LINE | G403, TU22 |

## Derived (not directly in the manual). Estimates, unverified against drawings
- **BUMPs.** Y extents: hub +/-23.5 in, then 73 in outward, i.e. blue [1.584, 3.438] and [4.631, 6.486] m. X: hub centre +/-22.2 in. Mirror for red.
- **TRENCHs.** X: centre +/-32.8 in. Y: [0, 47 in] and [270.7, 317.7 in]. Mirror for red. Tag positions agree in X, but the Y extents are a guess.
- **TOWER footprint centres.** From tags 31/32 (blue, Y 3.962 m) and 15/16 (red, Y 4.108 m).
- **OUTPOST centres.** Midpoints of tags 29/30 (blue, Y 0.882 m) and 13/14 (red, Y 7.187 m). Opening height 0.178 m as stated.
- **DEPOT Y centres** (blue 2.56 m, red 5.51 m). These are estimates only.
- **Hub opening rim height.** The 72 in front-edge figure is stated. The rear rim height, which is what a 3D trajectory test needs, is unverified.

## Caveats
- The manual says nominal dimensions carry no tolerance. The official CAD and drawings are authoritative.
- The 2026 Game Overview PDF was not read (binary-only fetch). Figure values come from the manual.
- Mirror symmetry was checked against tags: red 1/12 vs blue 17/22 match in X and Y.
