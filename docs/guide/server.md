# Platform API (`server/`)

The `api` container: FastAPI + SQLite. It owns accounts, roles, sessions, team settings, the TBA key, followed
events, shared scouting data (picklists, notes, identity overrides), the audit log, the Video-page setup
(sources / layout / calibration jobs), the livestream status view, and the "Updates" request channel to the host
updater. It does **not** serve the site JSON or videos: nginx does, and asks this API whether the browser is
signed in (`auth_request`).

Everything below was read from the code at the commit named in `docs/guide/README.md`. Where
`server/README.md` or other docs disagree, the code is described and the disagreement is listed in
[Docs vs code](#docs-vs-code).

> **In progress at time of writing.** Another agent is working on competitions features that touch
> `server/routers/*` (and `web/app.js`, `live/worker.py`). At the time of writing `git status` showed no
> uncommitted changes under `server/`, so this page describes the committed routers. Expect new endpoints
> (for example event/competition management) to appear; regenerate `server/openapi.json` when they do.
>
> **UX fix wave (2026-10-19, `56fda7c..c64b813`).** Brought up to date for that wave: `/session.processing` +
> `DATA/worker-status.json`, the setup check order and weak admin passwords, TBA not-found sentences, the audit
> `changes` filter and `revert-user` `dry_run`, Video-page link rules (`GET sources/webcasts`, `probe_http`,
> `CS_PROBE_LINKS`). `server/openapi.json` was regenerated in that wave (`c777728`). The competitions and offline-sync
> routers (`events/search`, `/sync/*`) are still not described here; `docs/SYNC.md` covers sync.

---

## 1. Files

| file | purpose |
|---|---|
| `server/__init__.py` | package marker (docstring only) |
| `server/main.py` | `create_app()`: FastAPI app, the `guard` middleware (CSRF safety net, security headers, fallback audit), admin-only `/openapi.json`, `/health`, router mounting, `startup()` (DB init, `.env` seed, setup code, worker.json) |
| `server/config.py` | `Config` / `cfg` (env-derived settings, re-read via `cfg.configure()` in tests); constants `SESSION_COOKIE="cs_session"`, `CSRF_COOKIE="cs_csrf"`, `CSRF_HEADER="x-csrf-token"`, `PREFIX="/app/v1"`, `ROLES`, `RANK` |
| `server/db.py` | `CORE_SCHEMA` DDL, `MIGRATIONS` (add-column list), `add_schema()` hook, `connect()`, `init()`, `get_db()` dependency, `tx` (BEGIN IMMEDIATE context manager), `get_setting` / `set_setting` (JSON values), `user_count` |
| `server/deps.py` | `User` dataclass, `client_ip`, `via_public` (LAN vs public), `passwordless_policy`, `lockdown_state`, `public_name`, `rate_limit`, cookie helpers, `load_session`, `check_csrf`, `require_role(role)` dependency factory, `valid_event`, regexes `EVENT_RE`, `MATCH_RE`, `TEAM_RE`, `USERNAME_RE` |
| `server/security.py` | argon2id hashing (`hash_password`, `verify_password` with dummy-hash timing equaliser), `new_token`, `token_hash` (SHA-256), `consteq`, `new_connect_key` / `normalize_connect_key` (section 3.11), `RateLimiter` (login failures), `WriteLimiter` (per-account write budgets), `atomic_write_json` |
| `server/audit.py` | `record()` (append to `audit_log`), `family()`, `revertible()`, `REVERTIBLE` |
| `server/textrules.py` | free-text validation: `plain_text`, `display_name`, `skeleton` (look-alike folding), `has_reserved_word`, `label` |
| `server/workerconf.py` | `build()` / `write()` of `DATA/config/worker.json` (0600) for the worker and recorder |
| `server/workerstatus.py` | `processing_state(admin=)`: reads `DATA/worker-status.json` (written by the worker every 30 s) for `/session.processing`; `WHY` sentences per reason; `FRESH_S` 3600; `worker_down` (`DOWN_S` 300, `FIRST_S` 600 after api start) + the updater's container view for admins |
| `server/tba.py` | minimal TBA client with an in-memory TTL cache; `validate_key()` |
| `server/manage.py` | break-glass CLI: `users`, `reset-password`, `make-admin` |
| `server/routers/auth.py` | `/session`, `/setup`, `/auth/*` (incl. `/auth/key-login`), password/display-name policy helpers |
| `server/routers/users.py` | `/users` CRUD (admin), `/users/{uid}/connect-key` (one-time connection keys) |
| `server/routers/settings.py` | `/settings`, `/settings/tba-key`, `/tba/*` lookups; `apply_settings_state` (revert) |
| `server/routers/events.py` | `/events` follow / unfollow; `apply_events_state` (revert) |
| `server/routers/scouting.py` | picklists (+ version history), notes (soft delete / hide), identity overrides + approval queue |
| `server/routers/admin.py` | `/admin/security` (lockdown, passwordless, review mode), `/admin/sessions/revoke`, `/audit` list / revert |
| `server/routers/sources.py` | Video page: sources, layout boxes, calibration wizard, jobs, livestream status (logic in `live/sources.py`, `live/layout.py`) |
| `server/routers/update.py` | `/admin/update*` and `/admin/server-env`: writes `update-request.json`, parses `update-status.json`; `explain()` / `container()`: the updater's container states (`services`) with why/fix in plain words |
| `server/openapi.json` | committed snapshot of the schema (regenerated in the cleanup pass) |
| `server/Dockerfile` | `python:3.12-slim`; installs `requirements.txt`; copies `server/` plus `live/sources.py` and `live/layout.py`; deletes `server/tests`; HEALTHCHECK on `/app/v1/health` |
| `server/entrypoint.sh` | root phase (refuse symlinked `$DATA` / `$DATA/config`, `mkdir -p $DATA/config`, `chown -h` a fixed list of files) then `setpriv --reuid=$PUID --regid=$PGID --clear-groups --no-new-privs` uvicorn, 1 process, no access log, no server header |
| `server/requirements.txt` | pinned runtime: fastapi 0.143.0, starlette 1.7.0, pydantic 2.14.1, uvicorn 0.54.0, argon2-cffi 25.1.0 |
| `server/requirements-dev.txt` | runtime + httpx 0.28.1, pytest 9.1.1 |
| `server/tests/*` | pytest suite (section 12) |

There is no numpy / OpenCV in the API image. That matters for the Video page jobs (section 7.8).

---

## 2. Process model and wiring

```
browser ──HTTP──> web (nginx :80, published as WEB_BIND:WEB_PORT, default 0.0.0.0:7560)
                    │  /app/v1/*        proxy_pass http://api:8000   (Docker DNS, resolved per request)
                    │  /api/*, /media/* auth_request /_auth -> http://api:8000/app/v1/auth/check
                    ▼
                  api (uvicorn server.main:app, ONE process, port 8000, not published)
                    │  sqlite3  DATA/app.db (WAL)
                    │  files    DATA/config/worker.json, DATA/events/<ev>/{overrides,sources,layout,camera,...}.json,
                    │           DATA/jobs/sources/*.json, DATA/config/update-request.json; reads DATA/worker-status.json
                    ▼
                  worker / recorder / host updater read those files (no HTTP between services)
```

* **One uvicorn process** (`entrypoint.sh`, and the `main.py` docstring): both rate limiters
  (`login_limiter`, `write_limiter`, the update limiter) are in-process memory. Running more workers would
  multiply every limit and is unsupported. Limits reset on restart.
* **Connections**: `db.connect()` opens a new sqlite3 connection per request (`get_db` dependency), with
  `isolation_level=None` (autocommit unless a `tx` block is open), `foreign_keys=ON`, `busy_timeout=10000`,
  `timeout=10`. `tx(db)` = `BEGIN IMMEDIATE` ... `COMMIT` / `ROLLBACK` on exception.
* **Startup** (`main.startup()` via the FastAPI `lifespan`):
  1. `DB.init()`: `journal_mode=WAL`, run `CORE_SCHEMA` (all `CREATE ... IF NOT EXISTS`), apply `MIGRATIONS`
     (add missing columns), backfill `picklist_versions` from `picklists` (`INSERT OR IGNORE`), run any
     `add_schema()` DDL (none registered today), `chmod 0600 app.db`.
  2. `seed_from_env()`: once per DB (setting `seeded_from_env`), import `TBA_KEY` and `EVENTS` from the
     environment if the DB has none. See the compose caveat in Docs vs code: the shipped compose does not pass
     these variables to the api container.
  3. `auth.ensure_setup_code()`: with no users, create `DATA/setup-code.txt` (0600, format `xxxx-xxxx-xxxx`,
     hex) unless `SETUP_CODE_REQUIRED=0`; with users, delete the file.
  4. `workerconf.write()`: rewrite `DATA/config/worker.json`.
  5. If there are no users, log a warning pointing at first-run setup.
* **Router mounting** (`create_app`): `auth, users, settings, events, scouting, sources, admin, update`, each with
  `prefix="/app/v1"`. `users` adds `/users`, `events` adds `/events`; the rest declare full paths.
* **OpenAPI**: `FastAPI(openapi_url=None, docs_url=None, redoc_url=None)`. There is no Swagger UI and no public
  schema. `GET /app/v1/openapi.json` returns `app.openapi()` to admins only (`include_in_schema=False`).
* **Health**: `GET /app/v1/health` -> `{"ok": true, "version": "1.0.0"}`, no auth. Used by the Dockerfile
  HEALTHCHECK (`urllib` to `127.0.0.1:8000`, every 30 s) and by the host updater's post-update health check
  (through nginx).

### 2.1 The `guard` middleware (`main.py`)

One `@app.middleware("http")` function, applied to every request:

1. **CSRF safety net.** For any method other than GET/HEAD/OPTIONS on a path starting with `/app/v1`, the
   `X-CSRF-Token` header must equal the `cs_csrf` cookie (constant-time compare, both non-empty), else
   `403 {"detail": "missing or bad CSRF token ..."}` before routing. This is the double-submit check. Routes that
   use `require_role` additionally check the header against the *session's* own CSRF token (section 3.3).
2. Call the route.
3. **Headers** (set only if the route did not set them): `Cache-Control: no-store`,
   `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
   `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `Referrer-Policy: same-origin`.
   nginx strips and re-adds X-Frame-Options / CSP / Referrer-Policy for `/app/v1/` with the same values plus
   `Permissions-Policy` and HSTS (when the browser used HTTPS).
4. **Fallback audit.** For a mutating `/app/v1` request that returned < 400 and whose handler did not call
   `audit.record()` (it sets `request.state.audited = True`), it opens its own connection, finds the user
   (`request.state.user` set by `require_role`, else `load_session`) and records action
   `http.<method>` with `target` = the URL path (max 300 chars) and `after = {"status": <code>}`. Not
   revertible. In practice this covers every mutating route in `routers/sources.py`, a picklist DELETE of a
   picklist that doesn't exist, and anything new that forgets to audit. Errors in the fallback are logged and
   swallowed.

There is no request-body size limit in the API itself. nginx sets `client_max_body_size 16k` on
`/app/v1/auth/login` and `25m` on the rest of `/app/v1/`. Individual models cap their fields (notes 4000 chars,
picklist blob 256 KiB, etc.).

---

## 3. Authentication

### 3.1 Cookies

| cookie | value | flags | lifetime |
|---|---|---|---|
| `cs_session` | `secrets.token_urlsafe(32)` (256-bit). Only `sha256(token)` is stored (`sessions.token_hash`) | `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` per below | `Max-Age = SESSION_MAX_DAYS` (30 d) |
| `cs_csrf` | `secrets.token_urlsafe(24)`; when signed in, equal to `sessions.csrf` | **not** HttpOnly (page JS reads it), `SameSite=Lax`, `Path=/`, `Secure` per below | `Max-Age = SESSION_MAX_DAYS` |

`Secure` (`deps.cookie_secure`): `COOKIE_SECURE=1` always, `0` never, `auto` (default) when
`X-Forwarded-Proto` (first value) is `https`, else the request scheme. nginx passes `X-Forwarded-Proto` from its
`$fwd_proto` map, which trusts the incoming header only from the docker gateway / loopback (host-side
cloudflared); LAN browsers get `http`.

### 3.2 Sessions (`deps.load_session`)

* Lookup by `sha256(cs_session)`; cookie values longer than 200 chars are ignored.
* Invalid and **deleted** when: the user is disabled, `now > expires_at` (created + `SESSION_MAX_DAYS`,
  default 30 d), or `now - last_seen > SESSION_IDLE_DAYS` (default 7 d).
* **`net_locked`** sessions (signed in with an empty password) are rejected (not deleted) when the passwordless
  policy is `never`, or `lan` and the current request is public (section 3.5).
* `last_seen` is updated at most every 300 s (auth_request hits this for every `/api/*` and `/media/*` file).
* `start_session` (login, setup) first deletes all expired/idle sessions of everyone, then inserts the new row,
  sets `users.last_login`, and sets both cookies.
* Role changes take effect on the next request (the role is read from `users` at every lookup).
* Sessions are deleted on: logout (this one), password change (all others of that user), admin password reset or
  disable (all of that user), `manage.py reset-password`, `/admin/sessions/revoke`, policy switch to `never`
  (all `net_locked=1`), and account deletion (FK cascade).
* `start_session` is also called by `/auth/key-login` (section 3.11), always with `net_locked=0`.

### 3.3 CSRF

Two layers, both comparing with `secrets.compare_digest`:

1. Middleware (all mutating `/app/v1/*`): header `X-CSRF-Token` == cookie `cs_csrf`.
2. `deps.check_csrf` (inside `require_role` and `csrf_anonymous`): signed in -> header == `sessions.csrf`;
   anonymous (`/setup`, `/auth/login`, `/auth/key-login`, `/auth/logout`) -> header == cookie.

`GET /session` makes sure a usable `cs_csrf` cookie exists (re-sets it to the session token when signed in, or
mints one of >= 16 chars when anonymous) and also returns it as `csrf`. The web client (`CS.app()` in
`web/setup.js`) sends `X-CSRF-Token: <cs_csrf cookie || CS.session.csrf>` on every non-GET.

### 3.4 Roles and `require_role`

`ROLES = ("viewer", "scout", "strategist", "admin")`, ascending; `User.can(r)` is `RANK[self.role] >= RANK[r]`.

`require_role(role)` returns a dependency that, in order:

1. loads the session -> `401 "sign in required"` if none;
2. `check_csrf` (mutating methods) -> 403;
3. role check -> `403 "needs role <role> or higher"`;
4. for mutating methods by **non-admins**: lockdown -> `423` (exempt path: `/app/v1/auth/password`), then the
   `write` bucket of the write limiter (120 / 60 s) -> 429 with `Retry-After`;
5. stores the user in `request.state.user`.

Admins are never locked down and never count against the generic `write` bucket, but explicit buckets
(`picklist`, `note`, `override`, `profile`, the update limiter) apply to every role.

### 3.5 LAN vs public, and the passwordless policy

`deps.client_ip(request)` = `X-Real-IP` header (nginx sets it to `$remote_addr`, which its realip module has
already rewritten when the TCP peer is a trusted proxy) or the socket peer, max 64 chars.

`deps.via_public(request)` returns **True** (public) unless *every* hop is trusted. Concretely:

* any of `Cf-Connecting-Ip`, `Cf-Ray`, `Cdn-Loop`, `Cf-Visitor`, `True-Client-Ip`, `Forwarded` present -> public;
* else the chain `[client_ip] + X-Forwarded-For entries` must all parse as IPs inside
  `10/8, 172.16/12, 192.168/16, 127/8, 100.64/10 (Tailscale CGNAT), ::1, fc00::/7, fe80::/10` (IPv4-mapped IPv6
  is unwrapped); any public or unparseable entry -> public.

This "fails closed": Cloudflare tunnel traffic is public even if nginx's `set_real_ip_from` list is wrong. A LAN
client sending a fake private `X-Forwarded-For` stays LAN (tested), which is harmless.

The result is stored per audit entry as `via` (`lan` / `public`) and returned by `GET /session` as `network`.

**Passwordless policy** (setting `passwordless_login`, `deps.passwordless_policy`):

| value | meaning | offered in the UI (`web/audit.js`) |
|---|---|---|
| `never` | **default** (also for any missing/invalid stored value). Empty-password login is refused; existing `net_locked` sessions stop working (and are deleted when an admin switches to `never`) | "Off: every account needs a password" |
| `lan` | empty-password login only when `via_public()` is False; such sessions stop working from public addresses | not offered any more (still accepted by the API; the test fixture uses it) |
| `everywhere` | empty-password login from anywhere | "On (testing only)" |

Login with an empty password is refused **before** the account lookup (`401` with a policy-specific message), so
the answer is identical for real and made-up usernames. Accounts without a password store `hash_password("")`
and `pw_strength = 0`.

### 3.6 Passwords (`routers/auth.py`)

* argon2id via `argon2-cffi` defaults; `verify_password` verifies against a dummy hash when the user doesn't
  exist (equal timing) and returns a re-hash when parameters changed (stored on login).
* `check_password_policy(pw, username, role)`:
  * `admin`: >= `PASSWORD_MIN` (10) chars, not equal (case-insensitive) to the username, and not `too_simple`
    (3 or fewer distinct characters, or a substring of a keyboard / alphabet / digit run such as `qwertyuiop`,
    `1234567890`, either direction) -> 422 "that admin password is too easy to guess ...";
  * others: optional; if set, >= `MEMBER_PASSWORD_MIN` (4).
* `password_strength`: 0 = none, 1 = member-rule only, 2 = admin-grade. Stored in `users.pw_strength`.
  Promoting someone to admin requires strength 2 (or a new password in the same PATCH) -> otherwise 409.
* An account without a password must have a display name whose `skeleton` differs from its username (the username
  is the whole credential and display names are shown to everyone).

### 3.7 Login rate limits

`security.RateLimiter` (in memory, sliding 900 s window):

* <= 10 failures per `(ip, username.lower())` and <= 30 per `ip` -> `429` + `Retry-After` (seconds until the oldest
  failure leaves the window). Checked before verifying.
* A successful login clears that `(ip, username)` pair (not the IP counter).
* `/setup` uses pseudo-username `#setup`; only a wrong setup code counts as a failure.
* `/auth/key-login` uses pseudo-username `#key` (10 failed keys per IP) and shares the per-IP bucket (30) with
  password sign-ins; checked before the key is looked up. A success clears `(ip, "#key")`.
* A wrong `current_password` in `POST /auth/password` records a failure for `(ip, username)` but that endpoint
  doesn't itself check `blocked()`.
* nginx adds `limit_req zone=cs_login rate=10r/m burst=10 nodelay` per real client IP on `/app/v1/auth/login`
  (an exact-match location: `/app/v1/auth/key-login` is covered only if `web/nginx.conf` has the same block for it).

### 3.8 First run

* `GET /session` -> `setup_required: true` while `users` is empty; `setup_code_required` mirrors
  `SETUP_CODE_REQUIRED`.
* `POST /setup` creates the first account as **admin** (admin password rules; display name may use reserved
  words). Order of checks: 409 once any user exists, then the code (case-insensitive against
  `DATA/setup-code.txt`; 403 + limiter failure if wrong), and only then username / password / display name, so a
  wrong code is reported before anything else. It signs in, audits `auth.setup`, then deletes the code file.
* `configured` (in `/session`) = TBA key set AND >= 1 followed event AND setting `setup_complete` true. The wizard
  (`web/setup.js`) sets `setup_complete` via `PATCH /settings` at the end; values imported from `.env` don't count.

### 3.9 Lockdown

Setting `lockdown` = `{"on": true, "at": <ts>, "by": <uid>}` or absent. While on, every mutating call through
`require_role` by a non-admin gets `423 "the site is read-only right now ..."`, except
`POST /auth/password`. Anonymous endpoints (login, key login, logout, setup) are unaffected. Reads continue. Shown to the UI
as `/session.lockdown`.

### 3.10 What nginx's gate checks

`GET /app/v1/auth/check`: 204 if `load_session` finds a valid session (any role, viewer+), else 401. It sets
response header `X-CS-Role: <role>`, but nginx's `/_auth` location does not read it (`auth_request_set` is not
used), so `/api/*` and `/media/*` are gated on "signed in", not on role. nginx forwards `X-Real-IP`,
`X-Forwarded-For` and `X-Forwarded-Proto` to `/_auth` so `net_locked` sessions are judged by the real network.
A 401 becomes `{"detail":"sign in required"}` from `@unauthorized`; an unreachable API becomes 503 from
`@api_down`.

### 3.11 Connection keys (one-time sign-in)

An admin hands a teammate one string that connects their Cyber-Scout Client and signs it in as that account, once.

* **Format** (`security.new_connect_key`): 16 characters from the Crockford base32 alphabet
  `0123456789ABCDEFGHJKMNPQRSTVWXYZ` via `secrets.choice` (80 random bits), shown as `XXXX-XXXX-XXXX-XXXX`.
* **Input** (`security.normalize_connect_key`): upper-cased, whitespace and `-` dropped, `O` -> `0`, `I` and
  `L` -> `1`; anything that isn't then 16 characters of the alphabet is invalid (the same 401 as a wrong key).
* **Storage**: table `connect_keys` holds `token_hash(<16 normalized chars>)` (SHA-256, like sessions), never the
  key. `user_id` is UNIQUE: making a key deletes that account's previous one (used or not). The key is in the
  `POST` response only; `idempotency.py` passes `/users/<id>/connect-key` and `/auth/key-login` through without
  storing the answer, so a retried request with the same `Idempotency-Key` makes a new key instead of replaying the
  old one from the DB.
* **Single use**: `/auth/key-login` runs in `BEGIN IMMEDIATE` and spends the key with
  `UPDATE ... SET used_at=? WHERE key_hash=? AND used_at IS NULL` (rowcount must be 1), so of two parallel uses only
  one signs in. A used row stays until its expiry so a second use is audited as `reason: "used"` against that
  account; expired rows are deleted on every key sign-in, key creation and revoke.
* **Expiry**: `hours` 1..720 (default 168 = 7 days) from creation, if unused.
* **The session** is a normal one (`start_session`, `net_locked=0`): the key is the credential, so it works for an
  account without a password, from the tunnel, and under passwordless policy `never`. It then follows the usual
  idle / absolute expiry. Lockdown doesn't stop it (as for `/auth/login`).
* **Refusals**: wrong, malformed, used or expired key, or a disabled account -> one answer, `401 "that connection
  key isn't valid (it works once and expires; ask your admin for a new one)"`, a limiter failure and an
  `auth.key_login_failed` entry with `after.reason` (`malformed` / `unknown` / `used` / `expired` / `disabled`).
* **Revoked** by `DELETE /users/{uid}/connect-key`, by a new key for the same account, by disabling the account
  (`PATCH /users/{uid} {"disabled": true}`; the `users.update` entry then has `after.connect_key_revoked`), and by
  deleting the account (FK cascade). `/admin/sessions/revoke` ends sessions only; unused keys stay.
* `UserOut.connect_key_expires` = expiry of the account's unused, unexpired key, else null.

---

## 4. Write budgets (`security.WriteLimiter`)

Per account, in memory, recorded only when allowed. A bucket with several windows must satisfy all of them.

| bucket | limits | applied by |
|---|---|---|
| `write` | 120 / 60 s | `require_role` on every mutating call by a non-admin |
| `note` | 10 / 60 s, 150 / h | `POST /events/{ev}/notes` |
| `note_edit` | 20 / 60 s, 300 / h | note PATCH / DELETE / restore |
| `picklist` | 60 / 60 s, 1200 / h | picklist PUT / DELETE / version restore |
| `override` | 20 / 60 s, 200 / h | override PUT / DELETE (`_submit`), approve |
| `profile` | 10 / h | `PATCH /auth/profile` |
| `update` (separate `WriteLimiter` instance in `routers/update.py`) | 6 / 60 s, 60 / h | `/admin/update/{check,apply,rollback}` |

Over budget -> `429 "slow down: ..."` with `Retry-After`. Additionally a scout may have at most 30 pending
identity-fix requests (`MAX_PENDING_PER_USER`) -> 429.

---

## 5. Text validation (`server/textrules.py`)

All user text is stored as plain text and the server refuses markup, so one missed `esc()` in the web client
can't become stored XSS.

* **`plain_text(s, max_len, multiline=True, what)`** (notes): NFC-normalise, CRLF/CR -> LF, trim; refuse empty,
  too long, control / format / surrogate / private-use / unassigned / line- and paragraph-separator characters
  (Unicode categories Cc, Cf, Cs, Co, Cn, Zl, Zp; tab and newline allowed when multiline), an explicit list of
  invisible characters (zero-width, bidi overrides, Hangul fillers, variation selectors, BOM, ...); refuse
  anything that would be an HTML tag/comment/doctype (`<` followed by a letter, `/`, `!`, `?`), any HTML entity
  (`&name;`, `&#..;`), and `javascript:` / `vbscript:` / `data:` / `file:` schemes. `<3` and `a < b` pass.
  Multi-line: 3+ consecutive newlines collapse to 2; max 60 lines. Raises `TextError` (mapped to 422).
* **`display_name(s, allow_reserved)`**: NFC, no invisible/control chars, whitespace collapsed, 1-40 chars,
  only `\w`, space, `. ' ( ) -`; must contain a letter/digit. Unless `allow_reserved` (admins naming someone
  from the Admin page, and the first admin at setup), refuse names containing a reserved word: `admin`,
  `administrator`, `sysadmin`, `moderator`, `staff`, `system`, `root`, `owner`, `official`, `cyberscout`,
  `security`, `server`, `mentor`, `coach`, `teacher`, `support`, `superuser` (whole name, any word, or a
  reserved word of >= 5 chars glued to the start/end).
* **`skeleton(s)`**: NFKD, strip accents, casefold, map Cyrillic/Greek/leet look-alikes to Latin
  (`0->o, 1->l, 3->e, 4->a, 5->s, @->a, i->l, ...`), `rn->m`, `vv->w`, drop non-word characters. Used for the
  reserved-word check and for `auth.check_display_name`, which refuses (409) a display name whose skeleton equals
  any other account's display name or username.
* **`label(s, max_len, what)`** (season goal): single line, allow-list `\w . , ( ) / + # ! : -` and space,
  no schemes.

Other validators (pydantic / regex):

| what | rule |
|---|---|
| username (`USERNAME_RE`) | `^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$`; unique, case-insensitive (`COLLATE NOCASE`) |
| event key (`EVENT_RE`, `valid_event`) | `^\d{4}[a-z0-9]{1,16}$` -> 422 |
| match key (`MATCH_RE`) | `^(<event>)_(qm\|ef\|qf\|sf\|f)\d{1,3}(m\d{1,2})?$`, event part must equal the path event |
| team key (`TEAM_RE`) | `^frc\d{1,5}[A-Z]?$` |
| picklist name | `^[A-Za-z0-9 _.-]{1,40}$` |
| picklist data (`PicklistData`, `extra="forbid"`) | `stars`, `dnp`: lists of team keys, <= 200 each; `weights`: <= 40 entries, key `^[a-z][a-z0-9_]{0,23}$`, value float in -10..10 and not NaN; serialized blob <= 256 KiB (413) |
| override entry | `{"red"\|"blue": {"<idx 0-99>": "frcNNNN"}}`, <= 12 per alliance |
| TBA key | 20-128 chars, `[A-Za-z0-9]` (whitespace trimmed) |
| settings | `team_number` 1..99999; `season_goal` <= 40 via `label`; `options.poll_seconds` 30..3600, `gpu_device` 0..15, `cpu_only`, `hubcam` booleans |

---

## 6. Audit log

Table `audit_log` (append-only: triggers `audit_no_update` / `audit_no_delete` raise on UPDATE / DELETE).

`audit.record(db, request, user, action, target, event, before, after, ref, actor=None)` stores: time, the
actor's id / username / display_name / role (copied, no FK, so entries outlive accounts), `ip` (`client_ip`),
`via` (`lan` / `public`), user agent (300 chars), `action` (`family.verb`), `event`, `target`, `before` / `after`
(compact JSON state snapshots), and `ref` (for `*.revert`, the id of the entry undone). Routers call it inside
their transaction; it sets `request.state.audited`.

Secrets are never recorded: password changes record only `has_password` / `password_reset`; TBA key changes
record `{set, hint: "••••abcd"}`; connection keys only their expiry.

### 6.1 Actions

| family | actions | target | snapshot | revertible |
|---|---|---|---|---|
| `auth` | `setup`, `login`, `login_failed` (actor from the looked-up row; `after = {reason, known_user}`), `logout`, `password`; connection keys: `key_created` (`after = {expires_at}`, `before = {expires_at}` of a key it replaced), `key_revoked` (`before = {expires_at}`; only when an unused key existed), `key_login`, `key_login_failed` (actor = the key's account when the key was found; `after = {reason}`) | `user:<id>` (none for an unknown key) | never the key | no |
| `users` | `create`, `update`, `delete`, `profile` | `user:<id>` | `{username, display_name, role, disabled, has_password}` | no |
| `settings` | `update`, `security`, `tba_key`, `revert` | comma-joined keys | `{key: value}` | yes, except `settings.tba_key` |
| `events` | `follow`, `unfollow`, `revert` | event key | `{"row": {...}}` or null | yes |
| `picklist` | `save`, `delete`, `restore`, `revert` | `<ev>/<name>` | `{"version": n}` or null | yes |
| `note` | `create`, `edit`, `delete`, `hide`, `restore`, `revert` | note id | `{text, removed_kind, removed_at, removed_by}` or null | yes |
| `override` | `set`, `clear`, `approve`, `revert`; `request`, `reject`, `withdraw` | `<ev>/<match>` | `{"entry": {...}}` | yes, except `request` / `reject` / `withdraw` |
| `sessions` | `revoke_all` | `sessions` | `{signed_out, include_admins}` | no |
| `update` | `check`, `apply`, `rollback` | `app` | before `{commit}`, after `{nonce, ytdlp}` | no |
| `http` | `post`, `put`, `patch`, `delete` (middleware fallback) | URL path | `{status}` | no |

### 6.2 Revert semantics (`routers/admin.py`)

`POST /audit/{id}/revert` puts the entry's **`before`** state back and records `<family>.revert` with
`ref = id` (itself revertible, so a revert can be undone). Refused with 409 if the action isn't revertible or
the entry already has a revert (`SELECT 1 FROM audit_log WHERE ref=id`).

| family | how `before` is applied |
|---|---|
| picklist | `{"version": n}` -> that version's data saved as a **new** version (note `restored vN`); `null` -> picklist row deleted (history kept) |
| note | snapshot -> text and removed fields restored; `null` (the note didn't exist) -> soft-removed with `removed_kind='reverted'` |
| override | `{"entry": {...}}` written back to `overrides.json` (`{}` removes the match), also logged in `override_log` |
| settings | each key in the snapshot that is in `REVERTIBLE_KEYS` (`team_number, team_name, season_goal, setup_complete, options, lockdown, passwordless_login, scout_overrides`) set back; worker.json rewritten |
| events | follow (upsert row) or unfollow to match; `events_configured` set; worker.json rewritten |

`POST /audit/revert-user {user_id, since}` walks that account's entries with `at >= since`, newest first (max
2000), reverts each revertible, not-yet-reverted one, and returns `{reverted: [ids], skipped: [ids], dry_run}`
(skipped = not revertible, already reverted, or the revert raised an HTTP error). One transaction for all. With
`dry_run: true` nothing changes: `reverted` lists what would be undone (the Activity page shows that count and asks
for the username before the real call).

Not undoable from the log: account changes, sign-ins, the TBA key, sources / layout / calibration edits (only
`http.*` entries; `calibration/accept` keeps a `.bak-<ts>` copy of the replaced camera file), and updates.

---

## 7. Endpoints

All paths are under **`/app/v1`**. "CSRF" = mutating, so both CSRF layers apply. "Lockdown/write" = goes through
`require_role` so non-admins get 423 under lockdown and count against the `write` bucket. Errors are
`{"detail": "..."}` (422 validation errors: `detail` is a list, or a string for hand-raised 422s; picklist 409 has
`detail = {message, current}`).

### 7.1 Meta (`main.py`)

| method | path | role | what |
|---|---|---|---|
| GET | `/health` | none | `{ok: true, version: "1.0.0"}` |
| GET | `/openapi.json` | admin | full schema (not itself in the schema) |

### 7.2 Auth (`routers/auth.py`)

| method | path | role | CSRF | limits | audit | what |
|---|---|---|---|---|---|---|
| GET | `/session` | anyone | | | | always 200: `{setup_required, setup_code_required, user: {id, username, display_name, role} \| null, csrf, configured, lockdown, network: lan\|public, passwordless_ok, processing}`; ensures the `cs_csrf` cookie. `processing` is null when signed out (below) |
| POST | `/setup` | anyone, only while no users exist | cookie | login limiter key `#setup` | `auth.setup` | body `SetupIn {setup_code?, username, password, display_name}` (`extra=forbid`). Creates the admin, signs in, deletes `setup-code.txt`. 403 wrong code, 409 already set up |
| POST | `/auth/login` | anyone | cookie | login limiter; nginx 10 r/min | `auth.login` (`after.password` bool) / `auth.login_failed` | body `{username (1-64), password ("" allowed)}`. Generic `401 "invalid username or password"` for unknown user, wrong password or disabled account. Empty password: policy check first (section 3.5). Session is `net_locked` when the password was empty |
| POST | `/auth/key-login` | anyone | cookie | login limiter key `#key` (+ per IP) | `auth.key_login` / `auth.key_login_failed` | body `KeyLoginIn {key (1-64)}` (`extra=forbid`); returns `SessionOut` and sets the session cookie like `/auth/login`. Spends the key (section 3.11); one generic 401 for a wrong / malformed / used / expired key or a disabled account; 429 + `Retry-After` when rate-limited. Session is never `net_locked` |
| POST | `/auth/logout` | anyone | cookie | | `auth.logout` if a valid session | deletes this session row, clears `cs_session`, sets a fresh `cs_csrf`; 204 |
| POST | `/auth/password` | viewer+ (exempt from lockdown) | session | `write` (non-admin) | `auth.password` | `{current_password, new_password}`; 403 if current is wrong; applies the policy for the user's role; deletes the user's other sessions; this session's `net_locked` = new password empty; 204 |
| PATCH | `/auth/profile` | scout+ | session | `write`, `profile` | `users.profile` | `{display_name}` (2-64 in, then `display_name` rules without reserved words, 409 if look-alike to another account); returns `SessionOut` |
| GET | `/auth/check` | viewer+ | | | | nginx `auth_request`: 204 + `X-CS-Role`, or 401 |

**Processing state** (`/session.processing`, signed in only; `server/workerstatus.py`). The worker writes
`DATA/worker-status.json` = `{at, paused, reason}` at the top of every loop pass (`live/worker.py:status_beat`;
every 30 s while it waits, once per pass while it works). The API turns it into `{paused: bool|null, worker_seen:
unix|null, reason: "paused"|"no_key"|"no_events"|null, why: sentence|null, worker_down}`. Missing / unreadable file (no worker,
or one older than this change) -> all null ("unknown"). `worker_down` (null while it checks in) = `{never, since,
quiet_s}` after `DOWN_S` (5 min) without a write, or `FIRST_S` (10 min) after the api started with no file at all;
admins also get `docker` = `routers/update.container("worker")`: the host updater's latest view of that container
(`state`, `error`, `exit_code`, `oom`, `restarts`, `log`, `problem: {why, fix}` from `update.explain()`, the same cases
`install.sh` explains), or null without a fresh updater view. A file older than `FRESH_S` (1 h) keeps `worker_seen` but
`paused` / `reason` become null, so a stopped worker never reads as "paused". `PAUSE_PROCESSING` semantics are
unchanged; this is reporting only. The web app polls `/session` every 60 s for it (`web.md` §4.7).

### 7.3 Users (`routers/users.py`, admin only)

| method | path | audit | what |
|---|---|---|---|
| GET | `/users` | | list `{id, username, display_name, role, disabled, created_at, last_login, active_sessions, has_password, connect_key_expires}` ordered by username (`connect_key_expires`: expiry of an unused connection key, else null) |
| POST | `/users` | `users.create` | `{username, display_name="", password="", role="viewer"}` (`extra=forbid`); default role is **viewer**; 409 username taken; admins need an admin-grade password; reserved words allowed in names set by an admin |
| PATCH | `/users/{uid}` | `users.update` | `{display_name?, role?, disabled?, password?}`; `password=""` removes it (non-admins only). Guards: 409 if it would leave no active admin; 409 when promoting to admin without an admin-grade password; disabling or resetting the password deletes the user's sessions; disabling also deletes the connection key |
| DELETE | `/users/{uid}` | `users.delete` | 409 for yourself or the last active admin; sessions and connection key cascade; their notes/picklists keep `author_id`/`updated_by` NULL (FK `SET NULL`) |
| POST | `/users/{uid}/connect-key` | `auth.key_created` | body `{hours: 1..720 = 168}` (optional, `extra=forbid`) -> **201** `{key: "XXXX-XXXX-XXXX-XXXX", user_id, username, display_name, expires_at}`. Replaces the account's previous key. 404 unknown user, 409 disabled account ("enable the account first"). The key is shown only here (section 3.11) |
| DELETE | `/users/{uid}/connect-key` | `auth.key_revoked` (only if an unused key existed) | 204, also when there was none (then no audit entry at all); 404 unknown user |

### 7.4 Settings and TBA lookups (`routers/settings.py`)

| method | path | role | audit | what |
|---|---|---|---|---|
| GET | `/settings` | viewer+ | | `{team_number, team_name, season_goal, tba_key_set, tba_key_hint (admin only, "••••" + last 4), setup_complete, options (admin only)}` |
| PATCH | `/settings` | admin | `settings.update` | `{team_number?, season_goal?, options?: {poll_seconds, gpu_device, cpu_only, hubcam}, setup_complete?}` (`extra=forbid`). A new `team_number` is looked up on TBA (`/team/frcN/simple`, cached 24 h) to store `team_name`. `options` is merged key by key; `null` means "use the worker's `.env` default". Rewrites worker.json |
| PUT | `/settings/tba-key` | admin | `settings.tba_key` | `{key}`; validated with `GET /status?cs=<random>` (cache-busting, TTL 0). TBA 401/403 -> 422; unreachable -> 502 (not saved). Rewrites worker.json |
| DELETE | `/settings/tba-key` | admin | `settings.tba_key` | removes the key; rewrites worker.json |
| GET | `/tba/team/{number}` | admin | | `{key, team_number, nickname, name, city, state_prov, country}` (cached 24 h) |
| GET | `/tba/events?year=` | admin | | `{year (default: this year), team_number, team_events[], events[]}`; each `{key, name, event_type, start_date, end_date, city, state_prov, country, district (abbreviation), year}`; `team_events` failures are swallowed |

TBA errors via `tba_call(key, path, ttl, not_found=None)`: stored key rejected -> 409 "set a new one"; 404 -> 404
with the caller's `not_found` sentence (team lookups: "Team 99998 isn't on The Blue Alliance: check the number";
`POST /events`: "No event 2026zzzz on The Blue Alliance: copy the key from the end of its TBA link ..."), else "The
Blue Alliance has no such team or event"; other -> 502. No key -> 409 "set the TBA key first". `server/tba.py` caches per `(last 8 chars of key, path)` in memory (default TTL 600 s,
max ~500 entries) and sends `X-TBA-Auth-Key` + `User-Agent: cyber-scout`, 12 s timeout.

### 7.5 Followed events (`routers/events.py`)

| method | path | role | audit | what |
|---|---|---|---|---|
| GET | `/events` | viewer+ | | `[{key, name, year, start_date, added_at}]` ordered by start date; a missing name is filled from `DATA/site/api/events.json` |
| POST | `/events` | admin | `events.follow` | `{key}`; lower-cased, `EVENT_RE`, looked up on TBA (`/event/{ev}/simple`, 1 h cache; 404 if unknown); upsert; sets `events_configured=true`; rewrites worker.json |
| DELETE | `/events/{ev}` | admin | `events.unfollow` | 404 if not followed; data on disk is kept |

Until any event is managed in the app (`events_configured`), worker.json has `"events": null` and the worker uses
`EVENTS` from its `.env`.

### 7.6 Scouting data (`routers/scouting.py`)

**Picklists.** Current state in `picklists`, every version in `picklist_versions`. Version numbers keep counting
across deletes (`max(current, max(history)) + 1`). `web/event.js` uses the name `main`.

| method | path | role | limits | audit | what |
|---|---|---|---|---|---|
| GET | `/events/{ev}/picklists` | viewer+ | | | all current picklists `{event, name, data, version, updated_at, updated_by: {id, display_name}}` |
| GET | `/events/{ev}/picklists/{name}` | viewer+ | | | one; 404 if none |
| PUT | `/events/{ev}/picklists/{name}` | strategist+ | `write`, `picklist` | `picklist.save` | `{data: PicklistData, base_version?}`; 409 `{message, current}` if `base_version` is stale; saves a new version |
| DELETE | `/events/{ev}/picklists/{name}` | strategist+ | `write`, `picklist` | `picklist.delete` | deletes the current row; history stays; 204 |
| GET | `/events/{ev}/picklists/{name}/versions?limit=` | viewer+ | | | newest first (default 200, max 1000), `{version, updated_at, updated_by, note, current}`; works for deleted picklists |
| GET | `/events/{ev}/picklists/{name}/versions/{v}` | viewer+ | | | one version incl. `data` |
| POST | `/events/{ev}/picklists/{name}/versions/{v}/restore` | strategist+ | `write`, `picklist` | `picklist.restore` | saves version v's data as a new version |

**Notes.** Plain text, <= 4000 chars, optional `team` and `match` references. Soft removal only.

| method | path | role | limits | audit | what |
|---|---|---|---|---|---|
| GET | `/events/{ev}/notes?team=&match=&include_removed=` | viewer+ (`include_removed`: strategist+) | | | newest first, max 2000; `{id, event, team, match, text, author, created_at, updated_at, edited_by, removed?, removed_by?, removed_at?}` |
| POST | `/events/{ev}/notes` | scout+ | `write`, `note` | `note.create` | `{team?, match?, text}` |
| PATCH | `/notes/{nid}` | author, or strategist+ | `write`, `note_edit` | `note.edit` | `{text}`; `edited_by` set when the editor isn't the author; 404 if removed |
| DELETE | `/notes/{nid}` | author (-> `deleted`) or strategist+ on someone else's (-> `hidden`) | `write`, `note_edit` | `note.delete` / `note.hide` | soft: sets `removed_at/by/kind`; 204 |
| POST | `/notes/{nid}/restore` | strategist+, or the author for their own `deleted` note | `write`, `note_edit` | `note.restore` | 409 if not removed; 403 if a strategist hid it and you're the author |

Every user reference in responses is `public_name()` = `{id, display_name}` ("Teammate #id" when empty,
"(deleted account)" when gone); usernames are never shown to other users.

**Identity overrides.** File `DATA/events/<ev>/overrides.json` = `{"<match key>": {"red": {"<track idx>":
"frcN"}, "blue": {...}}}`, read by the worker each poll (`identity.assign_event`). Written with a process lock and
`atomic_write_json` (0644). Every applied write also appends to `override_log`.

| method | path | role | limits | audit | what |
|---|---|---|---|---|---|
| GET | `/events/{ev}/overrides` | viewer+ | | | `{event, overrides: <file>, log: [last 100 {match, data, by, requested_by, at}], pending: [{id, match, data, base, current, stale, status, by, at, decided_by, decided_at}], mode}` |
| PUT | `/events/{ev}/overrides/{match}` | scout+ | `write`, `override` | `override.set` or `override.request` | body `{red: {...}, blue: {...}}` (`extra=forbid`); empty sides dropped. **Strategist+ or `mode=direct`**: written now, 200 `{event, match, entry, status: "applied"}`. **Scout in `review` mode**: 202 `{event, match, status: "pending", request}`; the scout's earlier pending request for that match becomes `superseded`; max 30 pending per scout |
| DELETE | `/events/{ev}/overrides/{match}` | scout+ | same | `override.clear` or `override.request` | same as PUT with `{}` (removes the match's entry); 204 when applied, 202 when queued |
| POST | `/events/{ev}/override-requests/{rid}/approve` | strategist+ | `write`, `override` | `override.approve` | applies the requested entry (`override_log.requested_by` = requester); 409 if not pending |
| POST | `/events/{ev}/override-requests/{rid}/reject` | strategist+, or the requester | `write` | `override.reject` / `override.withdraw` | status `rejected`, or `withdrawn` when a non-strategist drops their own |

A request carries `base` (the entry when requested) and is shown `stale` when the file has changed since.
The mode is the setting `scout_overrides` (`review` default, or `direct`).

### 7.7 Admin safety controls and audit (`routers/admin.py`, admin only)

| method | path | audit | what |
|---|---|---|---|
| GET | `/admin/security` | | `{lockdown: null \| {on, at, by: {id, display_name}}, passwordless_login, scout_overrides}` |
| PATCH | `/admin/security` | `settings.security` | `{lockdown?: bool, passwordless_login?: never\|lan\|everywhere, scout_overrides?: review\|direct}`; null fields ignored; switching to `never` deletes all `net_locked` sessions |
| POST | `/admin/sessions/revoke` | `sessions.revoke_all` | `{include_admins: false}` -> deletes every other session of non-admins (or of everyone); your own stays; `{signed_out: n}` |
| GET | `/audit?user_id=&action=&event=&since=&via=&before_id=&limit=&changes=` | | newest first; `action` is a prefix (LIKE-escaped); `changes=true` leaves out `auth.login`, `auth.login_failed`, `auth.key_login`, `auth.key_login_failed`, `auth.logout` and every `http.*` entry (the Activity page's default view); `limit` 1-500 (default 100); `{entries: [{id, at, action, event, target, user: {id, username, display_name, role}, ip, via, ua, before, after, ref, revertible, reverted_by}], next_before_id}` (paging cursor, null on the last page) |
| POST | `/audit/{aid}/revert` | `<family>.revert` | section 6.2; `{reverted, entry}` |
| POST | `/audit/revert-user` | one `*.revert` per entry (none with `dry_run`) | `{user_id, since, dry_run=false}` -> `{reverted, skipped, dry_run}` |

### 7.8 Video sources, layout, calibration, jobs, livestream (`routers/sources.py`)

Logic lives in `live/sources.py` and `live/layout.py`, which the API image copies to `/srv/live/` and imports
(`sys.path` additions: `../../live`, `/srv/live`, `/app/live`). If the import fails, every route here returns
`503 "video setup is not installed in this API image"`. None of these routes call `audit.record`; mutations get
`http.*` fallback entries.

Files (all under `DATA/events/<ev>/` unless noted):

| file | written by | read by |
|---|---|---|
| `sources.json` | `_save` (atomic, `event` + `updated_at` stamped); discover / playlist jobs | worker (`sources.video_config`, `station_pattern`, `livestream_url`, `media_for`), recorder |
| `layout.json` | `PUT/DELETE /layout/{sid}`, `PATCH /sources/{sid}` (primary) | pipeline (`live/layout.py` `find()`), stationcam, livestream |
| `camera.json`, `station_red.json`, `station_blue.json` | `calibration/{target}/accept` (old file kept as `<file>.bak-<ts>`) | worker (camera pose), stationcam |
| `pose_latest.json` | worker; **moved** to `.bak-<ts>` on overhead accept so the new calibration is used | worker |
| `calib_wizard/<target>.json` | detect / fit jobs; accept stamps `accepted_at` | wizard routes |
| `frames/<sid>.jpg`, `frames/calib_<target>.jpg` | frame / detect jobs | `frame.jpg` routes |
| `livestream.json` | recorder service (`live/liveingest.py`, ~every 10 s) | `GET /livestream` |
| `DATA/jobs/sources/<id>.json` (+ `<id>.lock`) | `sources.new_job`, runners | `GET /jobs/{jid}` |

**Jobs.** `sources.submit()` writes a job file (`id` = ms-timestamp hex + 6 random hex, `state: queued`). It runs
the job in a background thread *in the API process* only if the kind is in `LIGHT_JOBS` (`discover`) or numpy +
cv2 import (`vision_available()`). The API image has neither, so `frame`, `suggest`, `detect`, `fit`, `overlay`
and `index_playlist` stay queued until the **worker's** `sources.job_loop` thread (started in
`live/worker.py:main()`, also while `PAUSE_PROCESSING=1`) claims them with an `O_EXCL` lock file. Jobs older
than 1 h with no runner are marked `error: expired`. States: `queued -> running -> done | error`, with
`result` / `error`, `started`, `finished`.

| method | path | role | what |
|---|---|---|---|
| GET | `/events/{ev}/sources` | viewer+ | `{event, sources[] (+ has_layout, n_videos, has_frame), discovered_at, roles, kinds, jobs_inline, video_config, station_pattern, livestream}`; each `label` goes through `display_label` (a TBA webcast still named "Event webcast (twitch)" is shown as "Event webcast · Twitch firstwa_red2") |
| GET | `/events/{ev}/sources/webcasts` | admin | TBA `/event/{ev}` webcasts (YouTube / Twitch only, canonical URL, each once) for the Video page to offer above its paste box: `{event, webcasts: [{url, label, provider, kind, channel, date, source (id of the source that already has the link, else null), dismissed}]}`. No TBA key or TBA down -> `{webcasts: [], note}`, never an error |
| POST | `/events/{ev}/sources/discover` | admin | fetches TBA `/event/{ev}` (webcasts) and `/event/{ev}/matches` (per-match videos) and queues a `discover` job (runs inline); returns `{id, kind, state, created}`. Webcasts are named `webcast_label` ("Event webcast · <provider> <channel>[, <date>]"); `merge` renames an old automatic name |
| POST | `/events/{ev}/sources` | admin | `{url (<= 2000), role: wide\|dynamic\|alliance-side, label?, primary?, coverage?}`; `sources.add` -> `_checked`: `classify_url` (leading `-` refused), then only **YouTube / Twitch links, a direct video address** (path ending `.m3u8 .mp4 .m4v .mov .mkv .webm .ts .flv`) **or a file under the media / data dirs**; any other web page (vimeo.com, a site's home page) is refused. `safe_public_url` refuses private / loopback / link-local / docker / Tailscale hosts (allow-list `CS_URL_ALLOW`). Every refusal is a 422 with one sentence for the admin (`link_problem`, e.g. "vimeo.com isn't YouTube or Twitch. Paste the event's stream or video link ..."). A direct `http(s)` link then gets one `HEAD` (`probe_http`: 5 s, redirects not followed; 404 / 410 or `text/html` -> 422 "That address answers “404 Not Found” ..."; anything else, or unreachable, passes; `CS_PROBE_LINKS=0` skips it). Id = `url-<sha1[:8]>`; 201. Adding a link never changes the event's video mode. A playlist/channel (`kind: per_match`) also queues `index_playlist` |
| PATCH | `/events/{ev}/sources/{sid}` | admin | `{role?, primary?, enabled?, label?, coverage?, sample?, notes?, station?, dismissed?, url?, live?, dynamic?}`; a new `url` (admin-added sources only, id kept) goes through the same checks and probe as POST; `sample` validated by `clean_sample` (`{video, match, t}`); `primary: true` also sets `layout.json processing_source` if that source has a layout |
| DELETE | `/events/{ev}/sources/{sid}` | admin | removes the source; its layout entry is kept |
| POST | `/events/{ev}/sources/{sid}/frame` | admin | body `{t?, match?}` -> `frame` job |
| GET | `/events/{ev}/sources/{sid}/frame.jpg` | admin | last grabbed frame (path-checked, `no-store`) |
| GET | `/events/{ev}/layout` | viewer+ | `{event, layout, kinds, defaults: {W, H, wide, program, note}}` (defaults = the hard-coded 2026 FIRST boxes) |
| PUT | `/events/{ev}/layout/{sid}` | admin | `{W, H (1..8192), regions: [{id?, kind, label?, box: [x0,y0,x1,y1], light_on_dark?}] (<= 60), frame?}`; kinds from `layout.KINDS` (`field, overhead, station_red, station_blue, clock, scoreboard, mask, inset`); boxes clamped to the frame, must be >= 2 px; at most one each of `station_red, station_blue, clock, field, overhead`. Sets `processing_source` when the source is primary and `station_source` when it has station boxes |
| DELETE | `/events/{ev}/layout/{sid}` | admin | forget one source's boxes (clears `processing_source` / `station_source` if they pointed at it) |
| POST | `/events/{ev}/layout/{sid}/suggest` | admin | body `{other?, match?}` -> `suggest` job (auto-detect overlay / clock / inset / field boxes) |
| GET | `/events/{ev}/calibration` | viewer+ | per target (`overhead`, `station_red`, `station_blue`): `{target, file, saved, where: data\|repo, W, H, crop, calib, tag_rms, mtime, has_frame (the wizard's frame exists), candidate?: {fitted_at, report, has_camera, source}}`; looks in `DATA/events/<ev>/` then the repo's `events/<ev>/` |
| POST | `/events/{ev}/calibration/{target}/detect` | admin | body `{source?, match?}` -> `detect` job (sample frames + AprilTags, creates the wizard state) |
| POST | `/events/{ev}/calibration/{target}/fit` | admin | `{points: {name: [u,v]}, drop_tags: [id], line_clicks: {line: [[u,v],...]} (<= 60 per line), tags?, auto_lines=true, more_tags=true}` -> `fit` job; 409 before detect |
| GET | `/events/{ev}/calibration/{target}/frame.jpg` | admin | the wizard frame |
| GET | `/events/{ev}/calibration/{target}/wizard` | admin | wizard state minus `p`; 404 before detect |
| POST | `/events/{ev}/calibration/{target}/overlay` | admin | `overlay` job (projected grid of the saved calibration) |
| POST | `/events/{ev}/calibration/{target}/accept` | admin | writes the fitted camera (with `calib.accepted_by` = display name, `accepted_at`) to `camera.json` / `station_<side>.json`; 409 without a fit |
| GET | `/events/{ev}/jobs/{jid}` | admin | job state/result (`params` removed); adds a `hint` when a vision job has waited > 20 s with no inline runner |
| GET | `/events/{ev}/livestream` | viewer+ | recorder status (below) |

**Livestream status** (`GET /events/{ev}/livestream`): reads `livestream.json` and the recorder service's heartbeat
`DATA/recorder-status.json` (`{at, pid, events, events_at, expect_next_s, error, stopped}`, written every loop by
`live/liveingest.py`; fresh = written within 60 s + its `expect_next_s`, not `stopped`). No enabled livestream
source: `{state: "no_source"}`. A source but no file yet (the service picks it up on its next loop): fresh heartbeat →
`starting` "The recorder picks up this stream within a few seconds." (`error` with its error when the heartbeat
carries one; `idle` "…this one isn't followed" when the service has read the current `worker.json` and the event
isn't in it); no heartbeat while the api is < 90 s old → `starting` "The recorder service is starting."; else
`not_running` "The recorder service isn't running: start the recorder container (docker compose up -d recorder)."
(`recorder_seen` = the last heartbeat). With a file: its content plus `event`, `configured_url`, `age_s`,
`recorder_seen` and `stale` (`age > 60 s + expect_next_s`, at most +300 s: a slow stream-link check announces its busy
time); a stale `recording` / `waiting` / `starting` state gets "the recorder service is running but hasn't reported
this stream for N: see docker compose logs recorder" (fresh heartbeat) or "recorder service not reporting for N: start
the recorder container (docker compose up -d recorder)"; a `stopped` file without a fresh heartbeat becomes
`not_running` (`service_state: "stopped"`). Recorder states include `starting, recording, waiting, stream_over, idle,
error, no_source, disabled, stopped`, plus the API's `not_running`. (The video-mode checklist, `_readiness`, still
uses the plain 60 s rule.)

### 7.9 Updates (`routers/update.py`, admin only)

The API never runs git or docker. It writes a request file and reads a status file in `DATA/config/`; the host
updater (`deploy/updater/cs-updater.py`, systemd path + timer units) does the work. See `docs/guide/deploy.md`.

**Request** `DATA/config/update-request.json` (atomic write, mode 0644):

```json
{"v": 1, "action": "check|apply|rollback", "nonce": "<32 hex>", "at": 1791866000.0,
 "by": {"id": 1, "name": "<display name, <= 64>"}, "ytdlp": false}
```

No commands, URLs, refs or paths. The updater re-validates (regular file, <= 2 KB, exact fields and types,
< 15 min old, unseen nonce) and deletes it when consumed.

**Status** `DATA/config/update-status.json` is read with `O_NOFOLLOW`, must be a regular file <= 512 KiB, and is
passed through `parse_status()`, which keeps only known fields with checked types and lengths:
`installed` (heartbeat < 180 s old), `heartbeat`, `updater_version`, `state` (`idle, checking, ready, applying,
done, failed, rolled_back`), `action`, `branch`, `current` / `latest` / `previous` (`{sha (40 hex), short, date,
subject, local?}`), `checked_at`, `changelog[]` (<= 200), `changelog_more`, `touches {worker: heavy|light, api,
web, compose}`, `disk {free_gb, need_gb, ok}`, `progress[]` (last 150 lines), `result {action, ok, message, at}`,
`request_nonce`, `rejected {at, reason}`, `started_at`, `finished_at`, `paused`, `ytdlp_pinned`,
`ytdlp_allowed`, `features` (updater v2: `["env"]`), `env {schema, values, error}`, `env_progress[]`,
`env_result {ok, message, at, rolled_back}`. The API adds `pending` (an unconsumed request < 15 min old) and
`install_hint`.

| method | path | limits | audit | what |
|---|---|---|---|---|
| GET | `/admin/update` | | | status as above |
| POST | `/admin/update/check` | update 6/min, 60/h | `update.check` | 202 `{queued, nonce}` |
| POST | `/admin/update/apply` | same | `update.apply` | body `{ytdlp: StrictBool = false}` (`extra=forbid`) |
| POST | `/admin/update/rollback` | same | `update.rollback` | 409 when there is no `previous` |
| GET | `/admin/server-env` | | | Admin → Server: `{installed, supported, busy, running, other_task, schema, values, error, progress, result, install_hint}`; `schema` = the updater's `ENV_KEYS` (kinds bool / int / choice / gpus / mem / tz / hosts, checked copy), `values` = those keys in `.env` (null = unset) |
| POST | `/admin/server-env` | same as update | `server.env` (before/after values) | body `{changes: {KEY: str \| null}}` (`extra=forbid`); keys must be in `schema`, values match `ENV_FORMATS` (same regexes as the updater; `test_server_env` keeps them equal); unchanged keys dropped, nothing left → 400; writes `{..., action: "env", env: {...}}` → 202 `{queued, nonce, changes}`; 409 not installed / updater v1 / busy |

All three POSTs return 409 when the updater isn't installed / heartbeat stale (with the install hint), a task is
`checking` / `applying`, a request is already pending, or `ytdlp` is asked for but `ytdlp_allowed` is false.
They work during lockdown (admins). On atomosserver the updater is **not installed** at the time of writing
(no `cyber-scout*` systemd units, no `updater/` folder), so the Updates tab reports "not installed".

### 7.9a Worker logs (`routers/logs.py`, admin only)

`GET /app/v1/admin/logs?since=worker:120,recorder:40` -> `{roles: [{role, alive, at, age_s, doing, doing_since, also,

`GET /app/v1/admin/jobs` (admin only, `server/routers/jobs.py`): one snapshot for Admin → Jobs: `{now, runners: [{id, kind: worker|processor|client|helper|recorder, label, where, alive, age_s, doing, doing_since, log_role, gpu: {ids, device, cards: [{index, name, util_pct, mem_used_mb, mem_total_mb}]}, current: {ev, key, label, since, progress, stage}}], running: [{type: match|video|stream, by, ev, key, label, since, progress, stage}], queued: {videos: [{id, kind, ev, created, age_s}], matches: [{ev, key, label, wait, note, takers, pos}], matches_total, counts}, recent: [{type, ev, key, label, ok, at, took_s, error}]}`. Sources: `DATA/logs/*.json`, `processors/*.json`, `events/<ev>/claims` (owner `app-u<N>-<id>` = a Client, `helper-*` = ssh helper, a processor id, else the server worker), `processing/<ev>/<key>/job.json` (Client progress), `processing/streams`, `status.json` + `proc_status/`, `livestream.json`, `recorder-status.json`, `jobs/sources/*.json`, `site/api/<ev>/event.json` rows (`wait`, `state_note`, in `claims.order`). Files are untrusted (no symlinks, size caps, type checks); stale claims, dead heartbeats and video jobs running over 2 h are never shown as running. Match durations are not recorded, so `took_s` is only set for video jobs.
started, pid, seq, lines}]}`: one entry per `DATA/logs/<role>.json` (see architecture.md), `lines` only those newer than the
role's `since` seq, `alive` while the file's `at` is under 90 s old; a processor silent for over a day is left out. Files are
opened with `O_NOFOLLOW`, capped at 2 MB, parsed and type-checked field by field (the containers can write `data/`); anything
malformed is skipped. 403 for non-admins, 401 signed out.

### 7.10 Distributed processing (`routers/processing.py`, admin only)

Cyber-Scout Clients with a GPU track matches next to the worker ("Help process matches", `client/README.md`), and
any client can record a livestream and send match clips ("Clip only"). Uses `live/claims.py` (the worker's per-match
claims and order), `live/liveingest.py` (livestream records, `LiveView`, `live_sources`, `record_key`) and
`live/sources.py`; without them every route answers 503. Claim owners are `app-u<user id>-<processor id>`.
Heartbeats, chunk uploads and speed tests set `request.state.audited` (no fallback entries); the audit actions are
`processing.claim`, `processing.upload`, `processing.release`, `processing.stream`, `processing.stream_release`,
`processing.clip`.

| method | path | what |
|---|---|---|
| GET | `/processing/pipeline` | code version + `code` / `model` file refs, `requirements` (CPU), `runtimes` {cuda, coreml, clip} (the client's Set up installs one), `clip_extras`, `ytdlp_requirements`, `python`, `claim_s`, `heartbeat_s` |
| GET | `/processing/pipeline/code.tar.gz?v=`, `/processing/pipeline/model.onnx` | Range-resumable; 409 on an old `v` |
| POST | `/processing/claim` | `{processor, pipeline?, events?, skip?}` -> `{job \| null, why, counts}`: the next trackable livestream clip, claimed (409 when the pipeline changed) |
| GET | `/processing/{ev}/{key}/clip`, `.../view.png` | the claimed match's clip (Range) / the stream camera's view reference |
| POST | `/processing/{ev}/{key}/heartbeat` | `{processor, progress?, stage?}`; 409 when the claim was lost |
| PUT | `/processing/{ev}/{key}/results/{kind}` | fuel / shots / analytics / matches JSON (gzip ok; <= 20 MB on the wire, 64 MB decompressed), staged |
| POST | `/processing/{ev}/{key}/complete` | staged files into `events/<ev>/<kind>/` (matches last), the clip into `MEDIA/<ev>/<key>.mp4`, claim released; idempotent |
| POST | `/processing/{ev}/{key}/release` | `{processor, failed?, reason?}`; 2 failures -> the match is left to the worker |
| GET | `/processing/status` | live claims of the followed events |
| GET / POST | `/processing/speedtest` | `?bytes=` (<= 8 MB) of random bytes / an upload that is read and dropped: the client's slow-link notice |
| GET | `/processing/sample`, `/processing/sample.mp4?ev=&name=` | the first 24 MB of the newest faststart livestream clip (the client's benchmark; Range) + `typical_clip_bytes` |
| POST | `/processing/streams/claim` | `{processor, events?, ev?, source?}` -> `{assignment \| null, why}`: a livestream source of a followed event in its dates (`liveingest.live_sources`), one processor per source; a source the server's recorder isn't on (`livestream.json`) first (`role: secondary`), else the one it is on (`role: backup`). Same processor + event + source -> the same `aid` (`cs` + 10 hex), so a restart resumes |
| POST | `/processing/streams/{ev}/{aid}/heartbeat` | `{processor, state?, rec?}` -> `{matches (TBA times since the recording started), want, have, skipped, grace_s, stop}`. `want`: over (start + 193 s, + 240 s grace when the server records the event too), no clip from this stream yet, no usable clip from any other recording |
| POST | `/processing/streams/{ev}/{aid}/release` | stop recording it |
| POST | `/processing/streams/{ev}/{aid}/clips/{mid}` | `{processor, size (<= 1 GB), sha256}` -> `{offset, chunk_bytes, chunk_max}` (or `done`); 507 when the media disk would keep < 2 GB free. `mid` = `tba-qm12`, `tba-sf3m1`, `tba-f1m2` |
| PUT | `/processing/streams/{ev}/{aid}/clips/{mid}/data?processor=&offset=` | one chunk (<= 16 MB); 409 `expected offset N` when out of step |
| POST | `/processing/streams/{ev}/{aid}/clips/{mid}/finish` | `{processor, record}` (only the `tba_cut` record fields; `extra=forbid`, `record_key` must be this match): size + sha256 + MP4 checked, then `MEDIA/live/<ev>/<aid>/clips/<mid>.mp4`, `matches/<mid>.json` (+ `uploaded`, flag `uploaded_by_client`), `meta.json` (`mode: client`). Idempotent |

Files: `DATA/processing/<ev>/<key>/` (job.json + staged results), `DATA/processing/<ev>/failures.json`,
`DATA/processing/streams/<ev>/<aid>.json` (assignments, stale after `CLAIM_S`), `DATA/cache/processing/code-*.tar.gz`,
`MEDIA/live/<ev>/<aid>/.uploads/` (uploads in progress, same disk as the clips).

---

## 8. `DATA/config/worker.json`

Written by `workerconf.write(db)` with `atomic_write_json(..., mode=0o600)` (temp file `<path>.tmp<pid>`,
`chmod`, `os.replace`): at startup and after every settings change, TBA key change, event follow/unfollow, and
settings/events revert. Not after `/admin/security` changes (they don't affect the worker).

```json
{"version": 1, "updated": 1791866000.0,
 "tba_key": "..." ,                       // null -> worker uses its TBA_KEY env
 "events": ["2026wasam"],                 // null until events were managed in the app -> worker uses EVENTS env
 "team_number": 4911, "season_goal": "DCMP",
 "options": {"poll_seconds": null, "gpu_device": null, "cpu_only": null, "hubcam": null}}
```

`live/worker.py:load_config()` re-reads it every poll (and the worker's sleep loop wakes up when its mtime
changes); `live/liveingest.py` (recorder) reads it the same way. Option `null` = the `.env` value
(`POLL_SECONDS`, `GPU_DEVICE`, `FORCE_CPU`, `HUBCAM`).

---

## 9. Database (`DATA/app.db`)

SQLite, WAL (`app.db-wal`, `app.db-shm` alongside), file mode 0600, owned by `PUID:PGID`. All times are unix
seconds (REAL). Settings values are JSON text.

### 9.1 Tables

**`users`**

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `username` | TEXT NOT NULL UNIQUE COLLATE NOCASE | the login name; a credential for no-password accounts |
| `display_name` | TEXT NOT NULL DEFAULT '' | what other users see |
| `pw_hash` | TEXT NOT NULL | argon2id (hash of "" for no-password accounts) |
| `role` | TEXT NOT NULL CHECK in (viewer, scout, strategist, admin) | |
| `disabled` | INTEGER NOT NULL DEFAULT 0 | |
| `created_at`, `updated_at` | REAL NOT NULL | |
| `last_login` | REAL | |
| `pw_strength` | INTEGER NOT NULL DEFAULT 2 | 0 none, 1 member rule, 2 admin rule (migration) |

**`sessions`**: `token_hash` TEXT PK (sha256 hex), `user_id` FK users ON DELETE CASCADE, `csrf` TEXT,
`created_at`, `last_seen`, `expires_at` REAL, `net_locked` INTEGER DEFAULT 0 (migration). Index
`sessions_user(user_id)`.

**`connect_keys`**: `key_hash` TEXT PK (sha256 hex of the normalized key), `user_id` UNIQUE FK users ON DELETE
CASCADE, `created_at`, `expires_at` REAL NOT NULL, `created_by` (admin id, no FK), `used_at` REAL (null until spent).
In `CORE_SCHEMA`, so existing DBs get it on the next start.

**`settings`**: `key` TEXT PK, `value` TEXT (JSON), `updated_at`. Keys in use:

| key | value | set by |
|---|---|---|
| `tba_key` | string | `PUT /settings/tba-key`, `.env` seed |
| `team_number`, `team_name`, `season_goal` | int / string | `PATCH /settings` |
| `options` | `{poll_seconds, gpu_device, cpu_only, hubcam}` | `PATCH /settings` |
| `setup_complete` | bool | wizard via `PATCH /settings` |
| `events_configured` | bool | follow / unfollow / seed |
| `seeded_from_env` | bool | startup |
| `lockdown` | `{on, at, by}` or absent | `PATCH /admin/security` |
| `passwordless_login` | `never` / `lan` / `everywhere` | `PATCH /admin/security` |
| `scout_overrides` | `review` / `direct` | `PATCH /admin/security` |

**`events`**: `key` TEXT PK, `name`, `year` INTEGER, `start_date` TEXT, `added_at` REAL NOT NULL, `added_by` FK
users ON DELETE SET NULL.

**`picklists`**: (`event`, `name`) PK, `data` TEXT (JSON, `PicklistData`), `version` INTEGER, `updated_at`,
`updated_by` FK users ON DELETE SET NULL.

**`picklist_versions`**: (`event`, `name`, `version`) PK, `data`, `updated_at`, `updated_by` (no FK),
`note` (`restored vN`).

**`notes`**: `id` PK, `event` NOT NULL, `team`, `match`, `text` NOT NULL, `author_id` FK users ON DELETE SET NULL,
`created_at`, `updated_at`; migrations add `removed_at`, `removed_by`, `removed_kind` (`deleted | hidden |
reverted`), `edited_by`. Index `notes_ev(event, team, match)`.

**`override_log`**: `id` PK, `event`, `match`, `data` (JSON entry, `{}` = cleared), `user_id` FK users ON DELETE
SET NULL, `at`; migration adds `requested_by`.

**`override_requests`**: `id` PK, `event`, `match`, `data` (requested entry), `base` (entry at request time),
`status` CHECK in (`pending, approved, rejected, withdrawn, superseded`), `user_id`, `created_at`,
`decided_by`, `decided_at` (no FKs). Index `override_requests_ev(event, status)`.

**`audit_log`**: `id` PK, `at`, `user_id`, `username`, `display_name`, `role`, `ip`, `via`, `ua`, `action` NOT
NULL, `event`, `target`, `before`, `after`, `ref`. Indexes `audit_at(at)`, `audit_user(user_id, id)`,
`audit_ref(ref)`. Triggers `audit_no_update`, `audit_no_delete`.

### 9.2 Migrations

There is no schema version number. `CORE_SCHEMA` is idempotent DDL, and `MIGRATIONS` is a list of
`(table, column, "ALTER TABLE ... ADD COLUMN ...")` applied when `PRAGMA table_info` lacks the column. To change
the schema: add new tables to `CORE_SCHEMA` (or `add_schema()` from a router), add new columns as a
`MIGRATIONS` entry, never rename or drop. `test_migrates_an_existing_db` covers upgrading a first-release DB.
Older code reading a migrated DB generally still works (added columns only); the updater backs the DB up but does
not restore it on rollback.

### 9.3 Backup

`sqlite3 data/app.db ".backup data/app.db.bak"` while running, or stop the stack. The host updater keeps 5
online backups in `<stack>/updater/backups/` before each apply. nginx returns 404 for any `*.db`, `*.db-wal`,
`*.bak`, ... URL, and `app.db` is not under any served path.

---

## 10. `manage.py` (break-glass CLI)

```bash
docker compose exec api python -m server.manage users                       # username, role, active/disabled, last login
docker compose exec api python -m server.manage reset-password <username>   # prints a random xxxxxx-xxxxxx-xxxxxx password, ends sessions
docker compose exec api python -m server.manage make-admin <username>       # role=admin, disabled=0
```

When run as root (the default for `docker compose exec`), it first drops to `PUID`/`PGID` so it doesn't leave
root-owned DB/WAL files. Notes: `reset-password` sets the hash but leaves `pw_strength` as it was; `make-admin`
doesn't check `pw_strength`, so it can produce an admin with a weak or empty password (a recovery tool, not a
policy path). Neither writes an audit entry.

---

## 11. Environment variables

| variable | default | read by | meaning |
|---|---|---|---|
| `DATA` | `/data` | `config.py` | data root (compose sets `/data`, mounted from `./data`) |
| `PUID`, `PGID` | 1000 | `entrypoint.sh`, `manage.py` | uid/gid the API runs as |
| `SETUP_CODE_REQUIRED` | `1` | `config.py` | `0` = first-run setup without the code |
| `SESSION_IDLE_DAYS` | 7 | `config.py` | idle expiry |
| `SESSION_MAX_DAYS` | 30 | `config.py` | absolute expiry and cookie Max-Age |
| `COOKIE_SECURE` | `auto` | `config.py` | `1` / `0` / `auto` (by `X-Forwarded-Proto`) |
| `PASSWORD_MIN` | 10 | `config.py` | admin password minimum |
| `MEMBER_PASSWORD_MIN` | 4 | `config.py` | non-admin minimum when a password is set |
| `TBA_BASE` | `https://www.thebluealliance.com/api/v3` | `config.py` | TBA API root (tests stub it) |
| `TBA_KEY`, `EVENTS` | empty | `config.py` -> `seed_from_env` | imported once into an empty DB |
| `CS_URL_ALLOW` | empty | `live/sources.py` (in the api for pasted links) | private hosts / CIDRs a pasted video link may use |
| `CS_PROBE_LINKS` | `1` | `live/sources.py:probe_http` | `0` = no `HEAD` check of a pasted direct video link (tests set it) |
| `TZ`, `PYTHONDONTWRITEBYTECODE` | | compose | |

The shipped `deploy/docker-compose.yml` gives the api no `env_file`; its `environment:` is an explicit allowlist:
`DATA`, `PUID`, `PGID`, `TZ`, `COOKIE_SECURE`, `PYTHONDONTWRITEBYTECODE` and (since the cleanup pass)
`SESSION_IDLE_DAYS`, `SESSION_MAX_DAYS`, `PASSWORD_MIN`, `MEMBER_PASSWORD_MIN`, `SETUP_CODE_REQUIRED`, each as `${VAR:-<config.py default>}`. `TBA_BASE`,
`TBA_KEY` and `EVENTS` are not passed (the one-time `.env` import is off on purpose). `CS_URL_ALLOW` (empty) and
`CS_PROBE_LINKS` (`1`) are, because the pasted-link checks run in the api. A new api setting must be added to that block; `server/tests/test_hardening.py::test_compose_gives_api_an_env_allowlist` checks the list and its
defaults.

Container hardening (compose): `read_only: true`, tmpfs `/tmp` 64 MB, `cap_drop: ALL` + `CHOWN, DAC_OVERRIDE,
SETUID, SETGID` for the entrypoint's root phase, `no-new-privileges`, `pids_limit 256`, `mem_limit
${API_MEM_LIMIT:-1g}`, no published port.

---

## 12. Tests (`server/tests/`)

```bash
pip install -r server/requirements-dev.txt          # or use the repo's .venv
python -m pytest server/tests                        # 310 tests after connection keys (586 with tests/)
```

`conftest.py`: the `data` fixture points `cfg` at a tmp dir, clears the limiters and the TBA cache, and replaces
`tba.get` with `fake_get` (key `"k"*60`, canned `/status`, team 4911, 2026 events). Clients connect from
`10.0.0.23` (a LAN address) and wrap `TestClient` so the `cs_csrf` cookie is echoed as `X-CSRF-Token`. Fixtures:
`client`, `admin` (setup done, admin `coach` / `correct horse 1`; sets `passwordless_login=lan` because many
tests use no-password teammates), `team` (one signed-in client per role).

| file | covers |
|---|---|
| `test_auth.py` | setup mode + code, cookie flags, raw token not stored, CSRF required, login/logout, login rate limit, idle and absolute expiry, password change, disable kills sessions, last-admin guard, TBA key never returned, setup without code, `.env` seeding, optional non-admin passwords, imported config still needs the wizard |
| `test_connect_keys.py` | connection keys: create -> sign in once -> second use 401; expired (and purged); a new key replaces the old; revoke (204 twice, one audit entry); disabled accounts (disable drops the key, 409 on create); account deletion cascades; admin only; `hours` validation; normalization (case, dashes, spaces, O/0, I/L/1) and malformed keys; rate limit (`#key` pair, shared per-IP bucket); key sessions are full sessions (no password, policy `never`, tunnel, lockdown); 6 parallel uses -> one wins; CSRF; the key never in `audit_log` / `connect_keys` / `idempotency` / `sessions`; the Activity `changes` filter |
| `test_roles.py` | parametrized role matrix (20 endpoint cases x 4 roles), anonymous 401s, options hidden from non-admins, note ownership, role change applies immediately |
| `test_data.py` | wizard flow, worker.json `events: null` until configured, picklist versioning, overrides file, note validation, admin-only OpenAPI, picklist rejects markup |
| `test_hardening.py` | audit of every change (who/where), append-only triggers, no secrets in the log, failed logins logged without user enumeration, fallback `http.*` audit, picklist history / restore / revert-of-revert, revert-user, note soft delete / hide / restore, note markup and invisible-char refusal, scout fix approval queue, direct mode, pending cap, note flood and picklist/global write limits, display-name rules, new accounts default to viewer, passwordless LAN-only and fail-closed proxy detection, policy switching, password unlocks a session, rate limit keyed on the real IP, lockdown, sign everyone out, disable is instant, no self-promotion, no usernames leak to teammates, settings refuse markup, settings/events revert, audit filters, migrating an old DB, the compose api env allowlist (cleanup pass) |
| `test_notes_ui.py` | (cleanup pass) the contract `web/notes.js` relies on: match notes tagged with a team appear on both pages, viewers read but can't write, own edit / delete / undo, strategist hide / restore (author can't undo a hide), lockdown answers 423 for notes and fixes while reads still work, the "Wrong team?" merge-then-PUT flow (202 pending → approve → merged file), scout "ask to clear" queued vs strategist clear applied |
| `test_sources_api.py` | `POST /sources` rejects option-like, `file:`, `javascript:`, relative, private / loopback / Tailscale URLs; normalisation (Twitch, host lower-casing); `sample` validation; skipped if `live/sources.py` can't be imported (sets `CS_PROBE_LINKS=0`) |
| `test_video_links_api.py` | (UX fix wave) only YouTube / Twitch / direct video / server files accepted, each refusal a sentence; a dead direct link is refused on POST and on a PATCH of the URL and not saved; adding a link never sets the video mode; `GET sources/webcasts` (added / dismissed flags, admin only); old webcast names get their channel |
| `test_ux_a.py` | (UX fix wave) `/session.processing` (paused / stale / unknown, null when signed out), the setup code checked first, trivial admin passwords refused, TBA not-found sentences, `status_beat` writes `worker-status.json`, audit `changes=true` and `revert-user` `dry_run` |
| `test_livestream.py` | `/livestream` states `no_source` / `not_running` / `recording` / stale, bad event key, needs login |
| `test_update.py` | admin-only, 401, CSRF, not-installed, fixed-schema request + audit, strict apply body, display name can't break the request, busy/rollback rules, yt-dlp switch, lockdown doesn't block admins, rate limit, status parsing whitelist and types, garbage and symlinked status; plus the updater's own validator, helpers, yt-dlp lock validation and conf parsing (loads `deploy/updater/cs-updater.py` directly) |

---

## 13. Adding an endpoint (checklist)

1. Put it in an existing router or a new `server/routers/<x>.py` with `router = APIRouter(tags=[...])`, and add it
   to the tuple in `main.create_app()`.
2. Depend on `require_role("<min role>")` (gives you 401/403/CSRF/lockdown/write budget) and `valid_event` for
   `{ev}`; take `db=Depends(DB.get_db)`.
3. Bodies: pydantic models with `model_config = ConfigDict(extra="forbid")`; free text through `textrules`.
4. Mutations inside `with DB.tx(db):` and call `audit.record(...)` there with before/after snapshots. If it should
   be undoable, use an existing family or extend `audit.REVERTIBLE` and `admin._revert`.
5. Files under `cfg.DATA`: `security.atomic_write_json(path, obj, mode)`; 0600 for anything secret.
6. Show other users with `deps.public_name(db, uid)`, never `username`.
7. Chatty writes: `deps.rate_limit(user, "<bucket>")` with a bucket added to `WriteLimiter.LIMITS`.
8. New tables: `CORE_SCHEMA` or `DB.add_schema(...)`; new columns: `MIGRATIONS`.
9. Add tests (role matrix case in `test_roles.py`), then regenerate the schema snapshot:
   `DATA=$(mktemp -d) python -c "import json,server.main as m; json.dump(m.app.openapi(), open('server/openapi.json','w'), indent=1)"`.
10. Web side: `CS.app(method, "path", body)` (relative to `app/v1/`), `CS.can("role")`.

---

## Docs vs code

Where the code and existing docs disagree (the code is what runs):

1. **Passwordless default.** `server/README.md` and `deploy/README.md` say `passwordless_login` defaults to `lan`
   and describe LAN-only no-password sign-in as the normal mode. Code: default is **`never`**
   (`deps.passwordless_policy`), and the Admin UI (`web/audit.js`) offers only `never` ("Off") and `everywhere`
   ("On (testing only)"), per commit 52e9da6. `lan` is still accepted by the API and used by the test fixture.
   The `SecurityOut.passwordless_login` field description in `routers/admin.py` still calls `lan` the default.
2. **"Every request body rejects unknown fields (422)"** (`server/README.md`). True for most models, but not for
   `LoginIn`, the sources models (`SourceIn`, `SourcePatch`, `Region`, `LayoutIn`, `FitIn`) or the free-form
   `dict` bodies of `sources/{sid}/frame`, `layout/{sid}/suggest` and `calibration/{target}/detect`, which
   ignore unknown keys.
3. **`X-CS-Role`.** Documented as part of the nginx gate; the API sets it but nginx never reads it, so `/api/*` and
   `/media/*` are gated by "signed in" only. No role-based file gating exists.
4. *(Fixed in the cleanup pass.)* `server/openapi.json` was stale (it lacked the `/admin/update` endpoints); it was
   regenerated from `create_app().openapi()`.
5. **Env vars.** `server/README.md` lists `SETUP_CODE_REQUIRED`, `SESSION_IDLE_DAYS`, `SESSION_MAX_DAYS`,
   `PASSWORD_MIN`, `MEMBER_PASSWORD_MIN`, `TBA_BASE`, `TBA_KEY`, `EVENTS` as API settings, and
   `deploy/README.md` says the api imports `TBA_KEY` / `EVENTS` from `.env` on first start. The shipped compose
   gives the api no `env_file`; since the cleanup pass it passes the account settings (`SETUP_CODE_REQUIRED`,
   `SESSION_*`, `PASSWORD_MIN`, `MEMBER_PASSWORD_MIN`) as an allowlist but still not `TBA_BASE` / `TBA_KEY` /
   `EVENTS`. The `.env` import upgrade path is therefore inactive on purpose (the worker still falls back to `.env`
   `TBA_KEY` / `EVENTS` itself through `load_config()`).
6. **Roles matrix "video sources / layout / calibration (reads: per B)".** Concretely: viewer+ can read
   `/sources`, `/layout`, `/calibration` (status) and `/livestream`; frames, wizard state and job status are
   admin-only.
7. **Sources changes aren't audited explicitly.** The README tells router authors to call `audit.record`;
   `routers/sources.py` doesn't, so source / layout / calibration edits appear only as non-revertible
   `http.post|put|patch|delete` entries with the URL path, and can't be undone from the audit page.
8. **`add_schema()`** is described (db.py docstring, README) as how the sources router adds tables. No router calls
   it; sources, layouts and calibrations live in JSON files under `DATA/events/<ev>/`.
9. **"passwords are argon2id hashes, 10+ characters"** (`deploy/README.md` security notes) applies to admins
   only; other roles may have no password or 4+ characters.
10. **"the login rate limit is per client IP, which behind a proxy is the proxy's"** (`deploy/README.md`) is
    outdated: nginx's realip config trusts `X-Forwarded-For` from the docker gateway and loopback, so behind the
    host-side Cloudflare tunnel the limit keys on the real client address (`test_login_rate_limit_keys_on_real_client_ip`).
11. **Picklist weights** accept -10..10 (`PicklistData`); the security report's suggested fix said 0..5. Not a bug,
    just different from the recommendation.
12. **`manage.py`** isn't mentioned as bypassing the password policy and the audit log; it does both (section 10).
13. **`CS_URL_ALLOW` for pasted links.** Fixed: compose passes `CS_URL_ALLOW` and `CS_PROBE_LINKS` to the api, where
    the pasted-link check runs.
14. **Worker "stale" during a long pass.** Fixed: a `status-beat` thread in `live/worker.py` re-writes
    `worker-status.json` every `BEAT_S` (120 s) with the last reason, so a long pass stays fresh.
