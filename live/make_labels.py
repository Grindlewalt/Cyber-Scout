"""Build a domain training set from event videos: field-ROI crops + YOLO labels.

    python live/make_labels.py CAMERA.json MODEL.onnx OUTDIR VIDEO... [--val VIDEO...]

Labels = detector boxes (conf >= DET_CONF) plus bumper-colour proposals the
detector missed (live/bglabel.py), the latter grown upward to a whole-robot box
using the camera model.  Frames every STEP seconds inside the match.
"""
import os, sys, json, numpy as np, cv2, av
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera import Camera
from detector import Detector
from bglabel import field_mask, bumper_proposals, expected_box

STEP = float(os.environ.get("STEP", "2.0"))
DET_CONF = float(os.environ.get("DET_CONF", "0.35"))


def plate_of(video, n=45):
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    dur = float(s.duration * s.time_base) if s.duration else 200
    ts = np.linspace(4, min(dur, 190) - 2, n); fr = []; i = 0
    for f in c.decode(s):
        if i < len(ts) and f.time >= ts[i]:
            fr.append(f.to_ndarray(format="bgr24")); i += 1
        if i >= len(ts): break
    c.close()
    return np.median(np.stack(fr), 0).astype(np.uint8)


def grow(cam, b):
    """Bumper box -> whole-robot box: keep x-span and bottom, raise top to robot height."""
    x0, y0, x1, y1 = b
    G = cam.img_to_ground(np.array([[(x0 + x1) / 2, y1]], float))[0]
    _, eh = expected_box(cam, G + 0.4 * cam._away(G[None])[0], h=0.6)
    return (x0, min(y0, y1 - eh), x1, y1)


def inside(b, B, pad=4):
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return B[0] - pad <= cx <= B[2] + pad and B[1] - pad <= cy <= B[3] + pad


def run(cam, det, video, outdir, split, tag):
    x0, y0, x1, y1 = cam.roi(); W, H = x1 - x0, y1 - y0
    plate = plate_of(video); fm = field_mask(cam, h=0.2)
    os.makedirs(f"{outdir}/images/{split}", exist_ok=True); os.makedirs(f"{outdir}/labels/{split}", exist_ok=True)
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    nxt, k, stats = 3.0, 0, [0, 0]
    for f in c.decode(s):
        if f.time is None or f.time < nxt: continue
        if f.time > 178: break
        nxt += STEP
        im = f.to_ndarray(format="bgr24")
        boxes, sc = det(im[y0:y1, x0:x1], conf=DET_CONF)
        boxes = [tuple(b + [x0, y0, x0, y0]) for b in boxes]
        stats[0] += len(boxes)
        for p in bumper_proposals(im, plate, cam, fm):
            if any(inside(p[:4], B) for B in boxes): continue
            boxes.append(grow(cam, p[:4])); stats[1] += 1
        name = f"{tag}_{k:04d}"; k += 1
        cv2.imwrite(f"{outdir}/images/{split}/{name}.jpg", im[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 92])
        with open(f"{outdir}/labels/{split}/{name}.txt", "w") as fh:
            for bx in boxes:
                a, b_, c_, d = np.clip([bx[0] - x0, bx[1] - y0, bx[2] - x0, bx[3] - y0], 0, [W, H, W, H])
                if c_ - a < 6 or d - b_ < 6: continue
                fh.write(f"0 {(a + c_) / 2 / W:.6f} {(b_ + d) / 2 / H:.6f} {(c_ - a) / W:.6f} {(d - b_) / H:.6f}\n")
    c.close()
    print(f"{tag}: {k} frames, {stats[0]} detector boxes + {stats[1]} bumper boxes", flush=True)


if __name__ == "__main__":
    cam = Camera(sys.argv[1]); det = Detector(sys.argv[2]); out = sys.argv[3]
    args = sys.argv[4:]; split = "train"
    for a in args:
        if a == "--val": split = "val"; continue
        run(cam, det, a, out, split, os.path.splitext(os.path.basename(a))[0])
