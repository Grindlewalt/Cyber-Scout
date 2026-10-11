"""Intake detection: when / where each robot picks FUEL up off the carpet (or from a depot / outpost).

    detect_intakes(video, fuel, match, cam, rules, log=print) -> [ {t, match_t, track_idx, xy, n, conf, kind} ]
    python live/intakes.py VIDEO FUEL.json MATCH.json CAMERA.json RULES.json OUT.json

Signals (overhead fixed camera; robots hide the balls under them, so "gone" must be judged
before vs after the robot has been there):
 1. FLOOR (main).  The video is re-decoded at ~5 Hz.  Static yellow pixels are projected onto the carpet
    (z = ball radius) and weighted by 1/(ball pixel area) -> a ball-count density map on a 0.1 m grid.
    Cells covered by any robot (disc, ~0.75 m, incl. its hopper balls that project onto the carpet
    in the wrong place) are NaN.  For each robot and snapshot time t: within a disc around the robot,
    BEFORE = cell-wise min density over [t-1.4, t-0.6] s (transient airborne balls ignored), AFTER =
    cell-wise max over [t+0.6, t+1.4].  loss = sum(BEFORE - AFTER) over cells valid in both, minus
    the gain in a ring around the disc (balls that were only shoved aside).  Runs of loss >= ~1.5
    balls are one intake; n = round(loss); xy = loss centroid; t = robot's closest approach to it.
    Two robots claiming the same lost balls: the closer one keeps them.
 2. ROLLING (off by default: ~10% precise; mostly shots).  A non-airborne fuel track that ends within ~0.8 m of a robot (rolling ball swallowed).
    Merged into a FLOOR event if close in time/space, else a low-confidence n=1 event.
 3. OUTPOST (off by default: unverified).  A robot parked (< 0.35 m/s) < 1.3 m in front of its alliance outpost chute opening for
    >= 1.5 s: one event per stay (n unknown -> 1, conf 0.35).  Depot piles go through signal 1.
Snapshots with camera shake/zoom blur (sharpness < 0.85 x local 75th pct) or cuts are dropped.
Measured (hand-labelled, 3 matches): floor-event precision ~0.88, recall of intake moments ~0.45 (misses:
robot parked on the balls, which stay hidden under its hull).  ~7 s per match on the server.
Robot positions: match["tracks"][k]["xy"] (cm, 10 Hz), frame i <-> video time t_start - 0.5 + i/hz.
`match_t` = t - t_start (replay s from auto start, as shots.py).
"""
import os, sys, json, math, time
import numpy as np, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import camera as CAMM
import fuel as FUEL

CELL = 0.1
SNAP_DT = 0.2                  # s between snapshots
R_DISC = 0.75                  # m, robot footprint disc for gathering
R_RING = 1.6
FOOT = 0.91
BEF = (0.6, float(os.environ.get("BEF1", 1.4)))               # s before t
AFT = (0.6, float(os.environ.get("AFT1", 1.4)))
MIN_LOSS = 1.4                 # balls
BLUR_FRAC = 0.85
Y_THR = 110                    # yellowness (fuel.yellow_from_uv scale) of a floor-ball pixel


def _load(x):
    return json.load(open(x)) if isinstance(x, str) else x


class _Grid:
    """half-res ROI pixel -> carpet cell + ball-count weight, built once per camera."""

    def __init__(self, cam, S=2):
        self.S = S
        tr = FUEL.FuelTracker(cam, floor_every=0)
        self.roi = tr.roi
        x0, y0, x1, y1 = self.roi
        h, w = (y1 - y0) // S, (x1 - x0) // S
        uu, vv = np.meshgrid(x0 + (np.arange(w) + .5) * S, y0 + (np.arange(h) + .5) * S)
        uv = np.c_[uu.ravel(), vv.ravel()]
        z = FUEL.FUEL_D / 2
        G = cam.img_to_plane(uv, z)
        L, W = CAMM.LEN, CAMM.WID
        self.nx, self.ny = int(math.ceil(L / CELL)), int(math.ceil(W / CELL))
        ok = np.isfinite(G).all(1) & (G[:, 0] >= 0) & (G[:, 0] < L) & (G[:, 1] >= 0) & (G[:, 1] < W)
        ok &= (tr.mask.ravel() > 0)
        ix = np.clip((np.nan_to_num(G[:, 0]) / CELL).astype(int), 0, self.nx - 1)
        iy = np.clip((np.nan_to_num(G[:, 1]) / CELL).astype(int), 0, self.ny - 1)
        cell = iy * self.nx + ix
        # px radius per cell (evaluated at the cell centres)
        cx = (np.arange(self.nx) + .5) * CELL; cy = (np.arange(self.ny) + .5) * CELL
        CX, CY = np.meshgrid(cx, cy)
        P = np.c_[CX.ravel(), CY.ravel(), np.full(CX.size, z)]
        rp = FUEL.ball_radius_px(cam, P) / S
        area = 0.8 * np.pi * rp ** 2
        wgt = 1.0 / np.maximum(area[cell], 1.0)
        self.keep = np.flatnonzero(ok)
        self.cell = cell[self.keep]
        self.w = wgt[self.keep].astype(np.float32)
        self.shape = (h, w)
        self.n = self.nx * self.ny

    def density(self, yel, hulls=()):
        """ball-count map; cells whose pixels are hidden by a robot prism are NaN."""
        m = yel.ravel()[self.keep] >= Y_THR
        hid = None
        if len(hulls):
            hm = np.zeros(self.shape, np.uint8)
            for h in hulls:
                cv2.fillConvexPoly(hm, h, 1)
            hid = hm.ravel()[self.keep] > 0
            m &= ~hid
        d = np.bincount(self.cell[m], weights=self.w[m], minlength=self.n).astype(np.float32)
        if hid is not None:
            d[np.unique(self.cell[hid])] = np.nan
        return d.reshape(self.ny, self.nx)


def _snapshots(video, grid, t0, t1, log, R=None, cam=None):
    import av
    c = av.open(video)
    s = c.streams.video[0]; s.thread_type = "AUTO"
    if t0 > 1.0:
        c.seek(int(max(0.0, t0 - 1.0) / s.time_base), stream=s)
    x0, y0, x1, y1 = grid.roi
    ts, D, SH = [], [], []
    nxt = t0
    try:
        for f in c.decode(s):
            if f.time is None or f.time < nxt:
                continue
            if f.time > t1:
                break
            nxt = f.time + SNAP_DT - 0.01
            if f.format.name not in ("yuv420p", "yuvj420p"):
                bgr = f.to_ndarray(format="bgr24")[y0:y1, x0:x1]
                sharp = float(np.abs(np.diff(bgr[::2, ::2, 1].astype(np.int16), axis=1)).mean())
                small = cv2.resize(bgr, ((x1 - x0) // 2, (y1 - y0) // 2), interpolation=cv2.INTER_AREA)
                yel = FUEL.yellow_from_bgr(small)
            else:
                H = f.height
                p0, p1, p2 = f.planes
                Y = np.frombuffer(p0, np.uint8).reshape(H, p0.line_size)
                if Y[y0:y1:8, x0:x1:8].mean() < FUEL.NO_FEED_LEVEL:
                    continue
                sharp = float(np.abs(np.diff(Y[y0:y1:2, x0:x1:2].astype(np.int16), axis=1)).mean())
                U = np.frombuffer(p1, np.uint8).reshape(H // 2, p1.line_size)
                V = np.frombuffer(p2, np.uint8).reshape(H // 2, p2.line_size)
                yel = FUEL.yellow_from_uv(U[y0 // 2:y1 // 2, x0 // 2:x1 // 2], V[y0 // 2:y1 // 2, x0 // 2:x1 // 2])
            SH.append(sharp); ts.append(float(f.time)); D.append(grid.density(yel, _hulls(R, cam, grid, float(f.time)) if R is not None else ()))
    finally:
        c.close()
    return np.array(ts), np.array(D), np.array(SH)


PRISM_PAD, PRISM_H = float(os.environ.get("PPAD", 0.30)), float(os.environ.get("PH", 0.9))


def _hulls(R, cam, grid, t):
    """image hulls (half-res ROI px) of each robot's prism (footprint + pad, up to PRISM_H)."""
    if t < R.t0 or t > R.t1:
        return []
    h = FOOT / 2 + PRISM_PAD
    cs = np.array([[-h, -h], [h, -h], [h, h], [-h, h]])
    out = []
    x0, y0 = grid.roi[:2]
    for k in range(R.n):
        p = R.pos(k, t)
        P = np.r_[np.c_[p + cs, np.zeros(4)], np.c_[p + cs, np.full(4, PRISM_H)]]
        uv = (cam.project(P) - [x0, y0]) / grid.S
        out.append(cv2.convexHull(uv.astype(np.float32)).astype(np.int32).reshape(-1, 2))
    return out


class _Robots:
    def __init__(self, match):
        self.hz = float(match.get("hz", 10))
        self.t0 = float(match["t_start"]) - 0.5
        self.xy = [np.asarray(t["xy"], float) / 100.0 for t in match["tracks"]]
        self.obs = [np.asarray(t.get("obs", np.ones(len(t["xy"]))), float) for t in match["tracks"]]
        self.alliance = [t["alliance"] for t in match["tracks"]]
        self.n = len(self.xy)
        self.t1 = self.t0 + (len(self.xy[0]) - 1) / self.hz

    def pos(self, k, t):
        xy = self.xy[k]
        f = np.clip((np.asarray(t, float) - self.t0) * self.hz, 0, len(xy) - 1.000001)
        i = np.floor(f).astype(int); w = (f - i)[..., None]
        return xy[i] * (1 - w) + xy[np.minimum(i + 1, len(xy) - 1)] * w

    def speed(self, k, t, dt=0.5):
        return np.linalg.norm(self.pos(k, t + dt / 2) - self.pos(k, t - dt / 2), axis=-1) / dt


def _disc_offsets(r):
    n = int(math.ceil(r / CELL))
    o = np.arange(-n, n + 1)
    dx, dy = np.meshgrid(o, o)
    d = np.hypot(dx, dy) * CELL
    return dx, dy, d


def _floor_events(D, ts, R, grid, log, min_loss=MIN_LOSS):
    ev = []
    dx, dy, d = _disc_offsets(R_RING)
    inner = d <= R_DISC
    ring = (d > R_DISC) & (d <= R_RING)
    n = dx.shape[0] // 2
    Dp = np.pad(D, ((0, 0), (n, n), (n, n)), constant_values=np.nan)   # so slices never clip
    for k in range(R.n):
        tq = ts[(ts >= R.t0 + 0.3) & (ts <= R.t1 - 0.3)]
        series = []
        for t in tq:
            jb = np.flatnonzero((ts >= t - BEF[1]) & (ts <= t - BEF[0]))
            ja = np.flatnonzero((ts >= t + AFT[0]) & (ts <= t + AFT[1]))
            if len(jb) == 0 or len(ja) == 0:
                series.append((t, 0.0, None)); continue
            p = R.pos(k, t)
            ix = int(p[0] / CELL) + n; iy = int(p[1] / CELL) + n
            if not (n <= ix < Dp.shape[2] - n and n <= iy < Dp.shape[1] - n):
                series.append((t, 0.0, None)); continue
            sl = (slice(iy - n, iy + n + 1), slice(ix - n, ix + n + 1))
            with np.errstate(all="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    B = np.nanmin(Dp[jb][(slice(None),) + sl], 0)
                    A = np.nanmax(Dp[ja][(slice(None),) + sl], 0)
            diff = B - A
            valid = np.isfinite(diff)
            loss = np.where(valid & inner, np.maximum(diff, 0), 0)
            gain = np.where(valid & ring, np.maximum(-diff, 0), 0).sum()
            net = loss.sum() - 0.7 * min(gain, loss.sum())
            if net < 0.3:
                series.append((t, 0.0, None)); continue
            w = loss
            cxy = (np.array([(w * dx).sum(), (w * dy).sum()]) / max(w.sum(), 1e-6)) * CELL + p
            series.append((t, net, (cxy, float(valid[inner].mean()), float(np.nansum(np.where(valid & inner, B, 0))))))
        # runs above threshold
        S = np.array([s[1] for s in series])
        i = 0
        while i < len(S):
            if S[i] < min_loss * 0.6:
                i += 1; continue
            j = i
            while j + 1 < len(S) and S[j + 1] >= min_loss * 0.6:
                j += 1
            seg = range(i, j + 1)
            b = max(seg, key=lambda q: S[q])
            if S[b] >= min_loss and series[b][2] is not None:
                cxy, vfrac, before = series[b][2]
                tb = series[b][0]
                tt = np.linspace(tb - 1.5, tb + 1.5, 31)
                pp = R.pos(k, tt)
                te = float(tt[np.argmin(np.linalg.norm(pp - cxy, axis=1))])
                ev.append(dict(t=te, track_idx=k, xy=[float(cxy[0]), float(cxy[1])], n=int(round(S[b])), loss=float(S[b]),
                               vfrac=vfrac, before=before, kind="floor"))
            i = j + 1
    # two robots claiming the same loss: closer one keeps it
    ev.sort(key=lambda e: e["t"])
    drop = set()
    for a in range(len(ev)):
        for b in range(a + 1, len(ev)):
            if ev[b]["t"] - ev[a]["t"] > 1.2:
                break
            if ev[a]["track_idx"] != ev[b]["track_idx"] and np.hypot(*(np.subtract(ev[a]["xy"], ev[b]["xy"]))) < 0.9:
                da = np.linalg.norm(R.pos(ev[a]["track_idx"], ev[a]["t"]) - ev[a]["xy"])
                db = np.linalg.norm(R.pos(ev[b]["track_idx"], ev[b]["t"]) - ev[b]["xy"])
                drop.add(b if da <= db else a)
    ev = [e for i, e in enumerate(ev) if i not in drop]
    # same robot, overlapping runs of one pass
    out = []
    for e in ev:
        if out and out[-1]["track_idx"] == e["track_idx"] and e["t"] - out[-1]["t"] < 0.8 and \
                np.hypot(*(np.subtract(e["xy"], out[-1]["xy"]))) < 1.0:
            if e["loss"] > out[-1]["loss"]:
                out[-1] = e
            continue
        out.append(e)
    return out


def _rolling_events(fuel, R, cam, ints):
    ev = []
    z = FUEL.FUEL_D / 2
    for tr in fuel.get("tracks", []):
        if tr.get("airborne_score", 0) >= 0.2 or tr.get("view_ok", 1) < 0.5:
            continue
        uv = np.asarray(tr["uv"], float)
        T = np.asarray(tr.get("t") or np.asarray(tr["f"]) / float(fuel.get("fps", 60)), float)
        if len(uv) < 8 or T[-1] - T[0] < 0.4 or T[-1] < R.t0 or T[-1] > R.t1:
            continue
        g = cam.img_to_plane(uv[[0, -3, -2, -1]], z)
        if not np.isfinite(g).all():
            continue
        e = g[1:].mean(0)
        sp = np.linalg.norm(g[1:].mean(0) - g[0]) / (T[-1] - T[0])
        if sp > 5.0 or np.linalg.norm(e - g[0]) < 1.0:       # not a plain rolling ball
            continue
        best = None
        for k in range(R.n):
            dd = float(np.linalg.norm(R.pos(k, T[-1]) - e))
            d0 = float(np.linalg.norm(R.pos(k, T[0]) - g[0]))
            if dd < 0.8 and d0 > 1.5 and (best is None or dd < best[0]):
                best = (dd, k)
        if best:
            ev.append(dict(t=float(T[-1]), track_idx=best[1], xy=[float(e[0]), float(e[1])], n=1, loss=1.0,
                           kind="rolling", d=best[0]))
    return ev


def _outpost_events(R, rules, hz_min=1.5):
    ev = []
    for k in range(R.n):
        al = R.alliance[k]
        op = (rules.get("outposts") or {}).get(al)
        if not op:
            continue
        c = np.asarray(op["centre_m"], float)
        tt = np.arange(R.t0 + 0.5, R.t1 - 0.5, 0.1)
        P = R.pos(k, tt)
        near = (np.linalg.norm(P - c, axis=1) < 1.3) & (R.speed(k, tt) < 0.35)
        i = 0
        while i < len(tt):
            if not near[i]:
                i += 1; continue
            j = i
            while j + 1 < len(tt) and near[j + 1]:
                j += 1
            if tt[j] - tt[i] >= hz_min:
                ev.append(dict(t=float(tt[i] + 0.5 * (tt[j] - tt[i])), track_idx=k, xy=[float(c[0]), float(c[1])],
                               n=1, loss=0.0, kind="outpost", dur=float(tt[j] - tt[i])))
            i = j + 1
    return ev


def detect_intakes(video, fuel, match, cam, rules, log=print, use_rolling=False, use_outpost=False, min_loss=MIN_LOSS):
    t_all = time.time()
    fuel, match, rules = _load(fuel), _load(match), _load(rules)
    if not isinstance(cam, CAMM.Camera):
        cam = FUEL.load_camera(cam) if isinstance(cam, str) else CAMM.Camera(cam)
    R = _Robots(match)
    grid = _Grid(cam)
    ts, D, SH = _snapshots(video, grid, R.t0 - 1.0, R.t1 + 1.0, log, R, cam)
    log(f"  intakes: {len(ts)} snapshots in {time.time() - t_all:.1f}s")
    # bad frames (cuts / replays / moving camera): total density far from its local median
    tot = np.nansum(D.reshape(len(D), -1), 1)
    med = np.array([np.median(tot[max(0, i - 7):i + 8]) for i in range(len(tot))])
    good = np.abs(tot - med) <= 0.35 * np.maximum(med, 20)
    smed = np.array([np.percentile(SH[max(0, i - 150):i + 151], 75) for i in range(len(SH))])
    good &= SH >= BLUR_FRAC * smed                     # camera shake / zoom blur
    log(f"  intakes: {int((~good).sum())}/{len(good)} snapshots rejected (cut / blur)")
    ts, D = ts[good], D[good]
    ev = _floor_events(D, ts, R, grid, log, min_loss)
    roll = _rolling_events(fuel, R, cam, ev) if use_rolling else []
    for r in roll:                                     # merge into floor events
        for e in ev:
            if e["track_idx"] == r["track_idx"] and abs(e["t"] - r["t"]) < 1.2 and \
                    np.hypot(*(np.subtract(e["xy"], r["xy"]))) < 1.5:
                e["rolling"] = e.get("rolling", 0) + 1
                r["merged"] = True
                break
    ev += [r for r in roll if not r.get("merged")]
    if use_outpost:
        ev += _outpost_events(R, rules)
    out = []
    for e in sorted(ev, key=lambda e: e["t"]):
        if e["kind"] == "floor":
            conf = 1 - math.exp(-(e["loss"] - 0.8) / 1.5) if e["loss"] > 0.8 else 0.1
            conf *= 0.5 + 0.5 * e["vfrac"]
            conf = min(0.95, conf + 0.15 * min(e.get("rolling", 0), 2))
        elif e["kind"] == "rolling":
            conf = 0.45 - 0.2 * e["d"]
        else:
            conf = 0.35
        out.append(dict(t=round(e["t"], 3), match_t=round(e["t"] - R.t0 - 0.5, 3), track_idx=int(e["track_idx"]),
                        xy=[round(e["xy"][0], 2), round(e["xy"][1], 2)], n=int(min(40, max(1, e["n"]))), conf=round(float(conf), 3),
                        kind=e["kind"]))
    log(f"  intakes: {len(out)} events in {time.time() - t_all:.1f}s")
    return out


if __name__ == "__main__":
    video, fj, mj, cj, rj, outp = sys.argv[1:7]
    m = json.load(open(mj))
    cam = FUEL.load_camera(cj, mj)
    ev = detect_intakes(video, json.load(open(fj)), m, cam, json.load(open(rj)),
                        min_loss=float(os.environ.get('MIN_LOSS', MIN_LOSS)), use_rolling=bool(os.environ.get('ROLLING')),
                        use_outpost=bool(os.environ.get('OUTPOST')))
    json.dump(ev, open(outp, "w"))
    print("wrote", outp)
