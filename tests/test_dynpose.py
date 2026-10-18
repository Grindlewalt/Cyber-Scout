"""live/dynpose.py (dynamic view: a camera pose per frame from the field's AprilTags) and its process.py hooks.
Synthetic geometry only: no video, no detector, no GPU."""
import os, sys
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))
cv2 = pytest.importorskip("cv2")
pytest.importorskip("scipy")
import dynpose as DP  # noqa: E402

W, H = 1920, 1080


def look_at(C, tgt, f, k1=0.0):
    """Pose of a camera at C looking at tgt (image +u to the camera's right, +v down)."""
    C = np.asarray(C, float); z = np.asarray(tgt, float) - C; z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0]); x /= np.linalg.norm(x); y = np.cross(z, x)
    R = np.stack([x, y, z])
    return DP.Pose(cv2.Rodrigues(R)[0], C, f, W / 2, H / 2, np.r_[k1, 0, 0, 0, 0])


def visible_tags(p, lay, n=None):
    tags = {}
    for tid, (P4, c, nrm) in lay.items():
        if nrm @ (p.C - c) <= 0.2 * np.linalg.norm(p.C - c): continue
        q = p.project(P4)
        if np.isfinite(q).all() and (q[:, 0] > 5).all() and (q[:, 0] < W - 5).all() and (q[:, 1] > 5).all() and (q[:, 1] < H - 5).all():
            tags[tid] = q
    if n: tags = dict(list(sorted(tags.items()))[:n])
    return tags


@pytest.fixture(scope="module")
def lay():
    return DP.tag_layout()


def test_tag_layout_has_the_32_field_tags(lay):
    assert len(lay) == 32
    P4, c, n = lay[10]
    assert P4.shape == (4, 3) and np.allclose(P4.mean(0), c) and abs(np.linalg.norm(n) - 1) < 1e-9


def test_solve_free_recovers_a_closeup_camera(lay):
    true = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.8], 1720, k1=-0.13)     # 2026wagg close-up camera
    tags = visible_tags(true, lay)
    assert len(tags) >= 3
    rng = np.random.default_rng(1)
    tags = {k: v + rng.normal(0, 0.2, v.shape) for k, v in tags.items()}
    P, uv, ids = DP._points(tags, lay)
    p, e = DP.solve_free(P, uv, W, H)
    assert p is not None and e < 0.6
    assert np.linalg.norm(p.C - true.C) < 0.5
    assert abs(p.f / true.f - 1) < 0.05
    assert abs(p.D[0] - true.D[0]) < 0.06
    md, mx = DP.pose_px_diff(p, true, W, H)
    assert md < 2.0


def test_solve_rot_from_one_tag_when_the_centre_is_known(lay):
    true = look_at([16.8, 12.2, 1.95], [12.0, 4.6, 1.1], 4500)                 # zoomed in on one hub face
    tags = visible_tags(true, lay)
    assert tags
    one = dict([next(iter(tags.items()))])
    P, uv, ids = DP._points(one, lay)
    p, e = DP.solve_rot(P, uv, true.C, W, H)
    assert p is not None and e < 0.05
    assert abs(p.f / true.f - 1) < 0.01
    assert DP.rot_deg(p, true) < 0.05


def test_from_homography_follows_pan_and_zoom():
    a = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700)
    b = look_at([16.8, 12.2, 1.95], [11.0, 4.3, 0.2], 2100)                    # panned + zoomed, same mount
    Hm = b.K() @ b.R() @ a.R().T @ np.linalg.inv(a.K())
    p = DP.from_homography(a, Hm / Hm[2, 2])
    assert p is not None
    assert abs(p.f - b.f) < 1e-6 * b.f + 1e-6
    assert DP.rot_deg(p, b) < 1e-6
    md, _ = DP.pose_px_diff(p, b, W, H)
    assert md < 1e-3


def test_blend_endpoints_and_midpoint():
    a = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700)
    b = look_at([16.8, 12.2, 1.95], [10.9, 4.0, 0.0], 2000)
    assert DP.rot_deg(DP.blend(a, b, 0.0), a) < 1e-9 and DP.rot_deg(DP.blend(a, b, 1.0), b) < 1e-6
    m = DP.blend(a, b, 0.5)
    assert abs(DP.rot_deg(a, m) - DP.rot_deg(m, b)) < 1e-6
    assert abs(m.f - np.sqrt(1700 * 2000)) < 1e-6


def test_refine_rotation_re_aims_a_nudged_fixed_camera(lay):
    ref = look_at([7.42, 12.05, 6.59], [8.27, 3.5, 0.0], 1038)
    rv = ref.rv + np.array([0.004, -0.003, 0.002])                              # ~0.3 deg bump
    bumped = DP.Pose(rv, ref.C, ref.f, ref.cx, ref.cy, ref.D)
    P, uv, ids = DP._points(visible_tags(bumped, lay), lay)
    assert len(ids) >= 3
    assert np.median(np.linalg.norm(ref.project(P) - uv, axis=1)) > 2
    p = DP.refine_rotation(ref, P, uv)
    assert np.median(np.linalg.norm(p.project(P) - uv, axis=1)) < 0.05


def test_posetrack_roundtrip_and_cameras(tmp_path):
    ref = look_at([7.42, 12.05, 6.59], [8.27, 3.5, 0.0], 1038).raw(W, H, event="test")
    pt = DP.PoseTrack(6, W, H, ref)
    pt.t[:] = np.arange(6) / 30.0
    pt.set(0, DP.FIXED); pt.set(1, DP.FIXED)
    mv = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700, k1=-0.1)
    pt.set(3, DP.TAGS, mv, rms=0.4); pt.set(4, DP.FLOW, mv, age=0.03)
    assert pt.camera(0) is pt.camera(1)                                         # one object for the fixed view
    assert pt.camera(2) is None and pt.raw(2) is None
    c3 = pt.camera(3)
    assert abs(c3.K[0, 0] - 1700) < 1e-6 and abs(c3.D[0] + 0.1) < 1e-9
    f = str(tmp_path / "p.npz"); pt.save(f)
    q = DP.PoseTrack.load(f)
    assert q.ref["event"] == "test" and list(q.kind) == list(pt.kind)
    assert np.allclose(q.camera(4).project(np.array([[12.0, 4.0, 0.0]])), pt.camera(4).project(np.array([[12.0, 4.0, 0.0]])))
    s = q.summary()
    assert s["pose_ok"] == round(4 / 6, 3) and s["fixed"] == round(2 / 6, 3)
    assert q.view_string(1) == "FF-MM-"


def _anchor(p, lay):
    P, uv, _ = DP._points(visible_tags(p, lay), lay)
    return (p, 0.3, P, uv)


def _recs(n, Hs):
    return [dict(H=Hs[i] if i else None) for i in range(n)]


def test_fill_carries_poses_between_anchors(lay):
    a = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700)
    b = look_at([16.8, 12.2, 1.95], [11.5, 4.0, 0.0], 1700)                    # a slow pan over 6 samples
    n = 7
    poses = [DP.blend(a, b, i / 6) for i in range(n)]
    Hs = [None] + [poses[i].K() @ poses[i].R() @ poses[i - 1].R().T @ np.linalg.inv(poses[i - 1].K()) for i in range(1, n)]
    pt = DP.PoseTrack(n, W, H); pt.t[:] = np.arange(n) / 30.0
    DP._fill(pt, _recs(n, Hs), list(range(n)), {0: _anchor(a, lay), 6: _anchor(b, lay)})
    assert list(pt.kind) == [DP.TAGS] + [DP.FLOW] * 5 + [DP.TAGS]
    for i in range(1, 6):
        assert DP.rot_deg(pt.pose(i), poses[i]) < 1e-4
    assert pt.age[3] == pytest.approx(3 / 30.0)


def test_fill_does_not_trust_flow_that_misses_the_next_anchor(lay):
    a = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700)
    b = look_at([16.8, 12.2, 1.95], [11.5, 4.0, 0.0], 1700)
    n = 7
    Hs = [None] + [np.eye(3)] * (n - 1)                                         # flow says "static": wrong
    pt = DP.PoseTrack(n, W, H); pt.t[:] = np.arange(n) / 30.0
    DP._fill(pt, _recs(n, Hs), list(range(n)), {0: _anchor(a, lay), 6: _anchor(b, lay)})
    assert list(pt.kind[1:6]) == [DP.INTERP] * 5                                # 0.2 s apart: interpolated
    assert DP.rot_deg(pt.pose(3), DP.blend(a, b, 0.5)) < 1e-6
    assert pt.info["flow_bad_intervals"] == 1


def test_fill_stops_at_the_edge_horizon(lay):
    a = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700)
    n = int(DP.EDGE_AGE * 30) + 20
    pt = DP.PoseTrack(n, W, H); pt.t[:] = np.arange(n) / 30.0
    DP._fill(pt, _recs(n, [None] + [np.eye(3)] * (n - 1)), list(range(n)), {0: _anchor(a, lay)})
    assert pt.kind[int(DP.EDGE_AGE * 30)] == DP.FLOW                          # after the shot's last anchor
    assert (pt.kind[int(DP.EDGE_AGE * 30) + 2:] == DP.NONE).all()


def test_fill_rejects_a_runaway_flow(lay):
    a = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700)
    n = 10
    spin = np.array([[np.cos(0.3), -np.sin(0.3), 0], [np.sin(0.3), np.cos(0.3), 0], [0, 0, 1.0]])
    Hs = [None] + [a.K() @ spin @ np.linalg.inv(a.K())] * (n - 1)              # 17 deg per sample: impossible
    pt = DP.PoseTrack(n, W, H); pt.t[:] = np.arange(n) / 30.0
    DP._fill(pt, _recs(n, Hs), list(range(n)), {0: _anchor(a, lay)})
    assert (pt.kind[1:] == DP.NONE).all()


def test_clusters_group_camera_centres():
    cams = [(np.array([16.8, 12.2, 1.9]) + 0.1 * k, -0.1, 1700) for k in range(3)] + [(np.array([7.4, 12.0, 6.6]), 0.0, 1000)]
    cl = DP._clusters(cams)
    assert len(cl) == 2 and np.linalg.norm(cl[0][0] - [16.9, 12.3, 2.0]) < 0.2 and cl[0][1] == pytest.approx(-0.1)


def test_visible_field_share():
    wide = look_at([8.27, 13.0, 7.0], [8.27, 4.0, 0.0], 700)
    close = look_at([16.8, 12.2, 1.95], [12.5, 4.0, 0.0], 4000)
    assert DP.visible_field(wide, W, H) > 0.9
    assert DP.visible_field(close, W, H) < 0.5 * DP.visible_field(wide, W, H)


# ---------------------------------------------------------------- process.py hooks (dynamic=False untouched)
def test_picture_box_cuts_edge_bands_and_keeps_other_graphics():
    import process as PR
    box, rest = PR.picture_box(W, H, [(0, 826, 1920, 1080), (900, 70, 1010, 120)])
    assert box == (0, 0, 1920, 826) and rest == [(900, 70, 1010, 120)]
    box, rest = PR.picture_box(W, H, [(446, 0, 1474, 134)])                       # top score bar < 60% wide: a mask
    assert box == (0, 0, 1920, 1080) and rest == [(446, 0, 1474, 134)]


def test_gaps_of():
    import process as PR
    fw = np.array([True] * 30 + [False] * 15 + [True] * 30 + [False] * 3)
    assert PR.gaps_of(fw) == [[1.0, 1.5], [2.5, 2.6]]


def test_prepare_uses_per_detection_positions_in_dynamic_mode():
    """dyn rows: prepare() must take the given field positions / covariances (no projection with `cam`)."""
    import process as PR, camera as CAMM
    CAMM.install_geom_shim(CAMM.Camera(look_at([8.27, 13.0, 7.0], [8.27, 4.0, 0.0], 700).raw(W, H)))   # src/track.py

    class NoCam:                                       # any use of the single camera would raise
        def __getattr__(self, k): raise AssertionError(f"cam.{k} used in dynamic mode")
    n = 300
    rows, xy, cov, hh, view = [], [], [], [], []
    rng = np.random.default_rng(0)
    for fr in range(n):
        for r in range(3):
            p = np.array([3.0 + 3 * r + 0.01 * fr, 2.0 + r])
            rows.append([fr, fr / 30.0, -1, 100, 100, 150, 180, 0.9, 1 + r % 2])
            xy.append(p); cov.append(np.eye(2) * 0.01); hh.append(0.8); view.append(DP.FIXED if fr < 150 else DP.TAGS)
    det = np.array(rows, float)
    ch = rng.random((len(det), 40)).astype(np.float32)
    dyn = dict(xy=np.array(xy), cov=np.array(cov), h=np.array(hh), view=np.array(view, np.int8),
               frame_kind=np.where(np.arange(n) < 150, DP.FIXED, DP.TAGS).astype(np.int8))
    st = PR.prepare(det, ch, ch, n, [], np.ones(n, bool), np.zeros((0, 7)), NoCam(), log=lambda *a: None,
                    start_hint=1.0, dyn=dyn)
    assert len(st["d"]) == len(st["xy"]) == len(st["cov_d"]) == len(st["h_d"]) == len(st["view_d"])
    assert all(np.linalg.norm(dyn["xy"] - p, axis=1).min() < 1e-9 for p in st["xy"])      # the given positions
    assert np.allclose(st["cov_d"], 0.01 * np.eye(2)) and np.allclose(st["h_d"], 0.8)
    assert st["fwk"].dtype == np.int8 and (st["fwk"][:max(0, 150 - st["f0"])] == DP.FIXED).all()
    rep = PR.dynamic_report(dict(st, dyn_info={"pose_ok": 1.0}), st["d"])
    assert rep["dets"]["fixed"] + rep["dets"]["moving"] == len(st["d"])
    assert "fixed camera only" in rep["fuel_shots"] and set(rep["view"]) <= set("FM-")


def test_process_signature_default_is_not_dynamic():
    import inspect, process as PR
    sig = inspect.signature(PR.process)
    assert sig.parameters["dynamic"].default is False
    assert "dyn" not in inspect.signature(PR.detect_video).parameters        # the default detection pass is untouched


# ---------------------------------------------------------------- auto set-up helpers
def test_merge_boxes_and_picture():
    m = DP._merge_boxes([[0, 830, 600, 1080], [590, 828, 1300, 1080], [1310, 826, 1920, 1080], [900, 10, 1000, 60]])
    assert sorted(m) == [[0, 826, 1920, 1080], [900, 10, 1000, 60]]
    assert DP._picture(W, H, m) == (0, 0, 1920, 826)


def test_static_shots_finds_the_locked_off_wide_view():
    wide = look_at([7.42, 12.05, 6.59], [8.27, 3.5, 0.0], 1038)
    close = look_at([16.8, 12.2, 1.95], [11.9, 4.0, 0.0], 1700)
    n = 120
    pt = DP.PoseTrack(n, W, H); pt.t[:] = np.arange(n) / 30.0
    pt.shot[:60] = 0; pt.shot[60:] = 1
    for i in range(60):
        pt.set(i, DP.TAGS if i % 6 == 0 else DP.FLOW, wide, rms=0.3 if i % 6 == 0 else np.nan)
        pt.motion[i] = 0.1                                                     # flow: static
    for i in range(60, n):                                                     # panning close-up
        pt.set(i, DP.TAGS if i % 6 == 0 else DP.FLOW, DP.blend(close, look_at([16.8, 12.2, 1.95], [10.9, 4.0, 0.0], 1700),
                                                                 (i - 60) / 60), rms=0.3 if i % 6 == 0 else np.nan)
        pt.motion[i] = 8.0
    pt.info["shot_still_px"] = {"0": 0.2, "1": 6.0}                             # tags stayed put / wandered
    st = DP._static_shots(pt, min_s=1.0)
    assert [s[0] for s in st] == [0]
