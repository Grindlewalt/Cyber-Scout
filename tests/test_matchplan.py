import json
import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()


HARNESS = os.path.join(os.path.dirname(__file__), "..", ".work", "redesign", "backend-data", "data")


@unittest.skipUnless(os.path.isdir(os.path.join(HARNESS, "site", "api", "2026wasam", "m")), "harness data absent")
class SmokeRealData(unittest.TestCase):
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
        self.assertEqual(a[rows[0]["key"]]["alliances"]["red"]["pred"]["score"]["mean"] >= 0, True)
        self.assertTrue(all(not r["enough"] for r in a[rows[0]["key"]]["robots"]))   # nothing before match 1
