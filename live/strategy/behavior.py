"""Per-team behaviour model from that team's EARLIER tracked matches (feeds matchplan.py).

Everything here is in the canonical frame (blue point-reflected, so every alliance attacks toward low x and its
driver wall is at x = 16.541; driver's left = low canonical y).  matchplan converts to the field frame at the end.

Owner rules implemented here:
  * conditioning: use the robot's matches from the SAME driver station; else (only if that station has a clear side at
    this event) matches from the same side; else all of its matches, and `basis` says so.  Never mirrored, never
    majority-of-other-side.  (At 2026wasam station does not determine the start side: 55-75 % either way, so the side
    fallback almost never applies and is reported as "no clear side for station k".)
  * partner weighting: matches where this robot played with a current partner (>= 2 earlier matches together) weigh x2.
  * not enough info: < MIN_MATCHES tracked matches or no usable auto path -> enough False + plain `why`.
"""
import math

import numpy as np

LEN, WID = 16.541, 8.069
Z_OWN, Z_OPP = LEN - 4.03, 4.03          # alliance-zone boundaries (canonical x)
MIN_MATCHES = 2
PARTNER_MIN, PARTNER_W = 2, 2.0
SIDE_CLEAR = 0.65                         # a station "has a side" when >= this share of starts are on one side
ROLES = ("defense", "ferry_neutral", "robber", "collect")
PERIODS = ["auto", "transition", "shift1", "shift2", "shift3", "shift4", "endgame"]


def canon(x, y, al):
    return (LEN - x, WID - y) if al == "blue" else (x, y)


def side_of(y):
    return "L" if y < WID / 2 else "R"


def zone_of(x):
    return "own" if x >= Z_OWN else ("opp" if x < Z_OPP else "neutral")


def resample(path, n):
    """<= n points evenly along the path's arc length."""
    p = np.asarray(path, float)
    if len(p) <= n:
        return p
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
    if d[-1] < 1e-6:
        return p[:1]
    s = np.linspace(0, d[-1], n)
    return np.c_[np.interp(s, d, p[:, 0]), np.interp(s, d, p[:, 1])]


def medoid(paths, weights=None, n=24):
    """Path minimising the weighted mean distance to the others (all resampled to n points); None when empty."""
    ps = [resample(p, n) for p in paths if len(p) >= 2]
    ps = [p for p in ps if len(p) == n] or ps
    if not ps:
        return None
    w = np.ones(len(ps)) if weights is None else np.asarray(weights, float)[:len(ps)]
    if len(ps) == 1:
        return ps[0]
    k = min(len(p) for p in ps)
    A = np.stack([resample(p, k) for p in ps])
    D = np.array([[np.hypot(*(a - b).T).mean() for b in A] for a in A])
    return A[int(np.argmin((D * w[None, :]).sum(1)))]


def _wmean(v, w):
    v, w = np.asarray(v, float), np.asarray(w, float)
    return float((v * w).sum() / w.sum()) if len(v) and w.sum() > 0 else None


def _wsd(v, w):
    if len(v) < 2:
        return None
    m = _wmean(v, w)
    return float(math.sqrt(max(0.0, (np.asarray(w) * (np.asarray(v) - m) ** 2).sum() / np.sum(w))))


# ------------------------------------------------------------------ extraction from one site match file
def extract(m):
    """Robot records of one site m/<key>.json (tracked match).  Pure; cheap."""
    out = []
    hz = float(m.get("hz") or 10)
    t0 = float(m.get("t_start") or 0)
    per = {p["name"]: p for p in (m.get("periods") or [])}
    tr_t0, tr_t1 = (per.get("transition") or {}).get("t0", 20), (per.get("transition") or {}).get("t1", 30)
    shots = m.get("shots") or []
    intakes = m.get("intakes") or []
    for k, tr in enumerate(m.get("tracks") or []):
        al, team = tr.get("alliance"), tr.get("team")
        teams = (m.get(al) or {}).get("teams") or []
        if not team or team not in teams:
            continue
        xy, ob, cf = tr.get("xy") or [], tr.get("obs") or [], tr.get("conf") or []
        pts = []                                     # (match_t, cx, cy) gated frames
        for i, (p, o) in enumerate(zip(xy, ob)):
            if o and (cf[i] if i < len(cf) else 1) >= 0.5:
                cx, cy = canon(p[0] / 100.0, p[1] / 100.0, al)
                pts.append((i / hz - t0, cx, cy))
        an = (m.get("analytics") or [None] * (k + 1))[k] if k < len(m.get("analytics") or []) else None
        bp = ((an or {}).get("scoring") or {}).get("by_period") or {}
        rec = dict(key=m["key"], team=team, alliance=al, station=teams.index(team) + 1, track=k,
                   partners=[t for t in teams if t != team], pts=len(pts),
                   role=(an or {}).get("role"),
                   active={p: bool(((per.get(p) or {}).get("active") or {}).get(al, True)) for p in PERIODS},
                   made={p: (bp.get(p) or {}).get("m", 0) for p in PERIODS},
                   att={p: (bp.get(p) or {}).get("a", 0) for p in PERIODS})
        a = [(t, x, y) for t, x, y in pts if 0 <= t < 20]
        rec["start"] = [round(a[0][1], 2), round(a[0][2], 2)] if a and a[0][0] < 3 else None
        rec["auto_path"] = ([[round(x, 2), round(y, 2)] for _, x, y in a] if len(a) >= 20 else None)
        tp = [(x, y) for t, x, y in pts if tr_t0 <= t < tr_t1]
        rec["trans_path"] = [[round(x, 2), round(y, 2)] for x, y in tp] if len(tp) >= 20 else None
        mine = [s for s in shots if (s.get("shooter") or {}).get("track_idx") == k
                and (s.get("shooter") or {}).get("kind") == "robot"]
        rec["trans_scored"] = any(s.get("period") == "transition" and s.get("target") for s in mine)
        itk = [i for i in intakes if i.get("track_idx") == k]
        ints = []
        for i in itk:
            cx, cy = canon(i["xy"][0], i["xy"][1], al)
            ints.append((float(i.get("match_t", i.get("t", 0))), cx, cy, float(i.get("n") or 1)))
        rec["intakes"] = ints
        # cycles: teleop intake -> its next hub shot within 25 s, track slice between
        cyc = []
        hub = sorted(float(s["match_t"]) for s in mine if s.get("target") and s.get("match_t", 0) >= 20)
        for t, cx, cy, n in ints:
            if t < 20:
                continue
            nxt = [h for h in hub if t < h <= t + 25]
            if nxt:
                seg = [(x, y) for tt, x, y in pts if t - 0.5 <= tt <= nxt[0]]
                if len(seg) >= 5:
                    cyc.append(seg)
        rec["cycles"] = cyc
        rec["volleys"] = len(hub)
        rec["tele_min"] = max(1.0, (sum(1 for t, _, _ in [(p[0], 0, 0) for p in pts] if t >= 20) / hz) / 60.0)
        # inactive-hub job
        act = [(p["t0"], p["t1"]) for p in (m.get("periods") or [])
               if p["name"].startswith("shift") and not (p.get("active") or {}).get(al, True)]
        rec["inactive"] = classify_inactive(m, k, al, team, act, pts, mine, ints)
        out.append(rec)
    return out


def classify_inactive(m, k, al, team, windows, pts, shots, ints):
    """Job during the alliance's inactive shifts: defense / ferry_neutral / robber / collect (None if no data)."""
    inw = lambda t: any(a <= t < b for a, b in windows)
    zp = [zone_of(x) for t, x, _ in pts if inw(t)]
    if len(zp) < 30:
        return None
    ev = {}
    ev["defense_s"] = round(float(((m.get("analytics") or [{}] * (k + 1))[k] or {}).get("defense", {}).get("time_s") or 0), 1)
    ev["defense_ep"] = sum(1 for e in (m.get("_defense_eps") or []) if e.get("defender") == team)
    ev["opp_frac"] = round(zp.count("opp") / len(zp), 2)
    ev["opp_intakes"] = sum(1 for t, x, _, n in ints if inw(t) and zone_of(x) == "opp")
    ev["passes_neutral"] = sum(1 for s in shots if s.get("outcome") == "pass" and inw(s.get("match_t", -1))
                               and zone_of(canon(s["origin"][0], s["origin"][1], al)[0]) == "neutral")
    ev["intakes"] = sum(1 for t, *_ in ints if inw(t))
    if ev["defense_ep"] or ev["defense_s"] >= 4:
        role = "defense"
    elif ev["opp_intakes"] >= 2 or ev["opp_frac"] >= 0.25:
        role = "robber"
    elif ev["passes_neutral"] >= 3:
        role = "ferry_neutral"
    else:
        role = "collect"
    return dict(role=role, ev=ev)


# ------------------------------------------------------------------ the conditioned model for one robot
def station_sides(recs):
    """Empirical station -> side map from canonical start y across the event: {station: 'L'|'R'|None}."""
    c = {}
    for r in recs:
        if r.get("start"):
            c.setdefault(r["station"], []).append(side_of(r["start"][1]))
    out = {}
    for s, v in c.items():
        l = v.count("L") / len(v)
        out[s] = "L" if l >= SIDE_CLEAR else ("R" if 1 - l >= SIDE_CLEAR else None)
    return out


def select(hist, station, partners, smap):
    """Pick the conditioned matches and weights.  Returns (recs, weights, how, partner_rows)."""
    same = [r for r in hist if r["station"] == station]
    how, sel = f"station {station}", same
    if not same:
        sd = smap.get(station)
        if sd:
            sel = [r for r in hist if r.get("start") and side_of(r["start"][1]) == sd]
            how = f"side {sd}"
        if not sel:
            sel, how = list(hist), "all"
    tog = {}
    for r in hist:
        for p in r["partners"]:
            tog[p] = tog.get(p, 0) + 1
    prow = [dict(team=p, together=n, weight=PARTNER_W) for p, n in tog.items() if p in partners and n >= PARTNER_MIN]
    pset = {p["team"] for p in prow}
    w = [PARTNER_W if pset & set(r["partners"]) else 1.0 for r in sel]
    return sel, w, how, prow


def _phr(how, station, n, total):
    if how == f"station {station}":
        return f"{n} of its {total} matches from station {station}"
    if how.startswith("side"):
        return f"no matches from station {station} yet: using its {n} from the {how[5:]} side"
    return f"no matches from station {station} yet: using all {total}" if n == total else f"using {n} of {total}"


def team_plan(hist, station, partners, smap):
    """hist = the team's records from matches before the target match (any order).  Returns the robot-plan dict
    (without alliance/xy conversions: canonical frame) or an `enough: False` stub with a plain why."""
    n_all = len(hist)
    if n_all < MIN_MATCHES:
        return dict(enough=False, n_video=n_all,
                    why=f"only {n_all} tracked match{'es' if n_all != 1 else ''} (needs {MIN_MATCHES})")
    sel, w, how, prow = select(hist, station, partners, smap)
    wt = f", {sum(1 for x in w if x > 1)} with a current partner weighted x{PARTNER_W:g}" if prow else ""
    base = _phr(how, station, len(sel), n_all)
    P = dict(enough=True, why=None, n_video=n_all, partners=prow, how=how, n_sel=len(sel))
    # start
    st = [(r["start"], x) for r, x in zip(sel, w) if r.get("start")]
    if st:
        ys = [s[1] for s, _ in st]
        ww = [x for _, x in st]
        sd_ = "L" if _wmean([1.0 if y < WID / 2 else 0.0 for y in ys], ww) >= 0.5 else "R"
        grp = [(s, x) for s, x in st if side_of(s[1]) == sd_]
        P["start"] = dict(xy=[_wmean([s[0] for s, _ in grp], [x for _, x in grp]),
                              _wmean([s[1] for s, _ in grp], [x for _, x in grp])], side=sd_, n=len(st),
                          basis=f"{base}; started on the {sd_} side in {len(grp)} of {len(st)}{wt}")
    # auto
    ap = [(r, x) for r, x in zip(sel, w) if r.get("auto_path")]
    if not ap or not P.get("start"):
        return dict(enough=False, n_video=n_all, why="no usable auto path tracked yet")
    # only autos from the chosen start side (never blend left and right into a path through the middle)
    sd_ = P["start"]["side"]
    apx = [(r, x) for r, x in ap if r.get("start") and side_of(r["start"][1]) == sd_] or ap
    path = medoid([r["auto_path"] for r, _ in apx], [x for _, x in apx])
    pts = [r["made"]["auto"] for r, _ in apx]
    ww = [x for _, x in apx]
    P["auto"] = dict(path=path.tolist(), n=len(apx), pts=dict(mean=_wmean(pts, ww), sd=_wsd(pts, ww)),
                     fuel=dict(mean=_wmean(pts, ww), sd=_wsd(pts, ww)), routine=None,
                     basis=f"medoid of {len(apx)} {sd_}-side autos ({base})")
    # transition
    tp = [(r, x) for r, x in zip(sel, w) if r.get("trans_path")]
    if tp:
        P["transition"] = dict(path=medoid([r["trans_path"] for r, _ in tp], [x for _, x in tp], 16).tolist(),
                               scores_p=_wmean([1.0 if r["trans_scored"] else 0.0 for r, _ in tp], [x for _, x in tp]),
                               n=len(tp), basis=f"{len(tp)} transition periods ({base})")
    else:
        P["transition"] = None
    # cycle / collection heat (teleop intakes, observed positions only; recall ~0.45 so counts are a floor)
    cells, tot = {}, 0.0
    cxs, cw = [], []
    cyc, cycw = [], []
    for r, x in zip(sel, w):
        for t, cx, cy, n in r["intakes"]:
            if t < 20:
                continue
            ix, iy = min(33, max(0, int(cx / 0.5))), min(16, max(0, int(cy / 0.5)))
            cells[(ix, iy)] = cells.get((ix, iy), 0) + x
            tot += x
            cxs.append((cx, cy)); cw.append(x)
        for c in r["cycles"]:
            cyc.append(c); cycw.append(x)
    if cxs:
        mx = max(cells.values())
        ax = _wmean([c[0] for c in cxs], cw); ay = _wmean([c[1] for c in cxs], cw)
        spread = float(math.sqrt(_wmean([(c[0] - ax) ** 2 + (c[1] - ay) ** 2 for c in cxs], cw)))
        per_min = _wmean([r["volleys"] / r["tele_min"] for r in sel], w)
        P["cycle"] = dict(path=(medoid(cyc, cycw, 16).tolist() if cyc else None), per_min=per_min,
                          collect=dict(xy=[ax, ay], spread_m=spread),
                          heat=dict(cell=0.5, nx=34, ny=17,
                                    cells=[[ix, iy, round(v / mx, 3)] for (ix, iy), v in sorted(cells.items())]),
                          n=len(sel), basis=f"{base}{wt}; intake recall is about 45 %, so counts are a floor")
    else:
        P["cycle"] = None
    # inactive job
    inl = [(r["inactive"]["role"], x) for r, x in zip(sel, w) if r.get("inactive")]
    if inl:
        tw = sum(x for _, x in inl)
        pr = {k: round(sum(x for ro, x in inl if ro == k) / tw, 3) for k in ROLES}
        top = max(pr, key=pr.get)
        P["inactive"] = dict(role=top, p=pr, n=len(inl), basis=f"{top} in {sum(1 for ro, _ in inl if ro == top)} of {len(inl)} "
                                                              f"inactive-hub periods ({base})")
    else:
        P["inactive"] = None
    # scoring rates per period (all tracked matches of the team, station-agnostic: fuel is not position-bound)
    P["by_period"] = {}
    for p in PERIODS:                      # shifts: only matches where the hub was active in that shift
        rr = [r for r in hist if r["active"].get(p, True)] or hist
        P["by_period"][p] = (_wmean([r["made"][p] for r in rr], [1] * len(rr)), _wsd([r["made"][p] for r in rr], [1] * len(rr)))
    return P
