"""Admin: teammates' accounts and roles, and their one-time connection keys."""
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db as DB, textrules
from ..config import cfg
from ..deps import User, require_role
from ..security import hash_password, new_connect_key, normalize_connect_key, token_hash
from .auth import check_display_name, check_password_policy, check_username

router = APIRouter(prefix="/users", tags=["users"])
Role = Literal["viewer", "scout", "strategist", "admin"]


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: Role
    disabled: bool
    created_at: float
    last_login: float | None
    active_sessions: int
    has_password: bool
    connect_key_expires: float | None = Field(description="expiry of the account's unused connection key, or null")


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=2, max_length=32)
    display_name: str = Field("", max_length=64)
    password: str = Field("", max_length=256, description="optional for non-admins")
    role: Role = Field("viewer", description="least privilege: new accounts are viewers unless you pick a role")


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(None, max_length=64)
    role: Role | None = None
    disabled: bool | None = None
    password: str | None = Field(None, max_length=256, description="admin password reset ('' = no password, non-admins only); signs the user out")


class ConnectKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: int = Field(168, ge=1, le=720, description="how long it works if unused (default 7 days, max 30)")


class ConnectKeyOut(BaseModel):
    key: str = Field(description="XXXX-XXXX-XXXX-XXXX: shown once (only its hash is stored); signs in once")
    user_id: int
    username: str
    display_name: str
    expires_at: float


def _out(db, uid=None):
    now = time.time()
    q = ("SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id AND s.expires_at>?) AS n, "
         "(SELECT k.expires_at FROM connect_keys k WHERE k.user_id=u.id AND k.used_at IS NULL AND k.expires_at>?) "
         "AS ck FROM users u" + (" WHERE u.id=?" if uid else "") + " ORDER BY u.username")
    rows = db.execute(q, (now, now, uid) if uid else (now, now)).fetchall()
    return [UserOut(id=r["id"], username=r["username"], display_name=r["display_name"], role=r["role"],
                    disabled=bool(r["disabled"]), created_at=r["created_at"], last_login=r["last_login"],
                    active_sessions=r["n"], has_password=r["pw_strength"] > 0, connect_key_expires=r["ck"])
            for r in rows]


def _active_key(db, uid, now):
    """Expiry of the account's unused, unexpired connection key, or None."""
    r = db.execute("SELECT expires_at FROM connect_keys WHERE user_id=? AND used_at IS NULL AND expires_at>?",
                   (uid, now)).fetchone()
    return r["expires_at"] if r else None


def _snap(r):
    """Audit snapshot of an account (never the password hash)."""
    return {"username": r["username"], "display_name": r["display_name"], "role": r["role"],
            "disabled": bool(r["disabled"]), "has_password": r["pw_strength"] > 0}


def _name_vs_username(name, username, strength):
    # for an account without a password the username is the whole credential, and display names are shown to everyone
    if name and strength == 0 and textrules.skeleton(name) == textrules.skeleton(username):
        raise HTTPException(422, "an account without a password needs a display name that differs from its username "
                                 "(everyone sees display names; the username is what signs in)")


def _active_admins(db, excluding=None):
    return db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0 AND id<>?",
                      (excluding or -1,)).fetchone()[0]


@router.get("", response_model=list[UserOut], summary="List accounts (admin)")
def list_users(user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    return _out(db)


@router.post("", response_model=UserOut, status_code=201, summary="Create a teammate account (admin)")
def create_user(body: UserCreate, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    check_username(body.username)
    strength = check_password_policy(body.password, body.username, body.role)
    name = check_display_name(db, body.display_name, allow_reserved=True)
    _name_vs_username(name, body.username, strength)
    now = time.time()
    with DB.tx(db):
        if db.execute("SELECT 1 FROM users WHERE username=?", (body.username,)).fetchone():
            raise HTTPException(409, "username already taken")
        uid = db.execute("INSERT INTO users(username,display_name,pw_hash,role,created_at,updated_at,pw_strength) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (body.username, name, hash_password(body.password), body.role, now, now,
                          strength)).lastrowid
        audit.record(db, request, user, "users.create", f"user:{uid}", after=_snap(
            db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()))
    return _out(db, uid)[0]


@router.patch("/{uid}", response_model=UserOut, summary="Change role / name / disable / reset password (admin)")
def patch_user(uid: int, body: UserPatch, request: Request, user: User = Depends(require_role("admin")),
               db=Depends(DB.get_db)):
    with DB.tx(db):
        r = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not r:
            raise HTTPException(404, "no such user")
        demote = (body.role is not None and body.role != "admin") or body.disabled is True
        if r["role"] == "admin" and not r["disabled"] and demote and _active_admins(db, excluding=uid) == 0:
            raise HTTPException(409, "that would leave no active admin")
        sets, args = [], []
        if body.display_name is not None:
            name = check_display_name(db, body.display_name, uid=uid, allow_reserved=True)
            strength = r["pw_strength"] if body.password is None else (2 if body.password else 0)
            _name_vs_username(name, r["username"], 0 if strength == 0 else 1)
            sets.append("display_name=?"); args.append(name)
        if body.role is not None: sets.append("role=?"); args.append(body.role)
        if body.disabled is not None: sets.append("disabled=?"); args.append(int(body.disabled))
        role = body.role or r["role"]
        if body.password is not None:
            sets.append("pw_strength=?"); args.append(check_password_policy(body.password, r["username"], role))
            sets.append("pw_hash=?"); args.append(hash_password(body.password))
        elif role == "admin" and r["pw_strength"] < 2:
            raise HTTPException(409, f"admins need a password of {cfg.PASSWORD_MIN}+ characters: "
                                     "reset their password first (or include one with the promotion)")
        if sets:
            db.execute(f"UPDATE users SET {', '.join(sets)}, updated_at=? WHERE id=?", (*args, time.time(), uid))
        if body.disabled or body.password is not None:
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        key_revoked = bool(body.disabled) and _active_key(db, uid, time.time()) is not None
        if body.disabled:                    # a disabled account keeps no key (it would work again once re-enabled)
            db.execute("DELETE FROM connect_keys WHERE user_id=?", (uid,))
        after = _snap(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        if body.password is not None:
            after["password_reset"] = True
        if key_revoked:
            after["connect_key_revoked"] = True
        audit.record(db, request, user, "users.update", f"user:{uid}", before=_snap(r), after=after)
    return _out(db, uid)[0]


@router.delete("/{uid}", status_code=204, summary="Delete an account (admin; not yourself, not the last admin)")
def delete_user(uid: int, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    if uid == user.id:
        raise HTTPException(409, "you can't delete your own account")
    with DB.tx(db):
        r = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not r:
            raise HTTPException(404, "no such user")
        if r["role"] == "admin" and not r["disabled"] and _active_admins(db, excluding=uid) == 0:
            raise HTTPException(409, "that would leave no active admin")
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        audit.record(db, request, user, "users.delete", f"user:{uid}", before=_snap(r))
    return None


@router.post("/{uid}/connect-key", response_model=ConnectKeyOut, status_code=201,
             summary="Make a one-time connection key that signs this account in (admin; replaces its unused one)")
def create_connect_key(uid: int, request: Request, body: ConnectKeyIn | None = None,
                       user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    hours = (body or ConnectKeyIn()).hours
    key, now = new_connect_key(), time.time()
    exp = now + hours * 3600
    with DB.tx(db):
        r = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not r:
            raise HTTPException(404, "no such user")
        if r["disabled"]:
            raise HTTPException(409, "that account is disabled: enable the account first")
        old = _active_key(db, uid, now)
        db.execute("DELETE FROM connect_keys WHERE user_id=? OR expires_at<?", (uid, now))
        db.execute("INSERT INTO connect_keys(key_hash,user_id,created_at,expires_at,created_by) VALUES(?,?,?,?,?)",
                   (token_hash(normalize_connect_key(key)), uid, now, exp, user.id))
        # never the key itself: only when it stops working (and the one it replaced)
        audit.record(db, request, user, "auth.key_created", f"user:{uid}",
                     before={"expires_at": old} if old else None, after={"expires_at": exp})
    return ConnectKeyOut(key=key, user_id=uid, username=r["username"], display_name=r["display_name"], expires_at=exp)


@router.delete("/{uid}/connect-key", status_code=204,
               summary="Revoke this account's unused connection key (admin; 204 also when there was none)")
def revoke_connect_key(uid: int, request: Request, user: User = Depends(require_role("admin")),
                       db=Depends(DB.get_db)):
    now = time.time()
    with DB.tx(db):
        if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(404, "no such user")
        old = _active_key(db, uid, now)
        db.execute("DELETE FROM connect_keys WHERE user_id=? OR expires_at<?", (uid, now))
        if old:
            audit.record(db, request, user, "auth.key_revoked", f"user:{uid}", before={"expires_at": old})
        else:
            request.state.audited = True     # nothing changed: no fallback http.delete entry either
    return None
