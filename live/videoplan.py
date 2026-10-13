"""Per-match video plan: which upload the tracker uses for every match, picked automatically.

Why: TBA links several uploads per match (FIRST HQ "Qualification N - <event>", the district's "Match N"
program feed with station-cam insets, "Wide Match N" for part of the event, "Playoff Match N", "Final N",
"Final Tiebreaker", ...).  Grouping them by title ("families") and asking an admin to give every family a role
does not scale and breaks when a family only covers part of the event.  Instead, every candidate video of a
match is looked at once (a few low-res frames, no download) and classified by what it shows:

  wide       fixed overhead / wide field camera: no camera cuts, (almost) no global motion -- what the tracker
             needs
  stations   the same fixed program view with the two driver-station camera insets (FIRST Washington "Match N")
  broadcast  a switching program feed (camera cuts / pans): trackable only while the overhead camera is on air
             ("limited tracking")

Per match the plan picks: the best `wide` video, else the best `broadcast` (limited) one, else a `stations` one
(limited: the insets hide the near corners); a `stations` video for the station-camera pass; and the livestream
clip when the recorder cut one.  Identical uploads (same duration +-1 s and the same perceptual hashes) are
deduplicated; a title whose number names another match is not used for this one.  Admin overrides (pin / exclude
per match, a disabled source = an excluded family) are the exception.

Files
  DATA/cache/vclass.json                  {yt id: classification}  (global: a video is classified once)
  DATA/events/<ev>/video_plan.json        the plan (built by the worker every poll, by the Video page's
                                          "Check videos" job, or `python live/videoplan.py build DATA EV`)
    {"version": 1, "event", "built_at", "videos": {id: {title, channel, duration, height, type, conf, why,
       dup_of, title_key, matches: [...], families: [...]}},
     "groups": {gid: {id, type, channel, label, n, videos: {match: id}}},   # one layout per group (Layout tab)
     "matches": {key: {label, candidates: [id...], track: id|None, track_type, limited, stations: id|None,
                       live: {...}|None, dups: n, pinned: bool, problems: [..]}},
     "summary": {n, wide, broadcast, stations, live, limited, missing, pending, dups}}

Overrides live in sources.json (written by the API): "match_overrides": {key: {"pin": id, "pin_stations": id,
"exclude": [id, ...]}}; a disabled per-match source excludes its videos everywhere.

Classification needs numpy + cv2 + PyAV and yt-dlp (worker image); the plan itself (choose(), build_plan(),
candidates for the worker) is stdlib only so the API container can read and explain it.
"""
import collections, json, os, re, subprocess, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
YTDLP = os.environ.get("YTDLP", "yt-dlp")
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TYPES = ("wide", "broadcast", "stations")
CLASSIFIER = 2                       # bump when the heuristics change: older cache entries are redone
MAX_MATCH_S = 600                    # longer uploads are not per-match videos (whole streams, recaps)
MIN_MATCH_S = 100                    # shorter: a clip / highlight, not a whole match


def _ipv4():
    return ["-4"] if os.environ.get("YT_FORCE_IPV4", "1") != "0" else []


def _jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _jsave(p, obj, indent=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp{os.getpid()}{threading.get_ident()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent, separators=None if indent else (",", ":"))
    os.replace(tmp, p)


# ================================================================ titles -> match keys
_NUM = r"(\d{1,3})"


def parse_title(title):
    """Video title -> (level, number) or None.  level: "qm", "sf" (double-elimination playoff match number),
    "f" (final number; "Final Tiebreaker" = 3) or "any" (a bare "Match N" / "Wide Match N": qualification
    numbering for the district uploads, but it may also be a playoff).  Handles the FIRST / district naming:
       "Qualification 17 - <event>", "Qual 17", "Q17", "Match 17", "Wide Match 17", "Playoff Match 5",
       "Match 5 (R2) - <event>", "Upper/Lower Bracket ... Match 5", "Semifinal 3", "Final 2", "Finals 2",
       "Final Tiebreaker", "Einstein ..." (None)."""
    t = re.sub(r"\s+", " ", (title or "").lower()).strip()
    parts = re.split(r"\s[-|\u2013\u2014]\s", t)                    # drop " - PNW District Bonney Lake Event"
    t = " ".join([parts[0]] + [x for x in parts[1:] if re.search(r"match|round|bracket|final|qualif|playoff|tie", x)])
    if re.search(r"\btie-?breaker\b", t) and re.search(r"\bfinals?\b", t):
        return ("f", 3)
    m = re.search(r"\bfinals?\s*(?:match\s*)?" + _NUM + r"\b", t)
    if m and not re.search(r"\bsemi-?finals?\b|\bquarter-?finals?\b", t): return ("f", int(m.group(1)))
    m = re.search(r"\b(?:semi-?finals?|sf)\s*" + _NUM + r"\b", t)            # "Semifinal 2 Match 1": set 2
    if m: return ("sf", int(m.group(1)))
    if re.search(r"\b(?:playoffs?|elim(?:ination)?s?|bracket)\b", t):
        m = re.search(r"\bmatch\s*" + _NUM + r"\b", t) or \
            re.search(r"\b(?:playoffs?|elim(?:ination)?s?|bracket)\b\D*?" + _NUM + r"\b", t)
        if m: return ("sf", int(m.group(1)))
    m = re.search(r"\bmatch\s*" + _NUM + r"\s*\((?:r|round)\s*\d+\)", t)          # "Match 5 (R2)"
    if m: return ("sf", int(m.group(1)))
    m = re.search(r"\b(?:qualifications?|quals?|qm|q)\s*(?:match\s*)?" + _NUM + r"\b", t)
    if m: return ("qm", int(m.group(1)))
    m = re.search(r"^(?:wide\s+)?match\s*" + _NUM + r"\b", t)
    if m: return ("any", int(m.group(1)))
    return None


def key_parts(key):
    """'2026wabon_sf5m1' -> ("sf", 5); qm -> match number, sf -> set number, f -> match number."""
    s = key.split("_", 1)[-1]
    m = re.match(r"^qm(\d+)$", s)
    if m: return ("qm", int(m.group(1)))
    m = re.match(r"^(?:sf|qf|ef)(\d+)m\d+$", s)
    if m: return ("sf", int(m.group(1)))
    m = re.match(r"^f\d+m(\d+)$", s)
    if m: return ("f", int(m.group(1)))
    return None


def title_key(title, ev, keys=None):
    """Match key a title names (a bare "Match N" = qualification N), or None; with `keys`, only a real one."""
    p = parse_title(title)
    if not p: return None
    lv, n = p
    k = f"{ev}_qm{n}" if lv in ("qm", "any") else f"{ev}_sf{n}m1" if lv == "sf" else f"{ev}_f1m{n}"
    if keys and k not in set(keys): return None
    return k


def same_match(title, key, ev=None, keys=None):
    """True / False when the title names this / another match, None when it can't tell (no number, or a
    playoff title compared with a different playoff level: FIRST numbers double-elimination matches and
    finals inconsistently, TBA knows better)."""
    p, q = parse_title(title), key_parts(key)
    if not p or not q: return None
    (lv, n), (klv, kn) = p, q
    if lv == "any":
        return n == kn
    if lv == klv:
        return n == kn
    if lv in ("sf", "f") and klv in ("sf", "f"):
        return None
    return False


# ================================================================ metadata (yt-dlp -J, no download)
def probe_info(vid, timeout=120):
    """yt-dlp -J of a YouTube video -> (meta dict, low-res video-only format URL or None)."""
    if not isinstance(vid, str) or not YT_ID.match(vid): raise ValueError("not a YouTube id")
    r = subprocess.run([YTDLP, *_ipv4(), "-J", "--no-warnings", "--no-playlist", "--", f"https://www.youtube.com/watch?v={vid}"],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stderr.strip().split("\n") or [""])[-1][-240:] or "yt-dlp failed")
    j = json.loads(r.stdout)
    meta = dict(id=vid, title=j.get("title") or "", channel=j.get("channel") or j.get("uploader") or "",
                channel_url=j.get("channel_url") or j.get("uploader_url"), duration=float(j.get("duration") or 0),
                height=int(j.get("height") or 0), width=int(j.get("width") or 0),
                upload_date=j.get("upload_date"), live_status=j.get("live_status"))
    if not meta["height"]:
        hs = [f.get("height") or 0 for f in j.get("formats") or [] if f.get("vcodec") not in (None, "none")]
        meta["height"] = max(hs) if hs else 0
        ws = [f.get("width") or 0 for f in j.get("formats") or [] if (f.get("height") or 0) == meta["height"]]
        meta["width"] = max(ws) if ws else 0
    return meta, pick_format(j.get("formats") or [])


def pick_format(formats, lo=240, hi=360):
    """Smallest decent video-only format for classification: 240-360p, avc1 first (PyAV decodes it fast; the
    thresholds were tuned at that size), smallest first; then anything video-only near that size.
    -> (url, height, http headers) or None.  The headers matter: a URL extracted for one YouTube client only
    works with that client's User-Agent."""
    vo = [f for f in formats if f.get("url") and f.get("vcodec") not in (None, "none")
          and f.get("protocol", "https") in ("https", "http")]
    if not vo: return None
    def key(f):
        h = f.get("height") or 9999
        return (h > hi, h < lo, not (f.get("acodec") in (None, "none")), not str(f.get("vcodec", "")).startswith("avc1"),
                h if lo <= h <= hi else abs(h - lo), f.get("tbr") or 0)
    f = sorted(vo, key=key)[0]
    hdr = {k: v for k, v in (f.get("http_headers") or {}).items() if isinstance(v, str) and k.lower() in ("user-agent", "referer", "origin", "accept-language")}
    return f["url"], int(f.get("height") or 0), hdr


# ================================================================ frames over HTTP range requests
def _opener(v4=None):
    """urllib opener for media URLs: IPv4 only when YT_FORCE_IPV4 (googlevideo URLs are bound to the address
    yt-dlp resolved them from, which is IPv4 with -4: an IPv6 connection gets 403) and every redirect target
    re-checked with the SSRF guard."""
    import http.client, socket, ssl
    try:
        import sources as _S
    except ImportError:                       # pragma: no cover - package import
        from . import sources as _S
    v4 = (os.environ.get("YT_FORCE_IPV4", "1") != "0") if v4 is None else v4

    class V4Conn(http.client.HTTPSConnection):
        def connect(self):
            ip = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            sock = socket.create_connection((ip, self.port), self.timeout)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

    class V4Handler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(V4Conn, req, context=ssl.create_default_context())

    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _S.safe_public_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    hs = [SafeRedirect()] + ([V4Handler()] if v4 else [])
    return urllib.request.build_opener(*hs).open


def _S_safe(url):
    try:
        import sources as _S
    except ImportError:                       # pragma: no cover - package import
        from . import sources as _S
    return _S.safe_public_url(url)


class RangeFile:
    """Read-only, seekable file object over an HTTP(S) URL using Range requests (PyAV reads it); counts the
    bytes it fetched.  Blocks of `block` bytes are cached, so the demuxer's small reads cost one request per
    block; a failed request is retried twice.  Only public hosts (sources.safe_public_url)."""

    def __init__(self, url, block=128 * 1024, timeout=20, max_bytes=24 * 1024 * 1024, opener=None, headers=None, refresh=None):
        try:
            import sources as _S
        except ImportError:                       # pragma: no cover - package import
            from . import sources as _S
        _S.safe_public_url(url)
        self.url, self.block, self.timeout, self.max_bytes = url, block, timeout, max_bytes
        self.open = opener or _opener()
        self.headers = dict(headers or {})
        self.headers.setdefault("User-Agent", "Mozilla/5.0")
        self.pos, self.size, self.fetched, self.requests, self.errors = 0, None, 0, 0, 0
        self.refresh, self.refreshed = refresh, 0     # refresh() -> (url, headers): a fresh media URL after a 403
        self.cache = {}
        self._get(0)

    def _fetch(self, a):
        req = urllib.request.Request(self.url, headers=dict(self.headers, Range=f"bytes={a}-{a + self.block - 1}"))
        with self.open(req, timeout=self.timeout) as r:
            data = r.read(self.block + 1)
            m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range") or "")
            if m: self.size = int(m.group(1))
            elif getattr(r, "status", 206) == 200:                   # server ignored Range: whole body
                self.size = len(data); data = data[a:a + self.block]
        return data

    def _get(self, bi):
        if bi in self.cache: return self.cache[bi]
        if self.fetched > self.max_bytes: raise IOError("byte budget exceeded")
        a = bi * self.block
        if self.size is not None and a >= self.size: return b""
        for k in range(3):
            try:
                data = self._fetch(a); break
            except Exception as e:
                self.errors += 1
                if getattr(e, "code", None) == 403 and self.refresh and self.refreshed < 2:
                    # googlevideo now and then refuses a URL after a few requests: extract a fresh one
                    self.refreshed += 1
                    try:
                        u, hdr = self.refresh()
                        _S_safe(u)
                        self.url = u; self.headers = dict(hdr or {}, **{"User-Agent": (hdr or {}).get("User-Agent") or "Mozilla/5.0"})
                    except Exception:
                        pass
                if k == 2: raise
                time.sleep(0.5 * (k + 1))
        self.fetched += len(data); self.requests += 1
        if len(self.cache) > 96: self.cache.pop(next(iter(self.cache)))
        self.cache[bi] = data
        return data

    def read(self, n=-1):
        if self.size is not None and self.pos >= self.size: return b""
        if n is None or n < 0: n = (self.size or self.pos + self.block) - self.pos
        out = bytearray()
        while n > 0:
            bi, off = divmod(self.pos, self.block)
            d = self._get(bi)[off:off + n]
            if not d: break
            out += d; self.pos += len(d); n -= len(d)
        return bytes(out)

    def seek(self, off, whence=0):
        if whence == 65536: return self.size if self.size is not None else -1      # AVSEEK_SIZE
        if whence == 0: self.pos = off
        elif whence == 1: self.pos += off
        elif whence == 2: self.pos = (self.size or 0) + off
        return self.pos

    def tell(self):
        return self.pos

    def seekable(self):
        return True

    def readable(self):
        return True


def sample_times(duration, n=6):
    """n sample times inside the match part of an upload (skips most of the intro / results screen)."""
    d = float(duration or 0)
    if d <= 0: return [10.0 + 20 * i for i in range(n)]
    lo, hi = (0.12 * d, 0.85 * d) if d > 60 else (0.05 * d, 0.9 * d)
    if d < 400: lo, hi = max(lo, 15.0), min(hi, max(lo + 30.0, d - 20.0))
    return [round(lo + (hi - lo) * i / max(1, n - 1), 3) for i in range(n)]


def grab_samples(src, times, burst=6, width=320):
    """For each time: the keyframe at/before it + `burst` following frames (~0.2 s), grey, `width` px wide.
    src: a local path or a RangeFile.  -> ([(t, [grey frames])], frames decoded)"""
    import av, cv2
    c = av.open(src)
    s = c.streams.video[0]
    s.thread_type = "AUTO"
    out, decoded = [], 0
    try:
        for t in times:
            try:
                c.seek(int(max(0.0, t) / s.time_base), stream=s, any_frame=False, backward=True)
                fr, t0 = [], None
                for f in c.decode(s):
                    decoded += 1
                    if f.time is None: continue
                    if t0 is None: t0 = f.time
                    g = f.to_ndarray(format="gray")
                    h = int(round(g.shape[0] * width / g.shape[1]))
                    fr.append(cv2.resize(g, (width, h), interpolation=cv2.INTER_AREA))
                    if len(fr) > burst: break
            except Exception:
                continue                          # one unreadable sample: the others still count
            if fr: out.append((float(t0 if t0 is not None else t), fr))
    finally:
        c.close()
    return out, decoded


# ================================================================ features + classification
def phash(g):
    """64-bit DCT perceptual hash of a grey image -> 16 hex chars."""
    import numpy as np, cv2
    a = cv2.resize(g.astype(np.float32), (32, 32), interpolation=cv2.INTER_AREA)
    d = cv2.dct(a)[:8, :8].flatten()
    med = np.median(d[1:])
    bits = 0
    for i, v in enumerate(d):
        if v > med: bits |= 1 << i
    return f"{bits:016x}"


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _ncc(a, b):
    import numpy as np
    a = a.astype(np.float32) - a.mean(); b = b.astype(np.float32) - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum())) or 1.0
    return float((a * b).sum() / den)


def _small(g, w=96):
    import cv2
    h = max(8, int(round(g.shape[0] * w / g.shape[1])))
    return cv2.GaussianBlur(cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA), (3, 3), 0)


def _edges(g):
    import numpy as np, cv2
    e = cv2.Canny(g, 60, 160)
    return cv2.dilate(e, np.ones((3, 3), np.uint8)).astype(np.float32) / 255.0


def _shift(a, b):
    """Global translation between two grey frames (phase correlation) -> (dx, dy, response)."""
    import numpy as np, cv2
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(a.astype(np.float32), b.astype(np.float32), win)
    return float(dx), float(dy), float(resp)


INSET_BAND = (0.66, 0.9)          # rows inside both 2026 FIRST station-inset layouts (inset tops at 0.60 .. 0.66)
INSET_LO, INSET_HI = 0.58, 0.76   # border evidence: wide max 0.57, stations min 0.76 on the 2026wabon/wasam samples


def inset_score(frames, thr=12):
    """Evidence for two picture-in-picture insets in the bottom corners (driver-station cameras).  An inset's
    inner side is a vertical border between two unrelated pictures: an intensity step in (almost) every row of
    the lower band at one column, in every frame, on the left and on the right; the moving content of a fixed
    camera never makes such a column.  -> (score 0..1, {x_left, x_right, raw})"""
    import numpy as np
    if not frames: return 0.0, None
    H, W = frames[0].shape[:2]
    b0, b1 = int(INSET_BAND[0] * H), int(INSET_BAND[1] * H)
    per = []
    for g in frames:
        g = g.astype(np.float32)
        dx = np.zeros_like(g[b0:b1]); dx[:, 1:-1] = np.abs(g[b0:b1, 2:] - g[b0:b1, :-2])
        m = dx > thr
        per.append((m | np.roll(m, 1, axis=1)).mean(0))         # border may straddle two columns
    fx = np.median(np.stack(per), 0)
    l0, l1, r0, r1 = int(0.15 * W), int(0.45 * W), int(0.55 * W), int(0.88 * W)
    xl = l0 + int(np.argmax(fx[l0:l1])); xr = r0 + int(np.argmax(fx[r0:r1]))
    raw = float(min(fx[xl], fx[xr]))
    score = max(0.0, min(1.0, (raw - INSET_LO) / (INSET_HI - INSET_LO)))
    return round(score, 3), dict(x_left=round(xl / W, 3), x_right=round(xr / W, 3), raw=round(raw, 3))


def features(samples):
    """samples [(t, [grey frames])] -> feature dict for classify() (+ hashes for dedupe)."""
    import numpy as np
    keys = [fr[0] for _, fr in samples]
    n = len(keys)
    sm = [_small(k) for k in keys]
    ed = [_edges(_small(k, 160)) for k in keys]
    M = np.ones((n, n), np.float32)
    sims = []
    for i in range(n):
        for j in range(i + 1, n):
            M[i, j] = M[j, i] = _ncc(ed[i], ed[j]); sims.append(M[i, j])
    odd_idx = []
    if n >= 2:
        ref = int(np.argmax(M.sum(1)))
        odd_idx = [i for i in range(n) if i != ref and M[ref, i] < 0.35]
    # an intro / outro sample (first / last) from another camera is not a cut during the match
    edge_odd = [i for i in odd_idx if i in (0, n - 1)]
    mid_odd = [i for i in odd_idx if i not in (0, n - 1)]
    if len(odd_idx) > 2: edge_odd, mid_odd = [], odd_idx
    shifts, cuts = [], 0
    for i, (_, fr) in enumerate(samples):
        if i in odd_idx: continue
        s0 = [_small(f, 160) for f in fr]
        for a, b in zip(s0, s0[1:]):
            if _ncc(a, b) < 0.6: cuts += 1; continue
            dx, dy, _r = _shift(a, b)
            shifts.append(float(np.hypot(dx, dy)))
    good = [k for i, k in enumerate(keys) if i not in odd_idx] or keys
    ins, box = inset_score(good)
    th = [_small(k, 64) for k in keys]
    dh = [phash(np.clip(th[i + 1].astype(np.int16) - th[i].astype(np.int16) + 128, 0, 255).astype(np.uint8))
          for i in range(n - 1)]
    return dict(n=n, esim_med=round(float(np.median(sims)), 3) if sims else None,
                esim_min=round(float(np.min(sims)), 3) if sims else None,
                odd=len(mid_odd), edge_odd=len(edge_odd), cuts=cuts,
                motion=round(float(np.percentile(shifts, 75)), 3) if shifts else None,
                inset=ins, inset_box=box,
                times=[round(t, 2) for t, _ in samples], hashes=[phash(k) for k in keys], dhashes=dh)


ESIM_FIXED = 0.45      # median edge similarity between samples: fixed cameras 0.55-0.64, program feeds 0.05-0.4
MOTION_FIXED = 0.6     # px/frame at 320 px (75th percentile): fixed cameras <= 0.1


def classify(f, min_samples=4):
    """feature dict -> (type | None, confidence 0..1, why).  None: too few readable samples (try again later)."""
    if not f or f.get("n", 0) < min_samples:
        return None, 0.0, f"only {f.get('n', 0) if f else 0} readable samples"
    es, odd, mot, cuts, ins = f.get("esim_med") or 0, f.get("odd", 0), f.get("motion") or 0, f.get("cuts", 0), f.get("inset", 0)
    fixed = es >= ESIM_FIXED and odd == 0 and cuts == 0 and mot < MOTION_FIXED
    if ins >= 0.5 and (fixed or (es >= 0.35 and odd <= 1)):
        return "stations", round(min(1.0, 0.55 + ins * 0.45), 2), \
            f"driver-station insets in the bottom corners (border evidence {f['inset_box']['raw']:.2f})"
    if fixed:
        conf = 0.55 + min(0.25, (es - ESIM_FIXED) * 1.5) + min(0.2, (MOTION_FIXED - mot) * 0.4) - 0.3 * ins
        why = f"fixed camera, no cuts (edge similarity {es:.2f}, motion {mot:.2f} px/frame)"
        if f.get("edge_odd"): why += "; intro/outro from another camera"
        return "wide", round(max(0.5, min(1.0, conf)), 2), why
    why = []
    if odd: why.append(f"{odd} of {f['n']} samples from another camera")
    if cuts: why.append(f"{cuts} cut{'s' if cuts > 1 else ''} inside the sample bursts")
    if mot >= MOTION_FIXED: why.append(f"camera moves {mot:.1f} px/frame")
    if es < ESIM_FIXED: why.append(f"views differ (edge similarity {es:.2f})")
    conf = min(1.0, 0.5 + 0.15 * odd + 0.1 * cuts + (0.2 if mot >= 1.0 else 0) + (0.2 if es < 0.3 else 0))
    return "broadcast", round(conf, 2), "; ".join(why) or "dynamic view"


def classify_video(vid, log=print, n=6, burst=6, keep=None, probe=probe_info, opener=None):
    """One YouTube upload -> classification record (metadata + features + type).  Fetches only the byte
    ranges of n short bursts of a <=240p video-only stream (~0.5-2 MB), never the whole video."""
    t0 = time.time()
    meta, fmt = probe(vid)
    rec = dict(meta, classifier=CLASSIFIER, at=time.time())
    if meta.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        rec.update(type=None, why="a live stream, not an upload"); return rec
    if not fmt:
        rec.update(type=None, why="no video format"); return rec
    url, h, hdr = (tuple(fmt) + ({},))[:3]
    t1 = time.time()
    def refresh():
        f2 = probe(vid)[1]
        if not f2: raise RuntimeError("no format")
        return f2[0], (tuple(f2) + ({},))[2]
    rf = RangeFile(url, opener=opener, headers=hdr, refresh=refresh)
    samples, decoded = grab_samples(rf, sample_times(meta["duration"], n), burst=burst)
    t2 = time.time()
    if keep is not None: keep.extend(samples)
    f = features(samples) if samples else dict(n=0)
    ty, conf, why = classify(f)
    rec.update(type=ty, conf=conf, why=why,
               features={k: v for k, v in f.items() if k not in ("hashes", "dhashes", "times")},
               hashes=f.get("hashes"), dhashes=f.get("dhashes"), times=f.get("times"),
               probe=dict(height=h, bytes=rf.fetched, requests=rf.requests, errors=rf.errors, decoded=decoded,
                          meta_s=round(t1 - t0, 2), frames_s=round(t2 - t1, 2), total_s=round(time.time() - t0, 2)))
    return rec


# ================================================================ classification cache
def cache_path(data):
    return os.path.join(data, "cache", "vclass.json")


_CLOCK = threading.Lock()


def load_cache(data):
    return _jload(cache_path(data), {}) or {}


def save_cache(data, cache):
    with _CLOCK:
        cur = _jload(cache_path(data), {}) or {}
        cur.update(cache)                          # several writers (worker poll + Video page job): merge
        _jsave(cache_path(data), cur)
        cache.update(cur)


def needs_classify(rec, now=None):
    """No record, an old classifier, or a failed / partial try that is due for another one."""
    if not rec: return True
    if rec.get("classifier") != CLASSIFIER: return True
    if rec.get("type") is None and not rec.get("final"):
        return (now or time.time()) - rec.get("at", 0) > min(6 * 3600, 600 * 2 ** min(rec.get("tries", 1), 5))
    return False


# ================================================================ candidates per match
def _yt(v):
    return isinstance(v, str) and bool(YT_ID.match(v))


def tba_videos(m):
    out = []
    for v in m.get("videos") or []:
        if isinstance(v, dict) and v.get("type") == "youtube" and isinstance(v.get("key"), str):
            k = v["key"].split("?")[0].strip()
            if _yt(k) and k not in out: out.append(k)
    return out


# ================================================================ channel scan: uploads TBA does not link
CHAN_RE = re.compile(r"^https://www\.youtube\.com/(channel/UC[A-Za-z0-9_-]{22}|@[A-Za-z0-9._-]{1,100})$")


def list_channel(url, end=600, timeout=600):
    """yt-dlp --flat-playlist of a channel's uploads (newest first) -> [(id, title)]."""
    import sources as SRC
    SRC.safe_public_url(url)
    r = subprocess.run([YTDLP, *_ipv4(), "--no-warnings", "--flat-playlist", "--playlist-end", str(int(end)),
                        "--print", "%(id)s\t%(title)s", "--", url.rstrip("/") + "/videos"],
                       capture_output=True, text=True, timeout=timeout)
    out = []
    for line in r.stdout.split("\n"):
        v, _, t = line.partition("\t")
        if _yt(v): out.append((v, t))
    if r.returncode != 0 and not out:
        raise RuntimeError((r.stderr.strip().split("\n") or [""])[-1][-200:] or "yt-dlp failed")
    return out


def _suffix(t):
    """' - PNW District Bonney Lake Event' part of a title (lower case), '' without one."""
    parts = re.split(r"\s[-|\u2013\u2014]\s", (t or "").strip())
    return parts[-1].strip().lower() if len(parts) > 1 else ""


def scan_channels(data, ev, matches, recs, list_fn=None, near=4, margin=10, log=print, max_age=1800):
    """Uploads of the event's channels that TBA does not link (FIRST Washington posted "Wide Match N" for 67 of
    68 Bonney Lake quals, TBA linked 11).  A channel upload counts for match K when its title names K
    (parse_title) and it sits among this event's TBA-linked uploads of the same channel (within `margin` places of
    their span in the upload list), with the same " - <event>" title suffix when titles carry one.  When the span
    holds two uploads with that title (two events uploaded interleaved), only one within `near` places of an
    upload TBA links to K counts.  -> {match key: [ids]}, stored in events/<ev>/channel_scan.json."""
    list_fn = list_fn or list_channel
    evd = os.path.join(data, "events", ev)
    old = _jload(os.path.join(evd, "channel_scan.json"), {}) or {}
    if old.get("at") and time.time() - old["at"] < max_age:
        return old.get("matches") or {}
    keys = [m["key"] for m in matches or []]
    linked = {}                                       # TBA video id -> match key
    for m in matches or []:
        for v in tba_videos(m): linked.setdefault(v, m["key"])
    chans = {}
    for v, k in linked.items():
        r = recs.get(v) or {}
        cu = (r.get("channel_url") or "").rstrip("/")
        if CHAN_RE.match(cu): chans.setdefault(cu, set()).add(v)
    found, info = {}, {}
    for cu, vids in sorted(chans.items()):
        lst, end = [], 300
        try:
            while True:                                # deeper until every linked upload of the channel is in the list
                lst = list_fn(cu, end)
                have = {v for v, _ in lst}
                if vids <= have or len(lst) < end or end >= 3000: break
                if end >= 900 and not (vids & have): break      # not in this channel's upload list at all
                end *= 3
        except Exception as e:
            info[cu] = dict(error=str(e)[:200]); continue
        pos = {v: i for i, (v, _) in enumerate(lst)}
        lp = [pos[v] for v in vids if v in pos]
        if not lp:
            info[cu] = dict(listed=len(lst), linked=0, added=0); continue
        lo, hi = min(lp) - margin, max(lp) + margin
        sufs = collections.Counter(_suffix((recs.get(v) or {}).get("title")) for v in vids if v in pos)
        suf = sufs.most_common(1)[0][0] if sufs else ""
        window = [(i, v, t) for i, (v, t) in enumerate(lst) if lo <= i <= hi and v not in linked]
        same = collections.Counter(re.sub(r"\s+", " ", t.strip().lower()) for _, _, t in window)
        n_new = 0
        for i, v, t in window:
            k = title_key(t, ev, keys)
            if not k or _suffix(t) != suf: continue                 # another event's upload
            if same[re.sub(r"\s+", " ", t.strip().lower())] > 1 and \
                    not any(linked[w] == k and w in pos and abs(pos[w] - i) <= near for w in vids):
                continue
            L = found.setdefault(k, [])
            if v not in L: L.append(v); n_new += 1
        info[cu] = dict(listed=len(lst), linked=len(lp), added=n_new)
    _jsave(os.path.join(evd, "channel_scan.json"), {"at": time.time(), "matches": found, "channels": info}, indent=1)
    if log and found:
        per = ", ".join("%s: %d" % (c.rsplit("/", 1)[-1], d.get("added", 0)) for c, d in info.items())
        log(f"{ev}: channel scan found {sum(len(v) for v in found.values())} uploads TBA does not link ({per})")
    return found


def gather(matches, doc, scan=None):
    """-> ({match key: [video ids]}, {video id: {"families": [source ids], "via": ["tba", ...]}}).
    Candidates of a match: its TBA videos + every per-match source's video for that key (discovered families,
    pasted playlists / channels)."""
    cands, info = {}, {}
    def add(k, vid, via):
        if not _yt(vid): return
        L = cands.setdefault(k, [])
        if vid not in L: L.append(vid)
        d = info.setdefault(vid, {"families": [], "via": []})
        if via not in d["via"]: d["via"].append(via)
    keys = set()
    for m in matches or []:
        keys.add(m["key"])
        for vid in tba_videos(m): add(m["key"], vid, "tba")
    for s in (doc or {}).get("sources", []):
        if s.get("kind") != "per_match": continue
        for k, vid in (s.get("videos") or {}).items():
            if keys and k not in keys: continue
            add(k, vid, s["id"])
            fam = info[vid]["families"] if _yt(vid) else None
            if fam is not None and s["id"] not in fam: fam.append(s["id"])
    for k, L in (scan or {}).items():                                       # channel uploads TBA does not link
        if keys and k not in keys: continue
        for vid in L: add(k, vid, "channel")
    for k, ov in ((doc or {}).get("match_overrides") or {}).items():          # admin: extra videos for a match
        for vid in (ov or {}).get("add") or []:
            add(k, vid, "admin")
        for x in ("pin", "pin_stations"):
            if (ov or {}).get(x): add(k, ov[x], "admin")
    return cands, info


def duplicates(recs):
    """Identical uploads: same duration (+-1 s), same detected type, and at >= 4 sample times the same frame
    (pHash within 6 bits) and the same motion between samples (difference-image pHash within 10 bits).
    -> {video id: id of the copy kept (the first id in sorted order)}"""
    vids = sorted(v for v, r in recs.items() if r and r.get("hashes") and r.get("duration"))
    dup = {}
    for i, a in enumerate(vids):
        if a in dup: continue
        ra = recs[a]
        for b in vids[i + 1:]:
            if b in dup: continue
            rb = recs[b]
            if abs(ra["duration"] - rb["duration"]) > 1.0 or ra.get("type") != rb.get("type"): continue
            ta, tb = ra.get("times") or [], rb.get("times") or []
            ha, hb, da, db = ra["hashes"], rb["hashes"], ra.get("dhashes") or [], rb.get("dhashes") or []
            same = 0
            for k in range(min(len(ha), len(hb))):
                if ta and tb and k < len(ta) and k < len(tb) and abs(ta[k] - tb[k]) > 0.15: continue
                if hamming(ha[k], hb[k]) > 6: continue
                if k < len(da) and k < len(db) and hamming(da[k], db[k]) > 10: continue
                same += 1
            if same >= min(4, len(ha)):
                dup[b] = a
    return dup


TYPE_RANK = {"wide": 0, "stations": 1, "broadcast": 2}
LIMITED = {"stations": "the station-camera insets hide the near corners of the field",
           "broadcast": "camera cuts: robots are tracked only while the overhead camera is on air"}


def _rank(rec, prefer_channel=None):
    h = rec.get("height") or 0
    return (TYPE_RANK.get(rec.get("type"), 9), h < 720, -min(h, 1080), rec.get("channel") != prefer_channel,
            -(rec.get("conf") or 0), rec.get("id", ""))


def choose(key, vids, recs, info, ov=None, disabled=(), ev=None, keys=None, results=None):
    """One match's plan entry.  recs {id: classification}; info from gather(); ov the admin override
    {pin, pin_stations, exclude}; disabled: source ids switched off (their videos are excluded); results
    {video id: "wrong_video"} from processed matches."""
    ov = ov or {}
    excl = set(ov.get("exclude") or [])
    ev = ev or key.split("_", 1)[0]
    out, pending = [], 0
    for vid in vids:
        r = recs.get(vid) or {}
        c = dict(id=vid, title=r.get("title"), channel=r.get("channel"), duration=r.get("duration"),
                 height=r.get("height"), type=r.get("type"), conf=r.get("conf"), why=r.get("why"),
                 families=(info.get(vid) or {}).get("families", []), via=(info.get(vid) or {}).get("via", []))
        if not r or (r.get("type") is None and not r.get("final")): pending += 1; c["pending"] = True
        if r.get("dup_of"): c["dup_of"] = r["dup_of"]
        if r.get("type") == "stations" and (r.get("features") or {}).get("inset_box"):
            c["inset_box"] = r["features"]["inset_box"]
        ok = same_match(r.get("title"), key, ev, keys) if r.get("title") else None
        c["title_ok"] = ok
        why = None
        if vid in excl: why = "excluded for this match"
        elif any(f in disabled for f in c["families"]):
            why = "its source is switched off"
        elif ok is False: why = f"title names another match ({match_label(title_key(r.get('title'), ev) or '?')})"
        elif (results or {}).get(vid) == "wrong_video": why = "bumper numbers say it is another match"
        elif r.get("duration") and r["duration"] > MAX_MATCH_S: why = "too long for one match"
        elif r.get("duration") and r["duration"] < MIN_MATCH_S: why = "too short for a whole match"
        if why: c["excluded"] = why
        out.append(c)
    usable = [c for c in out if not c.get("excluded") and not c.get("dup_of") and c.get("type")]
    byid = {c["id"]: c for c in out}
    track = None
    pin = ov.get("pin")
    if pin and pin in byid and byid[pin].get("excluded") != "excluded for this match":
        track = byid[pin]
    elif usable:
        track = sorted(usable, key=lambda c: _rank(c))[0]
    st = None
    pst = ov.get("pin_stations")
    if pst and pst in byid:
        st = byid[pst]
    else:
        sts = [c for c in usable if c["type"] == "stations"]
        if sts:
            ch = track.get("channel") if track else None
            st = sorted(sts, key=lambda c: (c.get("channel") != ch, (c.get("height") or 0) < 1080,
                                            -(c.get("conf") or 0), c["id"]))[0]
    probs = []
    if pending and not track: probs.append("videos not checked yet")
    if not out: probs.append("no video linked yet")
    elif not track and not pending: probs.append("no usable video")
    if any(c.get("title_ok") is False for c in out): probs.append("a linked video's title names another match")
    ttype = track.get("type") if track else None
    return dict(candidates=out, track=track["id"] if track else None, track_type=ttype,
                limited=LIMITED.get(ttype) if track else None, stations=st["id"] if st else None,
                pinned=bool(pin and track and track["id"] == pin), pinned_stations=bool(pst and st and st["id"] == pst),
                dups=sum(1 for c in out if c.get("dup_of")), pending=pending, problems=probs)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "x"


TYPE_LABEL = {"wide": "Wide", "broadcast": "Broadcast", "stations": "Station cams"}
TYPE_ROLE = {"wide": "wide", "broadcast": "dynamic", "stations": "dynamic"}


def group_id(ty, channel):
    return f"auto-{ty}-{_slug(channel)}"


def groups(plan_matches, recs):
    """(type, channel) groups of the videos the plan uses: one layout per group on the Layout tab."""
    g = {}
    for k, e in plan_matches.items():
        for vid in (e.get("track"), e.get("stations")):
            r = recs.get(vid) if vid else None
            if not r or not r.get("type"): continue
            gid = group_id(r["type"], r.get("channel"))
            d = g.setdefault(gid, dict(id=gid, kind="per_match", provider="youtube", origin="auto", type=r["type"],
                                       role=TYPE_ROLE[r["type"]], channel=r.get("channel"), videos={}, enabled=True,
                                       label=f"{TYPE_LABEL[r['type']]} · {r.get('channel') or '?'}"))
            d["videos"].setdefault(k, vid)
    for d in g.values():
        some = sorted(d["videos"], key=_korder)
        mid = some[len(some) // 2]
        d["n"] = len(some)
        d["sample"] = dict(video=d["videos"][mid], match=mid, t=60.0)
    return g


def _korder(k):
    s = k.split("_", 1)[-1]
    lv = re.match(r"[a-z]+", s).group(0) if re.match(r"[a-z]+", s) else s
    return ({"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}.get(lv, 5), [int(x) for x in re.findall(r"\d+", s)])


def live_clips(ev):
    """{match key: livestream clip info} from the recorder's records (live/liveingest.py), {} without any."""
    try:
        import liveingest as LI
        lv = LI.LiveView(ev)
    except Exception:
        return {}
    out = {}
    for k, r in (lv.fast or {}).items():
        sid = (r.get("_meta") or {}).get("source")
        try:
            tr = lv.track(sid)
        except Exception:
            tr = dict(ok=False, reason="?")
        clip = LI.media_url(LI.clip_of(r)) if LI.clip_ok(r) else None
        out[k] = dict(record=r.get("id"), source=sid, clip=clip, trackable=bool(tr.get("ok")) and bool(clip),
                      note=tr.get("reason"), video_id=(r.get("_meta") or {}).get("video_id"))
    return out


def match_results(ev_dir):
    """{video id: "ok" | "wrong_video"} from processed match JSONs (the integrity check after processing)."""
    out = {}
    d = os.path.join(ev_dir, "matches")
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for f in names:
        if not f.endswith(".json"): continue
        r = _jload(os.path.join(d, f), {}) or {}
        vid = (r.get("video") or {}).get("id")
        if vid: out[vid] = "wrong_video" if r.get("wrong_video") else "ok"
    return out


def match_label(k):
    s = k.split("_", 1)[-1]
    m = re.match(r"^qm(\d+)$", s)
    if m: return f"Q{m.group(1)}"
    m = re.match(r"^sf(\d+)m(\d+)$", s)
    if m: return f"Playoff {m.group(1)}" + (f"-{m.group(2)}" if m.group(2) != "1" else "")
    m = re.match(r"^f(\d+)m(\d+)$", s)
    if m: return f"Final {m.group(2)}"
    return s


def load_scan(ev_dir):
    return (_jload(os.path.join(ev_dir, "channel_scan.json"), {}) or {}).get("matches") or {}


INSET_SURE = 0.78          # raw border evidence: above = station insets for sure; INSET_LO..INSET_SURE = ambiguous


def family(title):
    """'Wide Match 17' -> 'wide match #': uploads of one feed (used only to group, never to decide a type)."""
    t = re.sub(r"\s*\([^)]*\)", "", title or "").strip().lower()
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", t))


def refine(recs):
    """Event-level second look at fixed-view videos whose inset evidence is ambiguous: they show station insets
    when their inset borders sit where the event's clear station-cam videos have them, else they take the type
    of the clear videos of the same feed (same channel + title pattern).  -> {id: record (refined copy)}"""
    fx = lambda r: (r.get("features") or {}).get("inset_box") or {}
    sure = [r for r in recs.values() if r and r.get("type") == "stations" and (fx(r).get("raw") or 0) >= INSET_SURE]
    if sure:                                   # where this event's clear station-cam insets are (most common column)
        mode = lambda k: collections.Counter(round(fx(r)[k] * 200) for r in sure).most_common(1)[0][0] / 200
        mxl, mxr = mode("x_left"), mode("x_right")
    feed = collections.defaultdict(collections.Counter)
    for r in recs.values():
        if not r or r.get("type") not in ("wide", "stations"): continue
        raw = fx(r).get("raw") or 0
        if (r["type"] == "stations" and raw >= INSET_SURE) or (r["type"] == "wide" and raw < INSET_LO):
            feed[(r.get("channel"), family(r.get("title")))][r["type"]] += 1
    out = {}
    for v, r in recs.items():
        raw = fx(r).get("raw") if r else None
        if not r or r.get("type") not in ("wide", "stations") or raw is None or not (INSET_LO <= raw < INSET_SURE):
            out[v] = r; continue
        near = bool(sure) and abs(fx(r)["x_left"] - mxl) <= 0.008 and abs(fx(r)["x_right"] - mxr) <= 0.008
        c = feed.get((r.get("channel"), family(r.get("title"))))
        if near:
            ty, why = "stations", "station-cam insets where this event's other station-cam videos have them"
        elif c and len(c) == 1 or c and c.most_common(1)[0][1] >= 3 * min(c.values()):
            ty = c.most_common(1)[0][0]
            why = f"{'station-cam insets' if ty == 'stations' else 'fixed camera, no insets'}: same feed as {c[ty]} clear videos"
        else:
            out[v] = r; continue
        out[v] = dict(r, type=ty, why=why, conf=0.6, refined=True)
    return out


def build_plan(ev, ev_dir, matches, doc, recs, live=None, results=None, scan=None):
    """Everything stdlib: candidates + cached classifications + overrides -> the plan dict."""
    cands, info = gather(matches, doc, load_scan(ev_dir) if scan is None else scan)
    keys = [m["key"] for m in matches or []]
    used = {v for L in cands.values() for v in L}
    sub = refine({v: recs.get(v) for v in used if recs.get(v)})
    dup = duplicates(sub)
    recs2 = {v: dict(r, dup_of=dup.get(v)) if dup.get(v) else r for v, r in sub.items()}
    disabled = {s["id"] for s in (doc or {}).get("sources", []) if s.get("kind") == "per_match" and s.get("enabled") is False}
    ovs = (doc or {}).get("match_overrides") or {}
    live = live if live is not None else {}
    results = results if results is not None else match_results(ev_dir)
    played = {m["key"] for m in matches or [] if _played(m)}
    pm = {}
    for k in sorted(set(keys) | set(cands), key=_korder):
        e = choose(k, cands.get(k, []), recs2, info, ovs.get(k), disabled, ev, keys, results)
        e["label"] = match_label(k)
        e["played"] = k in played
        lc = live.get(k)
        if lc: e["live"] = lc
        if not e["track"] and lc and lc.get("trackable"):
            e["problems"] = [p for p in e["problems"] if p != "no video linked yet"]
        pm[k] = e
    vids = {}
    for v in used:
        r = recs2.get(v) or {}
        vids[v] = {k2: r.get(k2) for k2 in ("title", "channel", "duration", "height", "type", "conf", "why", "dup_of")}
        vids[v]["matches"] = sorted([k for k, L in cands.items() if v in L], key=_korder)
        vids[v]["families"] = (info.get(v) or {}).get("families", [])
        vids[v]["checked"] = bool(r) and r.get("type") is not None
    played = [k for k in pm if pm[k]["played"] or pm[k]["candidates"] or pm[k].get("live")]
    summ = dict(n=len(played),
                wide=sum(1 for k in played if any(c.get("type") == "wide" and not c.get("excluded") for c in pm[k]["candidates"])),
                broadcast=sum(1 for k in played if any(c.get("type") == "broadcast" and not c.get("excluded") for c in pm[k]["candidates"])),
                stations=sum(1 for k in played if pm[k]["stations"]),
                live=sum(1 for k in played if pm[k].get("live")),
                tracked_wide=sum(1 for k in played if pm[k]["track_type"] == "wide"),
                limited=sum(1 for k in played if pm[k]["limited"]),
                missing=sum(1 for k in played if not pm[k]["track"]),
                pending=sum(pm[k]["pending"] for k in played),
                dups=len(dup), videos=len(used), checked=sum(1 for v in vids.values() if v["checked"]))
    return {"version": 1, "event": ev, "built_at": time.time(), "classifier": CLASSIFIER, "summary": summ,
            "matches": pm, "videos": vids, "groups": groups(pm, recs2)}


def _played(m):
    if m.get("played") is not None: return bool(m["played"])
    s = ((m.get("alliances") or {}).get("red") or {}).get("score")
    return s is not None and s >= 0


def plan_path(ev_dir):
    return os.path.join(ev_dir, "video_plan.json")


def load_plan(ev_dir):
    return _jload(plan_path(ev_dir), None)


def save_plan(ev_dir, plan):
    _jsave(plan_path(ev_dir), plan)
    return plan


def todo(matches, doc, recs, done_keys=(), now=None, scan=None):
    """Video ids to classify, most useful first: candidates of played matches not processed yet (in match
    order), then the rest."""
    cands, _ = gather(matches, doc, scan)
    played = {m["key"] for m in matches or [] if _played(m)}
    first, rest = [], []
    for k in sorted(cands, key=_korder):
        for v in cands[k]:
            if not needs_classify(recs.get(v), now): continue
            (first if k in played and k not in done_keys else rest).append(v)
    seen, out = set(), []
    for v in first + rest:
        if v not in seen: seen.add(v); out.append(v)
    return out


def classify_many(data, vids, budget_s=None, max_n=None, workers=3, log=print, classify_fn=None):
    """Classify videos (a few threads), storing every record in the cache as it lands.  -> records done."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    fn = classify_fn or classify_video
    cache = load_cache(data)
    vids = list(vids)[:max_n] if max_n else list(vids)
    t0, done = time.time(), {}
    def one(v):
        if budget_s and time.time() - t0 > budget_s: return v, None
        try:
            return v, fn(v)
        except Exception as e:
            old = cache.get(v) or {}
            return v, dict(id=v, type=None, classifier=CLASSIFIER, at=time.time(), why=f"check failed: {str(e)[:160]}",
                           tries=old.get("tries", 0) + 1, title=old.get("title"), channel=old.get("channel"),
                           duration=old.get("duration"), height=old.get("height"))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(one, v) for v in vids]
        for f in as_completed(futs):
            v, r = f.result()
            if r is None: continue
            if r.get("type") is None and "tries" not in r: r["tries"] = (cache.get(v) or {}).get("tries", 0) + 1
            done[v] = r
            if len(done) % 5 == 0: save_cache(data, dict(done))
    if done: save_cache(data, dict(done))
    if log and done:
        n_ok = sum(1 for r in done.values() if r.get("type"))
        log(f"video check: {len(done)} videos ({n_ok} classified) in {time.time() - t0:.0f}s")
    return done


def refresh(data, ev, matches, doc=None, budget_s=60.0, max_n=12, log=print, classify=True, done_keys=()):
    """Worker step (every poll): classify a few new candidate videos (time-boxed), rebuild + save the plan."""
    import sources as SRC
    evd = os.path.join(data, "events", ev)
    doc = doc if doc is not None else SRC.load(evd)
    recs = load_cache(data)
    scan = load_scan(evd)
    if classify and budget_s and max_n:
        try:                                          # the event's channels: uploads TBA does not link (throttled)
            scan = scan_channels(data, ev, matches, recs, log=log, max_age=scan_age(matches))
        except Exception as e:
            log(f"{ev}: channel scan failed: {e!r:.160}")
        vids = todo(matches, doc, recs, done_keys, scan=scan)
        if vids:
            recs.update(classify_many(data, vids, budget_s=budget_s, max_n=max_n, log=log))
    plan = build_plan(ev, evd, matches, doc, recs, live=live_clips(ev), scan=scan)
    return save_plan(evd, plan)


def scan_age(matches):
    """Re-scan the channels every 20 min while matches are being played (uploads arrive), else twice a day."""
    ts = [m.get("actual_time") or m.get("time") or 0 for m in matches or []]
    return 1200 if ts and time.time() - max(ts) < 2 * 86400 else 12 * 3600


# ================================================================ worker: candidates from the plan
def plan_candidates(entry, recs_or_videos):
    """Plan entry -> ranked upload metadata for videos.candidates (track first, then the other usable ones in
    plan order: wide, stations, broadcast).  [] when the plan has nothing for this match."""
    if not entry or not entry.get("track"): return []
    cs = [c for c in entry.get("candidates") or [] if c.get("type") and not c.get("excluded") and not c.get("dup_of")]
    first = [c for c in entry["candidates"] if c["id"] == entry["track"]]
    rest = sorted([c for c in cs if c["id"] != entry["track"]], key=lambda c: _rank(c))
    out = []
    for c in first + rest:
        out.append(dict(id=c["id"], title=c.get("title") or "", channel=c.get("channel") or "", duration=c.get("duration") or 0,
                        height=c.get("height") or 0, type=c.get("type"), limited=LIMITED.get(c.get("type")),
                        group=group_id(c["type"], c.get("channel")) if c.get("type") else None, plan=True,
                        **({"inset_box": c["inset_box"]} if c.get("inset_box") else {})))
    return out


def layout_for(ev_dir, cand, W=1920, H=1080):
    """process(layout=...) for a plan-chosen upload: the layout the admin drew for its group (detected type +
    channel) when there is one; for a station-inset feed without one, the event's processing layout (or the
    2026 FIRST default boxes) plus the two detected corner insets as masks, so the tracker never looks for
    robots inside the station cameras.  None = the event's default lookup."""
    g = cand.get("group") if isinstance(cand, dict) else None
    lay = _jload(os.path.join(ev_dir, "layout.json"), None) or {}
    src = (lay.get("sources") or {}).get(g) if g else None
    if src:
        return {"version": 1, "processing_source": g, "sources": {g: src}}
    ib = cand.get("inset_box") if isinstance(cand, dict) else None
    if cand.get("type") != "stations" or not ib:
        return None
    import layout as LY
    base = None
    try:
        L = LY.Layout(lay) if lay.get("sources") else None
        base = L.processing() if L else None
    except Exception:
        base = None
    if base is not None:
        bw, bh = base.W, base.H
        regs = [dict(r) for r in base.regions if r.get("kind") not in ("station_red", "station_blue")]
    else:
        bw, bh = LY.REF_W, LY.REF_H
        regs = LY.first2026_regions(False)
    top = int(0.55 * bh)
    regs += [dict(id="auto_inset_l", kind="inset", label="Station camera inset (detected)",
                  box=[0, top, int(ib["x_left"] * bw) + 8, bh]),
             dict(id="auto_inset_r", kind="inset", label="Station camera inset (detected)",
                  box=[int(ib["x_right"] * bw) - 8, top, bw, bh])]
    sid = g or "auto-stations"
    return {"version": 1, "processing_source": sid,
            "sources": {sid: dict(W=bw, H=bh, role="dynamic", regions=regs)}}


if __name__ == "__main__":
    import argparse, sys
    if HERE not in sys.path: sys.path.insert(0, HERE)
    ap = argparse.ArgumentParser(description="per-match video plan")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("classify", help="classify YouTube ids (prints the records)"); a.add_argument("ids", nargs="+")
    a = sub.add_parser("build", help="classify an event's candidates + write its plan")
    a.add_argument("data"); a.add_argument("ev"); a.add_argument("--matches-json"); a.add_argument("--max", type=int, default=0)
    a.add_argument("--workers", type=int, default=3)
    g = ap.parse_args()
    if g.cmd == "classify":
        for v in g.ids: print(json.dumps(classify_video(v), indent=1))
    elif g.cmd == "build":
        ms = _jload(g.matches_json, []) if g.matches_json else []
        ms = ms.get("data", ms) if isinstance(ms, dict) else ms
        import sources as SRC
        doc = SRC.load(os.path.join(g.data, "events", g.ev))
        scan = scan_channels(g.data, g.ev, ms, load_cache(g.data), max_age=0)
        vids = todo(ms, doc, load_cache(g.data), scan=scan)
        print(f"{len(vids)} videos to check")
        classify_many(g.data, vids, max_n=g.max or None, workers=g.workers)
        p = build_plan(g.ev, os.path.join(g.data, "events", g.ev), ms, doc, load_cache(g.data), live=live_clips(g.ev))
        save_plan(os.path.join(g.data, "events", g.ev), p)
        print(json.dumps(p["summary"], indent=1))
