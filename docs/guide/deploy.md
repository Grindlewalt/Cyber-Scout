# Deploying and operating Cyber-Scout

This page covers installing, configuring, updating, backing up and running the stack day to day, with the exact
commands. Facts marked **(atomosserver)** were checked read-only on the production host on 2026-10-12.
[architecture.md](architecture.md) explains what each container does and where every file lives.

> **In progress at time of writing.** A performance pass (`web/nginx.conf`, `Dockerfile`s) and a competitions pass
> (`live/worker.py`, server routers) were under way. Commands below match the committed code. Newer commits may add
> flags or change the worker loop.

---

## 1. Install (`deploy/install.sh`)

```bash
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/Cyber-Scout/main/deploy/install.sh | bash
# or from a checkout, without git:
CS_SOURCE=/path/to/checkout CS_DIR=/opt/cyber-scout bash deploy/install.sh
```

What it does, in order:

1. **Privileges.** Uses `sudo` when it isn't root. Records the invoking user's uid/gid (`SUDO_UID`/`SUDO_GID` or
   `id -u`) as `PUID`/`PGID`.
2. **Docker.** Requires `docker` and Compose v2. Falls back to `sudo docker` if the user isn't in the `docker`
   group.
3. **GPU.** Uses the GPU when `nvidia-smi -L` works and the NVIDIA container toolkit is present (`docker info`
   runtimes, `nvidia-container-cli` or `nvidia-ctk`). Otherwise it warns and goes CPU-only, which needs Compose
   ≥ 2.24.4 for `!reset`.
4. **Location.** Asks for `CS_DIR` and `CS_PORT`, then creates and chowns `CS_DIR`.
5. **Code into `app/`.** With `CS_SOURCE`, it tars the checkout (excluding `.git .venv .work data node_modules
   __pycache__ .env *.key .roboflow_key`). If `app/.git` exists, it runs `git fetch/checkout/pull --ff-only`.
   Otherwise it runs `git clone --depth 1 --branch $CS_BRANCH $CS_REPO app`. It refuses when `app/` exists but
   isn't a git checkout and no `CS_SOURCE` is given. **The atomosserver stack is in that state.**
6. **Compose files.** Copies `app/deploy/docker-compose.yml` and `docker-compose.cpu.yml` into the stack folder. A
   changed old copy is saved as `*.bak`.
7. **`.env`** (only if missing, umask 077): `WEB_PORT`, `PUID`, `PGID`, `TZ` (from `/etc/timezone` or
   `timedatectl`), `MEDIA_DIR=./data/media`, `YT_FORCE_IPV4=1`, `CS_PREFIX` (if not the default) and, CPU-only,
   `COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml`. An existing `.env` is kept; only the CPU line is
   added if needed.
8. **Data.** Runs `mkdir -p data/{models,media,site/api,config}`. If `data/models/robot.onnx` is missing, it
   downloads `CS_MODEL_URL`, or warns that matches won't be processed until the file is there (no restart needed;
   the worker re-checks every 30 s).
9. **Start.** Runs `docker compose up -d --build`, then polls `http://127.0.0.1:$CS_PORT/app/v1/health` for up to
   120 s.
10. **Report.** Prints the LAN and Tailscale URLs and the **one-time setup code** from `data/setup-code.txt`.
11. **Updater (optional).** Offers to install the host updater (`CS_UPDATER=y|n`; with `CS_YES=1` the default is
    `n`). See §6.

Options (environment variables): `CS_DIR` (`/opt/cyber-scout`), `CS_PORT` (7560), `CS_REPO`
(`https://github.com/Grindlewalt/Cyber-Scout.git`), `CS_BRANCH` (`main`), `CS_SOURCE`, `CS_MODEL_URL`, `CS_CPU=1`,
`CS_PREFIX` (`cyber-scout`), `CS_YES=1` (no questions), `CS_UPDATER`.

Re-running `install.sh` is the documented update path when there is no updater and `app/` is a git checkout.

### First run

Open the URL. The setup wizard asks for the setup code and creates the admin account
(`POST /app/v1/setup`). It then asks for the TBA read key (validated against TBA, stored in `app.db` and
`data/config/worker.json`), the team number, the season goal and the events to follow. Then go to the **Video**
page for each event: sources, layout boxes and the calibration wizard (see §8.2). Teammates are added in
**Admin → Teammates**.

---

## 2. `.env` and settings

`.env` holds **infrastructure only**. App settings (TBA key, followed events, team, goal and the options
`poll_seconds`, `gpu_device`, `cpu_only`, `hubcam`) live in `app.db` and are mirrored to `data/config/worker.json`.
The worker re-reads that file every poll, so Admin changes apply without a restart. Anything left null in the
app falls back to the matching `.env` value.

| key | where it acts | notes |
|---|---|---|
| `WEB_PORT`, `WEB_BIND` | compose port mapping | `WEB_BIND` = a LAN or Tailscale IP to stop listening on every interface |
| `PUID`, `PGID` | entrypoints | owner of `./data` |
| `TZ` | all | |
| `MEDIA_DIR` | compose mounts | host path; put it on a big disk |
| `GPU_IDS` | compose GPU reservation | host card numbers (`nvidia-smi -L`) |
| `GPU_DEVICE` | worker, recorder | card index **inside** the container (normally 0) |
| `COMPOSE_FILE` | compose | CPU-only override |
| `COOKIE_SECURE` | api | `auto`/`1`/`0` |
| `PAUSE_PROCESSING=1` | worker | see below |
| `POLL_SECONDS`, `HUBCAM`, `EVENTS`, `TBA_KEY` | worker | fallbacks only; prefer the app |
| `YT_FORCE_IPV4` | worker, recorder | keep `1` on hosts whose IPv6 is bot-flagged by YouTube |
| `LIVESTREAM`, `LIVE_BUDGET_GB`, `LIVE_MIN_FREE_GB`, `LIVE_BACKFILL_MIN`, `LIVE_DIR`, ... | recorder | §8.7 |
| `CS_URL_ALLOW` | worker, recorder | private hosts allowed as video sources (e.g. a LAN OBS HLS server) |
| `DASH_TEAM`, `DASH_GOAL`, `DASH_AWARD_CONTENDER`, `DCMP_CAPACITY` | worker | strategy perspective. `DASH_TEAM` defaults to `frc4911`, and the Admin team number does **not** change it |
| `*_MEM_LIMIT` | compose | per-container caps |

The full variable list, including tuning knobs, is in [architecture.md §7](architecture.md#7-environment-variables).
API-only variables reach the api container through an explicit allowlist in `deploy/docker-compose.yml` (no
`env_file`): `SESSION_IDLE_DAYS`, `SESSION_MAX_DAYS`, `PASSWORD_MIN`, `MEMBER_PASSWORD_MIN`, `SETUP_CODE_REQUIRED` and `COOKIE_SECURE`, each defaulting to the
`server/config.py` value (7 / 30 / 10 / 4 / 1 / auto). Set one in `.env` and recreate the api (`docker compose up -d
api`). The legacy `TBA_KEY`/`EVENTS` import is deliberately **not** passed (enter the key in the setup wizard); a
new api setting must be added to the api service's `environment:` (see `deploy/README.md` "API settings").

**(atomosserver)** `/opt/stacks/cyber-scout/.env` (0600) contains only `POLL_SECONDS=120`, `GPU_DEVICE=0`,
`TZ=America/Los_Angeles`, `PUID=1000`, `PGID=1000`, `MEDIA_DIR=/mnt/nas/autoscout-media` and `PAUSE_PROCESSING=1`.
There is no `TBA_KEY` (it lives in the app). `worker.json` follows `["2026wasam"]`, team 4911, goal DCMP, with all
options null.

### `PAUSE_PROCESSING=1`

`worker.main()` checks it on every loop iteration and just logs `waiting: match processing paused
(PAUSE_PROCESSING=1)`. That means:

* **no new matches are processed** and **the site is not rebuilt** (`build_site` only runs inside `sync()`), so
  dashboards, rankings, schedule, selection and scout snapshots all stay as they are;
* the Video-page job thread (frame grabs, layout suggestions, calibration fits) **still runs**;
* the **recorder is not affected**. It records whenever a followed event with a livestream source is within its
  dates;
* the updater never edits `.env`, so the pause survives updates.

Production on atomosserver runs paused. The 2026wasam data is complete and frozen, and new site builds are made by
hand (§8.3).

---

## 3. The compose services

```bash
cd /opt/stacks/cyber-scout          # (atomosserver) — install.sh's default is /opt/cyber-scout
docker compose ps
docker compose up -d --build        # build changed images and (re)create changed containers
docker compose build worker && docker compose up -d worker recorder
docker compose restart web
docker compose config               # resolved config (careful: prints environment values)
```

| service | container | image | published | GPU |
|---|---|---|---|---|
| worker | cyber-scout-worker | cyber-scout-worker:latest (~3.8 GB) | — | `GPU_IDS` |
| recorder | cyber-scout-recorder | cyber-scout-worker:latest (shared) | — | `GPU_IDS` |
| api | cyber-scout-api | cyber-scout-api:latest (~146 MB), healthcheck | — (8000 internal) | — |
| web | cyber-scout-web | cyber-scout-web:latest (~64 MB) | `0.0.0.0:7560->80` | — |

**(atomosserver)** All four are up. `GPU_IDS` is unset, so worker and recorder share host **GPU 0** (RTX 3060 Ti,
8 GB). GPU 1 (RTX 3060, 12 GB) is used for development runs from `/mnt/nas/autoscout-scratch` (see
`live/HANDOFF.md`). The network is `cyber-scout_default` (bridge, gateway 172.25.0.1).

### GPUs: `GPU_IDS` vs `GPU_DEVICE`

* `GPU_IDS` (compose) chooses **which host cards** are passed into worker and recorder, e.g. `1` or `0,1`.
* Inside the container those cards are renumbered from 0. `GPU_DEVICE` (or Admin → `gpu_device`) chooses the
  **container-local** index for the detector (`live/detector.py`) and OCR (`OCR_GPU` defaults to it). With one card
  passed in, keep it at 0.
* `live/reprocess.py --gpus 0,1` round-robins jobs over container-local indices. Both cards must be passed in
  (`GPU_IDS=0,1`) for `--gpus 0,1` to mean two cards.
* CPU-only: `COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml` (sets `FORCE_CPU=1`), or Admin `cpu_only`.
  About 4× slower (~20 min per match instead of ~3–6).

---

## 4. How a change is deployed today (atomosserver)

The production stack is **not a git checkout** (`/opt/stacks/cyber-scout/app/.git` does not exist), so
`install.sh` refuses to update it and the updater is not installed. Changes are deployed by hand from the dev
machine:

```bash
# on the Mac, in the repo, after committing
git archive --format=tar HEAD | ssh atomosserver 'tar -x -C /opt/stacks/cyber-scout/app'
ssh atomosserver 'cd /opt/stacks/cyber-scout &&
  for f in docker-compose.yml docker-compose.cpu.yml; do
    cmp -s app/deploy/$f $f || { cp $f $f.bak-$(date +%s); cp app/deploy/$f $f; }
  done &&
  docker compose up -d --build'
```

* `git archive` writes tracked files only and **does not delete** files that were removed from the repo. Remove
  those by hand. **(atomosserver)** At this writing `app/` contains exactly the tracked files of `cc6297c`
  (identical file list; spot-checked `live/worker.py`, `web/nginx.conf`, `Dockerfile`, `web/app.js` and
  `server/main.py` by md5).
* The stack folder holds `docker-compose.yml.bak-1791758519`, `.bak-1791862243` and `.bak-1791865912`, earlier
  compose files kept by this procedure. The current `docker-compose.yml` is identical to
  `app/deploy/docker-compose.yml`.
* Rebuild cost: the worker's big pip layer is cached as long as the first lines of `Dockerfile` stay
  byte-identical. A code-only change rebuilds only the `COPY` layers (seconds). Changing anything before
  `COPY src/` (base image, apt, pip) rebuilds ~3.5 GB, which **does not fit** on the current root disk (§7).
* Only the containers whose image or config changed are recreated. The web image's cache-bust hash changes
  whenever any shipped JS or CSS changes.
* With `PAUSE_PROCESSING=1` a deploy changes nothing in `data/site/api`. Rebuild the site by hand (§8.3) if the
  change affects site JSON.

If `app/` is ever turned into a git checkout (or the updater is installed: its first run converts a copied `app/`
into a git checkout without changing any file), use `install.sh` or Admin → Updates instead.

---

## 5. Updates with the host updater (`deploy/updater/`)

The code is complete and tested (`server/tests/test_update.py`), but it is **not installed on atomosserver**: no
`cyber-scout-updater.*` systemd units and no `/opt/stacks/cyber-scout/updater/` folder. Admin → Updates therefore
shows "not installed" with the install hint, and its action buttons return 409.

Install, run and remove it:

```bash
cd /opt/stacks/cyber-scout
sudo ./app/deploy/updater/install-updater.sh             # system units, run as the folder's owner
./app/deploy/updater/install-updater.sh --user           # or user units (then: sudo loginctl enable-linger $USER)
sudo ./app/deploy/updater/install-updater.sh --uninstall
python3 updater/cs-updater.py --stack $PWD status | check | apply [--ytdlp] | rollback
tail -f updater/update.log
```

How it works:

* **Units.** `<prefix>-updater.service` is a oneshot that runs `cs-updater.py --stack <dir> run` as the stack
  owner, hardened with `NoNewPrivileges`, `ProtectSystem=full`, `PrivateTmp` and similar. `.path` starts it when
  `data/config/update-request.json` changes. `.timer` starts it 90 s after boot and then every 60 s for the
  heartbeat, missed requests and a daily auto-check (`AUTO_CHECK_HOURS`).
* **Request validation.** The request must be a regular file (opened `O_NOFOLLOW`), ≤ 2 KB, have exactly the keys
  `v action nonce at by ytdlp`, be less than 15 min old, and carry an unseen 32-hex nonce. Anything else is
  deleted and logged.
* **apply.** First a disk check: `MIN_FREE_GB` + `HEAVY_BUILD_GB` (3.5) when the worker Dockerfile changed
  before its first `COPY`, else `LIGHT_BUILD_GB` (0.3). Then:
  1. `sqlite3` online backup of `app.db` to `updater/backups/` (keeps 5);
  2. checkout of the new commit on local branch `cs-deployed`;
  3. copy of changed compose files (`.bak-<time>`);
  4. tag running images `:cs-rollback`, then `docker compose build` and `up -d`;
  5. health wait: api healthy, worker and recorder running without restarts, web 200 on `/` and
     `/app/v1/health`;
  6. removal of this project's now-unused images by id.

  Any failure rolls back automatically: previous commit, compose files and `:cs-rollback` tags.
* **rollback.** Goes back to the previous good version. Nothing is rebuilt if the `:cs-rollback` images still
  exist.
* **yt-dlp re-pin** (`--ytdlp` / checkbox). Runs `pip-compile --generate-hashes` in a throwaway container of the
  worker base image (`--pull never --cap-drop ALL`, non-root, tmpfs). It accepts only `name==version`, hash and
  comment lines, commits locally (`Local: yt-dlp …`) and rebuilds only the yt-dlp layer.
* **Never touched:** `.env` and `data/`, except the status file.
* **Configuration:** `updater/updater.conf` (`REMOTE`, `BRANCH`, `SSH_KEY`, limits). A private repo needs a
  read-only deploy key (`updater/deploy_key`, known_hosts pinned to GitHub's ed25519 key).

---

## 6. Backups

Everything stateful is in the stack folder: `data/` plus `.env`, and the compose files are reproducible from
`app/deploy/`. Videos are in `MEDIA_DIR`.

| what | importance | how |
|---|---|---|
| `data/app.db` | **critical**: accounts, settings, TBA key, picklists, notes, audit log | `sqlite3 data/app.db ".backup data/app.db.bak"` while running (WAL mode), or `docker compose stop api` and copy `app.db*`. Contains secrets: keep backups private. |
| `data/events/<ev>/matches/*.json` | expensive to redo (~3–6 GPU-min per match + re-download) | copy the folder |
| `data/events/<ev>/{fuel,shots,analytics,stations}/`, `overrides.json`, `camera*.json`, `station_*.json`, `sources.json`, `layout.json` | redoable but slow / hand-made | copy |
| `data/models/robot.onnx` | required; reproducible only by retraining | copy |
| `data/site/api/` | regenerated by the worker | optional |
| `data/tba/`, `data/cache/`, `data/training/` | disposable / re-downloadable (`training/rf` is a Roboflow export: the owner is on the free tier, so **do not** delete it casually) | optional |
| `MEDIA_DIR` | kept videos (re-downloadable from YouTube); livestream clips are **not** re-downloadable once YouTube drops the DVR | copy clips you care about |
| `.env` | small, 0600 | copy |

Restore: install Docker (+ NVIDIA toolkit), copy the folder back, `docker compose up -d --build`. Processed matches
are not redone because the worker skips any match whose `matches/<key>.json` exists.

The updater's DB backups (`updater/backups/`) exist only when the updater is installed. They are **not**
restored automatically after a failed update.

---

## 7. Disk space on atomosserver (read this before building)

**(atomosserver, 2026-10-12)**

| filesystem | size | used | free |
|---|---|---|---|
| `/` (`/dev/mapper/cryptroot`, holds `/var/lib/docker` and `/opt/stacks`) | 915 G | 866 G | **3.0 G (100%)** |
| `/mnt/nas` (`/dev/sdd`) | 7.3 T | 155 G | 7.2 T |

* Docker images total 37.65 GB, 8.5 GB of it reclaimable. The build cache is empty. The worker image alone is
  3.79 GB.
* A **heavy** worker rebuild (any change above `COPY src/` in `Dockerfile`) needs ~3.5 GB of free space plus margin
  and will fail or fill the disk right now. Free space first: `docker image ls`, remove images you know are unused
  by id (never a blind `docker system prune` on a shared host), and delete the stale downloads below.
* `data/cache/` holds ~185 MB of abandoned yt-dlp partials from 2026-10-10 (`*.part.mp4.part`,
  `2026wasam_qm53__Xi2WhK0L38.mp4`, `2026wasam_qm56.mp4`). The worker deletes its own working copies after each
  match, but not leftovers from killed runs. They are safe to delete while the worker is idle.
* `/var/lib/systemd/coredump` holds ~539 MB of core dumps from development Python runs (§9.5).
* `data/training/` is ~1 GB. Keep `training/rf` (Roboflow free-tier constraint).
* Big or throwaway data belongs on `/mnt/nas`: `MEDIA_DIR=/mnt/nas/autoscout-media` (~12 GB now), scratch in
  `/mnt/nas/autoscout-scratch`.
* The livestream recorder writes ~2 GB per hour of stream to `MEDIA_DIR/live`. That is on the NAS here, so it is
  not a root-disk risk.

---

## 8. Common operations

Commands run on the server in the stack folder. `exec` into the worker **as the data owner**, or files come out
root-owned:

```bash
W='docker compose exec -u 1000:1000 -e HOME=/tmp -w /app worker'
```

### 8.1 Logs

```bash
docker compose logs -f worker            # poll loop, per-match progress ("qm12: processing", "done (..., coverage 98%)")
docker compose logs -f recorder          # livestream service ("recording ...", child restarts)
docker compose logs -f api               # sign-ins, settings, warnings (no access log)
docker compose logs -f web               # nginx access + error log (the only request log)
tail -f /mnt/nas/autoscout-media/live/<ev>/<video id>/livestream.log   # one recorder child
tail -f updater/update.log               # updater (if installed)
```

### 8.2 Follow a competition

1. **Admin → Events → add** the event key (validated on TBA). This rewrites `worker.json`, and the worker and
   recorder notice within seconds.
2. **Video page** for the event:
   * **Sources.** TBA webcasts and per-match videos are discovered automatically. Paste a livestream or alternate
     angle if needed. Mark the wide overhead source.
   * **Layout.** Draw the field, station-inset, scoreboard and mask boxes on a sample frame (→ `layout.json`), or
     accept the suggestion.
   * **Calibration wizard** (overhead, and optionally `station_red`/`station_blue`): detect AprilTags, click field
     points, fit, check the overlay, **accept**. This writes `data/events/<ev>/camera.json` (the previous one is
     kept as `.bak-<ts>`, and `pose_latest.json` is set aside).
3. **The worker skips every match of an event without `camera.json`** (data dir or repo `events/<ev>/`), unless
   a trackable livestream clip exists. Calibrate before the first match if you can.
4. **Unpause** if needed: remove `PAUSE_PROCESSING=1` from `.env`, then run `docker compose up -d worker`.
   Changing `.env` needs a container recreate; a restart is not enough.
5. **Per-event video preferences** (optional): `data/events/<ev>/event.json` with `prefer` (default `^Wide
   Match`), `reject` (default `^Match \d`), `allow_channels` (default `["FIRSTRoboticsCompetition"]`) and
   `search_title`. Sources marked on the Video page override these through `sources.video_config()`.
6. **Livestream tracking** only works if the stream source has a field/overhead box **and**
   `data/events/<ev>/camera_<source id>.json` exists, or `camera.json` was calibrated on that source. Otherwise
   the stream gives fast results only (§8.7).

The legacy way still works: `.env` `EVENTS=2026xxx`, used only while no events were ever managed in the app.

### 8.3 Rebuild the site JSON (without processing matches)

The worker rebuilds the site after every processed match and, **when nothing was processed, once per poll**.
With `PAUSE_PROCESSING=1` it never does. To rebuild by hand:

```bash
$W python -c "
import sys; sys.path.insert(0, '/app/live'); import worker as W
key, evs = W.load_config(); tba = W.TBA(key, W.os.path.join(W.DATA, 'tba'))
for ev in evs: W.build_site(ev, tba)"
```

That is the full `build_site`: match, team and event files, then the dashboard (`--snapshots --only-missing`),
`scouting.site_build_extra`, all scout snapshots, `goals.site_build` and missing selection snapshots. It refreshes
TBA through the ETag cache. Expect minutes, not seconds: the scout snapshots alone are rebuilt every time (~3 s
each, 37 on 2026wasam).

Individual pieces (DATA = `/data`, EV = event key):

```bash
$W python -m live.strategy.dashboard /data EV --site                       # dashboard.json, dashboard.<team>.json, preview/
$W python -c "import sys; sys.path.insert(0,'/app/live'); import scouting; scouting.site_build_extra('/data','EV')"
                                                                           # t/* (verified), spec/*, defense.json, scout.json
$W python -m live.strategy.goals /data EV                                  # rankings.json, schedule.json, selection.json
```

`--offline` (dashboard, goals) skips network calls and uses cached TBA/Statbotics. `--team frcNNNN` / `--teams
all` add perspectives.

### 8.4 Regenerate replay snapshots

```bash
$W python -m live.strategy.dashboard /data EV --site --snapshots             # every dash/<ts>.json (30-min steps), not only missing
$W python -m live.scouting /data EV --snapshots                              # scout/<ts>.json for every dash/<ts>.json
$W python -m live.strategy.goals /data EV --selection-snapshots --all        # selection/<ts>.json (drop --all: only missing)
$W python -m live.strategy.goals /data EV --asof <unix ts>                   # one as-of goal/selection build
```

Order matters: dashboard snapshots first (they define the timestamps), then scout, then selection (pitch cards
read `scout/<ts>.json`). The prequential backtest rows are cached in `data/cache/strategy/backtest_<ev>.json`,
keyed by a hash of the video content and model settings. Delete the file to force a full recompute.

### 8.5 Re-track a match

* **Through the worker (re-downloads from YouTube):** delete
  `data/events/<ev>/matches/<key>.json` (and, for a clean v2 run, `fuel|shots|analytics/<key>.json`). If
  `status.json` shows `"state": "failed"` for it (3 errors), remove that entry too, or it stays skipped. Needs
  `PAUSE_PROCESSING` off.
* **From the kept video (no YouTube), works while paused:**

  ```bash
  $W python live/reprocess.py /data /media EV --only EV_qm12,EV_qm17 --jobs 1 --gpus 0 [--hubcam]
  ```

  This runs tracking, identity, fuel, shots and analytics for those matches from `MEDIA/<ev>/<key>.mp4`, starting
  from the old match JSON's camera pose and with TBA's lineup. It overwrites `matches/`, `fuel/`, `shots/` and
  `analytics/`. Afterwards rebuild the site (§8.3). Check the GPU first (`nvidia-smi`): the worker and recorder
  share it.
* **Enrichment only** (fuel → shots → analytics; tracking kept): `live/enrich_all.py DATA EVENT [--jobs N]
  [--force]`. It re-downloads the exact video id unless the file is present in its scratch dir. Written for
  backfills, CPU-only.
* **Wrong team on a robot:** fix it in the app (match page → identity fix), which writes
  `data/events/<ev>/overrides.json` (`{"<match key>": {"red": {"<track idx>": "frcNNNN"}}}`). The fix takes
  effect at the next `build_site` (§8.3 while paused). No re-tracking is needed.

**Rule of thumb from the owner:** iterate on a dev set (2026wasam qm1, qm10–19) and **ask before reprocessing a
whole event**.

### 8.6 Accounts (break-glass)

```bash
docker compose exec api python -m server.manage users
docker compose exec api python -m server.manage reset-password <username>   # prints a new password, ends sessions
docker compose exec api python -m server.manage make-admin <username>       # also re-enables the account
```

`server.manage` drops to PUID/PGID by itself.

### 8.7 Livestream recorder

* Status: the Video page, `GET /app/v1/events/<ev>/livestream`, or `data/events/<ev>/livestream.json`. States:
  `recording`, `waiting`, `stream_over`, `idle`, `error`, `no_source`, `disabled`.
* It records only for followed events that have an enabled livestream source and whose TBA dates (±1 day) include
  today (`LIVE_ANY_DATE=1` overrides). It picks the source marked primary/wide, then the webcast dated today, then
  an admin-added source.
* Output: `MEDIA_DIR/live/<ev>/<video id>/`. Retention per stream is `LIVE_BUDGET_GB` (60). Non-match segments are
  removed after 2 h and match segments after 12 h. Clips are kept. Segments of finished streams go after
  `LIVE_KEEP_SEG_DAYS` (2), or sooner below `LIVE_MIN_FREE_GB` (30) free.
* Turn it off with `LIVESTREAM=0` in `.env` and `docker compose up -d recorder`, or `docker compose stop
  recorder`.
* Offline test against a recorded file: `$W python live/livestream.py --replay FILE --out /tmp/ls`.

### 8.8 Run a one-off processing pass

```bash
docker compose exec -u 1000:1000 -e HOME=/tmp -e PAUSE_PROCESSING=0 -e ONCE=1 -e MAX_NEW=1 worker python -u live/worker.py
```

This runs one sync pass in a second process that processes at most one new match per event and exits. Don't run
it while the main worker is processing: there is no lock between the two.

---

## 9. Troubleshooting

### 9.1 YouTube: "Sign in to confirm you're not a bot", 403s, no formats

* atomosserver's **IPv6** egress is bot-flagged. `YT_FORCE_IPV4=1` (the default) makes `videos.meta` and
  `videos.download` pass `-4`. The last of the 4 download attempts goes without `-4` as a fallback. The recorder
  resolves and fetches chunks over IPv4 only, because HLS chunk URLs are bound to the IP that resolved them.
  `videos.search()` (fallback search) does **not** pass `-4`.
* 403 / "Requested format is not available" / signature errors usually mean yt-dlp is too old. Bump
  `live/requirements-ytdlp.txt` (instructions at the top of the file; with the updater, tick "also update yt-dlp")
  and rebuild the worker: a light rebuild of the yt-dlp layer only. yt-dlp needs a JS runtime for YouTube. The
  image ships `deno` 2.5.6 for that.
* Downloads use `--sleep-requests 1 --retry-sleep 5` and back off 10–20 s, 20–30 s and so on between attempts. A
  match with no acceptable video is retried after 300 s.
* Wrong video picked: check `data/cache/ytmeta.json` (cached titles) and the event's `prefer`/`reject`, or mark
  sources on the Video page.

### 9.2 Disk full

Symptoms: `docker compose build` fails with `no space left on device`, SQLite `database or disk is full` in the api
log, truncated JSON, the worker failing to write `matches/`. Fix: §7. Delete `data/cache/*.part*`, check
`/var/lib/systemd/coredump`, remove unused images by id. `MEDIA_DIR` and the recorder are on the NAS here and not
the cause.

### 9.3 Stale page after a deploy

* `index.html` and `/api/*` are served with `Cache-Control: no-cache`, so the browser revalidates them. JS and CSS
  URLs carry `?v=<md5 of all shipped JS+CSS>`, so a deploy changes every URL at once. Nobody should be able to
  mix old and new scripts, even through a Cloudflare edge.
* If a client still shows old UI: hard reload (Shift+Reload). Theme and layout choices live in `localStorage` per
  browser.
* Site data not updating: check that the worker is not paused (`docker compose logs --tail 5 worker`) and the
  `updated` field in `/api/<ev>/event.json`.

### 9.4 CUDA / onnxruntime

* The detector silently falls back to CPU when the CUDA provider can't load. `Detector.device` is
  `CPUExecutionProvider`, and a match takes ~20 min instead of 3–6. Check:
  ```bash
  docker compose exec worker nvidia-smi
  docker compose exec worker python -c "import onnxruntime as o; o.preload_dlls(); print(o.get_available_providers())"
  ```
  `CUDAExecutionProvider` must be in the list. `nvidia-smi` inside the container exists only when the toolkit
  injects the driver utilities. If it is missing or fails, check the toolkit and driver on the host:
  `nvidia-smi` there, and `docker info | grep -i runtime`.
* `GPU_DEVICE` out of range (e.g. `1` when only one card is passed in) gives CUDA errors. Keep it at 0 and pick
  the card with `GPU_IDS`.
* Out of GPU memory: the worker (detector + 4 OCR worker processes) and the recorder (overlay OCR) share the card.
  On the 8 GB RTX 3060 Ti avoid running `reprocess.py --jobs 2` next to a live worker. Use `--jobs 1`, or pass both
  cards.
* `onnxruntime` prints `[W:onnxruntime ...]` provider warnings. They are harmless, and liveingest filters them out
  of the status.

### 9.5 Core dumps / segfaults

* `libapriltag` (`pupil-apriltags`) has an intermittent destructor segfault. The worker avoids it with one
  module-level detector in `process.check_pose`. Scripts that create and drop detectors repeatedly can still
  crash.
* **(atomosserver)** `coredumpctl list` shows SIGSEGV dumps (2026-10-11 and 10-12) from
  `~/.local/share/uv/python/cpython-3.12.13/bin/python3.12`, the development `rtenv` Python, not the containers.
  They take ~539 MB in `/var/lib/systemd/coredump` on the full root disk. `sudo coredumpctl` / `journalctl --vacuum`
  or deleting old files there frees the space.
* A segfault inside the worker container kills the process, and Docker restarts it (`unless-stopped`). The match
  being processed stays `processing` in `status.json` and is retried on the next pass.

### 9.6 Recorder problems

* `state: no_source`: the event has no enabled livestream source. Add one on the Video page, or let discovery find
  the TBA webcast.
* `waiting` / not live yet: re-checked every `LIVE_RESOLVE_S` (120 s). After the stream ends (`was_live`) it is
  re-checked every 10 min.
* `livestream URL refused`: the SSRF guard (`sources.safe_public_url`) allows only http(s) to public hosts. Add
  private hosts to `CS_URL_ALLOW`.
* A crashed child is restarted with back-off (30 s to 10 min). After a drop or restart it resumes by chunk
  sequence number and backfills the gap while YouTube's DVR window (~1 h) still has it.
* Clips exist but matches aren't tracked from them: the stream isn't trackable (no field box or no
  `camera_<source>.json`). This is expected for program feeds. Those matches are tracked from the per-match
  uploads.
* A record whose bumper reads say it is another match is rejected (`status.json` `live_rejected`). After 2
  failures (`live_failed`) the worker waits for the uploads instead.

### 9.7 Worker says "waiting"

The log line names the reason: `match processing paused (PAUSE_PROCESSING=1)`, `no TBA key`, `no events followed`
or `model not found: /data/models/robot.onnx`. It re-checks every 30 s, and a settings change wakes it at once.

### 9.8 Sign-in problems

Locked out: §8.6. `429` with `Retry-After` means a login rate limit: nginx allows 10/min per IP, and the API allows
10 failures per (IP, user) and 30 per IP per 15 min. An account without a password gets 401 under the default
no-password policy `never` (Admin → Activity "Off"), and under the legacy `lan` policy whenever the request comes
through the Cloudflare tunnel or any public address — by design (see [server.md](server.md)).
