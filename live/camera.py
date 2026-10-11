"""Full-frame camera model for a locked-off broadcast camera (live pipeline).

Same conventions as src/geom.py -- robots on the carpet (Z=0), the box bottom is
the bumper corner nearest the camera, pushed back by the yaw-averaged corner
distance -- but with a full OpenCV lens model, because event cameras like
Sammamish's are wide-angle with strong barrel distortion, and in FULL-FRAME
pixels (no band crop).

`install_geom_shim(cam)` registers this camera as the `geom` module so the
unmodified tracker modules in src/ (track, fit6, assign) can be imported.
"""
import json, sys, types
import numpy as np, cv2

LEN, WID = 16.541, 8.069          # 2026 REBUILT, metres
FOOTPRINT = 0.91                  # robot square incl. bumpers (36 in)

_t = np.linspace(0, np.pi / 2, 20001)
_s = (FOOTPRINT / 2) * (np.abs(np.cos(_t)) + np.abs(np.sin(_t)))
CORNER_MEAN, CORNER_STD = float(_s.mean()), float(_s.std())

_CRIT = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-6)


class Camera:
    def __init__(self, path_or_dict):
        c = path_or_dict if isinstance(path_or_dict, dict) else json.load(open(path_or_dict))
        self.raw = c
        self.W, self.H = int(c["W"]), int(c["H"])
        self.K = np.array(c["K"], float)
        self.D = np.array(c["dist"], float)
        self.rvec = np.array(c["rvec"], float).reshape(3)
        self.tvec = np.array(c["tvec"], float).reshape(3)
        self.R, _ = cv2.Rodrigues(self.rvec)
        self.C = (-self.R.T @ self.tvec).ravel()

    # ---- projection -------------------------------------------------------
    def project(self, P):
        P = np.asarray(P, float)
        if P.shape[-1] == 2:
            P = np.concatenate([P, np.zeros(P.shape[:-1] + (1,))], -1)
        q, _ = cv2.projectPoints(P.reshape(-1, 1, 3), self.rvec, self.tvec, self.K, self.D)
        return q.reshape(P.shape[:-1] + (2,))

    field_to_img = project

    def img_to_plane(self, uv, Z=0.0):
        uv = np.asarray(uv, float).reshape(-1, 1, 2)
        if len(uv) == 0:
            return np.zeros((0, 2))
        n = cv2.undistortPoints(uv, self.K, self.D, R=None, P=None, criteria=_CRIT).reshape(-1, 2)
        d = np.concatenate([n, np.ones((len(n), 1))], 1) @ self.R
        with np.errstate(divide="ignore", invalid="ignore"):
            s = (Z - self.C[2]) / d[:, 2]
        G = self.C[None, :] + s[:, None] * d
        G[(s <= 0) | ~np.isfinite(s)] = np.nan
        return G[:, :2]

    def img_to_ground(self, uv):
        return self.img_to_plane(uv, 0.0)

    def _away(self, XY):
        v = np.atleast_2d(np.asarray(XY, float)) - self.C[:2]
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def point_to_field(self, uv, corner=CORNER_MEAN):
        G = self.img_to_ground(uv)
        return G + corner * self._away(G)

    def box_to_field(self, boxes, band_offset=0, corner=CORNER_MEAN):
        b = np.atleast_2d(np.asarray(boxes, float))
        if len(b) == 0:
            return np.zeros((0, 2))
        uv = np.c_[(b[:, 0] + b[:, 2]) / 2, b[:, 3] + band_offset]
        return self.point_to_field(uv, corner)

    # ---- uncertainty ------------------------------------------------------
    def jac(self, XY):
        P = np.atleast_2d(np.asarray(XY, float))
        J = np.empty((len(P), 2, 2)); h = 1e-3
        for j in range(2):
            e = np.zeros(2); e[j] = h
            J[:, :, j] = (self.project(P + e) - self.project(P - e)) / (2 * h)
        return J

    def cov(self, XY, su=3.0, sv=4.0, yaw=CORNER_STD):
        P = np.atleast_2d(np.asarray(XY, float))
        if len(P) == 0:
            return np.zeros((0, 2, 2))
        Ji = np.linalg.inv(self.jac(P))
        S = Ji @ np.diag([su ** 2, sv ** 2]) @ Ji.transpose(0, 2, 1)
        u = self._away(P)[:, :, None]
        return S + (yaw ** 2) * (u @ u.transpose(0, 2, 1))

    def heights(self, boxes, XY):
        """Robot height from the box top given its ground position (NaN if clipped)."""
        b = np.atleast_2d(boxes); v = b[:, 1]
        lo, hi = np.full(len(b), 0.05), np.full(len(b), 2.5)
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            pv = self.project(np.c_[XY, mid])[:, 1]
            up = pv > v
            lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
        h = 0.5 * (lo + hi)
        h[b[:, 1] <= 2] = np.nan
        return h

    def roi(self, margin_m=0.6, robot_h=1.4, pad_px=16):
        """Pixel rectangle (x0, y0, x1, y1) containing the field plus robot height."""
        g = []
        for X in np.linspace(-margin_m, LEN + margin_m, 60):
            for Y in (-margin_m, WID + margin_m):
                g += [(X, Y, 0), (X, Y, robot_h)]
        for Y in np.linspace(-margin_m, WID + margin_m, 30):
            for X in (-margin_m, LEN + margin_m):
                g += [(X, Y, 0), (X, Y, robot_h)]
        p = self.project(np.array(g))
        x0, y0 = np.floor(p.min(0)) - pad_px
        x1, y1 = np.ceil(p.max(0)) + pad_px
        return (int(max(0, x0)), int(max(0, y0)), int(min(self.W, x1)), int(min(self.H, y1)))


def field_nms(frames, XY, conf, sep=0.70):
    frames = np.asarray(frames).astype(int)
    XY = np.atleast_2d(np.asarray(XY, float))
    keep = np.zeros(len(XY), bool)
    for f in np.unique(frames):
        idx = np.where(frames == f)[0]
        idx = idx[np.argsort(-np.asarray(conf)[idx])]
        taken = []
        for i in idx:
            p = XY[i]
            if not np.isfinite(p).all():
                continue
            if all(np.linalg.norm(p - XY[j]) >= sep for j in taken):
                taken.append(i); keep[i] = True
    return keep


def in_field(XY, pad=0.5):
    P = np.atleast_2d(np.asarray(XY, float))
    return ((P[:, 0] > -pad) & (P[:, 0] < LEN + pad) &
            (P[:, 1] > -pad) & (P[:, 1] < WID + pad))


def install_geom_shim(cam):
    """Expose `cam` as module `geom` for the unmodified src/ tracker modules."""
    m = types.ModuleType("geom")
    m.LEN, m.WID, m.FOOTPRINT = LEN, WID, FOOTPRINT
    m.BAND0, m.BAND1 = 0, cam.H
    m.CORNER_MEAN, m.CORNER_STD = CORNER_MEAN, CORNER_STD
    m.CAM = cam.C
    for name in ("field_to_img", "img_to_plane", "img_to_ground", "point_to_field",
                 "box_to_field", "jac", "cov", "_away"):
        setattr(m, name, getattr(cam, name))
    m.field_nms, m.in_field = field_nms, in_field
    sys.modules["geom"] = m
    return m
