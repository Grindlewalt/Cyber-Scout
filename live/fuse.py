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


# =============================================================================== labeled
# Identity from bumper numbers: each output track is ONE team for the whole match.
#   detections carry OCR reads (team, CTC log-prob) -> pure Kalman tracklets, cut where
#   their reads change team -> duplicate merge (never across conflicting reads/colours)
#   -> per-tracklet evidence: reads (team) + bumper colour votes (alliance) -> ONE joint
#   ILP over all six teams: each team is a path through time-ordered tracklets (no two
#   at once, except a <= OVL_F-frame hand-over between two pieces of one robot), linked
#   either directly (motion-feasible gap <= MAXGAP_S, cost link_cost) or through "limbo"
#   (robot not tracked for a while: fixed cost J_LIMBO, no position claim).  Reads of team T make any other label expensive; colour makes the other
#   alliance expensive; a tracklet may stay unassigned (false positive / duplicate /
#   nobody's).  Frames in limbo are exported obs=0, conf=0 ("unknown") rather than a
#   held or interpolated position.
from scipy.optimize import milp as _milp, LinearConstraint as _LC, Bounds as _Bounds
from scipy import sparse as _sp

Q_OCR = float(E("Q_OCR", "0.97"))          # P(read names the robot it is attributed to)
A_OCR = float(E("A_OCR", "1.0"))           # temper: reads of one tracklet are correlated
W_ID = float(E("W_ID", "1.0"))             # nats -> cost
ID_CAP = float(E("ID_CAP", "200"))
W_COLV = float(E("W_COLV", "0.3"))         # cost per opposite-colour vote (> W_R: colour beats coverage)
COL_NOISE = float(E("COL_NOISE", "0.15"))  # tolerated fraction of wrong colour votes
COL_CAP = float(E("COL_CAP", "10"))        # colour is correlated within a tracklet (a robot parked
                                           # by a blue field element reads blue for minutes)
J_LIMBO = float(E("J_LIMBO", "12.0"))       # leave tracking + re-enter
LIMBO_BIN_S = float(E("LIMBO_BIN_S", "0.5"))
FORBID = float(E("FORBID", "20"))          # assignment cost above which the variable is dropped
EXCL_M = float(E("EXCL_M", "0.6"))         # two tracklets this close at once = one robot
SPLIT_GAIN = float(E("SPLIT_GAIN", "1.6")) # read weight that must flip to cut a tracklet
MILP_S = float(E("MILP_S", "90"))          # solver time limit


def read_weight(lp, margin, amb):
    """Per-read weight (0 = ignore) from the CTC log-prob, the margin over the runner-up
    candidate and whether the text box sat inside two robots' bumper regions.
    Calibrated on 8k reads of 19 matches against confidently-read tracklets: overall
    99.5% right; lp <= -6: 96%; margin < 2: 82%, 2-4: 95%; ambiguous: 99.3%."""
    lp, margin, amb = np.asarray(lp, float), np.asarray(margin, float), np.asarray(amb, float)
    w = np.where(lp > -6, 1.0, 0.5)
    w = w * np.where(margin < 2, 0.3, np.where(margin < 4, 0.7, 1.0))
    w = np.where((lp < -8) | (margin < 1.0), 0.0, w)
    return w * np.where(amb > 0, 0.7, 1.0)


X_TOL_S = float(E("X_TOL_S", "0.05"))      # s, extra read <-> detection frame time tolerance
X_GATE = float(E("X_GATE", "2.5"))         # max normalised distance read -> detection
X_AMB_DROP = float(E("X_AMB_DROP", "1.0")) # runner-up within this (normalised) of the best: drop
X_AMB_FLAG = float(E("X_AMB_FLAG", "2.0")) # runner-up within this: keep, flagged ambiguous
X_W = float(E("X_W", "1.0"))               # weight multiplier of extra (e.g. station-camera) reads


def attribute_extra(det, xy, extra, tol_s=None, gate=None):
    """Field-located reads from other cameras -> read rows [det idx, cand, lp, margin, ambiguous]
    on the detections `det` (cols: frame, t_video, ...; positions xy).

    extra rows: [t_video, cand, lp, margin, x, y, dir_x, dir_y, sig_along, sig_perp, ...]: the
    reader's estimate of the robot centre with an anisotropic uncertainty (dir = the viewing
    direction, along which a low camera's depth is poor; dir 0,0 = isotropic sig_perp).  Each read
    goes to the detection of the nearest overhead frame (|dt| <= tol_s) at the smallest normalised
    distance; dropped beyond `gate` or when a second detection is about as near (ambiguity guard,
    like the overhead reads' two-bumper-regions flag)."""
    tol_s = X_TOL_S if tol_s is None else tol_s
    gate = X_GATE if gate is None else gate
    extra = np.asarray(extra, float).reshape(len(extra), -1) if len(extra) else np.zeros((0, 10))
    n_oos = 0
    if extra.shape[1] >= 18:              # open-set rows (station_ocr): drop the rejected reads
        n_oos = int((extra[:, 17] > 0).sum()); extra = extra[extra[:, 17] == 0]
    out, stats = [], dict(n=len(extra), no_frame=0, far=0, amb_drop=0, amb=0)
    if n_oos: stats["out_of_match"] = n_oos
    if not len(extra) or not len(det): return np.zeros((0, 5)), stats
    tv = det[:, 1]; order = np.argsort(tv, kind="stable"); ts = tv[order]
    for r in extra:
        t = r[0]
        k = np.searchsorted(ts, t)
        near = [ts[i] for i in (k - 1, k) if 0 <= i < len(ts)]
        tn = min(near, key=lambda x: abs(x - t)) if near else None
        if tn is None or abs(tn - t) > tol_s:
            stats["no_frame"] += 1; continue
        g = order[np.searchsorted(ts, tn - 1e-6):np.searchsorted(ts, tn + 1e-6)]
        v = xy[g] - r[4:6]
        dvec = r[6:8]; nd = np.linalg.norm(dvec)
        if nd > 1e-6:
            dvec = dvec / nd; a = v @ dvec; q = v @ np.array([-dvec[1], dvec[0]])
            D = np.hypot(a / r[8], q / r[9])
        else:
            D = np.linalg.norm(v, axis=1) / r[9]
        o = np.argsort(D); b = o[0]
        if D[b] > gate:
            stats["far"] += 1; continue
        d2 = D[o[1]] if len(o) > 1 else np.inf
        if d2 - D[b] < X_AMB_DROP:
            stats["amb_drop"] += 1; continue
        amb = int(d2 - D[b] < X_AMB_FLAG)
        stats["amb"] += amb
        out.append([g[b], r[1], r[2], r[3], amb])
    return np.array(out, float).reshape(-1, 5), stats


class LTk:
    """Tracklet of the labeled pipeline: per-frame arrays (one representative detection
    per frame in gi; every member detection in gall) + read and colour evidence."""
    __slots__ = ("fr", "xs", "cv", "gi", "gall", "h", "ex", "rd", "col", "conf", "n")

    def __init__(self, fr, xs, cv, gi, G, gall=None, rd=None, col=None):
        o = np.argsort(fr, kind="stable")
        self.fr, self.xs, self.cv, self.gi = np.asarray(fr)[o], xs[o], cv[o], np.asarray(gi)[o]
        self.gall = self.gi if gall is None else np.asarray(gall)
        self.n = len(self.fr)
        h = G["hs"][self.gall]; h = h[np.isfinite(h)]
        self.h = float(np.median(h)) if len(h) >= 3 else np.nan
        ex = G["ex"][self.gall]
        if len(ex) > AS.NEX: ex = ex[np.random.default_rng(0).choice(len(ex), AS.NEX, replace=False)]
        self.ex = ex
        if rd is None:
            rr = [G["rd_by_det"][g] for g in self.gall if g in G["rd_by_det"]]
            rd = np.concatenate(rr) if rr else np.zeros((0, 4))
        self.rd = rd.reshape(-1, 4)               # [frame, team idx, weight, CTC log-prob]
        if col is None:
            c = G["colour"][self.gall]; col = np.array([(c == 1).sum(), (c == 2).sum()], float)
        self.col = col
        self.conf = float(np.mean(G["conf"][self.gall]))

    f0 = property(lambda s: int(s.fr[0]))
    f1 = property(lambda s: int(s.fr[-1]))

    def at(self, fs):
        return Tk.at(self, fs)

    def team_ll(self, nt):
        """Tempered log-likelihood of this tracklet's reads under each team."""
        L = np.zeros(nt)
        if len(self.rd):
            lq, lnq = np.log(Q_OCR), np.log((1 - Q_OCR) / (nt - 1))
            W = np.zeros(nt)
            np.add.at(W, self.rd[:, 1].astype(int), self.rd[:, 2])
            L = A_OCR * (W * lq + (W.sum() - W) * lnq)
        return L

    def sub(self, m, G):
        return LTk(self.fr[m], self.xs[m], self.cv[m], self.gi[m], G)


def _lse(a):
    m = np.max(a); return m + np.log(np.exp(a - m).sum())


def build_ltracklets(det, xy, cov, G):
    T.MISS_MAX = MISS
    order = np.argsort(det[:, 0].astype(int), kind="stable")
    out = []
    for t in T.build_tracklets(det, xy, cov):
        gi = order[np.array(t.di, int)]
        out.append(LTk(np.array(t.fr), np.array(t.xs), cov[gi], gi, G))
    return out


def _best_split(tk, nt):
    """Best single change point of a tracklet's reads: (gain, last frame before, first after)."""
    rd = tk.rd[np.argsort(tk.rd[:, 0], kind="stable")]
    rd = rd[rd[:, 2] > 0][:, :3]
    if len(rd) < 2: return None
    W = np.zeros((len(rd), nt)); W[np.arange(len(rd)), rd[:, 1].astype(int)] = rd[:, 2]
    tot = W.sum(0); base = tot.max(); cum = np.cumsum(W, 0)
    best = None
    for k in range(len(rd) - 1):
        if rd[k, 0] == rd[k + 1, 0]: continue
        a, b = cum[k], tot - cum[k]
        if a.argmax() == b.argmax() or a.max() < 1.0 or b.max() < 1.0: continue
        g = a.max() + b.max() - base
        if best is None or g > best[0]:
            best = (g, int(rd[k, 0]), int(rd[k + 1, 0]))
    return best


def split_by_reads(tks, G, nt, log=print):
    """Cut tracklets whose reads switch team (the Kalman tracklet jumped robots) at the
    frame of closest approach to another tracklet between the two runs of reads.
    -> (tracklets, cut records {frame, fa, fb, dist, gain, pieces: (before, after)})."""
    out, todo, n_split, cuts = [], list(tks), 0, []
    while todo:
        tk = todo.pop()
        b = _best_split(tk, nt)
        if b is None or b[0] < SPLIT_GAIN:
            out.append(tk); continue
        _, fa, fb = b
        fs = tk.fr[(tk.fr > fa) & (tk.fr <= fb)]
        cut = int(fs[0]) if len(fs) else fb; dmin = None
        if len(fs) > 1:
            p = tk.at(fs)[0]; d = np.full(len(fs), 9.0)
            for o in tks:
                if o is tk or o.f1 < fs[0] or o.f0 > fs[-1]: continue
                m = (fs >= o.f0) & (fs <= o.f1)
                if m.any():
                    d[m] = np.minimum(d[m], np.linalg.norm(o.at(fs[m])[0] - p[m], axis=1))
            cut = int(fs[int(np.argmin(d))]) if d.min() < 3 else int(fs[len(fs) // 2])
            dmin = float(d.min())
        n_split += 1
        pieces = []
        for m in (tk.fr < cut, tk.fr >= cut):
            if m.sum() >= T.MIN_LEN:
                pc = tk.sub(m, G); todo.append(pc); pieces.append(pc)
            else:
                pieces.append(None)
        cuts.append(dict(frame=cut, fa=fa, fb=fb, dist=dmin, gain=float(b[0]), pieces=pieces,
                         xy=tk.at(np.array([cut]))[0][0].tolist(), rd=tk.rd))
    log(f"  cut {n_split} tracklets where their bumper reads change team")
    return out, cuts


def _post(tk, nt):
    L = tk.team_ll(nt); p = np.exp(L - _lse(L))
    return int(p.argmax()), float(p.max()) if len(tk.rd) else 0.0


def merge_labeled(tks, G, nt):
    """merge_same_view, but never across confident different teams or clear colours."""
    n = len(tks); par = list(range(n))
    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    post = [_post(t, nt) for t in tks]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = tks[i], tks[j]
            lo, hi = max(a.f0, b.f0), min(a.f1, b.f1)
            if hi - lo + 1 < 3: continue
            if post[i][1] > 0.8 and post[j][1] > 0.8 and post[i][0] != post[j][0]: continue
            if a.col.sum() >= 5 and b.col.sum() >= 5 and \
                    abs(a.col[0] / a.col.sum() - b.col[0] / b.col.sum()) > 0.7: continue
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
            for f, p, c, gi in zip(tks[i].fr, tks[i].xs, tks[i].cv, tks[i].gi):
                acc.setdefault(int(f), []).append((p, c, gi))
        fr = np.array(sorted(acc)); xs = np.zeros((len(fr), 2)); cv = np.zeros((len(fr), 2, 2)); gis = []
        for k, f in enumerate(fr):
            I = sum(np.linalg.inv(c) for _, c, _ in acc[f]); bb = sum(np.linalg.inv(c) @ p for p, c, _ in acc[f])
            cv[k] = np.linalg.inv(I); xs[k] = cv[k] @ bb; gis.append(acc[f][0][2])
        out.append(LTk(fr, xs, cv, np.array(gis), G, gall=np.concatenate([tks[i].gall for i in g]),
                       rd=np.concatenate([tks[i].rd for i in g]), col=sum(tks[i].col for i in g)))
    return out


def assign_costs(tks, team_al, nt, use_reads=True):
    """(n, nt) cost of labelling tracklet i as team t (reward excluded), and the
    (n, nt) evidence log-posterior used later for confidences."""
    n = len(tks); C = np.zeros((n, nt)); LP = np.zeros((n, nt))
    for i, tk in enumerate(tks):
        L = tk.team_ll(nt) if use_reads else np.zeros(nt)
        lp = L - _lse(L)
        idc = np.clip(-W_ID * (lp + np.log(nt)), -W_ID * np.log(nt), ID_CAP)
        tot = tk.col.sum()
        colc = np.zeros(nt); col_ll = np.zeros(nt)
        if tot > 0:
            for t in range(nt):
                n_opp = tk.col[1] if team_al[t] == 1 else tk.col[0]
                colc[t] = min(COL_CAP, W_COLV * max(0.0, n_opp - COL_NOISE * tot))
            col_ll = -np.minimum(colc, 30.0)
        C[i] = idc + colc
        e = L + col_ll; LP[i] = e - _lse(e)
    return C, LP


UNREAD_ACC = float(E("UNREAD_ACC", "0.7"))  # accuracy of read-less tracklet labels (measured
                                           # 0.76-0.79 on 19 Sammamish quals vs hand labels)
GAP_CONF_S = float(E("GAP_CONF_S", "3.0"))  # s, longest gap whose interpolated frames keep conf > 0
OVL_F = int(E("OVL_F", "15"))              # frames two pieces of one robot may overlap
OVL_M = float(E("OVL_M", "1.2"))           # m, max median distance over such an overlap


def overlap_cost(a, b):
    """b starts while a is still running (the tracker briefly held two tracks on one robot):
    a valid link only if they are together over the overlap."""
    if not (a.f1 - OVL_F < b.f0 <= a.f1) or b.f1 <= a.f1: return np.inf
    fs = np.arange(b.f0, a.f1 + 1)
    d = float(np.median(np.linalg.norm(a.at(fs)[0] - b.at(fs)[0], axis=1)))
    if d > OVL_M: return np.inf
    c = W_M * (d / OVL_M) ** 2
    if np.isfinite(a.h) and np.isfinite(b.h):
        c += W_H * min(((a.h - b.h) / SIG_H) ** 2, 9.0)
    return c


def all_links(tks):
    links = {}
    for i in range(len(tks)):
        for j in range(len(tks)):
            if i == j: continue
            if tks[j].f0 > tks[i].f1:
                c = link_cost(tks[i], tks[j])
            elif OVL_F > 0:
                c = overlap_cost(tks[i], tks[j])
            else:
                continue
            if np.isfinite(c): links[(i, j)] = c
    return links


def solve_labeled(tks, team_al, nf, log=print, links=None, use_reads=True):
    """Joint 6-team labelled path cover (see section comment).  Returns per team the
    ordered tracklet indices and, per consecutive pair, whether the link is direct.
    use_reads=False: the same solve on motion + colour only (the 'what tracking alone
    would have done' reference for identity corrections)."""
    n, nt = len(tks), len(team_al)
    C, LP = assign_costs(tks, team_al, nt, use_reads)
    if links is None: links = all_links(tks)
    excl = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = tks[i], tks[j]
            lo, hi = max(a.f0, b.f0), min(a.f1, b.f1)
            if hi - lo + 1 < 3: continue
            fs = np.arange(lo, hi + 1, 3)
            if np.median(np.linalg.norm(a.at(fs)[0] - b.at(fs)[0], axis=1)) < EXCL_M: excl.append((i, j))
    nb = int(np.ceil(nf / (LIMBO_BIN_S / T.DT))) + 1
    bo = [min(nb - 1, int(tk.f1 * T.DT / LIMBO_BIN_S) + 1) for tk in tks]
    bi = [int(tk.f0 * T.DT / LIMBO_BIN_S) for tk in tks]

    cost, integ, rows, cols, vals, rhs_lo, rhs_hi = [], [], [], [], [], [], []
    nv = [0]; ncon = [0]
    def var(c, is_int=1):
        cost.append(c); integ.append(is_int); nv[0] += 1; return nv[0] - 1
    def con(terms, lo, hi):
        r = ncon[0]; ncon[0] += 1
        for v, a in terms: rows.append(r); cols.append(v); vals.append(a)
        rhs_lo.append(lo); rhs_hi.append(hi)
    X = {}; Evar = {}
    cover = {i: [] for i in range(n)}
    for t in range(nt):
        ok = [i for i in range(n) if C[i, t] < FORBID]
        oks = set(ok)
        for i in ok:
            X[(i, t)] = var(-W_R * tks[i].n + C[i, t]); cover[i].append(X[(i, t)])
        zin = {i: var(J_LIMBO / 2) for i in ok}
        zout = {i: var(J_LIMBO / 2) for i in ok}
        cb = [var(0.0, 0) for _ in range(nb - 1)]
        ins = {i: [] for i in ok}; outs = {i: [] for i in ok}
        for (i, j), c in links.items():
            if i in oks and j in oks:
                v = var(c); Evar[(i, j, t)] = v; outs[i].append(v); ins[j].append(v)
        for i in ok:
            con([(v, 1) for v in ins[i]] + [(zout[i], 1), (X[(i, t)], -1)], 0, 0)
            con([(v, 1) for v in outs[i]] + [(zin[i], 1), (X[(i, t)], -1)], 0, 0)
        b_in = {b: [] for b in range(nb)}; b_out = {b: [] for b in range(nb)}
        for i in ok:
            b_in[bo[i]].append(zin[i]); b_out[bi[i]].append(zout[i])
        for b in range(nb):
            terms = [(v, 1) for v in b_in[b]] + [(v, -1) for v in b_out[b]]
            if b > 0: terms.append((cb[b - 1], 1))
            if b < nb - 1: terms.append((cb[b], -1))
            r = (1 if b == nb - 1 else 0) - (1 if b == 0 else 0)
            con(terms, r, r)
    for i in range(n):
        if len(cover[i]) > 1: con([(v, 1) for v in cover[i]], 0, 1)
    for i, j in excl:
        con([(v, 1) for v in cover[i] + cover[j]], 0, 1)
    A = _sp.csr_matrix((vals, (rows, cols)), shape=(ncon[0], nv[0]))
    import time as _time
    t0 = _time.time()
    res = _milp(np.array(cost), integrality=np.array(integ), bounds=_Bounds(0, 1),
                constraints=_LC(A, rhs_lo, rhs_hi),
                options=dict(time_limit=MILP_S, mip_rel_gap=1e-3, disp=False))
    if res.x is None:
        raise RuntimeError(f"labelled path ILP failed: {res.message}")
    x = res.x
    log(f"  identity ILP{'' if use_reads else ' (motion only)'}: {n} tracklets, {len(links)} links, {nv[0]} vars, {ncon[0]} constraints, "
        f"{_time.time() - t0:.1f}s, {res.message.split('(')[0].strip()}")
    paths = []
    for t in range(nt):
        p = sorted([i for i in range(n) if (i, t) in X and x[X[(i, t)]] > 0.5], key=lambda i: tks[i].f0)
        direct = [((a, b, t) in Evar and x[Evar[(a, b, t)]] > 0.5) for a, b in zip(p[:-1], p[1:])]
        paths.append((p, direct))
    return paths, LP, links


def _link_conf(links, i, j):
    """Softmax of the chosen link against every competing successor of i / predecessor of j."""
    c = links.get((i, j))
    if c is None: return 0.0
    comp = [v for (a, b), v in links.items() if (a == i) ^ (b == j)]
    z = np.exp(-c) + sum(np.exp(-v) for v in comp)
    return float(np.exp(-c) / z)


def run_labeled(det, xy, cov, hs, ex, nf, LEN, WID, reads, teams, log=print, extra_reads=None):
    """reads: (R, 5+) rows [det idx, team idx, lp, margin, ambiguous] (det idx into det).
    teams: six team keys in output order (red 1-3, blue 1-3).
    extra_reads: optional field-located reads from other cameras (station_ocr rows, see
    attribute_extra): attributed to the nearest detection, weighted X_W x read_weight.
    -> list of six dicts {team, alliance, xy (nf,2), v, obs, conf (nf,), det_idx, height,
       team_conf, n_reads}."""
    nt = len(teams); team_al = np.array([1] * (nt // 2) + [2] * (nt - nt // 2))
    det = det.copy(); colour = det[:, 8].astype(int).copy()
    reads = np.asarray(reads, float).reshape(len(reads), -1) if len(reads) else np.zeros((0, 5))
    w = read_weight(reads[:, 2], reads[:, 3], reads[:, 4]) if len(reads) else np.zeros(0)
    n_main, x_stats = len(reads), None
    if extra_reads is not None and len(extra_reads):
        xr, x_stats = attribute_extra(det, xy, extra_reads)
        if len(xr):
            wx = read_weight(xr[:, 2], xr[:, 3], xr[:, 4]) * X_W
            reads = np.r_[reads[:, :5], xr]; w = np.r_[w, wx]
        log(f"  extra reads: {x_stats['n']} -> {len(xr)} attributed ({int((wx > 0).sum()) if len(xr) else 0} usable; "
            f"no frame {x_stats['no_frame']}, far {x_stats['far']}, ambiguous dropped {x_stats['amb_drop']} / flagged {x_stats['amb']}"
            + (f"; {x_stats['out_of_match']} rejected (open set) not used" if x_stats.get("out_of_match") else "") + ")")
    rd_by_det = {}
    for r, wi in zip(reads, w):
        if wi <= 0: continue
        g = int(r[0])
        rd_by_det.setdefault(g, []).append([det[g, 0], r[1], wi, r[2]])
        if wi >= 0.7 and r[4] == 0: det[g, 8] = team_al[int(r[1])]   # a clear read decides the alliance
    rd_by_det = {g: np.array(v) for g, v in rd_by_det.items()}
    G = dict(hs=hs, ex=ex, colour=colour, conf=det[:, 7], rd_by_det=rd_by_det)
    tks = build_ltracklets(det, xy, cov, G)
    n0 = len(tks)
    tks, cuts = split_by_reads(tks, G, nt, log)
    tks = merge_labeled(tks, G, nt)
    log(f"  {n0} tracklets -> {len(tks)} after read cuts + duplicate merge; "
        f"{sum(len(t.rd) > 0 for t in tks)} carry reads ({int((w > 0).sum())} usable reads)")
    paths, LP, links = solve_labeled(tks, team_al, nf, log)
    mpaths = None
    if E("MOTION_REF", "1") == "1":
        try:
            mpaths, _, _ = solve_labeled(tks, team_al, nf, log, links=links, use_reads=False)
        except Exception as e:
            log(f"  motion-only reference failed: {e!r}")

    # per-tracklet confidence: own evidence, then propagated along direct links
    conf = {}
    def own_conf(i, t):
        same = team_al == team_al[t]
        p_al = float(np.exp(_lse(LP[i, same])))                 # P(right alliance)
        p_t = float(np.exp(LP[i, t])) / max(p_al, 1e-12)       # P(team | alliance) from reads
        if not (tks[i].rd[:, 2] > 0).any():
            p_t = max(p_t, UNREAD_ACC)                          # read-less: the solve's own track record
        return p_al * p_t
    for t, (p, direct) in enumerate(paths):
        c = [own_conf(i, t) for i in p]
        lc = [(_link_conf(links, a, b) if d else 0.0) for a, b, d in zip(p[:-1], p[1:], direct)]
        for _ in range(2):
            for k in range(1, len(p)): c[k] = max(c[k], c[k - 1] * lc[k - 1])
            for k in range(len(p) - 2, -1, -1): c[k] = max(c[k], c[k + 1] * lc[k])
        for i, ci in zip(p, c): conf[(i, t)] = ci
    # exclusion: if the alliance's other two teams are confidently elsewhere, this is the third
    owner = {i: t for t, (p, _) in enumerate(paths) for i in p}
    for t, (p, _) in enumerate(paths):
        mates = [u for u in range(nt) if team_al[u] == team_al[t] and u != t]
        for i in p:
            a = tks[i]; best = []
            for u in mates:
                cs = [conf[(j, u)] for j in paths[u][0] if tks[j].f0 <= a.f1 and tks[j].f1 >= a.f0]
                best.append(max(cs) if cs else 0.0)
            if best:
                al = float(np.exp(_lse(LP[i, team_al == team_al[t]])))     # P(right alliance)
                conf[(i, t)] = max(conf[(i, t)], 0.9 * min(best) * al)

    out = []
    for t, (p, direct) in enumerate(paths):
        name = "red" if team_al[t] == 1 else "blue"
        cf = np.zeros(nf); obs = np.zeros(nf, bool)
        xs = np.full((nf, 2), np.nan); vs = np.zeros((nf, 2)); P = np.tile(np.eye(2) * 25.0, (nf, 1, 1))
        segs, cur = [], []
        for k, i in enumerate(p):
            cur.append(i)
            if k == len(p) - 1 or not direct[k]:
                segs.append(cur); cur = []
        for sg in segs:
            ob = {}
            for i in sg:
                for f, z, R in zip(tks[i].fr, tks[i].xs, tks[i].cv): ob[int(f)] = (z, R)
            s0, s1 = min(ob), max(ob)
            st, Ps = fit6.rts({f - s0: v for f, v in ob.items()}, s1 - s0 + 1, np.r_[ob[s0][0], 0, 0])
            xs[s0:s1 + 1] = st[:, :2]; vs[s0:s1 + 1] = st[:, 2:]; P[s0:s1 + 1] = Ps[:, :2, :2]
            obs[list(ob)] = True
            for k, i in enumerate(sg):
                cf[tks[i].f0:tks[i].f1 + 1] = conf[(i, t)]
                if k + 1 < len(sg):
                    j = sg[k + 1]; g0, g1 = tks[i].f1 + 1, tks[j].f0
                    gap = (g1 - g0) * T.DT
                    # short gaps: interpolated, slightly less sure; long ones: unknown
                    cf[g0:g1] = min(conf[(i, t)], conf[(j, t)]) * np.exp(-gap / 2.0) if gap <= GAP_CONF_S else 0.0
        # limbo / never seen: no position claim -> hold the nearest known position for
        # shape compatibility (obs=0, conf=0 flags it unknown)
        known = np.where(np.isfinite(xs[:, 0]))[0]
        if len(known):
            for d in (0, 1):
                xs[:, d] = np.interp(np.arange(nf), known, xs[known, d])
        else:
            xs[:] = [LEN - 2.0, WID / 2] if name == "red" else [2.0, WID / 2]
        xs[:, 0] = np.clip(xs[:, 0], 0, LEN); xs[:, 1] = np.clip(xs[:, 1], 0, WID)
        gall = np.concatenate([tks[i].gall for i in p]) if p else np.zeros(0, int)
        hh = hs[gall] if len(gall) else np.zeros(0); hh = hh[np.isfinite(hh)]
        nr = int(sum(((tks[i].rd[:, 1] == t) & (tks[i].rd[:, 2] > 0)).sum() for i in p))
        tc = float(cf[obs].mean()) if obs.any() else 0.0
        out.append(dict(team=teams[t], alliance=name, xy=xs, v=vs, obs=obs, P=P, conf=np.clip(cf, 0, 1),
                        det_idx=gall.astype(int), height=float(np.median(hh)) if len(hh) else float("nan"),
                        team_conf=tc, n_reads=nr))
    corr = corrections(tks, paths, mpaths, cuts, conf, out, teams, nf)
    for o in out:
        o["team_conf"] = float(o["conf"][o["obs"]].mean()) if o["obs"].any() else 0.0
    for t, (p, direct) in enumerate(paths):
        o = out[t]
        log(f"  {teams[t]} ({o['alliance']}): {len(p)} tracklets, {len(p) - sum(direct)} segments, "
            f"observed {o['obs'].mean():.0%}, {o['n_reads']} own reads, conf {o['team_conf']:.2f}")
    log(f"  identity corrections vs motion-only linking: {len(corr)} "
        f"({sum(c['anchored'] for c in corr)} with bumper reads within {10:.0f} s)")
    nr_used = int((w > 0).sum())
    per_team = {teams[t]: int(sum(((v[:, 1] == t) & (v[:, 2] > 0)).sum() for v in rd_by_det.values()))
                for t in range(nt)}
    identity = dict(method="ocr-ilp", n_reads=nr_used, per_team_reads=per_team,
                    n_tracklets=len(tks), n_cuts=len(cuts), corrections=corr)
    if x_stats is not None:
        we = w[n_main:]; rx = reads[n_main:]
        identity.update(n_reads_extra=int((we > 0).sum()), n_reads_extra_raw=int(x_stats["n"]),
                        per_team_reads_extra={teams[t]: int(((rx[:, 1] == t) & (we > 0)).sum()) for t in range(nt)})
    return out, identity


def _team_of(paths):
    return {i: t for t, (p, _) in enumerate(paths) for i in p}


def _reads_json(rd, f_lo, f_hi, teams, k=4):
    """Reads of a tracklet in [f_lo, f_hi] -> [[replay frame @10 Hz, team, CTC lp, weight]] (<= 2k, nearest the edges)."""
    r = rd[(rd[:, 0] >= f_lo) & (rd[:, 0] <= f_hi) & (rd[:, 2] > 0)]
    r = r[np.argsort(r[:, 0])]
    if len(r) > 2 * k: r = np.r_[r[:k], r[-k:]]
    return [[int(round(x[0] * T.DT * 10)), teams[int(x[1])], round(float(x[3]), 2), round(float(x[2]), 2)] for x in r]


def corrections(tks, paths, mpaths, cuts, conf, out, teams, nf, win_s=10.0):
    """Every place where the OCR-anchored identity differs from tracking alone:
      kind "cut":    a Kalman tracklet's reads change team -> it jumped robots; cut at
                     the closest encounter between the two runs of reads;
      kind "relink": the motion-only solve (same tracklets, no reads) links tracklet a
                     -> b directly, the OCR solve gives b to another team (or drops it).
    Frames between the contradicting reads are left low-confidence (conf capped at the
    correction's confidence).  Frames are replay frames @10 Hz."""
    own = _team_of(paths)
    det2tk = {}
    for i, tk in enumerate(tks):
        for g in tk.gall: det2tk[int(g)] = i
    W = int(win_s / T.DT)
    res = []

    def cap(t, f0, f1, c):
        if t is None: return
        a, b = max(0, int(f0)), min(nf, int(f1) + 1)
        out[t]["conf"][a:b] = np.minimum(out[t]["conf"][a:b], c)

    for c in cuts:
        pa, pb = c["pieces"]
        ia = det2tk.get(int(pa.gall[np.argmax(pa.fr)])) if pa is not None else None
        ib = det2tk.get(int(pb.gall[np.argmin(pb.fr)])) if pb is not None else None
        ta, tb = own.get(ia), own.get(ib)
        place = 0.8 if (c["dist"] is not None and c["dist"] < 1.2) else 0.5 if (c["dist"] or 9) < 2.0 else 0.3
        cc = float((1 - np.exp(-1.5 * c["gain"])) * place)
        cap(ta, c["fa"], c["fb"], cc); cap(tb, c["fa"], c["fb"], cc)
        res.append(dict(kind="cut", frame=int(round(c["frame"] * T.DT * 10)), t=round(c["frame"] * T.DT, 2),
                        anchored=True,
                        unresolved=[int(round(c["fa"] * T.DT * 10)), int(round(c["fb"] * T.DT * 10))],
                        teams=[teams[ta] if ta is not None else None, teams[tb] if tb is not None else None],
                        tracks=[ta, tb],
                        evidence=_reads_json(c["rd"], c["frame"] - W, c["frame"] + W, teams),
                        encounter=dict(dist_m=None if c["dist"] is None else round(c["dist"], 2),
                                       xy=[round(v, 2) for v in c["xy"]]),
                        conf=round(cc, 3)))
    if mpaths is not None:
        succ = {}
        for p, direct in paths:
            for a, b in zip(p[:-1], p[1:]): succ[a] = b
        seen = []
        for p, direct in mpaths:
            for a, b, d in zip(p[:-1], p[1:], direct):
                if not d or succ.get(a) == b: continue
                ta, tb = own.get(a), own.get(b)
                if ta is None and tb is None: continue
                if ta is not None and ta == tb: continue          # same team, via another tracklet
                f = (tks[a].f1 + tks[b].f0) // 2
                key = (f // 45, frozenset([ta, tb]))
                if key in seen: continue
                seen.append(key)
                # the same event already reported as a tracklet cut
                if any(r["kind"] == "cut" and abs(r["frame"] - f * T.DT * 10) <= 10 and
                       (({ta, tb} - {None}) & set(r["tracks"])) for r in res): continue
                dist = None
                if ta is not None and tb is not None:
                    lo, hi = max(0, f - 45), min(nf, f + 46)
                    dd = np.linalg.norm(out[ta]["xy"][lo:hi] - out[tb]["xy"][lo:hi], axis=1)
                    dist = round(float(dd.min()), 2); fe = lo + int(dd.argmin())
                else:
                    fe = f
                # evidence: the reads closest to the swap on both teams' OCR-anchored paths
                ev = []
                for t in (ta, tb):
                    if t is None: continue
                    rr = [tks[i].rd for i in paths[t][0] if tks[i].f1 >= f - W and tks[i].f0 <= f + W]
                    rr = np.concatenate(rr) if rr else np.zeros((0, 4))
                    rr = rr[(np.abs(rr[:, 0] - f) <= W) & (rr[:, 2] > 0)]
                    rr = rr[np.argsort(np.abs(rr[:, 0] - f))][:6]
                    ev += _reads_json(rr, f - W, f + W, teams, k=6)
                ca = conf.get((a, ta), 0.0) if ta is not None else 0.0
                cb = conf.get((b, tb), 0.0) if tb is not None else 0.0
                cc = float(min(c for c in (ca, cb) if c > 0) if (ca > 0 or cb > 0) else 0.0)
                for t, i in ((ta, a), (tb, b)):        # read-less pieces next to the swap stay uncertain
                    if t is not None and not (tks[i].rd[:, 2] > 0).any():
                        cap(t, tks[i].f0, tks[i].f1, max(cc, 0.3) * 0.7)
                res.append(dict(kind="relink", frame=int(round(fe * T.DT * 10)), t=round(fe * T.DT, 2),
                                anchored=bool(ev),
                                teams=[teams[ta] if ta is not None else None, teams[tb] if tb is not None else None],
                                tracks=[ta, tb],
                                motion_link=dict(frames=[int(round(tks[a].f1 * T.DT * 10)), int(round(tks[b].f0 * T.DT * 10))],
                                                 jump_m=round(float(np.linalg.norm(tks[b].xs[0] - tks[a].xs[-1])), 2)),
                                evidence=ev, encounter=dict(dist_m=dist), conf=round(cc, 3)))
    res.sort(key=lambda r: r["frame"])
    return res
