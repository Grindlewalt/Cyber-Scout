"""Video sources per event: where the match video comes from (any event, any broadcast).

Stdlib only (the API container imports it); the heavy jobs (frame grabs, layout suggestions, AprilTag
detection, calibration fits) need the vision stack and run in run_job() -- in the API process when numpy/cv2
are importable, else in the worker's job thread (job_loop) / `python live/sources.py jobs DATA --watch`.

data/events/<ev>/sources.json
  {"version": 1, "event": "2026wasam", "updated_at": ..., "discovered_at": ...,
   "sources": [{
      "id": "tba-uploads-wide-match", "origin": "tba" | "admin",
      "kind": "per_match" | "livestream" | "video" | "file",
      "provider": "youtube" | "twitch" | "file" | "http",
      "url": "...",                       # livestream / video / playlist / channel / file path
      "title_pattern": "^Wide Match \\d+", # per_match uploads: regex on the YouTube title
      "channel": "FIRST Washington",
      "role": "wide" | "dynamic" | "alliance-side",
      "primary": true,                    # the overhead video the tracker processes
      "coverage": {"mode": "all" | "matches" | "range", "matches": [...], "from": "qm1", "to": "qm40"},
      "videos": {"2026wasam_qm17": "<youtube id>"},   # per-match videos found so far
      "sample": {"video": "<youtube id | url | path>", "match": "2026wasam_qm17", "t": 60},
      "label": "...", "enabled": true, "updated_at": ..., "updated_by": "..."}]}

Roles: wide = fixed overhead / wide field camera (what the tracker needs); dynamic = switching broadcast
program feed (cuts between cameras; may carry picture-in-picture station cams); alliance-side = fixed
driver-station cameras.  Picture-in-picture regions are drawn per source in layout.json (live/layout.py).

Worker helpers: video_config(doc, cfg) -> videos.candidates() config (prefer / reject regexes from the
primary per-match source), station_pattern(doc) -> title regex of the upload that holds the station insets,
livestream_url(doc) -> URL for live/livestream.py, primary(doc).
"""
import hashlib, json, os, re, subprocess, threading, time, traceback, urllib.parse, urllib.request

ROLES = ("wide", "dynamic", "alliance-side")
KINDS = ("per_match", "livestream", "video", "file")
YTDLP = os.environ.get("YTDLP", "yt-dlp")
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- file
def path(ev_dir):
    return os.path.join(ev_dir, "sources.json")


def load(ev_dir):
    try:
        with open(path(ev_dir)) as f:
            d = json.load(f)
        d.setdefault("sources", [])
        return d
    except (OSError, ValueError):
        return {"version": 1, "event": os.path.basename(os.path.normpath(ev_dir)), "sources": []}


def save(ev_dir, doc):
    os.makedirs(ev_dir, exist_ok=True)
    doc["updated_at"] = time.time()
    p = path(ev_dir); tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, p)
    return doc


def get(doc, sid):
    for s in doc.get("sources", []):
        if s["id"] == sid: return s
    return None


def primary(doc):
    en = [s for s in doc.get("sources", []) if s.get("enabled", True)]
    for s in en:
        if s.get("primary"): return s
    for s in en:
        if s.get("role") == "wide": return s
    return en[0] if en else None


# ---------------------------------------------------------------- URLs
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def classify_url(url):
    """-> dict(kind, provider, url (normalised), video?, playlist?) or raises ValueError."""
    u = (url or "").strip()
    if not u: raise ValueError("empty URL")
    if u.startswith("/"):
        return dict(kind="file", provider="file", url=os.path.normpath(u))
    if _YT_ID.match(u):
        return dict(kind="video", provider="youtube", url=f"https://www.youtube.com/watch?v={u}", video=u)
    p = urllib.parse.urlparse(u if "://" in u else "https://" + u)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    q = urllib.parse.parse_qs(p.query)
    if host in ("youtube.com", "youtu.be", "music.youtube.com"):
        if "list" in q and not q.get("v"):
            return dict(kind="per_match", provider="youtube", url=f"https://www.youtube.com/playlist?list={q['list'][0]}",
                        playlist=q["list"][0])
        vid = q.get("v", [None])[0]
        if host == "youtu.be": vid = p.path.strip("/").split("/")[0]
        m = re.match(r"^/(live|shorts|embed)/([A-Za-z0-9_-]{11})", p.path)
        if m: vid = m.group(2)
        if vid and _YT_ID.match(vid):
            return dict(kind="video", provider="youtube", url=f"https://www.youtube.com/watch?v={vid}", video=vid)
        if re.match(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)", p.path):
            base = re.match(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)", p.path).group(1)
            if p.path.rstrip("/").endswith("/live") or p.path.rstrip("/").endswith("/streams"):
                return dict(kind="livestream", provider="youtube", url=f"https://www.youtube.com/{base}/live")
            return dict(kind="per_match", provider="youtube", url=f"https://www.youtube.com/{base}/videos")
        raise ValueError("unrecognised YouTube URL")
    if host.endswith("twitch.tv"):
        m = re.match(r"^/videos/(\d+)", p.path)
        if m: return dict(kind="video", provider="twitch", url=f"https://www.twitch.tv/videos/{m.group(1)}")
        ch = p.path.strip("/").split("/")[0]
        if ch: return dict(kind="livestream", provider="twitch", url=f"https://www.twitch.tv/{ch}")
        raise ValueError("unrecognised Twitch URL")
    if p.scheme in ("http", "https"):
        kind = "livestream" if p.path.endswith(".m3u8") else "video"
        return dict(kind=kind, provider="http", url=u)
    raise ValueError("unsupported URL")


def allowed_roots():
    """Local files an admin may point at: the media + data dirs (+ CS_SOURCE_ROOTS, colon separated)."""
    r = [os.environ.get("MEDIA", "/media"), os.environ.get("DATA", "/data")]
    r += [x for x in os.environ.get("CS_SOURCE_ROOTS", "").split(":") if x]
    return [os.path.realpath(x) for x in r]


def file_allowed(p):
    rp = os.path.realpath(p)
    return any(rp == r or rp.startswith(r.rstrip("/") + "/") for r in allowed_roots())


def webcast_url(w):
    t, ch, fl = (w.get("type") or "").lower(), w.get("channel") or "", w.get("file") or ""
    if t == "youtube": return f"https://www.youtube.com/watch?v={ch}" if _YT_ID.match(ch) else f"https://www.youtube.com/{ch}"
    if t == "twitch": return f"https://www.twitch.tv/{ch}"
    if t in ("html5", "direct_link", "dacast", "livestream", "iframe", "ustream", "stemtv", "mms", "rtmp"):
        if fl.startswith("http"): return fl
        if ch.startswith("http"): return ch
    return None


# ---------------------------------------------------------------- titles / matches
def oembed(vid, timeout=10):
    """YouTube title + channel without yt-dlp (works in the slim API container)."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cyber-scout"}), timeout=timeout) as r:
            d = json.load(r)
        return dict(id=vid, title=d.get("title", ""), channel=d.get("author_name", ""))
    except Exception:
        return None


def family(title):
    """'Wide Match 17' -> 'Wide Match #'; 'Match 8 (R2)' -> 'Match #'; event names keep their words."""
    t = re.sub(r"\s*\([^)]*\)", "", title or "").strip()
    t = re.sub(r"\d+", "#", t)
    return re.sub(r"\s+", " ", t)


def family_pattern(fam):
    """Regex for titles of one family (digits free, optional '(...)' suffix after the number)."""
    parts = [re.escape(p) for p in fam.split("#")]
    return "^" + r"\d+(?:\s*\([^)]*\))?".join(parts).replace(r"\ ", r"\s+") + r"\s*$"


_LV = [(r"\b(?:qualification|quals?|q)\s*(?:match\s*)?(\d+)\b", "qm"),
       (r"\b(?:finals?|f)\s*(?:match\s*)?(\d+)\b", "f"),
       (r"\b(?:playoffs?|semi-?finals?|sf|elim(?:ination)?s?|upper bracket|lower bracket)\b.*?\bmatch\s*(\d+)\b", "sf"),
       (r"^\s*match\s*(\d+)\s*\(", "sf")]


def match_key_from_title(title, ev, matches=None):
    """Best-effort match key from a video title ("Qualification 17 - ...", "Q17", "Playoff Match 3", "Final 2")."""
    t = (title or "").lower()
    keys = {m["key"] for m in (matches or [])}
    for pat, lv in _LV:
        m = re.search(pat, t)
        if not m: continue
        n = int(m.group(1))
        k = f"{ev}_qm{n}" if lv == "qm" else f"{ev}_f1m{n}" if lv == "f" else f"{ev}_sf{n}m1"
        if not keys or k in keys: return k
    return None


def _yt_videos(m):
    return [v["key"].split("?")[0] for v in (m.get("videos") or []) if v.get("type") == "youtube" and v.get("key")]


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "x"


# ---------------------------------------------------------------- discovery
def discover(ev, event, matches, meta_fn=oembed, meta_cache=None, sample=10, log=print, full=True):
    """TBA -> candidate sources: event webcasts (livestreams) + per-match upload families.
    event: TBA /event/{key}; matches: TBA /event/{key}/matches.  meta_fn(vid) -> {title, channel} (oEmbed by
    default; worker's videos.meta also fits).  full=True titles every match video (one request each) so
    `videos` covers the whole schedule; False only samples."""
    meta_cache = {} if meta_cache is None else meta_cache
    out = []
    for i, w in enumerate((event or {}).get("webcasts") or []):
        u = webcast_url(w)
        if not u: continue
        try:
            c = classify_url(u)
        except ValueError:
            c = None
        out.append(dict(id=f"tba-webcast-{i}", origin="tba", kind="livestream", provider=(w.get("type") or "").lower(),
                        url=u, role="dynamic", coverage={"mode": "all"}, enabled=True,
                        label=f"Event webcast ({w.get('type')}{', ' + w['date'] if w.get('date') else ''})",
                        sample=dict(video=(c or {}).get("video") or u, t=None), webcast=w))
    withv = sorted([m for m in matches or [] if _yt_videos(m)], key=_order)
    if withv:
        pick = withv if full else [withv[int(round(i))] for i in
                                   ([0] if len(withv) == 1 else
                                    [k * (len(withv) - 1) / (min(sample, len(withv)) - 1) for k in range(min(sample, len(withv)))])]
        fams = {}
        for m in pick:
            for vid in _yt_videos(m):
                if vid not in meta_cache:
                    meta_cache[vid] = meta_fn(vid)
                md = meta_cache[vid]
                if not md: continue
                f = family(md["title"])
                d = fams.setdefault((md.get("channel", ""), f), dict(videos={}, titles=[]))
                d["videos"].setdefault(m["key"], vid); d["titles"].append(md["title"])
        for (ch, f), d in sorted(fams.items(), key=lambda kv: -len(kv[1]["videos"])):
            role = "wide" if f.lower().startswith("wide") else "dynamic"
            some = sorted(d["videos"].items(), key=lambda kv: _order({"key": kv[0]}))
            mid = some[len(some) // 2]
            out.append(dict(id=f"tba-uploads-{_slug(ch)}-{_slug(f)}", origin="tba", kind="per_match", provider="youtube",
                            title_pattern=family_pattern(f), channel=ch, role=role, family=f,
                            coverage={"mode": "matches"}, videos=d["videos"], enabled=True,
                            label=f"Per-match uploads “{f.replace('#', 'N')}” ({ch})",
                            sample=dict(video=mid[1], match=mid[0], t=60.0), example_title=d["titles"][0]))
    # default primary: a wide per-match family, else the first livestream
    if out and not any(s.get("primary") for s in out):
        wide = [s for s in out if s["role"] == "wide"]
        (wide or out)[0]["primary"] = True
    return out


def _order(m):
    k = m["key"].split("_", 1)[-1]
    lv = re.match(r"[a-z]+", k).group(0)
    nums = [int(x) for x in re.findall(r"\d+", k)]
    return ({"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}.get(lv, 5), nums)


USER_FIELDS = ("role", "primary", "coverage", "enabled", "label", "sample", "notes", "updated_by")


def merge(doc, found):
    """Add newly discovered sources; refresh TBA facts (videos, url) of known ones, keeping admin edits."""
    have = {s["id"]: s for s in doc.get("sources", [])}
    user_primary = any(s.get("primary") for s in doc.get("sources", []))
    for s in found:
        if s["id"] in have:
            old = have[s["id"]]
            keep = {k: old[k] for k in USER_FIELDS if k in old}
            vids = dict(old.get("videos") or {}); vids.update(s.get("videos") or {})
            old.update(s); old.update(keep)
            if vids: old["videos"] = vids
        else:
            if user_primary: s.pop("primary", None)
            doc.setdefault("sources", []).append(s)
    doc["discovered_at"] = time.time()
    return doc


def add(doc, url, role="wide", label=None, coverage=None, by=None, primary=False):
    c = classify_url(url)
    if c["kind"] == "file" and not file_allowed(c["url"]):
        raise ValueError("file sources must be under the media or data directory")
    sid = "url-" + hashlib.sha1(c["url"].encode()).hexdigest()[:8]
    if get(doc, sid): raise ValueError("that source is already listed")
    if role not in ROLES: raise ValueError(f"role must be one of {ROLES}")
    s = dict(id=sid, origin="admin", kind=c["kind"], provider=c["provider"], url=c["url"], role=role,
             coverage=coverage or {"mode": "all"}, enabled=True, label=label or c["url"],
             sample=dict(video=c.get("video") or c["url"], t=None), updated_at=time.time(), updated_by=by)
    if c.get("playlist"): s["playlist"] = c["playlist"]
    if primary:
        for o in doc.get("sources", []): o["primary"] = False
        s["primary"] = True
    doc.setdefault("sources", []).append(s)
    return s


def update(doc, sid, patch, by=None):
    s = get(doc, sid)
    if s is None: raise KeyError(sid)
    for k, v in patch.items():
        if k not in USER_FIELDS or v is None: continue
        if k == "role" and v not in ROLES: raise ValueError(f"role must be one of {ROLES}")
        if k == "primary" and v:
            for o in doc.get("sources", []): o["primary"] = False
        s[k] = v
    s["updated_at"] = time.time(); s["updated_by"] = by
    return s


def remove(doc, sid):
    n = len(doc.get("sources", []))
    doc["sources"] = [s for s in doc.get("sources", []) if s["id"] != sid]
    return len(doc["sources"]) < n


def covers(s, match_key):
    c = s.get("coverage") or {"mode": "all"}
    if c.get("mode") == "matches" and c.get("matches"): return match_key in c["matches"]
    if c.get("mode") == "matches": return match_key in (s.get("videos") or {}) or s.get("kind") != "per_match"
    if c.get("mode") == "range":
        o = _order({"key": match_key})
        lo = _order({"key": c["from"]}) if c.get("from") else None
        hi = _order({"key": c["to"]}) if c.get("to") else None
        return (lo is None or o >= lo) and (hi is None or o <= hi)
    return True


# ---------------------------------------------------------------- worker integration
def video_config(doc, cfg=None, layout_doc=None):
    """videos.candidates() config: `prefer` = the primary per-match source's title regex; `reject` = per-match
    families the admin disabled or whose layout has picture-in-picture insets (they hide the field; the
    tracker must not run on them).  Other families stay acceptable fallbacks (allow_channels).  cfg values
    (event.json) are kept where the sources don't say anything."""
    cfg = dict(cfg or {})
    p = primary(doc)
    if p and p.get("kind") == "per_match" and p.get("title_pattern"):
        cfg["prefer"] = p["title_pattern"]
        if p.get("channel"):
            cfg["allow_channels"] = sorted(set(cfg.get("allow_channels", ["FIRSTRoboticsCompetition"])) | {p["channel"]})
    lay = (layout_doc or {}).get("sources") or {}
    def inset(s):
        return any(r.get("kind") in ("station_red", "station_blue", "inset") for r in (lay.get(s["id"]) or {}).get("regions", []))
    rej = [s["title_pattern"] for s in doc.get("sources", []) if s is not p and s.get("kind") == "per_match"
           and s.get("title_pattern") and (not s.get("enabled", True) or inset(s))]
    if rej: cfg["reject"] = "|".join(f"(?:{r})" for r in rej)
    return cfg


def station_pattern(doc, layout_doc=None):
    """Title regex of the per-match upload whose layout has station insets (stationcam.find_inset_video)."""
    for s in doc.get("sources", []):
        if not s.get("enabled", True) or s.get("kind") != "per_match": continue
        regs = ((layout_doc or {}).get("sources", {}).get(s["id"], {}) or {}).get("regions", [])
        if any(r.get("kind", "").startswith("station_") for r in regs) or s.get("role") == "alliance-side":
            return s.get("title_pattern")
    return None


def livestream_url(doc):
    for s in doc.get("sources", []):
        if s.get("enabled", True) and s.get("kind") == "livestream" and (s.get("primary") or s.get("role") == "wide"):
            return s["url"]
    ls = [s for s in doc.get("sources", []) if s.get("enabled", True) and s.get("kind") == "livestream"]
    return ls[0]["url"] if ls else None


def media_for(s, match_key=None):
    """What to grab a frame from: a youtube URL / direct URL / local path (+ a default time)."""
    if s.get("kind") == "per_match":
        vids = s.get("videos") or {}
        vid = vids.get(match_key) if match_key else None
        vid = vid or (s.get("sample") or {}).get("video") or (next(iter(vids.values())) if vids else None)
        if not vid: return None, None
        return (f"https://www.youtube.com/watch?v={vid}" if _YT_ID.match(vid) else vid), (s.get("sample") or {}).get("t") or 60.0
    smp = s.get("sample") or {}
    v = smp.get("video") or s.get("url")
    if v and _YT_ID.match(v): v = f"https://www.youtube.com/watch?v={v}"
    return v, smp.get("t")


# ---------------------------------------------------------------- jobs (frames, suggestions, calibration)
def jobs_dir(data):
    return os.path.join(data, "jobs", "sources")


def new_job(data, ev, kind, params, by=None):
    os.makedirs(jobs_dir(data), exist_ok=True)
    jid = f"{int(time.time() * 1000):x}{os.urandom(3).hex()}"
    j = dict(id=jid, ev=ev, kind=kind, params=params, state="queued", created=time.time(), by=by)
    _jsave(os.path.join(jobs_dir(data), jid + ".json"), j)
    return j


def get_job(data, jid):
    if not re.match(r"^[0-9a-f]{8,40}$", jid or ""): return None
    try:
        return json.load(open(os.path.join(jobs_dir(data), jid + ".json")))
    except (OSError, ValueError):
        return None


def _jsave(p, obj):
    tmp = f"{p}.tmp{os.getpid()}{threading.get_ident()}"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, p)


def _claim(data, jid):
    """Atomic claim via a lock file (several runners may poll)."""
    lk = os.path.join(jobs_dir(data), jid + ".lock")
    try:
        fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd); return True
    except FileExistsError:
        return False


def vision_available():
    try:
        import numpy, cv2  # noqa: F401
        return True
    except Exception:
        return False


def run_job(data, j, log=print):
    """Execute one job dict (claimed by the caller) and store its result/error."""
    p = os.path.join(jobs_dir(data), j["id"] + ".json")
    j.update(state="running", started=time.time()); _jsave(p, j)
    try:
        fn = JOBS[j["kind"]]
        j["result"] = fn(data, j["ev"], j.get("params") or {}, log)
        j["state"] = "done"
    except Exception as e:
        traceback.print_exc()
        j.update(state="error", error=str(e)[-500:])
    j["finished"] = time.time()
    _jsave(p, j)
    return j


def run_pending(data, kinds=None, log=print, max_age=3600):
    """Run every queued job once (oldest first).  Returns the number run."""
    d = jobs_dir(data)
    if not os.path.isdir(d): return 0
    n = 0
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"): continue
        try:
            j = json.load(open(os.path.join(d, f)))
        except (OSError, ValueError):
            continue
        if j.get("state") != "queued" or (kinds and j["kind"] not in kinds): continue
        if time.time() - j.get("created", 0) > max_age:
            j.update(state="error", error="expired (no job runner picked it up)"); _jsave(os.path.join(d, f), j); continue
        if not _claim(data, j["id"]): continue
        run_job(data, j, log); n += 1
    return n


def job_loop(data, poll=0.7, log=print, stop=None):
    """For the worker: `threading.Thread(target=sources.job_loop, args=(DATA,), daemon=True).start()`."""
    while not (stop and stop.is_set()):
        try:
            if not run_pending(data, log=log): time.sleep(poll)
        except Exception:
            traceback.print_exc(); time.sleep(5)


def submit(data, ev, kind, params, by=None, inline=None):
    """Create a job; run it in a background thread here if this process can (vision stack present, or a
    job that needs none), else leave it queued for the worker's job_loop."""
    j = new_job(data, ev, kind, params, by)
    can = (kind in LIGHT_JOBS) or (vision_available() if inline is None else inline)
    if can and _claim(data, j["id"]):
        threading.Thread(target=run_job, args=(data, j), daemon=True).start()
    return j


# ---- job implementations (vision imports inside)
def _ev_dir(data, ev):
    return os.path.join(data, "events", ev)


def _frames_dir(data, ev):
    d = os.path.join(_ev_dir(data, ev), "frames"); os.makedirs(d, exist_ok=True); return d


def _live_imports():
    import sys
    if HERE not in sys.path: sys.path.insert(0, HERE)
    import numpy as np, cv2, layout as LY, calibrate as CB
    return np, cv2, LY, CB


def _source(data, ev, sid):
    s = get(load(_ev_dir(data, ev)), sid)
    if s is None: raise ValueError(f"unknown source {sid}")
    if s.get("kind") == "file" and not file_allowed(s["url"]):
        raise ValueError("file source outside the allowed directories")
    return s


def _times(LY, media, t, n, span=(0.25, 0.85)):
    dur = LY.duration(media) or 0
    if dur > 30:
        lo, hi = span[0] * dur, span[1] * dur
        if dur < 400: lo, hi = max(12.0, 0.06 * dur), max(min(dur - 10.0, 150.0), 20.0)   # per-match upload: the match itself
        import numpy as np
        return [float(x) for x in np.linspace(lo, hi, n)], dur
    return [float(t or 0.0)], dur


def job_frame(data, ev, p, log=print):
    """Grab one frame of a source (t seconds, default: the source's sample time / 60 s) -> frames/<sid>.jpg."""
    np, cv2, LY, CB = _live_imports()
    s = _source(data, ev, p["source"])
    media, t0 = media_for(s, p.get("match"))
    if not media: raise ValueError("this source has no video yet (no per-match upload found)")
    t = p.get("t", t0)
    if t is None:
        dur = LY.duration(media)
        t = 0.5 * dur if dur and dur > 5 else None
    im = LY.grab_frame(media, t)
    H, W = im.shape[:2]
    fn = os.path.join(_frames_dir(data, ev), f"{p['source']}.jpg")
    cv2.imwrite(fn, im, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return dict(W=W, H=H, t=t, media=media if not media.startswith("http") or "youtube" in media else "remote",
                frame=os.path.basename(fn), at=time.time())


def job_suggest(data, ev, p, log=print):
    """Layout suggestions for a source: graphics (stillness), clock (1 Hz ticks), insets (vs a clean
    source of the same match if `other` is given, else persistent borders), field area."""
    np, cv2, LY, CB = _live_imports()
    s = _source(data, ev, p["source"])
    media, t0 = media_for(s, p.get("match"))
    if not media: raise ValueError("no video for this source yet")
    ts, dur = _times(LY, media, t0, int(p.get("n", 10)))
    if len(ts) == 1: ts = [ts[0] + k * 8.0 for k in range(int(p.get("n", 10)))]
    fr = [f for _, f in LY.grab_frames(media, ts)]
    if len(fr) < 3: raise ValueError("could not read enough frames from the video")
    dense = LY.grab_dense(media, ts[len(ts) // 2], seconds=12.0, hz=10.0)
    other = None
    if p.get("other"):
        o = _source(data, ev, p["other"])
        om, _ = media_for(o, (s.get("sample") or {}).get("match") or p.get("match"))
        if om:
            other = [f for _, f in LY.grab_frames(om, ts)] or None
    cam = None
    cp = os.path.join(_ev_dir(data, ev), "camera.json")
    if s.get("primary") and os.path.exists(cp):
        import camera as CAMM
        cam = CAMM.Camera(cp)
    sug = LY.suggest_all(fr, dense, other, cam)
    H, W = fr[0].shape[:2]
    return dict(W=W, H=H, suggestions=sug, frames=len(fr), dense=len(dense), times=ts)


def _target(data, ev, target):
    """Calibration target -> (source, crop box or None, out file, full_frame flag)."""
    import layout as LY
    L = LY.find(event_dir=_ev_dir(data, ev), ev=ev)
    if target == "overhead":
        s = L.processing() if L else None
        src = s.id if s else (primary(load(_ev_dir(data, ev))) or {}).get("id")
        crop = s.box("overhead") if s else None
        return src, crop, "camera.json", True
    if target in ("station_red", "station_blue"):
        s = L.stations() if L else None
        if s is None or not s.box(target):
            raise ValueError(f"draw the {target.replace('_', ' ')} inset in the layout first")
        return s.id, s.box(target), f"{target}.json", False
    raise ValueError("target must be overhead, station_red or station_blue")


def _wizard_path(data, ev, target):
    d = os.path.join(_ev_dir(data, ev), "calib_wizard"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{target}.json")


def job_detect(data, ev, p, log=print):
    """Calibration step 1: frames of the target camera -> AprilTags (+ a first fit when they suffice)."""
    np, cv2, LY, CB = _live_imports()
    target = p["target"]
    sid, crop, out, full = _target(data, ev, target)
    s = _source(data, ev, p.get("source") or sid)
    media, t0 = media_for(s, p.get("match"))
    if not media: raise ValueError("no video for this source yet")
    ts, dur = _times(LY, media, t0, int(p.get("n", 8)))
    if len(ts) == 1: ts = [ts[0] + k * 6.0 for k in range(int(p.get("n", 8)))]
    fr = [f for _, f in LY.grab_frames(media, ts)]
    if not fr: raise ValueError("could not read frames")
    FH, FW = fr[0].shape[:2]
    if crop: fr = [f[crop[1]:crop[3], crop[0]:crop[2]] for f in fr]
    H, W = fr[0].shape[:2]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in fr]
    tags = CB.detect_tags_frames(grays)
    plate = np.median(np.stack(fr), 0).astype(np.uint8)
    fn = os.path.join(_frames_dir(data, ev), f"calib_{target}.jpg")
    cv2.imwrite(fn, fr[len(fr) // 2], [cv2.IMWRITE_JPEG_QUALITY, 92])
    cv2.imwrite(os.path.join(_frames_dir(data, ev), f"calib_{target}_plate.png"), plate)
    np.save(os.path.join(_frames_dir(data, ev), f"calib_{target}_grays.npy"), np.stack(grays[:8]))
    st = dict(target=target, source=s["id"], crop=list(crop) if crop else None, W=W, H=H, frame_W=FW, frame_H=FH,
              full_frame=full, out=out, times=ts, tags=tags, frame=os.path.basename(fn), at=time.time(),
              points_catalog=CB.named_points(), lines_catalog=CB.named_lines())
    res = dict(st)
    if len(tags) >= 2:
        pp, rep = CB.wizard_fit(W, H, tags)
        if pp is not None and not rep.get("warnings"):
            st["p"] = pp.tolist()
            res.update(report=rep, overlay=CB.overlay_lines(pp, W, H))
    json.dump(st, open(_wizard_path(data, ev, target), "w"))
    res["points"] = st["points_catalog"]; res["lines"] = st["lines_catalog"]
    return res


def job_fit(data, ev, p, log=print):
    """Calibration step 2: tags (accepted ids, or edited) + clicked named points -> fit, more tags from the
    fit, the centre line, report + overlay.  The candidate stays in calib_wizard/<target>.json until accepted."""
    np, cv2, LY, CB = _live_imports()
    target = p["target"]
    st = json.load(open(_wizard_path(data, ev, target)))
    W, H = st["W"], st["H"]
    tags = {int(k): v for k, v in (p.get("tags") if p.get("tags") is not None else st["tags"]).items()}
    skip = set(int(x) for x in p.get("drop_tags") or [])
    tags = {k: v for k, v in tags.items() if k not in skip}
    pts = {k: v for k, v in (p.get("points") or {}).items() if v}
    lclicks = {k: v for k, v in (p.get("line_clicks") or {}).items() if v and len(v) >= 2}
    clk = CB.clicked_lines(lclicks)
    pp, rep = CB.wizard_fit(W, H, tags, pts, clk, p_init=st.get("p") if p.get("warm") else None)
    if pp is None: return dict(error=rep.get("error"), report=rep)
    gp = os.path.join(_frames_dir(data, ev), f"calib_{target}_grays.npy")
    if p.get("more_tags", True) and os.path.exists(gp):
        grays = list(np.load(gp))
        more = CB.detect_tags_predicted(grays, pp, W, H, have=set(tags) | skip)
        if more:
            tags.update(more)
            pp, rep = CB.wizard_fit(W, H, tags, pts, clk, p_init=pp)
    if p.get("auto_lines", True) and target == "overhead":
        plate = cv2.imread(os.path.join(_frames_dir(data, ev), f"calib_{target}_plate.png"), cv2.IMREAD_GRAYSCALE)
        if plate is not None:
            p3, r3 = CB.refine_with_lines(pp, plate, W, H, tags, pts, extra=clk)
            if r3 and (r3.get("point_rms_px") or 0) <= max(4.0, 1.5 * (rep.get("point_rms_px") or 0)):
                pp, rep = p3, r3
    which = target.replace("station_", "") if target != "overhead" else "overhead"
    cam = CB.wizard_camera(pp, W, H, crop=st.get("crop"), full_frame=st.get("full_frame"),
                           frame_wh=(st.get("frame_W"), st.get("frame_H")), event=ev, which=which,
                           model="wizard pinhole f,c,k1,k2,k3", source=st.get("source"),
                           calib=dict(method="wizard", tag_rms_px=rep.get("tag_rms_px"), point_rms_px=rep.get("point_rms_px"),
                                      line_med_px=rep.get("line_med_px"), n_tags=rep.get("n_tags"), n_points=rep.get("n_points"),
                                      at=time.time()))
    if target == "overhead":
        import camera as CAMM
        try:
            cam["field_roi"] = list(CAMM.Camera(cam).roi())     # suggestion for the layout's field box
        except Exception:
            pass
    st.update(p=pp.tolist(), fit_tags=tags, points=pts, line_clicks=lclicks, report=rep, fitted_at=time.time(), camera=cam)
    json.dump(st, open(_wizard_path(data, ev, target), "w"))
    return dict(report=rep, overlay=CB.overlay_lines(pp, W, H), tags=tags, points=pts, line_clicks=lclicks, W=W, H=H, crop=st.get("crop"),
                field_roi=cam.get("field_roi"))


def job_overlay(data, ev, p, log=print):
    """Overlay of the CURRENT saved calibration of a target on the wizard frame (to inspect / compare)."""
    np, cv2, LY, CB = _live_imports()
    target = p["target"]
    sid, crop, out, full = _target(data, ev, target)
    cp = os.path.join(_ev_dir(data, ev), out)
    if not os.path.exists(cp):
        import layout as _L
        for d in _L.event_dirs(ev):
            if os.path.exists(os.path.join(d, out)): cp = os.path.join(d, out); break
    if not os.path.exists(cp): raise ValueError("no saved calibration for this camera yet")
    cam = json.load(open(cp))
    K = np.array(cam["K"], float); D = np.ravel(cam["dist"]).tolist() + [0] * 5
    W, H = cam["W"], cam["H"]
    if full and crop:
        K[0, 2] -= crop[0]; K[1, 2] -= crop[1]; W, H = crop[2] - crop[0], crop[3] - crop[1]
    R, _ = cv2.Rodrigues(np.array(cam["rvec"], float)); C = -R.T @ np.array(cam["tvec"], float)
    pv = np.r_[K[0, 0], K[1, 1] / K[0, 0], K[0, 2], K[1, 2], D[0], D[1], np.ravel(cam["rvec"]), C, D[4]]
    if abs(D[2]) + abs(D[3]) > 0:
        return dict(note="tangential distortion: overlay approximate", overlay=CB.overlay_lines(pv, W, H), file=out)
    return dict(overlay=CB.overlay_lines(pv, W, H), file=out)


def job_discover(data, ev, p, log=print):
    """TBA discovery (needs no vision stack): webcasts + per-match upload families -> merged into sources.json."""
    event, matches = p.get("_event"), p.get("_matches")
    cache_p = os.path.join(data, "cache", "ytmeta_oembed.json")
    try: cache = json.load(open(cache_p))
    except (OSError, ValueError): cache = {}
    found = discover(ev, event, matches, oembed, cache, full=p.get("full", True), log=log)
    os.makedirs(os.path.dirname(cache_p), exist_ok=True)
    try: _jsave(cache_p, cache)
    except OSError: pass
    d = merge(load(_ev_dir(data, ev)), found)
    save(_ev_dir(data, ev), d)
    return dict(found=len(found), sources=d["sources"])


def job_index_playlist(data, ev, p, log=print):
    """Pasted playlist / channel: list its videos (yt-dlp --flat-playlist) and map titles to match keys."""
    doc = load(_ev_dir(data, ev)); s = get(doc, p["source"])
    if s is None or s.get("kind") != "per_match" or not s.get("url"): raise ValueError("not a playlist source")
    r = subprocess.run([YTDLP, "-4", "--flat-playlist", "--no-warnings", "--print", "%(id)s\t%(title)s",
                        "--playlist-end", "600", s["url"]], capture_output=True, text=True, timeout=600)
    vids = {}
    for line in r.stdout.strip().split("\n"):
        if "\t" not in line: continue
        vid, title = line.split("\t", 1)
        k = match_key_from_title(title, ev, p.get("_matches"))
        if k and k not in vids: vids[k] = vid
    doc = load(_ev_dir(data, ev)); s = get(doc, p["source"])
    s["videos"] = vids
    if vids:
        mk = sorted(vids, key=lambda k: _order({"key": k}))[len(vids) // 2]
        s["sample"] = dict(video=vids[mk], match=mk, t=60.0)
    save(_ev_dir(data, ev), doc)
    return dict(n=len(vids))


JOBS = dict(frame=job_frame, suggest=job_suggest, detect=job_detect, fit=job_fit, overlay=job_overlay,
            discover=job_discover, index_playlist=job_index_playlist)
LIGHT_JOBS = {"discover"}           # no vision stack needed: always runs in the API process


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="video sources: job runner / discovery")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("jobs", help="run queued frame/suggest/calibration jobs"); a.add_argument("data")
    a.add_argument("--watch", action="store_true")
    a = sub.add_parser("discover", help="discover sources from a TBA cache"); a.add_argument("data"); a.add_argument("ev")
    a.add_argument("--event-json"); a.add_argument("--matches-json")
    g = ap.parse_args()
    if g.cmd == "jobs":
        if g.watch: job_loop(g.data)
        else: print(run_pending(g.data), "jobs run")
    elif g.cmd == "discover":
        ld = lambda p: (lambda d: d.get("data", d))(json.load(open(p))) if p else None
        found = discover(g.ev, ld(g.event_json), ld(g.matches_json), full=False)
        d = merge(load(_ev_dir(g.data, g.ev)), found); save(_ev_dir(g.data, g.ev), d)
        print(json.dumps(d, indent=1)[:3000])
