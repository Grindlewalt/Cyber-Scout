"""Admin -> Updates and Admin -> Server: one-click update of the whole stack and a few .env settings, both done by a
HOST-side updater (deploy/updater/).

The api never runs git or docker, has no docker socket and can't read .env. It only
  * writes data/config/update-request.json with a fixed schema: {"v":1, "action": check|apply|rollback|env,
    "nonce": <32 hex>, "at": <unix s>, "by": {"id", "name"}, "ytdlp": bool[, "env": {KEY: value|null}]} -- no
    commands, URLs, refs or paths; "env" only with keys the updater lists (ENV_KEYS there), each value checked here
    against the same kinds (ENV_FORMATS = the updater's) and again by the updater;
  * reads data/config/update-status.json, written by the updater (progress, result, heartbeat), and passes on only
    known fields with checked types (the file lives in data/, which the worker container can also write).
The updater (systemd path + timer units, running as the stack's owner) validates the request again and is the only
thing that touches git / docker. A stale heartbeat means it isn't installed (or isn't running).
"""
import json, os, re, secrets, stat, time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .. import audit, db as DB
from ..config import cfg
from ..deps import User, require_role
from ..security import WriteLimiter, atomic_write_json

router = APIRouter(tags=["admin"])

REQUEST_FILE, STATUS_FILE = "update-request.json", "update-status.json"
STATES = ("idle", "checking", "ready", "applying", "done", "failed", "rolled_back")
ACTIONS = ("check", "apply", "rollback", "env")
HEARTBEAT_STALE = 180          # the updater's timer beats every ~60 s (every 15 s while it works)
PENDING_STALE = 900            # the updater ignores requests older than this
MAX_STATUS_BYTES = 512 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
# Admin -> Server: value formats, the same regexes as ENV_FORMATS in deploy/updater/cs-updater.py (test_server_env)
ENV_FORMATS = {
    "gpus": r"\A\d{1,2}(?:,\d{1,2}){0,7}\Z",
    "mem": r"\A[1-9]\d{0,5}[mg]\Z",
    "tz": r"\A[A-Za-z][A-Za-z0-9_+-]{0,30}(?:/[A-Za-z0-9_+-]{1,30}){0,2}\Z",
    "hosts": r"\A[A-Za-z0-9.:-]{1,100}(?:/\d{1,3})?(?:,[A-Za-z0-9.:-]{1,100}(?:/\d{1,3})?){0,19}\Z",
}
ENV_KINDS = ("bool", "int", "choice", *ENV_FORMATS)
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")
INSTALL_HINT = ("On the server, in the Cyber-Scout folder (e.g. /opt/cyber-scout): "
                "sudo ./app/deploy/updater/install-updater.sh   (or re-run install.sh and answer yes to the updater)")

# admins skip the generic write budget; update requests get their own small one
limiter = WriteLimiter()
limiter.LIMITS = {"update": [(60, 6), (3600, 60)]}


def _paths():
    d = os.path.join(cfg.DATA, "config")
    return os.path.join(d, REQUEST_FILE), os.path.join(d, STATUS_FILE)


def _read_json(path, limit):
    """Read a small JSON file without following symlinks; None if missing/bad."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- status parsing (whitelist + types)
def _s(v, n=300):
    return v[:n] if isinstance(v, str) else None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _commit(v):
    if not isinstance(v, dict) or not isinstance(v.get("sha"), str) or not SHA_RE.match(v["sha"]):
        return None
    out = {"sha": v["sha"], "short": v["sha"][:7], "date": _num(v.get("date")), "subject": _s(v.get("subject"), 200)}
    if isinstance(v.get("local"), bool):
        out["local"] = v["local"]
    return out


def _env_schema(v):
    """The updater's list of changeable .env keys -> checked copy (unknown kinds / odd entries dropped)."""
    out = {}
    for k, sp in (v.items() if isinstance(v, dict) else []):
        if len(out) >= 40 or not isinstance(k, str) or not ENV_KEY_RE.match(k) or not isinstance(sp, dict) \
                or sp.get("kind") not in ENV_KINDS:
            continue
        e = {"kind": sp["kind"], "default": _s(sp.get("default"), 100) or "", "api": sp.get("api") is True}
        if sp["kind"] == "int":
            lo, hi = sp.get("min"), sp.get("max")
            if type(lo) is not int or type(hi) is not int or lo > hi:
                continue
            e.update(min=lo, max=hi)
        if sp["kind"] == "choice":
            ch = [c for c in sp["choices"][:12] if isinstance(c, str) and re.match(r"^[A-Za-z0-9._-]{1,20}$", c)] \
                if isinstance(sp.get("choices"), list) else []
            if not ch:
                continue
            e["choices"] = ch
        out[k] = e
    return out


def _env(v):
    if not isinstance(v, dict):
        return None
    schema = _env_schema(v.get("schema"))
    vals = v.get("values") if isinstance(v.get("values"), dict) else {}
    return {"schema": schema, "values": {k: _s(vals.get(k), 600) for k in schema}, "error": _s(v.get("error"), 200)}


# ---------------------------------------------------------------- the stack's containers (updater: docker inspect)
# A container that can't start never writes anything itself: only Docker knows why. The updater copies each
# container's state + Docker's error every minute; these turn the usual ones into a sentence and a fix (the same
# cases deploy/install.sh explains when it starts the stack).
SVC_STATES = ("created", "running", "restarting", "exited", "paused", "dead", "removing")
SVC_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
CDI_SPEC = "/etc/cdi/nvidia.yaml"
IN_FOLDER = "On the server, in the Cyber-Scout folder (e.g. /opt/cyber-scout): "


def explain(c):
    """A container from the updater -> {"why", "fix"} in plain words; None while it runs normally."""
    svc, st = c.get("service") or "it", c.get("state")
    if st == "running":
        return None
    t = " ".join(x for x in (c.get("error"), c.get("log")) if x)
    start = f"docker compose up -d {svc}"
    if "failed to add device rules" in t or "device filter program" in t:
        return {"why": "Docker can't pass the GPU into it: the NVIDIA container hook can't add its device rules on "
                       "this kernel (this tends to start after a kernel, driver or NVIDIA Container Toolkit update, "
                       "once the server reboots). Often only some cards are affected.",
                "fix": "Set GPU_IDS to a single card that works (Admin → Server; try 0), or re-run the installer: it "
                       "tests every card and uses CDI for the ones the hook can't pass. No working card: the CPU "
                       "override (COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml in .env) is slower but runs."}
    if "failed to stat CDI host device" in t:
        dev = re.search(r'failed to stat CDI host device "([^"]{1,80})"', t)
        return {"why": "Docker can't pass the GPU into it: the CDI device list is out of date"
                       + (f" (it names {dev.group(1)}, which isn't there now)" if dev else "")
                       + ". A reboot can renumber the devices.",
                "fix": f"On the server: sudo nvidia-ctk cdi generate --output={CDI_SPEC}, then {start} in the "
                       "Cyber-Scout folder (re-running the installer offers the same refresh)."}
    if any(k in t for k in ("unresolvable CDI devices", "unknown CDI device", "CDI device injection failed")):
        return {"why": "Docker can't pass the GPU into it: there's no CDI device list for that card.",
                "fix": f"On the server: sudo nvidia-ctk cdi generate --output={CDI_SPEC}, then {start} in the "
                       "Cyber-Scout folder."}
    if "could not select device driver" in t:
        return {"why": "Docker has no NVIDIA runtime, so it can't pass the GPU.",
                "fix": "On the server: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart "
                       f"docker, then {start} in the Cyber-Scout folder (or use the CPU override)."}
    if "port is already allocated" in t or "address already in use" in t:
        return {"why": "Its port is taken by another program on the server.",
                "fix": IN_FOLDER + "change the port in .env (or stop the other program), then " + start + "."}
    if "no space left on device" in t:
        return {"why": "The server's disk is full.",
                "fix": "Free some space (docker image ls / docker image rm <unused>, old videos), then " + start + "."}
    if c.get("oom") or (st in ("exited", "restarting") and c.get("exit_code") == 137):
        return {"why": "It ran out of memory and the system stopped it.",
                "fix": "Raise its memory limit (Admin → Server, e.g. WORKER_MEM_LIMIT) or free memory on the server; "
                       "then " + start + "."}
    if st == "restarting" or ((c.get("restarts") or 0) >= 3 and st != "created"):
        return {"why": f"It keeps crashing and restarting ({c.get('restarts') or 0} restarts so far).",
                "fix": IN_FOLDER + f"docker compose logs --tail 80 {svc} shows why."}
    if st == "created":
        return {"why": "It was created but never started" + (f": Docker said {c['error'][:300]}" if c.get("error")
                                                              else "."),
                "fix": IN_FOLDER + start + " (Docker prints why if it still can't)."}
    if st in ("exited", "dead"):
        code = c.get("exit_code")
        return {"why": "It was stopped." if code == 0 else f"It stopped with an error (exit code {code}).",
                "fix": IN_FOLDER + (start if code == 0 else f"docker compose logs --tail 80 {svc} shows why; "
                                    f"{start} starts it again") + "."}
    if st == "paused":
        return {"why": "Docker has it paused.", "fix": IN_FOLDER + f"docker compose unpause {svc}."}
    return {"why": f"It's {st or 'not running'}.", "fix": IN_FOLDER + start + "."}


def _services(v):
    """The updater's container list -> checked copy, each with why/fix when it isn't running."""
    out = []
    for c in (v[:30] if isinstance(v, list) else []):
        if not isinstance(c, dict) or not isinstance(c.get("service"), str) or not SVC_RE.match(c["service"]):
            continue
        ec, rs = c.get("exit_code"), c.get("restarts")
        e = {"service": c["service"], "name": _s(c.get("name"), 100),
             "state": c.get("state") if c.get("state") in SVC_STATES else None,
             "exit_code": ec if type(ec) is int else None, "error": _s(c.get("error"), 600) or None,
             "oom": c.get("oom") is True, "restarts": rs if type(rs) is int else None,
             "started_at": _num(c.get("started_at")), "finished_at": _num(c.get("finished_at")),
             "log": _s(c.get("log"), 600) or None}
        e["problem"] = explain(e)
        out.append(e)
    return out


def container(service, t_now=None):
    """The updater's latest view of one service's (first) container, or None (no updater, stale view, no such
    container). For the "the worker isn't running" notice."""
    t_now = t_now or time.time()
    st = parse_status(_read_json(_paths()[1], MAX_STATUS_BYTES), t_now)
    at = st.get("services_at")
    if not st["installed"] or not at or t_now - at > HEARTBEAT_STALE:
        return None
    return next((c for c in st["services"] if c["service"] == service), None)


def parse_status(raw, t_now=None):
    """The updater's status file -> the API's view. Unknown fields dropped, types checked, sizes capped."""
    t_now = t_now or time.time()
    if not isinstance(raw, dict):
        return {"installed": False, "state": "idle"}
    hb = _num(raw.get("heartbeat"))
    st = raw.get("state") if raw.get("state") in STATES else "idle"
    res = raw.get("result") if isinstance(raw.get("result"), dict) else None
    tch = raw.get("touches") if isinstance(raw.get("touches"), dict) else None
    disk = raw.get("disk") if isinstance(raw.get("disk"), dict) else None
    rej = raw.get("rejected") if isinstance(raw.get("rejected"), dict) else None
    req = raw.get("request") if isinstance(raw.get("request"), dict) else None
    yt = raw.get("ytdlp") if isinstance(raw.get("ytdlp"), dict) else None
    return {
        "installed": hb is not None and t_now - hb < HEARTBEAT_STALE,
        "heartbeat": hb,
        "updater_version": _s(raw.get("updater_version"), 20),
        "state": st,
        "action": raw.get("action") if raw.get("action") in ACTIONS else None,
        "branch": _s(raw.get("branch"), 100),
        "current": _commit(raw.get("current")),
        "latest": _commit(raw.get("latest")),
        "previous": _commit(raw.get("previous")),
        "checked_at": _num(raw.get("checked_at")),
        "changelog": [c for c in (_commit(x) for x in (raw.get("changelog") or [])[:200]
                                  if isinstance(raw.get("changelog"), list)) if c],
        "changelog_more": int(_num(raw.get("changelog_more")) or 0),
        "touches": None if tch is None else {
            "worker": tch.get("worker") if tch.get("worker") in ("heavy", "light") else None,
            "api": tch.get("api") is True, "web": tch.get("web") is True, "compose": tch.get("compose") is True},
        "disk": None if disk is None else {"free_gb": _num(disk.get("free_gb")), "need_gb": _num(disk.get("need_gb")),
                                           "ok": disk.get("ok") is True},
        "progress": [x[:400] for x in (raw.get("progress") or [])[-150:] if isinstance(x, str)]
        if isinstance(raw.get("progress"), list) else [],
        "result": None if res is None else {
            "action": res.get("action") if res.get("action") in ("check", "apply", "rollback") else None,
            "ok": res.get("ok") is True, "message": _s(res.get("message"), 1200), "at": _num(res.get("at"))},
        "request_nonce": req.get("nonce") if req and isinstance(req.get("nonce"), str)
        and NONCE_RE.match(req["nonce"]) else None,
        "rejected": None if rej is None else {"at": _num(rej.get("at")), "reason": _s(rej.get("reason"), 200)},
        "started_at": _num(raw.get("started_at")),
        "finished_at": _num(raw.get("finished_at")),
        "paused": raw.get("paused") is True,
        "ytdlp_pinned": _s(yt.get("pinned"), 40) if yt else None,
        "ytdlp_allowed": raw.get("ytdlp_allowed") is True,
        "features": [f for f in raw.get("features")[:20] if isinstance(f, str)] if isinstance(raw.get("features"), list)
        else [],
        "env": _env(raw.get("env")),
        "env_progress": [x[:400] for x in (raw.get("env_progress") or [])[-150:] if isinstance(x, str)]
        if isinstance(raw.get("env_progress"), list) else [],
        "env_result": None if not isinstance(raw.get("env_result"), dict) else {
            "ok": raw["env_result"].get("ok") is True, "message": _s(raw["env_result"].get("message"), 1200),
            "at": _num(raw["env_result"].get("at")), "rolled_back": raw["env_result"].get("rolled_back") is True},
        "services": _services(raw.get("services")),
        "services_at": _num(raw.get("services_at")),
    }


def _pending(t_now):
    """The request the api wrote that the updater hasn't consumed yet (it deletes the file when it picks it up)."""
    req = _read_json(_paths()[0], 4096)
    if isinstance(req, dict) and req.get("action") in ACTIONS \
            and isinstance(req.get("at"), (int, float)) and t_now - req["at"] < PENDING_STALE:
        return {"action": req["action"], "at": req["at"]}
    return None


def current_status():
    t = time.time()
    st = parse_status(_read_json(_paths()[1], MAX_STATUS_BYTES), t)
    st["pending"] = _pending(t)
    st["install_hint"] = None if st["installed"] else INSTALL_HINT
    return st


# ---------------------------------------------------------------- endpoints
class UpdateStatus(BaseModel):
    model_config = ConfigDict(extra="allow")
    installed: bool = Field(description="false: no updater heartbeat in the last 3 minutes (not installed / stopped)")
    state: Literal["idle", "checking", "ready", "applying", "done", "failed", "rolled_back"]


@router.get("/admin/update", response_model=UpdateStatus, summary="Update status (admin): version, changelog, progress")
def get_update(user: User = Depends(require_role("admin"))):
    return current_status()


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ytdlp: StrictBool = Field(False, description="also re-pin yt-dlp to its newest release (worker image: small rebuild)")


def _request(request: Request, user: User, db, action: str, ytdlp: bool = False):
    wait = limiter.hit(user.id, "update")
    if wait:
        raise HTTPException(429, f"slow down: try again in {wait}s", headers={"Retry-After": str(wait)})
    st = current_status()
    if not st["installed"]:
        raise HTTPException(409, "The updater isn't installed on the server (or isn't running). " + INSTALL_HINT)
    if st["state"] in ("checking", "applying"):
        raise HTTPException(409, "An update task is already running; wait for it to finish.")
    if st["pending"]:
        raise HTTPException(409, f"A '{st['pending']['action']}' request is already waiting for the updater.")
    if action == "rollback" and not st["previous"]:
        raise HTTPException(409, "Nothing to roll back to.")
    if ytdlp and not st["ytdlp_allowed"]:
        raise HTTPException(409, "yt-dlp updates are turned off on this server (YTDLP=0 in updater/updater.conf).")
    nonce = secrets.token_hex(16)
    req = {"v": 1, "action": action, "nonce": nonce, "at": time.time(),
           "by": {"id": int(user.id), "name": (user.display_name or f"admin #{user.id}")[:64]}, "ytdlp": bool(ytdlp)}
    with DB.tx(db):
        audit.record(db, request, user, "update." + action, "app", None,
                     {"commit": (st["current"] or {}).get("sha")}, {"nonce": nonce, "ytdlp": bool(ytdlp)})
        atomic_write_json(_paths()[0], req)
    return {"queued": action, "nonce": nonce}


@router.post("/admin/update/check", status_code=202, summary="Ask the host updater to look for updates (admin)")
def post_check(request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    return _request(request, user, db, "check")


@router.post("/admin/update/apply", status_code=202,
             summary="Ask the host updater to update + restart everything (admin); rolls back by itself on failure")
def post_apply(body: ApplyIn, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    return _request(request, user, db, "apply", body.ytdlp)


@router.post("/admin/update/rollback", status_code=202, summary="Ask the host updater to go back to the previous version (admin)")
def post_rollback(request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    return _request(request, user, db, "rollback")


# ---------------------------------------------------------------- Admin -> Server (.env settings)
def env_check(spec, key, v):
    """Value for `key` (spec from the updater's schema) -> normalized (None = back to the default). Same rules as
    env_check in cs-updater.py, which checks the request again."""
    if v is None or v == "":
        return None
    kind = spec["kind"]
    if len(v) <= 600 and (kind == "bool" and v in ("0", "1") or kind == "choice" and v in spec["choices"]):
        return v
    if kind == "int" and re.fullmatch(r"\d{1,7}", v) and spec["min"] <= int(v) <= spec["max"]:
        return str(int(v))
    if kind in ENV_FORMATS and len(v) <= 600 and re.match(ENV_FORMATS[kind], v):
        return v
    hint = {"bool": "0 or 1", "int": f"a whole number from {spec.get('min')} to {spec.get('max')}",
            "choice": "one of " + ", ".join(spec.get("choices") or []), "gpus": "card numbers like 0, 1 or 0,1",
            "mem": "a size like 12g or 768m", "tz": "a time zone like America/Los_Angeles",
            "hosts": "host names, IPs or CIDRs separated by commas"}.get(kind, "a valid value")
    raise HTTPException(422, f"{key} must be {hint}.")


def server_env_status():
    st = current_status()
    env = st.get("env") or {}
    mine = st.get("action") == "env" or (st["pending"] or {}).get("action") == "env"
    busy = st["state"] in ("checking", "applying") or bool(st["pending"])
    fresh = st["installed"] and st.get("services_at") and time.time() - st["services_at"] <= HEARTBEAT_STALE
    return {"installed": st["installed"], "supported": "env" in st.get("features", []),
            "install_hint": st["install_hint"], "busy": busy, "running": busy and mine, "other_task": busy and not mine,
            "schema": env.get("schema") or {}, "values": env.get("values") or {}, "error": env.get("error"),
            "progress": st.get("env_progress") or [], "result": st.get("env_result"),
            "services": st.get("services") or [] if fresh else [], "services_at": st.get("services_at") if fresh else None}


class ServerEnvStatus(BaseModel):
    model_config = ConfigDict(extra="allow")
    installed: bool
    supported: bool = Field(description="false: the installed updater is older and can't change .env")
    schema_: dict = Field(alias="schema", description="KEY -> {kind, default, api, min/max, choices}")
    values: dict = Field(description="KEY -> value in .env, null = not set (the default applies)")


@router.get("/admin/server-env", response_model=ServerEnvStatus, response_model_by_alias=True,
            summary="The .env settings an admin may change from the web app, with their values (admin)")
def get_server_env(user: User = Depends(require_role("admin"))):
    return server_env_status()


class EnvIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, str | None] = Field(description="KEY -> new value; null or \"\" = remove it (default)",
                                           min_length=1, max_length=40)


@router.post("/admin/server-env", status_code=202,
             summary="Ask the host updater to change .env settings and restart what uses them (admin); "
                     "puts the old .env back by itself if the app doesn't come up healthy")
def post_server_env(body: EnvIn, request: Request, user: User = Depends(require_role("admin")),
                    db=Depends(DB.get_db)):
    wait = limiter.hit(user.id, "update")
    if wait:
        raise HTTPException(429, f"slow down: try again in {wait}s", headers={"Retry-After": str(wait)})
    st = server_env_status()
    if not st["installed"]:
        raise HTTPException(409, "The updater isn't installed on the server (or isn't running). " + INSTALL_HINT)
    if not st["supported"]:
        raise HTTPException(409, "The updater on the server is too old to change settings: re-run "
                                 "./app/deploy/updater/install-updater.sh on the server after updating.")
    if st["busy"]:
        raise HTTPException(409, "The updater is busy (an update or a settings change); wait for it to finish.")
    schema, cur = st["schema"], st["values"]
    changes = {}
    for k, v in body.changes.items():
        if k not in schema:
            raise HTTPException(422, f"{k} can't be changed from the web app.")
        v = env_check(schema[k], k, v.strip() if isinstance(v, str) else v)
        if v != cur.get(k):
            changes[k] = v
    if not changes:
        raise HTTPException(400, "Nothing changed.")
    nonce = secrets.token_hex(16)
    req = {"v": 1, "action": "env", "nonce": nonce, "at": time.time(),
           "by": {"id": int(user.id), "name": (user.display_name or f"admin #{user.id}")[:64]}, "ytdlp": False,
           "env": changes}
    if len(json.dumps(req)) > 4000:
        raise HTTPException(422, "Too much at once: change fewer settings.")
    with DB.tx(db):
        audit.record(db, request, user, "server.env", ",".join(sorted(changes)), None,
                     {k: cur.get(k) for k in changes}, dict(changes))
        atomic_write_json(_paths()[0], req)
    return {"queued": "env", "nonce": nonce, "changes": changes}
