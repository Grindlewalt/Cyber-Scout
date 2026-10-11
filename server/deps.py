"""Shared request dependencies for routers: sessions, role checks, CSRF, validation helpers.

Usage in a feature router (e.g. server/routers/sources.py):

    from fastapi import APIRouter, Depends
    from ..deps import require_role, valid_event, User
    from ..db import get_db

    router = APIRouter(tags=["sources"])

    @router.get("/events/{ev}/sources")
    def list_sources(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")), db=Depends(get_db)): ...

    @router.put("/events/{ev}/sources/{sid}")          # mutating -> require_role also enforces the CSRF header
    def put_source(..., user: User = Depends(require_role("admin"))): ...
"""
import re, secrets, time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response

from . import db as DB
from .config import cfg, SESSION_COOKIE, CSRF_COOKIE, CSRF_HEADER, RANK
from .security import token_hash, consteq

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EVENT_RE = re.compile(r"^\d{4}[a-z0-9]{1,16}$")
MATCH_RE = re.compile(r"^(\d{4}[a-z0-9]{1,16})_(qm|ef|qf|sf|f)\d{1,3}(m\d{1,2})?$")
TEAM_RE = re.compile(r"^frc\d{1,5}[A-Z]?$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")


@dataclass
class User:
    id: int
    username: str
    display_name: str
    role: str
    csrf: str = ""

    def can(self, role: str) -> bool:
        return RANK[self.role] >= RANK[role]

    def public(self):
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role}


def client_ip(request: Request) -> str:
    # nginx overwrites X-Real-IP with $remote_addr; the API port is not published outside the compose network
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() == "https"


def cookie_secure(request: Request) -> bool:
    if cfg.COOKIE_SECURE == "1": return True
    if cfg.COOKIE_SECURE == "0": return False
    return is_https(request)


def load_session(db, request: Request) -> User | None:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok or len(tok) > 200:
        return None
    th = token_hash(tok)
    r = db.execute("SELECT s.token_hash, s.csrf, s.last_seen, s.expires_at, u.id, u.username, u.display_name, u.role, u.disabled "
                   "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?", (th,)).fetchone()
    if not r:
        return None
    now = time.time()
    if r["disabled"] or now > r["expires_at"] or now - r["last_seen"] > cfg.SESSION_IDLE_DAYS * 86400:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
        return None
    if now - r["last_seen"] > 300:                       # throttle writes (auth_request hits this per file)
        db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, th))
    return User(r["id"], r["username"], r["display_name"], r["role"], r["csrf"])


def check_csrf(request: Request, user: User | None):
    """Mutating requests must echo the CSRF token in the X-CSRF-Token header: the session's token when signed in,
    else the cs_csrf cookie (double submit; used by /setup and /auth/login)."""
    if request.method in SAFE_METHODS:
        return
    hdr = request.headers.get(CSRF_HEADER)
    want = user.csrf if user else request.cookies.get(CSRF_COOKIE)
    if not consteq(hdr, want):
        raise HTTPException(403, "missing or bad CSRF token (send the cs_csrf cookie value as X-CSRF-Token)")


def optional_user(request: Request, db=Depends(DB.get_db)) -> User | None:
    return load_session(db, request)


def require_role(role: str):
    """Dependency factory: signed-in user with at least `role` (viewer < scout < strategist < admin).
    401 if not signed in, 403 if the role is too low; also enforces CSRF on mutating methods."""
    assert role in RANK, role

    def dep(request: Request, db=Depends(DB.get_db)) -> User:
        u = load_session(db, request)
        if not u:
            raise HTTPException(401, "sign in required")
        check_csrf(request, u)
        if not u.can(role):
            raise HTTPException(403, f"needs role {role} or higher")
        return u
    dep.__name__ = f"require_{role}"
    return dep


def csrf_anonymous(request: Request):
    check_csrf(request, None)


def valid_event(ev: str) -> str:
    if not EVENT_RE.match(ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    return ev


def set_session_cookies(request: Request, resp: Response, token: str | None, csrf: str | None):
    sec = cookie_secure(request)
    age = int(cfg.SESSION_MAX_DAYS * 86400)
    if token is None:
        resp.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=sec)
    else:
        resp.set_cookie(SESSION_COOKIE, token, max_age=age, path="/", httponly=True, samesite="lax", secure=sec)
    set_csrf_cookie(request, resp, csrf or secrets.token_urlsafe(24))


def set_csrf_cookie(request: Request, resp: Response, csrf: str):
    """Readable by page JS (not http-only): the double-submit token echoed in X-CSRF-Token."""
    resp.set_cookie(CSRF_COOKIE, csrf, max_age=int(cfg.SESSION_MAX_DAYS * 86400), path="/", httponly=False,
                    samesite="lax", secure=cookie_secure(request))
