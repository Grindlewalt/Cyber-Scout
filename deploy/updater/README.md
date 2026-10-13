# One-click updates (Admin → Updates)

The **Update** button in Admin is backed by a small program on the server, `cs-updater.py`, run by systemd
as the owner of the stack folder (a normal user in the `docker` group, never root). The web app has no docker
socket and cannot run commands. All it can do is **ask**: it writes `data/config/update-request.json` with a
fixed schema and reads back `data/config/update-status.json`.

```json
{"v": 1, "action": "check" | "apply" | "rollback", "nonce": "<32 hex>", "at": 1791866000.0,
 "by": {"id": 1, "name": "Coach"}, "ytdlp": false}
```

A request carries no commands, URLs, branches, paths or arguments. The updater checks the file strictly: it must
be a regular file (symlinks are never followed), at most 2 KB, have exactly these fields and types, be less than
15 minutes old, and use an unseen nonce. Anything else is deleted and logged ("ignored a request"). Where the code
comes from (remote, branch, deploy key) and the limits are set in `updater/updater.conf` on the host. That folder
is not mounted into any container.

## Install / remove

```bash
cd /opt/cyber-scout                                   # your stack folder (app/, data/, .env, docker-compose.yml)
sudo ./app/deploy/updater/install-updater.sh          # system units, run as the folder's owner
#   or, without sudo:  ./app/deploy/updater/install-updater.sh --user   (then: sudo loginctl enable-linger $USER)
sudo ./app/deploy/updater/install-updater.sh --uninstall      # add --user if you installed it that way
```

`install.sh` offers to do this at the end (`CS_UPDATER=y|n`). The installer:

* copies `cs-updater.py` to `<stack>/updater/`;
* writes `<stack>/updater/updater.conf` (only the first time; `REMOTE` defaults to `app/`'s git origin);
* installs `<prefix>-updater.service` (oneshot), `.path` (starts it as soon as a request appears) and `.timer`
  (every minute: heartbeat, a request the path unit missed, and a daily check). `<prefix>` is `CS_PREFIX`,
  `cyber-scout` by default.

Requirements: systemd, python3 ≥ 3.8, git, Docker Compose v2. Works with the CPU-only compose too.
Removing the units keeps `<stack>/updater/` (state, log, DB backups, key); delete it yourself if you want.

## What it does

* **check**: fetches `BRANCH` from `REMOTE` and reports the current and latest commit, the commit subjects in
  between, which images are rebuilt, and the free disk needed.
* **apply**:
  1. Refuses if the docker disk is short on space. Rebuilding the worker's ~3 GB layer needs
     `HEAVY_BUILD_GB` + `MIN_FREE_GB`; code-only changes need ~0.3 GB more than `MIN_FREE_GB`. A worker image
     change counts as "heavy" only if the Dockerfile changed *before* its first `COPY` of repo files.
  2. Backs up `data/app.db` to `updater/backups/`, using sqlite's online backup and keeping 5 copies.
  3. Checks out the new commit into `app/` on local branch `cs-deployed`.
  4. Copies `deploy/docker-compose*.yml` into the stack folder if they changed. The old file is kept as
     `.bak-<time>`.
  5. Tags the running images `:cs-rollback`, runs `docker compose build`, then `up -d`.
  6. Waits for health: api `healthy`, worker and recorder running without restarts, and web returning 200 on
     `/` and `/app/v1/health`.
  7. Removes only this project's images that nothing uses any more. It removes them by id; it never runs
     `docker image prune`.

  If any step fails, it **rolls back by itself**: previous commit, compose files and image tags, then
  `up -d` and a health check. It reports `rolled_back`, plus the last log lines of the container that failed.
* **rollback**: goes back to the previous successful version. If its images are still tagged `:cs-rollback`,
  nothing is rebuilt.
* It never edits `.env`, so `PAUSE_PROCESSING=1` still applies after the restart. It writes nothing in `data/`
  except the status file; the database backup goes to `updater/backups/`. Only one run happens at a time
  (`flock`).
* **First run on a copied `app/`** (rsync or `git archive`): `app/` becomes a git checkout without changing any
  file. If its contents match a published commit exactly, that commit is used. Otherwise the files are saved as
  a local commit on top of the closest published commit. Later hand edits to tracked files are saved the same
  way before an update, so a rollback brings them back.
* After a successful update it also refreshes its own installed copy from `app/deploy/updater/`. If the unit
  files changed, re-run the installer.

**Also update yt-dlp** (checkbox): re-pins `live/requirements-ytdlp.txt` to the newest yt-dlp, with hashes.
`pip-compile --generate-hashes` runs in a throwaway container of the worker's own base image
(`--pull never`, `--cap-drop ALL`, a non-root user, a tmpfs). Nothing is installed on the host. The output is
accepted only if every line is `name==version`, a sha256 hash or a comment. The result is committed locally
(`Local: yt-dlp …`) and only the small yt-dlp layer of the worker image is rebuilt. Later updates keep the
newer pin until the published file catches up. Set `YTDLP=0` in `updater.conf` to turn the option off.

## Private repo: read-only deploy key

```bash
cd /opt/cyber-scout          # your stack folder
ssh-keygen -t ed25519 -N '' -C "cyber-scout updater $(hostname)" -f updater/deploy_key
chmod 600 updater/deploy_key
cat updater/deploy_key.pub
```

Add the public key on GitHub under **repo → Settings → Deploy keys → Add deploy key**, and leave
"Allow write access" **off**. With the GitHub CLI:
`gh repo deploy-key add updater/deploy_key.pub -R <owner>/<repo> -t "updater $(hostname)"` (read-only by default).
Then set these in `updater/updater.conf`:

```
REMOTE=git@github.com:<owner>/<repo>.git
SSH_KEY=/opt/cyber-scout/updater/deploy_key
```

Test it with `python3 updater/cs-updater.py --stack /opt/cyber-scout check`. ssh only uses
`updater/known_hosts`, which the installer seeds with GitHub's published ed25519 host key
(`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`); host key checking is strict. A public repo works over
`https://` without a key.

## Shell

```bash
python3 updater/cs-updater.py --stack /opt/cyber-scout status            # what the Admin page sees
python3 updater/cs-updater.py --stack /opt/cyber-scout check | apply [--ytdlp] | rollback
tail -f updater/update.log                                               # full log incl. docker build output
```

## Security notes

* The containers can write to `data/`, so the request and status files are untrusted on both sides:
  * The updater opens `data/config` and the request with `O_NOFOLLOW`, always writes the status file by
    rename, and keeps its real state in `updater/state.json`.
  * The api reads the status without following symlinks and passes on only known fields, with checked types and
    lengths.
* Worst case, a compromised container can ask for a check, an update to the configured branch, or a rollback to
  the previous version. It cannot choose the code, the commands or the arguments.
* Members of the `docker` group effectively have root on the host. The updater runs as the account that already
  owns the stack and is in that group. The system unit also sets `NoNewPrivileges`, `ProtectSystem=full`,
  `PrivateTmp` and related options.
* The API endpoints are admin-only and CSRF-checked. They allow 6 requests a minute, are audited
  (`update.check` / `update.apply` / `update.rollback`), and keep working for admins during a lockdown.

## Limits

* The database is backed up but **not** restored on a failed update. Data is left alone on purpose. If an older
  version can't read a migrated database, restore it by hand from `updater/backups/` with the stack stopped.
* A match the worker is processing during the restart is redone afterwards.
* The disk estimate is conservative. Free space is measured on the filesystem holding Docker's data root.
* The "heavy" rebuild needs about 3.5 GB of free disk. On a nearly full disk, free space first
  (`docker image ls`, then remove old images you don't need).
