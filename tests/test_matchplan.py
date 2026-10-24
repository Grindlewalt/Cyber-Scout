import json
import os
import tempfile
import unittest
from unittest import mock

from live.strategy import matchplan as MP
from tests.test_behavior import rec


def make_event(d):
    site = os.path.join(d, "site", "api", "ev")
    os.makedirs(os.path.join(site, "m"))
    rows = []
    for i in range(1, 5):
        rows.append(dict(key=f"ev_qm{i}", short=f"Q{i}", label=f"Qual {i}", time=1000 * i, played=i < 4,
                         red=["frc1", "frc2", "frc3"], blue=["frc4", "frc5", "frc6"]))
    json.dump(dict(matches=rows), open(os.path.join(site, "schedule.json"), "w"))
    return site


class MatchPlanTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(MP, "_load_sb", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_leave_future_out_and_empty(self):
        with tempfile.TemporaryDirectory() as d:
            site = make_event(d)
            st = MP.build(d, "ev")
            self.assertEqual(st["matches"], 4)
            m = json.load(open(os.path.join(site, "mp", "ev_qm1.json")))
            self.assertFalse(m["tracked"])
            self.assertFalse(m["alliances"]["red"]["enough"])
            self.assertEqual(len(m["alliances"]["red"]["missing"]), 3)
            idx = json.load(open(os.path.join(site, "mp", "index.json")))
            self.assertEqual(len(idx["matches"]), 4)
            sch = json.load(open(os.path.join(site, "schedule.json")))
            self.assertTrue(all(r["mp"] for r in sch["matches"]))

    def test_finish_win_probability(self):
        z = {p: 0.0 for p in MP.PERIODS}
        hi = dict(z, auto=20.0, shift2=30.0)
        v = {p: 4.0 for p in MP.PERIODS}
        a, b = MP._finish(hi, v, z, v, 0, 0)
        self.assertGreater(a["win_p"], 0.9)
        self.assertAlmostEqual(a["win_p"] + b["win_p"], 1.0, 2)

def _sb(teams, t=100):
    """Minimal Statbotics payload: one finished match per team with a post-match EPA breakdown."""
    tm = {k: [dict(team=k, time=t, status="Completed",
                   epa=dict(post=40.0, post_breakdown=dict(total_points=40.0, auto_points=a, teleop_points=te, endgame_points=en)))]
          for k, (a, te, en) in teams.items()}
    return dict(team_events={}, team_matches=tm, info={}, ok=True)


class StandInTests(unittest.TestCase):
    def _plan(self, sb):
        with tempfile.TemporaryDirectory() as d:
            site = make_event(d)
            with mock.patch.object(MP, "_load_sb", return_value=sb):
                MP.build(d, "ev")
            return json.load(open(os.path.join(site, "mp", "ev_qm4.json")))

    def test_statbotics_stand_in_used(self):
        m = self._plan(_sb({"frc1": (12.0, 24.0, 6.0)}))
        r = {x["team"]: x for x in m["robots"]}
        self.assertTrue(r["frc1"]["stand_in"])
        self.assertEqual(r["frc1"]["stand_in_src"], "statbotics")
        self.assertIn("Statbotics EPA (no tracked matches here)", r["frc1"]["auto"]["basis"])
        self.assertEqual(r["frc1"]["auto"]["pts"]["mean"], 12.0)
        self.assertEqual(r["frc1"]["epa"]["tower"], 6.0)
        self.assertNotIn("stand_in_src", r["frc2"])                       # Statbotics has nothing on frc2
        red = m["alliances"]["red"]
        self.assertFalse(red["enough"])                                    # an estimate stays yellow
        self.assertEqual(len(red["missing"]), 3)
        self.assertIn("Statbotics", red["pred"]["partial"])
        empty = self._plan(None)["alliances"]["red"]["pred"]
        self.assertIsNone(empty["score"]["mean"])                          # nothing to go on: unknown, not 0
        self.assertTrue(empty["unknown"])
        self.assertGreater(red["pred"]["score"]["mean"], 0)                # frc1's EPA gives an estimate

    def test_event_average_when_statbotics_has_nothing(self):
        m = self._plan(None)
        self.assertTrue(all("stand_in_src" not in r for r in m["robots"]))   # no tracked data at all either
        stand = {p: (2.0, 1.0) for p in MP.PERIODS}
        scale = {p: 1.0 for p in MP.PERIODS}
        o = MP._robot_out("frc9", "red", 1, None, 0, None, stand, scale, None, None)
        self.assertEqual(o["stand_in_src"], "event_average")
        self.assertIn("event average", o["auto"]["basis"])

    def test_cache_used_when_api_fails(self):
        import urllib.error
        from live.strategy import epa
        with tempfile.TemporaryDirectory() as d:
            cdir = os.path.join(d, "cache", "statbotics")
            os.makedirs(cdir)
            te = [dict(team=1, epa=dict(total_points=dict(mean=30.0), breakdown=dict(auto_points=9.0, teleop_points=18.0, endgame_points=3.0)))]
            json.dump(dict(fetched=1.0, url="x", data=te), open(epa._cache_path(cdir, "team_events_ev"), "w"))
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("503")):
                sb = MP._load_sb(d, "ev")
            self.assertIsNotNone(sb)                                         # stale cache beats an outage
            self.assertEqual(sb["team_events"]["frc1"]["team"], 1)
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("503")):
                self.assertIsNone(MP._load_sb(os.path.join(d, "empty"), "ev"))   # no cache, no API: None, no exception

    def test_default_spots_and_stations(self):
        r = [MP.default_spot("red", s) for s in (1, 2, 3)]
        b = [MP.default_spot("blue", s) for s in (1, 2, 3)]
        self.assertEqual([x[1] for x in r], [0.75, round(MP.WID / 2, 2), round(MP.WID - 0.75, 2)])
        self.assertTrue(all(x[0] > 12.513 for x in r) and all(x[0] < 4.028 for x in b))   # inside the alliance starting areas
        self.assertTrue(r[0][1] < r[1][1] < r[2][1])                                      # red station 1 = low y = driver's left
        self.assertTrue(b[0][1] > b[1][1] > b[2][1])                                      # blue faces the other way
        self.assertAlmostEqual(MP.driver_station("red", 1)["xy"][0], MP.LEN, 1)
        self.assertEqual(MP.driver_station("blue", 3)["xy"][0], 0.0)
        ys = [MP.driver_station("blue", s)["xy"][1] for s in (1, 2, 3)]
        self.assertTrue(ys[0] > ys[1] > ys[2])

    def test_default_start_in_plan(self):
        m = self._plan(None)
        for x in m["robots"]:
            self.assertIn(x["station"], (1, 2, 3))
            self.assertTrue(x["start"]["stand_in"] and x["start"]["default"])
            self.assertEqual(x["start"]["xy"], MP.default_spot(x["alliance"], x["station"]))
            self.assertEqual(x["ds"], MP.driver_station(x["alliance"], x["station"]))
        s1 = [x for x in m["robots"] if x["station"] == 1 and x["alliance"] == "red"][0]
        self.assertIn("default spot for station 1 (left trench)", s1["start"]["basis"])
        self.assertEqual(s1["start"]["side"], "L")


if __name__ == "__main__":
    unittest.main()


HARNESS = os.path.join(os.path.dirname(__file__), "..", ".work", "redesign", "backend-data", "data")


@unittest.skipUnless(os.path.isdir(os.path.join(HARNESS, "site", "api", "2026wasam", "m")), "harness data absent")
class SmokeRealData(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(MP, "_load_sb", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_leave_future_out(self):
        import shutil
        src = os.path.join(HARNESS, "site", "api", "2026wasam")
        sched = json.load(open(os.path.join(src, "schedule.json")))
        rows = sorted(sched["matches"], key=lambda r: r["time"])[:12]
        outs = []
        for keep in (12, 8):                      # second run has matches 9..12 removed entirely
            with tempfile.TemporaryDirectory() as d:
                site = os.path.join(d, "site", "api", "2026wasam")
                os.makedirs(os.path.join(site, "m"))
                for r in rows[:keep]:
                    shutil.copy(os.path.join(src, "m", r["key"] + ".json"), os.path.join(site, "m"))
                json.dump(dict(matches=rows[:keep]), open(os.path.join(site, "schedule.json"), "w"))
                os.makedirs(os.path.join(d, "tba"))
                shutil.copy(os.path.join(HARNESS, "tba", "event__2026wasam__matches.json"), os.path.join(d, "tba"))
                MP.build(d, "2026wasam")
                outs.append({r["key"]: json.load(open(os.path.join(site, "mp", r["key"] + ".json"))) for r in rows[:8]})
        a, b = outs
        for k in a:                               # predictions of the first 8 never saw matches 9..12
            self.assertEqual(a[k]["alliances"]["red"]["pred"], b[k]["alliances"]["red"]["pred"], k)
            self.assertEqual([r["enough"] for r in a[k]["robots"]], [r["enough"] for r in b[k]["robots"]])
        self.assertIsNone(a[rows[0]["key"]]["alliances"]["red"]["pred"]["score"]["mean"])   # match 1: unknown, not 0
        self.assertTrue(all(not r["enough"] for r in a[rows[0]["key"]]["robots"]))   # nothing before match 1
