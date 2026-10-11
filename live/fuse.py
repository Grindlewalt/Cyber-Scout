"""Wide-camera-only identity: pure tracklets -> exact K=3 path cover per alliance -> RTS.

A port of the wide-only path of src/fuse_track.py (same tracklet builder, same
duplicate merge, same min-cost-flow identity solve and smoother), with the frame
count and inputs passed in instead of read from fixed files.  Requires the geom
shim (camera.install_geom_shim) to be installed before import.
"""
import os, numpy as np, networkx as nx
import track as T, fit6, assign as AS

E = os.environ.get
MISS = int(E("MISS", "20"))
DUP_M = float(E("DUP_M", "0.7"))
W_M, W_H, W_C, W_R = (float(E("W_M", "2.0")), float(E("W_H", "1.0")),
                      float(E("W_C", "0.5")), float(E("W_R", "0.10")))
SIG_H = float(E("SIG_H", "0.10"))
MAXGAP_S = float(E("MAXGAP_S", "10"))
W_G = float(E("W_G", "0.5"))
MARGIN = 0.3
K = 3


class Tk:
    __slots__ = ("fr", "xs", "cv", "al", "gi", "h", "hs", "ex", "n")
    def __init__(self, fr, xs, cv, al, hs, ex, gi):
        self.fr, self.xs, self.cv, self.al = fr, xs, cv, al
        self.hs, self.ex, self.gi, self.n = hs, ex, gi, len(fr)
        self.h = float(np.median(hs)) if len(hs) >= 3 else np.nan
    @property
    def f0(self): return int(self.fr[0])
    @property
    def f1(self): return int(self.fr[-1])
    def at(self, fs):
        p = np.c_[np.interp(fs, self.fr, self.xs[:, 0]), np.interp(fs, self.fr, self.xs[:, 1])]
        c = np.stack([np.interp(fs, self.fr, self.cv[:, i, j]) for i in range(2) for j in range(2)], 1).reshape(-1, 2, 2)
        return p, c


def tracklets(det, xy, cov, hs, ex):
    T.MISS_MAX = MISS
    order = np.argsort(det[:, 0].astype(int), kind="stable")
    out = []
    for t in T.build_tracklets(det, xy, cov):
        gi = order[np.array(t.di, int)]
        al = [a for a in t.al if a]; al = max(set(al), key=al.count) if al else 0
        h = hs[gi]; h = h[np.isfinite(h)]
        out.append(Tk(np.array(t.fr), np.array(t.xs), cov[gi], al, h, ex[gi], gi))
    return out


def merge_same_view(tks):
    n = len(tks); par = list(range(n))
    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for i in range(n):
        for j in range(i + 1, n):
            a, b = tks[i], tks[j]
            if a.al and b.al and a.al != b.al: continue
            lo, hi = max(a.f0, b.f0), min(a.f1, b.f1)
            if hi - lo + 1 < 3: continue
            fs = np.arange(lo, hi + 1)
            if np.median(np.linalg.norm(a.at(fs)[0] - b.at(fs)[0], axis=1)) < DUP_M:
                ri, rj = find(i), find(j)
                if ri != rj: par[ri] = rj
    groups = {}
    for i in range(n): groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        if len(g) == 1:
            out.append(tks[g[0]]); continue
        acc = {}
        for i in g:
            for f, p, c in zip(tks[i].fr, tks[i].xs, tks[i].cv):
                acc.setdefault(int(f), []).append((p, c))
        fr = np.array(sorted(acc)); xs = np.zeros((len(fr), 2)); cv = np.zeros((len(fr), 2, 2))
        for k, f in enumerate(fr):
            I = sum(np.linalg.inv(c) for _, c in acc[f]); b = sum(np.linalg.inv(c) @ p for p, c in acc[f])
            cv[k] = np.linalg.inv(I); xs[k] = cv[k] @ b
        al = [tks[i].al for i in g if tks[i].al]; al = max(set(al), key=al.count) if al else 0
        hs = np.concatenate([tks[i].hs for i in g])
        ex = np.concatenate([tks[i].ex for i in g])
        gi = np.concatenate([tks[i].gi for i in g])
        if len(ex) > AS.NEX: ex = ex[np.random.default_rng(0).choice(len(ex), AS.NEX, replace=False)]
        out.append(Tk(fr, xs, cv, al, hs, ex, gi))
    return out


def link_cost(a, b):
    gap = (b.f0 - a.f1) * T.DT
    if gap <= 0 or gap > MAXGAP_S: return np.inf
    d = np.linalg.norm(b.xs[0] - a.xs[-1]); reach = T.VMAX * gap + MARGIN
    if d > reach: return np.inf
    c = W_M * (d / reach) ** 2 + W_G * gap
    if np.isfinite(a.h) and np.isfinite(b.h):
        c += W_H * min(((a.h - b.h) / SIG_H) ** 2, 9.0)
    if len(a.ex) and len(b.ex):
        c += W_C * max(AS.bhat_sets(a.ex, b.ex) - 0.03, 0.0) / 0.02
    return c


def solve(tks, k=K):
    n = len(tks); G = nx.DiGraph(); SC = 1000
    G.add_node("s", demand=-k); G.add_node("t", demand=k)
    for i in range(n):
        G.add_edge("s", ("i", i), capacity=1, weight=0)
        G.add_edge(("i", i), ("o", i), capacity=1, weight=-int(round(SC * W_R * tks[i].n)))
        G.add_edge(("o", i), "t", capacity=1, weight=0)
    for i in range(n):
        for j in range(n):
            if i == j or tks[j].f0 <= tks[i].f1: continue
            c = link_cost(tks[i], tks[j])
            if np.isfinite(c): G.add_edge(("o", i), ("i", j), capacity=1, weight=int(round(SC * c)))
    flow = nx.min_cost_flow(G)
    paths = []
    for i in range(n):
        if flow["s"].get(("i", i), 0) != 1: continue
        p, cur = [], i
        while True:
            p.append(cur)
            nxt = [v for v, f in flow[("o", cur)].items() if f == 1 and v != "t"]
            if not nxt: break
            cur = nxt[0][1]
        paths.append(p)
    return paths


def run(det, xy, cov, hs, ex, nf, LEN, WID, log=print):
    """-> list of six dicts {alliance, xy (nf,2), v (nf,2), obs (nf,) bool, P, det_idx, height}."""
    wide = merge_same_view(tracklets(det, xy, cov, hs, ex))
    tracks = []
    for a, name in ((1, "red"), (2, "blue")):
        sub = [t for t in wide if t.al == a]
        if not sub:
            log(f"  {name}: no tracklets"); continue
        paths = solve(sub, k=min(K, len(sub)))
        log(f"  {name}: {len(sub)} tracklets -> {[len(p) for p in paths]} per identity")
        for p in paths:
            obs = {}
            for i in p:
                for f, z, R in zip(sub[i].fr, sub[i].xs, sub[i].cv): obs[int(f)] = (z, R)
            f0 = min(obs); xs, P = fit6.rts(obs, nf, np.r_[obs[f0][0], 0, 0])
            xs[:, 0] = np.clip(xs[:, 0], 0, LEN); xs[:, 1] = np.clip(xs[:, 1], 0, WID)
            m = np.zeros(nf, bool); m[list(obs)] = True
            hh = np.concatenate([sub[i].hs for i in p]) if p else np.zeros(0)
            tracks.append(dict(alliance=name, xy=xs[:, :2], v=xs[:, 2:], obs=m, P=P[:, :2, :2],
                               det_idx=np.concatenate([sub[i].gi for i in p]),
                               height=float(np.median(hh)) if len(hh) else float("nan")))
    return tracks
