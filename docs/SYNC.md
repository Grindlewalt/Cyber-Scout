# Client–server sync contract (v1)

Goal: inference stays on the server; every client keeps a full local copy of the *data* and works offline.
Venue Wi-Fi is slow and lossy (tunnel ≈ 0.6 s per request), so sync is **delta-based, bundled, resumable and
idempotent**. Three parts implement this contract; none may change it without updating this file.

```
 phone/tablet browser ──(PWA: service worker + IndexedDB + outbox)──┐
                                                                     ├──HTTPS──► server (nginx → api, static JSON, media)
 laptop: Cyber-Scout Client (local server, http://127.0.0.1:7600) ──┘           inference runs here only
          serves web/ + local mirror, outbox, sync engine
```

## 1. Server endpoints (all under `/app/v1/`, JSON unless noted)

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /info` | none | `{"app":"cyber-scout","api":1,"version":"<git short sha>","name":"<server display name>","time":<unix>}` — "Test connection". Tiny, cacheable 10 s. |
| `GET /sync/events` | viewer | Followed events `[{key,name,start_date,end_date,status,progress:{tracked,total}}]` |
| `GET /sync/manifest?ev=<ev>[&since=<cursor>]` | viewer | Data files for one event. Without `since`: everything. With `since`: changes after that cursor. Response: `{"ev","cursor":"<opaque>","full":bool,"files":[{"path","size","sha256","mtime"}],"deleted":["path",...]}` |
| `POST /sync/bundle` body `{"ev","paths":[...]}` | viewer | **One request for many files**: `application/gzip` tar of the requested paths (each must be in the event's manifest; max 64 MB per bundle, client splits). Supports `Range` for resume. |
| `GET /sync/media?ev=<ev>` | viewer | Media list `[{"path","size","sha256"?,"kind":"proxy"|"wide"|"inset"|"clip","match"}]`. `proxy` = small preview copy (≈480p, low bitrate). |
| existing write endpoints (notes, picklists, overrides/fixes, …) | as today | **Accept `Idempotency-Key: <uuid>` header**: the server stores key → (status, body) for 48 h per user and replays the stored response for a repeated key instead of applying twice. Picklists keep `base_version` conflict handling (409 + current). |

`path` values are relative to the site root and match the URLs the web app already fetches: data files are
`api/<ev>/...` (served at `/api/<ev>/...`), media are `media/<ev>/...`. Global files (e.g. `api/events.json`) are
listed in every event's manifest. sha256 is of the file bytes as served (uncompressed). Cursors are opaque
strings (e.g. a max-mtime + count); a client that sends an unknown/old cursor gets `full:true`.

Media proxies: the worker writes `media/<ev>/proxy/<match_key>.mp4` (H.264, ≈480p, ≈0.8–1.2 Mb/s, faststart)
after a match is processed, if `MEDIA_PROXIES=1` (default on); a backfill command exists for already-processed
matches.

## 2. Cyber-Scout Client (local server, laptops)

- Runs on `http://127.0.0.1:7600` (configurable). Opening it in a browser shows **the same web app** (the client
  keeps a copy of the server's `web/` files, refreshed when the server version changes).
- Serves `/api/...` and `/media/...` from its local mirror; proxies `/app/v1/...` to the server when online.
  When offline, **write** requests (POST/PUT/PATCH/DELETE to `/app/v1/...`) are accepted into a durable **outbox**
  (assigned an `Idempotency-Key`) and answered `202 {"queued":true,"id":...}`; reads of `/app/v1/...` that have no
  local equivalent return `503 {"offline":true}`.
- Local control API (exists **only** on the client — the web app uses it to detect client mode):

| Method & path | Purpose |
|---|---|
| `GET /client/v1/status` | `{"client":true,"version","server":"<url>|null","online":bool,"signed_in":bool,"user":{display_name,role}|null,"last_sync":<unix>|null,"syncing":bool,"progress":{done,total,bytes}|null,"outbox":<n>,"events":[{key,synced_at,files,bytes,media_bytes}],"errors":[...]}` |
| `PUT /client/v1/config` `{"server", "media":"none"|"proxy"|"full", "events":[...]}` | Settings |
| `POST /client/v1/test` `{"server"}` | Calls the server's `/app/v1/info`; returns latency + version or a clear error |
| `POST /client/v1/login` `{"username","password"}` / `POST /client/v1/logout` | Signs in to the server; the session is stored locally (file mode 0600), never exposed to the page |
| `POST /client/v1/sync` `{"ev"?: "...", "media"?: bool}` | Sync now |
| `GET /client/v1/outbox` / `DELETE /client/v1/outbox/<id>` | Inspect / discard queued changes |

- Sync engine: on start and every N s while online (and immediately when connectivity returns): `/sync/events`
  → for each followed event `/sync/manifest?since=` → download missing/changed files via `/sync/bundle` in
  chunks (verify sha256, write atomically, resume partial bundles) → flush the outbox in order (stop on auth
  errors; surface 409 conflicts to the user: the Connection sheet and Settings list them with their buttons). Back-off
  with jitter on failures. Never more than 2 concurrent requests (slow links).
- Security: binds to 127.0.0.1 only by default; the control API requires a per-install random token in a cookie
  set when the page is served by the client (prevents other local sites from driving it); no shell commands;
  only the configured server is contacted.
- Packaging: pure-Python (stdlib only, Python ≥ 3.9) single entry point `client/cyberscout_client.py`, plus
  scripts to build standalone binaries (PyInstaller) for Windows/macOS/Linux; launches the browser on start.
- Optional, admins: **Help process matches** (`/client/processing`, `GET/PUT /client/v1/processing`). A client with
  an NVIDIA GPU (CUDA) or Apple silicon (Core ML) tracks matches for the server through `/app/v1/processing/*`
  (same per-match claims as the worker); any client can run **Clip only** (record a livestream, cut matches by TBA
  time, upload the clips the server lacks). The pipeline runs in a separate Python the client installs with uv;
  the client itself stays stdlib-only. Details: `client/README.md`, `docs/guide/server.md` §7.10.

## 3. Web app (browser and client mode)

- **Client mode** (when `GET /client/v1/status` answers): the Settings area gets a **Connection** section (server
  address, Test connection, sign in/out, media option, which events to keep, storage used, Sync now, outbox list)
  and a **Help process matches** section (status line, GPU / speed / slow-Wi-Fi notices, link to
  `/client/processing`; `web/settings.js`); the app's own sign-in goes through `/client/v1/login`.
- **Browser mode** (phones/tablets): a service worker caches the app shell (versioned files) and the data the user
  views (stale-while-revalidate for `/api/...`), offers **"Save this event for offline"** (fetches the manifest +
  bundle and stores it), and keeps an **IndexedDB outbox** for writes with `Idempotency-Key`, flushed when back
  online (Background Sync where supported, else on `online`/focus events).
- **Both modes**: a small, always-visible connection pill — `Online · synced 2 min ago`, `Syncing 40%`,
  `Offline · 3 changes waiting` — plus a toast when going offline/online and when queued changes are sent or
  conflict. Never block reading because of the network.

  As built: on phones the toolbar pill is the state's dot + glyph (check / cloud-off / warning / arrows / person,
  shown by style.css) while online, and a full-width text bar when offline or unreachable; its full text is in the
  sheet it opens. Client mode adds `Connecting…` (just after a server change) and `Client not running` (the local
  Client process stopped answering; the pages say how to start it again).

## Deviations

Server side (part 1), as implemented in `server/routers/sync.py`, `server/syncindex.py`, `server/idempotency.py`,
`live/proxies.py`. Everything not listed here is as specified above.

- **Livestream clips** are listed with their real URL path `media/live/<ev>/<video id>/clips/<file>.mp4` (that is
  where the recorder keeps them and where nginx serves them), not under `media/<ev>/`. `match` is taken from the
  stream's match records (null if no record names the clip). Other media paths: `media/<ev>/<match>.mp4` (wide),
  `media/<ev>/insets/<match>.mp4` (inset), `media/<ev>/proxy/<match>.mp4` (proxy). `sha256` is given for proxies
  only (from `media/<ev>/proxy/index.json`, written by the encoder); hashing GBs of full videos per request is not
  practical.
- **`POST /sync/bundle` needs the CSRF header** like every other POST (`X-CSRF-Token` = the `cs_csrf` cookie /
  `csrf` from `GET /session`). It is treated as a read: no write budget, allowed during a lockdown, not audited,
  no Idempotency-Key handling. Errors: 422 malformed path / body, 403 path outside the event (other event,
  traversal, symlink), 404 not in the current manifest (re-fetch the manifest), 413 over 64 MB (sum of the
  manifest `size`s, i.e. uncompressed). Tar member names are the manifest paths. Response headers:
  `ETag` (send it back as `If-Range` with `Range: bytes=N-` to resume; the bytes are deterministic, also after the
  server-side cache is evicted), `X-Bundle-Files`, `X-Bundle-Bytes`. If a file changed between the manifest and
  the bundle, the bundle carries the new bytes: the sha256 check fails and the client should re-fetch the manifest.
- **`GET /sync/events`** `status` values: `needs_setup` (no camera calibration) | `upcoming` | `in_progress` |
  `finished`; an extra field `updated` (last site build, unix s) is included.
- **`GET /sync/manifest`**: `ev` may also be an event that is not followed but has site data (404 if neither).
  `mtime` is unix seconds (float). A cursor stays valid across API restarts; rewriting a file with identical bytes
  is not a change.
- **Idempotency-Key** details: keys are 1–128 chars of `A-Z a-z 0-9 . _ : -` (else 400). A replayed answer has the
  header `Idempotent-Replayed: true`. The same key with a different method/path/query/body → 422. The same key
  while the first request is still running → the server waits up to ~10 s, then answers **429** + `Retry-After`
  (not 409, so it is never confused with a picklist conflict). Not stored (a retry runs again): 5xx and
  401/403/408/423/425/429. Ignored on sign-in/out, password change, setup (they set cookies) and the bundle.
  Requests without a session are passed through (the route answers 401).
- **Media proxies**: ≤480p, ≤30 fps, ~1 Mb/s H.264 (main), faststart, no audio; ≈27 MB for a 3.5-min match. NVENC
  is used when a test encode works on `PROXY_GPU`; with the current worker image (Debian ffmpeg 7.1) and the
  server's NVIDIA 610 driver NVENC fails to open ("unsupported device"), so proxies are encoded with libx264
  (2 threads, `nice -n 10`, ≈25 s per match) on a background thread of the worker.

Client side (part 2), as implemented in `client/` (answer shapes of the control API: `client/README.md`).

- **"Never more than 2 concurrent requests"** applies to the sync engine's background traffic (probe, events,
  manifests, bundles, media, web-app refresh). Requests the page makes through the proxy (and the outbox send they
  trigger), sign-in and Test connection go out at once, so the UI never waits behind a 32 MB bundle.
- **Media** are downloaded with `GET /media/<path>` (Range-resumable; verified by sha256 when given, else by size),
  not through `/sync/bundle` (which only takes manifest paths). A single data file over 48 MB is fetched the same
  way via `GET /api/<path>` (a bundle can't exceed 64 MB).
- **Writes while online** also go through the outbox (an `Idempotency-Key` is always added): with an empty outbox
  the page gets the server's real answer if it arrives within 20 s, otherwise `202 {"queued":true,"id","key"}`.
  With older changes still queued, new ones queue behind them (order is kept). Sign-in/out, password, setup and
  `/sync/*` are never queued. `429`/`5xx`/`409 + Retry-After` keep the change queued and retry soon.
- **The page's own `POST /app/v1/auth/login` / `logout`** are handled by the client (they sign the client in/out,
  same as `/client/v1/login` / `logout`); every `csrf` the page sees is the client's local token.
- **`GET /client/v1/status`**: `progress.done/total` count files (`done_bytes/total_bytes` = file bytes, `bytes` =
  bytes received; the app's percentage is by bytes); `outbox` counts this server's changes not yet accepted (queued +
  sending); conflicts and rejected changes appear in `errors` (`kind` `conflict` / `failed`, with the server's
  answer) and in `GET /client/v1/outbox`. Extra fields: `active_server`, `alt_servers`, `connecting`, `outbox_held`,
  `media`, `followed`, `available_events`, `offline_reason`, `server_info`, `web_version`, `storage`.
  `POST /client/v1/test` always answers 200 with `ok:true|false` (+ `suggest` when an https:// address answered in
  plain http). `DELETE /client/v1/events/<ev>` removes the local copy of a competition that isn't synced any more.
- **Outbox (client)**: a picklist save replaces a still-unsent (never tried) save of the same list with the same
  `base_version`; once a picklist save is accepted, later queued saves of that list with the same `base_version` are
  rebased onto the version it created (a tried one gets a new key). Every row remembers the configured server it was
  written for and is sent only there (or to its `--alt-server` addresses): after a server change it waits, listed as
  held (`outbox_held`). Conflicts are never merged automatically (see part 3).
- **One client per data folder**: `client.lock` in the data folder (flock / msvcrt lock, released by the OS); a
  second start on the same folder opens the running one. Media sync keeps 2 GB of disk free.

Web app (part 3), as implemented in `web/sync.js`, `web/sw.js` (+ small hooks in `app.js`, `setup.js`, `event.js`,
`notes.js`, `settings.js`).

- **"Stale-while-revalidate" for `/api/…` has a 1.5 s network grace**: the network answer is used when it arrives
  within 1.5 s (so an online page is never one refresh behind), otherwise the cached copy is shown at once and the
  cache is updated in the background. Answers served from the cache carry `x-cs-src: offline|stale` and `x-cs-at`
  (ms) for the "Saved copy · as of …" label. `index.html` is network-first with a 4 s limit.
- **Browser-mode offline copies hold data only** (`api/…` paths of the manifest), not videos; `media/*.jpg|png|webp`
  get a 40-entry LRU, videos and Range requests pass through. Bundles are requested in ≤ 8 MB chunks and a failed
  chunk is re-requested for the files still missing (no `Range`/`If-Range` resume in the browser).
- **Outbox (browser)**: queued writes are notes, picklist, identity fixes and their approve/reject; every such write
  carries an `Idempotency-Key` even online (the key is reused if it ends up queued). Nothing is tried while the
  device is offline or the server unreachable (the probe flushes when it's back; a failed fetch while
  `navigator.onLine` is false doesn't count as a try), so a picklist save still replaces the earlier untried one: one
  queued save per list. After an accepted picklist save, later queued saves of the list made on the same
  `base_version` are rebased onto the new version (page and Background Sync alike). No Web Lock between the page and
  the Background-Sync worker: both send with the item's key and rely on the server's replay / 429; the worker reports
  the keys it sent, so the page shows one "N changes sent" toast per change, not one per sender. "Save mine" /
  "Retry" send a new key.
- **Picklist conflicts (both modes)**: a 409 is shown as a card in the Connection sheet (and client Settings) that
  says who saved and what each choice drops ("Save mine over theirs: removes their star on 8248. Use theirs: drops
  your star on 2046."). Nothing is merged: **Save mine over theirs** saves your whole list on top of their version
  (theirs stays in the picklist history), **Use theirs** drops yours. (A merge is an open owner decision.) Client
  mode reads the current version from the outbox item's `response.detail.current`.
- **Offline copies need a secure context**: service workers and the Cache API only exist on https:// (or localhost),
  so a phone on the shop LAN address (`http://10.0.0.58:7560`) gets no saved pages and no "Save this event for
  offline" (Settings → Offline says "Offline copies need the secure address of this site (https://…)"); its changes
  still wait in the IndexedDB outbox, sent while the page is open (no Background Sync). Use the https address (tunnel)
  on phones, or the Cyber-Scout Client on laptops.
- **Offline sign-in state**: the last signed-in user (id, name, role; no secret) is kept in `localStorage
  cs:sync:user` so the app opens read-only + outbox when `/app/v1/session` can't be reached; it is dropped on
  sign-out, on any 401 and when the server answers "no user".
