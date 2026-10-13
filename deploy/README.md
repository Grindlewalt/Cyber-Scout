# Cyber-Scout

Watches FRC events on The Blue Alliance. Every time a match is played and its video
shows up on YouTube, it downloads the video, tracks all six robots from the overhead
field camera, works out which robot is which team, and publishes everything to a
scouting site that your team signs in to.

## Install (any Linux box with Docker; NVIDIA GPU recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/Grindlewalt/Cyber-Scout/main/deploy/install.sh | bash
```

The installer checks Docker + Compose (and the NVIDIA Container Toolkit; without it it warns and runs CPU-only,
~4x slower per match), clones the code into `/opt/cyber-scout/app` (asks; or `CS_DIR=...`), writes `.env` with
infrastructure settings only (port, file owner, time zone, media dir), runs `docker compose up -d --build`, and
prints the URL and a **one-time setup code**. At the end it offers to install the **updater** (one-click
updates in Admin -> Updates, see below). Re-run it any time to update. Options: see the top of `install.sh`
(`CS_PORT`, `CS_MODEL_URL` for the robot detector, `CS_CPU=1`, `CS_YES=1`, ...).

## First run

Open the printed URL (LAN or Tailscale; plain HTTP is fine). The setup wizard asks for:

1. the setup code (`cat /opt/cyber-scout/data/setup-code.txt` if you lost it) and an **admin account**;
2. a **TBA Read API key** (thebluealliance.com/account; checked with TBA, stored only on the server);
3. your **team number**, 4. your **season goal** (win an event / DCMP / Worlds / ...);
5. the **event(s)** to follow (your team's events listed first) -> then set up the video for each event.

Then **Admin -> Teammates**: give each teammate a login and a role
(viewer = read-only, scout = + notes and identity fixes, strategist = + shared picklist, admin = everything).
Everything after that (key, team, events, GPU / hub-cam options) is changed in **Admin**, not in files; the worker
picks changes up on its next poll without a restart.

```
/opt/cyber-scout/
  docker-compose.yml   worker (GPU) + recorder (live streams) + api (accounts/settings) + web (nginx, port 7560)
  docker-compose.cpu.yml   CPU-only override (enabled via COMPOSE_FILE in .env)
  .env                 infra only: WEB_PORT, PUID/PGID, TZ, MEDIA_DIR (+ optional api settings, below)
  app/                 the code (git: github.com/Grindlewalt/Cyber-Scout)
  data/                ALL state                                  <- back up
    app.db               accounts, settings (incl. the TBA key), picklists, notes   (secret)
    config/worker.json   the worker's copy of the settings (written by the api)     (secret)
    models/robot.onnx    the trained robot detector
    events/<key>/        camera.json (calibration), matches/*.json (processed tracks),
                         status.json, overrides.json (manual team fixes)
    site/api/            generated site JSON (rebuilt automatically; served to signed-in users)
    media/               kept match videos (unless MEDIA_DIR points elsewhere)
      live/<event>/<video>/  live-stream recordings: clips/, matches/ (records), seg/ (trimmed automatically)
    training/ tba/ cache/   disposable / re-downloadable
```

## Updates (Admin -> Updates)

With the updater installed (`install.sh` offers it; or later, in the stack folder:
`sudo ./app/deploy/updater/install-updater.sh`), an admin clicks **Check for updates**, reads the changelog and
clicks **Update now**. A small systemd service on the server (not a container: no container gets the docker
socket) fetches the code, backs up `data/app.db`, rebuilds, restarts, waits until everything is healthy, and
rolls back by itself if anything fails. **Roll back** returns to the previous version. It refuses when the disk is
too full for the rebuild, never edits `.env` (PAUSE_PROCESSING stays as it is), and can also re-pin yt-dlp.
Private repository: a read-only deploy key. Details, the security model and removal:
[`updater/README.md`](updater/README.md).

## Backup and restore

**Backup = the install folder** (`/opt/cyber-scout`, really just `data/` + `.env`). `data/app.db` holds the TBA
key and password hashes, so keep backups private. For a consistent copy of the database while running:
`sqlite3 data/app.db ".backup data/app.db.bak"` (or stop the stack first: `docker compose stop`).

Restore on a fresh machine: install Docker (+ NVIDIA container toolkit), copy the folder back, then
`cd /opt/cyber-scout && docker compose up -d --build`. Already-processed matches are not redone.

## Security notes

* Only the login / setup shell is public; site JSON (`/api/`) and videos (`/media/`) need a signed-in user
  (nginx `auth_request` against the api). The api container isn't published on any port.
* Sessions: http-only, SameSite=Lax cookies, CSRF token on every change, 7 days idle / 30 days max; logins are
  rate-limited; passwords are argon2id hashes, 10+ characters.
* Meant for a LAN / Tailscale. Behind a TLS reverse proxy (Caddy, Tailscale serve, ...) forward to the web port and
  pass `X-Forwarded-Proto: https`; cookies then get `Secure` automatically. Note: the login rate limit is per
  client IP, which behind a proxy is the proxy's.
* Locked out? On the server: `docker compose exec api python -m server.manage users`, then
  `... server.manage reset-password <username>` (prints a new password) or `... server.manage make-admin <username>`.

## Day to day

```bash
docker compose logs -f worker          # what it's doing
docker compose logs -f api             # sign-ins / settings problems
docker compose up -d --build           # after changing .env or pulling code (or just re-run install.sh)
python3 updater/cs-updater.py --stack $PWD check | apply | rollback    # the updater from a shell (if installed)
```

* **Follow a different / live event:** Admin -> Events. The event needs a camera calibration / video setup first.
* **A robot is labelled as the wrong team:** a scout fixes it in the app (`PUT /app/v1/events/<ev>/overrides/<match>`),
  which writes `data/events/<event>/overrides.json`; hand edits still work:
  ```json
  {"2026wasam_qm12": {"red": {"0": "frc1983", "1": "frc9442", "2": "frc2903"}}}
  ```
  keys are the track numbers (`idx`) shown in the match JSON (`data/site/api/<event>/m/<match>.json`).
  The labelling re-solves the whole event on the next poll, so one fix also improves other matches.
* **Re-process a match:** delete `data/events/<event>/matches/<match>.json`.
* **Upgrading a stack that predates accounts** (settings in `.env`): add the new compose file, `docker compose up -d
  --build`, then run setup in the browser (setup code in `data/setup-code.txt`) to create the admin and enter the TBA
  key and events there. The api container does **not** receive `TBA_KEY` / `EVENTS` from `.env` (see "API settings"
  below), so the old one-time import only happens if you add them to the api's `environment:` yourself for one start.

## Live streams: results without waiting for YouTube

The `recorder` service records each followed event's live stream while the event is on (its dates on TBA) and
cuts every match out of it about 20 s after the final buzzer, with teams, score, and start and end times read
off the broadcast overlay. The final score comes from the results screen, so it includes penalties. The worker
uses these clips right away. It does not wait for the per-match YouTube uploads or for TBA to post the result.

* **Which stream:** the event's livestream source on the **Video page**. TBA webcasts are discovered as
  livestream sources. If there are several, the recorder uses the one marked primary or *wide*, then the
  webcast dated today, then one you added. One stream is recorded per event.
* **Fast results (always):** each match row on the site gets `live` data: stream teams, score, start/end time,
  and a link to the clip (`/media/live/...`). Until TBA posts, the row shows the stream's score
  (`score_source: "livestream"`, state `live`). Playoff matches TBA hasn't created yet are listed from the
  stream too.
* **Robot tracking from the stream** works only when the stream shows a **fixed field view the tracker is
  calibrated for**. That needs both of these:
  1. a **field** (or **overhead**, if it is an inset) box on the livestream source in the layout editor, and
  2. a calibration of that camera: `data/events/<ev>/camera_<source id>.json`. If you ran the calibration
     wizard with the livestream as the primary source, its `camera.json` also counts.

  Most event streams are a switching program feed with no fixed overhead camera (Bordie 2026 was one wide
  oblique stands camera). Those get **fast results only**, and each match is tracked from its per-match
  upload once that appears, as before. When the stream *is* trackable, a match is tracked as soon as its clip
  exists. Teams come from TBA's schedule, or from the stream if TBA has no lineup yet. When TBA posts, the
  worker checks the lineup (if it differs, the match is re-tracked) and re-runs the shot analysis with TBA's
  score breakdown. If the stream clip turns out to be the wrong match, or fails twice, the upload path takes
  over.
* **Status:** `GET /app/v1/events/<ev>/livestream` (any signed-in user), from
  `data/events/<ev>/livestream.json`. It reports the state (recording, waiting, stream_over, idle, error,
  no_source, disabled), how far it is behind live, analyser progress, the last match detected with recent
  matches, disk use, errors, and whether the stream is trackable. `docker compose logs -f recorder` shows
  the same.
* **Surviving problems:** the recorder fetches the stream's 5 s chunks by sequence number. After a network
  drop, an expired URL or a container restart it resumes where it stopped and fills the gap (YouTube keeps
  the chunks). A crashed recorder is restarted with back-off. A channel `/live` URL that moves to a new
  video starts a new recording directory.
* **Disk** (NAS, `MEDIA_DIR/live`): about 2 GB per hour of stream. Each stream is held under
  `LIVE_BUDGET_GB` (default 60). Non-match footage goes after 2 h, match footage after 12 h, and clips are
  kept. Segments of finished streams are deleted after 2 days, or sooner when the disk has less than
  `LIVE_MIN_FREE_GB` (default 30) free.
* Settings (`.env`): `LIVESTREAM=0` turns the recorder off. Also `LIVE_BUDGET_GB`, `LIVE_MIN_FREE_GB`,
  `LIVE_BACKFILL_MIN` (on first start, also record this many minutes the stream already played; default 60),
  and `LIVE_DIR` (default `MEDIA_DIR/live`).

## Sync & clients

Laptops (Cyber-Scout Client) and phones (offline mode of the web app) keep a full local copy of each event's
data and queue changes while offline; the contract is `docs/SYNC.md`. The server side is part of the `api`
service, nothing extra to run:

* `GET /app/v1/info` (no sign-in): name + version, used by "Test connection". The version is the git short sha
  when the image is built with `GIT_SHA=$(git -C app rev-parse --short HEAD) docker compose build`, else the
  contents of an `app/VERSION` file, else `src-<hash of the code>` (still changes on every code change).
  `SERVER_NAME=...` in `.env` sets the display name (default "Cyber-Scout · Team <n>").
* `/app/v1/sync/*` (any signed-in role): event list, per-event file manifest with delta cursors, bundles (one
  gzip tar for many files, resumable), media list. Bundles are cached in `data/cache/sync/bundles` (6 h, at
  most `SYNC_CACHE_MB`, default 256 MB, oldest first) and the manifest index in `data/cache/sync/index`; both are
  disposable. A full event (~340 files, 95 MB of JSON) is ~20 MB gzipped in two bundles.
* The api container mounts `MEDIA_DIR` **read-only** at `/media` to list videos; nginx still serves the files.
* **Media proxies**: after a match's video is kept, the worker writes a small copy for clients,
  `MEDIA_DIR/<ev>/proxy/<match>.mp4` (≤480p, ~1 Mb/s, no audio, ~27 MB per match). Settings (`.env`):
  `MEDIA_PROXIES=0` turns it off; `PROXY_GPU` (default `1`, GPU index *as the worker container sees it*) and
  `PROXY_ENCODER=auto|cpu|nvenc`. Without a working NVENC it uses 2 CPU threads, niced (~25 s per match, in the
  background). For matches processed before this existed (does not touch anything else):

      docker compose exec worker python -m live.proxies /data /media 2026wasam            # whole event
      docker compose exec worker python -m live.proxies /data /media 2026wasam --only qm12 f1m1

  Disk: ~2 GB per 75-match event on `MEDIA_DIR`.
* Writes from clients carry `Idempotency-Key`; the API remembers answers for 48 h per account (table
  `idempotency` in `data/app.db`), so a change resent after a dropped connection is applied once.

## How it works

1. `live/worker.py` polls TBA (ETag-cached) for played matches and picks the full-frame
   field-camera video (FIRST Washington "Wide Match N", else FIRST HQ's per-match upload),
   configurable per event via `events/<key>/event.json` (`prefer`, `reject`, `allow_channels`).
2. `live/process.py`: checks the camera hasn't been bumped (AprilTags + the white centre
   line; re-solves the pose if it moved, and carries it to the next match), runs the YOLO
   robot detector (ONNX, GPU) at 30 Hz on the field, reads the match start off the broadcast
   clock (or robot motion), and tracks with the tracker from autoscout (`src/track.py`, `fit6.py`,
   `assign.py`; wide-camera path of `fuse_track.py` in `live/fuse.py`).
   Broadcast glitches are handled: red/blue-swapped frames are un-swapped, and stretches where
   the field camera is missing from the video are marked (the next upload of the same match is
   tried; stats only count filmed time; the site shows "partial footage").
3. `live/identity.py` labels robots with teams by solving the whole event at once: a team's
   robot looks the same, starts in a similar spot and climbs (or not, per TBA) in every match.
4. The worker writes `data/site/api/**.json`; `web/` is a static app that reads it.

## Adding a new event (camera calibration, ~15 min, once per venue)

The geometry (pixels <-> field metres) is per camera. `events/<key>/camera.json` is fitted by
`live/calibrate.py` from `events/<key>/calib_input.json`:

* `tags`: pixel centres of field AprilTags (`id: [u, v, sigma_px]`), auto-detected on a
  median "plate" of a match video and/or clicked by hand;
* `lines`: pixel samples along straight world lines: the white centre line, and the top
  rails of the far and near guardrails.

```bash
python live/calibrate.py events/<key>/calib_input.json events/<key>/camera.json
```

A good fit has tag residuals of ~1 px and line residuals under 1 px, and recovers a rail height
of about 0.5 m. Project the field grid back onto the plate to check (see `2026wasam` for a
worked example). If the broadcast uses a different layout (e.g. picture-in-picture station
cams over the field), set `reject` in the event's `event.json` so those videos are skipped.

The detector was trained on public FRC robot datasets plus auto-labelled footage from this
venue (`live/make_labels.py`). A very different camera angle may need the same fine-tune.

## API settings (.env)
The api container has no `env_file`: compose passes it an explicit allowlist of `.env` values, each with the default
from `server/config.py`, so nothing else in `.env` (a legacy `TBA_KEY`, worker options) reaches it:

| variable | default | meaning |
|---|---|---|
| `SESSION_IDLE_DAYS` | `7` | signed out after this many days unused |
| `SESSION_MAX_DAYS` | `30` | signed out after this many days regardless |
| `PASSWORD_MIN` | `10` | admin password minimum length |
| `MEMBER_PASSWORD_MIN` | `4` | minimum for other accounts when they set a password |
| `SETUP_CODE_REQUIRED` | `1` | `0` = first-run setup without the code in `data/setup-code.txt` |
| `COOKIE_SECURE` | `auto` | `1` / `0` / `auto` (Secure when the proxy sends `X-Forwarded-Proto: https`) |
| `TZ`, `PUID`, `PGID` | `UTC`, `1000`, `1000` | as for the other services |

Change one in `.env`, then `docker compose up -d api` (recreates the container). A new api setting must be added to
the api service's `environment:` in `docker-compose.yml`.

## Hardening options (.env)
- `GPU_IDS` (default `0`; e.g. `1` or `0,1` — numbering from `nvidia-smi -L`; inside the container the cards count from 0, so leave `GPU_DEVICE=0`).
- `WEB_BIND` (default `0.0.0.0`): set a LAN or Tailscale IP to publish the site only there (an explicit address means no IPv6 listener).
- `WORKER_MEM_LIMIT`, `RECORDER_MEM_LIMIT`, `API_MEM_LIMIT`, `WEB_MEM_LIMIT`: per-container memory caps.
- `CS_URL_ALLOW`: comma-separated hosts/CIDRs the worker may fetch even though they're private (e.g. a LAN OBS HLS server). Otherwise video sources must be public http(s) URLs or absolute paths under the media/data directories.
