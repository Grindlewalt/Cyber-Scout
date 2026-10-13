"""Broadcast layout per event + video source: where things are in the frame.

The pipeline used to hard-code the 2026 FIRST Washington (Sammamish) broadcast: the clock box
(process.CLOCK_BOX), the overlay graphics to ignore (fuel.OVERLAY_BOXES) and the two driver-station
insets of the "Match N" upload (stationcam.RECTS).  This module reads them from a per-event
layout.json written by the box editor (web/sources.js via server/routers/sources.py) and falls back
to exactly those hard-coded values when no layout exists, so 2026wasam behaves as before.

layout.json (data/events/<ev>/layout.json, else the repo's events/<ev>/layout.json):
  {"version": 1,
   "processing_source": "<source id>",       # the overhead/wide video the tracker runs on (optional)
   "station_source": "<source id>",          # the video holding the station-camera insets (optional)
   "sources": {"<source id>": {
        "W": 1920, "H": 1080,                 # pixel frame the boxes are drawn in
        "role": "wide" | "dynamic" | "alliance-side",
        "regions": [{"id": "r1", "kind": "<KINDS key>", "label": "...", "box": [x0, y0, x1, y1],
                     "suggested": false}, ...],
        "frame": {"t": 60.0, "video": "..."}, "updated_at": 0, "updated_by": "..."}}}

Region kinds (KINDS): field (overhead field area = detection ROI), overhead (the overhead camera's
picture when it is itself an inset), station_red / station_blue (driver-station camera insets),
clock (match-clock digits: tick detection), scoreboard (score bar graphic), mask (any graphic to
ignore), inset (another picture-in-picture to ignore).

Pipeline accessors (all return the hard-coded default when there is no layout / no such box):
    L = layout.find(event_dir=..., cam_path=..., video_path=..., cam=...)   # Layout or None
    layout.clock_box(L, W, H)          -> (x0, y0, x1, y1)  process / stationcam tick detection
    layout.overlay_boxes(L, W, H)      -> [(x0, y0, x1, y1)] in W x H px, or None = fuel default
    layout.detect_masks(L, W, H)       -> [(x0, y0, x1, y1)] insets inside the processing video ([] default)
    layout.field_box(L, W, H)          -> (x0, y0, x1, y1) or None (detection ROI clip)
    layout.station_rects(L)            -> {"red": (...), "blue": (...)} or None = stationcam default
    layout.sync_region(L)              -> region shared by the wide and the inset upload, or None

Tools for the editor (server side):
    grab_frames(src, times)                    BGR frames from a file / URL (ffmpeg; yt-dlp -4 -g for YouTube)
    suggest_overlays(frames)                   static graphics (temporal stillness + edges)
    suggest_clock(frames_dense, hz)            the 1 Hz-ticking block inside the graphics
    suggest_insets(frames_a, frames_b)         picture-in-picture rectangles (program vs clean feed)
    suggest_insets_edges(frames)               ... from persistent straight borders (one source)
    suggest_field(frame, cam=None)             field area (projected field box if calibrated)
"""
import json, os, re, subprocess, time


class _LazyNumpy:
    """numpy on first use: the API container imports this module (KINDS, Layout, defaults) without numpy."""
    def __getattr__(self, k):
        import numpy
        return getattr(numpy, k)


np = _LazyNumpy()

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

KINDS = {
    "field": "Overhead field area (robot/fuel detection region)",
    "overhead": "Overhead camera picture (only if the overhead view is itself an inset)",
    "station_red": "Red driver-station camera inset",
    "station_blue": "Blue driver-station camera inset",
    "clock": "Match clock digits",
    "scoreboard": "Score bar graphic",
    "mask": "Graphic / area to ignore",
    "inset": "Other picture-in-picture to ignore",
}
REF_W, REF_H = 1920, 1080

# ---------------------------------------------------------------- the hard-coded 2026 FIRST values
# (kept here as data; process.py / fuel.py / stationcam.py keep their own constants as the defaults)
CLOCK_BOX = (912, 72, 1008, 116)                                      # process.CLOCK_BOX
OVERLAY_BOXES = [(446, 8, 1474, 134), (896, 126, 1024, 170),          # fuel.OVERLAY_BOXES
                 (28, 60, 358, 118), (1562, 60, 1890, 118),
                 (616, 1004, 1304, 1072)]
STATION_RECTS = {"red": (0, 708, 622, 1080), "blue": (1294, 708, 1920, 1080)}   # stationcam.RECTS
SYNC_REGION = (0, 0, 1920, 700)                                       # stationcam.SYNC_REGION


def first2026_regions(insets=False):
    """The 2026 FIRST (Sammamish) layout as editor regions -- what the pipeline does without a layout."""
    R = [dict(id="sb", kind="scoreboard", label="Score bar", box=list(OVERLAY_BOXES[0])),
         dict(id="clk", kind="clock", label="Clock digits", box=list(CLOCK_BOX)),
         dict(id="m1", kind="mask", label="Shift / timer box", box=list(OVERLAY_BOXES[1])),
         dict(id="m2", kind="mask", label="Red alliance box", box=list(OVERLAY_BOXES[2])),
         dict(id="m3", kind="mask", label="Blue alliance box", box=list(OVERLAY_BOXES[3])),
         dict(id="m4", kind="mask", label="Event banner", box=list(OVERLAY_BOXES[4]))]
    if insets:
        R += [dict(id="sr", kind="station_red", label="Red station cam", box=list(STATION_RECTS["red"])),
              dict(id="sbl", kind="station_blue", label="Blue station cam", box=list(STATION_RECTS["blue"]))]
    return R


def default_doc():
    """A layout.json equivalent to the hard-coded behaviour (2026wasam): wide upload + inset upload."""
    return {"version": 1, "processing_source": "wide", "station_source": "program",
            "sources": {"wide": dict(W=REF_W, H=REF_H, role="wide", regions=first2026_regions(False)),
                        "program": dict(W=REF_W, H=REF_H, role="dynamic", regions=first2026_regions(True))}}


# ---------------------------------------------------------------- file lookup
def data_dir():
    return os.environ.get("DATA", "/data")


def event_dirs(ev):
    """Where an event's layout/calibration may live: data dir first, then the repo's events/."""
    return [os.path.join(data_dir(), "events", ev), os.path.join(REPO, "events", ev)]


_EVKEY = re.compile(r"^(\d{4}[a-z][a-z0-9]*)(?:_|$)")


def event_key_from_path(p):
    """'.../2026wasam_qm17_abc.mp4' or '.../events/2026wasam/camera.json' -> '2026wasam' (or None)."""
    if not p: return None
    p = os.path.abspath(str(p))
    m = _EVKEY.match(os.path.basename(p))
    if m: return m.group(1)
    d = os.path.basename(os.path.dirname(p))
    m = _EVKEY.match(d)
    return m.group(1) if m else None


class SourceLayout:
    def __init__(self, sid, d):
        self.id, self.d = sid, d
        self.W, self.H = int(d.get("W") or REF_W), int(d.get("H") or REF_H)
        self.role = d.get("role")
        self.regions = [r for r in d.get("regions", []) if r.get("box") and len(r["box"]) == 4
                        and not r.get("suggested")]

    def boxes(self, kinds, W=None, H=None):
        """Boxes of these kinds, scaled from the layout frame to W x H px (ints, clipped)."""
        kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
        W = W or self.W; H = H or self.H
        sx, sy = W / self.W, H / self.H
        out = []
        for r in self.regions:
            if r.get("kind") not in kinds: continue
            x0, y0, x1, y1 = [float(v) for v in r["box"]]
            x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
            b = (int(round(x0 * sx)), int(round(y0 * sy)), int(round(x1 * sx)), int(round(y1 * sy)))
            b = (max(0, b[0]), max(0, b[1]), min(W, b[2]), min(H, b[3]))
            if b[2] > b[0] and b[3] > b[1]: out.append(b)
        return out

    def box(self, kind, W=None, H=None):
        b = self.boxes(kind, W, H)
        return b[0] if b else None

    def has(self, kind):
        return any(r.get("kind") == kind for r in self.regions)


class Layout:
    def __init__(self, doc, path=None):
        self.doc, self.path = doc, path
        self.sources = {k: SourceLayout(k, v) for k, v in (doc.get("sources") or {}).items()}

    def processing(self):
        """Layout of the video the overhead tracker runs on."""
        s = self.doc.get("processing_source")
        if s in self.sources: return self.sources[s]
        for role in ("wide", "dynamic"):
            for v in self.sources.values():
                if v.role == role and not (v.has("station_red") or v.has("station_blue")): return v
        for v in self.sources.values():
            if v.role == "wide": return v
        return next(iter(self.sources.values())) if len(self.sources) == 1 else None

    def stations(self):
        """Layout of the video holding the driver-station insets."""
        s = self.doc.get("station_source")
        if s in self.sources and (self.sources[s].has("station_red") or self.sources[s].has("station_blue")):
            return self.sources[s]
        for v in self.sources.values():
            if v.has("station_red") or v.has("station_blue"): return v
        return None


def load(path):
    try:
        with open(path) as f:
            return Layout(json.load(f), path)
    except (OSError, ValueError):
        return None


def find(event_dir=None, cam_path=None, video_path=None, cam=None, ev=None):
    """The event's Layout or None.  Looks at $LAYOUT (a file), then <event_dir>/layout.json, then
    data/events/<ev>/layout.json and the repo's events/<ev>/layout.json, where the event key comes
    from `ev`, the camera JSON ("event" key, written by the calibration wizard), or the event/match key
    in the paths (worker conventions: events/<ev>/camera.json, cache/<ev>_<match>_<video>.mp4).
    LAYOUT=none disables layouts entirely (hard-coded behaviour)."""
    env = os.environ.get("LAYOUT")
    if env:
        return None if env.lower() in ("0", "none", "off") else load(env)
    cands = []
    for d in (event_dir, os.path.dirname(cam_path) if cam_path else None):
        if d: cands.append(os.path.join(d, "layout.json"))
    keys = [ev, (getattr(cam, "raw", None) or {}).get("event") if cam is not None else None,
            event_key_from_path(event_dir) if event_dir else None,
            event_key_from_path(cam_path), event_key_from_path(video_path)]
    for k in keys:
        if k:
            cands += [os.path.join(d, "layout.json") for d in event_dirs(k)]
    seen = set()
    for p in cands:
        p = os.path.abspath(p)
        if p in seen: continue
        seen.add(p)
        if os.path.exists(p):
            L = load(p)
            if L is not None: return L
    return None


def save_doc(event_dir, doc):
    os.makedirs(event_dir, exist_ok=True)
    p = os.path.join(event_dir, "layout.json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------- pipeline accessors
def clock_box(L, W=REF_W, H=REF_H, default=CLOCK_BOX):
    s = L.processing() if L else None
    b = s.box("clock", W, H) if s else None
    return tuple(b) if b else tuple(default)


def overlay_boxes(L, W=REF_W, H=REF_H):
    """Graphics to ignore in the processing video, W x H px; None = no layout (caller's default)."""
    s = L.processing() if L else None
    if s is None: return None
    return s.boxes(("scoreboard", "clock", "mask", "inset", "station_red", "station_blue"), W, H)


def detect_masks(L, W=REF_W, H=REF_H):
    """Picture-in-picture areas inside the processing video (no robots there); [] without a layout."""
    s = L.processing() if L else None
    return s.boxes(("inset", "station_red", "station_blue"), W, H) if s else []


def field_box(L, W=REF_W, H=REF_H):
    s = L.processing() if L else None
    return s.box("field", W, H) if s else None


def station_rects(L):
    """{"red": box, "blue": box} in the station video's own pixels (None = hard-coded default)."""
    s = L.stations() if L else None
    if s is None: return None
    out = {}
    for w in ("red", "blue"):
        b = s.box("station_" + w)
        if b: out[w] = tuple(b)
    return out or None


def station_clock_box(L, default=CLOCK_BOX):
    s = L.stations() if L else None
    b = s.box("clock") if s else None
    return tuple(b) if b else tuple(default)


def sync_region(L, default=SYNC_REGION):
    """Area the station (program) video shares pixel-for-pixel with the wide video: the band above the
    insets when they sit at the bottom (2026 FIRST: y < 700), else the default."""
    s = L.stations() if L else None
    if s is None: return tuple(default)
    ins = s.boxes(("station_red", "station_blue", "inset"))
    if not ins: return tuple(default)
    top = min(b[1] for b in ins)
    if top < s.H * 0.4: return tuple(default)
    return (0, 0, s.W, max(1, top - 8))


# ---------------------------------------------------------------- frames
YTDLP = os.environ.get("YTDLP", "yt-dlp")
_RESOLVED = {}


def is_remote(src):
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", str(src)))


def public_url(url):
    """SSRF guard (live/sources.py safe_public_url): http(s) only, every resolved address public."""
    try:
        import sources as _S
    except ImportError:                                    # imported as a package module
        from . import sources as _S
    return _S.safe_public_url(url)


def local_media(src):
    """A local media path as an absolute path: never an option ('-x') nor an ffmpeg protocol ('concat:...')."""
    src = str(src)
    if not src or "\n" in src or "\0" in src: raise ValueError("bad media path")
    return os.path.abspath(src)


def resolve_media(url, max_age=1800):
    """A direct media URL ffmpeg can read for a YouTube/Twitch page URL (yt-dlp -4 -g), cached.
    Direct .mp4/.m3u8 URLs and local paths pass through (URLs only to public hosts; paths made absolute)."""
    if not is_remote(url):
        return local_media(url)
    public_url(url)
    if re.search(r"\.(mp4|m3u8|ts|mkv|webm)(\?|$)", url):
        return url
    hit = _RESOLVED.get(url)
    if hit and time.time() - hit[0] < max_age: return hit[1]
    r = subprocess.run([YTDLP, "-4", "--no-warnings", "-g", "-f",
                        "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]/b", "--", url],
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in r.stdout.strip().split("\n") if l.startswith("http")]
    if r.returncode != 0 or not lines:
        raise RuntimeError(f"could not resolve {url}: {r.stderr.strip()[-300:]}")
    public_url(lines[0])                                   # the extractor's media URL: public too
    _RESOLVED[url] = (time.time(), lines[0])
    return lines[0]


def duration(src):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            "-i", resolve_media(src)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except Exception:
        return None


def grab_frame(src, t=None, timeout=90, _retry=True):
    """One BGR frame at t seconds (None/live: the next frame) from a file or URL."""
    if _retry and is_remote(src):
        try:
            return grab_frame(src, t, timeout, False)
        except Exception:
            _RESOLVED.pop(src, None)                  # stale / IP-bound media URL: resolve again
            return grab_frame(src, t, timeout, False)
    import cv2
    media = resolve_media(src)
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if t is not None: cmd += ["-ss", f"{max(0.0, float(t)):.3f}"]
    cmd += ["-i", media, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"no frame at t={t} from {src}: {r.stderr.decode(errors='replace')[-300:]}")
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def grab_frames(src, times):
    """[(t, BGR)] for each time that decodes (files decode with PyAV in one pass; URLs one ffmpeg seek each)."""
    times = sorted(float(t) for t in times)
    out = []
    if not is_remote(src) and os.path.exists(src):
        try:
            import av
            c = av.open(local_media(src)); s = c.streams.video[0]
            for t in times:
                c.seek(int(max(0.0, t - 1.0) / s.time_base), stream=s)
                for f in c.decode(s):
                    if f.time is not None and f.time >= t - 1e-3:
                        out.append((t, f.to_ndarray(format="bgr24"))); break
            c.close()
            return out
        except Exception:
            out = []
    errs, todo = [], list(times)
    for attempt in range(2):
        left = []
        for t in todo:
            try:
                out.append((t, grab_frame(src, t)))
            except Exception as e:
                errs.append(str(e)[-200:]); left.append(t)
        if not left: break
        _RESOLVED.pop(src, None)                      # expired / IP-bound media URL: resolve again once
        todo = left
    if todo and len(out) < max(3, len(times) // 2) and is_remote(src) and not re.search(r"\.m3u8|/live", src):
        # streaming keeps failing (YouTube 403s): download the video once (yt-dlp -4) and decode locally
        local = cached_download(src)
        if local:
            got = dict(out)
            return sorted(list(got.items()) + [x for x in grab_frames(local, todo) if x[0] not in got], key=lambda x: x[0])
    if todo and len(out) < 3:
        raise RuntimeError(f"{len(todo)} of {len(times)} frame grabs failed: {errs[-1]}")
    return sorted(out, key=lambda x: x[0])


def cached_download(url, max_mb=1500):
    """Download a (non-live) video once to $DATA/cache/srcvid/ with yt-dlp -4; returns the path or None."""
    import hashlib
    d = os.path.join(data_dir(), "cache", "srcvid"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, hashlib.sha1(url.encode()).hexdigest()[:16] + ".mp4")
    if os.path.exists(p): return p
    public_url(url)
    r = subprocess.run([YTDLP, "-4", "-q", "--no-warnings", "--no-playlist", "-f",
                        "bv*[height<=1080][ext=mp4]/bv*[height<=1080]", "--max-filesize", f"{max_mb}M",
                        "-o", p + ".part.mp4", "--force-overwrites", "--", url], capture_output=True, text=True, timeout=1800)
    if r.returncode == 0 and os.path.exists(p + ".part.mp4"):
        os.replace(p + ".part.mp4", p); return p
    return None


def grab_dense(src, t0, seconds=12.0, hz=10.0):
    """Frames at `hz` over [t0, t0 + seconds] (for clock-tick detection)."""
    import cv2
    if not is_remote(src) and os.path.exists(src):
        import av
        c = av.open(local_media(src)); s = c.streams.video[0]; s.thread_type = "AUTO"
        c.seek(int(max(0.0, t0 - 1.0) / s.time_base), stream=s)
        out, nxt = [], t0
        for f in c.decode(s):
            if f.time is None or f.time < nxt - 1e-4: continue
            if f.time > t0 + seconds: break
            out.append((float(f.time), f.to_ndarray(format="bgr24"))); nxt += 1.0 / hz
        c.close()
        return out
    media = resolve_media(src)
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t0:.2f}", "-i", media, "-t", f"{seconds:.2f}",
                        "-vf", f"fps={hz}", "-f", "image2pipe", "-vcodec", "bmp", "-"], capture_output=True, timeout=300)
    data, out, i, k = r.stdout, [], 0, 0
    while i + 54 <= len(data) and data[i:i + 2] == b"BM":
        n = int.from_bytes(data[i + 2:i + 6], "little")
        im = cv2.imdecode(np.frombuffer(data[i:i + n], np.uint8), cv2.IMREAD_COLOR)
        out.append((t0 + k / hz, im)); i += n; k += 1
    return out


# ---------------------------------------------------------------- suggestions
def _boxes_from_mask(m, min_area, max_frac=0.5, pad=2, merge_gap=6):
    import cv2
    H, W = m.shape
    if merge_gap:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (merge_gap, merge_gap)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < min_area or w * h > max_frac * W * H: continue
        out.append([int(max(0, x - pad)), int(max(0, y - pad)), int(min(W, x + w + pad)), int(min(H, y + h + pad))])
    return out


def suggest_overlays(frames, still_thr=4, min_area_frac=0.0008):
    """Broadcast graphics = pixels that stay (nearly) identical across frames spread over minutes while
    the camera picture changes (graphics are composited: their pixels repeat exactly, camera pixels carry
    noise, motion and compression flicker), with crisp edges and a roughly rectangular footprint.
    frames: >= 6 BGR frames of one source, ideally 5-20 s apart during matches.
    -> [{kind, box, score, label}] (scoreboard = the biggest box in the top third, the rest mask)."""
    import cv2
    if len(frames) < 3: return []
    G = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.int16) for f in frames])
    H, W = G.shape[1:]
    still = ((G.max(0) - G.min(0)) <= still_thr).astype(np.uint8)
    med = np.median(np.stack(frames), 0).astype(np.uint8)
    ed = cv2.Canny(cv2.cvtColor(med, cv2.COLOR_BGR2GRAY), 60, 160) > 0
    m = cv2.morphologyEx(still, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < min_area_frac * W * H or w * h > 0.35 * W * H: continue
        fill = a / float(w * h); edge = float(ed[y:y + h, x:x + w].mean())
        if edge < 0.03 or fill < 0.25: continue
        if fill < 0.45:
            # irregular (graphic merged with a static patch of scenery): keep the edge-dense core
            core = (lab[y:y + h, x:x + w] == i) & cv2.dilate(ed[y:y + h, x:x + w].astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
            rows = np.where(core.mean(1) > 0.25)[0]; cols = np.where(core.mean(0) > 0.25)[0]
            if len(rows) < 8 or len(cols) < 8: continue
            x, y, w, h = x + cols.min(), y + rows.min(), cols.max() - cols.min() + 1, rows.max() - rows.min() + 1
        out.append(dict(kind="mask", box=[int(max(0, x - 2)), int(max(0, y - 2)), int(min(W, x + w + 2)), int(min(H, y + h + 2))],
                        score=round(min(1.0, fill * (0.5 + 5 * edge)), 3)))
    tops = [o for o in out if o["box"][3] < H / 3]
    if tops:
        sb = max(tops, key=lambda o: (o["box"][2] - o["box"][0]) * (o["box"][3] - o["box"][1]))
        sb["kind"] = "scoreboard"; sb["label"] = "Score bar"
    for o in out:
        o.setdefault("label", "Graphic")
    return out


def suggest_clock(dense, within=None, block=8):
    """The match clock = the block that changes once per second.  dense: [(t, BGR)] at ~10 Hz over
    >= 8 s of a running match; within: optional (x0, y0, x1, y1) to search (the score bar).
    -> {kind: "clock", box, score} or None.  Box = the 1 Hz seconds digit grown over the neighbouring
    digit cells that also tick (tens of seconds / minutes) and the dark/light ink around them."""
    import cv2
    if len(dense) < 30: return None
    ts = np.array([t for t, _ in dense])
    G = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in dense]).astype(np.int16)
    H, W = G.shape[1:]
    x0, y0, x1, y1 = within or (0, 0, W, H)
    g = G[:, y0:y1, x0:x1]
    h, w = (g.shape[1] // block) * block, (g.shape[2] // block) * block
    g = g[:, :h, :w].reshape(len(G), h // block, block, w // block, block)
    d = np.abs(np.diff(g, axis=0)).mean((2, 4))                     # (T-1, by, bx) mean change per block
    ch = d > 12
    # per block: change events (rising edges), their spacing
    best, score = None, np.zeros(ch.shape[1:])
    for by in range(ch.shape[1]):
        for bx in range(ch.shape[2]):
            e = np.where(ch[:, by, bx])[0]
            if len(e) < 5: continue
            ev = e[np.r_[True, np.diff(e) > 2]]                         # merge consecutive frames of one change
            if len(ev) < 5: continue
            iv = np.diff(ts[ev + 1])
            ok = np.abs(iv - 1.0) < 0.15
            span = ts[-1] - ts[0]
            score[by, bx] = ok.mean() * min(1.0, len(ev) / max(1.0, span - 1.0))
    if score.max() < 0.6: return None
    import cv2 as _cv
    _, lab = _cv.connectedComponents((score >= 0.6).astype(np.uint8), connectivity=8)
    by, bx = np.unravel_index(int(np.argmax(score)), score.shape)
    ys, xs = np.where(lab == lab[by, bx])
    cy0, cy1 = ys.min(), ys.max()
    px0, px1 = x0 + xs.min() * block, x0 + (xs.max() + 1) * block          # seconds digit(s), full-res px
    py0, py1 = y0 + cy0 * block, y0 + (cy1 + 1) * block
    # the clock's background box: walk left/right along the rows just above and below the digits
    # until the colour leaves the box background (white box of the FIRST overlay; any colour works)
    med = np.median(np.stack([f for _, f in dense[::max(1, len(dense) // 15)]]), 0).astype(np.int16)
    ra, rb = med[max(0, py0 - 4)], med[min(H - 1, py1 + 3)]
    bgc = np.median(np.r_[ra[px0:px1], rb[px0:px1]], 0)
    inbox = (np.abs(ra - bgc).max(1) < 45) & (np.abs(rb - bgc).max(1) < 45)
    dw = max(block, px1 - px0)
    l = px0
    while l - 1 >= max(0, px0 - 6 * dw) and inbox[l - 1]: l -= 1
    r = px1
    while r + 1 < min(W, px1 + 2 * dw) and inbox[r + 1]: r += 1
    box = [l + 2, py0 - 3, r - 1, py1 + 3]
    return dict(kind="clock", box=[int(v) for v in box], score=round(float(score.max()), 3), label="Clock digits")


def suggest_insets(frames_a, frames_b, thr=28, min_area_frac=0.01):
    """Picture-in-picture rectangles in source A that the clean source B doesn't have (e.g. "Match N"
    program vs "Wide Match N"): median frames of both (robots/balls average out, the static field
    agrees) -> large differing areas -> rectangles snapped to the inset borders.
    -> [{kind: "inset", box, score, label}] (the user labels which is the red / blue station)."""
    import cv2
    A = np.median(np.stack(frames_a), 0).astype(np.uint8)
    B = np.median(np.stack(frames_b), 0).astype(np.uint8)
    if A.shape != B.shape:
        B = cv2.resize(B, (A.shape[1], A.shape[0]), interpolation=cv2.INTER_AREA)
    H, W = A.shape[:2]
    d = cv2.GaussianBlur(np.abs(A.astype(np.int16) - B.astype(np.int16)).max(2).astype(np.uint8), (9, 9), 0)
    m = (d > thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    boxes = _boxes_from_mask(m, min_area_frac * W * H, max_frac=0.6, pad=0, merge_gap=0)
    out = []
    for b in boxes:
        b = _snap_rect(A, b)
        x0, y0, x1, y1 = b
        fill = float(m[y0:y1, x0:x1].mean())
        if fill < 0.5: continue
        out.append(dict(kind="inset", box=b, score=round(fill, 3), label="Inset"))
    return _label_sides(out, W)


def _snap_rect(img, b, search=14):
    """Move each side of a rough rectangle to the strongest straight edge nearby."""
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = g.shape
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)); gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    x0, y0, x1, y1 = b
    def best(prof, c, lo, hi):
        lo, hi = max(lo, c - search), min(hi, c + search)
        if hi <= lo: return c
        seg = prof[lo:hi]
        k = int(np.argmax(seg))
        return lo + k if seg[k] > 2.5 * (np.median(prof[max(0, c - 4 * search):c + 4 * search]) + 1) else c
    ys, xs = slice(y0, y1), slice(x0, x1)
    colp = gx[ys].mean(0); rowp = gy[:, xs].mean(1)
    x0n = 0 if x0 <= search else best(colp, x0, 0, W)
    x1n = W if x1 >= W - search else best(colp, x1, 0, W) + 1
    y0n = 0 if y0 <= search else best(rowp, y0, 0, H)
    y1n = H if y1 >= H - search else best(rowp, y1, 0, H) + 1
    return [int(x0n), int(y0n), int(x1n), int(y1n)]


def _label_sides(out, W):
    """Guess station sides: the inset nearest the left/right edge -- labelled only as a hint."""
    low = [o for o in out]
    if len(low) >= 2:
        l = min(low, key=lambda o: o["box"][0]); r = max(low, key=lambda o: o["box"][2])
        if l is not r:
            l["label"] = "Inset (left) - station camera?"; r["label"] = "Inset (right) - station camera?"
    return out


def suggest_insets_edges(frames, min_len_frac=0.18, persist=0.8):
    """Insets from one source: straight horizontal / vertical borders present in (almost) every frame
    while the content on both sides changes.  -> [{kind: "inset", box, score}]"""
    import cv2
    if len(frames) < 4: return []
    H, W = frames[0].shape[:2]
    E = np.zeros((H, W), np.float32)
    for f in frames:
        E += cv2.Canny(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 50, 150) > 0
    pe = (E / len(frames)) >= persist
    G = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.int16) for f in frames])
    moving = (G.max(0) - G.min(0)) > 25
    mv = cv2.dilate(moving.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    pe &= mv                                                       # borders between changing content
    hl = cv2.morphologyEx(pe.astype(np.uint8), cv2.MORPH_OPEN, np.ones((1, int(W * min_len_frac)), np.uint8))
    vl = cv2.morphologyEx(pe.astype(np.uint8), cv2.MORPH_OPEN, np.ones((int(H * min_len_frac), 1), np.uint8))
    rows = [y for y in range(H) if hl[y].sum() > W * min_len_frac]
    cols = [x for x in range(W) if vl[:, x].sum() > H * min_len_frac]
    def groups(v):
        g = []
        for x in v:
            if g and x - g[-1][-1] <= 3: g[-1].append(x)
            else: g.append([x])
        return [int(np.mean(a)) for a in g]
    rows, cols = groups(rows), groups(cols)
    ys = sorted(set([0] + rows + [H])); xs = sorted(set([0] + cols + [W]))
    out = []
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            if (x1 - x0) * (y1 - y0) < 0.02 * W * H or (x1 - x0) * (y1 - y0) > 0.5 * W * H: continue
            # needs a detected border on at least 3 sides (frame edges count)
            sides = sum([x0 == 0 or x0 in cols, x1 == W or x1 in cols, y0 == 0 or y0 in rows, y1 == H or y1 in rows])
            inner = sum([x0 in cols, x1 in cols, y0 in rows, y1 in rows])
            if sides == 4 and inner >= 2:
                out.append(dict(kind="inset", box=[x0, y0, x1, y1], score=round(inner / 4, 2), label="Inset"))
    return _label_sides(out, W)


def suggest_field(frame, cam=None):
    """Field area: the calibrated camera's projected field box (+ robot height) if available, else the
    tightest box holding 90% (per axis) of the yellow fuel below the top graphics (rough: confirm it)."""
    import cv2
    H, W = frame.shape[:2]
    if cam is not None:
        try:
            x0, y0, x1, y1 = cam.roi()
            return dict(kind="field", box=[int(x0), int(y0), int(x1), int(y1)], score=1.0, label="Field (from calibration)")
        except Exception:
            pass
    # 2026: the carpet is strewn with yellow fuel -> the densest big cluster of yellow = the field
    b, g, r = [frame[..., i].astype(np.int16) for i in range(3)]
    yel = ((np.minimum(r, g) - b - np.maximum(np.abs(r - g) - 30, 0)) > 60).astype(np.uint8)
    yel = cv2.morphologyEx(yel, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ys, xs = np.where(yel > 0)
    keep = ys > H * 0.15
    ys, xs = ys[keep], xs[keep]
    if len(xs) < 200: return None
    def span(v, frac=0.9):
        v = np.sort(v); k = int(frac * len(v))
        i = int(np.argmin(v[k:] - v[:len(v) - k]))
        return v[i], v[i + k - 1]
    x0, x1 = span(xs); y0, y1 = span(ys)
    padx, pady = 0.06 * (x1 - x0), 0.25 * (y1 - y0)
    return dict(kind="field", box=[int(max(0, x0 - padx)), int(max(0, y0 - pady)), int(min(W, x1 + padx)), int(min(H, y1 + 0.3 * pady))],
                score=0.5, label="Field (fuel-spread estimate)")


def suggest_all(frames, dense=None, other=None, cam=None):
    """Everything we can guess for one source.  frames: [BGR] spread over time; dense: [(t, BGR)] at ~10 Hz
    during a match; other: [BGR] of a clean (no-inset) source of the same matches; cam: camera.Camera."""
    out = []
    ov = suggest_overlays(frames)
    out += ov
    if dense:
        sb = [o for o in ov if o["kind"] == "scoreboard"]
        c = suggest_clock(dense, tuple(sb[0]["box"]) if sb else None) or suggest_clock(dense)
        if c: out.append(c)
    if other:
        out += suggest_insets(frames, other)
    else:
        out += suggest_insets_edges(frames)
    f = suggest_field(frames[len(frames) // 2], cam)
    if f: out.append(f)
    for i, o in enumerate(out):
        o["id"] = f"s{i}"; o["suggested"] = True
    return out


if __name__ == "__main__":
    import argparse, cv2
    ap = argparse.ArgumentParser(description="layout tools")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("suggest"); a.add_argument("src"); a.add_argument("--t0", type=float, default=30)
    a.add_argument("--t1", type=float, default=150); a.add_argument("--n", type=int, default=12)
    a.add_argument("--other"); a.add_argument("--dense", type=float, default=None); a.add_argument("--out")
    a = sub.add_parser("default"); a.add_argument("out")
    g = ap.parse_args()
    if g.cmd == "default":
        json.dump(default_doc(), open(g.out, "w"), indent=1); print("wrote", g.out)
    elif g.cmd == "suggest":
        ts = np.linspace(g.t0, g.t1, g.n)
        fr = [f for _, f in grab_frames(g.src, ts)]
        ot = [f for _, f in grab_frames(g.other, ts)] if g.other else None
        dn = grab_dense(g.src, g.dense) if g.dense is not None else None
        res = suggest_all(fr, dn, ot)
        print(json.dumps(res, indent=1))
        if g.out:
            im = fr[len(fr) // 2].copy()
            for o in res:
                x0, y0, x1, y1 = o["box"]
                col = {"scoreboard": (0, 200, 255), "clock": (0, 255, 0), "inset": (255, 0, 255), "field": (255, 200, 0)}.get(o["kind"], (0, 0, 255))
                cv2.rectangle(im, (x0, y0), (x1, y1), col, 3)
                cv2.putText(im, o["kind"], (x0 + 4, y0 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
            cv2.imwrite(g.out, im)
