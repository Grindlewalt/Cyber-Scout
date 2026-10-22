"""One-time connection keys: an admin makes one for a teammate (POST /users/{uid}/connect-key), it signs that account
in once (POST /auth/key-login), then it's spent; one per account, expires, revocable, never stored or logged."""
import json, re, threading, time

from server import db as DB
from server.security import normalize_connect_key, token_hash

from .conftest import new_client
from .test_hardening import audit, public_client, uid_of

KEY_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")
BAD = "that connection key isn't valid (it works once and expires; ask your admin for a new one)"


def make_key(admin, uid, **body):
    r = admin.post(f"/users/{uid}/connect-key", body or None)
    assert r.status_code == 201, r.text
    return r.json()


def key_login(app, key, client=None):
    c = client or new_client(app)
    c.get("/session")
    return c, c.post("/auth/key-login", {"key": key})


def log(admin, action):
    """Audit entries with exactly this action, newest first (the API's filter is a prefix match)."""
    return [e for e in audit(admin, action=action, limit=500) if e["action"] == action]


def user_row(admin, uid):
    return [u for u in admin.get("/users").json() if u["id"] == uid][0]


def test_key_signs_in_once(team, app):
    admin = team["admin"]
    uid = uid_of(admin, "scout1")
    assert user_row(admin, uid)["connect_key_expires"] is None
    t0 = time.time()
    k = make_key(admin, uid)
    assert KEY_RE.match(k["key"]) and k["user_id"] == uid and k["username"] == "scout1"
    assert set(k) == {"key", "user_id", "username", "display_name", "expires_at"}
    assert abs(k["expires_at"] - (t0 + 168 * 3600)) < 60                       # default: 7 days
    assert user_row(admin, uid)["connect_key_expires"] == k["expires_at"]
    c, r = key_login(app, k["key"])
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["user"]["username"] == "scout1" and s["user"]["role"] == "scout" and s["csrf"]
    assert c.tc.cookies.get("cs_session") and c.get("/auth/check").status_code == 204
    assert c.post("/events/2026wasam/notes", {"team": "frc4911", "text": "signed in by key"}).status_code == 201
    row = DB.connect().execute("SELECT net_locked FROM sessions WHERE token_hash=?",
                               (token_hash(c.tc.cookies.get("cs_session")),)).fetchone()
    assert row["net_locked"] == 0                                               # a full session
    # spent: a second use (another device, or the same one) gets the one generic answer
    _, r2 = key_login(app, k["key"])
    assert r2.status_code == 401 and r2.json()["detail"] == BAD
    assert user_row(admin, uid)["connect_key_expires"] is None


def test_expired_key_is_refused_and_purged(team, app):
    admin = team["admin"]
    uid = uid_of(admin, "viewer1")
    k = make_key(admin, uid, hours=1)
    c = DB.connect()
    c.execute("UPDATE connect_keys SET expires_at=? WHERE user_id=?", (time.time() - 1, uid))
    assert user_row(admin, uid)["connect_key_expires"] is None
    _, r = key_login(app, k["key"])
    assert r.status_code == 401 and r.json()["detail"] == BAD
    assert log(admin, "auth.key_login_failed")[0]["after"] == {"reason": "expired"}
    assert c.execute("SELECT COUNT(*) FROM connect_keys").fetchone()[0] == 0     # purged on the way


def test_new_key_replaces_the_old_one(team, app):
    admin = team["admin"]
    uid = uid_of(admin, "scout1")
    k1, k2 = make_key(admin, uid), make_key(admin, uid, hours=2)
    assert k1["key"] != k2["key"]
    assert DB.connect().execute("SELECT COUNT(*) FROM connect_keys WHERE user_id=?", (uid,)).fetchone()[0] == 1
    assert user_row(admin, uid)["connect_key_expires"] == k2["expires_at"]
    e = log(admin, "auth.key_created")[0]
    assert e["before"] == {"expires_at": k1["expires_at"]} and e["after"] == {"expires_at": k2["expires_at"]}
    assert key_login(app, k1["key"])[1].status_code == 401
    assert key_login(app, k2["key"])[1].status_code == 200


def test_revoke(team, app):
    admin = team["admin"]
    uid = uid_of(admin, "scout1")
    k = make_key(admin, uid)
    assert admin.delete(f"/users/{uid}/connect-key").status_code == 204
    assert user_row(admin, uid)["connect_key_expires"] is None
    assert key_login(app, k["key"])[1].status_code == 401
    assert admin.delete(f"/users/{uid}/connect-key").status_code == 204          # none left: still 204 ...
    revoked = log(admin, "auth.key_revoked")
    assert len(revoked) == 1 and revoked[0]["target"] == f"user:{uid}"          # ... and only one entry
    assert not audit(admin, action="http.delete")                              # (no fallback entry for the no-op)
    assert admin.delete("/users/9999/connect-key").status_code == 404
    assert admin.post("/users/9999/connect-key").status_code == 404


def test_disabled_accounts(team, app):
    admin = team["admin"]
    uid = uid_of(admin, "scout1")
    k = make_key(admin, uid)
    assert admin.patch(f"/users/{uid}", {"disabled": True}).status_code == 200    # disabling drops the key
    assert log(admin, "users.update")[0]["after"]["connect_key_revoked"] is True
    assert key_login(app, k["key"])[1].status_code == 401
    r = admin.post(f"/users/{uid}/connect-key")
    assert r.status_code == 409 and "enable the account first" in r.json()["detail"]
    # re-enabled the old key stays dead; a key left on an account disabled behind the API's back doesn't work either
    assert admin.patch(f"/users/{uid}", {"disabled": False}).status_code == 200
    assert key_login(app, k["key"])[1].status_code == 401
    k2 = make_key(admin, uid)
    DB.connect().execute("UPDATE users SET disabled=1 WHERE id=?", (uid,))
    _, r = key_login(app, k2["key"])
    assert r.status_code == 401 and r.json()["detail"] == BAD
    assert log(admin, "auth.key_login_failed")[0]["after"] == {"reason": "disabled"}


def test_deleting_the_account_deletes_its_key(team, app):
    admin = team["admin"]
    uid = uid_of(admin, "viewer1")
    k = make_key(admin, uid)
    assert admin.delete(f"/users/{uid}").status_code == 204
    assert DB.connect().execute("SELECT COUNT(*) FROM connect_keys").fetchone()[0] == 0
    assert key_login(app, k["key"])[1].status_code == 401


def test_only_admins_make_or_revoke_keys(team, app):
    uid = uid_of(team["admin"], "viewer1")
    for role in ("viewer", "scout", "strategist"):
        assert team[role].post(f"/users/{uid}/connect-key").status_code == 403, role
        assert team[role].delete(f"/users/{uid}/connect-key").status_code == 403, role
    anon = new_client(app)
    anon.get("/session")
    assert anon.post(f"/users/{uid}/connect-key").status_code == 401
    assert DB.connect().execute("SELECT COUNT(*) FROM connect_keys").fetchone()[0] == 0


def test_hours_validation(team):
    admin = team["admin"]
    uid = uid_of(admin, "viewer1")
    for body in ({"hours": 0}, {"hours": 721}, {"hours": "soon"}, {"hours": 1.5}, {"hours": 5, "user": 1}):
        assert admin.post(f"/users/{uid}/connect-key", body).status_code == 422, body
    t0 = time.time()
    assert abs(make_key(admin, uid, hours=720)["expires_at"] - (t0 + 720 * 3600)) < 60
    assert abs(make_key(admin, uid, hours=1)["expires_at"] - (t0 + 3600)) < 60
    assert abs(admin.post(f"/users/{uid}/connect-key", {}).json()["expires_at"] - (t0 + 168 * 3600)) < 60


def test_normalization(team, app, monkeypatch):
    from server.routers import users
    assert normalize_connect_key("7oql m9xa-p4hd w8ie") == "70Q1M9XAP4HDW81E"
    assert normalize_connect_key(" 70Q1-M9XA-P4HD-W81E\n") == "70Q1M9XAP4HDW81E"
    for bad in ("", "70Q1-M9XA-P4HD-W81", "70Q1-M9XA-P4HD-W81EE", "U0Q1-M9XA-P4HD-W81E", "70Q1_M9XA_P4HD_W81E",
                "70Q1-M9XA-P4HD-W8!E", "７0Q1-M9XA-P4HD-W81E"):
        assert normalize_connect_key(bad) is None, bad
    monkeypatch.setattr(users, "new_connect_key", lambda: "70Q1-M9XA-P4HD-W81E")
    admin = team["admin"]
    uid = uid_of(admin, "scout1")
    assert make_key(admin, uid)["key"] == "70Q1-M9XA-P4HD-W81E"
    assert key_login(app, "7oql m9xa-p4hd w8ie")[1].status_code == 200          # lowercase, O/0, L/1, I/1, spaces
    make_key(admin, uid)
    assert key_login(app, "70q1m9xap4hdw81e")[1].status_code == 200             # no dashes
    # not 16 characters of the alphabet: the same 401 (and it counts as a failed sign-in)
    for bad in ("70Q1-M9XA-P4HD-W81", "U0Q1-M9XA-P4HD-W81E"):
        _, r = key_login(app, bad)
        assert r.status_code == 401 and r.json()["detail"] == BAD
    assert log(admin, "auth.key_login_failed")[0]["after"] == {"reason": "malformed"}
    _, r = key_login(app, "x" * 65)
    assert r.status_code == 422


def test_rate_limit(team, app):
    admin = team["admin"]
    k = make_key(admin, uid_of(admin, "scout1"))
    c = new_client(app)
    c.get("/session")
    codes = [c.post("/auth/key-login", {"key": "0000-0000-0000-%04d" % i}).status_code for i in range(10)]
    assert codes == [401] * 10
    r = c.post("/auth/key-login", {"key": k["key"]})                            # checked before the lookup
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert key_login(app, k["key"], new_client(app, client=("10.0.0.99", 50000)))[1].status_code == 200
    # key failures also count against the per-IP bucket (30 / 15 min) that password sign-ins share
    other = new_client(app, client=("10.0.0.77", 50000))
    other.get("/session")
    assert [other.post("/auth/key-login", {"key": "1111-1111-1111-%04d" % i}).status_code
            for i in range(10)] == [401] * 10
    assert [other.post("/auth/login", {"username": f"u{i}", "password": "wrong pw"}).status_code
            for i in range(20)] == [401] * 20
    assert other.post("/auth/login", {"username": "scout1", "password": "password-scout"}).status_code == 429


def test_key_session_is_a_full_session(team, app):
    """No password, sign-in with no password switched off, from the internet, during a lockdown: the key is the
    credential, so the session works like a password sign-in (like /auth/login, lockdown doesn't stop sign-ins)."""
    admin = team["admin"]
    r = admin.post("/users", {"username": "rookie", "display_name": "Rookie Ray", "role": "scout"})
    assert r.status_code == 201
    assert admin.patch("/admin/security", {"passwordless_login": "never", "lockdown": True}).status_code == 200
    k = make_key(admin, uid_of(admin, "rookie"))
    pc = public_client(app)
    c, r = key_login(app, k["key"], pc)
    assert r.status_code == 200 and r.json()["user"]["username"] == "rookie" and r.json()["network"] == "public"
    assert c.get("/auth/check").status_code == 204                             # through the tunnel too
    e = log(admin, "auth.key_login")[0]
    assert e["user"]["username"] == "rookie" and e["via"] == "public"


def test_parallel_uses_only_one_wins(team, app):
    admin = team["admin"]
    k = make_key(admin, uid_of(admin, "scout1"))
    clients = [new_client(app, client=(f"10.0.1.{i}", 50000)) for i in range(6)]
    for c in clients:
        c.get("/session")
    go, codes = threading.Barrier(len(clients)), []

    def use(c):
        go.wait()
        codes.append(c.post("/auth/key-login", {"key": k["key"]}).status_code)
    ts = [threading.Thread(target=use, args=(c,)) for c in clients]
    for t in ts: t.start()
    for t in ts: t.join()
    assert sorted(codes) == [200] + [401] * 5
    assert len(log(admin, "auth.key_login")) == 1
    assert {e["after"]["reason"] for e in log(admin, "auth.key_login_failed")} == {"used"}


def test_key_login_needs_csrf(team, app):
    k = make_key(team["admin"], uid_of(team["admin"], "scout1"))
    c = new_client(app)
    c.get("/session")
    assert c.post("/auth/key-login", {"key": k["key"]}, csrf=False).status_code == 403
    assert c.post("/auth/key-login", {"key": k["key"], "username": "coach"}).status_code == 422
    assert key_login(app, k["key"], c)[1].status_code == 200                       # not spent by the refusals


def test_audit_and_storage_never_hold_the_key(team, app):
    admin = team["admin"]
    uid = uid_of(admin, "scout1")
    # the web app / Client send an Idempotency-Key on writes: a new key is never stored for replay
    h = {"X-CSRF-Token": admin.tc.cookies.get("cs_csrf"), "Idempotency-Key": "same-key-1"}
    r1 = admin.tc.post(f"/app/v1/users/{uid}/connect-key", json={"hours": 5}, headers=h)
    r2 = admin.tc.post(f"/app/v1/users/{uid}/connect-key", json={"hours": 5}, headers=h)
    assert r1.status_code == r2.status_code == 201 and "Idempotent-Replayed" not in r2.headers
    k1, k = r1.json()["key"], r2.json()["key"]
    assert k1 != k
    c, r = key_login(app, k)
    assert r.status_code == 200
    key_login(app, k)                                                            # reuse: logged as such
    key_login(app, "ZZZZ-ZZZZ-ZZZZ-ZZZZ")                                         # unknown
    make_key(admin, uid)
    assert admin.delete(f"/users/{uid}/connect-key").status_code == 204
    db = DB.connect()
    secrets_ = [k1, k, normalize_connect_key(k1), normalize_connect_key(k)]
    for table in ("audit_log", "connect_keys", "idempotency", "sessions", "settings"):
        blob = json.dumps([list(r) for r in db.execute(f"SELECT * FROM {table}")], default=str)
        assert not any(s in blob for s in secrets_), table
    entries = audit(admin, action="auth.key", limit=50)
    acts = [e["action"] for e in entries]
    assert acts.count("auth.key_created") == 3 and acts.count("auth.key_revoked") == 1
    assert acts.count("auth.key_login") == 1 and acts.count("auth.key_login_failed") == 2
    by = {e["action"]: e for e in reversed(entries)}                             # newest of each
    assert by["auth.key_created"]["target"] == f"user:{uid}" and set(by["auth.key_created"]["after"]) == {"expires_at"}
    assert by["auth.key_login"]["target"] == f"user:{uid}" and by["auth.key_login"]["user"]["username"] == "scout1"
    fails = [e for e in entries if e["action"] == "auth.key_login_failed"]
    assert {e["after"]["reason"] for e in fails} == {"used", "unknown"}
    assert {e["target"] for e in fails} == {f"user:{uid}", None}
    # Activity "changes only": making / revoking a key is a change, signing in with one isn't
    ch = {e["action"] for e in admin.get("/audit?changes=true&limit=500").json()["entries"]}
    assert {"auth.key_created", "auth.key_revoked"} <= ch and not ch & {"auth.key_login", "auth.key_login_failed"}
