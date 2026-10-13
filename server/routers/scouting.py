"""Shared scouting data: picklists (with full version history), notes (plain text, soft delete / hide), identity
overrides (scouts' fixes queued for strategist approval by default). Every change is in the audit log."""
import json, os, re, threading, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import audit, db as DB, textrules
from ..config import cfg
from ..deps import MATCH_RE, TEAM_RE, User, public_name, rate_limit, require_role, valid_event
from ..security import atomic_write_json

router = APIRouter(tags=["scouting"])
NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")
MAX_PICKLIST_BYTES = 256 * 1024
NOTE_MAX = 4000
MAX_PENDING_PER_USER = 30


def _who(db, uid):
    return public_name(db, uid)        # {id, display_name}: never the username (audit: user enumeration)


# ------------------------------------------------------------------ picklists
class PicklistOut(BaseModel):
    event: str
    name: str
    data: dict
    version: int
    updated_at: float
    updated_by: dict | None = Field(description="{id, display_name}")


class PicklistVersionOut(BaseModel):
    version: int
    updated_at: float
    updated_by: dict | None
    note: str | None = Field(None, description='e.g. "restored v3"')
    current: bool
    data: dict | None = Field(None, description="only on GET .../versions/{v}")


WEIGHT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class PicklistData(BaseModel):
    """Strict shape (web/event.js): team keys and numeric weights only -- anything else is rejected, so a
    strategist can't store markup that another user's browser renders (security audit finding 1)."""
    model_config = ConfigDict(extra="forbid")
    stars: list[str] | None = Field(None, max_length=200)
    dnp: list[str] | None = Field(None, max_length=200)
    weights: dict[str, float] | None = Field(None, max_length=40)

    @field_validator("stars", "dnp")
    @classmethod
    def _teams(cls, v):
        if v is not None and not all(isinstance(t, str) and TEAM_RE.match(t) for t in v):
            raise ValueError("team keys only, e.g. frc4911")
        return v

    @field_validator("weights")
    @classmethod
    def _weights(cls, v):
        if v is not None:
            for k, w in v.items():
                if not WEIGHT_KEY_RE.match(k) or not (-10.0 <= w <= 10.0) or w != w:
                    raise ValueError("weights: short lowercase metric names -> numbers in -10..10")
        return v


class PicklistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: PicklistData = Field(description="{stars:[team], dnp:[team], weights:{metric: number}} (web/event.js)")
    base_version: int | None = Field(None, description="optimistic concurrency: 409 if the stored version differs")


def _pl_name(name):
    if not NAME_RE.match(name):
        raise HTTPException(422, "picklist name: 1-40 letters, digits, space . _ -")
    return name


def _pl_out(db, r):
    return PicklistOut(event=r["event"], name=r["name"], data=json.loads(r["data"]), version=r["version"],
                       updated_at=r["updated_at"], updated_by=_who(db, r["updated_by"]))


def _pl_row(db, ev, name):
    return db.execute("SELECT * FROM picklists WHERE event=? AND name=?", (ev, name)).fetchone()


def _pl_save(db, ev, name, blob, user, note=None) -> int:
    """Store `blob` as the next version (numbers keep counting across deletes). Call inside a transaction."""
    cur = _pl_row(db, ev, name)
    hist = db.execute("SELECT MAX(version) FROM picklist_versions WHERE event=? AND name=?", (ev, name)).fetchone()[0]
    v = max(cur["version"] if cur else 0, hist or 0) + 1
    now = time.time()
    db.execute("INSERT INTO picklists(event,name,data,version,updated_at,updated_by) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(event,name) DO UPDATE SET data=excluded.data, version=excluded.version, "
               "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
               (ev, name, blob, v, now, user.id))
    db.execute("INSERT INTO picklist_versions(event,name,version,data,updated_at,updated_by,note) VALUES(?,?,?,?,?,?,?)",
               (ev, name, v, blob, now, user.id, note))
    return v


def apply_picklist_state(db, request, user, ev, name, state, action, ref=None):
    """Make picklist ev/name match an audit snapshot ({"version": n} -> that version's data as a new version;
    None -> deleted; history is kept either way) and record it. Call inside a transaction."""
    cur = _pl_row(db, ev, name)
    before = {"version": cur["version"]} if cur else None
    if state is None:
        if cur:
            db.execute("DELETE FROM picklists WHERE event=? AND name=?", (ev, name))
        after = None
    else:
        h = db.execute("SELECT data FROM picklist_versions WHERE event=? AND name=? AND version=?",
                       (ev, name, int(state["version"]))).fetchone()
        if not h:
            raise HTTPException(404, "that picklist version doesn't exist")
        after = {"version": _pl_save(db, ev, name, h["data"], user, note=f"restored v{state['version']}")}
    return audit.record(db, request, user, action, f"{ev}/{name}", ev, before, after, ref)


@router.get("/events/{ev}/picklists", response_model=list[PicklistOut], summary="All picklists for an event (viewer+)")
def list_picklists(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    return [_pl_out(db, r) for r in db.execute("SELECT * FROM picklists WHERE event=? ORDER BY name", (ev,))]


@router.get("/events/{ev}/picklists/{name}", response_model=PicklistOut, summary="One picklist (viewer+; 404 if none yet)")
def get_picklist(name: str, ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")),
                 db=Depends(DB.get_db)):
    r = _pl_row(db, ev, _pl_name(name))
    if not r:
        raise HTTPException(404, "no such picklist")
    return _pl_out(db, r)


@router.put("/events/{ev}/picklists/{name}", response_model=PicklistOut,
            summary="Create/replace a picklist (strategist+; every version is kept)")
def put_picklist(name: str, body: PicklistIn, request: Request, ev: str = Depends(valid_event),
                 user: User = Depends(require_role("strategist")), db=Depends(DB.get_db)):
    _pl_name(name)
    blob = json.dumps(body.data.model_dump(exclude_unset=True), separators=(",", ":"))
    if len(blob) > MAX_PICKLIST_BYTES:
        raise HTTPException(413, "picklist too large")
    rate_limit(user, "picklist")
    with DB.tx(db):
        r = _pl_row(db, ev, name)
        if r and body.base_version is not None and body.base_version != r["version"]:
            raise HTTPException(409, {"message": "changed by someone else", "current": _pl_out(db, r).model_dump()})
        v = _pl_save(db, ev, name, blob, user)
        audit.record(db, request, user, "picklist.save", f"{ev}/{name}", ev,
                     {"version": r["version"]} if r else None, {"version": v})
    return _pl_out(db, _pl_row(db, ev, name))


@router.delete("/events/{ev}/picklists/{name}", status_code=204,
               summary="Delete a picklist (strategist+; its history stays restorable)")
def delete_picklist(name: str, request: Request, ev: str = Depends(valid_event),
                    user: User = Depends(require_role("strategist")), db=Depends(DB.get_db)):
    _pl_name(name)
    rate_limit(user, "picklist")
    with DB.tx(db):
        if _pl_row(db, ev, name):
            apply_picklist_state(db, request, user, ev, name, None, "picklist.delete")
    return None


@router.get("/events/{ev}/picklists/{name}/versions", response_model=list[PicklistVersionOut],
            summary="Version history of a picklist, newest first (viewer+; also for deleted ones)")
def picklist_versions(name: str, ev: str = Depends(valid_event), limit: int = Query(200, ge=1, le=1000),
                      user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    cur = _pl_row(db, ev, _pl_name(name))
    rows = db.execute("SELECT version, updated_at, updated_by, note FROM picklist_versions WHERE event=? AND name=? "
                      "ORDER BY version DESC LIMIT ?", (ev, name, limit)).fetchall()
    if not rows and not cur:
        raise HTTPException(404, "no such picklist")
    return [PicklistVersionOut(version=r["version"], updated_at=r["updated_at"], updated_by=_who(db, r["updated_by"]),
                               note=r["note"], current=bool(cur and cur["version"] == r["version"])) for r in rows]


@router.get("/events/{ev}/picklists/{name}/versions/{v}", response_model=PicklistVersionOut,
            summary="One old version of a picklist, with its data (viewer+)")
def picklist_version(name: str, v: int, ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")),
                     db=Depends(DB.get_db)):
    r = db.execute("SELECT * FROM picklist_versions WHERE event=? AND name=? AND version=?",
                   (ev, _pl_name(name), v)).fetchone()
    if not r:
        raise HTTPException(404, "no such version")
    cur = _pl_row(db, ev, name)
    return PicklistVersionOut(version=r["version"], updated_at=r["updated_at"], updated_by=_who(db, r["updated_by"]),
                              note=r["note"], current=bool(cur and cur["version"] == v), data=json.loads(r["data"]))


@router.post("/events/{ev}/picklists/{name}/versions/{v}/restore", response_model=PicklistOut,
             summary="Restore an old version (saved as a new version; strategist+)")
def restore_picklist_version(name: str, v: int, request: Request, ev: str = Depends(valid_event),
                             user: User = Depends(require_role("strategist")), db=Depends(DB.get_db)):
    _pl_name(name)
    rate_limit(user, "picklist")
    with DB.tx(db):
        apply_picklist_state(db, request, user, ev, name, {"version": v}, "picklist.restore")
    return _pl_out(db, _pl_row(db, ev, name))


# ------------------------------------------------------------------ notes
class NoteOut(BaseModel):
    id: int
    event: str
    team: str | None
    match: str | None
    text: str = Field(description="plain text: render with textContent / escaped, never as HTML")
    author: dict | None = Field(description="{id, display_name}")
    created_at: float
    updated_at: float
    edited_by: dict | None = Field(None, description="set when someone other than the author last edited it")
    removed: str | None = Field(None, description="deleted | hidden | reverted (only with include_removed)")
    removed_by: dict | None = None
    removed_at: float | None = None


def _note_text(v):
    try:
        return textrules.plain_text(v, NOTE_MAX, multiline=True, what="note")
    except textrules.TextError as e:
        raise ValueError(str(e))


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str | None = Field(None, description="frc4911")
    match: str | None = Field(None, description="2026wasam_qm12")
    text: str = Field(min_length=1, max_length=NOTE_MAX, description="plain text (no HTML tags / entities)")

    @field_validator("text")
    @classmethod
    def check_text(cls, v):
        return _note_text(v)


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=NOTE_MAX)

    @field_validator("text")
    @classmethod
    def check_text(cls, v):
        return _note_text(v)


def _note_out(db, r, full=False):
    edited = r["edited_by"] if r["edited_by"] and r["edited_by"] != r["author_id"] else None
    return NoteOut(id=r["id"], event=r["event"], team=r["team"], match=r["match"], text=r["text"],
                   author=_who(db, r["author_id"]), created_at=r["created_at"], updated_at=r["updated_at"],
                   edited_by=_who(db, edited) if edited else None,
                   removed=r["removed_kind"] if full else None,
                   removed_by=_who(db, r["removed_by"]) if full and r["removed_by"] else None,
                   removed_at=r["removed_at"] if full else None)


def _note_snap(r):
    if r is None:
        return None
    return {"text": r["text"], "removed_kind": r["removed_kind"], "removed_at": r["removed_at"],
            "removed_by": r["removed_by"]}


def _note_row(db, nid):
    return db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()


def apply_note_state(db, request, user, nid, state, action, ref=None):
    """Make note `nid` match an audit snapshot (None = removed) and record it. Call inside a transaction."""
    r = _note_row(db, nid)
    if not r:
        raise HTTPException(404, "no such note")
    now = time.time()
    if state is None:
        if r["removed_at"] is None:
            db.execute("UPDATE notes SET removed_at=?, removed_by=?, removed_kind='reverted' WHERE id=?",
                       (now, user.id, nid))
    else:
        db.execute("UPDATE notes SET text=?, removed_at=?, removed_by=?, removed_kind=?, updated_at=? WHERE id=?",
                   (state["text"], state.get("removed_at"), state.get("removed_by"), state.get("removed_kind"), now, nid))
    after = _note_row(db, nid)
    return audit.record(db, request, user, action, str(nid), r["event"], _note_snap(r), _note_snap(after), ref)


def _check_refs(ev, team, match):
    if team is not None and not TEAM_RE.match(team):
        raise HTTPException(422, "team must look like frc4911")
    if match is not None:
        m = MATCH_RE.match(match)
        if not m or m.group(1) != ev:
            raise HTTPException(422, f"match must look like {ev}_qm12")


@router.get("/events/{ev}/notes", response_model=list[NoteOut],
            summary="Notes, optionally for one team / match (viewer+; include_removed: strategist+)")
def list_notes(ev: str = Depends(valid_event), team: str | None = Query(None), match: str | None = Query(None),
               include_removed: bool = Query(False, description="also deleted / hidden notes (strategist+)"),
               user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    _check_refs(ev, team, match)
    if include_removed and not user.can("strategist"):
        raise HTTPException(403, "needs role strategist or higher")
    q, a = "SELECT * FROM notes WHERE event=?", [ev]
    if not include_removed: q += " AND removed_at IS NULL"
    if team: q += " AND team=?"; a.append(team)
    if match: q += " AND match=?"; a.append(match)
    return [_note_out(db, r, include_removed) for r in db.execute(q + " ORDER BY created_at DESC LIMIT 2000", a)]


@router.post("/events/{ev}/notes", response_model=NoteOut, status_code=201, summary="Add a note (scout+; plain text)")
def add_note(body: NoteIn, request: Request, ev: str = Depends(valid_event), user: User = Depends(require_role("scout")),
             db=Depends(DB.get_db)):
    _check_refs(ev, body.team, body.match)
    rate_limit(user, "note")
    now = time.time()
    with DB.tx(db):
        nid = db.execute("INSERT INTO notes(event,team,match,text,author_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (ev, body.team, body.match, body.text, user.id, now, now)).lastrowid
        r = _note_row(db, nid)
        audit.record(db, request, user, "note.create", str(nid), ev, None, _note_snap(r))
    return _note_out(db, r)


def _live_note(db, nid, user, author_only=False):
    r = _note_row(db, nid)
    if not r or r["removed_at"] is not None:
        raise HTTPException(404, "no such note")
    if r["author_id"] != user.id and (author_only or not user.can("strategist")):
        raise HTTPException(403, "only the author or a strategist can change this note")
    return r


@router.patch("/notes/{nid}", response_model=NoteOut, summary="Edit a note (author, or strategist+; shown as edited)")
def edit_note(nid: int, body: NotePatch, request: Request, user: User = Depends(require_role("scout")),
              db=Depends(DB.get_db)):
    rate_limit(user, "note_edit")
    with DB.tx(db):
        r = _live_note(db, nid, user)
        db.execute("UPDATE notes SET text=?, updated_at=?, edited_by=? WHERE id=?",
                   (body.text, time.time(), None if user.id == r["author_id"] else user.id, nid))
        after = _note_row(db, nid)
        audit.record(db, request, user, "note.edit", str(nid), r["event"], _note_snap(r), _note_snap(after))
    return _note_out(db, after)


@router.delete("/notes/{nid}", status_code=204,
               summary="Delete your note, or hide someone else's (strategist+). Soft: restorable")
def delete_note(nid: int, request: Request, user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    rate_limit(user, "note_edit")
    with DB.tx(db):
        r = _live_note(db, nid, user)
        kind = "deleted" if r["author_id"] == user.id else "hidden"
        db.execute("UPDATE notes SET removed_at=?, removed_by=?, removed_kind=? WHERE id=?", (time.time(), user.id, kind, nid))
        audit.record(db, request, user, "note.delete" if kind == "deleted" else "note.hide", str(nid), r["event"],
                     _note_snap(r), _note_snap(_note_row(db, nid)))
    return None


@router.post("/notes/{nid}/restore", response_model=NoteOut,
             summary="Bring back a deleted / hidden note (strategist+, or the author for their own deletion)")
def restore_note(nid: int, request: Request, user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    rate_limit(user, "note_edit")
    with DB.tx(db):
        r = _note_row(db, nid)
        if not r or (r["author_id"] != user.id and not user.can("strategist")):
            raise HTTPException(404, "no such note")
        if r["removed_at"] is None:
            raise HTTPException(409, "this note isn't removed")
        if not user.can("strategist") and not (r["removed_kind"] == "deleted" and r["removed_by"] == user.id):
            raise HTTPException(403, "a strategist hid this note; ask one to bring it back")
        db.execute("UPDATE notes SET removed_at=NULL, removed_by=NULL, removed_kind=NULL WHERE id=?", (nid,))
        after = _note_row(db, nid)
        audit.record(db, request, user, "note.restore", str(nid), r["event"], _note_snap(r), _note_snap(after))
    return _note_out(db, after)


# ------------------------------------------------------------------ identity overrides -> events/<ev>/overrides.json
_ov_lock = threading.Lock()
IDX_RE = re.compile(r"^\d{1,2}$")


def _ov_side(v):
    if v is None:
        return {}
    if len(v) > 12:
        raise ValueError("at most 12 fixes per alliance")
    for idx, team in v.items():
        if not isinstance(idx, str) or not isinstance(team, str) or not IDX_RE.match(idx) or not TEAM_RE.match(team):
            raise ValueError('expected {"<track idx>": "frc1234"}')
    return dict(v)


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    red: dict[str, str] = Field(default_factory=dict, description='track idx -> team, e.g. {"0": "frc1983"}')
    blue: dict[str, str] = Field(default_factory=dict)

    @field_validator("red", "blue")
    @classmethod
    def check_sides(cls, v):
        return _ov_side(v)


def ov_path(ev):
    return os.path.join(cfg.DATA, "events", ev, "overrides.json")


def _read_ov(ev):
    try:
        d = json.load(open(ov_path(ev)))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def scout_overrides_mode(db) -> str:
    v = DB.get_setting(db, "scout_overrides", "review")
    return v if v in ("review", "direct") else "review"


def apply_override_state(db, request, user, ev, match, entry, action, ref=None, requested_by=None):
    """Write one match's entry ({} = remove it) to overrides.json (read by the worker each poll), log it, and
    record it. Call inside a transaction."""
    with _ov_lock:
        d = _read_ov(ev)
        before = d.get(match) or {}
        if entry: d[match] = entry
        else: d.pop(match, None)
        atomic_write_json(ov_path(ev), d, mode=0o644)
    db.execute("INSERT INTO override_log(event,match,data,user_id,at,requested_by) VALUES(?,?,?,?,?,?)",
               (ev, match, json.dumps(entry or {}), user.id, time.time(), requested_by))
    audit.record(db, request, user, action, f"{ev}/{match}", ev, {"entry": before}, {"entry": entry or {}}, ref)
    return d


def _req_out(db, r, current=None):
    data, base = json.loads(r["data"]), json.loads(r["base"])
    cur = current if current is not None else (_read_ov(r["event"]).get(r["match"]) or {})
    return {"id": r["id"], "match": r["match"], "data": data, "base": base, "current": cur,
            "stale": cur != base, "status": r["status"], "by": _who(db, r["user_id"]), "at": r["created_at"],
            "decided_by": _who(db, r["decided_by"]), "decided_at": r["decided_at"]}


@router.get("/events/{ev}/overrides",
            summary="Identity overrides for an event + recent change log + fixes waiting for approval (viewer+)")
def get_overrides(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    log = [{"match": r["match"], "data": json.loads(r["data"]), "by": _who(db, r["user_id"]),
            "requested_by": _who(db, r["requested_by"]), "at": r["at"]}
           for r in db.execute("SELECT * FROM override_log WHERE event=? ORDER BY id DESC LIMIT 100", (ev,))]
    ov = _read_ov(ev)
    pending = [_req_out(db, r, ov.get(r["match"]) or {}) for r in db.execute(
        "SELECT * FROM override_requests WHERE event=? AND status='pending' ORDER BY id", (ev,))]
    return {"event": ev, "overrides": ov, "log": log, "pending": pending, "mode": scout_overrides_mode(db)}


def _submit(db, request, user, ev, match, entry):
    """Strategists/admins (or everyone when scout_overrides=direct) write straight to the file; scouts' fixes wait for
    approval so a mistaken or malicious fix never silently changes the published identities."""
    rate_limit(user, "override")
    if user.can("strategist") or scout_overrides_mode(db) == "direct":
        with DB.tx(db):
            d = apply_override_state(db, request, user, ev, match, entry, "override.set" if entry else "override.clear")
        return None, d
    with DB.tx(db):
        n = db.execute("SELECT COUNT(*) FROM override_requests WHERE user_id=? AND status='pending'", (user.id,)).fetchone()[0]
        if n >= MAX_PENDING_PER_USER:
            raise HTTPException(429, "you have too many fixes waiting for approval; ask a strategist to review them")
        db.execute("UPDATE override_requests SET status='superseded', decided_at=? "
                   "WHERE event=? AND match=? AND user_id=? AND status='pending'", (time.time(), ev, match, user.id))
        base = _read_ov(ev).get(match) or {}
        rid = db.execute("INSERT INTO override_requests(event,match,data,base,status,user_id,created_at) "
                         "VALUES(?,?,?,?,'pending',?,?)",
                         (ev, match, json.dumps(entry), json.dumps(base), user.id, time.time())).lastrowid
        audit.record(db, request, user, "override.request", f"{ev}/{match}", ev, {"entry": base},
                     {"entry": entry, "request": rid})
        r = db.execute("SELECT * FROM override_requests WHERE id=?", (rid,)).fetchone()
    return JSONResponse({"event": ev, "match": match, "status": "pending", "request": _req_out(db, r)}, status_code=202), None


@router.put("/events/{ev}/overrides/{match}",
            summary="Set the robot->team fixes for one match (strategist+: applied; scout: 202, waits for approval)")
def put_override(match: str, body: OverrideIn, request: Request, ev: str = Depends(valid_event),
                 user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    _check_refs(ev, None, match)
    entry = {al: getattr(body, al) for al in ("red", "blue") if getattr(body, al)}
    queued, d = _submit(db, request, user, ev, match, entry)
    return queued or {"event": ev, "match": match, "entry": d.get(match), "status": "applied"}


@router.delete("/events/{ev}/overrides/{match}", status_code=204,
               summary="Clear one match's fixes (strategist+: applied; scout: 202, waits for approval)")
def delete_override(match: str, request: Request, ev: str = Depends(valid_event),
                    user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    _check_refs(ev, None, match)
    queued, _ = _submit(db, request, user, ev, match, {})
    return queued


def _pending(db, ev, rid):
    r = db.execute("SELECT * FROM override_requests WHERE id=? AND event=?", (rid, ev)).fetchone()
    if not r:
        raise HTTPException(404, "no such request")
    if r["status"] != "pending":
        raise HTTPException(409, f"already {r['status']}")
    return r


@router.post("/events/{ev}/override-requests/{rid}/approve", summary="Apply a scout's identity fix (strategist+)")
def approve_override(rid: int, request: Request, ev: str = Depends(valid_event),
                     user: User = Depends(require_role("strategist")), db=Depends(DB.get_db)):
    rate_limit(user, "override")
    with DB.tx(db):
        r = _pending(db, ev, rid)
        entry = json.loads(r["data"])
        d = apply_override_state(db, request, user, ev, r["match"], entry, "override.approve", requested_by=r["user_id"])
        db.execute("UPDATE override_requests SET status='approved', decided_by=?, decided_at=? WHERE id=?",
                   (user.id, time.time(), rid))
    return {"event": ev, "match": r["match"], "entry": d.get(r["match"]), "status": "approved"}


@router.post("/events/{ev}/override-requests/{rid}/reject",
             summary="Reject a pending identity fix (strategist+), or withdraw your own (scout)")
def reject_override(rid: int, request: Request, ev: str = Depends(valid_event),
                    user: User = Depends(require_role("scout")), db=Depends(DB.get_db)):
    with DB.tx(db):
        r = _pending(db, ev, rid)
        own = r["user_id"] == user.id
        if not own and not user.can("strategist"):
            raise HTTPException(403, "only a strategist (or whoever asked) can drop this request")
        status = "withdrawn" if own and not user.can("strategist") else "rejected"
        db.execute("UPDATE override_requests SET status=?, decided_by=?, decided_at=? WHERE id=?",
                   (status, user.id, time.time(), rid))
        audit.record(db, request, user, "override.reject" if status == "rejected" else "override.withdraw",
                     f"{ev}/{r['match']}", ev, None, {"request": rid, "entry": json.loads(r["data"])})
    return {"id": rid, "status": status}
