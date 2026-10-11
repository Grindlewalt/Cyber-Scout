# autoscout-live v2 — fully autonomous quantitative scouting

Goal: from the overhead broadcast camera alone (+ TBA), produce every data point a
scouting team would want about every robot, with honest confidence on each.

## What already exists (v1)

* `live/process.py` → per-match JSON (`data/events/<ev>/matches/<match>.json`), schema:
  ```
  t_start            video seconds of match start (auto start)            float
  frames, hz         replay frames (30 Hz internally, exported at hz=10)
  t_auto_end, t_teleop_start   seconds from replay frame 0 (frame 0 = t_start - 0.5 s)
  feed_ok, feed_gaps fraction of match with live camera; [[t0,t1],...] seconds from frame 0
  camera_pose        {rvec,tvec} actually used for this match (camera can be bumped)
  tracks[6]: {alliance:"red"|"blue", idx, xy:[[x_cm,y_cm]...] @hz, obs:[0/1...] @hz,
              stats:{...}, feat:{height, hist, start_xy, end_xy, auto_end_xy, ...}}
  video: {id, title, channel}
  ```
  Video time of replay frame i = `t_start - 0.5 + i / hz`.
* Team labels: `live/identity.py` (event-level). Site JSON `data/site/api/<ev>/m/<match>.json`
  has `tracks[k].team`, `id_conf`.
* Camera: `live/camera.py` `Camera(dict_or_path)` — `project(Nx3 field m)->Nx2 px`,
  `img_to_plane(uv, Z)`, `img_to_ground`, `cov`, `C` (camera centre, field m). Full-frame
  1920x1080 px, OpenCV lens model. Calibration `events/2026wasam/camera.json`; per-match pose
  override = `camera_pose` in the match JSON (use it!).
* Field frame: metres, X 0..16.541 (blue alliance wall at X=0, red at X=16.541),
  Y 0..8.069 (Y=0 = far wall in the image, Y=8.069 = near/camera side). Red hub centre
  (11.915, 4.035), blue hub (4.626, 4.035). Red end is image-LEFT. Field is point-symmetric.
* TBA per alliance per match (`score_breakdown`): `hubScore.{autoCount, transitionCount,
  shift1..4Count, endgameCount, teleopCount, totalCount, uncounted}` — FUEL counted per period;
  `uncounted` = fuel into an inactive hub. Per robot (station 1..3 = `team_keys` order):
  `autoTowerRobotN`, `endGameTowerRobotN` (None/Level1/2/3).
* Videos: 1920x1080 **60 fps**, YouTube id in `video.id` (`yt-dlp -f "bv*[height<=1080][ext=mp4]"`).

Samples for development (local, read-only): `.work/samples/matches/*.json` (processed),
`.work/samples/api/2026wasam/{event.json,m/*.json,t/*.json}` (with team labels),
`.work/samples/tba/event__2026wasam__matches.json` (TBA, includes score_breakdown),
`.work/vids/qm12.mp4`. Python: `.venv/bin/python` (numpy, scipy, opencv, av, networkx,
onnxruntime). Put scratch output in `.work/<your-name>/`.

## Metric catalogue (per robot per match → aggregated per team)

Every metric carries the coverage it was computed on (feed_ok, observed %, n events).

**A. Scoring (fuel, from shot reconstruction)**
shots attempted / made / missed; accuracy; by period (auto, transition, shift1–4, endgame)
and by hub state (active vs inactive → "uncounted"); estimated points; fire rate inside a
volley (balls/s); volley size (≈ hopper capacity = max balls per volley); volleys per match;
time between volleys (cycle time); shot distance + bearing to hub; shooting-location
heatmap; shoot-on-the-move rate (robot speed at launch); launch speed / angle consistency
(mechanism repeatability); miss pattern (short/long/left/right); time to first shot (auto,
teleop); share of alliance fuel; traced-vs-TBA reconciliation per alliance-period.

**B. Feeding / passing** — launches not aimed at a hub that land in own alliance zone or a
teammate's area (ferrying from neutral/opp zone): passes, pass origin/landing maps; role.

**C. Collection** — ground pickups (floor fuel vanishing at a robot), intake locations
(neutral / depot / outpost chute / opponent side), intake rate, time spent collecting,
fuel per cycle.

**D. Human player** — throws/feeds from the outpost; attributed to the alliance's HP.

**E. Movement** — trajectories, distance, speed profile (mean moving speed, p95, max accel),
% time moving, zone time + transitions, mid-field crossings by lane (BUMP vs TRENCH vs open,
near vs far side) and preference relative to the team's driver station, trench-capable
(ever drove through a trench), typical lanes.

**F. Autonomous** — start position (and station), path; per-team routine clustering
(distinct autos, frequency, which used when); auto distance; crosses into neutral zone;
auto fuel shot/made; auto climb (TBA); auto end position; consistency.

**G. Defense / counter-defense** — time defending (in opponent half, close to and
shadowing an opponent), contacts (opposing robots < ~1.0 m), pins (sustained contact,
victim nearly stopped), opponent fire-rate / cycle time when defended vs not (defense
effectiveness), being defended: own rate retained, evasion (path deviation / escape time).

**H. Endgame / climb** — climb level (TBA), time arrives at tower, climb duration, time it
stops scoring to go climb, attempted-but-failed climbs (at tower, TBA None).

**I. Reliability** — dead / disabled detection (no movement ≥ 15 s mid-match, not climbing),
stalls, matches with low tracking coverage.

**J. Strategy / teamwork** — role classification (scorer / feeder / collector / defender /
hybrid), alliance congestion (teammates < 1.5 m), consistency + trend across the event.

**K. Field-level** — floor-fuel distribution over time, hub-active timeline, where fuel
piles up.

## Module ownership (one owner per file; do NOT edit files you don't own; do NOT git commit)

| file | owner | contract |
|---|---|---|
| `live/field/rebuilt_rules.json` (+ `.md`) | game-facts | measured/manual geometry + timing (below) |
| `live/fuel.py` | fuel-tracker | `track_fuel(video, cam, t_from, t_to, log) -> FuelResult` |
| `live/shots.py` | shot-reconstruction | `reconstruct(fuel, match, cam, rules, log) -> Events` |
| `live/analytics.py` | robot-analytics | `match_analytics(match, events, rules, tba_match) -> dict`; `team_aggregate(list) -> dict` |
| `live/identity.py`, `live/verify_ids.py` | identity | same interfaces as v1 |
| `web/*` | web | consumes the site JSON below |
| `live/process.py`, `live/worker.py`, deploy | lead (integration) | |

### rules.json (game-facts)
```
{ "fuel_diameter_m", "hub": {"centers": {"red":[x,y],"blue":[x,y]}, "opening_height_m",
  "opening_shape", "opening_size_m", "footprint_m"}, "bumps": [...polygons...],
  "trenches": [...polygons + clearance_m...], "towers": {"red":{...},"blue":{...}},
  "outposts": {...}, "depots": {...}, "zones": {"red_alliance_x_min", "blue_alliance_x_max", ...},
  "periods": [{"name":"auto","t0":0,"t1":20}, {"name":"transition",...}, ...]  (seconds from auto start),
  "hub_active_rules": "...", "sources": [...] }
```

### FuelResult (fuel.py)
```
{ "fps": 60, "t_from", "t_to",
  "tracks": [ {"id", "f": [video frame idx...], "uv": [[u,v]...], "r": [px radius...],
               "moving": bool, "airborne_score": 0..1} ],      # moving fuel only
  "floor": {"t": [...], "counts_by_zone": {...}, "heat": [[...]]}  # optional, coarse
}
```
### Events (shots.py)
```
{ "shots": [ {"t": video s, "match_t": s from auto start, "period", "shooter":
     {"kind":"robot"|"human"|"unknown", "alliance", "track_idx"},
     "origin": [x,y,z], "v0": [vx,vy,vz], "landing": [x,y], "target": "red_hub"|"blue_hub"|null,
     "outcome": "made"|"missed"|"pass"|"unknown", "hub_active": bool|null,
     "fit_rms_px", "n_obs", "conf"} ],
  "intakes": [ {"t","match_t","track_idx","xy","conf"} ],
  "reconcile": {"red": {"auto": {"traced_made", "tba"}, ...}, "blue": {...}} }
```
### Site JSON additions (worker writes, web reads)
match: `shots[]` (above, plus `team`), `analytics[track_idx]`; team: `analytics` aggregate,
`shots_map`, `intakes_map`, `autos[].routine`, `routines[]`.

## v2.1 additions (tracker v2 + quarantine)
- Match JSON tracks are ordered red 1–3, blue 1–3 (TBA station order) with `idx` = position, and carry
  `team` ("frcNNNN"), `team_conf` (0..1 overall), `conf` (per frame @ hz; 0 = unknown/limbo: obs=0 and the xy is
  only the nearest known position). Match-level `identity` = {method "ocr-ilp", n_reads, n_reads_raw,
  per_team_reads, n_tracklets, n_cuts, seconds, corrections[{kind cut|relink, frame, t, t_video, teams, tracks,
  evidence [[frame, team, lp, weight]], encounter {dist_m, xy?}, motion_link?, conf, anchored, unresolved?}]}.
- Quarantine (ID_CONF_MIN, default 0.5): analytics run on a copy where frames with conf < 0.5 are unobserved;
  site shots launched while the shooter's conf < 0.5 get `team: null, unattributed: "low_identity_conf"`.
- Site match JSON adds `identity`, tracks[].team_conf/conf/reads; event/team files flag matches with team_conf < 0.6.
- Production entry: process(video, cam, onnx, tag_csv, log, start_hint, pose_guess, teams={red:[...],blue:[...]}).
- Videos are kept in MEDIA_DIR (/media in the containers) and served at /media/<ev>/<match>.mp4;
  live/reprocess.py re-runs matches from those files without YouTube.
