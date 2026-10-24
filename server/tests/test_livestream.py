"""GET /events/{ev}/livestream: the recorder service's status file (live/liveingest.py)."""
import json, time

from server.routers import sources as R


def _stream(data):
    evd = data / "events" / "2026wasam"
    evd.mkdir(parents=True, exist_ok=True)
    (evd / "sources.json").write_text(json.dumps({"sources": [
        {"id": "url-1", "kind": "livestream", "url": "https://www.youtube.com/@x/live", "enabled": True}]}))
    return evd


def _beat(data, age=0.0, **kw):
    (data / "recorder-status.json").write_text(json.dumps(dict(dict(at=time.time() - age, events=["2026wasam"],
                                                                    events_at=time.time() - age), **kw)))


def test_livestream_status(admin, data, monkeypatch):
    monkeypatch.setattr(R, "_API_STARTED", time.time() - 3600)
    r = admin.get("/events/2026wasam/livestream")
    assert r.status_code == 200 and r.json()["state"] == "no_source"
    evd = _stream(data)
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "not_running" and j["configured_url"].endswith("/live")
    assert j["message"] == "The recorder service isn't running: start the recorder container (docker compose up -d recorder)."
    (evd / "livestream.json").write_text(json.dumps({"state": "recording", "updated": time.time(), "video_id": "abc",
                                                     "last_match": {"key": "2026wasam_qm3"}}))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "recording" and j["stale"] is False and j["last_match"]["key"] == "2026wasam_qm3"
    (evd / "livestream.json").write_text(json.dumps({"state": "recording", "updated": time.time() - 600}))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["stale"] is True and "not reporting" in j["message"] and "docker compose up -d recorder" in j["message"]
    _beat(data)                                                   # the service runs, but doesn't write this stream
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["stale"] is True and "running but hasn't reported" in j["message"]
    assert admin.get("/events/BAD/livestream").status_code == 422


def test_livestream_status_starting(admin, data, monkeypatch):
    """A stream the service hasn't written a status for yet: starting while its heartbeat is fresh, never an outage."""
    monkeypatch.setattr(R, "_API_STARTED", time.time() - 3600)
    _stream(data)
    _beat(data, age=5)
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "starting" and j["stale"] is False
    assert j["message"] == "The recorder picks up this stream within a few seconds."
    _beat(data, age=5, error="RuntimeError('disk full')")       # the loop runs, its steps fail
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "error" and "disk full" in j["message"]
    _beat(data, age=600)                                          # heartbeat gone quiet
    assert admin.get("/events/2026wasam/livestream").json()["state"] == "not_running"
    _beat(data, age=1, stopped=True)                              # stopped cleanly
    assert admin.get("/events/2026wasam/livestream").json()["state"] == "not_running"
    # the service runs and has read the current config, which doesn't follow this competition
    (data / "config").mkdir(exist_ok=True)
    (data / "config" / "worker.json").write_text(json.dumps({"events": []}))
    (data / "recorder-status.json").write_text(json.dumps(dict(at=time.time(), events=["2026other"], events_at=time.time() + 1)))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "idle" and "isn't followed" in j["message"]


def test_livestream_status_fresh_api_and_slow_check(admin, data, monkeypatch):
    evd = _stream(data)
    monkeypatch.setattr(R, "_API_STARTED", time.time())          # the whole stack just started: no heartbeat yet
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "starting" and j["message"] == "The recorder service is starting."
    monkeypatch.setattr(R, "_API_STARTED", time.time() - 3600)
    # a stream-link check announced 120 s of work: 100 s without a write is not stale, 200 s is
    (evd / "livestream.json").write_text(json.dumps({"state": "starting", "updated": time.time() - 100, "expect_next_s": 120,
                                                     "message": "checking the stream link (YouTube @x live)"}))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["stale"] is False and j["message"] == "checking the stream link (YouTube @x live)"
    (evd / "livestream.json").write_text(json.dumps({"state": "starting", "updated": time.time() - 200, "expect_next_s": 120}))
    assert admin.get("/events/2026wasam/livestream").json()["stale"] is True
    (evd / "livestream.json").write_text(json.dumps({"state": "waiting", "updated": time.time() - 100, "expect_next_s": 99999}))
    assert admin.get("/events/2026wasam/livestream").json()["stale"] is False          # capped at 300 s of busy time
    (evd / "livestream.json").write_text(json.dumps({"state": "waiting", "updated": time.time() - 400, "expect_next_s": 99999}))
    assert admin.get("/events/2026wasam/livestream").json()["stale"] is True
    # the service's last word was "stopped" and it has no fresh heartbeat: not running (the Dashboard says so)
    (evd / "livestream.json").write_text(json.dumps({"state": "stopped", "updated": time.time() - 30}))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "not_running" and j["service_state"] == "stopped"
    _beat(data)                                                   # running again (it rewrites the file within 10 s)
    assert admin.get("/events/2026wasam/livestream").json()["state"] == "stopped"


def test_livestream_status_needs_login(client):
    assert client.get("/events/2026wasam/livestream").status_code in (401, 403)
