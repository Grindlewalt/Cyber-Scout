"""Per-match camera check (live/camcheck.py, process.check_pose): a bumped camera is re-aimed, a tag that
disagrees is left out, too few tags / an unfixable move say so in plain words.  Synthetic: the 2026wasam
calibration as the carried pose, tag detections faked from a "true" (bumped) camera, a plate with the true
white centre line; one test renders real tag36h11 tags for the window detector."""
import os
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("scipy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import camera as CAMM  # noqa: E402
import camcheck as CC  # noqa: E402

CAL = os.path.join(ROOT, "events", "2026wasam", "camera.json")
TAG_CSV = os.path.join(ROOT, "live", "field", "2026-rebuilt-welded.csv")
TAGS = CC.load_tags(TAG_CSV)
SIX = [2, 11, 13, 21, 22, 24]                       # both hubs + both far corners in the 2026wasam view
XG, YG = np.meshgrid(np.linspace(0, CAMM.LEN, 34), np.linspace(0, CAMM.WID, 17))
GRID = np.c_[XG.ravel(), YG.ravel(), np.zeros(XG.size)]


def base():
    return CAMM.Camera(CAL)


def bumped(cam, deg=0.0, axis=(0, 1, 0), zoom=1.0, k1=None):
    Rd, _ = cv2.Rodrigues(np.asarray(axis, float) * np.radians(deg))
    R = Rd @ cam.R; rv, _ = cv2.Rodrigues(R)
    raw = dict(cam.raw, rvec=rv.ravel().tolist(), tvec=(-R @ cam.C).tolist())
    K = cam.K.copy(); K[0, 0] *= zoom; K[1, 1] *= zoom; raw["K"] = K.tolist()
    if k1 is not None:
        raw["dist"] = [k1] + list(cam.raw["dist"][1:])
    return CAMM.Camera(raw)


def plate_for(truth):
    """Grey plate with the white centre line where `truth` sees it."""
    im = np.full((truth.H, truth.W), 90, np.uint8)
    Y = np.linspace(-0.3, CAMM.WID + 0.3, 400)
    q = truth.project(np.c_[np.full(400, CAMM.LEN / 2), Y, np.zeros(400)])
    cv2.polylines(im, [np.round(q).astype(np.int32).reshape(-1, 1, 2)], False, 255, 3)
    return im


def grid_err(a, b):
    q = b.project(GRID)
    m = (q[:, 0] >= 0) & (q[:, 0] < b.W) & (q[:, 1] >= 0) & (q[:, 1] < b.H)
    return float(np.median(np.linalg.norm(a.project(GRID[m]) - q[m], axis=1)))


@pytest.fixture
def fake(monkeypatch):
    """fake(truth, visible=SIX, offs={id: (du, dv)}) -> frames; detections come from `truth`: a window finds a
    tag when it lies within the window, the whole-frame pass finds every visible tag in the frame."""
    calls = {"full": 0}

    def setup(truth, visible=SIX, offs=None):
        offs = offs or {}

        def where(tid):
            return truth.project(TAGS[tid][None])[0] + np.asarray(offs.get(tid, (0, 0)), float)

        def inside(p, m=0):
            return m <= p[0] < truth.W - m and m <= p[1] < truth.H - m

        def window_tags(frames, cam, tags, det, win=CC.WIN, min_hits=CC.MIN_HITS):
            found, hits = {}, {}
            for tid in visible:
                p, t = cam.project(TAGS[tid][None])[0], where(tid)
                if inside(p, win) and inside(t) and np.all(np.abs(t - p) < win - 10):
                    found[tid] = t; hits[tid] = [(k, t[0], t[1]) for k in range(len(frames))]
            return found, hits

        def full_frame_tags(frames, tag_csv, n=CC.FULL_FRAMES, log=print):
            calls["full"] += 1
            return {tid: where(tid) for tid in visible if inside(where(tid), 5)}

        monkeypatch.setattr(CC, "window_tags", window_tags)
        monkeypatch.setattr(CC, "full_frame_tags", full_frame_tags)
        return [plate_for(truth)] * 8

    setup.calls = calls
    return setup


def run(frames, cam):
    return CC.check_frames(frames, cam, TAG_CSV, log=lambda *a: None, det=object())


def test_camera_in_place_is_ok_and_untouched(fake):
    cam = base()
    out, info = run(fake(cam), cam)
    assert out is cam
    assert info["status"] == "ok" and info["tags"] == 6 and info["moved_px"] < 0.01
    assert "applied" not in info and "full_frame" not in info and fake.calls["full"] == 0
    assert info["why"].startswith("Camera checked against 6 field tags: it hasn't moved")


def test_small_bump_reaimed_on_the_window_tags(fake):
    cam = base(); truth = bumped(cam, 1.0)
    out, info = run(fake(truth), cam)
    assert info["status"] == "re_aimed" and info["applied"]
    assert 8 < info["moved_px"] < 30 and info["resolved_px"] < 0.5
    assert fake.calls["full"] == 0                      # cheap path only
    assert grid_err(out, truth) < 0.5
    assert info["why"].startswith(f"Camera moved about {info['moved_px']:.0f} px (1.0°) since the last match; "
                                  "re-aimed automatically on 6 field tags")


def test_big_bump_needs_the_whole_frame(fake):
    cam = base(); truth = bumped(cam, 4.0, (0.6, 0.8, 0))
    out, info = run(fake(truth), cam)
    assert fake.calls["full"] == 1 and info["full_frame"]["tags"] == 6
    assert info["status"] == "re_aimed" and info["moved_px"] > 40
    assert abs(info["moved_deg"] - 4.0) < 0.1
    assert grid_err(out, truth) < 0.5


@pytest.mark.parametrize("zoom", [1.03, 1.08])
def test_zoom_refit(fake, zoom):
    cam = base(); truth = bumped(cam, 1.0, (1, 0, 0), zoom)
    out, info = run(fake(truth), cam)
    assert info["status"] == "re_aimed"
    assert abs(info["zoom"] - zoom) < 0.005
    assert abs(out.K[0, 0] / cam.K[0, 0] - zoom) < 0.005 and np.allclose(out.D, cam.D)
    assert grid_err(out, truth) < 0.5
    assert f"zoomed in {(zoom - 1) * 100:.0f}%" in info["why"]


def test_zoom_on_three_tags_not_faked_by_a_shift(fake):
    """3 tags, zoomed: a fixed-lens fit matches them by moving the camera ~0.5 m (2026wasam qm13 synthetic: 6 px
    off on the field while the tags said 0.4 px).  The zoom fit needs no shift and wins."""
    cam = base(); truth = bumped(cam, 1.0, (0, 1, 0), 1.05)
    out, info = run(fake(truth, visible=[2, 13, 22]), cam)
    assert info["status"] == "re_aimed" and abs(info["zoom"] - 1.05) < 0.005 and info["moved_m"] < 0.1
    assert grid_err(out, truth) < 0.5


def test_tag_that_disagrees_is_left_out(fake):
    cam = base(); truth = bumped(cam, 1.0)
    out, info = run(fake(truth, offs={21: (10, -8)}), cam)
    assert info["status"] == "re_aimed" and info["outliers"] == [21]
    assert grid_err(out, truth) < 0.5
    assert "on 5 of 6 field tags" in info["why"]


def test_lone_tag_on_the_far_side_is_kept(fake):
    """2026wasam qm41: four tags at the red end, tag 22 alone at the blue end.  The four extrapolate badly to it,
    but it fits with a vote: not an outlier."""
    cam = base(); truth = bumped(cam, 0.3, (0.2, 1, 0))
    out, info = run(fake(truth, visible=[1, 11, 13, 14, 22]), cam)
    assert info["status"] == "re_aimed" and "outliers" not in info
    assert grid_err(out, truth) < 0.3


def test_two_tags_after_a_big_bump_aim_the_windows(fake, monkeypatch):
    """The whole frame finds only 2 tags (small ones are missed at x3): aimed by those two (rotation + the zoom
    their angle implies), the windows find the rest."""
    cam = base(); truth = bumped(cam, 4.0, (0, 1, 0), 1.05)
    frames = fake(truth)
    full = CC.full_frame_tags
    monkeypatch.setattr(CC, "full_frame_tags", lambda *a, **k: {t: v for t, v in full(*a, **k).items() if t in (2, 22)})
    out, info = run(frames, cam)
    assert info["two_tag_start"]["tags"] == 6 and abs(info["two_tag_start"]["zoom"] - 1.05) < 0.01
    assert info["status"] == "re_aimed" and abs(info["zoom"] - 1.05) < 0.005
    assert grid_err(out, truth) < 0.5


def test_two_tag_start_closed_form():
    cam = base(); truth = bumped(cam, 2.0, (0.5, 0.8, 0), 1.06)
    P = np.array([TAGS[2], TAGS[22]])
    c, z = CC.two_tag_start(cam, P, truth.project(P))
    assert abs(z - 1.06) < 0.002 and grid_err(c, truth) < 1.0


def test_too_few_tags_is_unverified(fake):
    cam = base(); truth = bumped(cam, 1.0)
    out, info = run(fake(truth, visible=[2, 11]), cam)
    assert out is cam and info["status"] == "unverified" and info["tags"] == 2 and info["moved_px"] is None
    assert fake.calls["full"] == 1                      # the whole frame was tried
    assert info["why"] == ("Only 2 field tags were visible, so the camera position couldn't be checked: "
                           "positions in this match may be off if the camera moved.")


def test_inconsistent_tags_drift_unfixed(fake):
    cam = base(); truth = bumped(cam, 1.0)
    rng = np.random.default_rng(3)
    offs = {t: rng.normal(0, 25, 2) for t in SIX}
    out, info = run(fake(truth, offs=offs), cam)
    assert info["status"] == "drift_unfixed"
    assert info["why"].endswith("run Auto-calibrate on this match's video.")


def test_too_few_frames_and_no_detector():
    cam = base()
    out, info = CC.check_frames([np.zeros((cam.H, cam.W), np.uint8)] * 3, cam, TAG_CSV, log=lambda *a: None,
                                det=object())
    assert out is cam and info["status"] == "unverified" and not info["checked"]
    assert info["why"].startswith("The field camera was on screen for too few frames")
    assert CC.describe({"checked": False, "reason": "no_detector"})["why"].startswith("The AprilTag detector isn't")


def test_describe_texts():
    d = CC.describe({"checked": True, "tags": 5, "moved_px": 20.4, "resolved_px": 0.4, "applied": True,
                     "moved_deg": 1.3})
    assert d["status"] == "re_aimed"
    assert d["why"] == ("Camera moved about 20 px (1.3°) since the last match; re-aimed automatically on 5 field tags "
                        "(0.4 px).")
    d = CC.describe({"checked": True, "tags": 4, "moved_px": 50.0, "resolved_px": 9.0, "moved_deg": 3.2})
    assert d["status"] == "drift_unfixed"
    assert d["why"] == ("The camera moved more than it could be re-aimed (about 3.2°): run Auto-calibrate on this "
                        "match's video.")
    d = CC.describe({"checked": True, "tags": 3, "moved_px": 40.0, "resolved_px": 0.3, "applied": True,
                     "moved_deg": 2.5, "few_tags": True})
    assert d["status"] == "drift_unfixed"
    assert d["why"] == ("The camera moved about 2.5° but only 3 field tags could be used, too few to re-aim it "
                        "reliably: run Auto-calibrate on this match's video.")
    d = CC.describe({"checked": True, "tags": 0, "moved_px": None})
    assert d["status"] == "unverified" and d["why"].startswith("No field tags were visible")
    d = CC.describe({"checked": True, "tags": 1, "moved_px": None})
    assert d["why"].startswith("Only 1 field tag was visible")


def test_describe_old_match_files():
    """pose dicts from before the status (2026wasam match files) get one too."""
    assert CC.describe({"checked": True, "tags": 5, "moved_px": 20.37, "resolved_px": 0.57, "line_old_px": 5.7,
                        "line_new_px": 0.1, "applied": True})["status"] == "re_aimed"
    assert CC.describe({"checked": True, "tags": 4, "moved_px": 1.09})["status"] == "ok"
    assert CC.describe({"checked": True, "tags": 2, "moved_px": None})["status"] == "unverified"
    d = CC.describe({"checked": True, "tags": 4, "moved_px": 35.0, "resolved_px": 30.0})
    assert d["status"] == "drift_unfixed" and "about 2.0°" in d["why"]
    assert CC.describe({"checked": False})["status"] == "unverified"


def test_accept_rule_keeps_the_old_one():
    assert CC.accept(0.3, 20.0, 6.0, 0.2)               # halves, line better
    assert not CC.accept(0.3, 20.0, 1.0, 5.0)           # tags fit, centre line worse (> 3 px): rejected
    assert CC.accept(0.9, 1.5, 1.0, 1.0)                # ends <= 1 px
    assert not CC.accept(1.6, 2.4, 1.0, 1.0)


def test_rotation_from_tags_closed_form():
    cam = base(); truth = bumped(cam, 3.0, (0.3, 0.9, 0.1))
    P = np.array([TAGS[t] for t in SIX])
    est = CC.rotation_from_tags(cam, P, truth.project(P))
    assert CC.angle_deg(est, truth) < 1e-3


def test_process_camera_pose_carries_zoom_and_status():
    pr = pytest.importorskip("process")
    cam = base()
    info = CC.describe({"checked": True, "tags": 5, "moved_px": 0.5})
    cp = pr.camera_pose(cam, cam.K.copy(), info)
    assert set(cp) == {"rvec", "tvec", "status", "why"} and cp["status"] == "ok"
    z = bumped(cam, 0.0, zoom=1.04)
    cp = pr.camera_pose(z, cam.K.copy(), info)
    assert np.allclose(cp["K"], z.K)


def _render_tag(img, tid, corners, scale=4):
    """Paint tag36h11 `tid` (black square = corners, white rim) into img, supersampled."""
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    m = cv2.aruco.generateImageMarker(d, tid, 80)                   # 8x8 cells incl. the black border
    m = cv2.copyMakeBorder(m, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)   # white rim (1 cell)
    src = np.float32([[10, 10], [90, 10], [90, 90], [10, 90]])
    x0, y0 = np.floor(corners.min(0)).astype(int) - 8
    x1, y1 = np.ceil(corners.max(0)).astype(int) + 8
    dst = np.float32((corners - [x0, y0]) * scale)
    H = cv2.getPerspectiveTransform(src, dst)
    big = cv2.resize(img[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    w = cv2.warpPerspective(m, H, big.shape[::-1], flags=cv2.INTER_LINEAR, borderValue=0)
    a = cv2.warpPerspective(np.full_like(m, 255), H, big.shape[::-1], flags=cv2.INTER_NEAREST, borderValue=0)
    big = np.where(a > 0, w, big)
    img[y0:y1, x0:x1] = cv2.resize(big, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)


def test_window_detector_on_rendered_tags():
    pytest.importorskip("pupil_apriltags")
    if not hasattr(cv2, "aruco"):
        pytest.skip("no cv2.aruco")
    cam = base()
    det = CC.tag_detector()
    # 40 px squares at the predicted tag centres (real wide-shot tags are 10-20 px): deterministic detections
    img = np.full((cam.H, cam.W), 120, np.uint8)
    corners = {}
    for tid in (2, 21):
        c = cam.project(TAGS[tid][None])[0]
        sq = np.float32([[-20, -20], [20, -20], [20, 20], [-20, 20]]) + c
        _render_tag(img, tid, sq); corners[tid] = c
    found, hits = CC.window_tags([img] * 4, cam, {t: TAGS[t] for t in corners}, det)
    assert set(found) == {2, 21}
    for tid, c in corners.items():
        assert np.linalg.norm(found[tid] - c) < 1.0
