# Cyber-Scout platform API (`server/`)

FastAPI + SQLite (`DATA/app.db`). Accounts, roles, team settings, the TBA key, followed events, and the shared
scouting data (picklists, notes, identity overrides). nginx (`web/nginx.conf`) serves it at **`/app/v1/`** and uses
`GET /app/v1/auth/check` (auth_request) to gate the site JSON (`/api/*`) and videos (`/media/*`).

Full machine-readable contract: `server/openapi.json` (regenerate after adding routes:
`DATA=$(mktemp -d) python -c "import json,server.main as m; json.dump(m.app.openapi(), open('server/openapi.json','w'), indent=1)"`),
or live at `/app/v1/docs` and `/app/v1/openapi.json`.

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
  user/password; rate limit 10 failures per (IP, username) and 30 per IP per 15 min -> 429 + `Retry-After`.
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
| add notes; edit/delete **own** notes | | ✓ | ✓ | ✓ |
| identity overrides (writes `events/<ev>/overrides.json`) | | ✓ | ✓ | ✓ |
| write/delete picklists; edit/delete **anyone's** notes | | | ✓ | ✓ |
| users + roles, TBA key, team/goal/options, followed events, `/tba/*` | | | | ✓ |
| video sources / layout / calibration (agent B, `routers/sources.py`) | | | | ✓ (reads: per B) |

Guards: the last active admin can't be demoted, disabled or deleted; you can't delete yourself; disabling a user or
resetting their password ends their sessions; role changes apply on their next request.

## Endpoints (all under `/app/v1`)

| method | path | who | body / notes |
|---|---|---|---|
| GET | `/health` | anyone | `{ok, version}` |
| GET | `/session` | anyone | `{setup_required, setup_code_required, user: {id,username,display_name,role}\|null, csrf, configured}` |
| POST | `/setup` | first run | `{setup_code, username, password, display_name}` -> session (signed in) |
| POST | `/auth/login` | anyone | `{username, password}` -> session; 401 / 429 |
| POST | `/auth/logout` | anyone | 204 |
| POST | `/auth/password` | viewer+ | `{current_password, new_password}` -> 204 (other sessions signed out) |
| GET | `/auth/check` | (nginx) | 204 + `X-CS-Role` / 401 |
| GET/POST | `/users` | admin | create: `{username, display_name, password, role}` |
| PATCH/DELETE | `/users/{id}` | admin | `{display_name?, role?, disabled?, password?}` |
| GET/PATCH | `/settings` | viewer+ / admin | `{team_number, team_name, season_goal, tba_key_set, tba_key_hint*, options*}`; PATCH `{team_number?, season_goal?, options?: {poll_seconds, gpu_device, cpu_only, hubcam}}` (null = worker default from .env) |
| PUT/DELETE | `/settings/tba-key` | admin | `{key}`; 422 if TBA rejects it, 502 if TBA unreachable |
| GET | `/tba/team/{n}` | admin | TBA team (nickname, city, ...) |
| GET | `/tba/events?year=` | admin | `{year, team_number, team_events[], events[]}` (`key,name,start_date,end_date,city,state_prov,country,district,event_type`) |
| GET/POST | `/events` | viewer+ / admin | followed events `[{key,name,year,start_date,added_at}]`; POST `{key}` (checked on TBA) |
| DELETE | `/events/{ev}` | admin | stop following (data kept) |
| GET | `/events/{ev}/picklists[/{name}]` | viewer+ | `{event,name,data,version,updated_at,updated_by}` |
| PUT/DELETE | `/events/{ev}/picklists/{name}` | strategist+ | `{data: {...}, base_version?}` -> 409 `{detail:{message,current}}` on a stale base_version. `web/event.js` uses `main` = `{stars:[teamkey], dnp:[teamkey], weights:{metric:w}}` |
| GET/POST | `/events/{ev}/notes[?team=frc4911&match=2026wasam_qm12]` | viewer+ / scout+ | `{team?, match?, text<=4000}` -> `{id,event,team,match,text,author,created_at,updated_at}` |
| PATCH/DELETE | `/notes/{id}` | author or strategist+ | `{text}` |
| GET | `/events/{ev}/overrides` | viewer+ | `{event, overrides: <overrides.json>, log: [{match,data,by,at}]}` |
| PUT/DELETE | `/events/{ev}/overrides/{match}` | scout+ | `{red: {"<track idx>": "frc1983"}, blue: {...}}`; replaces that match's entry in `DATA/events/<ev>/overrides.json` (other entries kept); the worker re-solves identities on its next poll |

Errors are FastAPI-style `{"detail": "..."}` (422 validation errors: `detail` is a list).

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
The web helper is `CS.app(method, path, body)` (throws `Error` with `.status` / `.data`); role checks in the UI:
`CS.can("admin")`, nav entries `CS.nav({label, href, roles: ["admin"]})` or `{minRole: "strategist"}`.

## Running / testing

```bash
pip install -r server/requirements-dev.txt
python -m pytest server/tests            # 42 tests: auth, sessions, CSRF, rate limit, roles matrix, data
DATA=/tmp/cs uvicorn server.main:app --port 8000   # one process (rate limiter is in memory)
```
Env: `DATA`, `PUID/PGID` (container), `SETUP_CODE_REQUIRED`, `SESSION_IDLE_DAYS`, `SESSION_MAX_DAYS`,
`COOKIE_SECURE` (auto|1|0), `PASSWORD_MIN`, `MEMBER_PASSWORD_MIN`, `TBA_BASE`, and legacy `TBA_KEY` / `EVENTS` (imported once).
