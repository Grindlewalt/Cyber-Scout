"""Backtest + improvement log for the play-through model (live/strategy/timeline.py), leave-future-out.

Each tracked match's robots are predicted ONLY from that team's tracked matches that started earlier at the same event,
then compared with the observed 1 Hz tracks.  Tuning uses 2026wasam + the earliest 2026wamil matches (TUNE_WAMIL_CUTOFF);
the headline numbers are the held-out later 2026wamil matches.

    .venv/bin/python tools/eval_timeline.py DATA_DIR [--versions v1,v2,v3] [--note "what changed"] [--log FILE]
"""
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from live.strategy import behavior as B          # noqa: E402
from live.strategy import matchplan as MP        # noqa: E402
from live.strategy import timeline as TL         # noqa: E402

EVENTS = ["2026wasam", "2026wamil"]
TUNE_WAMIL_CUTOFF = 1792878794   # TBA time of the 20th tracked 2026wamil match in the first data copy (frozen)
PH = {p: (a, b) for p, a, b in TL.PHASES}


def load(data, ev):
    site = os.path.join(data, "site", "api", ev)
    sched = MP._jload(os.path.join(site, "schedule.json"))
    eps = {}
    for e in (MP._jload(os.path.join(site, "defense.json"), {}) or {}).get("episodes", []):
        eps.setdefault(e.get("key"), []).append(e)
    info = MP._load_records(site, eps)
    rows = sorted(sched["matches"], key=lambda r: (r.get("time") or 0, r["key"]))
    return rows, info


def zone(x):
    return 0 if x >= B.Z_OWN else (2 if x < B.Z_OPP else 1)


def phase_of(t):
    for p, (a, b) in PH.items():
        if a <= t < b:
            return p
    return "endgame"


def baseline_event_avg(flat, station):
    """Per-second median canonical position of earlier robots from the same station."""
    rr = [r["tl"] for r in flat if r["station"] == station and r.get("tl") is not None]
    if not rr:
        return None
    A = np.stack(rr)
    with np.errstate(all="ignore"):
        m = np.nanmedian(A, axis=0)
    for c in (0, 1):
        g = ~np.isnan(m[:, c])
        if not g.any():
            return None
        m[:, c] = np.interp(np.arange(len(m)), np.where(g)[0], m[g, c])
    return m


def run(data, versions):
    """Rows: dict(ev, key, time, team, ver->(err[T], zone[T]), fs_pred, fs_act, cps_pred, cps_act ...)."""
    out = []
    for ev in EVENTS:
        rows, info = load(data, ev)
        order = [(r.get("time") or 0, r["key"]) for r in rows if r["key"] in info]
        for t0, key in order:
            before = [(k, info[k]["recs"]) for t, k in order if t < t0 and k != key]
            hist, flat = {}, []
            for _, rs in before:
                for x in rs:
                    hist.setdefault(x["team"], []).append(x)
                    flat.append(x)
            smap = B.station_sides(flat)
            fs_all = [x["ev"]["first_score"] for x in flat if x.get("ev") and x["ev"].get("first_score") is not None]
            cps_all = {p: [x["bursts"][p] for x in flat if x["active"].get(p, True)] for p in TL.CPS_KEYS}
            for rec in info[key]["recs"]:
                act = rec.get("tl")
                if act is None or int((~np.isnan(act[:, 0])).sum()) < 20:
                    continue
                al, team = rec["alliance"], rec["team"]
                base = dict(ev=ev, key=key, time=t0, team=team, n_hist=len(hist.get(team, [])))
                eb = baseline_event_avg(flat, rec["station"])
                res = {}
                for v in versions:
                    r = TL.team_timeline(hist.get(team, []), rec["station"],
                                         {p for p in (info[key]["recs"] and [x["team"] for x in info[key]["recs"] if x["alliance"] == al]) if p != team},
                                         smap, al, version=v, prior=[x for x in flat if x['station'] == rec['station'] and x['team'] != team])
                    if r is None:
                        continue
                    res[v] = r
                if not res or eb is None:
                    continue
                v0 = next(iter(res))
                p0 = res[v0]["timeline"]["_canon"]
                variants = {v: res[v]["timeline"]["_canon"] for v in res}
                variants["stay"] = np.tile(p0[0], (TL.T, 1))
                variants["evavg"] = eb
                for name, path in variants.items():
                    d = np.hypot(*(path - act).T)
                    ok = ~np.isnan(d)
                    pa = act[ok]
                    rd = np.sqrt(((pa[:, None, :] - path[None, :, :]) ** 2).sum(2)).min(1)
                    zh = np.array([zone(path[i, 0]) == zone(act[i, 0]) for i in np.where(ok)[0]])
                    ts = np.where(ok)[0]
                    row = dict(base, version=name, route=rd.tolist(), err=d[ok].tolist(), t=ts.tolist(), zone=zh.tolist())
                    if name in res:
                        tlr = res[name]["timeline"]
                        e = next((e for e in tlr["events"] if e["kind"] == "first_score"), None)
                        row["fs_pred"] = e["t"] if e else None
                        row["fs_p"] = e["p"] if e else None
                        row["cps_pred"] = {k: res[name]["cycles_per_shift"].get(k, {}).get("mean") for k in TL.CPS_KEYS}
                    elif name == "evavg":
                        row["fs_pred"] = float(np.median(fs_all)) if fs_all else None
                        row["cps_pred"] = {k: (float(np.mean(cps_all[k])) if cps_all[k] else None) for k in TL.CPS_KEYS}
                    row["fs_act"] = rec["ev"].get("first_score")
                    row["cps_act"] = {k: rec["bursts"][k] for k in TL.CPS_KEYS if rec["active"].get(k, True)}
                    out.append(row)
    return out


def summarize(rows, version, pick):
    rr = [r for r in rows if r["version"] == version and pick(r)]
    if not rr:
        return None
    errs = np.concatenate([r["err"] for r in rr])
    ts = np.concatenate([r["t"] for r in rr])
    zh = np.concatenate([np.array(r["zone"], float) for r in rr])
    rt = np.concatenate([r["route"] for r in rr])
    by = {p: round(float(np.median(errs[[phase_of(t) == p for t in ts]])), 2) for p in PH if any(phase_of(t) == p for t in ts)}
    fe = [abs(r["fs_pred"] - r["fs_act"]) for r in rr if r.get("fs_pred") is not None and r.get("fs_act") is not None]
    ce = [abs(r["cps_pred"][k] - r["cps_act"][k]) for r in rr if r.get("cps_pred") for k in r["cps_act"]
          if r["cps_pred"].get(k) is not None]
    return dict(n=len(rr), med=round(float(np.median(errs)), 2), mean=round(float(errs.mean()), 2), by_phase=by,
                zone=round(float(zh.mean()), 3), route=round(float(np.median(rt)), 2), fs_err=round(float(np.median(fe)), 1) if fe else None, fs_n=len(fe),
                cps_mae=round(float(np.mean(ce)), 2) if ce else None)


def is_tune(r):
    return r["ev"] == "2026wasam" or r["time"] <= TUNE_WAMIL_CUTOFF


def is_held(r):
    return r["ev"] == "2026wamil" and r["time"] > TUNE_WAMIL_CUTOFF and r["n_hist"] >= B.MIN_MATCHES


def fmt(s):
    if not s:
        return "-"
    return (f"med {s['med']} m (mean {s['mean']}), zone {s['zone']}, route-dist {s['route']} m, 1st-score |err| {s['fs_err']} s (n={s['fs_n']}), "
            f"cycles/shift MAE {s['cps_mae']}, n={s['n']}")


def main(a):
    data = a[0]
    versions = (a[a.index("--versions") + 1] if "--versions" in a else "v1,v2,v3").split(",")
    note = a[a.index("--note") + 1] if "--note" in a else ""
    t0 = time.time()
    rows = run(data, versions)
    tune = lambda r: is_tune(r) and r["n_hist"] >= B.MIN_MATCHES
    res = {}
    for v in versions + ["stay", "evavg"]:
        res[v] = dict(tune=summarize(rows, v, tune), held=summarize(rows, v, is_held))
        print(f"{v:6s} TUNE {fmt(res[v]['tune'])}\n       HELD {fmt(res[v]['held'])}\n       held by phase {(res[v]['held'] or {}).get('by_phase')}")
    print(f"({time.time() - t0:.0f}s, {len(rows)} rows)")
    if "--log" in a:
        with open(a[a.index("--log") + 1], "a") as f:
            for v in versions:
                f.write(f"| {v} | {note} | {fmt(res[v]['tune'])} | {fmt(res[v]['held'])} | stay: {fmt(res['stay']['held'])}; evavg: {fmt(res['evavg']['held'])} |\n")
    return res


if __name__ == "__main__":
    main(sys.argv[1:])
