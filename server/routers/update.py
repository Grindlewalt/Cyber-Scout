"""Admin -> Updates: one-click update of the whole stack, done by a HOST-side updater (deploy/updater/).

The api never runs git or docker and has no docker socket. It only
  * writes data/config/update-request.json with a fixed schema: {"v":1, "action": check|apply|rollback,
    "nonce": <32 hex>, "at": <unix s>, "by": {"id", "name"}, "ytdlp": bool} -- no commands, URLs, refs or paths;
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
HEARTBEAT_STALE = 180          # the updater's timer beats every ~60 s (every 15 s while it works)
PENDING_STALE = 900            # the updater ignores requests older than this
MAX_STATUS_BYTES = 512 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
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
        "action": raw.get("action") if raw.get("action") in ("check", "apply", "rollback") else None,
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
    }


def _pending(t_now):
    """The request the api wrote that the updater hasn't consumed yet (it deletes the file when it picks it up)."""
    req = _read_json(_paths()[0], 4096)
    if isinstance(req, dict) and req.get("action") in ("check", "apply", "rollback") \
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
