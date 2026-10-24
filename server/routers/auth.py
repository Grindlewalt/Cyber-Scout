"""Session, first-run setup, login/logout, password change, and the nginx auth_request check."""
import os, secrets, time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db as DB, textrules, workerconf, workerstatus
from ..config import cfg, CSRF_COOKIE, SESSION_COOKIE
from ..deps import (User, client_ip, csrf_anonymous, load_session, lockdown_state, passwordless_policy, rate_limit,
                    require_role, set_csrf_cookie, set_session_cookies, via_public, USERNAME_RE)
from ..security import (consteq, hash_password, login_limiter, new_token, normalize_connect_key, token_hash,
                        verify_password)

router = APIRouter(tags=["auth"])


class SessionOut(BaseModel):
    setup_required: bool = Field(description="no users yet: the UI must show the first-run wizard")
    setup_code_required: bool = Field(description="setup needs the one-time code from DATA/setup-code.txt")
    user: dict | None = Field(description="{id, username, display_name, role} or null when signed out")
    csrf: str = Field(description="send as X-CSRF-Token on POST/PUT/PATCH/DELETE (also in the cs_csrf cookie)")
    configured: bool = Field(description="TBA key set, at least one followed event, and the setup wizard finished")
    lockdown: bool = Field(False, description="read-only mode: only admins can change anything right now")
    network: str = Field("lan", description="'lan' (direct from the team network / Tailscale) or 'public' (tunnel)")
    passwordless_ok: bool = Field(True, description="accounts without a password can sign in from here")
    processing: dict | None = Field(None, description="signed in only: the worker's state {paused: bool|null (null = "
                                    "unknown), worker_seen: unix|null (its last pass), reason: 'paused'|'no_key'|"
                                    "'no_events'|null, why: sentence|null, worker_down: null while it checks in, "
                                    "else {never, since: unix|null, quiet_s, docker (admins, with the host updater): "
                                    "{service, state, error, exit_code, problem: {why, fix}, ...}|null}}")


class SetupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setup_code: str | None = Field(None, max_length=64)
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(max_length=256)
    display_name: str = Field("", max_length=64)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field("", max_length=256)     # empty for teammates without a password


class KeyLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=64, description="a connection key from an admin (XXXX-XXXX-XXXX-XXXX; "
                                                              "case, spaces and dashes don't matter, O=0, I=L=1)")


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=2, max_length=64)


PASSWORD_NEEDED = {
    "lan": "A password is required to sign in from outside the team network (shop Wi-Fi or Tailscale). "
           "Sign in from there, or ask an admin to give your account a password.",
    "never": "Every account needs a password to sign in. Ask an admin to give your account one.",
}


def passwordless_allowed(db, request: Request) -> bool:
    pol = passwordless_policy(db)
    return pol == "everywhere" or (pol == "lan" and not via_public(request))


def check_display_name(db, name: str, uid: int | None = None, allow_reserved: bool = False) -> str:
    """Validated display name (textrules.display_name) that can't be mistaken for another account's display name or
    username (look-alike-insensitive)."""
    try:
        name = textrules.display_name(name, allow_reserved=allow_reserved)
    except textrules.TextError as e:
        raise HTTPException(422, str(e))
    if not name:
        return name
    sk = textrules.skeleton(name)
    for r in db.execute("SELECT id, username, display_name FROM users WHERE id<>?", (uid or -1,)):
        if sk in (textrules.skeleton(r["display_name"]), textrules.skeleton(r["username"])):
            raise HTTPException(409, "that display name is too close to another teammate's name")
    return name


def password_strength(pw: str, username: str = "") -> int:
    """0 = none, 1 = meets the teammate rule only, 2 = meets the admin rule."""
    if not pw:
        return 0
    return 2 if len(pw) >= cfg.PASSWORD_MIN and pw.lower() != (username or "").lower() else 1


_RUNS = ("0123456789", "1234567890", "abcdefghijklmnopqrstuvwxyz", "qwertyuiop", "asdfghjkl", "zxcvbnm")


def too_simple(pw: str) -> bool:
    """An admin password that only meets the length rule by repetition or a keyboard / alphabet run
    ('aaaaaaaaaa', 'abababab12', '1234567890', 'qwertyuiop')."""
    p = pw.lower()
    if len(set(p)) <= 3:
        return True
    return any(p in r or p in r[::-1] for r in _RUNS)


def check_password_policy(pw: str, username: str = "", role: str = "admin") -> int:
    """Admins need PASSWORD_MIN+ characters (not the username). Everyone else: optional, MEMBER_PASSWORD_MIN+ if set.
    Returns the strength to store in users.pw_strength."""
    if role == "admin":
        if len(pw) < cfg.PASSWORD_MIN:
            raise HTTPException(422, f"admin passwords must be at least {cfg.PASSWORD_MIN} characters")
        if username and pw.lower() == username.lower():
            raise HTTPException(422, "password must not be the username")
        if too_simple(pw):
            raise HTTPException(422, "that admin password is too easy to guess (one repeated character or a "
                                     "keyboard run): try a few words, e.g. 'robots like 9 fuel'")
    elif pw and len(pw) < cfg.MEMBER_PASSWORD_MIN:
        raise HTTPException(422, f"password must be at least {cfg.MEMBER_PASSWORD_MIN} characters (or leave it blank)")
    return password_strength(pw, username)


def check_username(u: str):
    if not USERNAME_RE.match(u):
        raise HTTPException(422, "username: 2-32 letters, digits, '.', '_' or '-'")


def start_session(db, request: Request, resp: Response, user_id: int, net_locked: bool = False) -> str:
    tok, csrf, now = new_token(), secrets.token_urlsafe(24), time.time()
    db.execute("DELETE FROM sessions WHERE expires_at < ? OR last_seen < ?",
               (now, now - cfg.SESSION_IDLE_DAYS * 86400))
    db.execute("INSERT INTO sessions(token_hash,user_id,csrf,created_at,last_seen,expires_at,net_locked) "
               "VALUES(?,?,?,?,?,?,?)",
               (token_hash(tok), user_id, csrf, now, now, now + cfg.SESSION_MAX_DAYS * 86400, int(net_locked)))
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
    # values imported from an old .env don't count: an admin walks the wizard once (and sets up the video there)
    return (bool(DB.get_setting(db, "tba_key")) and db.execute("SELECT 1 FROM events LIMIT 1").fetchone() is not None
            and bool(DB.get_setting(db, "setup_complete", False)))


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
    return _session_out(db, request, u, csrf, n)


def _session_out(db, request, u: User | None, csrf: str, n: int | None = None) -> SessionOut:
    n = DB.user_count(db) if n is None else n
    return SessionOut(setup_required=n == 0, setup_code_required=n == 0 and cfg.SETUP_CODE_REQUIRED,
                      user=u.public() if u else None, csrf=csrf, configured=configured(db),
                      lockdown=bool(lockdown_state(db)), network="public" if via_public(request) else "lan",
                      passwordless_ok=passwordless_allowed(db, request),
                      processing=workerstatus.processing_state(admin=u.can("admin")) if u else None)


@router.post("/setup", response_model=SessionOut, dependencies=[Depends(csrf_anonymous)],
             summary="First run only: create the admin account and sign in")
def setup(body: SetupIn, request: Request, response: Response, db=Depends(DB.get_db)):
    ip = client_ip(request)
    if login_limiter.blocked(ip, "#setup"):
        raise HTTPException(429, "too many attempts, try again later")
    if DB.user_count(db):
        raise HTTPException(409, "setup already done; sign in instead")
    if cfg.SETUP_CODE_REQUIRED:                        # the code first: no point fixing the rest with a wrong one
        want = setup_code()
        if not want or not consteq((body.setup_code or "").strip().lower(), want.lower()):
            login_limiter.fail(ip, "#setup")
            raise HTTPException(403, "wrong setup code (see data/setup-code.txt on the server)")
    check_username(body.username)
    check_password_policy(body.password, body.username)
    dname = check_display_name(db, body.display_name, allow_reserved=True)
    with DB.tx(db):
        if DB.user_count(db):
            raise HTTPException(409, "setup already done; sign in instead")
        now = time.time()
        uid = db.execute("INSERT INTO users(username,display_name,pw_hash,role,created_at,updated_at,pw_strength) "
                         "VALUES(?,?,?,?,?,?,2)",
                         (body.username, dname, hash_password(body.password), "admin", now, now)).lastrowid
        csrf = start_session(db, request, response, uid)
        u = User(uid, body.username, dname, "admin", csrf)
        audit.record(db, request, u, "auth.setup", f"user:{uid}", after={"username": body.username, "role": "admin"})
    ensure_setup_code(db)
    return _session_out(db, request, u, csrf)


@router.post("/auth/login", response_model=SessionOut, dependencies=[Depends(csrf_anonymous)],
             summary="Sign in (rate-limited); sets the http-only session cookie")
def login(body: LoginIn, request: Request, response: Response, db=Depends(DB.get_db)):
    ip = client_ip(request)
    wait = login_limiter.blocked(ip, body.username)
    if wait:
        raise HTTPException(429, "too many failed sign-ins, try again later", headers={"Retry-After": str(wait)})
    if not body.password and not passwordless_allowed(db, request):
        # decided before looking the account up: the answer is the same for real and made-up usernames
        raise HTTPException(401, PASSWORD_NEEDED[passwordless_policy(db)])
    r = db.execute("SELECT id, username, display_name, role, pw_hash, pw_strength, disabled FROM users WHERE username=?",
                   (body.username,)).fetchone()
    ok, rehash = verify_password(r["pw_hash"] if r else None, body.password)
    if not ok or r["disabled"]:
        login_limiter.fail(ip, body.username)
        actor = {"id": r["id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"]} if r else {}
        audit.record(db, request, None, "auth.login_failed", f"user:{r['id']}" if r else None, actor=actor,
                     after={"reason": "disabled" if r and ok else "bad credentials", "known_user": bool(r)})
        raise HTTPException(401, "invalid username or password")
    login_limiter.success(ip, body.username)
    u = User(r["id"], r["username"], r["display_name"], r["role"], "")
    with DB.tx(db):
        if rehash:
            db.execute("UPDATE users SET pw_hash=? WHERE id=?", (rehash, r["id"]))
        # no password -> this session only works where passwordless sign-in is allowed (re-checked every request)
        u.csrf = start_session(db, request, response, r["id"], net_locked=not body.password)
        audit.record(db, request, u, "auth.login", f"user:{r['id']}", after={"password": bool(body.password)})
    return _session_out(db, request, u, u.csrf)


KEY_INVALID = "that connection key isn't valid (it works once and expires; ask your admin for a new one)"


@router.post("/auth/key-login", response_model=SessionOut, dependencies=[Depends(csrf_anonymous)],
             summary="Sign in with a one-time connection key from an admin (rate-limited); sets the session cookie")
def key_login(body: KeyLoginIn, request: Request, response: Response, db=Depends(DB.get_db)):
    ip = client_ip(request)
    wait = login_limiter.blocked(ip, "#key")
    if wait:
        raise HTTPException(429, "too many failed sign-ins, try again later", headers={"Retry-After": str(wait)})
    k, now = normalize_connect_key(body.key), time.time()
    kh = token_hash(k) if k else None
    with DB.tx(db):          # BEGIN IMMEDIATE: of two parallel sign-ins with one key, the second sees used_at set
        r = db.execute("SELECT k.user_id, k.expires_at, k.used_at, u.username, u.display_name, u.role, u.disabled "
                       "FROM connect_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash=?",
                       (kh,)).fetchone() if kh else None
        why = ("malformed" if not k else "unknown" if not r else "used" if r["used_at"] is not None
               else "expired" if r["expires_at"] <= now else "disabled" if r["disabled"] else None)
        if not why and db.execute("UPDATE connect_keys SET used_at=? WHERE key_hash=? AND used_at IS NULL",
                                  (now, kh)).rowcount != 1:
            why = "used"
        if not why:
            u = User(r["user_id"], r["username"], r["display_name"], r["role"], "")
            u.csrf = start_session(db, request, response, u.id)     # a full session: the key was the credential
            audit.record(db, request, u, "auth.key_login", f"user:{u.id}")
        db.execute("DELETE FROM connect_keys WHERE expires_at < ?", (now,))
    if why:
        login_limiter.fail(ip, "#key")
        actor = {"id": r["user_id"], "username": r["username"], "display_name": r["display_name"],
                 "role": r["role"]} if r else {}
        audit.record(db, request, None, "auth.key_login_failed", f"user:{r['user_id']}" if r else None, actor=actor,
                     after={"reason": why})
        raise HTTPException(401, KEY_INVALID)
    login_limiter.success(ip, "#key")
    return _session_out(db, request, u, u.csrf)


@router.post("/auth/logout", status_code=204, summary="Sign out (ends this session)")
def logout(request: Request, response: Response, db=Depends(DB.get_db), _csrf=Depends(csrf_anonymous)):
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        u = load_session(db, request)
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(tok),))
        if u:
            audit.record(db, request, u, "auth.logout", f"user:{u.id}")
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
    strength = check_password_policy(body.new_password, user.username, user.role)
    keep = token_hash(request.cookies.get(SESSION_COOKIE, ""))
    with DB.tx(db):
        db.execute("UPDATE users SET pw_hash=?, pw_strength=?, updated_at=? WHERE id=?",
                   (hash_password(body.new_password), strength, time.time(), user.id))
        db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user.id, keep))
        db.execute("UPDATE sessions SET net_locked=? WHERE token_hash=?", (int(not body.new_password), keep))
        audit.record(db, request, user, "auth.password", f"user:{user.id}", after={"has_password": bool(body.new_password)})
    return None


@router.patch("/auth/profile", response_model=SessionOut,
              summary="Change your own display name (scout+; no staff words, not another teammate's name)")
def change_profile(body: ProfileIn, request: Request, user: User = Depends(require_role("scout")),
                   db=Depends(DB.get_db)):
    name = check_display_name(db, body.display_name, uid=user.id, allow_reserved=False)
    rate_limit(user, "profile")
    with DB.tx(db):
        db.execute("UPDATE users SET display_name=?, updated_at=? WHERE id=?", (name, time.time(), user.id))
        audit.record(db, request, user, "users.profile", f"user:{user.id}", before={"display_name": user.display_name},
                     after={"display_name": name})
    user.display_name = name
    return _session_out(db, request, user, user.csrf)


@router.get("/auth/check", status_code=204, summary="nginx auth_request: 204 if signed in (viewer+), else 401")
def check(request: Request, response: Response, db=Depends(DB.get_db)):
    u = load_session(db, request)
    if not u:
        raise HTTPException(401, "sign in required")
    response.headers["X-CS-Role"] = u.role
    return None
