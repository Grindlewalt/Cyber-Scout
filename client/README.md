# Cyber-Scout Client

The Cyber-Scout web app on a laptop, **with a full local copy of the server's data**, so it keeps working when the
venue Wi-Fi doesn't. Part 2 of [docs/SYNC.md](../docs/SYNC.md). The client downloads what the server has built
(site JSON, optionally videos) and queues your changes while you're offline. A laptop with a GPU can also **help
process matches** (track match video for the server), and any laptop can **record the stream and send match clips**
(Clip only); see [Help process matches](#help-process-matches-processor-mode).

```
browser ──► http://127.0.0.1:7600  (Cyber-Scout Client, this folder)
              ├─ /            the web app (a copy of the server's web/ files; a bundled copy before the first connect)
              ├─ /api/  /media/   from the local mirror (falls through to the server when a file isn't local yet)
              ├─ /app/v1/...  proxied to the server with the client's own session; writes go through the outbox
              └─ /client/v1/  local control API (status, settings, sign-in, sync now, outbox, stop, uninstall)
                       │
                       └──HTTPS──► your server (e.g. https://scout.example.dev or http://10.0.0.58:7560)
```

Pure Python standard library (Python 3.9+), no dependencies. Standalone executables are built with PyInstaller.

## Install and run

**One line** (from your team's downloads site, e.g. `http://10.0.0.58:7580`; it also shows these on its home page):

```bash
curl -fsSL http://<site>/downloads/get-client.sh | bash                 # macOS / Linux
irm http://<site>/downloads/get-client.ps1 | iex                         # Windows (PowerShell)
```

It picks this computer's build, checks its SHA-256, installs it (`~/CyberScout`, Windows `%LOCALAPPDATA%\CyberScout`),
presets the team server on a first install (the site's default, or `--server URL` / `$env:CS_SERVER`), starts it at
login (macOS LaunchAgent `com.cyberscout.client`, restarted after a crash but not after a clean exit; Linux systemd
user service, `Restart=on-failure`; Windows Startup shortcut) and opens `http://127.0.0.1:7600/`: sign in and it
syncs. No Gatekeeper / SmartScreen steps (a curl download isn't quarantined; the Windows script unblocks the file).
Run it again to update; `bash -s -- --uninstall` (`$env:CS_CLIENT_UNINSTALL='1'`) removes it and keeps the data
folder (or use **Uninstall** in the app, see [Stop and uninstall](#stop-and-uninstall)). The server installer, when
run through `get.sh` / `get.ps1`, prints both lines with this server's address. Source: `site/tools/get-client.*.in`.

**With a connection key** (an admin makes one for you on the server; it works once and expires): add it and the
Client is set up for that server and signed in, no password typed:

```bash
curl -fsSL http://<site>/downloads/get-client.sh | bash -s -- --key 'cyberscout:http://10.0.0.58:7560#7KQ2-M9XA-P4HD-W8NE'
$env:CS_KEY = 'cyberscout:http://10.0.0.58:7560#7KQ2-M9XA-P4HD-W8NE'; irm http://<site>/downloads/get-client.ps1 | iex
```

The key may also be written `<server>#<KEY>`, `server=<server> key=<KEY>` or just `<KEY>` (env `CS_KEY` for the
shell script too). Its server counts as `--server` / `CS_SERVER` unless that is given (it needs `http://` or
`https://`). Once the Client answers, the script opens `http://127.0.0.1:7600/#/connect?k=<the whole key, URL-encoded>`
instead of the plain page: the web app's Connect page connects, signs in with the key and drops it from the address.
With `--no-open` (or no desktop) the script prints that address instead: open it once.

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

**To help process matches** (optional, admins): open `http://127.0.0.1:7600/client/processing` (or Settings → Help
process matches → the link). The page checks the GPU first:

| computer | tracking | what Set up processing downloads once |
|---|---|---|
| Windows 10/11 or Linux with an NVIDIA GPU (GTX 16xx / RTX 20xx or newer, driver 580+, 2 GB+ video memory) | yes, CUDA | about 1.8 GB (3.2 GB on disk; needs ~9.5 GB free during the install) |
| Mac with Apple silicon, macOS 14+ | yes, Core ML | about 0.25 GB (0.6 GB on disk) |
| anything else (Intel Mac, AMD / Intel graphics, no driver) | no: the page says why | Clip only tools, about 0.23 GB |

Then: **Set up processing** (one click; it downloads uv, Python 3.12 and the packages into the client's data folder,
then runs a 20-second benchmark), turn **Help process matches** on, and leave the client running. Nothing else is
installed on the computer and no admin rights are needed; the files uv downloads aren't quarantined, so Gatekeeper /
SmartScreen don't ask again (only the client executable itself needs the first-start step above).

**From source** (any OS with Python 3.9+):

```bash
python3 client/cyberscout_client.py                 # opens http://127.0.0.1:7600/ in your browser
python3 client/cyberscout_client.py --server https://scout.example.dev --port 7600 --no-browser
```

A terminal window shows the address and the log; closing it (or Ctrl+C) stops the client; the app's pages then say
"Client not running" until you start it again (your data and queued changes stay). One client runs per data folder
(`client.lock` in it): starting it again on the same folder opens the running one, even on another port. A second
install on the same computer needs its own `--data` and `--port`.

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

1. Start the client; the browser opens `http://127.0.0.1:7600/` on the **Welcome to Cyber-Scout** page
   (`#/connect`: Connect → Sign in → Sync).
2. Type the server address, the one you use for Cyber-Scout in a browser, e.g. `scout.grantrindal.dev` (through the
   tunnel) or `10.0.0.58:7560` (shop LAN / Tailscale). Without `http://` / `https://`, an IP address, `localhost`, a
   name without a dot or one ending in `.local` / `.lan` / `.home` / `.internal` / `.ts.net` gets `http://`; any other
   name gets `https://`.
3. **Test connection** (or **Connect**, which tests first) shows the server's name, version and round-trip time, or
   what is wrong in a sentence: "Can't reach … connection refused (is the server running at that address?)", "This
   address doesn't use HTTPS. Try http://…" (with a button to use it), "That's this Cyber-Scout Client's own
   address", "Something answers at …, but it isn't Cyber-Scout", "… is a Cyber-Scout server, but a version without
   offline sync", "the server's certificate could not be verified".
4. **Sign in** with your Cyber-Scout account, or with a **connection key** from an admin (`XXXX-XXXX-XXXX-XXXX`,
   works once; the installers' `--key` / `CS_KEY` and the Connect page's `#/connect?k=…` use it, the control API is
   `POST /client/v1/login-key`). The client signs itself in; the browser page never sees the server session. The data
   downloads in the background; the app is usable meanwhile.
5. Later changes are in **Settings → Connection**: the server, sign out, **Videos** (**None**: data only, the default;
   **Previews**: a ≈480p copy of every match video, ≈27 MB per match; **Full videos**: everything the server keeps,
   GBs per event: the option shows the size first and asks before downloading), **Competitions** (default: every one
   the server follows; untick one to stop syncing it, then **Remove from this computer** to delete its local copy),
   storage, and **Changes waiting**.

The **connection pill** (bottom of the sidebar; in the toolbar on phones and in the Top bar / Command layouts) shows
the state at a glance: `Online · synced 2 min ago`, `Syncing 40%` (by bytes), `Connecting…`, `Offline · 3 changes
waiting`, `Signed out`, `Client not running` (the client process stopped). A red number = changes that need your
choice. Clicking it opens the **Connection** sheet: server (and the alternate address in use, if any), last sync,
what's on this computer, every change waiting or in conflict with its buttons, **Sync now**.

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
* **One picklist save per list**: a picklist save replaces a still-unsent save of the same list made on the same
  version (it keeps that one's place in the queue and takes the new key). If the earlier one was already tried, the
  later one stays separate and is **rebased** onto the version the earlier one creates once it is accepted (only
  saves made on that same base version: a save made after a newer version was seen is left alone). So any number of
  offline clicks end as one change, not as conflicts with yourself.
* `409` conflicts (a picklist someone else saved meanwhile) are kept, shown in `status.errors` (`kind:"conflict"`,
  with the server's answer incl. the current version) and, with their buttons, in the Connection sheet and Settings →
  Connection → Changes waiting. The card says who saved and what each choice drops; nothing is merged: **Save mine
  over theirs** replaces their list with yours (theirs stays in the picklist history), **Use theirs** drops yours.
  Other rejected changes (`kind:"failed"`, e.g. 403 lockdown, 422) are listed with **Try again** / **Discard**. A
  `401` stops the flush until you sign in again.
* **Changes belong to their server**: each outbox row remembers the server it was written for and is only sent there
  (or to that server's `--alt-server` addresses). After switching to another server they wait, listed as "For
  <address>" (`status.outbox_held`), until you switch back; the switch's confirmation says so.
* Sign-in, sign-out, password change and setup are never queued (they need the server).
* Videos never fill the disk: a media sync keeps 2 GB free (it downloads what fits, smallest first, and says how much
  was left out in `status.errors`, `kind:"media"`).

## Help process matches (processor mode)

Tracking one match takes the server about 3.5 minutes. Any computer running the client can share that work:
**Help process matches** makes it claim the next match the server would track, download its livestream clip, run
the server's own pipeline on it and upload the results. The server publishes them as if its worker had tracked the
match (and keeps the clip in `MEDIA/<ev>/<key>.mp4`). Server side: `server/routers/processing.py`.

* **Needs an admin account** (a processor's uploads become the event's tracking data). Signed in with another role,
  the page says who is signed in and keeps the switch and Set up off. Set up asks before its download.
* Open `http://127.0.0.1:7600/client/processing`: three readouts (**GPU**, **Tracking one match** in minutes,
  **Wi-Fi to the server**) each with a verdict, the switch with **Track matches / Clip only**, the status line, Set
  up and Advanced. Or start with `--process on [--process-python /path/to/python]`. The status line is also in
  `GET /client/v1/status` → `processing.line`, e.g. `processing qm12 at 40%, done 3`, and the main app's Settings
  page shows it (with the notices below and a link to the page) when it runs inside the client.
* **Same claims as the server's worker** (`events/<ev>/claims/<key>.json`, `live/claims.py`), in the same order
  (time order, `priority.json` first), so the server, `tools/helper.py` and any number of clients never track a
  match twice. A heartbeat refreshes the claim every minute; a claim not refreshed for 30 min is handed to someone
  else (a processor that disappears costs at most 30 min for that match). A match that fails here is released and
  skipped here for 30 min; after 2 processor failures the server leaves it to its own worker.
* One match at a time, never the same one twice: the server never hands a processor a match it already holds, and
  `processing/inflight.json` remembers the job in progress, so after a crash or restart the client releases that
  claim before it asks for work (instead of leaving it to go stale for 30 min).
* Only **livestream clips** are handed out (the worker's livestream input: a trackable stream with a calibrated
  camera). Per-match YouTube uploads stay with the server's worker.

### GPU only

A CPU-only laptop takes far longer per match than the server, so it would hold matches back. `csclient/gpu.py`
checks the machine once per run (**Check again** after installing a driver):

* **NVIDIA** (Windows / Linux): `nvidia-smi` lists the GPUs; one is usable with driver **580 or newer** (CUDA 13,
  which onnxruntime-gpu 1.31 is built for), compute capability **7.5+** (CUDA 13 dropped Maxwell / Pascal / Volta:
  GTX 16xx, RTX 20xx and newer) and 2 GB+ of memory. The one with the most free memory is used (`GPU_DEVICE`); one
  that other programs have filled is shown as "N GB in use by other programs".
* **Apple silicon** (also when the Intel build runs through Rosetta: `sysctl hw.optional.arm64`) on macOS 14+: Core
  ML (`FORCE_CPU=1 ORT_COREML=1`, like `tools/helper.py`; the compiled model is cached in
  `processing/coreml-cache/` between matches).
* Otherwise tracking stays off: state `no_gpu`, with the reason (e.g. "this computer's graphics (Intel(R) UHD
  Graphics 620) can't run the tracker: it needs an NVIDIA GPU with CUDA. Clip only works here.").

The benchmark is the final check: if onnxruntime falls back to the CPU (driver problem, the GPU's memory full), the
page says "the GPU did not work in the benchmark ... (the GPU's memory is full: close other programs that use it)"
and tracking stays off. `CS_FAKE_GPU=none|cuda|coreml` fakes the check (tests, screenshots).

### Set up processing

The pipeline (`live/` + `src/`: onnxruntime, PyAV, OpenCV, NumPy, SciPy, pupil-apriltags) is native code that can't
be part of this stdlib client, so it runs in a separate **processing Python**: `processing/venv/`, made once by
**Set up processing** (`csclient/procsetup.py`). It needs no Python on the computer and no admin rights:

1. **uv** ([astral.sh/uv](https://docs.astral.sh/uv/)): an installed one, else the pinned release (0.12.21) is
   downloaded from GitHub into `processing/tools/` and checked against its published sha256.
2. `uv venv --python 3.12` (uv downloads python-build-standalone into `processing/python/`).
3. `uv pip install` of the **runtime for this GPU**, with the server's pins (`GET /app/v1/processing/pipeline` →
   `runtimes`): `cuda` = `onnxruntime-gpu[cuda,cudnn]` (the pip CUDA 13 + cuDNN wheels; `ort.preload_dlls()` loads
   them, no CUDA toolkit needed), `coreml` = plain `onnxruntime`, `clip` = just numpy / opencv / av. Every runtime
   also gets the Clip only tools (`imageio-ffmpeg`'s ffmpeg binary, `deno` for yt-dlp's YouTube support) and yt-dlp
   from `live/requirements-ytdlp.txt` (`--require-hashes`, like the worker image).
4. uv's cache is deleted afterwards (the files live in the venv), then the benchmark runs.

The page shows what it will download and how much disk it needs before you press the button, and Set up refuses
when the disk is too full (it needs twice the install size + 3 GB free while it runs: the download sits in uv's
cache first). Measured installs (uv 0.12.21, 2026-10-18):

| runtime | download | on disk | time |
|---|---|---|---|
| `cuda` (Linux x86_64, RTX 3060) | 1.75 GB | 3.1 GB | 33 s on a fast link |
| `coreml` (Apple M1) | 0.24 GB | 0.6 GB | 12 s (setup + benchmark: 39 s) |
| `clip` | ~0.23 GB | ~0.5 GB | |

`/client/processing` → **Advanced** → **Processing Python** (`process_python`, `--process-python`) can instead name any Python that has the
packages, e.g. a Cyber-Scout checkout's `.venv/bin/python`. The client checks the Python once per version (`import
onnxruntime, av, cv2, numpy, scipy, pupil_apriltags`; Clip only: `av, cv2, numpy, yt_dlp` + an ffmpeg); the status
says `set up needed: …` until it works. `client/build/runtime_check.py --runtime auto|cuda|coreml|clip` runs the same
install + checks by hand (CI runs it on every OS).

### Benchmark

After setup (and with **Run benchmark**) `runner.py --bench` decodes the first 24 MB of a recent livestream clip from
the server (`GET /app/v1/processing/sample`; synthetic frames when the server has none) and times the detector on
40 frames with this GPU. Minutes per match = detector at 30 Hz over a 190 s clip + 5 × the decode time (the rest of
the pipeline, fuel tracker / OCR / shots / identity, is CPU work that scales with it) + 12 s, fitted to a real job:

| computer | detector | decode | estimate | real match |
|---|---|---|---|---|
| Apple M1, Core ML (2026wagg qm10, 1080p60 livestream clip) | 27.3 fps | 622 fps | 5.2 min | 5.2 min (312.7 s) |

Before this computer is measured, the page shows the time of a GPU we measured (an Apple M1: about 5.2 min per
match). After the first real matches it shows the measured time (median of the last 3) instead. A computer that needs
more than **25 min per match** pauses (state `slow`): "This computer needs about 34 min per match (benchmark; the
server needs about 3.5): tracking here would slow the event down. Clip only helps more, or track anyway." with
**Switch to Clip only** and **Track anyway** (`allow_slow`, saved).

### Slow connection and data cost

Each match downloads its clip (typically 150–250 MB; 191 MB at 2026wagg) and uploads 1–3 MB; the first run also
fetches the code (~12 MB) and the model (~38 MB). The client measures the link to the server
(`csclient/procnet.py`): a short speed test (`GET /app/v1/processing/speedtest`, 1 MB, 4 MB more on a fast link, and
1 MB up; again every 30 min without real transfers, or with **Check connection**) plus every real clip transfer.
When a clip would take more than 3 minutes to download, the page and the main app's Settings say so:

> Slow Wi-Fi: about 0.6 MB/s from the server, so a match clip (191 MB) takes about 6 min to download; processing
> here won't be optimal (the server may be faster on its own). Each clip is also over your 100 MB data warning, so
> you're asked before every download.

(measured through a link throttled to 0.6 MB/s down / 0.4 MB/s up). On a metered link (phone hotspot) **Ask before
downloading a clip over N MB** (`process_warn_mb`, default 100, `0` = never ask) pauses before each large clip (the
match goes back to the others) until you press **Allow large clips** (until the client restarts), raise the size or
turn processing off. The page and `processing.data` show the bytes used this session.

### Clip only

For a computer without a usable GPU, or one too slow to track: **Clip only** records a livestream and cuts every
match out of it, with the server's own code (`live/livestream.py` Recorder + ChunkStore, `live/liveingest.py`
`tba_cut`), run by `runner.py --clip` in the processing Python (`csclient/clipper.py` drives it):

1. `POST /app/v1/processing/streams/claim`: the server assigns a stream, one client per stream: a livestream source
   its own recorder isn't on (a second view) first, else the one it records (a backup recording). The client
   records it into `processing/streams/<aid>/rec/<ev>/r<n>/` (one folder per video id; YouTube's DVR fills a short
   gap after a restart), keeping the last 2 hours (and at most 40 % of the free disk, 3–12 GB; it needs 8 GB free
   to start).
2. A heartbeat every minute keeps the assignment and returns TBA's match start times plus which matches the server
   still lacks a clip for (`want`: over for 3 min, no usable clip from any other recording; when the server records
   the event itself it first waits 4 more minutes for its own clip). Only those are cut (TBA start − 15 s to the
   end + 10 s, the auto start measured on the clip by the overlay OCR or else taken from TBA) and uploaded.
3. Each clip goes up in 8 MB chunks (`POST …/clips/<mid>` → offset, `PUT …/data?offset=` per chunk, `POST …/finish`
   with the record); a broken link resumes from the server's offset. The local clip is deleted once it is sent.
4. The server checks size + sha256, then writes `MEDIA/live/<ev>/<aid>/clips/<mid>.mp4`, `matches/<mid>.json` (flag
   `uploaded_by_client`) and `meta.json`: an ordinary livestream record, so the worker or a GPU processor tracks it
   when that stream is calibrated (otherwise it is the match's video on the site).

The page lists the stream, the recorder (state, seconds behind live, disk), and per match: uploaded, the server has
it, cutting, waiting (e.g. "the recording is missing 11 of 37 chunks of this match" for a match that started before
the recording did). Recording a 1080p60 stream takes about 1 MB/s down; when the recording falls more than 90 s
behind live, or a clip would take over 5 min to upload, the slow-Wi-Fi notice says so.

### Code, model, files

Code and model come from the server, cached by version: `GET /app/v1/processing/pipeline` names the code version
(content hash of `live/` + `src/` + the runner + the pins, e.g. `p-e46bb97a3587`) and the model's sha256; the client
downloads `code.tar.gz` and `robot.onnx` (Range-resumable, checksum-verified; Clip only skips the model) into
`processing/pipelines/<version>/` and `processing/models/<sha256>.onnx`. Every claim carries the version; when the
server's code changes, the claim is refused (409) and the client fetches the new code first. The pipeline runs as
`<python> -u <pipeline>/runner.py job.json` (`csclient/procrunner.py`, shipped by the server inside its code tarball
so it always matches that code), which calls `live/worker.py`'s `process_match` on the clip and reports progress on
stdout (`@@CS {json}` lines).

Per match (all resumable on a flaky link): `POST /app/v1/processing/claim` → `GET …/clip` (Range; `processing/parts/`
survives restarts) → pipeline → `PUT …/results/{fuel,shots,analytics,matches}` (gzip) → `POST …/complete`
(Idempotency-Key, so a lost answer is not a problem) — or `POST …/release` on failure / when turned off.

| file | |
|---|---|
| `processing/pipelines/<version>/` | the server's pipeline code (`live/`, `src/`, `runner.py`, `requirements.txt`) |
| `processing/models/<sha256>.onnx` | the detector |
| `processing/venv/`, `processing/python/`, `processing/tools/` | the processing Python Set up made (`venv/cs-runtime.json` names its runtime), uv's Python, uv |
| `processing/bench.json`, `processing/perf.json` | the benchmark result; timings of the last 20 real matches |
| `processing/bench/` | the benchmark's sample clip (24 MB) |
| `processing/coreml-cache/` | Core ML's compiled detector (Macs) |
| `processing/work/<ev>/<key>/` | the match being tracked (clip, job.json, runner.log); removed afterwards |
| `processing/streams/<aid>/` | Clip only: the recording (`rec/`), `want.json`, `state.json` (uploaded), `runner.log` |
| `processing/logs/<key>.log`, `logs/benchmark.log` | the pipeline's log of the last 30 matches; the last benchmark |
| `processing/inflight.json` | the job in progress (its claim is released after a crash / restart) |

Control API: `GET /client/v1/processing` → `{enabled, mode (track | clip), state (off | waiting | checking | updating
| needs_setup | no_gpu | benchmarking | slow | idle | confirm | downloading | processing | uploading | clip | error),
line, message, key, match, pct, done, failed, last, large, data {down, up, clips}, warn_mb, large_ok, allow_slow,
processor, python, python_setting, pipeline, events, setup {state, message, log, progress}, gpu {ok, kind, runtime,
name, detail, why, device, gpus}, speed {per_match_min, from (measured | benchmark), measured_min, slow, slow_min,
server_min, bench, running, jobs}, net {down_bps, up_bps, clip_bytes, clip_down_min, clip_up_min, slow, notice,
probing}, notices [{kind (no_gpu | gpu_failed | slow_gpu | slow_net | metered), level, text}], clip {aid, ev, source,
label, url, role, rec, uploaded, skipped, upload, matches}, plan {runtime, label, download, disk, free, need,
installed, up_to_date, ok, why}}`; `PUT /client/v1/processing {enabled?, mode?: track | clip, python?: absolute path
| null, events?: [keys] | null, warn_mb?: 0-100000, allow_large?: true, allow_slow?: true}`; `POST
/client/v1/processing/setup` (202; progress in `setup`), `POST …/bench` (202), `POST …/netcheck` (202), `POST
…/gpu` (check the GPU again → `{gpu}`).

## Control API (`/client/v1/*`)

Only for pages served by this client: requests need the per-install `cs_client` cookie (HttpOnly,
SameSite=Strict; set when the client serves the page), an `Origin` (if sent) of `http://127.0.0.1:<port>` /
`http://localhost:<port>`, no cross-site `Sec-Fetch-Site`, and a `Host` of 127.0.0.1/localhost (DNS rebinding).
The same gate protects `/app/v1`, `/api` and `/media`; page writes to `/app/v1` also need `X-CSRF-Token` = the
local `cs_csrf` cookie (the client's own token — the server's CSRF token and session cookie stay inside the client).

| call | answer |
|---|---|
| `GET status` | `{client:true, version, server, online, signed_in, user:{display_name,role}\|null, last_sync, syncing, progress:{done,total,bytes,done_bytes,total_bytes,ev,phase}\|null, outbox, events:[{key,name,synced_at,files,bytes,media_bytes}], errors:[{time,kind,message,ev?,id?,response?}]}` plus `active_server` (the address in use: `server` or one of `alt_servers`), `alt_servers`, `connecting` (the server address just changed and hasn't answered yet), `outbox_held` (`[{server, n}]`: changes written for another server), `media, followed, available_events, offline_reason, server_info, web_version, storage`, `install:{frozen, program, autostart, can_uninstall, data_dir}` (see [Stop and uninstall](#stop-and-uninstall)). `progress.done/total` count files, `done_bytes/total_bytes` their bytes (the app's percentage uses these); `bytes` is bytes received. `outbox` = this server's changes not yet accepted. `errors[].kind`: `network`, `auth`, `sync`, `media`, `web`, `conflict`, `failed` |
| `PUT config` `{server?, alt_servers?, media?, events?, interval?}` | `200 {ok, config}`; `422 {detail}` on bad values. `events:null` = all followed events. Changing `server` signs out and resyncs |
| `POST test` `{server?}` | always `200`: `{ok:true, server, latency_ms, version, name, api}` or `{ok:false, server, error, suggest?}` (`suggest`: the `http://` address when an `https://` one answered in plain http) |
| `POST login` `{username,password}` | `200 {ok:true, user}`; server's status + `{ok:false, detail}` (401 bad password, 429 …); `503 {offline:true}`; `400 {ok:false}` when no server is set |
| `POST login-key` `{key}` | sign in with a connection key (the server's `POST /app/v1/auth/key-login`); the same answers as `login`, plus `422 {ok:false, detail}` when `key` isn't a string of 1–64 characters. The page's own `POST /app/v1/auth/key-login` is handled the same way (the client keeps the session) |
| `POST quit` | **Stop**: `200 {ok:true, how, message}`, then the client shuts down like Ctrl+C. `how`: `launchagent` (it ran under its LaunchAgent: unloaded so it isn't restarted, back at the next login), `systemd` (a clean exit; the unit restarts only on failure), `exit`. `message`: one sentence for the page |
| `POST uninstall` `{delete_data?: bool}` | `200 {ok:true, removed:[paths], kept:[paths], errors:[sentences]}`, then stops like `quit`; `409 {detail}` when run from source; `422` for a non-boolean `delete_data`. See [Stop and uninstall](#stop-and-uninstall) |
| `POST logout` | `200 {ok:true}` |
| `POST sync` `{ev?, media?}` | `202 {started:true, online}` |
| `GET outbox` | `[{id, key, method, path, created, updated, state:"queued"\|"sending"\|"conflict"\|"failed", attempts, status, error, body, response, server}]` |
| `DELETE outbox/<id>` | `204`; `404` unknown; `409` while it is being sent |
| `DELETE events/<ev>` | delete this computer's copy of a competition that isn't synced any more → `200 {ok, freed}`; `409` while it is still synced or a sync runs |
| `GET processing`, `PUT processing`, `POST processing/setup`, `POST processing/{bench,netcheck,gpu}` | processor mode, see [Help process matches](#help-process-matches-processor-mode) |

## Stop and uninstall

The app's Settings can stop the client or remove it (`POST /client/v1/quit`, `POST /client/v1/uninstall`;
`csclient/install.py`). `GET /client/v1/status` → `install` says what is possible:

| field | |
|---|---|
| `frozen` | `true` for the standalone executable, `false` when run from source |
| `program` | the executable (`null` from source) |
| `autostart` | the login item that starts this program: `launchagent` (`~/Library/LaunchAgents/com.cyberscout.client.plist`), `systemd` (`~/.config/systemd/user/cyberscout-client.service`), `startup` (`Cyber-Scout Client.lnk` in the Windows Startup folder) or `null`. One that starts another copy (another `--dir`) doesn't count |
| `can_uninstall` | `true` when frozen (from source there is nothing installed: Uninstall answers 409 "This Client runs from source (…): nothing is installed to remove. Use Stop instead.") |
| `data_dir` | the data folder in use |

**Stop** answers first, then shuts down exactly like Ctrl+C / `kill` (processing children stopped, claims given back,
the data-folder lock released). Nothing restarts it before the next login: under its LaunchAgent (launchd sets
`XPC_SERVICE_NAME=com.cyberscout.client`) the client unloads its own job afterwards (`launchctl bootout
gui/<uid>/com.cyberscout.client`, from a separate session; launchd's SIGTERM then just ends the process), since a
LaunchAgent from an older install has `KeepAlive` true (new installs: `KeepAlive` = `{SuccessfulExit: false}`). Under
the systemd user unit a clean exit is enough (`Restart=on-failure`); on Windows it just exits. To start it again
before the next login: run the program (`~/CyberScout/cyberscout-client`, Windows
`%LOCALAPPDATA%\CyberScout\cyberscout-client.exe`) or the install line again.

**Uninstall** `{delete_data}` undoes what `get-client.sh` / `get-client.ps1` installed, and nothing else:

1. the login item: macOS deletes the plist (and unloads the job: right away when it isn't the one running, else
   after the shutdown); Linux `systemctl --user disable`, deletes the unit, `daemon-reload`; Windows deletes the
   Startup shortcut;
2. the program file (`sys.executable`), and its folder only when it is the scripts' (the program is named
   `cyberscout-client[.exe]` and the folder holds nothing but it and `logs/client.out`); a folder with other files
   is listed in `kept`;
3. with `delete_data: true`, the data folder: only the Client's own entries (`config.json`, `client.lock`,
   `session.json`, `client.sqlite3*`, `site/`, `state/`, `parts/`, `tmp/`, `web/`, `logs/`, `processing/`), after
   the shutdown; anything else in it stays (in `kept`, and so does the folder). Without it the data folder is in
   `kept`. Never the home folder or a folder above it.

Symbolic links are never followed or removed (listed in `kept` with a line in `errors`). The login item and (macOS /
Linux) the program and its folder are removed before the answer; the rest right after the shutdown. A running `.exe`
can't delete itself on Windows: a detached `cmd.exe` (no window) retries deleting it for up to ~40 s until the
process has exited, then deletes the data entries and the emptied folders. `removed` lists those as well.

## Storage

| OS | default data folder |
|---|---|
| macOS | `~/Library/Application Support/CyberScout` |
| Windows | `%APPDATA%\CyberScout` |
| Linux | `$XDG_DATA_HOME/cyberscout` or `~/.local/share/cyberscout` |

```
config.json        settings + per-install secrets (0600)
client.lock        held while a client runs on this folder ({pid, url}: a second start opens that one)
session.json       the server session cookie + CSRF token (0600; never sent to the page)
client.sqlite3     outbox + cache of /app/v1 answers (0600)
site/api/…         the data mirror  (site/media/… videos)
state/<ev>.json    per event: manifest cursor, last sync, verified file index
web/v-<version>/   the server's web app; web/current.json names the one in use
parts/             partial downloads being resumed (removed after 2 days)
logs/client.log    rotating log (1 MB × 5)
processing/…       processor mode: pipeline code, model, venv + its Python + uv, benchmark, Clip only recordings,
                   work dir, logs (see Help process matches; delete processing/ to remove all of it)
```

Deleting the folder resets the client (queued changes are lost). Deleting `site/` and `state/` forces a full resync.

## Troubleshooting

* **"Can't reach the server"** — check the address with Test connection; on the LAN use `http://<ip>:7560`.
* **Certificate could not be verified** — the client uses the OS trust store (`/etc/ssl/cert.pem` on macOS, the
  Windows store); behind a TLS-inspecting proxy set `"ca_file": "/path/to/ca.pem"` in `config.json`.
* **"Open Cyber-Scout from the client's own address"** — open `http://127.0.0.1:7600/` (not a bookmark of another
  host name / port); the page sets the cookie the client requires.
* **Port 7600 in use** — `--port 7601`. If another Cyber-Scout Client holds it (a different data folder), the log
  says so.
* **"Client not running"** — the client process stopped (its terminal window was closed, or **Stop** in the app):
  start it again; nothing is lost.
* **Sync stuck / weird data** — look at `logs/client.log`; `status.errors` has the last error per event. Delete
  `state/<ev>.json` to make that event resync from scratch (files already present and correct are not downloaded
  again: they are re-hashed).
* **A change won't go through** — click the connection pill (or Settings → Connection → Changes waiting): conflicts
  and rejected changes stay there, with their buttons, until you resolve or discard them.

## Development

```bash
python3 -m unittest discover -s client/tests -v          # fake part-1 server; ~20 s
.venv/bin/python client/tests/e2e/run_e2e.py             # real API + staging data, LAN + slow link (needs .work/site_staging)
.venv/bin/python client/tests/e2e/run_e2e.py --serve 8833  # just run that API + front for manual testing
```

Code: `cyberscout_client.py` (entry) → `csclient/app.py` (wiring, CLI), `web.py` (local HTTP server, proxy,
control API, security checks), `sync.py` (sync engine + outbox flush), `mirror.py` (safe bundle extraction, web
mirror), `net.py` (keep-alive HTTP(S) to the server, session/CSRF), `store.py` (config, session, SQLite outbox,
per-event state), `install.py` (Stop / Uninstall: the login item, program and data folder), `util.py`, `processor.py` (processor mode), `gpu.py` (the GPU check), `procsetup.py` (Set up:
uv, runtimes, sizes), `procnet.py` (connection speed, the slow-Wi-Fi notice), `clipper.py` (Clip only), `procui.py`
(the processing page), `procrunner.py` (the pipeline side: track / `--bench` / `--clip`; runs in the processing
Python, shipped by the server).

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

**CI** (`.github/workflows/client.yml`): on push / PR touching `client/`, `web/`, `server/routers/processing.py` or
`live/requirements-ytdlp.txt`, a matrix of ubuntu-latest, macos-14 (arm64), macos-15-intel (x64) and windows-latest
runs the unit tests, builds with these scripts, smoke-tests the binary (incl. the processing page and its GPU check
inside the executable) and uploads the artifacts `cyberscout-client-linux-x64`, `cyberscout-client-macos-arm64`,
`cyberscout-client-macos-x64`, `cyberscout-client-windows-x64.exe` (each with its `.sha256`). Job **runtime** installs
the processing runtime with `build/runtime_check.py` on each OS (CUDA on Linux + Windows, Core ML on macos-14, Clip
only on macos-15-intel) and checks imports, the GPU execution provider, the pipeline modules and the Clip only tools.
A `v*` tag also creates a **draft** GitHub Release with the four files and `SHA256SUMS`.

Signing (not done; that is why the Gatekeeper / SmartScreen steps above exist): macOS: set `CS_CODESIGN_ID` to a
Developer ID Application identity, then notarize (`xcrun notarytool submit … --wait`; a bare executable can't be
stapled, Gatekeeper checks online). Windows: `signtool sign /fd sha256 /tr <timestamp url> /td sha256 …` with an
OV/EV certificate (SmartScreen reputation builds up per certificate).
