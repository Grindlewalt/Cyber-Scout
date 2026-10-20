# Cyber-Scout — notes for Claude

Autonomous FRC scouting + strategy for **team 4911 CyberKnights** (owner: the team's software lead, a strong programmer).
Tracks all 6 robots and every FUEL ball from match video (2026 REBUILT), reconstructs shots, identifies robots by
bumper OCR, and turns it into match plans, picklists, Selection odds, spec sheets and a defense library. Self-hosted;
offline-first clients. Repo: github.com/Grindlewalt/Cyber-Scout (private), branch `main`. Upstream tracker:
samdev-7/autoscout (MIT, credited).

**Read first:** `docs/guide/README.md` (mental model + glossary), then `architecture.md`, `pipeline.md`, `strategy.md`,
`server.md`, `web.md`, `deploy.md`, `files.md` (every file), `security.md` in `docs/guide/`. Sync protocol:
`docs/SYNC.md`. Strategist reviews that shaped the UI: `docs/STRATEGIST_REVIEW.md`.

## Layout
- `live/` — worker pipeline (worker.py loop, process.py tracking, fuel.py, shots, identity/OCR, enrich, sitev2 site JSON,
  sources.py + videoplan.py video selection, livestream.py + liveingest.py recorder, proxies.py preview videos,
  coverage.py trust rule, scouting.py specs/defense) and `live/strategy/` (model, sim, dashboard, matchday, goals,
  selection, district, backtest).
- `server/` — FastAPI + SQLite API (`/app/v1/*`): auth/roles/sessions/CSRF, audit log + revert, lockdown, notes,
  picklists (versioned), identity-fix queue, competitions, sources/layout/calibration, sync endpoints, update endpoints.
- `web/` — vanilla-JS SPA (no build). Plugin registry in app.js (`CS.route/CS.globalRoute/CS.nav`), `api()`/`CS.app()`,
  `CS.help` "?" popovers, table fitter, 18 themes (`themes.css`, boot `theme.js`, picker `settings.js`), offline layer
  (`sw.js`, `sync.js`), nginx.conf (CSP `script-src 'self'` — **no inline scripts**), Dockerfile adds `?v=<hash>`.
- `client/` — Cyber-Scout Client: stdlib-Python local server on 127.0.0.1:7600 (mirror + outbox + sync); PyInstaller
  builds; `client/dist/` (gitignored).
- `android/` — Kotlin WebView app. `site/` — demo/downloads website (`site/demo/`, `site/downloads/` generated).
- `lab/` — design lab (themes/layouts on the frozen Sun-09:00 dashboard). `deploy/` — compose, installers
  (`install.sh`, `install.ps1`), host updater. `.github/workflows/` — installers, client, android CI (all green).
- `.work/` — scratch (gitignored): QA harnesses, screenshots, agent outputs.

## Production (atomosserver)
- `ssh atomosserver` (Linux, 2 GPUs: **GPU 0 = production, GPU 1 = experiments/agents**; 46 GB RAM). LAN 10.0.0.58,
  Tailscale 100.86.234.25.
- Stack: `/opt/stacks/cyber-scout/` = `app/` (code copy), `data/` (all state), `.env`, `docker-compose.yml`
  (copied from `app/deploy/`). Services: worker, recorder, api, web. Media on NAS `/mnt/nas/autoscout-media`;
  scratch `/mnt/nas/autoscout-scratch` (deploy dir, backups, rtenv Python, agent scratch).
- **Ports:** 7560 = the app (also public via a Cloudflare tunnel as https://scout.grantrindal.dev), 7570 = design lab,
  7580 = demo + downloads site (`cyber-scout-demo` nginx container; serves get.sh/get.ps1, installers, code tarball,
  robot.onnx, client builds, APK). **8790 is NOT ours** (owner's "jav3" app) — don't touch.
- `PAUSE_PROCESSING=1` is set in `.env` (worker idles, still runs Video-page jobs). Remove it before a competition.
- **Deploy a change:** commit → `git archive HEAD | ssh atomosserver '…extract to /mnt/nas/autoscout-scratch/deploy…;
  rsync -a --delete --exclude data deploy/ /opt/stacks/cyber-scout/app/; cp app/deploy/docker-compose*.yml .;
  GIT_SHA=<sha> docker compose build <svcs> && GIT_SHA=<sha> docker compose up -d <svcs>'`. Back up `data/app.db` to
  `/mnt/nas/autoscout-scratch/backups/` before schema changes. Strategy/site regen: `/mnt/nas/autoscout-scratch/regen_prod.sh`.
- **Root disk is nearly full (~3–4 GB free).** Never change the big `pip install` line in the root Dockerfile
  (rebuilds a 3.3 GB layer and fills the disk); keep everything big on the NAS. After builds remove only *our*
  dangling images (check `docker history` for live/, /srv, nginx html) — **never `docker image prune -f`** (host-wide).
- Downloads site refresh: `bash site/tools/publish_downloads.sh http://10.0.0.58:7580 atomosserver /mnt/nas/autoscout-scratch/demo-site 7580`.
- Updater (Admin → Updates) is stubbed: needs a read-only GitHub deploy key — ask the owner before adding one.

## Rules
- **Secrets:** TBA key lives in the app DB / `data/config/worker.json` only; Roboflow key and the Discord webhook are
  secrets (webhook only for milestones the owner asked about). Never commit or print keys. Roboflow free tier: never
  re-download training data (cached).
- Heavy compute (video, ML, builds) on the server, not the Mac. Server Python jobs: prefix `ulimit -c 0;` (core dumps
  once filled the disk); outputs on the NAS; use `/mnt/nas/autoscout-scratch/rtenv/bin/python`.
- Never touch production containers/data for experiments: use throwaway stacks with another `CS_PREFIX` + port bound to
  127.0.0.1, or copies of data under the NAS scratch dir. No full-event reprocessing without the owner's OK.
- `pkill -f` over ssh can kill its own session — use self-safe patterns (`pgrep -f "foo[.]py"`) or PIDs. Don't
  `pkill` other agents' servers.
- **No Claude attribution anywhere:** no `Co-Authored-By: Claude`, "Generated with Claude Code" or similar lines in
  commits, PRs, docs or the UI (owner's rule; a local commit-msg hook strips them). Commit only the files you changed
  (other agents may have uncommitted work in the tree — never `git commit -a`). Push to origin main after review.
- Tests: `.venv/bin/python -m pytest -q server/tests tests` (343 pass), `python3 -m unittest discover -s client/tests`,
  `node --check web/*.js`. Regenerate `server/openapi.json` after API changes. GitHub Actions run on push (private
  repo minutes; quote YAML step names containing `: `).
- Parallel agents: give each disjoint file ownership; "don't go too ham" (≈3–4 at a time); verify with screenshots
  (headless Chrome, no remote-debugging ports) and real data; the owner prefers concise updates.
- Design: owner likes colorful, non-templated UI (not all-black); default look Jet & Paper (system light/dark);
  users pick from 18 themes + Sidebar/Top bar/Command layouts in Settings (note from Grant at the top). Use the
  installed skills (apple-design, frontend-design, impeccable, ui-ux-pro-max, web-design-guidelines, bencium) for UI work.
- Honesty: strategist-facing numbers carry coverage/uncertainty; video counts as censored evidence; "measured" only
  when statistically significant. Don't remove those labels.

## Status / open items (2026-10-13)
- All features live: dashboard + queue cards, Selection, picklist, notes, identity-fix queue, competitions switcher,
  activity log/lockdown, offline sync (client + PWA), video sources v2 (per-match plan, channel scan), full screen.
- Open: updater deploy key; owner to add the next competition + unpause processing; root disk space; Windows server
  install never run on real Windows; client binaries unsigned; Android APK debug-signed; Cloudflare Access in front of
  the public tunnel recommended (passwordless sign-in is off by default).
