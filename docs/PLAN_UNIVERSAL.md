# Cyber-Scout "universal" plan

Goal: any FRC team can spin Cyber-Scout up on their own hardware, sign in, paste a TBA key, pick their team and
event, point it at the video (auto-found or pasted), draw the layout once, and get autonomous quantitative scouting +
strategy for the whole event — with accounts for teammates.

## Product surface

1. **Install + setup wizard** — one install script (`curl … | bash`: checks Docker / NVIDIA toolkit, writes the
   stack, starts it). First visit: create the admin account → TBA key (validated) → your team number → season goal
   → pick an event (TBA list, filtered to your team's events first).
2. **Accounts + roles** — admin (everything incl. users, keys, video setup), strategist (picklists, notes, plans,
   overrides), scout (notes, identity fixes), viewer (read-only). Admin creates teammates; each has their own login.
   Shared, server-side picklists / notes / overrides (no more per-browser localStorage).
3. **Video sources** — auto-discover from TBA (`/event/{key}` webcasts + per-match video links); admin can paste a
   livestream URL and any alt-angle streams/uploads. Per source: type = *wide overhead* | *dynamic broadcast*
   (switching cameras) | *alliance-side* (station cams), and which matches it covers.
4. **Layout editor** — on a sample frame per source, draw boxes: overhead field area, red-station inset, blue-station
   inset, scoreboard/clock overlay, any area to mask. Saved as `layout.json` per event+source; the pipeline reads it
   instead of today's hard-coded Sammamish rectangles. Then a **calibration wizard** per camera: auto AprilTag
   detection + a few clicked field points → fit → overlay check ("does the grid sit on the field?") → save.
5. **Pages**
   - **Dashboard** (exists): next match plan, ratings, projections, replay.
   - **Schedule**: upcoming + played matches, predicted win/loss with where you'll be beaten
     (auto / teleop by shift / endgame) and by how much → what you need to make up.
   - **Matches**: watch any match (Analyze view: video + layers, camera switcher) with full results.
   - **Teams**: spec sheet per team — speed, cycle, accuracy, fire rate, hopper/volley size, autos, climb, lanes
     (trench / bump, left / right), **shooting locations** and **shooting paths** (the drive leading into each
     volley, so you see where/when they start shooting), defense played/received.
   - **Rankings & goals**: live rankings + your RP; district points now / projected; **season goal** (e.g. qualify
     for DCMP / Worlds) with the points still needed, odds, and what each remaining match is worth (TBA district
     points + Statbotics/EPA when up, our simulator otherwise).
   - **Defense library** (big): every defense episode on a scorer, scored by how much it actually cut their scoring
     rate vs their baseline; the successful ones as short **top-down tracking clips** (+ jump into the real video),
     filterable by target / defender / location / shift → "what works against team X".
6. **Runs on the host** — everything (processing, OCR, sims) on the machine it's installed on; GPU optional but
   recommended (CPU-only is ~4x slower); the worker reads all config from the app, not from `.env` files.

## Architecture changes
- New `api` service (Python, FastAPI + SQLite in `data/app.db`): auth (argon2 password hashes, http-only session
  cookies), users/roles, settings (TBA key stored server-side, never sent to browsers), events, video sources,
  layouts, calibrations, picklists, notes, overrides. nginx routes `/app/v1/*` to it and gates `/api/*` (site JSON)
  and `/media/*` behind a session check (auth_request). Worker polls the API/DB for configured events + sources.
- Web: plugin routes — each feature in its own `web/*.js` registering via `CS.route()` / `CS.nav()` (see app.js).
- Pipeline generalisation: layout.json-driven crops/overlay masks (process, fuel, stationcam, livestream, OCR of the
  scoreboard), per-event calibration files written by the wizard, sources table instead of the hard-coded
  "Wide Match N" preference.

## Work split (4 agents, file ownership)
| Agent | Owns | Delivers |
|---|---|---|
| A. Platform (opus) | `server/` (new API service), `deploy/` (compose, nginx, install script), `web/setup.js`, `web/admin.js` | accounts/roles, setup wizard, settings, auth gating, install script, worker config from DB |
| B. Video sources + layout + calibration (opus) | `live/sources.py`, `live/layout.py`, `web/sources.js`; layout-driven changes in stationcam/livestream/process/fuel (additive, default = current behaviour) | source discovery/paste/types, box editor, calibration wizard, pipeline reading layout.json |
| C. Competition & goals (sonnet) | `live/strategy/district.py`, `goals.py`, `web/rankings.js`, `web/schedule.js` | district points (manual rules), DCMP/Worlds goal odds + points needed, schedule with per-period where-you'll-be-beaten |
| D. Scouting intelligence (opus) | `live/scouting.py`, `web/teams2.js`, `web/defense.js` | team spec sheets, lanes, shooting locations + shooting paths, defense-episode detection + effectiveness + top-down clips |
| Lead | integration, `live/worker.py`, `web/app.js` router, reviews, deploys | |

Rules for all: dev set only (2026wasam qm1, qm10–19; showcase qm17 / team 4911) — no full reprocessing; heavy
compute on the server (GPU 1); never touch production `/opt/stacks/cyber-scout` except via the lead; no commits.
Friend's Pit Wall (district/DCMP engine) can replace C's math if its author licenses it; C implements from the
public game-manual rules meanwhile.
