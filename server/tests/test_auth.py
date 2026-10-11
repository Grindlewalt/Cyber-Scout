import json, os, time

from server import db as DB
from server.config import cfg
from server.security import token_hash

from .conftest import C, GOOD_KEY


def test_setup_mode_and_code(client, data):
    s = client.get("/session").json()
    assert s["setup_required"] and s["setup_code_required"] and s["user"] is None and s["csrf"]
    code_file = data / "setup-code.txt"
    assert code_file.exists() and oct(os.stat(code_file).st_mode & 0o777) == "0o600"
    # wrong code
    r = client.post("/setup", {"setup_code": "nope", "username": "coach", "password": "correct horse 1"})
    assert r.status_code == 403
    # short password
    r = client.post("/setup", {"setup_code": code_file.read_text().strip(), "username": "coach", "password": "short"})
    assert r.status_code == 422
    r = client.post("/setup", {"setup_code": code_file.read_text().strip(), "username": "coach", "password": "correct horse 1"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    assert not code_file.exists()
    # second setup refused
    r = client.post("/setup", {"setup_code": "x", "username": "evil", "password": "correct horse 2"})
    assert r.status_code == 409
    s = client.get("/session").json()
    assert not s["setup_required"] and s["user"]["username"] == "coach"


def test_session_cookie_flags(client, data):
    client.get("/session")
    code = (data / "setup-code.txt").read_text().strip()
    r = client.post("/setup", {"setup_code": code, "username": "coach", "password": "correct horse 1"})
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("cs_session=")][0].lower()
    assert "httponly" in sc and "samesite=lax" in sc and "secure" not in sc and "path=/" in sc
    # behind a TLS proxy the cookie is Secure
    r = client.tc.post("/app/v1/auth/login", json={"username": "coach", "password": "correct horse 1"},
                       headers={"X-CSRF-Token": client.tc.cookies.get("cs_csrf"), "X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("cs_session=")][0].lower()
    assert "secure" in sc


def test_raw_token_not_stored(admin, data):
    tok = admin.tc.cookies.get("cs_session")
    c = DB.connect()
    rows = [r["token_hash"] for r in c.execute("SELECT token_hash FROM sessions")]
    assert tok not in rows and token_hash(tok) in rows
    pw = c.execute("SELECT pw_hash FROM users").fetchone()[0]
    assert pw.startswith("$argon2id$")


def test_csrf_required(admin):
    r = admin.patch("/settings", {"season_goal": "DCMP"}, csrf=False)
    assert r.status_code == 403
    r = admin.tc.patch("/app/v1/settings", json={"season_goal": "DCMP"}, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403
    assert admin.patch("/settings", {"season_goal": "DCMP"}).status_code == 200
    # login without the token is refused too (login CSRF)
    c = C(admin.tc.__class__(admin.tc.app, base_url="http://testserver"))
    assert c.post("/auth/login", {"username": "coach", "password": "correct horse 1"}).status_code == 403


def test_login_logout_and_bad_password(admin, app):
    c = C(admin.tc.__class__(app, base_url="http://testserver"))
    assert c.login("coach", "wrong password!").status_code == 401
    assert c.login("nobody", "wrong password!").status_code == 401     # same answer for unknown users
    assert c.login("COACH", "correct horse 1").status_code == 200     # usernames are case-insensitive
    assert c.get("/auth/check").status_code == 204
    assert c.post("/auth/logout").status_code == 204
    assert c.get("/auth/check").status_code == 401
    assert c.get("/session").json()["user"] is None


def test_login_rate_limit(admin, app):
    c = C(admin.tc.__class__(app, base_url="http://testserver"))
    c.get("/session")
    codes = [c.post("/auth/login", {"username": "coach", "password": "bad password %d" % i}).status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    # even the right password is refused while blocked
    assert c.post("/auth/login", {"username": "coach", "password": "correct horse 1"}).status_code == 429


def test_session_expiry(admin):
    assert admin.get("/auth/check").status_code == 204
    c = DB.connect()
    c.execute("UPDATE sessions SET last_seen=?", (time.time() - (cfg.SESSION_IDLE_DAYS + 1) * 86400,))
    assert admin.get("/auth/check").status_code == 401
    assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_absolute_expiry(admin):
    c = DB.connect()
    c.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
    assert admin.get("/auth/check").status_code == 401


def test_password_change(admin, app):
    other = C(admin.tc.__class__(app, base_url="http://testserver"))
    assert other.login("coach", "correct horse 1").status_code == 200
    assert admin.post("/auth/password", {"current_password": "nope", "new_password": "another good one"}).status_code == 403
    assert admin.post("/auth/password", {"current_password": "correct horse 1", "new_password": "short"}).status_code == 422
    assert admin.post("/auth/password", {"current_password": "correct horse 1", "new_password": "another good one"}).status_code == 204
    assert admin.get("/auth/check").status_code == 204          # this session kept
    assert other.get("/auth/check").status_code == 401          # other sessions signed out
    c = C(admin.tc.__class__(app, base_url="http://testserver"))
    assert c.login("coach", "another good one").status_code == 200


def test_disable_kills_sessions(team):
    admin, scout = team["admin"], team["scout"]
    uid = [u for u in admin.get("/users").json() if u["username"] == "scout1"][0]["id"]
    assert scout.get("/auth/check").status_code == 204
    assert admin.patch(f"/users/{uid}", {"disabled": True}).status_code == 200
    assert scout.get("/auth/check").status_code == 401
    assert scout.login("scout1", "password-scout").status_code == 401


def test_last_admin_protected(admin):
    me = admin.get("/session").json()["user"]["id"]
    assert admin.patch(f"/users/{me}", {"role": "viewer"}).status_code == 409
    assert admin.patch(f"/users/{me}", {"disabled": True}).status_code == 409
    assert admin.delete(f"/users/{me}").status_code == 409


def test_tba_key_never_returned(admin, data):
    assert admin.put("/settings/tba-key", {"key": "x" * 40}).status_code == 422          # rejected by (fake) TBA
    r = admin.put("/settings/tba-key", {"key": GOOD_KEY})
    assert r.status_code == 200 and r.json()["tba_key_set"] and r.json()["tba_key_hint"] == "••••" + GOOD_KEY[-4:]
    for p in ("/settings", "/session", "/events", "/users"):
        assert GOOD_KEY not in admin.get(p).text
    # worker config gets it, private file
    wc = data / "config" / "worker.json"
    assert json.loads(wc.read_text())["tba_key"] == GOOD_KEY
    assert oct(os.stat(wc).st_mode & 0o777) == "0o600"


def test_setup_without_code_when_disabled(data, monkeypatch):
    monkeypatch.setenv("SETUP_CODE_REQUIRED", "0")
    cfg.configure(data=str(data))
    from fastapi.testclient import TestClient
    from server.main import create_app
    with TestClient(create_app(), base_url="http://testserver") as tc:
        c = C(tc)
        s = c.get("/session").json()
        assert s["setup_required"] and not s["setup_code_required"]
        assert not (data / "setup-code.txt").exists()
        assert c.post("/setup", {"username": "coach", "password": "correct horse 1"}).status_code == 200


def test_seed_from_env(data, monkeypatch):
    monkeypatch.setenv("TBA_KEY", GOOD_KEY)
    monkeypatch.setenv("EVENTS", "2026wasam, 2026cmptx")
    cfg.configure(data=str(data))
    from fastapi.testclient import TestClient
    from server.main import create_app
    with TestClient(create_app(), base_url="http://testserver"):
        pass
    wc = json.loads((data / "config" / "worker.json").read_text())
    assert wc["tba_key"] == GOOD_KEY and sorted(wc["events"]) == ["2026cmptx", "2026wasam"]


def test_optional_passwords_for_non_admins(admin, app):
    from .conftest import new_client
    # no password: sign in with just the username
    r = admin.post("/users", {"username": "kid", "role": "scout"})
    assert r.status_code == 201 and r.json()["has_password"] is False
    c = new_client(app)
    assert c.login("kid", "").status_code == 200
    assert new_client(app).login("kid", "guess").status_code == 401
    # short passwords are fine for teammates, too-short (<4) is not
    assert admin.post("/users", {"username": "kid2", "password": "abc", "role": "viewer"}).status_code == 422
    r = admin.post("/users", {"username": "kid3", "password": "abcd", "role": "strategist"})
    assert r.status_code == 201 and r.json()["has_password"] is True
    # admins still need the long password
    assert admin.post("/users", {"username": "boss", "role": "admin"}).status_code == 422
    assert admin.post("/users", {"username": "boss", "password": "abcd", "role": "admin"}).status_code == 422
    # promoting a no/short-password teammate needs a real password first (or in the same request)
    kid3 = r.json()["id"]
    assert admin.patch(f"/users/{kid3}", {"role": "admin"}).status_code == 409
    assert admin.patch(f"/users/{kid3}", {"role": "admin", "password": "abcd"}).status_code == 422
    assert admin.patch(f"/users/{kid3}", {"role": "admin", "password": "long enough now"}).status_code == 200
    # a teammate can add (and remove) their own password
    assert c.post("/auth/password", {"current_password": "", "new_password": "mine"}).status_code == 204
    assert new_client(app).login("kid", "mine").status_code == 200
    assert new_client(app).login("kid", "").status_code == 401
    # the admin can clear a teammate's password
    kid = [u for u in admin.get("/users").json() if u["username"] == "kid"][0]["id"]
    assert admin.patch(f"/users/{kid}", {"password": ""}).status_code == 200
    assert new_client(app).login("kid", "").status_code == 200


def test_imported_config_still_needs_the_wizard(admin, app):
    # a key + followed event (e.g. imported from .env) isn't enough: the wizard must be finished once
    assert admin.put("/settings/tba-key", {"key": "k" * 60}).status_code == 200
    assert admin.post("/events", {"key": "2026wasam"}).status_code == 201
    s = admin.get("/session").json()
    assert s["configured"] is False and admin.get("/settings").json()["setup_complete"] is False
    assert admin.patch("/settings", {"setup_complete": True}).status_code == 200
    assert admin.get("/session").json()["configured"] is True
