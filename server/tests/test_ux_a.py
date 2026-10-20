"""UX fix wave A: processing state in the session, first-run setup check order + weak admin passwords, TBA
not-found messages, the worker's status file."""
import json, os, sys, time

from server import workerstatus

from .conftest import GOOD_KEY


def _status(data, **st):
    (data / "worker-status.json").write_text(json.dumps(st))


def test_session_processing_state(client, admin, data):
    p = admin.get("/session").json()["processing"]
    assert p == {"paused": None, "worker_seen": None, "reason": None, "why": None}      # no worker status yet: unknown
    now = time.time()
    _status(data, at=now - 20, paused=True, reason="paused")
    p = admin.get("/session").json()["processing"]
    assert p["paused"] is True and p["reason"] == "paused" and abs(p["worker_seen"] - (now - 20)) < 1
    assert "paused" in p["why"].lower() and "PAUSE_PROCESSING" in p["why"]
    _status(data, at=now - 5, paused=False, reason=None)
    p = admin.get("/session").json()["processing"]
    assert p["paused"] is False and p["why"] is None
    # a stale "paused" isn't trusted (the worker may have been restarted without it), but the last-seen time stays
    _status(data, at=now - 2 * workerstatus.FRESH_S, paused=True, reason="paused")
    p = admin.get("/session").json()["processing"]
    assert p["paused"] is None and p["worker_seen"] is not None and p["why"] is None
    # garbage file: unknown, not an error
    (data / "worker-status.json").write_text("{nope")
    assert admin.get("/session").json()["processing"]["paused"] is None


def test_session_processing_hidden_when_signed_out(client, admin):
    admin.post("/auth/logout")
    assert admin.get("/session").json()["processing"] is None


def test_setup_code_checked_first(client, data):
    client.get("/session")
    # a wrong code is reported even when the username and password are also wrong (it used to come last)
    r = client.post("/setup", {"setup_code": "nope", "username": "-bad name", "password": "x"})
    assert r.status_code == 403 and "setup code" in r.json()["detail"]


def test_setup_rejects_trivial_admin_passwords(client, data):
    client.get("/session")
    code = (data / "setup-code.txt").read_text().strip()
    for pw in ("aaaaaaaaaa", "abababababab", "1234567890", "qwertyuiop", "0987654321"):
        r = client.post("/setup", {"setup_code": code, "username": "coach", "password": pw})
        assert r.status_code == 422 and "easy to guess" in r.json()["detail"], pw
    r = client.post("/setup", {"setup_code": code, "username": "coach", "password": "robots like 9 fuel"})
    assert r.status_code == 200, r.text


def test_tba_not_found_messages(admin):
    assert admin.put("/settings/tba-key", {"key": GOOD_KEY}).status_code == 200
    r = admin.patch("/settings", {"team_number": 99998})
    assert r.status_code == 404 and r.json()["detail"] == "Team 99998 isn't on The Blue Alliance: check the number"
    r = admin.post("/events", {"key": "2026zzzz"})
    assert r.status_code == 404 and "No event 2026zzzz on The Blue Alliance" in r.json()["detail"]


def test_worker_status_beat(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "live"))
    import worker
    monkeypatch.setattr(worker, "DATA", str(tmp_path))
    worker.status_beat("paused")
    st = json.loads((tmp_path / "worker-status.json").read_text())
    assert st["paused"] is True and st["reason"] == "paused" and time.time() - st["at"] < 5
    worker.status_beat(None)
    st = json.loads((tmp_path / "worker-status.json").read_text())
    assert st["paused"] is False and st["reason"] is None


def test_audit_changes_filter_and_dry_run(team):
    admin, strat = team["admin"], team["strategist"]
    assert admin.put("/settings/tba-key", {"key": GOOD_KEY}).status_code == 200
    assert admin.post("/events", {"key": "2026wasam"}).status_code == 201
    r = strat.put("/events/2026wasam/picklists/main", {"data": {"stars": ["frc4911"]}})
    assert r.status_code == 200, r.text
    acts = [e["action"] for e in admin.get("/audit?changes=true").json()["entries"]]
    assert "picklist.save" in acts and not any(a in ("auth.login", "auth.login_failed", "auth.logout") or a.startswith("http.")
                                               for a in acts)
    assert "auth.login" in [e["action"] for e in admin.get("/audit").json()["entries"]]
    uid = [u for u in admin.get("/users").json() if u["username"] == "strategist1"][0]["id"]
    d = admin.post("/audit/revert-user", {"user_id": uid, "since": 0, "dry_run": True}).json()
    assert d["dry_run"] is True and len(d["reverted"]) >= 1
    assert admin.get("/events/2026wasam/picklists/main").json()["data"]["stars"] == ["frc4911"]   # a dry run undoes nothing
    d = admin.post("/audit/revert-user", {"user_id": uid, "since": 0}).json()
    assert d["dry_run"] is False and len(d["reverted"]) >= 1
