# Cyber-Scout Client

The Cyber-Scout web app on a laptop, **with a full local copy of the server's data**, so it keeps working when the
venue Wi-Fi doesn't. Part 2 of [docs/SYNC.md](../docs/SYNC.md). Inference stays on the server; the client only
downloads what the server has built (site JSON, optionally videos) and queues your changes while you're offline.

```
browser ──► http://127.0.0.1:7600  (Cyber-Scout Client, this folder)
              ├─ /            the web app (a copy of the server's web/ files; a bundled copy before the first connect)
              ├─ /api/  /media/   from the local mirror (falls through to the server when a file isn't local yet)
              ├─ /app/v1/...  proxied to the server with the client's own session; writes go through the outbox
              └─ /client/v1/  local control API (status, settings, sign-in, sync now, outbox)
                       │
                       └──HTTPS──► your server (e.g. https://scout.example.dev or http://10.0.0.58:7560)
```

Pure Python standard library (Python 3.9+), no dependencies. Standalone executables are built with PyInstaller.

## Install and run

**Standalone executable** (no Python needed): download the file for your computer from the GitHub release (or the
`client` workflow's artifacts; see [Building](#building)) and run it. A terminal window opens with the address and
the log, and the browser opens `http://127.0.0.1:7600/`.

| computer | file |
|---|---|
| Windows 10/11, 64-bit | `cyberscout-client-windows-x64.exe` |
| Mac with Apple silicon (M1 and later) | `cyberscout-client-macos-arm64` |
| Intel Mac | `cyberscout-client-macos-x64` (also runs on Apple silicon through Rosetta) |
| Linux x86_64 (glibc 2.17+: Ubuntu 18.04+, Debian 10+, Fedora, …) | `cyberscout-client-linux-x64` |

Each file has a `.sha256` next to it (`shasum -a 256 -c <file>.sha256` / `sha256sum -c`; Windows:
`Get-FileHash <file>`). The builds are **not code-signed**, so the first start needs one extra step:

* **Windows**: double-click the `.exe`. SmartScreen says "Windows protected your PC" → **More info** →
  **Run anyway**. Allow the firewall prompt only if you started it with `--host 0.0.0.0` (the default listens on this
  computer only, so no prompt appears).
* **macOS**: the file downloads without the "executable" bit and Gatekeeper blocks unsigned programs. In Terminal:
  `chmod +x ~/Downloads/cyberscout-client-macos-arm64`, then in Finder **right-click (Control-click) the file → Open →
  Open** (once; afterwards a double-click works). Or, in Terminal:
  `xattr -d com.apple.quarantine cyberscout-client-macos-arm64 && ./cyberscout-client-macos-arm64`.
  On macOS 15+, if there is no "Open" button: System Settings → Privacy & Security → "… was blocked" → **Open Anyway**.
* **Linux**: `chmod +x cyberscout-client-linux-x64 && ./cyberscout-client-linux-x64` (add `--no-browser` on a
  machine without a desktop).

**From source** (any OS with Python 3.9+):

```bash
python3 client/cyberscout_client.py                 # opens http://127.0.0.1:7600/ in your browser
python3 client/cyberscout_client.py --server https://scout.example.dev --port 7600 --no-browser
```

A terminal window shows the address and the log; closing it (or Ctrl+C) stops the client. Starting it a second
time just opens the browser on the running one.

| option | env | default |
|---|---|---|
| `--port` | `CYBERSCOUT_PORT` | 7600 |
| `--host` | `CYBERSCOUT_HOST` | 127.0.0.1 (this computer only; anything else is an explicit opt-in to LAN access) |
| `--data` | `CYBERSCOUT_DATA` | see [Storage](#storage) |
| `--server` | | (set it in the app instead) |
| `--alt-server URL` | | other addresses of the **same** server (repeatable; saved), used automatically when the main one can't be reached, e.g. `--server https://scout.grantrindal.dev --alt-server http://100.86.234.25:7560` (tunnel + Tailscale). The session, outbox and sync cursors carry over; the client goes back to the main address once it answers again (checked every 2 min). An alternate that names itself differently (`/app/v1/info` `name`) is not used. `--alt-server ''` clears them |
| `--log-level` | `CYBERSCOUT_LOG` | INFO |
| `--no-browser` | | |

## First run

1. Start the client; the browser opens `http://127.0.0.1:7600/` with the app (the copy bundled into the client).
2. **Settings → Connection**: type the server address, e.g. `https://scout.grantrindal.dev` (through the tunnel)
   or `http://10.0.0.58:7560` (shop LAN / Tailscale). A bare host name gets `https://`.
3. **Test connection**: shows the server's name, version and the round-trip time, or a clear error ("connection
   refused", "certificate could not be verified", "answers, but is not a Cyber-Scout server", …).
4. **Sign in** with your Cyber-Scout account. The client signs itself in; the browser page never sees the server
   session. Sync starts immediately.
5. Choose **media**: `none` (default: data only), `proxy` (small ≈480p preview copies of match videos,
   ~15–20 MB per match) or `full` (every video the server keeps: GBs per event). Choose which events to keep
   (default: all events the server follows).

Without the web UI (scripts, troubleshooting), the same is possible with the control API below, from a page served
by the client.

## How sync works

On start, every 60 s while online, right after connectivity returns, and on **Sync now**:

1. `GET /app/v1/info` (a tiny probe; also tells the client when the server's web app changed → it re-mirrors
   `index.html` and every JS/CSS/font/image it references into a new folder and switches over atomically).
2. Flush the outbox (queued changes, in order).
3. `GET /app/v1/sync/events`, then per event `GET /app/v1/sync/manifest?ev=…&since=<cursor>`: only what changed
   since the last sync (or everything, `full:true`, on the first sync / when the server doesn't know the cursor;
   local files missing from a full manifest are deleted; `deleted` paths of a delta are deleted).
4. Files that are missing or whose sha256 differs are fetched with `POST /app/v1/sync/bundle` — one gzip'd tar per
   ≤ 32 MB of files, at most 2 requests at a time. Each partial download is kept in `parts/` and **resumed with
   `Range`** after a broken connection (also across restarts). Every file is checked against the manifest's size
   and sha256 and written atomically (temp file + rename); a file that fails is retried next time and the event's
   cursor is not advanced until everything verified.
5. Media (`proxy`/`full`): `GET /app/v1/sync/media?ev=…`, then each video with `GET /media/…` (Range-resumable,
   verified). The app's `media/<ev>/<match>.mp4` is answered with the local full video, else the local proxy copy.
6. Flush the outbox again.

Failures back off exponentially with jitter (5 s … 10 min); the probe keeps running (every 15 s while offline).

Measured end to end (real API + a copy of the 2026wasam site, 342 files / 94 MB of JSON; `client/tests/e2e/`):

| link | first sync | delta (3 changed, 1 new, 1 deleted) | nothing changed | 5 offline notes → sent |
|---|---|---|---|---|
| LAN | 1.5 s, 20 MB on the wire (+12.6 MB proxy video) | 0.07 s, 13 KB | 0.06 s, 350 B | 0.06 s |
| 600 ms latency + 1 MB/s, first bundle cut at 3 MB | 27 s, 19.8 MB (resumed, no re-download) | 2.5 s, 13 KB | 1.9 s, 350 B | 4.9 s (one answer lost → replayed, not duplicated) |

The web app itself (39 files, 1.8 MB) mirrors in 14 s on the slow link, once per server version.

## Offline behaviour

* Everything already synced keeps working: pages read `/api/…` and `/media/…` from the local copy.
* `GET /app/v1/…` answers the client has seen before (session, notes, picklists, …) are served from a cache with
  `X-CS-Offline: 1`; anything else answers `503 {"offline":true}`.
* Changes (`POST/PUT/PATCH/DELETE /app/v1/…`) go into the **outbox** (SQLite, fsync'd) with an `Idempotency-Key`
  and answer `202 {"queued":true,"id":…,"key":…}`. When online with an empty outbox the change is sent at once and
  the page gets the server's real answer (or `202` if it takes more than 20 s).
* The outbox is sent in order when the server is back. The same key is used on every retry, so a change whose
  answer got lost is replayed by the server, never applied twice.
* `409` conflicts (e.g. a picklist someone else changed) are kept, shown in `status.errors` (`kind:"conflict"`,
  with the server's answer incl. the current version) and in the outbox list until you discard them. Other rejected
  changes (`kind:"failed"`, e.g. 403 lockdown, 422) likewise. A `401` stops the flush until you sign in again.
* Sign-in, sign-out, password change and setup are never queued (they need the server).

## Control API (`/client/v1/*`)

Only for pages served by this client: requests need the per-install `cs_client` cookie (HttpOnly,
SameSite=Strict; set when the client serves the page), an `Origin` (if sent) of `http://127.0.0.1:<port>` /
`http://localhost:<port>`, no cross-site `Sec-Fetch-Site`, and a `Host` of 127.0.0.1/localhost (DNS rebinding).
The same gate protects `/app/v1`, `/api` and `/media`; page writes to `/app/v1` also need `X-CSRF-Token` = the
local `cs_csrf` cookie (the client's own token — the server's CSRF token and session cookie stay inside the client).

| call | answer |
|---|---|
| `GET status` | `{client:true, version, server, online, signed_in, user:{display_name,role}\|null, last_sync, syncing, progress:{done,total,bytes,done_bytes,total_bytes,ev,phase}\|null, outbox, events:[{key,name,synced_at,files,bytes,media_bytes}], errors:[{time,kind,message,ev?,id?,response?}]}` plus `active_server` (the address in use: `server` or one of `alt_servers`), `alt_servers`, `media, followed, available_events, offline_reason, server_info, web_version, storage`. `progress.done/total` count files; `bytes` is bytes received. `outbox` = changes not yet accepted. `errors[].kind`: `network`, `auth`, `sync`, `media`, `web`, `conflict`, `failed` |
| `PUT config` `{server?, alt_servers?, media?, events?, interval?}` | `200 {ok, config}`; `422 {detail}` on bad values. `events:null` = all followed events. Changing `server` signs out and resyncs |
| `POST test` `{server?}` | always `200`: `{ok:true, server, latency_ms, version, name, api}` or `{ok:false, server, error}` |
| `POST login` `{username,password}` | `200 {ok:true, user}`; server's status + `{ok:false, detail}` (401 bad password, 429 …); `503 {offline:true}` |
| `POST logout` | `200 {ok:true}` |
| `POST sync` `{ev?, media?}` | `202 {started:true, online}` |
| `GET outbox` | `[{id, key, method, path, created, updated, state:"queued"\|"sending"\|"conflict"\|"failed", attempts, status, error, body, response}]` |
| `DELETE outbox/<id>` | `204`; `404` unknown; `409` while it is being sent |

## Storage

| OS | default data folder |
|---|---|
| macOS | `~/Library/Application Support/CyberScout` |
| Windows | `%APPDATA%\CyberScout` |
| Linux | `$XDG_DATA_HOME/cyberscout` or `~/.local/share/cyberscout` |

```
config.json        settings + per-install secrets (0600)
session.json       the server session cookie + CSRF token (0600; never sent to the page)
client.sqlite3     outbox + cache of /app/v1 answers (0600)
site/api/…         the data mirror  (site/media/… videos)
state/<ev>.json    per event: manifest cursor, last sync, verified file index
web/v-<version>/   the server's web app; web/current.json names the one in use
parts/             partial downloads being resumed (removed after 2 days)
logs/client.log    rotating log (1 MB × 5)
```

Deleting the folder resets the client (queued changes are lost). Deleting `site/` and `state/` forces a full resync.

## Troubleshooting

* **"Can't reach the server"** — check the address with Test connection; on the LAN use `http://<ip>:7560`.
* **Certificate could not be verified** — the client uses the OS trust store (`/etc/ssl/cert.pem` on macOS, the
  Windows store); behind a TLS-inspecting proxy set `"ca_file": "/path/to/ca.pem"` in `config.json`.
* **"Open Cyber-Scout from the client's own address"** — open `http://127.0.0.1:7600/` (not a bookmark of another
  host name / port); the page sets the cookie the client requires.
* **Port 7600 in use** — `--port 7601`.
* **Sync stuck / weird data** — look at `logs/client.log`; `status.errors` has the last error per event. Delete
  `state/<ev>.json` to make that event resync from scratch (files already present and correct are not downloaded
  again: they are re-hashed).
* **A change won't go through** — Settings → Connection → outbox: conflicts and rejected changes stay there until
  discarded.

## Development

```bash
python3 -m unittest discover -s client/tests -v          # fake part-1 server; ~20 s
.venv/bin/python client/tests/e2e/run_e2e.py             # real API + staging data, LAN + slow link (needs .work/site_staging)
.venv/bin/python client/tests/e2e/run_e2e.py --serve 8833  # just run that API + front for manual testing
```

Code: `cyberscout_client.py` (entry) → `csclient/app.py` (wiring, CLI), `web.py` (local HTTP server, proxy,
control API, security checks), `sync.py` (sync engine + outbox flush), `mirror.py` (safe bundle extraction, web
mirror), `net.py` (keep-alive HTTP(S) to the server, session/CSRF), `store.py` (config, session, SQLite outbox,
per-event state), `util.py`.

## Building

PyInstaller can't cross-compile: each OS builds its own executable. The scripts make a throwaway venv per Python in
`.work/client-build/` with PyInstaller, run `build/make_bundle.py` (copies the repo's `web/` into
`client/web_bundle/`, git-ignored) and build `build/cyberscout.spec` (one file, console) into `client/dist/`:

```bash
client/build/build.sh                                   # macOS / Linux, this machine's arch
CS_TARGET_ARCH=x86_64 client/build/build.sh             # macOS: Intel build on an Apple-silicon Mac
CS_TARGET_ARCH=universal2 client/build/build.sh         # macOS: one file for both
CS_SMOKE=1 client/build/build.sh                        # + run the smoke test on the result
powershell -ExecutionPolicy Bypass -File client\build\build.ps1    # Windows (PYTHON=… or the py launcher)
python3 client/build/smoke_test.py client/dist/<file> [--server mock | --server https://scout.example.dev]
```

| output | where to build it |
|---|---|
| `cyberscout-client-macos-arm64` / `-macos-x64` / `-macos-universal2` | a Mac. `build.sh` uses the python.org Python (universal2, runs on macOS 10.13+/11+) when installed; Homebrew's Python is single-arch and its binaries need the macOS version they were built on |
| `cyberscout-client-linux-x64` | Linux x86_64 with an **old-glibc Python** so the binary runs on older distros: `uv python install 3.12` then `PYTHON=$(uv python find 3.12) client/build/build.sh` (python-build-standalone, glibc 2.17). A distro Python ties the binary to that distro's glibc |
| `cyberscout-client-windows-x64.exe` | Windows with Python 3.9+ from python.org; the exe gets the brand icon (`build/icon.ico`) |

`smoke_test.py` starts the executable with `--no-browser` on a free port with a throwaway data folder, checks
`GET /` (web app + `cs_client` cookie), `GET /client/v1/status`, optionally `POST /client/v1/test` (`--server mock`
starts a stand-in `/app/v1/info` on the machine; a real address only calls its public `/app/v1/info`), and stops it.

**CI** (`.github/workflows/client.yml`): on push / PR touching `client/` or `web/`, a matrix of ubuntu-latest,
macos-14 (arm64), macos-15-intel (x64) and windows-latest runs the unit tests, builds with these scripts, smoke-tests
the binary and uploads the artifacts `cyberscout-client-linux-x64`, `cyberscout-client-macos-arm64`,
`cyberscout-client-macos-x64`, `cyberscout-client-windows-x64.exe` (each with its `.sha256`). A `v*` tag also creates
a **draft** GitHub Release with the four files and `SHA256SUMS`.

Signing (not done; that is why the Gatekeeper / SmartScreen steps above exist): macOS: set `CS_CODESIGN_ID` to a
Developer ID Application identity, then notarize (`xcrun notarytool submit … --wait`; a bare executable can't be
stapled, Gatekeeper checks online). Windows: `signtool sign /fd sha256 /tr <timestamp url> /td sha256 …` with an
OV/EV certificate (SmartScreen reputation builds up per certificate).
