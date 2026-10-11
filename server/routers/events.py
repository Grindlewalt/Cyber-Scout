"""Followed events (what the worker tracks)."""
import json, os, time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db as DB, workerconf
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
    key: str = Field(min_length=5, max_length=24, description="TBA event key, e.g. 2026wasam")


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
def add_event(body: EventIn, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    ev = body.key.strip().lower()
    if not EVENT_RE.match(ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    e = tba_call(tba_key_or_409(db), f"/event/{ev}/simple", ttl=3600)
    now = time.time()
    with DB.tx(db):
        db.execute("INSERT INTO events(key,name,year,start_date,added_at,added_by) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET name=excluded.name, year=excluded.year, start_date=excluded.start_date",
                   (ev, e.get("name"), e.get("year"), e.get("start_date"), now, user.id))
        DB.set_setting(db, "events_configured", True)
        workerconf.write(db)
    r = db.execute("SELECT * FROM events WHERE key=?", (ev,)).fetchone()
    return EventOut(key=r["key"], name=r["name"], year=r["year"], start_date=r["start_date"], added_at=r["added_at"])


@router.delete("/{ev}", status_code=204, summary="Stop following an event (admin; its data is kept)")
def remove_event(ev: str = Depends(valid_event), user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    with DB.tx(db):
        if not db.execute("DELETE FROM events WHERE key=?", (ev,)).rowcount:
            raise HTTPException(404, "not followed")
        DB.set_setting(db, "events_configured", True)
        workerconf.write(db)
    return None
