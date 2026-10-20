# File inventory

Every git-tracked file at commit `cc6297c` (2026-10-12), with a one-line purpose, grouped by directory.
The big modules also list their important functions and classes. Line numbers are omitted on purpose because
they drift. Use `grep -n 'def name'` to find a symbol.

**Counts.** There are 496 tracked files. 306 are listed one by one below. The other 190 are grouped binary
assets: 149 lab screenshots, 33 font files, 2 OCR models, 2 copies of `field.jpg`, 1 demo GIF and 3 lab JSON
fixtures.

**Provenance tags.**
- **[U]** means upstream [autoscout](https://github.com/samdev-7/autoscout) by Sam Liu (MIT). This is the
  original single-match research tracker: `src/`, `tools/`, `INSTRUCTIONS.md`, `docs/AUTOSCOUT_README.md`
  and `docs/COMPONENTS.md`.
- Everything else is Cyber-Scout. Upstream files are kept as they were, except that the live pipeline imports
  three of them (see [What the live pipeline uses from `src/`](#what-the-live-pipeline-uses-from-src)).

**"Appears unused"** is written only after grepping the whole repo (code, Dockerfiles, compose, docs) for imports,
CLI invocations and references.

> **Edits in progress when this was written.** Other agents were editing `web/app.js` (nav), `server/routers/*`,
> `live/worker.py`, `live/sitev2.py`, `web/nginx.conf`, `Dockerfile` and the fetch code in the web files. This page
> describes the **committed** state (`git show HEAD:<path>`). After those changes land, the function lists for
> those files may need a refresh.

---

## Contents

- [Root](#root)
- [Local only (not in git)](#local-only-not-in-git)
- [`deploy/`](#deploy)
- [`docs/`](#docs)
- [`events/2026wasam/`](#events2026wasam)
- [`live/` (the pipeline)](#live-the-pipeline)
- [`live/strategy/`](#livestrategy)
- [`server/` (API)](#server-api)
- [`web/` (the SPA)](#web-the-spa)
- [`lab/` (design lab)](#lab-design-lab)
- [`tests/` (pipeline tests)](#tests-pipeline-tests)
- [`tools/` [U]](#tools-u)
- [`src/` [U]](#src-u)
- [Binary assets (grouped)](#binary-assets-grouped)
- [What the live pipeline uses from `src/`](#what-the-live-pipeline-uses-from-src)
- [Files that appear unused or legacy](#files-that-appear-unused-or-legacy)
- [Inconsistencies spotted while inventorying](#inconsistencies-spotted-while-inventorying)

---

## Root

| file | purpose |
|---|---|
| `.dockerignore` | Keeps `.venv`, `.git`, `.env`, `.roboflow_key`, `out` and `runs` out of the Docker build context. It does **not** exclude `.work/` (about 4.8 GB locally), so a local `docker build` uploads it as context. The server is not affected because its `app/` is a `git archive`. |
| `.gitignore` | Ignores secrets (`.roboflow_key`, `*.key`, `.env`), the venv, per-event artefacts (`data/`, `out/`, `frames/`, `runs/`), weights (`*.pt`, `*.onnx` except `live/models/ocr/*.onnx`), media and `.work/`. |
| `Dockerfile` | Worker image, also used by the `recorder` service. It is python:3.12-slim + deno (yt-dlp's JS runtime) + ffmpeg. One big pinned pip layer (onnxruntime-gpu, opencv-headless, av, numpy, scipy, networkx, pupil-apriltags) is kept byte-identical so the ~3 GB layer stays cached. yt-dlp is then installed hash-pinned from `live/requirements-ytdlp.txt`. It copies `src/ live/ events/`, and the entrypoint is `live/entrypoint.sh`. |
| `INSTRUCTIONS.md` | [U] Upstream walkthrough: one broadcast match through calibration, detection, tracking, evaluation and rendering, using `src/` and `tools/`. It does not describe the live pipeline. |
| `LICENSE` | MIT, copyright Sam Liu (autoscout) and Grindlewalt (Cyber-Scout). |
| `README.md` | Project overview: what is measured, the repo layout and credits. It points to `deploy/README.md`, `live/HANDOFF.md` and `live/SPEC_V2.md`. |
| `requirements.txt` | [U] Local dev / upstream Python deps (av, matplotlib, networkx, numpy, opencv, pillow, ...). The worker image does **not** use it; it pins its own deps in `Dockerfile`. |

## Local only (not in git)

These exist in the local working copy and matter, but are git-ignored. No contents are reproduced here.

| path | what |
|---|---|
| `.env` | Local env vars for dev runs. A secret file: the production `.env` lives in `/opt/stacks/cyber-scout/.env` on the server. |
| `.roboflow_key` | Roboflow API key used by the upstream dataset fetch scripts (`src/fetch_datasets.py`, `src/experiments/fetch*.py`, `probe*.py`). Secret. On the free tier there are about 10 credits a month, so don't re-download. |
| `.venv/` | Local Python env (numpy, scipy, opencv, av, networkx, onnxruntime). The docs refer to it as `.venv/bin/python`. |
| `.pytest_cache/`, `__pycache__/` | Test and bytecode caches. |
| `data/field/` | Local field assets for the upstream tools: `2026-rebuilt-{andymark,welded}.{csv,json}` and `2026-rebuilt.png`. The production data root is a different thing (`/opt/stacks/cyber-scout/data`; see `architecture.md`). |
| `.work/` | About 4.8 GB of agent scratch: dev samples (`.work/samples/`), hand ground truth (`.work/identity/ground_truth.json`, `.work/shots2/labels_*.json`), strategist review notes (`.work/strategist/`), the security audit (`.work/security/REPORT.md`, `findings.json`), backtest scripts (`.work/agentM/`), lab screenshots (`.work/lab/shots`) and staging sites. Many docs cite it. It is not reproducible from git. |
| `lab/shots/*.webp` (9 untracked) | New lab screenshots not yet committed (jetdark / sandearth variants). |

---

## `deploy/`

| file | purpose |
|---|---|
| `deploy/README.md` | Operator runbook: install, first run, updates, backup, security notes, day-to-day commands, livestream ingest, adding a new event (calibration) and hardening `.env` options. |
| `deploy/docker-compose.yml` | The stack: `worker` (GPU), `recorder` (the worker image with `--recorder`), `api` (FastAPI) and `web` (nginx, port 7560). Also volumes, caps and limits. It is copied into the stack folder (`/opt/stacks/cyber-scout/docker-compose.yml`). |
| `deploy/docker-compose.cpu.yml` | CPU-only override. `deploy: !reset {}` drops the GPU reservation. Enable it with `COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml` (Compose ≥ 2.24). |
| `deploy/env.example` | Template for the stack `.env`. It holds only infra values (WEB_PORT, PUID/PGID, TZ, MEDIA_DIR, ...); all app settings live in `data/app.db`. |
| `deploy/install.sh` | `curl \| bash` installer and updater (see the outline below). |

`deploy/install.sh` outline (bash; sections, in order):
- `bold/info/warn/die/ask` print helpers. `ask` reads `/dev/tty`, so it works when piped from curl.
- **privileges**: refuses root, needs Docker access.
- **Docker + compose**: checks versions (`ver_ge`).
- **GPU**: probes the NVIDIA Container Toolkit; otherwise uses the CPU override (`CS_CPU=1` forces it).
- **where**: `CS_DIR` (default `/opt/cyber-scout`).
- **code**: `git clone` / pull of `CS_REPO@CS_BRANCH`, or copies `CS_SOURCE`.
- **.env**: writes the infra-only `.env` (`CS_PORT`, PUID/PGID from the invoking user, TZ, MEDIA_DIR).
- **data dir + model**: creates `data/`; downloads `CS_MODEL_URL` into `data/models/robot.onnx` if it is missing.
- **start**: copies the compose files, runs `docker compose up -d --build`, and prints the URL and the one-time
  setup code from `data/setup-code.txt`.
- **one-click updates**: offers `deploy/updater/install-updater.sh` (`CS_UPDATER=y|n`).
- Other options: `CS_PREFIX` (run a second copy side by side) and `CS_YES=1` (non-interactive).

### `deploy/updater/`

The host-side updater behind Admin -> Updates. It is **not installed on atomosserver** at the time of writing: no
`cyber-scout*` systemd units and no `updater/` folder in the stack.

| file | purpose |
|---|---|
| `deploy/updater/README.md` | Design, install/remove, what check / apply / rollback do, the deploy-key setup, the security model and limits. |
| `deploy/updater/cs-updater.py` | The updater itself (stdlib Python, runs on the host as the stack owner). It is the only component that touches git and docker. |
| `deploy/updater/cs-updater.service` | systemd oneshot template (`@NAME@`, `@STACK@` placeholders). One run = heartbeat + any pending request. Hardened with `NoNewPrivileges` and `ProtectSystem=full`. |
| `deploy/updater/cs-updater.path` | Starts the service when `data/config/update-request.json` changes. |
| `deploy/updater/cs-updater.timer` | Runs the service every minute: heartbeat, a missed request, and the daily auto-check. |
| `deploy/updater/install-updater.sh` | Installs or removes the units: system units by default, or `--user` units. Copies `cs-updater.py` to `<stack>/updater/`, writes `updater.conf` once and seeds `known_hosts` with GitHub's key. |

`cs-updater.py` key symbols:
- `class Updater`: all state and actions.
  - `read_request` / `_reject`: open the request file with `O_NOFOLLOW` and enforce a strict schema.
  - `ensure_repo` / `snapshot_if_dirty`: turn a copied `app/` into a git checkout, committing local edits.
  - `fetch`, `check(auto)`: changelog, which images are rebuilt, and disk needed.
  - `apply(ytdlp)` → `_deploy(...)`: the full apply sequence.
    1. Disk check (`free_space`, `disk`).
    2. `backup_db` (sqlite online backup, keeps 5).
    3. `checkout` to branch `cs-deployed`.
    4. `_sync_compose` (keeps `.bak-<time>` copies).
    5. Tags images `:cs-rollback`, then `dc build`, `dc up -d`.
    6. `wait_healthy` / `_health_once`.
    7. `_drop_images` (by id, never `prune`).
    8. On any failure, auto-rollback.
  - `rollback`: back to the previous successful version.
  - `bump_ytdlp` / `carry_ytdlp`: re-pin `live/requirements-ytdlp.txt` with hashes, using pip-compile in a throwaway
    worker-image container.
  - `self_update`: refreshes the installed copy of the updater from `app/deploy/updater/`.
  - `lock` (flock), `heartbeat_thread`, `do(action)`, `tick()` (one systemd run).
- `validate_request(raw)`: the request schema check. The API tests import it too.
- `heavy_prefix` / `describe_touches` / `dockerfile_base`: decide whether a change needs the ~3 GB worker layer
  rebuilt (Dockerfile changed before its first repo `COPY`).
- `validate_ytdlp_lock`: accepts only `name==version`, sha256 hashes and comments.
- `WORKER_PATHS = ("Dockerfile", "live/", "src/", "events/", ".dockerignore")`: paths that mean "rebuild the worker".
- `main()`: CLI `--stack DIR status|check|apply [--ytdlp]|rollback|tick`.

---

## `docs/`

| file | purpose |
|---|---|
| `docs/AUTOSCOUT_README.md` | [U] The upstream autoscout README (how the original tracker works: calibration → detection → geometry → tracklets → identity). |
| `docs/COMPONENTS.md` | [U] Upstream component reference: accuracy, reliability and limits of each tracker piece, measured on one 2026 ONT Niagara match. |
| `docs/PLAN_UNIVERSAL.md` | The "universal" product plan: install, setup wizard, accounts, video sources, layout editor, pages, defense library. It also records the 4-agent work split (A platform, B sources, C competition, D scouting). |
| `docs/STRATEGIST_REVIEW.md` | Results of three critic rounds replaying 2026wasam at Sun 09:00: what held up, what broke, the trust policy (encoded in `live/coverage.py`), and the round 2/3 work splits for agents M and S. |
| `docs/demo.gif` | (binary) Upstream demo animation used by `AUTOSCOUT_README.md`. |
| `docs/guide/*` | This guide. |

## `events/2026wasam/`

Shipped per-event config for PNW District Sammamish. The worker falls back to these when
`DATA/events/<ev>/` has no copy (`worker.ev_file`, `layout.event_dirs`).

| file | purpose |
|---|---|
| `calib_input.json` | Input to `live/calibrate.py`: AprilTag pixel centres (`tags: {id: [u, v, sigma]}`), white-line and guardrail samples (`lines`), image size and the tag CSV path. |
| `camera.json` | Fitted overhead "Wide" camera (`W, H, K, dist, rvec, tvec`, ...), read by `live/camera.py`. |
| `event.json` | Video selection for this event: `prefer: "^Wide Match"`, `reject: "^Match \d"`, `allow_channels`, and `search_title` (YouTube search fallback). Used by `live/videos.py` when no `sources.json` exists. |
| `station_red.json`, `station_blue.json` | Driver-station inset camera calibrations: inset pixels (626/622×372) plus `crop` (inset rect in the "Match N" upload). Read by `live/stationcam.py`. |

---

## `live/` (the pipeline)

All Cyber-Scout. Modules import each other by bare name because `live/` is put on `sys.path`. The strategy
package is the exception: it is run as `python -m live.strategy.*` from the repo root.

| file | purpose |
|---|---|
| `live/HANDOFF.md` | Per-module build notes (fuel, shots, tracker v2, OCR, analytics, intakes, stationcam, hubcam, station OCR, integrity) with measured accuracy and weak spots. |
| `live/SPEC_V2.md` | Data contracts: match JSON, FuelResult, Events, rules.json and site JSON additions; v2.1 identity and quarantine fields; and the metric catalogue. |
| `live/worker.py` | **Main service.** Polls TBA, picks and downloads video, processes, enriches and builds all site JSON. With `--recorder` it runs the livestream recorder supervisor. |
| `live/entrypoint.sh` | Worker and recorder entrypoint. `chown -R -h -P` DATA to PUID/PGID (never following symlinks), then `setpriv --reuid/--regid --clear-groups --no-new-privs python -u /app/live/worker.py "$@"`. yt-dlp is pinned in the image, with no runtime upgrade (audit finding 3). |
| `live/tba.py` | Minimal TBA v3 client with ETag caching (`TBA` class, `match_label`, `short_label`, `sort_key`). |
| `live/videos.py` | Finds and downloads the field-camera video for a match: `candidates(match, cfg, cache)` (TBA videos + YouTube search filtered by `prefer`/`reject`/`allow_channels`), `meta` (yt-dlp metadata, cached), `download` (yt-dlp `-4`, retries), `search`, `match_fields`. |
| `live/sources.py` | Video sources per event (`DATA/events/<ev>/sources.json`) and the API job runner (see the function list below). |
| `live/layout.py` | Broadcast layout per event and source (`layout.json`): clock box, overlay masks, field box, station insets; media grabbing; layout suggestions (see below). |
| `live/camera.py` | `Camera` model for a locked-off full-frame camera: `project`, `img_to_plane`, `img_to_ground`, `cov`, `C`. Also `field_nms`, `in_field`, and `install_geom_shim(cam)`, which exposes the camera as module `geom` for the upstream `src/` tracker modules. |
| `live/calibrate.py` | Camera calibration: the CLI fit from `calib_input.json`, plus the wizard back end (tag detection, named points/lines, fit, overlay). |
| `live/detector.py` | `Detector`: YOLO robot detector on ONNX Runtime (CUDA if available) → rows `frame, t, tid, x0, y0, x1, y1, conf, alliance`. `alliance()` is the bumper-hue rule (same as `src/detect_match.py`). |
| `live/process.py` | **One match video → six tracks.** Pose check, 30 Hz detection, OCR sampling, match start, tracking + identity (`fuse.py`), integrity, and the match JSON. |
| `live/fuse.py` | Tracker core: pure tracklets → identity solve → RTS smoothing. `run` = v1 wide-only path cover; `run_labeled` = v2 OCR-anchored 6-team ILP with limbo. |
| `live/ocr.py` | Bumper-number OCR (PP-OCRv3 det + rec on ONNX Runtime), CTC-scored against candidate team numbers, run in 4 worker processes during detection (`OcrSampler`). |
| `live/integrity.py` | Match integrity from open-set reads. `check` detects the wrong video and substitutions; `should_retry` makes the worker try the next upload; `lineups` and `normalise` prepare the inputs. |
| `live/identity.py` | Event-level robot→team assignment. Today `assign_event` passes through tracker-v2 labels (`labeled_match`). The v1 HMM / legacy solvers remain for old match files, and its own OCR pass (`extract_features`) is legacy (see "appears unused"). |
| `live/station_ocr.py` | Bumper reads from the two driver-station insets (HUBCAM=1). `station_reads`, `debias`, and `extra_reads_fn` (the callable `process` uses to add reads to the ILP). |
| `live/stationcam.py` | Driver-station cameras: finds the "Match N" inset upload, syncs it to the Wide video, calibrates (`fit_station`), runs a per-match re-aim check, and provides the `StationCams` API. |
| `live/station_export.py` | Exports one match's station-camera data (sync, poses) for the Analyze camera switcher (`<match>.stations.json`). |
| `live/hubcam.py` | Checks shot outcomes from the station views: arc-guided ball matching, joint two-view refit, and conservative flips. Runs only in `enrich.station_pass` under HUBCAM=1. |
| `live/fuel.py` | Moving-FUEL detection and 2D tracking (yellowness map, running background, Hungarian, ballistic stitching, airborne score, scene cuts). |
| `live/shots.py` | Shot reconstruction: 3D ballistic fit per shooter hypothesis, made/missed/pass, periods, hub-active schedule, and reconciliation against TBA. |
| `live/intakes.py` | Floor-pickup detection (v0, lower bound) from a 5 Hz floor-ball density map around each robot. |
| `live/analytics.py` | Per-robot metrics from tracks + shots: movement, zones, lanes, autos (clustered), defense/contacts/pins, endgame, reliability, fuel, roles. Also per-team aggregates. |
| `live/enrich.py` | v2 enrichment of one processed match: `run_fuel_and_shots` → `quarantine` (identity conf < 0.5 frames unobserved) → `analytics_for`. Writes `fuel/`, `shots/` and `analytics/` per match. `station_pass` = HUBCAM station reads + hub-cam verification. |
| `live/enrich_all.py` | CLI: backfill enrichment for already-processed matches. |
| `live/reprocess.py` | CLI: re-run tracking + identity + enrichment from videos kept in MEDIA_DIR (no YouTube). `--hubcam` adds the station pass. |
| `live/sitev2.py` | Maps analytics output onto the web data contract: `web_track_analytics`, `web_team_analytics`, `team_frame`, `event_q`, and `verified_team_files` (rewrites `t/<team>.json` from verified matches only). |
| `live/coverage.py` | Trust rule shared by every page. `is_v2`, `verified_teams` (team_conf ≥ 0.6), `team_coverage`, `tier` (fact/grey/none), `reconcile` (credited vs TBA: ok/under/over/no_tba gate), `load_matches`, `label`. |
| `live/scouting.py` | Spec sheets, the defense library (placebo-controlled), `scout.json` (scouting truth), and replay snapshots. |
| `live/livestream.py` | Livestream → match clips + records. Gap-free HLS recorder, overlay OCR of clock/teams/score, match assembly, clipping. Run as a child process per event. |
| `live/liveingest.py` | Recorder service (`Service`, supervises `livestream.py` children) plus the worker's view of the recordings (`LiveView`: fast results, trackable clips). |
| `live/claims.py` | Per-match processing claims and processing order shared by the worker, `tools/helper.py` and the api. Stdlib only (the api image has no numpy). |
| `live/dynpose.py` | Dynamic view: a camera pose for every frame of a program feed from the field's AprilTags, plus the stream auto-setup. |
| `live/overlay.py` | CLI: renders the baked accuracy-check MP4 (robots, labels, arcs, minimap, traced-vs-TBA) on the broadcast video. Not called by the worker. |
| `live/verify_ids.py` | CLI: contact sheet of bumper crops per track, for checking robot→team labels by eye. Not called by any code. |
| `live/make_labels.py` | CLI: builds a domain training set (field-ROI crops + YOLO labels) from event videos, using `bglabel` and the current detector (the 2026 flow); `grow()` is reused by `cstrain.py autolabel --bglabel`. |
| `live/bglabel.py` | Model-free robot proposals for a locked-off camera (background subtraction + bumper colour + expected box size). Used only by `make_labels.py`. |
| `live/analytics_demo.py` | Dev script: runs `analytics.py` over `.work/samples` and writes `.work/robot-analytics/report.json`. Only works on a machine with `.work/`. |
| `live/requirements-ytdlp.txt` | yt-dlp and its deps pinned with sha256 hashes (pip-compile output). Bumped by hand or by the updater's "also update yt-dlp". |

### `live/field/`

| file | purpose |
|---|---|
| `2026-rebuilt-welded.csv` | AprilTag poses (ID, X, Y, Z in inches, rotations) for the welded 2026 field. This is the `tag_csv` for calibration and pose checks. |
| `2026-rebuilt-welded.json` | WPILib field JSON (dimensions, tag layout). It references `2026-rebuilt.png`, which is not in git. |
| `rebuilt_rules.json` | Machine-readable game facts: hub centres and opening geometry, bumps, trenches, towers, outposts, depots, zones, and period timing (from Game Manual TU22). Read by shots, analytics, scouting and calibrate (named points). |
| `rebuilt_rules.md` | Human-readable copy of the game facts, with sources and verification status. |

### `live/models/ocr/`

| file | purpose |
|---|---|
| `README` | Provenance and licence (Apache-2.0) of the two PP-OCRv3 ONNX models (from rapidocr_onnxruntime 1.2.3). |
| `ch_PP-OCRv3_det_infer.onnx`, `ch_PP-OCRv3_rec_infer.onnx` | (binary) Text detector (2.4 MB) and recogniser (10.7 MB). Excepted from the `*.onnx` ignore. |

The robot detector (`robot.onnx`) is **not** in git. It lives at `DATA/models/robot.onnx` on the server and is
downloaded by `install.sh` from `CS_MODEL_URL`.

### `live/train/`

Local detector training for any season (`cstrain.py`, recipe in `README.md`). Runs on the training box; none of it is
used at runtime: the output is `DATA/models/robot.onnx`.

| file | purpose |
|---|---|
| `README.md` | The recipe: project layout, init → import → harvest → autolabel → review → build → train → eval → export → install, a new game, measured speeds, the 2026 history. |
| `cstrain.py` | The CLI: every step above plus `status`; reads `live/camera.py`, `bglabel.py`, `make_labels.grow`, `detector.py` (ONNX eval / teachers the way the worker runs them). |
| `cstrain_core.py` | Pure helpers (numpy only): frame ids, DCT perceptual hash + near-duplicate filter, thumbnail change, split by match, IoU, weighted box fusion, uncertainty, label priority (review > import > auto), YOLO text, mAP / P / R. |
| `setup_env.sh` | Builds `/mnt/nas/autoscout-scratch/trainenv` with uv (Python 3.12, CUDA torch, ultralytics 8.4.138, onnx, onnxslim, onnxruntime-gpu 1.31.0, av, opencv headless), caches on the NAS; idempotent. |
| `review/server.py` | The label review / labelling web UI (stdlib): token auth (`?token=` once → cookie, or `X-Review-Token`), queues Uncertain / Gold set / Random / Mine / All with event / match / split filters; writes only `labels/review/<id>.json` (temp file + rename). `serve(project_dir, host, port, token)`; `cstrain.py review`. |
| `review/index.html`, `review/review.js`, `review/review.css` | Its page: canvas box editor, keyboard shortcuts, CSP `script-src 'self'`. |
| `dl.py` | 2026 one-off: downloaded 6 public FRC robot datasets from Roboflow (`RF=<key>`; free-tier credits, cached in `DATA/training/rf`). |
| `pool.py` | 2026 one-off: pooled those datasets into one single-class YOLO set (robot / bumper aliases → `robot`). |
| `r2.sh` | 2026 one-off: round 2, fine-tuned r1 on the public pool + Sammamish auto-labels (×2) on both GPUs. |

### Function index: big `live/` modules

**`live/worker.py`** (679 lines; being edited, see top note)
- `load_config()`: `(tba_key, events)` from `DATA/config/worker.json` (written by the API), falling back to env.
  `_setenv` exports GPU / TBA settings for downstream modules.
- `sync(tba, ev)`: one poll of one event. Lists TBA matches, merges livestream records (`live_view`), calls
  `process_match` for new or changed matches, `live_reconcile` when TBA posts a livestream-tracked match, then
  `build_site`.
- `process_match(...)`: video selection → download → `process.process` → integrity retry → `enrich.enrich`
  (+ `enrich.station_pass` under HUBCAM). A livestream clip is tried first when trackable.
- `station_reads(...)`: HUBCAM; fetches the inset upload and returns station OCR rows for the tracker.
- `event_ctx`, `match_teams` (TBA station order), `start_hint` (median start of earlier uploads from the same channel).
- `build_site(ev)`: writes `site/api/<ev>/{event.json, m/*.json, t/*.json}`. Runs `identity.assign_event`,
  the per-team aggregates (`AN.team_aggregate`, `AN.cluster_autos`), then the subprocesses
  `live.strategy.dashboard --site`, `live.scouting --snapshots`, `goals.site_build`, and
  `live.strategy.goals --selection-snapshots`.
- `live_view`, `live_summary`, `live_input`, `live_reconcile`: the livestream integration (see `liveingest.py`).
- `_sleep` (wakes early when settings or livestream records change), `main()` (`--recorder` →
  `liveingest.main`; `ONCE=1`; `PAUSE_PROCESSING`).

**`live/process.py`** (673 lines)
- `process(video, cam, onnx, tag_csv, log, start_hint, pose_guess, teams=, event=, extra_reads=)`: production
  entry. It runs `prepare` then `finish`.
- `prepare(...)`: `check_pose` (AprilTags in ~28 frames; `resolve_pose` with the white centre line if the camera
  was nudged), `detect_video` (30 Hz detection + `OcrSampler` hooks), and `pick_start`.
- `detect_video`: robot detection over the field ROI. `swap_check` un-swaps RGB↔BGR glitch frames, and feed gaps
  are marked.
- `clock_start` / `match_start` / `pick_start`: auto start from the broadcast clock, then a trusted hint, then
  robot motion.
- `finish(st, ...)`: `fuse.run_labeled` (with teams) or `fuse.run` (v1), plus `track_stats` and `match_integrity`.
- `layout_params`: clock box / masks / field from `layout.json`. `hsv_desc`: appearance descriptor.
  `centreline_*`: pose helpers.

**`live/fuse.py`** (776 lines)
- `Tk`, `tracklets`, `merge_same_view`, `link_cost`, `solve`, `run`: the v1 port of the upstream wide-only
  path (min-cost flow, K=3 per alliance).
- `LTk`, `build_ltracklets`, `split_by_reads` (cut tracklets where reads change team), `merge_labeled`,
  `assign_costs`, `overlap_cost`, `all_links`, `solve_labeled` (joint 6-team ILP via `scipy.optimize.milp`
  with limbo), `run_labeled` (v2 entry; per-frame conf), `corrections` (cut/relink list with evidence).
- `read_weight`, `attribute_extra`: weigh reads; attach station-camera reads to overhead detections.

**`live/ocr.py`** (563 lines)
- `class BumperOCR`: `read_many(crops, cands, open_cands=)` and `read`. Batched det/rec, CTC scoring of
  candidates upright and rotated 180°, `merge_confusable`.
- `class Read`: `cand, lp, margin`.
- `class OcrSampler`: runs inside `detect_video`. Picks detections (conf ≥ 0.3, not tried for 0.3 s, ≤ 3 per
  frame), crops bumpers (`bumper_rect`), feeds 4 OCR worker processes (`_worker_main`), and returns `rows` /
  `open_rows`.
- `team_numbers`, `_attribute`.

**`live/identity.py`** (641 lines)
- `assign_event(results, matches, overrides)`: what the worker calls. Uses `labeled_match` for v2 tracks,
  otherwise `solve_hmm` (v1 time-series HMM) or the legacy `solve`.
- `group_hmm`, `_emissions`, `_decode`, `climb_flags`, `climb_cost`: the v1 HMM evidence.
- `extract_features`, `read_bumpers`, `app_desc`, `_ocr_sessions`, `_ctc`: the v1 second-pass OCR/appearance.
  **Appears unused**: nothing outside `identity.py` calls `extract_features`.

**`live/fuel.py`** (1183 lines)
- `class FuelTracker(cam)`: `push(idx, t, bgr)`, `push_av(frame)`, `result()`. Internals: `_scene_cut` /
  `_hist_jump` (scene-cut reset, added in 97d184d), `_view_check` (`view_ok`), `_swapped_*`, `_blobs` (k-means
  split), `_track`, `_finish`.
- `track_fuel(video, cam, t_from, t_to, log, frame_iter=)`: CLI and enrichment entry.
- `stitch`, `merge_ballistic`, `split_track`, `finalize_track`: fragment joining.
- `_Models` (ballistic vs rolling fits), `_window_evidence`: airborne score.
- `yellow_from_bgr` / `yellow_from_uv`, `ball_radius_px`, `gravity_px`, `overlay_from_layout`, `load_camera`.

**`live/shots.py`** (1211 lines)
- `reconstruct(fuel, match, cam, rules, tba_match, log)` → Events. Built on `class Reconstructor`: candidates,
  per-shooter batched least-squares (`_Fitter`), outcome (`end_hub`, `in_hopper`, `img_near_hub`), `dedupe`,
  periods, `hub_active_at`, `coverage`.
- `traj`, `traj_vel`: the ballistic model with linear drag (`C_DRAG`). `overlay.py` imports both.
- `hub_schedule(tba_match)`: which hub is active per period. `reconcile(...)`: traced made vs TBA `hubScore`.
- `_Robots` (positions from match JSON), `_Field` (zones), `find_tba_match`.

**`live/analytics.py`** (1209 lines)
- `match_analytics(match, events, rules, tba_match)` → `{tracks: {idx: metrics}, alliance}`.
- `team_aggregate(list)`, `cluster_autos(paths_by_team)` (DTW clustering of auto paths).
- `match_clock`, `in_pause`: replay time → match clock (pause frozen at 20 s).
- `Ctx` (geometry/timing from rules), `Prep` (cleaned kinematics, zones, lanes).
- Section functions: `_movement`, `_auto`, `_defense_all`, `_endgame`, `_reliability`, `_fuel`, `_strategy`.

**`live/intakes.py`** (373 lines)
- `detect_intakes(video, fuel, match, cam, rules, log)`. Uses `_Grid` (floor density) and `_snapshots`,
  `_hulls`, `_floor_events`. `_rolling_events` and `_outpost_events` exist but are off (not validated).

**`live/sources.py`** (916 lines; stdlib only, so the API can import it)
- `load`, `save`, `get`, `primary`, `add`, `update`, `remove`, `merge`: the `sources.json` CRUD.
- `classify_url` (normalises; rejects leading `-`, bad schemes and bad characters), `media_ok`, `clean_sample`,
  `file_allowed` / `allowed_roots`.
- `safe_public_url`, `ip_blocked`, `_url_allow` (`CS_URL_ALLOW`): the SSRF guard.
- `discover` (TBA webcasts + per-match upload families), `webcast_url`, `oembed`, `family` /
  `family_pattern`, `match_key_from_title`.
- `video_config` (→ `videos.candidates` config), `station_pattern`, `livestream_url`, `media_for`, `covers`.
- Job queue in `DATA/jobs/sources/`: `new_job`, `get_job`, `_claim`, `run_job`, `run_pending`, `job_loop`
  (worker thread), `submit`.
- Job kinds: `job_frame`, `job_suggest`, `job_detect`, `job_fit`, `job_overlay`, `job_discover`,
  `job_index_playlist`.

**`live/layout.py`** (712 lines)
- `Layout`, `SourceLayout`, `load`, `find` (`$LAYOUT`, then `<event dir>/layout.json`, then default),
  `save_doc`, `default_doc` / `first2026_regions` (the hard-coded Sammamish layout).
- Accessors: `clock_box`, `overlay_boxes`, `detect_masks`, `field_box`, `station_rects`, `station_clock_box`,
  `sync_region`.
- Media: `public_url`, `local_media` (never an option or ffmpeg protocol), `resolve_media` (yt-dlp `-4 -g`),
  `duration`, `grab_frame(s)`, `cached_download`, `grab_dense`.
- Suggestions: `suggest_overlays`, `suggest_clock`, `suggest_insets`, `suggest_insets_edges`,
  `suggest_field`, `suggest_all`.

**`live/calibrate.py`** (635 lines)
- CLI fit: `init`, `residuals` (tags + straight lines), `report`.
- Pinhole/fisheye-ish model: `fit_points`, `pack_pinhole` / `unpack_pinhole`, `proj_pinhole`, `camera_dict`.
- Wizard back end: `detect_tags_gray`, `detect_tags_frames`, `detect_tags_predicted`, `named_points`,
  `named_lines`, `clicked_lines`, `_init_pose` (focal sweep + SQPnP), `wizard_fit`, `refine_with_lines`,
  `find_line_pixels`, `overlay_lines`, `wizard_camera`.
- `load_tag_poses`, `tag_corners`.

**`live/stationcam.py`** (848 lines)
- `find_inset_video`, `rects_for`, `layout_regions`.
- `sync_offset` (motion-energy xcorr `_coarse` + thumbnail compare `_fine`, piecewise for re-encodes),
  `wide_time` / `inset_time`, `verify_sync` (clock-digit check).
- `collect_pairs`, `_ball_pairs`, `fit_station` (joint multi-match fit), `check_station_pose` (per-match re-aim),
  `load_station`.
- `class StationCams`: `frames`, `frame_at`, `project`, `visible`, `ray`, `ground`, `robots_at`.
- `sheet` (verification image), `_main` CLI (`sync | check | sheet | calibrate`).

**`live/hubcam.py`** (666 lines)
- `verify_shots(events, match, stationcams, ...)`: revises outcomes. `run_match(...)`: sync + verify for one
  match.
- `detect`, `track`: yellow blobs and tracklets per station.
- `joint_fit`, `match_shot`, `station_evidence`, `decide`, `entry_tracklets`, `funnel_hull`, `hub_roi`.

**`live/scouting.py`** (1727 lines)
- `build(DATA, ev)`: writes `spec/index.json`, `spec/<team>.json` and `defense.json`. `build_truth` writes
  `scout.json`; `build_asof` writes `scout/<ts>.json`. `site_build_extra(DATA, ev)` is the worker hook and
  never raises.
- Model classes: `Geo` (lanes, sides, zones), `Match`, `Trk` (verified-only team attribution).
  `load_matches` skips v1 tracks.
- Spec sheets: `spec_sheets`, `crossings`, `volleys`, `shooting_paths`, `_lane_summary`, `_shooting_spots`.
- Defense library: `detect_episodes`, `_features`, `Baseline` (placebo windows), `defense_library`,
  `placebo_check`, `_verdicts`, `_boot_samples` / `_boot_ratio` (bootstrap), `team_retention`
  (per-team placebo-controlled retention with BH q and FCR-adjusted interval), `bh_q`, `_kish`,
  `summaries`, `_advice`, `clip_data` (top-down clips).
- TBA helpers: `_tba_robot_rows`, `_opr`, `wilson`.

**`live/livestream.py`** (1341 lines)
- `class Recorder`: `resolve` (yt-dlp, IPv4), `run` (fetches HLS chunks by sequence number, gap fill),
  `fetch_dash`, `retention`. `ChunkStore` (`chunks.jsonl` index; `cut`), `parse_playlist`, `synth_chunk`,
  `ts_first_pts`.
- `class Scanner`: overlay OCR per frame (`read_scoreboard`, `read_results`). `class OverlayOCR`: PP-OCRv3 rec
  with per-field alphabets.
- `parse_clock`, `parse_title`, `parse_team`, `parse_int`, `vote`, `colour_of`.
- `t_votes`, `find_matches`, `refine_T`: auto-start from clock ticks. `measure_auto_start`: on a clip.
- `class Assembler`: scanner observations → match records + clips + JSON (`attach_results`).
- `analyse_live`, `analyse_replay`, `FileClipper`, `keyframe_before`, `TBACheck`, `apply_layout`,
  `event_setup`.
- `_public` / `_NoPrivateRedirect`: the SSRF guard. `main()`: CLI.

**`live/liveingest.py`** (733 lines)
- `class Service`: the recorder service. One `livestream.py` child per followed event with a livestream
  source, run during the event dates. `step`, `start` / `stop`, `retention` (budget / min free / ages),
  `write_status` → `DATA/events/<ev>/livestream.json`.
- `class LiveView`: the worker's per-poll view. `track`, `extra_matches` (playoffs not on TBA yet),
  `for_processing` (trackable clip input), `site_row` / `decorate_row` (fast results:
  `score_source: "livestream"`).
- `load_records`, `latest`, `record_key`, `problems`, `clip_of` / `clip_ok`, `team_keys`, `lineup_common`,
  `synth_match`, `posted`, `stream_camera`, `trackable`, `pick_source`, `resolve`, `dir_status`,
  `status_summary`, `worker_config`, `media_url`.

**`live/overlay.py`** (583 lines)
- `render(video, site_match_json, shots_json, cam, out_mp4, ...)`: parallel ffmpeg segments.
- `class _R` (compose / panel / minimap), `_Shots`.

**`live/sitev2.py`** (306 lines; being edited, see top note)
- `web_track_analytics`, `web_team_analytics`, `team_frame`, `event_q`, `verified_team_files`.

---

## `live/strategy/`

Pure numpy/scipy. It reads the data root and writes site JSON. Run as `python -m live.strategy.<mod>`.

| file | purpose |
|---|---|
| `README.md` | The strategy engine reference: CLIs, model in one paragraph, preview / rankings / schedule / scout / selection / defense schemas, and backtest results by round. |
| `DASHBOARD.md` | `dashboard.json` contract plus change notes (match-day plans, queue card, goal mode, backtest_asof, round 3). |
| `__init__.py` | Package marker (docstring only). |
| `data.py` | Loads TBA matches + site match JSON. `load_event`, `load_tba`, `load_video` (alliance blocks + robot rows, credited-FUEL split), `shot_zone`, `match_clock`, `load_routines`. Constants `COMPONENTS`, `PERIODS`, `SHIFTS`, `CRED_CONF=0.8`. |
| `model.py` | Hierarchical GLS robot model per FUEL component with EPA prior, video as censored evidence (`VIDEO`, `_credited_block`), climbs, and shrunk descriptive features. |
| `sim.py` | Monte Carlo match simulator (`simulate`, `simulate_schedule`): auto → hub schedule → teleop → towers → fouls; Gamma noise + Gaussian copula. |
| `baselines.py` | TBA-only baselines: ridge OPR (`opr_predict`), EPA-style rating (`EPA`), `Prequential` normal win probability, `S_PRIOR`. |
| `backtest.py` | Honest backtest: every qual predicted from earlier matches only. Brier, log loss, accuracy, MAE, subsets and bootstrap CIs. |
| `preview.py` | Match preview JSON (`build_preview`, `key_factors`, `_robot_entry`). |
| `epa.py` | EPA: `pre_event_prior` (from the teams' earlier events, TBA cached, or Statbotics), `local_epa` / `ComponentEPA` (running as-of EPA), `fetch_statbotics`, `sb_asof`, `cached_get` (IPv4, JSON cache). |
| `rank.py` | 2026 RP rules + TBA tiebreakers; Monte Carlo rank projection. |
| `matchday.py` | Dashboard helpers: as-of coverage, cached prequential backtest rows + footer, defense-library effect, queue card, defender pick. |
| `dashboard.py` | Dashboard JSON (as-of view, blended ratings, projection, plans, levers, schedule, snapshots). |
| `district.py` | FRC district points (qual formula, alliance, playoff, awards, DCMP ×3) + TBA district loader; `CAPACITY`, `WORLDS_SLOTS`, `PARTIAL`. |
| `goals.py` | Season Monte Carlo (`Season`): DCMP / Worlds / rank goal odds, `rankings.json`, `schedule.json` (per-period breakdown), value of matches, replay. |
| `selection.py` | Alliance-selection view (`selection.json`): roles / seeds from the Season sim, need grid, pitch cards, per-captain re-simulation. |

### Function index: big strategy modules

**`dashboard.py`** (734 lines)
- `build(...)` → `(dashboard, previews)`. `site_build(DATA, ev)` is the worker entry point.
- `Context`: loaded once, reused by snapshots; has `bt_rows`. `asof_view`: replay filtering (video lag 10 min).
- `blend_rating`, `info_weights` (prior / tba / video split), `display_epa`.
- `next_match_plan`: plan for any remaining match. `levers`, `_points_gap`, `_our_factors`.
- `snapshot_times` (every 30 min per event day), `main` CLI (`--site --snapshots --only-missing --previews
  --teams --asof`).

**`goals.py`** (1117 lines)
- `class Season`: `_plan_events`, `_fixed_alliances`, `_run`, `dcmp_outcome`, `worlds`, `goal`, `team_events`.
- `sim_event`: one district event × n seasons: quals, draft, double-elim playoffs, awards.
- `district_strengths`, `statbotics_district`, `fit_bonus` (bonus RP logistic), `award_model`.
- `goal_block`, `no_award_mask`, `award_prequal_mask`, `_p_headline`, `value_of_matches`, `_cutoff_now`.
- `period_breakdown`, `narrative`: "where you'll be beaten".
- `build_event_json`, `site_build`, `selection_snapshots`, `replay`, `main`.

**`matchday.py`** (552 lines)
- `coverage_asof`, `load_coverage_matches`.
- `backtest_rows` (cached in `DATA/cache/strategy/backtest_<ev>.json`), `backtest_asof`, `epa_sd`.
- `load_library`, `library_effect`, `retention_for`: defense numbers come only from `defense.json`.
- `event_extras`, `queue_card` (six lines + goal mode), `pick_defender`, `their_view_zone`, `field_xy_ours`,
  `zone_centroid`, `disp` (5 % display steps).

**`model.py`** (677 lines)
- `fit_event_model(E, before=, epa_prior=)` → `EventModel`. Uses `fit_hyper` (marginal likelihood of TBA
  rows), `fit_posterior`, `_prior_terms`, `_credited_block`, `_resid_corr`.
- `team_theta`.
- Descriptive features: `_fit_climbs`, `_fit_descriptive`, `_hub_state`, `_zones`, `_by_period`, `_role`,
  `_fit_routines`, `_shrink`, `_wstats`.

**`selection.py`** (356 lines)
- `selection_block(S, ev, team, comps, ...)`, `roles`, `_need_text`, `_pitches`, `captain_resim`,
  `_attach_fit`.

---

## `server/` (API)

FastAPI + SQLite. Routers are mounted under `/app/v1`. All files are Cyber-Scout. The routers were **being
edited** when this was written (see top note).

| file | purpose |
|---|---|
| `server/README.md` | API reference: auth model, roles matrix, endpoint table, worker files, how to add endpoints, how to test. |
| `server/__init__.py` | Package docstring. |
| `server/main.py` | `create_app()`: mounts the routers; one HTTP middleware adds CSRF checks on mutating requests, security headers (`no-store`, `nosniff`, `X-Frame-Options: DENY`, CSP `default-src 'none'`) and a generic `http.<method>` audit entry for successful un-audited writes; OpenAPI schema admin-only, no Swagger. `lifespan` → `startup()` (DB init, `seed_from_env` imports `TBA_KEY` / `EVENTS` once). Runs as one uvicorn process because the rate limiters are in memory. |
| `server/config.py` | `cfg`: env-only infra settings (DATA, session lifetimes, password minimums, `COOKIE_SECURE`, `TBA_BASE`, `SETUP_CODE_REQUIRED`). |
| `server/db.py` | SQLite (`DATA/app.db`, WAL, one connection per request). Core schema: `users`, `sessions`, `settings`, `events`, `picklists`, `notes`, `override_log`, `picklist_versions`, `override_requests`, `audit_log` (UPDATE/DELETE refused by triggers). Also `add_schema`, `init`, `get_db`, `get_setting` / `set_setting`, `user_count`. |
| `server/deps.py` | Shared dependencies: session lookup, `load_session`, `require_role(role)` (also enforces CSRF and lockdown), `check_csrf`, `csrf_anonymous`, `valid_event`, `public_name`, `rate_limit`, `client_ip` / `_trusted_addr` / `via_public` (team network vs internet), `passwordless_policy`, `lockdown_state`, cookie helpers (`cookie_secure`, `set_session_cookies`). |
| `server/security.py` | argon2id hashing, session tokens (SHA-256 stored), `login_limiter`, `write_limiter` (`WriteLimiter`), `atomic_write_json`, `token_hash`. |
| `server/audit.py` | `record(db, request, user, action, target, event, before, after)` and the generic `http.<method>` entries. |
| `server/textrules.py` | Validation for user text: `plain_text` (notes: no tags/entities/`javascript:`/control/bidi/zero-width), `label`, display-name rules (no staff words, no look-alikes). |
| `server/tba.py` | Server-side TBA client; the key never leaves the server; short in-memory cache. |
| `server/workerconf.py` | Writes `DATA/config/worker.json` (0600) from the DB on every settings or events change. |
| `server/manage.py` | Break-glass CLI: `python -m server.manage users \| reset-password <u> \| make-admin <u>`. |
| `server/entrypoint.sh` | Drops to PUID/PGID. chowns only the API's own files, with `chown -h`; refuses a symlinked `config/` (audit finding 5). Then runs uvicorn. |
| `server/Dockerfile` | API image: python:3.12-slim + `server/requirements.txt`. Copies `server/` plus `live/sources.py` and `live/layout.py` (stdlib-importable; heavy jobs run in the worker). Removes tests; healthcheck on `/app/v1/health`. |
| `server/requirements.txt` | Pinned runtime deps (fastapi, starlette, pydantic, uvicorn, argon2-cffi). |
| `server/requirements-dev.txt` | + httpx, pytest. |
| `server/openapi.json` | Generated OpenAPI 3.1 contract (50 paths). **Stale:** it lacks the `/admin/update*` endpoints added in fa29065. Regenerate it with the command in `server/README.md`. |

### `server/routers/`

| file | endpoints (all under `/app/v1`) |
|---|---|
| `__init__.py` | Empty package marker. |
| `auth.py` | `GET /session`, `POST /setup`, `POST /auth/login`, `POST /auth/logout`, `POST /auth/password`, `PATCH /auth/profile`, `GET /auth/check` (nginx auth_request). Helpers: `passwordless_allowed`, `check_password_policy`, `password_strength`, `check_display_name`, `start_session`, `ensure_setup_code`, `configured`. |
| `users.py` | `GET`/`POST /users`, `PATCH`/`DELETE /users/{uid}`. Guards: last active admin, self-delete, admin-grade password before promotion. |
| `settings.py` | `GET`/`PATCH /settings`, `PUT`/`DELETE /settings/tba-key` (validated against TBA), `GET /tba/team/{n}`, `GET /tba/events?year=`. Helpers: `tba_call`, `settings_out`, `apply_settings_state` (revert support). |
| `events.py` | `GET`/`POST /events`, `DELETE /events/{ev}` (follow / unfollow; rewrites `worker.json`). |
| `scouting.py` | Picklists: `GET /events/{ev}/picklists[/{name}]`, `PUT`/`DELETE` (strategist+; `base_version` 409), plus versions, version restore. `PicklistData` is a strict schema (team keys ≤ 200, known weight keys, floats 0..5). Notes: `GET`/`POST /events/{ev}/notes`, `PATCH`/`DELETE /notes/{id}`, `POST /notes/{id}/restore`. Overrides: `GET /events/{ev}/overrides`, `PUT`/`DELETE /events/{ev}/overrides/{match}` (scout = queued request in `review` mode), `POST /events/{ev}/override-requests/{id}/approve\|reject`. Writes `DATA/events/<ev>/overrides.json`. |
| `admin.py` | `GET`/`PATCH /admin/security` (lockdown, passwordless policy, scout review mode), `POST /admin/sessions/revoke`, `GET /audit`, `POST /audit/{id}/revert`, `POST /audit/revert-user`. `_revert` dispatches to each family's `apply_*_state`. |
| `sources.py` | Sources: `GET /events/{ev}/sources`, `POST .../sources/discover`, `POST .../sources`, `PATCH`/`DELETE .../sources/{sid}`, `POST .../sources/{sid}/frame`, `GET .../frame.jpg`. Layout: `GET /events/{ev}/layout`, `PUT`/`DELETE .../layout/{sid}`, `POST .../layout/{sid}/suggest`. Calibration: `GET /events/{ev}/calibration`; for each `{target}`: `POST detect`, `POST fit`, `GET frame.jpg`, `GET wizard`, `POST overlay`, `POST accept`. Also `GET /events/{ev}/livestream` (recorder status) and `GET /events/{ev}/jobs/{jid}`. Heavy steps are jobs in `DATA/jobs/sources/`, run by the worker's `sources.job_loop` (the API image has no vision stack). |
| `update.py` | `GET /admin/update` (status from `DATA/config/update-status.json`; whitelist / type checks in `parse_status`), `POST /admin/update/check\|apply\|rollback` (write `update-request.json`; 6/min; allowed during lockdown). |
| `processing.py` | Distributed processing (admin): claims, clip download, result upload, pipeline code tarball, speed test (`/processing/*`). |

### `server/tests/`

| file | purpose |
|---|---|
| `__init__.py` | Empty. |
| `conftest.py` | Fixtures: temp DATA, `TestClient(create_app())`, fake TBA, limiter resets, login helpers. |
| `test_auth.py` | Setup code, login/logout, sessions (idle / max), cookies, CSRF, password policy, login rate limits. |
| `test_data.py` | Settings, events, `worker.json`, picklists, the overrides file. |
| `test_hardening.py` | Audit log + revert, picklist history, soft-deleted notes, identity-fix review, write limits, text / display-name validation, passwordless only on the team network, lockdown, sign everyone out, the compose api env allowlist. |
| `test_notes_ui.py` | The API contract `web/notes.js` relies on: notes on team + match pages, ownership, hide / restore, lockdown 423, the fix merge-then-PUT flow. |
| `test_livestream.py` | `GET /events/{ev}/livestream` reads the recorder status file. |
| `test_video_mode_api.py` | `GET/PUT /events/{ev}/video-mode`: livestream-only mode, readiness checklist, audit, roles. |
| `test_roles.py` | Role matrix: who may call what. |
| `test_sources_api.py` | Pasted source URLs are normalised and validated (audit findings 2 and 4). |
| `test_update.py` | Update endpoints: permissions, request-file schema (round trip through `cs-updater.validate_request`), status parsing, lockdown, CSRF, rate limit, the updater's pure helpers. |
| `test_processing.py` | Processing API: claims shared with the worker, order, clip Range download, uploads, complete / release, auth. |
| `test_auto_setup_api.py` | Stream auto-setup endpoints (`auto-setup`, `overlay.jpg`, `accept`, `dynamic`) and what `/sources` reports. |

---

## `web/` (the SPA)

Static, no build step, served by nginx. Script order is fixed in `index.html`: `theme.js` in `<head>`, then
`quant.js`, `event.js`, `player.js`, `dash.js`, `app.js`, `setup.js`, `admin.js`, `audit.js`, `notes.js`, `sources.js`,
`rankings.js`, `schedule.js`, `teams2.js`, `defense.js`, `settings.js`. The later files register through
`CS.route`, `CS.globalRoute` and `CS.nav`. `app.js` and the fetch code were **being edited** (see top note).

| file | purpose |
|---|---|
| `web/index.html` | Shell: CSP-friendly (no inline script), loads `theme.js`, `fonts.css`, `style.css`, `themes.css`, then the scripts above. The web Dockerfile rewrites every JS/CSS URL with `?v=<md5>`. |
| `web/Dockerfile` | nginx:1.29-alpine; copies `nginx.conf`, `web/*.html *.js *.css field.jpg` and `fonts/`; adds the cache-bust `?v=` hash to `index.html`. |
| `web/nginx.conf` | Real-IP trust (docker gateway + loopback only), security headers + CSP, denies dotfiles / `*.db` / `*.env` / keys, `auth_request` (`/_auth` → `/app/v1/auth/check`) gating `/api/` and `/media/`, login rate limiting, proxy `/app/v1/` → `api:8000`, static `/`. |
| `web/app.js` | Router + plugin registry + shared helpers + the data contract comment at the top (see the function list below). |
| `web/setup.js` | Platform plugin: sign-in, session, role-aware nav, first-run setup wizard, my account. Exposes `CS.app`, `CS.user`, `CS.can`, `CS.platform`, `CS.form`, `CS.genPassword`, `CS.eventPicker`, `CS.refreshSession`, `CS.navAllowed`. Global routes `#/login`, `#/account`, `#/setup[/step]`. |
| `web/admin.js` | `#/admin[/users\|settings\|events\|updates]` (admin): `users`, `settings` (team, goal, TBA key, worker options incl. GPU / HUBCAM), `events` (follow via `CS.eventPicker`), `updates` (changelog, progress, rollback). |
| `web/audit.js` | `#/activity` (admin: audit log, filters, revert, lockdown / passwordless / review switches, sign everyone out) and `#/<ev>/fixes` (strategist+: identity-fix approval queue per competition). |
| `web/notes.js` | `CS.notes` (Notes card on the team and match pages) and `CS.idFix` ("Wrong team?" identity-fix form on the match page). |
| `web/settings.js` | `#/settings`: per-browser theme, appearance (light / dark / auto) and layout (sidebar / top bar / command), stored by `theme.js` (`window.CSStyle`). |
| `web/theme.js` | **Generated** by `lab/tools/site-themes.mjs`. Runs in `<head>` before paint: reads `cs:style` / `cs:mode` / `cs:layout` from localStorage and sets `<html data-style data-mode data-layout>` and theme-colour metas. Default `{style: "jet", mode: "auto", layout: "sidebar"}`. |
| `web/themes.css` | **Generated**: every theme (18) scoped to `html[data-style]` + mode, plus the layout CSS. |
| `web/style.css` | Base tokens (type, spacing, shape) and all component styles. Colours come from `themes.css`. |
| `web/fonts.css` | Archivo variable font inlined as base64 `@font-face` (works offline on venue Wi-Fi). |
| `web/dash.js` | `#/<ev>/dash[/<ts\|live>[/<q51>]]` dashboard: match rail, queue card, our event, schedule, levers, ratings, honest footer. Entry `dashPage(ev, ts)`, called by `app.js`. |
| `web/player.js` | Match viewer `#/<ev>/m/<match>` and `#/<ev>/a/<match>[?t=]`: video + canvas layers (robots, labels, trails, arcs, fuel, field, HUD), View menu (Video / Field / Overlay), station-camera sync, identity corrections timeline, livestream clip playback (`liveMatchPage`). |
| `web/quant.js` | Shared quant helpers and charts (`tile`, `spark`, `bars`, `periodBars`, `drawShotChart`, `drawIntakeMap`, `drawPath`, `densityLayer`, `drawHubMark`, `chipRow`, `section`) and the team page `teamPage` (`#/<ev>/t/<team>`). |
| `web/event.js` | Event panes: team table (`teamsTable`, columns from `scout.json` + `event.json`), Compare (`comparePane`), Picklist (`picklistPane`: shared server picklist `main`, replay-aware, stars / DNP / weights). |
| `web/rankings.js` | `#/<ev>/rankings` (rankings, RP, projected range, district points, goal panel) and `#/<ev>/selection[/<ts>]` (Selection: seeds, captains, need grid, pitch cards). |
| `web/schedule.js` | `#/<ev>/schedule`: all matches, predicted win %, per-period "where you'll be beaten" for our matches. |
| `web/teams2.js` | `#/<ev>/specs[/<team>]` spec sheets: `listPage`, `specPage`, `laneDiagram`, `shotMap`, `pathsAnim`. |
| `web/defense.js` | `#/<ev>/defense[/<target>]` defense library: episodes, effect glyphs, top-down clip player (`clipPlayer`, `getClip`). |
| `web/sources.js` | `#/<ev>/sources[/sources\|layout\|calib][/<id>]` (admin): sources list, layout box editor (`layoutTab`, `FrameView`), calibration wizard (`calibTab`). Polls jobs. |
| `web/field.jpg` | (binary) Top-down field image for canvases. |
| `web/fonts/*.woff2` | (binary, 16 files) Theme fonts (see Binary assets). |

**`web/app.js`** (498 lines, long lines) key symbols:
- Plugin registry: `CS.route(name, fn)` → `CS.routes`; `CS.globalRoute(name, fn)` → `CS.globalRoutes`;
  `CS.nav({label, key, href, order, group, icon, tab, replay, global, roles|minRole})`.
- `route()`: dispatch on `location.hash`, in this order: global routes; home; plugin routes `#/<ev>/<name>/...`;
  `dash`; the event root (dashboard if `dashboard.json` exists, else the event page); `m` → `matchPage`;
  `a` → `analyzePage`; `t` → `teamPage`; everything else → `eventPage`.
- Data access: `api(path)` fetches `api/<path>`.
- Escaping and formatting: `esc`, `num`, `pct`, `fx`, `ago`.
- Identity, coverage and data-quality badges: `confBar`, `idPill`, `covPill`, `flagChips`, `dataBanner`,
  `svHtml`, `scoutData`.
- Help and table fitting: `CS.help` / `CS.closeHelp` (the "?" popovers), `fitTable` / `fitDetail` / `fitAll` /
  `CS.fit` (no horizontal scroll).
- Livestream: `CS.isStream`, `CS.liveTag`, `CS.mediaUrl`, `CS.liveTracking`.
- Navigation chrome: `CS.safeHref`, `CS.icon` (`ICON` SVG set), `CS.markActive`, `renderTopNav` (`NAV_GROUPS`),
  `renderTimebar` (replay as-of slider), `CS.asofGet` / `CS.asofSet`, `crumbs`.
- Pages built here: `home`, `eventPage` (`EV_TABS`), `matchesTable`, `statusPill`.
- Canvas: `fieldCanvas`.
- Shared constants and state: `FIELD`, `LEN` / `WID`, `COLORS`, `PALETTE`, `cleanup`.
- Built-in nav entries: Dashboard, Teams, Compare, Picklist, Matches.

---

## `lab/` (design lab)

A standalone copy of the site for trying themes and layouts on the real Dashboard at the Sun 09:00 replay.
Serve it with `python3 -m http.server 8794` from `lab/`. The `lab/*.js` page files are **snapshots of `web/*.js`
from when the lab was made** and are not kept in sync; for example, `lab/admin.js` has no Updates tab and
`lab/dash.js` is older. The lab's real outputs are `web/themes.css`, `web/theme.js` and `web/fonts/`, generated by
`lab/tools/site-themes.mjs`.

| file | purpose |
|---|---|
| `lab/index.html` | Lab shell: `themes.js` + `ext/a-d.js` + `lab-boot.js`, then the copied site scripts, then `lab.js`. |
| `lab/gallery.html` | Theme gallery with contrast tables for every token pair. |
| `lab/phone.html` | Phone-width (390 px) iframe frame around the lab. |
| `lab/themes.js` | **Source of truth for themes**: `LAB.THEMES` (base + light/dark tokens + rules), `LAB.LAYOUTS`, `LAB.FONTS`. |
| `lab/ext/a.js`, `a.css` | Extension slot a: extra themes + the **Notebook** layout. |
| `lab/ext/b.js`, `b.css` | Slot b: themes + the **Command** layout (laptop command centre). |
| `lab/ext/c.js`, `c.css` | Slot c: themes + the **Mobile-first** layout. |
| `lab/ext/d.js`, `d.css` | Slot d: the user's picks tuned for the site: "Sand & Earth" and "Jet". (`d.js` has uncommitted local edits.) |
| `lab/lab-boot.js` | In `<head>`: picks theme/layout/mode from the query string or localStorage, writes every theme's CSS, and pins the app to the 09:00 replay fixtures. |
| `lab/lab.js` | Floating "Design lab" button + panel, inert navigation, layout helpers. |
| `lab/lab.css` | Layout CSS (`html[data-layout]`) and the lab's own UI. |
| `lab/style.css`, `lab/fonts.css`, `lab/field.jpg` | Copies of the site base CSS (older), the Archivo font CSS, and the field image. |
| `lab/app.js`, `admin.js`, `audit.js`, `dash.js`, `defense.js`, `event.js`, `player.js`, `quant.js`, `rankings.js`, `schedule.js`, `setup.js`, `sources.js`, `teams2.js` | Snapshot copies of the site scripts (see above). |
| `lab/tools/site-themes.mjs` | **Generator**: lab registry → `web/themes.css`, `web/theme.js` (incl. default `DEF`), `web/fonts/*.woff2`. |
| `lab/tools/port-theme.mjs` | Prints one lab theme as a drop-in for `web/` (tokens + rules + `@font-face`, optionally inlined). |
| `lab/tools/shoot.sh` | Headless Chrome screenshots of every theme × mode × layout (→ `.work/lab/shots`, committed ones in `lab/shots/`). |
| `lab/tools/probe.sh` | Checks every theme × layout at 390 px for horizontal overflow. |
| `lab/tools/selftest.js` | In-page self-checks (`index.html?selftest=1`, read back via `--dump-dom`). |
| `lab/api/events.json`, `lab/api/2026wasam/dash/index.json`, `lab/api/2026wasam/dash/1774195200.json` | (fixtures) Event list + dashboard snapshot at 1774195200 (Sun 09:00 replay) that the lab renders. |
| `lab/shots/*.webp` | (binary, 149 tracked: 111 `desk-*`, 35 `phone-*`, 3 `panel*`) Screenshots per theme / mode / layout. |
| `lab/fonts/*.woff2` | (binary, 17) Theme fonts (same set as `web/fonts` + `c-saira`). |

---

## `client/` (Cyber-Scout Client, processing parts)

| file | purpose |
|---|---|
| `client/csclient/processor.py` | Processor mode ("Help process matches"): claim, download clip, run the runner, upload results. |
| `client/csclient/procrunner.py` | Tracks one claimed match with the server's own code; shipped by the server as `runner.py` in its code tarball. |
| `client/csclient/procui.py` | The client's `/client/processing` page. |
| `client/csclient/gpu.py` | GPU check: can this computer track matches (CUDA / DirectML / CoreML)? |
| `client/csclient/procsetup.py` | One-time "Set up processing": a uv-made Python 3.12 environment for the pipeline. |
| `client/csclient/procnet.py` | Link speed to the server (speed test and real transfers). |
| `client/csclient/clipper.py` | Clip only mode: records a stream and cuts matches for a computer that can't track. |
| `client/build/runtime_check.py` | CI / by-hand check that the processing runtime installs and works. |
| `client/tests/fakeprocessing.py` | Fake of the processing API for the client tests. |
| `client/tests/test_processor.py` | Processor mode against the fake server and fake processing API. |

---

## `tests/` (pipeline tests)

Run with `python -m pytest tests` from the repo root. They need no GPU, video or network.

| file | purpose |
|---|---|
| `tests/test_fuel_cuts.py` | `fuel.py` scene-cut handling (frozen lead-in + cut must not blind auto). |
| `tests/test_liveingest.py` | Livestream → worker: record selection, trackability, fast results, live-first processing (file fixtures). |
| `tests/test_livestream_only.py` | Livestream-only video mode: no upload lookups, synthesized quals, clip failures → failed, TBA-time cut, recorded frames. |
| `tests/test_matchday.py` | Pure helpers of `live/strategy/matchday.py`; defense "measured" only when the library says `significant` (queue card and preview factors). |
| `tests/test_goals_scale.py` | `goals.py` passes the dashboard blend to the season sim unscaled (per robot). |
| `tests/test_source_urls.py` | Audit findings 2 and 4: URL normalisation, `--` before URLs, SSRF guard in `live/sources.py`. |
| `tests/test_auto_setup.py` | Stream auto-setup in `live/sources.py` and the per-source `program_feed` / `dynamic` flags. |
| `tests/test_dynpose.py` | `live/dynpose.py` (a camera pose per frame from AprilTags) and its `process.py` hooks; synthetic geometry only. |
| `tests/test_overlay_layouts.py` | Scoreboard layouts in `live/livestream.py` (top-centre scoreboard, bottom score bar). Uses `tests/fixtures/scorebar_*.jpg`. |
| `tests/test_process_only.py` | `live/worker.py --process-only`: claim + track only, per-match state files, delegation, dynamic per source. |
| `tests/test_cstrain.py` | Local training (`live/train/cstrain.py`, `cstrain_core.py`): ids, hash + near-duplicate filter, split by match, WBF, uncertainty, label priority (review > import > auto, doubtful auto frames train-only), YOLO text, mAP / P / R, `build` end to end on a synthetic project (no torch / video). |
| `tests/test_train_review.py` | The label review UI (`live/train/review/server.py`): token, path safety, atomic review writes, queue order, validation. |
| `tests/test_undefined_names.py` | pyflakes check for undefined names in the pipeline modules (skipped without pyflakes). |
| `tests/fixtures/scorebar_bottom_f1m1.jpg`, `scorebar_bottom_qm33.jpg`, `scorebar_top_qm12.jpg` | Real score-bar frames for `test_overlay_layouts.py`. |

---

## `tools/` [U]

Browser tools from upstream autoscout for hand calibration and labelling. They are served by `src/serve.py`
(which POSTs `/solve` to `src/solve_api.py`). The live pipeline does not use them; the Cyber-Scout calibration
wizard in `web/sources.js` replaced them for new events.

| file | purpose |
|---|---|
| `tools/calib.html` | Three-view calibration: mark carpet points / tags in the wide and station panels; live solve. |
| `tools/lines.html` | Straight-line marking for lens distortion. |
| `tools/pair.html` | Cross-view point pairing between panels. |
| `tools/label.html` | Robot box labelling for the validation set. |
| `tools/trace.html` | Hand tracing of robot paths (ground truth for `eval_trace.py`). |
| `tools/helper.py` | Processing helper: tracks livestream clips on this machine for a Mac server (`deploy/macos`), alongside the server's worker. |
| `tools/priority.py` | Processing order for the worker (`events/<ev>/priority.json`): matches showing our next quals' teams first. |

---

## `src/` [U]

The upstream single-match pipeline (`INSTRUCTIONS.md`). It assumes the Niagara 3-panel layout, 30 fps and files
under `out/`. Apart from the three modules listed in the next section, nothing in `src/` runs in production. It
is still copied into the worker image (`COPY src/ src/`).

| file | purpose |
|---|---|
| `src/geom.py` | Field↔image geometry for the locked-off wide camera (band crop, corner correction). **Replaced at runtime** by `camera.install_geom_shim`. |
| `src/track.py` | **Used by live.** Field-space multi-target tracker: constant-velocity Kalman tracklets, speed gate (`build_tracklets`, `DT`, `VMAX`, `MISS_MAX`, `MIN_LEN`). |
| `src/fit6.py` | **Used by live.** Exactly three trajectories per alliance by EM; `rts` smoother. |
| `src/assign.py` | **Used by live.** Tracklet-level identity assignment (`bhat_sets`, `NEX`). |
| `src/fuse_track.py` | Multi-camera tracker (pure tracklets from every view → fused → exact identity). Its wide-only path was ported into `live/fuse.py`. |
| `src/mcam.py` | Per-camera field geometry for all broadcast views (wide + three stations). |
| `src/uncertainty.py` | Field-space position uncertainty per camera and fusion gain. |
| `src/project_dets.py` | Raw detections → field positions + appearance descriptors for the tracker. |
| `src/appear.py` | Per-detection appearance descriptors and a separability test. |
| `src/detect_match.py` | Detector + ByteTrack baseline over the whole match (`CROP` layout constants). |
| `src/eval_trace.py` | Tracks vs hand-traced paths, including ID switches. |
| `src/evaluate.py` | Post-training evaluation: cross-event transfer + bumper-hue alliance accuracy. |
| `src/render.py` | Renders the tracked match (broadcast panel + top-down field with tails). |
| `src/render_cmp.py` | Side-by-side tracker comparison video. |
| `src/viz.py` | Side-by-side broadcast + top-down visualisation helpers. |
| `src/serve.py` | Tiny HTTP server for `tools/*.html`; POST `/solve` → `solve_api`. |
| `src/solve_api.py` | One entry point that solves any view from the existing calibration data. |
| `src/solve_station.py` | Full 3D calibration of a panel from carpet points + AprilTag corners. |
| `src/calib_station_full.py` | Station calibration: lines → lens, vanishing points → focal, points → pose. |
| `src/calib3d_robust.py` | Constrained camera model + bootstrap uncertainty. |
| `src/lines_solve.py` | Distortion and focal from marked straight lines. |
| `src/carpet.py` | Carpet segmentation on the median plate (near/far boundaries). |
| `src/plates.py` | Background plates + reference frame per panel. |
| `src/tags_multi.py` | AprilTags in all three panels; corners + verification image. |
| `src/tags_views.py` | Detected tags per panel in the `calib.html` guide format. |
| `src/write_cams.py` | Assembles `out/cams.json` (every camera in one frame). |
| `src/pair_frames.py` | Time-aligned frames from the three panels for pairing. |
| `src/trace_frames.py` | Band-cropped frames at a fixed step for the tracing UI. |
| `src/extract_frames.py` | Sparse frames for labelling. |
| `src/fetch_datasets.py` | Downloads external training datasets (Roboflow; never prints the key). |
| `src/prep_yolo.py` | Builds a YOLO dataset, tiling the field band and splitting by time block. |
| `src/prep_val.py` | The 83 labelled frames → validation set only. |
| `src/prep_val_full.py` | Validation on full field-band frames. |
| `src/build_final.py` | Final training pool: normalise classes, drop non-robots, perceptual dedupe. |
| `src/build_multival.py` | Holds out whole source clips as a multi-event validation set. |
| `src/train.py` | YOLO11s training on Apple MPS (`out/yolo/data.yaml`, 140 epochs). |
| `src/train_kaggle.py` | Kaggle training: pull datasets, rebuild the pool, train from COCO. |
| `src/cont640.py` | Warm-start from the 960 checkpoint and continue at 640. |
| `src/ckpt_sweep.py` | Per-checkpoint validation on disjoint held-out sets. |
| `src/bench_models.py` | Every local detector on every validation set. |
| `src/imgsz_sweep.py` | Does inference resolution matter? (640 vs larger on a 1920×344 band.) |
| `src/show_dets.py` | Renders winning-model detections vs ground truth (CPU). |
| `src/fullrun.py` | Checkpointed full run of the upstream pipeline. |
| `src/conf_sweep.sh` | Sweeps detector confidence 0.15–0.25 through `detect_match.py`. |
| `src/eval_sweep.sh` | Evaluates the confidence sweep outputs. |

### `src/experiments/` [U] (71 files)

`src/experiments/README.md` describes them as one-off research scripts that are not part of any pipeline. Many
read artefacts that no longer exist. All of them **appear unused** by code; they are kept for reference. Run them
with `PYTHONPATH=src`.

| file | purpose |
|---|---|
| `README.md` | What this folder is and how to run the scripts. |
| `aabb_bias.py` | Bias of axis-aligned boxes and whether it can be corrected. |
| `appear2.py` | Spatially structured appearance descriptor. |
| `audit.py` | Audits candidate dataset sources for garbage-in. |
| `bench_infer.py` | Inference speed on the Mac (model size × resolution × backend). |
| `bench_mps.py` | MPS compute ceiling vs data loading. |
| `bg_plate.py` | Temporal median of the top panel over the play window. |
| `bow_check.py` | Bounds distortion using edges verified to be single lines. |
| `broadcast_detect.py` | Detects broadcast frames by flat-colour overlay regions. |
| `build_arms.py` | Training arms A (broadcast) / B (+jw), single class. |
| `build_arms2.py` | Same, with held-out validation clips removed. |
| `build_train.py` | Pools external datasets (unique sources, single class). |
| `build_train2.py` | Pools all usable sources, filters junk, dedupes. |
| `calib3d.py` | 3D calibration from carpet points + tag centres. |
| `calib_station.py` | Station panel calibration from AprilTags alone. |
| `calib_v1.html.bak` | Old hybrid calibration page (backup). |
| `calibrate.py` | Ground-plane calibration + distortion detectability test. |
| `check_accel.py` | Confirms the accelerator is real and faster. |
| `check_labels.py` | Label counts, field projection, size plausibility. |
| `click_accuracy.py` | Error budget for clicked image points → field. |
| `confound.py` | Separates model bias from validation-data properties. |
| `crossings.py` | Extracts crossing events + crops for identity labelling. |
| `dedupe_calib.py` | Calibrates the dedupe threshold with a control. |
| `dedupe_check.py` | All-pairs perceptual dedupe + hash validity. |
| `dedupe_v2.py` | Finer hash: re-encode vs different-frame separation. |
| `distortion.py` | Lens distortion via plumb lines. |
| `distortion_impact.py` | Field error from ignoring distortion. |
| `eval_field.py` | Ranks detectors by field-position error. |
| `eval_tracks.py` | Tracks vs the 83 hand-labelled frames. |
| `fetch2.py`, `fetch3.py`, `fetch4.py` | Roboflow dataset downloads (batches 2–4; read `.roboflow_key`). |
| `field_positions.py` | Plots labelled robot positions on the field. |
| `go.sh` | Runs `src/fullrun.py` under caffeinate. |
| `handoff.sh` | Waits for a 960 epoch, then warm-starts the 640 continuation. |
| `layout_scan.py` | Classifies broadcast layout per frame. |
| `link.py` | Chains tracklets into six tracks (old tracker variant). |
| `make_chart.py` | Training chart from `train.log`. |
| `make_pdf.py` | PDF of random samples per dataset. |
| `miss_analysis.py` | Are detector misses random or systematic? |
| `mosaic_test.py` | Mosaic augmentation on/off test. |
| `orient_test.py` | Field-frame orientation from held-out tags. |
| `outliers.py` | Leave-one-out calibration outliers. |
| `overlay.py` | Projects the calibrated field onto match frames. |
| `panel_geom.py` | Exact panel rectangles; static-camera test. |
| `pipeline.py` | Old end-to-end detections → six trajectories. |
| `probe5.py`, `probe_ds.py`, `probe_ds2.py` | Roboflow dataset probes (versions, sizes). |
| `probe_layout.py` | Panel boundaries + static top camera probe. |
| `propose.py` | Auto box proposals: background subtraction + field mask + bumper colour. |
| `propose2.py` | Colour-gated proposals. |
| `rate_sampler.sh` | Samples training s/it while `train_ext.py` runs. |
| `repair.py` | Post-hoc identity repair at crossings. |
| `run_assign.py` | Driver: tracklet-level assignment → trajectories. |
| `run_tracks.py` | Driver: detections → tracklets → EM → trajectories. |
| `smoke.py`, `smoke2.py`, `smoke3.py` | 2-epoch YOLO smoke tests on MPS. |
| `solve_vp.py` | Pose from vanishing points + ground points. |
| `source_audit.py` | Per-source-video quality audit across datasets. |
| `source_dedupe.py` | Dedupe by source video. |
| `speed_test.py` | Real training throughput across settings. |
| `summary.py` | Full-match trajectory summary plot. |
| `tag_accum.py` | Accumulates tag detections across a match. |
| `tag_plate.py` | Tags on the median plate with upscaling. |
| `tag_probe.py` | Can 36h11 tags be detected per panel? |
| `tag_visibility.py` | Which tags face the camera. |
| `top_view.py` | Grabs a top-panel frame at ~100 s. |
| `train_ext.py` | Trains on external data only (MPS + AMP). |
| `watch_train.sh` | Overnight training watcher with a status file. |

---

## Binary assets (grouped)

| group | count | notes |
|---|---|---|
| `lab/shots/*.webp` | 149 tracked (+9 untracked) | Lab screenshots. |
| `web/fonts/*.woff2` | 16 | Theme fonts (OFL), Latin subsets, generated by `site-themes.mjs`: fraunces, geologica, hanken-grotesk, lexend, atkinson-next, manrope, saira, bricolage, familjen, gabarito, chivo, instrument-sans, mona-sans, plex-sans, sofia-sans(-condensed). |
| `lab/fonts/*.woff2` | 17 | Same set + `c-saira.woff2`. |
| `live/models/ocr/*.onnx` | 2 | PP-OCRv3 det (2.4 MB) + rec (10.7 MB). |
| `web/field.jpg`, `lab/field.jpg` | 2 | Top-down field image (identical copies). |
| `docs/demo.gif` | 1 | Upstream demo. |
| `lab/api/**.json` | 3 | Lab fixtures (listed above). |

---

## What the live pipeline uses from `src/`

`live/process.py` and `live/fuse.py` add `src/` to `sys.path` and import **only** these three:

- **`track`**: `T.build_tracklets`, `T.DT`, `T.VMAX`, `T.MISS_MAX`, `T.MIN_LEN`.
- **`fit6`**: `fit6.rts` (the RTS smoother).
- **`assign`**: `AS.bhat_sets`, `AS.NEX` (appearance distance).

All three `import geom`. The live pipeline never loads `src/geom.py`: `live/camera.install_geom_shim(cam)`
registers a synthetic `geom` module in `sys.modules` first. That module carries the event camera's
`field_to_img`, `img_to_plane`, `img_to_ground`, `box_to_field`, `jac`, `cov`, `LEN`, `WID`, `FOOTPRINT`,
`CORNER_*`, `field_nms` and `in_field`. `src/fuse_track.py` was ported into `live/fuse.py` rather than imported.

`enrich_all.py`, `reprocess.py`, `station_export.py` and `station_ocr.py` also add `src/` to the path, for the
same three modules via `process` / `fuse`. Nothing else under `src/` is reachable from `live/`, `server/` or
`web/`.

## Files that appear unused or legacy

Checked by grepping the whole repo for imports, CLI calls and references:

- **`live/identity.py`**: `extract_features`, `read_bumpers`, `app_desc`, `_ocr_sessions`, `_det_boxes`, `_warp`,
  `_rec_logp`, `_ctc` (the v1 second-pass OCR with its own copy of the PP-OCR loading). Nothing calls
  `extract_features`. The module docstring still describes this v1 design, while production identity comes
  from `ocr.py` + `fuse.run_labeled`. `solve_hmm` / `solve` only matter for v1 match files.
- **`live/analytics_demo.py`**: dev script tied to `.work/samples`; not referenced anywhere.
- **`live/verify_ids.py`**: manual CLI; only mentioned in `SPEC_V2.md`.
- **`live/overlay.py`**: manual CLI renderer; the worker never calls it. Note that a module named `overlay.py`
  also exists in `src/experiments/`.
- **`live/enrich_all.py`**: manual backfill CLI; `reprocess.py` covers most of its use.
- **`live/intakes.py`**: `_rolling_events` and `_outpost_events` are implemented but switched off.
- **All of `src/experiments/`**, and in `src/` everything except `track`, `fit6`, `assign`: upstream research
  code, runnable by hand per `INSTRUCTIONS.md`. `src/geom.py` is shadowed in production.
- **`tools/*.html`**: upstream hand-calibration tools, superseded by the web calibration wizard.
- **`lab/*.js` page copies**: stale snapshots of `web/*.js`, used only inside the lab.
- **Root `requirements.txt`**: not used by any image.

## Inconsistencies spotted while inventorying

- `server/openapi.json` has 50 paths but lacks `/admin/update`, `/admin/update/check|apply|rollback` (added in
  fa29065). `server/README.md` says to regenerate it after adding routes.
- `web/themes.css` (generated header) says the default is "Sand & Earth". The last commit (cc6297c) made
  `theme.js` default to `{style: "jet", mode: "auto"}`. The `:root` fallback in `themes.css` is still Sand & Earth,
  so without JS the site shows Sand & Earth, and with JS it shows Jet.
- `live/field/2026-rebuilt-welded.json` references `2026-rebuilt.png`, which is not in git (only in local
  `data/field/`).
- `.dockerignore` does not exclude `.work/` (about 4.8 GB locally).
- `deploy/README.md` and `deploy/updater/README.md` describe the one-click updater as installable. On
  atomosserver it is **not installed**: the stack's `app/` is a plain `git archive` copy (no `.git`), with no
  systemd units and no `updater/` folder.
- `live/identity.py`'s docstring describes the superseded v1 identity design (see above).
- `lab/admin.js` documents `#/admin[/users|settings|events]`; the real `web/admin.js` also has `updates`.
- `server/README.md` ("Adding endpoints") tells authors to use `get_db` / `tx`. No `tx` helper exists in
  `server/`; routers use `DB.get_db` and explicit transactions.
