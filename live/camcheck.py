"""Per-match camera check for the fixed broadcast camera (process.check_pose).

A camera that gets bumped between matches is mostly re-aimed (pan / tilt of a few degrees), sometimes zoomed a
little; it rarely moves.  The check compares the field's AprilTags in ~28 frames with where the last verified pose
puts them, re-aims the camera when they are off, and says plainly what it found (`status`, `why`).

    check_frames(frames, cam, tag_csv, log) -> (cam, info)

1. **Windows** (the hot path, unchanged since 2026wasam): an 80x80 window around every predicted tag, x4;
   a tag counts when seen in >= 3 frames.  >= 3 tags with a median error <= OK_PX 2 px = `ok`, nothing else runs.
2. **Re-aim** (`fit_all`): rotation + camera centre (25 cm prior) fitted to the tags plus the white centre line
   (`resolve_pose`), leave-one-out on the tags (`loo_outliers`, >= 4 tags), and the focal length freed too when
   the fixed-lens fit stays > 1 px, drops a tag or has to move the camera > SHIFT_M (ZOOM_SIGMA 10 % prior,
   distortion kept).  Applied when the tag error at least halves (or ends <= 1 px) and the centre line lines up at
   least as well.  A first fit on >= 4 tags ending <= OK_PX without a big shift is final; otherwise the windows
   run again around the new prediction (more tags) and the fit repeats.
3. **Whole frame**, only when the windows find < 3 tags or a weak fit gets no new tags from them: the tiled
   detector (`calibrate.detect_tags_frames`) on FULL_FRAMES frames, tags matched by id, a robust rotation start
   (`robust_rotation`), then step 2.

status: ok | re_aimed | unverified (too few tags / frames to check) | drift_unfixed (the tags say the camera
moved and no trusted fit brought it back within OK_PX).  The pose is changed only when `applied` (a partial
re-aim that at least halves the error is still applied under drift_unfixed: better than the old pose).
"""
import csv, math, os, time
from itertools import combinations

import numpy as np, cv2

import camera as CAMM

OK_PX = float(os.environ.get("POSE_OK_PX", "2.0"))     # median tag error at / below this = camera where we think
MIN_TAGS = 3
WIN = 40                                                # window half size (px) around each predicted tag
MIN_HITS = 3                                            # frames a tag must be seen in (windows)
FULL_FRAMES = int(os.environ.get("POSE_FULL_FRAMES", "8"))   # frames for the whole-frame pass
LOO_PX = 4.0                                            # leave-one-out: dropped if the others put it further off
POS_SIGMA = 0.25                                        # m, camera centre prior (a bump re-aims, it doesn't move)
SHIFT_M = 0.25                                          # a fit moving the camera further is trusted only on >= 4 tags
                                                        # (3 tags: a shift can fake a zoom; 2026wasam re-aims <= 0.14 m)
ZOOM_SIGMA = float(os.environ.get("POSE_ZOOM_SIGMA", "0.10"))   # focal-length prior (log scale); 0.05 pulled
                                                        # 8 % zooms to 7 % on 3 tags
ZOOM_FIT = os.environ.get("POSE_ZOOM", "1") != "0"
THREADS = int(os.environ.get("POSE_TAG_THREADS", "4"))   # detector threads (same detections, ~2x faster whole frame)
_TAGDET = None
IN = 0.0254


def load_tags(tag_csv):
    """{id: field xyz (m)} of the tag centres (WPILib layout csv, inches)."""
    with open(tag_csv, encoding="utf-8-sig") as f:
        return {int(r["ID"]): np.array([float(r["X"]), float(r["Y"]), float(r["Z"])]) * IN for r in csv.DictReader(f)}


def tag_detector():
    """One detector for the process lifetime: pupil_apriltags' destructor intermittently segfaults
    (libapriltag, seen twice in 40 runs right after the pose check).  None if pupil_apriltags is missing."""
    global _TAGDET
    if _TAGDET is None:
        try:
            from pupil_apriltags import Detector as TagDet
        except ImportError:
            return None
        _TAGDET = TagDet(families="tag36h11", quad_decimate=1.0, decode_sharpening=0.25, nthreads=THREADS)
    return _TAGDET


# ---------------------------------------------------------------- tag finding
def window_tags(frames, cam, tags, det, win=WIN, min_hits=MIN_HITS):
    """Tags near their predicted pixels: per frame an (2 win)^2 window, x4, tag36h11 (hamming 0, margin > 15,
    the right id).  -> {id: median [u, v]} for tags seen in >= min_hits frames, and {id: [(frame, u, v)]}."""
    hits = {}
    pred = {tid: cam.project(P[None])[0] for tid, P in tags.items()}
    for k, g in enumerate(frames):
        for tid, (u, v) in pred.items():
            if not (win <= u < cam.W - win and win <= v < cam.H - win): continue
            x0, y0 = int(u) - win, int(v) - win
            roi = cv2.resize(g[y0:y0 + 2 * win, x0:x0 + 2 * win], None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            for d in det.detect(roi):
                if d.tag_id == tid and d.hamming == 0 and d.decision_margin > 15:
                    c = d.center / 4 + [x0, y0]
                    hits.setdefault(tid, []).append((k, float(c[0]), float(c[1])))
    found = {k: np.median(np.array(v)[:, 1:], 0) for k, v in hits.items() if len(v) >= min_hits}
    return found, hits


def full_frame_tags(frames, tag_csv, n=FULL_FRAMES, log=print):
    """Whole-frame tiled detection (calibrate.detect_tags_frames) on n evenly spread frames -> {id: [u, v]}
    (tags seen in >= 1/3 of them).  {} if the detector is unavailable."""
    if not len(frames): return {}
    sub = [frames[int(i)] for i in np.unique(np.linspace(0, len(frames) - 1, min(n, len(frames))).round())]
    try:
        import calibrate as CAL
        if getattr(CAL, "_WTAGDET", 0) is None and tag_detector() is not None:
            CAL._WTAGDET = tag_detector()       # share ours (same settings, threaded): one detector per process
        got = CAL.detect_tags_frames(sub, tag_csv=tag_csv, min_frac=0.34)
    except Exception as e:                       # never lose the match over the fallback
        log(f"  pose check: whole-frame tag pass failed ({e!r})")
        return {}
    return {int(k): np.asarray(v["center"], float) for k, v in got.items()}


def in_match_shift(hits, frames_n):
    """Median over tags of how far a tag's position moved between the first and second half of the frames
    (camera bumped DURING the match).  None with < 3 tags seen in both halves."""
    d = []
    for v in hits.values():
        a = np.array(v)
        h1, h2 = a[a[:, 0] < frames_n / 2, 1:], a[a[:, 0] >= frames_n / 2, 1:]
        if len(h1) >= 2 and len(h2) >= 2:
            d.append(float(np.linalg.norm(np.median(h1, 0) - np.median(h2, 0))))
    return round(float(np.median(d)), 2) if len(d) >= MIN_TAGS else None


# ---------------------------------------------------------------- geometry
def centreline_pixels(cam, plate, search=25):
    """White centre-line pixels near where `cam` predicts it, from a robot-free plate."""
    Y = np.linspace(0.5, CAMM.WID - 0.5, 70)
    pred = cam.project(np.c_[np.full(70, CAMM.LEN / 2), Y, np.zeros(70)])
    g = cv2.GaussianBlur(plate, (3, 3), 0).astype(float)
    pts = []
    for u, v in pred:
        if not (np.isfinite(u) and np.isfinite(v)): continue
        u, v = int(round(u)), int(round(v))
        if not (search <= u < g.shape[1] - search and 0 <= v < g.shape[0]): continue
        row = g[v, u - search:u + search + 1]
        k = int(row.argmax())
        if row[k] - np.median(row) < 50: continue          # no clear white line here (ball/robot)
        w = np.clip(row - np.median(row), 0, None); xs = np.arange(len(row))
        w[np.abs(xs - k) > 3] = 0
        pts.append([u - search + float((xs * w).sum() / w.sum()), float(v)])
    return np.array(pts).reshape(-1, 2)


def centreline_score(cam, plate):
    """Median pixel offset between the predicted and the actual white centre line
    (lower = the calibration lines up).  inf if the line isn't found."""
    pts = centreline_pixels(cam, plate)
    if len(pts) < 10: return float("inf")
    Y = np.linspace(-0.3, CAMM.WID + 0.3, 200)
    poly = cam.project(np.c_[np.full(200, CAMM.LEN / 2), Y, np.zeros(200)])
    return float(np.median(np.abs(np.interp(pts[:, 1], poly[:, 1], poly[:, 0]) - pts[:, 0])))


def _K(cam, ls):
    K = cam.K.copy(); K[0, 0] *= math.exp(ls); K[1, 1] *= math.exp(ls)
    return K


def _camera(cam, x, zoom):
    rv = np.asarray(x[:3], float); R, _ = cv2.Rodrigues(rv); tv = -R @ np.asarray(x[3:6], float)
    new = dict(cam.raw); new["rvec"] = np.ravel(rv).tolist(); new["tvec"] = np.ravel(tv).tolist()
    if zoom: new["K"] = _K(cam, x[6]).tolist()
    return CAMM.Camera(new)


def resolve_pose(cam, P, uv, plate=None, zoom=False, rounds=2, C0=None, ls0=0.0):
    """Camera was nudged: re-fit its pose from the tags plus the white centre line
    (tags alone may all sit on one side), position held near C0 (default `cam`'s) by a soft prior (a bump mostly
    re-aims, it doesn't relocate).  Lens fixed; zoom=True also fits the focal length (fx = fy scaled from `cam`'s,
    starting at exp(ls0), ZOOM_SIGMA prior; principal point and distortion kept).  Without a plate: tags only,
    one fit."""
    from scipy.optimize import least_squares
    C0 = cam.C.copy() if C0 is None else np.asarray(C0, float)
    line = np.zeros((0, 2))
    def proj(x, X):
        rv, C = x[:3], x[3:6]
        R, _ = cv2.Rodrigues(rv); tv = -R @ C
        q, _ = cv2.projectPoints(X.reshape(-1, 1, 3), rv, tv, _K(cam, x[6]) if zoom else cam.K, cam.D)
        return q.reshape(-1, 2)
    Yl = np.linspace(-0.3, CAMM.WID + 0.3, 200)
    LW = np.c_[np.full(200, CAMM.LEN / 2), Yl, np.zeros(200)]
    def res(x):
        r = [(proj(x, P) - uv).ravel()]
        if len(line):
            poly = proj(x, LW)
            d = np.abs(np.interp(line[:, 1], poly[:, 1], poly[:, 0]) - line[:, 0])   # line is near-vertical
            r.append(d * np.sqrt(2 * len(P) / max(len(line), 1)))                    # line ~ as much weight as the tags
        r.append((x[3:6] - C0) / POS_SIGMA)                                        # camera position prior, 25 cm
        if zoom: r.append([x[6] / ZOOM_SIGMA])                                     # focal change prior
        return np.concatenate(r)
    x = np.r_[cam.rvec, cam.C, [ls0] if zoom else []]
    if plate is None: rounds = 0
    for _ in range(rounds):     # fit, then re-extract the line near the improved guess
        x = least_squares(res, x, loss="soft_l1", f_scale=2.0).x
        line = centreline_pixels(_camera(cam, x, zoom), plate)
    x = least_squares(res, x, loss="soft_l1", f_scale=2.0).x
    return _camera(cam, x, zoom)


def rotation_from_tags(cam, P, uv):
    """Rotation-only re-aim in closed form (camera centre and lens fixed): the rotation taking the tags' field
    directions onto their pixel rays (Kabsch / Wahba) -> camera.Camera."""
    n = cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), cam.K, cam.D, R=None, P=None,
                            criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-9)).reshape(-1, 2)
    b = np.c_[n, np.ones(len(n))]; b /= np.linalg.norm(b, axis=1, keepdims=True)
    w = np.asarray(P, float) - cam.C; w /= np.linalg.norm(w, axis=1, keepdims=True)
    U, _, Vt = np.linalg.svd(b.T @ w)
    R = U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt
    rv, _ = cv2.Rodrigues(R)
    return _camera(cam, np.r_[rv.ravel(), cam.C], False)


def two_tag_start(cam, P, uv):
    """Rotation + zoom that put exactly two tags on their pixels (the angle between their rays fixes the focal
    length, 0.85-1.2x; then the closed-form rotation) -> (camera, zoom).  Only a prediction to search the windows
    again after a big bump: two tags can't verify anything."""
    w = np.asarray(P, float) - cam.C; w /= np.linalg.norm(w, axis=1, keepdims=True)
    tw = math.acos(float(np.clip(w[0] @ w[1], -1, 1)))
    crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-9)
    best = None
    for z in np.linspace(0.85, 1.2, 351):
        n = cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), _K(cam, math.log(z)), cam.D, R=None, P=None,
                                criteria=crit).reshape(-1, 2)
        b = np.c_[n, np.ones(2)]; b /= np.linalg.norm(b, axis=1, keepdims=True)
        d = abs(math.acos(float(np.clip(b[0] @ b[1], -1, 1))) - tw)
        if best is None or d < best[0]: best = (d, float(z))
    z = best[1]
    return rotation_from_tags(_camera(cam, np.r_[cam.rvec, cam.C, math.log(z)], True), P, uv), z


def robust_rotation(cam, P, uv):
    """Start for a big re-aim: the rotation from all tags or from the pair of tags that explains the most of the
    others (MSAC, threshold 4 % of the focal length -- loose enough for a few % zoom)."""
    T2 = (0.04 * cam.K[0, 0]) ** 2
    best, cost = cam, np.inf
    for s in [tuple(range(len(P)))] + list(combinations(range(len(P)), 2)):
        c = rotation_from_tags(cam, P[list(s)], uv[list(s)])
        e2 = ((c.project(P) - uv) ** 2).sum(1)
        k = float(np.minimum(np.nan_to_num(e2, nan=T2), T2).sum())
        if k < cost: best, cost = c, k
    return best


def errs(cam, P, uv):
    return np.linalg.norm(cam.project(P) - uv, axis=1)


def loo_outliers(cam, P, uv, min_keep=MIN_TAGS, **fit):
    """Leave-one-out over the tags (>= 4).  The tag the fit of all of them explains worst is dropped when it is
    off both with a vote (> max(LOO_PX, 4x the others' median) in the robust fit of all) and without one (the
    others, fitting each other <= OK_PX, put it > max(LOO_PX, 4x their own error) away).  A good tag alone on
    one side of the field fits with a vote, so it is kept even though the others extrapolate badly to it.
    Repeats while >= min_keep + 1 tags are left.  -> (kept indices, dropped indices)."""
    keep, out = list(range(len(P))), []
    while len(keep) > min_keep:
        c = resolve_pose(cam, P[keep], uv[keep], **fit)
        r = errs(c, P[keep], uv[keep])
        w = int(np.argmax(r)); i = keep[w]
        if not r[w] > max(LOO_PX, 4 * float(np.median(np.delete(r, w)))): break
        o = [j for j in keep if j != i]
        ci = resolve_pose(cam, P[o], uv[o], **fit)
        ei, ro = float(errs(ci, P[i:i + 1], uv[i:i + 1])[0]), float(np.median(errs(ci, P[o], uv[o])))
        if not (ei > max(LOO_PX, 4 * ro) and ro <= OK_PX): break
        keep.remove(i); out.append(i)
    return keep, out


def angle_deg(a, b):
    """Rotation between two cameras' orientations (degrees)."""
    return float(np.degrees(np.linalg.norm(cv2.Rodrigues(a.R @ b.R.T)[0])))


# ---------------------------------------------------------------- the check
def _tags_arrays(tags, pos):
    ids = sorted(pos)
    return ids, np.array([tags[i] for i in ids]).reshape(-1, 3), np.array([pos[i] for i in ids], float).reshape(-1, 2)


def _loo(cam, P, uv, **fit):
    return loo_outliers(cam, P, uv, **fit) if len(P) >= 4 else (list(range(len(P))), [])


def check_frames(frames, cam, tag_csv, log=print, det=None):
    """frames: grey frames of the calibrated camera (feed lost / other cameras already left out).  cam: the camera
    as last verified.  -> (camera to use, info) -- info keeps the old keys {checked, tags, moved_px,
    resolved_px?, line_old_px?, line_new_px?, applied?} and adds status, why, check_s and, when they ran:
    outliers (tag ids left out), full_frame {tags, s}, zoom (focal / the carried one), moved_deg, moved_m
    (camera centre shift of the re-aim), rounds, in_match_px, few_tags (re-aim rests on 3 tags + a big shift)."""
    t0 = time.time()
    det = det if det is not None else tag_detector()
    if det is None:
        return cam, describe({"checked": False, "reason": "no_detector"})
    if len(frames) < 5:
        return cam, describe({"checked": False, "reason": "few_frames", "frames": len(frames)})
    tags = load_tags(tag_csv)
    f0 = float(cam.K[0, 0])

    def done(c, inf, fit=None):
        if fit is not None:
            inf.pop("outliers", None)
            if fit["drop"]: inf["outliers"] = fit["drop"]
            if c is not cam:                            # applied re-aim
                z = float(fit["cam"].K[0, 0]) / f0
                if abs(z - 1) > 1e-4: inf["zoom"] = round(z, 4)
        inf["check_s"] = round(time.time() - t0, 2)
        if inf.get("moved_px") is not None and inf["moved_px"] > OK_PX and "moved_deg" not in inf:
            inf["moved_deg"] = round(math.degrees(math.atan(inf["moved_px"] / f0)), 2)
        log(f"  pose check: {describe(inf)}")
        return c, inf

    # 1. windows around the predicted tags (unchanged)
    found, hits = window_tags(frames, cam, tags, det)
    info = {"checked": True, "tags": len(found), "moved_px": None}
    if len(found) >= MIN_TAGS:
        ids, P, uv = _tags_arrays(tags, found)
        err = errs(cam, P, uv)
        info["moved_px"] = round(float(np.median(err)), 2)
        shift = in_match_shift(hits, len(frames))
        if shift is not None: info["in_match_px"] = shift
        if np.median(err) <= OK_PX:                     # camera where it was: nothing else runs
            return done(cam, info)
    plate = np.median(np.stack(frames), 0).astype(np.uint8)
    s_old = centreline_score(cam, plate)
    pos, src = dict(found), dict.fromkeys(found, "win")

    def full_pass():
        full = full_frame_tags(frames, tag_csv, log=log)
        info["full_frame"] = dict(tags=len(full), s=round(time.time() - t0, 2))
        new = {k: v for k, v in full.items() if k in tags and k not in pos}
        pos.update(new); src.update(dict.fromkeys(new, "full"))
        return bool(new)

    start, ls_start = cam, None
    if len(pos) < MIN_TAGS:                             # 2. too few tags in the windows: the whole frame
        full_pass()
        if len(pos) == 2:                               # 2 tags that moved: aim by them, look in the windows again
            ids, P, uv = _tags_arrays(tags, pos)
            if np.median(errs(cam, P, uv)) > OK_PX:
                pred, z = two_tag_start(cam, P, uv)
                f2, _h = window_tags(frames, pred, tags, det)
                new = {k: v for k, v in f2.items() if k in tags and (k not in pos or src[k] == "full")}
                pos.update(new); src.update(dict.fromkeys(new, "win"))
                info["two_tag_start"] = dict(zoom=round(z, 3), tags=len(pos))
                if abs(z - 1) > 0.01: ls_start = math.log(z)
        info["tags"] = len(pos)
        if len(pos) < MIN_TAGS:
            info["moved_px"] = None
            return done(cam, info)
        ids, P, uv = _tags_arrays(tags, pos)
        err = errs(cam, P, uv)
        info["moved_px"] = round(float(np.median(err)), 2)
        if np.median(err) <= OK_PX:                     # enough tags only in the whole frame: camera in place
            return done(cam, info)
        start = robust_rotation(cam, P, uv)
    # 3. re-aim (rotation + centre + the centre line; leave-one-out; zoom if needed), then the windows again
    #    around the new prediction for more tags, the whole frame once if that finds nothing and the fit is weak
    tried, fit, rnd = [], None, 0
    for rnd in range(5):
        ids, P, uv = _tags_arrays(tags, pos)
        fit = fit_all(start if fit is None else fit["cam_rot"], P, uv, ids, plate, cam.C,
                      ls0=ls_start if fit is None else fit["ls"])
        err = errs(cam, P, uv)
        fit.update(e_old=float(np.median(err[fit["keep"]])), s_new=centreline_score(fit["cam"], plate),
                   moved_m=float(np.linalg.norm(fit["cam"].C - cam.C)), n=len(ids),
                   moved_px=float(np.median(err)))
        fit["ok"] = accept(fit["e"], fit["e_old"], s_old, fit["s_new"])
        fit["trusted"] = len(fit["keep"]) >= 4 or fit["moved_m"] <= SHIFT_M
        tried.append(fit)
        good = fit["ok"] and fit["e"] <= OK_PX and fit["trusted"]
        if good and rnd == 0 and len(fit["keep"]) >= 4 and fit["moved_m"] <= SHIFT_M:
            break                                       # the usual bump: re-aimed on the window tags
        f2, _h = window_tags(frames, fit["cam"], tags, det)
        new = {k: v for k, v in f2.items() if k in tags and (k not in pos or src[k] == "full")}
        pos.update(new); src.update(dict.fromkeys(new, "win"))
        if not new and (good or "full_frame" in info or not full_pass()):
            break
    info["rounds"] = rnd + 1
    ok = [f for f in tried if f["ok"]]                  # applicable fits; prefer trusted <= OK_PX, then more tags
    fit = max(ok, key=lambda f: (f["trusted"] and f["e"] <= OK_PX, f["trusted"], f["n"], -f["e"])) if ok else tried[-1]
    info.update(tags=max(info["tags"], fit["n"]), moved_px=round(fit["moved_px"], 2), resolved_px=round(fit["e"], 2),
                line_old_px=round(s_old, 1), line_new_px=round(fit["s_new"], 1))
    if fit["e"] < 0.5 * fit["e_old"]:                   # the fit explains the move: its angle (else a px estimate)
        info["moved_deg"] = round(angle_deg(fit["cam"], cam), 2)
    if not fit["ok"]:
        return done(cam, info, fit)
    info.update(applied=True, moved_m=round(fit["moved_m"], 3))
    if not fit["trusted"]: info["few_tags"] = True
    return done(fit["cam"], info, fit)


def accept(e_new, e_old, s_old, s_new):
    """A re-solve is applied when the tag error at least halves (or ends <= 1 px) and the centre line lines up at
    least as well (<= max(before, 3 px)): tags alone may all sit on one side of the field."""
    return (e_new < 0.5 * e_old or (e_new <= 1.0 and e_new < e_old)) and s_new <= max(s_old, 3.0)


def fit_all(start, P, uv, ids, plate, C0, ls0=None):
    """Re-aim from `start` (carried lens): rotation + centre with leave-one-out on the tags; then -- when that
    ends > 1 px, left a tag out, moved the camera > SHIFT_M, or the camera was zoomed already (ls0) -- the same
    with the focal length free, kept if it explains more tags, clearly fits better, or needs half the shift (a
    zoom makes the outer tags look like outliers; on 3 tags a fixed-lens fit fakes it by moving the camera).
    -> {cam, cam_rot (the fixed-lens fit), e (median px of the kept tags), keep, drop (tag ids), ls (log zoom)}."""
    keep, drop = _loo(start, P, uv, C0=C0)
    c = resolve_pose(start, P[keep], uv[keep], plate, C0=C0)
    e = float(np.median(errs(c, P[keep], uv[keep])))
    out = dict(cam=c, cam_rot=c, e=e, keep=keep, drop=[ids[i] for i in drop], ls=None)
    shift = float(np.linalg.norm(c.C - C0))
    if ZOOM_FIT and len(P) >= MIN_TAGS and (e > 1.0 or drop or ls0 is not None or shift > SHIFT_M):
        z = dict(zoom=True, C0=C0, ls0=ls0 or 0.0)
        kz, dz = _loo(c, P, uv, **z)
        cz = resolve_pose(c, P[kz], uv[kz], plate, **z)
        ez = float(np.median(errs(cz, P[kz], uv[kz])))
        ls = math.log(cz.K[0, 0] / c.K[0, 0])
        sz = float(np.linalg.norm(cz.C - C0))
        better = (len(kz) > len(keep) and ez <= max(e, OK_PX)) or (len(kz) == len(keep) and ez < 0.6 * e) \
            or (ls0 is not None and len(kz) >= len(keep) and ez <= e) \
            or (shift > SHIFT_M and sz < 0.5 * shift and len(kz) >= len(keep) and ez <= max(e, OK_PX))
        if better and abs(ls) < 0.2:
            out.update(cam=cz, e=ez, keep=kz, drop=[ids[i] for i in dz], ls=ls)
    return out


# ---------------------------------------------------------------- plain words
def describe(info):
    """Sets status (ok | re_aimed | unverified | drift_unfixed) and why (plain words) on a check's info; returns it.
    Works on the `pose` dict of match files written before the status existed: describe(dict(match["pose"]))."""
    n = info.get("tags") or 0
    tail = "positions in this match may be off if the camera moved."
    if not info.get("checked"):
        why = {"no_detector": "The AprilTag detector isn't installed, so the camera position couldn't be checked: ",
               "few_frames": "The field camera was on screen for too few frames to check its position: ",
               "no_tags": "There's no field tag layout, so the camera position couldn't be checked: "}.get(
            info.get("reason"), "The camera position wasn't checked: ")
        info.update(status="unverified", why=why + tail)
        return info
    moved, res = info.get("moved_px"), info.get("resolved_px")
    if moved is None or n < MIN_TAGS:
        what = "No field tags were" if n == 0 else f"Only {n} field tag{' was' if n == 1 else 's were'}"
        info.update(status="unverified", why=f"{what} visible, so the camera position couldn't be checked: {tail}")
        return info
    deg = info.get("moved_deg")
    if deg is None:                                     # older match files: rough angle for a ~1000 px focal length
        deg = math.degrees(math.atan(moved / 1000.0))
    zoom = info.get("zoom")
    zs = f" and zoomed {'in' if zoom > 1 else 'out'} {abs(zoom - 1) * 100:.0f}%" \
        if zoom is not None and abs(zoom - 1) >= 0.005 else ""
    k = len(info.get("outliers") or ())
    used = f"{n - k} of {n} field tags" if k else f"{n} field tags"
    if moved <= OK_PX and not info.get("applied"):
        info.update(status="ok", why=f"Camera checked against {n} field tags: it hasn't moved ({moved:.1f} px).")
    elif info.get("applied") and info.get("few_tags"):
        info.update(status="drift_unfixed", why=f"The camera moved about {deg:.1f}° but only {n - k} field tags could "
                                                 "be used, too few to re-aim it reliably: run Auto-calibrate on this "
                                                 "match's video.")
    elif info.get("applied") and res is not None and res <= OK_PX:
        info.update(status="re_aimed", why=f"Camera moved about {moved:.0f} px ({deg:.1f}°){zs} since the last match; "
                                            f"re-aimed automatically on {used} ({res:.1f} px).")
    elif info.get("applied"):
        info.update(status="drift_unfixed", why=f"The camera moved about {deg:.1f}°{zs} and could only be re-aimed to "
                                                 f"within {res:.1f} px: run Auto-calibrate on this match's video.")
    else:
        info.update(status="drift_unfixed", why=f"The camera moved more than it could be re-aimed (about {deg:.1f}°): "
                                                 "run Auto-calibrate on this match's video.")
    return info
