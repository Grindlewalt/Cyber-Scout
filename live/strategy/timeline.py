"""The play-through model: where a robot is, second by second, over the match clock, predicted from the same team's
EARLIER tracked matches (station-conditioned and partner-weighted exactly like behavior.py).

Output of `team_timeline` (field frame, metres, blue wall x=0) feeds mp/<match>.json `robots[].timeline`,
`tele.cycles_per_shift`, `tele.path_heat` (see live/strategy/README.md, .work/redesign/CONTRACT2.md).

A timeline is a plausible path, not a mean (an average of a team that goes left half the time and right half the time
sits in the middle, where it never is).  Versions (OPTS) are measured by tools/eval_timeline.py, leave-future-out:

  v1  weighted medoid of the selected matches' per-second tracks (a real trajectory), gaps filled, speed-limited.
  v2  Viterbi path through the time-binned density of those tracks under a speed limit (the most likely route,
      not one match's jitter), refined to the local weighted mean.
  v3  v2 + event anchoring: the typical first-score spot is pulled into the path at the typical first-score time.

No team, match or position is hard-coded; every number comes from the selected matches.
"""
import math

import numpy as np

from . import behavior as B

VERSION = "v6"
T = 161                         # samples 0..160 s on the match clock (auto start = 0)
VMAX = 3.8                      # m/s ceiling for a plausible step (FRC robots top out near 4-5 m/s; tracks are noisy)
GAP = 6                         # fill observed-gaps up to this many seconds by interpolation
CELL = 0.25                     # path_heat grid (m)
NX, NY = 67, 33
PHASES = [("auto", 0, 20), ("transition", 20, 30), ("shift1", 30, 55), ("shift2", 55, 80), ("shift3", 80, 105),
          ("shift4", 105, 130), ("endgame", 130, 160)]
CPS_KEYS = ["transition", "shift1", "shift2", "shift3", "shift4", "endgame"]
OPTS = {
    "v1": dict(method="medoid", anchor=False),
    "v2": dict(method="viterbi", anchor=False),
    "v3": dict(method="viterbi", anchor=True),
    "v4": dict(method="viterbi", anchor=True, kappa=2.0),
    "v5": dict(method="viterbi", anchor=True, kappa=2.0, alpha=0.5),
    "v6": dict(method="viterbi", anchor=True, kappa=4.0, alpha=0.5),
    "v7": dict(method="viterbi", anchor=True, kappa=2.0, alpha=0.75),
}
MIN_HIST = 1                    # tracked earlier matches needed (fewer: no timeline)
MIN_OBS = 40                    # observed seconds a track needs to count
EV_MIN_P = 0.25                 # an event is predicted when at least this share of the selected matches had it


# ------------------------------------------------------------------ helpers
def fill(tl, gap=GAP):
    """Linear-interpolate NaN gaps of at most `gap` seconds inside the observed range (copy)."""
    a = np.array(tl, float)
    ok = ~np.isnan(a[:, 0])
    if ok.sum() < 2:
        return a
    idx = np.where(ok)[0]
    for i0, i1 in zip(idx[:-1], idx[1:]):
        if 1 < i1 - i0 <= gap + 1:
            for c in (0, 1):
                a[i0 + 1:i1, c] = np.interp(np.arange(i0 + 1, i1), [i0, i1], [a[i0, c], a[i1, c]])
    return a


def limit_speed(p, vmax=VMAX, dt=1.0):
    """Forward and backward step-clamped copies averaged: every step <= vmax*dt (convexity keeps the bound)."""
    p = np.asarray(p, float)
    lim = vmax * dt

    def one(q):
        o = q.copy()
        for i in range(1, len(o)):
            d = o[i] - o[i - 1]
            n = math.hypot(*d)
            if n > lim:
                o[i] = o[i - 1] + d * (lim / n)
        return o
    f = one(p)
    b = one(p[::-1])[::-1]
    return 0.5 * (f + b)


def smooth(p, w=(0.25, 0.5, 0.25)):
    q = np.array(p, float)
    r = q.copy()
    r[1:-1] = w[0] * q[:-2] + w[1] * q[1:-1] + w[2] * q[2:]
    return r


def _wmedian(v, w):
    v, w = np.asarray(v, float), np.asarray(w, float)
    o = np.argsort(v)
    c = np.cumsum(w[o])
    return float(v[o][int(np.searchsorted(c, c[-1] / 2.0))])


def _stack(sel):
    """(n,T,2) filled tracks of the selected records with >= MIN_OBS observed seconds, and the kept indices."""
    keep = [i for i, r in enumerate(sel) if r.get("tl") is not None and int((~np.isnan(r["tl"][:, 0])).sum()) >= MIN_OBS]
    return np.stack([fill(sel[i]["tl"]) for i in keep]) if keep else np.zeros((0, T, 2)), keep


def _pair_dist(A):
    n = len(A)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                d = np.hypot(*(A[i] - A[j]).T)
                d = d[~np.isnan(d)]
                D[i, j] = d.mean() if len(d) >= 30 else 6.0
    return D


def _complete(path, A, w):
    """Fill NaN seconds of a path from the other tracks' weighted median at that second, then nearest valid."""
    p = path.copy()
    for t in range(len(p)):
        if np.isnan(p[t, 0]):
            ok = ~np.isnan(A[:, t, 0])
            if ok.any():
                p[t] = [_wmedian(A[ok, t, c], w[ok]) for c in (0, 1)]
    good = ~np.isnan(p[:, 0])
    if not good.any():
        return None
    gi = np.where(good)[0]
    for c in (0, 1):
        p[:, c] = np.interp(np.arange(len(p)), gi, p[gi, c])
    return p


# ------------------------------------------------------------------ path methods
def path_medoid(A, w):
    D = _pair_dist(A)
    i = int(np.argmin((D * w[None, :]).sum(1))) if len(A) > 1 else 0
    return _complete(A[i], A, w)


_G = 0.75
_GX, _GY = int(math.ceil(B.LEN / _G)), int(math.ceil(B.WID / _G))
_CX = (np.arange(_GX) + 0.5) * _G
_CY = (np.arange(_GY) + 0.5) * _G
_CELLS = np.array([(x, y) for x in _CX for y in _CY])
_DD = np.hypot(_CELLS[:, None, 0] - _CELLS[None, :, 0], _CELLS[:, None, 1] - _CELLS[None, :, 1])


def path_viterbi(A, w, sigma=1.1, lam=0.08):
    """Most likely route through the time-binned weighted density of the tracks, steps limited to VMAX."""
    n = len(A)
    w = w / w.sum()
    ok = ~np.isnan(A[..., 0])
    Z = np.where(ok[..., None], A, 1e6)                                   # (n,T,2); unobserved never near a cell
    d2 = ((Z[:, :, None, :] - _CELLS[None, None, :, :]) ** 2).sum(3)       # (n,T,C)
    k = np.exp(-d2 / (2 * sigma ** 2)) * ok[..., None] * w[:, None, None]
    dens = k.sum(0)                                                        # (T,C)
    sm = dens.copy()
    sm[1:] += dens[:-1]
    sm[:-1] += dens[1:]
    cost = -np.log(sm / 3.0 + 0.03)
    trans = np.where(_DD <= VMAX * 1.0, lam * _DD, np.inf)
    V = cost[0].copy()
    bp = np.zeros((T, len(_CELLS)), int)
    cols = np.arange(len(_CELLS))
    for t in range(1, T):
        M = V[:, None] + trans
        bp[t] = np.argmin(M, axis=0)
        V = M[bp[t], cols] + cost[t]
    s = int(np.argmin(V))
    seq = [s]
    for t in range(T - 1, 0, -1):
        s = bp[t, s]
        seq.append(s)
    p = _CELLS[seq[::-1]].copy()
    for t in range(T):                       # refine: local weighted mean of the tracks near the chosen cell
        lo, hi = max(0, t - 1), min(T, t + 2)
        pts, ww = A[:, lo:hi].reshape(-1, 2), np.repeat(w, hi - lo)
        m = ~np.isnan(pts[:, 0])
        m[m] = np.hypot(*(pts[m] - p[t]).T) < 1.2
        if m.any():
            p[t] = (pts[m] * ww[m, None]).sum(0) / ww[m].sum()
    return p


def anchor(path, t_ev, xy, sd_t=3.0, max_pull=3.0):
    """Pull the path toward a typical event spot around its typical time (gaussian in time), then re-limit speed."""
    if xy is None or t_ev is None:
        return path
    p = path.copy()
    i = int(round(t_ev))
    if not (0 <= i < len(p)):
        return path
    d = np.asarray(xy, float) - p[i]
    n = math.hypot(*d)
    if n > max_pull:
        d = d * (max_pull / n)
    ts = np.arange(len(p))
    g = np.exp(-((ts - t_ev) / max(2.0, sd_t)) ** 2 / 2.0)
    return p + g[:, None] * d[None, :]


# ------------------------------------------------------------------ events, cycles, heat
def _events(sel, w, path, opp_rate=None):
    """Event list on the predicted path: weighted-median time over the matches that had it, p = their weighted share."""
    out, wt = [], float(sum(w))
    for kind, key in (("first_pickup", "first_pickup"), ("cross_neutral", "cross_neutral"), ("first_score", "first_score"),
                      ("first_defense", "first_defense"), ("climb", "climb")):
        have = [(r["ev"][key], x, r) for r, x in zip(sel, w) if r.get("ev") and r["ev"].get(key) is not None]
        if not have or sum(x for _, x, _ in have) / wt < EV_MIN_P:
            continue
        ts = [t for t, _, _ in have]
        ww = [x for _, x, _ in have]
        t = _wmedian(ts, ww)
        e = dict(t=round(float(min(max(t, 0.0), 160.0)), 1), kind=kind, p=round(float(sum(ww) / wt), 2),
                 sd_t=round(float(B._wsd(ts, ww) or 0.0), 1))
        out.append(e)
    out.sort(key=lambda e: e["t"])
    return out


def cycles_per_shift(hist):
    """Bursts of hub shots per period from ALL the team's earlier tracked matches (a burst is not position-bound).
    Shifts count only matches where the hub was active in that shift."""
    out = {}
    for p in CPS_KEYS:
        rr = [r for r in hist if r.get("bursts") is not None and (not p.startswith("shift") or r["active"].get(p, True))] or \
             [r for r in hist if r.get("bursts") is not None]
        if not rr:
            continue
        v = [r["bursts"][p] for r in rr]
        out[p] = dict(mean=round(B._wmean(v, [1] * len(v)), 2), sd=round(B._wsd(v, [1] * len(v)) or 0.0, 2), n=len(v))
    return out


def raster(segs, w, al, cell=CELL, nx=NX, ny=NY):
    """Rasterize path SEGMENTS (canonical xy lists) as line density (each ~cell/2 of length adds to its cell), blur
    slightly, normalise to max 1; returns sparse [[ix, iy, w], ...] in the FIELD frame."""
    g = np.zeros((nx, ny))
    for seg, x in zip(segs, w):
        s = np.asarray(seg, float)
        if al == "blue":
            s = np.c_[B.LEN - s[:, 0], B.WID - s[:, 1]]
        for a, b in zip(s[:-1], s[1:]):
            L = math.hypot(*(b - a))
            if L > 6.0:                     # tracking jump, not a route
                continue
            k = max(1, int(L / (cell / 2)))
            for u in (np.arange(k) + 0.5) / k:
                q = a + (b - a) * u
                ix, iy = int(q[0] / cell), int(q[1] / cell)
                if 0 <= ix < nx and 0 <= iy < ny:
                    g[ix, iy] += x * L / k
    k3 = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], float) / 16.0
    P = np.pad(g, 1)
    h = sum(k3[a, b] * P[a:a + nx, b:b + ny] for a in range(3) for b in range(3))
    mx = h.max()
    if mx <= 0:
        return None
    h = h / mx
    cells = [[int(ix), int(iy), round(float(h[ix, iy]), 3)] for ix, iy in zip(*np.where(h >= 0.04))]
    return dict(cell=cell, nx=nx, ny=ny, cells=cells)


# ------------------------------------------------------------------ one robot
def _phases():
    return [dict(phase=p, t0=a, t1=b) for p, a, b in PHASES]


def cluster_median(A, w, win=3):
    """Per-second weighted median (+-win s) of the tracks that move like the medoid (the team's dominant pattern; a
    team that goes left half the time and right half is not averaged into the middle), NaN-aware, filled."""
    D = _pair_dist(A)
    i = int(np.argmin((D * w[None, :]).sum(1))) if len(A) > 1 else 0
    thr = max(2.5, float(np.median(D[i])) * 1.2)
    use = [j for j in range(len(A)) if j == i or D[i, j] <= thr]
    q = np.full((T, 2), np.nan)
    for t in range(T):
        v = A[use, max(0, t - win):t + win + 1]
        ww = np.repeat(w[use][:, None], v.shape[1], 1)
        ok = ~np.isnan(v[..., 0])
        if ok.any():
            q[t] = [_wmedian(v[..., c][ok], ww[ok]) for c in (0, 1)]
    return _complete(q, A[use], w[use])


def team_timeline(hist, station, partners, smap, al, version=VERSION, sel_override=None, prior=None):
    """hist = the team's records from matches BEFORE the target.  Returns None when there is nothing to build from, else
    dict(timeline, cycles_per_shift, path_heat) in the field frame for alliance `al`.  `timeline["_canon"]` carries
    the canonical (T,2) path for the evaluator (callers drop it)."""
    if len(hist) < MIN_HIST:
        return None
    o = OPTS[version]
    sel, w, how, prow = B.select(hist, station, partners, smap) if sel_override is None else sel_override
    A, keep = _stack(sel)
    if len(A) == 0:                                       # none of the conditioned matches has a usable track: all of them
        sel, w, how = list(hist), [1.0] * len(hist), "all"
        A, keep = _stack(sel)
    if len(A) == 0:
        return None
    sel = [sel[i] for i in keep]
    w = np.asarray([w[i] for i in keep], float)
    A0, w0 = A, w
    if o.get("kappa") and prior:                        # shrink toward what this station does at this event
        PA, pk = _stack(prior)
        if len(PA):
            take = np.linspace(0, len(PA) - 1, min(len(PA), 30)).astype(int)
            A = np.concatenate([A, PA[take]])
            w = np.concatenate([w, np.full(len(take), o["kappa"] / len(take))])
    path = path_medoid(A, w) if o["method"] == "medoid" else path_viterbi(A, w)
    if path is None:
        return None
    if o.get("alpha"):
        q = cluster_median(A0, w0)
        if q is not None:
            path = (1 - o["alpha"]) * path + o["alpha"] * q
    evs = _events(sel, w0, path)
    if o["anchor"]:
        fs = [(r["ev"]["first_score"], x, r["ev"]["first_score_xy"]) for r, x in zip(sel, w0)
              if r["ev"].get("first_score") is not None and r["ev"].get("first_score_xy")]
        e = next((e for e in evs if e["kind"] == "first_score"), None)
        if e and fs:
            near = [(t, x, xy) for t, x, xy in fs if abs(t - e["t"]) <= 12]
            if near:
                xs = [_wmedian([xy[0] for *_, xy in near], [x for _, x, _ in near]),
                      _wmedian([xy[1] for *_, xy in near], [x for _, x, _ in near])]
                path = anchor(path, e["t"], xs, sd_t=max(2.0, e["sd_t"] / 2))
    path = limit_speed(smooth(path))
    path[:, 0] = np.clip(path[:, 0], 0.3, B.LEN - 0.3)
    path[:, 1] = np.clip(path[:, 1], 0.3, B.WID - 0.3)
    # spread: weighted median distance from the path to the observed tracks at that second (+-2 s), floor 0.5 m
    spread = np.zeros(T)
    for t in range(T):
        d, ww = [], []
        for i in range(len(A)):
            for tt in range(max(0, t - 2), min(T, t + 3)):
                if not np.isnan(A[i, tt, 0]):
                    d.append(math.hypot(*(A[i, tt] - path[t]))); ww.append(w[i])
        spread[t] = _wmedian(d, ww) if d else 3.0
    spread = np.maximum(0.5, smooth(np.c_[spread, spread], (0.25, 0.5, 0.25))[:, 0])
    for e in evs:
        i = int(round(min(e["t"], 160)))
        e["xy"] = list(map(float, path[i]))
    fld = lambda q: [round(float(v), 2) for v in (B.canon(q[0], q[1], al))]
    for e in evs:
        e["xy"] = fld(e["xy"])
    if any(e["kind"] == "first_defense" for e in evs):    # who: the opponent it most often defended
        pass
    n_sel = len(sel)
    w = w0
    basis = (f"{n_sel} of its {len(hist)} tracked matches ({how}"
             f"{', ' + str(sum(1 for x in w if x > 1)) + ' with a current partner weighted x' + format(B.PARTNER_W, 'g') if prow else ''}); "
             f"play-through model {version}")
    tl = dict(dt=1.0, xy=[fld(q) for q in path], spread=[round(float(s), 2) for s in spread], phases=_phases(),
              events=evs, basis=basis, _canon=path)
    segs, sw = [], []
    for r, x in zip(sel, w):
        for c in r.get("cycles") or []:
            segs.append(c); sw.append(x)
    return dict(timeline=tl, cycles_per_shift=cycles_per_shift(hist), path_heat=raster(segs, sw, al))


def actual_timeline(rec, al):
    """1 Hz observed track of a played match (field frame), null where the robot was not seen."""
    if not rec or rec.get("tl") is None:
        return None
    xy = [None if np.isnan(q[0]) else [round(float(v), 2) for v in B.canon(q[0], q[1], al)] for q in rec["tl"]]
    if sum(1 for q in xy if q) < 10:
        return None
    ev = rec.get("ev") or {}
    return dict(dt=1.0, xy=xy, events=[dict(t=round(float(ev[k]), 1), kind=k) for k in
                                       ("first_pickup", "cross_neutral", "first_score", "first_defense", "climb")
                                       if ev.get(k) is not None])


# ------------------------------------------------------------------ honest accuracy (predicted vs observed)
def score(tlr, rec):
    """Errors of one predicted timeline against the robot's observed record (canonical frame); None if unobservable."""
    act = rec.get("tl")
    if tlr is None or act is None or int((~np.isnan(act[:, 0])).sum()) < 20:
        return None
    path = tlr["timeline"]["_canon"]
    d = np.hypot(*(path - act).T)
    ok = ~np.isnan(d)
    zone = lambda x: 0 if x >= B.Z_OWN else (2 if x < B.Z_OPP else 1)
    ts = np.where(ok)[0]
    e = next((e for e in tlr["timeline"]["events"] if e["kind"] == "first_score"), None)
    fs = abs(e["t"] - rec["ev"]["first_score"]) if e and rec["ev"].get("first_score") is not None else None
    cps = [abs(tlr["cycles_per_shift"][k]["mean"] - rec["bursts"][k]) for k in CPS_KEYS
           if k in tlr["cycles_per_shift"] and rec["active"].get(k, True)]
    stay = np.hypot(*(path[0] - act).T)[ok]
    return dict(err=d[ok], t=ts, zone=np.array([zone(path[i, 0]) == zone(act[i, 0]) for i in ts], float),
                fs=fs, cps=cps, stay=stay)


def calib_summary(scores):
    """Aggregate `score` results into mp/index.json calib.timeline."""
    sc = [s for s in scores if s]
    if not sc:
        return None
    err = np.concatenate([s["err"] for s in sc])
    ts = np.concatenate([s["t"] for s in sc])
    by = {}
    for p, a, b in PHASES:
        m = (ts >= a) & (ts < b)
        if m.any():
            by[p] = round(float(np.median(err[m])), 2)
    fs = [s["fs"] for s in sc if s["fs"] is not None]
    cps = [c for s in sc for c in s["cps"]]
    return dict(version=VERSION, n_robots=len(sc),
                pos_err_m=dict(median=round(float(np.median(err)), 2), by_phase=by),
                zone_hit=round(float(np.concatenate([s["zone"] for s in sc]).mean()), 3),
                first_score_t_err_s=round(float(np.median(fs)), 1) if fs else None,
                cycles_per_shift_mae=round(float(np.mean(cps)), 2) if cps else None,
                baseline_stay_pos_err_m=round(float(np.median(np.concatenate([s["stay"] for s in sc]))), 2))
