"""Bumper-number OCR: PP-OCRv3 text detection + recognition on ONNX Runtime.

Models (live/models/ocr/, from rapidocr_onnxruntime 1.2.3, Apache-2.0; see README there):
  ch_PP-OCRv3_det_infer.onnx   DB text detector
  ch_PP-OCRv3_rec_infer.onnx   CRNN/SVTR recogniser (CTC over ~6600 chars)

We never decode free text: each detected text box is scored by CTC against the
team numbers that can be on the field this match (6 TBA team keys), both upright
and rotated 180 degrees, so a read is (candidate index, log P, margin over the
runner-up).  Measured on 20 Sammamish quals: ~96% of reads with lp > -8 name the
robot actually in the crop.

GPU: CUDA on device $OCR_GPU (default: $GPU_DEVICE, the detector's GPU, default 0);
FORCE_CPU=1 or no CUDA provider -> CPU.  Crops are batched: detector inputs are
padded to a few fixed bucket sizes (no per-shape cuDNN re-tuning), recogniser
inputs are a fixed 48x320 batch.

    o = BumperOCR()
    reads = o.read_many(crops, ["2910", "1983", ...])   # per crop: [Read, ...]
"""
import os, sys, threading, numpy as np, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIRS = [os.environ.get("CYBERSCOUT_OCR_DIR", ""), os.path.join(HERE, "models", "ocr"),
              os.path.join(os.path.dirname(HERE), ".work", "identity", "models")]
UP = 3.0                 # crop upscale before text detection (numbers are ~10-20 px tall)
BUCKETS = (192, 256, 320, 384, 448, 512)   # detector input sizes (square, pad)
MIN_LP = -8.0            # CTC log-prob floor for a read to be reported
MAX_DET = int(os.environ.get("OCR_MAX_DET", "512"))      # px, longest side of a detector input
ANGLES = tuple(float(a) for a in os.environ.get("OCR_ANGLES", "0,45,-45").split(","))


class Read:
    __slots__ = ("cand", "lp", "margin", "uv")
    def __init__(self, cand, lp, margin, uv):
        self.cand, self.lp, self.margin, self.uv = cand, lp, margin, uv
    def __repr__(self):
        return f"Read({self.cand}, lp={self.lp:.2f}, m={self.margin:.1f}, uv=({self.uv[0]:.0f},{self.uv[1]:.0f}))"


def _find_models():
    for d in MODEL_DIRS:
        if d and os.path.exists(os.path.join(d, "ch_PP-OCRv3_rec_infer.onnx")):
            return d
    raise FileNotFoundError("OCR models not found (live/models/ocr or $CYBERSCOUT_OCR_DIR)")


class BumperOCR:
    def __init__(self, model_dir=None, device=None, threads=2):
        import onnxruntime as ort
        d = model_dir or _find_models()
        so = ort.SessionOptions(); so.log_severity_level = 3; so.intra_op_num_threads = threads
        if device is None:
            device = int(os.environ.get("OCR_GPU", os.environ.get("GPU_DEVICE", "0")))
        prov = ["CPUExecutionProvider"]
        if os.environ.get("FORCE_CPU") != "1" and "CUDAExecutionProvider" in ort.get_available_providers():
            prov = [("CUDAExecutionProvider", {"device_id": int(device), "cudnn_conv_algo_search": "HEURISTIC"}),
                    "CPUExecutionProvider"]
        self.det = ort.InferenceSession(os.path.join(d, "ch_PP-OCRv3_det_infer.onnx"), so, providers=prov)
        self.rec = ort.InferenceSession(os.path.join(d, "ch_PP-OCRv3_rec_infer.onnx"), so, providers=prov)
        self.device = self.det.get_providers()[0]
        self.det_in = self.det.get_inputs()[0].name
        self.rec_in = self.rec.get_inputs()[0].name
        chars = ["<b>"] + self.rec.get_modelmeta().custom_metadata_map["character"].splitlines() + [" "]
        self.cidx = {}
        for i, ch in enumerate(chars): self.cidx.setdefault(ch, i)
        self._lab = {}
        self.n_crops = self.n_boxes = 0

    # ------------------------------------------------------------ detection
    def _det_batch(self, ims, thr=0.25, box_thr=0.4, unclip=1.8):
        """Upscaled crops -> per crop list of minAreaRects (in that crop's pixels)."""
        out = [None] * len(ims)
        groups = {}
        for i, im in enumerate(ims):
            h, w = im.shape[:2]; m = max(h, w)
            b = next((s for s in BUCKETS if s >= m), None)
            if b is None:                      # too big: shrink into the largest bucket
                b = BUCKETS[-1]
            groups.setdefault(b, []).append(i)
        # one crop per run: a fixed (1, 3, b, b) shape per bucket.  Varying the batch dim
        # costs ~70 ms of CUDA re-planning per new shape; bs=1 is only ~30% slower/img.
        mean = np.array([0.485, 0.456, 0.406], np.float32) * 255; std = np.array([0.229, 0.224, 0.225], np.float32) * 255
        for b, idx in groups.items():
            for i in idx:
                X = np.zeros((1, 3, b, b), np.float32)
                im = ims[i]; h, w = im.shape[:2]; s = min(1.0, b / max(h, w))
                if s < 1.0: im = cv2.resize(im, (int(w * s), int(h * s)))
                hh, ww = im.shape[:2]
                X[0, :, :hh, :ww] = ((im.astype(np.float32) - mean) / std).transpose(2, 0, 1)
                p = self.det.run(None, {self.det_in: X})[0][0, 0, :hh, :ww]
                m = (p > thr).astype(np.uint8)
                cs, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                rects = []
                for c in cs:
                    if len(c) < 4: continue
                    x, y, cw, ch = cv2.boundingRect(c)
                    if max(cw, ch) * 1.0 / s < 20: continue           # too small to hold digits
                    mask = np.zeros((ch, cw), np.uint8)
                    cv2.drawContours(mask, [c - [x, y]], -1, 1, -1)
                    if cv2.mean(p[y:y + ch, x:x + cw], mask=mask)[0] < box_thr: continue
                    (cx, cy), (rw, rh), a = cv2.minAreaRect(c)
                    if min(rw, rh) < 2: continue
                    dd = rw * rh * unclip / (2 * (rw + rh))
                    rects.append(((cx / s, cy / s), ((rw + 2 * dd) / s, (rh + 2 * dd) / s), a))
                out[i] = rects
        return out

    @staticmethod
    def _warp(img, r):
        (cx, cy), (rw, rh), a = r
        if rw < rh: rw, rh, a = rh, rw, a + 90
        if rw < 4 or rh < 4: return None
        # rotate only a neighbourhood of the box (much cheaper than the full crop)
        R = int(np.hypot(rw, rh) / 2) + 4
        x0, y0 = int(max(0, cx - R)), int(max(0, cy - R))
        sub = img[y0:int(cy + R) + 1, x0:int(cx + R) + 1]
        if sub.size == 0: return None
        M = cv2.getRotationMatrix2D((cx - x0, cy - y0), a, 1.0)
        rot = cv2.warpAffine(sub, M, (sub.shape[1], sub.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        w = cv2.getRectSubPix(rot, (int(rw), int(rh)), (cx - x0, cy - y0))
        return w if w.size else None

    # ------------------------------------------------------------ recognition
    def _rec_batch(self, lines, bs=8):
        """text-line images -> list of (T, C) log-prob arrays (fixed batch shape, padded)."""
        out = []
        for s in range(0, len(lines), bs):
            chunk = lines[s:s + bs]
            X = np.zeros((bs, 3, 48, 320), np.float32); T = []
            for r, im in enumerate(chunk):
                h, w = im.shape[:2]
                nw = int(min(320, max(16, np.ceil(48 * w / max(h, 1)))))
                x = (cv2.resize(im, (nw, 48)).astype(np.float32) / 255. - 0.5) / 0.5
                X[r, :, :, :nw] = x.transpose(2, 0, 1); T.append(int(np.ceil(nw / 8)) + 1)
            P = self.rec.run(None, {self.rec_in: X})[0]
            for r in range(len(chunk)):
                out.append(np.log(np.clip(P[r, :min(T[r], P.shape[1])], 1e-9, 1)))
        return out

    def _labels(self, cands):
        key = tuple(cands)
        if key not in self._lab:
            labs = [[self.cidx[c] for c in s] for s in cands]
            S = max(2 * len(l) + 1 for l in labs)
            ext = np.zeros((len(labs), S), int); valid = np.zeros((len(labs), S), bool)
            skip = np.zeros((len(labs), S), bool); last = np.zeros(len(labs), int)
            for k, l in enumerate(labs):
                e = [0] + [x for c in l for x in (c, 0)]
                ext[k, :len(e)] = e; valid[k, :len(e)] = True; last[k] = len(e) - 1
                for j in range(2, len(e)):
                    skip[k, j] = e[j] != 0 and e[j] != e[j - 2]
            self._lab[key] = (ext, valid, skip, last)
        return self._lab[key]

    def ctc(self, lp, cands):
        """log P(candidate | per-step log probs) for every candidate at once."""
        ext, valid, skip, last = self._labels(cands)
        C, S = ext.shape
        NEG = -1e30
        a = np.full((C, S), NEG); a[:, 0] = lp[0, ext[:, 0]]; a[:, 1] = lp[0, ext[:, 1]]
        a[~valid] = NEG
        for t in range(1, len(lp)):
            a1 = np.concatenate([np.full((C, 1), NEG), a[:, :-1]], 1)
            a2 = np.where(skip, np.concatenate([np.full((C, 2), NEG), a[:, :-2]], 1), NEG)
            m = np.maximum(np.maximum(a, a1), a2)
            a = m + np.log(np.exp(a - m) + np.exp(a1 - m) + np.exp(a2 - m)) + lp[t, ext]
            a[~valid] = NEG
        r = np.arange(C)
        return np.logaddexp(a[r, last], a[r, last - 1])

    # ------------------------------------------------------------ API
    def read_many(self, crops, cands, offsets=None, up=UP, min_lp=MIN_LP, angles=None):
        """crops: BGR images (robot bumper regions); cands: candidate number strings.
        offsets: optional (x0, y0) of each crop in the frame -> Read.uv in frame pixels.
        angles: also detect text in the crop rotated by these degrees (bumper numbers on
        a robot seen at 45 deg are slanted and the text detector misses most of them;
        (0, 45, -45) doubles the readable crops at ~2.5x the cost).
        Returns per crop a list of Read (one per text box that matches a candidate)."""
        angles = ANGLES if angles is None else angles
        big, inv, own, ups = [], [], [], []
        for i, c in enumerate(crops):
            for ang in angles:
                im, M = c, None
                if ang:
                    h, w = c.shape[:2]; M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
                    cs, sn = abs(M[0, 0]), abs(M[0, 1]); nw, nh = int(h * sn + w * cs), int(h * cs + w * sn)
                    M[0, 2] += nw / 2 - w / 2; M[1, 2] += nh / 2 - h / 2
                    im = cv2.warpAffine(c, M, (nw, nh), borderMode=cv2.BORDER_REPLICATE)
                u = min(up, MAX_DET / max(im.shape[:2]))          # cap the detector input size
                big.append(cv2.resize(im, None, fx=u, fy=u, interpolation=cv2.INTER_CUBIC)); ups.append(u)
                inv.append(None if M is None else cv2.invertAffineTransform(M)); own.append(i)
        rects = self._det_batch(big)
        lines, owner = [], []
        for b, (im, rs) in enumerate(zip(big, rects)):
            for r in rs or []:
                if max(r[1]) < 30: continue
                w = self._warp(im, r)
                if w is None: continue
                lines += [w, cv2.rotate(w, cv2.ROTATE_180)]; owner.append((b, r))
        self.n_crops += len(crops); self.n_boxes += len(owner)
        out = [[] for _ in crops]
        if not owner: return out
        lps = self._rec_batch(lines)
        for k, (b, r) in enumerate(owner):
            s = np.maximum(self.ctc(lps[2 * k], cands), self.ctc(lps[2 * k + 1], cands))
            o = np.argsort(-s); j = int(o[0])
            if s[j] <= min_lp: continue
            m = float(s[j] - (s[o[1]] if len(o) > 1 else -30.0))
            cx, cy = r[0][0] / ups[b], r[0][1] / ups[b]
            if inv[b] is not None:
                cx, cy = inv[b] @ [cx, cy, 1.0]
            i = own[b]
            ox, oy = offsets[i] if offsets is not None else (0, 0)
            rd = Read(j, float(s[j]), m, (ox + cx, oy + cy))
            # the same text found at two rotations: keep the better read
            dup = [q for q, x in enumerate(out[i]) if np.hypot(x.uv[0] - rd.uv[0], x.uv[1] - rd.uv[1]) < 12]
            if dup:
                if out[i][dup[0]].lp < rd.lp: out[i][dup[0]] = rd
            else:
                out[i].append(rd)
        return out

    def read(self, crop, cands, **kw):
        return self.read_many([crop], cands, **kw)[0]


# ---------------------------------------------------------------------------- sampler
OCR_PERIOD = float(os.environ.get("OCR_PERIOD", "0.3"))   # s between attempts on one robot
# optional: re-read robots that were read in the last OCR_FRESH s less often.  Measured: it
# moves the budget to robots whose numbers are unreadable and LOWERS total reads (qm12:
# 514 -> 281), so it is off by default (= OCR_PERIOD).
OCR_PERIOD_READ = float(os.environ.get("OCR_PERIOD_READ", str(OCR_PERIOD)))
OCR_FRESH = float(os.environ.get("OCR_FRESH", "3.0"))
OCR_MAX_PER_FRAME = int(os.environ.get("OCR_MAX_PER_FRAME", "3"))
OCR_CONF = float(os.environ.get("OCR_CONF", "0.30"))        # detector confidence to bother
OCR_T1 = float(os.environ.get("OCR_T1", "190"))


def bumper_rect(cam, xy, W, H, half=0.6, z1=0.5, pad=4):
    """Pixel rect around a robot's bumpers (+-half m, z 0..z1) -- where its number is."""
    x, y = xy
    P = np.array([[x + dx, y + dy, z] for dx in (-half, half) for dy in (-half, half) for z in (0, z1)])
    p = cam.project(P)
    x0, y0 = np.floor(p.min(0)).astype(int) - pad; x1, y1 = np.ceil(p.max(0)).astype(int) + pad
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


def _attribute(ocr_res, jobs, rects, cen, row0):
    """Reads of a frame's crops -> rows [det_row, cand, lp, margin, ambiguous, u, v]: each read
    goes to the detection whose bumper region holds the text (nearest robot centre)."""
    rows = []
    for (j, _, _), rr in zip(jobs, ocr_res):
        for r in rr:
            u, v = r.uv
            inside = [k for k, (x0, y0, x1, y1) in rects.items() if x0 + 2 <= u <= x1 - 2 and y0 + 2 <= v <= y1 - 2]
            if j not in inside: inside.append(j)
            d = {k: float(np.hypot(*(cen[k] - (u, v)))) for k in inside}
            best = min(d, key=d.get)
            amb = len(d) > 1 and sorted(d.values())[1] < 1.5 * d[best] + 3
            rows.append([row0 + best, r.cand, round(r.lp, 3), round(r.margin, 2), int(amb), round(u, 1), round(v, 1)])
    return rows


def _worker_main(cands, device):
    """OCR worker process (python live/ocr.py --worker ...): pickled jobs on stdin,
    pickled ("reads", rows) / ("ready", device) / ("error", msg) / ("done", busy_s) on stdout.
    A separate interpreter, so OCR never contends with decoding/detection for the GIL."""
    import pickle, time
    fin, fout = sys.stdin.buffer, sys.stdout.buffer
    def send(x):
        pickle.dump(x, fout, protocol=4); fout.flush()
    try:
        o = BumperOCR(device=device)
        send(("ready", o.device))
    except Exception as e:
        send(("error", repr(e))); return
    busy = 0.0
    while True:
        try:
            item = pickle.load(fin)
        except EOFError:
            break
        if item is None: break
        t0 = time.time()
        try:
            row0, jobs, rects, cen = item
            res = o.read_many([c for _, c, _ in jobs], cands, offsets=[x for _, _, x in jobs])
            send(("reads", _attribute(res, jobs, rects, cen, row0)))
        except Exception as e:
            send(("error", repr(e)))
        busy += time.time() - t0
    send(("done", busy))


class OcrSampler:
    """Runs inside process.detect_video: every frame it picks the robot detections whose
    position hasn't been read for OCR_PERIOD seconds (most stale first, at most
    OCR_MAX_PER_FRAME), crops their bumpers and reads them in a worker process (OCR_PROC=0: thread).

    Each read is attributed to the detection whose bumper region holds the text box
    (a crop often contains a neighbour's bumper too); reads that fall in two robots'
    regions are kept but flagged ambiguous.

    result: self.reads, rows [det_row, cand_idx, lp, margin, ambiguous, u, v]
    (det_row = index into detect_video's raw detection rows), self.n_tried."""

    def __init__(self, cam, teams, log=print, ocr=None, use_process=None):
        import threading, queue
        self.cam, self.teams = cam, list(teams)
        self.cands = [t[3:] if t.startswith("frc") else t for t in self.teams]
        self.log = log
        self.recent = []                    # (t, x, y) of attempts
        self.good = []                      # (t, x, y) of successful reads (filled asynchronously)
        self.rowpos = {}                    # det row -> (t, x, y) for rows in submitted frames
        self.reads, self.n_tried, self.busy_s = [], 0, 0.0
        self.err = None; self.device = None; self.dead = False
        use_process = (os.environ.get("OCR_PROC", "1") == "1") if use_process is None else use_process
        self.proc = None
        if use_process and ocr is None:
            import subprocess, json as _json
            dev = int(os.environ.get("OCR_GPU", os.environ.get("GPU_DEVICE", "0")))
            nw = max(1, int(os.environ.get("OCR_WORKERS", "4")))
            self.procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker",
                                            _json.dumps(self.cands), str(dev)],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE) for _ in range(nw)]
            self.proc = self.procs[0]; self.rr = 0; self.busy = [0.0] * nw
            self.done = [threading.Event() for _ in range(nw)]
            self.ths = [threading.Thread(target=self._reader, args=(k,), daemon=True) for k in range(nw)]
            for th in self.ths: th.start()
        else:
            self.ocr = ocr or BumperOCR(); self.device = self.ocr.device
            self.q = queue.Queue(maxsize=64)
            self.th = threading.Thread(target=self._work, daemon=True); self.th.start()

    def frame(self, t, im, boxes, conf, row0):
        """boxes: (N,4) full-frame px of this frame's detections; their raw rows are row0.. ."""
        if self.dead or t > OCR_T1 or not len(boxes): return
        ok = conf >= OCR_CONF
        if not ok.any(): return
        xy = self.cam.box_to_field(boxes)
        self.recent = [r for r in self.recent if t - r[0] < 1.5]
        R = np.array(self.recent).reshape(-1, 3)
        self.good = [g for g in self.good if t - g[0] < OCR_FRESH]
        Gd = np.array(self.good).reshape(-1, 3)
        cand = []
        for j in np.where(ok & np.isfinite(xy).all(1))[0]:
            if len(R):
                near = np.hypot(R[:, 1] - xy[j, 0], R[:, 2] - xy[j, 1]) < 0.8
                stale = t - R[near, 0].max() if near.any() else 9.0
            else:
                stale = 9.0
            # robots read recently are re-read rarely: spend the budget on the unread ones
            fresh = OCR_PERIOD_READ > OCR_PERIOD and len(Gd) and \
                (np.hypot(Gd[:, 1] - xy[j, 0], Gd[:, 2] - xy[j, 1]) < 1.0).any()
            if stale >= (OCR_PERIOD_READ if fresh else OCR_PERIOD) - 1e-3:
                cand.append((-stale + (5.0 if fresh else 0.0), -conf[j], j))
        if not cand: return
        cand.sort()
        H, W = im.shape[:2]
        rects = {j: bumper_rect(self.cam, xy[j], W, H) for j in np.where(np.isfinite(xy).all(1) & (conf >= 0.2))[0]}
        cen = {j: self.cam.project(np.array([[xy[j, 0], xy[j, 1], 0.15]]))[0] for j in rects}
        jobs = []
        for _, _, j in cand[:OCR_MAX_PER_FRAME]:
            x0, y0, x1, y1 = rects[j]
            if x1 - x0 < 20 or y1 - y0 < 20: continue
            jobs.append((j, im[y0:y1, x0:x1].copy(), (x0, y0)))
            self.recent.append((t, xy[j, 0], xy[j, 1]))
        if jobs:
            self.n_tried += len(jobs)
            for j in rects: self.rowpos[row0 + j] = (t, float(xy[j, 0]), float(xy[j, 1]))
            self._put((row0, jobs, rects, cen))

    def _put(self, item):
        import queue, pickle
        if self.proc is not None:
            alive = [k for k, p in enumerate(self.procs) if p.poll() is None]
            if not alive:
                self.dead = True; self.err = self.err or "OCR workers died"; return
            k = alive[self.rr % len(alive)]; self.rr += 1
            try:
                pickle.dump(item, self.procs[k].stdin, protocol=4); self.procs[k].stdin.flush()
            except (BrokenPipeError, OSError):
                self.err = self.err or "an OCR worker died"
            return
        while not self.dead:
            try:
                self.q.put(item, timeout=5); return
            except queue.Full:
                if self.proc is not None and not self.proc.is_alive():
                    self.dead = True; self.err = "OCR worker died"

    def _reader(self, k):
        import pickle
        try:
            while True:
                kind, val = pickle.load(self.procs[k].stdout)
                if kind == "reads":
                    self.reads += val
                    for r in val:
                        if r[2] > -6 and r[3] >= 4 and r[0] in self.rowpos: self.good.append(self.rowpos[r[0]])
                elif kind == "ready": self.device = val
                elif kind == "error": self.err = val
                elif kind == "done": self.busy[k] = val; break
        except Exception as e:
            if not self.err: self.err = f"OCR worker: {e!r}"
        self.done[k].set()

    def _work(self):
        import time
        while True:
            item = self.q.get()
            if item is None: break
            try:
                t0 = time.time()
                row0, jobs, rects, cen = item
                res = self.ocr.read_many([c for _, c, _ in jobs], self.cands, offsets=[o for _, _, o in jobs])
                val = _attribute(res, jobs, rects, cen, row0)
                self.reads += val
                for r in val:
                    if r[2] > -6 and r[3] >= 4 and r[0] in self.rowpos: self.good.append(self.rowpos[r[0]])
                self.busy_s += time.time() - t0
            except Exception as e:          # never kill detection over OCR
                self.err = repr(e)

    def close(self):
        if self.proc is not None:
            import pickle
            for k, p in enumerate(self.procs):
                try:
                    pickle.dump(None, p.stdin, protocol=4); p.stdin.flush(); p.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            for k, p in enumerate(self.procs):
                self.done[k].wait(timeout=600)
                try: p.wait(timeout=30)
                except Exception: p.kill()
            self.busy_s = sum(self.busy)
        else:
            self.q.put(None); self.th.join()
        if self.err: self.log(f"  OCR error: {self.err}")
        self.log(f"  OCR: {self.n_tried} crops, {len(self.reads)} reads "
                 f"({self.device}, {self.busy_s:.0f}s busy)")
        return np.array(self.reads, float).reshape(-1, 7)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--worker":
    import json as _json
    _worker_main(_json.loads(sys.argv[2]), int(sys.argv[3]))
