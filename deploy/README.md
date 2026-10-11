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
prints the URL and a **one-time setup code**. Re-run it any time to update. Options: see the top of `install.sh`
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
  docker-compose.yml   worker (GPU) + api (accounts/settings) + web (nginx, port 7560)
  docker-compose.cpu.yml   CPU-only override (enabled via COMPOSE_FILE in .env)
  .env                 infra only: WEB_PORT, PUID/PGID, TZ, MEDIA_DIR
  app/                 the code (git: github.com/Grindlewalt/Cyber-Scout)
  data/                ALL state                                  <- back up
    app.db               accounts, settings (incl. the TBA key), picklists, notes   (secret)
    config/worker.json   the worker's copy of the settings (written by the api)     (secret)
    models/robot.onnx    the trained robot detector
    events/<key>/        camera.json (calibration), matches/*.json (processed tracks),
                         status.json, overrides.json (manual team fixes)
    site/api/            generated site JSON (rebuilt automatically; served to signed-in users)
    media/               kept match videos (unless MEDIA_DIR points elsewhere)
    training/ tba/ cache/   disposable / re-downloadable
```

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
  --build`; on first start the api imports `TBA_KEY` and `EVENTS` from `.env` once, then run setup in the browser
  (setup code in `data/setup-code.txt`) to create the admin. After that, manage them in the app.

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
