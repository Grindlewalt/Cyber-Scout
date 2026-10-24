# The web app (`web/`) and the design lab (`lab/`)

Describes the committed state at `cc6297c` (2026-10-12). Code wins over every older doc. File:line references
are to that commit (`git show HEAD:web/<file>`).

> **In progress at time of writing.** Other agents were editing `web/app.js` (navigation for the competitions work,
> fetch/caching code for the performance work), `web/nginx.conf`, `web/Dockerfile` and `live/sitev2.py` while this
> was written. The router, nav registry, `api()` fetch helper, cache headers and cache-busting described below are
> the committed versions and may have changed since. `lab/ext/d.js` had an uncommitted local edit: the lab name of
> theme `jetdark` was changed from "Jet" to "Jet (dark only)" (lab panel only; the site name comes from
> `lab/tools/site-themes.mjs`).

> **UX fix wave (2026-10-19, `56fda7c..c64b813`).** The parts that wave changed are described as of `c64b813`:
> processing state (`CS.processing`, §4.7), the global notice, error / empty states, scroll memory, the time
> control, shared helpers (`CS.form`, `CS.fmtWhen`, `data-tip`, …), the toolbar at narrow widths and the touch
> block, the picklist, the offline layer's page hooks (§6 `sync.js`), and the pages' fixes. Audit:
> `.work/ux-audit/FLAWS.md`; fixer notes: `.work/ux-fix/{A,B,C,D}/FIXED.md`.

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

Files (sizes at `c64b813`):

| file | lines | role |
|---|---:|---|
| `index.html` | 60 | shell markup: skip link, sidebar, toolbar, `#app`, phone tab bar, "More" sheet; script order; first-paint `theme-color` metas (Jet & Paper `#f3f3f1` / `#050505`) |
| `manifest.webmanifest` | | PWA manifest (Jet & Paper colours) |
| `theme.js` | 50 | **generated**; applies theme/appearance/layout before first paint; `window.CSStyle` |
| `fonts.css` | 5 (65 KB) | Archivo variable font inlined as a base64 `data:` URL (works offline on venue Wi-Fi) |
| `style.css` | 1218 | base tokens (type scale, spacing, radii, layout sizes), every component, the toolbar container queries and the coarse-pointer (touch) block |
| `themes.css` | 2213 | **generated**; 18 themes' colour tokens + fonts + per-theme rules + Top bar / Command layouts |
| `fonts/*.woff2` | 16 files | self-hosted theme fonts (copied from `lab/fonts/` by `site-themes.mjs`) |
| `field.jpg` | 118 KB | top-down field image used by every canvas (`FIELD`) |
| `quant.js` | 357 | chart helpers (shot chart, density, paths, bars, sparklines) + the team page |
| `event.js` | ~470 | Compare, Picklist (shared list, drafts, conflicts, History): panes of Selection; `scoutRows` |
| `player.js` | 950 | match page / Analyze viewer, livestream-only match page, untracked-match card |
| `dash.js` | 601 | strategy Dashboard |
| `app.js` | 1125 | data contract, plugin registry, shared helpers, shell/nav, global notice, processing state, time control, router |
| `setup.js` | 562 | session (+ 60 s poll), `CS.app`, `CS.form`, roles, login, account, TBA event picker, first-run wizard |
| `sync.js` | 1248 | offline layer and Client mode: cached reads, outbox, connection pill / sheet, offline copies (`docs/SYNC.md` part 3) |
| `sw.js` | 264 | service worker (browser mode, https only): cache, offline copies, Background Sync of the outbox |
| `admin.js` | 443 | Admin: Teammates, Settings (+ worker status line, pause switch), Events, Server (.env via the updater), Updates |
| `audit.js` | 242 | Activity (audit log in sentences, lockdown, sign-in policy) and Identity-fixes queue (+ nav badge) |
| `notes.js` | 356 | `CS.notes` (Notes card on the team and match pages) and `CS.idFix` ("Wrong team?" identity-fix form on the match page) |
| `sources.js` | 1444 | Video setup: per-video cards, link checks, TBA webcasts, layout box editor, calibration wizard; livestream recorder status |
| `rankings.js` | ~315 | Rankings & season goal; Selection (tabs Odds / Picklist / Compare / Defense) |
| `schedule.js` | 156 | Schedule with per-period breakdown |
| `teams2.js` | ~700 | Teams (list, expanded fields, a field's own page) + the spec-sheet renderer |
| `defense.js` | ~470 | Defense tab of Selection: Best defenders + library with top-down clips |
| `settings.js` | 112 | per-browser look: theme / appearance / layout; Offline / Connection section first (sync.js) |
| `nginx.conf` | 265 | the web container's nginx (see `deploy.md` for the non-SPA parts) |
| `Dockerfile` | 14 | `nginx:1.29-alpine` image, cache-bust stamp |

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
| `= /app/v1/auth/login`, `= /app/v1/auth/key-login` | as above + `limit_req` 10/min per client IP (burst 10, one zone `cs_login` for both) | | |

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
quant.js  event.js  player.js  dash.js  app.js  setup.js  sync.js  admin.js  audit.js  notes.js
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
- `sync.js` (right after setup.js) wraps `CS.app` again (cached reads, queued writes) and `route` (clears the
  "Saved copy · as of" label per page); setup.js waits for `CS.sync.ready` before the first session check.
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
time), `global` (shown even without an event), `roles: [...]` or `minRole` (filtered by `setup.js`), `badge(ev)`
(optional: a count drawn as `.nav-badge` after the label, signed-in only; Identity fixes uses it for the requests
waiting at that competition, fetched at most once a minute and patched into the nav in place).

Registered items (committed):

| order | label | key / route | group | flags | file |
|---:|---|---|---|---|---|
| 10 | Dashboard | `#/<ev>/dash[/<ts>]` | day | tab, replay | app.js |
| 20 | Schedule | `#/<ev>/schedule` | day | tab | schedule.js |
| 30 | Rankings | `#/<ev>/rankings` | day | tab | rankings.js |
| 35 | Teams | `#/<ev>/teams[/<team>[/<field>]]` | day | tab | app.js (page in teams2.js) |
| 40 | Selection | `#/<ev>/selection[/<ts>]`, tabs `/picklist[/<ts>]`, `/compare[/<teams>]`, `/defense[/<team>]` | picks | replay | rankings.js |
| 80 | Video | `#/<ev>/sources` | manage | roles admin | sources.js |
| 94 | Identity fixes | `#/<ev>/fixes` (old `#/fixes` redirects to the current competition's) | manage | minRole strategist, `badge` | audit.js |
| 95 | Admin | `#/admin` | manage | global, roles admin | admin.js |
| 96 | Activity | `#/activity` (icon `activity`) | manage | global, roles admin | audit.js |
| 99 | Settings | `#/settings` | manage | global | settings.js |

The **Scouting** group is gone (NAV_GROUPS keeps it, empty groups aren't drawn). Matches, Specs, Defense, Compare and
Picklist are no longer nav items. The phone tab bar is Dashboard · Schedule · Rankings · Teams + More (Selection is in
More). `CS.markActive("matches")` (player.js) is treated as Schedule.

**Old links redirect** (`oldLink(p)` in app.js, before the route runs; `location.replace`, so Back is not trapped):
`#/<ev>/matches` → `schedule`; `specs[/<team>]` → `teams[/<team>]`; `defense[/<x>]` → `selection/defense[/<x>]`;
`compare[/<x>]` → `selection/compare[/<x>]`; `picklist[/<ts>]` → `selection/picklist[/<ts>]`. An unknown
`#/<ev>/<word>` lands on Teams. `activeKey(p)` maps compare / picklist / defense to Selection, specs / `t` to Teams, `m` / `a` to Schedule.

"My account" (`#/account`) is not a nav item; it is the user box link and an extra group in the phone More sheet
(account name + **Sign out**, `CS.signOut`).

### 4.3 Route table

`route(scroll = true)` (app.js ~474) runs every `cleanup` function from the previous page, splits the hash into
`p[]`, calls `renderTopNav(p)`, then dispatches:

| hash | handler |
|---|---|
| `#/<name>/…` where `<name>` is a global route | `CS.globalRoutes[name](...rest)` — `login`, `setup`, `account`, `admin`, `activity`, `fixes`, `settings` |
| `#/` (empty) | `home()`: `api/events.json`; one event → redirect to it, else event cards |
| `#/<ev>/<name>/…` where `<name>` is a plugin route | `CS.routes[name](ev, ...rest)` — `rankings`, `selection`, `schedule`, `teams`, `sources` |
| `#/<ev>/dash[/<ts\|live>[/<plan>]]` | `dashPage(ev, ts)` (dash.js) |
| `#/<ev>` | `dashboard.json` exists → `dashPage`, 404 → `eventPage(ev)` (Teams) |
| `#/<ev>/m/<match>` and `#/<ev>/a/<match>[?t=<video s>]` | `analyzePage` (player.js); `m` is an alias kept for old links |
| `#/<ev>/t/<team>` | `teamPage` (quant.js) |
| `#/<ev>/t/<team>/<field>` | `CS.fieldPage` (teams2.js), same as `#/<ev>/teams/<team>/<field>` |
| `#/<ev>/teams[/<team>[/<field>]]` | `CS.routes.teams` (teams2.js): list, a team expanded, a field's page |
| `#/<ev>/selection[/<ts>\|<tab>[/<arg>]]` | `CS.routes.selection` (rankings.js); tab = odds, picklist, compare, defense |
| old `matches`, `specs`, `defense`, `compare`, `picklist` | redirected by `oldLink` (see 4.2) |
| anything else under an event | `eventPage` → redirects to Teams |

Errors in a handler (UX fix wave) never keep the previous page's title: the toolbar title is the page that was
asked for (`pageLabel(p)`: "Selection", "Match Q12", "Team 4911"), and pages set their own title before awaiting
data. Then, in this order:

| case | what shows |
|---|---|
| offline (`CS.sync.isOffline()`) | `CS.sync.offlineHint()` card ("This page isn't saved on this device" / "isn't on this computer yet" / "The Cyber-Scout Client isn't running"); title "Not saved on this device" or "Client not running" (`CS.sync.clientDown()`); the "Saved copy · as of" label is cleared. No console error. |
| a followed competition with no `event.json` yet | `compNotBuilt`: "<competition> isn't ready yet" + `CS.buildStall()` reason (paused / no TBA key / worker quiet) or "builds on its next pass"; re-routes on `cs:processing` |
| match page, `m/<match>.json` 404 | player.js first: livestream row → `liveMatchPage`, any other row → `untrackedMatchPage` (§6); without a row: "Match Q12 wasn't tracked" + stall reason, links to Schedule / All matches |
| another page file 404 (`<ev>/…`) | "<Page> isn't built for <competition> yet" + `CS.buildStall()` or a per-page sentence (Selection: needs enough schedule and results) |
| anything else | "Couldn't load this page: The server didn't answer as expected (<error>). Check the connection, then try again." |

Every card has **Try again** (`#routeretry` → `route(false)`).

**Back / Forward keep your place.** `history.scrollRestoration = "manual"`; each history entry gets an id in
`history.state.csn`; the scroll position is saved under it (`sessionStorage cs:scroll`, last 60 entries) on
leaving / `pagehide`, and restored when the route returns to that entry (Back, Forward, reload), retrying for ~1 s
while late content arrives. A new entry starts at the top. After a route, focus moves to `#app` (`tabindex=-1`)
unless the page focused a field; "Skip to content" (`a.skip`) also focuses `#app` without changing the hash.

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
(compares `event` block and next match); Rankings and Schedule every 90 s (`generated`); the Picklist asks for the
team list every 20 s and on focus / visibility; setup.js re-reads `/session` every 60 s while the tab is visible
(lockdown and `processing`).

### 4.4 Shell and navigation

- `renderTopNav(p)` builds the sidebar (`#topnav`, grouped), the phone tab bar (`#tabbar`: every `tab:true` item +
  "More"), and the More `<dialog>` (all groups + Account). The current item gets `aria-current="page"` via
  `activeKey(p)` (team page → `teams`, match pages → `matches`, `dash` default) and `CS.markActive(key)`.
- The event used for nav links: the hash's first segment if it matches `^\d{4}[a-z0-9]{1,16}$`, else
  `localStorage cs:lastev` (same check), else the first entry of `api/events.json`. The regex check is a security
  fix (fragment XSS into nav hrefs; see `security.md`).
- `body.bare` is set when the platform is present but nobody is signed in (sidebar/tab bar/time control hidden).
- Sidebar toggle `#sbtoggle` → `body.sb-off`, remembered in `localStorage cs:sboff`.
- `crumbs(parts)` sets the toolbar title (last crumb = page title, earlier ones = back links) and `document.title`
  ("<page> · <competition> · Cyber-Scout").
- **Global notice** (`#gnotice`, above `#app`, `role=status`, `renderNotice()` on every nav render and
  `CS.setProcessing`): signed-in pages except login / setup / connect show at most one processing line — "Processing
  is paused on the server" (with a "?" that has `CS.PAUSE_HOW` for admins), "Nothing is processed yet: the server has
  no TBA key" (admins, links Admin → Settings), or "The worker isn't running" (first: it outranks paused; admins get
  Docker's reason + fix from the updater, else the commands to check, `DOWN_HOW` in a "?"; others get "The server
  isn't taking in new data right now") — with **Hide**
  (until the next visit: `sessionStorage cs:gnotice` = that line's kind). On phones (≤ 760 px) it also carries the
  lockdown line ("Read-only (lockdown)", admins get "End it" → Activity), because the toolbar's lockdown chip is hidden
  there.
- **Toolbar at narrow widths** (style.css, a container query on `.toolbar`, so it works with the sidebar open or
  hidden and in every layout): earlier crumbs give way first and the page title ends in an ellipsis; under 960 px the
  user box becomes the name's initial in a circle (name and role stay its tooltip / accessible name; the lockdown
  chip keeps its icon) and the connection pill its short text; under 700 px a Live / Replay time control moves to
  its own full-width row (the static "Latest" tag stays in the first row). Phones (≤ 760 px) show only the page title
  and the connection state as an icon in a 44 px target. `main#app` holds a screen's height while loading (no footer
  flash).

### 4.5 The time control (replay)

`renderTimebar(p, ev)` puts at most one control in the toolbar, per page:

- **Replayable pages** (nav item `replay:true`): Dashboard (`#/<ev>/dash/<ts>`), Selection Odds (`#/<ev>/selection/<ts>`)
  and its Picklist tab (`#/<ev>/selection/picklist/<ts>`, ts ≥ 9 digits; Compare and Defense get the static tag).
  `urlTs(p)` / `selTs(p)` read the ts. They get the full control (earlier /
  later, a select of snapshot times, "Go live" / "Latest") with **that page's** times: Selection reads
  `selection/index.json` (`{times}`), the others `dash/index.json` (`[{ts,label,quals_played}]`, falling back to the
  selection index). Lists are cached 2 min. With no snapshots and no ts the page gets the static tag instead.
- **Other data pages** (Teams, Selection's Compare / Defense, Schedule, Rankings): a static tag
  `CS.latestLabel(ev)` — "Latest data" while the competition is on or upcoming, "End-of-event data" once it's over
  ("Latest" / "Final" on phones) — with a "?" (everything posted so far, not replay time; paused processing noted).
- **Nothing** on Video, Identity fixes, match pages, global pages, or a competition whose `event.json` isn't built.
- The tag says **Live** only while the competition is on, processing isn't paused, the worker isn't stale and
  `event.json updated` is under 30 min old; otherwise **Latest** (with a tap tip giving the build time).
- The chosen ts is shared across replay pages via `sessionStorage cs:asof:<ev>` (`CS.asofGet/CS.asofSet`), and nav
  hrefs of replay items carry it.

### 4.6 Shared helpers (globals defined in app.js)

| helper | what |
|---|---|
| `esc(s)` | HTML-escape `& < > " '`. Use for every data/user string put into HTML. |
| `api(p)` | `fetch("api/" + p, {cache:"no-cache"})`, throws `Error("<p>: <status>")`; wrapped by setup.js for 401 |
| `num`, `pct`, `fx` | `frc4911`→`4911`; 0.42→`42%`; fixed decimals, `–` for null |
| `confBar(c)`, `idPill(c, n)` | identity-confidence bar / pill (tiers `IDC`: ≥0.8 good, ≥0.5 warn); both explain themselves on tap (`data-tip`) |
| `scoutData(ev)` | cached `api/<ev>/scout.json` with `.by[teamKey]` index (the canonical per-team numbers) |
| `covPill(c)`, `svHtml(m)`, `flagChips(row)`, `dataBanner(sd)` | render scout.json coverage tiers, canonical metrics `{v,raw,n,tier,lower_bound,over,gates}`, flags, and the "Latest data / End-of-event data, not replay time" banner |
| `tipA(tip, key)` + `[data-tip]` | **honesty labels open on tap / click / Enter, not only hover.** `tipA` returns ` data-tip="…" title="…"` (and, with `key`, `role="button" tabindex="0"`). One shared popover (`#cstip`, same look and closing rules as `CS.help`) shows `data-tip` as text. Stand-alone chips (coverage, ID %, flags, status pills) are Tab-reachable buttons; `svHtml` numbers and `confBar` in table cells open on tap but stay out of the Tab order (the page's "?" explains them). `title` keeps mouse hover. |
| `shortKey(k)` | `2026wasam_qm12` → `Q12`, `sf1m2` → `SF1-2` |
| `crumbs`, `ago(ts)` | toolbar title; "updated N min ago" / "updated today 11:03 PM" / `CS.fmtWhen` |
| `CS.fmtWhen(ts, {sec, day})` | the one date format: "11:03 PM" today, "Oct 12, 11:03 PM" this year, "Oct 12, 2025, 11:03 PM" before; `day` forces the date, `sec` adds seconds (logs) |
| `CS.sentence(m)` | a server message as a sentence: capital first letter, full stop added ("invalid username or password" → "Invalid username or password.") |
| `CS.latestLabel(ev)` | "End-of-event data" once the competition is over, else "Latest data" (banners, the time control's static tag) |
| `CS.buildStall(admin)` | why nothing new is being built, as a sentence for empty states: processing paused (admins get a "?" with `CS.PAUSE_HOW`), no TBA key (admins: link to Admin → Settings), the worker isn't running (admins: Docker's reason + fix, or what to run); `""` when the worker runs normally |
| `CS.PAUSE_HOW` | HTML: how an admin turns processing back on (remove `PAUSE_PROCESSING=1` from `.env`, `docker compose up -d worker`; Video jobs still run) |
| `CS.setProcessing(p)`, `CS.processing`, `CS.renderNotice()` | §4.7 |
| `fieldCanvas(el)` | HiDPI field canvas: `{c, ctx, P(x,y), S(m), bg(alpha), size}`; broadcast orientation (red end left) |
| `CS.help(html, label)` | "?" button + popover (`.hlp`), inline content only; one open at a time; Esc/outside click closes |
| `CS.fit()` / table fitter | every `.tablewrap > table` with a `<thead>` is fitted to its width: low-priority columns fold into a per-row "›" disclosure. Priority via `<th data-pri="N">` (0 = always folded; default keeps the first two columns). Re-fits via MutationObserver, resize and `<details>` toggle. Tables never scroll sideways. The open detail row **moves the folded cells' real nodes** in (and back on close / re-fit), so buttons, selects and links keep their handlers and state, and it copies the row's `data-*` attributes so delegated handlers reading `tr.dataset` still work. The "›" button is labelled from the first cell's `data-label` (or its `b` / `a` text). |
| sortable headers | `th[data-k]` with a click handler gets `tabindex=0`, `aria-sort` (from the ▲ / ▼ mark) and Enter / Space sorting that keeps focus on the header (`a11ySort`, run with the fitter) |
| `CS.icon(key, cls)` | inline SVG from the `ICON` set (24-grid, 1.8 stroke): nav icons plus `star`, `starOn`, `external`, `chevL/R`, `close`, `up`, `warn`, `check`, `ban`, `key`, `copy`, `power` |
| `CS.sheet({title, html, wide, cls, onClose})` | a modal in the competition list's look (`.compsheet`; `wide` = 760 px): centred on a desktop, a bottom sheet on phones. Esc, `[data-x]` and a backdrop click close it (a text selection that ends on the backdrop doesn't); it is removed when closed, focus returns to its opener, and a page change closes it (`cleanup`). → `{dlg, body, close}`. Used by the connection-key sheet (admin.js) |
| `CS.ask({title, html, ok, cancel, danger, mount(body), value(body)})` | `CS.sheet` with Cancel / OK (`.sh-acts`; `danger` = a red-tinted OK and Cancel focused first); resolves to `false` or `value(body)` (default `true`). For questions with choices or lists in them (Client Stop / Uninstall); quick yes/no questions elsewhere still use `confirm()` |
| `CS.isStream(m)`, `CS.liveTag(m)` | match row has `score_source:"livestream"` → "LIVE · stream" tag |
| `CS.liveTracking(live, tracked)` | status text for a stream result ("Tracking from stream" / "Waiting for uploads") |
| `CS.mediaUrl(u)` | accepts only `media/...` paths (`^/?media/[\w\-./]+$`, no `..`), else null |
| `CS.safeHref(u)` | only `http(s)://` URLs become links (security audit) |
| `CS.asofGet/Set`, `CS.markActive`, `CS.fit` | see above |
| constants | `LEN=16.541`, `WID=8.069`, `FIELD` (field.jpg `Image`), `COLORS` (3 shades per alliance), `PALETTE` |

### 4.7 Processing state (`CS.processing`, `cs:processing`)

Source: `GET app/v1/session` → `processing` ([server.md](server.md) §7.2, from `data/worker-status.json`).

- `CS.processing` = `{paused: bool|null, worker_seen: unix|null, reason: "paused"|"no_key"|"no_events"|null, why,
  worker_down}`,
  `null` when signed out, `undefined` before the first session answer (pages guard for both).
- setup.js calls `CS.setProcessing(session.processing)` after every session check, after sign-in, and from its 60 s
  poll (visible tabs only; also on `visibilitychange`); sign-out sets it to null.
- `CS.setProcessing` stores it, redraws the global notice, and dispatches **`cs:processing`** on `window` (detail =
  the state) only when `paused`, `reason` or "worker down" (kind + container state) changed.
- **Worker down** = `CS.workerDown(p)` = the server's `worker_down` (no status write for 5 min, or never since the api
  started; server.md §7.2); an older api without the field: `worker_seen` over 1 h old and not paused.
- Who reacts: the notice (§4.4); the time control's Live / Latest; Matches ("not tracked (paused)" instead of
  "waiting", redrawn on the event); not-built cards and Selection / page empty states (`CS.buildStall`); the
  Dashboard's "now" vs "data as of" (redrawn on the event); the untracked-match card; notes.js re-label wording;
  the "Competition added" dialog; Admin → Settings worker line ("Worker: paused / running / waiting for a TBA key /
  idle / not heard from / status unknown").

---

## 5. `setup.js`: session, `CS.app`, sign-in, roles, setup wizard

Exports: `CS.app`, `CS.can`, `CS.user`, `CS.session`, `CS.platform`, `CS.refreshSession`, `CS.navAllowed`,
`CS.form`, `CS.fieldErr`, `CS.signOut`, `CS.genPassword`, `CS.eventPicker`, `CS.ROLES`, `CS.ROLE_TIP`, `CS.GOALS`,
`CS.USER_PATTERN`.

**`CS.app(method, path, body)`** → `fetch("app/v1/" + path)` with `credentials: same-origin`, `cache: no-store`,
JSON body, and for every non-GET the header `X-CSRF-Token` = cookie `cs_csrf` (fallback `CS.session.csrf`). Returns
parsed JSON (null for 204). Errors throw `Error(message)` with `.status` and `.data`; FastAPI `detail` strings, 422
lists and `{message}` objects are turned into readable text. A 401 while signed in clears `CS.user` and goes to the
login page. Every answer is handed to `CS.sync.seenApp(path, response)` (Client mode labels cached answers).

**Session**: `CS.refreshSession()` → `GET app/v1/session`. 404 or non-JSON → `CS.platform=false` (static site).
Network error / 5xx → "Can't reach the Cyber-Scout server" card with retry. Otherwise `CS.session` =
`{setup_required, setup_code_required, user, csrf, configured, lockdown, network, passwordless_ok, processing}` and
`CS.setProcessing(processing)` (§4.7). A 60 s poll (visible tab, signed in) refreshes `lockdown` (redraws the user
box) and `processing`; a changed user is left to the next request.

**`CS.form(el, onSubmit, opts)`**: submit → `onSubmit(values, form)` with the submit button disabled
(`opts.busy` = its text meanwhile, e.g. "Checking with The Blue Alliance…"); old `.csok` lines are cleared. A thrown
error lands **under the field it is about**: `Error.field` (`CS.fieldErr(name, message)` builds one), else guessed
from the server's sentence ("setup code", "don't match", "current password", "password", "username", "display
name", "goal"); that input gets `aria-invalid` + `aria-describedby`, focus and selection; the message clears on
input. Anything else goes to the form's `.cserr`. Messages pass through `CS.sentence`. Every password input gets a
Show / Hide toggle (`.pwrow`). First focus: the checked radio or the first input / select.

**Route guard** (wrapped `route`): setup required → `#/setup`; not signed in (except the Client's `#/connect`, which
stays reachable signed out for "Change server" and connection keys) → remember the hash in
`sessionStorage cs:next` and go to `#/login`; signed in on `#/login` → back to `cs:next` (never back to login /
setup / the Client's `#/connect`); an admin with `configured:false` landing on `#/` → `#/setup`.

**Roles**: `viewer < scout < strategist < admin`. `CS.can(role)` is true when the user's role is at least `role`
(always true in static mode). Nav items are filtered by `roles` (exact membership) or `minRole`. Pages also check
`CS.can` themselves; the server enforces everything regardless (UI checks are cosmetic).

**Pages**:

- `#/login` in Client mode also shows **Use a connection key** (a link card to `#/connect/key`). `CS.afterAuth()` (the
  same "go to `cs:next`, or the setup wizard for an unconfigured admin" step the sign-in form ends with) is exported for
  the Connect page's key path.
- `#/login`: username + optional password (blank = passwordless, if the server policy allows it) →
  `POST auth/login`. A wrong password: "Invalid username or password." under the password field, which is cleared
  and focused; a "Forgot your password?" line points at the admin.
- `#/account`: password card (`POST auth/password`; admins 10+ chars required; members optional 4+, and leaving
  both fields blank removes the password = sign in with just the username), **Your name** for scout+ (`PATCH
  auth/profile {display_name}`, redraws the nav), and **Sign out**.
- `#/setup[/<step>]`: first-run wizard. Steps `account` (only while `setup_required`: setup code from
  `data/setup-code.txt`, username with `pattern=CS.USER_PATTERN`, display name, 10+ char password → `POST setup`),
  `tba` (`PUT settings/tba-key`, busy "Checking with The Blue Alliance…"), `team` (`PATCH settings {team_number}`,
  busy "Looking it up…"), `goal` (`PATCH settings {season_goal}`; choices Win an event / DCMP / Worlds / Einstein /
  Scouting / Other), `event` (`CS.eventPicker`; Continue stays off with "Follow at least one event to continue";
  following refreshes the competition switcher at once), `video` (lists `GET events/<ev>/sources` per followed event,
  links to `#/<ev>/sources`), `done` (sets `setup_complete: true`). The step list ("Step n of 7") shows from the
  first page: ✓ done, Video "· later" once skipped (`localStorage cs:wiz-video`) or ✓ when every followed event has
  its video set up. Required steps (`tba`, `team`, `event`) can't be skipped: a link or URL to a later step
  redirects to the first one missing. Until `setup_complete`, the wizard walks every step in order even when a
  value exists (e.g. imported from an old `.env`). Each step focuses its first control.
- `CS.eventPicker(el, opts)`: the competition switcher's ranked type-ahead (`GET events/search?q=&year=&limit=12`:
  on now / soon / your team's events first; a full key from any season is looked up directly), followed events on
  top with Unfollow, a season select, Enter searches; 409 → "Set your TBA Read API key first". Follow (`POST events
  {key}`) / Unfollow (`DELETE events/<key>`). Used by the wizard and Admin → Competitions.

The user box (`#userbox`) shows a lockdown chip when `CS.session.lockdown` ("Read-only", admins: "Read-only
(lockdown)" linking to Activity), "name · role" (link to `#/account`) and Sign out. **`CS.signOut()`** (user box,
More sheet, account page): in Client mode it first asks "Sign this computer out of the server? Saved data stays…"
and calls `CS.sync.clientLogout()`; otherwise `POST auth/logout` + `CS.sync.signedOut()`; then clears the user and
the processing state and goes to `#/login`.

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
Scoring section is replaced by a note pointing at Scouting truth. (UX fix wave) Tiles, hub pills and stats explain
themselves on tap (`tipA`); the Matches table shows "–" instead of null/null and its Auto (routine) column only when
a verified match has a routine; routine chips are keyboard buttons in ink with a colour swatch; a star that doesn't
reach the server says so in a toast (the Picklist keeps the change); read-only users see a plain star mark.

### `event.js` — Compare, Picklist (panes of Selection)

(The Teams table that used to open here is the Teams page in `teams2.js`; `eventPage` in app.js only redirects to it.)
`comparePane(el, d, ev, arg)` and `picklistPane(el, d, ev)` are called by the Selection tabs (rankings.js); `scoutRows`,
`VID`, `qv` are the helpers they share.

- **Compare** (`#/<ev>/selection/compare[/<t1,t2,t3>]`): up to 3 teams (selection remembered in
  `localStorage cyberscout:cmp:<ev>` and written to the URL with `replaceState`, so a comparison can be shared);
  labelled selects "Team 1/2/3"; a Blend ± sd row first; rows nobody has data for are left out; canonical rows + shot
  chart + auto paths, **verified matches only**.
- **Picklist** (`#/<ev>/selection/picklist[/<ts>]`): shared server picklist `main`
  (`GET/PUT app/v1/events/<ev>/picklists/main`, data `{stars:[teamkey], dnp:[teamkey], order:[teamkey],
  weights:{metric:w}}`). Everyone keeps a copy in `localStorage cyberscout:picks:<ev>` (the team page and the Teams
  page read stars from there). Rules (UX fix wave):
  - **Who edits:** strategist+ (`plCanEdit`); during lockdown only admins. Viewers, scouts and lockdown get no grips,
    star / do-not-pick buttons, Reset or sliders, just plain marks and a line saying why ("Read-only: only strategists
    and admins…" / "Lockdown is on…"). The team page's star is a plain mark for them.
  - **Saving:** debounced 500 ms, one PUT at a time per event (`plSend` chains them, each on the version the previous
    one created), with `base_version`. Status line beside the filters: Saving… / Saved · <who> · <when> / Updated
    (someone else's save picked up) / Waiting to send · offline / Not saved / Not loaded / "Not started: your first
    change creates the team's list".
  - **A change is never dropped silently:** until the server has it, it is kept as a per-user draft
    (`localStorage cyberscout:picks:unsaved:<ev>:<user id>`, survives a reload). A failed save (423 lockdown, 500, …)
    shows a banner with the reason, **Try again** and **Discard my change**; the team page's star shows a toast.
  - **Conflict (409) — no auto-merge:** your list stays on screen; the banner says who saved and when ("… on another
    device or tab" when it was you), lists **Only in yours** / **Only in theirs** (stars, do-not-picks), and whether
    the order or the weights differ, with **Use theirs (drop my change)** and **Save mine (replaces theirs)** (sent
    on top of their version). Either way the other list stays in History. If their save equals yours, it is adopted
    silently.
  - **Offline:** a queued save (`r.queued`) shows "Waiting to send · offline"; on load the newest queued PUT from
    `CS.sync.pending()` (browser outbox or the Client's) is drawn over the saved copy; the page follows `cs:outbox`
    and `cs:sent`.
  - **Polling:** teammates' saves show up every 20 s and on focus / visibility (not while dragging or typing, never
    over an unsaved change); before any list exists it asks `GET events/<ev>/picklists` (no 404 noise).
  - **History** (disclosure): versions newest first (`…/versions?limit=` + each version's data), who, when, and what
    changed in words ("starred 4469 · unstarred 1318", "changed our order", "restored version 3"), Show older;
    strategist+ get **Restore** (confirm; `POST …/versions/<v>/restore`, saved as a new version).
  - Sort modes Blend / EPA / OPR / Custom / **Our order** (`localStorage cyberscout:plmode`; Our order = the
    strategists' dragged ranking `order`, shared like the stars; unplaced teams follow by blend; the help says which
    views are per-device). Custom = weighted mean of event z-scores over `PL_KEYS` (blend, video made/m, acc, auto,
    cycle ↓, climb (disabled when `scout.json event_stats.climb_matters` is false), defense matches), weights 0–5
    sliders, default `{blend: 4}`. Filters All / Starred / Hide do-not-pick; do-not-pick rows in `--ink-2` with the
    team number struck through in red; nicknames ellipsised (full name in `title`).
  - **Copy list** copies a numbered text list: the async clipboard on https / localhost, else `execCommand("copy")`,
    else a selected textarea to copy by hand (the venue LAN is plain http).
  - Weights are coerced on load and on render (`plWeights` / `plWeight`: known keys only, `Number()` clamped to
    0–5) so a stored value can never reach `value=""` or the shown number as markup (security audit finding 1; the
    server schema also rejects it). In replay (`/<ts>`) it reads `scout/<ts>.json` and uses that snapshot's rank/OPR.
- **Matches** (`matchesTable`, in app.js): every match with its tracking state. Chips **All** / **<our team> only**
  (default ours while the competition is on), a search box (team number or match, "4911", "Q12"), short names as on
  the Schedule (Q12, SF4), an "ID confidence" column with a "?", "not tracked (paused)" while processing is paused
  (redrawn on `cs:processing`), "No match matches …" when the filter is empty.

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
- **`untrackedMatchPage`** (UX fix wave): `m/<match>.json` is 404 and the row has no `live` data (played but not
  tracked, or not played yet): a "Not tracked" card instead of an error page: TBA score and winner, the status pill,
  why ("Processing is paused on the server…" while `CS.processing.paused`, failed, being tracked now, or "tracked
  once its video is available") plus the worker's `state_note`, a The Blue Alliance link, the six teams, links to
  All matches / Schedule, and the match **Notes** card. A dashboard "Full preview" of an unplayed match lands here.
- Every video listener returns once the page is left (`dead`), so leaving a match page logs no errors. The
  timeline scrubber draws the seconds before / after the match as hatched segments (the bar runs edge to edge under
  the video) and gives identity-fix triangles their own lane. On phones the Robots card comes right after the player.
- `?t=<video s>` (used by the defense library) seeks there on load.
- (cleanup pass) **Wrong team?** in the Robots card header (and "Propose a fix" in the Identity section) opens the
  identity-fix form (`CS.idFix`), and a **Notes** card ends the page (`CS.notes`, match notes, optionally about one
  of the six teams). Both only for signed-in users; the form only for scout+.

### `dash.js` — Dashboard (`#/<ev>/dash[/<ts|live>[/<plan>]]`)

Contract: `live/strategy/DASHBOARD.md`. Loads `dashboard.json` (live) or `dash/<ts>.json` (replay); live view also
loads `event.json` for livestream results and `CS.liveRecorder` status, and per plan its match plan `mp/<match>.json`
(`live/strategy/matchplan.py`, contract in `live/strategy/README.md`). Order on screen (Match Day redesign, Oct 2026):

0. **Match Day head** (top of the queue card): label + countdown, a full-width **bar in our alliance colour**, one line
   **red teams | blue teams** (us marked), then `CS.matchField` (below) for that match and the **predicted scoreboard**
   (`CS.matchPeriods`: alliance totals + period bars). An alliance with `enough: false` turns yellow on the bar, the
   team line and the scoreboard, with "not enough info (team 1234: why)" (`CS.matchNote`). Yellow is for predictions
   only: actual results are always solid alliance colours. A match after the replay / as-of time is predicted-only
   (no Actual switch, no actual numbers or paths). No mp file (older data, no tracking): a short note and the plan
   content below as before.
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
`event` block, the next match, or `event.json updated` changes. The perspective team defaults to `frc4911` and is
set per load from `dashboard.json` (see §13).

Freshness (UX fix wave): the subtitle and rail say "now" only while the data is current (under 30 min old,
processing not paused, worker checked in); otherwise "data as of <CS.fmtWhen date>", redrawn on `cs:processing`.
Records are labelled ("6–9–0 · all matches, playoffs too" in the rail, "Qual record" in the side panel). Once every
qual is played, rank and top 8 are stated as decided ("Final rank", top 8 yes / no) and Expected RP / projected rank
are hidden. Δ signs come from the rounded shown value. Section "?" buttons sit inside the open section, not in its
`<summary>`. On phones the rail is one swipeable row scrolled to the open plan.

### `rankings.js` — Rankings (`#/<ev>/rankings`) and Selection (`#/<ev>/selection[/<ts>|<tab>[/<arg>]]`)

- Rankings: `rankings.json` (contract `live/strategy/README.md`). Table of rank, RP, record, projected rank range
  (p10–p90), P(top 8), district points now / projected / P(DCMP); season-goal panel (odds with interval, points
  needed at p50/p80/p95, cutoff projection, DCMP capacity, value of each remaining match as Δ P(goal) for win vs
  loss, per-event outcomes, awards variants). Polls every 90 s.
- **Selection is four tabs** (real links in a `role=tablist`, `aria-selected`; the page shell renders even when a tab's data
  is missing): **Odds** (`selection`, `selection/<ts>`, `selection/odds[/<ts>]`; the replay keeps working), **Picklist**
  (`selection/picklist[/<ts>]`, `picklistPane`), **Compare** (`selection/compare[/<teams>]`, `comparePane`; the picked teams
  stay in the address) and **Defense** (`selection/defense[/<team>|defenders|team]`, `CS.defensePane`). The replay time
  carries between Odds and Picklist. Phones: four equal icon tabs.
- Odds: `selection.json` or `selection/<ts>.json` + `selection/index.json`. Shows our P(captain / R1 / R2 /
  unpicked) and P(DCMP), the "need" grid (alliance × role → least playoff result reaching the DCMP need), who
  is likely to pick us, **pitch cards** (per likely captain: what they lack, what we bring, re-sim Δwin / ΔP(DCMP),
  fit facts), the per-captain re-simulation table ("if this captain takes us vs their next best"), projected seeds,
  and draft odds per team. `status: done` shows the actual alliances instead.
- (UX fix wave) Both pages: one probability format (whole %, "<1%" / ">99%"); title set before data loads; subtitle
  with the event name ("· us = 4911"); keyboard-sortable headers. Rankings: district columns (Dist. pts, Projected
  pts, DCMP) only when some team has district points; once all quals are played top 8 is "yes" / "no" and the banner
  says "All quals played"; "as of <date>" when the file was built to a time. Selection: Δ win and pitch deltas from
  the rounded shown values with a "pp" unit; pitch cards lead with "Pitch to <team>" (We bring / They lack) and fold
  the simulation numbers and fit facts into "Simulation and fit" (honesty labels kept inside). **Empty state:** no
  `selection.json` → "No alliance-selection projection for this competition" (built for district events once the
  simulation has run) plus `CS.buildStall()` when the worker is stalled, and links to Rankings / Picklist; a replay
  time without a snapshot → "No selection projection at <time>" with the nearest snapshots.

### `schedule.js` — Schedule (`#/<ev>/schedule[/<match>]`)

Replaces the old Matches tab (`#/<ev>/matches` redirects here). `schedule.json` (rows carry `mp` and `video`) +
`event.json` (livestream results, `queue`, row `wait` / `state_note`). Filters Ours / All / Upcoming / Played; our next
match's row opens automatically. Every row is a button (Enter / Space, `aria-expanded`) that expands into the mini
`CS.matchField` (Auto / Tele, Predicted / Actual), the period bars (red and blue back to back from a centre line, the
points inside; Actual shows the prediction as ticks; the table follows the field's Predicted / Actual switch) and the
match video behind a "Play match video" button (local mp4 first, a real 11-character YouTube id embedded, a livestream
clip; nothing loads until tapped), plus "Full analysis and replay" (`#/<ev>/a/<key>`). Status pills from the queue backend
(`wait`: not played yet / needs video setup / queued / no video yet / waiting for stream clip / retrying / waiting for
the detector / failed; "not tracked (paused)" while paused; `state_note` as the tooltip) and a header line from
`event.json queue` ("12 of 54 matches tracked · tracking Q13 now · 3 need video setup · … · next: Q15 at 10:45"). `#/<ev>/schedule/<matchkey>` scrolls to
that row. Polls every 90 s. Its banner uses `CS.latestLabel` ("Latest data" until the competition is over).

### `matchfield.js` — the match field (`CS.matchField`, `CS.matchPeriods`, `CS.matchNote`)

`CS.matchField(el, plan, {mini, us, asof})` draws one match plan (`mp/<match>.json`) top-down on a canvas in theme
colours (red end on the right, redrawn on theme / mode change, crisp on retina): the six robots at their predicted
start spots, **Auto** (arrowed auto paths, points per robot, alliance auto totals) and **Tele** (typical cycle,
dotted transition path, collect point with a capped ring, smooth heat for the selected team only, "scores in transition
N %", cycles / min, the inactive-shift job chip Defense / Ferry from neutral / Robber / Collect with its `basis` in a
"?"). Played matches get Predicted | Actual (actual paths with the prediction as a ghost). Tap a team (canvas or chip)
to isolate it. `enough: false` robots are dashed / grey with their `why`; stand-in numbers (`stand_in`) read "≈N (event
avg)". `mp/index.json calib` (hit rates measured so far: start side, job, auto FUEL error, score-range cover) goes into
the "?" text and the scoreboard's track-record line. `prefers-reduced-motion` skips the path reveal.

### `teams2.js` — Teams (`#/<ev>/teams[/<team>[/<field>]]`) and the spec-sheet renderer

**Teams list.** `teamsData(ev)` joins `event.json teams[]` (all teams, `q`), `scout.json` (video metrics `{v,raw,n,tier}`, rating,
climb, coverage), `dashboard.json ratings[]` (EPA total / auto / teleop / endgame, blend ± sd, projection) and
`rankings.json` (rank / record fallback). One row per team: rank, team + name (our team tinted with a "us" tag, starred
teams marked), record, EPA with a stacked auto / teleop / endgame bar (length compares teams), our model rating ± sd with its
coverage pill, made / match, accuracy, cycle, climb (verified-video values show `n=`; grey = under 3 matches). Sort by
clicking a column head (`aria-sort`; a select on phones), search by number or name. At 390 px each row is a card (rating top
right, EPA, four stat chips). Click a row (the team button is a stretched link, `aria-expanded`) and it expands into **every
field** (`FIELDS`, grouped Rating / Scoring / Auto / Collection / Movement / Defense / Endgame / Reliability): value, the
honesty line (`n=9 verified matches`, `n=2 verified, anecdote (under 3)`, `TBA, 12 quals`, the coverage label for the model
rating) and a thin bar for where the team stands among the event's teams (faded + striped under 3 matches). The expanded team
is in the address (`#/<ev>/teams/<team>`, replaceState).

**A field's page** (`#/<ev>/teams/<team>/<field>`, also `t/<team>/<field>`; `CS.fieldPage`): header (value, coverage pill,
flags, how it is computed, "All of 4911 →", Compare), a strip chart of every team on that field, the per-match values (verified
matches only, from `t/<team>.json`), then **the existing renderers, not copies**: `teamPage(ev, team, {host, only, quiet})`
(quant.js) draws only the listed sections and `specPage(ev, team, {host, only, quiet})` only the listed spec cards (`data-sx`:
drive, scoring, autoclimb, lanes, shots, paths, defense). Rating / EPA / OPR / Rank have their own bodies (parts, source
weights, trend, simulated rank). Defense fields carry "effect on opponents is an assumption, not measured". The old
Specs list is gone (its columns are fields); `specs[/<team>]` redirects here.

Spec sheet data: `spec/<team>.json`. Cards: Drive, Scoring, Auto & climb, Lanes (trench / bump bands, left/right from the
driver's view), Shooting locations, Shooting paths (the drive into each volley), Defense. Team frame: own alliance wall on
the left (blue matches point-reflected), driver's left = top.

### `defense.js` — Defense tab of Selection (`#/<ev>/selection/defense[/<target team>|defenders|team]`)

`defense.json` (from `live/scouting.py`), `event.json`, `scout.json`. `CS.defensePane(ev, target, host)` draws into the Selection
tab. **Best defenders** (default): a ranked list, points denied per match (Σ denied / its verified matches), episodes
(+ testable), contact seconds, the share of output the defended teams kept (with its 90% range when ≥ 3 testable), each
tagged **assumption** (no per-team effect survives the multiple-testing correction; a row says "N episodes significant" only
when some of its episodes are individually significant). A row expands to its episodes as clips (best by points denied, "show
more"). "Defend a team" is the older per-target answer. Built around "how do I defend team X?":
pick a target → its retention while defended (placebo-controlled, with interval and verdict), the best clips, and who
has defended it. Filters: target, defender, zone, shift, method, verdict; sort. Each episode is a **top-down clip**
drawn on a canvas (`clipPlayer`): defender and target circled, the episode window highlighted, shots drawn. Clip data
is embedded in `defense.json` or rebuilt on demand from `m/<match>.json` (`clipFromMatch`, same slice as
`live/scouting.py`). Clips animate only while visible (IntersectionObserver) and are stopped in `cleanup`. "Watch at
N s" links to `#/<ev>/a/<match>?t=<video s>`. Verdicts: clearly reduced / reduced, not clear / no effect / nothing at
stake / tracking unsure.

**Three-episode rule** (UX fix wave): the event-wide headline ("Defended scorers kept N% of their expected output")
needs **≥ 3 testable episodes**; below that the page says "Too few defended episodes to say how much defense slows
scorers here (1 testable episode; at least 3 are needed for an estimate)" and that the clips are single examples,
not a measurement. An interval that includes 100% adds "not significant: the range includes no effect"; no
interval, none printed. Defenders are ranked ("slowed it most") only with ≥ 3 testable episodes; fewer are listed
unranked and say so; "Who is easiest to slow down" has a sentence when no team qualifies. Clip captions are ink text
with coloured dots for defender / target.

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
3. **Calibration tab** (wizard) per target `overhead | station_red | station_blue`: 1 Detect tags (job: grabs
   frames, finds AprilTags, draws the first fit with its verdict; a line above the frame says what was read and,
   with fewer than 2 tags, what to do: `readLine` / `noTagsNext`) → 2 click named field points / line samples
   (magnifier, drag to adjust, right-click removes; catalog from the server, defaults `DEFAULT_POINTS`/`DEFAULT_LINES`
   mirror `live/calibrate.py`) → 3 Fit (job; the verdict box, per-tag errors, residual report + overlay of grid, field
   lines, hubs, tags; a tag the fit left out is red, struck in the chips "✕", with a dashed ring where the other tags
   put it) → 4 Accept & save (old file kept as backup). A `bad` verdict turns step 4 into "Save anyway" (not
   highlighted, the reason in its tooltip and the confirm). "Show the saved calibration" overlays the current file.

**Per-video cards** (the page today; the tabs above survive as Field setup sub-views and old links): one card per
source with what it is (main / broadcast / station cam), its link, Replace / Remove, the recorder state and matches
found (redrawn every 15 s), and a collapsed **Field setup** (Auto-calibrate · Field box · Calibration, mounted one at a
time; the wizard detects on that card's video: `detect {source}`). A broadcast stream gets the overhead calibration as
*optional* field setup (its fixed wide camera, for tracking a program feed).

**Automatic set-up** (block in the card body of every livestream / file source; `GET/POST
events/<ev>/sources/<sid>/auto-setup`, `…/overlay.jpg`, `…/accept`; data from `GET sources`: `auto_setup`,
`auto_ready`, `calibration`, `program_feed`, `dynamic_on`). The worker runs `dynpose.auto_setup` on the stream's
recording on its own (pipeline.md §1.2 "Automatic set-up"); the card shows, by state:
* *waiting*: why (`auto_ready.why`: nothing / too little recorded), "Set up now" once there is enough;
* *queued / working*: spinner, polled every 4 s (`pollAuto`) until it finishes, then only that block is redrawn;
* *ready*: the proof image (projected carpet edge, grid, hubs and tags on the found camera's median view; graphics,
  clock and field boxes; tap opens it full size) with a colour key, the fit (on air %, AprilTags, px), the feed
  (program feed or one camera, and why), the scoreboard layout, the difference to a saved calibration, and
  **Looks right: accept** (one click; flash + reload) / Run again / Calibrate by hand (opens Field setup → Calibration);
* *accepted*: who / when / which file, "What was found" collapsed, Run again;
* *no fixed camera* / *failed*: the reason, Calibrate by hand first.

**Track robots from** (select next to "What it is", not for station cams / upload families): Automatic (the label says
what the set-up detected) / Every camera / The fixed camera only → `PATCH sources/<sid> {dynamic: null | true | false}`.

**Adding a link** (UX fix wave): `linkProblem()` checks the pasted link in the page first (same sentences as the
server's `link_problem`, no request): only YouTube / Twitch links, a direct video address (`.mp4`, `.m3u8`, …) or a
server file. The error sits under the field in the error colour with `aria-invalid`; the server's 422 sentence
(including a dead direct link, `probe_http`) lands there too. Adding a link never switches the video mode (the old
silent switch to Livestream-only is gone; the suggestion stays a choice at the top). **TBA's webcasts** (`GET
events/<ev>/sources/webcasts`) not yet added are offered above the paste box in every mode, with Add (or Restore for
a removed one), named "Event webcast · Twitch firstwa_red2". The second and later links default to "Broadcast"
(never a station cam); one camera vocabulary everywhere (Main / wide view · Broadcast · Station cam; "Field box").
**Calibration verdict** (Auto-calibrate, the wizard, the automatic set-up card): the fit's `verdict` (good / check /
bad) and `verdict_reasons` from `live/calibrate.py report()` render as one box (`verdictHtml`: problems as a list, the
passed checks in one muted line), the tags as chips with their px error (`tagErrs`; a left-out tag dashed red with how
far off it is), and the left-out tag on the frame (`paintDropped`). Auto-calibrate's result screen: good / check →
**Looks right: save** (primary) · Fix it by hand; **bad** → "This fit can't be trusted", **Calibrate by hand**
(primary) · Save anyway (+ "not recommended: <reason>", confirm). Failures say what was read (`readLine`: "16 frames at
2:00:00–6:48:00 of the stream's recording (the stream has ended): none had an AprilTag"), show the frame that was read,
and the next step; a worker that isn't running makes Try again the main button. The automatic set-up card does the
same with the fixed camera's verdict ("can't be trusted" pill, Calibrate by hand first, Accept anyway + confirm).
Raw texts that still arrive (older states, job errors, the runner's queue hint) go through `calPlain` (the
`calib-plain` block, run in node by `tests/test_calib_robust.py`): "need more correspondences", "pose initialisation
failed", "unknown source …", "expired (no job runner picked it up)" → "The worker isn't running video jobs: is it
running?", "waiting for the worker's video job runner" → "waiting for the worker to pick it up (is it running?)".
**Tool errors** (yt-dlp / ffmpeg, from Auto-calibrate, Grab frame, Suggest, Detect / Fit / Accept, Discover, Check
videos) become one plain sentence (`plainErr`: not live, not found, sign-in / bot check, unreadable link, unreachable,
no frames) with the raw text behind a **Details** disclosure. The box editor and calibration take touch / pen
(Pointer Events) and the keyboard (Add box, arrows move, Shift ×10, Alt / Option + arrows resize; calibration points
are buttons with a × remove, Enter places, Delete removes).

`CS.liveRecorder` (exported here, used by dash.js): `load(ev, evj)` (`GET events/<ev>/livestream`, else the
`livestream` summary in `event.json`, flagged stale after 10 min), `line()` (one quiet Dashboard line while
recording/waiting/error), `card()` (state, behind-live, analyser lag, last match + clip, tracking from stream or
uploads, disk, recent errors).

### `admin.js` — Admin (`#/admin[/users|settings|events|server|updates]`, admin)

Tabs Teammates / Settings / Competitions / Server / Updates (the tab row scrolls on phones with the current tab in view).

- **Teammates** (full width): `GET users`; create (`POST users`, generated passwords via `CS.genPassword`), change
  role, disable/enable, reset password, delete (`PATCH/DELETE users/<id>`). Shows "no password: can't sign in"
  accounts (when passwordless sign-in is Off), last sign-in, active sessions; you cannot change your own role. Add
  teammate and Reset password follow the sign-in policy (passwordless Off → a password is required and
  pre-generated). Reset password / Disable / Delete are visible on a desktop and fold into the row's "›" on a phone;
  one delegated handler per table, errors shown in the row; the chevron is named "More for <username>".
- **Connection key** (every row, the user's own included; Teammates): a one-time key that connects a laptop's Cyber-Scout
  Client to this server and signs it in as that teammate, no password. A wide `CS.sheet`: who it signs in as (name,
  username, role; admin accounts get a caution line "This key signs in as an admin … Hand it over in person"), the
  active key if any ("A key is active until …", **Revoke** = `DELETE users/<id>/connect-key`; the key itself can't be
  shown again), **Works once, for** 1 day / 7 days / 30 days (`hours` 24 / 168 / 720), **Server address laptops use**
  (default: in the Client its server `CS.sync.status.server`, else this page's base address; checked with
  `CS.sync.normServer`; a loopback address gets a warning), **Make key** (`POST users/<id>/connect-key {hours}`; "Make a
  new key … cancels the active key"). The result: the connection string `cyberscout:<server>#<KEY>` in a big monospace
  box (address small, the key large; one click selects it all) with **Copy**, "Works once, until <date>. Making a new
  one cancels this one" and who can use it, then "On <name>'s laptop": 1. paste it on the Client's Connect page; 2. the
  one-line Client install with the key added (`curl -fsSL <downloads>/downloads/get-client.sh | bash -s -- --key
  '<string>'`, Windows `$env:CS_KEY='<string>'; irm <downloads>/downloads/get-client.ps1 | iex`). The app doesn't know
  the downloads site: a **Downloads page address** field fills the placeholder (remembered per browser, `cs:dlsite`);
  until then the commands show a dashed "downloads page" placeholder and their Copy is off. Revoke / Make a new key /
  Done. The key lives only in the sheet's memory (never in storage). Disabled accounts get no form ("disabled, so a key
  couldn't sign it in. Enable it first"; the server answers 409 and deletes keys on disable). A row with an unused key
  shows a "key until <date>" pill (`connect_key_expires`); the list is redrawn when the sheet closes after a change.
- **Settings**: team number + season goal (`PATCH settings`; goal radios as in the wizard, plus None), TBA key
  (`PUT settings/tba-key`, shows only the hint), a **worker status line** from `CS.processing` (paused with
  `CS.PAUSE_HOW` / running / waiting for a TBA key / idle / not heard from / unknown, with the last check time;
  redrawn on `cs:processing`), processing options (`PATCH settings {options: {poll_seconds, gpu_device, cpu_only,
  hubcam, paused}}`; blank = `.env` default; applied by the worker on its next poll). "Match processing" (paused)
  overrides `PAUSE_PROCESSING`; a paused worker line has **Resume processing** (`paused: false`).
- **Server**: `.env` keys through the host updater. Polls `GET admin/server-env` while a change runs (2.5 s; 3 s while
  the api restarts). Not installed / updater v1 → install hint. Fields come from the updater's schema, labels and
  help from `ENV_UI` in admin.js (a key without a label shows its raw name under "Other"); blank = the default
  (placeholder shows it). Changed rows are tinted; Save asks to confirm (lists old → new and what restarts) and posts
  only the changed keys (`POST admin/server-env {changes}`); progress log + result (incl. "previous .env is back").
- **Competitions** (`events`): followed competitions (Open / Video setup buttons / Unfollow) + `CS.eventPicker`.
- **Updates**: polls `GET admin/update` (every 2.5 s while busy, 3 s while the server is unreachable during a
  restart). Not installed → instructions to run `install-updater.sh`. Installed → current version (+ "local
  changes"), changelog, what restarts (worker heavy/light, api, web), disk needed vs free, buttons Check / Update now
  / Roll back to <prev> (`POST admin/update/check|apply|rollback`, apply body `{ytdlp}`), optional "also update
  yt-dlp", progress log, result, and "Reload the page" once the version changed. Notes a paused worker
  (`CS.processing`). An Admin → Server run shows as "Changing server settings".

#### Admin → Worker (`#/admin/worker`)

One card per role from `GET admin/logs`: a dot (green running; amber quiet = heartbeat fresh but no line for 2 min; red
stopped = no heartbeat for 90 s), "Doing now: ... for 2 min 10 s", "last line N s ago" (ticks every second; caution after
2 min, bad after 5), then a monospace log pane that follows the newest line until you scroll up or press Pause, a filter box
and Copy. Polls every 3 s with `since=` while visible; stops when hidden or left. All log text is escaped; lines wrap, so it
fits at 390 px. Linked from the worker-down notice, `buildStall` and the Settings worker line ("See what it's doing").

### `audit.js` — Activity (`#/activity`, admin) and Identity fixes (`#/fixes`, strategist+)

- Activity: **Lockdown** toggle (`PATCH admin/security {lockdown}`), **Sign everyone out** (`POST
  admin/sessions/revoke {include_admins}`), sign-in policy form (`passwordless_login`: UI offers only `never` "Off"
  and `everywhere` "On (testing only)"; `scout_overrides`: review | direct), and the **audit log** (`GET audit?…`,
  filters by user / kind / via lan|public / competition, 100 per page with "more"). The default kind is "changes
  (no sign-ins)" = `changes=true` (no sign-ins, sign-outs, failed sign-ins or raw `http.*` requests); "everything",
  "sign-ins" and "other requests" bring them back. Each entry reads as a sentence ("Sam saved the picklist", "Coach
  changed the season goal: — → DCMP"; accounts by who they are, not a field diff), with the time (`CS.fmtWhen`,
  seconds), team net vs internet + IP, and **Undo** (`POST audit/<id>/revert`, confirm shows what comes back).
  **Undo all their changes since…** is only on other people's write rows, behind a ⋯ menu in danger style: a
  `revert-user` **`dry_run: true`** first gives the count ("Undo ALL of Sam's changes since …: 7 changes (2 more
  can't be undone)"), then the admin must type that **username** to confirm before the real call.
- Connection keys in the log: "made a connection key" (works once, until …; "replaced the unused one"), "revoked a
  connection key", "signed in with a connection key", "tried a connection key (not accepted)" with the reason (not a
  key / no such key / already used / expired / account disabled); a disabled account's revoked key is noted on the
  account change. Never the key itself.
- Identity fixes (`#/<ev>/fixes`, one queue per competition; other competitions' waiting counts link to theirs):
  `GET events/<ev>/overrides`; pending scout requests with requested vs current mapping (robots named as the match
  page shows them, from `m/<match>.json`; "changed" when stale) and Approve / Reject
  (`POST …/override-requests/<id>/approve|reject`, one delegated handler, so they work from a folded row). The match
  cell is a link with a chevron. Requests come from the match page's "Wrong team?" form. The nav item's `badge`
  shows the waiting count.

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
- Offline / queued (UX fix wave): a queued write shows `CS.sync.queuedMsg()` inline (no second toast). Queued note
  edits and deletes are drawn over the saved copy ("edit waiting to send"), also after a reload; the header says
  "3 · 1 waiting to send" / "not connected" and "Saved copy from <time>" when the list came from this device
  (`CS.sync.readInfo`). The fix form's button is off until a select changes, says "Sending…" while sending and
  resets after "Sent for review" / queued (no double filing); queued fixes are listed as "Waiting to send", network
  errors are words. While `CS.processing.paused`, the wording says re-labelling waits for processing; an applied fix
  the page doesn't show yet is tagged "not re-labelled yet" ("<robot> will show as <team>").

### `settings.js` — Settings (`#/settings`, everyone)

sync.js's **Offline** (browser) / **Connection** (Client) section comes first in every mode, then the theme grid (18
themes, light/dark swatch previews built from `CSStyle.themes[].sw`), Appearance (Auto / Light / Dark; locked for
single-appearance themes), Layout (Sidebar / Top bar / Command), Reset to default (its message is built from the
defaults: "Back to Jet & Paper, Auto, Sidebar."). Non-admins aren't offered the Client's processing set-up. Stored
per browser only (`CSStyle.set` → localStorage). Redraws on `cs:style` events (another tab changed the look) unless
focus is inside the page.

### `sync.js` + `sw.js` — offline layer and Client mode (protocol: `docs/SYNC.md`)

`CS.sync.ready` resolves to `"client"` (page served by the local Cyber-Scout Client, `GET client/v1/status`) or
`"browser"`. In browser mode `sw.js` (https / localhost only) caches pages and offline copies and replays the outbox
by Background Sync; on plain http (the shop LAN address) there are no offline copies, and changes wait in the
IndexedDB outbox while the page is open. Reads of scouting data fall back to a saved copy (slow 4 s / unreachable)
and the toolbar says "Saved copy · as of <time>" ("Saved <time>" on phones). Writes of notes, picklists and
identity fixes carry an `Idempotency-Key` and are queued when the server can't be reached. The connection pill
(sidebar footer; toolbar on phones / Top bar / Command) opens the Connection sheet; on phones it is the state's
icon in a 44 px target. Hooks the pages use:

| helper | what |
|---|---|
| `CS.sync.isOffline()`, `mode` | offline / unreachable now; `"client"` / `"browser"` |
| `CS.sync.offlineHint()` | the offline error page's card (`"<b>Title</b>Text"`, `""` when online); clears the "as of" label |
| `CS.sync.clientDown()` | Client mode and the local Client stopped answering → "Client not running" |
| `CS.sync.seenApp(path, r)` | setup.js hands it every `CS.app` response: a Client answer from its own cache (`X-CS-Offline` + `X-CS-At`) gets the "Saved copy · as of" label |
| `CS.sync.readInfo(path)` | how the last read of `path` was answered: `{at (ms), saved}` |
| `CS.sync.queuedMsg()` | one wording per mode: "Saved on this device. It's sent when the connection to the server is back." / "Saved on this computer. It's sent when the server is back." |
| `CS.sync.pending(filter)` | queued changes still to send (browser outbox: this user's, not failed / conflict; Client: its outbox minus rows held for another server), for pages to draw over the saved copy |
| events (on `document`) | `cs:outbox` (outbox changed), `cs:sent` (a queued write was accepted; detail `{path, method, data}`) |

Browser outbox rules: nothing is tried while offline or unreachable (a fetch that fails while `navigator.onLine` is
false isn't a try), so a picklist save replaces the earlier untried one (one queued save per list); after an accepted
picklist save, later queued saves on the same `base_version` are rebased (page and `sw.js` alike); one "N changes
sent" toast per flush. Picklist **conflicts** are cards that say who saved and what each choice drops ("Save mine
over theirs: removes their star on 8248… Use theirs: drops your star on 2046."); nothing is merged.

Client mode (the Client does the queueing): the pill adds "Connecting…" (right after a server change) and "Client not
running"; sync progress is by bytes; the sheet lists every change (waiting / conflict / rejected / **held for another
server** — the Client sends a row only to the server it was written for, `status.outbox_held`) with its buttons;
unticking a competition offers **Remove from this computer** (`DELETE client/v1/events/<ev>`); choosing Previews /
Full videos first shows the extra size against the free disk and refuses what can't fit, since the Client keeps
2 GB free (`MEDIA_SPARE`, same as `client/csclient/sync.py`). One Client per data folder (`client.lock`). Details:
`client/README.md`, `docs/SYNC.md` "Deviations".

**Connection keys** (`#/connect`, Client mode). The Connect page leads with **Have a connection key?**: one paste field
and **Connect and sign in**; the server address form follows ("or connect with the server address"). `parseConn`
(`CS.sync.parseConn`) accepts `cyberscout:<server>#<KEY>` (also out of a pasted install line), `<server>#<KEY>`,
`server=<s> key=<k>` (any order; spaces, `;` or new lines between) and a bare KEY (then the Client's current server);
`normKey` (`CS.sync.normKey`) forgives case, spaces, dashes, O → 0, I / L → 1 and wants 16 characters of
`0123456789ABCDEFGHJKMNPQRSTVWXYZ` (sent as `XXXX-XXXX-XXXX-XXXX`). While typing, a line says what it will do ("Connects to
<host> instead of <old> and signs in", changes waiting for the old server stay and go only there); errors come on submit,
in the page's `say()` style. Then: `POST client/v1/test {server}` (a failure says the key wasn't used), `PUT
client/v1/config {server}` when it differs, `POST client/v1/login-key {key}` (retried twice after a server change while
the Client still connects), `CS.refreshSession()`, a toast and `CS.afterAuth()`. While it runs (`KEYFLOW`) the Client's
status changes don't re-route the page; a failure after a server change says the Client now uses that server. Error
wording per status (`keyErr`): 503 / offline (the key wasn't used), 401-family (the server's sentence, which already
says to ask the admin), 422, 429. `#/connect/key` is the sign-in page's link; **`#/connect?k=<connection string>`** (the
Client installer opens it; URL-encoded or not) is read by `takeKeyLink()` in sync.js's `route` wrapper before the route
checks (a `?k=` segment isn't a page, and the sign-in redirect would drop it): the key is kept in memory only, the
address bar becomes `#/connect` (`history.replaceState`), and the page fills the field and runs it once.

**This app** (Settings → Connection, last row; from `status.install {frozen, program, autostart, can_uninstall,
data_dir}`): the program, how it starts (LaunchAgent / systemd user service / Startup shortcut / not by itself / from
source), **Stop the Client** and, when `can_uninstall`, **Uninstall…** (redrawn only when that data changes). Stop:
`CS.ask` (its pages stop opening until it runs again, how many changes wait unsent, when it starts again) → `POST
client/v1/quit` → a full-page "The Client stopped" (it starts again at the next login, or from <program>; **Reload**).
Uninstall: `CS.ask` listing the login item and the program, a checkbox **Also delete this computer's data (<size>)**
with the folder; the line under it counts the changes not sent yet (`outbox` + held + conflicts / rejected) and says
they're lost (red) or stay in the kept folder, with "Sync now first"; OK reads "Uninstall and delete data" when ticked →
`POST client/v1/uninstall {delete_data}` → a full page "The Client is uninstalled" with Removed / Kept / Couldn't remove
(paths as code). A 409 (runs from source) or other error is a toast; no answer at all shows "The Client stopped
answering". The full pages (`gonePage`) cover the app (`.cx-gone`; the rest made `inert`) and stop status polling
(`GONE`), since nothing on the page can reach the Client any more. From source the row says there is nothing to
uninstall.

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
| `<ev>/selection.json`, `<ev>/selection/<ts>.json`, `<ev>/selection/index.json` | Selection (its own time control), Dashboard / Picklist time-control fallback |
| `<ev>/spec/<team>.json` | Teams field pages (spec-sheet cards) |
| `<ev>/defense.json` | Selection, Defense tab |

Written but **not read by the UI**: `dashboard.<team>.json` (dash.js always loads `dashboard.json`),
`preview/<match>.json` (the "Full preview" button only links to the match page, which shows the untracked-match card
for an unplayed match).

Platform API (`app/v1/…`), by file: setup.js `session` (+ 60 s poll), `setup`, `auth/login|logout|password|profile`,
`settings`, `settings/tba-key`, `events`, `events/search`, `events/<ev>/sources`; admin.js `users[/<id>[/connect-key]]`, `settings`,
`settings/tba-key`, `events[/<key>]`, `admin/update[/check|apply|rollback]`; audit.js `admin/security`,
`admin/sessions/revoke`, `audit?changes=true…`, `audit/<id>/revert`, `audit/revert-user` (`dry_run` first),
`events/<ev>/overrides` (also for the nav badge), `events/<ev>/override-requests/<id>/approve|reject`; notes.js
`events/<ev>/notes`, `notes/<id>`, `notes/<id>/restore`, `events/<ev>/overrides[/<match>]`,
`events/<ev>/override-requests/<id>/approve|reject`; event.js `events/<ev>/picklists[/main[/versions[/<v>[/restore]]]]`;
sources.js `events/<ev>/sources[/webcasts|/discover|/<id>|/<id>/frame|/<id>/frame.jpg|/<id>/auto-setup[/accept|/overlay.jpg]]`,
`events/<ev>/video-mode`, `events/<ev>/video-plan[/check|/matches/<key>]`, `events/<ev>/layout[/<id>[/suggest]]`,
`events/<ev>/calibration[/<target>/wizard|detect|fit|accept|overlay|frame.jpg]`, `events/<ev>/jobs/<id>`,
`events/<ev>/livestream`. Full list and roles: `server.md`.

Client control API (sync.js `ctl()`, Client mode only, served by the local Client): `client/v1/status`, `outbox[/<id>]`,
`test`, `config`, `login`, `login-key`, `logout`, `sync`, `events/<ev>` (DELETE), `quit`, `uninstall` (`client/README.md`).

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
| `cs:next` | session | where to go after sign-in (never login / setup / connect) |
| `cs:scroll` | session | `{<history entry id>: scrollY}` for Back / Forward (last 60; ids in `history.state.csn`) |
| `cs:gnotice` | session | the global notice line hidden until the next visit (`paused` / `no_key` / `stale`) |
| `cs:wiz-video` | local | setup wizard: the Video step was left for later |
| `cs:dlsite` | local | Admin → Teammates → Connection key: the downloads page address typed for the one-line Client install (never the key) |
| `cyberscout:picks:<ev>` | local | picklist copy `{stars, dnp, order, weights}` (server is the source of truth when signed in) |
| `cyberscout:picks:unsaved:<ev>:<user id>` | local | a picklist change the server doesn't have yet `{data, base, at, why}` (failed save, conflict, page closed mid-save) |
| `cyberscout:plmode`, `cyberscout:cmp:<ev>` | local | picklist sort mode; Compare selection (`cyberscout:cols2`, the old Teams column picker, is unused) |
| `pl.cfg`, `pl.trailS`, `pl.offset` | local | match viewer mode/panes/layers, trail length, data sync offset |
| `lab:theme`, `lab:layout`, `lab:mode` | local | design lab only |
| `cs:sync:user` + IndexedDB `cyberscout` (`outbox`, `reads`, `kv`) | local / IDB | sync.js: last signed-in user, offline outbox, saved reads (`docs/SYNC.md`) |

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
styled without JavaScript: Jet & Paper since `themes.css` was regenerated in the UX fix wave (`58a199a`), matching
`theme.js`, `index.html`'s first-paint `theme-color` metas and the manifest.
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
- `topbar`: nav across the top (`html[data-style][data-layout="topbar"] …` in `themes.css`). Every item keeps its
  label (icon over label); the brand is logo-only at ≤ 1240 px; when an admin's items don't fit, the bar scrolls
  (with a fade) instead of hiding names.
- `command`: icon rail + denser dashboard grid for a big laptop. With no next match, Levers moves under "Next match"
  and Schedule + Ratings sit side by side at full height (no hole, no inner scroll boxes).
- Phones (≤ 760 px) always get the stacked layout with the bottom tab bar. Layout selectors carry `[data-style]` so
  they outrank theme rules.

### Touch (`@media (pointer: coarse)` in style.css)

With a finger (phones, the pit iPad) every button, select and text field is at least **44 px** tall (Apple HIG,
WCAG 2.5.5): chips 40 px, controls repeated in every table row (star, grip, the fold "›") 40 × 40, toolbar buttons
and the time control 44, the "?" help buttons keep their look with a 44 px hit area (`.hlp-b::after`). Pages that
inject their own CSS add the same for their controls (setup.js, audit.js, app.js's Matches bar). Mouse layouts are
unchanged.

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
   `LAYOUTS`. The same applies to a fix in a theme's `rules` or a layout slice (e.g. the UX fix wave's Jet
   `.oe dd small, .bigp small { letter-spacing: normal }`, the Top bar labels, the Command grid): edit the lab, then
   regenerate.

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
4. Tables: wrap in `.tablewrap` and give `<th data-pri>` priorities (folded cells keep working: don't copy cell
   HTML yourself, and use delegated handlers that read `tr.dataset`); sortable headers as `th[data-k]` with an
   `onclick`; help text via `CS.help`; colours via tokens. Set the title (`crumbs`) **before** awaiting data. Forms:
   `CS.form(el, fn, {busy})` and throw `CS.fieldErr(name, msg)`; dates `CS.fmtWhen`; server messages
   `CS.sentence`; honesty labels with `tipA(text, true)` (tap / Enter, not only hover); a missing page file is
   handled by the router's empty state, and your own empty states can add `CS.buildStall(admin)`. Anything that
   depends on `CS.processing` listens to `cs:processing` and removes the listener through `cleanup`. Touch targets
   follow the coarse-pointer block (§9).
5. If the page should follow the replay time, give its nav item `replay: true`, take the ts as the first route
   argument, and teach `urlTs()` in app.js about the key (currently hard-coded for dash/selection/picklist).
6. Rebuild the web image (see `deploy.md`). Nothing else registers routes.

---

## 13. Known inconsistencies and gaps

Found while writing this (code at `cc6297c`), updated for the UX fix wave (`c64b813`):

- *(Fixed in the UX fix wave.)* **Default theme half-switched.** `themes.css` was regenerated (`58a199a`): its
  plain-`:root` / no-JS fallback is Jet & Paper, and `index.html`'s `theme-color` metas and the manifest use Jet &
  Paper colours. Left over: the generated header comment (from `site-themes.mjs`) still says "Default = Sand & Earth".
- **"Users who picked a style keep it" is only partly true.** `theme.js` deletes `cs:style` when it equals the
  default, so anyone who had explicitly chosen Sand & Earth while it was the default has no stored key and now gets
  Jet & Paper.
- *(Fixed in the UX fix wave.)* The `settings.js` Reset message is built from the defaults ("Back to Jet & Paper,
  Auto, Sidebar.").
- Perspective team: `dash.js` takes `US` from `dashboard.json` per load (default `frc4911`) and the `event.js`
  picklist highlight falls back to `frc4911`, while the backend uses `DASH_TEAM` / the team setting. A different team
  without a rebuilt dashboard would see wrong "us" markers.
- `dashboard.<team>.json` and `preview/<match>.json` are written but never read. The queue card's "Full preview"
  button links to `#/<ev>/m/<match>`; for an unplayed match that is now the untracked-match card ("This match hasn't
  been played or posted yet"), not an error card, but still not a preview.
- The Video page's private-host refusal suggests `CS_URL_ALLOW`, which the api container doesn't get (server.md,
  Docs vs code 13).
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
