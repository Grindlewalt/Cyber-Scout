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
import json, sys, csv, numpy as np, cv2
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
