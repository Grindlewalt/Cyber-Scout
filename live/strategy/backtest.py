"""Honest backtest: every qualification match is predicted using ONLY matches played before it (TBA actual_time order).

    python -m live.strategy.backtest DATA_DIR [--event 2026wasam] [--out results.json] [--sims 4000] [--recency H]

DATA_DIR = production data root (/opt/stacks/cyber-scout/data) or a copy with the same layout (see data.py).
Methods:
  coin      p = 0.5, score = mean of prior alliance scores
  opr       ridge OPR on total score (baselines.py), prequential normal win prob
  epa       Statbotics-style EPA updated match by match, prequential normal win prob
  sim_tba   strategy model with TBA data only (component OPR with shrinkage + schedule effect + climbs) -> Monte Carlo
  sim_full  same + video robot-level data (identity-confidence weighted)                             -> Monte Carlo
  full_preq sim_full's mean margin with the prequential normal win prob (separates mean accuracy from MC calibration)
  with a pre-event EPA prior (--epa-prior, see epa.py; only when one is available):
  epa_pre   component EPA started from the teams' earlier events this season, updated match by match (Statbotics-like)
  sim_epa_tba  sim_tba with the EPA prior as the team-effect prior
  sim_epa   sim_full with the EPA prior (the dashboard's model)
Metrics: Brier, log loss, accuracy (ties excluded), alliance-score MAE, margin MAE.  Reported on all quals, on the
"warm" subset (all six teams have >= 2 prior matches) and on the "video" subset (>= 1 team has trusted prior video).
Paired bootstrap 90% CIs for Brier differences.  Sample sizes are printed with everything.
"""
import argparse
import json
import math
import sys
import time

import numpy as np

from .baselines import EPA, Prequential, opr_predict, _mean_score
from . import epa as EPAMOD
from .data import load_event
from .model import fit_event_model
from .sim import simulate

METHODS = ["coin", "opr", "epa", "sim_tba", "sim_full", "full_preq"]
EPA_METHODS = ["epa_pre", "sim_epa_tba", "sim_epa"]


def run(E, sims=4000, recency=None, log=print, epa_prior=None, prior_events=None):
    """epa_prior: epa.pre_event_prior(...) result (adds EPA_METHODS); prior_events: raw TBA matches of the teams' earlier
    events (for epa_pre)."""
    quals = [m for m in E["tba"] if m["played"] and m["comp_level"] == "qm"]
    played = [m for m in E["tba"] if m["played"]]
    epa = EPA()
    preq = {k: Prequential() for k in ("opr", "epa", "full_preq", "epa_pre")}
    cepa = EPAMOD.local_epa(E, [], [], prior_events=prior_events) if epa_prior is not None else None
    epa_done = set()
    vid_w = {}
    for r in E["video"]["robot"]:
        vid_w.setdefault(r["match"], {})[r["team"]] = r["w"]
    rows = []
    for i, m in enumerate(quals):
        t0 = time.time()
        prior = [p for p in played if p["time"] < m["time"]]
        prior_mean = _mean_score(prior)
        for p in prior:                      # EPA: replay all prior matches in order (once)
            if p["key"] not in epa_done:
                epa.update(p, _mean_score([q for q in played if q["time"] < p["time"]]))
                if cepa is not None:
                    cepa.update(p)
                epa_done.add(p["key"])
        red, blue = m["teams"]["red"], m["teams"]["blue"]
        actual = (m["red"]["score"], m["blue"]["score"])
        y = m["red_win"]
        pred = {}
        pred["coin"] = (0.5, prior_mean, prior_mean)
        o = opr_predict(prior, E["teams"], red, blue)
        pred["opr"] = (preq["opr"].p(o[0] - o[1]), o[0], o[1])
        e = epa.predict(red, blue, prior_mean)
        pred["epa"] = (preq["epa"].p(e[0] - e[1]), e[0], e[1])
        extra = {}
        for name, uv in (("sim_tba", False), ("sim_full", True)):
            M = fit_event_model(E, m["time"], use_video=uv, recency_halflife=recency)
            s = simulate(M, red, blue, n=sims, seed=i)
            pred[name] = (s["p_red_win"], s["alliances"]["red"]["score"]["mean"], s["alliances"]["blue"]["score"]["mean"])
            extra[name] = {"p_red_auto": s["alliances"]["red"]["p_win_auto"],
                           "sd_margin": s["margin"]["sd"]}
            if uv:
                extra["video_matches_used"] = len(M.video_matches)
        fm = pred["sim_full"][1] - pred["sim_full"][2]
        pred["full_preq"] = (preq["full_preq"].p(fm), pred["sim_full"][1], pred["sim_full"][2])
        if epa_prior is not None:
            fmean = float(np.mean([q[a]["fouls"] for q in prior for a in ("red", "blue")])) if prior else 0.0
            ce = [sum(cepa.value(t)["total"] for t in tl) + fmean for tl in (red, blue)]
            pred["epa_pre"] = (preq["epa_pre"].p(ce[0] - ce[1]), ce[0], ce[1])
            for name, uv in (("sim_epa_tba", False), ("sim_epa", True)):
                M = fit_event_model(E, m["time"], use_video=uv, recency_halflife=recency, epa_prior=epa_prior)
                s = simulate(M, red, blue, n=sims, seed=i)
                pred[name] = (s["p_red_win"], s["alliances"]["red"]["score"]["mean"], s["alliances"]["blue"]["score"]["mean"])
                extra[name] = {"p_red_auto": s["alliances"]["red"]["p_win_auto"], "sd_margin": s["margin"]["sd"]}
            preq["epa_pre"].add(ce[0] - ce[1], actual[0] - actual[1])
        # update prequential error trackers AFTER predicting
        am = actual[0] - actual[1]
        preq["opr"].add(o[0] - o[1], am)
        preq["epa"].add(e[0] - e[1], am)
        preq["full_preq"].add(fm, am)
        # subsets
        n_prior = {t: sum(1 for p in prior if t in p["teams"]["red"] + p["teams"]["blue"]) for t in red + blue}
        vid = {t: sum(vid_w.get(p["key"], {}).get(t, 0.0) for p in prior) for t in red + blue}
        rows.append({"key": m["key"], "time": m["time"], "actual": actual, "red_win": y,
                     "red_won_auto": m["red"]["won_auto"], "pred": pred, "extra": extra,
                     "min_prior_matches": min(n_prior.values()),
                     "teams_with_video": sum(1 for v in vid.values() if v >= 0.5),
                     "video_weight": round(sum(vid.values()), 2)})
        log(f"{m['key']:>18}  prior={len(prior):2d}  actual {actual[0]:3d}-{actual[1]:3d}  "
            + "  ".join(f"{k}={pred[k][0]:.2f}" for k in pred) + f"  ({time.time() - t0:.1f}s)")
    return rows


def _metrics(rows, k):
    p = np.array([r["pred"][k][0] for r in rows])
    y = np.array([r["red_win"] for r in rows])
    pc = np.clip(p, 0.01, 0.99)
    brier = float(np.mean((p - y) ** 2))
    ll = float(np.mean(-(y * np.log(pc) + (1 - y) * np.log(1 - pc))))
    dec = y != 0.5
    acc = float(np.mean((p[dec] > 0.5) == (y[dec] == 1))) if dec.any() else float("nan")
    sp = np.array([[r["pred"][k][1], r["pred"][k][2]] for r in rows])
    sa = np.array([r["actual"] for r in rows], float)
    mae = float(np.mean(np.abs(sp - sa)))
    mmae = float(np.mean(np.abs((sp[:, 0] - sp[:, 1]) - (sa[:, 0] - sa[:, 1]))))
    return {"n": len(rows), "brier": round(brier, 4), "logloss": round(ll, 4), "acc": round(acc, 3),
            "score_mae": round(mae, 1), "margin_mae": round(mmae, 1)}


def _boot_diff(rows, a, b, nb=4000, seed=0):
    """paired bootstrap of Brier(a) - Brier(b): mean and 90% CI (negative = a better)."""
    rng = np.random.default_rng(seed)
    y = np.array([r["red_win"] for r in rows])
    da = (np.array([r["pred"][a][0] for r in rows]) - y) ** 2
    db = (np.array([r["pred"][b][0] for r in rows]) - y) ** 2
    d = da - db
    if len(d) == 0:
        return None
    bs = rng.choice(d, size=(nb, len(d)), replace=True).mean(1)
    return {"mean": round(float(d.mean()), 4), "ci90": [round(float(np.percentile(bs, 5)), 4),
                                                         round(float(np.percentile(bs, 95)), 4)],
            "p_a_better": round(float(np.mean(bs < 0)), 3)}


def _calibration(rows, k, bins=(0, .2, .4, .6, .8, 1.0001)):
    p = np.array([r["pred"][k][0] for r in rows])
    y = np.array([r["red_win"] for r in rows])
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        s = (p >= lo) & (p < hi)
        if s.any():
            out.append({"bin": f"{lo:.1f}-{min(hi, 1):.1f}", "n": int(s.sum()), "mean_p": round(float(p[s].mean()), 3),
                        "freq": round(float(y[s].mean()), 3)})
    return out


def summarize(rows):
    methods = [k for k in METHODS + EPA_METHODS if rows and k in rows[0]["pred"]]
    subsets = {
        "all_quals": rows,
        "warm (all 6 teams >=2 prior matches)": [r for r in rows if r["min_prior_matches"] >= 2],
        "early (some team has <2 prior matches)": [r for r in rows if r["min_prior_matches"] < 2],
        "first20 (qm1-qm20)": [r for r in rows if int(r["key"].rsplit("qm", 1)[1]) <= 20],
        "video (>=1 team with trusted prior video)": [r for r in rows if r["teams_with_video"] >= 1],
        "video4 (>=4 teams with trusted prior video)": [r for r in rows if r["teams_with_video"] >= 4],
    }
    out = {"subsets": {}, "paired_brier_diff": {}, "calibration": {}}
    for name, rs in subsets.items():
        out["subsets"][name] = {k: _metrics(rs, k) for k in methods} if rs else {}
        pairs = [("sim_full", "opr"), ("sim_full", "epa"), ("sim_full", "sim_tba"), ("sim_tba", "opr"),
                 ("full_preq", "sim_full")]
        if "sim_epa" in methods:
            pairs += [("sim_epa", "sim_full"), ("sim_epa", "epa_pre"), ("sim_epa", "sim_epa_tba"),
                      ("sim_epa_tba", "sim_tba"), ("epa_pre", "epa")]
        out["paired_brier_diff"][name] = {f"{a} - {b}": _boot_diff(rs, a, b) for a, b in pairs} if rs else {}
    warm = subsets["warm (all 6 teams >=2 prior matches)"]
    for k in methods[1:]:
        out["calibration"][k] = _calibration(warm, k)
    # schedule effect check: did the predicted auto winner matter
    out["auto_winner"] = {
        "red_won_auto_and_match": int(sum(1 for r in rows if r["red_won_auto"] and r["red_win"] == 1)),
        "red_won_auto": int(sum(1 for r in rows if r["red_won_auto"])),
        "auto_winner_won_match_frac": round(float(np.mean([(r["red_win"] == 1) == r["red_won_auto"]
                                                            for r in rows if r["red_win"] != 0.5])), 3),
        "sim_full_auto_brier": round(float(np.mean([(r["extra"]["sim_full"]["p_red_auto"] - r["red_won_auto"]) ** 2
                                                    for r in rows])), 4)}
    return out


def print_summary(S, log=print):
    for name, ms in S["subsets"].items():
        if not ms:
            continue
        n = next(iter(ms.values()))["n"]
        log(f"\n== {name}: n = {n} matches")
        log(f"{'method':>10} {'brier':>7} {'logloss':>8} {'acc':>6} {'scoreMAE':>9} {'marginMAE':>10}")
        for k, v in ms.items():
            log(f"{k:>10} {v['brier']:7.4f} {v['logloss']:8.4f} {v['acc']:6.3f} {v['score_mae']:9.1f} {v['margin_mae']:10.1f}")
        for pair, d in S["paired_brier_diff"][name].items():
            if d:
                log(f"   Brier {pair:>22}: {d['mean']:+.4f}  90% CI [{d['ci90'][0]:+.4f}, {d['ci90'][1]:+.4f}]  "
                    f"P(first better)={d['p_a_better']:.2f}")
    log("\ncalibration on warm subset (bin: n, mean p, observed red-win freq):")
    for k, c in S["calibration"].items():
        log(f"  {k:>10}: " + "; ".join(f"{b['bin']}: {b['n']}, {b['mean_p']:.2f}->{b['freq']:.2f}" for b in c))
    log(f"\nauto winner: {S['auto_winner']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--event")
    ap.add_argument("--out", help="write per-match predictions + summary JSON here")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--recency", type=float, default=None, help="optional recency half-life in matches")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--epa-prior", choices=["none", "cache", "fetch"], default="cache",
                    help="pre-event EPA prior from earlier events (epa.py): cache = cached files only (default), "
                         "fetch = may call TBA (TBA_KEY) / Statbotics, none = phase-1 methods only")
    a = ap.parse_args(argv)
    E = load_event(a.data_dir, a.event)
    prior = pev = None
    if a.epa_prior != "none":
        off = a.epa_prior == "cache"
        prior = EPAMOD.pre_event_prior(E["paths"]["root"], E, offline=off, use_statbotics=False)
        if prior:
            st = E["info"].get("start_date") or "9999"
            pev = EPAMOD.fetch_prior_events(E["paths"]["root"], E["event"], E["teams"], st,
                                            int(E["info"].get("year") or 2026), offline=True)["matches"]
            print(f"EPA prior: {prior['n_teams']}/{len(E['teams'])} teams from {prior['sources']['local_events']}")
        else:
            print("EPA prior: none available (no cached earlier-event data); EPA methods skipped")
    nv = len({b["match"] for b in E["video"]["alliance"]})
    print(f"event {E['event']}: {sum(1 for m in E['tba'] if m['played'] and m['comp_level'] == 'qm')} played quals, "
          f"{len(E['teams'])} teams, {nv} matches with shot data, {len(E['video']['robot'])} robot-match video records "
          f"({sum(1 for r in E['video']['robot'] if r['w'] >= 0.5)} with identity weight >= 0.5)")
    rows = run(E, sims=a.sims, recency=a.recency, log=(lambda *x: None) if a.quiet else print, epa_prior=prior,
               prior_events=pev)
    S = summarize(rows)
    print_summary(S)
    if a.out:
        json.dump({"event": E["event"], "generated": time.time(), "summary": S, "matches": rows}, open(a.out, "w"),
                  indent=1, default=float)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
