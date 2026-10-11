"""Hub cams: check shot OUTCOMES with the two driver-station cameras (stationcam.py insets).

    verify_shots(events, match, stationcams, log, fuel=, ocam=, rules=, tba_match=, det=) -> events
    run_match(event_dir, match, events, fuel, wide_video, inset_video, tba_match=, sync=, log=) -> events
    python live/hubcam.py EVENT_DIR MATCH.json SHOTS.json FUEL.json WIDE.mp4 INSET.mp4 OUT.json [TBA.json]

Geometry (checked on the images): the station cameras sit ~1.8-1.9 m high, i.e. AT the 1.829 m
opening, so the opening is seen edge-on (a line along the top of the clear funnel walls).  The white
"lid" is the hub box roof (~1.35 m, bottom of the clear funnel), which is why it looks lower than the
opening.  A made ball is seen dropping between the rim line and the lid and vanishing into the lid.
The station pictures are 60p content and lead the overhead picture by LEAD = 0.085 s (fitted).

Method
  1. detect(): yellowness (chroma planes) in a hub ROI of each inset, minus a temporal-min background
     (+-12 frames excluding +-3), connected blobs (balls 6-25 px).  track(): short 2D tracklets (only
     used for station-only entries).
  2. match_shot(): for every hub-bound shot (made / missed, or a pass whose arc passes <1.6 m from a
     hub) the shot's own 3D arc (shots.py fit) predicts the ball in every station frame; the nearest
     plausible blob within a shrinking gate (45 -> 5 px) is taken and ONE ballistic arc refit to the
     overhead track + station sightings jointly (2 views ~70 deg apart -> depth resolved).
  3. station_evidence(): rim-plane crossing distance of the joint arc (d_cross), sightings inside the
     funnel, last sighting inside the funnel image, a ball rising out of the funnel after the last
     sighting (bounce-out), crowding, d0 = how far the sightings are from the shot's own arc.
  4. decide(): station evidence + the overhead outcome -> revised outcome / outcome_conf.  Flips need
     the station ball to be the shot's ball beyond doubt (on the shot's arc, >= 6 sightings, overhead
     fit not degraded, no other ball nearby for bounce calls); otherwise the station view only confirms
     (made + entry seen -> outcome_conf 0.95) or leaves the shot alone.
  5. Station-only entries: tracklets coming down into the funnel that no shot's arc explains
     (events["station_entries"], with period / hub_active); NOT added to the reconciliation totals
     (each period's "station_only" count is reported alongside) -- see NOTES for why.

Output (Events schema unchanged, additive): shots[].hubcam = {hub, status (match | no_match |
no_overhead), verdict (station_entry | bounce_out | station_wide | station_seen | no_match ...),
outcome0, outcome_conf0, evidence..., arc_joint {origin, v0, acc}, obs [[t, u, v] inset px]};
events.station_entries[], events.hubcam (stats), events.reconcile recomputed (old one kept as
reconcile_overhead).
"""
import os, sys, json, math, time
import numpy as np, cv2, av

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import camera as CAMM
import stationcam as SC

HUB = {"red": np.array([11.915, 4.035]), "blue": np.array([4.626, 4.035])}
RIM_Z = 1.829                 # m, opening (top of the clear funnel; the camera sees it edge-on)
LID_Z = 1.35                  # m, white lid of the hub box (bottom of the clear funnel)
OPEN_R = 0.53                 # m, opening half-width (hexagon ~1.06 m)
LEAD = 0.085                  # s, station picture leads the overhead picture (fitted on dev matches, +-0.017)

# ---------------------------------------------------------------- detection
ROI_BOX = (1.5, 1.3, 0.9, 4.2)     # +-x, +-y (m) around the hub, z from .. to (m)
K_BG = 12                          # frames (station insets are 60p content) each side of the temporal-min background
K_SKIP = 3                         # ... excluding this many neighbours (the ball itself)
TY, TD = 55, 40                    # min yellowness, min yellowness above the background
MIN_AREA = 10                      # px (inset full res)


def yellow_from_uv(U, V):
    y = cv2.subtract(np.full(U.shape, 128, np.uint8), U)
    y = cv2.add(y, y)
    pen = cv2.subtract(cv2.absdiff(V, 140), 15)
    return cv2.subtract(y, cv2.add(pen, pen))


def hub_roi(cam, w, W, H):
    """Inset-pixel rect (x0, y0, x1, y1) around station w's own hub (opening + approach)."""
    c = HUB[w]; ax, ay, z0, z1 = ROI_BOX
    P = np.array([[c[0] + a, c[1] + b, z] for a in (-ax, ax) for b in (-ay, ay) for z in (z0, z1)])
    q = cam.project(P)
    x0, y0 = np.floor(np.nanmin(q, 0)).astype(int); x1, y1 = np.ceil(np.nanmax(q, 0)).astype(int)
    x0 -= x0 % 2; y0 -= y0 % 2
    return max(0, x0), max(0, y0), min(W, x1 + (x1 % 2)), min(H, y1 + (y1 % 2))


def detect(sc, t0=None, t1=None, log=print):
    """Moving yellow blobs in each station view around its hub.
    -> {w: dict(t=(n,) overhead-equivalent s, dets=[(m,4) u, v, r, peak] inset px), roi}"""
    rois, crops = {}, {}
    for w in ("red", "blue"):
        if w not in sc.cam or not sc.usable.get(w): continue
        x0, y0, _, _ = SC.RECTS[w]
        r = hub_roi(sc.cam[w], w, sc.cam[w].W, sc.cam[w].H)
        rois[w] = r
        crops[w] = (r[0] + x0, r[1] + y0, r[2] + x0, r[3] + y0)      # full-frame px
    out = {w: dict(t=[], dets=[], roi=rois[w]) for w in rois}
    if not rois: return out
    c = av.open(sc.inset_video); s = c.streams.video[0]; s.thread_type = "AUTO"
    if t0 is not None and t0 > 2:
        c.seek(int(max(0.0, float(sc.inset_time(t0)) - 2) / s.time_base), stream=s)
    buf = {w: [] for w in rois}       # (t, yel)
    prev = None
    nd = nf = 0
    def proc(w, i):
        B = buf[w]; t, y = B[i]
        others = [B[j][1] for j in range(len(B)) if K_SKIP < abs(j - i) <= K_BG]
        bg = np.min(others, 0) if others else np.zeros_like(y)
        d = cv2.subtract(y, bg)
        m = ((d > TD) & (y > TY)).astype(np.uint8)
        n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
        dets = []
        for k in range(1, n):
            x, yy0, ww_, hh_, a = st[k]
            if a < MIN_AREA: continue
            wgt = d[yy0:yy0 + hh_, x:x + ww_].astype(np.float32) * (lab[yy0:yy0 + hh_, x:x + ww_] == k)
            gy, gx = np.mgrid[0:hh_, 0:ww_]
            sw = wgt.sum() + 1e-6
            dets.append(((wgt * gx).sum() / sw + x + out[w]["roi"][0], (wgt * gy).sum() / sw + yy0 + out[w]["roi"][1],
                         math.sqrt(a / math.pi), float(wgt.max()), ww_, hh_))
        out[w]["t"].append(t); out[w]["dets"].append(np.array(dets, np.float32).reshape(-1, 6))

    def flush(w, final=False):
        B = buf[w]
        if final:
            for i in range(max(0, len(B) - K_BG), len(B)): proc(w, i)
            return
        if len(B) == 2 * K_BG + 1:
            proc(w, K_BG); B.pop(0)
        elif len(B) <= K_BG and not out[w]["t"]:
            pass
    try:
        for f in c.decode(s):
            if f.time is None: continue
            tw = float(sc.wide_time(f.time))
            if t0 is not None and tw < t0 - 0.3: continue
            if t1 is not None and tw > t1 + 0.3: break
            H, W = f.height, f.width
            p0, p1, p2 = f.planes
            Y = np.frombuffer(p0, np.uint8).reshape(H, p0.line_size)
            smp = np.concatenate([Y[cr[1]:cr[3]:4, cr[0]:cr[2]:4].ravel() for cr in crops.values()]).astype(np.int16)
            if prev is not None and (np.abs(smp - prev) > 10).mean() * 100 < 0.05:
                nd += 1; continue                                   # exact duplicate
            prev = smp
            if smp.mean() < 20: continue
            nf += 1
            U = np.frombuffer(p1, np.uint8).reshape(H // 2, p1.line_size)
            V = np.frombuffer(p2, np.uint8).reshape(H // 2, p2.line_size)
            for w, (X0, Y0, X1, Y1) in crops.items():
                yel = yellow_from_uv(U[Y0 // 2:Y1 // 2, X0 // 2:X1 // 2], V[Y0 // 2:Y1 // 2, X0 // 2:X1 // 2])
                yel = cv2.resize(yel, (X1 - X0, Y1 - Y0), interpolation=cv2.INTER_LINEAR)
                buf[w].append((tw + LEAD, yel))
                flush(w)
    finally:
        c.close()
    for w in buf:
        flush(w, final=True)
        out[w]["t"] = np.array(out[w]["t"])
    log(f"  hubcam detect: {nf} frames ({nd} duplicates), dets/frame "
        + ", ".join(f"{w} {np.mean([len(d) for d in out[w]['dets']]):.2f}" for w in out))
    return out


# ---------------------------------------------------------------- station 2D tracklets
TRK_GATE, TRK_GATE_V, TRK_COAST = 9.0, 0.35, 3      # px, x |velocity px/frame|, frames
TRK_MIN = 3


def track(det):
    """Greedy-Hungarian linking of one station's detections (60 Hz) into 2D tracklets.
    -> list of dict(t (n,), uv (n,2), r (n,))"""
    from scipy.optimize import linear_sum_assignment
    T, Ds = det["t"], det["dets"]
    act, done = [], []
    for i, (t, D) in enumerate(zip(T, Ds)):
        P = np.array([a["pred"] for a in act]).reshape(-1, 2)
        used = set()
        if len(act) and len(D):
            C = np.linalg.norm(P[:, None] - D[None, :, :2], axis=2)
            g = np.array([TRK_GATE + TRK_GATE_V * np.linalg.norm(a["vel"]) for a in act])[:, None]
            C = np.where(C < g, C, 1e6)
            ri, ci = linear_sum_assignment(C)
            for a_, d_ in zip(ri, ci):
                if C[a_, d_] >= 1e6: continue
                a = act[a_]; d = D[d_]
                dt = (t - a["t"][-1]) * 60.0
                a["vel"] = (d[:2] - np.array(a["uv"][-1])) / max(dt, 1.0)
                a["t"].append(t); a["uv"].append(d[:2].tolist()); a["r"].append(float(d[2])); a["miss"] = 0
                used.add(d_)
        keep = []
        for a in act:
            if a["t"][-1] != t:
                a["miss"] += 1
            if a["miss"] > TRK_COAST:
                done.append(a)
            else:
                k = (T[i + 1] - a["t"][-1]) * 60.0 if i + 1 < len(T) else 1.0
                a["pred"] = np.array(a["uv"][-1]) + a["vel"] * k
                keep.append(a)
        act = keep
        for d_ in range(len(D)):
            if d_ in used: continue
            d = D[d_]
            act.append(dict(t=[t], uv=[d[:2].tolist()], r=[float(d[2])], vel=np.zeros(2), pred=d[:2].copy(), miss=0))
    done += act
    out = []
    for a in done:
        if len(a["t"]) >= TRK_MIN:
            out.append(dict(t=np.array(a["t"]), uv=np.array(a["uv"]), r=np.array(a["r"])))
    out.sort(key=lambda a: a["t"][0])
    return out


# ---------------------------------------------------------------- joint fit (overhead + station)
G = 9.81
C_DRAG = 0.15
SIG_ACC = 0.7


def _phi(tau, c=C_DRAG):
    tau = np.asarray(tau, float)
    e = -np.expm1(-c * tau); p1 = e / c
    return p1, (tau - p1) / c


def traj(th, tau):
    p1, p2 = _phi(tau)
    return th[0:3] + th[3:6] * p1[..., None] + (th[6:9] + [0, 0, -G]) * p2[..., None]


def traj_vel(th, tau):
    tau = np.asarray(tau, float)[..., None]; e = np.exp(-C_DRAG * tau)
    return th[3:6] * e + (th[6:9] + [0, 0, -G]) * (1 - e) / C_DRAG


def _norm(cam, uv):
    return cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), cam.K, cam.D).reshape(-1, 2)


def joint_fit(tL, obs, th0, prior_p=None, sig_p=(0.5, 0.5, 0.4), iters=3):
    """Ballistic arc (launch time tL fixed) from observations in several cameras.
    obs: list of (cam, t (n,), uv (n,2), sig_px).  th0: initial [p, v, a] (depth weights).
    -> th (9,), per-obs-set rms px list"""
    th = np.asarray(th0, float).copy()
    pre = [(cam, np.asarray(t, float) - tL, _norm(cam, uv), np.asarray(uv, float), s) for cam, t, uv, s in obs]
    for it in range(iters):
        rows, rhs = [], []
        for cam, tau, xn, uv, s in pre:
            if not len(tau): continue
            R, tv, f = cam.R, cam.tvec, float(cam.K[0, 0])
            p1, p2 = _phi(tau)
            X = traj(th, tau)
            depth = np.maximum(X @ R[2] + tv[2], 0.5)
            wgt = f / (s * depth)
            for j, (r, b) in enumerate(((R[0], tv[0]), (R[1], tv[1]))):
                cc = r[None, :] - xn[:, j:j + 1] * R[2][None, :]
                bb = xn[:, j] * tv[2] - b
                A = np.concatenate([cc, cc * p1[:, None], cc * p2[:, None]], 1)
                rhs.append((bb + G * cc[:, 2] * p2) * wgt); rows.append(A * wgt[:, None])
        for j in range(3):
            a = np.zeros(9); a[6 + j] = 1 / SIG_ACC; rows.append(a[None]); rhs.append(np.zeros(1))
        if prior_p is not None:
            for j in range(3):
                a = np.zeros(9); a[j] = 1 / sig_p[j]; rows.append(a[None]); rhs.append(np.array([prior_p[j] / sig_p[j]]))
        A = np.concatenate(rows); b = np.concatenate(rhs)
        th = np.linalg.lstsq(A, b, rcond=None)[0]
    rms = []
    for cam, tau, xn, uv, s in pre:
        if not len(tau): rms.append(None); continue
        q = cam.project(traj(th, tau)).reshape(-1, 2)
        rms.append(float(np.sqrt(((q - uv) ** 2).sum(1).mean())))
    return th, rms


def resid(cam, th, tL, t, uv):
    q = cam.project(traj(th, np.asarray(t, float) - tL)).reshape(-1, 2)
    return np.linalg.norm(q - np.asarray(uv, float), axis=1)


# ---------------------------------------------------------------- per-shot matching
GATE_PX = 45.0          # px, station tracklet points this close to the overhead arc are candidates
TRIM_PX = 5.0           # px, joint-fit inlier residual in the station view
SIG_OVER, SIG_ST = 2.5, 2.0
MIN_IN = 4              # station inliers for a match
MAX_RS = 3.5            # px, station rms of a match
MAX_DRO = 1.5           # px, allowed increase of the overhead rms ...
MAX_RO = 4.0            # px, ... or overhead rms up to this


def _fit_trim(tL, oobs, scam, ts, uv, th0, prior):
    keep = np.ones(len(ts), bool)
    while True:
        th, (ro, rs) = joint_fit(tL, [oobs, (scam, ts[keep], uv[keep], SIG_ST)], th0, prior_p=prior)
        r = resid(scam, th, tL, ts, uv)
        bad = np.where(keep & (r > TRIM_PX))[0]
        if not len(bad) or keep.sum() <= MIN_IN:
            break
        keep[bad[np.argmax(r[bad])]] = False
    return th, ro, rs, keep, r


def match_shot(s, w, det, scam, ocam, T, UV, tho, ro0, er=None):
    """The shot's ball in one station view, by arc-guided association on the per-frame detections
    (no 2D tracker: dense volleys and 60 Hz motion break tracklets).  The shot's own arc (tho, the
    shots.py fit incl. its hub-entry anchor) predicts where the ball is in every station frame; the
    nearest plausible blob within a shrinking gate is taken, the 2-camera ballistic arc refit, repeat.
    -> dict(th, ro, rs, n_in, t (n,), uv (n,2), j (frame idx)) or None.  ro0: overhead rms of tho."""
    tL = s["t"]; te = s.get("t_entry") or s["t_obs"][1]
    Td, Dd = det["t"], det["dets"]
    fr = np.where((Td > tL + 0.05) & (Td < te + 0.35))[0]
    if len(fr) < MIN_IN: return None
    oobs = (ocam, T, UV, SIG_OVER)
    th = tho
    sel = None
    for gate in (GATE_PX, 16.0, 8.0, 5.0):
        q = scam.project(traj(th, Td[fr] - tL)).reshape(-1, 2)
        ts, uv, js = [], [], []
        for n, j in enumerate(fr):
            D = Dd[j]
            if not len(D) or not np.isfinite(q[n]).all(): continue
            d = np.linalg.norm(D[:, :2] - q[n], axis=1)
            if er is not None:
                d = np.where((D[:, 2] > 0.4 * er) & (D[:, 2] < 2.5 * er + 2), d, np.inf)
            m = int(np.argmin(d))
            if d[m] < gate:
                ts.append(Td[j]); uv.append(D[m, :2]); js.append(j)
        if len(ts) < MIN_IN:
            return None
        ts, uv = np.array(ts), np.array(uv, float)
        th, (ro, rs) = joint_fit(tL, [oobs, (scam, ts, uv, SIG_ST)], th, prior_p=tho[:3])
        sel = (ts, uv, np.array(js))
    r = resid(scam, th, tL, sel[0], sel[1]); keep = r < TRIM_PX
    if keep.sum() < MIN_IN: return None
    ts, uv, js = sel[0][keep], sel[1][keep], sel[2][keep]
    th, (ro, rs) = joint_fit(tL, [oobs, (scam, ts, uv, SIG_ST)], th, prior_p=tho[:3])
    if rs > MAX_RS or ro > max(ro0 + MAX_DRO + 0.1 * ro0, MAX_RO):
        return None
    d0 = float(np.median(np.linalg.norm(scam.project(traj(tho, ts - tL)).reshape(-1, 2) - uv, axis=1)))
    return dict(th=th, ro=ro, rs=rs, n_in=int(len(ts)), t=ts, uv=uv, j=js, d0=d0)


def _rim_cross(th, tL, t_from, t_to=None, z=RIM_Z):
    """First descending crossing of plane z after t_from -> (t, xy) or None."""
    ts = np.arange(t_from, (t_to or t_from + 3.0), 1 / 240.)
    X = traj(th, ts - tL); V = traj_vel(th, ts - tL)
    i = np.where((X[:-1, 2] >= z) & (X[1:, 2] < z) & (V[1:, 2] < 0))[0]
    if not len(i): return None
    return float(ts[i[0] + 1]), X[i[0] + 1, :2]


def funnel_hull(cam, w):
    """Image hull (inset px) of the clear funnel: the opening hexagon swept from the lid to the rim."""
    c = HUB[w]; a = np.linspace(0, 2 * np.pi, 13)[:-1]
    P = np.concatenate([np.c_[c[0] + OPEN_R * np.cos(a), c[1] + OPEN_R * np.sin(a), np.full(len(a), z)]
                        for z in (LID_Z, RIM_Z)])
    return cv2.convexHull(cam.project(P).reshape(-1, 2).astype(np.float32)).reshape(-1, 2)


def _inside(hull, uv, margin=0.0):
    return np.array([cv2.pointPolygonTest(hull, (float(u), float(v)), True) >= -margin for u, v in np.atleast_2d(uv)])


def station_evidence(s, w, det, scam, mt, hull):
    """What the station camera saw of a matched shot (features for decide())."""
    tL = s["t"]; th = mt["th"]; ti, ui = mt["t"], mt["uv"]
    X = traj(th, ti - tL); d = np.linalg.norm(X[:, :2] - HUB[w], axis=1); z = X[:, 2]
    ev = dict(n_in=int(mt["n_in"]), rs=round(float(mt["rs"]), 2), ro=round(float(mt["ro"]), 2),
              t_first=round(float(ti[0]), 3), t_last=round(float(ti[-1]), 3),
              z_last=round(float(z[-1]), 2), d_last=round(float(d[-1]), 2))
    ev["n_inside"] = int(((z < RIM_Z - 0.08) & (z > LID_Z - 0.10) & (d < OPEN_R - 0.02)).sum())
    ev["end_in_funnel"] = bool(_inside(hull, ui[-1:])[0])
    ev["hull_d_last"] = round(float(cv2.pointPolygonTest(hull, (float(ui[-1][0]), float(ui[-1][1])), True)), 1)
    ev["d0"] = round(float(mt["d0"]), 1)
    # other balls near the last sighting (+-0.15 s): dense streams make bounce / identity calls unsafe
    Td_, Dd_ = det["t"], det["dets"]; crowd = 0
    for j in range(max(0, int(mt["j"][-1]) - 9), min(len(Td_), int(mt["j"][-1]) + 10)):
        D_ = Dd_[j]
        if len(D_):
            dd_ = np.linalg.norm(D_[:, :2] - ui[-1], axis=1)
            crowd = max(crowd, int(((dd_ > 6) & (dd_ < 45)).sum()))
    ev["crowd"] = crowd
    cr = _rim_cross(th, tL, float(ti[0]) - 0.5)
    if cr is not None:
        ev["t_cross"] = round(cr[0], 3); ev["d_cross"] = round(float(np.linalg.norm(cr[1] - HUB[w])), 2)
        ev["seen_to_cross"] = round(float(ti[-1] - cr[0]), 3)
    # bounce-out: after the last sighting, a blob near it that RISES (follow it frame by frame)
    Td, Dd = det["t"], det["dets"]
    j0 = int(mt["j"][-1]); p = ui[-1].copy(); up = 0.0; n = 0
    for j in range(j0 + 1, len(Td)):
        if Td[j] > ti[-1] + 0.45: break
        D = Dd[j]
        if not len(D): continue
        dd = np.linalg.norm(D[:, :2] - p, axis=1); m = int(np.argmin(dd))
        if dd[m] < 14.0:
            p = D[m, :2].copy(); n += 1
            up = max(up, float(ui[-1][1] - p[1]))
    ev.update(after_n=n, after_up=round(up, 1))
    return ev


# ---------------------------------------------------------------- decision
BOUNCE_UP_PX = 25.0     # px, a ball rising this far after reaching the rim = bounce-out
IN_R = 0.50             # m, rim-plane crossing this close to the hub centre (seen near the crossing) = entry
FAR_R = 1.0             # m, ... this far = clear miss
SEEN_DT = -0.12         # s, last station sighting no earlier than this before the rim crossing
BOUNCE_CROWD = int(os.environ.get("HC_BOUNCE_CROWD", 1))   # max other balls near a bounce-out
BOUNCE_D0 = float(os.environ.get("HC_BOUNCE_D0", 25.0))  # px
D0_FLIP = 15.0          # px, median distance of the station sightings from the shot's own arc to allow a flip


def decide(s, e, ro0=None):
    """Overhead outcome + station evidence e -> (outcome, outcome_conf, verdict).
    Flips need the station ball to be THE shot's ball beyond doubt: it lies on the shot's own arc
    (d0 <= D0_FLIP px median), several sightings, overhead fit not degraded; bounce calls also need
    no other ball nearby.  Otherwise the station view can only confirm."""
    o0, c0 = s["outcome"], float(s.get("outcome_conf") or 0.5)
    if e is None or e.get("status") != "match":
        return o0, c0, (e or {}).get("status", "none")
    seen = e.get("seen_to_cross") is not None and e["seen_to_cross"] > SEEN_DT
    dc = e.get("d_cross")
    sure = e["d0"] <= D0_FLIP and e["n_in"] >= 6 and (ro0 is None or e["ro"] <= ro0 + 1.0)
    bounce = (e["after_up"] > BOUNCE_UP_PX and LID_Z - 0.1 < e["z_last"] < RIM_Z + 0.6 and e["d_last"] < 1.0
              and e["crowd"] <= BOUNCE_CROWD)
    entry = (not bounce) and dc is not None and dc < IN_R and seen and (e["n_inside"] >= 1 or e["end_in_funnel"])
    far = (not bounce) and dc is not None and dc > FAR_R and e["n_inside"] == 0 and e["n_in"] >= 8 \
        and e.get("seen_to_cross", -9) > -0.05 and e["hull_d_last"] < -10
    if o0 == "made":
        if bounce and e["d0"] <= BOUNCE_D0 and e["n_in"] >= 6:
            return "missed", 0.8, "bounce_out"
        if far and sure and e["rs"] < 3.0:
            return "missed", 0.75, "station_wide"
        if entry:
            return "made", max(c0, 0.95), "station_entry"
        return o0, c0, "station_seen"
    if o0 in ("missed", "pass"):
        if entry and sure and e["n_inside"] >= 2 and e["end_in_funnel"] and dc < 0.45 and e["rs"] < 3.0:
            return "made", 0.8, "station_entry"
        if bounce or far:
            return o0, max(c0, 0.8), "bounce_out" if bounce else "station_wide"
    return o0, c0, "station_seen"


# ---------------------------------------------------------------- station-only entries
ENTRY_MIN_N = 5
CLAIM_PX = 45.0         # px, tracklet points this close to some shot's arc (same time) are that shot's ball
ENTRY_MERGE = (0.2, 30.0)   # s, px: entry tracklets closer than this are one ball
ENTRY_DROP_PX = 6.0     # px, an entry must be seen this far below the rim line inside the funnel
R_RATIO = (0.55, 1.7)   # blob radius / expected radius of a ball at the hub (depth check)


def _exp_r(cam, w):
    return float(cam.K[0, 0]) * 0.075 / float(np.linalg.norm(np.r_[HUB[w], 1.6] - cam.C))


def entry_tracklets(trk, cam, w, hull):
    """Tracklets that come down from above the rim and end inside the funnel image (no bounce-out)."""
    top = hull[:, 1].min()
    er = _exp_r(cam, w)
    out = []
    for k, a in enumerate(trk):
        if len(a["t"]) < ENTRY_MIN_N: continue
        uv = a["uv"]
        if not _inside(hull, uv[-1:])[0]: continue
        if uv[:, 1].min() > top + 4: continue                         # never above the rim line
        if uv[-1, 1] - uv[max(0, len(uv) - 4), 1] < 2: continue        # not descending at the end
        inside = _inside(hull, uv)
        if not inside.any() or uv[-1, 1] - top < ENTRY_DROP_PX: continue
        # it crossed into the funnel image from above (not from the side, e.g. a ball rolling off the lid)
        j0 = int(np.argmax(inside))
        if j0 == 0 or uv[j0 - 1, 1] > uv[j0, 1]: continue
        rr = float(np.median(a["r"])) / er
        if not (R_RATIO[0] < rr < R_RATIO[1]): continue
        bounce = False
        for b in trk:
            if b is a or b["t"][0] < a["t"][-1] - 0.02 or b["t"][0] > a["t"][-1] + 0.25: continue
            if np.linalg.norm(b["uv"][0] - uv[-1]) < 30 and uv[-1, 1] - b["uv"][:, 1].min() > BOUNCE_UP_PX:
                bounce = True; break
        if bounce: continue
        out.append(dict(k=k, t_end=float(a["t"][-1]), r_ratio=round(rr, 2)))
    return out


# ---------------------------------------------------------------- main
def _ocam(sc, match):
    cj = json.load(open(os.path.join(sc.event_dir, "camera.json")))
    if match.get("camera_pose"): cj.update(match["camera_pose"])
    return CAMM.Camera(cj)


def _fuel_index(fuel):
    if fuel is None: return {}
    tr = fuel["tracks"] if isinstance(fuel, dict) else fuel
    return {t["id"]: t for t in tr}


def verify_shots(events, match, stationcams, log=print, fuel=None, ocam=None, rules=None, tba_match=None,
                 det=None):
    """Check every hub-bound shot against the station ("hub cam") views and revise outcomes.

    events  Events of shots.reconstruct (modified in place and returned)
    match   processed match JSON; stationcams  stationcam.StationCams for this match (synced)
    fuel    FuelResult (or the compact airborne list) whose tracks the shots' fuel_ids refer to --
            the overhead observations of each flight (needed for the 2-camera fit)
    ocam    overhead camera.Camera with this match's pose (default: event camera.json + camera_pose)
    rules / tba_match: for periods / hub-active / reconciliation (shots.Reconstructor)
    det     precomputed {"det":..., "tr":...} (detect()/track()) to skip decoding

    Per shot (hub-bound ones): s["hubcam"] = {status, verdict, outcome0, outcome_conf0, + evidence};
    s["outcome"] / s["outcome_conf"] revised.  events["station_entries"]: balls seen entering a hub in
    the station view that no shot explains (unattributed made fuel).  events["hubcam"]: stats.
    events["reconcile"] recomputed from the revised outcomes, each period also gets "station_only"."""
    import shots as SH
    t0 = time.time()
    sc = stationcams
    ocam = ocam or _ocam(sc, match)
    if det is None:
        m = match; a = m["t_start"] - 1.0; b = m["t_start"] - 0.5 + m["frames"] / 30.0 + 3.0
        D = detect(sc, a, b, log=log); TR = {w: track(D[w]) for w in D}
    else:
        D, TR = det["det"], det["tr"]
    t_det = time.time() - t0
    FU = _fuel_index(fuel)
    hulls = {w: funnel_hull(sc.cam[w], w) for w in TR}
    ers = {w: _exp_r(sc.cam[w], w) for w in TR}
    claimed = {w: set() for w in TR}         # station blobs used as some shot's ball (u, v)
    st = dict(checked=0, matched=0, changed={}, verdicts={})
    for s in events["shots"]:
        th0 = np.r_[s["origin"], s["v0"], s.get("acc") or [0, 0, 0]]
        w = (s.get("target") or "").split("_")[0]
        if not w:
            if s.get("hub_dmin") is None or s["hub_dmin"] > 1.6: continue
            XX = traj(th0, np.arange(0, 4, 0.01)); XX = XX[XX[:, 2] > 0.5]
            if not len(XX): continue
            w = min(HUB, key=lambda h: np.min(np.linalg.norm(XX[:, :2] - HUB[h], axis=1)))
        if w not in TR: continue
        e = dict(hub=w)
        tr = [FU[f] for f in s.get("fuel_ids", []) if f in FU]
        T = np.concatenate([np.asarray(x["t"], float) for x in tr]) if tr else np.zeros(0)
        if len(T) < 4:
            e["status"] = "no_overhead"
        else:
            UV = np.concatenate([np.asarray(x["uv"], float) for x in tr]); o = np.argsort(T); T, UV = T[o], UV[o]
            ro0 = float(np.sqrt((resid(ocam, th0, s["t"], T, UV) ** 2).mean())); e["ro0"] = round(ro0, 2)
            te = s.get("t_entry") or s["t_obs"][1]
            q = sc.cam[w].project(traj(th0, np.array([te - s["t"]]))).reshape(2)
            e["visible"] = bool(np.isfinite(q).all() and 0 <= q[0] < sc.cam[w].W and 0 <= q[1] < sc.cam[w].H)
            st["checked"] += 1
            mt = match_shot(s, w, D[w], sc.cam[w], ocam, T, UV, th0, ro0, er=ers[w])
            if mt is None:
                e["status"] = "no_match"
            else:
                st["matched"] += 1
                e["status"] = "match"; e.update(station_evidence(s, w, D[w], sc.cam[w], mt, hulls[w]))
                claimed[w].update(tuple(np.round(u, 1)) for u in mt["uv"])
                e["obs"] = [[round(float(t_), 3), round(float(u_[0]), 1), round(float(u_[1]), 1)] for t_, u_ in zip(mt["t"], mt["uv"])]
                e["_th"] = mt["th"]
        out, conf, verdict = decide(s, e, e.get("ro0"))
        e.update(verdict=verdict, outcome0=s["outcome"], outcome_conf0=s.get("outcome_conf"))
        st["verdicts"][verdict] = st["verdicts"].get(verdict, 0) + 1
        if out != s["outcome"]:
            key_ = f"{s['outcome']}->{out}"; st["changed"][key_] = st["changed"].get(key_, 0) + 1
            if out == "made" and not s.get("target"):
                s["target"] = f"{w}_hub"
            if out == "made":
                s["t_entry"] = e.get("t_cross", s.get("t_entry") or s["t_obs"][1])
            s["outcome"] = out
        s["outcome_conf"] = round(float(conf), 2)
        thj = e.pop("_th", None)
        if thj is not None:
            e["arc_joint"] = dict(origin=np.round(thj[0:3], 3).tolist(), v0=np.round(thj[3:6], 2).tolist(),
                                  acc=np.round(thj[6:9], 2).tolist())
        s["hubcam"] = e
    # balls seen entering that no shot explains
    entries = []
    for w in TR:
        for en in entry_tracklets(TR[w], sc.cam[w], w, hulls[w]):
            a = TR[w][en["k"]]
            # its points were already used as some shot's ball
            pts = {tuple(np.round(u, 1)) for u in a["uv"]}
            if len(pts & claimed[w]) >= 0.5 * len(pts): continue
            best = None
            for s in events["shots"]:
                if s["t"] > a["t"][-1] or (s.get("t_obs") or [0, 0])[1] + 1.0 < a["t"][0]: continue
                th = np.r_[s["origin"], s["v0"], s.get("acc") or [0, 0, 0]]
                q = sc.cam[w].project(traj(th, a["t"] - s["t"])).reshape(-1, 2)
                dd = np.linalg.norm(q - a["uv"], axis=1)
                if np.mean(dd < CLAIM_PX) > 0.5 and (best is None or np.median(dd) < best[0]):
                    best = (float(np.median(dd)), s)
            if best is not None:
                if best[1]["outcome"] != "made":
                    best[1].setdefault("hubcam", dict(hub=w, status="not_checked", verdict="none"))["loose_entry"] = dict(t=round(en["t_end"], 3), d_px=round(best[0], 1), n=int(len(a["t"])))
                continue
            if any(e_["hub"] == w and abs(e_["t"] - en["t_end"]) < ENTRY_MERGE[0]
                   and np.linalg.norm(np.array(e_["uv_end"]) - a["uv"][-1]) < ENTRY_MERGE[1] for e_ in entries):
                continue
            entries.append(dict(hub=w, t=round(en["t_end"], 3), t_first=round(float(a["t"][0]), 3),
                                n=int(len(a["t"])), uv_end=np.round(a["uv"][-1], 1).tolist(), r_ratio=en["r_ratio"]))
    # periods / hub state / reconciliation with the revised outcomes
    if rules is not None:
        R = SH.Reconstructor(match, ocam, rules, tba_match, log=lambda *a: None)
        for en in entries:
            mc, pe = R.clock(en["t"])
            act = R.hub_active_at(en["hub"], en["t"])
            cnt = pe
            if pe and pe.startswith("shift") and R.sched[en["hub"]].get(pe) is False and act:
                names = [p[0] for p in R.F.periods]; cnt = names[names.index(pe) - 1]
            en.update(match_clock=round(mc, 2), period=pe, hub_active=act, counted_period=cnt)
        for s in events["shots"]:                 # newly made shots need counted_period / hub_active
            h = s.get("hubcam")
            if h and h.get("outcome0") != s["outcome"] and s["outcome"] == "made":
                al = s["target"].split("_")[0]; te = s["t_entry"]
                mc, pe = R.clock(te); act = R.sched[al].get(pe) if pe else None; cnt = pe
                if act is False and pe and pe.startswith("shift"):
                    names = [p[0] for p in R.F.periods]; pi = names.index(pe)
                    if mc - R.F.periods[pi][1] <= SH.GRACE_S and R.sched[al].get(names[pi - 1]):
                        act, cnt = True, names[pi - 1]
                s["hub_active"], s["counted_period"] = act, cnt
        cov = (events.get("reconcile") or {}).get("coverage")
        rec = SH.reconcile(events["shots"], tba_match, R.sched, cov)
        for al in ("red", "blue"):
            for p, _ in SH.PERIOD_KEYS:
                rec[al][p]["station_only"] = sum(1 for en in entries if en["hub"] == al and en.get("counted_period") == p
                                                 and en.get("hub_active") is not False)
        events["reconcile_overhead"] = events.get("reconcile")
        events["reconcile"] = rec
    events["station_entries"] = entries
    st.update(station_entries=len(entries), wall_s=round(time.time() - t0, 1), detect_s=round(t_det, 1),
              usable={w: bool(sc.usable.get(w)) for w in ("red", "blue")})
    events["hubcam"] = st
    log(f"  hubcam: {st['checked']} hub-bound shots, {st['matched']} seen in a station view, changed {st['changed']}, "
        f"{len(entries)} station-only entries ({st['wall_s']} s)")
    return events


# ---------------------------------------------------------------- wrapper / CLI
def run_match(event_dir, match, events, fuel, wide_video, inset_video, tba_match=None, sync=None, rules=None,
              log=print):
    """Everything for one match: sync the "Match N" inset upload to the wide video (unless `sync` is
    given), check the station poses, verify the shots.  Returns events (modified) or the input events
    untouched when no station camera is usable."""
    t0 = time.time()
    if rules is None:
        rules = json.load(open(os.path.join(HERE, "field", "rebuilt_rules.json")))
    if sync is None:
        sync = SC.sync_offset(wide_video, inset_video, log=log)
        if not sync.get("ok"):
            log("  hubcam: inset sync not confident -> skipped")
            events["hubcam"] = dict(skipped="sync")
            return events
    sc = SC.StationCams(event_dir, match, inset_video, sync, check_pose=True, log=log)
    if not any(sc.usable.values()):
        events["hubcam"] = dict(skipped="no usable station camera", pose=sc.pose_info)
        return events
    t1 = time.time()
    ev = verify_shots(events, match, sc, log=log, fuel=fuel, rules=rules, tba_match=tba_match)
    ev["hubcam"]["setup_s"] = round(t1 - t0, 1)
    ev["hubcam"]["sync"] = {k: sync[k] for k in ("offset", "constant", "knots") if k in sync} if isinstance(sync, dict) else sync
    return ev


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 7:
        print(__doc__); sys.exit(1)
    ev_dir, mj, sj, fj, wide, inset, out = a[:7]
    tbm = None
    if len(a) > 7:
        import shots as SH
        m_ = json.load(open(mj)); tbm = SH.find_tba_match(a[7], m_.get("key"))
    ev = run_match(ev_dir, json.load(open(mj)), json.load(open(sj)), json.load(open(fj)), wide, inset, tbm,
                   log=lambda *x: print(*x, flush=True))
    json.dump(ev, open(out, "w"), default=float)
    print("wrote", out, flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)          # skip libapriltag's destructor (stationcam.py)
