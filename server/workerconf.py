"""DATA/config/worker.json: the worker's view of the app settings (re-read by live/worker.py every poll).

    {"version": 1, "updated": <unix ts>,
     "tba_key": "..." | null,            # null -> worker falls back to its TBA_KEY env
     "events": ["2026wasam", ...] | null, # null -> never configured in the app -> worker uses EVENTS env
     "team_number": 4911 | null, "season_goal": "DCMP" | null,
     "options": {"poll_seconds": 120 | null, "gpu_device": 0 | null, "cpu_only": bool | null, "hubcam": bool | null}}

Written atomically with mode 0600 (it holds the TBA key); the data dir is the only place secrets live.
"""
import time

from . import db as DB
from .config import cfg
from .security import atomic_write_json

OPTION_KEYS = ("poll_seconds", "gpu_device", "cpu_only", "hubcam")


def build(db) -> dict:
    events = None
    if DB.get_setting(db, "events_configured", False):
        events = [r["key"] for r in db.execute("SELECT key FROM events ORDER BY start_date, key")]
    opts = DB.get_setting(db, "options", {}) or {}
    return {"version": 1, "updated": time.time(),
            "tba_key": DB.get_setting(db, "tba_key"),
            "events": events,
            "team_number": DB.get_setting(db, "team_number"),
            "season_goal": DB.get_setting(db, "season_goal"),
            "options": {k: opts.get(k) for k in OPTION_KEYS}}


def write(db):
    atomic_write_json(cfg.WORKER_CONF, build(db), mode=0o600)
