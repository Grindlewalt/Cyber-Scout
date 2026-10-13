"""goals.py feeds the dashboard blend into the season simulation in the same unit as the district ratings (points per
robot per match), not a third of it (bug until 2026-10: event teams ran at ~1/3 strength in the DCMP odds)."""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.strategy import goals as G  # noqa: E402


def test_blend_theta_is_per_robot_unscaled():
    assert G.blend_theta({"total": 85.6, "sd": 7.6}) == (85.6, 7.6)
    assert G.blend_theta({"total": 40.0, "sd": 1.0}) == (40.0, 3.0)     # sd floor
    assert G.blend_theta({"total": 40.0}) == (40.0, 6.0)                 # missing sd


class _Captured(Exception):
    pass


def test_build_event_json_passes_blend_totals_to_season(tmp_path, monkeypatch):
    api = tmp_path / "site" / "api" / "2026xx"
    api.mkdir(parents=True)
    dash = {"ratings": [{"team": "frc4911", "blend": {"total": 85.6, "sd": 7.6}},
                        {"team": "frc1983", "blend": {"total": 26.7, "sd": 7.1}},
                        {"team": "frc9999", "blend": {}}]}
    (api / "dashboard.json").write_text(json.dumps(dash))
    seen = {}

    def fake_season(D, n=800, seed=0, ratings=None, **kw):
        seen.update(ratings or {})
        raise _Captured()

    monkeypatch.setattr(G, "district_key_of", lambda d, ev: "2026pnw")
    monkeypatch.setattr(G.DI, "load_district", lambda *a, **k: {"asof": None})
    monkeypatch.setattr(G, "Season", fake_season)
    ctx = types.SimpleNamespace(E={"event": "2026xx", "teams": [], "tba": []})
    with pytest.raises(_Captured):
        G.build_event_json(str(tmp_path), "2026xx", team="frc4911", ctx=ctx, with_matches=False, offline=True)
    assert seen == {"frc4911": (85.6, 7.6), "frc1983": (26.7, 7.1)}
