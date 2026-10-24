"""Saving a calibration: a fit whose check says "bad" (tags that don't line up, an impossible lens) is refused unless
the admin confirmed "Save anyway" (force), which is audited; the verdict is kept in camera.json."""
import json

from server import db as DB

EV = "2026wamil"
CAM = {"K": [[1095, 0, 934], [0, 1095, 540], [0, 0, 1]], "dist": [-0.29, 0, 0, 0, 0], "rvec": [0, 0, 0],
       "tvec": [0, 0, 10], "W": 1920, "H": 1080, "calib": {"tag_rms_px": 6.5}}


def _wizard(data, verdict):
    d = data / "events" / EV / "calib_wizard"
    d.mkdir(parents=True, exist_ok=True)
    (d / "overhead.json").write_text(json.dumps({"camera": CAM, "report": {"verdict": verdict, "verdict_reasons": [
        {"level": "bad", "code": "tag_rms", "text": "The AprilTags don't line up with one camera."}]}}))
    return data / "events" / EV / "camera.json"


def _forced():
    return DB.connect().execute("SELECT COUNT(*) FROM audit_log WHERE action='sources.calibration_forced'").fetchone()[0]


def test_bad_fit_needs_force(admin, data):
    cam = _wizard(data, "bad")
    r = admin.post(f"/events/{EV}/calibration/overhead/accept")
    assert r.status_code == 409 and "can't be trusted" in r.text and not cam.exists()
    assert admin.post(f"/events/{EV}/calibration/overhead/accept", {"force": "yes"}).status_code == 422   # strict bool
    r = admin.post(f"/events/{EV}/calibration/overhead/accept", {"force": True})
    assert r.status_code == 200, r.text
    assert json.loads(cam.read_text())["calib"]["verdict"] == "bad" and _forced() == 1


def test_good_fit_saves_without_force(admin, data):
    cam = _wizard(data, "good")
    assert admin.post(f"/events/{EV}/calibration/overhead/accept").status_code == 200
    assert json.loads(cam.read_text())["calib"]["verdict"] == "good" and _forced() == 0
