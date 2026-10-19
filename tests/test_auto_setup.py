"""Automatic set-up of a stream (live/sources.py job_auto_setup / accept_auto / auto_setup_sweep, dynpose.feed_summary)
and the per-source `program_feed` / `dynamic` flags (sources.dynamic_on, liveingest.trackable / for_processing).
dynpose.auto_setup itself is replaced by a fake result (it needs a real clip: measured in docs/guide/pipeline.md 15.3);
the job's drawing, comparison and file handling run for real."""
import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "live"))
import sources as SRC  # noqa: E402
import liveingest as LI  # noqa: E402

EV, SID, VID = "2026wagg", "url-e6e5c488", "319604392919"
# the accepted 2026wagg wide camera (wizard, 6 tags): K, dist, rvec, C
CAM = {"W": 1920, "H": 1080, "K": [[1038.3327, 0.0, 1008.1053], [0.0, 1038.3327, 536.6326], [0.0, 0.0, 1.0]],
       "dist": [-0.381295, 0.286458, 0.0, 0.0, -0.098822], "rvec": [0.00235982, -2.83313873, 1.22447949],
       "tvec": [8.0211973, -3.6132604, 12.8919362]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, media = tmp_path / "data", tmp_path / "media"
    evd = data / "events" / EV
    evd.mkdir(parents=True)
    (media / "live").mkdir(parents=True)
    monkeypatch.setenv("DATA", str(data))
    monkeypatch.setenv("MEDIA", str(media))
    monkeypatch.delenv("LIVE_DIR", raising=False)
    monkeypatch.delenv("LAYOUT", raising=False)
    monkeypatch.setattr(SRC, "AUTO_SETUP", True)
    doc = {"version": 1, "sources": [
        dict(id=SID, origin="admin", kind="livestream", provider="twitch", url="https://www.twitch.tv/firstwa_red1",
             role="wide", primary=True, enabled=True, label="Event livestream"),
        dict(id="tba-uploads-wide", origin="tba", kind="per_match", provider="youtube", role="wide", enabled=True)]}
    (evd / "sources.json").write_text(json.dumps(doc))
    return dict(data=data, media=media, evd=evd)


def recorder_dir(env, seconds=0, clip=False, vid=VID):
    d = env["media"] / "live" / EV / vid
    (d / "clips").mkdir(parents=True, exist_ok=True)
    (d / "seg").mkdir(exist_ok=True)
    (d / "meta.json").write_text(json.dumps(dict(event=EV, source=SID, url="https://www.twitch.tv/firstwa_red1",
                                                 video_id=vid, started=1.0)))
    rows, seg = [], d / "seg" / "s0.ts"
    data = b""
    for k in range(int(seconds // 2)):
        b = bytes([k % 256]) * 2000
        rows.append(dict(sq=k, file="seg/s0.ts", off=len(data), size=len(b), pts=2.0 * k, dur=2.0, wall=1e9 + 2 * k))
        data += b
    seg.write_bytes(data)
    (d / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if clip:
        (d / "clips" / "tba-qm33.mp4").write_bytes(b"\0" * 200_000)
    return d


# ---------------------------------------------------------------- flags
def test_dynamic_follows_the_program_feed_unless_forced(env):
    doc = SRC.load(env["evd"])
    s = SRC.get(doc, SID)
    assert SRC.dynamic_on(s) is False                                  # nothing detected yet
    SRC.set_program_feed(doc, SID, dict(program_feed=True, why="4 camera views"))
    assert SRC.dynamic_on(s) is True and s["program_feed_why"] == "4 camera views"
    SRC.set_program_feed(doc, SID, dict(program_feed=None, why="too few tags"))   # undecided: kept
    assert s["program_feed"] is True
    SRC.set_dynamic(doc, SID, False, by="Coach")
    assert s["dynamic"] is False and SRC.dynamic_on(s) is False
    SRC.update(doc, SID, {"dynamic": True})
    assert SRC.dynamic_on(s) is True
    SRC.set_dynamic(doc, SID, None)
    assert "dynamic" not in s and SRC.dynamic_on(s) is True            # null = auto again
    with pytest.raises(ValueError):
        SRC.update(doc, SID, {"dynamic": "yes"})
    with pytest.raises(KeyError):
        SRC.set_dynamic(doc, "nope", True)
    # TBA re-discovery keeps the admin's choice and the detection
    SRC.set_dynamic(doc, SID, False)
    SRC.merge(doc, [dict(id=SID, origin="admin", kind="livestream", url=s["url"], role="dynamic")])
    assert s["dynamic"] is False and s["program_feed"] is True and s["role"] == "wide"


def test_trackable_carries_the_dynamic_flag(env):
    evd = env["evd"]
    (evd / "layout.json").write_text(json.dumps({"sources": {SID: {"W": 1920, "H": 1080, "regions": [
        {"kind": "field", "box": [0, 142, 1920, 824]}]}}}))
    (evd / f"camera_{SID}.json").write_text(json.dumps(CAM))
    assert LI.trackable(EV, SID)["dynamic"] is False
    doc = SRC.load(evd)
    SRC.set_program_feed(doc, SID, dict(program_feed=True, why="x")); SRC.save(evd, doc)
    t = LI.trackable(EV, SID)
    assert t["ok"] and t["dynamic"] is True
    SRC.set_dynamic(doc, SID, False); SRC.save(evd, doc)
    assert LI.trackable(EV, SID)["dynamic"] is False
    assert LI.stream_dynamic(EV, "no-such-source") is False


# ---------------------------------------------------------------- media for the set-up
def test_setup_media_waits_for_enough_recording(env):
    s = dict(SRC.get(SRC.load(env["evd"]), SID), _ev=EV)
    assert SRC.setup_ready(EV, s) == (None, "nothing recorded from this stream yet")
    recorder_dir(env, seconds=40)
    k, why = SRC.setup_ready(EV, s)
    assert k is None and "40 s recorded" in why
    assert SRC.setup_media(EV, s)[0] is None
    recorder_dir(env, seconds=120)
    assert SRC.setup_ready(EV, s) == ("sample", None)
    p, kind = SRC.setup_media(EV, s)
    assert kind == "sample" and p.endswith("sample.ts") and os.path.getsize(p) > 100_000 - 50_000
    assert SRC.setup_media(EV, s, build=False) == (None, "no match clip yet")
    recorder_dir(env, seconds=120, clip=True)
    assert SRC.setup_ready(EV, s) == ("clip", None)
    p, kind = SRC.setup_media(EV, s, build=False)
    assert kind == "clip" and p.endswith("tba-qm33.mp4")
    per = SRC.get(SRC.load(env["evd"]), "tba-uploads-wide")
    assert SRC.setup_ready(EV, per)[0] is None and "Auto-calibrate" in SRC.setup_ready(EV, per)[1]


# ---------------------------------------------------------------- the job (fake dynpose.auto_setup)
def _p_of(cam):
    import cv2
    K = np.array(cam["K"]); D = np.ravel(cam["dist"]).tolist() + [0] * 5
    R, _ = cv2.Rodrigues(np.array(cam["rvec"], float)); C = -R.T @ np.array(cam["tvec"], float)
    return np.r_[K[0, 0], K[1, 1] / K[0, 0], K[0, 2], K[1, 2], D[0], D[1], np.ravel(cam["rvec"]), C, D[4]]


def fake_setup(camera=True):
    import calibrate as CB
    p = _p_of(CAM)
    cam = CB.wizard_camera(p, 1920, 1080, which="overhead", model="wizard pinhole f,c,k1,k2,k3", source=SID,
                           calib=dict(method="dynpose auto_setup", tag_rms_px=0.25, n_tags=6)) if camera else None
    if cam: cam["field_roi"] = [0, 142, 1920, 824]
    img = np.full((1080, 1920, 3), 70, np.uint8)
    overlays = [[622, 0, 1298, 70], [0, 824, 1920, 1080]]
    regions = [dict(id="a0", kind="scoreboard", box=overlays[1]), dict(id="a1", kind="mask", box=overlays[0]),
               dict(id="aclock", kind="clock", box=[866, 988, 1052, 1064])]
    if camera: regions.append(dict(id="afield", kind="field", box=[0, 142, 1920, 824]))
    rep = dict(W=1920, H=1080, duration=190.0, overlays=overlays,
               feed=dict(program_feed=True, why="3 camera views (3 locked-off, 0 moving) and 8 cuts in 190 s"),
               scoreboard=dict(layout="bottom", share=1.0), clock=[866, 988, 1052, 1064],
               pose=dict(pose_ok=0.99, shots=9, n_cameras=4, anchor_rms_px=dict(median=0.48, p90=1.17)),
               fixed=dict(on_air=0.553, shots=4, tags=6, tag_rms_px=0.25, warnings=[]) if camera else None,
               fixed_why=None if camera else "no locked-off view of the whole field in this clip")
    return dict(camera=cam, layout={"version": 1, "processing_source": SID, "sources": {SID: dict(W=1920, H=1080, regions=regions)}},
                view_ref=img if camera else None, overlays=overlays, report=rep, poses=None,
                p=[float(x) for x in p] if camera else None, frame=img)


def run_job(env, monkeypatch, setup):
    import dynpose as DP
    monkeypatch.setattr(DP, "auto_setup", lambda media, **kw: setup)
    j = SRC.submit_auto_setup(str(env["data"]), EV, SID, by="auto set-up", auto=True, inline=False)
    assert SRC.auto_state(str(env["evd"]), SID, str(env["data"]))["running"]
    assert SRC._claim(str(env["data"]), j["id"])
    return SRC.run_job(str(env["data"]), j, log=lambda *a: None)


def test_job_writes_a_candidate_and_the_program_feed(env, monkeypatch):
    pytest.importorskip("cv2")
    recorder_dir(env, seconds=120, clip=True)
    (env["evd"] / "camera.json").write_text(json.dumps(dict(CAM, source=SID)))        # the wizard's calibration
    j = run_job(env, monkeypatch, fake_setup())
    assert j["state"] == "done", j.get("error")
    d = env["evd"] / "auto_setup" / SID
    for f in ("state.json", f"camera_{SID}.json", "layout.json", f"view_{SID}.png", "overlay.jpg", "auto_setup.json", "job.json"):
        assert (d / f).exists(), f
    st = SRC.auto_state(str(env["evd"]), SID, str(env["data"]))
    assert st["status"] == "ok" and not st["running"] and st["media_kind"] == "clip" and st["program_feed"] is True
    assert st["fixed"]["tags"] == 6 and st["clock"] == [866, 988, 1052, 1064] and st["field"] == [0, 142, 1920, 824]
    assert st["vs_saved"]["file"] == "camera.json" and st["vs_saved"]["median_px"] < 1.0   # same camera
    s = SRC.get(SRC.load(env["evd"]), SID)
    assert s["program_feed"] is True and SRC.dynamic_on(s)
    import cv2
    im = cv2.imread(str(d / "overlay.jpg"))
    assert im.shape[:2] == (720, 1280) and im[300:500, 200:1100].std() > 5              # the projected field drawn


def test_job_without_a_fixed_camera_and_failures(env, monkeypatch):
    pytest.importorskip("cv2")
    recorder_dir(env, seconds=120)
    j = run_job(env, monkeypatch, fake_setup(camera=False))
    st = SRC.auto_state(str(env["evd"]), SID, str(env["data"]))
    assert j["state"] == "done" and st["status"] == "no_camera" and "locked-off" in st["message"]
    assert st["media_kind"] == "sample"
    with pytest.raises(ValueError):
        SRC.accept_auto(str(env["evd"]), SID, by="Coach")
    # a failing run keeps the last good candidate (and says what failed)
    import dynpose as DP

    def boom(media, **kw): raise RuntimeError("decode failed")
    monkeypatch.setattr(DP, "auto_setup", boom)
    j = SRC.submit_auto_setup(str(env["data"]), EV, SID, inline=False)
    SRC._claim(str(env["data"]), j["id"]); j = SRC.run_job(str(env["data"]), j, log=lambda *a: None)
    st = SRC.auto_state(str(env["evd"]), SID, str(env["data"]))
    assert j["state"] == "error" and st["status"] == "no_camera" and "decode failed" in st["last_error"]["message"]


# ---------------------------------------------------------------- accept
def _candidate(env, monkeypatch):
    recorder_dir(env, seconds=120, clip=True)
    assert run_job(env, monkeypatch, fake_setup())["state"] == "done"


def test_accept_writes_camera_layout_view_like_the_wizard(env, monkeypatch):
    pytest.importorskip("cv2")
    evd = env["evd"]
    (evd / "layout.json").write_text(json.dumps({"version": 1, "processing_source": SID, "sources": {SID: {
        "W": 1280, "H": 720, "regions": [{"id": "f", "kind": "field", "box": [0, 0, 1280, 600]},
                                         {"id": "s", "kind": "station_red", "box": [0, 500, 400, 720]}]}}}))
    (evd / f"pose_latest_{SID}.json").write_text("{}")
    _candidate(env, monkeypatch)
    out = SRC.accept_auto(str(evd), SID, by="Coach", now=1000.0)
    assert out == dict(camera="camera.json", layout=True, view=f"view_{SID}.png", at=1000.0)
    cam = json.loads((evd / "camera.json").read_text())
    assert cam["source"] == SID and cam["calib"]["accepted_by"] == "Coach" and cam["calib"]["method"] == "dynpose auto_setup"
    lay = json.loads((evd / "layout.json").read_text())
    regs = lay["sources"][SID]["regions"]
    assert lay["sources"][SID]["W"] == 1920 and lay["processing_source"] == SID
    assert [r["kind"] for r in regs] == ["scoreboard", "mask", "clock", "field", "station_red"]
    assert next(r for r in regs if r["kind"] == "station_red")["box"] == [0, 750, 600, 1080]        # rescaled, kept
    assert (evd / f"view_{SID}.png").exists() and (evd / "layout.json.bak-1000").exists()
    assert not (evd / f"pose_latest_{SID}.json").exists() and (evd / f"pose_latest_{SID}.json.bak-1000").exists()
    st = SRC.auto_state(str(evd), SID, str(env["data"]))
    assert st["accepted_by"] == "Coach" and st["accepted_file"] == "camera.json"
    t = LI.trackable(EV, SID)
    assert t["ok"] and t["camera"].endswith("camera.json") and t["view_ref"].endswith(f"view_{SID}.png")


def test_accept_never_overwrites_another_videos_camera(env, monkeypatch):
    pytest.importorskip("cv2")
    evd = env["evd"]
    (evd / "camera.json").write_text(json.dumps(dict(CAM, source="tba-uploads-wide")))
    _candidate(env, monkeypatch)
    assert SRC.accept_target(str(evd), SID) == f"camera_{SID}.json"
    assert SRC.accept_auto(str(evd), SID, by="Coach")["camera"] == f"camera_{SID}.json"
    assert json.loads((evd / "camera.json").read_text())["source"] == "tba-uploads-wide"
    assert SRC.stream_calibration(str(evd), SID).endswith(f"camera_{SID}.json")
    assert SRC.accept_target(str(evd), SID) == f"camera_{SID}.json"                  # next time: the same file


# ---------------------------------------------------------------- the sweep
def test_sweep_queues_one_set_up_per_recorded_stream(env, monkeypatch):
    data = str(env["data"])
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None                    # nothing recorded
    recorder_dir(env, seconds=40)
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None                    # too little
    recorder_dir(env, seconds=120)
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) == SID
    assert SRC.auto_state(str(env["evd"]), SID, data)["running"]
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None                    # one at a time
    jid = json.loads((env["evd"] / "auto_setup" / SID / "job.json").read_text())["id"]
    j = SRC.get_job(data, jid)
    assert j["kind"] == "auto_setup" and j["params"]["auto"] is True and j["state"] == "queued"
    # finished from a between-matches sample without a camera: run again once a match clip exists
    j.update(state="done"); SRC._jsave(os.path.join(SRC.jobs_dir(data), jid + ".json"), j)
    st = dict(status="no_camera", at=1.0, media_kind="sample", message="x")
    (env["evd"] / "auto_setup" / SID / "state.json").write_text(json.dumps(st))
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None                    # no clip yet
    recorder_dir(env, seconds=120, clip=True)
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) == SID
    # a result from a clip, or an accepted one, is final; a failure is retried after AUTO_RETRY_S
    jid = json.loads((env["evd"] / "auto_setup" / SID / "job.json").read_text())["id"]
    j = SRC.get_job(data, jid); j.update(state="done"); SRC._jsave(os.path.join(SRC.jobs_dir(data), jid + ".json"), j)
    (env["evd"] / "auto_setup" / SID / "state.json").write_text(json.dumps(dict(st, media_kind="clip")))
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None
    import time
    (env["evd"] / "auto_setup" / SID / "state.json").write_text(json.dumps(dict(status="error", at=time.time())))
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None
    (env["evd"] / "auto_setup" / SID / "state.json").write_text(json.dumps(dict(status="error", at=time.time() - 4000)))
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) == SID
    monkeypatch.setattr(SRC, "AUTO_SETUP", False)
    assert SRC.auto_setup_sweep(data) is None


def test_stale_jobs_do_not_block(env):
    data = str(env["data"])
    import time
    j = SRC.new_job(data, EV, "auto_setup", {"source": SID})
    (env["evd"] / "auto_setup" / SID).mkdir(parents=True)
    (env["evd"] / "auto_setup" / SID / "job.json").write_text(json.dumps(dict(id=j["id"])))
    assert SRC.auto_state(str(env["evd"]), SID, data)["running"]
    j.update(state="running", started=time.time() - 4000); SRC._jsave(os.path.join(SRC.jobs_dir(data), j["id"] + ".json"), j)
    assert not SRC.auto_state(str(env["evd"]), SID, data)["running"]                  # its runner died
    with pytest.raises(ValueError):
        SRC.auto_dir(str(env["evd"]), "../x")
    assert SRC.auto_state(str(env["evd"]), "../x") is None


def test_slow_jobs_have_their_own_runner(env):
    data = str(env["data"])
    SRC.new_job(data, EV, "auto_setup", {"source": SID})
    ran = []
    SRC.JOBS["noop"] = lambda *a: ran.append(1) or {}
    try:
        SRC.new_job(data, EV, "noop", {})
        assert SRC.run_pending(data, log=lambda *a: None, exclude=SRC.SLOW_JOBS) == 1 and ran == [1]
        states = sorted(json.loads(open(os.path.join(SRC.jobs_dir(data), f)).read())["state"]
                        for f in os.listdir(SRC.jobs_dir(data)) if f.endswith(".json"))
        assert states == ["done", "queued"]                                          # the auto set-up waits
    finally:
        SRC.JOBS.pop("noop")


# ---------------------------------------------------------------- dynpose.feed_summary
def _track(shots, still):
    import dynpose as DP
    n = sum(m for _, m, _ in shots)
    pt = DP.PoseTrack(n, 1920, 1080)
    i = 0
    for sid, m, pose in shots:
        pt.shot[i:i + m] = sid
        if pose is not None:
            pt.kind[i:i + m] = DP.TAGS
            pt.rv[i:i + m], pt.C[i:i + m], pt.f[i:i + m], pt.k1[i:i + m] = pose
            pt.rms[i:i + m] = 0.4; pt.ntag[i:i + m] = 5
        i += m
    pt.info["shot_still_px"] = {str(k): v for k, v in still.items()}
    return pt


def test_feed_summary_tells_a_program_feed_from_one_camera():
    pytest.importorskip("cv2")
    import dynpose as DP
    wide = (np.array([0.0, -2.83, 1.22]), np.array([7.4, 12.0, 6.6]), 1038.0, 0.0)
    close = (np.array([0.3, -2.5, 1.0]), np.array([16.7, 12.2, 1.9]), 2400.0, 0.0)
    one = DP.feed_summary(_track([(0, 900, wide), (1, 90, None), (2, 600, wide)], {0: 0.3, 2: 0.4}))
    assert one["program_feed"] is False and one["views"] == 1 and one["cuts"] == 2      # wide, results screen, wide
    two = DP.feed_summary(_track([(0, 600, wide), (1, 300, close), (2, 600, wide)], {0: 0.3, 1: 6.0, 2: 0.4}))
    assert two["program_feed"] is True and two["static_views"] == 1 and two["moving_shots"] == 1 and two["moving_s"] == 10.0
    still2 = DP.feed_summary(_track([(0, 600, wide), (1, 300, close)], {0: 0.3, 1: 0.5}))
    assert still2["program_feed"] is True and still2["static_views"] == 2
    cuts = DP.feed_summary(_track([(k, 60, None) for k in range(6)], {}))
    assert cuts["program_feed"] is True and cuts["cuts"] == 5
    none = DP.feed_summary(_track([(0, 300, None)], {}))
    assert none["program_feed"] is None


def test_fixed_camera_is_the_high_view_on_air_longest():
    pytest.importorskip("cv2")
    import dynpose as DP
    wide = DP.Pose(np.array([0.0, -2.83, 1.22]), np.array([7.4, 12.0, 6.6]), 1038.0, 960, 540)
    corner = DP.Pose(np.array([0.3, -2.5, 1.0]), np.array([16.7, 12.2, 1.9]), 1300.0, 960, 540)
    idx = lambda n: np.arange(n)
    # 2026wagg qm30: the red-corner camera is locked off and on air longer than the wide one in this clip
    g, why, views = DP._fixed_group([(0, idx(700), corner), (1, idx(500), wide), (2, idx(300), corner)], 2000)
    assert why is None and g[0][2] is wide and len(views) == 2 and views[0]["on_air"] == 0.5 and views[0]["C"][2] == 1.9
    g, why, _ = DP._fixed_group([(0, idx(700), corner)], 2000)
    assert g is None and "1.9 m above the carpet" in why


def test_sweep_skips_old_recordings_and_duplicate_links(env):
    data = str(env["data"])
    d = recorder_dir(env, seconds=120, clip=True)
    doc = SRC.load(env["evd"])                     # TBA lists the admin's stream again (same link)
    doc["sources"].insert(0, dict(id="tba-webcast-1", origin="tba", kind="livestream", url="https://www.twitch.tv/firstwa_red1",
                                  role="dynamic", enabled=True))
    SRC.save(env["evd"], doc)
    old = 10 * 86400
    os.utime(d / "chunks.jsonl", (os.path.getmtime(d) - old, os.path.getmtime(d) - old))
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None                    # an old event
    os.utime(d / "chunks.jsonl", None)
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) == SID                     # the admin's copy, once
    jid = json.loads((env["evd"] / "auto_setup" / SID / "job.json").read_text())["id"]
    j = SRC.get_job(data, jid); j.update(state="done"); SRC._jsave(os.path.join(SRC.jobs_dir(data), jid + ".json"), j)
    (env["evd"] / "auto_setup" / SID / "state.json").write_text(json.dumps(dict(status="ok", at=1.0, media_kind="clip", clock=[1, 2, 3, 4])))
    assert SRC.auto_setup_sweep(data, log=lambda *a: None) is None
