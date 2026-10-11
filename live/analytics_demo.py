"""Run live/analytics.py over .work/samples; write .work/robot-analytics/report.json + summary."""
import json, os, sys, glob, time
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))
import analytics as A

S = os.path.join(ROOT, ".work", "samples")
OUT = os.path.join(ROOT, ".work", "robot-analytics")
os.makedirs(OUT, exist_ok=True)
rules_p = os.path.join(ROOT, "live", "field", "rebuilt_rules.json")
rules = json.load(open(rules_p)) if os.path.exists(rules_p) else None
tba = {m["key"]: m for m in json.load(open(os.path.join(S, "tba", "event__2026wasam__matches.json")))["data"]}
per_team, autos, bymatch = {}, {}, {}
t0 = time.time()
for f in sorted(glob.glob(os.path.join(S, "matches", "*.json"))):
    m = json.load(open(f))
    key = m["key"]
    sj = os.path.join(S, "api", "2026wasam", "m", key + ".json")
    if os.path.exists(sj):
        site = json.load(open(sj))
        for k, tr in enumerate(site["tracks"]):
            m["tracks"][k]["team"] = tr.get("team"); m["tracks"][k]["id_conf"] = tr.get("id_conf")
    res = A.match_analytics(m, None, rules, tba.get(key))
    bymatch[key] = res
    for k, t in res["tracks"].items():
        team = t["meta"]["team"] or "unknown_%s_%d" % (key, k)
        per_team.setdefault(team, []).append(t)
        a = t["auto"]
        autos.setdefault(team, []).append({"match": key, "alliance": t["meta"]["alliance"], "path": a.get("path"), "coverage": a.get("coverage")})
print("matches", len(bymatch), "%.1fs" % (time.time() - t0), "fallbacks:", res["fallbacks_used"])
clusters = A.cluster_autos(autos)
agg = {tm: A.team_aggregate(l) for tm, l in per_team.items()}
for tm in agg:
    agg[tm]["auto_routines"] = clusters[tm]
json.dump({"matches": bymatch, "teams": agg}, open(os.path.join(OUT, "report.json"), "w"), default=str)

# ---- summary
lines = []
def g(a, k, f="mean"):
    v = a["metrics"].get(k); return None if not v else v.get(f)
for tm in sorted(agg, key=lambda x: str(x)):
    a = agg[tm]
    rt = clusters[tm]["routines"]
    lines.append("%s n=%d stations=%s dist=%s mv_speed=%s/p95 %s accel99=%s moving=%s own/neu/opp=%s/%s/%s lanes=%s defend=%ss defended=%ss dead=%d lowcov=%d role=%s autos=%s" % (
        tm, a["n_matches"], a["stations"], g(a, "movement.distance_m"), g(a, "movement.moving_speed_mean"), g(a, "movement.moving_speed_p95"),
        g(a, "movement.max_accel_p99"), g(a, "movement.pct_moving"), g(a, "movement.zone_frac.own"), g(a, "movement.zone_frac.neutral"), g(a, "movement.zone_frac.opp"),
        a["lane_totals"], g(a, "defense.defending_s"), g(a, "defense.defended_s"), a["dead_matches"], a["low_coverage_matches"], a["role_counts"],
        [(r["id"], r["n"], r["kind"], r["mean_len_m"]) for r in rt]))
open(os.path.join(OUT, "summary.txt"), "w").write("\n".join(lines))
print("\n".join(lines))

# ---- sanity
sp, dist, acc = [], [], []
for r in bymatch.values():
    for t in r["tracks"].values():
        mv = t["movement"]
        if mv.get("distance_m") is not None: dist.append(mv["distance_m"])
        if mv.get("moving_speed_p95") is not None: sp.append(mv["moving_speed_p95"])
        if mv.get("max_accel_p99") is not None: acc.append(mv["max_accel_p99"])
print("SANITY p95 speed: min %.2f med %.2f max %.2f | distance: min %.0f med %.0f max %.0f | accel99 med %.1f max %.1f | n=%d" % (
    min(sp), np.median(sp), max(sp), min(dist), np.median(dist), max(dist), np.median(acc), max(acc), len(dist)))
# station vs canonical start side
st = {}
for r in bymatch.values():
    for t in r["tracks"].values():
        s = t["meta"]["station"]; sc = t["auto"].get("start_xy_canon")
        if s and sc: st.setdefault(s, []).append(sc[1])
print("canon start y by station:", {s: (round(float(np.mean(v)), 2), round(float(np.std(v)), 2), len(v)) for s, v in sorted(st.items())})
