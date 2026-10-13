"""Admin: teammates' accounts and roles."""
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db as DB, textrules
from ..config import cfg
from ..deps import User, require_role
from ..security import hash_password
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


def _out(db, uid=None):
    q = ("SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id AND s.expires_at>?) AS n "
         "FROM users u" + (" WHERE u.id=?" if uid else "") + " ORDER BY u.username")
    rows = db.execute(q, (time.time(), uid) if uid else (time.time(),)).fetchall()
    return [UserOut(id=r["id"], username=r["username"], display_name=r["display_name"], role=r["role"],
                    disabled=bool(r["disabled"]), created_at=r["created_at"], last_login=r["last_login"],
                    active_sessions=r["n"], has_password=r["pw_strength"] > 0) for r in rows]


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
        after = _snap(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        if body.password is not None:
            after["password_reset"] = True
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
