# The web app (`web/`) and the design lab (`lab/`)

Describes the committed state at `cc6297c` (2026-10-12). Code wins over every older doc. File:line references
are to that commit (`git show HEAD:web/<file>`).

> **In progress at time of writing.** Other agents were editing `web/app.js` (navigation for the competitions work,
> fetch/caching code for the performance work), `web/nginx.conf`, `web/Dockerfile` and `live/sitev2.py` while this
> was written. The router, nav registry, `api()` fetch helper, cache headers and cache-busting described below are
> the committed versions and may have changed since. `lab/ext/d.js` had an uncommitted local edit: the lab name of
> theme `jetdark` was changed from "Jet" to "Jet (dark only)" (lab panel only; the site name comes from
> `lab/tools/site-themes.mjs`).

Contents

1. [What the SPA is](#1-what-the-spa-is)
2. [How it is served](#2-how-it-is-served-nginx-csp-caching-cache-busting)
3. [Boot order](#3-boot-order)
4. [`app.js`: router, shell, shared helpers](#4-appjs-router-shell-shared-helpers)
5. [`setup.js`: session, `CS.app`, sign-in, roles, setup wizard](#5-setupjs-session-csapp-sign-in-roles-setup-wizard)
6. [Every page file](#6-every-page-file)
7. [Site JSON and API endpoints the browser uses](#7-site-json-and-api-endpoints-the-browser-uses)
8. [Browser storage keys](#8-browser-storage-keys)
9. [Themes, appearance, layouts](#9-themes-appearance-layouts)
10. [The design lab (`lab/`)](#10-the-design-lab-lab)
11. [CSP rules for contributors](#11-csp-rules-for-contributors)
12. [Recipe: add a page](#12-recipe-add-a-page)
13. [Known inconsistencies and gaps](#13-known-inconsistencies-and-gaps)

---

## 1. What the SPA is

- Plain browser JavaScript, **no build step, no framework, no modules**. Every file is a classic `<script>` that
  shares one global scope. Pages render by assigning HTML strings to `#app.innerHTML`; user/data text goes through
  `esc()`.
- Hash routing: everything after `#/` is the route. No server-side routes besides nginx's `try_files … /index.html`.
- Two kinds of data:
  - **Site JSON** under `api/…` (static files written by the worker into `data/site/api/`, served by nginx behind a
    session check). Read with `api(path)`.
  - **Platform API** under `app/v1/…` (FastAPI, `server/`). Read/write with `CS.app(method, path, body)`.
- Also works as a "legacy static site" (no API: `GET app/v1/session` returns 404 or non-JSON). Then
  `CS.platform = false`, there is no sign-in, and shared state (picklist) falls back to `localStorage`. The design lab
  relies on this mode.

Files (committed sizes):

| file | lines | role |
|---|---:|---|
| `index.html` | 57 | shell markup: sidebar, toolbar, `#app`, phone tab bar, "More" sheet; script order |
| `theme.js` | 50 | **generated**; applies theme/appearance/layout before first paint; `window.CSStyle` |
| `fonts.css` | 5 (65 KB) | Archivo variable font inlined as a base64 `data:` URL (works offline on venue Wi-Fi) |
| `style.css` | 619 | base tokens (type scale, spacing, radii, layout sizes) and every component |
| `themes.css` | 2192 | **generated**; 18 themes' colour tokens + fonts + per-theme rules + Top bar / Command layouts |
| `fonts/*.woff2` | 16 files | self-hosted theme fonts (copied from `lab/fonts/` by `site-themes.mjs`) |
| `field.jpg` | 118 KB | top-down field image used by every canvas (`FIELD`) |
| `quant.js` | 270 | chart helpers (shot chart, density, paths, bars, sparklines) + the team page |
| `event.js` | 215 | Teams table, Compare, Picklist |
| `player.js` | 851 | match page / Analyze viewer, livestream-only match page |
| `dash.js` | 563 | strategy Dashboard |
| `app.js` | 498 | data contract, plugin registry, shared helpers, shell/nav, time control, router |
| `setup.js` | 395 | session, `CS.app`, roles, login, account, TBA event picker, first-run wizard |
| `admin.js` | 240 | Admin: Teammates, Settings, Events, Updates |
| `audit.js` | 130 | Activity (audit log, lockdown, sign-in policy) and Identity-fixes queue |
| `notes.js` | ~300 | (cleanup pass) `CS.notes` (Notes card on the team and match pages) and `CS.idFix` ("Wrong team?" identity-fix form on the match page) |
| `sources.js` | 649 | Video setup: sources, layout box editor, calibration wizard; livestream recorder status |
| `rankings.js` | 227 | Rankings & season goal; Selection tab |
| `schedule.js` | 151 | Schedule with per-period breakdown |
| `teams2.js` | 304 | Spec sheets (list + per team) |
| `defense.js` | 415 | Defense library with top-down clips |
| `settings.js` | 69 | per-browser look: theme / appearance / layout |
| `nginx.conf` | 161 | the web container's nginx (see `deploy.md` for the non-SPA parts) |
| `Dockerfile` | 9 | `nginx:1.29-alpine` image, cache-bust stamp |

---

## 2. How it is served (nginx, CSP, caching, cache-busting)

`web/Dockerfile` copies `web/*.html web/*.js web/*.css web/field.jpg` and `web/fonts/` into
`/usr/share/nginx/html/`, and `web/nginx.conf` into `/etc/nginx/conf.d/default.conf`. Compose mounts
`./data/site/api` read-only at `/usr/share/nginx/html/api` and `${MEDIA_DIR}` read-only at
`/usr/share/nginx/html/media`. So the URL `api/2026wasam/event.json` is the file
`data/site/api/2026wasam/event.json`.

**Cache-busting** (Dockerfile `RUN`, at image build time): `v = md5(cat *.js *.css)[:10]`, then `sed` rewrites every
`src="x.js"` / `href="x.css"` in `index.html` to `x.js?v=<v>`, and `grep -c` fails the build if nothing was
stamped. Only `index.html` is rewritten; fonts (`themes.css` → `fonts/*.woff2`), `field.jpg` and JSON are not
versioned. Purpose (comment in the Dockerfile): after a deploy no browser or CDN edge (Cloudflare tunnel) can mix an
old cached script with new ones. The source `index.html` in git has no `?v=`; the lab and a plain `python -m
http.server` of `web/` work without it.

**Locations relevant to the SPA** (`nginx.conf`):

| location | access | cache | headers |
|---|---|---|---|
| `/` (shell: html/js/css/fonts) | public | `Cache-Control: no-cache` (revalidate every time) | full page CSP `$csp`, `X-Frame-Options SAMEORIGIN`, Permissions-Policy, HSTS when https |
| `/api/` (site JSON) | `auth_request /_auth` → `GET /app/v1/auth/check`; 401 → JSON `{"detail":"sign in required"}` | `no-cache` | `$csp_data` (`default-src 'none'`) |
| `/media/` (videos, clips) | same auth_request | `private, max-age=86400` | `$csp_data` |
| `/app/v1/` | proxied to `api:8000` | (API's own) | `default-src 'none'; frame-ancestors 'none'`, `X-Frame-Options DENY` |
| `= /app/v1/auth/login` | as above + `limit_req` 10/min per client IP (burst 10) | | |

`try_files $uri /index.html` makes any unknown path return the shell. Dotfiles and `*.db|*.bak|*.env|*.pem|…` return
404 everywhere. `api()` additionally fetches with `{cache: "no-cache"}`.

**The page CSP** (`$csp`):

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob: https://i.ytimg.com https://img.youtube.com;
media-src 'self' blob:; connect-src 'self'; frame-src https://www.youtube.com https://www.youtube-nocookie.com;
worker-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'
```

Consequences are in [§11](#11-csp-rules-for-contributors).

---

## 3. Boot order

`index.html` `<head>`:

1. `theme.js` (synchronous, before any CSS paints): reads `localStorage` `cs:style` / `cs:mode` / `cs:layout`, sets
   `<html data-style data-mode data-layout>`, rewrites the `color-scheme` and both `theme-color` metas. Defines
   `window.CSStyle`. An external file because the CSP forbids inline scripts.
2. `fonts.css` (Archivo, inline `data:`), `style.css` (tokens + components), `themes.css` (theme tokens/rules +
   layouts; later in the cascade, so themes override base).

End of `<body>`, in this order (order matters, see below):

```
quant.js  event.js  player.js  dash.js  app.js  setup.js  admin.js  audit.js  notes.js
sources.js  rankings.js  schedule.js  teams2.js  defense.js  settings.js
```

- `quant.js`, `event.js`, `player.js`, `dash.js` load **before** `app.js`. They only declare functions
  (`teamPage`, `teamsTable`, `comparePane`, `picklistPane`, `analyzePage`, `window.dashPage`, chart helpers) that
  use `app.js` globals (`api`, `esc`, `crumbs`, `$app`, `cleanup`, …) at call time, never at load time.
- `app.js` creates `window.CS` (`routes`, `globalRoutes`, `navItems`) and the core nav items, defines the router and
  registers `hashchange` and `load` listeners. The first `route()` runs on `window.load` (or immediately if the
  document is already complete), i.e. after **all** scripts have loaded and registered.
- `setup.js` **wraps three app.js globals** by reassigning them (they are function declarations, so the global
  bindings are writable):
  - `route` → first `CS.refreshSession()`, then enforce setup/login redirects, then call the original router;
  - `renderTopNav` → temporarily filters `CS.navItems` by role (`CS.navAllowed`) and renders the user box;
  - `api` → on a 401 from site JSON, refresh the session and go to `#/login`.
  Because app.js's listeners call `route()` by name, the wrapped version is what runs.
- The remaining files are self-contained IIFE plugins that call `CS.route` / `CS.globalRoute` / `CS.nav`.
  `settings.js` needs `window.CSStyle` from `theme.js`.

Page plugins that need extra CSS inject it at runtime: `setup.js` (a `<style>` with form styles), `dash.js`
(`injectStyle()`), `defense.js` (`#dx-css`), `rankings.js`, `schedule.js`, `teams2.js` (`css()`), `sources.js`
(`<style>${CSS}</style>` inside `#app`). This is allowed by `style-src 'unsafe-inline'`.

---

## 4. `app.js`: router, shell, shared helpers

### 4.1 Data contract comment

Lines 9–48 document the site JSON shape the UI expects (v2 additions on top of v1):
`m/<match>.json` (`shots[]`, `intakes[]`, `analytics[track_idx]`, optional `periods[]`), `t/<team>.json`
(`analytics` aggregate with `{v,n,conf}` leaves, `matches[].analytics`, `shots_map[]`, `intakes_map[]`, `routines[]`),
identity fields (`tracks[].team/team_conf/conf[]`, `identity.corrections[]`, quarantined shots
`team:null, unattributed:"low_identity_conf"`), and `event.json teams[].q` (pre-summarised team row). Units: cm for
`xy` in track/team files, metres for shots/intakes. "Everything v2 is optional: missing fields simply hide the
widget." The authoritative producer is `live/sitev2.py` + `live/scouting.py` (see `pipeline.md`).

### 4.2 The plugin registry

```js
window.CS = window.CS || { routes: {}, globalRoutes: {}, navItems: [] };
CS.route(name, fn)        // #/<ev>/<name>/...  -> fn(ev, ...rest)
CS.globalRoute(name, fn)  // #/<name>/...       -> fn(...rest)
CS.nav(item)              // sidebar / tab bar / More sheet entry
```

Nav item fields: `label`, `key` (matches `activeKey`), `href(ev, ts)`, `order` (sort; default 50), `group`
(`day` "Match day" | `picks` "Picks" | `scout` "Scouting" | `manage` "Manage"; default `manage` for global items,
else `scout`), `icon` (a key of `ICON`), `tab` (also on the phone tab bar), `replay` (href receives the shared as-of
time), `global` (shown even without an event), `roles: [...]` or `minRole` (filtered by `setup.js`).

Registered items (committed):

| order | label | key / route | group | flags | file |
|---:|---|---|---|---|---|
| 10 | Dashboard | `#/<ev>/dash[/<ts>]` | day | tab, replay | app.js |
| 20 | Schedule | `#/<ev>/schedule` | day | tab | schedule.js |
| 30 | Rankings | `#/<ev>/rankings` | day | tab | rankings.js |
| 40 | Selection | `#/<ev>/selection[/<ts>]` | picks | replay | rankings.js |
| 42 | Picklist | `#/<ev>/picklist[/<ts>]` | picks | replay | app.js (pane in event.js) |
| 50 | Teams | `#/<ev>/teams` | scout | tab | app.js (pane in event.js) |
| 55 | Compare | `#/<ev>/compare` | scout | | app.js (pane in event.js) |
| 60 | Matches | `#/<ev>/matches` | scout | | app.js |
| 65 | Specs | `#/<ev>/specs` | scout | | teams2.js |
| 70 | Defense | `#/<ev>/defense` | scout | | defense.js |
| 80 | Video | `#/<ev>/sources` | manage | roles admin | sources.js |
| 94 | Identity fixes | `#/<ev>/fixes` (old `#/fixes` redirects to the current competition's) | manage | minRole strategist | audit.js |
| 95 | Admin | `#/admin` | manage | global, roles admin | admin.js |
| 96 | Activity | `#/activity` | manage | global, roles admin | audit.js |
| 99 | Settings | `#/settings` | manage | global | settings.js |

"My account" (`#/account`) is not a nav item; it is the user box link and an extra group in the phone More sheet.

### 4.3 Route table

`route(scroll = true)` (app.js ~474) runs every `cleanup` function from the previous page, splits the hash into
`p[]`, calls `renderTopNav(p)`, then dispatches:

| hash | handler |
|---|---|
| `#/<name>/…` where `<name>` is a global route | `CS.globalRoutes[name](...rest)` — `login`, `setup`, `account`, `admin`, `activity`, `fixes`, `settings` |
| `#/` (empty) | `home()`: `api/events.json`; one event → redirect to it, else event cards |
| `#/<ev>/<name>/…` where `<name>` is a plugin route | `CS.routes[name](ev, ...rest)` — `rankings`, `selection`, `schedule`, `specs`, `defense`, `sources` |
| `#/<ev>/dash[/<ts\|live>[/<plan>]]` | `dashPage(ev, ts)` (dash.js) |
| `#/<ev>` | `dashboard.json` exists → `dashPage`, 404 → `eventPage(ev)` (Teams) |
| `#/<ev>/m/<match>` and `#/<ev>/a/<match>[?t=<video s>]` | `analyzePage` (player.js); `m` is an alias kept for old links |
| `#/<ev>/t/<team>` | `teamPage` (quant.js) |
| `#/<ev>/teams\|compare[/<a,b,c>]\|picklist[/<ts>]\|matches` | `eventPage(ev, tab, arg)` |
| anything else under an event | `eventPage` → falls back to Teams |

Errors in a handler render a "Couldn't load this page … Try again" card (`#routeretry` re-runs `route(false)`).

**Route validation** (cleanup pass, security audit): before any handler runs, `routeOk(p)` (also `CS.routeOk`)
checks every hash segment once: each matches `^[A-Za-z0-9_.,=?&%:-]{1,160}$`; the first is a global route or an
event key (`EVK`); and the argument shapes of the core routes: `m`/`a` match key `^[A-Za-z0-9_]{1,40}$` (before
`?t=`), `t`/`specs` team `^frc\d{1,5}$`, `defense` team or `defenders`, `dash` `live` or a 9–12 digit ts (+ plan
key), `selection`/`picklist` ts, `schedule` match key, `compare` up to three team numbers. Anything else shows
"Page not found" without calling a page, so no unvalidated route parameter reaches an `href` or `innerHTML`. A new
route with arguments should add its shape to `routeOk`.

`cleanup` is a global array: pages push teardown functions (intervals, observers, global listeners). Anything a page
adds to `window`/`document` must be removed through it.

Polling: `eventPage` re-fetches `event.json` every 60 s and re-routes when `updated` changes; Dashboard every 60 s
(compares `event` block and next match); Rankings and Schedule every 90 s (`generated`).

### 4.4 Shell and navigation

- `renderTopNav(p)` builds the sidebar (`#topnav`, grouped), the phone tab bar (`#tabbar`: every `tab:true` item +
  "More"), and the More `<dialog>` (all groups + Account). The current item gets `aria-current="page"` via
  `activeKey(p)` (team page → `teams`, match pages → `matches`, `dash` default) and `CS.markActive(key)`.
- The event used for nav links: the hash's first segment if it matches `^\d{4}[a-z0-9]{1,16}$`, else
  `localStorage cs:lastev` (same check), else the first entry of `api/events.json`. The regex check is a security
  fix (fragment XSS into nav hrefs; see `security.md`).
- `body.bare` is set when the platform is present but nobody is signed in (sidebar/tab bar/time control hidden).
- Sidebar toggle `#sbtoggle` → `body.sb-off`, remembered in `localStorage cs:sboff`.
- `crumbs(parts)` sets the toolbar title (last crumb = page title, earlier ones = back links) and `document.title`.

### 4.5 The time control (replay)

`renderTimebar(p, ev)` puts one control in the toolbar for every event page:

- Pages that can replay are those whose nav item has `replay:true`: Dashboard (`#/<ev>/dash/<ts>`), Selection
  (`#/<ev>/selection/<ts>`), Picklist (`#/<ev>/picklist/<ts>`, ts must be ≥ 9 digits). `urlTs(p)` reads the ts.
- Other event pages show a static "Latest data" tag (end-of-event data).
- Snapshot list: `api/<ev>/dash/index.json` (`[{ts,label,quals_played}]`), falling back to
  `selection/index.json` (`{times:[…]}`).
- The chosen ts is shared across replay pages via `sessionStorage cs:asof:<ev>` (`CS.asofGet/CS.asofSet`), and nav
  hrefs of replay items carry it.

### 4.6 Shared helpers (globals defined in app.js)

| helper | what |
|---|---|
| `esc(s)` | HTML-escape `& < > " '`. Use for every data/user string put into HTML. |
| `api(p)` | `fetch("api/" + p, {cache:"no-cache"})`, throws `Error("<p>: <status>")`; wrapped by setup.js for 401 |
| `num`, `pct`, `fx` | `frc4911`→`4911`; 0.42→`42%`; fixed decimals, `–` for null |
| `confBar(c)`, `idPill(c, n)` | identity-confidence bar / pill (tiers `IDC`: ≥0.8 good, ≥0.5 warn) |
| `scoutData(ev)` | cached `api/<ev>/scout.json` with `.by[teamKey]` index (the canonical per-team numbers) |
| `covPill(c)`, `svHtml(m)`, `flagChips(row)`, `dataBanner(sd)` | render scout.json coverage tiers, canonical metrics `{v,raw,n,tier,lower_bound,over,gates}`, flags, and the "End-of-event data" banner |
| `shortKey(k)` | `2026wasam_qm12` → `Q12`, `sf1m2` → `SF1-2` |
| `crumbs`, `ago(ts)` | toolbar title; "updated N min ago" |
| `fieldCanvas(el)` | HiDPI field canvas: `{c, ctx, P(x,y), S(m), bg(alpha), size}`; broadcast orientation (red end left) |
| `CS.help(html, label)` | "?" button + popover (`.hlp`), inline content only; one open at a time; Esc/outside click closes |
| `CS.fit()` / table fitter | every `.tablewrap > table` with a `<thead>` is fitted to its width: low-priority columns fold into a per-row "More" disclosure. Priority via `<th data-pri="N">` (0 = always folded; default keeps the first two columns). Re-fits via MutationObserver, resize and `<details>` toggle. Tables never scroll sideways. |
| `CS.icon(key, cls)` | inline SVG from the `ICON` set (24-grid, 1.8 stroke): nav icons plus `star`, `starOn`, `external`, `chevL/R`, `close`, `up`, `warn`, `check`, `ban` |
| `CS.isStream(m)`, `CS.liveTag(m)` | match row has `score_source:"livestream"` → "LIVE · stream" tag |
| `CS.liveTracking(live, tracked)` | status text for a stream result ("Tracking from stream" / "Waiting for uploads") |
| `CS.mediaUrl(u)` | accepts only `media/...` paths (`^/?media/[\w\-./]+$`, no `..`), else null |
| `CS.safeHref(u)` | only `http(s)://` URLs become links (security audit) |
| `CS.asofGet/Set`, `CS.markActive`, `CS.fit` | see above |
| constants | `LEN=16.541`, `WID=8.069`, `FIELD` (field.jpg `Image`), `COLORS` (3 shades per alliance), `PALETTE` |

---

## 5. `setup.js`: session, `CS.app`, sign-in, roles, setup wizard

Exports: `CS.app`, `CS.can`, `CS.user`, `CS.session`, `CS.platform`, `CS.refreshSession`, `CS.navAllowed`,
`CS.form`, `CS.genPassword`, `CS.eventPicker`, `CS.ROLES`, `CS.ROLE_TIP`, `CS.GOALS`.

**`CS.app(method, path, body)`** → `fetch("app/v1/" + path)` with `credentials: same-origin`, `cache: no-store`,
JSON body, and for every non-GET the header `X-CSRF-Token` = cookie `cs_csrf` (fallback `CS.session.csrf`). Returns
parsed JSON (null for 204). Errors throw `Error(message)` with `.status` and `.data`; FastAPI `detail` strings, 422
lists and `{message}` objects are turned into readable text. A 401 while signed in clears `CS.user` and goes to the
login page.

**Session**: `CS.refreshSession()` → `GET app/v1/session`. 404 or non-JSON → `CS.platform=false` (static site).
Network error / 5xx → "Can't reach the Cyber-Scout server" card with retry. Otherwise `CS.session` =
`{setup_required, setup_code_required, user, csrf, configured, lockdown, network, passwordless_ok}`.

**Route guard** (wrapped `route`): setup required → `#/setup`; not signed in → remember the hash in
`sessionStorage cs:next` and go to `#/login`; signed in on `#/login` → back to `cs:next` (never back to login/setup);
an admin with `configured:false` landing on `#/` → `#/setup`.

**Roles**: `viewer < scout < strategist < admin`. `CS.can(role)` is true when the user's role is at least `role`
(always true in static mode). Nav items are filtered by `roles` (exact membership) or `minRole`. Pages also check
`CS.can` themselves; the server enforces everything regardless (UI checks are cosmetic).

**Pages**:

- `#/login`: username + optional password (blank = passwordless, if the server policy allows it) →
  `POST auth/login`.
- `#/account`: name/role, change password (`POST auth/password`; admins 10+ chars required, members optional 4+).
- `#/setup[/<step>]`: first-run wizard. Steps `account` (only while `setup_required`: setup code from
  `data/setup-code.txt`, username, display name, 10+ char password → `POST setup`), `tba` (`PUT settings/tba-key`),
  `team` (`PATCH settings {team_number}`), `goal` (`PATCH settings {season_goal}`; choices Win an event / DCMP /
  Worlds / Einstein / Scouting / Other), `event` (`CS.eventPicker`), `video` (lists `GET events/<ev>/sources` per
  followed event, links to `#/<ev>/sources`), `done` (sets `setup_complete: true`). Until `setup_complete`, the
  wizard walks every step in order even when a value exists (e.g. imported from an old `.env`).
- `CS.eventPicker(el, opts)`: `GET tba/events?year=` + `GET events`; your team's events first, search, year select,
  Follow (`POST events {key}`) / Unfollow (`DELETE events/<key>`), "or an event key". Used by the wizard and
  Admin → Events.

The user box (`#userbox`) shows "name · role" (link to `#/account`) and Sign out (`POST auth/logout`).

---

## 6. Every page file

### `quant.js` — chart helpers + team page (`#/<ev>/t/<team>`)

Helpers (globals, also used by event.js/teams2.js): `val/mn/mc` (read `{v,n,conf}` metrics), `has`, `fmtV`,
`lsGet/lsSet` (JSON localStorage), `getTeam(ev, tk)` (cached `t/<team>.json`), `tile`, `spark`, `bars`,
`periodBars`, `drawHubMark`, `densityLayer`, `drawShotChart`, `drawIntakeMap`, `drawPath`, `liveCanvas` (field
canvas that redraws on resize), `chipRow` (single-select chips), `section`. Constants: `PERIODS`, `PLABEL`, `HUBS`
(`red [11.915, 4.035]`, `blue [4.626, 4.035]`), `C_MADE/C_MISS/C_PASS`.

`teamPage` fetches `t/<team>.json`, `event.json`, `scout.json`. Renders: header with rank/record and a picklist
star (writes through `plLoad/plSave` from event.js, i.e. the shared server picklist), **Scouting truth** (the
canonical scout.json values, coverage, flags, trend), then sections Scoring (tiles, by-period bars), Shot chart
(chips: All/Auto/Teleop × Points+density/Points/Density), Intake map, Autonomous (routine paths, chips per routine,
"show all auto paths"), Movement (tiles, zone time, mid-field crossings by lane, teleop heatmap), Defense, Endgame,
Reliability & role, Matches table, and (cleanup pass) a **Notes** card under Scouting truth (`CS.notes`, see
`notes.js` below). When the team file mixes unverified matches (`tainted`), the per-team aggregate
Scoring section is replaced by a note pointing at Scouting truth.

### `event.js` — Teams, Compare, Picklist (panes of `eventPage`)

- **Teams** (`teamsTable`): one row per team from `event.json teams[]` joined with `scout.json` (`scoutRows`).
  Columns `QCOLS`: Blend ± sd, EPA, Video coverage, Made/m, Acc, Auto, Cycle s, Volley p90, Fire /s, Climb (TBA),
  Dead (only n ≥ 3), p95 m/s, Def. played, plus Rank / Team / Name / OPR. Sortable headers; a column picker
  (visibility in `localStorage cyberscout:cols2`); fold priorities `TPRI`.
- **Compare** (`#/<ev>/compare[/<t1,t2,t3>]`): up to 3 teams (selection remembered in
  `localStorage cyberscout:cmp:<ev>`); canonical rows + shot chart + auto paths, **verified matches only**.
- **Picklist** (`#/<ev>/picklist[/<ts>]`): shared server picklist `main`
  (`GET/PUT app/v1/events/<ev>/picklists/main`, data `{stars:[teamkey], dnp:[teamkey], weights:{metric:w}}`).
  Saves are debounced 500 ms and send `base_version`; a 409 replaces the local state with the server's
  (`detail.current`) and re-renders. Only strategist+ saves to the server; everyone keeps a copy in
  `localStorage cyberscout:picks:<ev>` (the team page and Teams table read stars from there). Sort modes Blend / EPA /
  OPR / Custom (`localStorage cyberscout:plmode`); Custom = weighted mean of event z-scores over `PL_KEYS`
  (blend, video made/m, acc, auto, cycle ↓, climb (disabled when `scout.json event_stats.climb_matters` is false),
  defense matches), weights 0–5 sliders, default `{blend: 4}`. Filters All / Starred / Hide do-not-pick. "Copy list"
  copies a numbered text list. Weights are coerced on load and on render (`plWeights` / `plWeight`: known keys only,
  `Number()` clamped to 0–5) so a stored value can never reach `value=""` or the shown number as markup (security
  audit finding 1; the server schema also rejects it). In replay (`/<ts>`) it reads `scout/<ts>.json` and uses that snapshot's rank/OPR.

### `player.js` — match page / Analyze (`#/<ev>/m/<match>`, `#/<ev>/a/<match>[?t=<s>]`)

Data: `m/<match>.json` (+ `m/<fuel_url>` = `<match>.fuel.json` on demand for the raw fuel layer), `event.json`.
Everything is drawn client-side on canvases over the real video; nothing is baked in.

- **Camera**: `makeCamera(d.camera)` re-implements OpenCV `projectPoints` (Rodrigues rvec, K, k1 k2 p1 p2 k3) to
  project field-metre points into video pixels. Exported via `module.exports` when run under Node.
- **Shot arcs**: `shotPos(s, tau)` = the ballistic model from `live/shots.py` (gravity + linear drag `s.drag`,
  default 0.15/s, optional extra acceleration `s.acc`); arcs end at hub entry (`t_entry`) for made shots or at
  ground contact.
- **Timing**: replay frame `i` ↔ video time `t_start − 0.5 + i/hz`; match clock with the auto/teleop pause
  (`mt2vt`, `matchT`, `gameClock`); periods default to 0/20/30/55/80/105/130/160 s with shifts 1,3 red-active and 2,4
  blue-active unless `d.periods` is present.
- **View menu** (one popover, `#mvViewB`): modes **Video** (raw, camera radio Overhead / Red station / Blue station),
  **Field** (top-down replay on `field.jpg`) and **Overlay** (video with drawings, up to 3 panes, the first is large;
  add / remove / promote panes; layers per pane). Remembered in `localStorage pl.cfg`.
- **Layers** (`PL.layers`, key = keyboard shortcut): Robots `r`, Team numbers `l`, Trails `t`, Shot arcs `a`, Shot
  history `h`, Identity fixes `i`, Scoreboard `s`, Raw fuel tracks `f` (overhead only), Calibration grid `g`
  (overhead + stations). Labels: solid ≥ 0.8 identity confidence, "?" 0.5–0.8, "unknown" < 0.5.
- **Keyboard**: Space play/pause, ←/→ one frame (Shift: 5 s), `v` cycle mode, `[`/`]` data sync −/+ one frame
  (`localStorage pl.offset`), layer letters as above. Trail length 2–12 s (`pl.trailS`). Speed select.
- **Station cams**: when `d.stations.{video,cams}` exist, a second hidden `<video id="anIns">` plays the full inset
  upload; each station pane is a crop. `insOff()` maps overhead→inset time piecewise-linearly over
  `d.stations.sync.knots` (or a constant offset); the inset is re-seeked when drift > 1.2 frames (playing) or 0.4
  frames (paused). If there are no station cams the menu says so ("Overhead only · no station cams").
- **Draw loop**: `requestVideoFrameCallback` when available (frame-accurate), else `requestAnimationFrame`; with no
  video, an internal clock drives the field replay. A video that fails to load (`MEDIA_ERR_SRC_NOT_SUPPORTED`)
  falls back to Field mode.
- **Side panel**: scoreboard, per-team tallies (made/attempts auto/teleop, unattributed), robots card, shot list
  (click seeks to 1 s before the shot), identity corrections (cuts/relinks with evidence; weak un-anchored relinks
  with conf < 0.2 hidden by default), timeline scrubber with hub-active bars, shot ticks and correction triangles.
- **Video source**: `d.video_local` (the kept upload under `media/…`), else — when the match was tracked from the
  livestream clip (`d.live.tracking === "from_stream"`) — the clip (`CS.mediaUrl(d.live.clip)`). A YouTube link
  (`https://youtu.be/<id>?t=<s>`) and a "Clip" link are offered in the header.
- **`liveMatchPage`**: if `m/<match>.json` is 404 but the `event.json` row has `live` data (the recorder cut the
  match from the livestream), shows the stream score, teams read off the overlay, and the clip with native controls,
  seeking to `live.auto_start_in_clip − 3 s`.
- `?t=<video s>` (used by the defense library) seeks there on load.
- (cleanup pass) **Wrong team?** in the Robots card header (and "Propose a fix" in the Identity section) opens the
  identity-fix form (`CS.idFix`), and a **Notes** card ends the page (`CS.notes`, match notes, optionally about one
  of the six teams). Both only for signed-in users; the form only for scout+.

### `dash.js` — Dashboard (`#/<ev>/dash[/<ts|live>[/<plan>]]`)

Contract: `live/strategy/DASHBOARD.md`. Loads `dashboard.json` (live) or `dash/<ts>.json` (replay); live view also
loads `event.json` for livestream results and `CS.liveRecorder` status. Order on screen:

1. **Match rail** (`rail`): our matches as one wrapping strip — played results as W/L pills (phone: last three, "+N
   earlier"), a "now/replay" marker, upcoming plans as buttons with win %, a "RP" badge in goal mode. Stream-scored
   matches TBA hasn't posted show as "LIVE · stream" pills.
2. **Queue card** (`queueCard`, one per plan, selected by the rail): header (label, "we are RED/BLUE", countdown or
   "after the earlier match", plan as-of stamp); us/them team chips (blend rating + coverage); headline = win %
   (model) beside EPA win %, "models disagree" chip, win bar, expected scores; or in **goal mode** the bonus-RP lever.
   Six lines: Auto (win-auto → our live shifts, swap if we lose it, TBA auto history, caution), Defend (who defends
   whom, when, where on a mini field seen from our driver station, FUEL denied, measured vs assumption), opposing
   defenders, RP odds us/them (ENERGIZED, SUPERCHARGED, TRAVERSAL only when shown), fouls. "Details" disclosure:
   every defense option, auto routines, TBA auto history, key factors.
3. **Our event** block, then disclosures (open state in `localStorage cs:dash:<id>`): Schedule and results,
   Levers (gap to event best, pit material), Ratings table (sortable), and the honest **model footer**
   (`model.backtest_asof`: accuracy and Brier vs EPA over posted quals).

Display policy: win % / bonus odds in 5 % steps via `wp5` ("<5%" / ">95%" at the ends) everywhere. Plan tabs are
linkable: `#/<ev>/dash/<ts|live>/<q51 | index>`. The live view refreshes every 60 s and re-routes when the
`event` block, the next match, or `event.json updated` changes. The perspective team is hard-coded
(`US = "frc4911"`, see §13).

### `rankings.js` — Rankings (`#/<ev>/rankings`) and Selection (`#/<ev>/selection[/<ts>]`)

- Rankings: `rankings.json` (contract `live/strategy/README.md`). Table of rank, RP, record, projected rank range
  (p10–p90), P(top 8), district points now / projected / P(DCMP); season-goal panel (odds with interval, points
  needed at p50/p80/p95, cutoff projection, DCMP capacity, value of each remaining match as Δ P(goal) for win vs
  loss, per-event outcomes, awards variants). Polls every 90 s.
- Selection: `selection.json` or `selection/<ts>.json` + `selection/index.json`. Shows our P(captain / R1 / R2 /
  unpicked) and P(DCMP), the "need" grid (alliance × role → least playoff result reaching the DCMP need), who
  is likely to pick us, **pitch cards** (per likely captain: what they lack, what we bring, re-sim Δwin / ΔP(DCMP),
  fit facts), the per-captain re-simulation table ("if this captain takes us vs their next best"), projected seeds,
  and draft odds per team. `status: done` shows the actual alliances instead.

### `schedule.js` — Schedule (`#/<ev>/schedule[/<match>]`)

`schedule.json` + `event.json` (livestream results). Filter Ours / All; our next match's row opens automatically.
Upcoming matches show predicted win %; our upcoming ones expand into a per-period table (auto, transition, shift
1–4, endgame: ours vs theirs, Δ, P(ahead), P(our hub active), if-won/lost-auto variants) plus the plain-English
"where you will be beaten" summary. Played rows link to the Analyze view. `#/<ev>/schedule/<matchkey>` scrolls to
that row. Polls every 90 s.

### `teams2.js` — Spec sheets (`#/<ev>/specs[/<team>]`)

`spec/index.json` (list) and `spec/<team>.json` (sheet), plus `event.json` and `scout.json`. List: sortable table of
made/match, accuracy, pts per active minute, cycle, fire rate, hopper, speed, trench share, left share, on-the-move
rate, climb (TBA), defense received/played, flags. Sheet sections: Drive, Scoring, Auto & climb, Lanes (trench /
bump bands, left/right from the driver's view), Shooting locations, Shooting paths (the drive into each volley),
Defense. Team frame: own alliance wall on the left (blue matches point-reflected), driver's left = top.

### `defense.js` — Defense library (`#/<ev>/defense[/<target team>]`)

`defense.json` (from `live/scouting.py`), `event.json`, `scout.json`. Built around "how do I defend team X?":
pick a target → its retention while defended (placebo-controlled, with interval and verdict), the best clips, and who
has defended it. Filters: target, defender, zone, shift, method, verdict; sort. Each episode is a **top-down clip**
drawn on a canvas (`clipPlayer`): defender and target circled, the episode window highlighted, shots drawn. Clip data
is embedded in `defense.json` or rebuilt on demand from `m/<match>.json` (`clipFromMatch`, same slice as
`live/scouting.py`). Clips animate only while visible (IntersectionObserver) and are stopped in `cleanup`. "Watch at
N s" links to `#/<ev>/a/<match>?t=<video s>`. Verdicts: clearly reduced / reduced, not clear / no effect / nothing at
stake / tracking unsure.

### `sources.js` — Video setup (`#/<ev>/sources[/sources|layout|calib][/<id>]`, admin)

Talks only to `server/routers/sources.py`. Has its own `A()` fetch helper (falls back when `CS.app` is missing).
Slow steps are **jobs**: the POST returns `{id, state}` and `job()` polls `GET events/<ev>/jobs/<id>` every 800 ms
until `done` / `error`. Frames are loaded as images from `app/v1/…/frame.jpg?ts=<now>`.

1. **Sources tab**: "Discover from The Blue Alliance" (`POST sources/discover`), table of sources (label, URL or
   title pattern, kind/provider/origin, camera role `wide | dynamic | alliance-side`, the **tracked** (primary)
   radio, coverage, layout link, enabled checkbox, remove), "Add a source" form (URL, role, label, tracked). URLs are
   only links when `CS.safeHref` accepts them. The **livestream recorder card** (`CS.liveRecorder.card`) sits here.
2. **Layout tab** (box editor): pick a source, "Grab frame" at time t (job), "Suggest boxes" (job; can compare with
   another source to find picture-in-picture rectangles), draw/move/resize boxes of kinds `field`, `overhead`,
   `station_red`, `station_blue`, `clock`, `scoreboard`, `mask`, `inset` (with magnifier lens), "Load 2026 FIRST
   defaults" (wide or program variant), Save (`PUT events/<ev>/layout/<source>` with `{W, H, regions[]}`). Warns on
   leaving with unsaved boxes.
3. **Calibration tab** (wizard) per target `overhead | station_red | station_blue`: 1 Detect tags (job: grabs a
   frame, finds AprilTags, may draw a first fit) → 2 click named field points / line samples (magnifier, drag to
   adjust, right-click removes; catalog from the server, defaults `DEFAULT_POINTS`/`DEFAULT_LINES` mirror
   `live/calibrate.py`) → 3 Fit (job; residual report + overlay of grid, field lines, hubs, tags) → 4 Accept & save
   (old file kept as backup). "Show the saved calibration" overlays the current file.

`CS.liveRecorder` (exported here, used by dash.js): `load(ev, evj)` (`GET events/<ev>/livestream`, else the
`livestream` summary in `event.json`, flagged stale after 10 min), `line()` (one quiet Dashboard line while
recording/waiting/error), `card()` (state, behind-live, analyser lag, last match + clip, tracking from stream or
uploads, disk, recent errors).

### `admin.js` — Admin (`#/admin[/users|settings|events|updates]`, admin)

- **Teammates**: `GET users`; create (`POST users`, generated passwords via `CS.genPassword`), change role,
  disable/enable, reset password, delete (`PATCH/DELETE users/<id>`). Shows "no password" accounts, last sign-in,
  active sessions; you cannot change your own role.
- **Settings**: team number + season goal (`PATCH settings`), TBA key (`PUT settings/tba-key`, shows only the hint),
  processing options (`PATCH settings {options: {poll_seconds, gpu_device, cpu_only, hubcam}}`; blank = `.env`
  default; applied by the worker on its next poll).
- **Events**: followed events (open / video setup / Unfollow) + `CS.eventPicker`.
- **Updates**: polls `GET admin/update` (every 2.5 s while busy, 3 s while the server is unreachable during a
  restart). Not installed → instructions to run `install-updater.sh`. Installed → current version (+ "local
  changes"), changelog, what restarts (worker heavy/light, api, web), disk needed vs free, buttons Check / Update now
  / Roll back to <prev> (`POST admin/update/check|apply|rollback`, apply body `{ytdlp}`), optional "also update
  yt-dlp", progress log, result, and "Reload the page" once the version changed. Notes `PAUSE_PROCESSING=1`.

### `audit.js` — Activity (`#/activity`, admin) and Identity fixes (`#/fixes`, strategist+)

- Activity: **Lockdown** toggle (`PATCH admin/security {lockdown}`), **Sign everyone out** (`POST
  admin/sessions/revoke {include_admins}`), sign-in policy form (`passwordless_login`: UI offers only `never` "Off"
  and `everywhere` "On (testing only)"; `scout_overrides`: review | direct), and the **audit log** (`GET audit?…`,
  filters by user / action family / via lan|public, 100 per page with "more"). Each row: time, who, action + target,
  before/after (truncated JSON), team net vs internet + IP, **Undo** (`POST audit/<id>/revert`) and **Undo all
  since** (`POST audit/revert-user {user_id, since}`).
- Identity fixes (`#/<ev>/fixes`, one queue per competition; other competitions' waiting counts link to theirs):
  `GET events/<ev>/overrides`; pending scout requests with requested vs current mapping (robots named as the match
  page shows them, from `m/<match>.json`; "changed" when stale) and Approve / Reject
  (`POST …/override-requests/<id>/approve|reject`). Requests come from the match page's "Wrong team?" form.

### `notes.js` — Notes and identity-fix proposals (cleanup pass)

Two widgets mounted by other pages; both need the platform and a signed-in user (static site / signed out: nothing
is shown), call only `CS.app` (CSRF), inject their CSS once (`#nt-css`, theme tokens only) and render every
user string with `esc()` (note text with `white-space: pre-wrap`, never as HTML).

- **`CS.notes.mount(el, ev, {team} | {match, teams})`**: `GET events/<ev>/notes?team=|match=` (newest first). Each
  note: author display name, relative time (full time in `title`), match chip on the team page / team chip on the
  match page, "edited (by X)" and, for removed notes, "hidden by X" / "deleted by its author". Scout+ get a
  textarea (`maxlength` 4000, live "n / 4000" counter, warn at 3800) → `POST events/<ev>/notes` (`{team}` on the team
  page; `{match, team?}` on the match page, "About: the whole match | <team>"), so a match note about a team also
  shows on that team's page. Own notes: Edit (`PATCH notes/<id>`), Delete (`DELETE`, soft, with an **Undo** =
  `POST notes/<id>/restore`). Strategist+: Hide on others' notes (`DELETE` → hidden), a "show hidden and deleted"
  toggle (`include_removed=true`) and Restore. Viewers get a read-only line.
- **`CS.idFix.mount(el, ev, match, tracks, teams)`** (match page): a select per robot listing its alliance's TBA
  teams; picking a team that another robot of the alliance has swaps the two (the worker applies fixes the same
  way). Submit merges the changed robots into the match's current entry from `GET events/<ev>/overrides` and sends
  `PUT events/<ev>/overrides/<match>` (`{red: {<track idx>: team}, blue: {...}}`, track idx = `tracks[].idx`).
  The button says **Send for review** for a scout in `review` mode (202 → "Sent for review", the request appears
  in `#/<ev>/fixes`) and **Apply fix** for strategist+ or `direct` mode (200 → applied on the worker's next pass).
  It also lists the applied fixes (Clear / Ask to clear = `DELETE`) and this match's pending requests (Approve /
  Reject for strategist+, Withdraw for your own). Robots are named the way the page shows them ("robot shown as
  9442 → 1318"); the queue page uses the same wording from `m/<match>.json`.
- Errors: 423 → "Read-only right now: an admin turned on lockdown…", 429 → "Slow down… try again in N s" (or the
  server's "too many fixes waiting" text), 403 → "Your role can't do that." (`CS.writeErr`). With
  `CS.session.lockdown` set, non-admins see the forms replaced by a read-only line.

### `settings.js` — Settings (`#/settings`, everyone)

Theme grid (18 themes, light/dark swatch previews built from `CSStyle.themes[].sw`), Appearance (Auto / Light /
Dark; locked for single-appearance themes), Layout (Sidebar / Top bar / Command), Reset to default. Stored per
browser only (`CSStyle.set` → localStorage). Redraws on `cs:style` events (another tab changed the look) unless
focus is inside the page.

---

## 7. Site JSON and API endpoints the browser uses

Site JSON (`api/…`, i.e. `data/site/api/…`, written by the worker):

| path | read by |
|---|---|
| `events.json` | home, nav (event name, default event) |
| `<ev>/event.json` | event pages, match pages, dashboard (live rows), schedule, defense, specs, team page |
| `<ev>/m/<match>.json`, `<ev>/m/<match>.fuel.json` (`fuel_url`) | player.js, defense.js clips |
| `<ev>/t/<team>.json` | team page, Compare, Teams (when `teams[].q` missing) |
| `<ev>/scout.json`, `<ev>/scout/<ts>.json` | every page with team numbers (`scoutData`), picklist replay |
| `<ev>/dashboard.json`, `<ev>/dash/<ts>.json`, `<ev>/dash/index.json` | Dashboard, time control |
| `<ev>/rankings.json` | Rankings |
| `<ev>/schedule.json` | Schedule |
| `<ev>/selection.json`, `<ev>/selection/<ts>.json`, `<ev>/selection/index.json` | Selection, time-control fallback |
| `<ev>/spec/index.json`, `<ev>/spec/<team>.json` | Specs |
| `<ev>/defense.json` | Defense |

Written but **not read by the UI**: `dashboard.<team>.json` (dash.js always loads `dashboard.json`),
`preview/<match>.json` (the "Full preview" button only links to the match page).

Platform API (`app/v1/…`), by file: setup.js `session`, `setup`, `auth/login|logout|password`, `settings`,
`settings/tba-key`, `events`, `tba/events`, `events/<ev>/sources`; admin.js `users[/<id>]`, `settings`,
`settings/tba-key`, `events[/<key>]`, `admin/update[/check|apply|rollback]`; audit.js `admin/security`,
`admin/sessions/revoke`, `audit`, `audit/<id>/revert`, `audit/revert-user`, `events/<ev>/overrides`,
`events/<ev>/override-requests/<id>/approve|reject`; notes.js `events/<ev>/notes`, `notes/<id>`,
`notes/<id>/restore`, `events/<ev>/overrides[/<match>]`, `events/<ev>/override-requests/<id>/approve|reject`;
event.js `events/<ev>/picklists/main`; sources.js
`events/<ev>/sources[/discover|/<id>|/<id>/frame|/<id>/frame.jpg]`, `events/<ev>/layout[/<id>[/suggest]]`,
`events/<ev>/calibration[/<target>/wizard|detect|fit|accept|overlay|frame.jpg]`, `events/<ev>/jobs/<id>`,
`events/<ev>/livestream`. Full list and roles: `server.md`.

---

## 8. Browser storage keys

All access is wrapped in try/catch (private windows / blocked storage just lose the preference).

| key | storage | what |
|---|---|---|
| `cs:style`, `cs:mode`, `cs:layout` | local | look (theme.js); removed when equal to the default |
| `cs:lastev` | local | last event opened (nav default) |
| `cs:sboff` | local | sidebar hidden |
| `cs:dash:<section>` | local | Dashboard disclosure open |
| `cs:asof:<ev>` | session | shared replay time |
| `cs:next` | session | where to go after sign-in |
| `cyberscout:picks:<ev>` | local | picklist copy `{stars, dnp, weights}` (server is the source of truth when signed in) |
| `cyberscout:plmode`, `cyberscout:cmp:<ev>`, `cyberscout:cols2` | local | picklist sort mode; Compare selection; Teams column visibility |
| `pl.cfg`, `pl.trailS`, `pl.offset` | local | match viewer mode/panes/layers, trail length, data sync offset |
| `lab:theme`, `lab:layout`, `lab:mode` | local | design lab only |

---

## 9. Themes, appearance, layouts

### Token structure

- `style.css :root` holds theme-independent tokens: `--font`, `--font-num` + `--num-stretch` (scoreboard numerals),
  `--mono`, type scale `--fs-cap 12 / foot 13 / body 14 / callout 16 / t3 18 / t2 22 / t1 28 / disp 56` px, spacing
  `--sp-1…7` (4, 8, 12, 16, 24, 32, 48 px), radii `--r-tag/-s/-p/-m`, layout `--sb-w 232px`, `--tb-h 56px`,
  `--gut 24px`, `--ease`, and legacy aliases (`--panel`, `--text`, `--muted`, `--warn`, …) for older page code.
- Each theme (in `themes.css`) defines colour tokens: `--bg`, `--surface`, `--surface-2/-3`, `--line`,
  `--line-strong`, `--ink`, `--ink-2/-3`, accent group (`--accent`, `--accent-fill`, `--on-accent`, `--accent-tint`,
  `--accent-line`, `--focus`), the sidebar **plate** (`--plate`, `--plate-2`, `--plate-ink/-2/-3`,
  `--plate-accent`), alliance colours (`--red`, `--blue`, `--red-fill`, `--blue-fill`, `--red-tint`, `--blue-tint`),
  `--caution` (+ tint/line), `--good`, `--bad`, `--spark`, derived `--sel`, `--hover`, `--shadow-pop`; optionally
  `--font`, `--font-num`, radii. Some themes add rules for specific components (e.g. the `.dchip.us` tag).
- Colour rules (style.css header): red/blue only for alliances (content, never chrome); `--accent` = us / you are
  here / selected / focus; `--caution` = replay, read with care; `--good` = healthy/live. Text pairs ≥ 4.5:1 (checked
  in `lab/gallery.html`).

### Selectors

`theme.js` sets `html[data-style=<id>][data-mode=auto|light|dark][data-layout=<id>]`. A theme with both appearances
generates:

```
html[data-style="x"]                    { light tokens }
html[data-style="x"][data-mode="dark"]  { dark tokens }
@media (prefers-color-scheme: dark) { html[data-style="x"][data-mode="auto"] { dark tokens } }
```

Single-appearance themes are locked to it (`effMode()`), and the Settings page disables the other choices.
The generator also puts one theme on plain `:root` and `html:is([data-style=…], :not([data-style]))` so the page is
styled without JavaScript (in the committed `themes.css` that is still Sand & Earth, see §13).
`@media (prefers-contrast: more)` collapses secondary inks to `--ink`.

### The 18 site themes

`sandearth` Sand & Earth, `jetdark` Jet (dark only), `earth` Earth, `sandstone` Sandstone (light only), `jet` Jet &
Paper (**default**, follows the system), `fieldday` Field Day, `pine` Pine & Brass (dark), `anodized` Anodized,
`meadow` Meadow, `forestfloor` Forest Floor (dark), `olivelinen` Olive & Linen (light), `carbon` Carbon (dark),
`onyx` Onyx & Gold (dark), `smoke` Smoke (dark), `knight` Knight, `frost` Nordic Frost, `chalk` Chalk & Slate,
`harbor` Harbor (the original look).

Fonts: each theme names font families; `themes.css` declares one `@font-face` per family from `fonts/*.woff2`
(Latin subsets, SIL OFL), so a browser downloads only the active theme's faces. Archivo (fallback) is inlined in
`fonts.css`.

### Layouts

- `sidebar` (default): `style.css` as is.
- `topbar`: nav across the top (`html[data-style][data-layout="topbar"] …` in `themes.css`).
- `command`: icon rail + denser dashboard grid for a big laptop.
- Phones (≤ 760 px) always get the stacked layout with the bottom tab bar. Layout selectors carry `[data-style]` so
  they outrank theme rules.

### Persistence and API

Per browser, not per account: `CSStyle.get()`, `CSStyle.set({style, mode, layout})`, `CSStyle.reset()`; changes
dispatch `cs:style` on `document` and other tabs follow via the `storage` event. **Do not hand-edit `theme.js` or
`themes.css`**: both are generated by `node lab/tools/site-themes.mjs` (see §10).

---

## 10. The design lab (`lab/`)

Purpose: try themes and layouts on the **real Dashboard** (2026wasam at the Sunday 09:00 replay) without a
server, compare them side by side, screenshot them, and port the winners into `web/`. It is not shipped:
`web/Dockerfile` copies only `web/`.

### Run it

```bash
cd lab && python3 -m http.server 8794 --bind 127.0.0.1
# open http://127.0.0.1:8794/index.html?theme=earth&layout=sidebar&mode=light   (all params optional)
```

Without an API, `GET app/v1/session` is a 404, so the app runs in static mode (no sign-in). Site JSON comes from the
fixtures `lab/api/events.json`, `lab/api/2026wasam/dash/index.json` and `lab/api/2026wasam/dash/1774195200.json`.

### Files

| file | what |
|---|---|
| `index.html` | the site shell + lab CSS (`lab.css`, `ext/a–d.css`) and scripts (`themes.js`, `ext/a–d.js`, `lab-boot.js` in `<head>`; the app scripts + `lab.js` at the end). Uses inline-free markup like the site. |
| `themes.js` | theme registry, single source of truth: `LAB.THEMES` (`{id, name, blurb, why, fonts, base, light, dark, rules, defaultMode}`), `LAB.FONTS`, `LAB.LAYOUTS` (`sidebar`, `topbar`, `compact`, `focus`), and `LAB.port()` used by the port tool. Rules use `:T` as the theme scope. |
| `ext/a.js` + `a.css` | slot "a": Meadow, Forest Floor, Olive & Linen + "Notebook" layout |
| `ext/b.js` + `b.css` | slot "b": Carbon, Onyx & Gold, Smoke + "Command" layout (its CSS is copied into the site) |
| `ext/c.js` + `c.css` | slot "c": Knight, Nordic Frost, Chalk & Slate + "Mobile-first" layout |
| `ext/d.js` + `d.css` | slot "d": the owner's picks tuned for the site: Sand & Earth (`sandearth`) and Jet dark-only (`jetdark`) |
| `lab-boot.js` | runs in `<head>`: generates every theme's CSS once into `<style id="lab-themes">` (switching = attribute change), reads `?theme=&layout=&mode=` or `localStorage lab:*`, sets `html[data-theme][data-layout][data-mode]` (note: `data-theme`, not the site's `data-style`), pins the route to `#/2026wasam/dash/1774195200` (other hashes are reverted before app.js sees them) |
| `lab.js` | the floating "Design lab" button + panel (theme / layout / appearance radios), toasts, inert links, keyboard (`t`/`l` cycle theme/layout), `?open=1` opens the panel, `?probe=1` writes overflow metrics to `<html data-probe>`, `?selftest=1` loads `tools/selftest.js` |
| `lab.css` | panel styles + the Top bar / Compact / Focus layouts (Top bar is copied into the site) |
| `gallery.html` | all themes at a glance with contrast tables (its own inline styles; not under the site CSP) |
| `phone.html` | 390×844 iframe wrapper for phone screenshots (headless Chrome can't go below 500 px) |
| `tools/site-themes.mjs` | **builds the site**: evaluates `themes.js` + `ext/a–d.js`, picks the 18 site themes in `SITE` order with site names, `DEFAULT = "jet"`, writes `web/themes.css`, `web/theme.js` and copies fonts to `web/fonts/` |
| `tools/port-theme.mjs` | prints one lab theme as a drop-in for `web/` (`--inline` embeds fonts as base64); older single-theme path |
| `tools/shoot.sh` | headless-Chrome screenshots of theme × mode × layout to `.work/lab/shots` (theme list hard-coded to the original 7) |
| `tools/probe.sh` | phone-width horizontal-overflow check per theme × layout |
| `tools/selftest.js` | in-page checks (dashboard rendered, nav icons, route pinned, panel, every theme/layout applies) → `<html data-selftest>` |
| `shots/*.webp` | committed screenshots (149 tracked, more untracked) |
| `app.js`, `dash.js`, `player.js`, … `style.css`, `fonts.css`, `field.jpg` | **snapshots of the web files** taken when the lab was made. Several have drifted from `web/` (app, admin, audit, dash, player, schedule, setup, sources, style differ); defense, event, quant, rankings, teams2, fonts.css, field.jpg are identical. The lab has no `theme.js`, `themes.css` or `settings.js`. |

### Add a theme and ship it

1. Add an object to an `ext/<slot>.js` (`LAB.THEMES.push({...})`), using the same token keys as existing themes;
   put woff2 files in `lab/fonts/` and register them in `LAB.FONTS`. Optional component overrides go in `rules`
   with `:T` as the scope (`:T[data-mode="dark"]` for dark-only rules).
2. Check it in the lab (`?theme=<id>`), in `gallery.html` (contrast), and with `tools/shoot.sh` / `probe.sh` (add
   the id to their hard-coded lists) / `?selftest=1`.
3. Add `[id, "Site name"]` to `SITE` in `tools/site-themes.mjs` (and a `BLURB` if the lab blurb is wrong for the
   site), optionally change `DEFAULT`.
4. `node lab/tools/site-themes.mjs` → regenerates `web/themes.css`, `web/theme.js`, `web/fonts/`. Commit those
   generated files together with the lab change. A new layout additionally needs its CSS slice wired into the
   generator (only Top bar from `lab.css` and Command from `ext/b.css` are copied today) and an entry in its
   `LAYOUTS`.

---

## 11. CSP rules for contributors

- **No inline scripts** (`script-src 'self'`): no `<script>…</script>` in HTML, no `onclick="…"` attributes, no
  `javascript:` URLs, no `eval`/`new Function`. Wire events in JS (`el.onclick = …`). New scripts must be separate
  files under `web/` and added to `index.html` (the Dockerfile stamps them automatically).
- **Inline styles are allowed** (`'unsafe-inline'` for styles): `style="…"` attributes and runtime `<style>` elements
  are used widely. Prefer classes and tokens anyway.
- **No third-party fetches**: `connect-src 'self'`, `img-src 'self' data: blob:` (+ YouTube thumbnails), `media-src
  'self' blob:`. All data goes through `api/` or `app/v1/`; videos must be under `media/`. YouTube embeds and Google
  Fonts are allowed by the policy but unused (fonts are self-hosted so the app works on venue Wi-Fi).
- `frame-ancestors 'self'`: the site cannot be framed by other origins.
- Escape everything: user- and data-provided strings go through `esc()`; URLs through `CS.safeHref` (http/https
  only) or `CS.mediaUrl` (only `media/...`). Every hash segment is validated once in `route()` (`routeOk`, §4.3) before any page sees it.
- The site JSON responses have their own CSP (`default-src 'none'`), so opening a JSON URL directly cannot run
  anything.

---

## 12. Recipe: add a page

1. Create `web/<feature>.js`:

   ```js
   "use strict";
   (function () {
     CS.nav({ label: "Pit map", key: "pitmap", href: ev => `#/${ev}/pitmap`, order: 75,
              group: "scout", icon: "teams" /* or add an ICON entry in app.js */, minRole: "scout" });
     CS.route("pitmap", async function (ev, arg) {          // #/<ev>/pitmap/<arg>
       crumbs([[ev, `#/${ev}`], ["Pit map"]]); $updated.textContent = "";
       const d = await api(`${ev}/pitmap.json`);            // site JSON (signed-in users)
       $app.innerHTML = `<h1>Pit map</h1><div class="card"><div class="tablewrap"><table>…${esc(d.title)}…</table></div></div>`;
       const iv = setInterval(() => { /* poll */ }, 60000);
       cleanup.push(() => clearInterval(iv));                // always undo timers/listeners
     });
   })();
   ```

   Use `CS.globalRoute` + `global: true` for pages without an event. Use `CS.app(...)` for API calls (CSRF handled)
   and check `CS.can("strategist")` before showing edit controls; the server must enforce the same role.
2. Add `<script src="<feature>.js"></script>` to `web/index.html` after `app.js` and `setup.js` (anywhere in the
   plugin block). `web/Dockerfile` copies `web/*.js` and versions it automatically.
3. If the page reads new site JSON, have the worker write it under `data/site/api/<ev>/` (`live/sitev2.py` or a
   module's `site_build`), and document it in the data contract comment / `pipeline.md`.
4. Tables: wrap in `.tablewrap` and give `<th data-pri>` priorities; help text via `CS.help`; colours via tokens.
5. If the page should follow the replay time, give its nav item `replay: true`, take the ts as the first route
   argument, and teach `urlTs()` in app.js about the key (currently hard-coded for dash/selection/picklist).
6. Rebuild the web image (see `deploy.md`). Nothing else registers routes.

---

## 13. Known inconsistencies and gaps

Found while writing this (code at `cc6297c`):

- **Default theme half-switched.** `cc6297c` changed `DEFAULT` to `"jet"` in `site-themes.mjs` and hand-edited
  `theme.js`, but did not regenerate `themes.css`: its plain-`:root` / no-JS fallback and header comment are still
  Sand & Earth, and `index.html`'s `theme-color` metas are Sand & Earth colours. With JS (always, in practice) the
  default is Jet & Paper. Running `site-themes.mjs` will move the fallback to Jet but keep the stale header text.
- **"Users who picked a style keep it" is only partly true.** `theme.js` deletes `cs:style` when it equals the
  default, so anyone who had explicitly chosen Sand & Earth while it was the default has no stored key and now gets
  Jet & Paper.
- `settings.js` Reset message still says "Back to Sand & Earth, Auto, Sidebar."
- Perspective team hard-coded: `dash.js` `US = "frc4911"` (chips, "us" tags) and `event.js` picklist highlight
  falls back to `frc4911`, while the backend uses `DASH_TEAM` / the team setting. A different team would see wrong
  "us" markers.
- `dashboard.<team>.json` and `preview/<match>.json` are written but never read. The queue card's "Full preview"
  button links to `#/<ev>/m/<match>`, which for an unplayed match is a 404 → error card.
- *(Fixed in the cleanup pass.)* Notes and identity-fix proposals now have a UI (`notes.js`).
- Sign-in policy UI offers only `never` / `everywhere` (commit 52e9da6), but `server/routers/admin.py` still accepts
  `never | lan | everywhere` and its field description calls `lan` the default; a server-side `lan` value is not an option in the
  UI's select, so the browser shows the first option ("Off") and saving the form silently changes it to `never`. See `server.md` for the effective default.
- `player.js` Calibration-grid layer draws hub rings at x = 4.03 m / 12.51 m (`PL.HUB_X`, the alliance-zone line),
  not at the hub centres 4.626 / 11.915 m used by `quant.js` and `live/field/rebuilt_rules.json`. Likely a bug in
  that layer (it is a visual check aid).
- `lab/` copies of web files have drifted; `lab/tools/shoot.sh` / `probe.sh` / `port-theme.mjs` usage text still list
  only the first 7 themes and the lab's layouts (`compact`, `focus`), not the site's.
- `sources.js` keeps its own fetch helper `A()` duplicating `CS.app` (only used if setup.js is absent).
- `setup.js` monkey-patches `route`, `renderTopNav` and `api`; any refactor of those app.js functions into
  `const`/module scope will silently break sign-in gating.
