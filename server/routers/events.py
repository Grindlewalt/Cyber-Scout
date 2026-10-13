"""Followed events (what the worker tracks)."""
import json, os, time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db as DB, workerconf
from ..config import cfg
from ..deps import EVENT_RE, User, require_role, valid_event
from .settings import tba_call, tba_key_or_409

router = APIRouter(prefix="/events", tags=["events"])


class EventOut(BaseModel):
    key: str
    name: str | None
    year: int | None
    start_date: str | None
    added_at: float


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=5, max_length=24, description="TBA event key, e.g. 2026wasam")


def _snap(db, ev):
    r = db.execute("SELECT key, name, year, start_date, added_at, added_by FROM events WHERE key=?", (ev,)).fetchone()
    return {"row": dict(r)} if r else None


def apply_events_state(db, request, user, ev, state, action, ref=None):
    """Follow / unfollow `ev` to match an audit snapshot ({"row": {...}} or None) and record it. Inside a tx."""
    before = _snap(db, ev)
    if state is None:
        db.execute("DELETE FROM events WHERE key=?", (ev,))
    else:
        row = state["row"]
        db.execute("INSERT INTO events(key,name,year,start_date,added_at,added_by) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET name=excluded.name, year=excluded.year, start_date=excluded.start_date",
                   (ev, row.get("name"), row.get("year"), row.get("start_date"), row.get("added_at") or time.time(),
                    row.get("added_by")))
    DB.set_setting(db, "events_configured", True)
    workerconf.write(db)
    return audit.record(db, request, user, action, ev, ev, before, _snap(db, ev), ref)


def _site_names():
    try:
        return {e["key"]: e.get("name") for e in json.load(open(os.path.join(cfg.DATA, "site", "api", "events.json")))}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


@router.get("", response_model=list[EventOut], summary="Followed events (viewer+)")
def list_events(user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    rows = db.execute("SELECT * FROM events ORDER BY start_date, key").fetchall()
    names = _site_names() if any(not r["name"] for r in rows) else {}
    return [EventOut(key=r["key"], name=r["name"] or names.get(r["key"]), year=r["year"], start_date=r["start_date"],
                     added_at=r["added_at"]) for r in rows]


@router.post("", response_model=EventOut, status_code=201, summary="Follow an event (admin; checked on TBA)")
def add_event(body: EventIn, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    ev = body.key.strip().lower()
    if not EVENT_RE.match(ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    e = tba_call(tba_key_or_409(db), f"/event/{ev}/simple", ttl=3600)
    now = time.time()
    with DB.tx(db):
        before = _snap(db, ev)
        db.execute("INSERT INTO events(key,name,year,start_date,added_at,added_by) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET name=excluded.name, year=excluded.year, start_date=excluded.start_date",
                   (ev, e.get("name"), e.get("year"), e.get("start_date"), now, user.id))
        DB.set_setting(db, "events_configured", True)
        workerconf.write(db)
        audit.record(db, request, user, "events.follow", ev, ev, before, _snap(db, ev))
    r = db.execute("SELECT * FROM events WHERE key=?", (ev,)).fetchone()
    return EventOut(key=r["key"], name=r["name"], year=r["year"], start_date=r["start_date"], added_at=r["added_at"])


@router.delete("/{ev}", status_code=204, summary="Stop following an event (admin; its data is kept)")
def remove_event(request: Request, ev: str = Depends(valid_event), user: User = Depends(require_role("admin")),
                 db=Depends(DB.get_db)):
    with DB.tx(db):
        if not _snap(db, ev):
            raise HTTPException(404, "not followed")
        apply_events_state(db, request, user, ev, None, "events.unfollow")
    return None
