"""The worker's own status, for the "processing is paused" notice (GET app/v1/session -> processing).

live/worker.py writes DATA/worker-status.json {at, paused, reason, paused_by} on every pass of its main loop (every 30 s
while it waits: paused, no TBA key, nothing followed). paused_by "app" = the Admin → Settings switch, "env" =
PAUSE_PROCESSING=1 in .env (or an older worker). Missing file = an older worker or none at all: unknown."""
import json, os, time

from .config import cfg

FRESH_S = 3600          # a "paused" older than this is not trusted (the worker writes every 30 s while it waits)
WHY = {
    "paused": "Processing is paused on the server: no new schedules, results, rankings or tracked matches until an "
              "admin turns it back on (Admin → Settings → Processing).",
    "no_key": "The server has no TBA key yet, so nothing is processed: an admin sets it in Admin → Settings.",
    "no_events": "No competition is followed, so the worker is idle.",
}


def status_path() -> str:
    return os.path.join(cfg.DATA, "worker-status.json")


def processing_state(now: float | None = None) -> dict:
    """{paused: bool|None, paused_by: "app"|"env"|None, worker_seen: unix|None, reason: str|None, why: str|None}.
    paused None = unknown."""
    try:
        with open(status_path()) as f:
            st = json.load(f)
        at = float(st.get("at") or 0) or None
    except (OSError, ValueError, TypeError, AttributeError):
        return {"paused": None, "paused_by": None, "worker_seen": None, "reason": None, "why": None}
    now = time.time() if now is None else now
    fresh = at is not None and now - at < FRESH_S
    reason = st.get("reason") if fresh and isinstance(st.get("reason"), str) else None
    paused = bool(st.get("paused")) if fresh else None
    return {"paused": paused, "paused_by": (st.get("paused_by") if st.get("paused_by") in ("app", "env") else "env")
            if paused else None, "worker_seen": at,
            "reason": reason, "why": WHY.get(reason) if reason else None}
