"""Admin safety controls: lockdown (read-only), passwordless sign-in policy, scout identity-fix review, "sign
everyone out", and the audit log (list / filter / revert one change / revert everything an account did)."""
import json, time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db as DB
from ..config import SESSION_COOKIE
from ..deps import EVENT_RE, PASSWORDLESS, User, lockdown_state, passwordless_policy, public_name, require_role
from ..security import token_hash
from .events import apply_events_state
from .scouting import apply_note_state, apply_override_state, apply_picklist_state, scout_overrides_mode
from .settings import apply_settings_state, settings_snap

router = APIRouter(tags=["admin"])


# ------------------------------------------------------------------ security switches
class SecurityOut(BaseModel):
    lockdown: dict | None = Field(description="null, or {on, at, by:{id,display_name}}: non-admin writes are refused (423)")
    passwordless_login: Literal["never", "lan", "everywhere"] = Field(
        description="where accounts without a password may sign in: never | lan (direct from a private/Tailscale "
                    "address, not through the tunnel; default) | everywhere")
    scout_overrides: Literal["review", "direct"] = Field(
        description="review (default): scouts' identity fixes wait for a strategist/admin; direct: applied at once")


class SecurityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lockdown: bool | None = None
    passwordless_login: Literal["never", "lan", "everywhere"] | None = None
    scout_overrides: Literal["review", "direct"] | None = None


def _security_out(db):
    lk = lockdown_state(db)
    return SecurityOut(lockdown={"on": True, "at": lk.get("at"), "by": public_name(db, lk.get("by"))} if lk else None,
                       passwordless_login=passwordless_policy(db), scout_overrides=scout_overrides_mode(db))


@router.get("/admin/security", response_model=SecurityOut, summary="Lockdown / sign-in policy / review mode (admin)")
def get_security(user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    return _security_out(db)


@router.patch("/admin/security", response_model=SecurityOut, summary="Change lockdown / sign-in policy / review mode (admin)")
def patch_security(body: SecurityPatch, request: Request, user: User = Depends(require_role("admin")),
                   db=Depends(DB.get_db)):
    fs = [k for k in body.model_fields_set if getattr(body, k) is not None]
    assert all(k in ("lockdown", "passwordless_login", "scout_overrides") for k in fs)
    with DB.tx(db):
        before = settings_snap(db, fs)
        if "lockdown" in fs:
            DB.set_setting(db, "lockdown", {"on": True, "at": time.time(), "by": user.id} if body.lockdown else None)
        if "passwordless_login" in fs:
            assert body.passwordless_login in PASSWORDLESS
            DB.set_setting(db, "passwordless_login", body.passwordless_login)
            if body.passwordless_login == "never":
                db.execute("DELETE FROM sessions WHERE net_locked=1")
        if "scout_overrides" in fs:
            DB.set_setting(db, "scout_overrides", body.scout_overrides)
        if fs:
            audit.record(db, request, user, "settings.security", ",".join(fs), None, before, settings_snap(db, fs))
    return _security_out(db)


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_admins: bool = Field(False, description="also sign out the other admins (your own session always stays)")


@router.post("/admin/sessions/revoke", summary="Sign everyone out (admin; your own session stays)")
def revoke_sessions(body: RevokeIn, request: Request, user: User = Depends(require_role("admin")),
                    db=Depends(DB.get_db)):
    keep = token_hash(request.cookies.get(SESSION_COOKIE, ""))
    q = "DELETE FROM sessions WHERE token_hash<>?"
    if not body.include_admins:
        q += " AND user_id IN (SELECT id FROM users WHERE role<>'admin')"
    with DB.tx(db):
        n = db.execute(q, (keep,)).rowcount
        audit.record(db, request, user, "sessions.revoke_all", "sessions", None, None,
                     {"signed_out": n, "include_admins": body.include_admins})
    return {"signed_out": n}


# ------------------------------------------------------------------ audit log
def _entry(r, reverted_by):
    return {"id": r["id"], "at": r["at"], "action": r["action"], "event": r["event"], "target": r["target"],
            "user": {"id": r["user_id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"]},
            "ip": r["ip"], "via": r["via"], "ua": r["ua"],
            "before": json.loads(r["before"]) if r["before"] else None,
            "after": json.loads(r["after"]) if r["after"] else None,
            "ref": r["ref"], "revertible": audit.revertible(r["action"]), "reverted_by": reverted_by}


@router.get("/audit", summary="Audit log, newest first (admin). Filter by account, action prefix, event, time")
def list_audit(user_id: int | None = Query(None), action: str | None = Query(None, max_length=40,
               description="prefix, e.g. 'picklist' or 'note.hide'"),
               event: str | None = Query(None, max_length=24, description="a competition key (only its changes), or "
                                         "'none' (changes not tied to one: accounts, settings, sign-ins)"),
               since: float | None = Query(None, description="unix seconds"), before_id: int | None = Query(None),
               via: Literal["lan", "public"] | None = Query(None), limit: int = Query(100, ge=1, le=500),
               changes: bool = Query(False, description="only changes: leave out sign-ins / sign-outs / failed sign-ins "
                                     "and raw request entries (http.*)"),
               user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    q, a = "SELECT * FROM audit_log WHERE 1=1", []
    if changes: q += " AND action NOT IN ('auth.login','auth.login_failed','auth.logout') AND action NOT LIKE 'http.%'"
    if user_id is not None: q += " AND user_id=?"; a.append(user_id)
    if action: q += " AND action LIKE ? ESCAPE '\\'"; a.append(action.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if event == "none": q += " AND event IS NULL"
    elif event:
        if not EVENT_RE.match(event):
            raise HTTPException(422, "bad event key (expected e.g. 2026wasam, or 'none')")
        q += " AND event=?"; a.append(event)
    if since is not None: q += " AND at>=?"; a.append(since)
    if via: q += " AND via=?"; a.append(via)
    if before_id is not None: q += " AND id<?"; a.append(before_id)
    rows = db.execute(q + " ORDER BY id DESC LIMIT ?", (*a, limit)).fetchall()
    ids = [r["id"] for r in rows]
    rev = {}
    if ids:
        for x in db.execute(f"SELECT id, ref FROM audit_log WHERE ref IN ({','.join('?' * len(ids))})", ids):
            rev.setdefault(x["ref"], x["id"])
    return {"entries": [_entry(r, rev.get(r["id"])) for r in rows],
            "next_before_id": rows[-1]["id"] if len(rows) == limit else None}


def _revert(db, request, user, e) -> int:
    """Apply entry e's `before` state back (inside a transaction); returns the new audit entry id."""
    if not audit.revertible(e["action"]):
        raise HTTPException(409, f"'{e['action']}' changes can't be reverted from the log")
    if db.execute("SELECT 1 FROM audit_log WHERE ref=?", (e["id"],)).fetchone():
        raise HTTPException(409, "already reverted")
    fam, before, tgt = audit.family(e["action"]), json.loads(e["before"]) if e["before"] else None, e["target"] or ""
    act = fam + ".revert"
    if fam == "picklist":
        ev, name = tgt.split("/", 1)
        return apply_picklist_state(db, request, user, ev, name, before, act, ref=e["id"])
    if fam == "note":
        return apply_note_state(db, request, user, int(tgt), before, act, ref=e["id"])
    if fam == "override":
        ev, match = tgt.split("/", 1)
        apply_override_state(db, request, user, ev, match, (before or {}).get("entry") or {}, act, ref=e["id"])
        return db.execute("SELECT MAX(id) FROM audit_log").fetchone()[0]
    if fam == "settings":
        return apply_settings_state(db, request, user, before or {}, act, ref=e["id"])
    if fam == "events":
        return apply_events_state(db, request, user, tgt, before, act, ref=e["id"])
    raise HTTPException(409, "not revertible")


@router.post("/audit/{aid}/revert", summary="Undo one logged change (admin): restores its 'before' state")
def revert_entry(aid: int, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    with DB.tx(db):
        e = db.execute("SELECT * FROM audit_log WHERE id=?", (aid,)).fetchone()
        if not e:
            raise HTTPException(404, "no such entry")
        new = _revert(db, request, user, e)
    return {"reverted": aid, "entry": new}


class RevertUserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int
    since: float = Field(description="unix seconds: undo this account's changes made at or after this time")
    dry_run: bool = Field(False, description="only count: what would be undone / skipped, nothing changes")


@router.post("/audit/revert-user", summary="Undo everything one account changed since a time (admin), newest first")
def revert_user(body: RevertUserIn, request: Request, user: User = Depends(require_role("admin")),
                db=Depends(DB.get_db)):
    done, skipped = [], []
    with DB.tx(db):
        rows = db.execute("SELECT * FROM audit_log WHERE user_id=? AND at>=? ORDER BY id DESC LIMIT 2000",
                          (body.user_id, body.since)).fetchall()
        for e in rows:
            if not audit.revertible(e["action"]) or db.execute("SELECT 1 FROM audit_log WHERE ref=?", (e["id"],)).fetchone():
                skipped.append(e["id"])
                continue
            if body.dry_run:
                done.append(e["id"])
                continue
            try:
                _revert(db, request, user, e)
                done.append(e["id"])
            except HTTPException:
                skipped.append(e["id"])
    return {"reverted": done, "skipped": skipped, "dry_run": body.dry_run}
