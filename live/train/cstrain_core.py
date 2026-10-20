"""Pure helpers for live/train/cstrain.py: ids, perceptual hashes, near-duplicate filter, splits, weighted box fusion,
uncertainty, label priority, YOLO text, detection metrics.  numpy only (no torch / cv2 / av), so the unit tests
(tests/test_cstrain.py) run anywhere."""
import hashlib, re
import numpy as np

SAFE = re.compile(r"[^A-Za-z0-9_-]+")
# class names of public FRC datasets that mean "a whole robot" (from live/train/pool.py)
ROBOT_NAMES = {"robot", "robots", "red_alliance", "blue_alliance", "unknown_alliance", "blu", "red", "blue-robot",
               "red-robot", "red_bumper", "blue_bumper"}


def safe_id(s):
    return SAFE.sub("-", str(s)).strip("-") or "x"


def frame_id(event, match, t):
    """2026wasam, qm12, 43.5 -> 2026wasam_qm12_043500 (t in ms)."""
    return f"{safe_id(event)}_{safe_id(match)}_{int(round(float(t) * 1000)):06d}"


# ---------------------------------------------------------------- perceptual hash + near duplicates
def _dct(n):
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    return m


_D32 = _dct(32)


def phash(gray32):
    """64-bit DCT hash (16 hex) of a 32x32 grayscale image: the 8x8 lowest frequencies vs their median."""
    g = np.asarray(gray32, np.float64)
    D = _D32 if g.shape == (32, 32) else _dct(g.shape[0])
    low = (D @ g @ D.T)[:8, :8].ravel()
    bits = low > np.median(low[1:])                 # the DC term would dominate the median
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _bits(hexes):
    a = np.array([int(h, 16) for h in hexes], np.uint64)
    return np.unpackbits(a.view(np.uint8).reshape(-1, 8), axis=1)


class Dedupe:
    """A frame is a near duplicate when its hash is within `dist` bits of an already kept one."""

    def __init__(self, dist=6, hashes=()):
        self.dist = dist
        hs = list(hashes)
        self.buf = _bits(hs) if hs else np.zeros((0, 64), np.uint8)
        self.n = len(self.buf)

    def near(self, h):
        """Indices (in the order kept) of the kept hashes within `dist` bits of h."""
        if not self.n:
            return []
        return np.flatnonzero((self.buf[:self.n] != _bits([h])).sum(1) <= self.dist).tolist()

    def push(self, h):
        if self.n == len(self.buf):                     # grow by doubling (amortised O(1) per frame)
            self.buf = np.vstack([self.buf, np.zeros((max(64, self.n), 64), np.uint8)])
        self.buf[self.n] = _bits([h])[0]
        self.n += 1

    def add(self, h):
        if self.near(h):
            return False
        self.push(h)
        return True


def thumb_change(a, b):
    """Mean absolute difference of two grey thumbnails (0..255): how much moved between two frames."""
    return float(np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).mean())


# ---------------------------------------------------------------- splits
def split_of(event, match, val_frac=0.1, test_events=(), test_frac=0.0):
    """train / val / test for a whole match (every frame of one match lands in one split), deterministic."""
    if event in set(test_events or ()):
        return "test"
    h = int(hashlib.sha1(f"{event}/{match}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if h < test_frac:
        return "test"
    if h < test_frac + val_frac:
        return "val"
    return "train"


# ---------------------------------------------------------------- boxes
def iou(a, B):
    """IoU of box a (4,) against boxes B (N, 4), x0 y0 x1 y1."""
    B = np.asarray(B, float).reshape(-1, 4)
    a = np.asarray(a, float)
    ix = np.clip(np.minimum(a[2], B[:, 2]) - np.maximum(a[0], B[:, 0]), 0, None)
    iy = np.clip(np.minimum(a[3], B[:, 3]) - np.maximum(a[1], B[:, 1]), 0, None)
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (B[:, 2] - B[:, 0]) * (B[:, 3] - B[:, 1]) - inter
    return np.where(ua > 0, inter / np.maximum(ua, 1e-9), 0.0)


def wbf(sets, weights=None, iou_thr=0.55, skip=0.0):
    """Weighted boxes fusion (Solovyev et al.) of T prediction sets [(boxes (N,4), scores (N,), classes (N,)), ...].
    One set per teacher; a teacher's TTA is already inside its set.  -> [{c, b, p, n}]: b = score-weighted mean box,
    p = sum over teachers of (weight x that teacher's best score in the cluster) / sum of weights (a box only half the
    teachers see scores about half), n = how many teachers have a box in the cluster."""
    T = len(sets)
    w = [1.0] * T if weights is None else [float(x) for x in weights]
    items = []
    for t, (B, S, C) in enumerate(sets):
        for b, s, c in zip(np.asarray(B, float).reshape(-1, 4), np.ravel(S), np.ravel(C)):
            if s >= skip:
                items.append((int(c), float(s), t, b))
    out = []
    for cls in sorted({i[0] for i in items}):
        clusters = []                                   # [members [(s, t, b)], fused box]
        for _, s, t, b in sorted((i for i in items if i[0] == cls), key=lambda i: -i[1]):
            best, bi = -1, iou_thr
            for k, cl in enumerate(clusters):
                v = float(iou(cl[1], b[None])[0])
                if v > bi:
                    best, bi = k, v
            if best < 0:
                clusters.append([[(s, t, b)], b.copy()])
                continue
            cl = clusters[best]
            cl[0].append((s, t, b))
            sw = np.array([m[0] * w[m[1]] for m in cl[0]])
            cl[1] = (sw[:, None] * np.array([m[2] for m in cl[0]])).sum(0) / max(sw.sum(), 1e-9)
        for mem, box in clusters:
            best_t = {}
            for s, t, _ in mem:
                best_t[t] = max(best_t.get(t, 0.0), s)
            p = sum(w[t] * s for t, s in best_t.items()) / max(sum(w), 1e-9)
            out.append(dict(c=cls, b=[round(float(v), 1) for v in box], p=round(min(float(p), 1.0), 4),
                            n=len(best_t)))
    return out


def box_uncertainty(p, n=1, T=1):
    """0 = sure, 1 = coin flip: a mid confidence or teachers that disagree."""
    conf_u = 1.0 - abs(2.0 * float(p) - 1.0)
    agree_u = 1.0 - n / T if T > 1 else 0.0
    return max(conf_u, agree_u)


def frame_uncertainty(boxes, T=1):
    """The most uncertain box decides (one doubtful robot makes the frame worth a look); no boxes = 0."""
    return round(max((box_uncertainty(b["p"], b.get("n", T), T) for b in boxes), default=0.0), 4)


def inside(b, B, pad=4):
    """Centre of box b inside box B."""
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return B[0] - pad <= cx <= B[2] + pad and B[1] - pad <= cy <= B[3] + pad


# ---------------------------------------------------------------- label priority
def pick_labels(review=None, imported=None, auto=None, auto_min_conf=0.45, auto_mid=0.3, auto_max_u=1.0, strict=True):
    """-> (source, [(c, [x0, y0, x1, y1])]) or (None, None) to leave the frame out.
    review (ok / fixed) > import > auto.  Auto keeps boxes with p >= auto_min_conf, and leaves the whole frame out
    when a box sits just under that (auto_mid <= p < auto_min_conf: probably a robot, which training would learn as
    background), when an unreviewed bglabel proposal is in it, or when u > auto_max_u -- unless not strict (val /
    test: every frame stays).  bglabel proposals are never labels.
    A review marked skip / bad leaves the frame out whatever else it has; extra review fields are ignored."""
    if review is not None:
        st = review.get("status")
        if st in ("skip", "bad"):
            return None, None
        if st in ("ok", "fixed"):
            return "review", [(int(b["c"]), list(b["b"])) for b in review.get("boxes") or []]
    if imported is not None:
        return "import", [(int(b["c"]), list(b["b"])) for b in imported.get("boxes") or []]
    if auto is not None:
        bx = auto.get("boxes") or []
        if strict and (float(auto.get("u") or 0) > auto_max_u or any(
                b.get("src") == "bglabel" or auto_mid <= float(b.get("p", 1.0)) < auto_min_conf for b in bx)):
            return None, None
        return "auto", [(int(b["c"]), list(b["b"])) for b in bx
                        if b.get("src") != "bglabel" and float(b.get("p", 1.0)) >= auto_min_conf]
    return None, None


# ---------------------------------------------------------------- YOLO text
def to_yolo(boxes, W, H, min_px=4):
    rows = []
    for c, b in boxes:
        x0, x1 = (float(np.clip(v, 0, W)) for v in (b[0], b[2]))
        y0, y1 = (float(np.clip(v, 0, H)) for v in (b[1], b[3]))
        if x1 - x0 < min_px or y1 - y0 < min_px:
            continue
        rows.append(f"{int(c)} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} {(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}")
    return "".join(r + "\n" for r in rows)


def from_yolo(text, W, H, cmap=None):
    """YOLO rows (boxes, or polygons -> their bounding box) -> [(c, [x0, y0, x1, y1] px)].
    cmap {source class: project class}: classes not in it are dropped (None = keep as is)."""
    out = []
    for line in text.splitlines():
        v = line.split()
        if len(v) < 5:
            continue
        c = int(float(v[0]))
        if cmap is not None:
            c = cmap.get(c)
            if c is None:
                continue
        f = [float(x) for x in v[1:]]
        if len(f) == 4:
            cx, cy, w, h = f
            x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        elif len(f) >= 6 and len(f) % 2 == 0:
            xs, ys = f[0::2], f[1::2]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        else:
            continue
        out.append((c, [round(x0 * W, 1), round(y0 * H, 1), round(x1 * W, 1), round(y1 * H, 1)]))
    return out


def class_map(src_names, project_classes, extra=None):
    """Source dataset class index -> project class index, by name (case-insensitive; the public robot aliases map to
    'robot'); extra {source name: project index} wins."""
    pc = [c.lower() for c in project_classes]
    m = {}
    for i, nm in enumerate(src_names):
        n = str(nm).strip().lower()
        if extra and str(nm) in extra:
            m[i] = int(extra[str(nm)])
        elif n in pc:
            m[i] = pc.index(n)
        elif n in ROBOT_NAMES and "robot" in pc:
            m[i] = pc.index("robot")
    return m


# ---------------------------------------------------------------- metrics
IOU_THRS = np.round(np.arange(0.5, 0.951, 0.05), 2)


def match_frame(gt, det, thr):
    """Greedy COCO matching for one frame and one class: det sorted by score (desc) -> tp flags."""
    gt = np.asarray(gt, float).reshape(-1, 4)
    used = np.zeros(len(gt), bool)
    tp = np.zeros(len(det), bool)
    for k, b in enumerate(det):
        if not len(gt):
            break
        v = iou(b, gt)
        v[used] = -1
        j = int(v.argmax())
        if v[j] >= thr:
            used[j] = True
            tp[k] = True
    return tp


def average_precision(tp, scores, n_gt):
    """101-point interpolated AP (COCO) from per-detection tp flags and scores pooled over frames."""
    if n_gt == 0:
        return float("nan")
    if not len(tp):
        return 0.0
    o = np.argsort(-np.asarray(scores, float), kind="stable")
    t = np.asarray(tp, bool)[o]
    ctp, cfp = np.cumsum(t), np.cumsum(~t)
    rec = ctp / n_gt
    prec = ctp / np.maximum(ctp + cfp, 1e-9)
    prec = np.maximum.accumulate(prec[::-1])[::-1]
    r = np.linspace(0, 1, 101)
    idx = np.searchsorted(rec, r, side="left")
    return float(np.mean([prec[i] if i < len(prec) else 0.0 for i in idx]))


def evaluate(frames, nc=1, conf=0.30):
    """frames: [(gt [(c, box)], det [(c, box, score)])].  -> mAP50, mAP50-95, and P / R / F1 at `conf` (IoU 0.5),
    per class and overall (the means skip classes with no ground truth)."""
    per = {}
    for c in range(nc):
        n_gt = 0
        pooled = {thr: ([], []) for thr in IOU_THRS}
        tp_c = fp_c = 0
        for gt, det in frames:
            g = [b for k, b in gt if k == c]
            d = sorted([(s, b) for k, b, s in det if k == c], key=lambda x: -x[0])
            n_gt += len(g)
            boxes = [b for _, b in d]
            sc = [s for s, _ in d]
            for thr in IOU_THRS:
                pooled[thr][0].extend(match_frame(g, boxes, thr).tolist())
                pooled[thr][1].extend(sc)
            keep = [b for s, b in d if s >= conf]
            t = match_frame(g, keep, 0.5)
            tp_c += int(t.sum())
            fp_c += int((~t).sum())
        aps = [average_precision(pooled[thr][0], pooled[thr][1], n_gt) for thr in IOU_THRS]
        p = tp_c / (tp_c + fp_c) if tp_c + fp_c else 0.0
        r = tp_c / n_gt if n_gt else 0.0
        per[c] = dict(n_gt=n_gt, ap50=aps[0], ap=float(np.mean(aps)) if n_gt else float("nan"), p=p, r=r,
                      f1=2 * p * r / (p + r) if p + r else 0.0, tp=tp_c, fp=fp_c, fn=n_gt - tp_c)
    have = [v for v in per.values() if v["n_gt"]]
    mean = lambda k: float(np.mean([v[k] for v in have])) if have else float("nan")
    return dict(map50=mean("ap50"), map=mean("ap"), p=mean("p"), r=mean("r"), f1=mean("f1"), conf=conf,
                frames=len(frames), n_gt=sum(v["n_gt"] for v in per.values()), per_class=per)
