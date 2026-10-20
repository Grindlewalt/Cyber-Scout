"""Local detector training for any season (classes come from the project), on our own GPUs: no Roboflow / Kaggle.

    TRAIN_DIR=/mnt/nas/autoscout-scratch/train  trainenv/bin/python live/train/cstrain.py <command> ...

  init P --classes robot[,piece]                a project (one per season) under TRAIN_DIR
  import-yolo DIR --project P                   human labels from YOLO-format sets (e.g. the cached Roboflow exports)
  harvest --project P --event EV --videos V...  full frames every --every s; near duplicates dropped
  autolabel --project P --teacher a.pt[,b.pt]   ensemble pseudo-labels (WBF) + an uncertainty per frame
  review --project P                            the labelling / review web UI (live/train/review/)
  build NAME --project P                        a YOLO dataset: review > import > auto labels; splits by match
  train NAME --project P --dataset D            ultralytics on one or both GPUs (--detach: in the background)
  eval MODEL --project P [--baseline X.onnx]    mAP50 / mAP50-95 / P / R at the worker's CONF_TRACK, ms per frame
  export RUN --project P                        rectangular fp16 ONNX like production (544x1280) + card.json
  install MODEL_DIR --project P --data DATA     into DATA/models (old one backed up); used from the next match on
  status --project P

Project layout and the recipe: live/train/README.md.  Pure helpers (tested): cstrain_core.py."""
import argparse, fcntl, glob, json, os, re, shutil, subprocess, sys, time
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.dirname(HERE)
REPO = os.path.dirname(LIVE)
sys.path.insert(0, HERE)
import cstrain_core as K  # noqa: E402

TRAIN_DIR = os.environ.get("TRAIN_DIR", "/mnt/nas/autoscout-scratch/train")
PROD_DATA = "/opt/stacks/cyber-scout/data"          # read only here (match files, camera.json)
CONF_TRACK = 0.30                                   # live/process.py: detections the tracker uses
EXPORT_HW = (544, 1280)                             # production export (live/detector.py reads it from the model)
RF_AUG = re.compile(r"[._](jpg|png|jpeg)\.rf\.[0-9a-f]+\.(jpg|png|jpeg)$", re.I)   # Roboflow copies of one image
SUBDIRS = ("frames", "labels/auto", "labels/import", "labels/review", "datasets", "runs", "models")


# ---------------------------------------------------------------- files
def pdir(p):
    return os.path.abspath(p) if ("/" in p or p.startswith(".")) else os.path.join(TRAIN_DIR, p)


def jload(p, default=None):
    try:
        with open(p) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def jsave(p, obj, indent=None):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=indent)
    os.replace(tmp, p)


def load_project(name):
    P = pdir(name)
    pj = jload(os.path.join(P, "project.json"))
    if not pj:
        sys.exit(f"no project at {P} (cstrain.py init {name} --classes robot)")
    return P, pj


def load_index(P):
    """frames/index.jsonl -> {id: record}; a later line for the same id wins.  A reviewer's view correction (the
    review tool's Fixed / Moving toggle, `view` in labels/review/<id>.json) replaces the index's F / M / ? tag."""
    idx = {}
    try:
        with open(os.path.join(P, "frames", "index.jsonl")) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    idx[r["id"]] = r
    except OSError:
        pass
    for f in glob.glob(os.path.join(P, "labels", "review", "*.json")):
        fid = os.path.basename(f)[:-5]
        v = (jload(f) or {}).get("view") if fid in idx else None
        if v in ("F", "M", "?") and v != idx[fid].get("view"):
            idx[fid] = dict(idx[fid], view=v)
    return idx


def append_index(P, recs):
    if not recs:
        return
    with open(os.path.join(P, "frames", "index.jsonl"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))


def write_index(P, idx):
    p = os.path.join(P, "frames", "index.jsonl")
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in idx.values()))
    os.replace(tmp, p)


def label(P, kind, fid):
    return jload(os.path.join(P, "labels", kind, f"{fid}.json"))


def expand(paths):
    out = []
    for p in paths:
        g = sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
        out += [x for x in g if os.path.isfile(x)]
    return list(dict.fromkeys(out))


def data_dir(a):
    d = getattr(a, "data", None) or os.environ.get("DATA")
    return d if d else (PROD_DATA if os.path.isdir(PROD_DATA) else None)


def find_camera(event, data, P=None):
    """The project's own copy (cameras/<event>.json, saved by harvest) first, then the stack's, then the repo's."""
    for p in ((os.path.join(P, "cameras", f"{event}.json") if P else None),
              (os.path.join(data, "events", event, "camera.json") if data else None),
              os.path.join(REPO, "events", event, "camera.json")):
        if p and os.path.isfile(p):
            return p
    return None


def link(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        os.symlink(src, dst)


def sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def yolo_env(P):
    """ultralytics keeps its settings / fonts / downloaded weights on the NAS, never in the home dir."""
    os.environ.setdefault("YOLO_CONFIG_DIR", os.path.join(TRAIN_DIR, ".ultralytics"))
    from ultralytics import settings
    wd = os.path.join(TRAIN_DIR, "weights")
    os.makedirs(wd, exist_ok=True)
    settings.update({"sync": False, "weights_dir": wd, "runs_dir": os.path.join(P, "runs")})
    return wd


# ---------------------------------------------------------------- init / status
def cmd_init(a):
    P = pdir(a.project)
    classes = [c.strip() for c in a.classes.split(",") if c.strip()]
    old = jload(os.path.join(P, "project.json"))
    if old and old.get("classes") != classes:
        sys.exit(f"{P} exists with classes {old['classes']} (a project's class list is fixed: new season = new project)")
    for d in SUBDIRS:
        os.makedirs(os.path.join(P, d), exist_ok=True)
    pj = old or {"name": os.path.basename(P), "classes": classes, "created": time.time(), "notes": a.notes or ""}
    if a.notes:
        pj["notes"] = a.notes
    jsave(os.path.join(P, "project.json"), pj, indent=1)
    print(f"project {P}: classes {classes}")


def run_state(rd):
    """-> (epochs done, best mAP50-95 on val, running?) from an ultralytics run dir."""
    rows = []
    try:
        with open(os.path.join(rd, "results.csv")) as fh:
            head = [h.strip() for h in fh.readline().split(",")]
            for line in fh:
                v = [x.strip() for x in line.split(",")]
                if len(v) == len(head):
                    rows.append(dict(zip(head, v)))
    except OSError:
        pass
    best = max((float(r.get("metrics/mAP50-95(B)", 0) or 0) for r in rows), default=None)
    pid = None
    try:
        pid = int(open(os.path.join(rd, "cstrain.pid")).read())
        os.kill(pid, 0)
    except (OSError, ValueError):
        pid = None
    return len(rows), best, pid


def cmd_status(a):
    P, pj = load_project(a.project)
    idx = load_index(P)
    print(f"{P}  classes {pj['classes']}  {len(idx)} frames")
    by = defaultdict(Counter)
    for r in idx.values():
        by[r["event"]][r["split"]] += 1
        by[r["event"]]["view " + r.get("view", "?")] += 1
    for ev in sorted(by):
        print(f"  {ev:28s} " + "  ".join(f"{k} {v}" for k, v in sorted(by[ev].items())))
    lab = {k: set(f[:-5] for f in os.listdir(os.path.join(P, "labels", k)) if f.endswith(".json"))
           for k in ("auto", "import", "review")}
    st = Counter((label(P, "review", i) or {}).get("status") for i in lab["review"])
    unc = [float((label(P, "auto", i) or {}).get("u") or 0) for i in lab["auto"]] if a.verbose else []
    print(f"  labels: auto {len(lab['auto'])}, import {len(lab['import'])}, review {len(lab['review'])} "
          f"({', '.join(f'{k} {v}' for k, v in st.items())}); unlabelled "
          f"{len(set(idx) - lab['auto'] - lab['import'] - lab['review'])}")
    if unc:
        print(f"  auto uncertainty: median {np.median(unc):.2f}, >0.5: {sum(u > 0.5 for u in unc)}")
    for d in sorted(glob.glob(os.path.join(P, "datasets", "*", "manifest.json"))):
        m = jload(d, {})
        print(f"  dataset {m.get('name')}: images " + ", ".join(f"{s} {n}" for s, n in (m.get("images") or {}).items())
              + f"  (labels {dict(sum((Counter({k: v for k, v in c.items() if not k.startswith('left_out')}) for c in (m.get('counts') or {}).values()), Counter()))})")
    for rd in sorted(glob.glob(os.path.join(P, "runs", "*", ""))):
        n, best, pid = run_state(rd)
        if os.path.exists(os.path.join(rd, "args.yaml")):
            print(f"  run {os.path.basename(rd[:-1])}: {n} epochs, best val mAP50-95 "
                  f"{'-' if best is None else f'{best:.3f}'}{f'  (running, pid {pid})' if pid else ''}")
    for c in sorted(glob.glob(os.path.join(P, "models", "*", "card.json"))):
        m = jload(c, {})
        r = (m.get("metrics") or {}).get("model") or {}
        print(f"  model {m.get('name')}: {m.get('imgsz')} {'fp16' if m.get('half') else 'fp32'}"
              + (f", {r.get('split')} mAP50-95 {r['all']['map']:.3f}" if (r.get("all") or {}).get("map") is not None
                 else ""))


# ---------------------------------------------------------------- import-yolo
def yolo_sets(root):
    """Directories with a data.yaml under root (depth <= 3); none -> root itself."""
    ys = sorted(set(os.path.dirname(p) for d in range(4)
                    for p in glob.glob(os.path.join(root, *(["*"] * d), "data.yaml"))))
    return ys or [root]


def yaml_names(d, override=None):
    if override:
        return override
    p = os.path.join(d, "data.yaml")
    if not os.path.exists(p):
        return None
    import yaml
    n = (yaml.safe_load(open(p)) or {}).get("names")
    return [n[k] for k in sorted(n)] if isinstance(n, dict) else n


def cmd_import(a):
    import cv2
    P, pj = load_project(a.project)
    idx = load_index(P)
    extra = dict(kv.split("=", 1) for kv in a.map or [])
    extra = {k: int(v) for k, v in extra.items()}
    total = Counter()
    for sd in yolo_sets(os.path.abspath(a.dir)):
        setname = K.safe_id(a.name or os.path.basename(sd.rstrip("/")))
        names = yaml_names(sd, a.names.split(",") if a.names else None)
        if not names:
            print(f"{sd}: no data.yaml / --names, skipped")
            continue
        cmap = K.class_map(names, pj["classes"], extra)
        if not cmap:
            print(f"{sd}: no class maps to {pj['classes']} ({names}); use --map NAME=INDEX")
            continue
        ev = f"pub_{setname}"
        os.makedirs(os.path.join(P, "frames", ev), exist_ok=True)
        dd = K.Dedupe(a.dedupe, [r["phash"] for r in idx.values() if r["event"] == ev])
        seen, recs, n = set(), [], Counter()
        for im in sorted(glob.glob(os.path.join(sd, "**", "images", "**", "*.*"), recursive=True)):
            if os.path.splitext(im)[1].lower() not in (".jpg", ".jpeg", ".png"):
                continue
            stem = os.path.splitext(RF_AUG.sub("", os.path.basename(im)))[0]
            lb = os.path.splitext(im.replace(f"{os.sep}images{os.sep}", f"{os.sep}labels{os.sep}"))[0] + ".txt"
            parts = im[len(sd):].lower().split(os.sep)
            split = "val" if ("valid" in parts or "val" in parts) else "test" if "test" in parts else "train"
            fid = f"{ev}_{K.safe_id(stem)[:80]}"
            if stem in seen or fid in idx:
                n["dup"] += 1
                continue
            img = cv2.imread(im)
            if img is None:
                n["unreadable"] += 1
                continue
            H, W = img.shape[:2]
            boxes = K.from_yolo(open(lb).read() if os.path.exists(lb) else "", W, H, cmap)
            if not boxes and not a.keep_empty:
                n["no_boxes"] += 1
                continue
            h = K.phash(cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (32, 32), interpolation=cv2.INTER_AREA))
            if a.dedupe >= 0 and not dd.add(h):
                n["near_dup"] += 1
                continue
            seen.add(stem)
            rel = f"frames/{ev}/{fid}.jpg"
            if im.lower().endswith((".jpg", ".jpeg")):
                shutil.copyfile(im, os.path.join(P, rel))
            else:
                cv2.imwrite(os.path.join(P, rel), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            jsave(os.path.join(P, "labels", "import", f"{fid}.json"),
                  {"id": fid, "boxes": [{"c": c, "b": b, "src": f"import:{setname}"} for c, b in boxes],
                   "at": time.time()})
            recs.append(dict(id=fid, path=rel, event=ev, match=setname, video=im, t=0.0, w=W, h=H, view="?",
                             phash=h, src="public", split=split))
            n["kept"] += 1
        append_index(P, recs)
        total.update(n)
        print(f"{sd}: classes {[names[i] for i in cmap]} -> {[pj['classes'][j] for j in cmap.values()]}; "
              + ", ".join(f"{k} {v}" for k, v in sorted(n.items())), flush=True)
    print("import:", dict(total))


# ---------------------------------------------------------------- harvest
def match_of(video, event, rx=None):
    st = os.path.splitext(os.path.basename(video))[0]
    if rx:
        m = re.search(rx, st)
        if m:
            return K.safe_id(m.group(1) if m.groups() else m.group(0))
    for pre in (f"{event}_", "tba-", "live-"):
        if st.startswith(pre):
            st = st[len(pre):]
    return K.safe_id(st)


def view_info(event, match, data, matches_dir, cam, src):
    """What camera a frame shows: ('dyn', view string at 10 Hz) from a dynamic match file, ('F',) for an upload of a
    calibrated event (the worker tracks those as the fixed camera), else ('?',)."""
    md = matches_dir or (os.path.join(data, "events", event, "matches") if data else None)
    mf = jload(os.path.join(md, f"{event}_{match}.json")) if md else None
    dv = ((mf or {}).get("dynamic") or {}).get("view")
    if dv:
        return ("dyn", dv)
    if cam and src == "upload":
        return ("F",)
    return ("?",)


def frame_view(info, t):
    if info[0] == "dyn":
        k = int(t * 10)
        ch = info[1][k] if 0 <= k < len(info[1]) else "-"
        return ch if ch in "FM" else "?"
    return info[0]


def harvest_one(j):
    """One video -> frame records (JPEGs written).  Runs in a worker process."""
    import av, cv2
    try:
        c = av.open(j["video"])
    except Exception as e:
        return j["video"], [], f"cannot open ({e})"
    s = c.streams.video[0]
    s.thread_type = "AUTO"
    dur = float(s.duration * s.time_base) if s.duration else (c.duration / 1e6 if c.duration else None)
    t1 = min(j["t1"], dur) if dur else j["t1"]
    every = j["every"]
    if j["max"] and dur:
        every = max(every, (t1 - j["t0"]) / j["max"])
    last = None                                  # thumbnail of the last kept frame
    outdir = os.path.join(j["P"], "frames", j["event"])
    os.makedirs(outdir, exist_ok=True)
    nxt, out, n = j["t0"], [], Counter()
    try:
        for f in c.decode(s):
            if f.time is None or f.time < nxt:
                continue
            if f.time > t1:
                break
            nxt += every * max(1, int((f.time - nxt) // every) + 1)
            fid = K.frame_id(j["event"], j["match"], f.time)
            if fid in j["have"]:
                n["have"] += 1
                continue
            im = f.to_ndarray(format="bgr24")
            g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
            th = cv2.resize(g, (96, 54), interpolation=cv2.INTER_AREA)
            if last is not None and K.thumb_change(th, last) < j["min_change"]:
                n["static"] += 1                 # nothing moved: an idle field, a held graphic, a frozen feed
                continue
            last = th
            h = K.phash(cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA))
            rel = f"frames/{j['event']}/{fid}.jpg"
            cv2.imwrite(os.path.join(j["P"], rel), im, [cv2.IMWRITE_JPEG_QUALITY, j["q"]])
            out.append((dict(id=fid, path=rel, event=j["event"], match=j["match"], video=j["video"],
                             t=round(float(f.time), 3), w=im.shape[1], h=im.shape[0],
                             view=frame_view(j["view"], f.time), phash=h, src=j["src"], split=j["split"]), th))
    except Exception as e:                       # a truncated recording: keep what was read
        n[f"decode error {type(e).__name__}"] += 1
    c.close()
    return j["video"], out, ", ".join(f"{k} {v}" for k, v in n.items())


def cmd_harvest(a):
    from concurrent.futures import ProcessPoolExecutor
    P, pj = load_project(a.project)
    ev = K.safe_id(a.event)
    idx = load_index(P)
    data = data_dir(a)
    cam = a.camera or find_camera(a.event, data)
    vids = expand(a.videos)
    if not vids:
        sys.exit("no videos")
    jobs = []
    for v in vids:
        m = match_of(v, a.event, a.match_re)
        src = a.src or ("livestream" if (f"{os.sep}live{os.sep}" in v or os.path.basename(v).startswith("tba-"))
                        else "upload")
        jobs.append(dict(P=P, video=v, event=ev, match=m, src=src, every=a.every, t0=a.t0, t1=a.t1,
                         max=a.max_per_match, min_change=a.min_change, q=a.quality,
                         split=K.split_of(ev, m, a.val_frac, a.test_events or ()),
                         view=view_info(a.event, m, data, a.matches, cam, src),
                         have={i for i, r in idx.items() if r["event"] == ev and r["match"] == m}))
    if cam:
        os.makedirs(os.path.join(P, "cameras"), exist_ok=True)
        if not os.path.exists(os.path.join(P, "cameras", f"{ev}.json")):
            shutil.copyfile(cam, os.path.join(P, "cameras", f"{ev}.json"))
    print(f"harvest {len(jobs)} videos of {ev} every {a.every}s (camera {cam or '-'}; "
          f"views: {dict(Counter(j['view'][0] for j in jobs))}), {a.jobs} jobs", flush=True)
    # across videos: a duplicate = a near hash AND (almost) no pixel change -- the hash alone can't tell two matches
    # apart on a fixed overhead camera (robots are a few cells of its 32x32 picture)
    import cv2
    old = [r for r in idx.values() if r["event"] == ev]
    dd = K.Dedupe(a.dedupe, [r["phash"] for r in old])
    kept = [(r, None) for r in old]                    # thumbnails of older frames are read when needed

    def thumb(k):
        r, th = kept[k]
        if th is None:
            im = cv2.imread(os.path.join(P, r["path"]), cv2.IMREAD_GRAYSCALE)
            th = cv2.resize(im, (96, 54), interpolation=cv2.INTER_AREA) if im is not None else np.zeros((54, 96))
            kept[k] = (r, th)
        return th

    total = Counter()
    with ProcessPoolExecutor(a.jobs) as ex:
        for v, recs, note in ex.map(harvest_one, jobs):
            keep = []
            for r, th in recs:
                if a.dedupe < 0 or not any(K.thumb_change(th, thumb(k)) < a.min_change for k in dd.near(r["phash"])):
                    keep.append(r)
                    dd.push(r["phash"])
                    kept.append((r, th))
                else:
                    os.remove(os.path.join(P, r["path"]))
            append_index(P, keep)
            total["frames"] += len(keep)
            total["near_dup_across"] += len(recs) - len(keep)
            print(f"  {os.path.basename(v)}: {len(keep)} frames" + (f" ({note})" if note else ""), flush=True)
    print("harvest:", dict(total))


# ---------------------------------------------------------------- predictors (autolabel, eval)
def model_tag(path):
    st = os.path.splitext(os.path.basename(path))[0]
    if st in ("best", "last") and os.path.basename(os.path.dirname(path)) == "weights":
        st = os.path.basename(os.path.dirname(os.path.dirname(path))) + ("" if st == "best" else "-last")
    elif st == "robot" and os.path.basename(os.path.dirname(path)) not in ("", "models"):
        st = os.path.basename(os.path.dirname(path))      # models/<name>/robot.onnx -> <name>
    return st


def predictor(path, classes, imgsz=1280, device="0", conf=0.01, tta=False, extra_map=None):
    """-> f(list of BGR images) -> [(boxes (N,4) px, scores, project classes)].  .pt through ultralytics; .onnx
    through live/detector.py exactly as the worker runs it (fixed input size, class-agnostic: the robot class)."""
    dev = str(device).split(",")[0]
    if path.endswith(".onnx"):
        os.environ["GPU_DEVICE"] = dev
        sys.path.insert(0, LIVE)
        from detector import Detector
        det = Detector(path)
        rc = classes.index("robot") if "robot" in classes else 0

        def run_onnx(ims):
            out = []
            for im in ims:
                B, S = det(im, conf=conf, iou=0.6, max_det=100)
                out.append((np.asarray(B, float).reshape(-1, 4), np.asarray(S, float), np.full(len(S), rc, int)))
            return out
        run_onnx.device = det.device
        return run_onnx
    from ultralytics import YOLO
    m = YOLO(path)
    names = [m.names[k] for k in sorted(m.names)]
    cmap = K.class_map(names, classes, extra_map)
    if not cmap and len(names) == 1:
        cmap = {0: 0}                               # one-class teacher with an unknown name: the project's first class
    if not cmap:
        sys.exit(f"{path}: none of its classes {names} is in the project {classes}")

    def run_pt(ims):
        res = m.predict(ims, imgsz=imgsz, conf=conf, iou=0.6, augment=tta, device=dev, quantize=16 if dev != "cpu" else None,
                        verbose=False, max_det=100)
        out = []
        for r in res:
            B = r.boxes.xyxy.cpu().numpy()
            S = r.boxes.conf.cpu().numpy()
            C = r.boxes.cls.cpu().numpy().astype(int)
            k = np.array([c in cmap for c in C], bool)
            out.append((B[k], S[k], np.array([cmap[c] for c in C[k]], int)))
        return out
    run_pt.device = f"cuda:{dev}" if dev != "cpu" else "cpu"
    return run_pt


def batches(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------- autolabel
def cmd_autolabel(a):
    import cv2
    P, pj = load_project(a.project)
    idx = load_index(P)
    teachers = [t for t in a.teacher.split(",") if t]
    w = [float(x) for x in a.weights.split(",")] if a.weights else None
    tag = "+".join(model_tag(t) for t in teachers) + ("@tta" if a.tta else "") + f"@{a.imgsz}"
    sel = [r for r in idx.values() if (a.public or r["src"] != "public")
           and (not a.events or r["event"] in a.events)]
    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        sel = [r for r in sel if int(r["phash"][:8], 16) % n == i]
    if not a.redo:
        sel = [r for r in sel if (label(P, "auto", r["id"]) or {}).get("teacher") != tag]
    print(f"autolabel {len(sel)} frames with {tag} (conf >= {a.conf})", flush=True)
    extra = {k: int(v) for k, v in (kv.split("=", 1) for kv in a.map or [])}
    preds = [predictor(t, pj["classes"], a.imgsz, a.device, a.conf * 0.5, a.tta, extra) for t in teachers]
    t0, done, nbox = time.time(), 0, 0
    us = []
    for bt in batches(sel, a.batch):
        ims = [cv2.imread(os.path.join(P, r["path"])) for r in bt]
        ok = [k for k, im in enumerate(ims) if im is not None]
        per = [pr([ims[k] for k in ok]) for pr in preds]
        for j, k in enumerate(ok):
            r = bt[k]
            fused = [b for b in K.wbf([p[j] for p in per], w, a.iou) if b["p"] >= a.conf]
            boxes = [dict(c=b["c"], b=b["b"], p=b["p"], n=b["n"], src=f"teacher:{tag}") for b in fused]
            u = K.frame_uncertainty(boxes, len(teachers))
            jsave(os.path.join(P, "labels", "auto", f"{r['id']}.json"),
                  {"id": r["id"], "boxes": boxes, "u": u, "teacher": tag, "at": time.time()})
            nbox += len(boxes)
            us.append(u)
        done += len(bt)
        if done % (a.batch * 50) < a.batch:
            print(f"  {done}/{len(sel)} frames, {(time.time() - t0) / done * 1000:.0f} ms/frame", flush=True)
    if a.bglabel:                                # locked-off uploads only: on program feeds it over-proposes
        bglabel_pass(P, pj, [r for r in sel if r.get("view") == "F" and r["src"] == "upload"], data_dir(a))
    if us:
        print(f"autolabel: {done} frames, {nbox} boxes, uncertainty median {np.median(us):.2f}, "
              f"> 0.5: {sum(u > 0.5 for u in us)}; {(time.time() - t0) / max(done, 1) * 1000:.0f} ms/frame")


def bglabel_pass(P, pj, frames, data):
    """Fixed-camera frames: robots the teachers missed, from bumper colour vs the match's median plate
    (live/bglabel.py, grown to a whole-robot box with the event camera).  They go in with p 0.4 (u 0.8), so the
    frame is left out of training until someone reviews it."""
    import cv2
    sys.path.insert(0, LIVE)
    from camera import Camera
    from bglabel import field_mask, bumper_proposals
    from make_labels import grow
    rc = pj["classes"].index("robot") if "robot" in pj["classes"] else 0
    by = defaultdict(list)
    for r in frames:
        by[(r["event"], r["match"])].append(r)
    cams, added = {}, 0
    for (ev, m), rs in sorted(by.items()):
        if len(rs) < 9:
            continue
        if ev not in cams:
            cp = find_camera(ev, data, P)
            cams[ev] = (Camera(cp), None) if cp else None
            if cams[ev]:
                cams[ev] = (cams[ev][0], field_mask(cams[ev][0], h=0.2))
        if not cams[ev]:
            continue
        cam, fm = cams[ev]
        pick = rs[:: max(1, len(rs) // 45)]
        stack = [cv2.imread(os.path.join(P, r["path"])) for r in pick]
        stack = [s for s in stack if s is not None and s.shape[:2] == (cam.H, cam.W)]
        if len(stack) < 9:
            continue
        plate = np.median(np.stack(stack), 0).astype(np.uint8)
        for r in rs:
            im = cv2.imread(os.path.join(P, r["path"]))
            lp = os.path.join(P, "labels", "auto", f"{r['id']}.json")
            lab = jload(lp)
            if im is None or lab is None or im.shape[:2] != (cam.H, cam.W):
                continue
            new = []
            for p in bumper_proposals(im, plate, cam, fm):
                if any(K.inside(p[:4], b["b"]) for b in lab["boxes"]):
                    continue
                g = [round(float(v), 1) for v in grow(cam, p[:4])]
                new.append(dict(c=rc, b=g, p=0.4, n=0, src="bglabel"))
            if new:
                lab["boxes"] += new
                lab["u"] = max(float(lab.get("u") or 0), 0.8)
                jsave(lp, lab)
                added += len(new)
    print(f"bglabel: {added} proposals the teachers missed (frames marked for review)")


# ---------------------------------------------------------------- build
def cmd_build(a):
    P, pj = load_project(a.project)
    idx = load_index(P)
    nc = len(pj["classes"])
    D = os.path.join(P, "datasets", a.name)
    if os.path.exists(D):
        if not a.force:
            sys.exit(f"{D} exists (--force rebuilds it)")
        shutil.rmtree(D)
    dup = {k: int(v) for k, v in (kv.split("=", 1) for kv in a.dup or [])}
    test_ev = set(a.test_events or ())
    skip_by = set(a.exclude_reviewers or ())
    counts = defaultdict(Counter)
    frames, negs = {}, defaultdict(list)
    for s in ("train", "val", "test"):
        os.makedirs(os.path.join(D, "images", s))
        os.makedirs(os.path.join(D, "labels", s))

    def put(r, split, boxes, srcl, reps=1):
        for k in range(reps):
            name = r["id"] if k == 0 else f"{r['id']}__d{k}"
            link(os.path.join(P, r["path"]), os.path.join(D, "images", split, name + ".jpg"))
            with open(os.path.join(D, "labels", split, name + ".txt"), "w") as fh:
                fh.write(K.to_yolo(boxes, r["w"], r["h"], a.min_box))
        frames[r["id"]] = [split, srcl, len(boxes)]
        counts[split][srcl] += reps

    for r in idx.values():
        pub = r["src"] == "public"
        if (a.events and r["event"] not in a.events and not pub) or r["event"] in (a.exclude_events or ()):
            continue
        if pub and a.no_public:
            continue
        split = r["split"]
        if a.resplit and not pub:
            split = K.split_of(r["event"], r["match"], a.val_frac)
        if r["event"] in test_ev:
            split = "test"
        elif pub and not a.public_val:
            split = "train"
        # the doubtful-frame rules are for train only (val / test keep every frame, or their scores would flatter)
        rv = label(P, "review", r["id"])
        if rv is not None and rv.get("by") in skip_by:
            rv = None                                   # --exclude-reviewers: as if never reviewed
        srcl, boxes = K.pick_labels(rv, label(P, "import", r["id"]),
                                    label(P, "auto", r["id"]), a.auto_min_conf, a.auto_mid, a.auto_max_u,
                                    strict=split == "train")
        if srcl is None or (a.no_auto and srcl == "auto"):
            counts[split]["left_out"] += 1
            continue
        boxes = [(c, b) for c, b in boxes if 0 <= c < nc]
        if not boxes and split == "train":
            negs[split].append((r, srcl))              # empty frames: a capped share, below
            continue
        put(r, split, boxes, srcl, dup.get(srcl, 1) if split == "train" else 1)
    for split, lst in negs.items():
        cap = int(a.neg_max * sum(v for k, v in counts[split].items() if k != "left_out"))
        lst.sort(key=lambda x: x[0]["phash"])           # deterministic, spread over events
        for r, srcl in lst[:cap]:
            put(r, split, [], srcl)
        counts[split]["left_out_empty"] += max(0, len(lst) - cap)
    n = {s: len(os.listdir(os.path.join(D, "images", s))) for s in ("train", "val", "test")}
    if not n["val"]:
        sys.exit(f"{D}: no val frames (harvest more matches, or --public-val)")
    with open(os.path.join(D, "data.yaml"), "w") as fh:
        fh.write(f"path: {D}\ntrain: images/train\nval: images/val\n" + ("test: images/test\n" if n["test"] else "")
                 + f"nc: {nc}\nnames: {json.dumps(pj['classes'])}\n")
    man = dict(name=a.name, created=time.time(), classes=pj["classes"], images=n,
               counts={s: dict(c) for s, c in counts.items()},
               params=dict(auto_min_conf=a.auto_min_conf, auto_mid=a.auto_mid, auto_max_u=a.auto_max_u, dup=dup,
                           neg_max=a.neg_max,
                           test_events=sorted(test_ev), events=a.events, exclude_events=a.exclude_events,
                           public=not a.no_public, public_val=a.public_val, resplit=a.resplit, min_box=a.min_box),
               frames=frames)
    jsave(os.path.join(D, "manifest.json"), man)
    print(f"dataset {D}: images {n}")
    for s, c in sorted(counts.items()):
        print(f"  {s}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))


# ---------------------------------------------------------------- train
HYP = dict(epochs=150, imgsz=1280, batch=8, workers=8, patience=40, cos_lr=True, close_mosaic=10, mosaic=1.0,
           scale=0.5, translate=0.1, degrees=0.0, fliplr=0.5, flipud=0.0, hsv_h=0.012, hsv_s=0.6, hsv_v=0.35,
           seed=0, plots=True, amp=True)


def parse_hyp(kvs):
    out = {}
    for kv in kvs or []:
        k, v = kv.split("=", 1)
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def cmd_train(a):
    P, pj = load_project(a.project)
    rd = os.path.join(P, "runs", a.name)
    if a.detach:
        os.makedirs(rd, exist_ok=True)
        argv = [x for x in sys.argv[1:] if x != "--detach"]
        log = os.path.join(P, "runs", f"{a.name}.log")
        with open(log, "ab") as fh:
            pr = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__)] + argv, stdout=fh,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        open(os.path.join(rd, "cstrain.pid"), "w").write(str(pr.pid))
        print(f"training {a.name} in the background: pid {pr.pid}, log {log}\n  cstrain.py status --project {a.project}")
        return
    D = os.path.join(P, "datasets", a.dataset or "")
    if not a.resume and not os.path.exists(os.path.join(D, "data.yaml")):
        sys.exit(f"no dataset {D} (cstrain.py build)")
    if os.path.isfile(a.model):
        a.model = os.path.abspath(a.model)
    wd = yolo_env(P)
    os.chdir(wd)                                     # base weights (yolo11m.pt ...) download here once
    from ultralytics import YOLO
    os.makedirs(rd, exist_ok=True)
    open(os.path.join(rd, "cstrain.pid"), "w").write(str(os.getpid()))
    meta = dict(name=a.name, dataset=a.dataset, model=a.model, started=time.time(), argv=sys.argv[1:])
    if a.resume:
        last = os.path.join(rd, "weights", "last.pt")
        if not os.path.exists(last):
            sys.exit(f"nothing to resume ({last})")
        meta = jload(os.path.join(rd, "cstrain.json"), meta)
        YOLO(last).train(resume=True)
    else:
        hyp = dict(HYP, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, workers=a.workers, patience=a.patience)
        hyp.update(parse_hyp(a.hyp))
        if a.cache:
            hyp["cache"] = a.cache
        meta["hyp"] = hyp
        meta["manifest"] = {k: v for k, v in (jload(os.path.join(D, "manifest.json")) or {}).items() if k != "frames"}
        jsave(os.path.join(rd, "cstrain.json"), meta, indent=1)
        device = a.device if "," in str(a.device) else (int(a.device) if str(a.device).isdigit() else a.device)
        YOLO(a.model).train(data=os.path.join(D, "data.yaml"), project=os.path.join(P, "runs"), name=a.name,
                            exist_ok=True, device=device, **hyp)
    n, best, _ = run_state(rd)
    meta.update(finished=time.time(), epochs_done=n, best_val_map=best)
    jsave(os.path.join(rd, "cstrain.json"), meta, indent=1)
    try:
        os.remove(os.path.join(rd, "cstrain.pid"))
    except OSError:
        pass
    print(f"trained {a.name}: {n} epochs, best val mAP50-95 {best}; weights {rd}/weights/best.pt\n"
          f"  next: cstrain.py eval {a.name} --project {a.project} --baseline <production robot.onnx>")


# ---------------------------------------------------------------- eval
def resolve_model(m, P):
    """run name -> runs/<m>/weights/best.pt; models/<m> -> robot.onnx; else a path."""
    for p in (os.path.join(P, "runs", m, "weights", "best.pt"), os.path.join(P, "models", m, "robot.onnx"),
              os.path.join(m, "robot.onnx"), m):
        if os.path.isfile(p):
            return os.path.abspath(p)
    sys.exit(f"no model {m}")


def report_path(model, P):
    d = os.path.dirname(model)
    if os.path.basename(d) == "weights":
        return os.path.join(os.path.dirname(d), "report.json")
    if os.path.dirname(d) == os.path.join(P, "models"):
        return os.path.join(d, "report.json")
    return os.path.join(P, "reports", f"{model_tag(model)}-{time.strftime('%Y%m%d-%H%M%S')}.json")


def eval_frames(P, D, split, idx, man, max_frames, as_worker, data):
    """-> [(id, image path, gt [(c, box)], view, label source, roi or None)]."""
    import cv2
    sys.path.insert(0, LIVE)
    from camera import Camera
    cams = {}
    out = []
    files = sorted(glob.glob(os.path.join(D, "images", split, "*.jpg")))
    files = [f for f in files if "__d" not in os.path.basename(f)]
    if max_frames and len(files) > max_frames:
        files = files[:: int(np.ceil(len(files) / max_frames))]
    for f in files:
        fid = os.path.splitext(os.path.basename(f))[0]
        r = idx.get(fid) or {}
        W, H = r.get("w"), r.get("h")
        if not W:
            H, W = cv2.imread(f).shape[:2]
        gt = K.from_yolo(open(os.path.join(D, "labels", split, fid + ".txt")).read(), W, H)
        roi = None
        if as_worker and r.get("view") == "F":
            if r["event"] not in cams:
                cp = find_camera(r["event"], data, P)
                cams[r["event"]] = Camera(cp) if cp else None
            cam = cams[r["event"]]
            if cam is not None and (cam.W, cam.H) == (W, H):
                roi = cam.roi()
        out.append((fid, f, gt, r.get("view", "?"), ((man.get("frames") or {}).get(fid) or [None, "?"])[1], roi))
    return out


def crop_gt(gt, roi):
    x0, y0, x1, y1 = roi
    out = []
    for c, b in gt:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if x0 <= cx < x1 and y0 <= cy < y1:
            out.append((c, [max(b[0], x0) - x0, max(b[1], y0) - y0, min(b[2], x1) - x0, min(b[3], y1) - y0]))
    return out


def run_eval(pred, frames, nc, conf):
    import cv2
    res, t_total = [], 0.0
    for fid, f, gt, view, srcl, roi in frames:
        im = cv2.imread(f)
        if roi:
            im = im[roi[1]:roi[3], roi[0]:roi[2]]
            gt = crop_gt(gt, roi)
        t = time.perf_counter()
        B, S, C = pred([im])[0]
        t_total += time.perf_counter() - t
        res.append((view, srcl, gt, [(int(c), list(b), float(s)) for b, s, c in zip(B, S, C)]))
    groups = {"all": [(g, d) for _, _, g, d in res]}
    for key, pos in (("view", 0), ("labels", 1)):
        for v in sorted({x[pos] for x in res}):
            groups[f"{key}:{v}"] = [(g, d) for x in res for g, d in [(x[2], x[3])] if x[pos] == v]
    out = {k: K.evaluate(v, nc, conf) for k, v in groups.items()}
    out["ms_per_frame"] = round(t_total / max(len(res), 1) * 1000, 1)
    return out


def cmd_eval(a):
    P, pj = load_project(a.project)
    model = resolve_model(a.model, P)
    ds = a.dataset
    if not ds:
        for c in (os.path.join(os.path.dirname(os.path.dirname(model)), "cstrain.json"),
                  os.path.join(os.path.dirname(model), "card.json")):
            ds = (jload(c) or {}).get("dataset") or ((jload(c) or {}).get("data") or {}).get("dataset")
            if ds:
                break
    if not ds:
        sys.exit("which dataset? (--dataset)")
    D = os.path.join(P, "datasets", ds)
    split = a.split or ("test" if glob.glob(os.path.join(D, "images", "test", "*.jpg")) else "val")
    idx, man = load_index(P), jload(os.path.join(D, "manifest.json"), {})
    frames = eval_frames(P, D, split, idx, man, a.max_frames, not a.full_frame, data_dir(a))
    if not frames:
        sys.exit(f"no {split} frames in {D}")
    nc = len(pj["classes"])
    print(f"eval on {len(frames)} {split} frames of {ds} (labels: {dict(Counter(f[4] for f in frames))}; "
          f"{'fixed-camera frames cropped to the field ROI like the worker' if not a.full_frame else 'full frames'})",
          flush=True)
    teachers = sorted({(label(P, "auto", f[0]) or {}).get("teacher") for f in frames if f[4] == "auto"} - {None})
    if teachers:
        print(f"  NOTE: {sum(f[4] == 'auto' for f in frames)} frames carry auto labels from {teachers}: a teacher (or a "
              "copy of it) is graded on its own answers there -- compare models on labels:review (the gold set)")
    rep = dict(dataset=ds, split=split, frames=len(frames), conf=a.conf, at=time.time(), auto_label_teachers=teachers,
               note="labels:auto = the teacher's pseudo-labels (silver), not human gold; .onnx = the worker's "
                    "detector path (fixed input size); .pt = ultralytics at --imgsz")
    for key, path in [("model", model)] + [(f"baseline{'' if i == 0 else i}", b) for i, b in enumerate(a.baseline or [])]:
        pred = predictor(path, pj["classes"], a.imgsz, a.device, 0.001)
        r = run_eval(pred, frames, nc, a.conf)
        r.update(path=path, device=getattr(pred, "device", "?"), split=split)
        rep[key] = r
        print(f"  {key:10s} {model_tag(path):18s} mAP50 {r['all']['map50']:.3f}  mAP50-95 {r['all']['map']:.3f}  "
              f"P {r['all']['p']:.3f} R {r['all']['r']:.3f} @ {a.conf}  {r['ms_per_frame']} ms/frame ({r['device']})")
        for g, v in r.items():
            if g.startswith(("view:", "labels:")):
                print(f"      {g:14s} n {v['frames']:5d}  mAP50 {v['map50']:.3f}  mAP50-95 {v['map']:.3f}  "
                      f"P {v['p']:.3f} R {v['r']:.3f}")
    out = report_path(model, P)
    jsave(out, rep, indent=1)
    print("report", out)


# ---------------------------------------------------------------- export / install
def cmd_export(a):
    P, pj = load_project(a.project)
    pt = resolve_model(a.run, P)
    if not pt.endswith(".pt"):
        sys.exit(f"{pt}: export needs a .pt")
    rd = os.path.dirname(os.path.dirname(pt)) if os.path.basename(os.path.dirname(pt)) == "weights" else None
    name = a.name or (os.path.basename(rd) if rd else model_tag(pt))
    md = os.path.join(P, "models", name)
    if os.path.exists(os.path.join(md, "robot.onnx")) and not a.force:
        sys.exit(f"{md} exists (--force)")
    os.makedirs(md, exist_ok=True)
    yolo_env(P)
    from ultralytics import YOLO
    tmp = os.path.join(md, "export.pt")
    shutil.copyfile(pt, tmp)                        # ultralytics writes the .onnx next to the .pt
    hw = [int(x) for x in a.imgsz.split(",")]
    half = not a.fp32
    out = YOLO(tmp).export(format="onnx", imgsz=hw, quantize=16 if half else None, device=a.device, simplify=True,
                           opset=17)                # quantize=16 = the old half=True (ultralytics >= 8.4)
    import onnx
    m = onnx.load(out)
    key = sha256(pt)[:32]                           # Core ML cache key (Mac server): a new model never reuses the old cache
    for k, v in (("COREML_CACHE_KEY", key), ("cyberscout_model", name)):
        e = next((p for p in m.metadata_props if p.key == k), None) or m.metadata_props.add()
        e.key, e.value = k, v
    onnx.save(m, os.path.join(md, "robot.onnx"))
    os.remove(out)
    os.replace(tmp, os.path.join(md, "robot.pt"))
    # the worker's own decoder must read it
    sys.path.insert(0, LIVE)
    os.environ.setdefault("GPU_DEVICE", str(a.device))
    from detector import Detector
    det = Detector(os.path.join(md, "robot.onnx"))
    B, S = det(np.full((1080, 1920, 3), 114, np.uint8))
    meta = jload(os.path.join(rd, "cstrain.json"), {}) if rd else {}
    rep = jload(os.path.join(rd, "report.json")) if rd else None
    card = dict(name=name, created=time.time(), source=pt, classes=pj["classes"], imgsz=hw, half=half, opset=17,
                input=[det.h, det.w], provider=det.device, sha256=sha256(os.path.join(md, "robot.onnx")),
                size=os.path.getsize(os.path.join(md, "robot.onnx")), coreml_cache_key=key,
                data=dict(dataset=meta.get("dataset"), manifest=meta.get("manifest"), base_model=meta.get("model"),
                          hyp=meta.get("hyp"), epochs_done=meta.get("epochs_done"), best_val_map=meta.get("best_val_map")),
                metrics=rep)
    jsave(os.path.join(md, "card.json"), card, indent=1)
    if len(pj["classes"]) > 1:
        print(f"NOTE: {len(pj['classes'])} classes; live/detector.py is class-agnostic (max over classes = robot) -- "
              "the worker needs a class-aware decoder before a multi-class model goes in")
    print(f"exported {md}/robot.onnx ({card['size'] >> 20} MB, input {det.h}x{det.w}, {'fp16' if half else 'fp32'}, "
          f"runs on {det.device})")


def cmd_install(a):
    P, pj = load_project(a.project)
    md = a.model if os.path.isdir(a.model) else os.path.join(P, "models", a.model)
    src = os.path.join(md, "robot.onnx")
    card = jload(os.path.join(md, "card.json"), {})
    if not os.path.isfile(src):
        sys.exit(f"no {src}")
    if not a.data:
        sys.exit("--data DATA_DIR (the stack's data dir, e.g. /opt/stacks/cyber-scout/data)")
    rep = card.get("metrics") or jload(os.path.join(md, "report.json")) or {}
    new, base = (rep.get("model") or {}).get("all", {}), (rep.get("baseline") or {}).get("all", {})
    if not a.force:
        if not new:
            sys.exit("no eval report for this model (cstrain.py eval ... --baseline <current robot.onnx>); --force")
        if base and new.get("map", 0) < base.get("map", 0):
            sys.exit(f"worse than the baseline (mAP50-95 {new['map']:.3f} < {base['map']:.3f}); --force")
    import onnxruntime as ort
    s = ort.InferenceSession(src, providers=["CPUExecutionProvider"])
    print("model input", s.get_inputs()[0].shape, s.get_inputs()[0].type)
    dst = os.path.join(a.data, "models")
    os.makedirs(dst, exist_ok=True)
    bk = os.path.join(dst, "backup", time.strftime("%Y%m%d-%H%M%S"))
    for f in ("robot.onnx", "robot.pt", "card.json"):
        if os.path.exists(os.path.join(dst, f)):
            os.makedirs(bk, exist_ok=True)
            shutil.copy2(os.path.join(dst, f), os.path.join(bk, f))
    for f in ("robot.pt", "card.json", "robot.onnx"):             # robot.onnx last: the one the worker opens
        if os.path.exists(os.path.join(md, f)):
            tmp = os.path.join(dst, f".{f}.tmp")
            shutil.copyfile(os.path.join(md, f), tmp)
            os.replace(tmp, os.path.join(dst, f))
    print(f"installed {card.get('name', md)} -> {dst}/robot.onnx" + (f" (old one in {bk})" if os.path.isdir(bk) else "")
          + "\n  no restart: the worker and processors open DATA/models/robot.onnx for every match; Client processors"
            " re-download it (the server hashes it by size + mtime)")


def cmd_review(a):
    P, _ = load_project(a.project)
    sys.path.insert(0, HERE)
    try:
        from review.server import serve
    except ImportError as e:
        sys.exit(f"the review UI is not installed (live/train/review/server.py): {e}")
    serve(P, host=a.host, port=a.port, token=a.token or os.environ.get("CS_REVIEW_TOKEN"))


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="cstrain.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project")
    s.add_argument("project")
    s.add_argument("--classes", required=True, help="comma list, e.g. robot or robot,piece")
    s.add_argument("--notes")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("status", help="frames, labels, datasets, runs, models")
    s.add_argument("--project", required=True)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("import-yolo", help="human labels from YOLO-format sets (labels/import)")
    s.add_argument("dir")
    s.add_argument("--project", required=True)
    s.add_argument("--map", nargs="*", help="NAME=INDEX: a source class name -> project class index")
    s.add_argument("--names", help="class names when there is no data.yaml (comma list)")
    s.add_argument("--name", help="set name (default: the directory name)")
    s.add_argument("--keep-empty", action="store_true", help="also images without a mapped box (negatives)")
    s.add_argument("--dedupe", type=int, default=-1,
                   help="also drop images within this many hash bits of a kept one (default -1: only Roboflow's "
                        "augmented copies of one image)")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("harvest", help="frames from match videos")
    s.add_argument("--project", required=True)
    s.add_argument("--event", required=True)
    s.add_argument("--videos", nargs="+", required=True, help="files or quoted globs")
    s.add_argument("--every", type=float, default=1.0, help="seconds between frames (default 1)")
    s.add_argument("--t0", type=float, default=0.0)
    s.add_argument("--t1", type=float, default=200.0, help="video time to stop at (default 200 s)")
    s.add_argument("--max-per-match", type=int, default=0, help="cap per video (spreads the frames out)")
    s.add_argument("--min-change", type=float, default=1.0,
                   help="skip a frame whose 96x54 grey thumbnail differs from the last kept one by less than this "
                        "(mean abs, grey levels; default 1.0)")
    s.add_argument("--dedupe", type=int, default=6,
                   help="across the event's videos: a frame within this many hash bits of a kept one AND under "
                        "--min-change from it is a duplicate (default 6; -1 off)")
    s.add_argument("--quality", type=int, default=92)
    s.add_argument("--val-frac", type=float, default=0.1)
    s.add_argument("--test-events", nargs="*")
    s.add_argument("--src", choices=("upload", "livestream"), help="default: from the path")
    s.add_argument("--match-re", help="regex on the file name for the match (group 1)")
    s.add_argument("--data", help="stack data dir for match files + camera.json (default $DATA or production)")
    s.add_argument("--matches", help="match files dir (default DATA/events/EV/matches)")
    s.add_argument("--camera", help="event camera.json (default DATA/events/EV or repo events/EV)")
    s.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 4) // 3)))
    s.set_defaults(fn=cmd_harvest)

    s = sub.add_parser("autolabel", help="teacher pseudo-labels (labels/auto)")
    s.add_argument("--project", required=True)
    s.add_argument("--teacher", required=True, help="a.pt[,b.pt|c.onnx]")
    s.add_argument("--weights", help="per teacher, comma list")
    s.add_argument("--imgsz", type=int, default=1280)
    s.add_argument("--tta", action="store_true", help="ultralytics test-time augmentation (.pt teachers)")
    s.add_argument("--conf", type=float, default=0.2, help="keep fused boxes from this score (default 0.2)")
    s.add_argument("--iou", type=float, default=0.55, help="WBF cluster IoU")
    s.add_argument("--device", default="0")
    s.add_argument("--batch", type=int, default=8)
    s.add_argument("--events", nargs="*")
    s.add_argument("--public", action="store_true", help="also the imported public frames")
    s.add_argument("--redo", action="store_true", help="also frames this teacher already labelled")
    s.add_argument("--shard", help="i/n: this process takes 1/n of the frames (one process per GPU)")
    s.add_argument("--map", nargs="*", help="NAME=INDEX for teacher classes")
    s.add_argument("--bglabel", action="store_true", help="fixed-camera frames: bumper proposals the teachers missed")
    s.add_argument("--data")
    s.set_defaults(fn=cmd_autolabel)

    s = sub.add_parser("review", help="the labelling / review web UI")
    s.add_argument("--project", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=7590)
    s.add_argument("--token")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("build", help="a YOLO dataset from the labels")
    s.add_argument("name")
    s.add_argument("--project", required=True)
    s.add_argument("--events", nargs="*", help="only these events (public sets stay unless --no-public)")
    s.add_argument("--exclude-events", nargs="*")
    s.add_argument("--test-events", nargs="*", help="whole events held out as test")
    s.add_argument("--resplit", action="store_true", help="recompute train/val by match with --val-frac")
    s.add_argument("--val-frac", type=float, default=0.1)
    s.add_argument("--auto-min-conf", type=float, default=0.45, help="auto boxes from this score")
    s.add_argument("--auto-mid", type=float, default=0.3,
                   help="auto frames with a box in [auto-mid, auto-min-conf) left out (probably a missed robot)")
    s.add_argument("--auto-max-u", type=float, default=1.0, help="auto frames above this uncertainty left out")
    s.add_argument("--no-auto", action="store_true", help="human labels only")
    s.add_argument("--exclude-reviewers", nargs="*", metavar="NAME",
                   help="ignore the reviews by these reviewer names (test sessions, a reviewer to redo)")
    s.add_argument("--no-public", action="store_true")
    s.add_argument("--public-val", action="store_true", help="keep the public sets' own val split (default: train)")
    s.add_argument("--dup", nargs="*", help="SOURCE=N: repeat train frames of a label source, e.g. review=3")
    s.add_argument("--neg-max", type=float, default=0.1, help="empty train frames, as a share of the rest")
    s.add_argument("--min-box", type=float, default=4, help="drop boxes smaller than this (px)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("train", help="ultralytics training")
    s.add_argument("name")
    s.add_argument("--project", required=True)
    s.add_argument("--dataset")
    s.add_argument("--model", default="yolo11s.pt", help="yolo11s.pt / yolo11m.pt / a .pt to fine-tune")
    s.add_argument("--imgsz", type=int, default=1280)
    s.add_argument("--epochs", type=int, default=150)
    s.add_argument("--batch", type=int, default=8, help="total over the GPUs")
    s.add_argument("--device", default="1", help="0, 1 or 0,1")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--patience", type=int, default=40)
    s.add_argument("--cache", choices=("ram", "disk"))
    s.add_argument("--hyp", nargs="*", help="KEY=VALUE ultralytics overrides (e.g. lr0=0.003 mosaic=0.8)")
    s.add_argument("--resume", action="store_true")
    s.add_argument("--detach", action="store_true", help="run in the background (log runs/NAME.log)")
    s.set_defaults(fn=cmd_train)

    s = sub.add_parser("eval", help="accuracy on a held-out split, side by side with a baseline")
    s.add_argument("model", help="run name, models/<name>, or a .pt / .onnx path")
    s.add_argument("--project", required=True)
    s.add_argument("--dataset")
    s.add_argument("--split", choices=("val", "test"))
    s.add_argument("--baseline", nargs="*", help="e.g. the production robot.onnx")
    s.add_argument("--conf", type=float, default=CONF_TRACK)
    s.add_argument("--imgsz", type=int, default=1280, help=".pt models only")
    s.add_argument("--device", default="1")
    s.add_argument("--max-frames", type=int, default=0)
    s.add_argument("--full-frame", action="store_true", help="no field-ROI crop for fixed-camera frames")
    s.add_argument("--data")
    s.set_defaults(fn=cmd_eval)

    s = sub.add_parser("export", help="production ONNX + card")
    s.add_argument("run", help="run name or a .pt")
    s.add_argument("--project", required=True)
    s.add_argument("--name", help="models/<name> (default: the run name)")
    s.add_argument("--imgsz", default=",".join(map(str, EXPORT_HW)), help="H,W (default 544,1280)")
    s.add_argument("--fp32", action="store_true")
    s.add_argument("--device", default="1")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("install", help="put a model into a stack's DATA/models")
    s.add_argument("model", help="models/<name> or a dir with robot.onnx")
    s.add_argument("--project", required=True)
    s.add_argument("--data", help="the stack's data dir")
    s.add_argument("--force", action="store_true", help="without / despite the eval report")
    s.set_defaults(fn=cmd_install)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
