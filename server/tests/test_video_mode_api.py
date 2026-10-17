"""GET/PUT /events/{ev}/video-mode: match video mode (uploads + livestream | livestream only) + readiness checklist."""
import json, time

EV = "2026wasam"


def test_video_mode_set_get_and_audit(admin, data):
    j = admin.get(f"/events/{EV}/video-mode").json()
    assert j["video_mode"] == "auto" and j["modes"] == ["auto", "livestream"]
    assert [c["key"] for c in j["checks"]][:1] == ["source"] and j["checks"][0]["state"] == "bad"
    assert admin.put(f"/events/{EV}/video-mode", {"video_mode": "uploads"}).status_code == 422
    assert admin.put(f"/events/{EV}/video-mode", {"video_mode": "livestream"}, csrf=False).status_code == 403
    r = admin.put(f"/events/{EV}/video-mode", {"video_mode": "livestream"})
    assert r.status_code == 200 and r.json()["video_mode"] == "livestream" and r.json()["by"] == "Coach"
    doc = json.loads((data / "events" / EV / "sources.json").read_text())
    assert doc["video_mode"] == "livestream"
    assert admin.get(f"/events/{EV}/sources").json()["video_mode"] == "livestream"
    e = [x for x in admin.get(f"/audit?event={EV}").json()["entries"] if x["action"] == "sources.video_mode"]
    assert e and e[0]["target"] == EV


def test_readiness_checklist(admin, data):
    evd = data / "events" / EV
    admin.put(f"/events/{EV}/video-mode", {"video_mode": "livestream"})
    s = admin.post(f"/events/{EV}/sources", {"url": "https://www.youtube.com/@gg/live", "role": "dynamic"}).json()
    C = {c["key"]: c for c in admin.get(f"/events/{EV}/video-mode").json()["checks"]}
    assert C["source"]["state"] == "warn" and C["recorder"]["state"] == "bad"
    assert C["layout"]["state"] == "bad" and C["calibration"]["state"] == "bad" and C["layout"]["tab"] == "layout"
    assert admin.patch(f"/events/{EV}/sources/{s['id']}", {"role": "wide", "primary": True}).status_code == 200
    (evd / "livestream.json").write_text(json.dumps({"state": "recording", "updated": time.time(), "behind_live_s": 12}))
    (evd / "layout.json").write_text(json.dumps({"sources": {s["id"]: {"W": 1920, "H": 1080, "regions": [
        {"kind": "field", "box": [0, 100, 1920, 1080]}]}}}))
    (evd / "camera.json").write_text(json.dumps({"source": s["id"], "W": 1920, "H": 1080}))
    j = admin.get(f"/events/{EV}/video-mode").json()
    C = {c["key"]: c for c in j["checks"]}
    assert j["stream_source"] == s["id"]
    assert all(C[k]["state"] == "ok" for k in ("source", "recorder", "layout", "calibration")), C
    assert C["clock"]["state"] == "warn" and C["tba"]["state"] == "warn"


def test_video_mode_roles(team):
    assert team["viewer"].get(f"/events/{EV}/video-mode").status_code == 200
    assert team["strategist"].put(f"/events/{EV}/video-mode", {"video_mode": "livestream"}).status_code == 403
