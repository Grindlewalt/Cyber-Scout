# Cyber-Scout — the complete guide

> **Describes commit `cc6297c`** ("Default look: Jet & Paper following the system appearance …", 2026-10-12 22:08 PDT).
> Written 2026-10-12/13. Where this guide and an older doc (`README.md`, `live/HANDOFF.md`, `live/SPEC_V2.md`,
> `server/README.md`, `deploy/README.md`, `live/strategy/README.md`) disagree, the guide was checked against the
> code and says so in a **Docs vs code** note.
>
> **In progress at time of writing** (not described here beyond the committed state): a *competitions* change
> (navigation in `web/app.js`, new/changed `server/routers/*`, `live/worker.py`) and a *performance* change
> (`web/nginx.conf`, `web/Dockerfile`, the fetch code in `web/app.js`, `live/sitev2.py`). If those files differ from
> `git show cc6297c:<path>`, the code is newer than this guide.

## Contents

| file | what it covers |
|---|---|
| [architecture.md](architecture.md) | services and containers, volumes, ports, users, how they talk, where every piece of state lives, the request path |
| [pipeline.md](pipeline.md) | the video pipeline stage by stage: video choice → download → camera pose → detection → tracking + identity → station cams → FUEL → shots → intakes → analytics → site JSON; livestream ingest |
| [strategy.md](strategy.md) | the rating model, simulator, dashboard / queue card, backtests, rankings + district points + DCMP goal, Selection, scouting truth, defense library, measured accuracy |
| [server.md](server.md) | the API: every endpoint, auth, sessions, CSRF, roles, audit + revert, lockdown, validation, DB schema, tests |
| [web.md](web.md) | the single-page app: boot order, router + plugin registry, every page file, helpers, themes / layouts, design lab, CSP |
| [deploy.md](deploy.md) | install, `.env`, compose, GPUs, deploying a change, the updater, backups, operations, troubleshooting |
| [files.md](files.md) | every file in the repo with its purpose; key functions of the big files |
| [security.md](security.md) | threat model, defences layer by layer, audit findings and status, open owner actions |

Read order for a newcomer: this page → architecture → pipeline (skim) → strategy → whichever of server / web /
deploy you are about to touch. `files.md` is the lookup table.

## The system on one page

Cyber-Scout is four containers built from this repo plus (optionally) a host-side updater:

```
                ┌──────────── worker (GPU) ────────────┐        ┌─ recorder (same image, --recorder) ─┐
 TBA API  ───▶  │ poll TBA → pick video → download →   │        │ record the event's YouTube live    │
 YouTube  ───▶  │ process → enrich → build site JSON → │ ◀──────│ stream, cut one clip + record per  │
                │ strategy / scouting / goals builds    │ files  │ match from the broadcast overlay   │
                └──────────────┬────────────────────────┘        └────────────────────────────────────┘
                               │ writes data/site/api/<ev>/*.json, media/<ev>/<match>.mp4
                               ▼
 browser ─▶ (Cloudflare tunnel | LAN | Tailscale) ─▶ web (nginx :7560) ──auth_request──▶ api (FastAPI + SQLite)
                                                     serves SPA, /api/* JSON, /media/*   accounts, roles, settings,
                                                                                        picklists, notes, overrides,
                                                                                        writes data/config/worker.json
```

The worker and the API never call each other over HTTP. They share the `data/` directory: the API writes
`config/worker.json`, `events/<ev>/overrides.json`, sources / layout files and job requests; the worker reads them on
its next poll and writes everything under `events/` and `site/api/`. nginx serves the generated JSON and videos
directly from disk, after asking the API (`GET /app/v1/auth/check`) whether the request has a valid session.

### From "a match is played" to "a strategist reads the queue card"

1. **The match ends.** Two things can notice it:
   * the **recorder** has been recording the event's live stream in 5 s chunks. It reads the broadcast clock and
     overlay, and about 20 s after the final buzzer writes a *livestream record* (teams, score, start/end time read
     off the overlay) and cuts a clip into `MEDIA/live/<ev>/<video>/`. The next site build shows the stream score
     at once (`score_source: "livestream"`);
   * **TBA** posts the result. The worker polls TBA every `POLL_SECONDS` (default 120 s; ETag-cached).
2. **The worker picks a video.** For a played match (or a trackable stream clip) without a
   `data/events/<ev>/matches/<match>.json`, it builds a candidate list (`live/videos.py:candidates`, steered by the
   event's video sources / `event.json`: FIRST Washington's "Wide Match N" overhead upload first). If nothing is
   uploaded yet the match is `no_video` and retried after 5 minutes.
3. **Download** with yt-dlp (forced IPv4) into `data/cache/`.
4. **Process** (`live/process.py:process`, ~3–4 min on the GPU): check the camera has not been bumped (AprilTags +
   centre line; re-solve the pose if it moved and carry the verified pose to the next match), find the match start
   (broadcast clock or robot motion), run the YOLO robot detector (ONNX) at 30 Hz, build field-space tracklets,
   read bumper numbers with PP-OCRv3, and solve which tracklet is which of the six TBA teams with one integer
   program (`live/fuse.py`). Every robot gets a per-frame identity confidence. If the bumper reads say the video
   is another match's (integrity check), the next upload is tried. Result: `matches/<match>.json`.
5. **Enrich** (`live/enrich.py`): FUEL tracking (`fuel.py`: yellow moving blobs → 2D tracks, airborne score),
   shot reconstruction (`shots.py`: a 3D ballistic arc per ball, shooter, made / missed / pass, period, hub
   state, reconciled against TBA's per-period hub counts), floor intakes (`intakes.py`) and per-robot analytics
   (`analytics.py`). Frames whose identity confidence is below 0.5 are quarantined: shots launched there are
   not credited to a team. Optional `HUBCAM=1` pass adds station-camera bumper reads and made/miss checks.
6. **Keep the video** at `MEDIA/<ev>/<match>.mp4` (served at `/media/…`), mark the match `done` in
   `status.json`.
7. **Build the site** (`live/worker.py:build_site`, after every processed match and on every idle poll):
   event, match and team JSON (`live/sitev2.py`), then three builds in turn:
   * `live.strategy.dashboard --site --snapshots --only-missing` — ratings (EPA prior + TBA + verified video),
     Monte-Carlo match predictions, a **plan with a queue card for every remaining match of our team**, rank
     projections, replay snapshots every 30 min;
   * `scouting.site_build_extra` — verified-only team pages, spec sheets, the defense library, `scout.json`
     (the one canonical per-team numbers), plus replay snapshots;
   * `goals.site_build` — rankings, district points, DCMP goal odds, schedule with per-period breakdown,
     `selection.json` (alliance-selection sims and pitch cards), plus replay snapshots.
8. **A strategist opens the Dashboard** (`#/<ev>/dash`). The browser loads the SPA from nginx, signs in
   (session cookie), and fetches `/api/<ev>/dashboard.json`; nginx checks the session with the API and serves the
   file from disk. The **queue card** for the next match says: our win % (blend, with EPA beside it), what winning
   or losing auto does to our active shifts, who to defend, when and where, which opponent is likely to defend us,
   the RP line (win, ENERGIZED, SUPERCHARGED), foul risk — or, when the match is nearly decided (win < 10% or >
   90%), the bonus RP that is closest to a coin flip ("goal mode").

Nothing in that chain needs a human, except setting up an event's video once (sources, layout boxes, camera
calibration) and, optionally, correcting a robot's team (an identity override; see the glossary).

### Things that are true right now on atomosserver

* Stack at `/opt/stacks/cyber-scout` (not `/opt/cyber-scout` as `deploy/README.md` assumes), owner `Grindlewalt`
  (uid 1000). `app/` is a copy made with `git archive`, **not a git checkout**. The updater is **not installed**.
* `.env` has **`PAUSE_PROCESSING=1`**: the worker's loop does nothing at all (no processing *and no site builds*)
  until it is removed. The recorder and API run independently of it.
* The root filesystem is full (3 GB free of 915 GB). `data/` is 1.5 GB; videos and livestream recordings live on
  the NAS (`MEDIA_DIR=/mnt/nas/autoscout-media`, 7.2 TB free). Image rebuilds need several GB on `/`.
* One event, `2026wasam` (PNW District Sammamish), 74 processed matches in `data/events/2026wasam/matches/`.
  There is no `camera.json`, `sources.json` or `layout.json` under `data/events/2026wasam/`: production uses the
  calibration shipped in the image (`events/2026wasam/` in the repo) and the hard-coded Sammamish layout.
* `HUBCAM` is off; worker and recorder both use GPU 0 (`GPU_DEVICE=0`).
* A separate design-lab container `cyber-scout-lab` (not part of the compose stack) serves a real dashboard
  snapshot **without sign-in on `0.0.0.0:7570`** — see [security.md](security.md) (finding N1).

### Known gaps the owner should know about (details in the linked pages)

* The **team number and season goal set in Admin are not used** by the strategy builds; they read the `DASH_TEAM`
  / `DASH_GOAL` environment variables (default `frc4911` / `DCMP`). `web/dash.js` also hard-codes `frc4911`.
  ([strategy.md](strategy.md), [web.md](web.md))
* *(Fixed in the cleanup pass, 2026-10-13.)* The api container now gets an explicit allowlist of `.env` values
  (`SESSION_IDLE_DAYS`, `SESSION_MAX_DAYS`, `PASSWORD_MIN`, `MEMBER_PASSWORD_MIN`, `SETUP_CODE_REQUIRED`, `COOKIE_SECURE`, with the `server/config.py` defaults); still no
  `env_file`, so the one-time `TBA_KEY`/`EVENTS` import stays off on purpose. ([deploy.md](deploy.md),
  [server.md](server.md))
* The **no-password sign-in default is `never`** (Off), not `lan` as `server/README.md` says; the UI offers only
  Off / On-for-testing. ([server.md](server.md))
* *(Fixed in the cleanup pass.)* `goals.py` fed the event's teams into the season simulation at one third of their
  blend rating; it now passes the blend unchanged (DCMP-odds Brier on the as-of replay .129 → .122 for the event's
  teams). ([strategy.md](strategy.md))
* *(Fixed in the cleanup pass.)* Notes and identity fixes have a UI: a Notes card on the team and match pages and a
  "Wrong team?" form on the match page (scouts' fixes go to the per-competition queue `#/<ev>/fixes`).
  ([web.md](web.md))
* Every idle poll (120 s) rebuilds the whole site, including the dashboard, the defense library and all
  `scout/<ts>.json` replay snapshots. ([pipeline.md](pipeline.md), [architecture.md](architecture.md))
* `camera_<source>.json` (needed to track from a livestream) is written by no code; it must be made by hand.
  ([pipeline.md](pipeline.md) §14)

## Headline numbers (measured, 2026wasam)

| what | number | source |
|---|---|---|
| identity, all samples right (19 hand-labelled matches) | 87.6% (95.3% of answered; 98.9% at conf ≥ 0.8) | [pipeline.md](pipeline.md) §4 |
| identity with station-camera reads (dev set) | answered 95.6% → 98.5% | §5 |
| shots: traced / TBA made, held-out | ≈ 0.91–1.00 per alliance (red ≈ 1.00, blue ≈ 0.94 after fix) | §8 |
| shot attribution / made calls / missed calls | ~96% / ~90% / ~60% (missed-vs-pass 0.83) | §8 |
| intakes | precision ~0.88, recall ~0.45 (lower bound) | §9 |
| win-probability Brier, 58 quals (as-of footer) | blend .1185, no video .1333, EPA .1474, coin .25 | [strategy.md](strategy.md) |
| blend − EPA | −.029 [−.051, −.009] (90% CI) | strategy.md |
| video increment | −.015 [−.036, +.004] (not significant) | strategy.md |
| defense: share of scoring kept while defended (event-wide, placebo-controlled) | 0.71 [0.56, 0.88]; 0 of 39 per-team tests significant (so every queue-card defense effect is an "assumption") | strategy.md |
| district points | exact vs TBA on all 309 2026 PNW team-events | strategy.md |

## Glossary

**2026 REBUILT** — the FRC game this is built for. Field 16.541 × 8.069 m, point-symmetric; blue alliance wall at
X = 0, red at X = 16.541; Y = 0 is the far wall in the broadcast image. Red end is image-left.

**FUEL** — the game piece: 0.150 m yellow foam balls. Robots (and the human player from the OUTPOST) shoot them
into their alliance's **HUB** (hexagonal opening, front edge 1.829 m up). One point per FUEL in an *active* hub.
TBA reports counted FUEL per alliance per period in `score_breakdown.hubScore` (`autoCount`, `transitionCount`,
`shift1Count`…`shift4Count`, `endgameCount`, `uncounted` = FUEL into an inactive hub).

**Periods** (match clock): AUTO 0–20 s, TRANSITION 20–30, SHIFT 1 30–55, SHIFT 2 55–80, SHIFT 3 80–105, SHIFT 4
105–130, END GAME 130–160. The pipeline counts the 3 s AUTO→TELEOP assessment pause as auto and credits FUEL that
enters ≤ 3 s after a hub deactivates to the previous period (`live/field/rebuilt_rules.json`, `analytics.match_clock`).

**Hub shifts / hub active** — both hubs are active in AUTO, TRANSITION and END GAME. In SHIFTs 1–4 they alternate:
the alliance that **won auto** has its hub *inactive* in SHIFT 1 and 3 (active in 2 and 4); the auto loser is
active in 1 and 3. So winning auto is a schedule decision, and the right time to defend an opponent is during
*their* active shifts (= our inactive ones). The model's `beta` is the value of that schedule.

**TOWER / climb** — L1 / L2 / L3 climbs (10 / 20 / 30 points in teleop; auto L1 15 points, max 2 robots). Climb
data comes from TBA per robot (`endGameTowerRobotN`), never from video.

**ENERGIZED / SUPERCHARGED / TRAVERSAL** — the 2026 bonus ranking points: ≥ 100 FUEL counted (ENERGIZED),
≥ 360 FUEL counted (SUPERCHARGED), ≥ 50 TOWER points (TRAVERSAL). RP = 3·win + 1·tie + bonuses
(`live/strategy/rank.py`). TRAVERSAL is hidden in the UI while nobody at the event has earned it.

**Wide / overhead camera** — the fixed high field camera in FIRST Washington broadcasts ("Wide Match N" uploads).
All robot tracking uses it. **Station cams / insets** — the two driver-station cameras shown as insets at the
bottom of "Match N" uploads; used optionally (`HUBCAM=1`) for extra bumper reads and made/miss checks.

**Camera pose / calibration** — `events/<ev>/camera.json`: OpenCV lens model + pose fitted from AprilTags and
straight field lines (`live/calibrate.py` or the calibration wizard). `camera_<source>.json` for a livestream
camera. The per-match pose check can re-solve a bumped camera; the verified pose is carried forward in
`pose_latest.json`.

**Layout** — `data/events/<ev>/layout.json`: boxes drawn per video source in the layout editor (field area,
station insets, scoreboard, masks). Replaces hard-coded Sammamish pixel rectangles.

**Tracklet** — a short, pure piece of one robot's path in field metres (Kalman filter + speed gate). Tracklets are
cut wherever bumper reads change team.

**Identity / OCR / ILP** — bumper numbers read with PP-OCRv3, scored against the six match teams (and every event
team for the integrity check). One integer linear program assigns tracklets to six team paths, with a "limbo"
state for frames where a robot is not tracked. Each track carries `team`, `team_conf` (whole match) and `conf`
(per frame at 10 Hz). `identity.method` `ocr-ilp` = tracker **v2**; older matches without it are **v1**
(event-level appearance labelling; per-frame conf missing).

**Quarantine** — frames with identity `conf < ID_CONF_MIN` (0.5) are treated as unobserved by analytics, and
shots launched there get `team: null, unattributed: "low_identity_conf"`. Nothing is guessed.

**Integrity** — open-set OCR check that the video really is this match (wrong video → try the next upload; on a
livestream clip → reject the record) and flags substitutions (informational).

**Overrides / identity fixes** — `data/events/<ev>/overrides.json`, `{match: {alliance: {track idx: team}}}`.
Through the API (`PUT /app/v1/events/<ev>/overrides/<match>`) a scout's fix is queued for approval by default and a
strategist's is applied directly. In the web app: "Wrong team?" on the match page proposes / applies one, and
`#/<ev>/fixes` is the approval queue.
The worker applies them on its next site build (for v2 matches per match; v1 matches re-solve event-wide). An
overridden track keeps the tracker's per-frame confidence, so quarantine still applies.

**Shot** — one reconstructed FUEL flight: shooter (robot track / human player / unknown), 3D origin and launch
velocity, landing, target hub, `outcome` made / missed / pass, period, hub active, confidence.

**Reconcile** — two related things. (1) Per shot pipeline: `shots.reconcile` compares traced made FUEL per
alliance per period with TBA's counts (held-out traced/TBA ≈ 0.91–1.00). (2) Per team stats: `coverage.reconcile`
compares FUEL *credited to robots* + human player with the TBA alliance total and gates the match: `ok`, `under`
(> 20% uncredited → only a lower bound), `over` (robots > 120% of TBA), `no_tba`. Team FUEL stats use only `ok`.

**Verified video match** — a match tracked by v2 (`identity.method` starts with `ocr`) in which that team's track has
`team_conf ≥ 0.6` (`live/coverage.py:VERIFIED_CONF`). Only verified matches feed team numbers; v1 tracks feed
nothing but replays.

**Coverage tiers** — per team: n ≥ 3 verified matches = `fact`; 1–2 = `grey` (shown with "n=2 (Q12, Q17)");
0 = `none` (hidden). Always shown "as of" the last verified match.

**EPA** — Expected Points Added, a Statbotics-style rating of a team's points contribution. Here there are two:
the *pre-event prior* (the team's rating from its earlier events this season, fitted from TBA, or Statbotics) and
the *as-of EPA* used as the comparison baseline (Statbotics team-match history or a local running EPA; local in
production because Statbotics was returning 503).

**Blend** — the dashboard rating, in points per robot per match (three partners' blends ≈ an alliance score): the
EPA prior updated by this event's TBA alliance results and by verified video (a hierarchical GLS model per FUEL
component: auto, transition, active shifts, endgame). `video_weight` is the share
of a rating's information that came from video; `weights` splits prior / TBA / video.

**Video as censored evidence** — robot rows count only FUEL confidently credited (attribution ≥ 0.8) plus an equal
share of the rest, scaled to TBA; when > 20% of an alliance-period is uncredited, the credited counts are only
lower bounds that may raise a rating, never lower it; over-credited results give no robot information.

**Queue card** — the six-line plan for one upcoming match (`plans[].queue` in `dashboard.json`), described in
step 8 above. **Goal mode** — the card's headline when win < 10% or > 90%: the bonus RP lever instead.

**Plans / snapshots / replay** — the dashboard builds one plan per remaining match; `dash/<ts>.json` snapshots
(every 30 min of each event day) let the UI replay the event "as of" a time, using only results posted before then
and video only 10 min after posting. `scout/<ts>.json` and `selection/<ts>.json` are the matching snapshots.

**Honest footer** — the dashboard's accuracy line: an as-of backtest over quals posted before the view's time, each
predicted only from matches before it (Brier: blend vs no-video vs EPA vs coin), with a 90% interval for the
video increment.

**Selection** — `selection.json`: Monte-Carlo of the rest of the quals, the draft and playoffs; P(captain), P(first
pick), P(second pick) per team, per-captain re-simulation ("if C takes us vs their next best"), pitch cards, and the
district points we need from this event ("need grid").

**District points / DCMP goal** — `district.py` implements FRC district-points rules (exact against TBA for all
2026 PNW team-events); `goals.py` simulates the season to P(reaching DCMP / Worlds) and the points still needed.

**Defense library** — `defense.json`: every detected defense episode on a scorer, with the scorer's observed vs
expected active-hub scoring, compared against random undefended *placebo windows*; per-team retention with bootstrap
intervals, Benjamini–Hochberg q-values and FCR-adjusted intervals; `significant` only when q ≤ 0.10 and the
adjusted interval excludes 100%.

**Livestream record** — a JSON the recorder writes per detected match on the stream (teams and score OCR'd from the
overlay, start/end time, clip path) under `MEDIA/live/<ev>/<video>/matches/`. It gives fast results before TBA, and
— only if the stream has a calibrated fixed field view — tracking from the clip before the YouTube upload exists.

**Site JSON** — everything under `data/site/api/` (served at `/api/`): `events.json`, `<ev>/event.json`,
`m/<match>.json`, `t/<team>.json`, `dashboard*.json`, `dash/`, `preview/`, `rankings.json`, `schedule.json`,
`selection*.json`, `scout*.json`, `spec/`, `defense.json`.

**v1 / v2** — tracker generations (see Identity); also "V2=1" enables the FUEL/shots/analytics enrichment.

**PAUSE_PROCESSING** — `.env` flag; `1` makes the worker idle entirely (production is paused at time of writing).

**Dev set** — 2026wasam qm1, qm10–19 (showcase qm17 / team 4911): the matches every tuning change is checked on;
full-event reprocessing is avoided until final.
