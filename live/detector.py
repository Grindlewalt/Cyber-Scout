"""YOLO robot detector on ONNX Runtime (CPU by default; CUDA if the provider exists).

No torch/ultralytics at runtime: the trained model is exported once to ONNX and
this file does letterbox -> forward -> decode -> NMS itself.  Output rows match
src/detect_match.py: frame, t, tid(-1), x0, y0, x1, y1 (FULL-FRAME px), conf, alliance.
"""
import os, numpy as np, cv2
import onnxruntime as ort


def alliance(img, box):
    """0 abstain, 1 red, 2 blue -- bumper colour in the lower part of the box
    (same rule as src/detect_match.py)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.shape[1], x1), min(img.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return 0
    crop = img[y0 + int(0.45 * (y1 - y0)):y1, x0:x1]
    if crop.size == 0:
        return 0
    h = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = h[..., 0].astype(int), h[..., 1].astype(int), h[..., 2].astype(int)
    sat = (S > 95) & (V > 45)
    red = (((H <= 9) | (H >= 168)) & sat).sum()
    blue = ((H >= 100) & (H <= 132) & sat).sum()
    if red + blue < 12:
        return 0
    return 1 if red > blue else 2


class Detector:
    def __init__(self, onnx_path, imgsz=None, threads=None):
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        if hasattr(ort, "preload_dlls"):          # pip CUDA/cuDNN wheels (onnxruntime-gpu[cuda,cudnn])
            try: ort.preload_dlls()
            except Exception: pass
        dev = int(os.environ.get("GPU_DEVICE", "0"))
        want = [] if os.environ.get("FORCE_CPU") == "1" else [("CUDAExecutionProvider", {"device_id": dev})]
        if os.environ.get("ORT_COREML") == "1":   # Mac server (deploy/macos): Apple GPU / Neural Engine via Core ML
            want.append(("CoreMLExecutionProvider", {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL",
                                                     "ModelCacheDirectory": os.path.join(os.environ.get(
                                                         "DATA", "/tmp"), "cache", "coreml")}))
        prov = [p for p in want if p[0] in ort.get_available_providers()] + ["CPUExecutionProvider"]
        self.sess = ort.InferenceSession(onnx_path, so, providers=prov)
        self.device = self.sess.get_providers()[0]
        i = self.sess.get_inputs()[0]
        self.inp = i.name
        self.h = int(i.shape[2]) if isinstance(i.shape[2], int) else (imgsz or 1280)
        self.w = int(i.shape[3]) if isinstance(i.shape[3], int) else (imgsz or 1280)
        self.dtype = np.float16 if "float16" in i.type else np.float32

    def _letterbox(self, img):
        h, w = img.shape[:2]; s = min(self.h / h, self.w / w)
        nh, nw = int(round(h * s)), int(round(w * s))
        out = np.full((self.h, self.w, 3), 114, np.uint8)
        top, left = (self.h - nh) // 2, (self.w - nw) // 2
        out[top:top + nh, left:left + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        return out, s, left, top

    def __call__(self, bgr, conf=0.10, iou=0.6, max_det=40):
        lb, s, dx, dy = self._letterbox(bgr)
        x = (lb[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0).astype(self.dtype)
        y = self.sess.run(None, {self.inp: np.ascontiguousarray(x)})[0][0].astype(np.float32)   # (4+nc, N)
        sc = y[4:].max(0); m = sc >= conf
        if not m.any():
            return np.zeros((0, 4)), np.zeros(0)
        cx, cy, w, h = y[0, m], y[1, m], y[2, m], y[3, m]; sc = sc[m]
        b = np.c_[cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
        b[:, [0, 2]] = (b[:, [0, 2]] - dx) / s
        b[:, [1, 3]] = (b[:, [1, 3]] - dy) / s
        keep = cv2.dnn.NMSBoxes(np.c_[b[:, :2], b[:, 2:] - b[:, :2]].tolist(), sc.tolist(), conf, iou)
        keep = np.array(keep, int).ravel()[:max_det]
        return b[keep], sc[keep]
