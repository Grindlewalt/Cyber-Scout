import unittest

from live.strategy import behavior as B


def rec(station, y, partners=(), role="collect", made=5, key="k"):
    path = [[13.0 + i * 0.05, y] for i in range(24)]
    return dict(key=key, team="frc1", alliance="red", station=station, partners=list(partners), start=[13.0, y],
                auto_path=path, trans_path=None, trans_scored=False, intakes=[(30.0, 8.0, y, 3)], cycles=[],
                volleys=3, tele_min=2.0, inactive=dict(role=role, ev={}), role=None,
                made={p: made for p in B.PERIODS}, active={p: True for p in B.PERIODS})


class BehaviorTests(unittest.TestCase):
    def test_station_beats_majority(self):
        hist = [rec(1, 1.0)] + [rec(2, 7.0) for _ in range(4)]
        P = B.team_plan(hist, 1, set(), {})
        self.assertEqual(P["start"]["side"], "L")                      # its one station-1 match, not the 4 R ones
        self.assertIn("1 of its 5 matches from station 1", P["start"]["basis"])

    def test_no_mirroring(self):
        hist = [rec(1, 7.0) for _ in range(3)]                          # from the left station it goes right
        P = B.team_plan(hist, 1, set(), {})
        self.assertEqual(P["start"]["side"], "R")
        self.assertGreater(P["cycle"]["collect"]["xy"][1], B.WID / 2)

    def test_fallback_says_so(self):
        hist = [rec(2, 1.0), rec(2, 1.5)]
        P = B.team_plan(hist, 3, set(), {})
        self.assertIn("no matches from station 3 yet: using all 2", P["start"]["basis"])

    def test_partner_weight(self):
        hist = [rec(1, 1.0, ["frc9"]), rec(1, 1.0, ["frc9"]), rec(1, 7.0), rec(1, 7.0), rec(1, 7.0)]
        P = B.team_plan(hist, 1, {"frc9"}, {})
        self.assertEqual(P["partners"], [dict(team="frc9", together=2, weight=2.0)])
        self.assertEqual(P["start"]["side"], "L")                       # 2x2 = 4 vs 3
        self.assertIn("weighted x2", P["cycle"]["basis"])

    def test_not_enough(self):
        P = B.team_plan([rec(1, 1.0)], 1, set(), {})
        self.assertFalse(P["enough"])
        self.assertIn("only 1 tracked match", P["why"])
        self.assertFalse(B.team_plan([], 1, set(), {})["enough"])
        h = [rec(1, 1.0), rec(1, 1.0)]
        for r in h:
            r["auto_path"] = None
        self.assertEqual(B.team_plan(h, 1, set(), {})["why"], "no usable auto path tracked yet")


if __name__ == "__main__":
    unittest.main()


class CycleRateTests(unittest.TestCase):
    def test_volleys_not_shots(self):
        m = dict(key="k", hz=10, t_start=0, red=dict(teams=["frc1", "frc2", "frc3"]), blue=dict(teams=["frc4", "frc5", "frc6"]),
                 periods=[dict(name="transition", t0=20, t1=30)],
                 tracks=[dict(alliance="red", team="frc1", xy=[[1300, 400]] * 1600, obs=[1] * 1600, conf=[1.0] * 1600)],
                 intakes=[],
                 shots=[dict(shooter=dict(kind="robot", track_idx=0, alliance="red"), match_t=40 + i * 0.4 + 15 * (i // 10),
                             target="red_hub", period="shift2", outcome="made") for i in range(30)])   # 3 bursts of 10
        r = B.extract(m)[0]
        self.assertEqual(r["volleys"], 3)
        self.assertAlmostEqual(r["tele_min"], 140 / 60, 1)
        P = B.team_plan([dict(r, key="a"), dict(r, key="b")], 1, set(), {})
        if P.get("cycle"):
            self.assertTrue(0.5 < P["cycle"]["per_min"] < 3)
