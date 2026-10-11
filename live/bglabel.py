"""Model-free robot proposals for a locked-off camera (used to make training labels).

foreground = differs from the match's median plate, is not ball-yellow, sits on the
carpet; blobs are kept when their size matches a robot footprint projected at that
field position (the calibration says how big a 0.91 m robot must look there).
"""
import numpy as np, cv2
from camera import Camera, LEN, WID, FOOTPRINT


def field_mask(cam, h=1.0, pad=0.3):
    """Pixels that can contain a robot: the carpet polygon swept up to height h."""
    m = np.zeros((cam.H, cam.W), np.uint8)
    X = np.r_[np.linspace(-pad, LEN + pad, 80), np.full(40, LEN + pad), np.linspace(LEN + pad, -pad, 80), np.full(40, -pad)]
    Y = np.r_[np.full(80, -pad), np.linspace(-pad, WID + pad, 40), np.full(80, WID + pad), np.linspace(WID + pad, -pad, 40)]
    for z in (0.0, h):
        p = cam.project(np.c_[X, Y, np.full(len(X), z)]).astype(np.int32)
        cv2.fillPoly(m, [p], 255)
    return m


def expected_box(cam, XY, h=0.55):
    """Pixel width/height of a FOOTPRINT robot of height h at field XY (yaw-averaged)."""
    s = FOOTPRINT / 2
    pts = []
    for dx, dy in ((-s, -s), (s, -s), (s, s), (-s, s)):
        for z in (0.0, h):
            pts.append((XY[0] + dx, XY[1] + dy, z))
    p = cam.project(np.array(pts))
    return p[:, 0].max() - p[:, 0].min(), p[:, 1].max() - p[:, 1].min()


def proposals(frame, plate, cam, fmask, diff_thr=38):
    d = cv2.absdiff(cv2.GaussianBlur(frame, (5, 5), 0), cv2.GaussianBlur(plate, (5, 5), 0)).max(2)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (18, 90, 110), (38, 255, 255))
    fg = ((d > diff_thr) & (fmask > 0)).astype(np.uint8) * 255
    fg_ny = cv2.bitwise_and(fg, cv2.bitwise_not(yellow))
    fg_ny = cv2.morphologyEx(fg_ny, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # robots full of balls: let yellow pixels inside a non-yellow blob's hull back in
    k = cv2.morphologyEx(fg_ny, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(k, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 250: continue
        blob = (lab[y:y + h, x:x + w] == i)
        solid = fg_ny[y:y + h, x:x + w][blob].mean() / 255
        G = cam.img_to_ground(np.array([[x + w / 2, y + h]], float))[0]
        if not np.isfinite(G).all(): continue
        ew, eh = expected_box(cam, G + 0.4 * cam._away(G[None])[0])
        r = w / max(ew, 1)
        if 0.55 < r < 1.7 and 0.4 < h / max(eh, 1) < 2.2 and solid > 0.35:
            out.append((x, y, x + w, y + h, round(float(r), 2), round(float(solid), 2)))
    return out


def colour_masks(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (0, 110, 70), (8, 255, 255)) | cv2.inRange(hsv, (170, 110, 70), (180, 255, 255))
    blue = cv2.inRange(hsv, (100, 130, 60), (125, 255, 255))
    return red, blue


IN = 0.0254
HUBS = ((182.111 * IN, 158.844 * IN), (469.111 * IN, 158.844 * IN))   # 2026 hub centres (tag layout)


def hub_tops(cam, z0=0.9, z1=2.1, half=0.75):
    """Image hulls of the upper part of each hub -- their lights change colour."""
    hulls = []
    for X, Y in HUBS:
        P = [(X + dx, Y + dy, z) for dx in (-half, half) for dy in (-half, half) for z in (z0, z1)]
        hulls.append(cv2.convexHull(cam.project(np.array(P)).astype(np.float32)))
    return hulls


def bumper_proposals(frame, plate, cam, fmask):
    """Robots from their bumpers: red/blue pixels that are not red/blue in the plate."""
    hubs = hub_tops(cam)
    fr, fb = colour_masks(frame); pr, pb = colour_masks(plate)
    k5 = np.ones((5, 5), np.uint8)
    out = []
    for al, m, pm in ((1, fr, pr), (2, fb, pb)):
        new = cv2.bitwise_and(m, cv2.bitwise_not(cv2.dilate(pm, k5)))
        new = cv2.bitwise_and(new, fmask)
        new = cv2.morphologyEx(new, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        new = cv2.morphologyEx(new, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n, lab, st, cen = cv2.connectedComponentsWithStats(new, 8)
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if a < 60: continue
            G = cam.img_to_ground(np.array([[x + w / 2, y + h]], float))[0]
            if not np.isfinite(G).all(): continue
            ew, eh = expected_box(cam, G + 0.4 * cam._away(G[None])[0], h=0.15)
            r = w / max(ew, 1)
            c = (float(x + w / 2), float(y + h / 2))
            if any(cv2.pointPolygonTest(hh, c, False) >= 0 for hh in hubs): continue
            if 0.5 < r < 1.6:
                out.append((x, y, x + w, y + h, al, round(float(r), 2)))
    return out
