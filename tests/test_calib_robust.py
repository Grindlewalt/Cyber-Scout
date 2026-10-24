"""Calibration that survives a bad AprilTag and says plainly when a fit can't be trusted (live/calibrate.py wizard_fit
robust / report verdict / keep_refit / line_check; live/sources.py job_detect / job_fit / VOD sampling / plain job
errors / the zero-based recorder sample; live/dynpose.py clock0; web/sources.js calPlain).

The fixture is the real 2026wamil wizard state (PNW Block Party, Twitch firstwa_red2, 2026-10-24): AprilTag 1 sits
~160 px above where the field drawings put it, and the fit the owner accepted had tags 6.5 px RMS, f 2766 and the
principal point (1370, 1452) below a 1920x1080 frame.  Without tag 1: < 0.5 px and a plausible camera."""
import json
import os
import shutil
import subprocess
import sys
import time
import types

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))
cv2 = pytest.importorskip("cv2")
pytest.importorskip("scipy")
import calibrate as CB  # noqa: E402
import sources as SRC  # noqa: E402

W, H, EV = 1920, 1080, "2026wamil"
# AprilTag corners (px, detection order) from DATA/events/2026wamil/calib_wizard/overhead.json
TAGS = {1: [[453.62, 543.15], [462.65, 556.23], [468.29, 539.59], [459.65, 526.89]],
        11: [[504.48, 444.11], [519.14, 442.77], [513.15, 429.43], [498.22, 430.91]],
        13: [[91.23, 716.62], [98.96, 703.64], [88.88, 693.55], [81.36, 706.96]],
        14: [[110.79, 681.84], [118.54, 669.04], [109.01, 658.78], [101.53, 671.66]],
        24: [[1385.79, 397.13], [1402.77, 396.9], [1406.01, 382.33], [1388.73, 382.17]]}
# the wizard vector of the camera that was accepted (camera.json 2026-10-24 09:33)
ACCEPTED = [2766.0766, 1.0, 1370.3433, 1451.5952, -0.73505, 0.063557, -0.126668, -3.236225, 1.251313,
            10.113585, 22.688647, 8.386761, 0.223759]


def tags(ids=None):
    return {k: {"corners": v, "center": np.mean(v, 0).tolist()} for k, v in TAGS.items() if ids is None or k in ids}


def codes(rep, level=None):
    return {x["code"] for x in rep["verdict_reasons"] if level is None or x["level"] == level}


# ---------------------------------------------------------------- the robust fit
def test_the_tag_that_moved_is_left_out_and_the_fit_is_good():
    p, rep = CB.wizard_fit(W, H, tags(), robust=True)
    assert [d["id"] for d in rep["dropped_tags"]] == [1]
    d = rep["dropped_tags"][0]
    assert d["off_px"] > 100 and d["err_with_px"] > 8                  # ~160 px from where the other tags put it
    assert rep["tag_rms_px"] < 0.55 and rep["n_tags"] == 4             # 0.49 px here
    assert rep["verdict"] == "good"
    cx, cy = rep["c"]
    assert abs(cx - W / 2) <= 0.15 * W and abs(cy - H / 2) <= 0.15 * H
    assert 2 < rep["C"][2] < 25 and 900 < rep["f"] < 1200 and -0.4 < rep["k1"] < 0
    note = next(x for x in rep["verdict_reasons"] if x["code"] == "dropped")
    assert note["level"] == "note" and note["text"].startswith("AprilTag 1 doesn't line up with the other tags (")
    assert "left out" in note["text"]
    out = [i for i in rep["items"] if i.get("dropped")]
    assert [i["id"] for i in out] == [1] and out[0]["pred"] is not None    # the page draws where it should be
    assert rep["tag_rms_px"] == pytest.approx(np.sqrt(np.mean([i["err_px"] ** 2 for i in rep["items"] if not i.get("dropped")])), abs=0.01)


def test_kept_in_the_fit_is_bad_and_says_why():
    p, rep = CB.wizard_fit(W, H, tags())                    # robust off: tag 1 stays in
    assert rep["verdict"] == "bad" and rep["dropped_tags"] == []
    why = next(x for x in rep["verdict_reasons"] if x["code"] == "tag_error")
    assert why["level"] == "bad" and "don't line up with one camera" in why["text"]
    assert "moved, isn't mounted yet, or was read wrong" in why["text"]


def test_the_camera_that_was_accepted_is_judged_bad():
    groups, rep0 = CB._corr(tags(), None)
    rep = CB.report(np.array(ACCEPTED), W, H, rep0)
    assert rep["verdict"] == "bad"
    assert {"tag_error", "lens_centre"} <= codes(rep, "bad")
    lc = next(x for x in rep["verdict_reasons"] if x["code"] == "lens_centre")
    assert "(1370, 1452)" in lc["text"] and "picture centre (960, 540)" in lc["text"]


def test_a_stray_detection_and_the_moved_tag_are_both_left_out():
    """A second wrong tag (a stray "AprilTag 3" 442 px off, seen in one detect today): 6 tags -> pairs are tried."""
    t = tags()
    t[3] = {"corners": (np.array(TAGS[24]) + [-442.0, 60.0]).tolist()}
    t0 = time.time()
    p, rep = CB.wizard_fit(W, H, t, robust=True)
    assert sorted(d["id"] for d in rep["dropped_tags"]) == [1, 3]
    assert rep["verdict"] == "good" and rep["tag_rms_px"] < 0.55
    assert time.time() - t0 < 60


def test_no_drop_without_enough_tags_to_tell():
    p, rep = CB.wizard_fit(W, H, tags({1, 11, 24}), robust=True)         # 3 tags: can't tell which one is off
    assert rep["dropped_tags"] == [] and rep["verdict"] == "bad"
    why = next(x for x in rep["verdict_reasons"] if x["code"] == "tag_error")
    assert "can't tell which one is off" in why["text"]


def test_all_tags_on_one_face_is_bad():
    p, rep = CB.wizard_fit(W, H, tags({13, 14}))
    assert rep["verdict"] == "bad" and "one_face" in codes(rep, "bad")


def test_the_lens_moves_only_with_data_that_pins_it():
    """Corner count alone (4 tags = 16 corners) or the automatic centre line never frees the principal point / k2 / k3;
    a clicked line does."""
    p, rep = CB.wizard_fit(W, H, tags({11, 13, 14, 24}), p_init=np.r_[ACCEPTED])     # warm start from the bad lens
    assert rep["lens_free"] is False and rep["c"] == [960.0, 540.0] and rep["k2"] == 0 and rep["k3"] == 0
    Pw = CB.line_world("centerline", 400)
    pts = CB.wproj(p, Pw[::20])
    _, r2 = CB.wizard_fit(W, H, tags({11, 13, 14, 24}), lines=[(Pw, pts, 1.0, "centerline (auto)")], p_init=p)
    assert r2["lens_free"] is False and r2["c"] == [960.0, 540.0]
    _, r3 = CB.wizard_fit(W, H, tags({11, 13, 14, 24}), lines=CB.clicked_lines({"centerline": pts.tolist()}), p_init=p)
    assert r3["lens_free"] is True


def test_too_few_and_no_pose_errors_are_plain():
    p, rep = CB.wizard_fit(W, H, {})
    assert p is None and rep["code"] == "too_few" and rep["error"].startswith("No AprilTags were found")
    p, rep = CB.wizard_fit(W, H, tags({11}))
    assert p is None and "Not enough to fit the camera: 1 AprilTag." in rep["error"]
    for t in (rep["error"],):
        assert "correspondences" not in t and "initialisation" not in t


def test_a_refit_is_kept_only_when_it_does_not_make_things_worse():
    good = dict(tag_rms_px=0.48, verdict="good")
    assert CB.keep_refit(good, dict(tag_rms_px=1.23, verdict="good"))            # stays under 1.5 px: kept
    assert not CB.keep_refit(good, dict(tag_rms_px=1.6, verdict="good"))
    assert not CB.keep_refit(good, dict(tag_rms_px=0.5, verdict="check"))         # the verdict got worse
    doubtful = dict(tag_rms_px=6.0, verdict="bad")
    assert not CB.keep_refit(doubtful, dict(tag_rms_px=6.5, verdict="bad"))       # today's refit: worse, not kept
    assert CB.keep_refit(doubtful, dict(tag_rms_px=5.0, verdict="bad"))
    assert not CB.keep_refit(dict(tag_rms_px=1.0, point_rms_px=2.0, verdict="good"), dict(tag_rms_px=1.0, point_rms_px=6.5, verdict="good"))
    assert not CB.keep_refit(good, None) and not CB.keep_refit(good, {"error": "x"})


def test_line_check_measures_the_centre_line_against_the_fit():
    p, rep = CB.wizard_fit(W, H, tags(), robust=True)
    q = CB.wproj(p, CB.line_world("centerline", 400)); q = q[np.isfinite(q).all(1)]
    for shift, level in ((0, "ok"), (9, "check")):
        g = np.full((H, W), 60, np.uint8)
        cv2.polylines(g, [np.round(q + [shift, 0]).astype(np.int32).reshape(-1, 1, 2)], False, 230, 3)
        r = CB.line_check(json.loads(json.dumps(rep)), p, g)
        x = next(y for y in r["verdict_reasons"] if y["code"] == "centre_line")
        assert x["level"] == level and r["verdict"] == ("good" if level == "ok" else "check")
        assert r["centre_line_px"] == pytest.approx(shift, abs=1.5)


def test_detect_counts_tags_per_frame(monkeypatch):
    seen = iter([{}, {11: (np.array(TAGS[11]), 40.0), 24: (np.array(TAGS[24]), 40.0)}, {11: (np.array(TAGS[11]), 40.0)}])
    monkeypatch.setattr(CB, "detect_tags_gray", lambda g, **kw: next(seen))
    per = []
    out = CB.detect_tags_frames([np.zeros((4, 4), np.uint8)] * 3, per_frame=per)
    assert per == [0, 2, 1] and sorted(out) == [11]        # 24 in 1 of 3 frames: under the 2-frame minimum


# ---------------------------------------------------------------- the Video page jobs
@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"; evd = data / "events" / EV; evd.mkdir(parents=True)
    vid = tmp_path / "media" / "field.mp4"; vid.parent.mkdir(); vid.write_bytes(b"\0" * 10)
    monkeypatch.setenv("DATA", str(data)); monkeypatch.setenv("MEDIA", str(tmp_path / "media"))
    monkeypatch.setenv("LAYOUT", "none")
    doc = {"version": 1, "sources": [dict(id="file-a", origin="admin", kind="file", provider="file", url=str(vid), role="wide",
                                          primary=True, enabled=True, label="Field")]}
    (evd / "sources.json").write_text(json.dumps(doc))
    return dict(data=str(data), evd=evd, vid=str(vid))


class FakeLY:
    """layout.py's frame tools on a 180 s file: grey frames, no network."""
    grabbed = []

    @staticmethod
    def is_remote(m): return str(m).startswith("http")
    @staticmethod
    def duration(m): return 180.0
    @classmethod
    def grab_frames(cls, m, ts):
        cls.grabbed.append(list(ts))
        return [(t, np.full((H, W, 3), 90, np.uint8)) for t in ts]


def fake_imports(monkeypatch, found, per_frame):
    cb = types.SimpleNamespace(**{k: getattr(CB, k) for k in dir(CB) if not k.startswith("__")})

    def det(grays, per_frame=None, **kw):
        if per_frame is not None: per_frame.extend(per_frame_counts[:len(grays)])
        return found
    per_frame_counts = per_frame
    cb.detect_tags_frames = det
    monkeypatch.setattr(SRC, "_live_imports", lambda: (np, cv2, FakeLY, cb))


def test_detect_says_what_it_read_and_keeps_a_doubtful_first_fit(env, monkeypatch):
    fake_imports(monkeypatch, tags(), [5, 5, 4, 5, 5, 4, 5, 5])
    r = SRC.job_detect(env["data"], EV, {"target": "overhead"})
    assert r["read"] == dict(kind="video", n=8, cutaways=0, t0=12.0, t1=150.0, finished_stream=False, when="latest")
    assert [x["n"] for x in r["frame_tags"]] == [5, 5, 4, 5, 5, 4, 5, 5] and r["frame_tags"][0]["t"] == 12.0
    assert r["report"]["verdict"] == "good" and [d["id"] for d in r["report"]["dropped_tags"]] == [1]
    st = json.loads((env["evd"] / "calib_wizard" / "overhead.json").read_text())
    assert st["p"] and st["read"]["n"] == 8 and len(st["frame_tags"]) == 8
    # 3 tags, one of them off: the first fit is shown with a "bad" verdict (no longer dropped silently), no warm start
    fake_imports(monkeypatch, tags({1, 11, 24}), [3] * 8)
    r = SRC.job_detect(env["data"], EV, {"target": "overhead"})
    assert r["report"]["verdict"] == "bad" and r["overlay"]
    assert "p" not in json.loads((env["evd"] / "calib_wizard" / "overhead.json").read_text())
    # nothing found: the frame is still written for the page to show, with the per-frame counts
    fake_imports(monkeypatch, {}, [0] * 8)
    r = SRC.job_detect(env["data"], EV, {"target": "overhead"})
    assert r["tags"] == {} and "report" not in r and r["frame"] == "calib_overhead.jpg"
    assert (env["evd"] / "frames" / "calib_overhead.jpg").exists() and all(x["n"] == 0 for x in r["frame_tags"])


def test_fit_leaves_out_the_moved_tag_and_stamps_the_verdict(env, monkeypatch):
    fake_imports(monkeypatch, tags(), [5] * 8)
    SRC.job_detect(env["data"], EV, {"target": "overhead"})
    os.remove(env["evd"] / "frames" / "calib_overhead_grays.npy")     # no second tag pass
    r = SRC.job_fit(env["data"], EV, {"target": "overhead", "points": {}, "line_clicks": {}, "auto_lines": True})
    rp = r["report"]
    assert rp["verdict"] in ("good", "check") and [d["id"] for d in rp["dropped_tags"]] == [1]
    assert sorted(r["tags"]) == [1, 11, 13, 14, 24]                # the page still lists tag 1 (marked left out)
    cam = json.loads((env["evd"] / "calib_wizard" / "overhead.json").read_text())["camera"]
    assert cam["calib"]["dropped_tags"] == [1] and cam["calib"]["verdict"] == rp["verdict"]
    assert abs(cam["K"][0][2] - W / 2) < 1 and abs(cam["K"][1][2] - H / 2) < 1
    r = SRC.job_fit(env["data"], EV, {"target": "overhead", "tags": {"11": {"corners": TAGS[11]}}})
    assert r["error"].startswith("Not enough to fit the camera") and r["code"] == "too_few"


def test_a_finished_stream_is_sampled_across_its_length(monkeypatch):
    """A livestream link whose stream has ended resolves to the VOD of the whole day: read from its middle, not its
    first 42 s (2026wabon's frames were a results screen); a stream that is live now is read forward."""
    calls = []

    class LY:
        dur = 8 * 3600.0
        @staticmethod
        def is_remote(m): return True
        @classmethod
        def duration(cls, m): return cls.dur
        @staticmethod
        def grab_frames(m, ts): calls.append(("seek", list(ts))); return [(t, np.zeros((4, 4, 3), np.uint8)) for t in ts]
        @staticmethod
        def grab_dense(m, t0, seconds, hz): calls.append(("forward", t0, seconds)); return [(k / hz, np.zeros((4, 4, 3), np.uint8)) for k in range(int(seconds * hz))]
    s = dict(kind="livestream", id="tba-webcast-0")
    SRC._DUR.clear()
    assert SRC._live_url(LY, s, "https://www.youtube.com/watch?v=tqImKZrD6B0") is False
    fr, ts = SRC._grab_many(LY, s, "https://www.youtube.com/watch?v=tqImKZrD6B0", None, 16, 6.0)
    assert calls[-1][0] == "seek" and min(ts) >= 0.25 * LY.dur - 1 and max(ts) <= 0.85 * LY.dur + 1 and len(fr) == 16
    LY.dur = None; SRC._DUR.clear()
    assert SRC._live_url(LY, s, "https://www.twitch.tv/firstwa_red2") is True
    fr, ts = SRC._grab_many(LY, s, "https://www.twitch.tv/firstwa_red2", None, 16, 6.0)
    assert calls[-1] == ("forward", 0.0, 96.0) and len(fr) == 16
    assert SRC._live_url(LY, dict(kind="video"), "https://www.youtube.com/watch?v=x") is False


def test_job_errors_are_plain(tmp_path):
    data = str(tmp_path); (tmp_path / "events" / EV).mkdir(parents=True)
    with pytest.raises(ValueError, match="No video is set as the main"):
        SRC._source(data, EV, None)
    with pytest.raises(ValueError, match=r"This video \(url-x\) isn't one of the event's videos any more"):
        SRC._source(data, EV, "url-x")
    j = SRC.new_job(data, EV, "frame", {})
    p = os.path.join(SRC.jobs_dir(data), j["id"] + ".json")
    j["created"] = time.time() - 4000; SRC._jsave(p, j)
    SRC.run_pending(data, log=lambda *a: None)
    err = json.load(open(p))["error"]
    assert err.startswith("The worker isn't running video jobs: is it running?") and "job runner" not in err


# ---------------------------------------------------------------- the recorder sample's clock (A4)
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_recorder_sample_starts_at_zero(tmp_path):
    av = pytest.importorskip("av")
    import dynpose as DP
    p = str(tmp_path / "sample.ts.tmp")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
                    "-c:v", "mpeg2video", "-q:v", "2", "-output_ts_offset", "6748", "-f", "mpegts", p], check=True)
    with av.open(p) as c:
        assert DP.clock0(c.streams.video[0]) > 6700          # what dynpose.estimate now subtracts
    assert SRC._zero_based(p) is True
    with av.open(p) as c:
        s = c.streams.video[0]
        assert DP.clock0(s) == 0.0 and float(next(c.decode(s)).time) < 0.1
    (tmp_path / "junk.ts").write_bytes(b"A" * 200_000)
    assert SRC._zero_based(str(tmp_path / "junk.ts")) is False and (tmp_path / "junk.ts").stat().st_size == 200_000


# ---------------------------------------------------------------- web/sources.js: the jargon in plain words
@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_calibration_messages_are_translated():
    js = open(os.path.join(ROOT, "web", "sources.js"), encoding="utf-8").read()
    block = js[js.index("// calib-plain:start"):js.index("// calib-plain:end")]
    cases = ["need more correspondences (0 points): mark more tags or click field points", "pose initialisation failed",
             "unknown source None", "unknown source url-abc", "expired (no job runner picked it up)",
             "waiting for the worker's video job runner (sources.job_loop)",
             "large residuals: some point is probably mislabelled", "run detect first", "Something else entirely."]
    out = subprocess.run(["node", "-e", block + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(calPlain)))"],
                         capture_output=True, text=True, check=True).stdout
    t = json.loads(out)
    assert t[0].startswith("Not enough to fit the camera (0 tag corners and points)")
    assert t[1].startswith("Couldn't find a camera position that fits these AprilTags")
    assert t[2].startswith("No video is set as the main (wide) view")
    assert "url-abc" in t[3] and "reload the Video page" in t[3]
    assert t[4] == "The worker isn't running video jobs: is it running?"
    assert t[5] == "waiting for the worker to pick it up (is it running?)"
    assert "don't line up with the fit" in t[6] and t[7] == "Read the video first (Detect tags)."
    assert t[8] == "Something else entirely."
    for x in t[:8]:
        assert not any(w in x for w in ("correspondences", "initialisation", "job runner", "job_loop", "mislabelled"))


# ---------------------------------------------------------------- a tag on a part that moves (2026wamil: the bar went up)
def test_steady_keeps_where_a_tag_sat_most_of_the_time():
    import numpy as np
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    C = np.array([sq + [500, 300]] * 6 + [sq + [500, 180]] * 2)            # raised for 2 of 8 frames
    keep, moving = CB.steady(C, need=3)
    assert not moving and keep.tolist() == [True] * 6 + [False] * 2
    C = np.array([sq + [500, 300 - 25 * i] for i in range(8)])            # moving the whole time
    assert CB.steady(C, need=3)[1] is True


def test_a_moving_tag_is_left_out_with_a_reason():
    tags = {1: {"corners": [[0, 0]] * 4, "moving": True}}
    r = CB.moving_note({"verdict_reasons": []}, tags)
    assert r["moving_tags"] == [1] and "moved while the frames were read" in r["verdict_reasons"][0]["text"]
    assert len(CB.moving_note(r, tags)["verdict_reasons"]) == 1               # said once
    groups, rep = CB._corr(tags, None)
    assert not groups and not rep
