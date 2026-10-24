"""Fit one locked-off full-frame camera (fisheye-ish broadcast cam) to the field.

Inputs (one JSON, see events/<event>/calib_input.json):
  tags:   {id: [u, v]}           tag CENTRES in pixels (detected or hand-marked)
  lines:  {name: [[u, v], ...]}  pixel samples of straight world lines:
          centerline (X = LEN/2, Z = 0), farrail (Y = 0 - d, Z = h), nearrail (Y = WID + d, Z = h)
          h and d are fitted (rail height / rail set-back are not in the tag layout).
Output: camera JSON {W,H,K,dist,rvec,tvec,...} consumed by live/camera.py.

Model: OpenCV pinhole + radial k1..k3 (+ tangential).  Lines carry most of the
lens information; the tags pin pose and scale.
"""
import json, sys, csv, itertools, numpy as np, cv2
from scipy.optimize import least_squares

IN = 0.0254
LEN, WID = 16.541, 8.069


def load_tags(path):
    T = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        T[int(r["ID"])] = np.array([float(r["X"]), float(r["Y"]), float(r["Z"])]) * IN
    return T


def unpack(p):
    f, cx, cy, k1, k2, k3, p1, p2 = p[:8]
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
    return K, np.array([k1, k2, p1, p2, k3]), p[8:11], p[11:14], p[14], p[15]


def proj(P, p):
    K, D, r, t, _, _ = unpack(p)
    q, _ = cv2.projectPoints(np.asarray(P, float).reshape(-1, 1, 3), r, t, K, D)
    return q.reshape(-1, 2)


def seg_dist(pts, poly):
    a, b = poly[:-1], poly[1:]; ab = b - a
    ap = pts[:, None, :] - a[None]
    t = np.clip((ap * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-9), 0, 1)
    d = np.linalg.norm(ap - t[..., None] * ab, axis=-1)
    return d.min(1)


def world_line(name, h, d, n=300):
    s = np.linspace(-1.0, 1.0, n)
    if name == "centerline":
        return np.c_[np.full(n, LEN / 2), (s + 1) / 2 * WID * 1.2 - 0.6, np.zeros(n)]
    X = (s + 1) / 2 * (LEN + 2) - 1
    if name == "farrail":  return np.c_[X, np.full(n, -d), np.full(n, h)]
    if name == "nearrail": return np.c_[X, np.full(n, WID + d), np.full(n, h)]
    raise KeyError(name)


def residuals(p, tags, lines, w_tag):
    res = []
    ids = sorted(tags)
    P = np.array([TAGS[i] for i in ids]); uv = np.array([tags[i][:2] for i in ids])
    sig = np.array([tags[i][2] if len(tags[i]) > 2 else 1.0 for i in ids])
    res.append(((proj(P, p) - uv) / sig[:, None]).ravel() * w_tag)
    for name, pts in lines.items():
        poly = proj(world_line(name, p[14], p[15]), p)
        res.append(seg_dist(np.asarray(pts, float), poly))
    return np.concatenate(res)


def init(W, H):
    C = np.array([LEN / 2, WID + 4.0, 7.0]); tgt = np.array([LEN / 2, WID / 2, 0.0])
    z = tgt - C; z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0]); x /= np.linalg.norm(x)   # image +u = world -X here
    y = np.cross(z, x)
    R = np.stack([x, y, z]); rv, _ = cv2.Rodrigues(R); t = -R @ C
    return np.r_[0.42 * W, W / 2, H / 2, -0.3, 0.1, 0, 0, 0, rv.ravel(), t, 0.5, 0.05]


if __name__ == "__main__":
    cfg = json.load(open(sys.argv[1])); out = sys.argv[2]
    TAGS = load_tags(cfg["tag_csv"])
    W, H = cfg["W"], cfg["H"]
    tags = {int(k): v for k, v in cfg["tags"].items()}
    lines = cfg["lines"]
    p0 = init(W, H)
    # stage 1: pose + focal only, stage 2: everything (robust loss)
    free1 = np.r_[0, 8, 9, 10, 11, 12, 13, 14, 15, 3]
    def r1(q):
        p = p0.copy(); p[free1] = q; return residuals(p, tags, lines, 3.0)
    s1 = least_squares(r1, p0[free1], loss="soft_l1", f_scale=3.0, max_nfev=4000)
    p = p0.copy(); p[free1] = s1.x
    s2 = least_squares(lambda q: residuals(q, tags, lines, 3.0), p, loss="soft_l1", f_scale=2.0, max_nfev=20000)
    p = s2.x
    K, D, rv, tv, h, d = unpack(p)
    R, _ = cv2.Rodrigues(rv); C = (-R.T @ tv).ravel()
    ids = sorted(tags)
    tr = np.linalg.norm(proj(np.array([TAGS[i] for i in ids]), p) - np.array([tags[i][:2] for i in ids]), axis=1)
    print("tag residuals px:", dict(zip(ids, tr.round(2))))
    for name, pts in lines.items():
        dd = seg_dist(np.asarray(pts, float), proj(world_line(name, h, d), p))
        print(f"{name:10s} n={len(pts):3d} median {np.median(dd):.2f}px  p90 {np.percentile(dd, 90):.2f}px")
    print(f"f {K[0,0]:.1f}  c ({K[0,2]:.1f},{K[1,2]:.1f})  dist {np.round(D, 4).tolist()}")
    print(f"camera at X {C[0]:.2f} Y {C[1]:.2f} Z {C[2]:.2f} m; rail h {h:.3f} m, set-back {d:.3f} m")
    json.dump(dict(W=W, H=H, K=K.tolist(), dist=D.tolist(), rvec=rv.ravel().tolist(), tvec=tv.ravel().tolist(),
                   C=C.tolist(), rail_h=float(h), rail_d=float(d),
                   tag_rms=float(np.sqrt((tr ** 2).mean()))), open(out, "w"), indent=1)
    print("wrote", out)


# ---------------------------------------------------------------------------------------------
# Additive helpers (live/stationcam.py): AprilTag corner geometry and a generic camera fit from
# 3D-2D point correspondences.  Nothing above depends on these.
TAG_SIZE = 6.5 * IN          # 2026 FRC tags (36h11): black square edge = the detected corners


def load_tag_poses(path):
    """{id: (centre xyz in m, yaw rad)} -- yaw = direction the tag faces (Z-Rotation)."""
    T = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        T[int(r["ID"])] = (np.array([float(r["X"]), float(r["Y"]), float(r["Z"])]) * IN,
                           np.radians(float(r["Z-Rotation"])))
    return T


def tag_corners(pose, size=TAG_SIZE):
    """4x3 field points of a vertical tag's corners in pupil_apriltags / AprilTag-3 order
    (as the tag is seen from the front: bottom-left, bottom-right, top-right, top-left)."""
    c, yaw = pose
    n = np.array([np.cos(yaw), np.sin(yaw), 0.0])
    rt = np.array([-n[1], n[0], 0.0]); up = np.array([0, 0, 1.0]); h = size / 2
    return np.array([c - rt * h - up * h, c + rt * h - up * h, c + rt * h + up * h, c - rt * h + up * h])


def pack_pinhole(cam):
    """camera dict (K, dist, rvec, tvec) -> parameter vector of fit_points."""
    K = np.array(cam["K"], float); D = np.array(cam["dist"], float)
    R, _ = cv2.Rodrigues(np.array(cam["rvec"], float)); C = -R.T @ np.array(cam["tvec"], float)
    return np.r_[K[0, 0], K[1, 1] / K[0, 0], K[0, 2], K[1, 2], D[0], D[1], np.ravel(cam["rvec"]), C]


def unpack_pinhole(p):
    """[f, aspect, cx, cy, k1, k2, rvec(3), C(3)] -> K, dist, rvec, tvec, R, C"""
    f, a, cx, cy, k1, k2 = p[:6]
    K = np.array([[f, 0, cx], [0, f * a, cy], [0, 0, 1.0]])
    rv = np.asarray(p[6:9], float); C = np.asarray(p[9:12], float)
    R, _ = cv2.Rodrigues(rv)
    return K, np.array([k1, k2, 0, 0, 0.0]), rv, -R @ C, R, C


def proj_pinhole(p, P):
    """Project field points with the fit_points parameter vector (NaN behind the camera)."""
    K, D, rv, tv, R, C = unpack_pinhole(p)
    P = np.asarray(P, float).reshape(-1, 3)
    q, _ = cv2.projectPoints(P.reshape(-1, 1, 3), rv, tv, K, D)
    q = q.reshape(-1, 2)
    q[(P - C) @ R[2] <= 0.1] = np.nan
    return q


def fit_points(W, H, groups, p0, sig_c=10.0, sig_aspect=0.05, sig_k2=0.002, prior_C=None, sig_C=None,
               free=None, loss="soft_l1", f_scale=2.0):
    """Fit [f, aspect, cx, cy, k1, k2, rvec, C] to point groups [(P Nx3, uv Nx2, sigma_px), ...].
    Soft priors: principal point near the image centre (sig_c px), square pixels, k2 ~ 0, and
    optionally the camera centre near prior_C (sig_C m).  `free` = indices to fit (default all).
    Returns (p, least_squares result)."""
    p0 = np.asarray(p0, float)
    free = np.arange(12) if free is None else np.asarray(free)

    def res(q):
        p = p0.copy(); p[free] = q
        r = []
        for P, uv, s in groups:
            if len(P) == 0: continue
            e = proj_pinhole(p, P) - np.asarray(uv, float)
            e[~np.isfinite(e)] = 100.0
            r.append((e / s).ravel())
        r.append([(p[2] - W / 2) / sig_c, (p[3] - H / 2) / sig_c, (p[1] - 1) / sig_aspect, p[5] / sig_k2])
        if prior_C is not None:
            r.append((p[9:12] - np.asarray(prior_C, float)) / sig_C)
        return np.concatenate(r)

    s = least_squares(res, p0[free], loss=loss, f_scale=f_scale, max_nfev=5000)
    p = p0.copy(); p[free] = s.x
    return p, s


def camera_dict(p, W, H, **extra):
    """fit_points vector -> camera JSON as live/camera.py reads it."""
    K, D, rv, tv, R, C = unpack_pinhole(p)
    d = dict(W=int(W), H=int(H), K=K.tolist(), dist=D.tolist(), rvec=rv.ravel().tolist(),
             tvec=tv.ravel().tolist(), C=C.tolist())
    d.update(extra)
    return d


# ---------------------------------------------------------------------------------------------
# Calibration wizard (server/routers/sources.py + web/sources.js).  Additive: nothing above uses it.
#   1. detect_tags_frames(grays)          AprilTags anywhere in a region (tiled, upscaled), median over frames
#   2. NAMED_POINTS                       field landmarks the user can click when tags are not enough
#   3. wizard_fit(W, H, tags, points)     focal sweep + PnP init -> staged least squares (pose, f, k1, c, k2);
#                                         robust=True leaves out a tag that doesn't line up; report() verdict
#      refine_with_lines(...)             + the white centre line found near its predicted position (keep_refit)
#   4. overlay_lines(p, W, H)             projected field outline / lines / 1 m grid / hubs / tags
#   5. wizard_camera(p, W, H, crop, ...)  camera JSON (full-frame pixels for the overhead camera; inset
#                                         pixels + "crop" for station cameras, as stationcam expects)
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
TAG_CSV = _os.path.join(_HERE, "field", "2026-rebuilt-welded.csv")
RULES = _os.path.join(_HERE, "field", "rebuilt_rules.json")
_WTAGDET = None


def _unpack_w(p):
    """wizard vector [f, aspect, cx, cy, k1, k2, rvec(3), C(3), (k3)] -> K, dist, rvec, tvec, R, C"""
    K, D, rv, tv, R, C = unpack_pinhole(p[:12])
    if len(p) > 12: D = np.array([D[0], D[1], 0, 0, p[12]])
    return K, D, rv, tv, R, C


def wproj(p, P):
    """Project field points with a wizard vector (NaN behind the camera)."""
    if len(p) <= 12: return proj_pinhole(p, P)
    K, D, rv, tv, R, C = _unpack_w(p)
    P = np.asarray(P, float).reshape(-1, 3)
    q, _ = cv2.projectPoints(P.reshape(-1, 1, 3), rv, tv, K, D)
    q = q.reshape(-1, 2)
    q[(P - C) @ R[2] <= 0.1] = np.nan
    return q


def _wtagdet():
    # one detector per process: pupil_apriltags' destructor intermittently segfaults (see process.py)
    global _WTAGDET
    if _WTAGDET is None:
        from pupil_apriltags import Detector
        _WTAGDET = Detector(families="tag36h11", quad_decimate=1.0, decode_sharpening=0.25)
    return _WTAGDET


def detect_tags_gray(gray, scale=None, tile=480, overlap=96, min_margin=15, valid=None):
    """{id: (4x2 corners, decision margin)} in `gray` px.  Small regions (station insets) are upscaled
    whole; big frames in overlapping tiles (tags on a 1080p wide shot are ~8-20 px)."""
    H, W = gray.shape[:2]
    if scale is None:
        scale = 4.0 if W * H <= 700 * 420 else 3.0
    out = {}
    tiles = [(0, 0, W, H)] if W * H <= 700 * 420 else \
        [(x, y, min(W, x + tile), min(H, y + tile)) for y in range(0, max(1, H - overlap), tile - overlap)
         for x in range(0, max(1, W - overlap), tile - overlap)]
    det = _wtagdet()
    for x0, y0, x1, y1 in tiles:
        sub = gray[y0:y1, x0:x1]
        if sub.size == 0 or sub.std() < 8: continue
        up = cv2.resize(sub, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        for d in det.detect(up):
            if d.hamming != 0 or d.decision_margin < min_margin: continue
            tid = int(d.tag_id)
            if valid is not None and tid not in valid: continue
            c = d.corners / scale + [x0, y0]
            if tid not in out or d.decision_margin > out[tid][1]:
                out[tid] = (c, float(d.decision_margin))
    return out


MOVE_PX = 5.0      # a tag whose centre is this far from where it sat in most frames moved while the frames were read


def steady(C, need):
    """Per-frame corners C (n x 4 x 2) of one tag -> (frames to keep, moving).  Keeps the biggest group of frames
    that agree (centres within MOVE_PX of one frame's), so a tag on a part that moves (a raised bar, a door) counts
    only where it sat most of the time; moving=True when no position held at least half the frames (and need)."""
    ctr = C.mean(1)
    near = np.linalg.norm(ctr[:, None] - ctr[None], axis=2) <= MOVE_PX
    keep = near[int(near.sum(1).argmax())]
    return keep, bool(keep.sum() < need or keep.sum() < 0.5 * len(C))


def detect_tags_frames(grays, tag_csv=TAG_CSV, min_frac=0.34, per_frame=None, **kw):
    """Per-frame detection over several frames (a median plate blurs tiny tags) -> {id: {"corners": 4x2,
    "center": [u, v], "n": frames seen, "margin": median margin, "spread_px": corner scatter}} for tags
    seen in >= min_frac of the frames (and >= 2).  per_frame: a list that gets each frame's tag count (so the
    Video page can say which frames had none).  A tag that moved during the frames (steady()) keeps only the
    position it held most of the time ("moved_frames": how many it was elsewhere); with none, "moving": True and
    the fit leaves it out (_corr)."""
    valid = set(load_tag_poses(tag_csv))
    hits = {}
    for g in grays:
        got = detect_tags_gray(g, valid=valid, **kw)
        if per_frame is not None: per_frame.append(len(got))
        for tid, (c, m) in got.items():
            hits.setdefault(tid, []).append((c, m))
    need = max(2, int(np.ceil(min_frac * len(grays)))) if len(grays) > 1 else 1
    out = {}
    for tid, v in hits.items():
        if len(v) < need: continue
        C = np.array([c for c, _ in v])
        keep, moving = steady(C, need)
        med = np.median(C[keep], 0)
        out[tid] = dict(corners=med.tolist(), center=med.mean(0).tolist(), n=len(v),
                        margin=round(float(np.median([m for _, m in v])), 1),
                        spread_px=round(float(np.median(np.linalg.norm(C[keep] - med, axis=2))), 2))
        if moving: out[tid]["moving"] = True
        elif not keep.all(): out[tid]["moved_frames"] = int((~keep).sum())
    return out


def detect_tags_predicted(grays, p, W, H, tag_csv=TAG_CSV, have=(), win=40, scale=4.0, min_hits=2):
    """Second pass after a first fit: look for the remaining tags around their predicted pixels."""
    poses = load_tag_poses(tag_csv)
    det = _wtagdet(); hits = {}
    for tid, pose in poses.items():
        if tid in have: continue
        u, v = wproj(p, pose[0][None])[0]
        if not (np.isfinite(u) and win <= u < W - win and win <= v < H - win): continue
        # facing the camera? (tag normal vs view direction)
        K, D, rv, tv, R, C = _unpack_w(p)
        n = np.array([np.cos(pose[1]), np.sin(pose[1]), 0.0])
        if n @ (C - pose[0]) <= 0: continue
        x0, y0 = int(u) - win, int(v) - win
        for g in grays:
            roi = cv2.resize(g[y0:y0 + 2 * win, x0:x0 + 2 * win], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            for d in det.detect(roi):
                if d.tag_id == tid and d.hamming == 0 and d.decision_margin > 15:
                    hits.setdefault(tid, []).append(d.corners / scale + [x0, y0])
    out = {}
    for tid, v in hits.items():
        if len(v) < min(min_hits, len(grays)): continue
        med = np.median(np.array(v), 0)
        out[tid] = dict(corners=med.tolist(), center=med.mean(0).tolist(), n=len(v), margin=None,
                        spread_px=round(float(np.median(np.linalg.norm(np.array(v) - med, axis=2))), 2), predicted=True)
    return out


def _rules():
    import json as _json
    try:
        return _json.load(open(RULES))
    except Exception:
        return {}


def named_points():
    """Clickable field landmarks {name: {"xyz": [X, Y, Z] m, "label": str, "group": str}}.  Field frame:
    blue alliance wall at X = 0, red at X = 16.541; the BLUE outpost is near Y = 0, the RED outpost near
    Y = 8.069 (use the outposts / human-player chutes to tell the two long sides apart)."""
    L2, W = LEN / 2, WID
    P = {
        "corner_blue_bo": ((0, 0, 0), "Blue wall corner, blue-outpost side", "field"),
        "corner_blue_ro": ((0, W, 0), "Blue wall corner, red-outpost side", "field"),
        "corner_red_ro": ((LEN, W, 0), "Red wall corner, red-outpost side", "field"),
        "corner_red_bo": ((LEN, 0, 0), "Red wall corner, blue-outpost side", "field"),
        "centre_bo": ((L2, 0, 0), "Centre line end, blue-outpost side", "lines"),
        "centre_ro": ((L2, W, 0), "Centre line end, red-outpost side", "lines"),
        "centre": ((L2, W / 2, 0), "Field centre (centre line midpoint)", "lines"),
    }
    R = _rules()
    zx = (R.get("zones") or {})
    bx, rx = zx.get("blue_alliance_x_max", 4.028), zx.get("red_alliance_x_min", 12.513)
    P.update({
        "line_blue_bo": ((bx, 0, 0), "Blue alliance-zone line end, blue-outpost side", "lines"),
        "line_blue_ro": ((bx, W, 0), "Blue alliance-zone line end, red-outpost side", "lines"),
        "line_red_ro": ((rx, W, 0), "Red alliance-zone line end, red-outpost side", "lines"),
        "line_red_bo": ((rx, 0, 0), "Red alliance-zone line end, blue-outpost side", "lines"),
    })
    hub = (R.get("hub") or {})
    hc = hub.get("centers") or {"red": [11.915, 4.035], "blue": [4.626, 4.035]}
    hs = (hub.get("footprint_m") or [1.194, 1.194])[0] / 2
    for side in ("blue", "red"):
        cx, cy = hc[side]
        for sx, sy, nm in ((-1, -1, "blue-wall side, blue-outpost side"), (-1, 1, "blue-wall side, red-outpost side"),
                           (1, 1, "red-wall side, red-outpost side"), (1, -1, "red-wall side, blue-outpost side")):
            P[f"hub_{side}_{'b' if sx < 0 else 'r'}{'bo' if sy < 0 else 'ro'}"] = (
                (cx + sx * hs, cy + sy * hs, 0), f"{side.title()} hub base corner ({nm})", "hubs")
    out = {k: dict(xyz=[float(a) for a in v[0]], label=v[1], group=v[2]) for k, v in P.items()}
    for tid, (c, yaw) in load_tag_poses(TAG_CSV).items():
        out[f"tag_{tid}"] = dict(xyz=c.tolist(), label=f"AprilTag {tid} centre", group="tags")
    return out


def named_lines():
    """Straight field edges / tape lines the user can click ANY points along (no need to see the ends):
    {name: {"ends": [[X, Y, Z], [X, Y, Z]], "label", "group": "lines"}}."""
    R = _rules(); zx = R.get("zones") or {}
    bx, rx = zx.get("blue_alliance_x_max", 4.028), zx.get("red_alliance_x_min", 12.513)
    L = {"edge_ro": ((0, WID, 0), (LEN, WID, 0), "Carpet edge along the red-outpost side (long wall)"),
         "edge_bo": ((0, 0, 0), (LEN, 0, 0), "Carpet edge along the blue-outpost side (long wall)"),
         "edge_blue": ((0, 0, 0), (0, WID, 0), "Carpet edge at the blue alliance wall"),
         "edge_red": ((LEN, 0, 0), (LEN, WID, 0), "Carpet edge at the red alliance wall"),
         "centerline": ((LEN / 2, 0, 0), (LEN / 2, WID, 0), "Centre line (white tape)"),
         "zone_blue": ((bx, 0, 0), (bx, WID, 0), "Blue alliance-zone line"),
         "zone_red": ((rx, 0, 0), (rx, WID, 0), "Red alliance-zone line")}
    return {k: dict(ends=[list(map(float, a)), list(map(float, b))], label=lbl, group="lines") for k, (a, b, lbl) in L.items()}


def clicked_lines(line_clicks, sigma=2.0):
    """{line name: [[u, v], ...]} (>= 2 clicks each) -> wizard_fit `lines` [(world polyline, image pts, sigma)]."""
    NL = named_lines(); out = []
    for k, pts in (line_clicks or {}).items():
        pts = [p for p in (pts or []) if p and len(p) == 2]
        if k not in NL or len(pts) < 2: continue
        a, b = np.array(NL[k]["ends"], float)
        Pw = a + np.linspace(-0.03, 1.03, 400)[:, None] * (b - a)
        out.append((Pw, np.asarray(pts, float), sigma, k))
    return out


def _corr(tags, points, tag_csv=TAG_CSV, sig_corner=0.6, sig_centre=1.5, sig_click=3.0):
    """Correspondence groups [(P, uv, sigma)] + flat lists for reporting."""
    poses = load_tag_poses(tag_csv); NP = named_points()
    groups, rep = [], []
    for tid, t in (tags or {}).items():
        tid = int(tid)
        if tid not in poses or (isinstance(t, dict) and t.get("moving")): continue    # moved while we watched
        c = np.asarray(t["corners"] if isinstance(t, dict) and t.get("corners") is not None else
                       (t["center"] if isinstance(t, dict) else t), float)
        if c.shape == (4, 2):
            groups.append((tag_corners(poses[tid]), c, sig_corner)); rep.append(("tag", tid, poses[tid][0], c.mean(0)))
        else:
            c = c.reshape(-1)[:2]
            groups.append((poses[tid][0][None], c[None], sig_centre)); rep.append(("tag", tid, poses[tid][0], c))
    for name, uv in (points or {}).items():
        if name not in NP or uv is None: continue
        X = np.array(NP[name]["xyz"], float)
        groups.append((X[None], np.asarray(uv, float).reshape(1, 2), sig_click)); rep.append(("point", name, X, np.asarray(uv, float)))
    return groups, rep


def _line_pairs(lines):
    """For PnP initialisation only: nothing -- clicked line points have no fixed world position."""
    return []


def _init_pose(W, H, groups, lines=()):
    """Focal sweep: PnP (SQPNP) on all point correspondences for each focal guess; with clicked lines the
    8 best candidates get a short pose + focal refinement including the lines and the cheapest wins."""
    P = np.concatenate([g[0] for g in groups]); U = np.concatenate([g[1] for g in groups])
    if len(P) < 4: return None
    cands = []
    for f in np.geomspace(0.3 * W, 4.0 * W, 40):
        K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1.0]])
        try:
            ok, rv, tv = cv2.solvePnP(P.reshape(-1, 1, 3), U.reshape(-1, 1, 2), K, None, flags=cv2.SOLVEPNP_SQPNP)
        except cv2.error:
            continue
        if not ok: continue
        R, _ = cv2.Rodrigues(rv); C = (-R.T @ tv).ravel()
        p = np.r_[f, 1.0, W / 2, H / 2, 0, 0, rv.ravel(), C, 0.0]
        e = wproj(p, P) - U
        if not np.isfinite(e).all(): continue
        cands.append((float(np.sqrt((e ** 2).sum(1).mean())), p))
    if not cands: return None
    cands.sort(key=lambda c: c[0])
    if not lines: return cands[0][1]
    from scipy.optimize import least_squares
    best = None
    for _, p0 in cands[:8]:
        free = np.r_[0, 6:12]

        def res(q):
            p = p0.copy(); p[free] = q
            r = []
            for Pg, uv, sg in groups:
                e = wproj(p, Pg) - uv; e[~np.isfinite(e)] = 100.0; r.append((e / sg).ravel())
            for ln in lines:
                poly = wproj(p, ln[0]); poly = poly[np.isfinite(poly).all(1)]
                r.append(seg_dist(ln[1], poly) / ln[2] if len(poly) >= 2 else np.full(len(ln[1]), 100.0))
            return np.concatenate(r)
        sol = least_squares(res, p0[free], loss="soft_l1", f_scale=2.0, max_nfev=200)
        p = p0.copy(); p[free] = sol.x
        if best is None or sol.cost < best[0]: best = (sol.cost, p)
    return best[1]


def _fit_lines(p0, free, groups, lines, W, H, sig_c, sig_k, nfev=4000):
    """fit_points + straight world lines: lines = [(world polyline Nx3, image points Mx2, sigma)]."""
    from scipy.optimize import least_squares

    def res(q):
        p = p0.copy(); p[free] = q
        r = []
        for P, uv, s in groups:
            e = wproj(p, P) - uv; e[~np.isfinite(e)] = 100.0
            r.append((e / s).ravel())
        for ln in lines:
            Pw, pts, s = ln[:3]
            poly = wproj(p, Pw); poly = poly[np.isfinite(poly).all(1)]
            if not len(pts): continue
            # (a constant residual count: a line that left the view costs 100 px a point instead of vanishing)
            r.append(seg_dist(pts, poly) / s if len(poly) >= 2 else np.full(len(pts), 100.0 / s))
        r.append([(p[2] - W / 2) / sig_c, (p[3] - H / 2) / sig_c, (p[1] - 1) / 0.02, p[4] / sig_k, p[5] / sig_k,
                  p[12] / sig_k])
        return np.concatenate(r)
    s = least_squares(res, p0[free], loss="soft_l1", f_scale=2.0, max_nfev=nfev)
    p = p0.copy(); p[free] = s.x
    return p


# ---- robust tag fit + verdict.  One tag that moved, isn't mounted yet or was read wrong bends the whole fit
# (2026wamil: AprilTag 1 sat ~160 px above where the field drawings put it -> tags 6.5 px RMS, the principal point
# below the frame, k1 -0.74, saved as "fitted"; without it 0.5 px and a plausible camera).  Cameras get bumped, so
# the automatic path has to survive one bad tag, and say plainly when a fit can't be trusted.
DROP_ABOVE_PX = 2.0      # tag error (RMS of the tag centres) that starts the leave-out search (>= 4 tags; pairs >= 6)
DROP_TO_PX = 1.5         # a drop must bring the tag error under this ...
DROP_GAIN = 3.0          # ... and cut it at least this many times
LENS_TAGS = 8            # tags (or clicked lines, or LENS_POINTS clicked points) that let the fit move the principal
LENS_POINTS = 6          # point and k2 / k3: a few tags on two hub faces don't pin them, the automatic centre line neither
CAM_Z = (2.0, 25.0)      # plausible height of a wide / overhead camera, m (station cams: min_z 0.8)
PP_FRAC = 0.15           # the principal point within this share of the picture's width / height of its centre
TAG_GOOD_PX, TAG_BAD_PX = 1.5, 3.0
VERDICT_RANK = {"good": 0, "check": 1, "bad": 2}


def _is_auto(li):
    """A line refine_with_lines found in the picture (not clicked by hand)."""
    return len(li) > 3 and "(auto)" in str(li[3])


def _faces(ids, poses):
    """Distinct flat faces (facing direction, plane offset) the tags sit on: tags on one face don't fix the distance."""
    return {(round(float(np.degrees(poses[i][1]))) % 360, round(float(poses[i][0] @ [np.cos(poses[i][1]), np.sin(poses[i][1]), 0]), 1))
            for i in ids if i in poses}


def _centre(t):
    """Tag centre in px of a detection ({"corners"} | {"center"} | [u, v])."""
    c = np.asarray(t["corners"] if isinstance(t, dict) and t.get("corners") is not None else
                   (t["center"] if isinstance(t, dict) else t), float)
    return c.mean(0) if c.shape == (4, 2) else c.reshape(-1)[:2]


def _n(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def too_few(n_tag, n_pt=0):
    """The plain sentence for a fit with too little to go on."""
    if not n_tag and not n_pt:
        return ("No AprilTags were found and no field points are marked: a fit needs at least 2 AprilTags, or a few field "
                "lines or points clicked by hand.")
    have = _n(n_tag, "AprilTag") + (" and " + _n(n_pt, "clicked point") if n_pt else "")
    return (f"Not enough to fit the camera: {have}. It needs at least 2 AprilTags, or field lines or points clicked by "
            "hand, spread over the field.")


def wizard_fit(W, H, tags=None, points=None, lines=None, tag_csv=TAG_CSV, p_init=None, robust=False, dropped=(),
               min_z=CAM_Z[0], _nfev=4000):
    """Fit [f, aspect, cx, cy, k1, k2, rvec, C, k3] to tag corners + clicked points (+ line samples).
    tags {id: {"corners": 4x2} | [u, v]}; points {name: [u, v]}; lines [(world Nx3, image Mx2, sigma(, name))].
    Stages: pose + f -> + k1 (>= 8 points or lines) -> + principal point, k2 (>= 14 points or clicked lines) -> + k3
    (>= 30 points and clicked lines).  The lens stages need data that pins a lens (LENS_TAGS tags, clicked lines or
    LENS_POINTS clicked points), never the corner count alone nor the automatic centre line: without it the
    principal point stays at the picture centre and k2 = k3 = 0.
    robust: a tag error > DROP_ABOVE_PX with >= 4 tags -> refit leaving each tag out (pairs with >= 6) and drop the
    one that brings it under DROP_TO_PX and at least DROP_GAIN times down (report "dropped_tags").
    dropped: tags an earlier robust fit left out (reported again against this fit).  min_z: the lowest plausible
    camera height (verdict, see report()).
    Returns (p, report) or (None, {"error": plain sentence, "code": too_few | no_pose})."""
    groups, rep = _corr(tags, points, tag_csv)
    n = sum(len(g[0]) for g in groups)
    lines = lines or []
    nl = sum(len(l[1]) for l in lines if len(l) > 3)            # clicked line points (each ~ half a point)
    n_tag = sum(1 for r in rep if r[0] == "tag"); n_pt = len(rep) - n_tag
    if n + nl // 2 < 4 or (len(groups) < 2 and n + nl // 2 < 6):
        return None, dict(error=too_few(n_tag, n_pt), code="too_few", n_tags=n_tag, n_points=n_pt)
    p = np.array(p_init, float) if p_init is not None else _init_pose(W, H, groups, lines)
    if p is None:
        return None, dict(error="Couldn't find a camera position that fits these AprilTags and points together: one of them "
                                "is probably on the wrong spot or has the wrong name. Leave one out, or click field lines.",
                          code="no_pose", n_tags=n_tag, n_points=n_pt)
    if len(p) == 12: p = np.r_[p, 0.0]
    n += nl // 2
    clicked = [l for l in lines if not _is_auto(l)]
    lens = n_tag >= LENS_TAGS or bool(clicked) or n_pt >= LENS_POINTS
    if not lens: p[2], p[3], p[5], p[12] = W / 2, H / 2, 0.0, 0.0
    sig_c, sig_k = 0.03 * W, 0.3
    stages = [np.r_[0, 6:12]]
    if n >= 8 or lines: stages.append(np.r_[0, 4, 6:12])
    if lens and (n >= 14 or clicked): stages.append(np.r_[0, 2, 3, 4, 5, 6:12])
    if lens and n >= 30 and clicked: stages.append(np.r_[0, 2, 3, 4, 5, 6:12, 12])
    for free in stages:
        p = _fit_lines(p, free, groups, lines, W, H, sig_c, sig_k, _nfev)
    r = report(p, W, H, rep, lines, dropped=dropped, min_z=min_z, tag_csv=tag_csv)
    r["lens_free"] = bool(lens)
    if robust:
        p, r = _leave_out(W, H, tags, points, lines, tag_csv, p_init, p, r, dropped, min_z)
    return p, moving_note(r, tags)


def moving_note(r, tags):
    """Says which tags were left out because they moved while the frames were read (detect_tags_frames)."""
    mv = sorted(int(k) for k, t in (tags or {}).items() if isinstance(t, dict) and t.get("moving"))
    if mv and r is not None and not any(x.get("code") == "moving_tag" for x in r.get("verdict_reasons") or []):
        r["moving_tags"] = mv
        r.setdefault("verdict_reasons", []).append(dict(level="note", code="moving_tag", text=(
            f"AprilTag{'s' if len(mv) > 1 else ''} {', '.join(map(str, mv))} moved while the frames were read (on a "
            "part of the field that moves, e.g. a raised bar), so it was left out of the fit.")))
    return r


def _leave_out(W, H, tags, points, lines, tag_csv, p_init, p, rep, dropped, min_z):
    """wizard_fit(robust=True): the fit without the tag (or pair) that doesn't line up with the others, if one
    explains the error (fresh start each: the all-tags pose is bent by the bad tag) -> (p, report)."""
    e0 = rep.get("tag_rms_px")
    poses = load_tag_poses(tag_csv)
    tags = {int(k): v for k, v in (tags or {}).items()}
    ids = sorted(i for i in tags if i in poses)
    if e0 is None or e0 <= DROP_ABOVE_PX or len(ids) < 4:
        return p, rep
    err = {i["id"]: i["err_px"] or 0.0 for i in rep["items"] if i["kind"] == "tag" and not i.get("dropped")}
    clicked = bool(points) or any(not _is_auto(l) for l in lines or [])

    def without(drop):
        if len(_faces([i for i in ids if i not in drop], poses)) < 2 and not clicked:
            return None                                   # the rest on one flat face: any fit of them looks perfect
        sub = {k: v for k, v in tags.items() if k not in drop}
        info = [dict(id=i, uv=np.round(_centre(tags[i]), 2).tolist(), err_with_px=err.get(i), rms_with_px=e0) for i in drop]
        best = None
        for init in [None] + ([p_init] if p_init is not None else []):
            q, r = wizard_fit(W, H, sub, points, lines, tag_csv, p_init=init, dropped=list(dropped) + info, min_z=min_z,
                              _nfev=nfev)
            if q is not None and r.get("tag_rms_px") is not None and (best is None or r["tag_rms_px"] < best[1]["tag_rms_px"]):
                best = (q, r, init)
        return best

    def fixes(b):
        return b is not None and b[1]["tag_rms_px"] < DROP_TO_PX and b[1]["tag_rms_px"] * DROP_GAIN <= e0

    nfev = 300                                            # screening: a bad subset can take seconds to settle
    found = [(d, b) for d, b in (((i,), without((i,))) for i in ids) if fixes(b)]
    if not found and len(ids) >= 6:
        pool = ids if len(ids) <= 8 else sorted(ids, key=lambda i: -err.get(i, 0.0))[:6]
        found = [(d, b) for d, b in ((d, without(d)) for d in itertools.combinations(pool, 2)) if fixes(b)]
    if found:
        d, (q, r, init) = min(found, key=lambda x: x[1][1]["tag_rms_px"])
        nfev = 4000                                       # the winner once more with the full budget
        b = without(d)
        return (b[0], b[1]) if fixes(b) else (q, r)
    rep["leave_out"] = dict(tried=len(ids), found=False)
    for x in rep.get("verdict_reasons") or []:
        if x["code"] == "tag_error" and x["level"] == "bad":
            x["text"] += " Leaving out any one tag doesn't fix it, so more than one is off: click field lines by hand."
    return p, rep


def keep_refit(r0, r1):
    """Keep a refit (the automatic centre line) only when the tags stay good (<= TAG_GOOD_PX, or 1.5x) -- a doubtful
    tag fit must not get worse at all --, the clicked points too, and the verdict is not worse.  (The wizard used
    to check only the point error, empty in auto mode, so a refit that moved the grid 16 px was always kept.)"""
    if r1 is None or r1.get("error"): return False
    for k, good in (("tag_rms_px", TAG_GOOD_PX), ("point_rms_px", 4.0)):
        e0, e1 = r0.get(k), r1.get(k)
        if e0 is None: continue
        if e1 is None or e1 > (max(good, 1.5 * e0) if e0 <= good else 1.05 * e0): return False
    return VERDICT_RANK.get(r1.get("verdict"), 2) <= VERDICT_RANK.get(r0.get("verdict"), 2)


def report(p, W, H, rep=(), lines=(), dropped=(), min_z=CAM_Z[0], tag_csv=TAG_CSV):
    """Residuals of a fit + "verdict" (good | check | bad) and "verdict_reasons" [{level: ok | note | check | bad,
    code, text}] in plain words: tag / point / line error, tags on >= 2 faces, principal point near the picture
    centre, plausible camera height, a sane lens; "dropped_tags" [{id, uv, off_px (vs this fit), err_with_px}]."""
    K, D, rv, tv, R, C = _unpack_w(p)
    items = []
    for kind, ident, X, uv in rep:
        q = wproj(p, np.asarray(X)[None])[0]
        items.append(dict(kind=kind, id=ident, uv=np.round(uv, 2).tolist(),
                          pred=np.round(q, 2).tolist() if np.isfinite(q).all() else None,
                          err_px=round(float(np.linalg.norm(q - uv)), 2) if np.isfinite(q).all() else None))
    errs = [i["err_px"] for i in items if i["err_px"] is not None]
    tag_e = [i["err_px"] for i in items if i["kind"] == "tag" and i["err_px"] is not None]
    pt_e = [i["err_px"] for i in items if i["kind"] == "point" and i["err_px"] is not None]
    ln, by_line = [], {}
    for li in lines:
        Pw, pts, s = li[:3]
        poly = wproj(p, Pw); poly = poly[np.isfinite(poly).all(1)]
        if len(poly) >= 2 and len(pts):
            d = seg_dist(pts, poly); ln.append(float(np.median(d)))
            if len(li) > 3: by_line[li[3]] = round(float(np.median(d)), 2)
    poses = load_tag_poses(tag_csv)
    warn = []
    if C[2] < 0.5: warn.append("The camera comes out below 0.5 m: a clicked point is probably on the wrong spot.")
    if errs and np.sqrt(np.mean(np.square(errs))) > 4:
        warn.append("Some AprilTags or points don't line up with the fit (over 4 px): one is probably on the wrong spot, "
                    "has moved, or has the wrong name.")
    n_line = sum(len(li[1]) for li in lines if len(li) > 3 and not _is_auto(li))
    tids = [ident for kind, ident, X, uv in rep if kind == "tag"]
    nf = len(_faces(tids, poses))
    one_face = bool(tids) and len(tids) == len(rep) and not n_line and nf <= 1
    if one_face:
        warn.append("All the AprilTags found are on one flat face, so the camera's distance and lens can't be worked out: "
                    "click field lines or points elsewhere on the field.")
    if len(rep) + n_line / 2 < 6: warn.append("Few AprilTags or points to fit: check the grid over the whole field.")
    tag_rms = round(float(np.sqrt(np.mean(np.square(tag_e)))), 2) if tag_e else None
    pt_rms = round(float(np.sqrt(np.mean(np.square(pt_e)))), 2) if pt_e else None
    # ---- verdict
    why = []
    add = lambda level, code, text: why.append(dict(level=level, code=code, text=text))
    nt = len(tag_e)
    if tag_rms is not None:
        # with under 4 tags the fit has little left over to show an error: 2 px there is as bad as 3 px with more
        if tag_rms > (TAG_BAD_PX if nt >= 4 else TAG_GOOD_PX + 0.5):
            add("bad", "tag_error", f"The AprilTags don't line up with one camera: {tag_rms} px off on average (a good fit is "
                f"under {TAG_GOOD_PX:g} px). " + ("One has probably moved, isn't mounted yet, or was read wrong." if nt >= 4 else
                f"With only {_n(nt, 'tag')} Cyber-Scout can't tell which one is off: click field lines by hand."))
        elif tag_rms > TAG_GOOD_PX:
            add("check", "tag_error", f"The AprilTags line up within {tag_rms} px: usable, but check the grid on the far side "
                "of the field.")
        else:
            add("ok", "tag_error", f"The AprilTags line up within {tag_rms} px.")
    if pt_rms is not None:
        if pt_rms > 8:
            add("bad", "point_error", f"Your clicked points are {pt_rms} px from the fit on average: one is probably on the "
                "wrong spot or has the wrong name (the yellow strokes show where each one should be).")
        elif pt_rms > 4:
            add("check", "point_error", f"Your clicked points are {pt_rms} px from the fit on average: check the ones with the "
                "longest yellow strokes.")
    NL = named_lines()
    for k, v in by_line.items():
        if v <= 3:
            if "(auto)" in str(k): add("ok", "centre_line", f"The white centre line in the picture sits within {v} px of the fit.")
            continue
        if "(auto)" in str(k):
            add("check", "centre_line", f"The white centre line in the picture is {v} px from where the fit puts it: "
                "check the grid in the middle of the field.")
        else:
            add("check", "line_error", f"Your clicks along “{(NL.get(k) or {}).get('label', k)}” are {v} px from the fitted line.")
    if one_face:
        add("bad", "one_face", "All the AprilTags found are on one flat face, so the camera's distance and lens can't be "
            "worked out: click field lines or points elsewhere on the field.")
    elif nf >= 2:
        add("ok", "faces", f"Tags on {nf} different faces of the field.")
    if nt and nt < 4 and not pt_e and not n_line:
        add("check", "few_tags", f"Only {_n(nt, 'AprilTag')}: the grid is pinned only near them; check it over the whole field.")
    cx, cy = float(K[0, 2]), float(K[1, 2])
    if abs(cx - W / 2) > PP_FRAC * W or abs(cy - H / 2) > PP_FRAC * H:
        add("bad", "lens_centre", f"The fitted lens centre ({cx:.0f}, {cy:.0f}) is far from the picture centre ({W / 2:.0f}, "
            f"{H / 2:.0f}): no real camera is built like that, so the fit is bending to match a wrong tag or point.")
    z, z_hi = float(C[2]), CAM_Z[1]
    if not (min_z <= z <= z_hi):
        add("bad", "height", f"The camera comes out {z:.1f} m above the floor, but this camera sits {min_z:g}–{z_hi:g} m up: "
            "the fit is wrong.")
    else:
        add("ok", "height", f"Camera {z:.1f} m up, {float(np.hypot(C[0] - LEN / 2, C[1] - WID / 2)):.1f} m from the field centre.")
    f = float(K[0, 0]); k1, k2, k3 = float(D[0]), float(D[1]), float(D[4])
    rmax = max(np.hypot(x - cx, y - cy) for x in (0, W) for y in (0, H)) / max(f, 1e-6)
    r2 = np.linspace(0, rmax, 64) ** 2
    if (1 + 3 * k1 * r2 + 5 * k2 * r2 ** 2 + 7 * k3 * r2 ** 3 <= 0).any():
        add("bad", "lens", "The fitted lens folds the picture back on itself near the edges: no real lens does that, so the "
            "fit is wrong.")
    else:
        bend = abs(k1 * r2[-1] + k2 * r2[-1] ** 2 + k3 * r2[-1] ** 3)
        if bend > 0.4:
            add("check", "lens", f"The fitted lens bends the picture corners by {bend:.0%}: more than a broadcast camera "
                "usually does; check the grid near the edges.")
    if not (0.25 * W <= f <= 5 * W):
        add("check", "focal", f"The fitted zoom (focal length {f:.0f} px) is unusual for a {W} px wide picture: check the grid.")
    drop_out = []
    for d in dropped or ():
        i = int(d["id"])
        if i not in poses or i in tids: continue
        q = wproj(p, poses[i][0][None])[0]
        ok = bool(np.isfinite(q).all())
        off = round(float(np.linalg.norm(q - np.asarray(d["uv"], float))), 1) if ok else None
        drop_out.append(dict(d, off_px=off, pred=np.round(q, 2).tolist() if ok else None))
        items.append(dict(kind="tag", id=i, uv=d["uv"], pred=drop_out[-1]["pred"], err_px=off, dropped=True))
        add("note", "dropped", f"AprilTag {i} doesn't line up with the other tags ({off if off is not None else '?'} px from "
            "where they put it), so it may have moved or not be mounted yet. It was left out; check that part of the grid.")
    return dict(f=round(f, 1), c=[round(cx, 1), round(cy, 1)],
                k1=round(k1, 4), k2=round(k2, 5), k3=round(k3, 5), C=np.round(C, 3).tolist(),
                n_tags=len(tag_e), n_points=len(pt_e), tag_rms_px=tag_rms, point_rms_px=pt_rms,
                line_med_px=round(float(np.median(ln)), 2) if ln else None, lines=by_line,
                items=items, warnings=warn, verdict=_level(why), verdict_reasons=_ordered(why), dropped_tags=drop_out)


def _level(reasons):
    """good | check | bad: the worst reason."""
    lv = {x["level"] for x in reasons}
    return "bad" if "bad" in lv else "check" if "check" in lv else "good"


def _ordered(reasons):
    return sorted(reasons, key=lambda x: -{"bad": 3, "check": 2, "note": 1, "ok": 0}[x["level"]])


def line_check(rep, p, gray, name="centerline"):
    """How far the white centre line in the picture is from where fit p puts it (median px of the tape pixels found
    near it), for a fit the line was not used in -> rep["centre_line_px"] + a verdict reason ("check" over 3 px)."""
    pts = find_line_pixels(gray, p, name)
    poly = wproj(p, line_world(name, 400)); poly = poly[np.isfinite(poly).all(1)]
    if len(pts) < 10 or len(poly) < 2: return rep
    med = round(float(np.median(seg_dist(pts, poly))), 2)
    rep["centre_line_px"] = med
    why = [x for x in rep.get("verdict_reasons") or [] if x["code"] != "centre_line"]
    if med > 3:
        why.append(dict(level="check", code="centre_line", text=f"The white centre line in the picture is {med} px from "
                        "where the fit puts it: check the grid in the middle of the field."))
    else:
        why.append(dict(level="ok", code="centre_line", text=f"The white centre line in the picture sits within {med} px "
                        "of the fit."))
    rep["verdict_reasons"], rep["verdict"] = _ordered(why), _level(why)
    return rep


def line_world(name, n=120):
    """World polylines the wizard can snap to (white tape on the carpet)."""
    if name == "centerline":
        return np.c_[np.full(n, LEN / 2), np.linspace(0.05, WID - 0.05, n), np.zeros(n)]
    raise KeyError(name)


def find_line_pixels(gray, p, name="centerline", search=20, min_contrast=40):
    """Bright-tape pixels near the predicted world line, searched along the image normal (a robot-free
    median plate works best).  -> Mx2 image points."""
    Pw = line_world(name)
    q = wproj(p, Pw)
    H, W = gray.shape[:2]
    g = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    pts = []
    for i in range(1, len(q) - 1):
        if not (np.isfinite(q[i - 1:i + 2]).all()): continue
        t = q[i + 1] - q[i - 1]; t /= max(np.linalg.norm(t), 1e-6); nrm = np.array([-t[1], t[0]])
        s = np.arange(-search, search + 1)
        xy = q[i] + s[:, None] * nrm
        if (xy[:, 0] < 1).any() or (xy[:, 0] > W - 2).any() or (xy[:, 1] < 1).any() or (xy[:, 1] > H - 2).any(): continue
        v = cv2.remap(g, xy[:, 0].astype(np.float32).reshape(1, -1), xy[:, 1].astype(np.float32).reshape(1, -1),
                      cv2.INTER_LINEAR).ravel()
        k = int(v.argmax())
        if v[k] - np.median(v) < min_contrast or k in (0, len(v) - 1): continue
        w = np.clip(v - np.median(v), 0, None); w[np.abs(s - s[k]) > 3] = 0
        pts.append(q[i] + float((s * w).sum() / w.sum()) * nrm)
    return np.array(pts).reshape(-1, 2)


def refine_with_lines(p, gray, W, H, tags=None, points=None, tag_csv=TAG_CSV, names=("centerline",), sigma=1.0, extra=None,
                      **kw):
    """Re-fit with the white lines found near their predicted positions (two rounds); `extra` = clicked lines;
    kw (dropped, min_z) go to wizard_fit.  The automatic line never frees the lens (wizard_fit); keep the result
    only when keep_refit() says so."""
    rep = None
    for _ in range(2):
        lines = list(extra or [])
        for nm in names:
            pts = find_line_pixels(gray, p, nm)
            if len(pts) >= 10: lines.append((line_world(nm, 400), pts, sigma, nm + " (auto)"))
        if len(lines) == len(extra or []): break
        p2, rep2 = wizard_fit(W, H, tags, points, lines, tag_csv, p_init=p, **kw)
        if p2 is None: break
        p, rep = p2, rep2
    return p, rep


def overlay_lines(p, W, H, grid=1.0):
    """Projected field geometry for the accept/reject overlay: [{"kind", "name", "pts": [[u, v] | null...]}]
    (null breaks a polyline: behind the camera)."""
    R = _rules()
    out = []

    def add(kind, name, P, n=80):
        P = np.asarray(P, float)
        dense = np.concatenate([np.linspace(P[i], P[i + 1], n) for i in range(len(P) - 1)])
        q = wproj(p, dense)
        pts = [None if not np.isfinite(a).all() or abs(a[0]) > 4 * W or abs(a[1]) > 4 * H else [round(float(a[0]), 1), round(float(a[1]), 1)] for a in q]
        out.append(dict(kind=kind, name=name, pts=pts))
    if grid:
        for x in np.arange(grid, LEN, grid):
            add("grid", f"x={x:.0f}", [(x, 0, 0), (x, WID, 0)], 30)
        for y in np.arange(grid, WID, grid):
            add("grid", f"y={y:.0f}", [(0, y, 0), (LEN, y, 0)], 60)
    add("field", "boundary", [(0, 0, 0), (LEN, 0, 0), (LEN, WID, 0), (0, WID, 0), (0, 0, 0)])
    add("line", "centre line", [(LEN / 2, 0, 0), (LEN / 2, WID, 0)])
    zx = R.get("zones") or {}
    for x in (zx.get("blue_alliance_x_max", 4.028), zx.get("red_alliance_x_min", 12.513)):
        add("line", "alliance line", [(x, 0, 0), (x, WID, 0)])
    hub = R.get("hub") or {}
    hc = hub.get("centers") or {"red": [11.915, 4.035], "blue": [4.626, 4.035]}
    hs = (hub.get("footprint_m") or [1.194, 1.194])[0] / 2
    hz = hub.get("opening_height_m", 1.829)
    for side, (cx, cy) in hc.items():
        sq = [(cx - hs, cy - hs), (cx + hs, cy - hs), (cx + hs, cy + hs), (cx - hs, cy + hs), (cx - hs, cy - hs)]
        add("hub_" + side, f"{side} hub base", [(x, y, 0) for x, y in sq], 20)
        add("hub_" + side, f"{side} hub top", [(x, y, hz) for x, y in sq], 20)
        for x, y in sq[:4]:
            add("hub_" + side, f"{side} hub edge", [(x, y, 0), (x, y, hz)], 10)
    for tid, pose in load_tag_poses(TAG_CSV).items():
        c = tag_corners(pose)
        add("tag", str(tid), list(c) + [c[0]], 4)
    return out


def wizard_camera(p, W, H, crop=None, full_frame=False, frame_wh=None, **extra):
    """Camera JSON.  Station cameras: inset pixels + "crop" (stationcam convention).  The overhead
    camera (full_frame=True): converted to full-frame pixels (principal point shifted by the crop
    offset) because the tracker works on the whole frame."""
    d = camera_dict(p[:12], W, H, **extra)
    if len(p) > 12 and abs(p[12]) > 0: d["dist"] = [float(p[4]), float(p[5]), 0.0, 0.0, float(p[12])]
    if crop is not None and full_frame:
        x0, y0 = crop[:2]
        K = np.array(d["K"]); K[0, 2] += x0; K[1, 2] += y0
        d["K"] = K.tolist()
        if frame_wh: d["W"], d["H"] = int(frame_wh[0]), int(frame_wh[1])
        d["picture"] = list(crop)
    elif crop is not None:
        d["crop"] = [int(v) for v in crop]
    return d
