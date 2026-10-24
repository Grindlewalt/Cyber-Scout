"""The worker's own status, for the "processing is paused" / "the worker isn't running" notices (GET app/v1/session
-> processing).

live/worker.py writes DATA/worker-status.json {at, paused, reason, paused_by} on every pass of its main loop, and a
background thread re-writes it every 30 s (also mid-match). paused_by "app" = the Admin → Settings switch, "env" =
PAUSE_PROCESSING=1 in .env (or an older worker). Missing file = no worker has run against this data folder yet.

worker_down: quiet for DOWN_S, or no file FIRST_S after this api started -> {never, since, quiet_s}. A container
that can't start (a GPU passthrough error, say) never writes anything, so for admins it also carries the host
updater's view of the container (state, Docker's error, why/fix in plain words) when the updater is installed."""
import json, os, time

from .config import cfg
from .routers import update

FRESH_S = 3600          # a "paused" older than this is not trusted (the worker writes every 30 s while it waits)
DOWN_S = 300            # no status write for this long = the worker isn't running (it writes every 30 s)
FIRST_S = 600           # no status file this long after the api started = it never started (first start: ~1-2 min)
STARTED = time.time()   # this api process
WHY = {
    "paused": "Processing is paused on the server: no new schedules, results, rankings or tracked matches until an "
              "admin turns it back on (Admin → Settings → Processing).",
    "no_key": "The server has no TBA key yet, so nothing is processed: an admin sets it in Admin → Settings.",
    "no_events": "No competition is followed, so the worker is idle.",
}


def status_path() -> str:
    return os.path.join(cfg.DATA, "worker-status.json")


def worker_down(at, now):
    """None while the worker checks in; else {never: no status file yet, since: its last check-in or None,
    quiet_s: seconds without one (since this api started when never)}."""
    if at is None:
        quiet = now - STARTED
        return {"never": True, "since": None, "quiet_s": int(quiet)} if quiet >= FIRST_S else None
    return {"never": False, "since": at, "quiet_s": int(now - at)} if now - at >= DOWN_S else None


def processing_state(now: float | None = None, admin: bool = False) -> dict:
    """{paused: bool|None, paused_by: "app"|"env"|None, worker_seen: unix|None, reason: str|None, why: str|None,
    worker_down: dict|None}. paused None = unknown. admin: worker_down also gets "docker" (the updater's view of the
    worker container, or None)."""
    now = time.time() if now is None else now
    try:
        with open(status_path()) as f:
            st = json.load(f)
        at = float(st.get("at") or 0) or None
    except (OSError, ValueError, TypeError, AttributeError):
        st, at = {}, None
    fresh = at is not None and now - at < FRESH_S
    reason = st.get("reason") if fresh and isinstance(st.get("reason"), str) else None
    paused = bool(st.get("paused")) if fresh else None
    down = worker_down(at, now)
    if down and admin:
        down["docker"] = update.container("worker", now)
    return {"paused": paused, "paused_by": (st.get("paused_by") if st.get("paused_by") in ("app", "env") else "env")
            if paused else None, "worker_seen": at,
            "reason": reason, "why": WHY.get(reason) if reason else None, "worker_down": down}
