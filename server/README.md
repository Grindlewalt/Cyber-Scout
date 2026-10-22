# Cyber-Scout platform API (`server/`)

FastAPI + SQLite (`DATA/app.db`). Accounts, roles, team settings, the TBA key, followed events, and the shared
scouting data (picklists, notes, identity overrides). nginx (`web/nginx.conf`) serves it at **`/app/v1/`** and uses
`GET /app/v1/auth/check` (auth_request) to gate the site JSON (`/api/*`) and videos (`/media/*`).

Full machine-readable contract: `server/openapi.json` (regenerate after adding routes:
`DATA=$(mktemp -d) python -c "import json,server.main as m; json.dump(m.app.openapi(), open('server/openapi.json','w'), indent=1)"`),
or live at `/app/v1/openapi.json` (admins only; there is no Swagger UI).

## Auth model

* **Session cookie** `cs_session`: random 256-bit token, http-only, SameSite=Lax, Path=/, `Secure` when the request
  came over HTTPS (`X-Forwarded-Proto: https` from a TLS proxy; `COOKIE_SECURE=1|0` forces it). Only its SHA-256
  is stored. Expires after 7 days unused or 30 days total (`SESSION_IDLE_DAYS`, `SESSION_MAX_DAYS`).
* **CSRF**: every POST/PUT/PATCH/DELETE must send header `X-CSRF-Token` equal to the `cs_csrf` cookie (readable by
  page JS; also returned as `csrf` by `GET /session`). Signed in, it must also equal the session's own token.
  `CS.app()` in `web/setup.js` does this for you.
* **Roles** (ascending): `viewer` < `scout` < `strategist` < `admin`. 401 = not signed in, 403 = role too low / CSRF.
* **Passwords**: argon2id. Admins: min 10 chars (`PASSWORD_MIN`), not equal to the username. Everyone else:
  optional (blank = sign in with the username), min 4 chars if set (`MEMBER_PASSWORD_MIN`); promoting someone to
  admin needs an admin-grade password first (`users.pw_strength`). Login: generic error for bad
  user/password; rate limit 10 failures per (IP, username) and 30 per IP per 15 min -> 429 + `Retry-After`
  (IP = the real client: nginx's realip trusts X-Forwarded-For only from the docker gateway / loopback).
* **Passwordless sign-in** (admin setting `passwordless_login`): `lan` (default) = accounts without a password sign in
  only when the request comes straight from a private / Tailscale address; anything through the Cloudflare tunnel
  (Cf-* headers, or any public address in X-Real-IP / X-Forwarded-For: fails closed) gets 401 "A password is
  required ..." -- the same answer for real and made-up usernames. `never` / `everywhere` as named. Sessions made
  without a password are `net_locked` and stop working from the internet (and everywhere under `never`).
* **Troll-proofing**: every change is in the append-only `audit_log` (who, role, IP, lan/public, browser, before /
  after); admins list it and undo one change or everything an account did since a time. Picklists keep every
  version; notes are soft-deleted / hidden; scouts' identity fixes wait for approval (`scout_overrides=review`,
  default). Per-account write budgets (security.py `WriteLimiter`, 429). Lockdown: non-admin writes -> 423.
  Notes are plain text (no tags / entities / `javascript:`, no control / zero-width / bidi characters); display names
  1-40 chars, no staff words for self-chosen names, not look-alike to another account's name. Other users only
  ever see `{id, display_name}` of an account, never its username.
* **First run**: no users -> `GET /session` says `setup_required`; `POST /setup` creates the admin once, with the
  one-time code in `DATA/setup-code.txt` (0600; printed by `deploy/install.sh`; `SETUP_CODE_REQUIRED=0` disables).
* **Secrets**: the TBA key is validated against TBA, stored in the DB and in `DATA/config/worker.json` (0600) for the
  worker; responses only ever say `tba_key_set` + `tba_key_hint` ("••••abcd", admin only). TBA lookups the browser
  needs go through `/tba/*`. No access log (nginx logs requests); nothing secret is logged.

## Roles matrix

| | viewer | scout | strategist | admin |
|---|:-:|:-:|:-:|:-:|
| site JSON `/api/*`, videos `/media/*` | ✓ | ✓ | ✓ | ✓ |
| `GET /settings` (team, goal; no secrets), `GET /events` | ✓ | ✓ | ✓ | ✓ |
| read picklists, notes, overrides | ✓ | ✓ | ✓ | ✓ |
| change own password | ✓ | ✓ | ✓ | ✓ |
| add notes; edit/delete (soft) **own** notes; own display name | | ✓ | ✓ | ✓ |
| propose identity fixes (queued; applied directly when `scout_overrides=direct`) | | ✓ | ✓ | ✓ |
| identity fixes applied directly; approve / reject proposed ones | | | ✓ | ✓ |
| write/delete/restore picklists; edit / hide / restore **anyone's** notes | | | ✓ | ✓ |
| users + roles, TBA key, team/goal/options, followed events, `/tba/*` | | | | ✓ |
| audit log + undo, lockdown, sign-in policy, sign everyone out, OpenAPI schema | | | | ✓ |
| writes while lockdown is on | | | | ✓ |
| video sources / layout / calibration (agent B, `routers/sources.py`) | | | | ✓ (reads: per B) |

Guards: the last active admin can't be demoted, disabled or deleted; you can't delete yourself; disabling a user or
resetting their password ends their sessions; role changes apply on their next request.

## Endpoints (all under `/app/v1`)

| method | path | who | body / notes |
|---|---|---|---|
| GET | `/health` | anyone | `{ok, version}` |
| GET | `/session` | anyone | `{setup_required, setup_code_required, user: {id,username,display_name,role}\|null, csrf, configured, lockdown, network: lan\|public, passwordless_ok}` |
| POST | `/setup` | first run | `{setup_code, username, password, display_name}` -> session (signed in) |
| POST | `/auth/login` | anyone | `{username, password}` -> session; 401 / 429 |
| POST | `/auth/key-login` | anyone | `{key}` (a one-time connection key from an admin) -> session; spends the key; 401 / 429 |
| POST | `/auth/logout` | anyone | 204 |
| POST | `/auth/password` | viewer+ | `{current_password, new_password}` -> 204 (other sessions signed out) |
| PATCH | `/auth/profile` | scout+ | `{display_name}` (validated; 409 if too close to another account's name) |
| GET | `/auth/check` | (nginx) | 204 + `X-CS-Role` / 401 |
| GET/POST | `/users` | admin | create: `{username, display_name, password, role}` |
| PATCH/DELETE | `/users/{id}` | admin | `{display_name?, role?, disabled?, password?}` |
| POST/DELETE | `/users/{id}/connect-key` | admin | POST `{hours?: 1-720 = 168}` -> 201 `{key: "XXXX-XXXX-XXXX-XXXX", user_id, username, display_name, expires_at}` (shown once; replaces the account's previous key; 409 if disabled); DELETE -> 204 |
| GET/PATCH | `/settings` | viewer+ / admin | `{team_number, team_name, season_goal, tba_key_set, tba_key_hint*, options*}`; PATCH `{team_number?, season_goal?, options?: {poll_seconds, gpu_device, cpu_only, hubcam}}` (null = worker default from .env) |
| PUT/DELETE | `/settings/tba-key` | admin | `{key}`; 422 if TBA rejects it, 502 if TBA unreachable |
| GET | `/tba/team/{n}` | admin | TBA team (nickname, city, ...) |
| GET | `/tba/events?year=` | admin | `{year, team_number, team_events[], events[]}` (`key,name,start_date,end_date,city,state_prov,country,district,event_type`) |
| GET/POST | `/events` | viewer+ / admin | followed events `[{key,name,year,start_date,added_at}]`; POST `{key}` (checked on TBA) |
| DELETE | `/events/{ev}` | admin | stop following (data kept) |
| GET | `/events/{ev}/picklists[/{name}]` | viewer+ | `{event,name,data,version,updated_at,updated_by:{id,display_name}}` |
| GET | `/events/{ev}/picklists/{name}/versions[/{v}]` | viewer+ | history, newest first (also of deleted picklists); `/{v}` includes `data` |
| POST | `/events/{ev}/picklists/{name}/versions/{v}/restore` | strategist+ | saves version v as a new version |
| PUT/DELETE | `/events/{ev}/picklists/{name}` | strategist+ | `{data: {...}, base_version?}` -> 409 `{detail:{message,current}}` on a stale base_version. `web/event.js` uses `main` = `{stars:[teamkey], dnp:[teamkey], weights:{metric:w}}` |
| GET/POST | `/events/{ev}/notes[?team=frc4911&match=2026wasam_qm12&include_removed=1]` | viewer+ / scout+ | `{team?, match?, text<=4000 plain}` -> `{id,event,team,match,text,author,created_at,updated_at,edited_by}`; `include_removed`: strategist+ |
| PATCH/DELETE | `/notes/{id}` | author or strategist+ | `{text}`; DELETE = soft delete (author) or hide (strategist on someone else's) |
| POST | `/notes/{id}/restore` | strategist+, or author for own delete | |
| GET | `/events/{ev}/overrides` | viewer+ | `{event, overrides: <overrides.json>, log: [{match,data,by,requested_by,at}], pending: [{id,match,data,base,current,stale,by,at}], mode}` |
| PUT/DELETE | `/events/{ev}/overrides/{match}` | scout+ | `{red: {"<track idx>": "frc1983"}, blue: {...}}`; strategist+ (or `mode=direct`): 200, replaces that match's entry in `DATA/events/<ev>/overrides.json` (other entries kept; the worker re-solves identities on its next poll); scout in `review` mode: 202 `{status:"pending", request}` |
| POST | `/events/{ev}/override-requests/{id}/approve` | strategist+ | applies the requested entry |
| POST | `/events/{ev}/override-requests/{id}/reject` | strategist+ (or the requester: withdraw) | |
| GET/PATCH | `/admin/security` | admin | `{lockdown: bool, passwordless_login: never\|lan\|everywhere, scout_overrides: review\|direct}` |
| POST | `/admin/sessions/revoke` | admin | `{include_admins?}` -> `{signed_out}`; your session stays |
| GET | `/audit?user_id=&action=<prefix>&event=&since=&via=lan\|public&before_id=&limit=` | admin | `{entries:[{id,at,action,event,target,user,ip,via,ua,before,after,ref,revertible,reverted_by}], next_before_id}` |
| POST | `/audit/{id}/revert` | admin | puts the `before` state back (logged as `<family>.revert`, itself revertible); 409 if already undone / not undoable |
| POST | `/audit/revert-user` | admin | `{user_id, since}`: undo every undoable change of that account since `since`, newest first |

Errors are FastAPI-style `{"detail": "..."}` (422 validation errors: `detail` is a list). 423 = lockdown,
429 = rate limit (`Retry-After`). Every request body rejects unknown fields (422).

## Files the API writes for the worker

* `DATA/config/worker.json` (0600), rewritten on every settings/events change; `live/worker.py:load_config()` re-reads
  it every poll (falls back to `.env` for anything null):
  `{version, updated, tba_key, events: [..] | null, team_number, season_goal, options: {poll_seconds, gpu_device, cpu_only, hubcam}}`.
  `events` is null until events were managed in the app (then the worker uses `EVENTS` from `.env`).
* `DATA/events/<ev>/overrides.json` (format unchanged, see deploy/README.md).
* Upgrade path: on first start with an empty DB, `TBA_KEY` / `EVENTS` from the environment are imported once.

## Adding endpoints (agent B: `server/routers/sources.py`)

`routers/sources.py` is already mounted under `/app/v1` with `router = APIRouter(tags=["sources"])`. Use
`require_role("admin")` (also enforces CSRF), `valid_event`, `get_db` / `tx`, `add_schema("CREATE TABLE IF NOT EXISTS ...")`
for your tables, and `security.atomic_write_json()` for files under `cfg.DATA` (see `server/deps.py` docstring).
Record changes with `audit.record(db, request, user, "<family>.<verb>", target, event, before, after)` inside your
transaction (mutating routes without one still get a generic `http.<method>` entry from the middleware); show other
users as `deps.public_name(db, uid)` (display name only, never `user.username`); validate free text with
`textrules.plain_text()` / `textrules.label()`; use `deps.rate_limit(user, bucket)` for write-heavy routes.
The web helper is `CS.app(method, path, body)` (throws `Error` with `.status` / `.data`); role checks in the UI:
`CS.can("admin")`, nav entries `CS.nav({label, href, roles: ["admin"]})` or `{minRole: "strategist"}`.

## Running / testing

```bash
pip install -r server/requirements-dev.txt
python -m pytest server/tests            # auth, sessions, CSRF, rate limits, roles matrix, data, troll-proofing
DATA=/tmp/cs uvicorn server.main:app --port 8000   # one process (rate limiter is in memory)
```
Env: `DATA`, `PUID/PGID` (container), `SETUP_CODE_REQUIRED`, `SESSION_IDLE_DAYS`, `SESSION_MAX_DAYS`,
`COOKIE_SECURE` (auto|1|0), `PASSWORD_MIN`, `MEMBER_PASSWORD_MIN`, `TBA_BASE`, and legacy `TBA_KEY` / `EVENTS` (imported once).
