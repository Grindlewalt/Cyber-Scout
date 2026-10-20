#!/usr/bin/env python3
"""Cyber-Scout host-side updater: the only thing that touches git and docker for Admin -> Updates.

Runs on the HOST as the stack's owner (a normal user in the `docker` group), started by systemd
(<name>.path when the web app drops a request, <name>.timer every minute for the heartbeat). Never runs in a
container, and no container gets the docker socket: the api can only *ask*, by writing
data/config/update-request.json with this fixed schema (anything else is ignored and deleted):

    {"v": 1, "action": "check" | "apply" | "rollback" | "env", "nonce": "<32 hex>", "at": <unix s>,
     "by": {"id": <int>, "name": "<display name>"}, "ytdlp": <bool, apply only>,
     "env": {"<KEY>": "<value>" | null, ...}  <- action "env" only (Admin -> Server)}

There are no commands, URLs, refs, paths or arguments in a request. "env" may only name keys from ENV_KEYS below
(infrastructure knobs, never secrets, ports, paths or ids), each value checked against its kind; the updater writes
them into .env, recreates the containers and puts the old .env back if the app doesn't come up healthy.
The git remote/branch, deploy key and limits come from updater/updater.conf on the host (outside data/, invisible to
the containers). Progress and results go to data/config/update-status.json (read by the api) and updater/update.log.

    cs-updater.py --stack DIR run                  systemd: heartbeat, handle a pending request, daily check
    cs-updater.py --stack DIR check | apply [--ytdlp] | rollback | status      the same, from a shell
    cs-updater.py --stack DIR env KEY=VALUE ... | KEY=                          Admin -> Server, from a shell

Python 3.8+ standard library only. See deploy/updater/README.md.
"""
import argparse, errno, fcntl, json, os, re, secrets, shlex, shutil, sqlite3, stat, subprocess, sys, threading, \
    time, urllib.error, urllib.parse, urllib.request

UPDATER_VERSION = "2"
FEATURES = ("env",)
ACTIONS = ("check", "apply", "rollback", "env")
REQ_NAME, STATUS_NAME = "update-request.json", "update-status.json"
REQ_KEYS = {"v", "action", "nonce", "at", "by", "ytdlp"}
NONCE_RE = re.compile(r"\A[0-9a-f]{32}\Z")
SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_REQ_BYTES = 4096
REQ_MAX_AGE = 900                 # a request older than 15 min (e.g. written while the updater was off) is ignored
PROGRESS_KEEP = 150
ROLLBACK_TAG = "cs-rollback"      # <image repo>:cs-rollback keeps the previous version's images for a fast rollback
LOCAL_BRANCH = "cs-deployed"
UPSTREAM_REF = "refs/cs-updater/upstream"

DEFAULTS = {
    "REMOTE": "https://github.com/Grindlewalt/Cyber-Scout.git",
    "BRANCH": "main",
    "SSH_KEY": "",                # private deploy key for an ssh REMOTE (git@github.com:owner/repo.git)
    "MIN_FREE_GB": "1.0",         # always leave this much free on the docker disk after building
    "HEAVY_BUILD_GB": "3.5",      # the worker image's big layer (onnxruntime-gpu, opencv, ...) is rebuilt
    "LIGHT_BUILD_GB": "0.3",      # code / small layers only
    "HEALTH_TIMEOUT": "300",      # seconds to wait for api healthy + web 200 + worker running
    "BUILD_TIMEOUT": "5400",
    "AUTO_CHECK_HOURS": "24",     # look for updates by itself this often (0 = only when asked)
    "DB_BACKUPS_KEEP": "5",
    "YTDLP": "1",                 # allow "also update yt-dlp" (0 = refuse it)
}
CONF_RE = {
    "REMOTE": re.compile(r"\A(?:https://|ssh://|git@|file://|/)[A-Za-z0-9@:/._~+-]{1,300}\Z"),
    "BRANCH": re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,99}\Z"),
    "SSH_KEY": re.compile(r"\A(?:/[A-Za-z0-9._/-]{1,300})?\Z"),
    "YTDLP": re.compile(r"\A[01]\Z"),
}
NUMERIC = ("MIN_FREE_GB", "HEAVY_BUILD_GB", "LIGHT_BUILD_GB", "HEALTH_TIMEOUT", "BUILD_TIMEOUT", "AUTO_CHECK_HOURS",
           "DB_BACKUPS_KEEP")
# what each part of the repo ends up in (for "does this update rebuild the worker image" and the disk estimate)
WORKER_PATHS = ("Dockerfile", "live/", "src/", "events/", ".dockerignore")
API_PATHS = ("server/", "live/sources.py", "live/layout.py", ".dockerignore")
WEB_PATHS = ("web/", ".dockerignore")
# Admin -> Server: the only .env keys the web app may read or change. kind: bool "0"/"1" | int (min..max) | choice |
# a text format from ENV_FORMATS (server/routers/update.py has the same regexes; a test keeps them equal). api: the
# key also reaches the api container. Every .env key reaches the env_file services (worker, recorder, processors), so
# `docker compose up -d` recreates those on any change (and the api for api keys). Pausing is not here: it's a live
# switch in Admin -> Settings (no restart).
ENV_FORMATS = {
    "gpus": r"\A\d{1,2}(?:,\d{1,2}){0,7}\Z",                                         # 0 | 1 | 0,1
    "mem": r"\A[1-9]\d{0,5}[mg]\Z",                                                  # 12g | 768m
    "tz": r"\A[A-Za-z][A-Za-z0-9_+-]{0,30}(?:/[A-Za-z0-9_+-]{1,30}){0,2}\Z",           # America/Los_Angeles
    "hosts": r"\A[A-Za-z0-9.:-]{1,100}(?:/\d{1,3})?(?:,[A-Za-z0-9.:-]{1,100}(?:/\d{1,3})?){0,19}\Z",   # host / CIDR list
}
ENV_KEYS = {
    "GPU_IDS": {"kind": "gpus", "default": "0"},
    "PROCESSORS_GPU0": {"kind": "int", "min": 0, "max": 8, "default": "0"},
    "PROCESSORS_GPU1": {"kind": "int", "min": 0, "max": 8, "default": "0"},
    "WORKER_TRACK": {"kind": "choice", "choices": ["auto", "1", "0"], "default": "auto"},
    "WORKER_MEM_LIMIT": {"kind": "mem", "default": "12g"},
    "PROCESSOR_MEM_LIMIT": {"kind": "mem", "default": "6g"},
    "MEDIA_PROXIES": {"kind": "bool", "default": "1"},
    "PROXY_ENCODER": {"kind": "choice", "choices": ["auto", "nvenc", "cpu"], "default": "auto"},
    "PROXY_GPU": {"kind": "gpus", "default": "1"},
    "YT_FORCE_IPV4": {"kind": "bool", "default": "1"},
    "CS_PROBE_LINKS": {"kind": "bool", "default": "1", "api": True},
    "CS_URL_ALLOW": {"kind": "hosts", "default": "", "api": True},
    "LIVESTREAM": {"kind": "bool", "default": "1"},
    "LIVE_BUDGET_GB": {"kind": "int", "min": 1, "max": 100000, "default": "60"},
    "LIVE_MIN_FREE_GB": {"kind": "int", "min": 0, "max": 100000, "default": "30"},
    "RECORDER_MEM_LIMIT": {"kind": "mem", "default": "8g"},
    "TZ": {"kind": "tz", "default": "UTC", "api": True},
    "SESSION_IDLE_DAYS": {"kind": "int", "min": 1, "max": 365, "default": "7", "api": True},
    "SESSION_MAX_DAYS": {"kind": "int", "min": 1, "max": 3650, "default": "30", "api": True},
}
ENV_LINE_RE = re.compile(r"\A\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)\Z")
ENV_MARK = "# Set in Admin -> Server (cs-updater)"
ENV_BACKUPS_KEEP = 10
COMPOSE_NAME_RE = re.compile(r"\Adocker-compose(?:\.[a-z0-9-]+)?\.yml\Z")
IMAGE_RE = re.compile(r"\A[a-z0-9][a-z0-9._/-]{0,200}(?::[A-Za-z0-9._-]{1,128})?(?:@sha256:[0-9a-f]{64})?\Z")


class Fail(Exception):
    """An expected failure with a message meant for the admin."""


def now():
    return time.time()


def clean(s, n=300):
    return CTRL_RE.sub(" ", str(s))[:n]


def tail(text, n=12, width=4000):
    return "\n".join([l for l in (text or "").strip().splitlines() if l.strip()][-n:])[-width:]


def last_lines(logs, n=3):
    """Last few lines of `docker compose logs` without the 'name  | ' prefixes, one line."""
    ls = [l.split("|", 1)[-1].strip() for l in (logs or "").splitlines() if l.strip()]
    return clean(" / ".join(ls[-n:]), 500)


def load_conf(path):
    conf = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                k, sep, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if not sep or k not in DEFAULTS:
                    raise Fail(f"{path}:{i}: unknown setting {clean(k, 40)!r}")
                conf[k] = v
    for k, rx in CONF_RE.items():
        if not rx.match(conf[k]):
            raise Fail(f"{path}: bad {k} value")
    if ".." in conf["BRANCH"] or conf["BRANCH"].endswith((".lock", "/")):
        raise Fail(f"{path}: bad BRANCH value")
    for k in NUMERIC:
        try:
            conf[k] = float(conf[k])
            assert 0 <= conf[k] < 1e6
        except (ValueError, AssertionError):
            raise Fail(f"{path}: {k} must be a number")
    return conf


class Updater:
    def __init__(self, stack, conf_path=None):
        self.stack = os.path.realpath(stack)
        self.app = os.path.join(self.stack, "app")
        self.data = os.path.join(self.stack, "data")
        self.udir = os.path.join(self.stack, "updater")
        for p, what in ((self.app, "app/"), (self.data, "data/"), (os.path.join(self.stack, "docker-compose.yml"),
                                                                    "docker-compose.yml")):
            if not os.path.exists(p):
                raise Fail(f"{self.stack} doesn't look like a Cyber-Scout stack folder (no {what})")
        os.makedirs(self.udir, mode=0o750, exist_ok=True)
        self.conf = load_conf(conf_path or os.path.join(self.udir, "updater.conf"))
        self.state_path = os.path.join(self.udir, "state.json")
        self.log_path = os.path.join(self.udir, "update.log")
        self.state = self._load_state()
        self._wlock = threading.Lock()
        self._lockf = None
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "GIT_"))}
        env.update(GIT_TERMINAL_PROMPT="0", LC_ALL="C", GIT_CONFIG_NOSYSTEM="1")
        if self.conf["SSH_KEY"]:
            kh = os.path.join(self.udir, "known_hosts")
            env["GIT_SSH_COMMAND"] = " ".join(shlex.quote(x) for x in (
                "ssh", "-i", self.conf["SSH_KEY"], "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + kh, "-F", "/dev/null"))
        self.env = env

    # ------------------------------------------------------------------ state, status file, log
    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                st = json.load(f)
                if isinstance(st, dict):
                    return st
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.log("state.json unreadable; starting fresh")
        return {"state": "idle", "progress": [], "nonces": []}

    def _save_state(self):
        tmp = f"{self.state_path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1, sort_keys=True)
        os.replace(tmp, self.state_path)

    def log(self, msg):
        try:
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > 5_000_000:
                os.replace(self.log_path, self.log_path + ".1")
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.rstrip() + "\n")
        except OSError:
            pass

    def say(self, msg):
        """One progress line: log file + the status file the Admin page polls."""
        msg = clean(msg, 400)
        self.log(msg)
        if sys.stdout.isatty():
            print(msg, flush=True)
        p = self.state.setdefault("env_progress" if self.state.get("action") == "env" else "progress", [])
        p.append(time.strftime("%H:%M:%S ") + msg)
        del p[:-PROGRESS_KEEP]
        self.write_status()

    def _cfg_dir(self):
        """data/config opened without following symlinks (the containers can write data/: a planted symlink must
        never redirect our writes)."""
        dfd = os.open(self.data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            try:
                os.mkdir("config", 0o755, dir_fd=dfd)
            except FileExistsError:
                pass
            return os.open("config", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        finally:
            os.close(dfd)

    def status_doc(self):
        s = self.state
        return {"v": 1, "updater_version": UPDATER_VERSION, "heartbeat": now(), "state": s.get("state", "idle"),
                "action": s.get("action"), "branch": self.conf["BRANCH"], "current": s.get("current"),
                "latest": s.get("latest"), "checked_at": s.get("checked_at"), "changelog": s.get("changelog") or [],
                "changelog_more": s.get("changelog_more", 0), "touches": s.get("touches"), "disk": s.get("disk"),
                "previous": {k: v for k, v in (s.get("previous") or {}).items() if k in ("sha", "date", "subject")}
                or None,
                "progress": s.get("progress") or [], "result": s.get("result"), "request": s.get("request"),
                "rejected": s.get("rejected"), "started_at": s.get("started_at"), "finished_at": s.get("finished_at"),
                "paused": self._env_flag("PAUSE_PROCESSING"), "ytdlp": s.get("ytdlp"),
                "ytdlp_allowed": self.conf["YTDLP"] == "1", "features": list(FEATURES), "env": self.env_doc(),
                "env_progress": s.get("env_progress") or [], "env_result": s.get("env_result")}

    def write_status(self):
        with self._wlock:
            self._save_state()
            body = json.dumps(self.status_doc(), indent=1, sort_keys=True).encode()
            cfd = self._cfg_dir()
            tmp = f".update-status.{os.getpid()}.{secrets.token_hex(4)}.tmp"
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644,
                             dir_fd=cfd)
                with os.fdopen(fd, "wb") as f:
                    f.write(body)
                os.replace(tmp, STATUS_NAME, src_dir_fd=cfd, dst_dir_fd=cfd)   # rename never follows a symlink
            finally:
                try:
                    os.unlink(tmp, dir_fd=cfd)
                except OSError:
                    pass
                os.close(cfd)

    def _env_flag(self, key):
        try:
            with open(os.path.join(self.stack, ".env"), encoding="utf-8") as f:
                vals = [l.split("=", 1)[1].strip().strip('"') for l in f if l.strip().startswith(key + "=")]
            return bool(vals) and vals[-1] == "1"
        except OSError:
            return False

    # ------------------------------------------------------------------ the request file (untrusted input)
    def read_request(self):
        """-> validated request dict or None. The file is always consumed; anything off-schema is only logged."""
        cfd = self._cfg_dir()
        try:
            try:
                fd = os.open(REQ_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=cfd)
            except FileNotFoundError:
                return None
            except OSError as e:                         # ELOOP: a symlink -> never followed
                self._unlink_req(cfd)
                return self._reject(f"request file is not a plain file ({errno.errorcode.get(e.errno, e.errno)})")
            try:
                st = os.fstat(fd)
                raw = os.read(fd, MAX_REQ_BYTES + 1) if stat.S_ISREG(st.st_mode) else None
            finally:
                os.close(fd)
            self._unlink_req(cfd)
        finally:
            os.close(cfd)
        if raw is None:
            return self._reject("request file is not a regular file")
        if len(raw) > MAX_REQ_BYTES:
            return self._reject("request file too big")
        try:
            req = validate_request(raw, now(), self.state.get("nonces") or [])
        except ValueError as e:
            return self._reject(str(e))
        if req["ytdlp"] and self.conf["YTDLP"] != "1":
            return self._reject("yt-dlp updates are turned off on this server (YTDLP=0 in updater.conf)")
        self.state["nonces"] = ((self.state.get("nonces") or []) + [req["nonce"]])[-50:]
        return req

    def _unlink_req(self, cfd):
        try:
            os.unlink(REQ_NAME, dir_fd=cfd)
        except FileNotFoundError:
            pass
        except OSError as e:
            self.log(f"couldn't remove the request file: {e}")

    def _reject(self, why):
        self.log("ignored a request: " + why)
        self.state["rejected"] = {"at": now(), "reason": clean(why, 200)}
        self.write_status()
        return None

    # ------------------------------------------------------------------ subprocess helpers (argv lists, no shell)
    def run(self, argv, cwd=None, timeout=600, check=True, env=None, log_output=False):
        e = dict(self.env)
        e.update(env or {})
        try:
            p = subprocess.run(argv, cwd=cwd or self.stack, env=e, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Fail(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} took longer than {int(timeout)} s")
        except FileNotFoundError:
            raise Fail(f"{argv[0]} is not installed on this server")
        if log_output and (p.stdout or p.stderr):
            self.log("$ " + " ".join(shlex.quote(a) for a in argv) + "\n" + p.stdout[-200000:] + p.stderr[-200000:])
        if check and p.returncode:
            raise Fail(f"{' '.join(argv[:3])} failed: {tail(p.stderr or p.stdout, 6, 800)}")
        return p

    def git(self, *args, check=True, timeout=600, env=None):
        return self.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "advice.detachedHead=false",
                         "-c", "safe.directory=" + self.app, "-C", self.app, *args],
                        check=check, timeout=timeout, env=env).stdout.strip()

    def dc(self, *args, timeout=600, check=True, log_output=False):
        return self.run(["docker", "compose", *args], timeout=timeout, check=check, log_output=log_output)

    def docker(self, *args, timeout=120, check=True):
        return self.run(["docker", *args], timeout=timeout, check=check)

    # ------------------------------------------------------------------ git
    def commit_info(self, rev):
        out = self.git("show", "-s", "--format=%H%x1f%ct%x1f%s", rev + "^{commit}")
        sha, ct, subj = out.split("\x1f", 2)
        return {"sha": sha, "date": int(ct), "subject": clean(subj, 200)}

    def head(self):
        return self.git("rev-parse", "--verify", "HEAD^{commit}")

    def fetch(self, quiet=False):
        (self.log if quiet else self.say)(f"Fetching {self.conf['BRANCH']} from {redact(self.conf['REMOTE'])}")
        try:
            self.git("fetch", "--quiet", "--no-tags", "--", self.conf["REMOTE"],
                     f"+refs/heads/{self.conf['BRANCH']}:{UPSTREAM_REF}", timeout=300)
        except Fail as e:
            hint = ""
            if "Permission denied" in str(e) or "Repository not found" in str(e) or "could not read" in str(e):
                hint = (" The repository is private: set REMOTE=git@github.com:<owner>/<repo>.git and SSH_KEY in "
                        "updater/updater.conf and add the key's .pub as a read-only deploy key (see "
                        "app/deploy/updater/README.md).")
            raise Fail(f"Couldn't fetch the code: {e}.{hint}")
        return self.git("rev-parse", "--verify", UPSTREAM_REF + "^{commit}")

    def _worktree_tree(self, tracked_only=False):
        """Tree object of app/ as it is on disk (respects .gitignore), without touching the real index."""
        idx = os.path.join(self.udir, f"tmp-index-{os.getpid()}")
        try:
            if tracked_only:
                shutil.copyfile(os.path.join(self.app, ".git", "index"), idx)
            env = {"GIT_INDEX_FILE": idx}
            self.git("add", "-u" if tracked_only else "-A", "--", ".", env=env, timeout=300)
            return self.git("write-tree", env=env)
        finally:
            if os.path.exists(idx):
                os.unlink(idx)

    def ensure_repo(self):
        """First run on a stack whose app/ is a plain copy (rsync / git archive): make it a git checkout of the
        commit it matches -- or, if it matches none, of a local snapshot commit of exactly these files (so the
        files on disk and the running containers stay as they are, and a rollback can come back to them)."""
        if os.path.isdir(os.path.join(self.app, ".git")):
            if not self.git("remote", check=False):
                self.git("remote", "add", "origin", self.conf["REMOTE"], check=False)   # keeps install.sh working
            return
        self.say("First run: turning app/ into a git checkout (no files change)")
        try:
            self.git("init", "--quiet")
            self.git("remote", "add", "origin", self.conf["REMOTE"])
            up = self.fetch()
            tree = self._worktree_tree()
            cands = [l.split() for l in self.git("log", "--format=%H %T", "--max-count=400", up).splitlines()]
            match = next((c for c, t in cands if t == tree), None)
            if match:
                sha = match
                self.say(f"app/ is exactly commit {sha[:7]}")
            else:
                best, bestn = None, None
                for c, _t in cands[:200]:
                    n = len(self.git("diff-tree", "-r", "--name-only", c, tree).splitlines())
                    if bestn is None or n < bestn:
                        best, bestn = c, n
                sha = self.git("commit-tree", tree, "-p", best, "-m",
                               f"Local copy of app/ before the first update ({bestn} files differ from {best[:7]})",
                               env=_bot_env())
                self.say(f"app/ matches no published commit; saved it as local commit {sha[:7]} "
                         f"(closest: {best[:7]}, {bestn} files differ)")
            self.git("update-ref", "refs/heads/" + LOCAL_BRANCH, sha)
            self.git("symbolic-ref", "HEAD", "refs/heads/" + LOCAL_BRANCH)
            self.git("reset", "--quiet")                      # index = HEAD; files untouched
        except BaseException:
            shutil.rmtree(os.path.join(self.app, ".git"), ignore_errors=True)
            raise

    def snapshot_if_dirty(self):
        """Hand edits to tracked files in app/ are saved as a local commit before an update replaces them."""
        if not self.git("status", "--porcelain", "--untracked-files=no"):
            return
        tree = self._worktree_tree(tracked_only=True)
        sha = self.git("commit-tree", tree, "-p", "HEAD", "-m", "Local edits in app/ (saved by the updater)",
                       env=_bot_env())
        self.git("update-ref", "HEAD", sha)
        self.git("reset", "--quiet")
        self.say(f"app/ had local edits: saved them as commit {sha[:7]} (a rollback brings them back)")

    def checkout(self, sha):
        self.git("checkout", "--quiet", "--force", "-B", LOCAL_BRANCH, sha)

    # ------------------------------------------------------------------ what an update changes
    def touches(self, frm, to):
        files = self.git("diff", "--name-only", frm, to).splitlines()
        hit = lambda pre: any(f == p or (p.endswith("/") and f.startswith(p)) for f in files for p in pre)
        worker = None
        if hit(WORKER_PATHS):
            worker = "light"
            if "Dockerfile" in files and heavy_prefix(self.git("show", f"{frm}:Dockerfile", check=False)) != \
                    heavy_prefix(self.git("show", f"{to}:Dockerfile", check=False)):
                worker = "heavy"
        return {"worker": worker, "api": hit(API_PATHS), "web": hit(WEB_PATHS),
                "compose": any(f.startswith("deploy/docker-compose") for f in files), "files": len(files)}

    def _active(self, t):
        """A stack without the worker/recorder services (turned off with a compose profile) doesn't build that image."""
        if t and t.get("worker") and not {"worker", "recorder"} & set(self._project()["services"]):
            t = dict(t, worker=None)
        return t

    def disk(self, t):
        need = self.conf["MIN_FREE_GB"]
        if t:
            need += {"heavy": self.conf["HEAVY_BUILD_GB"], "light": self.conf["LIGHT_BUILD_GB"]}.get(t.get("worker"), 0)
            need += 0.3 if t.get("api") else 0
            need += 0.05 if t.get("web") else 0
        path, free = self.free_space()
        return {"free_gb": round(free, 2), "need_gb": round(need, 2), "ok": free >= need, "path": path}

    def free_space(self):
        root = "/var/lib/docker"
        p = self.run(["docker", "info", "--format", "{{.DockerRootDir}}"], check=False, timeout=30)
        if p.returncode == 0 and p.stdout.strip().startswith("/"):
            root = p.stdout.strip()
        best = None
        for path in (root, self.stack):
            q = path
            while q and q != "/" and not os.path.exists(q):
                q = os.path.dirname(q)
            try:
                v = os.statvfs(q or "/")
            except OSError:
                continue
            g = v.f_bavail * v.f_frsize / 1e9
            if best is None or g < best[1]:
                best = (path, g)
        return best or ("/", 0.0)

    # ------------------------------------------------------------------ actions
    def check(self, auto=False):
        if not auto:
            self._begin("check", "checking")
        self.ensure_repo()
        up = self.fetch(quiet=auto)
        cur = self.head()
        cinfo, uinfo = self.commit_info(cur), self.commit_info(up)
        cinfo["local"] = not self._is_ancestor(cur, up)
        commits = []
        for l in self.git("log", "--format=%H%x1f%ct%x1f%s", "--max-count=201", f"{cur}..{up}").splitlines():
            sha, ct, subj = l.split("\x1f", 2)
            commits.append({"sha": sha, "date": int(ct), "subject": clean(subj, 200)})
        t = self._active(self.touches(cur, up)) if commits else None
        s = self.state
        s.update(current=cinfo, latest=uinfo, checked_at=now(), changelog=commits[:200],
                 changelog_more=max(0, len(commits) - 200), touches=t, disk=self.disk(t))
        s["ytdlp"] = {"pinned": ytdlp_pin(self.app)}
        msg = (f"{len(commits)} update{'s' if len(commits) != 1 else ''} available: {cur[:7]} -> {up[:7]}"
               if commits else f"Up to date ({cur[:7]})")
        if t:
            msg += "; rebuilds " + ", ".join(describe_touches(t)) + f"; needs ~{s['disk']['need_gb']} GB free " \
                   f"({s['disk']['free_gb']} GB free)"
        if auto:
            if s.get("state") in ("idle", "ready", "done"):
                s["state"] = "ready" if commits else "idle"
            self.log("auto check: " + msg)
            self.write_status()
        else:
            self.say(msg)
            self._finish("ready" if commits else "idle", True, msg)
        return commits

    def _is_ancestor(self, a, b):
        return self.run(["git", "-C", self.app, "merge-base", "--is-ancestor", a, b], check=False,
                        timeout=60).returncode == 0

    def apply(self, ytdlp=False):
        self._begin("apply", "applying")
        self.ensure_repo()
        up = self.fetch()
        self.snapshot_if_dirty()
        cur = self.head()
        changes = not self._is_ancestor(up, cur)
        if not changes and not ytdlp:
            self.check(auto=True)
            return self._finish("idle", True, f"Already up to date ({cur[:7]}).")
        target = up if changes else cur
        t = self.touches(cur, target) or {}
        if ytdlp:
            t["worker"] = t.get("worker") or "light"
        t = self._active(t)
        d = self.disk(t)
        self.state["disk"] = d
        if not d["ok"]:
            raise Fail(f"Not enough disk space: {d['free_gb']} GB free on {d['path']}, this update needs about "
                       f"{d['need_gb']} GB ({', '.join(describe_touches(t)) or 'rebuild'}"
                       f"{' -- the worker image is rebuilt from scratch' if t.get('worker') == 'heavy' else ''}). "
                       "Free some space (e.g. old Docker images you no longer need) and try again. Nothing was changed.")
        self.say(f"Updating {cur[:7]} -> {target[:7]}: rebuilds {', '.join(describe_touches(t)) or 'nothing big'} "
                 f"({d['free_gb']} GB free, needs ~{d['need_gb']} GB)")
        self._deploy(cur, target, ytdlp=ytdlp, carry_ytdlp=not ytdlp)

    def rollback(self):
        self._begin("rollback", "applying")
        prev = self.state.get("previous")
        if not prev or not SHA_RE.match(prev.get("sha", "")):
            raise Fail("Nothing to roll back to (no earlier successful update on this server).")
        self.ensure_repo()
        cur = self.head()
        if self.run(["git", "-C", self.app, "cat-file", "-e", prev["sha"] + "^{commit}"], check=False,
                    timeout=60).returncode:
            raise Fail(f"The previous version {prev['sha'][:7]} is no longer in app/.git.")
        self.snapshot_if_dirty()
        cur = self.head()
        self.say(f"Rolling back {cur[:7]} -> {prev['sha'][:7]}")
        self._deploy(cur, prev["sha"], images=prev.get("images") or {}, is_rollback=True)

    # ------------------------------------------------------------------ the deploy / restore machinery
    def _project(self):
        p = self.dc("config", "--format", "json", timeout=60)
        try:
            cfg = json.loads(p.stdout)
        except ValueError:
            raise Fail("docker compose config didn't return JSON (Docker Compose too old? needs v2.20+)")
        svcs = cfg.get("services") or {}
        images = sorted({v["image"] for v in svcs.values() if v.get("build") and IMAGE_RE.match(v.get("image", ""))})

        def scale(v):
            try:
                return int((v.get("deploy") or {}).get("replicas", 1))
            except (TypeError, ValueError):
                return 1
        web = None
        for pt in (svcs.get("web") or {}).get("ports") or []:
            if int(pt.get("target", 0)) == 80 and str(pt.get("published", "")).isdigit():
                host = pt.get("host_ip") or ""
                host = "127.0.0.1" if host in ("", "0.0.0.0") else "[::1]" if host == "::" else \
                    f"[{host}]" if ":" in host else host            # WEB_BIND=<LAN / Tailscale IP>
                web = f"http://{host}:{int(pt['published'])}"
        return {"name": cfg.get("name"), "services": sorted(k for k, v in svcs.items() if scale(v) > 0),
                "images": images, "web": web}

    def image_id(self, ref):
        p = self.docker("image", "inspect", "--format", "{{.Id}}", ref, check=False)
        return p.stdout.strip() if p.returncode == 0 else None

    def _compose_files(self):
        out = {}
        for n in sorted(os.listdir(self.stack)):
            if COMPOSE_NAME_RE.match(n) and os.path.isfile(os.path.join(self.stack, n)):
                with open(os.path.join(self.stack, n), "rb") as f:
                    out[n] = f.read()
        return out

    def _sync_compose(self):
        """Copy app/deploy/docker-compose*.yml to the stack folder when they changed (old one -> .bak-<time>)."""
        src = os.path.join(self.app, "deploy")
        for n in sorted(os.listdir(src)) if os.path.isdir(src) else []:
            if not COMPOSE_NAME_RE.match(n):
                continue
            with open(os.path.join(src, n), "rb") as f:
                new = f.read()
            dst = os.path.join(self.stack, n)
            old = open(dst, "rb").read() if os.path.isfile(dst) else None
            if old == new:
                continue
            if old is not None:
                bak = f"{dst}.bak-{int(now())}"
                shutil.copy2(dst, bak)
                self.say(f"{n} changed: updated it (previous saved as {os.path.basename(bak)})")
            else:
                self.say(f"{n}: new file")
            _atomic_write(dst, new)

    def _restore_compose(self, saved):
        for n, body in saved.items():
            dst = os.path.join(self.stack, n)
            if not os.path.isfile(dst) or open(dst, "rb").read() != body:
                _atomic_write(dst, body)
                self.say(f"restored the previous {n}")

    def backup_db(self, label):
        src = os.path.join(self.data, "app.db")
        try:
            st = os.lstat(src)
        except FileNotFoundError:
            self.say("No database yet: nothing to back up")
            return None
        if not stat.S_ISREG(st.st_mode):
            raise Fail("data/app.db is not a regular file; not updating")
        bdir = os.path.join(self.udir, "backups")
        os.makedirs(bdir, mode=0o700, exist_ok=True)
        dst = os.path.join(bdir, f"app-{time.strftime('%Y%m%d-%H%M%S')}-{label}.db")
        s = sqlite3.connect("file:" + urllib.parse.quote(src) + "?mode=ro", uri=True, timeout=30)
        try:
            d = sqlite3.connect(dst)
            try:
                s.backup(d)
            finally:
                d.close()
        except sqlite3.Error as e:
            raise Fail(f"Couldn't back up data/app.db ({e}); not updating")
        finally:
            s.close()
        os.chmod(dst, 0o600)
        keep = int(self.conf["DB_BACKUPS_KEEP"])
        olds = sorted(f for f in os.listdir(bdir) if f.startswith("app-") and f.endswith(".db"))
        for f in olds[:-keep] if keep else []:
            os.unlink(os.path.join(bdir, f))
        self.say(f"Backed up the database to updater/backups/{os.path.basename(dst)}")
        return dst

    def _deploy(self, cur, target, ytdlp=False, carry_ytdlp=False, images=None, is_rollback=False):
        proj = self._project()
        saved_compose = self._compose_files()
        cur_ids = {img: self.image_id(img) for img in proj["images"]}
        rb_ids = {img: self.image_id(rb_ref(img)) for img in proj["images"]}
        old_pin = ytdlp_file(self.app)
        cur_info = self.commit_info(cur)
        self.backup_db(target[:7])
        stage = "code"
        try:
            # 1. code + compose files
            self.checkout(target)
            if ytdlp:
                self.bump_ytdlp()
            elif carry_ytdlp and old_pin:
                self.carry_ytdlp(old_pin)
            new_sha = self.head()
            self._sync_compose()
            self.dc("config", "--quiet", timeout=60)
            proj = self._project()
            # 2. images: keep the running ones as :cs-rollback, then build (or reuse the rollback images)
            if not is_rollback:
                for img, iid in cur_ids.items():
                    if iid:
                        self.docker("tag", iid, rb_ref(img))
            stage = "build"
            reuse = is_rollback and images and all(self.image_id(iid) for iid in images.values()) and \
                set(images) >= set(proj["images"])
            if reuse:
                for img, iid in images.items():
                    self.docker("tag", iid, img)
                self.say("Reusing the previous version's images (no rebuild)")
            else:
                self.say("Building images (a few minutes; the site keeps running)")
                self.dc("build", timeout=self.conf["BUILD_TIMEOUT"], log_output=True)
            # 3. restart
            stage = "restart"
            if self._env_flag("PAUSE_PROCESSING"):
                self.say("PAUSE_PROCESSING=1 is set in .env: the worker comes back paused, as before")
            self.say("Restarting the containers (the site blinks for a few seconds)")
            self.dc("up", "-d", "--no-build", timeout=600, log_output=True)
            self.wait_healthy(proj)
        except BaseException as e:
            msg = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"
            self.say(f"Failed while {'building' if stage == 'build' else 'restarting' if stage == 'restart' else 'updating the code'}: {msg}")
            self.say(f"Rolling back to {cur[:7]}")
            try:
                self.checkout(cur)
                self._restore_compose(saved_compose)
                built = {img: self.image_id(img) for img in proj["images"]}
                for img, iid in cur_ids.items():
                    if iid:
                        self.docker("tag", iid, img)
                if not is_rollback:
                    for img, iid in rb_ids.items():
                        if iid:
                            self.docker("tag", iid, rb_ref(img))
                        elif self.image_id(rb_ref(img)):
                            self.docker("rmi", rb_ref(img), check=False)
                if stage == "restart":
                    self.dc("up", "-d", "--no-build", timeout=600, log_output=True)
                    self.wait_healthy(self._project())
                self._drop_images([i for i in built.values() if i and i not in cur_ids.values()
                                   and i not in rb_ids.values()])
                self.state["current"] = cur_info
                self._finish("rolled_back", False, f"Update failed ({msg}). Rolled back to {cur[:7]}; everything "
                                                   "runs the previous version again.", frm=cur, to=target)
            except BaseException as e2:
                m2 = str(e2) if isinstance(e2, Fail) else f"{type(e2).__name__}: {e2}"
                self._finish("failed", False, f"Update failed ({msg}) and the automatic rollback failed too ({m2}). "
                             f"On the server: cd {self.stack} && docker compose ps / logs; the database backup is in "
                             "updater/backups/.", frm=cur, to=target)
            if isinstance(e, KeyboardInterrupt):
                raise
            return
        # success
        stale = [i for i in rb_ids.values() if i and i not in cur_ids.values()]
        if is_rollback:
            for img in proj["images"]:                          # the version we left: no longer needed
                self.docker("rmi", rb_ref(img), check=False)
            self._drop_images([i for i in cur_ids.values() if i and i not in (images or {}).values()])
            self.state["previous"] = None
        else:
            self.state["previous"] = dict(cur_info, images={k: v for k, v in cur_ids.items() if v})
            self._drop_images(stale)
        self.state["current"] = self.commit_info(new_sha)
        self.state["current"]["local"] = not self._is_ancestor(new_sha, UPSTREAM_REF)
        self.self_update()
        try:
            self.check(auto=True)
        except Fail:
            pass
        verb = "Rolled back" if is_rollback else "Updated"
        self._finish("rolled_back" if is_rollback else "done", True,
                     f"{verb} to {new_sha[:7]}: {self.state['current']['subject']}", frm=cur, to=new_sha)

    def _drop_images(self, ids):
        """Remove this project's images that nothing uses any more (by id: never anything else on the host)."""
        for iid in dict.fromkeys(ids):
            p = self.docker("image", "inspect", "--format", "{{json .RepoTags}}", iid, check=False)
            if p.returncode:
                continue
            tags = json.loads(p.stdout or "[]") or []
            if tags:
                continue                                  # still tagged (e.g. by something else): keep
            r = self.docker("rmi", iid, check=False)
            self.log(f"removed unused image {iid[:19]}" if r.returncode == 0 else f"kept {iid[:19]}: {tail(r.stderr, 1)}")

    def wait_healthy(self, proj):
        self.say("Waiting for the app to come up")
        deadline, first_rc, good_since = now() + self.conf["HEALTH_TIMEOUT"], {}, None
        why = "?"
        while now() < deadline:
            ok, why, fatal = self._health_once(proj, first_rc)
            if fatal:
                raise Fail(why)
            if ok:
                good_since = good_since or now()
                if now() - good_since >= 10:
                    self.say("Healthy: " + why)
                    return
            else:
                good_since = None
            time.sleep(3)
        raise Fail(f"not healthy after {int(self.conf['HEALTH_TIMEOUT'])} s: {why}")

    def _health_once(self, proj, first_rc):
        p = self.dc("ps", "-a", "--format", "json", check=False, timeout=60)
        rows = parse_ps(p.stdout)
        byservice = {}
        for r in rows:
            if "com.docker.compose.oneoff=True" not in str(r.get("Labels") or ""):
                byservice.setdefault(r.get("Service"), []).append(r)
        ok_parts = []
        for svc in proj["services"]:
            if not byservice.get(svc):
                return False, f"{svc}: no container yet", False
            for r in byservice[svc]:
                cid = r.get("ID") or r.get("Name")
                ins = self.docker("inspect", "--format", "{{json .State}}|{{.RestartCount}}", cid, check=False)
                if ins.returncode:
                    return False, f"{svc}: not found", False
                js, _, rc = ins.stdout.strip().rpartition("|")
                st = json.loads(js)
                rc = int(rc or 0)
                first_rc.setdefault(cid, rc)
                if st.get("Status") != "running" or st.get("Restarting"):
                    if rc > first_rc[cid] + 1 or st.get("Status") in ("exited", "dead"):
                        logs = self.dc("logs", "--no-color", "--tail", "15", svc, check=False).stdout
                        return False, (f"{svc} keeps crashing ({st.get('Status')}); its last log lines: "
                                       f"{last_lines(logs)}"), True
                    return False, f"{svc}: {st.get('Status')}", False
                health = (st.get("Health") or {}).get("Status")
                if health and health != "healthy":
                    if health == "unhealthy":
                        logs = self.dc("logs", "--no-color", "--tail", "15", svc, check=False).stdout
                        return False, f"{svc} is unhealthy; its last log lines: {last_lines(logs)}", True
                    return False, f"{svc}: {health}", False
                if rc != first_rc[cid]:
                    return False, f"{svc} restarted", False
            n = len(byservice[svc])
            ok_parts.append(f"{svc}{f' x{n}' if n > 1 else ''} {'healthy' if health else 'running'}")
        if proj.get("web"):
            for path, want in (("/", None), ("/app/v1/health", '"ok"')):
                try:
                    with urllib.request.urlopen(proj["web"] + path, timeout=5) as r:
                        body = r.read(4096).decode("utf-8", "replace")
                        if r.status != 200 or (want and want not in body):
                            return False, f"web {path}: HTTP {r.status}", False
                except (urllib.error.URLError, OSError) as e:
                    return False, f"web {path}: {getattr(e, 'code', '') or getattr(e, 'reason', e)}", False
            ok_parts.append("web 200")
        return True, ", ".join(ok_parts), False

    # ------------------------------------------------------------------ yt-dlp
    def bump_ytdlp(self):
        """Re-pin live/requirements-ytdlp.txt to the newest yt-dlp (with hashes), in a throwaway container of the
        worker's own base image (same Python as the image; nothing installed on the host), then commit it locally."""
        path = os.path.join(self.app, "live", "requirements-ytdlp.txt")
        if not os.path.isfile(path):
            self.say("This version has no pinned yt-dlp file: skipping the yt-dlp update")
            return
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^# source: (yt-dlp\[[a-z,]+\])==([0-9.]+)\s*$", text, re.M)
        if not m:
            raise Fail("live/requirements-ytdlp.txt has no '# source:' line; update yt-dlp by hand")
        spec, old = m.group(1), m.group(2)
        base = dockerfile_base(os.path.join(self.app, "Dockerfile"))
        self.say(f"Looking up the newest yt-dlp (now {old}) in a throwaway {base} container")
        script = ('pip install -q --disable-pip-version-check --no-warn-script-location --target /tmp/pt '
                  '"pip-tools>=7.4,<9" >&2 && printf "%s\\n" "$SPEC" > /tmp/in.txt && '
                  'PYTHONPATH=/tmp/pt python -m piptools compile -q --generate-hashes --allow-unsafe --no-header '
                  '--strip-extras -o /tmp/out.txt /tmp/in.txt >&2 && cat /tmp/out.txt')
        p = self.docker("run", "--rm", "--pull", "never", "--cap-drop", "ALL", "--security-opt",
                        "no-new-privileges:true", "--user", "65534:65534", "--memory", "1g", "--tmpfs",
                        "/tmp:rw,exec,size=600m", "-e", "HOME=/tmp", "-e", "SPEC=" + spec, "--entrypoint", "sh",
                        base, "-c", script, timeout=900, check=False)
        if p.returncode:
            raise Fail(f"yt-dlp lookup failed: {tail(p.stderr, 4, 600)}")
        body, new = validate_ytdlp_lock(p.stdout)
        if vtuple(new) <= vtuple(old):
            self.say(f"yt-dlp {old} is already the newest")
            return
        header = text[:m.start()]
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + f"# source: {spec}=={new}\n" + body)
        self._commit_local(f"Local: yt-dlp {new} (was {old}; pinned with hashes by the updater)")
        self.say(f"yt-dlp {old} -> {new} (pinned with hashes; local commit {self.head()[:7]})")

    def carry_ytdlp(self, old_text):
        """Keep a locally bumped yt-dlp across updates until the published version catches up."""
        path = os.path.join(self.app, "live", "requirements-ytdlp.txt")
        new_text = ytdlp_file(self.app)
        a, b = pin_of(old_text), pin_of(new_text) if new_text else None
        if a and b and vtuple(a) > vtuple(b):
            with open(path, "w", encoding="utf-8") as f:
                f.write(old_text)
            self._commit_local(f"Local: keep yt-dlp {a} (the published version pins {b})")
            self.say(f"Kept the newer yt-dlp {a} (this version pins {b})")

    def _commit_local(self, msg):
        self.git("add", "--", "live/requirements-ytdlp.txt")
        self.git("commit", "--quiet", "--no-verify", "-m", msg, env=_bot_env())

    # ------------------------------------------------------------------ keep the installed copy of this script current
    # ------------------------------------------------------------------ .env (Admin -> Server)
    def _env_path(self):
        return os.path.join(self.stack, ".env")

    def _env_text(self):
        try:
            with open(self._env_path(), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def env_doc(self):
        """The allowlisted .env keys and their values for the status file (nothing else in .env is ever reported)."""
        try:
            vals = env_values(self._env_text())
        except (OSError, UnicodeDecodeError) as e:
            return {"error": clean(f"can't read .env: {e}", 200)}
        return {"values": {k: None if vals.get(k) is None else clean(vals[k], 600) for k in ENV_KEYS},
                "schema": ENV_KEYS}

    def _env_backup(self, text):
        bdir = os.path.join(self.udir, "env-backups")
        os.makedirs(bdir, mode=0o700, exist_ok=True)
        name = f"env-{time.strftime('%Y%m%d-%H%M%S')}"
        _atomic_write(os.path.join(bdir, name), text.encode("utf-8"), 0o600)
        for f in sorted(f for f in os.listdir(bdir) if f.startswith("env-"))[:-ENV_BACKUPS_KEEP]:
            os.unlink(os.path.join(bdir, f))
        return name

    def env_apply(self, changes):
        """Write `changes` (validated: validate_env) into .env, recreate what uses them, wait for health; on any failure
        put the previous .env back and recreate again."""
        s = self.state
        s["env_prev_state"] = s.get("state") if s.get("state") not in ("checking", "applying") else "idle"
        s.update(action="env", state="applying", env_progress=[], env_result=None)
        self.write_status()
        path, old = self._env_path(), self._env_text()
        cur = env_values(old)
        diff = {k: v for k, v in changes.items() if cur.get(k) != v}
        if not diff:
            return self._env_finish(True, "Nothing to change: .env already has these values.")
        desc = "; ".join(f"{k} {cur.get(k) if cur.get(k) is not None else '(default)'} -> "
                         f"{v if v is not None else '(default)'}" for k, v in sorted(diff.items()))
        api = any(ENV_KEYS[k].get("api") for k in diff)
        self.say("Changing .env: " + desc)
        existed = os.path.exists(path)
        mode = stat.S_IMODE(os.stat(path).st_mode) if existed else 0o600
        self.say(f"Saved the current .env as updater/env-backups/{self._env_backup(old)}")
        _atomic_write(path, env_edit(old, diff).encode("utf-8"), mode)
        try:
            self.dc("config", "--quiet", timeout=60)
            proj = self._project()
            self.say(f"Restarting the worker, recorder{', api' if api else ''} and any extra tracking jobs")
            self.dc("up", "-d", "--no-build", timeout=600, log_output=True)
            self.wait_healthy(proj)
        except BaseException as e:
            msg = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"
            self.say(f"Failed: {msg}")
            self.say("Putting the previous .env back")
            try:
                if existed:
                    _atomic_write(path, old.encode("utf-8"), mode)
                else:
                    os.unlink(path)
                self.dc("up", "-d", "--no-build", timeout=600, log_output=True)
                self.wait_healthy(self._project())
                self._env_finish(False, f"Couldn't apply the change ({msg}). The previous .env is back and everything "
                                        "runs as before.", rolled_back=True)
            except BaseException as e2:
                m2 = str(e2) if isinstance(e2, Fail) else f"{type(e2).__name__}: {e2}"
                self._env_finish(False, f"Couldn't apply the change ({msg}), and restarting with the previous .env "
                                        f"failed too ({m2}). On the server: cd {self.stack} && docker compose ps / "
                                        "logs; earlier .env files are in updater/env-backups/.")
            if isinstance(e, KeyboardInterrupt):
                raise
            return
        self._env_finish(True, f"Saved and restarted: {desc}.")

    def _env_finish(self, ok, message, rolled_back=False):
        s = self.state
        s.update(state=s.pop("env_prev_state", None) or "idle", action=None,
                 env_result={"ok": ok, "message": clean(message, 1200), "at": now(), "rolled_back": rolled_back})
        self.log(f"RESULT env {'ok' if ok else 'failed'}: {message}")
        if sys.stdout.isatty():
            print(f"=> env: {message}", flush=True)
        self.write_status()

    def self_update(self):
        src = os.path.join(self.app, "deploy", "updater", "cs-updater.py")
        dst = os.path.join(self.udir, "cs-updater.py")
        if not os.path.isfile(src) or not os.path.isfile(dst):
            return
        with open(src, "rb") as a, open(dst, "rb") as b:
            new, old = a.read(), b.read()
        if new == old:
            return
        try:
            compile(new, src, "exec")
        except SyntaxError:
            self.log("new cs-updater.py doesn't compile; keeping the installed one")
            return
        shutil.copy2(dst, dst + ".prev")
        _atomic_write(dst, new, 0o755)
        self.say("Updated the updater itself (takes effect on its next run)")
        self.log("if deploy/updater/*.service|path|timer changed, re-run install-updater.sh to update the units")

    # ------------------------------------------------------------------ run bookkeeping
    def _begin(self, action, state):
        self.state.update(action=action, state=state, progress=[], result=None, started_at=now(), finished_at=None)
        self.write_status()

    def _finish(self, state, ok, message, frm=None, to=None):
        s = self.state
        s.update(state=state, finished_at=now(), result={"action": s.get("action"), "ok": ok, "message": clean(message, 1200),
                                                         "at": now(), "from": frm, "to": to})
        self.log(f"RESULT {state}: {message}")
        if sys.stdout.isatty():
            print(f"=> {state}: {message}", flush=True)
        s["action"] = None
        self.write_status()

    def lock(self, wait=False):
        self._lockf = open(os.path.join(self.udir, "lock"), "w")
        try:
            fcntl.flock(self._lockf, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            return True
        except BlockingIOError:
            return False

    def heartbeat_thread(self):
        stop = threading.Event()

        def beat():
            while not stop.wait(15):
                try:
                    self.write_status()
                except Exception as e:               # never kill a run over a status write
                    self.log(f"heartbeat write failed: {e}")
        th = threading.Thread(target=beat, daemon=True)
        th.start()
        return stop

    def do(self, action, ytdlp=False, request=None, env=None):
        if request:
            self.state["request"] = {k: request[k] for k in ("nonce", "action", "at", "by", "ytdlp")}
        self.log(f"=== {action}{' +yt-dlp' if ytdlp else ''}{' ' + ','.join(sorted(env)) if env else ''}" +
                 (f" (asked by {clean(request['by']['name'], 64)} #{request['by']['id']})" if request else " (shell)"))
        stop = self.heartbeat_thread()
        try:
            {"check": self.check, "apply": lambda: self.apply(ytdlp=ytdlp), "rollback": self.rollback,
             "env": lambda: self.env_apply(env)}[action]()
        except Fail as e:
            self.say(str(e))
            self._env_finish(False, str(e)) if action == "env" else self._finish("failed", False, str(e))
        except Exception as e:
            self.log(f"crash: {type(e).__name__}: {e}")
            m = f"The updater hit an unexpected error: {type(e).__name__}: {e}"
            self._env_finish(False, m) if action == "env" else self._finish("failed", False, m)
        finally:
            stop.set()

    def tick(self):
        """systemd entry point: heartbeat, then a pending request, else the periodic check."""
        if not self.lock():
            return 0                                   # another run is busy and keeps the heartbeat itself
        if self.state.get("state") in ("checking", "applying") and self.state.get("action") == "env":
            self._env_finish(False, "The settings change was interrupted (server restart?). Check Admin -> Server: "
                                    ".env may already have the new values.")
        elif self.state.get("state") in ("checking", "applying"):
            # a previous run died mid-way (reboot, kill): say so instead of showing "applying" forever
            self.state.update(state="failed", action=None, finished_at=now(), result={
                "action": self.state.get("action"), "ok": False, "at": now(),
                "message": "The updater was interrupted (server restart?). Check the site; use Roll back if needed."})
        if os.path.isdir(os.path.join(self.app, ".git")):        # also notices updates made outside the updater
            try:
                cur = self.head()
                if (self.state.get("current") or {}).get("sha") != cur:
                    info = self.commit_info(cur)
                    if self.git("rev-parse", "--verify", "--quiet", UPSTREAM_REF, check=False):
                        info["local"] = not self._is_ancestor(cur, UPSTREAM_REF)
                    self.state["current"] = info
            except Fail as e:
                self.log(f"can't read the current commit: {e}")
        self.write_status()
        req = self.read_request()
        if req:
            self.do(req["action"], ytdlp=req["ytdlp"], request=req, env=req.get("env"))
            return 0
        hrs = self.conf["AUTO_CHECK_HOURS"]
        if hrs and now() - (self.state.get("checked_at") or 0) > hrs * 3600:
            try:
                self.check(auto=True)
            except Fail as e:
                self.log(f"auto check failed: {e}")
                self.state["checked_at"] = now()            # don't retry every minute
                self.write_status()
        return 0


# ---------------------------------------------------------------------- pure helpers (unit-testable)
def validate_request(raw, t_now, seen_nonces=()):
    """Strict schema check of the api's request file. Raises ValueError(reason)."""
    try:
        req = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("request is not JSON")
    if not isinstance(req, dict) or set(req) != (REQ_KEYS | {"env"} if req.get("action") == "env" else REQ_KEYS):
        raise ValueError("request has the wrong fields")
    if type(req["v"]) is not int or req["v"] != 1:
        raise ValueError("unknown request version")
    if req["action"] not in ACTIONS:
        raise ValueError("unknown action")
    if not isinstance(req["nonce"], str) or not NONCE_RE.match(req["nonce"]):
        raise ValueError("bad nonce")
    if req["nonce"] in seen_nonces:
        raise ValueError("request already handled")
    if type(req["at"]) not in (int, float) or not (t_now - REQ_MAX_AGE <= req["at"] <= t_now + 120):
        raise ValueError("request is stale or from the future")
    by = req["by"]
    if not isinstance(by, dict) or set(by) != {"id", "name"} or type(by["id"]) is not int or by["id"] < 1 or \
            not isinstance(by["name"], str) or len(by["name"]) > 64 or CTRL_RE.search(by["name"]):
        raise ValueError("bad 'by'")
    if type(req["ytdlp"]) is not bool or (req["ytdlp"] and req["action"] != "apply"):
        raise ValueError("bad ytdlp flag")
    if req["action"] == "env":
        req["env"] = validate_env(req["env"])
    return req


def env_check(key, v):
    """One Admin -> Server value -> its normalized form (None = remove from .env: the default). Raises ValueError."""
    spec = ENV_KEYS.get(key) if isinstance(key, str) else None
    if spec is None:
        raise ValueError(f"{clean(key, 40)!r} can't be changed from the web app")
    if v is None or v == "":
        return None
    if not isinstance(v, str) or len(v) > 600:
        raise ValueError(f"bad value for {key}")
    kind = spec["kind"]
    if kind == "bool" and v in ("0", "1") or kind == "choice" and v in spec["choices"]:
        return v
    if kind == "int" and re.fullmatch(r"\d{1,7}", v) and spec["min"] <= int(v) <= spec["max"]:
        return str(int(v))
    if kind in ENV_FORMATS and re.match(ENV_FORMATS[kind], v):
        return v
    raise ValueError(f"bad value for {key}")


def validate_env(changes):
    if not isinstance(changes, dict) or not 1 <= len(changes) <= len(ENV_KEYS):
        raise ValueError("bad 'env'")
    return {k: env_check(k, v) for k, v in changes.items()}


def _env_unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v.split(" #", 1)[0].rstrip()


def env_values(text):
    """KEY -> value of every assignment in a .env text (the last one wins, like Compose)."""
    out = {}
    for line in text.splitlines():
        m = ENV_LINE_RE.match(line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = _env_unquote(m.group(2))
    return out


def env_edit(text, changes):
    """.env text with `changes` applied: a key's last assignment is replaced (earlier duplicates dropped), a None value
    removes the key (back to the compose default), a new key is appended under ENV_MARK. Comments and every other
    line stay as they are."""
    lines = text.splitlines()
    where = {}
    for i, line in enumerate(lines):
        m = ENV_LINE_RE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) in changes:
            where.setdefault(m.group(1), []).append(i)
    drop, put = set(), {}
    for k, v in changes.items():
        idx = where.get(k, [])
        drop.update(idx[:-1] if v is not None else idx)
        if v is not None and idx:
            put[idx[-1]] = f"{k}={v}"
    out = [put.get(i, l) for i, l in enumerate(lines) if i not in drop]
    new = [f"{k}={v}" for k, v in changes.items() if v is not None and k not in where]
    if new:
        if ENV_MARK not in out:
            out += ([""] if out and out[-1].strip() else []) + [ENV_MARK]
        out += new
    return "\n".join(out) + "\n" if out else ""


def heavy_prefix(dockerfile):
    """The part of the worker Dockerfile before the first COPY of repo files: base image, apt, the big pip layer.
    If it is unchanged, a rebuild reuses the cached ~3 GB layer."""
    out = []
    for line in (dockerfile or "").splitlines():
        if re.match(r"\s*COPY\s+(?!--from)", line, re.I):
            break
        out.append(line.rstrip())
    return [l for l in out if l and not l.lstrip().startswith("#")]


def describe_touches(t):
    out = []
    if t.get("worker") == "heavy":
        out.append("the worker image (big rebuild)")
    elif t.get("worker") == "light":
        out.append("the worker image (code layers only)")
    if t.get("api"):
        out.append("api")
    if t.get("web"):
        out.append("web")
    return out


def dockerfile_base(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*FROM\s+(\S+)", line, re.I)
            if m:
                if not IMAGE_RE.match(m.group(1)):
                    raise Fail("unexpected base image in Dockerfile")
                return m.group(1)
    raise Fail("no FROM line in Dockerfile")


LOCK_LINE = [re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9.+!_-]+ \\\Z"),
             re.compile(r"\A    --hash=sha256:[0-9a-f]{64}(?: \\)?\Z"),
             re.compile(r"\A\s*#[^\x00-\x1f]*\Z"), re.compile(r"\A\Z")]


def validate_ytdlp_lock(text):
    """pip-compile output must be only pinned names + sha256 hashes + comments. -> (text, yt-dlp version)."""
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    for l in lines:
        if not any(rx.match(l) for rx in LOCK_LINE):
            raise Fail(f"unexpected line in the yt-dlp lock output: {clean(l, 80)!r}")
    pkgs = [l for l in lines if LOCK_LINE[0].match(l)]
    if not pkgs or len([l for l in lines if l.startswith("    --hash=")]) < len(pkgs):
        raise Fail("yt-dlp lock output has packages without hashes")
    ver = next((l.split("==")[1].split()[0] for l in pkgs if l.lower().startswith("yt-dlp==")), None)
    if not ver or not re.match(r"\A[0-9]+(\.[0-9]+)+\Z", ver):
        raise Fail("yt-dlp lock output doesn't pin yt-dlp")
    return "\n".join(lines) + "\n", ver


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def ytdlp_file(app):
    try:
        with open(os.path.join(app, "live", "requirements-ytdlp.txt"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def pin_of(text):
    m = re.search(r"^yt-dlp==([0-9.]+)", text or "", re.M)
    return m.group(1) if m else None


def ytdlp_pin(app):
    return pin_of(ytdlp_file(app))


def rb_ref(img):
    repo = img.split("@")[0]
    if ":" in repo.rsplit("/", 1)[-1]:
        repo = repo.rsplit(":", 1)[0]
    return f"{repo}:{ROLLBACK_TAG}"


def redact(url):
    return re.sub(r"//[^/@]+@", "//***@", url)


def parse_ps(out):
    out = (out or "").strip()
    if not out:
        return []
    if out.startswith("["):
        return json.loads(out)
    return [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]


def _bot_env():
    return {"GIT_AUTHOR_NAME": "cs-updater", "GIT_AUTHOR_EMAIL": "cs-updater@localhost",
            "GIT_COMMITTER_NAME": "cs-updater", "GIT_COMMITTER_EMAIL": "cs-updater@localhost"}


def _atomic_write(path, body, mode=0o644):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cyber-Scout host updater (see deploy/updater/README.md)")
    ap.add_argument("--stack", required=True, help="the stack folder (app/, data/, .env, docker-compose.yml)")
    ap.add_argument("--conf", help="config file (default: <stack>/updater/updater.conf)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="systemd: heartbeat + pending request + periodic check")
    sub.add_parser("check")
    a = sub.add_parser("apply")
    a.add_argument("--ytdlp", action="store_true", help="also re-pin yt-dlp to its newest release")
    sub.add_parser("rollback")
    sub.add_parser("status")
    e = sub.add_parser("env", help="change allowlisted .env keys (KEY=VALUE; KEY= resets to the default), then restart")
    e.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    args = ap.parse_args(argv)
    if os.geteuid() == 0 and os.environ.get("CS_UPDATER_ALLOW_ROOT") != "1":
        print("refusing to run as root: run it as the stack's owner (a member of the docker group)", file=sys.stderr)
        return 2
    os.umask(0o022)
    try:
        u = Updater(args.stack, args.conf)
        if args.cmd == "status":
            print(json.dumps(u.status_doc(), indent=1, sort_keys=True))
            return 0
        if args.cmd == "run":
            return u.tick()
        if not u.lock():
            print("another update is running", file=sys.stderr)
            return 1
        if args.cmd == "env":
            try:
                env = validate_env(dict(p.partition("=")[::2] for p in args.pairs))
            except ValueError as e:
                print(f"cs-updater: {e} (allowed: {', '.join(ENV_KEYS)})", file=sys.stderr)
                return 2
            u.do("env", env=env)
            return 0 if (u.state.get("env_result") or {}).get("ok") else 1
        u.do(args.cmd, ytdlp=getattr(args, "ytdlp", False))
        return 0 if (u.state.get("result") or {}).get("ok") else 1
    except Fail as e:
        print(f"cs-updater: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
