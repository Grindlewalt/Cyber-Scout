"""goals.site_build in snapshot mode (the worker's --selection-snapshots pass, after the live build) writes only the dated
selection snapshot, never the live rankings.json / schedule.json: an event without a selection projection used to fall
through and overwrite them with the last snapshot's (2026wamil: "as of 12:30" at 13:42, match-plan links gone)."""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.strategy import goals as G


def _fake(sel):
    def build(*a, **k):
        rk = {"kind": "rankings", "build_s": 0.1}
        if sel is not None:
            rk["_selection"] = sel
        return rk, {"kind": "schedule", "asof": a[6] if len(a) > 6 else None}
    return build


def test_snapshot_never_overwrites_live_files(tmp_path, monkeypatch):
    out = tmp_path / "api"; out.mkdir()
    (out / "schedule.json").write_text(json.dumps({"live": True}))
    for sel in (None, {"kind": "selection"}):
        monkeypatch.setattr(G, "build_event_json", _fake(sel))
        G.site_build(str(tmp_path), "2026x", out_dir=str(out), asof=1000, snapshot_only=True)
        assert json.loads((out / "schedule.json").read_text()) == {"live": True}
        assert not (out / "rankings.json").exists()
    assert json.loads((out / "selection" / "1000.json").read_text()) == {"kind": "selection"}


def test_live_build_still_writes(tmp_path, monkeypatch):
    out = tmp_path / "api"; out.mkdir()
    monkeypatch.setattr(G, "build_event_json", _fake(None))
    G.site_build(str(tmp_path), "2026x", out_dir=str(out))
    assert json.loads((out / "schedule.json").read_text())["kind"] == "schedule" and (out / "rankings.json").exists()
