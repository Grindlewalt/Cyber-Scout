"""GET /events/{ev}/livestream: the recorder service's status file (live/liveingest.py)."""
import json, time


def test_livestream_status(admin, data):
    r = admin.get("/events/2026wasam/livestream")
    assert r.status_code == 200 and r.json()["state"] == "no_source"
    evd = data / "events" / "2026wasam"
    evd.mkdir(parents=True, exist_ok=True)
    (evd / "sources.json").write_text(json.dumps({"sources": [
        {"id": "url-1", "kind": "livestream", "url": "https://www.youtube.com/@x/live", "enabled": True}]}))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "not_running" and j["configured_url"].endswith("/live")
    (evd / "livestream.json").write_text(json.dumps({"state": "recording", "updated": time.time(), "video_id": "abc",
                                                     "last_match": {"key": "2026wasam_qm3"}}))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["state"] == "recording" and j["stale"] is False and j["last_match"]["key"] == "2026wasam_qm3"
    (evd / "livestream.json").write_text(json.dumps({"state": "recording", "updated": time.time() - 600}))
    j = admin.get("/events/2026wasam/livestream").json()
    assert j["stale"] is True and "not reporting" in j["message"]
    assert admin.get("/events/BAD/livestream").status_code == 422


def test_livestream_status_needs_login(client):
    assert client.get("/events/2026wasam/livestream").status_code in (401, 403)
