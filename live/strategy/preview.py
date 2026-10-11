"""Match preview JSON (schema: live/strategy/README.md).

    python -m live.strategy.preview DATA_DIR MATCH_KEY [--event EV] [--out FILE] [--asof prior|all] [--sims 8000]

--asof prior (default): use only matches played before MATCH_KEY (a genuine pre-match preview; for a scheduled match
this is everything played so far).  --asof all: use every played match (retrospective view of a played match).
"""
import argparse
import json
import math
import sys
import time

import numpy as np
from scipy import stats

from .data import COMPONENTS, PERIODS, SHIFTS, load_event
from .model import fit_event_model, team_theta
from .sim import simulate

SCHEMA = "cyber-scout/strategy-preview@1"
PERIOD_LABEL = {"auto": "auto", "transition": "transition", "shift1": "shift 1", "shift2": "shift 2",
                "shift3": "shift 3", "shift4": "shift 4", "endgame": "endgame"}


def _num(t):
    return t[3:] if t.startswith("frc") else t


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _routines(M, team, station):
    rs = M.auto_routines.get(team) or []
    if not rs:
        return None
    by = {}
    for r in rs:
        d = by.setdefault(r["routine"], {"routine": r["routine"], "name": r["name"], "n": 0, "stations": [],
                                         "made": [], "n_at_station": 0})
        d["n"] += 1
        d["stations"].append(r["station"])
        d["n_at_station"] += int(r["station"] == station)
        if r["auto_made"] is not None and r["w"] >= 0.5:
            d["made"].append(r["auto_made"])
    tot = sum(d["n"] for d in by.values())
    tot_st = sum(d["n_at_station"] for d in by.values())
    out = []
    for d in sorted(by.values(), key=lambda d: -d["n"]):
        out.append({"routine": d["routine"], "name": d["name"], "n": d["n"], "freq": round(d["n"] / tot, 2),
                    "freq_at_station": round(d["n_at_station"] / tot_st, 2) if tot_st else None,
                    "stations": sorted(set(d["stations"])),
                    "auto_made_mean": round(float(np.mean(d["made"])), 1) if d["made"] else None})
    return {"n_matches": tot, "n_at_station": tot_st, "conditioned_on_station": tot_st > 0, "routines": out}


def _robot_entry(M, sim_r, team, alliance, station):
    D = M.robot["team"].get(team) or {}
    cl = M.climb["team"].get(team) or {}
    pe = cl.get("end") or M.climb["event_end"]
    rating = {}
    for c in COMPONENTS:
        m, sd = team_theta(M.comp[c], team)
        z = m / sd if sd > 0 else 0.0
        pos = m * stats.norm.cdf(z) + sd * stats.norm.pdf(z) if sd > 0 else max(m, 0.0)   # E[max(theta, 0)]
        rating[c] = {"mean": _r(pos, 1), "sd": _r(sd, 1), "linear_mean": _r(m, 1)}
    n_tba = cl.get("n", 0)
    vw = D.get("w_sum", 0.0) or 0.0
    role = D.get("role")
    if role:
        role = {"v": role["v"], "scores": role["scores"], "basis": "video", "w_sum": role["w_sum"]}
    else:
        tot = sim_r["fuel_mean"]
        role = {"v": "scorer" if tot >= 25 else "low-scorer", "scores": None, "basis": "model (no trusted video)"}
    flags = []
    dead = D.get("dead")
    if dead and not dead.get("prior_only") and dead["v"] >= 0.15:
        flags.append(f"dead/disabled in ~{dead['v'] * 100:.0f}% of tracked matches (shrunk; n_eff {dead['n_eff']})")
    if vw < 0.5:
        flags.append("no trusted video: per-robot split comes from TBA alliance totals only")
    st = D.get("stalls")
    zones = D.get("zones")
    hs = D.get("hub_state")
    d = lambda k: D.get(k)
    return {
        "team": team, "number": int(_num(team)), "alliance": alliance, "station": station,
        "role": role,
        "expected_fuel": {"total": sim_r["fuel_mean"], "sd": sim_r["fuel_sd"], "by_period": sim_r["fuel_by_period_mean"],
                          "if_won_auto": sim_r["fuel_if_won_auto"], "if_lost_auto": sim_r["fuel_if_lost_auto"]},
        "rating": rating,
        "hub_state": None if not hs else {"active": hs["active"], "inactive": hs["inactive"], "w_sum": hs["w_sum"]},
        "top_zones": None if not zones else {"w_made": zones["w_made"], "zones": zones["share"][:3]},
        "auto": {"expected_fuel": sim_r["fuel_by_period_mean"]["auto"], "auto_l1_prob": _r(cl.get("auto_l1"), 3),
                 "start_station": station, "routines": _routines(M, team, station)},
        "climb": {"auto_l1": _r(cl.get("auto_l1"), 3),
                  "end": {f"L{i}": _r(p, 3) for i, p in enumerate(pe)},
                  "expected_pts": sim_r["tower_pts_mean"], "n_obs": n_tba, "obs_end_levels": cl.get("end_obs")},
        "reliability": {"dead_rate": dead, "stalls_per_match": st, "flags": flags},
        "defended": {"rate_retained": d("rate_retained_defended"), "defended_s_per_match": d("defended_s")},
        "defense": {"defense_s_per_match": d("defense_s"), "contacts_per_match": d("contacts"), "pins_per_match": d("pins")},
        "shooting": {k: d(k) for k in ("fire_rate", "volley_size", "volley_max", "cycle_s", "accuracy", "shot_dist_m",
                                       "on_move_rate", "passes", "intakes", "congestion")},
        "data_quality": {"n_matches_tba": n_tba, "n_video_matches": D.get("n_matches", 0), "video_weight": vw,
                         "video_matches": D.get("matches", [])},
    }


def _fmt_periods(bp, k=2):
    top = sorted(bp.items(), key=lambda kv: -kv[1])[:k]
    return " and ".join(PERIOD_LABEL[p] for p, _ in top)


def key_factors(M, S, robots, red, blue):
    F = []
    pr = S["p_red_win"]
    fav = "Red" if pr >= 0.5 else "Blue"
    pf = max(pr, 1 - pr)
    mg = S["margin"]
    if pf < 0.6:
        F.append({"kind": "outlook", "weight": 1.0,
                  "text": f"Toss-up: red wins {pr * 100:.0f}% of {S['n_sims']} simulations "
                          f"(margin {mg['mean']:+.0f}, 50% range {mg['q25']:+.0f} to {mg['q75']:+.0f})."})
    else:
        F.append({"kind": "outlook", "weight": 1.0,
                  "text": f"{fav} favoured: wins {pf * 100:.0f}% of simulations, expected margin "
                          f"{abs(mg['mean']):.0f} points (red-minus-blue 50% range {mg['q25']:+.0f} to {mg['q75']:+.0f})."})
    # auto battle + schedule (causal schedule value = sum of the event-level beta's; counterfactual sims)
    A = S["alliances"]
    pa = A["red"]["p_win_auto"]
    gain = sum(M.comp[c]["beta"] for c in ("trans", "active", "endgame"))
    gsd = math.sqrt(sum(M.comp[c]["beta_sd"] ** 2 for c in ("trans", "active", "endgame")))
    cf = S.get("counterfactual_auto") or {}
    cond = ""
    if cf:
        cond = (f" With the same robots, red's win chance would be {cf['red_wins_auto'] * 100:.0f}% if red won auto "
                f"vs {cf['blue_wins_auto'] * 100:.0f}% if blue did.")
    F.append({"kind": "auto_battle", "weight": round(min(1.0, abs(gain) / 30 + 0.3 * (1 - abs(2 * pa - 1))), 2),
              "text": f"Auto battle: red wins auto in {pa * 100:.0f}% of sims (expected auto FUEL red "
                      f"{A['red']['fuel_by_period_mean']['auto']:.0f} vs blue {A['blue']['fuel_by_period_mean']['auto']:.0f}). "
                      f"The auto winner's hub is inactive in shift 1 and active in shifts 2+4; at this event that "
                      f"schedule is worth {gain:+.0f} ± {gsd:.0f} teleop FUEL to an alliance." + cond})
    # top scorer per alliance: when + where
    for a in ("red", "blue"):
        rs = [r for r in robots if r["alliance"] == a]
        top = max(rs, key=lambda r: r["expected_fuel"]["total"])
        af = A[a]["fuel"]["mean"]
        share = top["expected_fuel"]["total"] / af if af else 0
        p_aw = A[a]["p_win_auto"]
        act = "2 and 4" if p_aw >= 0.5 else "1 and 3"
        bp = top["expected_fuel"]["by_period"]
        teleop_shift = {p: bp[p] for p in SHIFTS}
        txt = (f"{a.capitalize()}'s {top['number']} carries ~{top['expected_fuel']['total']:.0f} of {a}'s "
               f"{af:.0f} expected FUEL ({share * 100:.0f}%), mostly in {_fmt_periods(bp)}; {a}'s hub is most likely "
               f"active in shifts {act} ({max(p_aw, 1 - p_aw) * 100:.0f}% of sims).")
        tz = top.get("top_zones")
        if tz and tz["w_made"] >= 5:
            z = tz["zones"][0]
            txt += (f" Its tracked made FUEL comes {z['share'] * 100:.0f}% from {z['zone']} "
                    f"(driver's view) — a defender there during {a}-active shifts {act} matters most.")
        else:
            txt += " No trusted shot locations yet for this robot."
        F.append({"kind": "top_scorer", "alliance": a, "team": top["team"], "weight": round(min(1.0, share + 0.3), 2),
                  "text": txt})
    # defence susceptibility
    for r in robots:
        rr = (r["defended"] or {}).get("rate_retained")
        if rr and not rr.get("prior_only") and rr["n_eff"] >= 0.4 and r["expected_fuel"]["total"] >= 20:
            if rr["v"] < 0.75:
                F.append({"kind": "defense_target", "team": r["team"], "weight": 0.6,
                          "text": f"{r['number']} kept only ~{rr['v'] * 100:.0f}% of its shot rate while defended "
                                  f"(shrunk estimate, {rr['n_eff']:.1f} weighted defended matches) — defending it pays."})
            elif rr["v"] > 1.0:
                F.append({"kind": "defense_resistant", "team": r["team"], "weight": 0.3,
                          "text": f"Defence has not slowed {r['number']} so far (retained ~{rr['v'] * 100:.0f}% of "
                                  f"its shot rate, {rr['n_eff']:.1f} weighted defended matches)."})
    # reliability
    for r in robots:
        dr = r["reliability"]["dead_rate"]
        if dr and not dr.get("prior_only") and dr["v"] >= 0.15:
            F.append({"kind": "reliability", "team": r["team"], "weight": round(min(1.0, dr["v"] * 2), 2),
                      "text": f"{r['number']} has been dead/disabled in part of its tracked matches "
                              f"(shrunk rate {dr['v'] * 100:.0f}%) — plan for it stopping."})
    # climbs
    for r in robots:
        cl = r["climb"]
        pc = 1 - (cl["end"]["L0"] or 1)
        if r["climb"]["n_obs"] and (pc >= 0.3 or (cl["auto_l1"] or 0) >= 0.3):
            F.append({"kind": "climb", "team": r["team"], "weight": 0.4,
                      "text": f"{r['number']} climbs: P(endgame climb) {pc * 100:.0f}%, P(auto L1) "
                              f"{(cl['auto_l1'] or 0) * 100:.0f}% (~{cl['expected_pts']:.0f} tower pts expected)."})
    # inactive-shift behaviour of feeders
    for r in robots:
        hs = r.get("hub_state")
        if hs and hs["w_sum"] >= 1 and (hs["inactive"]["passes_per_min"] or 0) >= 5.0:
            F.append({"kind": "inactive_behaviour", "team": r["team"], "weight": 0.35,
                      "text": f"{r['number']} launches FUEL as passes (not at a hub) while its hub is inactive "
                              f"(~{hs['inactive']['passes_per_min']:.0f} passes/min vs "
                              f"{hs['active']['passes_per_min'] or 0:.0f} when active; "
                              f"{r['data_quality']['n_video_matches']} tracked match(es))."})
    # data quality / uncertainty
    nov = [r["number"] for r in robots if r["data_quality"]["video_weight"] < 0.5]
    if nov:
        F.append({"kind": "data_quality", "weight": 0.5,
                  "text": f"No trusted robot-level video yet for {', '.join(map(str, nov))}: their share of alliance "
                          f"scoring is inferred from TBA totals only (OPR-like), so their numbers are less certain."})
    wide = max(robots, key=lambda r: r["rating"]["active"]["sd"] or 0)
    F.append({"kind": "uncertainty", "team": wide["team"], "weight": 0.3,
              "text": f"Biggest uncertainty: {wide['number']} (active-shift rating {wide['rating']['active']['mean']:.0f} "
                      f"± {wide['rating']['active']['sd']:.0f} FUEL)."})
    F.sort(key=lambda f: -f["weight"])
    return F


def build_preview(E, key, asof="prior", sims=8000, seed=0):
    m = E["by_key"].get(key)
    if not m:
        raise SystemExit(f"unknown match {key}")
    before = m["time"] if (asof == "prior" and m["played"]) else None
    M = fit_event_model(E, before, use_video=True)
    red, blue = m["teams"]["red"], m["teams"]["blue"]
    S = simulate(M, red, blue, n=sims, seed=seed)
    S["counterfactual_auto"] = {
        "red_wins_auto": simulate(M, red, blue, n=sims // 2, seed=seed + 1, force_red_auto=True)["p_red_win"],
        "blue_wins_auto": simulate(M, red, blue, n=sims // 2, seed=seed + 2, force_red_auto=False)["p_red_win"]}
    robots = [_robot_entry(M, S["robots"][i], t, "red" if i < 3 else "blue", i % 3 + 1)
              for i, t in enumerate(red + blue)]
    lvl, num, sub = m["order"]
    label = {0: "Qualification", 1: "Elimination", 2: "Quarterfinal", 3: "Semifinal", 4: "Final"}.get(lvl, "Match")
    out = {
        "schema": SCHEMA, "event": E["event"], "match": key, "comp_level": m["comp_level"],
        "label": f"{label} {num}" + (f" (match {sub})" if lvl > 0 and sub else ""),
        "time": m["time"], "played": m["played"], "generated": int(time.time()),
        "data": {"asof": asof if m["played"] else "all_played", "before_time": before, "n_matches_used": M.n_matches,
                 "video_matches_used": M.video_matches,
                 "model": {c: {"mu": _r(M.comp[c]["mu"], 1), "beta": _r(M.comp[c]["beta"], 1),
                               "beta_sd": _r(M.comp[c]["beta_sd"], 1), "sigma": _r(math.sqrt(M.comp[c]["sigma2"]), 1),
                               "tau": _r(math.sqrt(M.comp[c]["tau2"]), 1), "hp_alliance": _r(M.h[c], 1)}
                           for c in COMPONENTS},
                 "not_modelled": ["defence/congestion interactions (not estimable from current data)",
                                  "per-team fouls (event empirical foul distribution resampled)"]},
        "prediction": {"p_red_win": S["p_red_win"], "p_blue_win": round(1 - S["p_red_win"], 4), "margin": S["margin"],
                       "p_red_win_if_red_wins_auto": S["counterfactual_auto"]["red_wins_auto"],
                       "p_red_win_if_blue_wins_auto": S["counterfactual_auto"]["blue_wins_auto"],
                       "schedule_value_fuel": {"mean": _r(sum(M.comp[c]["beta"] for c in ("trans", "active", "endgame")), 1),
                                               "sd": _r(math.sqrt(sum(M.comp[c]["beta_sd"] ** 2
                                                                      for c in ("trans", "active", "endgame"))), 1)},
                       "n_sims": S["n_sims"]},
        "alliances": {a: {"teams": S["alliances"][a]["teams"], "score": S["alliances"][a]["score"],
                          "score_no_fouls": S["alliances"][a]["score_no_fouls"], "fuel": S["alliances"][a]["fuel"],
                          "fuel_by_period": S["alliances"][a]["fuel_by_period_mean"],
                          "p_win_auto": S["alliances"][a]["p_win_auto"],
                          "tower_pts_mean": S["alliances"][a]["tower_pts_mean"],
                          "foul_pts_mean": S["alliances"][a]["foul_pts_mean"],
                          "hp_fuel_mean": S["alliances"][a]["hp_fuel_mean"]} for a in ("red", "blue")},
        "robots": robots,
    }
    out["key_factors"] = key_factors(M, S, robots, red, blue)
    if m["played"]:
        out["actual"] = {"red_score": m["red"]["score"], "blue_score": m["blue"]["score"], "red_win": m["red_win"],
                         "red_won_auto": m["red"]["won_auto"],
                         "fuel_by_period": {a: {p: m[a]["fuel"][p] for p in PERIODS} for a in ("red", "blue")},
                         "end_tower": {a: m[a]["end_tower"] for a in ("red", "blue")},
                         "fouls": {a: m[a]["fouls"] for a in ("red", "blue")}}
    else:
        out["actual"] = None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("match")
    ap.add_argument("--event")
    ap.add_argument("--out")
    ap.add_argument("--asof", choices=["prior", "all"], default="prior")
    ap.add_argument("--sims", type=int, default=8000)
    a = ap.parse_args(argv)
    E = load_event(a.data_dir, a.event)
    key = a.match if "_" in a.match else f"{E['event']}_{a.match}"
    P = build_preview(E, key, a.asof, a.sims)
    s = json.dumps(P, indent=1)
    if a.out:
        open(a.out, "w").write(s)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(s)
    for f in P["key_factors"]:
        print(f"- {f['text']}", file=sys.stderr)


if __name__ == "__main__":
    main()
