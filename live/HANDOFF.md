# Handoff notes (read before starting work on a module)

Condensed from each module's build report, so a fresh agent can pick a piece up
without the original agent's context. Contracts: `live/SPEC_V2.md`.
Run heavy jobs on atomosserver (`/mnt/nas/autoscout-scratch`, `rtenv` Python, GPU 1 only,
`nice -n 10`, detached). Never touch `/opt/stacks/cyber-scout` (production) or GPU 0.

## fuel.py — moving-fuel detection + 2D tracking (done)
- Videos are 30p content in a 60p container (every other frame a duplicate) — handled; tracker works in seconds.
- Yellowness map at half-res over the field extruded 5 m up; 2026 FIRST overlay boxes masked (`OVERLAY_BOXES`,
  hard-coded for 1920x1080 FIRST graphics). Asymmetric running background (falls ~0.05 s, rises ~0.8 s) so streams
  of balls on one path still register. Oversized blobs split by k-means (fast shutter: blob ≈ 1 ball).
- Quadratic prediction + Hungarian; fragments stitched only when one 3D ballistic flight explains them.
- airborne_score from 3D ballistic-vs-rolling fit, image parabola curvature, implied rolling speed, leaving field.
- Perf: 37–64 s per match on the server. ~1500 airborne tracks/match ≈ 2.5–3× real shots (fragments + non-shots);
  per-ball recall in dense streams ~70%; airborne precision ~80–85%.
- Failure modes: touching balls in dense volleys; apex hidden behind the score bar (y<134 px, x 446–1474);
  yellow shirts; robot-shoved balls; moving/wrong camera (`view_ok`=0 → ignore those tracks).
- API: `FuelTracker(cam).push(idx, t, bgr)/.push_av(...)/.result()`, `track_fuel(...)`, `load_camera(cam_json, match_json)`.

## shots.py — shot reconstruction (done; follow-ups open)
- Ballistic fit (gravity + linear drag 0.15/s) with launch time fixed ⇒ linear in image measurements; every shooter
  hypothesis (6 robots @ ~0.55 m launch height, 2 HPs behind the outpost @ ~1.7 m, "floor" bounce) × launch time is
  solved in one batched least-squares; cheapest wins, margin → conf. Attribution and depth solved together.
- Made: track ends inside hub opening/funnel image, opening-anchored arc fits ~as well as free fit, beats
  "landed behind the hub", above rim and falling, no pop-up. Missed = non-scoring flight near an opening; pass = lands
  ≥3 m from launch away from hubs. Many non-shot filters (hopper churn, short/low flights, floor launches).
- Periods via `analytics.match_clock`; pause counted as auto; hub-active from TBA (auto winner inactive shift 1,
  alternating); fuel entering ≤3 s after deactivation counts for the previous period.
- Results: held-out 11 matches traced/TBA made = 0.91 (per alliance-match median 0.92, r=0.96); tuning 0.93.
  Attribution ~96% on checkable shots; made calls ~90%; missed ~60%. ~560 shots/match (335 made, 180 missed, 45 pass).
- UPDATE (shots2 agent): blue undercount fixed (held-out blue 0.853→~0.94, red ~1.00): cause was far-side blue HP
  throws toward the camera ending in the funnel below the near rim, funnel-start veto, track breaks behind blue net
  poles; missed-vs-pass accuracy 0.69→0.83 (labels .work/shots2/labels_*.json). New per-shot pass_kind/receiver_idx.
  Remaining: dense frc8032-style streams lose balls in fuel.py; feeds barely validated; red HP makes slightly over.
- (old) OPEN: blue hub ratio ~0.85 vs red ~1.00 (track ends cluster just outside the projected blue opening: camera pose at
  rim height on image-right? dense blue volleys losing balls?); missed vs pass vs feed-to-teammate; intakes not
  implemented; cross-alliance attributions (6%) are upstream robot identity swaps.
- Data: events in `.work/shots/ev_v14` (tuning), `ev_hold` (held-out: qm21,22,24,25,26,29,30,31,34,35,36);
  server `/mnt/nas/autoscout-scratch/agents/shots/{fuel,vids,ev_*}` (fuel results for 34 matches).

## tracker v2 — OCR-anchored identity (done; integrated by lead)
- live/ocr.py: OcrSampler picks detections (conf≥0.3) not tried for 0.3 s, ≤3/frame; bumper crop (±0.6 m, z 0–0.5 m)
  → PP-OCRv3 det at 0°/±45° + rec in 4 worker processes (GPU via OCR_GPU, default GPU_DEVICE); CTC-scored against
  the 6 TBA teams; ~99.5% of reads right. Models live/models/ocr/ (Apache-2.0).
- live/fuse.py run_labeled: reads set alliance before tracklets; tracklets cut where reads change team; merge never
  across conflicting reads; one ILP (scipy milp) over 6 team paths with "limbo" (untracked, no position claim);
  per-frame conf (reads posterior × alliance, propagated along links; contradicting-read windows capped).
- Results vs hand GT (19 matches, position metric): 87.6% of samples (95.3% of answered, 8% abstain) vs old 32.5%;
  conf≥0.8 → 98.9%. Ghost frames 11.5%→0.9%. Calibrated: 0.8–0.95 → 97.5%, ≥0.95 → 99.3%.
- Output: tracks ordered red1-3, blue1-3 with team/team_conf/conf[@10Hz]; match.identity {method, n_reads,
  per_team_reads, corrections[...] (cut/relink with evidence, encounter, conf, anchored, unresolved)}.
  Limbo frames: obs=0, conf=0 → treat as unknown.
- process(..., teams={"red":[...],"blue":[...]}) — without teams it falls back to v1 output.
- ~200 s/match on the server GPU. Weak spots: robots whose numbers OCR never reads (fonts/angles, e.g. 4469, 949) —
  identity from linking only, conf 0.3–0.7. Next ideas: more angles/digit detector, pre-match frames, appearance
  prototypes learned from read-anchored tracklets, margin-based conf, TensorRT for OCR.
- libapriltag destructor segfault (intermittent) fixed by a single module-level detector in process.check_pose.
- Scratch: .work/tracker/ (eval_new.py, refit.py, corr_sheet.py); server agents/tracker/{code2,work/out,out4}.

## identity — bumper OCR (done) → superseded by tracker v2 above
- Hand ground truth: `.work/identity/ground_truth.json` (20 matches, 114 confident tracks, per-crop reads with
  replay frames). Old labels were chance level (32% per-frame); 67% of old tracks swap robots; some follow opponents.
- PP-OCRv3 det+rec (onnxruntime, ~13 MB, Apache-2.0 via rapidocr_onnxruntime 1.2.3), reads matched against the 6 match
  teams: 96% precise. Swap-aware HMM per (match, alliance): 87% per-frame, calibrated confidence.
- Appearance features are weak (AUC 0.49–0.65) — don't rely on them.

## analytics.py — robot behaviour metrics (done)
- `match_clock(t, match)` maps replay time (s from auto start) to the match clock (pause frozen at 20).
- Canonical red frame (blue point-reflected). Solid: distance/speed/zones, autos, lanes (rules polygons), clustering.
  Shaky: contacts/pins (ID swaps at contact), shadowing proxy, climb timing (lifted robots project wrong), roles
  without shot events.

## web/ — v2 UI (done, against mock data)
- Data contract at the top of `web/app.js`; `live/sitev2.py` adapts analytics → contract. Mock generator
  `.work/web/mock.py`. Compare tab and match-page shot arcs not yet checked with real data.

## intakes.py — floor pickups (done, v0)
- 5 Hz floor-ball density map (static yellow projected to carpet, robot prisms masked) → net ball loss in a 0.75 m
  disc around each robot between [t-1.4,t-0.6] and [t+0.6,t+1.4] s, minus balls shoved aside; blur/cut gating essential.
- ~7 s/match. Precision ~0.88 (68 labelled events), recall ~0.45 (tiny sample) — robots parked on balls are missed.
  Treat intake counts as a LOWER BOUND. Rolling-ball and outpost-chute signals exist but are off (not validated).
