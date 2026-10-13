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
    end_date: str | None = None
    city: str | None = None
    state_prov: str | None = None
    country: str | None = None
    short_name: str | None = Field(None, description="TBA short name, e.g. Sammamish (site build)")
    # from the worker's site build (DATA/site/api/<ev>/event.json) + the calibration files on disk
    n_done: int | None = Field(None, description="matches tracked so far (site build)")
    n_total: int | None = Field(None, description="matches known (TBA + livestream)")
    updated: float | None = Field(None, description="last site build (unix s); null = never built")
    calibrated: bool = Field(False, description="an overhead/livestream camera calibration exists")
    needs_setup: bool = Field(True, description="no camera calibration yet: matches are not tracked until Video setup")


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=5, max_length=24, description="TBA event key, e.g. 2026wasam")


COLS = ("name", "year", "start_date", "end_date", "city", "state_prov", "country")


def _snap(db, ev):
    r = db.execute("SELECT key, name, year, start_date, end_date, city, state_prov, country, added_at, added_by "
                   "FROM events WHERE key=?", (ev,)).fetchone()
    return {"row": dict(r)} if r else None


def _upsert(db, ev, row, added_at, added_by):
    db.execute("INSERT INTO events(key,name,year,start_date,end_date,city,state_prov,country,added_at,added_by) "
               "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET name=excluded.name, year=excluded.year, "
               "start_date=excluded.start_date, end_date=excluded.end_date, city=excluded.city, "
               "state_prov=excluded.state_prov, country=excluded.country",
               (ev, *[row.get(k) for k in COLS], added_at, added_by))


def apply_events_state(db, request, user, ev, state, action, ref=None):
    """Follow / unfollow `ev` to match an audit snapshot ({"row": {...}} or None) and record it. Inside a tx."""
    before = _snap(db, ev)
    if state is None:
        db.execute("DELETE FROM events WHERE key=?", (ev,))
    else:
        row = state["row"]
        _upsert(db, ev, row, row.get("added_at") or time.time(), row.get("added_by"))
    DB.set_setting(db, "events_configured", True)
    workerconf.write(db)
    return audit.record(db, request, user, action, ev, ev, before, _snap(db, ev), ref)


_SITE = {}      # path -> (mtime, summary): the worker rewrites event.json after every match


def _site_summary(ev):
    """Header of the worker's DATA/site/api/<ev>/event.json (name, dates, place, progress), {} if never built."""
    p = os.path.join(cfg.DATA, "site", "api", ev, "event.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    c = _SITE.get(p)
    if c and c[0] == mt:
        return c[1]
    try:
        d = json.load(open(p))
        out = {k: d.get(k) for k in ("name", "short_name", "start", "end", "city", "state_prov", "n_done", "n_total", "updated",
                                     "calibrated")}
    except (OSError, ValueError, AttributeError):
        out = {}
    _SITE[p] = (mt, out)
    return out


def calibrated(ev) -> bool:
    """Overhead camera (camera.json) or a livestream camera (camera_<source>.json) saved for this event.  The repo's
    shipped calibrations (events/<ev>/) aren't in the api image: the worker reports those in the site build."""
    d = os.path.join(cfg.DATA, "events", ev)
    try:
        names = os.listdir(d)
    except OSError:
        return False
    return any(n == "camera.json" or (n.startswith("camera_") and n.endswith(".json")) for n in names)


def event_out(r) -> EventOut:
    s = _site_summary(r["key"])
    cal = calibrated(r["key"]) or bool(s.get("calibrated"))
    return EventOut(key=r["key"], name=r["name"] or s.get("name"), year=r["year"],
                    start_date=r["start_date"] or s.get("start"), end_date=r["end_date"] or s.get("end"),
                    city=r["city"] or s.get("city"), state_prov=r["state_prov"] or s.get("state_prov"),
                    country=r["country"], short_name=s.get("short_name"), added_at=r["added_at"],
                    n_done=s.get("n_done"), n_total=s.get("n_total"),
                    updated=s.get("updated"), calibrated=cal, needs_setup=not cal)


@router.get("", response_model=list[EventOut], summary="Followed events with dates, place, progress, setup state (viewer+)")
def list_events(user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    return [event_out(r) for r in db.execute("SELECT * FROM events ORDER BY start_date, key").fetchall()]


@router.post("", response_model=EventOut, status_code=201, summary="Follow an event (admin; checked on TBA)")
def add_event(body: EventIn, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    ev = body.key.strip().lower()
    if not EVENT_RE.match(ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    e = tba_call(tba_key_or_409(db), f"/event/{ev}/simple", ttl=3600)
    now = time.time()
    with DB.tx(db):
        before = _snap(db, ev)
        _upsert(db, ev, e, now, user.id)
        DB.set_setting(db, "events_configured", True)
        workerconf.write(db)
        audit.record(db, request, user, "events.follow", ev, ev, before, _snap(db, ev))
    return event_out(db.execute("SELECT * FROM events WHERE key=?", (ev,)).fetchone())


@router.delete("/{ev}", status_code=204, summary="Stop following an event (admin; its data is kept)")
def remove_event(request: Request, ev: str = Depends(valid_event), user: User = Depends(require_role("admin")),
                 db=Depends(DB.get_db)):
    with DB.tx(db):
        if not _snap(db, ev):
            raise HTTPException(404, "not followed")
        apply_events_state(db, request, user, ev, None, "events.unfollow")
    return None
