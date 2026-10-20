# Deploying and operating Cyber-Scout

This page covers installing, configuring, updating, backing up and running the stack day to day, with the exact
commands. Facts marked **(atomosserver)** were checked read-only on the production host on 2026-10-12.
[architecture.md](architecture.md) explains what each container does and where every file lives.

> **In progress at time of writing.** A performance pass (`web/nginx.conf`, `Dockerfile`s) and a competitions pass
> (`live/worker.py`, server routers) were under way. Commands below match the committed code. Newer commits may add
> flags or change the worker loop.
>
> **UX fix wave (2026-10-19, `56fda7c..c64b813`):** the app now shows the worker's paused / waiting state
> (`data/worker-status.json`, §2), the Video page refuses links it can't track (§2 table), and the downloads site has
> its own publish script and nginx config (§8.9).

---

## 1. Install (`deploy/install.sh`, `deploy/install.ps1`)

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/Cyber-Scout/main/deploy/install.sh | bash
curl -fsSL .../deploy/install.sh | bash -s -- --cpu --port 8080      # options after "bash -s --"
bash deploy/install.sh --source . --dir /opt/cyber-scout             # from a checkout, without git
bash deploy/install.sh --dry-run                                     # print the plan, change nothing
```

```powershell
# Windows 10/11 (PowerShell 5.1 or 7; no execution-policy change needed beyond -ExecutionPolicy Bypass)
irm https://raw.githubusercontent.com/Grindlewalt/Cyber-Scout/main/deploy/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1 [-Cpu] [-Port 8080] [-DryRun]
```

What `install.sh` does, in order (`install.ps1` is the same unless noted):

1. **Options.** Flags or the matching environment variables (`CS_DIR`, `CS_PORT`, `CS_BIND`, `CS_REPO`,
   `CS_BRANCH`, `CS_TARBALL`, `CS_SOURCE`, `CS_MODEL_URL`, `CS_MODEL_SHA256`, `CS_CPU=1`, `CS_GPU_IDS`,
   `CS_PREFIX`, `CS_SERVICES`, `CS_YES=1`, `CS_UPDATER=y|n`, `CS_DRY_RUN=1`). `--help` lists them all. Run as
   root without sudo it stops (PUID would be 0) unless `--allow-root`.
2. **Docker.** Needs `docker` + Compose v2. Linux: if Docker is missing it offers `get.docker.com` (only after
   you answer yes, or with `--install-docker`; it runs with sudo and adds you to the `docker` group); if your
   user can't reach the daemon it uses `sudo docker` and says so. macOS / Windows: explains how to install
   Docker Desktop, or offers to start it. Windows also needs Linux containers and warns without the WSL2 backend.
3. **Same-name guard.** If containers of the compose project `CS_PREFIX` already run from another folder, it
   stops (compose project names are global; it would otherwise recreate those containers).
4. **GPU.** Linux: `nvidia-smi -L`, then the toolkit (docker runtime `nvidia`, `nvidia-ctk` or
   `nvidia-container-cli`), then `docker run --rm --gpus all python:3.12-slim nvidia-smi -L`. Several GPUs: asks
   for `GPU_IDS`. No working GPU: asks to continue CPU-only (needs Compose >= 2.24.4 for `!reset`). macOS:
   always CPU. Windows: `nvidia-smi` on the host + the same container test through WSL2.
   ARM hosts (Apple Silicon, Windows/Linux on ARM): `DOCKER_DEFAULT_PLATFORM=linux/amd64` in `.env`, because
   `pupil-apriltags` ships no linux/aarch64 wheel; the worker runs emulated (Rosetta / binfmt).
5. **Disk + memory.** Warns under 20 GB free where Docker keeps images (`DockerRootDir` on Linux, the Docker
   Desktop VM disk on macOS, the drive of `%LOCALAPPDATA%\Docker` on Windows; 2 GB when no worker is built) and
   when Docker has < 8 GB of memory.
6. **Port.** If `CS_PORT` is taken (listener or a container's published port) and not by this stack's own
   `web` container, it offers the next free port.
7. **Folder.** Default `/opt/cyber-scout` (Linux; created with sudo and handed to you), `~/CyberScout` (macOS),
   `%USERPROFILE%\CyberScout` (Windows). An existing `.env` means "update".
8. **Code into `app/`.** `--source DIR`: copy of a checkout (no `.git .venv .work ./data node_modules
   __pycache__ .env *.key .roboflow_key`). `--tarball PATH|URL`: unpacked (one top-level folder is stripped).
   `app/.git` present: `git fetch origin BRANCH` + `checkout -B BRANCH FETCH_HEAD` (refuses with local changes).
   Otherwise `git clone --depth 1` (https with prompts off; on failure, a GitHub repo is retried over SSH when
   you have a key); without git, a GitHub tarball from codeload. Windows clones with `core.autocrlf=false` and
   refuses a CRLF checkout (the container entrypoints are shell scripts). An `app/` that isn't a git checkout is
   only replaced with `--replace-app` or after you say yes (kept as `app.bak-<time>`). **The atomosserver stack
   is in that state.** Then `app/deploy/docker-compose*.yml` are copied next to `.env` (a changed old copy ->
   `*.bak`).
9. **`.env`** (only if missing, mode 600): `CS_PREFIX`, `WEB_PORT`, `WEB_BIND`, `PUID`/`PGID` (the invoking user;
   1000 on Windows), `TZ` (IANA; converted from the Windows zone on Windows), `MEDIA_DIR=./data/media`,
   `YT_FORCE_IPV4=1`, then `GPU_IDS` or `COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml` (`;` as the
   separator on Windows), and `DOCKER_DEFAULT_PLATFORM` on ARM. No secrets are needed: the api makes the setup
   code. Windows writes UTF-8 without BOM, LF endings. On a re-run only what you pass (`--port`, `--bind`,
   `--gpu-ids`, `--prefix`, `--gpu`) or what the hardware forces (CPU override) is changed.
10. **Data + model.** `mkdir data/{models,media,site/api,config}`. If `data/models/robot.onnx` is missing and
    `--model PATH|URL` is given, it is copied/downloaded (checked against `--model-sha256` if given); otherwise a
    note says matches won't be processed until the file is there (the worker re-checks every 30 s).
11. **Build + start.** `docker compose build` (with `GIT_SHA` = the checkout's short hash) then
    `docker compose up -d --no-build`. `--services api,web` / `--no-worker` builds and starts only those (a later
    plain `docker compose up -d`, or the updater, starts everything). `--worker-image IMG` tags an existing image
    as `<prefix>-worker:latest` instead of building; `--use-images` builds nothing.
12. **Health.** Polls `/app/v1/health` (`"ok":true`) and `/` on the bound address for up to 3 minutes, then
    prints the LAN (and Tailscale) URL and the **one-time setup code** from `data/setup-code.txt`.
13. **Updater (Linux + systemd only).** Offers `deploy/updater/install-updater.sh` (`--updater` / `--no-updater`;
    with `--yes` the default is no). macOS, Windows and WSL: update by re-running the installer.

**Uninstall:** `install.sh --uninstall [--dir DIR]` removes the updater units (if any), runs
`docker compose down --remove-orphans --rmi all` and removes leftover `<prefix>-{worker,api,web}:*` tags; the
folder stays. `--purge` then deletes the folder after you type `DELETE` (or with `--yes`); files a container
wrote as another uid are removed through a throwaway container, then sudo. A `MEDIA_DIR` outside the folder is
left alone. `--keep-images` keeps the images. Windows: `-Uninstall [-Purge] [-KeepImages]`.

**Tests.** `bash deploy/tests/test-installers.sh` (syntax, shellcheck if installed, dry-run scenarios; works
with macOS' bash 3.2) and `pwsh deploy/tests/test-install.ps1 [-InstallAnalyzer]` (parser, PSScriptAnalyzer
incl. 5.1 syntax compatibility, dry-run scenarios). CI: `.github/workflows/installers.yml` (ubuntu, macOS,
Windows with PowerShell 7 and 5.1). Real end-to-end run without touching production, on atomosserver (the
classic builder reuses the production layers; no worker build):

```bash
ulimit -c 0; S=/mnt/nas/autoscout-scratch/agents/installer
bash install.sh --dir $S/stack --prefix cs-instest --port 7596 --bind 127.0.0.1 --cpu --no-worker --yes --no-updater
curl -s http://127.0.0.1:7596/app/v1/session      # setup_required: true
bash install.sh --dir $S/stack --worker-image cyber-scout-worker:latest --yes --no-updater   # re-run = update, all 4
bash install.sh --uninstall --purge --yes --dir $S/stack
docker images -f dangling=true                    # a build without GIT_SHA can leave 1-2 <none> images: rmi by id
```

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
| `CS_URL_ALLOW` | worker, recorder | private hosts allowed as video sources (e.g. a LAN OBS HLS server). Pasting such a link on the Video page is checked by the **api**, which doesn't get this variable: add it to the api's `environment:` too, or the paste is refused |
| `PROCESSORS_GPU0`, `PROCESSORS_GPU1`, `PROCESSOR_*`, `WORKER_TRACK` | compose, worker | extra tracking jobs, §3.1 |
| `DYNAMIC` | worker, processors | `1` / `0` forces dynamic mode on / off for every livestream source; unset = per source (§3.1) |
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
* the updater never edits `.env`, so the pause survives updates;
* the web app **says so**: the worker writes `data/worker-status.json` (`{at, paused, reason}`) on every loop pass
  (every 30 s while waiting), the api returns it in `/app/v1/session` → `processing`, and signed-in pages show
  "Processing is paused on the server" (admins get how to turn it back on), Matches says "not tracked (paused)",
  empty pages give that reason, the time control says "Latest" instead of "Live", and Admin → Settings has a
  worker status line. The same channel reports "no TBA key" and "The worker hasn't checked in since …" (no pass for
  over an hour: a stopped worker, or a very long pass). The notice goes away within about a minute of unpausing
  (the app re-reads the session every 60 s).

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
| processor-gpu0 | cyber-scout-processor-gpu0-*N* | cyber-scout-worker:latest (shared) | — | host card `PROCESSOR_GPU0_ID` (0) |
| processor-gpu1 | cyber-scout-processor-gpu1-*N* | cyber-scout-worker:latest (shared) | — | host card `PROCESSOR_GPU1_ID` (1) |

The two processor services have `PROCESSORS_GPU0` / `PROCESSORS_GPU1` replicas (default 0: not started); see
[§3.1](#31-several-tracking-jobs-process-only-processors).

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

### 3.1 Several tracking jobs: process-only processors

One worker tracks one match at a time. A backlog (a day of livestream clips, a reprocess in dynamic mode) goes
faster with **processors**: more instances of `live/worker.py` started with `--process-only` (or
`PROCESS_ONLY=1`) that do nothing but claim and track matches.

**What a processor does.**
* It takes the next match in the worker's own order: competitions on now first; in each, by time with
  `events/<ev>/priority.json` (`tools/priority.py`) first. Claims are `events/<ev>/claims/<key>.json`
  (`live/claims.py`, shared with `tools/helper.py` and the Client's "Help process matches"), so no match is tracked
  twice.
* Livestream-only mode: only clips that exist (TBA-time cuts stay with the worker). Auto mode: a trackable
  livestream clip, else the uploads once TBA posted, using the worker's `video_plan.json`.
* It writes the match outputs (`fuel/ shots/ analytics/`, then `matches/<key>.json` **last**), the kept clip
  `MEDIA/<ev>/<key>.mp4`, its state per match in `events/<ev>/proc_status/<key>.json` (never `status.json`) and a
  heartbeat `DATA/processors/<id>.json`. It reads TBA from the worker's cache `DATA/tba/` only.
* Its own `WORKER_ID` (default `<PROCESSOR_TAG>-<hostname>-<pid>`) and GPU: `GPU_DEVICE` from its environment wins
  over Admin's `gpu_device`.
* `PAUSE_PROCESSING=1` pauses processors too.
* Safety:
  - `docker stop` / SIGTERM gives the match in hand back without counting a try.
  - A processor whose heartbeat is older than `PROC_DEAD_S` (300 s; killed, container recreated) loses its
    claims to the others at once, not after `CLAIM_S` (30 min).
  - A GPU out-of-memory error is never a failed try of the match.
  - `PROC_FAIL_TRIP` (2) failed matches in a row pause that processor for `PROC_FAIL_PAUSE` (600 s). Its heartbeat
    then says `unhealthy`, and the worker tracks meanwhile.

**What the main worker does then.**
* It keeps everything else: TBA sync, site build, discovery, video plan, TBA-time cuts, reconcile, preview proxies.
* `WORKER_TRACK=auto` (default): it leaves tracking to the processors while at least one healthy processor
  heartbeat is fresh (< 3 min), and tracks itself otherwise. `1` = always track here too, `0` = never. The log
  says `match tracking left to the processors (...)` / `tracking matches here`.
* It wakes up when a match file appears and rebuilds the site at once. A claimed match shows as `processing`.
* Reconcile is driven by the match file: `source: "livestream"` and not `live.tba_posted` → reconciled when TBA
  posts, whoever tracked it.
* Proxies: kept clips without a preview proxy (processors, helper, Client) are queued on the worker's proxy
  thread (`MEDIA_PROXIES=1`).

**Dynamic mode per source.** `sources.json` source field `"dynamic"`: `true` / `false` / `null`. `null` (or
missing) = auto: dynamic when the source is a program feed (`"program_feed": true`, set by the stream
auto-setup). `live/liveingest.py` resolves it into the clip's live input (`live["dynamic"]`); `DYNAMIC=1` /
`DYNAMIC=0` in the worker's or processor's environment forces it on / off for every source
(`worker.py:dynamic_for`). Only the main worker runs the Video-page job thread (and with it the auto set-up
sweep); processors never do.

**Compose (Linux).** In `.env`:

| key | default | meaning |
|---|---|---|
| `PROCESSORS_GPU0`, `PROCESSORS_GPU1` | 0 | processor containers (jobs) on host card 0 / 1 |
| `PROCESSOR_GPU0_ID`, `PROCESSOR_GPU1_ID` | 0 / 1 | the host card of each service (`nvidia-smi -L`; with CDI: `nvidia.com/gpu=N`) |
| `PROCESSOR_GPU_DRIVER` | `nvidia` | `cdi` to pass cards by CDI name (see the GPU 1 note below) |
| `PROCESSOR_MEM_LIMIT` | 6g | per processor container (one job peaks at ~2 GB RSS) |
| `WORKER_TRACK` | auto | main worker: `auto` / `1` / `0` (above) |

```bash
cd /opt/stacks/cyber-scout
docker compose build worker                      # the processors use the worker image
docker compose up -d worker processor-gpu0 processor-gpu1
docker compose ps                                # cyber-scout-processor-gpu1-1, -2, ...
docker compose logs -f processor-gpu1            # "processor gpu1-<id>: tracking only (GPU_DEVICE=0)", per match "tracked by ..."
cat data/processors/*.json                       # heartbeats: state idle | tracking | waiting | unhealthy, current match
docker compose up -d --scale processor-gpu1=1    # change the count for now (.env for good)
```

With the CPU override the processor services keep their replicas and lose only the GPU reservation:
`PROCESSORS_GPU0=N` then means N CPU-only jobs.

**macOS server (`deploy/macos`).** Put `CS_PROCESSORS=N` in `~/CyberScout/server/server.env`, or pass it to
`install-server.sh` once. `deploy/macos/deploy.sh` re-runs keep it. That adds the LaunchAgents
`com.cyberscout.processor-1..N` (`run-processor.sh` → `run-worker.sh --process-only`, `WORKER_ID
<host>-proc<i>`, logs `logs/com.cyberscout.processor-<i>.log`). Lowering N removes the extra agents. Default 0
(unchanged). Not measured on the M2 (production): one Core ML job already keeps most of its cores busy, so try 1
and compare the per-match times in the logs before going higher.

**Measured (atomosserver, 2026-10-18; real CUDA pipeline, dynamic mode, 2026wagg 1080p60 program-feed clips
qm20 / qm35 / qm45, worker image, GPU 1 only, throwaway containers; scripts `.work/gpuscale/`).**

| jobs on one GPU | result |
|---|---|
| 1 (new model, FP16) | **6.9 matches/h**: 447 / 575 / 533 s per match (dynpose 53–303 s, detection 179–301 s, fuel 58–71 s); +282 MiB VRAM; GPU util median 0 %, p90 20 %; host CPU 34 % of 24 threads (5.2 cores mean, peaks ~10); ~2 GB RSS |
| 1 (new model, FP32) | qm20 435 s; +337 MiB VRAM |
| 2 | **out of GPU memory** (twice, FP32 and FP16): `Failed to allocate memory` / `CUBLAS failure 3` |
| 3 | not possible (needs ≥ 800 MiB) |

* **VRAM is the limit here, not the GPU or the CPU.** llama-server holds 11.4 GB of GPU 1 and 5.9 GB of GPU 0 (plus
  ollama ~1.4 GB). After the driver's reserve (~390 MiB per card) **GPU 1 has ~355 MiB free and GPU 0 7–391 MiB**
  (the other apps' use moves by ~0.5 GB). One job needs 267 MiB (FP16 model) to 337 MiB (FP32): **one job per
  card**, and GPU 0 only while the other apps leave room.
* **The job is bound by single-thread work**: the dynpose scan, the detection loop at ~20 fps, decode. FP16 vs FP32
  did not change the time (447 vs 435 s), and the GPU sits mostly idle. A second job per card would add nearly
  its full rate CPU-wise (34 % host CPU per job) once there is VRAM for it: with ≥ 1 GB free on a card, try
  `PROCESSORS_GPU1=2`.
* OCR device (`OCR_CUDA`, live/ocr.py): by default the OCR worker processes run on the CPU (no extra VRAM, 0 errors;
  qm20: 428 s, 728 reads). `OCR_PROC=0` runs OCR in a thread on the detector's CUDA context: **312 s, 730 reads, +260 MiB
  VRAM** — the faster choice when the card has room (atomosserver with its LLM services stopped). If CUDA runs out of
  memory, OCR reloads on the CPU and keeps every read (slower: one thread). `OCR_CUDA=1` forces CUDA, `0` never.
  (Before 8276dbf, `OCR_PROC=0` on a full card read 0 bumpers: the OCR's CUDA allocations failed silently.)
* For comparison, the M2 (Core ML) did 5–6 min per match in dynamic mode, about the same as one atomosserver job.

**Recommended.** Code default: no processors (`PROCESSORS_GPU0=0`, `PROCESSORS_GPU1=0`, `CS_PROCESSORS=0`), so
nothing changes. **atomosserver for a competition**: `PROCESSORS_GPU1=1`. Add `PROCESSORS_GPU0=1` only if the GPU 0
apps leave ≥ 300 MiB (a GPU out-of-memory error costs nothing but a 10-minute pause of that processor), and keep
`WORKER_TRACK=auto`. That is ~7 matches/h per job, against a match every ~7 min at an event. Use the FP16 export of the
detector on GPU hosts (identical boxes within 0.5 px, −55 MiB per job); keep FP32 on Macs (Core ML).

**GPU 1 cannot be passed into containers on atomosserver right now.** `--gpus device=1` and `device=all` fail
in the NVIDIA hook (`failed to add device rules: unable to generate new device filter program`); `device=0`
works. `/etc/cdi/nvidia.yaml` is stale: it lists `/dev/dri/card0` for GPU 1, which is `card2` since the last boot.
Fix (owner, root): `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`, then in `.env`
`PROCESSOR_GPU_DRIVER=cdi` and `PROCESSOR_GPU1_ID=nvidia.com/gpu=1` (and `PROCESSOR_GPU0_ID=nvidia.com/gpu=0` if
you use GPU 0 processors). Compose's `driver: cdi` reservation was checked to reach CDI; it fails only on that stale
`card0` node. Until then, the benchmark used a hook-free fallback: device nodes `/dev/nvidia1`, `/dev/nvidiactl` and
`/dev/nvidia-uvm*`, the host's `libcuda` / `libnvidia-ptxjitcompiler` / `-gpucomp` / `-nvvm` / `-ml` mounted into
`/usr/local/nvidia/lib64`, `LD_LIBRARY_PATH` set, and `CUDA_VISIBLE_DEVICES=<GPU 1 UUID>` (`.work/gpuscale/nvrun.sh`).

**Other knobs.** `PROC_POLL_SECONDS` (20: idle processors look for work this often, and also wake on a new
livestream record), `PROC_BEAT_S` (30: claim + heartbeat refresh while tracking), `PROC_TBA_STALE_S` (600: older
TBA cache → the processor fetches into its own cache `DATA/cache/tba-<id>` if it has the key).

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

### 8.2b Offseason event: livestream only (comp-day checklist)

For an event with **no per-match uploads, only a livestream** (TBA lists the event, teams and matches). In order:

1. **Unpause**: remove `PAUSE_PROCESSING=1` from `.env`, `docker compose up -d worker recorder`.
2. **Admin → Events → add** the event key (e.g. `2026wagg`).
3. **Video page → Match video → Livestream only.** Uploads are never looked up from now on; the checklist under it
   says what is missing.
4. **Add the stream** (the checklist's box): the YouTube `/live` or watch URL, or the Twitch channel. It is added as
   the tracked, `wide` source. If TBA lists the webcast it is discovered too; use the checklist's button to make the
   right one the tracked wide stream.
5. **Recorder** must say *recording* (a few minutes after the stream goes live; *waiting* before). Check
   `docker compose logs -f recorder` if it says error. It only records on the TBA event dates (±1 day);
   `LIVE_ANY_DATE=1` overrides for a test.
6. **Layout** (checklist → Draw boxes): grab a frame (it comes from the recorder's copy of the stream), draw the
   **field** box, and the **clock** box if the overlay clock is not at the usual FIRST position. Save.
7. **Calibration** (checklist → Calibrate): detect AprilTags, click field points, fit, check the overlay,
   **accept**. Best on a frame with the whole field empty or a match clip. The checklist turns green.
8. Matches are tracked as each one is cut from the stream (~20 s after the final buzzer). A match the analyser
   could not read off the overlay is cut by TBA's start time (needs TBA `actual_time`, i.e. the event's FMS
   syncs to TBA). Matches without a clip show **waiting for stream clip** (hover for why) on the Matches table.

If the stream is a switching broadcast (cuts between cameras) rather than a fixed wide view, tracking will be
limited: matches still get fast results (teams, score) from the overlay.

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
  `status.json` shows `"state": "failed"` for it (3 errors), remove that entry too, or it stays skipped. With
  processors (§3.1) also delete `events/<ev>/proc_status/<key>.json`: the newer of the two entries wins. Needs
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

This runs one sync pass in a second process that processes at most one new match per event and exits. The two
never track the same match (claims, `live/claims.py`), but both rewrite `status.json`: run it while the main worker
is idle, or start a process-only processor instead (§3.1). It also writes `worker-status.json` (not paused) once;
a paused main worker writes "paused" again within 30 s, so the app's notice may flicker off for that long.

### 8.9 Downloads / demo site (port 7580)

The demo and the install downloads are a separate, read-only nginx container `cyber-scout-demo` on atomosserver
port 7580 (not part of the compose stack; 8790 on that host is another app, never use it). It is **LAN-only**: no
tunnel. Publish from the Mac, in the repo:

```bash
bash site/tools/publish_downloads.sh http://10.0.0.58:7580 atomosserver /mnt/nas/autoscout-scratch/demo-site 7580
bash site/tools/publish_downloads.sh --listing-only [SITE_URL]     # only rewrite site/downloads/README.txt
```

* **`SITE_URL` is required** (no default): it is baked into `get.sh` / `get.ps1`, so it must be the address other
  computers use. It must be `http(s)://host[:port]` with no path; `PORT` defaults to its port. A private address
  (10.x, 192.168.x, 100.64/10 Tailscale, `.local`, a bare host name, …) gets a note: the one-liners only work where
  that address is reachable (the shop network or a VPN into it).
* It builds `site/downloads/` locally (`install.sh`, `install.ps1`, `get.sh`, `get.ps1`, `cyberscout-src.tar.gz` =
  `git archive HEAD`, Client builds from `client/dist/` and `client/dist/ci/`, the APK from `android/build/`,
  `SHA256SUMS`, `VERSION` = `<commit> <commit date> <site>`, `README.txt` last), rsyncs `site/` to the remote dir,
  takes `robot.onnx` and its SHA-256 from the server's own stack, and (re)starts the container with
  `site/tools/nginx.conf`. It **refuses a port** that anything but `cyber-scout-demo` is listening on.
* **Checksummed one-liners:** `get.sh` / `get.ps1` download `install.sh` / `install.ps1` and the code tarball from the
  site, check each against the SHA-256 written in at publish time ("not running it" on a mismatch), then run the
  installer with `--tarball`, `--model <site>/downloads/robot.onnx` and `--model-sha256`. They say the site must be
  reachable when it isn't.
* **`CS_SITE`** overrides the baked-in address when the site is reached another way (e.g. Tailscale):
  `curl -fsSL <addr>/downloads/get.sh | CS_SITE=<addr> bash`, or `$env:CS_SITE='<addr>'; irm <addr>/downloads/get.ps1
  | iex`. The install page adds it to the commands by itself when opened at another address than the published one.
* `nginx.conf` serves scripts, `SHA256SUMS` and `VERSION` as `text/plain` (readable in the browser before piping),
  binaries as downloads, and redirects `/downloads/` to the install guide.

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

The log line names the reason: `match processing paused (PAUSE_PROCESSING=1)`, `no TBA key` or `no events
followed`. It re-checks every 30 s, and a settings change wakes it at once. The same reason is in
`data/worker-status.json` and in the app (the notice above every page, Admin → Settings worker line). A missing
model doesn't make it wait: it logs `no detector model (…): publishing TBA + livestream results` and tracks robots
once the file appears. "The worker hasn't checked in since …" in the app means no loop pass for over an hour: check
`docker compose ps worker` and its log (a long backlog pass can also cause it).

### 9.8 Sign-in problems

Locked out: §8.6. `429` with `Retry-After` means a login rate limit: nginx allows 10/min per IP, and the API allows
10 failures per (IP, user) and 30 per IP per 15 min. An account without a password gets 401 under the default
no-password policy `never` (Admin → Activity "Off"), and under the legacy `lan` policy whenever the request comes
through the Cloudflare tunnel or any public address — by design (see [server.md](server.md)).
