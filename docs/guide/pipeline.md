# The video pipeline

How one played match becomes tracked robots, identities, shots and site JSON. Sections 0–6
cover the worker loop, video selection, camera pose, detection, tracking and identity, the
station cameras, and the offline tools. Sections 7–14 cover fuel tracking, shots, intakes,
analytics, enrichment, site JSON and livestream ingest.

Function references are `file.py:function`. All paths are relative to the repo unless they
start with `DATA/` (the `data/` dir of the stack, `/data` in the containers) or `MEDIA/`
(`MEDIA_DIR`, `/media` in the containers; on atomosserver `/mnt/nas/autoscout-media`).

> **In progress at time of writing.** Another agent is changing `live/worker.py` for
> competition use, and another one is changing `live/sitev2.py` for performance. Sections 0–6
> describes `live/worker.py` as committed at the documented HEAD. Re-check `sync()`,
> `build_site()` and `process_match()` against the current file before you rely on line-level
> detail.

---

## 0. Overview

### 0.1 Stages per match

1. **Poll.** `worker.py:main` reloads its config (`load_config`) and, for every followed
   event, calls `sync()`. That fetches TBA matches (ETag-cached) and merges in any
   livestream-only matches.
2. **Pick a video.** `sources.py:video_config` turns the admin's Video page into a config.
   `videos.py:candidates` uses it to rank TBA-linked YouTube uploads (by default "Wide Match
   N" first, then the FIRST channels). If a livestream clip of the match exists and the stream
   camera is calibrated, the clip is tried first.
3. **Download.** `videos.py:download` runs yt-dlp over IPv4, video only, ≤1080p, with 4
   attempts, into `DATA/cache/<match>_<ytid>.mp4`.
4. **Camera pose.** `process.py:check_pose` looks for AprilTags in about 28 frames. If the
   camera was nudged, it re-solves the pose with the tags plus the white centre line, starting
   from the last verified pose (`DATA/events/<ev>/pose_latest.json`).
5. **Detect.** `process.py:detect_video` runs the YOLO ONNX detector (`detector.py:Detector`)
   at 30 Hz over the field ROI. Along the way it fixes colour-swapped frames, marks feed gaps
   and samples the clock box. The same pass feeds the bumper OCR sampler (`ocr.py:OcrSampler`).
6. **Match start.** `process.py:pick_start` takes the broadcast clock ticks first. Next comes
   a measured livestream start, then robot motion, then a per-channel hint or the default.
7. **Track + identity.** `fuse.py:run_labeled` builds Kalman tracklets, cuts them where the
   reads change team, merges duplicates, then runs one 6-team ILP with "limbo" and assigns
   per-frame confidence. The result is six tracks, one team each. The v1 fallback,
   `fuse.py:run`, produces unlabelled tracks.
8. **Integrity.** `integrity.py:check` scores the open-set reads against every lineup at the
   event. If they say "another match's video", the worker tries the next upload.
9. **Save** `DATA/events/<ev>/matches/<match>.json`.
10. **Enrich (v2, §7–11).** `enrich.py:enrich` runs fuel tracking (`fuel.py`), then shots
    (`shots.py`), then intakes (`intakes.py`), then analytics (`analytics.py`, on a
    quarantined copy). Output goes to `DATA/events/<ev>/{fuel,shots,analytics}/<match>.json`.
11. **Keep the video** at `MEDIA/<ev>/<match>.mp4` (served at `/media/...`).
12. **Station pass (optional, `HUBCAM=1`).** Downloads the "Match N" inset upload, exports
    the station cameras and verifies shot outcomes from the hub side (`enrich.py:station_pass`).
    With `HUBCAM=1`, station-camera bumper reads are also fed into step 7.
13. **Site build.** `worker.py:build_site` handles identity labels and overrides, quarantine of
    shot credit, and the per-match, per-team and event JSON. It then runs the strategy
    dashboard, scouting and goals builds.

### 0.2 Worker loop

```
                      ┌──────────────────────────── worker.py:main ─────────────────────────────┐
 recorder container ──┤ "--recorder" → liveingest.Service(DATA).run()   (separate process; §14)│
                      └──────────────────────────────────────────────────────────────────────────┘
 worker container:
   start ─► thread: sources.job_loop(DATA)   (Video-page jobs: frame grabs, layout suggestions, calibration fits)
     │
     ▼
   loop forever:
     load_config()  ◄── DATA/config/worker.json (api writes it) ─ fallback .env: TBA_KEY EVENTS POLL_SECONDS HUBCAM GPU_DEVICE FORCE_CPU
     │
     ├─ PAUSE_PROCESSING=1 | no TBA key | no events | no MODEL file  →  log "waiting: …", sleep 30 s, repeat
     │
     ├─ for ev in events:  sync(ev, tba)
     │     ms     = TBA /event/<ev>/matches  (+ LiveView.extra_matches: stream-only matches)
     │     status = DATA/events/<ev>/status.json
     │     cfg    = event.json  ⊕  sources.video_config(sources.json, layout.json)
     │     for m in ms (time order):
     │        live = live_input(...)                # trackable livestream clip, unless rejected / failed twice
     │        skip if no camera.json and no live clip
     │        skip if not played (red score None or < 0) and no live clip
     │        if played and status.live_reconcile → live_reconcile()   (re-track or re-enrich with TBA breakdown)
     │        skip if matches/<m>.json exists, or status.state == "failed"
     │        process_match(...)  ─ ok ─► save ytmeta cache, build_site(ev)   (after EVERY new match)
     │     if nothing new this poll: build_site(ev) anyway
     │
     └─ _sleep(POLL)   wakes early when worker.json mtime changes or a new livestream record appears
```

`build_site(ev)` ends with a chain of strategy and scouting builds. Each step is wrapped so a
failure never stops the worker:

```
build_site
  ├─ identity.assign_event(results, TBA matches, overrides.json)   → labels per match/track
  ├─ site/api/<ev>/m/<k>.json, m/<k>.fuel.json, t/<team>.json, event.json, site/api/events.json
  ├─ subprocess: python -m live.strategy.dashboard DATA ev --site --snapshots --only-missing   (timeout 900 s; DASHBOARD=0 disables)
  ├─ scouting.site_build_extra(DATA, ev)          → verified team files, spec/, defense.json, scout.json
  ├─ subprocess: python -m live.scouting DATA ev --snapshots   (timeout 900 s)
  ├─ goals.site_build(DATA, ev, team=DASH_TEAM)   → rankings.json, schedule.json, selection.json
  └─ subprocess: python -m live.strategy.goals DATA ev --selection-snapshots   (timeout 900 s)
```

The site is rebuilt after every newly processed match and on every idle poll (default every
120 s). That is how identity overrides, newly posted TBA results and livestream records reach
the site without any match being reprocessed. The cost is that the whole strategy chain runs
every poll. The snapshot steps only write missing snapshots.

### 0.3 Stage table

| # | stage | module:function | reads | writes |
|---|---|---|---|---|
| 1 | config | `worker.py:load_config` | `DATA/config/worker.json`, env | env vars `TBA_KEY`, `GPU_DEVICE`, `FORCE_CPU`; globals `POLL`, `HUBCAM` |
| 1 | TBA poll | `tba.py:TBA.get` | TBA API v3 | `DATA/tba/<endpoint>.json` (`{etag, fetched, data}`) |
| 2 | sources → config | `sources.py:video_config` | `DATA/events/<ev>/sources.json`, `layout.json`, `event.json` | (in memory) |
| 2 | candidates | `videos.py:candidates` | TBA `match.videos`, yt-dlp metadata | `DATA/cache/ytmeta.json` |
| 3 | download | `videos.py:download` | YouTube | `DATA/cache/<match>_<ytid>.mp4` (temporary) |
| 4 | pose check | `process.py:check_pose`, `resolve_pose` | `events/<ev>/camera.json`, `pose_latest.json`, `live/field/2026-rebuilt-welded.csv` | `DATA/events/<ev>/pose_latest.json` (worker, when verified) |
| 5 | detection | `process.py:detect_video`, `detector.py:Detector` | video, `DATA/models/robot.onnx`, `layout.json` | (in memory) |
| 5 | bumper OCR | `ocr.py:OcrSampler`, `BumperOCR` | `live/models/ocr/*.onnx` | (in memory) |
| 6 | match start | `process.py:clock_start`, `match_start`, `pick_start` | clock-box crops, tracklets | `t_start`, `start_source` in match JSON |
| 7 | tracking + identity | `process.py:prepare`/`finish`, `fuse.py:run_labeled` (v1: `fuse.py:run`) | detections, reads | match JSON `tracks[]`, `identity` |
| 7b | station reads (`HUBCAM=1`) | `worker.py:station_reads`, `station_ocr.py:extra_reads_fn` | `MEDIA/<ev>/insets/<match>.mp4`, `station_{red,blue}.json` | (reads into the ILP) |
| 8 | integrity | `process.py:match_integrity`, `integrity.py:check`/`should_retry` | open-set reads, TBA lineups | `identity.integrity` |
| 9 | save | `worker.py:process_match` | – | `DATA/events/<ev>/matches/<match>.json`, `status.json` |
| 10 | fuel (§7–14) | `enrich.py:run_fuel_and_shots` → `fuel.py:track_fuel` | video, camera + match pose, `layout.json` | `DATA/events/<ev>/fuel/<match>.json` (airborne tracks only) |
| 10 | shots (§7–14) | `shots.py:reconstruct` | fuel tracks, match JSON, `rebuilt_rules.json`, TBA match | `DATA/events/<ev>/shots/<match>.json` |
| 10 | intakes (§7–14) | `intakes.py:detect_intakes` | video, fuel, match | `shots/<match>.json` → `intakes[]` |
| 10 | analytics (§7–14) | `enrich.py:analytics_for` → `analytics.py:match_analytics` | quarantined match, events, rules, TBA | `DATA/events/<ev>/analytics/<match>.json` |
| 11 | keep video | `worker.py:process_match` | – | `MEDIA/<ev>/<match>.mp4` |
| 12 | station pass (§5) | `enrich.py:station_pass` → `station_export.py:export`, `hubcam.py:run_match` | inset upload | `DATA/events/<ev>/stations/<match>.json`, refreshed `shots/` + `analytics/` |
| 13 | site JSON (§7–14) | `worker.py:build_site` + `sitev2.py` helpers | everything above + TBA | `DATA/site/api/<ev>/{event.json,m/,t/}`, `DATA/site/api/events.json` |
| 13 | strategy/scouting | `live/strategy/dashboard.py`, `live/scouting.py`, `live/strategy/goals.py` | site JSON, TBA caches | `dashboard*.json`, `preview/`, `dash/`, `spec/`, `defense.json`, `scout.json`, `scout/`, `rankings.json`, `schedule.json`, `selection*.json`, `selection/` |
| – | livestream (§7–14) | `liveingest.py:Service` (recorder), `liveingest.py:LiveView` (worker), `livestream.py` | stream URL from `sources.json` / TBA webcast | `MEDIA/live/<ev>/<video>/{seg,clips,matches}/`, `DATA/events/<ev>/livestream.json` |
| – | overlay video (§7–14) | `overlay.py` | match JSON, shots, video | rendered check videos (manual tool) |

### 0.4 Match state (`DATA/events/<ev>/status.json`)

`{ "<match key>": {state, at, video, attempts, error, source, live_rejected?, live_failed?, live_reconcile?} }`

| state | meaning | what happens next |
|---|---|---|
| `processing` | started (written before the download) | if the worker dies here, the next poll redoes the match (no match JSON exists yet) |
| `no_video` | no acceptable upload yet | retried after `RETRY_NO_VIDEO` = 300 s (a livestream clip bypasses the wait) |
| `error` | an exception, `attempts` < `MAX_ATTEMPTS` (3) | retried on the next poll, with no back-off |
| `failed` | 3 attempts used | **never retried automatically**: edit or delete the entry in `status.json` |
| `done` | match JSON written | skipped while `matches/<k>.json` exists |

To re-process a match: delete `DATA/events/<ev>/matches/<k>.json`. If the state is `failed`,
also remove its `status.json` entry. A `processing` entry left behind is harmless.

### 0.5 Worker environment variables (stage-independent)

| var | default | effect |
|---|---|---|
| `DATA` | `/data` | state root |
| `MEDIA` | `/media` | kept videos and livestream root (compose mounts `MEDIA_DIR` here) |
| `MODEL` | `$DATA/models/robot.onnx` | detector |
| `POLL_SECONDS` | 120 | poll period (`worker.json` `options.poll_seconds` wins) |
| `EVENTS`, `TBA_KEY` | – | used only while `worker.json` has `events: null` or `tba_key: null` |
| `PAUSE_PROCESSING` | unset | `1`: the loop idles and logs "match processing paused". The job thread still runs, so calibration jobs keep working. Production has this set to 1 at the time of writing. |
| `ONCE` | unset | `1`: one pass, then exit |
| `MAX_NEW` | unset | at most N new matches per event per poll |
| `V2` | 1 | run fuel/shots/analytics enrichment |
| `KEEP_VIDEO` | 1 | move the processed video into `MEDIA/<ev>/` (the Analyze view needs it) |
| `VIDEO_CACHE` | `$DATA/cache` | download scratch |
| `ID_CONF_MIN` | 0.5 | quarantine threshold (see §4.6) |
| `HUBCAM` | 0 | station-camera pass (`worker.json` `options.hubcam` wins) |
| `DASHBOARD` | 1 | run the strategy dashboard build in `build_site` |
| `DASH_TEAM` | `frc4911` | perspective team for goals and selection |
| `GPU_DEVICE` / `FORCE_CPU` | 0 / unset | detector and OCR device (`worker.json` `options.gpu_device`/`cpu_only` win) |

---

## 1. Video selection and download

### 1.1 TBA client — `live/tba.py`

`TBA(key, cache_dir).get(endpoint, max_age)` makes a conditional GET with `If-None-Match`.
Every response is stored as `DATA/tba/<endpoint with / → __>.json` = `{etag, fetched, data}`.
A 304 response and any network error both return the cached copy, so a TBA outage never stops
the worker once something is cached. Freshness per endpoint:

| method | endpoint | `max_age` |
|---|---|---|
| `matches(ev)` | `/event/<ev>/matches` | 0 (conditional GET every poll) |
| `event(ev)` | `/event/<ev>` | 3600 s |
| `teams(ev)` | `/event/<ev>/teams/simple` | 3600 s |
| `rankings(ev)`, `oprs(ev)` | | 120 s |

`match_label` / `short_label` / `sort_key` give "Qualification 12" / "Q12" / the
(qm < ef < qf < sf < f, set, number) ordering used everywhere.

A match counts as **played** when `alliances.red.score` is not null and is ≥ 0, and it is not a
stream-only placeholder (`m.livestream`).

### 1.2 Sources — `live/sources.py`

`DATA/events/<ev>/sources.json` is the admin's Video page (API: `server/routers/sources.py`,
UI: `web/sources.js`). Schema (module docstring):

```
{"version":1, "event", "updated_at", "discovered_at",
 "sources":[{"id", "origin": "tba"|"admin", "kind": "per_match"|"livestream"|"video"|"file",
             "provider": "youtube"|"twitch"|"file"|"http", "url", "title_pattern", "channel",
             "role": "wide"|"dynamic"|"alliance-side", "primary": bool,
             "coverage": {"mode": "all"|"matches"|"range", "matches", "from", "to"},
             "videos": {"<match key>": "<yt id>"}, "sample": {"video","match","t"},
             "label", "enabled", "updated_at", "updated_by"}]}
```

- **Roles.** `wide` is a fixed overhead or wide camera, which is what the tracker needs.
  `dynamic` is a switching program feed, possibly with picture-in-picture station insets.
  `alliance-side` is a fixed driver-station camera.
- **`discover(ev, event, matches)`.** Builds sources from TBA:
  - Each TBA webcast becomes a `livestream` source. Only YouTube and Twitch webcasts are
    accepted (`webcast_url`); other types are arbitrary URLs from TBA data and are dropped.
  - Every TBA match video is titled through YouTube oEmbed and grouped into **families** by
    `family(title)`: digits become `#` and "(…)" suffixes are dropped, so "Wide Match 17"
    becomes "Wide Match #".
  - Each (channel, family) becomes a `per_match` source with a `title_pattern` regex
    (`family_pattern`) and a `videos` map.
  - A family whose name starts with "Wide" gets role `wide`; everything else gets `dynamic`.
  - The first `wide` source becomes `primary` (else the first source). `merge()` keeps admin
    edits (`USER_FIELDS`) when discovery runs again.
- **`add(doc, url)`.** For a pasted URL it calls `classify_url` and refuses files outside the
  allowed roots and private hosts.
- **`video_config(doc, cfg, layout)`** is the only thing the worker takes from
  `sources.json` for match uploads:
  - `prefer` = the primary `per_match` source's `title_pattern`; its channel is added to
    `allow_channels`.
  - `reject` = the OR of `title_pattern`s of per-match sources that are disabled, or whose
    layout has station or inset regions (the tracker must not run on a picture-in-picture
    layout).
  - `event.json` values stay where the sources say nothing.

#### URL validation and the SSRF guard

These were added in security round 2 (commit 74e84af).

- **`classify_url(url)`** accepts these forms and normalises each:

  | input | result |
  |---|---|
  | absolute path | `kind=file` (callers then check `file_allowed`: under `MEDIA`, `DATA` or `CS_SOURCE_ROOTS`) |
  | bare 11-char YouTube id | `kind=video` |
  | YouTube watch / youtu.be / live / shorts / embed / playlist / channel URLs | rebuilt in canonical form; `/live` or `/streams` gives `livestream`, a channel page gives `per_match` |
  | Twitch `videos/N` | `kind=video` |
  | Twitch channel | `kind=livestream` |
  | other http(s) URLs with a host | `.m3u8` gives `livestream`, else `video`, `provider=http` |

  It rejects:
  - a leading `-` (that would reach yt-dlp or ffmpeg as an option);
  - `file:`, `javascript:`, `data:` and other schemes, and relative paths;
  - credentials in the URL, control characters and bad hosts.
- **`safe_public_url(url)`** resolves the host and refuses the URL if any address is
  loopback, RFC1918, link-local, CGNAT/Tailscale 100.64/10, docker 172.16/12, multicast or
  reserved. The IPv6 forms are covered, including IPv4-mapped, 6to4, Teredo and NAT64.
  `CS_URL_ALLOW` (hosts or CIDRs) whitelists private sources such as a LAN OBS server.
  Passing checks are cached for 60 s.
- Every yt-dlp and ffmpeg call puts `--` before the URL (`videos.py`, `layout.py`,
  `sources.py:job_index_playlist`, `livestream.py`).

#### Video-page jobs (calibration wizard, frame grabs)

`sources.submit()` writes `DATA/jobs/sources/<id>.json`. A runner claims a job atomically with
`<id>.lock` (`O_EXCL`). Jobs:

| kind | function | what it does |
|---|---|---|
| `discover` | `job_discover` | TBA discovery |
| `index_playlist` | `job_index_playlist` | lists a pasted playlist or channel with `yt-dlp --flat-playlist` and maps titles to match keys via `match_key_from_title` |
| `frame` | `job_frame` | grabs one frame |
| `suggest` | `job_suggest` | layout box suggestions |
| `detect` | `job_detect` | calibration step 1 |
| `fit` | `job_fit` | calibration step 2 |
| `overlay` | `job_overlay` | overlay of the saved calibration |

- `discover` is the only light job (`LIGHT_JOBS`) and runs in the API process.
- Vision jobs run in the API process only when numpy and cv2 import there. The API image is
  slim, so in practice they run in the worker's `job_loop` thread (0.7 s poll).
- A job still queued after 1 h is marked "expired".
- CLI: `python live/sources.py jobs DATA [--watch]`.

### 1.2b Per-match video plan — `live/videoplan.py`

Replaces the per-family roles as the way a match's upload is chosen (families stay as an "Advanced" fallback).

- **Candidates** per match (`gather`): its TBA videos, every per-match source's video for that key, admin
  additions, and the **channel scan** (`scan_channels`): the event's channels are listed (`yt-dlp --flat-playlist`)
  and an unlinked upload whose title names match K counts when a TBA-linked upload of K from the same channel sits
  within 4 places of it (2026wabon: TBA links 11 "Wide Match N", the channel has 67).
- **Classification** (`classify_video`, once per video, cached in `DATA/cache/vclass.json`): `yt-dlp -J` (IPv4) for
  metadata + a 240–360p video-only URL; six short bursts (keyframe + 6 frames) are read with HTTP range requests
  (`RangeFile`, ~1.5 MB, ~5 s per video, never the whole file). `wide` = fixed camera (edge similarity between
  samples, no cuts, no global motion); `stations` = fixed view + two bottom-corner insets (a vertical intensity
  step at one column in every frame); `broadcast` = cuts/pans. A different-camera first/last sample is an
  intro/outro, not a cut. Fewer than 4 readable samples → unknown, retried later.
- **Choice** (`choose`): drop excluded (admin, switched-off family, title naming another match via `parse_title`,
  integrity "wrong video" from a processed match, too long/short) and duplicates (`duplicates`: duration ±1 s and
  matching frame + motion pHashes); track = best wide → stations (limited: insets hide the near corners) →
  broadcast (limited: cuts); a `stations` video for the station pass; the livestream clip if recorded.
- **Files**: `DATA/events/<ev>/video_plan.json` (matches, videos, groups = type + channel for one layout per type),
  `channel_scan.json`; overrides in `sources.json` `match_overrides` (`pin`, `pin_stations`, `exclude`, `add`).
- **Worker**: `worker.py:video_plan` per poll (auto TBA discovery when stale, ≤12 checks / 60 s, rebuild);
  `videos.candidates` returns the plan's ranking (with `type`, `limited`, `group`), else the title rules;
  `videoplan.layout_for` passes the group's drawn layout (or detected inset masks) to `process.process`;
  `stationcam.find_inset_video` uses the plan's `stations` video. API: `GET/PUT/POST /events/<ev>/video-plan...`.
- **Livestream**: the recorder (`liveingest.live_sources`) uses today's TBA webcasts (or the TBA event's webcasts
  when sources.json has none), resolves each (`/@channel/live` → current video), drops webcasts that resolve to the
  same video, records the first live one, and re-resolves when a stream ends.

### 1.3 Choosing the upload — `live/videos.py:candidates`

The config comes from `event.json` merged with `video_config`. Defaults are
`prefer="^Wide Match"`, `reject="^Match \d"` and `allow_channels=["FIRSTRoboticsCompetition"]`.
The repo ships `events/2026wasam/event.json`, which also allows "FIRST Washington" and sets
`search_title`.

1. Take the match's TBA `videos` of type youtube. Each key must match the YouTube id regex
   `^[A-Za-z0-9_-]{11}$`; nothing else ever reaches yt-dlp.
2. Get metadata (channel, title, duration, height) with `videos.py:meta` (yt-dlp
   `--skip-download --print`, IPv4). It is memoised in `DATA/cache/ytmeta.json`.
3. Drop videos whose title matches `reject`, and videos at least 600 s long (full-day streams).
4. Rank: title matches `prefer` first, then the rest from `allow_channels`.
5. If nothing qualifies and `search_title` is set, search YouTube with
   `ytsearch6:"<name> - <event>"` (`match_fields`: "Qualification N", "Match N" for sf,
   "Final N"). Only exact title matches from allowed channels are accepted, with an optional
   "(R2)" suffix. At most 2 are returned.

The worker tries at most `cands[:3]`, after a livestream clip if there is one (§1.5).

### 1.4 Download — `videos.py:download`

```
yt-dlp -4 --sleep-requests 1 --retry-sleep 5 -q --no-warnings
       -f "bv*[height<=1080][ext=mp4]/bv*[height<=1080]" -o <out>.part.mp4 --force-overwrites -- https://youtu.be/<id>
```

- **Video only, no audio**, at most 1080p. The detector and fuel tracker want the 60 fps
  1920×1080 upload. The content is 30p in a 60p container (see §7).
- **4 attempts.** The wait before attempt i+1 is 10·(i+1) s plus 0–10 s of jitter. **The
  last attempt drops `-4`.** The server's IPv6 egress gets YouTube's "Sign in to confirm
  you're not a bot", and IPv4 works, so `-4` is forced unless `YT_FORCE_IPV4=0`.
- Timeout is 30 min per attempt. On success the file is renamed to `<out>`.
- **`YTDLP`** overrides the binary. yt-dlp is hash-pinned in the worker image
  (`live/requirements-ytdlp.txt`, Dockerfile). It is no longer upgraded at container start
  (security finding 3).

### 1.5 The worker's attempt loop — `worker.py:process_match`

```
tries = [("live", clip)] if a trackable livestream clip exists  +  [("upload", v) for v in candidates[:3]]
for each try:
    download (or copy the clip) → DATA/cache/<match>_<id>.mp4
    [HUBCAM] station_reads(): fetch the "Match N" inset upload → extra reads callable
    process.process(...)                        # §2–§4
    keep pose if verified (≥3 tags and (re-solve applied or moved < 2 px)) → pose_latest[_<source>].json
    wrong = integrity.should_retry(identity.integrity)        # §4.5
    best  = this result if: no best yet, or best was a wrong video and this isn't,
            or (same wrong-ness and feed_ok better by > 0.02)
    stop early if feed_ok ≥ 0.9 and not wrong
```

Then:
- A rejected livestream clip (wrong video) is recorded in `status.live_rejected` and never used
  again. The upload path takes over.
- A livestream clip that fails twice (`live_failed` ≥ 2) is dropped for the uploads.
- Otherwise the best result is saved. If it came from the stream before TBA posted, the worker
  sets `live_reconcile` (§7–14).
- Every downloaded working copy is deleted in `finally`. The kept video is first moved to
  `MEDIA/<ev>/<match>.mp4`.

`feed_ok` is the fraction of the match in which the field camera was live (§3.3). Some
broadcast uploads drop the camera for stretches, so a second upload of the same match is tried
and the better one kept.

**Start hint.** `worker.py:start_hint(ev, v)` is the median detected `t_start` of at least 3
earlier matches from the same channel whose title has the same first word ("Wide" vs
"Qualification"). Cut points are consistent per source. It is used only when neither the clock
nor robot motion gives a start (§3.4).

### 1.6 Failure modes and gotchas (video stage)

- **Failed metadata lookups are cached as `null`.** `candidates` stores `cache[vid] =
  meta(vid)` even when `meta` returns `None`. The cache is persisted to `ytmeta.json`. So a
  video whose lookup failed once (bot check, timeout) is ignored for that event until you
  delete `DATA/cache/ytmeta.json` or that entry. This behaviour is not documented elsewhere.
- **Partial downloads accumulate in `DATA/cache/`.** A download that fails all attempts leaves
  yt-dlp's `<out>.part.mp4[.part]` behind. `finally` removes only the final names. Production
  has several of these from 10-Oct, about 250 MB, on a root disk that is full.
- `videos.py:search` does not pass `-4`, unlike `meta`, `download` and the layout/playlist
  helpers.
- If **every** candidate looks like the wrong video, the best of them is still saved, with
  `wrong_video` set on the match JSON. Nothing downstream reads `wrong_video` (checked by
  grep; flagged for the owner).
- **Admin-pasted per-match playlists only feed the Video page.** Their `videos` map is used
  for frame grabs and calibration. The worker's upload choice uses only TBA-linked videos
  (plus the `search_title` fallback): `video_config` contributes regexes, not video ids. A
  playlist whose videos TBA does not link is never processed.
- `sources.station_pattern()` is shown on the Video page but not used by the pipeline.
  `stationcam.py:find_inset_video` hard-codes the title `^Match <n>( (…))?$`.

---

## 2. Camera geometry and pose

### 2.1 Field frame and the camera model — `live/camera.py`

- **Field frame.** Metres, X 0..16.541 (blue wall at X=0, red wall at X=16.541), Y 0..8.069.
  Y=0 is the far wall in the 2026wasam image and Y=8.069 is the camera side.
  - Hub centres are red (11.915, 4.035) and blue (4.626, 4.035).
  - The field is point-symmetric, so a team's own-end view of blue robots is a 180° rotation.
  - AprilTag positions come from `live/field/2026-rebuilt-welded.csv` (inches, WPILib layout).
- **`Camera(path_or_dict)`.** A full OpenCV pinhole camera with lens distortion in
  full-frame pixels. Fields: `W, H, K, dist, rvec, tvec`. `R` and the centre `C` are derived.
  `raw` keeps the JSON, including extras such as `crop` and `event`.

  | method | does |
  |---|---|
  | `project(P)` | field→pixels; Z=0 assumed for 2-D input |
  | `img_to_plane(uv, Z)` | pixels→plane at height Z |
  | `img_to_ground(uv)` | pixels→carpet (Z=0) |
  | `point_to_field(uv)` | ground point pushed away from the camera by `CORNER_MEAN` |
  | `box_to_field(boxes)` | bottom-centre of each box through `point_to_field` |
  | `cov(XY, su=3, sv=4)` | per-detection covariance (see below) |
  | `heights(boxes, XY)` | robot height from the box top, by bisection; NaN if clipped |
  | `roi(margin_m=0.6, robot_h=1.4, pad_px=16)` | field ROI in pixels |

  - The bottom-centre of a box is the bumper corner nearest the camera. `CORNER_MEAN` is the
    yaw-averaged corner distance of a 0.91 m robot (≈0.58 m), so the push turns that corner
    into the footprint centre.
  - `cov` is pixel noise propagated through the inverse Jacobian, plus yaw uncertainty along
    the viewing direction. `process.py` calls it with `su=2.0, sv=2.5`.
- **`install_geom_shim(cam)`** registers the camera as the module `geom`. The original
  autoscout tracker modules in `src/` (`track.py`, `fit6.py`, `assign.py`) then run unchanged
  in full-frame pixels.
- **`field_nms(frames, XY, conf, sep=0.70)`** suppresses detections in field space: in each
  frame, keep the highest-confidence detection within 0.70 m.

### 2.2 Event calibration — `events/<ev>/camera.json`

Lookup order: `DATA/events/<ev>/camera.json` first, then the repo's `events/<ev>/camera.json`
(`worker.py:ev_file`). The worker processes nothing for an event without a calibration unless a
calibrated livestream clip exists.

**Hand path (original, 2026wasam): `live/calibrate.py`**

```
python live/calibrate.py events/<ev>/calib_input.json events/<ev>/camera.json
```

- Input: `{W, H, tag_csv, tags: {id: [u, v, sigma]}, lines: {centerline|farrail|nearrail: [[u, v], ...]}}`.
- Model: pinhole + k1..k3 + tangential. The rail height `h` and set-back `d` are fitted, since
  they are not in the tag layout.
- Stage 1 fits pose, focal length and k1. Stage 2 fits everything. Both use the `soft_l1`
  robust loss. The lines carry most of the lens information; the tags pin pose and scale.
- A good fit has tags ≈1 px, lines < 1 px and a rail height ≈0.5 m. The shipped 2026wasam
  file has `tag_rms` 0.90 and `rail_h` 0.498.

**Wizard path (any event): Video page → Calibration**

1. `sources.py:job_detect` grabs about 8 frames of the target camera.
   - Targets are `overhead`, `station_red` and `station_blue`; station targets use the inset
     crop from the layout.
   - AprilTags are detected (`calibrate.py:detect_tags_frames`, tiled). A median plate is
     saved, and a first fit runs if at least 2 tags are found.
   - State goes to `DATA/events/<ev>/calib_wizard/<target>.json` and frames to
     `DATA/events/<ev>/frames/calib_<target>*.{jpg,png,npy}`.
2. `job_fit` takes tags, clicked named field points (`calibrate.py:named_points`) and clicked
   lines.
   - It runs `wizard_fit`, then `detect_tags_predicted` to find more tags near the fit.
   - For the overhead camera only, `refine_with_lines` uses the white centre line. That
     refinement is kept only if the point RMS stays ≤ max(4, 1.5× before).
   - `wizard_camera` builds the camera dict: model "wizard pinhole f,c,k1,k2,k3", a `calib`
     block with RMS values, `crop` for stations, `field_roi` for the overhead camera.
3. `POST /app/v1/events/<ev>/calibration/<target>/accept` (`server/routers/sources.py:calib_accept`):
   - copies the candidate to `DATA/events/<ev>/camera.json` or `station_<w>.json`;
   - backs up the old file as `.bak-<ts>`;
   - stamps `accepted_by` with the display name, never the username;
   - for the overhead camera, moves `pose_latest.json` aside so the new calibration is not
     overridden by a stale carried pose.

Livestream cameras are calibrated as `camera_<source id>.json`. `camera.json` also counts if
the wizard ran with the livestream as the primary source (§7–14).

### 2.3 Broadcast layout — `live/layout.py`

`DATA/events/<ev>/layout.json` (else the repo's `events/<ev>/layout.json`) is drawn in the box
editor. For each source it holds a reference frame size `W×H` and regions of these kinds:

| kind | used by |
|---|---|
| `field` | clips the detection ROI (`process.py`) |
| `overhead` | the overhead picture when it is itself an inset |
| `station_red`, `station_blue` | station insets (`stationcam.py`); also greyed out in detection crops |
| `clock` | clock-tick start detection (`process.py`, station sync) |
| `scoreboard`, `mask`, `inset` | ignored by fuel (§7–14); `inset` also greyed out for detection |

Accessors fall back to the **hard-coded 2026 FIRST Washington values** when no layout exists:

| constant | value (1920×1080) | used for |
|---|---|---|
| `CLOCK_BOX` | (912, 72, 1008, 116) | clock digits |
| `OVERLAY_BOXES` | score bar (446, 8, 1474, 134), shift box (896, 126, 1024, 170), alliance boxes (28, 60, 358, 118) / (1562, 60, 1890, 118), banner (616, 1004, 1304, 1072) | fuel masks |
| `STATION_RECTS` | red (0, 708, 622, 1080), blue (1294, 708, 1920, 1080) | "Match N" insets |
| `SYNC_REGION` | (0, 0, 1920, 700) | area shared by the wide and inset uploads |

- `layout.find(...)` looks for, in order:
  1. `$LAYOUT`;
  2. `<event_dir>/layout.json`;
  3. the camera file's directory;
  4. `DATA/events/<ev>/` and then `events/<ev>/`, with the event key taken from `ev`, the
     camera JSON's `"event"` field, or the paths.

  `LAYOUT=none` forces the hard-coded behaviour.
- `processing()` picks the source the tracker runs on: `processing_source`, else a `wide` or
  `dynamic` source without station insets. `stations()` picks the source holding the insets.
- Frame tools for the editor:
  - `grab_frame(s)`, `grab_dense` and `resolve_media` (`yt-dlp -4 -g`, cached 30 min).
    Every URL goes through `safe_public_url`, including the media URL the extractor returns.
  - `cached_download`.
  - Suggestions: `suggest_overlays` (temporal stillness), `suggest_clock` (the 1 Hz-ticking
    block), `suggest_insets` (program vs clean feed), `suggest_insets_edges` (persistent
    borders), `suggest_field` (projected field box when calibrated), `suggest_all`.

Production at the time of writing has no `sources.json` or `layout.json` for 2026wasam, so the
hard-coded defaults apply.

### 2.4 Per-match pose check — `process.py:check_pose`

The broadcast camera gets bumped. A bump mostly re-aims it rather than moving it.

1. Start from `camera.json` with the pose replaced by `pose_guess`. The worker passes
   `DATA/events/<ev>/pose_latest.json`, the last verified pose, because a bump persists
   across matches.
2. Decode one grey frame every 6 s from t=8 s to t=170 s (about 28 frames). Skip frames whose
   field mean is ≤ 20 (feed lost).
3. For each tag predicted ≥ 40 px inside the frame, take an 80×80 crop, upscale it ×4, and run
   `pupil_apriltags` (tag36h11). A detection needs hamming 0, decision margin > 15 and the
   right id. A tag counts if it is seen in at least 3 frames; its position is the median.
4. With fewer than 3 tags the calibration is kept (`moved_px: null`).
5. Otherwise `moved_px` is the median reprojection error. If it is > 2.0 px,
   `resolve_pose` runs:
   - It fits rotation and camera centre to the tags plus the white centre line. The line is
     extracted from a robot-free median plate (`centreline_pixels`: brightest column within
     ±25 px of the prediction, ≥ 50 above the median) and weighted like the tags.
   - A soft 0.25 m prior holds the position.
   - It uses the `soft_l1` loss and re-extracts the line twice.
6. The new pose is **applied only if** the tag error at least halves **and** the centre-line
   score (`centreline_score`, median px offset) is ≤ max(old score, 3 px). Tags alone may all
   sit on one side of the field; the line guards against a fit that is good there and wrong
   elsewhere.
7. The worker writes the pose used to `pose_latest.json` when ≥ 3 tags were found and
   (a re-solve was applied or `moved_px` < 2.0). For livestream clips the file is
   `pose_latest_<source>.json`.

Output in the match JSON:
- `camera_pose = {rvec, tvec}` (always use it instead of `camera.json`'s pose);
- `pose = {checked, tags, moved_px, resolved_px?, line_old_px?, line_new_px?, applied?}`.

One AprilTag detector is kept for the life of the process (`_TAGDET`). `pupil_apriltags`'
destructor segfaulted intermittently (2 in 40 runs).

---

## 3. Detection — `live/detector.py`, `live/process.py`

### 3.1 The detector

`Detector(onnx_path)`:
- A YOLO (ultralytics v8/11 head) exported to ONNX and run with onnxruntime. There is no
  torch at runtime. One class, "robot".
- Provider: `CUDAExecutionProvider` on `GPU_DEVICE` (default 0) unless `FORCE_CPU=1`, with CPU
  as fallback. `ort.preload_dlls()` loads the pip CUDA/cuDNN wheels
  (`onnxruntime-gpu[cuda,cudnn]==1.31.0` in the worker image).
- **Input size comes from the model.** The production export is rectangular 544×1280 fp16
  (`live/train/README.md`), about 3× faster than square fp32. Dynamic shapes fall back to
  1280. Input is letterboxed with pad value 114.
- Decode: per-anchor max class score ≥ `conf` (default 0.10), then `cv2.dnn.NMSBoxes`
  (IoU 0.6), keeping at most 40.
- `alliance(img, box)` reads bumper colour from the lower 55% of the box (HSV: S > 95,
  V > 45). Red hue is ≤ 9 or ≥ 168, blue is 100–132, and at least 12 pixels are needed.
  Returns 0 (abstain), 1 (red) or 2 (blue).

The model file is `DATA/models/robot.onnx` (19 MB; `robot.pt` is kept next to it). Training is
covered in §6.

### 3.2 `detect_video` — the single decode pass

Constants in `process.py`:

| name | value | why |
|---|---|---|
| `FPS` | 30 | detection rate. Uploads are 60 fps containers carrying 30p content. |
| `AUTO_S, PAUSE_S, TELEOP_S` | 20, 3, 140 | 2026 REBUILT timing; `MATCH_S` = 163 s |
| `CONF_KEEP` | 0.10 | raw detections kept (env) |
| `CONF_TRACK` | 0.30 | detections used for tracking (env) |
| `SCAN_T1` | 190 s | stop decoding after this video time (env) |
| `OUT_HZ` | 10 | export rate of track arrays |
| `NO_FEED_LEVEL` | 20 | ROI mean below this = camera feed lost |
| `START_FALLBACK` | 6.5 s | last-resort match start (env) |
| `CLOCK_BOX` | 912,72,1008,116 | env wins over layout |

For every decoded frame up to `SCAN_T1`:

1. **Feed check.** The mean of the ROI subsampled 16× must be > 20. Otherwise the frame is
   "no feed": it is recorded in `feed` and nothing is detected.
2. **Colour-swap fix** (`swap_check`). Broadcast glitches sometimes swap RGB↔BGR for a few
   frames, which would flip every robot's alliance. The red and blue bumps are static. If the
   red bump reads blue *and* the blue bump reads red, the frame is channel-reversed. Sample
   points are at X = 16.541−4.64 and 4.64, with Y = 6.6 on the camera side (`bump_y`).
3. **Hooks.** Every live frame at full rate goes to `hooks[i].push(idx, t, bgr)`. This lets
   the fuel tracker ride the same decode, but the worker currently does not use it: enrich
   decodes again (§7–14).
4. On 30 Hz sample frames:
   - During the first 45 s, crop the clock box to grey (inverted if the layout marks the clock
     `light_on_dark`).
   - Crop the ROI and grey out the layout masks (pad value 114).
   - Run the detector at `CONF_KEEP`.
   - Hand the frame to the OCR sampler.
   - Record rows `[frame, t, -1, x0, y0, x1, y1, conf, alliance]` plus two HSV descriptors
     (whole box and top 60%; 12×3 H-S histogram + 4 V bins).

The ROI is `cam.roi()`: field ±0.6 m, swept up to 1.4 m, padded 16 px. It is clipped to the
layout's `field` box when one exists.

### 3.3 Feed gaps

`feed` (per 30 Hz frame) becomes `fw` within the match window. Runs of missing feed are
exported as `feed_gaps` (`[[t0, t1], ...]`, seconds from replay frame 0). Also exported:
`feed_ok` = mean over the match, and `auto_feed_ok` = mean over the first 20.5 s.
- Per-robot stats count only filmed time and are scaled to the full match (`track_stats`).
- `build_site` skips a team's auto path when `auto_feed_ok` < 0.7, and excludes gaps from
  heatmaps.
- The site shows "partial footage".

### 3.4 Match start — `prepare` → `pick_start`

`t_start` is the video time of the start of auto. The replay frame 0 is `t_start − 0.5 s`;
video time of replay frame i = `t_start − 0.5 + i/hz`.

1. **Clock** (`clock_start`). Once the match starts, the broadcast clock digits change exactly
   once per second.
   - The clock box must be white (mean > 150). Count changed dark-digit pixels between
     frames: > 25 is a "change". Changes within 0.2 s are merged.
   - Find the first 4 changes spaced 1.0 s ± 0.12 apart. The clock must have been visible
     and static for ≥ 2 s before the first change; an overlay that appears mid-match does not
     count.
   - Start = first change − 1.0 s. Accepted if 0 ≤ t < 40.
2. **Livestream** start, when `start_trusted=True`. A stream clip carries a measured auto
   start (`livestream.py`). Used if 0 ≤ t < 60.
3. **Motion** (`match_start`).
   - Build pure tracklets (`src/track.py`). A robot is "moving" when its 0.5 s displacement
     is > 0.6 m/s.
   - Find the first 1 s window in 0.5–30 s where ≥ 3 robots move in ≥ 70% of frames. Walk
     back while ≥ 2 move, then subtract 0.3 s reaction latency.
   - Pre-match robots are parked, and detection flicker moves no tracklet.
4. **Default.** Otherwise the worker's `start_hint` (§1.5) or `START_FALLBACK` (6.5 s), with
   `start_detected=false`.

`start_source` ∈ {`clock`, `livestream`, `motion`, `default`}.

**Window.** Frames from `t_start − 0.5` for `MATCH_S + 1` = 164 s at 30 Hz, so `frames` ≈
4920. `frames` in the match JSON is this **30 Hz** count, even though `hz` is 10.
`t_auto_end` = 20.5 and `t_teleop_start` = 23.5 (replay seconds).

> Because decoding stops at `SCAN_T1` = 190 s, a video whose match starts after about 26 s
> loses the end of teleop. Clock detection only looks at the first 45 s, and motion at the
> first 30 s.

### 3.5 Detections → tracking inputs — `prepare`

1. Keep detections with conf ≥ `CONF_TRACK` (0.30).
2. `box_to_field`, keep detections inside the field ±0.5 m, then `field_nms` at 0.70 m.
3. Cut to the match window and re-index frames.
4. **Re-map OCR reads.** A read on a detection that was suppressed by NMS moves to the kept
   detection of the same robot in the same frame (nearest within 0.7 m).

`prepare` returns the stage state `st` (`d, ch, bo, xy, reads, nf, fw, f0, t_start, found,
how, ...`). `process(dump=path)` pickles it so `finish()` can be re-run without the video
(development).

---

## 4. Tracking and identity

There are two code paths.

| | v2 "ocr-ilp" (production) | v1 (fallback) |
|---|---|---|
| when | `process(teams={red:[3], blue:[3]})` and OCR available | no teams, OCR failed to start, or `run_labeled` raised |
| entry | `fuse.py:run_labeled` | `fuse.py:run` |
| output tracks | 6, ordered red 1–3, blue 1–3 (TBA station order), each with `team`, `team_conf`, `conf[]` | up to 3 per alliance, unlabelled; labels come later from `identity.assign_event` |
| site identity | `identity.method = "ocr-ilp"` | `legacy` (conf capped 0.2) or `hmm` |

The worker always passes `teams` from TBA, or from the stream overlay for stream-only matches.

### 4.1 Bumper OCR — `live/ocr.py`

**Models.** `live/models/ocr/ch_PP-OCRv3_det_infer.onnx` (DB text detector, 2.4 MB) and
`ch_PP-OCRv3_rec_infer.onnx` (SVTR/CTC recogniser, 10.7 MB). They come from the
rapidocr_onnxruntime 1.2.3 wheel, Apache-2.0. `CYBERSCOUT_OCR_DIR` overrides the location.

**Closed-set CTC scoring.** Text is never decoded freely.
- Each detected text box is scored by CTC log-probability against the **6 match teams'
  numbers**, upright and rotated 180°.
- A read is `(cand index, lp, margin over the runner-up candidate, uv)`. Reads with
  lp ≤ `MIN_LP` (−8) are not reported.
- `CONFUSABLE` maps digits to look-alike glyphs (4911's font reads "4S11" / "HS11"). It is
  off for overhead reads and on for station reads.
- Crops are upscaled ×3 (`UP`; numbers are 10–20 px tall). The text detector runs at angles
  `OCR_ANGLES` = 0, +45, −45, with fixed square input buckets (192…512 px) to avoid cuDNN
  re-tuning.

**Open set.** `OCR_OPENSET=1` is the default when the event's team list is known. Every text
box is also scored against all event teams.
- `oos` (out of match) is set unless the best candidate is a match team that beats the best
  non-match team by `OCR_OPEN_MARGIN` (1.0 nats).
- Open-set reads feed only `integrity.py`. `OCR_OPEN_REJECT=0` (default) keeps the identity
  solve on closed-set reads.
- Dropping out-of-match reads was measured as *worse*: samples right went 0.920 → 0.913 on
  the 10-match dev set. Wrong reads are mostly a neighbouring robot's number, which is a match
  team, and look-alike teams such as 948/949 would lose correct reads.

**Sampler** (`OcrSampler`, called from `detect_video` on 30 Hz frames):

| knob | default | meaning |
|---|---|---|
| `OCR_CONF` | 0.30 | min detector conf to try |
| `OCR_PERIOD` | 0.3 s | min time between attempts on one robot position (0.8 m radius) |
| `OCR_MAX_PER_FRAME` | 3 | the most-stale robots first |
| `OCR_T1` | 190 s | stop reading after this video time |
| `OCR_WORKERS` | 4 | OCR worker processes (`python live/ocr.py --worker`), fed pickled jobs over stdin; separate interpreters so OCR doesn't fight decode/detection for the GIL. `OCR_PROC=0` uses a thread instead. |
| `OCR_GPU` | `GPU_DEVICE` | OCR device |
| `OCR_PERIOD_READ`, `OCR_FRESH` | = period, 3 s | re-read recently-read robots less often. Off: measured to *lower* total reads (qm12: 514 → 281). |

- Crop: `bumper_rect`, the projection of ±0.6 m around the robot from z 0 to 0.5 m, padded
  4 px. At least 20×20 px is required.
- **Attribution** (`_attribute`). A read goes to the detection whose bumper region contains
  the text centre, choosing the nearest robot centre. If a second region also contains it and
  that robot is within 1.5× the distance + 3 px, the read is flagged **ambiguous**.
- Read rows: `[det_row, cand, lp, margin, ambiguous, u, v]`. Open-set rows have 14 columns
  (`OPEN_COLS`).

**Read weight** (`fuse.py:read_weight`). Calibrated on 8k reads from 19 matches; overall
99.5% right.

```
w = 1.0 if lp > -6 else 0.5           # lp ≤ -6: 96% right
w *= 0.3 if margin < 2 else 0.7 if margin < 4 else 1.0   # margin <2: 82%, 2–4: 95%
w  = 0 if lp < -8 or margin < 1
w *= 0.7 if ambiguous                 # ambiguous: 99.3% right
```

A read with weight ≥ 0.7 that is not ambiguous also overrides that detection's colour-based
alliance.

### 4.2 Tracklets — `src/track.py` via `fuse.py:build_ltracklets`

This is the field-space Kalman tracker from the original autoscout:
- constant velocity, `DT` = 1/30, hard speed ceiling `VMAX` = 5 m/s;
- process noise `SIG_A` = 7 m/s², χ² gate 13.8 (2 dof, 99.9%), Hungarian association;
- tracklets close after `MISS` missed frames (fuse sets 20, overriding track.py's 12) and are
  discarded below `MIN_LEN` = 5.

Tracklets are short but pure. Upstream measured 100% purity on its hand trace.

Per tracklet (`LTk`) the evidence is:
- `rd`: reads `[frame, team idx, weight, lp]`;
- `col`: [red votes, blue votes] from bumper colour;
- `h`: median height;
- `ex`: up to 24 appearance exemplars.

### 4.3 Cut, merge, solve — `fuse.py:run_labeled`

1. **`split_by_reads`.** When a tracklet's reads switch team, the Kalman tracklet jumped
   robots.
   - `_best_split` finds the single change point that maximises
     (max team weight before) + (max team weight after) − (max team weight overall). It
     requires different winners on each side with ≥ 1.0 weight each.
   - It cuts if the gain is ≥ `SPLIT_GAIN` (1.6). The cut goes at the frame of closest
     approach to another tracklet between the two read runs (if < 3 m; else the midpoint).
   - Applied recursively. Each cut is recorded as a correction (§4.4).
2. **`merge_labeled`.** Duplicate merge: tracklets overlapping ≥ 3 frames with median
   distance < `DUP_M` (0.7 m) are fused, inverse-covariance weighted. They are **never** merged
   when both have a confident (> 0.8) posterior for different teams, or when both have ≥ 5
   colour votes with red fractions differing by > 0.7.
3. **`solve_labeled`.** One MILP (`scipy.optimize.milp`, HiGHS) over all six teams. Each team
   is a path through time-ordered tracklets.
   - Variables:
     - `X[i,t]`: tracklet i is team t. Cost `−W_R·n_i + C[i,t]`, where `W_R` = 0.10 per
       frame rewards coverage. Pairs with cost ≥ `FORBID` (20) are dropped.
     - `E[i,j,t]`: direct link i→j. Cost `link_cost`, or `overlap_cost` for a ≤ 15-frame
       hand-over between two pieces of one robot (median distance ≤ 1.2 m).
     - **Limbo** in/out per tracklet: cost `J_LIMBO`/2 each (12 total). It means "robot
       untracked for a while", makes no position claim, and is time-binned at 0.5 s so a team
       can't be in two places.
   - `link_cost` (shared with v1): infeasible if the gap is > `MAXGAP_S` (10 s) or the
     distance is > 5 m/s·gap + 0.3 m. Otherwise:
     `W_M (2.0)·(d/reach)² + W_G (0.5)·gap + W_H (1.0)·min(((Δh)/0.10)², 9) + W_C (0.5)·appearance term`.
   - Assignment cost `C[i,t]`:
     - **reads**: tempered log-likelihood with `Q_OCR` = 0.97, turned into
       `−W_ID·(log posterior + log 6)` and clipped at `ID_CAP` 200;
     - **colour**: `W_COLV` (0.3) per opposite-colour vote beyond `COL_NOISE` (15%), capped at
       `COL_CAP` 10. Colour is correlated within a tracklet: a robot parked by a blue field
       element reads blue for minutes.
   - Constraints:
     - flow conservation per tracklet and team;
     - each tracklet used at most once across teams;
     - two tracklets within `EXCL_M` (0.6 m) at the same time can't both be used (they are
       one robot);
     - each team's limbo chain covers every time bin.
   - Time limit `MILP_S` = 90 s, relative gap 1e-3.
   - **Motion-only reference.** With `MOTION_REF=1` the same ILP is solved again without
     reads. The difference becomes the "relink" corrections.
4. **Smoothing.** Each run of directly linked tracklets becomes a segment, smoothed with the
   RTS smoother (`src/fit6.py:rts`). Positions and velocities are written per frame.
   - Frames in limbo or never seen get the nearest known position with `obs=0` and `conf=0`
     ("unknown").
   - Positions are clipped to the field.

### 4.4 Per-frame confidence and corrections

**Confidence** (`run_labeled`, after the solve):
- **Own evidence** of tracklet i as team t: P(right alliance) × P(team | alliance).
  P(alliance) combines reads and colour; P(team | alliance) comes from reads. A tracklet with
  no reads gets P(team | alliance) ≥ `UNREAD_ACC` (0.7), the measured accuracy of read-less
  labels (0.76–0.79 on 19 quals).
- **Propagation along direct links.** Two forward/backward passes of
  `c[k] = max(c[k], c[k−1]·link_conf)`, where `link_conf` is a softmax of the chosen link
  against every competing successor or predecessor.
- **Exclusion.** If both alliance-mates are confidently elsewhere at the time, this tracklet is
  the third: `conf ≥ 0.9·min(mates' conf)·P(alliance)`.
- **Gaps** inside a segment of ≤ `GAP_CONF_S` (3 s) get `min(conf_a, conf_b)·exp(−gap/2)`.
  Longer gaps get 0.
- `team_conf` = mean `conf` over observed frames.

**Corrections** (`fuse.py:corrections`) list every place where the OCR solve differs from
tracking alone. They are written to `identity.corrections[]`:
`{kind cut|relink, frame (10 Hz), t, t_video, teams, tracks, evidence [[frame, team, lp, weight]], encounter {dist_m, xy?}, motion_link?, conf, anchored, unresolved?}`.
- `cut`: a tracklet's reads changed team.
- `relink`: the motion-only solve linked a→b directly, but the OCR solve gave b to another
  team.

Frames between contradicting reads are capped at the correction's confidence. The Analyze
view words these by kind ("swap" vs "re-link") and hides low-confidence re-links by default.

**`identity` block** in the match JSON:
`{method: "ocr-ilp", n_reads, n_reads_raw, per_team_reads, n_tracklets, n_cuts, seconds, corrections[]}`.
With station reads it adds `n_reads_extra`, `n_reads_extra_raw` and `per_team_reads_extra`.
With open set it adds `open_set: {n_teams}` and `integrity`.

**Measured** (HANDOFF, hand ground truth, 19 matches, position metric):

| | value |
|---|---|
| samples right | 87.6% (95.3% of answered, 8% abstain); v1 was 32.5% |
| at `conf` ≥ 0.8 | 98.9% right |
| calibration | 0.8–0.95 → 97.5% right; ≥ 0.95 → 99.3% |
| ghost frames | 11.5% → 0.9% |
| answered, with station reads (10-match dev set) | 95.6% → 98.5% |
| hand labels after re-tracking (strategist round 2) | 97.6% |
| runtime | ~200 s/match on the server GPU (qm17 with station reads: 399 s in production) |

**Weak spot.** Robots whose numbers OCR never reads (fonts, angles; e.g. 4469, 949) get
identity from linking only, with conf 0.3–0.7. Station reads fix most of these (§5.2).

### 4.5 Match integrity — `live/integrity.py`

The worker passes `event=event_ctx(ev, m)` = `{teams: event team keys, matches: TBA matches, key}`.
For a stream-only match it passes none. `process.py:match_integrity` normalises the open-set
reads (overhead, plus station rows with open-set columns) to
`[best_team, best_lp, best_margin, rejected, track, source]` and calls `check()`.

- **Confident read**: best_lp > −4.0 and best_margin ≥ 2.0 (`INTEG_CONF_LP`, `INTEG_CONF_MARGIN`).
- **Lineup score**: Σ over the lineup's 6 teams of min(reads, 10)/10 (`INTEG_CAP`).
- **`possible_wrong_video`** requires all of:
  - ≥ 20 confident reads (`INTEG_MIN_CONF`);
  - another lineup at the event scores ≥ 1.5 more (`INTEG_WV_GAIN`);
  - and this lineup explains < 50% of the confident reads (`INTEG_WV_FRAC`), or the other one
    explains 20 points more.

  The flag names `likely_match`. Measured: 570/570 swapped-lineup videos detected, 0/10 false
  alarms.
- **`lineup_mismatch`** (informational): < 50% explained, but no better lineup.
- **`possible_substitution`**: one non-listed team read ≥ 6 times on one output track and
  ≥ 50% of that team's track-attributed reads, unless the track is mostly read as its listed
  team. Informational only. Synthetic test: 4/4.

`should_retry(integrity)` is true only for `possible_wrong_video`. The worker then tries the
next upload (§1.5).

### 4.6 Labels at site-build time — `live/identity.py:assign_event`, overrides, quarantine

`build_site` calls `identity.assign_event(all match JSONs, TBA matches, overrides.json)` on
every rebuild.

- **v2 matches** (`labeled_match`). Pass-through: labels = each track's `team`. This is used
  only if each alliance's three tracks carry exactly TBA's three teams; otherwise the match
  falls back to the solvers below.
  - **Manual overrides** relabel a track, swapping with the track that had that team so teams
    stay unique.
  - An overridden track gets `confidence` 1.0, `manual: true` and `segments` with conf 1.0.
  - `track_conf` = team_conf.
- **v1 matches** whose tracks carry time-series features (`feat.ocr` / `feat.app` from
  `identity.extract_features`, a second video pass) use a per-(match, alliance) HMM over the
  3! assignments. Swaps are possible when tracks are close. Emissions are OCR, appearance
  prototypes, climbs and overrides. `method: "hmm"`. **The worker never runs
  `extract_features` any more**, so this path is effectively dead.
- **Other v1 matches** use the legacy per-track solver: height, colour histogram, start
  position, TBA climbs. It is measured at chance level (34% vs 33% random). `confidence` is
  capped at `LEGACY_CONF_CAP` = 0.2, and `method: "legacy"`.

**Overrides file.** `DATA/events/<ev>/overrides.json` =
`{"<match key>": {"red": {"<track idx>": "frc1234"}, "blue": {...}}}`.
- Written by `PUT /app/v1/events/<ev>/overrides/<match>`: directly by a strategist+, or after
  approval for a scout's request (see server.md). Hand edits also work.
- Takes effect on the next poll's `build_site`, with no reprocessing. Site tracks get the new
  `team`, so shots are re-attributed through `team_of`.
- The track's per-frame `conf` in the site JSON is **still the tracker's**. A manually
  relabelled track therefore stays subject to quarantine wherever the tracker was unsure.

**Quarantine** (`ID_CONF_MIN` = 0.5) is applied in two places:
1. **Analytics.** `enrich.py:quarantine` gives analytics a copy of the match in which frames
   with `conf` < 0.5 are unobserved. A team's movement, defense and other stats then use only
   stretches where the system is confident who it is.
2. **Shot credit** (`worker.py:build_site`). A shot launched while the shooter track's conf at
   the launch frame is < 0.5 gets `team: null, unattributed: "low_identity_conf"`.

Downstream, `live/coverage.py` calls a match **verified** for a team only if
`identity.method` starts with `ocr` **and** that track's `team_conf` ≥ 0.6. Only verified
matches feed team stats (see strategy.md).

> **Docs vs code.**
> - `deploy/README.md` ("How it works" §3) says `identity.py` labels robots by solving the whole
>   event at once, and that one fix "re-solves the whole event … so one fix also improves other
>   matches". In current code that is true only for v1 matches. v2 matches (all production
>   matches) are labelled inside the tracker per match. `assign_event` passes them through, and
>   an override affects only its own match.
> - The `identity.py` docstring still describes the HMM with OCR and appearance as the main
>   path. It is the fallback, and its feature extraction is not run by the worker.

### 4.7 Visual check — `live/verify_ids.py`

```
python live/verify_ids.py VIDEO CAMERA.json SITE_MATCH.json OUT.jpg [N_CROPS=12]
```

Builds a contact sheet: per track, N enlarged crops spread over the match. In each time bin it
picks the moment the robot is observed, isolated, slow and near the camera. Each crop is
captioned with time, team and that frame's conf (or the v1 segment's team). Use it to check
labels by eye.

---

## 5. Station cameras and hub cams

FIRST Washington posts two uploads per match:
- **"Wide Match N"**: the clean overhead camera, which is what the tracker uses.
- **"Match N"**: the same program with two fixed low driver-station cameras inset at the
  bottom. Red is x 0–622, y 708–1080; blue is x 1294–1920, y 708–1080. A middle inset crops
  the overhead camera and is ignored.

Above y = 708 the two uploads are pixel-identical. The default config **rejects** "Match N"
for tracking (`reject="^Match \d"`).

Camera positions:
- About 3.5 m outside the near side and 1.8–1.9 m up, level with the 1.829 m hub opening.
- Red at X ≈ 16.3 sees the red hub's +Y face (tags 2, 11) and the red tower.
- Blue at X ≈ 0.24 sees the blue hub (tags 21, 24, 26).

Everything in this section is **optional**. It runs only with `HUBCAM=1` (env or Admin →
options) or `reprocess.py --hubcam`. Production at the time of writing has it off
(`options.hubcam` null, no `HUBCAM` in `.env`). It costs about 1–2 min per match extra.

### 5.1 `live/stationcam.py` — sync, calibration, pose

- **`find_inset_video(tba_match, meta_cache)`** returns the "Match N" upload.
  - The title must match `^Match <n>( (…))?$`, where n is the match number (qm/f) or the set
    number (sf). Titles starting with "Wide" are excluded.
  - Height must be ≥ 1080 and duration < 600 s. FIRST Washington's channel is preferred.
  - It is downloaded to `MEDIA/<ev>/insets/<match>.mp4` with the same `videos.download`.
- **`sync_offset(wide, inset)`** maps inset time to wide time. It compares grey thumbnails of
  the shared region (`SYNC_REGION` or the layout's `sync_region`) plus the clock box.
  1. Coarse: cross-correlate motion energy, ±30 s, on 5–45 s.
  2. Fine: direct frame comparison ±0.15 s, in 15 s windows every 40 s.
  3. Reject windows with error > 1.6× the median, or more than 3 frames off a robust line.
  4. Result: one constant offset if all offsets agree within 1.5 frames. Otherwise piecewise
     knots, because FIRST HQ re-encodes at 59.94p drift by about 0.25% (e.g. 2026wasam qm19).

  Output `{offset, constant, knots, windows, err, conf, ok, dt}`. Validated against clock
  ticks to ±1 frame at 30 fps. Offsets are 0.03–0.30 s.
- **Calibration** is done once per event, in **inset pixels**:
  `events/<ev>/station_{red,blue}.json` = a camera JSON plus `crop`, `which`, `tag_ids` and a
  `calib` block.
  - 2026wasam's were fitted jointly over 9 matches (`stationcam.py:fit_station`,
    `collect_pairs`).
  - Inputs: tags (σ 0.3 px) plus about 3900 static-ball pairs per camera, with the balls
    located by the overhead camera.
  - Result: f ≈ 804/813, k1 only, tag RMS 0.2–0.6 px, ball median about 1 px.
  - The wizard (§2.2) can now produce these for other events.
- **`check_station_pose(cam, inset, which)`** finds tags in about 28 inset frames.
  - If the median error is > 0.7 px it re-aims the camera, rotation only. A bump re-aims a
    camera rather than moving it, and with two coplanar tags a free position fit trades
    0.7° against 9 cm.
  - Only if rotation can't get the RMS under 0.8 px does it also free the position, with a
    0.10 m prior.
  - The camera is `usable` only if the final RMS is < 1.5 px.
  - In 2026wasam the blue camera was re-aimed between qm1 and qm10 (12 px), and this fixed it.
- **`StationCams(event_dir, match_json, inset, sync=None, wide_video=None)`** bundles both
  cameras for one match:
  - `.cam`, `.usable`, `.pose_info`;
  - frame iteration in wide time: `.frames()`, `.frame_at()`;
  - geometry: `.project()`, `.visible()`, `.ray()`, `.ground()`, `.robots_at()`, `.to_full()`.
- CLI: `sync | check | sheet | calibrate`.

### 5.2 `live/station_ocr.py` — bumper reads from the station views (into the ILP)

Called through `worker.py:station_reads` → `station_ocr.extra_reads_fn(cal_dir, inset, wide)`,
which `process.process` runs after `prepare()`. Robots near an alliance end face that station
camera, and their numbers are 2–3× larger than in the overhead view.

1. Read inset frames at `STOCR_HZ` (5 Hz), synced to wide time. The station picture leads the
   overhead one by `STOCR_LEAD` (0.08 s), so each station frame is matched to the overhead
   instant `t + 0.08`.
2. Project the overhead detections of that instant into each usable station view.
   - Skip robots beyond `STOCR_RANGE` (11 m) from the camera.
   - Crop ±(0.5 + `STOCR_MARGIN` 0.35) m, z 0–0.6 m, with at least `MIN_PX` (14) px.
   - Upscale ×3 and OCR upright only (`STOCR_ANGLES` = 0; ±25° gives +4% reads at 2× cost),
     with look-alike glyphs on (`STOCR_CONFUSABLE`).
3. Back-project each read's text centre to z = 0.12 m and push it 0.40 m away from the camera
   (bumper face → robot centre). The uncertainty is anisotropic: lateral σ = 0.45 m; depth
   σ = 0.30 + 0.03·range m.
4. **`debias`**: per camera, shift read locations by the robust median offset to the overhead
   detections they clearly belong to. Measured up to 0.5 m sideways per camera.

Rows: `[t_video, cand, lp, margin, x, y, dir_x, dir_y, sig_along, sig_perp, cam, u, v]`, plus 7
open-set columns. **Attribution** happens in `fuse.py:attribute_extra`:
- match each read to the nearest overhead frame within `X_TOL_S` (0.05 s), then to the
  detection at the smallest normalised distance;
- drop it if that distance is > `X_GATE` (2.5);
- drop it if the runner-up is within `X_AMB_DROP` (1.0) normalised units; flag it ambiguous if
  within `X_AMB_FLAG` (2.0).

Station reads then join the ILP with weight `X_W` (1.0) × `read_weight`.

Measured on 10 dev matches (563 GT samples):

| | without station reads | with |
|---|---|---|
| all samples right | 88.1% | 91.7% |
| answered right | 95.6% | 98.5% |
| wrong answers | 23 | 8 |
| station reads correct | – | 202/202 |
| reads per match for never-read teams | ~0 | 25–330 |

Untested tuning hint: `W_R=0.2`, `J_LIMBO=24` scored higher with station reads; check it on
held-out matches first.

### 5.3 `live/station_export.py` — data for the Analyze camera switcher

```
python live/station_export.py DATA MEDIA EVENT MATCH_KEY
```

Writes `DATA/events/<ev>/stations/<match>.json`:

```
{video: "media/<ev>/insets/<match>.mp4",
 sync: {offset, knots, constant},
 cams: {red|blue: {W, H, K, dist, rvec, tvec, crop, usable, pose}}}
```

`build_site` copies it into the site match JSON as `stations` when the inset video exists.

### 5.4 `live/hubcam.py` — station-view check of shot outcomes

Run by `enrich.py:station_pass` (under `HUBCAM=1`) after the shots exist:
`hubcam.run_match(cal_dir, match, events, fuel, wide, inset, tba, sync)`.

1. Detect yellow blobs in a hub ROI of each inset: chroma yellowness minus a temporal-min
   background (±12 frames, excluding ±3). Balls are 6–25 px; min area 10.
2. For every hub-bound shot, the shot's own 3-D arc (from `shots.py`) predicts the ball in
   each station frame. The nearest blob within a shrinking gate (45 → 5 px) is taken, and one
   ballistic arc is refit jointly to the overhead track and the station sightings. The two
   views are about 70° apart, which pins depth near the hub.
3. Evidence: rim-plane crossing distance (≤ 0.50 m from the hub centre = entry, ≥ 1.0 m =
   clear miss), sightings inside the funnel, bounce-outs (a ball rising 25 px after the rim),
   crowding, and the distance of the sightings from the shot's own arc.
4. **Decide, conservatively.**
   - An outcome flips only when the station ball is on the shot's own arc (≤ 15 px median,
     ≥ 6 sightings) and the overhead fit is not degraded.
   - Otherwise the station view only confirms: a made shot with the entry seen gets
     `outcome_conf` 0.95.
   - Wrong-ball matches in volleys were the failure mode of looser versions.
5. Station-only entries (balls into the funnel that no shot explains) are listed in
   `events.station_entries` but **not** added to reconciliation.
6. Output, all additive: `shots[].hubcam = {hub, status, verdict, outcome0, outcome_conf0, evidence…, arc_joint, obs}`,
   `events.hubcam` stats, and `events.reconcile` recomputed (the old one is kept as
   `reconcile_overhead`).

`station_pass` then rewrites `shots/<match>.json` and re-runs analytics.

Measured on the dev set:

| | value |
|---|---|
| made shots confirmed | 36%, with 16/16 precision |
| held-out outcome accuracy | unchanged (0.892) |
| reconciliation | unchanged |
| hub shots with no station match | ~45% (edge of view, occlusion) |
| cost | ~137 s/match, of which ~105 s is sync + pose |

It boosts confidence; it does not fix accuracy.

> **Docs vs code.** `live/HANDOFF.md` says hubcam is "done; NOT in the pipeline yet". It *is*
> wired in: `worker.py:process_match` → `enrich.py:station_pass` → `hubcam.run_match` when
> `HUBCAM=1` (since commit e42b461). It is off by default.

---

## 6. Offline tools

### 6.1 `live/reprocess.py` — re-run matches from kept videos

```
python live/reprocess.py DATA MEDIA EVENT --only qm17,qm18 [--jobs 2] [--gpus 0,1] [--model PATH] [--hubcam]
```

For each match, in a process pool with one GPU per job (round-robin):
- reads the old match JSON (for `camera_pose` as `pose_guess` and `video`), the TBA match from
  `DATA/tba/event__<ev>__matches.json`, `camera.json` and `MEDIA/<ev>/<key>.mp4`;
- runs `process.process` (with station reads under `--hubcam` if the inset exists), then
  writes the match JSON;
- runs `enrich.enrich`, and `enrich.station_pass` under `--hubcam`.

Differences from the worker:
- No `event=` context, so there is no open-set scoring or integrity check.
- No YouTube.
- No site rebuild; the worker's next poll does that.
- Default `--gpus 0,1`. On atomosserver, agents were told to use GPU 1 only (HANDOFF).

### 6.2 Detector training — `live/train/`, `live/make_labels.py`, `live/bglabel.py`

These ran once on atomosserver. Inputs are kept in `DATA/training/` so a rebuild never
re-downloads.

1. `live/train/dl.py` downloads 6 public FRC robot datasets from Roboflow (`RF=<key>`). The
   free tier is about 10 credits a month, so it skips datasets already present under `rf/`.
2. `live/train/pool.py` normalises them into one-class YOLO (`pool/`, 8% val).
3. Round 1: `yolo detect train data=pool/data.yaml model=yolo11s.pt imgsz=960 epochs=40`.
4. `live/make_labels.py CAMERA.json MODEL.onnx OUTDIR VIDEO... [--val VIDEO...]` auto-labels
   event footage every `STEP` (2 s):
   - round-1 detections at conf ≥ `DET_CONF` (0.35);
   - plus robots the detector missed, from `live/bglabel.py`. These are model-free
     proposals: foreground vs the median plate, not ball-yellow, size consistent with a
     0.91 m robot projected at that field position, grown upward to a whole-robot box.
5. `live/train/r2.sh` fine-tunes round 1 at 1280 on pool + event labels (event labels
   duplicated ×2), on both GPUs, 30 epochs.
6. Export: `yolo export model=r2.pt format=onnx imgsz=544,1280 half=True simplify=True opset=17`,
   then copy to `DATA/models/robot.onnx`.

Training environment: `uv venv -p 3.12 trainenv && uv pip install ultralytics==8.4.138 roboflow==1.4.2 onnx onnxslim`.
The detector is not the accuracy bottleneck (`docs/COMPONENTS.md`: position error is flat at
~0.125 m over 3× mAP). A very different camera angle may still need the event fine-tune.

### 6.3 Other one-off modules in `live/`

- `live/analytics_demo.py`, `live/overlay.py`, `live/enrich_all.py`: see §10, §13, §11.
- The `src/` scripts are the original autoscout tools (see files.md). The worker imports only
  `src/track.py`, `src/fit6.py` and `src/assign.py`, through the geom shim.

---

## 7. Fuel tracking (`live/fuel.py`)

Finds every **moving** FUEL ball in the overhead camera picture and follows it in 2D image space. No neural
network: it is a colour + motion detector with a small multi-target tracker. Shots (section 8) are built on
top of these 2D tracks.

### 7.1 Entry points

| call | what |
|---|---|
| `fuel.py:track_fuel(video_path, cam, t_from, t_to, log, frame_iter=None, fps=None, cv_threads=None, layout=None)` | decode `[t_from-0.3, t_to]` with PyAV and return a `FuelResult` |
| `fuel.py:FuelTracker(cam, fps, ...)` + `.push(idx, t, bgr)` / `.push_av(idx, t, av_frame)` + `.result()` | stateful tracker; `push_av` is the fast path (reads the YUV planes directly, no RGB conversion) |
| `fuel.py:load_camera(cam_json, match_json=None)` | `camera.Camera` with the match's `camera_pose` (rvec/tvec) applied if the match JSON has one |
| `fuel.py:overlay_from_layout(cam, video_path, layout)` | broadcast-graphics boxes from the event's `layout.json` (via `live/layout.py`), else `None` |
| CLI | `python live/fuel.py VIDEO CAMERA.json T_FROM T_TO OUT.json [MATCH.json]` |

In production it is called from `enrich.py:run_fuel_and_shots` with
`t_from = t_start - 1.0`, `t_to = t_start - 0.5 + frames/30 + 3.0` (3 s after the buzzer for balls still in
the air) and `cv_threads=1` (same wall time on these small images at about a third of the CPU).

### 7.2 Per-frame processing

1. **ROI.** `_field_box_hull(cam)` projects the field box extruded up to `ZMAX = 5.0` m (shot apexes are far
   above the carpet in the image) and pads it by 0.3 m. Everything else is ignored. Detection runs at
   `SCALE = 2` (half resolution).
2. **Overlay mask.** Broadcast graphics are blacked out of the ROI mask. Default boxes are
   `OVERLAY_BOXES` (1920x1080, FIRST 2026 graphics: title/score bar `(446,8,1474,134)`, clock, alliance boxes,
   event banner). If the event has a `layout.json` with scoreboard/clock/mask/inset boxes, those replace the
   defaults (`overlay_from_layout`); no layout means the hard-coded boxes, unchanged.
3. **Frame gating** (`_gate_frame`), in this order:
   * **Dark frame**: sample mean luma `<= NO_FEED_LEVEL = 20` means the feed is lost. Counted in `stats.dark`;
     the first dark frame is also treated as a scene cut (the feed may come back as something else).
   * **Histogram jump** (`_hist_jump`): % of the 16-bin luma histogram mass that moved versus ~`CUT_LAG = 0.1` s
     ago. `>= CUT_HIST = 10 %` is a transition (graphics, another camera, a wipe or dissolve) and triggers
     `_scene_cut`. Measured on clean 2026wasam uploads: the histogram shifts at most 3.5 % even over 0.5 s;
     real cuts shift 20-80 %.
   * **Hard cut**: % of sample pixels changed by more than 10 levels vs the last processed frame
     `>= CUT_D = 35 %` and `>= 6x` the running mean (`ema_diff`). Camera bumps on clean uploads reach ~21 %.
   * **Duplicate frame**: broadcasts are 60 fps containers carrying 30p content, so every other frame is a
     re-encoded repeat. A frame changed less than `0.3 x ema_diff` is a duplicate and skipped (`stats.duplicates`).
     Everything downstream works in seconds, so true 60p or 30 Hz input works the same.
   * **Inflated-threshold guard**: `DUP_RUN_MAX = 4` "duplicates" in a row while the picture still changes by
     `>= DUP_STUCK_D = 1.0 %` means `ema_diff` was inflated by a missed transition; it is re-seeded
     (`stats.dup_resets`).
   * **Moving camera**: phase correlation between consecutive samples with shift > 0.3 px and response > 0.05
     (handheld / other camera / bumped mount). The frame is skipped and the background reset
     (`stats.camera_moving`).
4. **Red/blue swap check** (`_swapped_bgr` / `_swapped_yuv`): the broadcast sometimes swaps colour channels
   for whole frames. Two points on the red and blue bumps (on the camera's side of the field) are sampled; if the
   red bump reads blue and vice versa, the crop is un-swapped (`stats.swapped`). The same test records whether
   the picture looks like the calibrated overhead view at all (`self.view`, smoothed +-2 s, becomes each track's
   `view_ok`).
5. **Yellowness map.** From YUV: strongly negative Cb with near-neutral Cr (`yellow_from_uv`); from BGR:
   `min(R,G) - B` (`yellow_from_bgr`), same scale. Orange/red has high Cr and is rejected.
6. **Asymmetric running background** (`_step`): the background yellowness rises slowly
   (`BG_TAU_UP = 0.8` s: a ball that comes to rest is absorbed in about a second) and falls fast
   (`BG_TAU_DOWN = 0.05` s: a ball that leaves is forgotten at once, so the next ball of a stream on the same path
   still counts as new).
7. **Motion mask**: pixel is fuel-in-motion when yellowness `>= TY = 60`, above the dilated background by
   `>= TD = 45`, and new vs the previous processed frame OR vs ~0.1 s ago (kills things that just stopped while
   keeping slow movers and streams). If more than `GLITCH_FRAC = 3 %` of the ROI fires, the frame is a glitch /
   wipe: background restarted, frame dropped (`stats.glitch`).
8. **Blobs** (`_blobs`): connected components (area 2..600 half-res px) -> weighted centroid, radius, 2nd
   moments (streak length + angle). The shutter is fast, so one blob is about one ball diameter long. Blobs
   whose area implies `k >= 2` balls and are elongated (> 2.2) or `k >= 3` are split by a small weighted
   k-means along the major axis (k <= 6).
9. **Floor snapshot** every `floor_every = 2` s (`_floor`): static yellow (`>= 110`) blobs on the ground mask,
   projected to the carpet -> ball counts per zone and a 1 m heat grid (coarse; piles are undercounted).

### 7.3 Tracking (`_track`, `_Track`)

* Per-track prediction: quadratic fit of the last <= 10 observations (`_qfit`); young tracks use an acceleration
  allowance `AMAX = 4000` px/s^2. Gate: `VMAX = 2400` px/s (fastest plausible image speed).
* Birth links are constrained by the blob's streak direction (an elongated blob moving perpendicular to its
  streak is not the same ball).
* Hungarian assignment (`scipy.optimize.linear_sum_assignment`) on a normalised cost; cost > 1 is forbidden.
* Coasting: established tracks (>= 5 obs) survive `COAST_S = 0.15` s unobserved, young ones `COAST_YOUNG_S = 0.045` s.
* Output filter (`_finalize_seg`): `MIN_DETS = 5` detections, spanning `MIN_SPAN_S = 0.12` s, moving
  `MIN_DISP_PX = 24` px, local jitter `<= MAX_LOCAL_RMS = 3.5` px (higher = association noise).

### 7.4 Post-processing (`result()`)

1. `stitch(tracks, max_gap=0.25, cuts)`: join fragment A -> B when B starts within 0.25 s after A ends and each
   one's quadratic extrapolation lands on the other. **Never across a scene cut** (`_cut_between`).
2. `split_track`: split again at gaps the motion does not bridge (e.g. a shot entering the hub top, then a ball
   leaving its base).
3. `finalize_track` -> per-window **airborne evidence** (`_window_evidence`, windows <= 0.8 s). Noisy-OR of:
   * (a) a linear 3D fit (`_Models.ballistic`: gravity + small drag, `DRAG_SIGMA = 1.5` m/s^2 prior) explains the
     path much better than rolling on the carpet (`_Models.rolling`): `ratio = rms_rolling / rms_ballistic`,
     evidence ramps from 1.5 to 4;
   * (b) image-space parabola whose downward curvature is 0.4-2.5x the gravity expected at that image spot
     (`gravity_px`), robust to the depth ambiguity of short 3D fits;
   * (c) implied rolling ground speed > ~5 m/s (fuel can't roll that fast);
   * (d) ground projection leaves the field.
   Shape evidence is down-weighted for short windows. `airborne_score` = best window.
4. `merge_ballistic(out, M, max_gap=0.6, cuts)`: a second stitching pass for airborne fragments (score >= 0.3):
   join A to a later B when ONE free 3D flight (<= 2.5 s, z in [-0.4, 7] m) explains both. This bridges an apex
   hidden behind the score bar, a net or a robot. The next ball of a volley flies the same path 0.1-0.2 s later,
   which one time-parameterised flight can't fit, so volleys are not merged.
5. `view_ok` per track from the bump-colour view test.

### 7.5 Scene cuts (commit 97d184d)

Livestream clips, spliced uploads, wipes and inserted graphics break the running statistics. Before this fix, a
hard cut seeded `ema_diff` with ~81 % changed pixels, after which every real frame was called a duplicate and the
tracker was blind (livestream clip of 2026wasam qm13: frozen lead-in, then a cut -> every auto shot lost).

`_scene_cut(t)` now: records `t` in `self.cuts`, clears `ema_diff`, the duplicate run, the background, the recent
frame buffer, and finishes all live tracks. Frames up to `CUT_LAG` after a cut compare against the old scene and
are skipped. `stitch` and `merge_ballistic` never join across a recorded cut. Stats: `cuts`, `cut_t` (first 50),
`cut_frames`, `dup_resets`.

Tests: `tests/test_fuel_cuts.py` (no GPU, no video; synthetic 30p-in-60p sequences through `FuelTracker.push`,
needs `events/2026wasam/camera.json`): `test_clean_sequence_unchanged`, `test_frozen_lead_in_then_cut`,
`test_cut_to_graphics_and_back`, `test_wipe_transition`, `test_inflated_threshold_recovers`,
`test_no_stitching_across_cut`.

### 7.6 FuelResult (in memory)

```
{"fps", "t_from", "t_to",
 "tracks": [{"id", "f": [video frame idx], "t": [video s], "uv": [[u,v] full-res px], "r": [px radius],
             "streak": [px], "moving": true, "airborne_score": 0..1, "view_ok": 0..1, "diag": {...}}],
 "floor": {"t", "zones": ["blue_alliance","neutral","red_alliance"], "zone_x": [4.028, 12.513],
           "counts_by_zone": {...}, "heat": [[...]] (rows Y, cols X, 1 m), "heat_cell_m": 1.0, "note"},
 "stats": {frames_in, frames_processed, duplicates, dark, swapped, glitch, camera_moving, cuts, cut_t,
           cut_frames, dup_resets, view_ok_frac, detections, raw_tracks, tracks, airborne_tracks,
           t_detect_s, t_track_s, roi, wall_s}}
```

The full result (~10 MB per match) is **not kept**. `enrich.py:airborne_tracks` writes only airborne tracks
(`airborne_score >= 0.5`, `view_ok`, >= 3 samples) to `data/events/<ev>/fuel/<match>.json` as
`{"tracks": [{"id", "t": [...], "uv": [[u,v]...] (ints), "s": airborne_score}]}` for the in-browser player.
The site copies it to `site/api/<ev>/m/<match>.fuel.json` (section 12).

### 7.7 Performance and accuracy (HANDOFF.md; dev set on atomosserver)

* ~4-9 ms CPU per processed frame incl. decode; 37-64 s per match.
* ~1500 airborne tracks per match, about 2.5-3x the real shots (fragments + non-shots). Shots (section 8) filters
  them.
* Per-ball recall in dense streams ~70 %; airborne precision ~80-85 %.
* Failure modes: touching balls in dense volleys; apex hidden behind the score bar (y < 134 px, x 446-1474);
  yellow shirts; balls shoved by robots; wrong/moving camera (`view_ok` = 0, ignore those tracks).

---

## 8. Shot reconstruction (`live/shots.py`)

Turns the 2D fuel tracks into shots: who launched each FUEL flight, from where, with what 3D arc, and whether it
was made, missed or a pass.

### 8.1 Entry points and I/O

* `shots.py:reconstruct(fuel, match, cam, rules, tba_match=None, log)` -> `Reconstructor(...).run(fuel)` -> Events.
* `shots.py:find_tba_match(tba_json, key)`; `shots.py:hub_schedule(tba_match)`; `shots.py:reconcile(...)`.
* CLI: `python live/shots.py FUEL.json MATCH.json CAMERA.json RULES.json OUT.json [TBA_MATCHES.json]`.
* Inputs: FuelResult; processed match JSON (`t_start`, `t_teleop_start`, `feed_gaps`, `tracks[6].xy` cm @ `hz`,
  `camera_pose`); `camera.Camera` with the match pose; `live/field/rebuilt_rules.json`; the TBA match
  (`score_breakdown`) - optional, but without it hub-active and reconciliation are unknown.
* Output: written by `enrich.py` to `data/events/<ev>/shots/<match>.json` (see 8.6).

### 8.2 Candidates (`candidates`, `dedupe`)

A fuel track becomes a candidate flight when `view_ok >= MIN_VIEW = 0.5`, it has `>= MIN_N = 6` samples,
`airborne_score >= MIN_AIR = 0.5` (or `>= MIN_AIR_HUB = 0.15` if it ends inside a hub opening), and it does not
start inside a feed gap. Duplicate parallel tracks of one motion-blurred ball (closer than `DUP_R = 1.4` ball
radii) are dropped.

### 8.3 Physics: batched ballistic least squares (`_Fitter.solve`, `fit_hyps`)

Model: linear drag + gravity + a small free constant acceleration with a Gaussian prior:

```
X(tau) = p + v*phi1(tau) + (a - g*z_hat)*phi2(tau)
phi1 = (1 - e^(-c*tau))/c,   phi2 = (tau - phi1)/c,   c = C_DRAG = 0.15 /s
```

`C_DRAG` approximates quadratic drag of the 0.2 kg / 0.15 m foam ball at shot speeds (k|v| with k ~ 0.025/m at
~10 m/s). For a **fixed launch time** this is linear in `(p, v, a)`: each pixel observation gives two linear
equations (DLT on undistorted normalised coordinates, reweighted by 1/depth so residuals are in pixels). So every
shooter hypothesis x every launch time is solved as one batched least squares. Gravity fixes the metric scale;
the launch-point prior fixes the remaining monocular depth ambiguity.

Noise: `SIG_PX = 2.5` px (chi^2 scale); `SIG_ACC = 0.7` m/s^2 prior on the free acceleration.

### 8.4 Shooter = discrete choice (`_sources`, `_mu`, `_gap_cost`)

Hypotheses for each flight:

| source | launch prior |
|---|---|
| each of the 6 robots | its tracked position at the launch time, z = `ROBOT_H0 = 0.55` m, sigma 0.45 m (xy) / 0.35 m (z) |
| each human player (red, blue) | behind the alliance wall at the outpost, z = `HP_H0 = 1.7` m, sigma (0.35, 0.5, 0.4) |
| "floor" | a bounce / kicked ball off the carpet below the first sighting (`FLOOR_SXY = 1.5` m, max upward speed `FLOOR_VZ = 4.5` m/s), handicapped by `FLOOR_MARGIN = 4.0` |

Launch time is searched on a 1/30 s grid (`TAU_STEP`) up to `TAU_MAX = 0.9` s before the first sighting.
Cost = reprojection chi^2 + priors + a penalty for predicted flight that should have been **visible but was not
tracked** (free for `GAP_FREE = 0.10` s, scale `GAP_SIG = 0.12` s). Lowest cost wins.
`conf = 1 - exp(-margin/4)` where margin = runner-up cost - best cost. A fit RMS above `MAX_RMS = 6` px makes the
shooter `unknown` (conf x 0.3); a winning "floor" hypothesis on a real shot also becomes `unknown` (conf 0).
Floor-launched non-scoring flights are dropped as hops. `CROSS_PEN` and `AUTO_SIDE_PEN` exist but are 0 (off):
penalties moved shots to far-away robots when the upstream identity was wrong.

### 8.5 Track repair and outcome (`split_bad`, `join_fragments`, `join_continuations`, `mark_bounces`, `decide`)

Run order in `Reconstructor.run`: candidates -> `analyse` -> `split_bad` -> `join_fragments` ->
`join_continuations` -> `mark_bounces` -> `decide` per candidate -> `shot_record` -> `volleys` -> `reconcile`.

* `split_bad`: a hub-ending track that fits no single arc (`> SPLIT_RMS = 6` px; ball in hand at the start, or the
  tracker switched balls) keeps its longest clean tail (tails of 1.0/0.8/0.6/0.45 s, `<= SPLIT_TAIL_RMS = 4.5` px).
* `join_fragments`: fragments of one flight (gap `<= JOIN_GAP = 0.8` s) are joined when one ballistic fit explains
  all pieces.
* `join_continuations`: a short piece (`>= CONT_MIN_N = 3` samples, gap `<= CONT_GAP = 0.35` s, within
  `CONT_NEAR_PX = 60` px of an opening) re-appearing in the funnel after passing behind the hub-net poles is
  appended to the flight whose arc predicts it. (This fixed the blue-hub undercount.)
* `mark_bounces`: a ball popping back UP out of the hub right after an entry marks that entry a bounce-out
  (-> missed); the bounce track is not a new shot.

**Made** (`decide`): the track ENDS inside the image of the hub opening (circle `HUB_POLY_R = 0.65` m at the
opening height 1.829 m, up to `FUNNEL_DZ = 0.20` m above the rim) or inside the clear funnel below it (down to
`FUNNEL_Z0 = 1.60` m; `END_Z_DEEP` anchors), AND:
* a fit with the last sighting anchored at the opening costs at most `MADE_DC = 25` more than the free best fit
  (`MADE_DC_INACTIVE = 8` into an inactive hub, `MADE_DC_HP = 20` for human-player lobs);
* it is not beaten by "came down on the carpet behind the hub" by more than `LAND_MARGIN = 8`;
* it was above the rim and falling at the end, with no pop-up above `FUNNEL_POP_DZ = 0.35` m;
* it started at least `START_OUT_PX = 20` px outside the funnel (not a ball already in the hub);
* human-player lobs need an end inside the opening only when flying away from the camera or into an inactive hub.

**Missed**: a non-scoring flight whose second half passes within `MISS_PX = 40` px of an opening, or whose 3D arc
passes within `HUB_R_NEAR = 1.6` m, **and** was launched by a robot from that hub's alliance zone
(+`ZONE_MARGIN = 0.5` m). Lobs from the neutral zone over/into the hub toward the alliance zone and HP throws are
passes, not misses (labelled by eye, `.work/shots2/labels_*.json`).

**Pass**: lands `>= PASS_MIN_RANGE = 3.0` m from launch with apex `>= PASS_MIN_APEX = 1.5` m, away from hubs.
`pass_kind = "feed"` with `receiver_idx` when it ends at / lands within `FEED_R = 1.0` m of a teammate, else
`"floor"`.

**Not shots**: start and end on one robot (hopper churn, `in_hopper`); end low within `END_AT_ROBOT = 0.9` m of a
robot (intake); horizontal travel `< MIN_TRAVEL = 1.2` m; apex `< MIN_APEX = 1.0` m or speed `< MIN_SPEED = 3.0`
m/s (hop / kicked ball); launch faster than `MAX_SPEED = 16` m/s (fit failure); non-scoring flights shorter than
`MIN_DUR_OUTCOME = 0.45` s, landing more than `LAND_OUT_M = 0.5` m outside the field, or fitting worse than
`JUNK_RMS = 20` px.

`outcome_conf` (`oconf`): 0.9 for a made call with `made_dc < MADE_DC/3`, else 0.7; misses 0.5-0.8 depending on
evidence (bounce-out, end near the hub); passes 0.6. hubcam (§5, `HUBCAM=1`) can raise confirmed makes to 0.95.

### 8.6 Periods, hub state, grace

* Clock (`Reconstructor.clock`): replay time -> match clock via `analytics.match_clock` (frozen at 20 during the
  pause). Replay times 20 s to max(T, 23 s) (`T = t_teleop_start - 0.5`) count as **auto**, so the auto->teleop
  pause and the 3 s assessment after the auto timer belong to auto.
* Periods from `rebuilt_rules.json` (match clock, s): auto 0-20, transition 20-30, shift1 30-55, shift2 55-80,
  shift3 80-105, shift4 105-130, endgame 130-160.
* `hub_schedule(tba_match)`: both hubs active in auto, transition, endgame. The alliance that scored more FUEL in
  auto (`hubScore.autoCount`) has its hub **inactive in shift 1**, then it alternates. Tie: FMS picks at random,
  inferred from which shifts the counts landed in (`shift1+shift3 - shift2 - shift4`). Without TBA every shift is
  `None` (unknown). Returns `({"red": {period: bool|None}, "blue": ...}, first_inactive_alliance)`.
* Grace: FUEL entering `<= GRACE_S = 3.0` s after a hub deactivates counts for the previous period
  (game manual 6.5: assessment continues 3 s). `shot.counted_period` holds the period it counts for.
* `hub_active` on a shot = state of the TARGET hub at entry (or at the nearest approach / launch). Made into an
  inactive hub = "uncounted" in TBA terms (0 points).
* `volleys(shots, gap=VOLLEY_GAP = 1.0 s)`: consecutive shots by the same shooter <= 1 s apart share a `volley` id.

### 8.7 Reconciliation (`reconcile`)

Per alliance hub, per period: traced made (outcome made, target that hub, `counted_period` = p, hub not inactive)
vs TBA `hubScore.<period>Count`; also `uncounted` (made into an inactive hub vs TBA `uncounted`), `total` (vs
`totalCount`), `by_shooter` (`robot:<idx>` / `human:<alliance>` / `unknown:None`), `traced_attempts`,
`cross_alliance_made`. Each period carries `coverage` = fraction of the period with live feed inside the
fuel window (`Reconstructor.coverage`: feed gaps and 0.5 s bins dominated by `view_ok`-bad tracks don't count).
Compare only where coverage is ~1. Downstream, `live/coverage.py:reconcile` gates per-team FUEL stats on this
(see strategy.md).

### 8.8 Events file: `data/events/<ev>/shots/<match>.json`

```
{"shots": [{t (video s, launch), match_t (replay s from auto start), match_clock, period,
            shooter: {kind: robot|human|unknown, alliance, track_idx},
            origin [x,y,z] m, v0 [vx,vy,vz] m/s, acc [ax,ay,az], drag (=0.15), landing [x,y], apex_z, speed,
            target: "red_hub"|"blue_hub"|null, outcome: made|missed|pass|unknown, hub_active, counted_period,
            t_entry, hub_dmin, fit_rms_px, n_obs, t_obs [first,last], conf, outcome_conf,
            alt {kind, alliance, track_idx, dcost} (runner-up shooter), vis_gap, fuel_ids, excess,
            made_dc, land_dc, bounced, pass_kind (feed|floor|null), receiver_idx, cross_alliance,
            robot_xy, robot_speed (robot shooters), volley}],
 "intakes": [...section 9...],
 "first_inactive": "red"|"blue"|null, "hub_active": {"red": {period: bool|null}, "blue": {...}},
 "reconcile": {"red": {...}, "blue": {...}, "coverage": {period: frac}},
 "stats": {candidates, duplicates, joined, continued, split, hops, bounce_outs, shots, made, wall_s},
 "timing": {fuel_s, shots_s, fuel_tracks},
 "hubcam"?: {...}   (only after enrich.station_pass with HUBCAM=1)}
```

Field frame: metres, X 0..16.541 (blue wall at X=0), Y 0..8.069 (Y=0 far wall). Absolute, not team-frame.

### 8.9 Accuracy (HANDOFF.md)

* Held-out 11 matches: traced/TBA made = 0.91 (median per alliance-match 0.92, r = 0.96); tuning set 0.93. After
  the shots2 fixes: held-out blue 0.853 -> ~0.94, red ~1.00.
* Attribution ~96 % on checkable shots; made calls ~90 %; missed ~60 %; missed-vs-pass 0.69 -> 0.83.
* ~560 shots per match (about 335 made, 180 missed, 45 pass).
* Cross-alliance attributions (~6 %) are upstream robot identity swaps, not shot errors.
* Weak spots: dense frc8032-style streams lose balls in fuel.py; feeds barely validated; red HP makes slightly over.

---

## 9. Intakes (`live/intakes.py`)

Detects when/where each robot picks FUEL up. Treat the counts as a **lower bound**.

* Entry: `intakes.py:detect_intakes(video, fuel, match, cam, rules, log, use_rolling=False, use_outpost=False,
  min_loss=MIN_LOSS)`. Called from `enrich.py:run_fuel_and_shots` inside `try` (an intake failure never loses a
  match; the import is optional). CLI: `python live/intakes.py VIDEO FUEL.json MATCH.json CAMERA.json RULES.json OUT.json`
  (env `MIN_LOSS`, `ROLLING`, `OUTPOST`, `BEF1`, `AFT1` for experiments).
* **FLOOR signal** (the only one on): the video is re-decoded at ~5 Hz (`SNAP_DT = 0.2` s). Static yellow pixels
  (`Y_THR = 110`) are projected to the carpet and weighted by 1/(ball pixel area) -> ball-count density on a
  `CELL = 0.1` m grid. Cells under any robot (disc ~0.75 m incl. hopper balls that project wrongly) are NaN. For
  each robot and time t, in a disc `R_DISC = 0.75` m around it: BEFORE = cell-wise min over [t-1.4, t-0.6] s,
  AFTER = cell-wise max over [t+0.6, t+1.4] s; loss = sum(BEFORE - AFTER) minus the gain in a ring
  (`R_RING = 1.6` m; balls shoved aside). Runs of loss `>= MIN_LOSS = 1.4` balls are one intake; n = round(loss);
  xy = loss centroid; t = robot's closest approach. Two robots claiming the same balls: the closer one keeps them.
* Gating: snapshots with total density more than 35 % off its local median (cuts, replays, moving camera) or
  sharpness `< BLUR_FRAC = 0.85` x the local 75th percentile (shake / zoom blur) are dropped. HANDOFF: "blur/cut
  gating essential".
* ROLLING (non-airborne track ending within ~0.8 m of a robot) and OUTPOST (robot parked < 0.35 m/s within 1.3 m of
  its outpost chute for >= 1.5 s) exist but are **off** (rolling ~10 % precise; outpost unvalidated).
* conf (floor): `1 - exp(-(loss-0.8)/1.5)`, scaled by the valid-cell fraction, capped 0.95.
* Output (in `shots/<match>.json` `intakes[]`): `{t (video s), match_t, track_idx, xy [x,y] m, n (1..40), conf, kind}`.
* Measured: precision ~0.88 (68 labelled events), recall ~0.45 (tiny sample); misses are robots parked on the
  balls. ~7 s per match.

---

## 10. Robot analytics (`live/analytics.py`)

Per-robot behaviour metrics for one match, from the tracks + shot/intake events, and their per-team aggregate.
Pure numpy; team-agnostic (keyed by track index; teams are joined at site-build time).

### 10.1 API

* `match_analytics(match, events=None, rules=None, tba_match=None)` ->
  `{"tracks": {idx: metrics}, "alliance": {...}, "fallbacks_used": [...]}`.
* `team_aggregate(per_match_list)` -> `{n_matches, matches, stations, lane_totals, trench_capable, role_counts,
  role_counts_events_only, auto_start_canon_mean, dead_matches, low_coverage_matches,
  metrics: {"group.path": {mean, median, std, min, max, n, trend, coverage} | {counts, n, coverage}}}`.
  `trend` = linear slope over match order when n >= 4.
* `cluster_autos(auto_paths_by_team, threshold=1.3)` -> per team `{labels: {match: "A"|None}, routines: [...],
  top_routine_freq, n_autos}`. Paths (coverage >= 0.6) are canonicalised, arc-resampled to 32 points, compared by
  DTW + 0.15 x length difference + 0.5 x start distance, then hierarchical clustering.
* `match_clock(t, match)`, `in_pause(t, match)` (also used by shots.py and scouting).
* `analytics_demo.py`: runs analytics over `.work/samples` and writes `.work/robot-analytics/report.json`
  (developer tool; needs the local sample set).

### 10.2 Conventions

* **Time.** Replay time = seconds from auto start (`frame/hz - 0.5`). The rules periods use the **match clock**:
  `match_clock(t) = t` for t <= 20, 20 (frozen) during the pause, `t - (T - 20)` after, with
  `T = t_teleop_start - 0.5`. The pause belongs to no period (kept in movement totals only). Reported times
  (arrival_s, crossings) are match clock.
* **Good frames**: observed (`obs == 1`), or interpolated across a gap `<= GAP_MAX_S = 1.5` s, and outside
  `feed_gaps`. Speeds above `V_GLITCH = 6.0` m/s are identity swaps / projection glitches (counted, excluded).
  Moving = `> V_MOVING = 0.30` m/s. Zone transitions use `HYST = 0.75` m hysteresis.
* **Canonical red frame**: blue positions are point-reflected `(x,y) -> (16.541-x, 8.069-y)`, so "own zone" is
  x > 11.915 and station k looks the same for both colours.
* **Coverage** per metric group (`out["coverage"]`). `team_aggregate` drops a match from a group when its
  coverage `< MIN_COV = 0.4`, and drops fuel metrics when the match had no events.
* **Fallbacks**: if `rebuilt_rules.json` lacks an entry, `FALLBACK` values are used and listed in
  `fallbacks_used`. Note the fallback periods (transition 23-33 ... endgame 133-163) are on a different basis from
  the rules file (20-30 ... 130-160); with the shipped rules file they are never used.

### 10.3 Metric groups (per track)

| group | content (selected keys) | method / thresholds |
|---|---|---|
| `movement` | distance_m, distance_observed_m, moving_speed_mean/p95, max_accel_p99, pct_moving, zone_frac, zone_transitions, crossings[] {t, dir, side, lane, y_canon, near}, lane_counts {bump, trench, open, unknown}, trench_capable, frac_crossings_high_y_canon | lanes from rules polygons (`Ctx.lane`), else y-band fallback |
| `auto` | start_xy(_canon), path (24 pts), end_xy, distance_m, enters_neutral, enters_opp_zone, first_neutral_s, tba_auto_tower | TBA `autoTowerRobotN` by station |
| `fuel` | n_shots, n_made, n_missed, accuracy (>= 3 decided), by_period, est_points (= made into a non-inactive hub; 1 pt each), uncounted_made, volleys, fire_rate_in_volley, volley_size_max/p90/mean, cycle_time_s, gap_between_volleys_s, shot_distance {...}, shoot_on_move_frac (speed > 0.5 m/s, >= 3 shots), launch_speed {mean, cv}, miss_pattern, passes {...}, intakes {n, per_min, zone_counts, near_outpost, per_cycle} | volleys here: gap <= **1.2 s** on the match clock (shots.py uses 1.0 s); fire rate = median over volleys of >= 3 balls; cycle = median gap between volley starts (< 45 s) |
| `defense` | defending_s/frac, n_contacts, pins_made, defended_s, retained_rate, defense_effectiveness, mean_defended_run_s | defending: opponent within 1.5 m, in the opponent's half while the victim is at home, sustained >= 2 s (gaps <= 0.5 s merged). Contact: centres < 1.0 m for >= 0.4 s. Pin: contact >= 2 s, victim nearly stopped (< 0.3 m/s), not at tower/outpost; attacker = the one that arrived |
| `endgame` | tba_end_level, at_tower_at_end, arrival_s, climb_duration_proxy_s, left_scoring_area_s, last_shot_s, climb_attempt_detected, climb_status, climb_points {auto, teleop} | attempt = >= 5 s in endgame within 0.8 m of the tower centre line, >= 60 % stationary. Points from rules (`tower_auto.level1` 15, teleop 10/20/30) |
| `reliability` | dead_s, dead_flag (stopped >= 15 s, judged from teleop start, not at tower in endgame), n_stalls_5_15s, stationary_runs_ge15s, low_coverage_flag (coverage < 0.6 or feed_ok < 0.8), observed_frac | None when coverage < 0.5 |
| `strategy` | teleop_zone_frac, congestion_frac (teammate < 1.5 m), role, role_basis, role_scores | role from events when >= 4 launches: defender (defending >= 20 % and < 15 shots), feeder (passes >= 50 %), hybrid (defending >= 15 %), scorer (>= 8 shots); else from movement; `disabled` when dead > 60 s |

`alliance[al]`: tracks, congestion_pairs/mean, tba_fuel_counts, tba_tower_points, n_climbing_at_end, reconcile.

HANDOFF caveats: solid = distance/speed/zones, autos, lanes, clustering. Shaky = contacts/pins (identity swaps at
contact), shadowing proxy, climb timing (lifted robots project wrong), roles without shot events.

### 10.4 Identity quarantine

`enrich.py:quarantine(res, thr=ID_CONF_MIN)` (env `ID_CONF_MIN`, default 0.5) makes a copy of the match where frames
with per-frame identity conf < 0.5 are unobserved; analytics run on that copy (`analytics_for`). A team's stats then
only use stretches where we are confident it is that team. v1 tracks without `conf` pass through unchanged (they are
excluded later by the verified-match rule). Shots are quarantined separately at site build (section 12.2).

---

## 11. Enrichment (`live/enrich.py`, `live/enrich_all.py`)

"Enrich" = the v2 per-match pass that runs **after** `process.process()` (tracking + identity) on the same video:
fuel -> shots (+ intakes) -> analytics. It is a separate decode pass. Worker env `V2=1` (default) turns it on;
an enrichment exception is caught, so the v1 tracking result is still published.

| function | does |
|---|---|
| `match_camera(cam_path, res)` | camera with the match's `camera_pose` |
| `run_fuel_and_shots(video, cam_path, res, tba_match, log)` | `track_fuel` -> `reconstruct` -> `detect_intakes`; adds `timing` and `_fuel_air` |
| `airborne_tracks(fuel, min_score=0.5)` | compact airborne tracks for the player |
| `quarantine(res, thr)` / `analytics_for(res, events, tba_match)` | section 10.4 |
| `enrich(ev_dir, key, video, cam_path, res, tba_match, log)` | writes the three files below (atomic tmp + rename) |
| `station_pass(data_dir, media, ev, key, res, tba_match, meta_cache, log)` | `HUBCAM=1` only: fetch the inset upload to `MEDIA/<ev>/insets/<key>.mp4`, `station_export.export`, `hubcam.run_match`, rewrite shots + analytics (§0–6) |

Files per match:
```
data/events/<ev>/fuel/<match>.json       {"tracks": [{id, t, uv, s}]}       airborne tracks (player layer)
data/events/<ev>/shots/<match>.json      Events (section 8.8, incl. intakes)
data/events/<ev>/analytics/<match>.json  match_analytics() on the quarantined copy
```

Worker detail: for a livestream-tracked match before TBA posts, the TBA match has `score_breakdown: null`; the worker
strips that key before calling `enrich` (shots then has no hub schedule/reconcile), and re-runs `enrich` with TBA's
breakdown when TBA posts (`worker.py:live_reconcile`, section 14.4).

`enrich_all.py` (backfill, CPU only, safe next to the worker):
`python live/enrich_all.py DATA_DIR EVENT [--jobs 6] [--force] [--only k1,k2] [--media MEDIA_DIR] [--scratch DIR]`.
Re-downloads each match's exact video id (so `t_start` and the pose line up), enriches, then deletes the video (or
moves it to `--media`). Skips matches with a shots file unless `--force`. Default `--scratch` is the atomosserver
NAS path `/mnt/nas/autoscout-scratch/enrich-tmp` (root disk full). `live/reprocess.py` is the GPU equivalent that
re-runs tracking + identity + enrichment from videos already in `MEDIA_DIR` (no YouTube).

---

## 12. Site JSON (`worker.py:build_site` + `live/sitev2.py`)

> **In progress at time of writing.** A performance agent is editing `live/sitev2.py` (and nginx / fetch code).
> This section describes commit cc6297c.

### 12.1 Who writes what

`live/sitev2.py` does **not** write `events.json`, `event.json` or match files itself. The writer is
`worker.py:build_site(ev, tba)` (lead-owned; also being edited at time of writing), which calls sitev2 helpers to
reshape analytics. Then it runs the strategy/scouting builders (documented in strategy.md). Order inside
`build_site`:

1. Load TBA matches (+ synthesized livestream matches, `LiveView.extra_matches`), teams, OPRs, rankings,
   `status.json`, every `events/<ev>/matches/*.json`.
2. `identity.assign_event(results, ms, overrides.json)` - event-level team labels (honours manual overrides).
3. Per match: row for `event.json`, and for tracked matches `m/<key>.json` (+ `m/<key>.fuel.json`).
4. Per team: `t/<team>.json` (all tracked matches), `cluster_autos` routines, `event_q` summary.
5. `event.json`, then `site/api/events.json` (list of events, newest first).
6. `python -m live.strategy.dashboard DATA ev --site --snapshots --only-missing` (env `DASHBOARD=1`, default;
   timeout 900 s): `dashboard*.json`, `preview/`, `dash/`.
7. `scouting.site_build_extra(DATA, ev)`: calls `sitev2.verified_team_files` (rewrites `t/*.json` from verified
   matches only), spec sheets, `defense.json`, `scout.json`; then `python -m live.scouting DATA ev --snapshots`.
8. `goals.site_build(DATA, ev, team=DASH_TEAM)`: `rankings.json`, `schedule.json`, `selection.json`; then
   `python -m live.strategy.goals DATA ev --selection-snapshots`.

`build_site` runs after every newly processed match and once per poll when nothing new was processed.

### 12.2 `site/api/<ev>/m/<match>.json`

v1 fields: `key, label, short, event, hz, frames, t_start, start_detected, t_auto_end, t_teleop_start, video
{id,title,channel}, feed_ok, feed_gaps, field, red {teams, score}, blue, winner, breakdown` (TBA score_breakdown,
scalar fields only), `tracks[]`, `identity`.
`tracks[]`: `{alliance, idx, team, id_conf, manual, team_conf, conf[] (per frame), reads, xy (cm @hz), obs,
stats, height}`.

v2 additions (only when the enrichment files exist):

| field | source |
|---|---|
| `shots[]` | shots file, whitelisted keys `t, match_t, period, shooter, origin, v0, acc, drag, landing, target, outcome, hub_active, conf, apex_z, t_entry, t_obs, outcome_conf` + `team` + `unattributed` |
| `intakes[]`, `reconcile` | shots file |
| `periods[]` | rules periods + `{active: {red, blue}}` from the shots file's `hub_active` |
| `analytics[]` | `sitev2.web_track_analytics(analytics[idx])`, same order as `tracks[]` |
| `camera` | `{W, H, K, dist, rvec, tvec}` from `camera.json` (or the stream camera `camera_file`) + the match pose |
| `fuel_url` | `"<key>.fuel.json"` (copy of `events/<ev>/fuel/<key>.json`) |
| `video_local` | `"media/<ev>/<key>.mp4"` when the video was kept |
| `stations` | `events/<ev>/stations/<key>.json` when the inset video exists (§0–6) |
| `live`, `score_source` | livestream row (section 14.4) |

**Shot quarantine:** a robot shot whose shooter's per-frame identity conf at the launch frame
(`fi = round((t - (t_start - 0.5)) * hz)`) is below `ID_CONF_MIN` (0.5) gets `team: null,
unattributed: "low_identity_conf"`. It still counts for the alliance.

### 12.3 `site/api/<ev>/t/<team>.json`

Written twice:
* by `build_site` from **every** tracked match: `key, number, nickname, rank, opr, tracked, distance_m,
  auto_distance_m, max_speed, time_own_zone, time_neutral, time_opp_zone, id_conf, q, event, matches[] (per-match
  stats + analytics), autos[] {match, short, alliance, xy (cm, team frame, every 2nd frame of auto), obs, conf,
  routine}, grid (0.5 m), heat (teleop occupancy, normalised), analytics, shots_map[], intakes_map[], routines[]`.
  Autos are skipped when `auto_feed_ok < 0.7`.
* then by `sitev2.verified_team_files(DATA, ev)` from **verified matches only** (trust rule in
  `live/coverage.py:verified_teams`: v2 tracker and `team_conf >= 0.6`). It re-reads `m/*.json` and the raw
  `events/<ev>/analytics/*.json`, and rebuilds `analytics`, `shots_map` (only verified matches' shorts),
  `intakes_map`, `heat`, `autos`, `routines`, `q`; sets `verified_only: true`, `verified_n`,
  `matches[].verified`, `id_conf` / `team_conf` = mean team_conf over verified matches. It also patches
  `event.json` `teams[].q / id_conf / verified_n`.

Team frame (`sitev2.team_frame`): own end on the left like red; blue positions point-reflected. Map coordinates in
cm.

### 12.4 sitev2 reshaping

* `web_track_analytics(a)`: analytics tree -> web tree `{scoring, collection, movement, auto, defense, defended,
  endgame, reliability, congestion, role, role_basis, coverage}` (mapping in the code; e.g. `scoring.points =
  fuel.est_points`, `scoring.volley_size = fuel.volley_size_mean`, `defended.rate_retained =
  defense.retained_rate.retained`, `endgame.level` 0-3 from TBA).
* `web_team_analytics(agg, per_match_web, id_conf)`: numeric leaves become `{v (mean), n, conf, trend}` with
  `conf = min(1, coverage x min(1, n/6)) x id_conf`; plus pooled `made_total`, `attempts_total`,
  `accuracy_pooled`, per-period averages, endgame level counts / climb_rate / attempt_rate, role counts.
* `event_q(team_web)`: compact row for the event table `{n, acc, made, att, fire, volley, cycle, intakes, climb,
  defs, pins, automade, dead, role, speed, dist, conf}`.

### 12.5 `event.json` and `events.json`

`site/api/<ev>/event.json`: `key, name, short_name, year, start, end, city, state_prov, updated, n_done, n_total,
calibrated, matches[] (rows: key, label, short, level, time, red {teams, score}, blue, winner, state
(done|pending|processing|no_video|error|failed|live), id_conf, feed_ok, live?, score_source?), teams[] (team rows
with q, sorted by rank), livestream` (`liveingest.status_summary`).
`site/api/events.json`: `[{key, name, year, start, n_done, n_total}]`, newest first.

### 12.6 Docs vs code (data contract at the top of `web/app.js`)

* `collection.by_loc`: contract says `{neutral, depot, outpost, opp}`; sitev2 writes `{neutral, own, opp, outpost}`
  (no `depot`).
* `intakes[]`: contract `{t, match_t, track_idx, xy, conf}`; files also carry `n` and `kind`.
* `routines[]`: contract lists `shots_avg, made_avg, neutral_cross_rate, climb_rate`; neither writer produces them
  (they write `kind`, `consistency`, `start_xy`, `end_xy`).
* `shots[]` in the site files do not carry `pass_kind` / `receiver_idx` / `counted_period` / `volley` (the
  worker's key whitelist drops them); they exist only in `events/<ev>/shots/*.json`.
* `periods[]` default in the contract ("shifts 1,3 red-active") is a UI fallback, not a rule: the active hub depends
  on who won auto.
* `scoring.volley_size` is the **mean** volley size (sitev2), while `web/quant.js` labels it "max balls in a volley
  ≈ hopper capacity". `volley_max` holds the max.
* SPEC_V2 says the worker writes `analytics` aggregate etc. into team files; correct, but the verified-only rewrite
  (12.3) overwrites them afterwards, so what the browser sees is the verified-only version.

---

## 13. Overlay renderer (`live/overlay.py`)

A standalone tool for shareable accuracy-check videos. **Not called by the worker or the API.**

* `render(video_path, site_match_json, shots_json, cam, out_mp4, log, width=1920, t_from, t_to, crf=23, workers=8)`.
* Draws robot footprints (0.91 m) + team tags, reconstructed ball arcs (`shots.traj`, `C_DRAG`) with made/missed/
  pass marks (made held for `MADE_HOLD = 1.0` s), and a right-hand panel (`PANEL_W = 440` px): per-robot
  made/attempts, hub state, traced vs TBA, minimap. Output 30 fps (`OUT_FPS`), H.264 yuv420p.
* Parallel: the output is split into `workers` time segments; each runs `ffmpeg` decode -> draw -> `ffmpeg` encode
  (`libx264 -preset veryfast`), then `ffmpeg -f concat -c copy -movflags +faststart`.
* CLI: `python live/overlay.py VIDEO SITE_MATCH.json SHOTS.json CAMERA.json PROCESSED.json OUT.mp4
  [--from S --to S --width W --workers N]`.

---

## 14. Livestream ingest (`live/livestream.py`, `live/liveingest.py`)

Per-match YouTube uploads arrive minutes to hours after a match (FIRST WA uploads only once TBA posts). The
livestream path records the event's live stream continuously, finds each match on the broadcast overlay, cuts a
clip, and reads teams + score. This gives **fast results** always, and **tracking from the stream** when the
stream shows a calibrated fixed field view.

```
recorder container (worker image, `python live/worker.py --recorder`)
  liveingest.Service  --(one child per followed event)-->  livestream.py URL --event EV --source SID
        |                                                       Recorder thread: HLS chunks -> seg/*.ts + chunks.jsonl
        |                                                       analyse_live:    Scanner (overlay OCR) -> Assembler
        |                                                                        -> clips/<id>.mp4, matches/<id>.json
        v
  DATA/events/<ev>/livestream.json  (status for GET /app/v1/events/<ev>/livestream)

worker container (`live/worker.py`)
  liveingest.LiveView(ev) reads MEDIA/live/<ev>/*/matches/*.json each poll
     -> fast results on match rows (build_site), synthesized playoff matches
     -> trackable clip -> process_match(live=...) before TBA posts
     -> live_reconcile when TBA posts
```

### 14.1 Recorder (`livestream.py:Recorder`)

* `resolve()`: `yt-dlp -4 -g` with format `LS_FORMAT` (default `312/301/bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]`)
  -> HLS media playlist: 5 s MPEG-TS chunks addressed by media sequence number `sq`, `EXT-X-PROGRAM-DATE-TIME`,
  ~1 h DVR. The URL is checked by `_public()` first (http(s) to a public host only; SSRF guard), and redirects to
  private hosts are refused (`_NoPrivateRedirect`).
* **IPv4 only** (`_ipv4_only`): the server's IPv6 is bot-flagged by YouTube, and chunk URLs are bound to the
  resolving IP.
* Chunks are fetched by `sq` and appended to 60 s segment files (`seg_chunks=12`) `DIR/seg/*.ts`; each chunk is
  indexed in `DIR/chunks.jsonl` `{sq, file, off, size, pts, dur, wall}`. Chunk pts = 5 x sq, so everything is on
  one absolute stream-time axis.
* **Gap-free / resume**: addressing by sq makes recording duplicate-free across network drops, expired URLs and
  restarts. Chunks older than the playlist window are still served (`/sq/N/` substituted in the URL), so a
  restarted recorder backfills the gap. 404/410 on a chunk after retries -> logged `GAP chunk sq N unavailable;
  skipped`. 5 min without a new chunk -> re-resolve; errors back off up to 60 s, until `--max-hours`.
* **post_live**: after the stream ends there is no HLS; the rest is fetched as fragmented MP4 by `&sq=N` from the
  DASH URL and remuxed to TS (`fetch_dash`).
* A channel `/live` URL that moves to a new video id -> with `--exit-on-new-video` the process exits with code 3
  (the service starts a new output dir at once).
* `--backfill-min` (service: `LIVE_BACKFILL_MIN`, default 60, first start only): also record this many minutes the
  stream already played.
* Retention (`Recorder.retention`, every 60 s): analysed segments that hold no match go after `--keep-hours`
  (`LIVE_KEEP_HOURS`, 2); match segments (T-60 s .. T+163+300 s) after `--keep-match-hours`
  (`LIVE_KEEP_MATCH_HOURS`, 12); then oldest first while over `--budget-gb` (`LIVE_BUDGET_GB`, 60). Clips are kept.

### 14.2 Analyser (`Scanner`, `find_matches`, `Assembler`)

* `analyse_live` follows `chunks.jsonl`, decodes each chunk into the `Scanner`, steps the `Assembler`, and saves
  its cursor in `analyzer.json` (restartable; re-scans 40 chunks before the resume point).
* `Scanner.push` at `SAMPLE_HZ = 10` Hz (env `LS_SAMPLE_HZ`): scoreboard present = white clock box + opposite
  red/blue score boxes (side colours are **read**, never assumed: Bordie put blue on the left); results screen
  present; field-camera motion; **clock ticks** (binarised clock digits change between samples). Scoreboard OCR at
  `OCR_HZ = 2` Hz (clock, hub-shift box, scores); title, alliance names and team numbers every `FULL_EVERY_S = 4` s.
  Qualification vs playoff layout is picked per frame (`SB_QUAL` vs `SB` team boxes).
* OCR (`OverlayOCR`): the PP-OCRv3 recogniser in `live/models/ocr`, greedy CTC restricted to a per-field alphabet;
  digit crops are stretched so repeated digits ("9988") survive. GPU from `--gpu` (service passes
  `options.gpu_device` / `GPU_DEVICE`; standalone default env `OCR_GPU` = 1). `FORCE_CPU=1` with `cpu_only`.
* Layout: `apply_layout` moves every scoreboard box by the offset/scale that maps the default `CLOCK_BOX
  (912,72,1008,116)` onto the event layout's clock box for this source. The results-screen boxes are not moved.
* **Start detection** (`t_votes`, `find_matches`, `refine_T`): every clock reading votes for an auto-start time T
  (auto: `T = t - (20 - c)`; teleop, recognised by the "n / 6" hub-shift box: `T = t - 23 - (140 - c)`; a reading
  <= 0:20 without the shift box votes both ways). Readings agreeing within 0.9 s cluster; a match needs >= 6
  readings of >= 4 distinct clock values (a frozen pre-match clock never qualifies). T = median of auto votes
  (when >= 4), refined to the median phase of the 1 s auto ticks (teleop runs on its own timer, ~0.15-0.9 s off,
  so teleop ticks are used only when auto was not seen). Clusters inside a bigger cluster's span are dropped;
  clusters within 3 s merge.
* **Cutting** (`Assembler.step` / `finish`): a match is finished when `t_now > T + 163 + TAIL_S + 2`
  (`TAIL_S = 5`, env `LS_TAIL`), i.e. 7 s of stream time after the final 0:00, plus recording/analysis latency.
  Clip = chunks covering `[T - LEAD_S, T + 163 + TAIL_S]` (`LEAD_S = 8`, env `LS_LEAD`), stream copy, starting at
  the chunk boundary at/before T-8 (`ChunkStore.cut`). `auto_start_in_clip` is re-measured on the clip at 30 Hz
  (`measure_auto_start`); flags `clip_start_unverified`, `clip_offset_+X.XXs` (> 0.3 s off),
  `clip_missing_chunks_N`, `clip_failed`.
* Teams: per-slot vote over the match's reads; score: last scoreboard reads (`score_scoreboard`).
* **Results screen** (`read_results`, `attach_results`): the post-match full-frame graphic gives the official
  score incl. penalties, the team set (fixes one bad scoreboard read; 3-4 teams per alliance in playoffs), and the
  breakdown (auto/teleop FUEL, tower, penalties). The record is updated with `score_source: "results_screen"`. A
  run with 0-0, no auto and no results screen -> `not_a_match`, flag `no_results_screen`.
* Flags: `aborted_at_Ns` / `clock_lost_at_Ns` (clock stopped early), `aborted_scores_reset`, `timeout_countdown`
  (a timeout clock, not a match), `zero_score_no_auto`, `auto_not_seen`, `end_not_seen`,
  `overlay_coverage_0.xx` (< 0.9), `same_name_as_<id>` / `replay_of_aborted_<id>` / `possible_replay_or_rerun`
  (a second match with the same name gets id `<id>_2`).
* `confidence = {match, name, teams, score}`; `match` = OCR read density x overlay coverage, x0.6 if auto or end
  not seen, x0.3 if aborted.

**Match record** `DIR/matches/<id>.json` (id `qm12`, `sf3m1`, `f1m2`, `unknown`, `+_2`...):
`{id, match_name, comp_level, set, number, teams {red, blue} (station order), score {red, blue}, score_source,
score_scoreboard, breakdown, alliance_names, t_start_stream (s, stream pts), t_start_utc, clip,
auto_start_in_clip, confidence, flags, evidence, tba (optional cross-check), not_a_match?}`.

### 14.3 Output directory

```
MEDIA_DIR/live/<ev>/<video id>/          (LIVE_DIR, default $MEDIA/live; NAS-backed on atomosserver)
  meta.json          {event, source, url, video_id, label, managed, started}   (written by the service)
  seg/*.ts           60 s segments (trimmed by retention)
  chunks.jsonl       chunk index
  recorder.json      {last_sq, at, video_id, ...}      analyzer.json  {last_sq, cursor_wall, t_last, at}
  assembler.json     {done: {id: {T, title, record, results_done}}}
  clips/<id>.mp4     match clips (kept)               matches/<id>.json   records
  evidence/          results-screen stills etc.       livestream.log, service.out
```
Clips are served by nginx at `/media/live/...` (signed-in users only).

### 14.4 Service (`liveingest.py:Service`) and worker view (`LiveView`)

**Service** (compose service `recorder`; `python live/worker.py --recorder` or `python live/liveingest.py serve`),
every `LIVE_POLL` (10 s):
* `wanted()`: followed events (`DATA/config/worker.json` `events`, else `$EVENTS`) with an enabled `livestream`
  source in `events/<ev>/sources.json`, and today within the event's TBA dates (`LIVE_ANY_DATE=1` skips).
  `LIVESTREAM=0` disables the service.
* `pick_source`: an enabled livestream source marked `primary` or role `wide`, else the webcast dated today, else
  an admin-added one, else the first.
* One child per event: `livestream.py --out <dir> --event EV --source SID --data DATA --gpu N --max-hours
  ${LIVE_MAX_HOURS:-16} --budget-gb ... --keep-hours ... --keep-match-hours ... --backfill-min ...
  --exit-on-new-video --tba-env /nonexistent -- URL` (`--` so the URL can never be an option; TBA key passed via
  env for the optional cross-check).
* States written to `DATA/events/<ev>/livestream.json`: `recording`, `starting`, `waiting` (not live yet;
  re-check every `LIVE_RESOLVE_S` = 120 s), `stream_over` (re-check every 10 min), `error` (restart back-off
  30 s .. 10 min), `idle` (outside dates), `no_source`, `disabled`, `stopped`. Plus `dir_status`: recorder/analyser
  sq and lag (`behind_live_s`), recent matches, `last_match`, disk (`seg_gb`, `clips_gb`, `free_gb`,
  `budget_gb`, `min_free_gb`), last error lines, `tracking {ok, note}`.
* The API (`server/routers/sources.py`, `GET /app/v1/events/<ev>/livestream`) serves this file with
  `configured_url`, `age_s` and `stale` added; with no file it answers `not_running` (a livestream source is
  configured) or `no_source`, and a stale (`age_s > LIVE_STALE_S`) `recording`/`waiting`/`starting` state keeps its state but gets the
  message "recorder service not reporting for Ns (is the `recorder` container running?)".
* Service retention (every 5 min): segments of finished dirs deleted after `LIVE_KEEP_SEG_DAYS` (2 days), and
  oldest first while the `LIVE_DIR` disk has less than `LIVE_MIN_FREE_GB` (30) free.

**Worker side** (`LiveView(ev)`, built each poll):
* `record_key`: `qm<n>` -> `<ev>_qm<n>`; `sf` set s -> `<ev>_sf<s>m1` (2023+ double elimination); `f` n ->
  `<ev>_f1m<n>`.
* `latest(records, need_clip)`: per key the latest record (by wall time) without `not_a_match` and without
  `BAD_FLAGS` (`aborted_*`, `clock_lost_*`, `timeout_countdown`, `zero_score_no_auto`, `no_results_screen`,
  `clip_failed`); with `need_clip`, also a clip file and `0 <= auto_start_in_clip <= 60`.
* **Fast results** (`decorate_row`, always): match row gets `live = {source, record, source_id, video_id, name,
  teams, score, score_source, breakdown, t_start_utc, t_end_utc, clip (media URL), auto_start_in_clip, confidence,
  flags, tracking: from_stream|uploads, tracking_note}`. Until TBA posts, the row shows the stream's score/winner,
  `score_source: "livestream"`, `state: "live"`; after, `score_source: "tba"`. Playoff matches TBA has not created
  yet become synthesized TBA-shaped matches (`synth_match`, `livestream: true`). The web shows "LIVE · stream"
  (`CS.liveTag`).
* **Trackable** (`trackable(ev, sid)`): the stream source's layout has a `field` or `overhead` box AND a
  calibration exists: `events/<ev>/camera_<sid>.json`, or `camera.json` whose `source` key equals `sid`
  (calibration wizard). Most event streams are switching program feeds -> fast results only.
* `for_processing(m, is_posted)`: usable clip + trackable + lineup check (`lineup_common < 4` of 6 slots matching
  TBA -> refuse). Teams from TBA when it has a lineup, else from the stream. Returns the clip, measured start,
  camera, a one-source layout, teams.
* `worker.py:process_match(live=...)`: the clip is tried first with `start_hint` = measured auto start and
  `start_trusted=True`, the stream camera and `pose_latest_<source>.json`; the per-match uploads follow only once
  TBA has posted and `camera.json` exists. Result gets `source: "livestream"`, `camera_file`, `live`.
  - Integrity says the clip is another match (bumper reads) -> `status.live_rejected = record_path`; never used
    again; uploads later.
  - Clip failed and no uploads -> `status.live_failed += 1`; after 2 failures the worker waits for the uploads.
  - Tracked before TBA posted -> `status.live_reconcile = true`.
* `worker.py:live_reconcile` (when TBA posts): stores `live.tba_score`; if TBA's lineup differs from the teams used,
  the result is moved to `<key>.live-teams.json` and the match re-tracked; otherwise `enrich` is re-run with TBA's
  score breakdown (hub-active shifts, reconciliation). Sets `live.tba_posted`, `reconciled_at`.
* The worker's poll sleep wakes early when `records_signature` (set of record files) changes.

Tests: `tests/test_liveingest.py`: `test_record_keys`, `test_latest_valid_record_wins`,
`test_trackable_needs_field_box_and_calibration`, `test_for_processing_teams_and_lineup_check`,
`test_fast_results_rows_and_synth`, `test_pick_source`, `test_process_prefers_trusted_livestream_start`,
`test_worker_sync_processes_live_clips_before_tba`, `test_worker_sync_untrackable_stream_waits_for_uploads`,
`test_service_status_files`. `server/tests/test_livestream.py` covers the status endpoint.

### 14.5 Environment variables (livestream)

| var | default | effect |
|---|---|---|
| `LIVESTREAM` | 1 | 0 = service off |
| `LIVE_DIR` | `$MEDIA/live` | output root |
| `LIVE_POLL` | 10 s | service loop |
| `LIVE_RESOLVE_S` | 120 s | re-check a not-yet-live stream |
| `LIVE_ANY_DATE` | unset | 1 = ignore TBA event dates |
| `LIVE_MAX_HOURS` | 16 | per child recording cap (not in deploy/README) |
| `LIVE_BUDGET_GB` | 60 | per stream dir |
| `LIVE_KEEP_HOURS` / `LIVE_KEEP_MATCH_HOURS` | 2 / 12 | segment retention |
| `LIVE_KEEP_SEG_DAYS` | 2 | finished dirs |
| `LIVE_MIN_FREE_GB` | 30 | disk floor |
| `LIVE_BACKFILL_MIN` | 60 | DVR minutes on first start |
| `LS_LEAD` / `LS_TAIL` | 8 / 5 s | clip padding |
| `LS_SAMPLE_HZ` / `LS_OCR_HZ` / `LS_FULL_EVERY` | 10 / 2 / 4 | analyser rates |
| `LS_FORMAT`, `YTDLP` | see 14.1 | yt-dlp |

### 14.6 Docs vs code

* "Cut ~20 s after the final buzzer" (deploy/README, liveingest docstring) vs the code's T + 163 + 7 s of stream
  time: the extra ~13 s is chunk arrival (5 s chunks) and analyser latency, not a constant.
* deploy/README lists `LIVE_BUDGET_GB`, `LIVE_MIN_FREE_GB`, `LIVE_BACKFILL_MIN`, `LIVE_DIR`; `LIVE_MAX_HOURS`,
  `LIVE_KEEP_HOURS`, `LIVE_KEEP_MATCH_HOURS`, `LIVE_KEEP_SEG_DAYS`, `LIVE_POLL`, `LIVE_RESOLVE_S`, `LIVE_ANY_DATE`
  and the `LS_*` variables are undocumented outside the code.
* The livestream.py standalone `--budget-gb` default is 20 GB; under the service it is 60 GB (`LIVE_BUDGET_GB`).
* `--tba-env` defaults to `/opt/stacks/cyber-scout/.env` (a host path) for standalone runs; the service passes
  `/nonexistent` and the key via env.

## Docs vs code: summary for sections 0–6

| claim (doc) | code |
|---|---|
| HANDOFF: hubcam "NOT in the pipeline yet" | wired in under `HUBCAM=1` (`enrich.station_pass`) |
| deploy/README: identity solves the event at once; a fix improves other matches | true for v1 only; v2 labels are per match and overrides are per match |
| identity.py docstring: HMM on OCR + appearance is the method | fallback only; `extract_features` is never run by the worker |
| SPEC_V2: `tracks[6]` | v1 fallback can emit fewer than 6 (an alliance with no tracklets) |
| deploy/README "re-process a match: delete matches/<m>.json" | also clear a `failed` entry in `status.json`, else it stays skipped |
| sources.py: `station_pattern` used for station-cam uploads | computed for the UI only; `find_inset_video` hard-codes "Match N" |
| Video page playlists give per-match videos | the worker ignores `sources[].videos`; only TBA-linked uploads (+ `search_title`) are downloaded |

Undocumented behaviour worth knowing:
- `ytmeta.json` caches failed lookups as `null` permanently.
- Failed downloads leave partial files in `DATA/cache/`.
- A wrong-video result is still saved when every candidate is wrong.
- `build_site` and the whole strategy chain run on every idle poll.
- Decoding stops at 190 s of video, so late-starting uploads lose the end of teleop.
