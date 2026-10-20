"""The worker's own status, for the "processing is paused" notice (GET app/v1/session -> processing).

live/worker.py writes DATA/worker-status.json {at, paused, reason} on every pass of its main loop (every 30 s while
it waits: PAUSE_PROCESSING=1, no TBA key, nothing followed). Missing file = an older worker or none at all: unknown."""
import json, os, time

from .config import cfg

FRESH_S = 3600          # a "paused" older than this is not trusted (the worker writes every 30 s while it waits)
WHY = {
    "paused": "Processing is paused on the server (PAUSE_PROCESSING=1): no new schedules, results, rankings or "
              "tracked matches until an admin turns it back on.",
    "no_key": "The server has no TBA key yet, so nothing is processed: an admin sets it in Admin → Settings.",
    "no_events": "No competition is followed, so the worker is idle.",
}


def status_path() -> str:
    return os.path.join(cfg.DATA, "worker-status.json")


def processing_state(now: float | None = None) -> dict:
    """{paused: bool|None, worker_seen: unix|None, reason: str|None, why: str|None}. paused None = unknown."""
    try:
        with open(status_path()) as f:
            st = json.load(f)
        at = float(st.get("at") or 0) or None
    except (OSError, ValueError, TypeError, AttributeError):
        return {"paused": None, "worker_seen": None, "reason": None, "why": None}
    now = time.time() if now is None else now
    fresh = at is not None and now - at < FRESH_S
    reason = st.get("reason") if fresh and isinstance(st.get("reason"), str) else None
    return {"paused": bool(st.get("paused")) if fresh else None, "worker_seen": at,
            "reason": reason, "why": WHY.get(reason) if reason else None}
