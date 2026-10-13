"""Per-match video plan (live/videoplan.py): classification heuristics on synthetic frames, dedupe, per-match
choice, playoff title parsing, the plan honoured by videos.candidates / stationcam.find_inset_video, and the
recorder's channel -> live resolution (yt-dlp mocked)."""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import videoplan as VP  # noqa: E402
import sources as SRC  # noqa: E402
import videos as VID  # noqa: E402
import liveingest as LI  # noqa: E402

cv2 = pytest.importorskip("cv2")
H, W = 180, 320


# ---------------------------------------------------------------- synthetic frames
def field(rng, robots=6, seed_shift=0):
    """A fixed overhead 'field': carpet, curved rails, a centre line, a few hubs; robots = moving blobs."""
    g = np.full((H, W), 70, np.uint8)
    cv2.ellipse(g, (160, 260), (230, 210), 0, 180, 360, 150, 2)          # curved near rail (fisheye)
    cv2.ellipse(g, (160, -40), (230, 120), 0, 0, 180, 140, 2)            # far rail
    cv2.line(g, (160, 20), (165, 170), 200, 1)
    for x in (100, 220):
        cv2.rectangle(g, (x - 12, 60), (x + 12, 90), 30, -1)
    cv2.rectangle(g, (100, 4), (220, 16), 230, -1)                        # static graphic
    for k in range(robots):
        x, y = rng.integers(30, 290), rng.integers(30, 150)
        cv2.rectangle(g, (int(x), int(y)), (int(x) + 10, int(y) + 10), int(rng.integers(0, 255)), -1)
    return cv2.GaussianBlur(g, (3, 3), 0)


def burst(base, rng, shift=0.0, n=7):
    out = []
    for i in range(n):
        f = base.copy()
        if shift:
            M = np.float32([[1, 0, shift * i], [0, 1, 0]])
            f = cv2.warpAffine(f, M, (W, H), borderMode=cv2.BORDER_REFLECT)
        noise = rng.integers(-3, 4, f.shape)
        out.append(np.clip(f.astype(np.int16) + noise, 0, 255).astype(np.uint8))
    return out


def scene(rng):
    """A different camera: random big shapes."""
    g = np.full((H, W), int(rng.integers(40, 200)), np.uint8)
    for _ in range(8):
        x, y, r = int(rng.integers(0, W)), int(rng.integers(0, H)), int(rng.integers(10, 60))
        cv2.circle(g, (x, y), r, int(rng.integers(0, 255)), -1)
    return cv2.GaussianBlur(g, (5, 5), 0)


def with_insets(g, rng, top=0.62, xl=0.28, xr=0.74):
    f = g.copy()
    y0, x0, x1 = int(top * H), int(xl * W), int(xr * W)
    f[y0:, :x0] = scene(rng)[y0:, :x0]
    f[y0:, x1:] = scene(rng)[y0:, x1:]
    return f


def samples_wide(seed=1):
    rng = np.random.default_rng(seed)
    return [(10.0 * i, burst(field(rng), rng)) for i in range(6)]


def samples_stations(seed=2):
    rng = np.random.default_rng(seed)
    return [(10.0 * i, burst(with_insets(field(rng), rng), rng)) for i in range(6)]


def samples_broadcast(seed=3):
    rng = np.random.default_rng(seed)
    return [(10.0 * i, burst(field(rng) if i % 2 == 0 else scene(rng), rng)) for i in range(6)]


def samples_pan(seed=4):
    rng = np.random.default_rng(seed)
    return [(10.0 * i, burst(field(rng), rng, shift=3.0)) for i in range(6)]


# ---------------------------------------------------------------- classification
def test_classify_wide():
    f = VP.features(samples_wide())
    ty, conf, why = VP.classify(f)
    assert ty == "wide" and conf >= 0.5 and "fixed camera" in why, (f, why)


def test_classify_stations():
    f = VP.features(samples_stations())
    ty, conf, why = VP.classify(f)
    assert ty == "stations", (f, why)
    assert abs(f["inset_box"]["x_left"] - 0.28) < 0.02 and abs(f["inset_box"]["x_right"] - 0.74) < 0.02


def test_classify_broadcast_cuts_and_pans():
    ty, _, why = VP.classify(VP.features(samples_broadcast()))
    assert ty == "broadcast" and "another camera" in why
    ty, _, why = VP.classify(VP.features(samples_pan()))
    assert ty == "broadcast" and "moves" in why


def test_intro_outro_sample_is_not_a_cut():
    rng = np.random.default_rng(9)
    s = samples_wide(5)
    s[-1] = (60.0, burst(scene(rng), rng))                 # the outro animation in the last sample
    f = VP.features(s)
    ty, _, why = VP.classify(f)
    assert ty == "wide" and f["edge_odd"] == 1 and "outro" in why


def test_too_few_samples_is_unknown():
    s = samples_wide()[:2]
    assert VP.classify(VP.features(s))[0] is None
    assert VP.classify(dict(n=0))[0] is None


def test_classify_video_byte_ranges_only():
    """classify_video reads only byte ranges of the low-res stream (RangeFile over a fake HTTP server)."""
    import io
    import av
    rng = np.random.default_rng(7)
    buf = io.BytesIO()
    c = av.open(buf, "w", format="mp4")
    st = c.add_stream("libx264", rate=30)
    st.width, st.height, st.pix_fmt = W, H, "yuv420p"
    st.options = {"g": "30"}
    base = field(rng)
    for i in range(30 * 40):
        g = base if i % 15 else field(rng)
        fr = av.VideoFrame.from_ndarray(cv2.cvtColor(g, cv2.COLOR_GRAY2RGB), format="rgb24")
        for p in st.encode(fr): c.mux(p)
    for p in st.encode(): c.mux(p)
    c.close()
    data = buf.getvalue()

    class Resp:
        def __init__(self, a, b):
            self.body, self.status = data[a:b + 1], 206
            self.headers = {"Content-Range": f"bytes {a}-{min(b, len(data) - 1)}/{len(data)}"}
        def read(self, n=-1): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    calls = []
    def opener(req, timeout=None):
        a, b = (int(x) for x in req.headers["Range"].split("=")[1].split("-"))
        calls.append((a, b))
        return Resp(a, b)
    probe = lambda vid: (dict(id=vid, title="Wide Match 3", channel="X", duration=40.0, height=1080, width=1920),
                         ("https://93.184.216.34/v.mp4", 180, {}))
    rec = VP.classify_video("abcdefghijk", probe=probe, opener=opener)
    assert rec["type"] == "wide" and rec["probe"]["bytes"] <= len(data) and rec["hashes"] and len(calls) >= 1
    assert all(b - a < 200 * 1024 for a, b in calls)        # block-sized range requests


def test_rangefile_retries_and_counts():
    data = bytes(range(256)) * 4096                          # 1 MiB
    fails = {"n": 1}

    class Resp:
        def __init__(self, a, b): self.body = data[a:b + 1]; self.status = 206; self.headers = {"Content-Range": f"bytes {a}-{b}/{len(data)}"}
        def read(self, n=-1): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def opener(req, timeout=None):
        if fails["n"]:
            fails["n"] -= 1; raise OSError("reset")
        a, b = (int(x) for x in req.headers["Range"].split("=")[1].split("-"))
        return Resp(a, b)
    rf = VP.RangeFile("https://93.184.216.34/x", opener=opener, block=64 * 1024)
    rf.seek(300000)
    assert rf.read(10) == data[300000:300010]
    assert rf.errors == 1 and rf.size == len(data) and rf.fetched == 2 * 64 * 1024
    assert rf.seek(0, 65536) == len(data)


def test_rangefile_refuses_private_hosts(monkeypatch):
    monkeypatch.setattr(SRC, "_resolve", lambda h, p: ["10.0.0.5"])
    SRC._PUBLIC_OK.clear()
    with pytest.raises(ValueError):
        VP.RangeFile("https://lan.example.com/x", opener=lambda *a, **k: None)


def test_pick_format_prefers_small_avc_video_only():
    fs = [dict(url="u1", vcodec="avc1.64", acodec="none", height=1080), dict(url="u2", vcodec="vp9", acodec="none", height=240),
          dict(url="u3", vcodec="avc1.4d", acodec="none", height=360, http_headers={"User-Agent": "ua", "Cookie": "x"}),
          dict(url="u4", vcodec="avc1", acodec="mp4a", height=360), dict(url="u5", vcodec="avc1", acodec="none", height=144)]
    url, h, hdr = VP.pick_format(fs)
    assert (url, h) == ("u3", 360) and hdr == {"User-Agent": "ua"}           # never a cookie


# ---------------------------------------------------------------- titles
@pytest.mark.parametrize("title,key,ok", [
    ("Qualification 17 - PNW District Bonney Lake Event", "e_qm17", True),
    ("Qualification 17 - PNW District Bonney Lake Event", "e_qm18", False),
    ("Wide Match 3", "e_qm3", True), ("Match 9", "e_qm10", False), ("Match 9", "e_sf9m1", True),
    ("Playoff Match 10", "e_sf10m1", True), ("Playoff Match 10", "e_qm10", False),
    ("Match 5 (R2) - PNW District Bonney Lake Event", "e_sf5m1", True),
    ("Match 13 (R5) - X", "e_f1m1", None),                   # a playoff number vs a final: TBA knows better
    ("Final 2 - PNW District Bonney Lake Event", "e_f1m2", True), ("Finals 1", "e_f1m2", False),
    ("Final Tiebreaker - PNW District Bonney Lake Event", "e_f1m3", True),
    ("Upper Bracket - Round 2 - Match 7", "e_sf7m1", True), ("Semifinal 2 Match 1", "e_sf2m1", True),
    ("Robot reveal", "e_qm1", None)])
def test_title_parsing(title, key, ok):
    assert VP.same_match(title, key) is ok


def test_title_key_and_sources_match_key():
    keys = ["2026x_qm17", "2026x_sf5m1", "2026x_f1m3"]
    assert VP.title_key("Final Tiebreaker", "2026x", keys) == "2026x_f1m3"
    assert VP.title_key("Playoff Match 5", "2026x", keys) == "2026x_sf5m1"
    assert VP.title_key("Qualification 99", "2026x", keys) is None
    assert SRC.match_key_from_title("Match 5 (R2) - Event", "2026x", [{"key": k} for k in keys]) == "2026x_sf5m1"


# ---------------------------------------------------------------- dedupe + choice
def rec(vid, ty, title="", channel="FIRST Washington", dur=214.0, h=1080, hashes=None, dh=None, conf=0.9, times=None):
    return dict(id=vid, type=ty, title=title, channel=channel, duration=dur, height=h, conf=conf, classifier=VP.CLASSIFIER,
                hashes=hashes or ["0f0f0f0f0f0f0f0f"] * 6, dhashes=dh or ["00000000ffffffff"] * 5,
                times=times or [30.0, 60.0, 90.0, 120.0, 150.0, 180.0], at=1.0)


def test_duplicates():
    a = rec("aaaaaaaaaaa", "wide")
    b = rec("bbbbbbbbbbb", "wide", dur=214.6)
    c = rec("ccccccccccc", "wide", dh=["ffffffff00000000"] * 5)          # same field, different robots: not a copy
    d = rec("ddddddddddd", "wide", dur=230.0)
    e = rec("eeeeeeeeeee", "stations")
    assert VP.duplicates({x["id"]: x for x in (a, b, c, d, e)}) == {"bbbbbbbbbbb": "aaaaaaaaaaa"}


def test_choose_prefers_wide_then_stations_then_broadcast():
    recs = {"wwwwwwwwwww": rec("wwwwwwwwwww", "wide", "Wide Match 4", hashes=["1" * 16] * 6),
            "sssssssssss": rec("sssssssssss", "stations", "Match 4", hashes=["2" * 16] * 6),
            "bbbbbbbbbbb": rec("bbbbbbbbbbb", "broadcast", "Qualification 4 - E", "FIRSTRoboticsCompetition", hashes=["3" * 16] * 6)}
    recs["sssssssssss"]["features"] = {"inset_box": {"x_left": 0.28, "x_right": 0.74, "raw": 0.9}}
    info = {v: {"families": [], "via": ["tba"]} for v in recs}
    e = VP.choose("2026x_qm4", list(recs), recs, info)
    assert e["track"] == "wwwwwwwwwww" and e["track_type"] == "wide" and not e["limited"] and e["stations"] == "sssssssssss"
    e = VP.choose("2026x_qm4", ["sssssssssss", "bbbbbbbbbbb"], recs, info)
    assert e["track"] == "sssssssssss" and "insets" in e["limited"]
    e = VP.choose("2026x_qm4", ["bbbbbbbbbbb"], recs, info)
    assert e["track"] == "bbbbbbbbbbb" and "cuts" in e["limited"] and e["stations"] is None


def test_choose_excludes_wrong_title_overrides_and_disabled_families():
    recs = {"w1aaaaaaaaa": rec("w1aaaaaaaaa", "wide", "Wide Match 5"), "w2aaaaaaaaa": rec("w2aaaaaaaaa", "wide", "Wide Match 4", h=720),
            "bbbbbbbbbbb": rec("bbbbbbbbbbb", "broadcast", "Qualification 4")}
    info = {"w1aaaaaaaaa": {"families": ["fam-wide"], "via": ["tba"]}, "w2aaaaaaaaa": {"families": ["fam-wide"], "via": ["tba"]},
            "bbbbbbbbbbb": {"families": ["fam-q"], "via": ["tba"]}}
    e = VP.choose("2026x_qm4", list(recs), recs, info)
    w1 = next(c for c in e["candidates"] if c["id"] == "w1aaaaaaaaa")
    assert "another match" in w1["excluded"] and e["track"] == "w2aaaaaaaaa"
    assert "a linked video's title names another match" in e["problems"]
    e = VP.choose("2026x_qm4", list(recs), recs, info, ov={"exclude": ["w2aaaaaaaaa"]})
    assert e["track"] == "bbbbbbbbbbb" and e["limited"]
    e = VP.choose("2026x_qm4", list(recs), recs, info, ov={"pin": "bbbbbbbbbbb"})
    assert e["track"] == "bbbbbbbbbbb" and e["pinned"]
    e = VP.choose("2026x_qm4", list(recs), recs, info, disabled={"fam-wide"})
    assert e["track"] == "bbbbbbbbbbb"
    e = VP.choose("2026x_qm4", list(recs), recs, info, results={"w2aaaaaaaaa": "wrong_video"})
    assert e["track"] == "bbbbbbbbbbb"


def test_pending_videos_leave_the_match_to_the_fallback():
    e = VP.choose("2026x_qm4", ["zzzzzzzzzzz"], {}, {})
    assert e["track"] is None and e["pending"] == 1 and "videos not checked yet" in e["problems"]
    assert VP.plan_candidates(e, None) == []


def test_build_plan_summary_groups_and_overrides(tmp_path):
    ev = "2026x"
    ms = [dict(key=f"{ev}_qm{i}", alliances={"red": {"score": 10}}, videos=[{"type": "youtube", "key": f"w{i}aaaaaaaaa"[:11]},
                                                                            {"type": "youtube", "key": f"s{i}aaaaaaaaa"[:11]}])
          for i in (1, 2)] + [dict(key=f"{ev}_qm3", alliances={"red": {"score": -1}}, videos=[])]
    recs = {}
    for i in (1, 2):
        recs[f"w{i}aaaaaaaaa"[:11]] = rec(f"w{i}aaaaaaaaa"[:11], "wide" if i == 1 else "broadcast", f"Wide Match {i}", hashes=[str(i) * 16] * 6)
        recs[f"s{i}aaaaaaaaa"[:11]] = rec(f"s{i}aaaaaaaaa"[:11], "stations", f"Match {i}", hashes=[str(i + 4) * 16] * 6)
    doc = {"sources": [], "match_overrides": {f"{ev}_qm2": {"add": ["https://youtu.be/xxxxxxxxxxx"][:0] + ["xxxxxxxxxxx"]}}}
    plan = VP.build_plan(ev, str(tmp_path), ms, doc, recs, live={f"{ev}_qm2": {"clip": "media/live/c.mp4", "trackable": False}})
    s = plan["summary"]
    assert s["n"] == 2 and s["wide"] == 1 and s["stations"] == 2 and s["tracked_wide"] == 1 and s["limited"] == 1 and s["live"] == 1
    assert plan["matches"][f"{ev}_qm2"]["pending"] == 1                       # the admin-added video is a candidate
    assert {g["type"] for g in plan["groups"].values()} == {"wide", "stations"}
    g = plan["groups"][VP.group_id("wide", "FIRST Washington")]
    assert g["videos"] == {f"{ev}_qm1": "w1aaaaaaaaa"} and g["kind"] == "per_match"
    VP.save_plan(str(tmp_path), plan)
    assert SRC.find_source(str(tmp_path), g["id"])["type"] == "wide"            # layout / frame jobs find groups


def test_todo_orders_unprocessed_played_matches_first():
    ms = [dict(key="e_qm1", alliances={"red": {"score": 5}}, videos=[{"type": "youtube", "key": "a" * 11}]),
          dict(key="e_qm2", alliances={"red": {"score": 5}}, videos=[{"type": "youtube", "key": "b" * 11}]),
          dict(key="e_qm3", alliances={"red": {"score": None}}, videos=[{"type": "youtube", "key": "c" * 11}])]
    assert VP.todo(ms, {}, {}, done_keys={"e_qm1"}) == ["b" * 11, "a" * 11, "c" * 11]
    assert VP.todo(ms, {}, {"b" * 11: rec("b" * 11, "wide")}) == ["a" * 11, "c" * 11]


# ---------------------------------------------------------------- the worker honours the plan
def test_candidates_honours_plan(monkeypatch):
    recs = {"wwwwwwwwwww": rec("wwwwwwwwwww", "wide", "Wide Match 4"), "bbbbbbbbbbb": rec("bbbbbbbbbbb", "broadcast", "Match 4")}
    info = {v: {"families": [], "via": ["tba"]} for v in recs}
    entry = VP.choose("2026x_qm4", list(recs), recs, info)
    m = dict(key="2026x_qm4", comp_level="qm", match_number=4, set_number=1,
             videos=[{"type": "youtube", "key": "bbbbbbbbbbb"}, {"type": "youtube", "key": "wwwwwwwwwww"}])
    monkeypatch.setattr(VID, "meta", lambda vid: pytest.fail("the plan needs no metadata calls"))
    cache = {}
    got = VID.candidates(m, {"video_plan": {"2026x_qm4": entry}}, cache)
    assert [g["id"] for g in got] == ["wwwwwwwwwww", "bbbbbbbbbbb"] and got[0]["type"] == "wide" and got[1]["limited"]
    assert got[0]["group"] == VP.group_id("wide", "FIRST Washington") and "wwwwwwwwwww" in cache
    # no plan entry (or nothing checked yet): the title rules as before
    monkeypatch.setattr(VID, "meta", lambda vid: dict(id=vid, channel="FIRST Washington", title="Wide Match 4" if vid[0] == "w" else "Match 4",
                                                      duration=200, height=1080))
    got = VID.candidates(m, {"allow_channels": ["FIRST Washington"]}, {})
    assert [g["id"] for g in got] == ["wwwwwwwwwww"]


def test_find_inset_video_uses_plan(monkeypatch, tmp_path):
    import stationcam as SC
    m = dict(key="2026x_qm4", comp_level="qm", match_number=4, videos=[{"type": "youtube", "key": "sssssssssss"}])
    recs = {"sssssssssss": rec("sssssssssss", "stations", "Qualification 4 - E")}
    entry = VP.choose("2026x_qm4", list(recs), recs, {"sssssssssss": {"families": [], "via": ["tba"]}})
    v = SC.find_inset_video(m, {}, meta_fn=lambda v: pytest.fail("no metadata needed"), plan=entry)
    assert v["id"] == "sssssssssss"
    recs = {"sssssssssss": rec("sssssssssss", "wide", "Match 4")}
    entry = VP.choose("2026x_qm4", list(recs), recs, {})
    assert SC.find_inset_video(m, {}, meta_fn=lambda v: pytest.fail("no metadata needed"), plan=entry) is None
    # read from DATA/events/<ev>/video_plan.json when not given
    monkeypatch.setenv("DATA", str(tmp_path))
    os.makedirs(tmp_path / "events" / "2026x")
    recs = {"sssssssssss": rec("sssssssssss", "stations", "Match 4")}
    (tmp_path / "events" / "2026x" / "video_plan.json").write_text(json.dumps(
        {"matches": {"2026x_qm4": VP.choose("2026x_qm4", list(recs), recs, {})}}))
    assert SC.find_inset_video(m, {}, meta_fn=lambda v: pytest.fail("no metadata needed"))["id"] == "sssssssssss"


def test_layout_for_masks_detected_insets(tmp_path):
    cand = dict(id="sssssssssss", type="stations", group="auto-stations-x", plan=True,
                inset_box={"x_left": 0.28, "x_right": 0.74, "raw": 0.9})
    lay = VP.layout_for(str(tmp_path), cand)
    regs = lay["sources"]["auto-stations-x"]["regions"]
    ins = [r for r in regs if r["kind"] == "inset"]
    assert len(ins) == 2 and ins[0]["box"][0] == 0 and ins[0]["box"][2] == int(0.28 * 1920) + 8 and ins[1]["box"][2] == 1920
    assert any(r["kind"] == "clock" for r in regs)                              # the default graphics stay masked
    (tmp_path / "layout.json").write_text(json.dumps({"sources": {"auto-stations-x": {"W": 1920, "H": 1080, "regions": []}}}))
    assert VP.layout_for(str(tmp_path), cand)["sources"]["auto-stations-x"]["regions"] == []   # the drawn layout wins
    assert VP.layout_for(str(tmp_path / "none"), dict(cand, type="wide", group="g")) is None


def test_auto_discover_throttled(tmp_path):
    ev = "2026x"
    event = {"webcasts": [{"type": "youtube", "channel": "UCabcdefghijklmnopqrstuv"}, {"type": "twitch", "channel": "firstinspires"},
                          {"type": "livestream", "channel": "http://10.0.0.1/x"}], "start_date": "2000-01-01", "end_date": "2000-01-02"}
    ms = [dict(key=f"{ev}_qm1", videos=[{"type": "youtube", "key": "wwwwwwwwwww"}])]
    meta = lambda vid: dict(id=vid, title="Wide Match 1", channel="FIRST Washington")
    assert SRC.auto_discover(str(tmp_path), ev, event, ms, log=lambda *a: None, meta_fn=meta) is True
    doc = SRC.load(str(tmp_path / "events" / ev))
    urls = sorted(s.get("url") or "" for s in doc["sources"] if s["kind"] == "livestream")
    assert urls == ["https://www.twitch.tv/firstinspires", "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live"]
    assert any(s["kind"] == "per_match" and s["role"] == "wide" for s in doc["sources"])
    assert SRC.auto_discover(str(tmp_path), ev, event, ms, log=lambda *a: None, meta_fn=meta) is False    # fresh: skipped


# ---------------------------------------------------------------- livestream: today's webcasts, channel -> live, dedupe
def test_live_sources_per_day_and_implicit_webcasts():
    doc = {"sources": [
        {"id": "w0", "kind": "livestream", "url": "https://www.youtube.com/watch?v=day1aaaaaaa", "webcast": {"date": "2026-03-07"}, "role": "wide", "primary": True},
        {"id": "w1", "kind": "livestream", "url": "https://www.youtube.com/watch?v=day2aaaaaaa", "webcast": {"date": "2026-03-08"}},
        {"id": "w2", "kind": "livestream", "url": "https://www.youtube.com/watch?v=day2bbbbbbb", "webcast": {"date": "2026-03-08"}}]}
    assert [s["id"] for s in LI.live_sources(doc, {"2026-03-08"})] == ["w1", "w2"]          # day 2: never yesterday's stream
    assert LI.pick_source(doc, {"2026-03-07"})["id"] == "w0"
    assert [s["id"] for s in LI.live_sources(doc, {"2026-03-09"})] == ["w0", "w1", "w2"]    # no webcast today: all
    ev = {"webcasts": [{"type": "youtube", "channel": "@FIRSTWashington"}, {"type": "dacast", "channel": "x"}]}
    got = LI.live_sources({"sources": []}, {"2026-03-08"}, ev)
    assert [s["url"] for s in got] == ["https://www.youtube.com/@FIRSTWashington/live"] and got[0]["auto"]


def test_recorder_resolves_channel_live_and_dedupes(monkeypatch, tmp_path):
    resolved = {"https://www.youtube.com/@FIRSTWashington/live": ("LIVEvideo01", "is_live"),
                "https://www.youtube.com/watch?v=LIVEvideo01": ("LIVEvideo01", "is_live"),
                "https://www.youtube.com/watch?v=upcomingvid": ("upcomingvid", "is_upcoming")}
    def fake_resolve(url, timeout=90):
        if url not in resolved: raise RuntimeError("This channel is not currently live")
        return resolved[url]
    monkeypatch.setattr(LI, "resolve", fake_resolve)
    svc = LI.Service(str(tmp_path), log=lambda *a: None)
    srcs = [dict(id="a", url="https://www.youtube.com/watch?v=upcomingvid"),
            dict(id="b", url="https://www.youtube.com/watch?v=LIVEvideo01", auto=True),
            dict(id="c", url="https://www.youtube.com/@FIRSTWashington/live"),
            dict(id="d", url="https://www.youtube.com/@other/live")]
    es = {}
    src, vid, ls, err = svc.choose("2026x", srcs, es)
    assert (src["id"], vid, ls) == ("b", "LIVEvideo01", "is_live")                 # live beats upcoming
    cs = {c["id"]: c for c in es["candidates"]}
    assert cs["c"]["same_as"] == "b" and "error" in cs["d"] and cs["b"]["auto"]   # the channel URL is the same stream
    resolved.pop("https://www.youtube.com/watch?v=LIVEvideo01")
    resolved["https://www.youtube.com/@FIRSTWashington/live"] = ("NEXTday0001", "is_live")   # next day: a new stream
    src, vid, ls, err = svc.choose("2026x", srcs, {})
    assert (src["id"], vid) == ("c", "NEXTday0001")
    src, vid, ls, err = svc.choose("2026x", srcs[3:], {})
    assert vid is None and "not currently live" in err


def test_resolve_uses_ytdlp_ipv4_and_double_dash(monkeypatch):
    seen = {}
    class R:
        returncode, stdout, stderr = 0, "LIVEvideo01\tis_live\n", ""
    def run(cmd, **kw):
        seen["cmd"] = cmd; return R()
    monkeypatch.setattr(LI.subprocess, "run", run)
    monkeypatch.setattr(SRC, "_resolve", lambda h, p: ["142.250.69.206"])
    SRC._PUBLIC_OK.clear()
    assert LI.resolve("https://www.youtube.com/@FIRSTWashington/live") == ("LIVEvideo01", "is_live")
    assert "-4" in seen["cmd"] and seen["cmd"][-2] == "--"


def test_webcast_url_channel_id():
    assert SRC.webcast_url({"type": "youtube", "channel": "UCabcdefghijklmnopqrstuv"}) == \
        "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live"
    assert SRC.webcast_url({"type": "youtube", "channel": "UCshort"}) is None


def test_channel_scan_pairs_with_tba_siblings(tmp_path):
    """Unlinked "Wide Match N" uploads count only next to the TBA-linked upload of the same match (same channel)."""
    ev = "2026x"
    chan = "https://www.youtube.com/channel/UCGlwi9YkxGx0zZh8kaETJ9A"
    ms = [dict(key=f"{ev}_qm{i}", videos=[{"type": "youtube", "key": f"m{i:02d}aaaaaaaa"}]) for i in (1, 2, 3)]
    recs = {f"m{i:02d}aaaaaaaa": dict(id=f"m{i:02d}aaaaaaaa", channel_url=chan, type="stations") for i in (1, 2, 3)}
    listing = [("w03aaaaaaaa", "Wide Match 3"), ("m03aaaaaaaa", "Match 3"), ("w02aaaaaaaa", "Wide Match 2"), ("m02aaaaaaaa", "Match 2"),
               ("o01aaaaaaaa", "Wide Match 1"),            # another event's "Wide Match 1", far from our Match 1
               ("x1aaaaaaaaa", "Opening Ceremonies"), ("x2aaaaaaaaa", "Practice Wide Match 1"), ("p1aaaaaaaaa", "Wide Playoff Match 1"),
               ("f1aaaaaaaaa", "Filler"), ("f2aaaaaaaaa", "Filler"), ("f3aaaaaaaaa", "Filler"), ("f4aaaaaaaaa", "Filler"),
               ("m01aaaaaaaa", "Match 1"), ("w01aaaaaaaa", "Wide Match 1")]
    calls = []
    def fake_list(url, end):
        calls.append((url, end)); return listing[:end]
    got = VP.scan_channels(str(tmp_path), ev, ms, recs, list_fn=fake_list, log=lambda *a: None, max_age=0)
    # two "Wide Match 1" in the span (events uploaded interleaved): only the one next to our Match 1 counts
    assert got == {f"{ev}_qm3": ["w03aaaaaaaa"], f"{ev}_qm2": ["w02aaaaaaaa"], f"{ev}_qm1": ["w01aaaaaaaa"]}
    assert calls[0][0] == chan
    assert VP.load_scan(str(tmp_path / "events" / ev)) == got
    assert VP.scan_channels(str(tmp_path), ev, ms, recs, list_fn=lambda *a: pytest.fail("cached"), max_age=600) == got
    # FIRST HQ style titles carry the event: another event's upload in between never counts
    recs2 = {k: dict(v, title=f"Qualification {int(k[1:3])} - PNW District X Event") for k, v in recs.items()}
    listing2 = [("q03aaaaaaaa", "Qualification 3 - CA District Los Angeles Event"), ("m03aaaaaaaa", "Qualification 3 - PNW District X Event"),
                ("q02aaaaaaaa", "Qualification 2 - PNW District X Event"), ("m01aaaaaaaa", "Qualification 1 - PNW District X Event")]
    ms2 = [dict(m, videos=m["videos"] if m["key"] != f"{ev}_qm2" else []) for m in ms]
    got2 = VP.scan_channels(str(tmp_path), ev, ms2, recs2, list_fn=lambda u, e: listing2, log=lambda *a: None, max_age=0)
    assert got2 == {f"{ev}_qm2": ["q02aaaaaaaa"]}
    VP.scan_channels(str(tmp_path), ev, ms, recs, list_fn=fake_list, log=lambda *a: None, max_age=0)
    cands, info = VP.gather(ms, {}, got)
    assert cands[f"{ev}_qm3"] == ["m03aaaaaaaa", "w03aaaaaaaa"] and info["w03aaaaaaaa"]["via"] == ["channel"]


def test_rangefile_refreshes_url_after_403():
    import urllib.error
    data = bytes(200) * 1000
    state = {"bad": True, "refreshed": 0}

    class Resp:
        def __init__(self, a, b): self.body = data[a:b + 1]; self.status = 206; self.headers = {"Content-Range": f"bytes {a}-{b}/{len(data)}"}
        def read(self, n=-1): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def opener(req, timeout=None):
        if "old" in req.full_url: raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        a, b = (int(x) for x in req.headers["Range"].split("=")[1].split("-"))
        return Resp(a, b)
    def refresh():
        state["refreshed"] += 1; return "https://93.184.216.34/new", {"User-Agent": "ua"}
    rf = VP.RangeFile("https://93.184.216.34/old", opener=opener, refresh=refresh, block=50000)
    assert rf.read(10) == data[:10] and state["refreshed"] == 1 and rf.url.endswith("/new")


def test_refine_ambiguous_insets_by_event_layout_and_feed():
    def r(vid, ty, title, raw, xl, xr, ch="FIRST Washington"):
        return dict(id=vid, type=ty, title=title, channel=ch, features={"inset_box": {"x_left": xl, "x_right": xr, "raw": raw}})
    recs = {f"s{i}": r(f"s{i}", "stations", f"Match {i}", 0.9, 0.32, 0.69) for i in range(5)}
    recs.update({f"w{i}": r(f"w{i}", "wide", f"Wide Match {i}", 0.45, 0.22, 0.57) for i in range(5)})
    recs["amb_w"] = r("amb_w", "stations", "Wide Match 8", 0.67, 0.225, 0.569)        # wide video, a field post looks like a border
    recs["amb_s"] = r("amb_s", "wide", "Match 20", 0.70, 0.321, 0.692)                 # station-cam video, weak borders
    recs["amb_f"] = r("amb_f", "wide", "Match 24", 0.70, 0.19, 0.83)                   # off position, but its feed has insets
    out = VP.refine(recs)
    assert out["amb_w"]["type"] == "wide" and out["amb_s"]["type"] == "stations" and out["amb_f"]["type"] == "stations"
    assert out["s1"] is recs["s1"] and out["amb_w"]["refined"]
