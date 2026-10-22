# Security

Describes commit `cc6297c` (2026-10-12). Every status below was checked against the committed code
(`git show HEAD:<path>`) and, read-only, against the production stack on atomosserver
(`/opt/stacks/cyber-scout`) on 2026-10-12.

> **In progress at time of writing.** Two other agents were changing `server/routers/*`, `web/app.js` (nav and fetch
> code), `web/nginx.conf`, `web/Dockerfile`, `live/worker.py` and `live/sitev2.py`. Line numbers in those files
> will move. Re-check the nginx headers, the `/_auth` gate and the hash-route validation in `web/app.js` after those
> changes land.

Sources: the security audit of 2026-10-12 at ref `0e21afe` (`.work/security/REPORT.md`,
`.work/security/findings.json`, 10 records) and the fix commits after it:

| commit | what it fixed |
|---|---|
| `a94d6ee` | strict picklist schema (audit finding 1, stored XSS) |
| `83b16b7` | audit log + revert, picklist history, identity-fix approval queue, write budgets, passwordless policy, lockdown, CSP and headers, `/docs` off, entrypoint `chown -h`, hash-pinned yt-dlp, nav event-key validation |
| `52e9da6` | passwordless sign-in default **off** (`never`); Admin shows only Off / On-for-testing |
| `74e84af` | source URL normalisation, `--` before every yt-dlp URL, SSRF guard, compose capabilities and limits |
| `fa29065` | host-side updater (no docker socket in any container) |

---

## 1. Summary

- **No path from an anonymous or LAN user to the host was found.** No container is privileged. None gets the
  docker socket or host networking. The API port is not published. The API and worker run as uid 1000 with no
  capabilities. Every subprocess call uses an argv list (no shell), and nothing unsafe is deserialised.
- **All 5 confirmed audit findings are fixed in code and deployed.** Two need the owner to confirm something or
  carry a small residual risk: the Cloudflare tunnel exposure, and the client-side `Number()` coercion (added in the
  cleanup pass, not yet deployed).
- **The biggest real risks now are operational, not code:**
  1. The site is published on `0.0.0.0:7560`. Whether a dashboard-managed Cloudflare tunnel exposes it is not
     visible from the host.
  2. PUID 1000 is the owner's account (groups `docker`, `wheel`), so a container escape as that uid is host root.
  3. The root disk is 100% full (3.0 GB free). The updater refuses a worker rebuild below ~4.5 GB, so security
     updates to the worker image can't be applied.
  4. The design-lab container `cyber-scout-lab` serves a real dashboard snapshot with no auth on
     `0.0.0.0:7570` (and IPv6).

---

## 2. Assets

| asset | where | why it matters |
|---|---|---|
| TBA Read API key | `data/app.db` table `settings` (key `tba_key`), `data/config/worker.json` (0600) | Lets someone act as the team on TBA's read API (rate limit, revocation). Low value, but a secret. |
| Password hashes, session hashes | `data/app.db` tables `users` (argon2id), `sessions` (SHA-256 of the token) | Offline guessing. Session hashes can't be replayed. |
| Scouting-data integrity | `app.db` (picklists, notes, override requests), `data/events/<ev>/overrides.json`, site JSON under `data/site/api/` | A troll or a leaked login could corrupt the picklist or the identity fixes the day of alliance selection. |
| Scouting-data confidentiality | `data/site/api/**`, match videos under `MEDIA_DIR` | Strategy and picklists are competitively sensitive during an event. |
| The host (atomosserver) | Docker, NAS (`/mnt/nas`), other stacks (Portainer, restic REST, searxng, jellyfin, ...) | The worker shares a docker bridge with the host gateway. |
| The code supply chain | public GitHub repo `Grindlewalt/Cyber-Scout`, PyPI (yt-dlp), base images | The updater deploys whatever is on `main`. |

---

## 3. Actors and trust boundaries

```
  Internet ──(Cloudflare tunnel? owner to confirm)──┐
  LAN / Tailscale browsers ─────────────────────────┤
                                                    ▼
                                   host :7560  (WEB_BIND, default 0.0.0.0)
                                                    │
                     ┌──────────────── web (nginx, read-only rootfs) ─────────────────┐
                     │  /            app shell (public; no data)                      │
                     │  /app/v1/*    → api:8000 (login limited 10 r/min per IP)       │
                     │  /api/*       site JSON  ─┐  auth_request /_auth →             │
                     │  /media/*     videos     ─┘  api /app/v1/auth/check           │
                     └────────────────────────────────────────────────────────────────┘
                                                    │ docker bridge cyber-scout_default (172.25.0.0/16,
                                                    │ internal=false, gateway 172.25.0.1)
              api (uid 1000, read-only rootfs) ── data/ (app.db, config/, events/, site/) ── worker / recorder
                                                                                           (uid 1000, GPU,
                                                                                            egress to internet)
              host updater (systemd, owner uid; NOT installed on atomosserver yet)
                 reads data/config/update-request.json, writes update-status.json
```

| actor | can reach | trusted for |
|---|---|---|
| Anonymous internet user | Only if a tunnel or port-forward publishes :7560. Then the shell, `/app/v1/session`, `/app/v1/auth/login`, `/app/v1/auth/key-login`, `/app/v1/setup` (only while there are no users), `/app/v1/health`. | nothing |
| LAN / Tailscale user, not signed in | same as above | nothing |
| viewer | site JSON, videos, picklists, notes, overrides (read) | reading |
| scout | + notes, own display name, identity-fix *requests* | adding notes; identity fixes queue for review (`scout_overrides=review`, the default) |
| strategist | + picklists, editing or hiding anyone's notes, approving identity fixes | shared strategy data |
| admin | everything: accounts, TBA key, sources and layouts, calibration, lockdown, audit revert, updates | the whole app (not the host) |
| Compromised worker or recorder (e.g. a malicious video exploiting ffmpeg, PyAV or OpenCV) | `data/` as uid 1000 (incl. `app.db` and the TBA key), `MEDIA_DIR`, the internet, the docker gateway | — |
| TBA / YouTube data | event names, team lists, webcast entries, match video ids, video bytes | parsed as untrusted (ids validated, URLs rebuilt) |
| Upstream supply chain | PyPI yt-dlp, base images, the GitHub repo | pinned where noted below |

---

## 4. What is protected, and how (layer by layer)

### 4.1 Network exposure
- `deploy/docker-compose.yml` publishes **only** the web container: `"${WEB_BIND:-0.0.0.0}:${WEB_PORT:-7560}:80"`.
  The api (`8000/tcp`) is not published. It is reachable only through nginx on the compose network.
- `WEB_BIND` (added in `74e84af`) can pin the listener to a LAN or Tailscale address. **Production doesn't set it**:
  `docker ps` shows `0.0.0.0:7560->80/tcp`.
- The compose network is `internal=false`, so worker and recorder have full egress, including to the host gateway
  `172.25.0.1`. Host services published on `0.0.0.0` (Portainer 8000/9443, restic REST 8200/8201, searxng 8080,
  backrest 9898, uptime-kuma 3001) are reachable from inside the containers. The SSRF guard (§4.12) is the only
  control for URL-driven fetches. Nothing stops code that is already running in the worker.

### 4.2 nginx gate (`web/nginx.conf`)
- `/api/` and `/media/` use `auth_request /_auth`. `/_auth` is `internal` and proxies to `GET /app/v1/auth/check`,
  which returns 204 (plus an `X-CS-Role` header) for any signed-in role and 401 otherwise. A 401 maps to
  `@unauthorized` (a JSON 401). API errors 500/502/504 map to `@api_down` (a 503), so the gate **fails closed**.
- Plain prefix locations with `root` (no `alias`), no autoindex, `try_files $uri =404`. The audit rejected the
  bypass hypothesis. Verified on production: `/api/events.json` → 401 and `/media/x.mp4` → 401 when signed out.
- `location ~ /\.` → 404 (dotfiles). `location ~* \.(db|db-wal|db-shm|db-journal|sqlite|sqlite3|bak|swp|env|pem|key)$`
  → 404. Both regex locations take precedence over the plain `/api/` and `/media/` prefixes. Verified
  `/api/app.db` → 404.
- Only `data/site/api` and `MEDIA_DIR` are mounted into the web container, both `:ro`. `app.db`, `config/` and
  `events/` are never in the web root.
- **Real client IP**: `set_real_ip_from 172.16.0.0/12` and `127.0.0.1`, `real_ip_header X-Forwarded-For`,
  `real_ip_recursive on`. Only a peer on a docker bridge or loopback may name the client. That covers host-side
  cloudflared, which reaches the published port through the gateway. `X-Forwarded-Proto` is trusted only from
  those peers (`$fwd_proto`). HSTS (`max-age=15552000`) is sent only when that proto is https.
- **Login brake**: `limit_req_zone ... rate=10r/m`, `burst=10 nodelay` on `/app/v1/auth/login`, per real client IP.
  This is on top of the API's own limiter. It is an exact-match location, so `/app/v1/auth/key-login` (§4.16) gets
  it only if `web/nginx.conf` has the same block for that path; the API limiter applies either way.
- Body size: 16 KB for login, 25 MB for the rest of `/app/v1/` (calibration uploads).

### 4.3 Sessions and CSRF (`server/deps.py`, `server/routers/auth.py`, `server/main.py`)
- The session cookie `cs_session` holds `secrets.token_urlsafe(32)` and is `HttpOnly`, `SameSite=Lax`,
  `Path=/`. It is `Secure` when `X-Forwarded-Proto: https` (`COOKIE_SECURE=auto|1|0`). The DB stores only
  `sha256(token)`. A session expires after 7 days idle or 30 days total (`SESSION_IDLE_DAYS`,
  `SESSION_MAX_DAYS`). `last_seen` is written at most every 300 s, because `auth_request` hits it on every file.
- **CSRF, double submit plus session binding.** Every mutating request under `/app/v1` must send `X-CSRF-Token`
  equal to the `cs_csrf` cookie. The global middleware in `main.py` checks this. `require_role()` then checks the
  header against **the session's own** `csrf` column (`deps.check_csrf`). `/setup`, `/auth/login` and
  `/auth/logout` use `csrf_anonymous`, and so does `/auth/key-login` (§4.16).
- API responses carry `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` and `Referrer-Policy: same-origin`
  (middleware, repeated by nginx).
- Passwords are argon2id (`argon2-cffi` defaults) and are rehashed on login when the parameters change. An unknown
  username is verified against a dummy hash, so timing doesn't reveal which usernames exist.
- First run: `POST /setup` works only while `users` is empty. It needs the one-time code in
  `DATA/setup-code.txt` (mode 0600, format `xxxx-xxxx-xxxx`, compared with `consteq`, failures rate-limited).
  `SETUP_CODE_REQUIRED=0` disables the code. The file is deleted once a user exists (it is gone on production).

### 4.4 Roles (`server/config.py: ROLES`, `deps.require_role`)
- `viewer < scout < strategist < admin`. Every route depends on `require_role(...)`. The audit enumerated all 48
  routes then, and found no mutating route without the intended role. Account, settings, sources, layout,
  calibration and update mutations are admin-only. Role changes take effect on the user's next request.
- New accounts default to `viewer` (`users.py`, `role: Role = Field("viewer")`).
- Guards: the last active admin can't be demoted, disabled or deleted, and you can't delete yourself. Disabling an
  account or resetting its password deletes its sessions. Promoting someone to admin requires
  `pw_strength == 2`: a password of 10+ characters that isn't the username.
- Other users only ever see `{id, display_name}` (`deps.public_name`), never the username. For a passwordless
  account the username is the credential.

### 4.5 Passwordless sign-in policy
- Admins always need a password (`PASSWORD_MIN=10`, not equal to the username). For other accounts a password is
  optional (`MEMBER_PASSWORD_MIN=4` if set).
- Setting `passwordless_login`: `never` | `lan` | `everywhere`. The **code default is `never`** (`52e9da6`). The
  Admin → Activity → Security UI (`web/audit.js`) offers only "Off" (`never`) and "On (testing only)"
  (`everywhere`). `lan` is still accepted for older settings but isn't offered. **Production: `"never"`.**
- `via_public(request)` fails closed. Any Cloudflare/CDN header (`cf-connecting-ip`, `cf-ray`, `cdn-loop`,
  `cf-visitor`, `true-client-ip`, `forwarded`) counts as public. So does any address in X-Real-IP or
  X-Forwarded-For outside RFC 1918, loopback, `100.64/10` (Tailscale) or `fc00::/7`.
- A login without a password creates a `net_locked` session, and the policy is re-checked on **every** request
  (`load_session`). Switching to `never` also deletes every `net_locked` session.
- The refusal message is decided before the account is looked up, so real and made-up usernames get the same
  answer.
- **Doc drift:** `server/README.md` ("`lan` (default)") and the `SecurityOut` field description in
  `server/routers/admin.py` still say `lan` is the default. The code wins: the default is `never`.

### 4.6 Rate limits and write budgets (`server/security.py`)
- `RateLimiter` (login, connection-key login and setup): 10 failures per (IP, username) and 30 per IP per 15 min →
  429 with `Retry-After`. A success clears the (IP, username) pair. Connection-key sign-ins use the pseudo-username
  `#key` (10 wrong keys per IP) and count against the same per-IP 30 as password sign-ins. It is in memory, so the API must run as **one**
  process (`server/entrypoint.sh` starts a single uvicorn).
- `WriteLimiter`, per account (non-admins):

  | bucket | limit |
  |---|---|
  | `write` | 120/min (every mutating call) |
  | `note` | 10/min, 150/h |
  | `note_edit` | 20/min, 300/h |
  | `picklist` | 60/min, 1200/h |
  | `override` | 20/min, 200/h |
  | `profile` | 10/h |

  Update requests have their own limiter: 6/min, 60/h.
- At most 30 pending identity-fix requests per user (`MAX_PENDING_PER_USER`).

### 4.7 Lockdown, sign everyone out
- `PATCH /admin/security {"lockdown": true}` makes every non-admin write fail with 423. Changing your own
  password stays allowed (`LOCKDOWN_EXEMPT`). Update endpoints keep working for admins.
- `POST /admin/sessions/revoke {"include_admins": bool}` deletes all sessions except yours.

### 4.8 Audit log and revert (`server/audit.py`, `server/routers/admin.py`)
- Table `audit_log`, append-only through SQLite triggers (`audit_no_update`, `audit_no_delete` →
  `RAISE(ABORT)`). Each entry records time, user id, username, display name, role, IP, `via` (lan/public), user
  agent, action, event, target, and before/after snapshots.
- Every mutating route records an explicit entry inside its transaction. Routers without one (e.g.
  `routers/sources.py`) still get a generic `http.<method>` entry from the middleware (`_fallback_audit`). Failed
  logins are recorded as `auth.login_failed`, failed connection-key sign-ins as `auth.key_login_failed` (with a
  reason, never the key).
- Revertible families: `picklist`, `note`, `override`, `settings`, `events`. Not revertible: `settings.tba_key`
  and the override request, reject and withdraw actions. `POST /audit/{id}/revert` puts back the `before` state,
  which is logged as `<family>.revert` and can itself be reverted. `POST /audit/revert-user {user_id, since}`
  undoes everything that account did since a time, newest first.
- Picklists keep every version (`picklist_versions`), notes are soft-deleted or hidden, and scouts' identity fixes
  wait for approval.
- **Limit:** the triggers stop the API from rewriting history, but not anyone who can write `app.db` directly.
  That includes the worker, which runs as the same uid and shares `data/`. Treat the log as tamper-evident against
  web users only.

### 4.9 Input validation (`server/textrules.py`, pydantic models)
- Notes (`plain_text`, at most 4000 characters, at most 60 lines) reject HTML tags, character entities,
  `javascript:`/`vbscript:`/`data:`/`file:` schemes, control characters, and zero-width or bidi characters.
- Display names: 1–40 characters from `[\w .'()-]`, no staff words (admin, moderator, official, ...) for
  self-chosen names, and not look-alike (`skeleton()` folding) to another account's display name or username.
- `label()` checks short labels. `EVENT_RE`, `MATCH_RE`, `TEAM_RE` and `USERNAME_RE` (in `deps.py`) check every
  path or body identifier.
- Picklist body (`PicklistData`, `extra="forbid"`): `stars` and `dnp` are lists of `TEAM_RE` keys (at most 200).
  `weights` is a dict of `^[a-z][a-z0-9_]{0,23}$` → float in −10..10, NaN rejected, at most 40 keys.
- Most request models set `extra="forbid"`. Exception: `LoginIn` in `routers/auth.py` doesn't, so unknown login
  fields are ignored rather than rejected. `server/README.md` says every body rejects unknown fields; that is not
  quite true.
- Sources: `live/sources.py: classify_url()` (§4.11).

### 4.10 Browser side: CSP and escaping
- CSP for the app shell (nginx `$csp`): `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'
  https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:
  https://i.ytimg.com https://img.youtube.com; media-src 'self' blob:; connect-src 'self'; frame-src
  https://www.youtube.com https://www.youtube-nocookie.com; worker-src 'self' blob:; object-src 'none';
  base-uri 'self'; form-action 'self'; frame-ancestors 'self'`. Verified live on production.
  - **No inline script is allowed.** `index.html` loads only same-origin script files, and no `web/*.js` uses inline
    `on*=` handlers. Injected markup therefore can't run script, which makes this the main backstop against XSS.
    `'unsafe-inline'` styles still allow CSS injection (UI spoofing, not script).
- JSON and media responses use `$csp_data` (`default-src 'none'; media-src 'self'`).
- `esc()` (`web/app.js`) escapes `& < > " '`. External links go through `CS.safeHref()`, which accepts only
  `https?://` (`web/app.js`, `web/sources.js: urlHtml`).
- `renderTopNav()` builds nav hrefs only from event keys matching `^\d{4}[a-z0-9]{1,16}$`, from the hash or from
  `localStorage cs:lastev`, and runs every href through `esc()` (`83b16b7`).

### 4.11 Source URLs and subprocess argv (`live/sources.py`, `live/layout.py`, `live/livestream.py`, `live/liveingest.py`, `live/videos.py`)
- `classify_url()` accepts only these:
  - an absolute local path, which callers check with `file_allowed()` against `realpath` of `MEDIA`, `DATA` and
    `CS_SOURCE_ROOTS`;
  - a bare YouTube id;
  - an `http(s)` URL with a host. YouTube and Twitch URLs are **rebuilt** in canonical form; other URLs are
    returned re-assembled with `urlunsplit`;
  - a scheme-less YouTube or Twitch URL.

  It rejects a leading `-`, `//`, relative paths, other schemes (`file:`, `javascript:`, `data:`), userinfo,
  bad ports, control characters and spaces. `media_ok()` re-validates stored values, including sources.json files
  written before this fix and the `sample.video` field (`clean_sample`).
- Every yt-dlp call puts `--` before the URL: `layout.py:318,404`, `livestream.py:874`, `liveingest.py:362,515`,
  `sources.py:878`, `videos.py:32,43,100`. ffprobe and ffmpeg get `-i <url>` or absolute local paths. No call
  uses `shell=True` or `os.system`.
- `live/ocr.py` uses `pickle` only over pipes to its own child processes; no untrusted data is unpickled.

### 4.12 SSRF guard (`live/sources.py: safe_public_url`, `ip_blocked`)
- Before a URL reaches yt-dlp, ffmpeg or urllib, the code requires http(s) with a host and no credentials, and
  every resolved address must be public. Blocked: loopback, RFC 1918, link-local, CGNAT/Tailscale `100.64/10`,
  docker `172.16/12`, TEST-NETs, multicast, reserved, and the IPv6 equivalents (including IPv4-mapped, 6to4,
  Teredo and NAT64-embedded addresses). Anything `not ip.is_global` is also blocked.
- Called from `sources.add()` / `clean_sample()` (at save time, no cache), `layout.resolve_media`,
  `livestream` (including a urllib redirect handler that re-checks each `Location`), `liveingest.py:357` and
  `sources.py:876`.
- TBA webcasts are accepted only as YouTube or Twitch, rebuilt (`webcast_url`). TBA match video keys must match
  the YouTube-id pattern (`_yt_videos`).
- Escape hatch: `CS_URL_ALLOW`, comma-separated hosts or CIDRs (e.g. a LAN OBS HLS server).
- **Residual:** the check resolves the name, then yt-dlp or ffmpeg resolves it again, which allows DNS rebinding.
  Redirects followed *inside* yt-dlp or ffmpeg aren't re-checked. Both need an attacker-controlled public host
  and an admin adding it, or a TBA entry, which is now limited to YouTube/Twitch.

### 4.13 Secrets handling
- The TBA key is validated against TBA on `PUT /settings/tba-key` (422 if rejected, 502 if TBA is unreachable).
  It is stored in `settings`, and `server/workerconf.py` copies it to `data/config/worker.json` (0600) for the
  worker. Responses carry only `tba_key_set` and an admin-only `tba_key_hint` (`••••` + last 4). The audit log
  stores only the hint.
- On production `app.db` and `worker.json` are `-rw------- Grindlewalt` (verified).
- Since `83b16b7` compose no longer passes `TBA_KEY`/`EVENTS` to the api. They are imported once (`seed_from_env`)
  only if present in the api's environment. **Production `.env` has no `TBA_KEY`.** Note that the worker and
  recorder use `env_file: .env`, so anything placed in `.env` reaches them.
- No access log in uvicorn (`--no-access-log`). The API logs nothing secret.
- `.gitignore` excludes `.env`, `.roboflow_key`, `*.key`, `data/` and `.work/`. No secrets are tracked. **The repo
  is public** (`gh repo view`: PUBLIC), so anything committed is world-readable. That includes the
  `lab/api/2026wasam/dash/*.json` dashboard snapshot.

### 4.14 Containers (`deploy/docker-compose.yml`, Dockerfiles, entrypoints)

| service | user at runtime | caps (after `cap_drop: [ALL]`) | other |
|---|---|---|---|
| worker | uid/gid PUID:PGID via `setpriv --clear-groups --no-new-privs` | CHOWN, DAC_READ_SEARCH, SETUID, SETGID (root phase only; CapEff 0 after setpriv) | `no-new-privileges`, `pids_limit 4096`, `mem_limit 12g`, GPU `device_ids: ["${GPU_IDS:-0}"]` |
| recorder | same image and entrypoint (`--recorder`) | same | `mem_limit 8g` |
| api | PUID:PGID via setpriv | CHOWN, DAC_OVERRIDE, SETUID, SETGID | `read_only: true`, tmpfs `/tmp` 64 MB, `pids_limit 256`, `mem_limit 1g`, not published |
| web | nginx workers as `nginx` | CHOWN, NET_BIND_SERVICE, SETUID, SETGID | `read_only: true`, tmpfs for cache/run/tmp, `pids_limit 256`, `mem_limit 256m`, site and media mounted `:ro` |

Verified on production with `docker inspect`: web is `ReadonlyRootfs=true`, `CapDrop=[ALL]`, with the listed
CapAdd, `no-new-privileges`, PidsLimit 256 and 256 MB. Worker has the listed caps and `DeviceIDs ["0"]`.

- `live/entrypoint.sh` runs `chown -R -h -P "$PUID:$PGID" "$DATA"`, so symlinks are never followed. There is no
  runtime `pip install`.
- `server/entrypoint.sh` refuses to start if `$DATA` or `$DATA/config` is a symlink. It skips symlinked files and
  uses `chown -h` on a fixed list: `app.db*`, `setup-code.txt`, `config/worker.json`.
- yt-dlp: `Dockerfile` still installs an unpinned `yt-dlp[default]` in the big cached layer. That line is kept
  byte-identical so the 3 GB layer stays cached. The next layer then runs
  `pip install --require-hashes --no-deps -r live/requirements-ytdlp.txt`, which pins
  `yt-dlp[default,pin]==2026.8.19` and its dependencies with 435 sha256 hashes. The pinned install wins.
  **Residual:** the unpinned install still ran as root at the time that layer was built (build-time only).
- Base images are pinned by tag, not digest: `python:3.12-slim`, `nginx:1.29-alpine`, `denoland/deno:bin-2.5.6`.
  The worker's other pip packages are pinned by version, not hash. The api's `server/requirements.txt` is not
  hash-pinned.
- Untrusted media (YouTube video, live HLS) is decoded by ffmpeg, PyAV and OpenCV inside the worker and recorder.
  That is the most plausible code-execution path. It lands as uid 1000 with no caps, but with read/write access to
  all of `data/` and `MEDIA_DIR`, plus egress (§4.1).

### 4.15 Updater isolation (`deploy/updater/`, `server/routers/update.py`)
- No container gets the docker socket. The api only writes `data/config/update-request.json`:
  `{"v":1, "action": "check"|"apply"|"rollback", "nonce": "<32 hex>", "at", "by": {id, name}, "ytdlp": bool}`.
  It then reads `update-status.json` without following symlinks and passes on only known, type-checked fields.
- `cs-updater.py` runs on the host as the stack owner (systemd `.path`/`.timer`/`.service`; `NoNewPrivileges`,
  `ProtectSystem=full`, `PrivateTmp`, ...). It opens `data/` and `config/` with `O_NOFOLLOW|O_DIRECTORY`, opens the
  request with `O_NOFOLLOW|O_NONBLOCK`, and requires a regular file of at most 2048 bytes with exactly those keys,
  a 32-hex nonce not among the last 50 seen, and `at` within −15 min / +2 min. It writes the status by rename.
- Remote, branch, deploy key and limits come from `<stack>/updater/updater.conf`, which is outside `data/` and not
  mounted anywhere. A request can't choose code, commands or arguments.
- **Residual:** the worker and recorder run as the same uid and share `data/`, so a compromised worker can also
  write a valid request. That lets it trigger "update to `BRANCH` of `REMOTE`" or "rollback" (not arbitrary code).
  The deployed code is whatever is on `main` of the public GitHub repo, so **GitHub account security is
  deployment security** once the updater is installed.
- **Production: the updater is not installed.** No `cyber-scout*` systemd units and no `<stack>/updater/`
  directory exist, and `app/` is not a git checkout. Deployment is still by hand (see `deploy.md`).

### 4.16 Connection keys (`server/routers/users.py`, `/auth/key-login` in `server/routers/auth.py`)
Added after this review (2026-10-22). An admin makes a one-time key for an account
(`POST /app/v1/users/{uid}/connect-key`), hands it to the teammate, and their Cyber-Scout Client signs in with it
(`POST /app/v1/auth/key-login`). Details: `server.md` §3.11.
- **Strength.** 16 characters of Crockford base32 from `secrets` = 80 random bits, shown as `XXXX-XXXX-XXXX-XXXX`.
  Guessing is online only and limited to 10 wrong keys per IP and 30 failed sign-ins of any kind per IP per 15 min.
  There is at most one live key per account, so there is nothing realistic to guess.
- **Storage.** Only SHA-256 of the normalized key (`connect_keys.key_hash`). Unsalted is fine for an 80-bit random
  value, as for session tokens. The key appears once, in the `POST` answer (`Cache-Control: no-store`). It is never
  in the audit log (only `expires_at`), and `server/idempotency.py` never stores that answer for replay (the web app
  and the Client send an `Idempotency-Key` on writes). uvicorn has no access log.
- **Single use, short life.** Spent atomically (`BEGIN IMMEDIATE` plus `UPDATE ... WHERE used_at IS NULL`; a
  parallel second use gets 401). Expires after `hours` (1–720, default 168). A new key replaces the account's
  previous one. Revoked by `DELETE .../connect-key`, by disabling the account (and refused for a disabled account,
  409), and by deleting the account.
- **One answer.** A wrong, malformed, spent or expired key and a disabled account all get the same 401. The reason
  goes only into the admin-only audit log. A second use of a spent key is logged as `reason: "used"` against that
  account. That means the key reached two people (or one device retried), so check that teammate's sessions.
- **It is a full session.** The key is the credential, so the session is not `net_locked`. An account without a
  password that signs in with a key works through the tunnel even with passwordless sign-in `never`, until the
  session's own idle (7 d) / absolute (30 d) expiry. Lockdown doesn't block it (it doesn't block `/auth/login`
  either). Keys can be made for admin accounts too, and such a key signs in as that admin.
- **Residual.** Whoever holds an unused key can sign in as that account from anywhere until it expires. Send keys
  privately (not in a team channel), prefer short `hours`, and revoke unused ones (Admin → Teammates shows
  `connect_key_expires`). "Sign everyone out" (`/admin/sessions/revoke`) ends sessions but leaves unused keys. The
  nginx `limit_req` brake covers this path only if `web/nginx.conf` has a location for it (§4.2).

---

## 5. Findings and their status

Severity is the audit's overall rating. "Status" is what the current code (`cc6297c`) and production show.

| # | sev | finding | fix | status (verified) | residual |
|---|---|---|---|---|---|
| A1 | **High** | Stored XSS via picklist weights. `PUT /events/{ev}/picklists/{name}` (strategist+) stored any JSON, and `web/event.js` put `weights` values into `innerHTML` raw. Strategist → admin session. | `a94d6ee`, plus CSP in `83b16b7` | **Fixed.** `server/routers/scouting.py: PicklistData` (`extra="forbid"`, TEAM_RE lists, numeric weights −10..10). CSP `script-src 'self'` blocks inline handlers. | (a) The client-side `Number()` coercion was added in the cleanup pass (`web/event.js` `plWeight` / `plWeights`: known keys, `Number()` clamped 0–5, for both `value=""` and the shown number). (b) Picklists saved before `a94d6ee` were copied into `picklist_versions` by a `server/db.py` migration. `GET` and `.../restore` (`apply_picklist_state`) return or re-save stored blobs **without re-validating**. Both are low risk with the CSP in place. |
| A2 | Med | Un-normalised source URLs (admin): a leading `-` reached yt-dlp as an option; relative/`file:` paths skipped the root check; `javascript:` hrefs; `sample.video` unvalidated. | `74e84af` | **Fixed.** `classify_url`, `media_ok`, `clean_sample`; `--` in every yt-dlp call; `CS.safeHref` in `web/sources.js` (`urlHtml`). Covered by `tests/test_source_urls.py` (14 tests) and `server/tests/test_sources_api.py`. | — |
| A3 | Med | Worker ran `pip install -U yt-dlp[default]` as root on every start, unpinned. | `83b16b7` | **Fixed.** No pip in `live/entrypoint.sh`; `live/requirements-ytdlp.txt` hash-pinned. Deployed `entrypoint.sh` verified. | An unpinned yt-dlp in the cached build layer (build-time, overridden). Updating yt-dlp now means editing the pin and rebuilding the image, which is blocked by the full disk (§7). |
| A4 | Low | SSRF: TBA webcast/video values and admin URLs could make the worker fetch internal URLs (Portainer, restic, searxng on the gateway). | `74e84af` | **Fixed** for direct fetches: `safe_public_url`, YouTube/Twitch-only webcasts, YouTube-id check on TBA video keys. | DNS rebinding and in-tool redirects (§4.12). The network is still `internal=false`, so nothing stops code already running in the worker. |
| A5 | Low | `server/entrypoint.sh` chowned PUID-writable paths as root, following symlinks. | `83b16b7` | **Fixed.** Symlinked dirs refused, symlinked files skipped, `chown -h`. The worker uses `chown -R -h -P`. | — |
| V1 | needs validation | Cloudflare tunnel / reverse proxy publishing :7560. | — | **Still open (owner).** Local cloudflared configs (`~/.cloudflared/*.yml`: nas, glances, mc) and the caddy container's Caddyfile don't mention 7560. Three cloudflared processes run with **dashboard-managed ingress** (token, no local config), so they can't be checked from the host. The nginx and API code assume a tunnel might exist and fail closed for passwordless sign-in. | If a tunnel exists, the login page is on the internet, protected by passwords plus rate limits only. |
| V2 | needs validation | Hash-route segments interpolated unescaped into nav hrefs (DOM XSS). | `83b16b7` | **Fixed in the nav** (`renderTopNav`: EVK regex + `esc()`). | `route()` still passes the raw `p[0]` (event key) and later segments to page renderers. Several build `href="#/${ev}/..."` without `esc()`, e.g. `web/sources.js` tabs and links. Modern browsers percent-encode `"`, `<`, `>`, space and `` ` `` in `location.hash`, and the CSP blocks inline script, so this isn't practically exploitable. Validating `p[0]` once in `route()` would close it. |
| R1 | rejected | Non-admins reaching admin endpoints. | — | Still correct: every route has `require_role`. | — |
| R2 | rejected | `auth_request` bypass for `/api` and `/media`. | — | Still correct (§4.2). Production 401s verified. | — |
| R3 | rejected | TBA key readable through the API, `/api` or `/media`. | — | Still correct (§4.13). | — |

**Hardening items from REPORT.md:**

| item | status |
|---|---|
| Dedicated PUID instead of 1000 (the owner, in `docker`+`wheel`) | **Open.** Production `.env` has `PUID=1000`, and uid 1000 = Grindlewalt (groups docker, wheel, kvm, ...). |
| compose `no-new-privileges`, `cap_drop ALL`, `read_only`, pids/mem limits | **Done** (`83b16b7`, `74e84af`), verified on production. Worker and recorder are not `read_only` (they write caches in the image). |
| `setpriv --no-new-privs` | **Done** in both entrypoints. |
| Worker GPUs by `device_ids` | **Done** (`GPU_IDS`, default `0`). |
| Bind the port to a LAN or Tailscale IP | **Available** (`WEB_BIND`), **not used** on production. |
| Internal network / egress limits | **Open.** `cyber-scout_default` has `internal=false`. |
| Remove `TBA_KEY` from `.env` after import | **Done** on production. The env passthrough was removed from compose. |
| `install.sh` refuse uid 0, model checksum | **Open.** `install.sh` runs as root or as a user (it uses `sudo` for privileged steps when not root) and sets PUID from `SUDO_UID` / `id -u`. Run directly as root without sudo, PUID becomes 0. It doesn't refuse that. `CS_MODEL_URL` is downloaded with `curl` with **no checksum**. |
| Pin base images and deno by digest | **Open** (tags only). |
| CSP, no inline scripts | **Done.** |
| `/app/v1/docs` public with CDN Swagger → disable | **Done.** `docs_url=None`, `openapi_url=None`; `/app/v1/openapi.json` is admin-only. Production: `/app/v1/docs` 404, `/app/v1/openapi.json` 401. |
| Frame headers on `/app/v1/` | **Done** (`X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`). |
| Consider passwords for strategist+ | **Partly.** Passwordless defaults to `never` and production is `never`, so everyone needs a password today. Still, only admins are forced to use a 10+ character password. A strategist may set a 4-character one. |
| `_who()` returns display name only | **Done** (`public_name`). |
| nginx deny `*.db`; MEDIA_DIR never contains `data/` | **Done** (deny rule). MEDIA_DIR on production is `/mnt/nas/autoscout-media`, separate from `data/`. |
| `set_real_ip_from` behind a proxy | **Done**, with one caveat: `172.16.0.0/12` trusts **every** docker bridge peer, including the worker and recorder, which could spoof `X-Forwarded-For`. That only affects login rate-limit keys and audit IPs. `via_public` still fails closed. |

**New observations from this review (not in the audit):**

| # | sev | observation | where |
|---|---|---|---|
| N1 | Low | The design-lab nginx container `cyber-scout-lab` (not part of the compose stack; it mounts `/mnt/nas/autoscout-scratch/lab`) is published on `0.0.0.0:7570` **and IPv6**, with no auth. It serves a real 2026wasam dashboard snapshot (`/api/2026wasam/dash/index.json` → 200 from the LAN). The same data is in the public repo under `lab/api/`. | production `docker ps` |
| N2 | Info | Documentation says passwordless defaults to `lan`; the code default is `never`. | `server/README.md`, `server/routers/admin.py: SecurityOut` |
| N3 | Info | `LoginIn` has no `extra="forbid"`, contradicting "every request body rejects unknown fields". | `server/routers/auth.py` |
| N4 | Low | `manage.py reset-password` sets a new hash but doesn't update `pw_strength`. A passwordless account reset this way still shows "no password", and its sessions are not `net_locked`-aware. `make-admin` skips the admin-password-strength rule. | `server/manage.py` |
| N5 | Low | The audit log is append-only only against the API (§4.8). | `server/db.py` triggers |
| N6 | Low | A compromised worker can request updates or rollbacks (§4.15). | `deploy/updater/README.md` acknowledges this |
| N7 | Ops | Root disk 100% full (915 GB, 3.0 GB free) on the filesystem holding Docker's data root. Security rebuilds of the worker (~3.5 GB plus 1 GB margin) can't run. Even code-only rebuilds are close to the limit. | `df -h /` on atomosserver |

---

## 6. Owner actions still open

In priority order:

1. **Confirm the Cloudflare tunnels (V1).** In Cloudflare Zero Trust → Networks → Tunnels, list the public
   hostnames of every tunnel on atomosserver. Check whether any points at `localhost:7560`, `10.0.0.58:7560`,
   `atomosserver:7560` or the `cyber-scout-web` container.
   - If **none** does and the site should stay LAN/Tailscale-only, set `WEB_BIND=<tailscale or LAN IP>` in
     `.env` and recreate web (`docker compose up -d web`).
   - If **one** does (a `scout.*` hostname is implied by the nginx comment "https://scout..."), put **Cloudflare
     Access** in front of it: an Access application on that hostname with an allow policy for team emails (one-time
     PIN or Google). Unauthenticated internet traffic then never reaches the login page. Keep
     `passwordless_login = never`.
2. **Close the lab port (N1).** Stop `cyber-scout-lab`, or publish it on `127.0.0.1` / the Tailscale IP only.
3. **Free root-disk space (N7)** so security updates can be built: `docker image ls`, then remove dangling and old
   images by id (the updater doc warns against blanket `docker image prune` on a shared host). Also check the old
   `data/cache/*.mp4.part*` files, ~250 MB.
4. **Passwords.** Keep "No-password sign-in" **Off** (Admin → Activity → Security). Give every strategist a strong
   password: only admins are forced to 10+ characters. Production today has 1 admin (strong password),
   1 strategist (password set, teammate-grade), 1 viewer (no password: that viewer can't sign in at all while the
   policy is `never`).
5. **Review admin accounts** before each event (Admin → Teammates), plus `docker compose exec api python -m
   server.manage users`. Keep ≥ 2 admins only if both use strong passwords.
6. **Dedicated PUID.** Create a system user (e.g. `cyberscout`, not in `docker`/`wheel`), `chown -R` the stack's
   `data/` and `MEDIA_DIR` to it, and set `PUID`/`PGID` in `.env`. A container escape then lands on an
   unprivileged account rather than a docker-group (= root) one. The updater must still run as a docker-group
   user, so its units would need `User=` set separately.
7. **Updater deploy key**, when installing the updater. The repo is public, so `https://` needs no key. If it ever
   goes private, generate an ed25519 key with **write access off** (`deploy/updater/README.md`). Also turn on 2FA
   and branch protection for `main` on GitHub: whoever can push to `main` can get code onto the server through
   "Update now".
8. **Backups are secret.** `data/app.db` holds the TBA key and password hashes, and
   `updater/backups/app-*.db` will too. Keep them 0600 and out of shared NAS folders; `restic` repos should be
   encrypted.
9. **Egress (optional).** Put worker and recorder on a network that can't reach the docker gateway's admin ports
   (host firewall rules for `172.25.0.0/16` → 8000, 9443, 8200, 8201, 8080, 9898, 3001), or bind those host
   services to specific interfaces.
10. Small code follow-ups: re-validate picklist versions on restore and GET (the `Number()` coercion and a single
    hash-route check in `route()`, `routeOk`, were done in the cleanup pass); fix the README and `SecurityOut` doc drift; `pw_strength`
    in `manage.py`; a checksum for `CS_MODEL_URL`; image digests.

---

## 7. Incident response recipes

All from the stack folder on the server (`cd /opt/stacks/cyber-scout`; `/opt/cyber-scout` on a fresh install).

| situation | do this |
|---|---|
| Someone is vandalising picklists, notes or identity fixes | Admin → Activity → **Lockdown on** (or `PATCH /app/v1/admin/security {"lockdown": true}`). Every non-admin write then gets 423. Then find the account under Activity (filter by user, action prefix, `via=public`). |
| Undo their changes | Activity → **Undo everything by this user since …** (`POST /app/v1/audit/revert-user {"user_id": N, "since": <unix>}`), or revert single entries (`POST /app/v1/audit/{id}/revert`). Then disable the account (`PATCH /app/v1/users/{id} {"disabled": true}`), which also ends its sessions. |
| A login may have leaked | Admin → Security → **Sign everyone out** (`POST /app/v1/admin/sessions/revoke {"include_admins": true}`; your own session stays). Reset that user's password in Admin → Teammates. |
| Locked out / forgot the admin password | `docker compose exec api python -m server.manage users`, then `... server.manage reset-password <username>` (prints a new 20-character password and ends that user's sessions) or `... server.manage make-admin <username>` (also re-enables the account). |
| TBA key leaked | Revoke it on thebluealliance.com/account, create a new one, then Admin → Settings → TBA key (`PUT /app/v1/settings/tba-key {"key": "..."}`, validated against TBA). `worker.json` is rewritten automatically, and the worker picks it up on its next poll. |
| A worker or recorder container looks compromised | `docker compose stop worker recorder`. Check `docker compose logs worker recorder`. Diff `data/config/` and `data/events/*/sources.json` against backups, and look for an unexpected `data/config/update-request.json`. Rotate the TBA key. Then rebuild from known-good code (`docker compose build --no-cache worker && docker compose up -d`). |
| Suspect the site is publicly exposed | `docker ps` (the `0.0.0.0:7560` binding), the Cloudflare dashboard (tunnel public hostnames), and the Activity log filtered by `via=public`. Set `WEB_BIND` and/or add Cloudflare Access. |
| A bad update is deployed (once the updater is installed) | Admin → Updates → **Roll back**, or `python3 updater/cs-updater.py --stack $PWD rollback`. The database isn't restored automatically: copy from `updater/backups/` with the stack stopped. |

---

## 8. Where the security code lives

| file | what |
|---|---|
| `server/deps.py` | sessions, `require_role`, CSRF, `via_public` / trusted nets, passwordless policy, lockdown, identifier regexes, `public_name`, `rate_limit` |
| `server/security.py` | argon2id, tokens, `consteq`, connection-key format (`new_connect_key`, `normalize_connect_key`), `RateLimiter` (login), `WriteLimiter` (budgets), `atomic_write_json` |
| `server/routers/auth.py` | `/session`, `/setup` (setup code), `/auth/login` (passwordless check, net-locked sessions), `/auth/key-login` (connection keys), `/auth/password`, `/auth/profile`, `/auth/check` |
| `server/routers/users.py` | accounts and roles (last-admin guards), `/users/{uid}/connect-key` (make / revoke a connection key) |
| `server/routers/admin.py` | `/admin/security`, `/admin/sessions/revoke`, `/audit`, revert endpoints |
| `server/audit.py`, `server/db.py` | audit records and snapshots, append-only triggers, schema and migrations |
| `server/textrules.py` | plain text, display names, look-alike skeletons, labels |
| `server/routers/scouting.py` | `PicklistData` schema, notes, the identity-fix review queue |
| `server/routers/update.py`, `deploy/updater/cs-updater.py` | request/status file protocol |
| `server/main.py` | global CSRF middleware, security headers, fallback audit, docs off |
| `server/entrypoint.sh`, `live/entrypoint.sh` | root phase (`chown -h`), `setpriv --no-new-privs` |
| `web/nginx.conf` | auth_request gate, CSP and headers, realip, login `limit_req`, deny rules |
| `web/app.js` | `esc`, `CS.safeHref`, nav event-key validation |
| `live/sources.py` | `classify_url`, `media_ok`, `file_allowed`, `safe_public_url`, `ip_blocked`, `webcast_url` |
| `deploy/docker-compose.yml` | caps, read-only roots, limits, `WEB_BIND`, `GPU_IDS` |
| tests | `server/tests/test_hardening.py` (33 tests), `test_auth.py` (16), `test_connect_keys.py` (14), `test_roles.py` (5), `test_update.py` (19), `test_data.py` (7), `tests/test_source_urls.py` (14) |
