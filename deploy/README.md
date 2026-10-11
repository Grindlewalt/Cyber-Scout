# Cyber-Scout

Watches FRC events on The Blue Alliance. Every time a match is played and its video
shows up on YouTube, it downloads the video, tracks all six robots from the overhead
field camera, works out which robot is which team, and publishes everything to a
scouting site: http://atomosserver:7560 (LAN/Tailscale only).

```
/opt/stacks/cyber-scout/
  docker-compose.yml   two containers: worker (GPU) + web (nginx, port 7560)
  .env                 TBA_KEY, EVENTS, POLL_SECONDS, PUID/PGID   <- back up (secret)
  app/                 the code (git: github.com/Grindlewalt/Cyber-Scout)
  data/                ALL state                              <- back up
    models/robot.onnx    the trained robot detector (irreplaceable-ish: ~2 h to retrain)
    events/<key>/        camera.json (calibration), matches/*.json (processed tracks),
                         status.json, overrides.json (manual team fixes)
    site/api/            generated site JSON (rebuilt automatically)
    training/            public datasets (from Roboflow, once), event labels, checkpoints
    tba/ cache/          disposable
```

## Restore on a fresh machine

1. Install Docker (+ NVIDIA container toolkit if there's an NVIDIA GPU).
2. Copy the backed-up `/opt/stacks/cyber-scout` folder back into place.
3. `cd /opt/stacks/cyber-scout && docker compose up -d --build`

That's it. Already-processed matches are not redone (they're in `data/events/*/matches`).
No GPU? Delete the `deploy:` block in `docker-compose.yml`; it runs on CPU, ~4x slower.

## Day to day

```bash
docker compose logs -f worker          # what it's doing
docker compose restart worker          # pick up .env changes
```

* **Follow a different / live event:** set `EVENTS=2026xxxx` (comma-separate several) in
  `.env`, then `docker compose up -d`. The event needs a camera calibration first (below).
* **A robot is labelled as the wrong team:** add to `data/events/<event>/overrides.json`
  ```json
  {"2026wasam_qm12": {"red": {"0": "frc1983", "1": "frc9442", "2": "frc2903"}}}
  ```
  keys are the track numbers (`idx`) shown in the match JSON (`data/site/api/<event>/m/<match>.json`).
  The labelling re-solves the whole event on the next poll, so one fix also improves other matches.
* **Re-process a match:** delete `data/events/<event>/matches/<match>.json`.

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
