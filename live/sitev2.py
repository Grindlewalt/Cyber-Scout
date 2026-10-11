"""v2 site JSON: map live/analytics.py output onto the web data contract (top of web/app.js).

analytics.py stays the source of truth for metric definitions; this module only
renames/reshapes, builds team-frame shot/intake maps, and the event table summary.
"""
import numpy as np

LEN, WID = 16.541, 8.069


def _g(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict) or p not in d or d[p] is None:
            return default
        d = d[p]
    return d


def _lvl(v):
    return {"Level1": 1, "Level2": 2, "Level3": 3}.get(v or "", 0)


def web_track_analytics(a):
    """One robot-match: analytics.match_analytics()['tracks'][k] -> web contract tree."""
    fu = a.get("fuel") or {}
    bp = fu.get("by_period") or {}
    sd = fu.get("shot_distance") or {}
    ints = fu.get("intakes") or {}
    zc = ints.get("zone_counts") or {}
    mv = a.get("movement") or {}
    au = a.get("auto") or {}
    de = a.get("defense") or {}
    eg = a.get("endgame") or {}
    re = a.get("reliability") or {}
    st = a.get("strategy") or {}
    att = None if fu.get("n_shots") is None else (fu.get("n_made") or 0) + (fu.get("n_missed") or 0)
    auto_p = bp.get("auto") or {}
    return {
        "scoring": {
            "attempts": att, "made": fu.get("n_made"), "missed": fu.get("n_missed"),
            "passes": _g(fu, "passes", "n"), "accuracy": fu.get("accuracy"),
            "points": fu.get("est_points"), "uncounted": fu.get("uncounted_made"),
            "by_period": {p: {"a": v.get("made", 0) + v.get("missed", 0), "m": v.get("made", 0)}
                          for p, v in bp.items() if p not in ("pause", "other")} or None,
            "fire_rate": fu.get("fire_rate_in_volley"), "volley_size": fu.get("volley_size_mean"),
            "volley_max": fu.get("volley_size_max"), "volleys": fu.get("volleys"),
            "cycle_s": fu.get("cycle_time_s"), "dist_mean": sd.get("mean"),
            "on_move_rate": fu.get("shoot_on_move_frac"),
            "first_shot_auto": fu.get("time_first_shot_auto_s"),
            "first_shot_teleop": fu.get("time_first_shot_teleop_s"),
            "launch_cv": _g(fu, "launch_speed", "cv"), "miss_pattern": fu.get("miss_pattern"),
        },
        "collection": {"intakes": ints.get("n"), "per_min": ints.get("per_min"),
                       "by_loc": {"neutral": zc.get("neutral", 0), "own": zc.get("own", 0), "opp": zc.get("opp", 0),
                                  "outpost": ints.get("near_outpost", 0)} if ints else None,
                       "per_cycle": ints.get("per_cycle")},
        "movement": {"distance_m": mv.get("distance_m"), "mean_speed": mv.get("moving_speed_mean"),
                     "p95_speed": mv.get("moving_speed_p95"), "max_accel": mv.get("max_accel_p99"),
                     "pct_moving": mv.get("pct_moving"), "zone": mv.get("zone_frac"),
                     "lanes": {k: v for k, v in (mv.get("lane_counts") or {}).items() if k != "unknown"} or None,
                     "near_side_pct": mv.get("frac_crossings_high_y_canon"),
                     "trench_capable": mv.get("trench_capable"), "crossings": mv.get("n_crossings")},
        "auto": {"routine": a.get("_routine"), "start_xy": au.get("start_xy"), "end_xy": au.get("end_xy"),
                 "distance_m": au.get("distance_m"), "neutral_cross": au.get("enters_neutral"),
                 "shots": None if not auto_p else auto_p.get("made", 0) + auto_p.get("missed", 0),
                 "made": auto_p.get("made") if auto_p else None,
                 "climb": None if au.get("tba_auto_tower") is None else au.get("tba_auto_tower") != "None"},
        "defense": {"time_s": de.get("defending_s"), "contacts": de.get("n_contacts"), "pins": de.get("pins_made"),
                    "effect": _g(de, "defense_effectiveness", "reduction")},
        "defended": {"time_s": de.get("defended_s"), "rate_retained": _g(de, "retained_rate", "retained"),
                     "escape_s": de.get("mean_defended_run_s")},
        "endgame": {"level": _lvl(eg.get("tba_end_level")), "status": eg.get("climb_status"),
                    "attempted": eg.get("climb_attempt_detected") or _lvl(eg.get("tba_end_level")) > 0,
                    "arrive_t": eg.get("arrival_s"), "duration_s": eg.get("climb_duration_proxy_s"),
                    "stop_scoring_t": eg.get("last_shot_s") or eg.get("left_scoring_area_s"),
                    "points": eg.get("climb_points")},
        "reliability": {"dead_s": re.get("dead_s"), "dead": re.get("dead_flag"), "stalls": re.get("n_stalls_5_15s"),
                        "low_coverage": re.get("low_coverage_flag")},
        "congestion": st.get("congestion_frac"), "role": st.get("role"), "role_basis": st.get("role_basis"),
        "coverage": {"feed_ok": _g(a, "coverage", "feed_ok"), "observed": re.get("observed_frac")},
    }


def _leaf(m, key, conf_scale=1.0):
    x = m.get(key)
    if not x or x.get("mean") is None:
        return None
    n = x.get("n") or 0
    conf = min(1.0, (x.get("coverage") or 0) * min(1.0, n / 6.0)) * conf_scale
    return {"v": x["mean"], "n": n, "conf": round(conf, 2), "trend": x.get("trend")}


def web_team_analytics(agg, per_match_web, id_conf=1.0):
    """analytics.team_aggregate() output (+ the per-match web trees) -> team analytics tree."""
    m = agg.get("metrics") or {}
    L = lambda k: _leaf(m, k, id_conf)
    made = sum((x["scoring"]["made"] or 0) for x in per_match_web)
    att = sum((x["scoring"]["attempts"] or 0) for x in per_match_web)
    levels = {}
    for x in per_match_web:
        levels[str(x["endgame"]["level"])] = levels.get(str(x["endgame"]["level"]), 0) + 1
    byp = {}
    for x in per_match_web:
        for p, v in (x["scoring"]["by_period"] or {}).items():
            d = byp.setdefault(p, {"a": 0, "m": 0, "k": 0}); d["a"] += v["a"]; d["m"] += v["m"]; d["k"] += 1
    nm = max(1, len(per_match_web))
    rc = agg.get("role_counts") or {}
    role = max(rc, key=rc.get) if rc else None
    return {
        "n": agg.get("n_matches"),
        "scoring": {"attempts": L("fuel.n_shots"), "made": L("fuel.n_made"), "accuracy": L("fuel.accuracy"),
                    "passes": L("fuel.passes.n"), "points": L("fuel.est_points"), "uncounted": L("fuel.uncounted_made"),
                    "fire_rate": L("fuel.fire_rate_in_volley"), "volley_size": L("fuel.volley_size_mean"),
                    "volleys": L("fuel.volleys"), "cycle_s": L("fuel.cycle_time_s"),
                    "dist_mean": L("fuel.shot_distance.mean"), "on_move_rate": L("fuel.shoot_on_move_frac"),
                    "first_shot_auto": L("fuel.time_first_shot_auto_s"),
                    "first_shot_teleop": L("fuel.time_first_shot_teleop_s"),
                    "made_total": made, "attempts_total": att,
                    "accuracy_pooled": round(made / att, 3) if att else None,
                    "by_period": {p: {"a": round(v["a"] / nm, 2), "m": round(v["m"] / nm, 2)} for p, v in byp.items()}},
        "collection": {"intakes": L("fuel.intakes.n"), "per_min": L("fuel.intakes.per_min")},
        "movement": {"distance_m": L("movement.distance_m"), "mean_speed": L("movement.moving_speed_mean"),
                     "p95_speed": L("movement.moving_speed_p95"), "max_accel": L("movement.max_accel_p99"),
                     "pct_moving": L("movement.pct_moving"),
                     "zone": {z: L(f"movement.zone_frac.{z}") for z in ("own", "neutral", "opp")},
                     "lanes_total": {k: v for k, v in (agg.get("lane_totals") or {}).items() if k != "unknown"},
                     "trench_capable": agg.get("trench_capable"),
                     "near_side_pct": L("movement.frac_crossings_high_y_canon")},
        "auto": {"distance_m": L("auto.distance_m"), "neutral_cross": L("auto.enters_neutral")},
        "defense": {"time_s": L("defense.defending_s"), "contacts": L("defense.n_contacts"),
                    "pins": L("defense.pins_made"), "effect": L("defense.defense_effectiveness.reduction")},
        "defended": {"time_s": L("defense.defended_s"), "rate_retained": L("defense.retained_rate.retained")},
        "endgame": {"levels": levels,
                    "climb_rate": round(sum(v for k, v in levels.items() if k != "0") / nm, 2),
                    "attempt_rate": round(sum(1 for x in per_match_web if x["endgame"]["attempted"]) / nm, 2),
                    "arrive_t": L("endgame.arrival_s"), "duration_s": L("endgame.climb_duration_proxy_s"),
                    "stop_scoring_t": L("endgame.left_scoring_area_s"), "n": len(per_match_web)},
        "reliability": {"dead_matches": agg.get("dead_matches"), "n": agg.get("n_matches"),
                        "mean_dead_s": L("reliability.dead_s"), "low_coverage_matches": agg.get("low_coverage_matches")},
        "role": {"v": role, "scores": {k: round(v / max(1, sum(rc.values())), 2) for k, v in rc.items()},
                 "n": sum(rc.values()), "conf": round(id_conf, 2)},
    }


def team_frame(xy, alliance):
    """Own end on the left (red's end): blue positions are point-reflected."""
    x, y = xy[0], xy[1]
    return (LEN - x, WID - y) if alliance == "blue" else (x, y)


def event_q(team_web):
    """Compact per-team summary for the event table / compare / picklist."""
    a = team_web.get("analytics") or {}
    v = lambda *p: (_g(a, *p) or {}).get("v") if isinstance(_g(a, *p), dict) else _g(a, *p)
    rel = a.get("reliability") or {}
    return {"n": a.get("n"), "acc": _g(a, "scoring", "accuracy_pooled"), "made": v("scoring", "made"),
            "att": v("scoring", "attempts"), "fire": v("scoring", "fire_rate"), "volley": v("scoring", "volley_size"),
            "cycle": v("scoring", "cycle_s"), "intakes": v("collection", "intakes"),
            "climb": _g(a, "endgame", "climb_rate"), "defs": v("defense", "time_s"), "pins": v("defense", "pins"),
            "automade": _g(a, "scoring", "by_period", "auto", "m"),
            "dead": round(rel.get("dead_matches", 0) / max(1, rel.get("n") or 1), 2) if rel else None,
            "role": _g(a, "role", "v"), "speed": v("movement", "p95_speed"), "dist": v("movement", "distance_m"),
            "conf": team_web.get("id_conf")}
