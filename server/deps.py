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
import ipaddress, re, secrets, time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response

from . import db as DB
from .config import cfg, PREFIX, SESSION_COOKIE, CSRF_COOKIE, CSRF_HEADER, RANK
from .security import token_hash, consteq, write_limiter

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
    # nginx overwrites X-Real-IP with $remote_addr, which its realip module sets to the real client address when
    # the TCP peer is a trusted proxy (docker gateway / cloudflared); the API port isn't published outside compose
    return (request.headers.get("x-real-ip") or (request.client.host if request.client else "?")).strip()[:64]


# addresses that count as "the team network": RFC 1918 LAN, loopback, Tailscale (100.64/10 CGNAT, fd7a:115c:a1e0::/48)
TRUSTED_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "100.64.0.0/10",
    "::1/128", "fc00::/7", "fe80::/10")]
# headers only a proxy / CDN adds: Cloudflare always sets Cf-Connecting-Ip / Cf-Ray / Cdn-Loop on tunnel traffic
_PROXY_MARKERS = ("cf-connecting-ip", "cf-ray", "cdn-loop", "cf-visitor", "true-client-ip", "forwarded")


def _trusted_addr(a: str) -> bool:
    a = (a or "").strip()
    if a.startswith("[") and "]" in a:
        a = a[1:a.index("]")]
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in TRUSTED_NETS)


def via_public(request: Request) -> bool:
    """True unless the request came straight from a private/Tailscale address. Fails closed: any CDN header, any
    public or unparseable address in X-Real-IP / X-Forwarded-For counts as public (so tunnel traffic is public even
    if nginx's realip trust list is wrong)."""
    h = request.headers
    if any(h.get(k) for k in _PROXY_MARKERS):
        return True
    chain = [client_ip(request)] + [x for x in h.get("x-forwarded-for", "").split(",") if x.strip()]
    return not all(_trusted_addr(a) for a in chain)


# ---- team-wide safety switches (settings table; admin page "Security")
PASSWORDLESS = ("never", "lan", "everywhere")


def passwordless_policy(db) -> str:
    v = DB.get_setting(db, "passwordless_login", "lan")
    return v if v in PASSWORDLESS else "lan"


def lockdown_state(db) -> dict | None:
    v = DB.get_setting(db, "lockdown")
    return v if isinstance(v, dict) and v.get("on") else None


def public_name(db, uid):
    """What other users may see about an account: its display name only (never the username: for passwordless
    accounts the username IS the credential)."""
    if uid is None:
        return None
    r = db.execute("SELECT id, display_name FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        return {"id": uid, "display_name": "(deleted account)"}
    return {"id": r["id"], "display_name": r["display_name"] or f"Teammate #{r['id']}"}


def rate_limit(user: "User", bucket: str):
    wait = write_limiter.hit(user.id, bucket)
    if wait:
        raise HTTPException(429, f"slow down: too many changes in a short time, try again in {wait}s",
                            headers={"Retry-After": str(wait)})


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
    r = db.execute("SELECT s.token_hash, s.csrf, s.last_seen, s.expires_at, s.net_locked, u.id, u.username, "
                   "u.display_name, u.role, u.disabled "
                   "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?", (th,)).fetchone()
    if not r:
        return None
    now = time.time()
    if r["disabled"] or now > r["expires_at"] or now - r["last_seen"] > cfg.SESSION_IDLE_DAYS * 86400:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
        return None
    if r["net_locked"]:
        # signed in without a password: only valid where passwordless sign-in is allowed right now
        pol = passwordless_policy(db)
        if pol == "never" or (pol == "lan" and via_public(request)):
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


# writes that stay allowed for everyone during a lockdown (your own password)
LOCKDOWN_EXEMPT = {PREFIX + "/auth/password"}


def require_role(role: str):
    """Dependency factory: signed-in user with at least `role` (viewer < scout < strategist < admin).
    401 if not signed in, 403 if the role is too low; also enforces CSRF on mutating methods. Mutating calls by
    non-admins are refused with 423 during a lockdown and count against the per-account write budget (429)."""
    assert role in RANK, role

    def dep(request: Request, db=Depends(DB.get_db)) -> User:
        u = load_session(db, request)
        if not u:
            raise HTTPException(401, "sign in required")
        check_csrf(request, u)
        if not u.can(role):
            raise HTTPException(403, f"needs role {role} or higher")
        if request.method not in SAFE_METHODS and not u.can("admin"):
            if request.url.path not in LOCKDOWN_EXEMPT and lockdown_state(db):
                raise HTTPException(423, "the site is read-only right now (an admin turned on lockdown)")
            rate_limit(u, "write")
        request.state.user = u
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
