"""Shared scouting data: picklists, notes (per team / per match), identity overrides."""
import json, os, re, threading, time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db as DB
from ..config import cfg
from ..deps import MATCH_RE, TEAM_RE, User, require_role, valid_event
from ..security import atomic_write_json

router = APIRouter(tags=["scouting"])
NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")
MAX_PICKLIST_BYTES = 256 * 1024


def _who(db, uid):
    if uid is None: return None
    r = db.execute("SELECT id, username, display_name FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ picklists
class PicklistOut(BaseModel):
    event: str
    name: str
    data: dict
    version: int
    updated_at: float
    updated_by: dict | None


class PicklistIn(BaseModel):
    data: dict = Field(description="client-defined JSON object (web/event.js: {stars:[team], dnp:[team], weights:{metric:w}})")
    base_version: int | None = Field(None, description="optimistic concurrency: 409 if the stored version differs")


def _pl_name(name):
    if not NAME_RE.match(name):
        raise HTTPException(422, "picklist name: 1-40 letters, digits, space . _ -")
    return name


def _pl_out(db, r):
    return PicklistOut(event=r["event"], name=r["name"], data=json.loads(r["data"]), version=r["version"],
                       updated_at=r["updated_at"], updated_by=_who(db, r["updated_by"]))


@router.get("/events/{ev}/picklists", response_model=list[PicklistOut], summary="All picklists for an event (viewer+)")
def list_picklists(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    return [_pl_out(db, r) for r in db.execute("SELECT * FROM picklists WHERE event=? ORDER BY name", (ev,))]


@router.get("/events/{ev}/picklists/{name}", response_model=PicklistOut, summary="One picklist (viewer+; 404 if none yet)")
def get_picklist(name: str, ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")),
                 db=Depends(DB.get_db)):
    r = db.execute("SELECT * FROM picklists WHERE event=? AND name=?", (ev, _pl_name(name))).fetchone()
    if not r:
        raise HTTPException(404, "no such picklist")
    return _pl_out(db, r)


@router.put("/events/{ev}/picklists/{name}", response_model=PicklistOut, summary="Create/replace a picklist (strategist+)")
def put_picklist(name: str, body: PicklistIn, ev: str = Depends(valid_event),
                 user: User = Depends(require_role("strategist")), db=Depends(DB.get_db)):
    _pl_name(name)
    blob = json.dumps(body.data, separators=(",", ":"))
    if len(blob) > MAX_PICKLIST_BYTES:
        raise HTTPException(413, "picklist too large")
    with DB.tx(db):
        r = db.execute("SELECT * FROM picklists WHERE event=? AND name=?", (ev, name)).fetchone()
        if r and body.base_version is not None and body.base_version != r["version"]:
            raise HTTPException(409, {"message": "changed by someone else", "current": _pl_out(db, r).model_dump()})
        v = (r["version"] + 1) if r else 1
        db.execute("INSERT INTO picklists(event,name,data,version,updated_at,updated_by) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(event,name) DO UPDATE SET data=excluded.data, version=excluded.version, "
                   "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                   (ev, name, blob, v, time.time(), user.id))
    return _pl_out(db, db.execute("SELECT * FROM picklists WHERE event=? AND name=?", (ev, name)).fetchone())


@router.delete("/events/{ev}/picklists/{name}", status_code=204, summary="Delete a picklist (strategist+)")
def delete_picklist(name: str, ev: str = Depends(valid_event), user: User = Depends(require_role("strategist")),
                    db=Depends(DB.get_db)):
    db.execute("DELETE FROM picklists WHERE event=? AND name=?", (ev, _pl_name(name)))
    return None


# ------------------------------------------------------------------ notes
class NoteOut(BaseModel):
    id: int
    event: str
    team: str | None
    match: str | None
    text: str
    author: dict | None
    created_at: float
    updated_at: float


class NoteIn(BaseModel):
    team: str | None = Field(None, description="frc4911")
    match: str | None = Field(None, description="2026wasam_qm12")
    text: str = Field(min_length=1, max_length=4000)


class NotePatch(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _note_out(db, r):
    return NoteOut(id=r["id"], event=r["event"], team=r["team"], match=r["match"], text=r["text"],
                   author=_who(db, r["author_id"]), created_at=r["created_at"], updated_at=r["updated_at"])


def _check_refs(ev, team, match):
    if team is not None and not TEAM_RE.match(team):
        raise HTTPException(422, "team must look like frc4911")
    if match is not None:
        m = MATCH_RE.match(match)
        if not m or m.group(1) != ev:
            raise HTTPException(422, f"match must look like {ev}_qm12")


@router.get("/events/{ev}/notes", response_model=list[NoteOut], summary="Notes, optionally for one team / match (viewer+)")
def list_notes(ev: str = Depends(valid_event), team: str | None = Query(None), match: str | None = Query(None),
               user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    _check_refs(ev, team, match)
    q, a = "SELECT * FROM notes WHERE event=?", [ev]
    if team: q += " AND team=?"; a.append(team)
    if match: q += " AND match=?"; a.append(match)
    return [_note_out(db, r) for r in db.execute(q + " ORDER BY created_at DESC LIMIT 2000", a)]


@router.post("/events/{ev}/notes", response_model=NoteOut, status_code=201, summary="Add a note (scout+)")
def add_note(body: NoteIn, ev: str = Depends(valid_event), user: User = Depends(require_role("scout")),
             db=Depends(DB.get_db)):
    _check_refs(ev, body.team, body.match)
    now = time.time()
    nid = db.execute("INSERT INTO notes(event,team,match,text,author_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                     (ev, body.team, body.match, body.text.strip(), user.id, now, now)).lastrowid
    return _note_out(db, db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone())


def _own_note(db, nid, user):
    r = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if not r:
        raise HTTPException(404, "no such note")
    if r["author_id"] != user.id and not user.can("strategist"):
        raise HTTPException(403, "only the author or a strategist can change this note")
    return r


@router.patch("/notes/{nid}", response_model=NoteOut, summary="Edit a note (author, or strategist+)")
def edit_note(nid: int, body: NotePatch, user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    _own_note(db, nid, user)
    db.execute("UPDATE notes SET text=?, updated_at=? WHERE id=?", (body.text.strip(), time.time(), nid))
    return _note_out(db, db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone())


@router.delete("/notes/{nid}", status_code=204, summary="Delete a note (author, or strategist+)")
def delete_note(nid: int, user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    _own_note(db, nid, user)
    db.execute("DELETE FROM notes WHERE id=?", (nid,))
    return None


# ------------------------------------------------------------------ identity overrides -> events/<ev>/overrides.json
_ov_lock = threading.Lock()
IDX_RE = re.compile(r"^\d{1,2}$")


class OverrideIn(BaseModel):
    red: dict[str, str] = Field(default_factory=dict, description='track idx -> team, e.g. {"0": "frc1983"}')
    blue: dict[str, str] = Field(default_factory=dict)


def ov_path(ev):
    return os.path.join(cfg.DATA, "events", ev, "overrides.json")


def _read_ov(ev):
    try:
        d = json.load(open(ov_path(ev)))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


@router.get("/events/{ev}/overrides", summary="Identity overrides for an event + recent change log (viewer+)")
def get_overrides(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    log = [{"match": r["match"], "data": json.loads(r["data"]), "by": _who(db, r["user_id"]), "at": r["at"]}
           for r in db.execute("SELECT * FROM override_log WHERE event=? ORDER BY id DESC LIMIT 100", (ev,))]
    return {"event": ev, "overrides": _read_ov(ev), "log": log}


def _write_match(ev, match, entry, user, db):
    with _ov_lock:
        d = _read_ov(ev)
        if entry: d[match] = entry
        else: d.pop(match, None)
        atomic_write_json(ov_path(ev), d, mode=0o644)
    db.execute("INSERT INTO override_log(event,match,data,user_id,at) VALUES(?,?,?,?,?)",
               (ev, match, json.dumps(entry or {}), user.id, time.time()))
    return d


@router.put("/events/{ev}/overrides/{match}", summary="Set the robot->team fixes for one match (scout+)")
def put_override(match: str, body: OverrideIn, ev: str = Depends(valid_event),
                 user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    _check_refs(ev, None, match)
    entry = {}
    for al in ("red", "blue"):
        m = getattr(body, al)
        for idx, team in m.items():
            if not IDX_RE.match(idx) or not TEAM_RE.match(team):
                raise HTTPException(422, 'expected {"<track idx>": "frc1234"}')
        if m: entry[al] = dict(m)
    d = _write_match(ev, match, entry, user, db)
    return {"event": ev, "match": match, "entry": d.get(match)}


@router.delete("/events/{ev}/overrides/{match}", status_code=204, summary="Clear one match's fixes (scout+)")
def delete_override(match: str, ev: str = Depends(valid_event), user: User = Depends(require_role("scout")),
                    db=Depends(DB.get_db)):
    _check_refs(ev, None, match)
    _write_match(ev, match, {}, user, db)
    return None
