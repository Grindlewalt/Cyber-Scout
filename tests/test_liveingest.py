"""Livestream ingest -> worker: record selection, trackability, fast results, live-first processing.
Pure file fixtures (no video, no network)."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import liveingest as LI  # noqa: E402

EV = "2026test"


def rec(i, lv="qm", n=1, s=1, T=0.0, flags=(), teams=None, score=None, clip=True, auto=8.5, **kw):
    r = dict(id=i, comp_level=lv, set=s, number=n, match_name=f"Qualification {n}",
             teams=teams or {"red": [1, 2, 3], "blue": [4, 5, 6]}, score=score or {"red": 10, "blue": 20},
             score_source="results_screen", flags=list(flags), t_start_stream=T,
             t_start_utc=f"2026-10-10T18:{int(T) // 60 % 60:02d}:{int(T) % 60:02d}.00Z",
             clip=f"/nowhere/clips/{i}.mp4" if clip else None, auto_start_in_clip=auto)
    r.update(kw)
    return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, media = tmp_path / "data", tmp_path / "media"
    live = media / "live"
    for d in (data / "events" / EV, live):
        d.mkdir(parents=True)
    monkeypatch.setenv("DATA", str(data))
    monkeypatch.setenv("MEDIA", str(media))
    monkeypatch.delenv("LIVE_DIR", raising=False)
    monkeypatch.delenv("LAYOUT", raising=False)
    return dict(data=data, media=media, live=live)


def write_dir(env, vid, recs, source="url-abc", clips=True):
    d = env["live"] / EV / vid
    (d / "matches").mkdir(parents=True)
    (d / "clips").mkdir()
    (d / "meta.json").write_text(json.dumps(dict(event=EV, source=source, video_id=vid, started=1.0)))
    for r in recs:
        (d / "matches" / f"{r['id']}.json").write_text(json.dumps(r))
        if clips and r.get("clip"):
            (d / "clips" / os.path.basename(r["clip"])).write_bytes(b"x")
    return d


def tba_match(n, teams=True, score=-1):
    tk = (lambda a: [f"frc{x}" for x in ((1, 2, 3) if a == "red" else (4, 5, 6))]) if teams else (lambda a: [])
    return dict(key=f"{EV}_qm{n}", comp_level="qm", set_number=1, match_number=n, time=1000 + n,
                alliances={a: dict(team_keys=tk(a), score=score) for a in ("red", "blue")}, videos=[])


def test_record_keys():
    assert LI.record_key(EV, rec("qm7", n=7)) == f"{EV}_qm7"
    assert LI.record_key(EV, rec("sf4m1", lv="sf", s=4, n=1)) == f"{EV}_sf4m1"
    assert LI.record_key(EV, rec("f1m2", lv="f", n=2)) == f"{EV}_f1m2"
    assert LI.record_key(EV, rec("pm1", lv="pm")) is None
    assert LI.record_key(EV, dict(id="unknown")) is None


def test_latest_valid_record_wins(env):
    write_dir(env, "vidA", [
        rec("qm1", T=100),
        rec("qm1_2", T=400, flags=["same_name_as_qm1", "possible_replay_or_rerun"]),   # replay: official
        rec("qm1_3", T=700, flags=["aborted_at_21s"]),                                   # later, but aborted
        rec("qm2", n=2, T=900, not_a_match=True),
        rec("qm3", n=3, T=1200, flags=["zero_score_no_auto", "no_results_screen"]),
        rec("qm4", n=4, T=1500, auto=-130.0),                                           # bad clip offset
    ])
    lv = LI.LiveView(EV)
    assert lv.fast[f"{EV}_qm1"]["id"] == "qm1_2"
    assert lv.clips[f"{EV}_qm1"]["id"] == "qm1_2"
    assert f"{EV}_qm2" not in lv.fast and f"{EV}_qm3" not in lv.fast
    assert lv.fast[f"{EV}_qm4"]["id"] == "qm4" and f"{EV}_qm4" not in lv.clips   # fast result, no tracking
    # the clip path in the record is stale: found in the record's own clips/ dir
    assert LI.clip_of(lv.clips[f"{EV}_qm1"]).endswith(f"{EV}/vidA/clips/qm1_2.mp4")


def test_trackable_needs_field_box_and_calibration(env):
    evd = env["data"] / "events" / EV
    assert LI.trackable(EV, "url-abc")["ok"] is False                 # no layout at all
    lay = {"version": 1, "sources": {"url-abc": {"W": 1920, "H": 1080, "role": "dynamic",
                                                 "regions": [{"kind": "clock", "box": [912, 72, 1008, 116]}]}}}
    (evd / "layout.json").write_text(json.dumps(lay))
    t = LI.trackable(EV, "url-abc")
    assert not t["ok"] and "field" in t["reason"]
    lay["sources"]["url-abc"]["regions"].append({"kind": "field", "box": [0, 150, 1920, 1080]})
    (evd / "layout.json").write_text(json.dumps(lay))
    t = LI.trackable(EV, "url-abc")
    assert not t["ok"] and "calibration" in t["reason"]
    (evd / "camera.json").write_text(json.dumps({"source": "tba-uploads-wide"}))      # another camera
    assert not LI.trackable(EV, "url-abc")["ok"]
    (evd / "camera.json").write_text(json.dumps({"source": "url-abc"}))               # wizard on the stream
    assert LI.trackable(EV, "url-abc")["camera"].endswith("camera.json")
    (evd / "camera_url-abc.json").write_text("{}")                                    # per-source file wins
    t = LI.trackable(EV, "url-abc")
    assert t["ok"] and t["camera"].endswith("camera_url-abc.json")
    assert t["layout"]["processing_source"] == "url-abc" and list(t["layout"]["sources"]) == ["url-abc"]


def _trackable_event(env):
    evd = env["data"] / "events" / EV
    (evd / "layout.json").write_text(json.dumps({"sources": {"url-abc": {"W": 1920, "H": 1080, "regions": [
        {"kind": "field", "box": [0, 150, 1920, 1080]}]}}}))
    (evd / "camera_url-abc.json").write_text("{}")


def test_for_processing_teams_and_lineup_check(env):
    _trackable_event(env)
    write_dir(env, "vidA", [rec("qm1", T=100), rec("qm2", n=2, T=400, teams={"red": [1, 2, 3], "blue": [4, 5, None]}),
                            rec("qm3", n=3, T=700, teams={"red": [7, 8, 9], "blue": [10, 11, 12]})])
    lv = LI.LiveView(EV)
    a = lv.for_processing(tba_match(1), False)
    assert a["teams_from"] == "tba" and a["start"] == 8.5 and a["camera"].endswith("camera_url-abc.json")
    assert a["info"]["tba_posted"] is False and a["video"]["id"].startswith("live-vidA-")
    b = lv.for_processing(tba_match(1, teams=False), False)                  # TBA has no lineup yet
    assert b["teams_from"] == "stream" and b["teams"] == {"red": ["frc1", "frc2", "frc3"], "blue": ["frc4", "frc5", "frc6"]}
    assert lv.for_processing(tba_match(2, teams=False), False) is None       # a team slot unread, no TBA lineup
    assert lv.for_processing(tba_match(2), False)["teams_from"] == "tba"     # 5/6 agree with TBA: fine
    assert lv.for_processing(tba_match(3), False) is None                    # stream says another lineup


def test_fast_results_rows_and_synth(env):
    write_dir(env, "vidA", [rec("qm1", T=100, score={"red": 50, "blue": 40}),
                            rec("sf2m1", lv="sf", s=2, T=400, score={"red": 1, "blue": 9})])
    lv = LI.LiveView(EV)
    m = tba_match(1)
    row = dict(red=dict(teams=m["alliances"]["red"]["team_keys"], score=-1),
               blue=dict(teams=m["alliances"]["blue"]["team_keys"], score=-1), winner=None, state="pending")
    lv.decorate_row(row, m)
    assert row["red"]["score"] == 50 and row["blue"]["score"] == 40 and row["winner"] == "red"
    assert row["state"] == "live" and row["score_source"] == "livestream"
    assert row["live"]["tracking"] == "uploads" and row["live"]["clip"] == f"media/live/{EV}/vidA/clips/qm1.mp4"
    extra = lv.extra_matches({m["key"]})
    assert [x["key"] for x in extra] == [f"{EV}_sf2m1"]
    sm = extra[0]
    assert sm["livestream"] and not LI.posted(sm) and sm["set_number"] == 2 and sm["match_number"] == 1
    assert sm["alliances"]["blue"]["team_keys"] == ["frc4", "frc5", "frc6"] and sm["winning_alliance"] == "blue"
    posted = tba_match(1, score=55)
    row2 = dict(red=dict(teams=[], score=55), blue=dict(teams=[], score=41), winner="red", state="done")
    lv.decorate_row(row2, posted)
    assert row2["red"]["score"] == 55 and row2["score_source"] == "tba" and row2["state"] == "done"


def test_pick_source():
    doc = {"sources": [
        dict(id="tba-webcast-0", kind="livestream", url="u0", origin="tba", webcast={"date": "2026-10-10"}),
        dict(id="tba-webcast-1", kind="livestream", url="u1", origin="tba", webcast={"date": "2026-10-11"}),
        dict(id="up", kind="per_match", url="p", primary=True)]}
    assert LI.pick_source(doc, {"2026-10-11"})["id"] == "tba-webcast-1"
    assert LI.pick_source(doc, {"2026-10-12"})["id"] == "tba-webcast-0"
    doc["sources"].append(dict(id="url-x", kind="livestream", url="ux", origin="admin", role="wide"))
    assert LI.pick_source(doc, {"2026-10-11"})["id"] == "url-x"
    doc["sources"][-1]["enabled"] = False
    doc["sources"][1]["enabled"] = False
    assert LI.pick_source(doc, {"2026-10-11"})["id"] == "tba-webcast-0"
    assert LI.pick_source({"sources": []}) is None


def test_process_prefers_trusted_livestream_start():
    import process as P
    assert P.pick_start(9.0, 5.0, True, 8.5, True) == (9.0, True, "clock")           # clock still first
    assert P.pick_start(None, 5.0, True, 8.5, True) == (8.5, True, "livestream")     # measured start > motion
    assert P.pick_start(None, 5.0, True, 8.5, False) == (5.0, True, "motion")        # uploads: unchanged
    assert P.pick_start(None, 6.5, False, None, True) == (6.5, False, "default")
    assert P.pick_start(None, 5.0, True, -120.0, True)[2] == "motion"                # bad offset ignored


class FakeTBA:
    def __init__(self, ms): self.ms = ms
    def matches(self, ev): return self.ms


def test_worker_sync_processes_live_clips_before_tba(env, monkeypatch):
    import worker as W
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    _trackable_event(env)
    write_dir(env, "vidA", [rec("qm1", T=100), rec("qm2", n=2, T=400)])
    calls = []

    def fake_pm(ev, m, cfg, status, meta_cache, live=None):
        calls.append((m["key"], live and live["teams_from"], live and live["tba_posted"]))
        return False
    monkeypatch.setattr(W, "process_match", fake_pm)
    monkeypatch.setattr(W, "build_site", lambda ev, tba: None)
    # qm1 scheduled (lineup known, not posted), qm2 not posted, qm3 posted but no clip and no upload camera
    ms = [tba_match(1), tba_match(2, teams=False), tba_match(3, score=12)]
    W.sync(EV, FakeTBA(ms))
    assert calls == [(f"{EV}_qm1", "tba", False), (f"{EV}_qm2", "stream", False)]
    # a rejected record is not offered again; uploads (camera.json) path stays as before
    st = {f"{EV}_qm1": {"live_rejected": str(env["live"] / EV / "vidA" / "matches" / "qm1.json")}}
    (env["data"] / "events" / EV / "status.json").write_text(json.dumps(st))
    calls.clear()
    W.sync(EV, FakeTBA(ms))
    assert calls == [(f"{EV}_qm2", "stream", False)]


def test_worker_sync_untrackable_stream_waits_for_uploads(env, monkeypatch):
    import worker as W
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    write_dir(env, "vidA", [rec("qm1", T=100)])                  # no layout / calibration for the stream
    calls = []
    monkeypatch.setattr(W, "process_match", lambda ev, m, *a, live=None: calls.append((m["key"], live)) or False)
    monkeypatch.setattr(W, "build_site", lambda ev, tba: None)
    W.sync(EV, FakeTBA([tba_match(1)]))
    assert calls == []                                          # fast results only (site), no tracking yet
    (env["data"] / "events" / EV / "camera.json").write_text("{}")
    W.sync(EV, FakeTBA([tba_match(1, score=30)]))
    assert calls == [(f"{EV}_qm1", None)]                       # TBA posted: the upload path, as before


def test_service_status_files(env, monkeypatch):
    """The service writes a status for a followed event with a livestream source (no network: resolve fails)."""
    evd = env["data"] / "events" / EV
    (env["data"] / "config").mkdir()
    (env["data"] / "config" / "worker.json").write_text(json.dumps({"events": [EV]}))
    (evd / "sources.json").write_text(json.dumps({"sources": [dict(id="url-abc", kind="livestream", url="https://x/live",
                                                                   origin="admin", enabled=True)]}))
    write_dir(env, "vidA", [rec("qm1", T=100)])
    monkeypatch.setenv("LIVE_ANY_DATE", "1")
    monkeypatch.setattr(LI, "resolve", lambda url, timeout=90: (_ for _ in ()).throw(RuntimeError("not live")))
    s = LI.Service(str(env["data"]), log=lambda *a: None)
    s.step()
    st = json.load(open(evd / "livestream.json"))
    assert st["state"] == "waiting" and "not live" in st["message"] and st["source"]["id"] == "url-abc"
    assert st["tracking"]["ok"] is False
    monkeypatch.setattr(LI, "resolve", lambda url, timeout=90: ("vidA", "was_live"))
    s.ev_state[EV]["next_try"] = 0
    s.step()
    st = json.load(open(evd / "livestream.json"))
    assert st["state"] == "stream_over" and st["video_id"] == "vidA"
    assert st["last_match"]["key"] == f"{EV}_qm1" and st["matches"]["n"] == 1
