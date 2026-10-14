# Cyber-Scout website and live demo

A static site: no build step for the pages, no external CDNs, no cookies, no analytics.

```
site/
  index.html        home: headline, what it captures / produces / how it runs, one screenshot, one-line server
                    install per OS (auto-detected, copy button; ?os=linux|macos|windows forces one), Client downloads
  download.html     install guide: what the installers do, first run per OS, Android, system requirements
  docs.html         readable overview of docs/guide/README.md (links to the repo for the rest)
  assets/           site.css (field palette: navy ink, alliance red / blue, FUEL yellow), mode.js (appearance before
                    first paint), site.js (switch, menu), download.js (install commands, OS pick, copy, downloads)
  fonts/            Instrument Sans (variable, OFL; copied from web/fonts/)
  img/              webp screenshots and broadcast frames (see "Images" below)
  downloads/        installers and apps go here (from the build pipeline; README.txt lists the exact names)
  demo/             GENERATED: the real web app, offline and without sign-in (see below)
  tools/build_demo.py, tools/demo/   the demo builder and the three files it adds to the app
```

Appearance follows the system (light / dark); the footer switch overrides it per device and uses the same
`localStorage` key as the app (`cs:mode`), so the demo opens in the same appearance. `?mode=light|dark` forces one
without remembering it (handy for screenshots).

## The live demo (`site/demo/`)

`demo/` is the real Cyber-Scout web app copied from `web/` unchanged, reading frozen JSON for **2026wasam** (PNW
District Sammamish) and opening on the Dashboard at the **Sunday 09:00 replay**. The builder adds three small files:

* `demo-boot.js` (in `<head>`, before the app): answers the app's own API (`app/v1/...`) in the browser: a signed-in
  "Demo visitor" with the strategist role, the competition list, empty notes; the picklist saves in this browser
  only; every other change gets a plain "read-only demo" answer. Nothing is sent anywhere.
* `demo-tail.js` (after the app): pages whose data isn't copied (other match pages, admin, video setup, identity
  fixes, account) show a friendly "isn't in the demo" panel in the app's own style; "Sign out" becomes "Leave demo".
* `demo.css`, plus a generated `demo-config.js` (event, replay time, which snapshots / teams / matches are included).

Not copied: `sw.js` and `sync.js` (no server to sync with), the PWA manifest, Dockerfile / nginx.conf, and videos.

Included pages: Dashboard (every replay snapshot, so the time control works), Schedule, Rankings, Selection,
Picklist, Teams, Compare, Matches list, every team page and spec sheet, Defense library, Settings (theme / layout
picker), and the match pages of Q12, Q17 and Q40 in field view (no video; the YouTube link stays).

### Rebuild / refresh

```bash
python3 site/tools/build_demo.py                                   # web/ + .work/site_staging/api -> site/demo
python3 site/tools/build_demo.py --data /path/to/site/api          # any copy of the server's data/site/api
python3 site/tools/build_demo.py --matches qm12,qm17,qm40 --ts 1774195200 --snapshots all
```

Run it after any change to `web/` (the app is copied fresh every time) or to refresh the data. It replaces
`site/demo/` (only if that folder carries the `.cyberscout-demo` marker it writes), minifies the JSON and prints the
size. Stdlib Python 3.9+. To use production data, copy `data/site/api/` from the server first, e.g.
`rsync -a server:/opt/stacks/cyber-scout/data/site/api/ /tmp/cs-api/` and pass `--data /tmp/cs-api`.

If `web/index.html` changes shape so the script tags can't be patched, the builder stops with a message
(`patch_index()` in `build_demo.py`).

Size: the whole site is about 21 MB (demo ~19.9 MB, of which ~18 MB JSON; images 1.3 MB). Each extra match page adds
about 1.2 MB; `--snapshots one` drops ~6 MB of replay snapshots.

## Serve it

Any static file server works; everything uses relative paths, so it can live at `/` or under a sub-path.

```bash
python3 -m http.server 8080 --directory site          # quick local check: http://127.0.0.1:8080/
```

nginx container (e.g. on the server, next to the stack):

```yaml
# /opt/stacks/cyber-scout-site/docker-compose.yml
services:
  site:
    image: nginx:1.27-alpine
    restart: unless-stopped
    ports: ["7580:80"]
    volumes:
      - ./site:/usr/share/nginx/html:ro
      - ./site.conf:/etc/nginx/conf.d/default.conf:ro
```

```nginx
# site.conf
server {
  listen 80;
  root /usr/share/nginx/html;
  gzip on; gzip_types application/json text/css application/javascript image/svg+xml;
  add_header X-Content-Type-Options nosniff always;
  add_header Referrer-Policy no-referrer always;
  add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'" always;
  location /downloads/ { add_header Content-Disposition attachment; }
  location ~* \.(webp|woff2)$ { expires 30d; }
  location /demo/api/ { expires 1h; }
}
```

Copy with `rsync -a --delete site/ server:/opt/stacks/cyber-scout-site/site/`. JSON compresses well (gzip makes the
demo's data about a fifth of its size on the wire). The pages have no inline scripts, so the CSP above holds; the
demo's `style-src 'unsafe-inline'` is needed because the app sets inline styles.

## Deploy notes

* `downloads/` is filled by the build pipeline with exactly: `cyberscout-client-windows-x64.exe`,
  `cyberscout-client-macos-arm64`, `cyberscout-client-macos-x64`, `cyberscout-client-linux-x64`,
  `cyberscout-android.apk`, `install.sh`, `install.ps1`. Until then the download links 404; `download.html` says
  so. Remove that note once the files are published.
* The demo is public and has no sign-in: it only contains data already public on TBA / YouTube plus Cyber-Scout's
  derived numbers for one past event. It sets `noindex`.
* The site's numbers (accuracy, backtest, sync timings) are quoted from `docs/guide/` (pipeline §4/§8/§9,
  strategy §7.4), `client/README.md` and `deploy/README.md`. Update `index.html` / `docs.html` when those change.

## Images

From `.work/` and the demo itself, converted to webp: `q12-overlay` (the Q12 accuracy-check overlay frame),
`q12-fuel` (airborne FUEL tracking, Q12), and screenshots of the demo at 2x in Jet & Paper light and dark
(`dashboard-*`, `match-*` = Q17 field view at 1:13, `selection-*`, `team-*` = 4911, `defense-*`, `phone-*`).
Re-shoot them after a visual change of the app with headless Chrome against a local server, e.g.

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --force-device-scale-factor=2 \
  --window-size=1440,960 --virtual-time-budget=12000 --screenshot=dash.png \
  "http://127.0.0.1:8080/demo/?shot=1&mode=light#/2026wasam/dash/1774195200"
cwebp -q 80 -resize 2000 0 dash.png -o site/img/dashboard-light.webp
```

`?shot=1` hides the demo's own chrome ("Demo visitor", the demo footer line). Note that headless Chrome won't go
below a 500 px window; load a page inside a 390 px iframe to check phone width.
