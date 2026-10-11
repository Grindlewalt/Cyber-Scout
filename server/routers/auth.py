"""Session, first-run setup, login/logout, password change, and the nginx auth_request check."""
import os, secrets, time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import db as DB, workerconf
from ..config import cfg, CSRF_COOKIE, SESSION_COOKIE
from ..deps import (User, client_ip, csrf_anonymous, load_session, require_role, set_csrf_cookie,
                    set_session_cookies, USERNAME_RE)
from ..security import consteq, hash_password, login_limiter, new_token, token_hash, verify_password

router = APIRouter(tags=["auth"])


class SessionOut(BaseModel):
    setup_required: bool = Field(description="no users yet: the UI must show the first-run wizard")
    setup_code_required: bool = Field(description="setup needs the one-time code from DATA/setup-code.txt")
    user: dict | None = Field(description="{id, username, display_name, role} or null when signed out")
    csrf: str = Field(description="send as X-CSRF-Token on POST/PUT/PATCH/DELETE (also in the cs_csrf cookie)")
    configured: bool = Field(description="TBA key set and at least one followed event (setup wizard finished)")


class SetupIn(BaseModel):
    setup_code: str | None = Field(None, max_length=64)
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(max_length=256)
    display_name: str = Field("", max_length=64)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def check_password_policy(pw: str, username: str = ""):
    if len(pw) < cfg.PASSWORD_MIN:
        raise HTTPException(422, f"password must be at least {cfg.PASSWORD_MIN} characters")
    if username and pw.lower() == username.lower():
        raise HTTPException(422, "password must not be the username")


def check_username(u: str):
    if not USERNAME_RE.match(u):
        raise HTTPException(422, "username: 2-32 letters, digits, '.', '_' or '-'")


def start_session(db, request: Request, resp: Response, user_id: int) -> str:
    tok, csrf, now = new_token(), secrets.token_urlsafe(24), time.time()
    db.execute("DELETE FROM sessions WHERE expires_at < ? OR last_seen < ?",
               (now, now - cfg.SESSION_IDLE_DAYS * 86400))
    db.execute("INSERT INTO sessions(token_hash,user_id,csrf,created_at,last_seen,expires_at) VALUES(?,?,?,?,?,?)",
               (token_hash(tok), user_id, csrf, now, now, now + cfg.SESSION_MAX_DAYS * 86400))
    db.execute("UPDATE users SET last_login=? WHERE id=?", (now, user_id))
    set_session_cookies(request, resp, tok, csrf)
    return csrf


def setup_code() -> str | None:
    try:
        return open(cfg.SETUP_CODE_FILE).read().strip() or None
    except OSError:
        return None


def ensure_setup_code(db):
    """No users yet -> make sure DATA/setup-code.txt exists (0600). Users exist -> remove it."""
    if DB.user_count(db):
        try: os.remove(cfg.SETUP_CODE_FILE)
        except OSError: pass
        return
    if not cfg.SETUP_CODE_REQUIRED or setup_code():
        return
    code = "-".join(secrets.token_hex(2) for _ in range(3))
    os.makedirs(os.path.dirname(cfg.SETUP_CODE_FILE), exist_ok=True)
    fd = os.open(cfg.SETUP_CODE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(code + "\n")


def configured(db) -> bool:
    return bool(DB.get_setting(db, "tba_key")) and db.execute("SELECT 1 FROM events LIMIT 1").fetchone() is not None


@router.get("/session", response_model=SessionOut, summary="Who am I / is setup needed (always 200)")
def session(request: Request, response: Response, db=Depends(DB.get_db)):
    u = load_session(db, request)
    n = DB.user_count(db)
    if u:
        csrf = u.csrf
        if request.cookies.get(CSRF_COOKIE) != csrf:
            set_csrf_cookie(request, response, csrf)
    else:
        csrf = request.cookies.get(CSRF_COOKIE) or ""
        if len(csrf) < 16:
            csrf = secrets.token_urlsafe(24)
            set_csrf_cookie(request, response, csrf)
    return SessionOut(setup_required=n == 0, setup_code_required=n == 0 and cfg.SETUP_CODE_REQUIRED,
                      user=u.public() if u else None, csrf=csrf, configured=configured(db))


@router.post("/setup", response_model=SessionOut, dependencies=[Depends(csrf_anonymous)],
             summary="First run only: create the admin account and sign in")
def setup(body: SetupIn, request: Request, response: Response, db=Depends(DB.get_db)):
    ip = client_ip(request)
    if login_limiter.blocked(ip, "#setup"):
        raise HTTPException(429, "too many attempts, try again later")
    check_username(body.username)
    check_password_policy(body.password, body.username)
    with DB.tx(db):
        if DB.user_count(db):
            raise HTTPException(409, "setup already done; sign in instead")
        if cfg.SETUP_CODE_REQUIRED:
            want = setup_code()
            if not want or not consteq((body.setup_code or "").strip().lower(), want.lower()):
                login_limiter.fail(ip, "#setup")
                raise HTTPException(403, "wrong setup code (see data/setup-code.txt on the server)")
        now = time.time()
        uid = db.execute("INSERT INTO users(username,display_name,pw_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                         (body.username, body.display_name.strip(), hash_password(body.password), "admin", now, now)).lastrowid
        csrf = start_session(db, request, response, uid)
    ensure_setup_code(db)
    u = User(uid, body.username, body.display_name.strip(), "admin", csrf)
    return SessionOut(setup_required=False, setup_code_required=False, user=u.public(), csrf=csrf, configured=configured(db))


@router.post("/auth/login", response_model=SessionOut, dependencies=[Depends(csrf_anonymous)],
             summary="Sign in (rate-limited); sets the http-only session cookie")
def login(body: LoginIn, request: Request, response: Response, db=Depends(DB.get_db)):
    ip = client_ip(request)
    wait = login_limiter.blocked(ip, body.username)
    if wait:
        raise HTTPException(429, "too many failed sign-ins, try again later", headers={"Retry-After": str(wait)})
    r = db.execute("SELECT id, username, display_name, role, pw_hash, disabled FROM users WHERE username=?",
                   (body.username,)).fetchone()
    ok, rehash = verify_password(r["pw_hash"] if r else None, body.password)
    if not ok or r["disabled"]:
        login_limiter.fail(ip, body.username)
        raise HTTPException(401, "invalid username or password")
    login_limiter.success(ip, body.username)
    with DB.tx(db):
        if rehash:
            db.execute("UPDATE users SET pw_hash=? WHERE id=?", (rehash, r["id"]))
        csrf = start_session(db, request, response, r["id"])
    u = User(r["id"], r["username"], r["display_name"], r["role"], csrf)
    return SessionOut(setup_required=False, setup_code_required=False, user=u.public(), csrf=csrf, configured=configured(db))


@router.post("/auth/logout", status_code=204, summary="Sign out (ends this session)")
def logout(request: Request, response: Response, db=Depends(DB.get_db), _csrf=Depends(csrf_anonymous)):
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(tok),))
    set_session_cookies(request, response, None, None)
    return None


@router.post("/auth/password", status_code=204, summary="Change your own password (signs out your other sessions)")
def change_password(body: PasswordIn, request: Request, response: Response,
                    user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    r = db.execute("SELECT pw_hash FROM users WHERE id=?", (user.id,)).fetchone()
    ok, _ = verify_password(r["pw_hash"], body.current_password)
    if not ok:
        login_limiter.fail(client_ip(request), user.username)
        raise HTTPException(403, "current password is wrong")
    check_password_policy(body.new_password, user.username)
    keep = token_hash(request.cookies.get(SESSION_COOKIE, ""))
    with DB.tx(db):
        db.execute("UPDATE users SET pw_hash=?, updated_at=? WHERE id=?", (hash_password(body.new_password), time.time(), user.id))
        db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user.id, keep))
    return None


@router.get("/auth/check", status_code=204, summary="nginx auth_request: 204 if signed in (viewer+), else 401")
def check(request: Request, response: Response, db=Depends(DB.get_db)):
    u = load_session(db, request)
    if not u:
        raise HTTPException(401, "sign in required")
    response.headers["X-CS-Role"] = u.role
    return None
