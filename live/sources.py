"""Video sources per event: where the match video comes from (any event, any broadcast).

Stdlib only (the API container imports it); the heavy jobs (frame grabs, layout suggestions, AprilTag
detection, calibration fits) need the vision stack and run in run_job() -- in the API process when numpy/cv2
are importable, else in the worker's job thread (job_loop) / `python live/sources.py jobs DATA --watch`.

data/events/<ev>/sources.json
  {"version": 1, "event": "2026wasam", "updated_at": ..., "discovered_at": ...,
   "video_mode": "auto" | "livestream",  # match video: uploads + livestream (default) | livestream only
   "video_mode_at": ..., "video_mode_by": "...",
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
      "station": "red" | "blue",          # alliance-side: which driver-station camera (Video page "Red station cam")
      "dismissed": true,                  # a TBA source the admin removed: kept disabled so discovery doesn't re-add it
      "program_feed": true,               # detected by the auto set-up (cuts / several cameras); + program_feed_why, _at
      "dynamic": true | false,            # admin: track from every camera; absent / null = auto (= program_feed)
      "label": "...", "enabled": true, "updated_at": ..., "updated_by": "..."}]}

Auto set-up of a stream (job_auto_setup / accept_auto / auto_setup_sweep, below): candidate in
DATA/events/<ev>/auto_setup/<sid>/, Accept on the Video page.

Roles: wide = fixed overhead / wide field camera (what the tracker needs); dynamic = switching broadcast
program feed (cuts between cameras; may carry picture-in-picture station cams); alliance-side = fixed
driver-station cameras.  Picture-in-picture regions are drawn per source in layout.json (live/layout.py).

Worker helpers: video_config(doc, cfg) -> videos.candidates() config (prefer / reject regexes from the
primary per-match source), station_pattern(doc) -> title regex of the upload that holds the station insets,
livestream_url(doc) -> URL for live/livestream.py, primary(doc), video_mode(doc).

Match video mode (video_mode): "auto" = per-match uploads (TBA links, channel scan, search) + the livestream clip
when it is trackable (unchanged behaviour); "livestream" = offseason / no uploads: TBA only gives the match list
(and lineups, results), every match is tracked from the recorded livestream (a fixed wide view), never an upload.
"""
import hashlib, ipaddress, json, os, re, socket, subprocess, threading, time, traceback, urllib.parse, urllib.request

ROLES = ("wide", "dynamic", "alliance-side")
KINDS = ("per_match", "livestream", "video", "file")
VIDEO_MODES = ("auto", "livestream")
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


def video_mode(doc):
    """"auto" (uploads + livestream) or "livestream" (livestream only; TBA for the match list)."""
    m = (doc or {}).get("video_mode")
    return m if m in VIDEO_MODES else "auto"


def set_video_mode(doc, mode, by=None):
    if mode not in VIDEO_MODES: raise ValueError(f"video_mode must be one of {VIDEO_MODES}")
    doc["video_mode"] = mode
    doc["video_mode_at"] = time.time(); doc["video_mode_by"] = by
    return doc


# ---------------------------------------------------------------- URLs
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_LIST = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_YT_BASE = re.compile(r"^/(@[A-Za-z0-9._%-]{1,100}|channel/[A-Za-z0-9_-]{1,64}|c/[A-Za-z0-9._%-]{1,100}|user/[A-Za-z0-9._%-]{1,100})(?=/|$)")
_TWITCH_CH = re.compile(r"^[A-Za-z0-9_]{1,25}$")
_YT_CHAN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}$")
_YT_HOSTS = ("youtube.com", "youtu.be", "music.youtube.com")


def _bad_chars(u, allow_space=False):
    return any(ord(c) < 32 or ord(c) == 127 or (c.isspace() and not allow_space) for c in u)


def classify_url(url):
    """-> dict(kind, provider, url (normalised), video?, playlist?) or raises ValueError.

    Accepted: an absolute local path (checked against allowed_roots() by the callers), a bare YouTube id,
    an http(s) URL with a host (YouTube / Twitch URLs are rebuilt in canonical form, others returned as
    urlsplit().geturl()), or a scheme-less YouTube / Twitch URL ("youtube.com/@x/live").  Never anything
    starting with '-' (it would reach yt-dlp / ffmpeg as an option), file:, javascript:, data:, relative
    paths or other schemes."""
    u = (url if isinstance(url, str) else "").strip()
    if not u: raise ValueError("empty URL")
    if u.startswith("-"): raise ValueError("a source must not start with '-'")
    if u.startswith("/"):
        if _bad_chars(u, allow_space=True): raise ValueError("control characters in the path")
        if u.startswith("//"): raise ValueError("enter an http(s) URL or an absolute file path")
        return dict(kind="file", provider="file", url=os.path.normpath(u))
    if _bad_chars(u): raise ValueError("spaces or control characters in the URL")
    if _YT_ID.match(u):
        return dict(kind="video", provider="youtube", url=f"https://www.youtube.com/watch?v={u}", video=u)
    if "://" not in u:
        # scheme-less: only the well-known video hosts (rebuilt below); never a relative path / other scheme
        h = u.split("/", 1)[0].split("?", 1)[0].lower()
        h = h.removeprefix("www.").removeprefix("m.")
        if not (h in _YT_HOSTS or h == "twitch.tv" or h.endswith(".twitch.tv")):
            raise ValueError("enter a full http(s) URL, a YouTube id, or an absolute file path")
        u = "https://" + u
    p = urllib.parse.urlsplit(u)
    if p.scheme.lower() not in ("http", "https"):
        raise ValueError("only http(s) URLs, YouTube ids or absolute file paths are supported")
    raw_host = p.hostname or ""
    if not raw_host: raise ValueError("the URL has no host")
    if p.username is not None or p.password is not None: raise ValueError("URLs with a user name / password are not supported")
    try:
        p.port
    except ValueError:
        raise ValueError("invalid port in the URL")
    if not _HOST_RE.match(raw_host):
        try:
            ipaddress.ip_address(raw_host)
        except ValueError:
            raise ValueError("invalid host in the URL")
    host = raw_host.removeprefix("www.").removeprefix("m.")
    q = urllib.parse.parse_qs(p.query)
    if host in _YT_HOSTS:
        lst = (q.get("list") or [None])[0]
        if lst and not q.get("v"):
            if not _YT_LIST.match(lst): raise ValueError("unrecognised YouTube playlist id")
            return dict(kind="per_match", provider="youtube", url=f"https://www.youtube.com/playlist?list={lst}", playlist=lst)
        vid = q.get("v", [None])[0]
        if host == "youtu.be": vid = p.path.strip("/").split("/")[0]
        m = re.match(r"^/(live|shorts|embed)/([A-Za-z0-9_-]{11})", p.path)
        if m: vid = m.group(2)
        if vid and _YT_ID.match(vid):
            return dict(kind="video", provider="youtube", url=f"https://www.youtube.com/watch?v={vid}", video=vid)
        m = _YT_BASE.match(p.path)
        if m:
            base = m.group(1)
            if p.path.rstrip("/").endswith("/live") or p.path.rstrip("/").endswith("/streams"):
                return dict(kind="livestream", provider="youtube", url=f"https://www.youtube.com/{base}/live")
            return dict(kind="per_match", provider="youtube", url=f"https://www.youtube.com/{base}/videos")
        raise ValueError("unrecognised YouTube URL")
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        m = re.match(r"^/videos/(\d{1,20})(?:/|$)", p.path)
        if m: return dict(kind="video", provider="twitch", url=f"https://www.twitch.tv/videos/{m.group(1)}")
        ch = p.path.strip("/").split("/")[0]
        if ch and _TWITCH_CH.match(ch): return dict(kind="livestream", provider="twitch", url=f"https://www.twitch.tv/{ch}")
        raise ValueError("unrecognised Twitch URL")
    kind = "livestream" if p.path.endswith(".m3u8") else "video"
    norm = urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))
    return dict(kind=kind, provider="http", url=norm)


def allowed_roots():
    """Local files an admin may point at: the media + data dirs (+ CS_SOURCE_ROOTS, colon separated)."""
    r = [os.environ.get("MEDIA", "/media"), os.environ.get("DATA", "/data")]
    r += [x for x in os.environ.get("CS_SOURCE_ROOTS", "").split(":") if x]
    return [os.path.realpath(x) for x in r]


def file_allowed(p):
    if not isinstance(p, str) or not p.startswith("/"): return False
    rp = os.path.realpath(p)
    return any(rp == r or rp.startswith(r.rstrip("/") + "/") for r in allowed_roots())


def media_ok(v):
    """A stored source / sample value -> the normalised media string (YouTube id -> watch URL, URL, allowed
    absolute path) or raises ValueError.  Re-validates sources.json written before classify_url was strict."""
    if isinstance(v, str) and _YT_ID.match(v):
        return f"https://www.youtube.com/watch?v={v}"
    c = classify_url(v)
    if c["kind"] == "file" and not file_allowed(c["url"]):
        raise ValueError("file sources must be under the media or data directory")
    return c["url"]


# ---- SSRF guard: the worker / API only fetch public hosts
_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "64:ff9b:1::/48", "100::/64", "2001:db8::/32", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8"))
_PUBLIC_OK = {}                       # (host, port) -> time of the last passing check (short cache for chunk loops)


def _resolve(host, port):
    """[ip strings] for host (tests replace this)."""
    return sorted({i[4][0] for i in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)})


def _url_allow():
    """CS_URL_ALLOW: comma separated host names / CIDRs an admin explicitly allows although private
    (e.g. an OBS / NDI HLS server on the LAN)."""
    hosts, nets = set(), []
    for x in os.environ.get("CS_URL_ALLOW", "").split(","):
        x = x.strip().lower()
        if not x: continue
        try: nets.append(ipaddress.ip_network(x, strict=False))
        except ValueError: hosts.add(x)
    return hosts, nets


def ip_blocked(ip):
    """True for loopback / RFC1918 / link-local / CGNAT (incl. Tailscale 100.64/10) / docker 172.16/12 /
    multicast / reserved addresses and their IPv6 forms (incl. IPv4-mapped / 6to4 / Teredo)."""
    ip = ipaddress.ip_address(str(ip).split("%", 1)[0])
    if ip.version == 6:
        for inner in (ip.ipv4_mapped, ip.sixtofour, (ip.teredo or (None, None))[1]):
            if inner is not None and ip_blocked(inner): return True
        if ip in ipaddress.ip_network("64:ff9b::/96"):            # NAT64: the embedded IPv4 address
            if ip_blocked(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)): return True
    if any(ip in n for n in _BLOCKED_NETS if n.version == ip.version): return True
    return not ip.is_global or ip.is_multicast


def safe_public_url(url, resolve=None, cache_s=60.0):
    """-> url when it is http(s) with a host whose every address is public; raises ValueError otherwise.
    Called before anything in live/ or the API hands a URL to yt-dlp / ffmpeg / urllib (SSRF guard: the
    worker shares a docker bridge with the host's gateway and services)."""
    if not isinstance(url, str) or url.startswith("-"): raise ValueError("not a URL")
    p = urllib.parse.urlsplit(url.strip())
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        raise ValueError("only http(s) URLs with a host can be fetched")
    if p.username is not None or p.password is not None: raise ValueError("URLs with credentials are not fetched")
    host = p.hostname.lower().rstrip(".")
    try:
        port = p.port or (443 if p.scheme.lower() == "https" else 80)
    except ValueError:
        raise ValueError("invalid port")
    allow_hosts, allow_nets = _url_allow()
    if host in allow_hosts: return url
    key = (host, port)
    if resolve is None and cache_s and time.time() - _PUBLIC_OK.get(key, 0) < cache_s: return url
    try:
        addrs = [str(ipaddress.ip_address(host))]           # IP literal: no lookup
    except ValueError:
        addrs = None
    try:
        addrs = addrs or (resolve or _resolve)(host, port)
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot resolve {host}: {e}")
    if not addrs: raise ValueError(f"cannot resolve {host}")
    for a in addrs:
        try:
            ip = ipaddress.ip_address(str(a).split("%", 1)[0])
        except ValueError:
            raise ValueError(f"{host}: unexpected address {a!r}")
        if ip_blocked(ip) and not any(ip in n for n in allow_nets if n.version == ip.version):
            raise ValueError(f"{host} resolves to a non-public address ({ip}); not fetched")
    if resolve is None: _PUBLIC_OK[key] = time.time()
    return url


def webcast_url(w):
    """TBA webcast -> canonical YouTube / Twitch URL, else None (other types: arbitrary URLs from TBA data)."""
    t, ch = (w.get("type") or "").lower(), str(w.get("channel") or "").strip()
    if t == "youtube":
        if _YT_ID.match(ch): return f"https://www.youtube.com/watch?v={ch}"
        if _YT_CHAN.match(ch): return f"https://www.youtube.com/channel/{ch}/live"     # a channel id: its current live
        m = _YT_BASE.match("/" + ch.strip("/"))
        return f"https://www.youtube.com/{m.group(1)}/live" if m else None
    if t == "twitch":
        return f"https://www.twitch.tv/{ch}" if _TWITCH_CH.match(ch) else None
    return None


# ---------------------------------------------------------------- titles / matches
def oembed(vid, timeout=10):
    """YouTube title + channel without yt-dlp (works in the slim API container)."""
    if not isinstance(vid, str) or not _YT_ID.match(vid): return None
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


def match_key_from_title(title, ev, matches=None):
    """Best-effort match key from a video title ("Qualification 17 - ...", "Q17", "Match 17", "Playoff Match 3",
    "Match 5 (R2)", "Final 2", "Final Tiebreaker"); see videoplan.parse_title."""
    import videoplan as VP
    return VP.title_key(title, ev, [m["key"] for m in (matches or [])] or None)


def _yt_videos(m):
    """TBA match videos -> YouTube ids (anything that is not an 11-char id is dropped: never fetched raw)."""
    out = []
    for v in (m.get("videos") or []):
        if not isinstance(v, dict) or v.get("type") != "youtube" or not isinstance(v.get("key"), str): continue
        k = v["key"].split("?")[0].strip()
        if _YT_ID.match(k): out.append(k)
    return out


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
        u = webcast_url(w) if isinstance(w, dict) else None
        if not u: continue                       # only YouTube / Twitch webcasts (canonical URLs)
        try:
            c = classify_url(u)
        except ValueError:
            continue
        u = c["url"]
        out.append(dict(id=f"tba-webcast-{i}", origin="tba", kind="livestream", provider=(w.get("type") or "").lower(),
                        url=u, role="dynamic", coverage={"mode": "all"}, enabled=True, auto=True,
                        label=f"Event webcast ({w.get('type')}{', ' + w['date'] if w.get('date') else ''})",
                        sample=dict(video=c.get("video") or u, t=None), webcast=w))
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


USER_FIELDS = ("role", "primary", "coverage", "enabled", "label", "sample", "notes", "updated_by", "station", "dismissed",
               "dynamic")
DETECTED_FIELDS = ("program_feed", "program_feed_why", "program_feed_at")     # set by the auto set-up, kept by merge()
STATIONS = ("red", "blue")


def dynamic_on(s):
    """Track this source with a camera per frame (dynpose, process(dynamic=True))?  `dynamic` true / false is the
    admin's choice; null / absent = auto: on for a detected program feed (`program_feed`, auto set-up)."""
    d = (s or {}).get("dynamic")
    return bool((s or {}).get("program_feed")) if d is None else bool(d)


def set_dynamic(doc, sid, v, by=None):
    """Video page "Track from every camera": True / False forces it, None = auto (program feed)."""
    s = get(doc, sid)
    if s is None: raise KeyError(sid)
    if v not in (True, False, None): raise ValueError("dynamic must be true, false or null (auto)")
    if v is None: s.pop("dynamic", None)
    else: s["dynamic"] = v
    s["updated_at"] = time.time(); s["updated_by"] = by
    return s


def set_program_feed(doc, sid, feed):
    """Store what the auto set-up detected (dynpose.feed_summary): program_feed true / false (+ why).  An
    undecided result (None) never overwrites a known one."""
    s = get(doc, sid)
    if s is None or not isinstance(feed, dict) or feed.get("program_feed") is None: return s
    s.update(program_feed=bool(feed["program_feed"]), program_feed_why=str(feed.get("why") or "")[:200],
             program_feed_at=time.time())
    return s


def merge(doc, found):
    """Add newly discovered sources; refresh TBA facts (videos, url) of known ones, keeping admin edits.  A new
    source whose URL is already another source (a TBA webcast the admin pasted first) is not added again."""
    have = {s["id"]: s for s in doc.get("sources", [])}
    urls = {s.get("url") for s in doc.get("sources", []) if s.get("url")}
    user_primary = any(s.get("primary") for s in doc.get("sources", []))
    for s in found:
        if s["id"] in have:
            old = have[s["id"]]
            keep = {k: old[k] for k in USER_FIELDS + DETECTED_FIELDS if k in old}
            vids = dict(old.get("videos") or {}); vids.update(s.get("videos") or {})
            old.update(s); old.update(keep)
            if vids: old["videos"] = vids
        elif s.get("url") and s["url"] in urls:
            continue
        else:
            if s.get("url"): urls.add(s["url"])
            if user_primary: s.pop("primary", None)
            doc.setdefault("sources", []).append(s)
    doc["discovered_at"] = time.time()
    return doc


def _checked(url, live=None):
    """classify_url + the same checks as a pasted source (allowed file roots, public http hosts).  live=True turns
    a YouTube video link into a livestream (a watch link of a stream that is live / scheduled: the recorder
    records it); a channel /live link or a Twitch channel is a livestream anyway, a Twitch /videos/ link a VOD."""
    c = classify_url(url)
    if c["kind"] == "file" and not file_allowed(c["url"]):
        raise ValueError("file sources must be under the media or data directory")
    if c["provider"] == "http":
        safe_public_url(c["url"], cache_s=0)       # private / loopback / docker / Tailscale hosts: refused
    if live and c["kind"] == "video" and c["provider"] == "youtube":
        c = dict(c, kind="livestream")
    return c


def default_label(c):
    """A short name for a pasted link: "Twitch firstwa", "YouTube @FIRSTWashington live", "YouTube video <id>"."""
    u = urllib.parse.urlsplit(c["url"]) if "://" in c["url"] else None
    if c["provider"] == "twitch":
        tail = u.path.strip("/") if u else c["url"]
        return f"Twitch {tail.replace('videos/', 'video ')}"
    if c["provider"] == "youtube":
        if c.get("playlist"): return f"YouTube playlist {c['playlist']}"
        if c.get("video"): return f"YouTube {'stream' if c['kind'] == 'livestream' else 'video'} {c['video']}"
        base = (u.path.strip("/").split("/") or [""])[0] if u else ""
        if base in ("channel", "c", "user"): base = "/".join(u.path.strip("/").split("/")[:2])
        return f"YouTube {base} {'live' if c['kind'] == 'livestream' else 'uploads'}".strip()
    if c["provider"] == "file": return os.path.basename(c["url"]) or c["url"]
    return (u.hostname if u else None) or c["url"]


def add(doc, url, role="wide", label=None, coverage=None, by=None, primary=False, station=None, live=None):
    c = _checked(url, live)
    for o in doc.get("sources", []):
        if o.get("url") == c["url"] and not o.get("dismissed"):    # a removed TBA copy of the link doesn't count
            raise ValueError("that link is already listed" + (" (from TBA): change what it is instead" if o.get("origin") == "tba" else ""))
    if role not in ROLES: raise ValueError(f"role must be one of {ROLES}")
    if station not in (None, *STATIONS): raise ValueError(f"station must be one of {STATIONS}")
    sid = base = "url-" + hashlib.sha1(c["url"].encode()).hexdigest()[:8]
    n = 1
    while get(doc, sid): n += 1; sid = f"{base}-{n}"        # a replaced link kept its id: never reuse it
    s = dict(id=sid, origin="admin", kind=c["kind"], provider=c["provider"], url=c["url"], role=role,
             coverage=coverage or {"mode": "all"}, enabled=True, label=label or default_label(c),
             sample=dict(video=c.get("video") or c["url"], t=None), updated_at=time.time(), updated_by=by)
    if c.get("playlist"): s["playlist"] = c["playlist"]
    if station and role == "alliance-side": s["station"] = station
    if primary:
        for o in doc.get("sources", []): o["primary"] = False
        s["primary"] = True
    doc.setdefault("sources", []).append(s)
    return s


def set_url(doc, sid, url, by=None, live=None):
    """Replace the link of an admin source in place: same id, so its layout boxes and camera calibration
    (layout.json / camera.json carry the source id) stay with it.  A TBA source's link comes from TBA: remove
    it (discard) and add the new link instead."""
    s = get(doc, sid)
    if s is None: raise KeyError(sid)
    if s.get("origin") != "admin": raise ValueError("this link comes from TBA: remove it and add yours instead")
    c = _checked(url, live)
    if any(o is not s and o.get("url") == c["url"] and not o.get("dismissed") for o in doc.get("sources", [])):
        raise ValueError("that link is already listed")
    old, auto = s.get("url"), {"", s.get("url")}
    try: auto.add(default_label(classify_url(old)))
    except (ValueError, TypeError): pass
    s.update(kind=c["kind"], provider=c["provider"], url=c["url"], sample=dict(video=c.get("video") or c["url"], t=None),
             updated_at=time.time(), updated_by=by)
    if (s.get("label") or "") in auto: s["label"] = default_label(c)      # an automatic name follows the link
    s.pop("playlist", None)
    if c.get("playlist"): s["playlist"] = c["playlist"]
    if c["kind"] != "per_match": s.pop("videos", None)
    return s


def discard(doc, sid, by=None):
    """Video page "Remove": an admin source is deleted; a TBA source (webcast, upload family) is kept but switched
    off and marked dismissed, so TBA discovery (auto_discover / merge) does not add it back and the recorder
    does not fall back to TBA's webcasts.  -> "removed" | "dismissed" | None (no such source)."""
    s = get(doc, sid)
    if s is None: return None
    if s.get("origin") == "admin":
        remove(doc, sid); return "removed"
    s.update(enabled=False, dismissed=True, primary=False, updated_at=time.time(), updated_by=by)
    return "dismissed"


_MATCH_KEY = re.compile(r"^[0-9]{4}[a-z0-9]{1,16}_[a-z0-9]{1,12}$")


def clean_sample(v):
    """PATCH sample -> {video, match, t} with video validated like a pasted URL (classify_url / allowed roots)."""
    if not isinstance(v, dict): raise ValueError("sample must be an object")
    out = {}
    vid = v.get("video")
    if vid is not None:
        if not isinstance(vid, str) or len(vid) > 2000: raise ValueError("sample.video must be a string")
        if _YT_ID.match(vid.strip()):
            out["video"] = vid.strip()
        else:
            c = classify_url(vid)
            if c["kind"] == "file" and not file_allowed(c["url"]):
                raise ValueError("file sources must be under the media or data directory")
            if c["provider"] == "http": safe_public_url(c["url"], cache_s=0)
            out["video"] = c.get("video") or c["url"]
    mk = v.get("match")
    if mk is not None:
        if not isinstance(mk, str) or not _MATCH_KEY.match(mk): raise ValueError("sample.match must be a match key")
        out["match"] = mk
    t = v.get("t")
    if t is not None:
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not (0 <= float(t) <= 86400):
            raise ValueError("sample.t must be seconds (0..86400)")
        out["t"] = float(t)
    else:
        out["t"] = None
    return out


def update(doc, sid, patch, by=None):
    s = get(doc, sid)
    if s is None: raise KeyError(sid)
    for k, v in patch.items():
        if k not in USER_FIELDS or v is None: continue
        if k == "role" and v not in ROLES: raise ValueError(f"role must be one of {ROLES}")
        if k == "sample": v = clean_sample(v)
        if k == "station":
            if v == "": s.pop("station", None); continue
            if v not in STATIONS: raise ValueError(f"station must be one of {STATIONS}")
        if k == "dismissed":
            v = bool(v)
            if not v: s.pop("dismissed", None); continue
        if k == "dynamic" and not isinstance(v, bool): raise ValueError("dynamic must be true, false or null (auto)")
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
        return media_ok(vid), (s.get("sample") or {}).get("t") or 60.0
    smp = s.get("sample") or {}
    if s.get("kind") == "livestream" and s.get("_ev") and smp.get("video") in (None, "", s.get("url")):
        p, t = recorded_media(s["_ev"], s)          # the recorder's copy: local, seekable, many frames
        if p: return p, t
    v = smp.get("video") or s.get("url")
    return (media_ok(v) if v else None), smp.get("t")


def live_root():
    return os.environ.get("LIVE_DIR") or os.path.join(os.environ.get("MEDIA", "/media"), "live")


def recorded_media(ev, s, root=None):
    """Local video of a livestream source recorded by the recorder service (MEDIA/live/<ev>/<video>/, see
    live/liveingest.py): the newest match clip (t = 60 s: mid-match, robots on the field), else the newest
    complete 60 s segment (t = 30 s).  -> (path, t) or (None, None).  Frame grabs, layout suggestions and the
    calibration wizard use it for a livestream source: a live URL cannot be seeked."""
    base = os.path.join(root or live_root(), ev)
    try:
        dirs = [os.path.join(base, d) for d in os.listdir(base)]
    except OSError:
        return None, None
    best = []
    for d in dirs:
        try:
            with open(os.path.join(d, "meta.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if meta.get("source") != s.get("id") and meta.get("url") != s.get("url"): continue
        best.append((meta.get("started") or os.path.getmtime(d), d))
    for _, d in sorted(best, reverse=True):
        clips = sorted((os.path.join(d, "clips", f) for f in _ls(os.path.join(d, "clips")) if f.endswith(".mp4")),
                       key=os.path.getmtime)
        clips = [c for c in clips if os.path.getsize(c) > 100_000]
        if clips: return clips[-1], 60.0
        p = recent_sample(d)
        if p: return p, 90.0
    return None, None


def recent_sample(d, seconds=180.0, max_age=120.0):
    """<d>/sample.ts: the last ~`seconds` of real recorded chunks, concatenated (a Twitch segment file is only
    24 s; frame grabs, layout suggestions and the calibration wizard want a few minutes to pick from).
    Rebuilt when older than max_age.  -> path or None."""
    out = os.path.join(d, "sample.ts")
    try:
        if time.time() - os.path.getmtime(out) < max_age and os.path.getsize(out) > 100_000: return out
    except OSError:
        pass
    rows, n = [], {}
    try:
        with open(os.path.join(d, "chunks.jsonl")) as f:
            for line in f:
                try: rows.append(json.loads(line))
                except ValueError: pass
    except OSError:
        return None
    for r in rows:
        if r.get("pts") is not None: n[r["pts"]] = n.get(r["pts"], 0) + 1
    rows = sorted((r for r in rows if r.get("pts") is not None and n[r["pts"]] == 1 and r.get("file")), key=lambda r: r["sq"])
    rows = rows[:-1]                                         # the newest chunk may still be written
    got, keep = 0.0, []
    for r in reversed(rows):
        if not os.path.exists(os.path.join(d, r["file"])): break
        keep.append(r); got += r.get("dur") or 5.0
        if got >= seconds: break
    if got < 10: return None
    tmp = out + ".tmp"
    with open(tmp, "wb") as o:
        for r in reversed(keep):
            with open(os.path.join(d, r["file"]), "rb") as f:
                f.seek(r["off"]); o.write(f.read(r["size"]))
    os.replace(tmp, out)
    return out


def _ls(d):
    try:
        return os.listdir(d)
    except OSError:
        return []


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
        __import__("numpy"); __import__("cv2")
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


def run_pending(data, kinds=None, log=print, max_age=3600, exclude=()):
    """Run every queued job once (oldest first; kinds: only these, exclude: not these).  Returns the number run."""
    d = jobs_dir(data)
    if not os.path.isdir(d): return 0
    n = 0
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"): continue
        try:
            j = json.load(open(os.path.join(d, f)))
        except (OSError, ValueError):
            continue
        if j.get("state") != "queued" or (kinds and j["kind"] not in kinds) or j.get("kind") in exclude: continue
        if time.time() - j.get("created", 0) > max_age:
            j.update(state="error", error="expired (no job runner picked it up)"); _jsave(os.path.join(d, f), j); continue
        if not _claim(data, j["id"]): continue
        run_job(data, j, log); n += 1
    return n


def job_loop(data, poll=0.7, log=print, stop=None):
    """For the worker: `threading.Thread(target=sources.job_loop, args=(DATA,), daemon=True).start()`.
    Quick jobs (frames, suggestions, calibration) here; minutes-long ones (SLOW_JOBS: the auto set-up) and the
    auto set-up sweep in a second thread, so a Grab frame never waits behind them."""
    threading.Thread(target=slow_job_loop, args=(data,), kwargs=dict(log=log, stop=stop), daemon=True).start()
    while not (stop and stop.is_set()):
        try:
            if not run_pending(data, log=log, exclude=SLOW_JOBS): time.sleep(poll)
        except Exception:
            traceback.print_exc(); time.sleep(5)


def slow_job_loop(data, poll=2.0, log=print, stop=None):
    """SLOW_JOBS one at a time + every AUTO_SETUP_POLL s the auto set-up sweep (auto_setup_sweep)."""
    last = 0.0
    while not (stop and stop.is_set()):
        try:
            if AUTO_SETUP and time.time() - last > AUTO_SETUP_POLL:
                last = time.time()
                auto_setup_sweep(data, log=log)
            if not run_pending(data, kinds=SLOW_JOBS, log=log): time.sleep(poll)
        except Exception:
            traceback.print_exc(); time.sleep(10)


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


def plan_group(ev_dir, sid):
    """A detected video type group of the event's plan (videoplan.groups: one layout per type + channel) as a
    source-like dict, or None."""
    try:
        with open(os.path.join(ev_dir, "video_plan.json")) as f:
            g = (json.load(f).get("groups") or {}).get(sid)
        return dict(g) if g else None
    except (OSError, ValueError, AttributeError):
        return None


def find_source(ev_dir, sid, doc=None):
    """A source of sources.json, else a plan group with that id, else None."""
    s = get(doc if doc is not None else load(ev_dir), sid)
    return s if s is not None else plan_group(ev_dir, sid)


def _source(data, ev, sid):
    s = find_source(_ev_dir(data, ev), sid)
    if s is None: raise ValueError(f"unknown source {sid}")
    s = dict(s, _ev=ev)
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


def _live_url(LY, s, media):
    return s.get("kind") == "livestream" and LY.is_remote(media)


def _grab_many(LY, s, media, t0, n, step):
    """n frames of a source -> (frames, times).  A live URL (nothing recorded yet) cannot be seeked: read it
    forward once, one frame every `step` s (n x step s of real time)."""
    if _live_url(LY, s, media):
        got = LY.grab_dense(media, 0.0, seconds=n * step, hz=1.0 / step)[:n]
        return [f for _, f in got], [t for t, _ in got]
    ts, dur = _times(LY, media, t0, n)
    if len(ts) == 1: ts = [ts[0] + k * step for k in range(n)]
    got = LY.grab_frames(media, ts)
    return [f for _, f in got], ts


def job_frame(data, ev, p, log=print):
    """Grab one frame of a source (t seconds, default: the source's sample time / 60 s) -> frames/<sid>.jpg."""
    np, cv2, LY, CB = _live_imports()
    s = _source(data, ev, p["source"])
    media, t0 = media_for(s, p.get("match"))
    if not media: raise ValueError("this source has no video yet (no per-match upload found)")
    t = p.get("t", t0)
    if _live_url(LY, s, media):
        t = None                                       # live URL (nothing recorded yet): the current frame
    elif t is None:
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
    fr, ts = _grab_many(LY, s, media, t0, int(p.get("n", 10)), 8.0)
    if len(fr) < 3: raise ValueError("could not read enough frames from the video")
    dense = LY.grab_dense(media, 0.0 if _live_url(LY, s, media) else ts[len(ts) // 2], seconds=12.0, hz=10.0)
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
    live = s.get("kind") == "livestream" and not LY.is_remote(media)
    fr, ts = _grab_many(LY, s, media, t0, int(p.get("n", 16 if live else 8)), 6.0)
    if not fr: raise ValueError("could not read frames")
    if live and len(fr) >= 6:          # a program feed cuts away (close-ups, results screen): keep the main view
        fr, ts = _main_view(np, cv2, fr, ts)
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


def _main_view(np, cv2, fr, ts):
    """Frames showing the view most frames show (distance to the median thumbnail): drops cut-aways."""
    th = np.stack([cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (96, 54), interpolation=cv2.INTER_AREA).astype(np.float32)
                   for f in fr])
    d = np.abs(th - np.median(th, 0)).mean((1, 2))
    keep = [i for i in range(len(fr)) if d[i] <= max(12.0, 2.0 * float(np.median(d)))]
    return ([fr[i] for i in keep], [ts[i] for i in keep]) if len(keep) >= 3 else (fr, ts)


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
    try:
        import videoplan as VP
        if p.get("_matches") is not None:
            VP.save_plan(_ev_dir(data, ev), VP.build_plan(ev, _ev_dir(data, ev), p["_matches"], d, VP.load_cache(data),
                                                         live=VP.live_clips(ev)))
    except Exception:
        traceback.print_exc()
    return dict(found=len(found), sources=d["sources"])


def job_plan(data, ev, p, log=print):
    """Video page "Check videos": classify every candidate upload not checked yet (a few low-res frames
    each, see videoplan.classify_video) and rebuild the per-match plan."""
    import videoplan as VP
    matches = p.get("_matches") or []
    doc = load(_ev_dir(data, ev))
    recs = VP.load_cache(data)
    if not p.get("only"):
        recs.update(VP.classify_many(data, [v for v in VP.todo(matches, doc, recs)], workers=int(p.get("workers", 3)), log=log))
        try:                                       # channels of the classified uploads: what TBA does not link
            VP.scan_channels(data, ev, matches, recs, log=log, max_age=0)
        except Exception as e:
            log(f"{ev}: channel scan failed: {e!r:.160}")
    vids = VP.todo(matches, doc, recs, scan=VP.load_scan(_ev_dir(data, ev))) if not p.get("only") else \
        [v for v in p["only"] if _YT_ID.match(str(v))]
    done = VP.classify_many(data, vids, workers=int(p.get("workers", 3)), log=log) if vids else {}
    plan = VP.build_plan(ev, _ev_dir(data, ev), matches, load(_ev_dir(data, ev)), VP.load_cache(data), live=VP.live_clips(ev))
    VP.save_plan(_ev_dir(data, ev), plan)
    return dict(checked=len(done), summary=plan["summary"])


def auto_discover(data, ev, event, matches, max_age=6 * 3600, live_max_age=1200, log=print, meta_fn=oembed):
    """Worker (every poll, throttled): run TBA discovery on its own -- event webcasts become livestream
    sources and new per-match uploads are titled -- when sources.json was never discovered or is older than
    max_age (live_max_age while the event is on).  Returns True when it ran."""
    evd = _ev_dir(data, ev)
    doc = load(evd)
    import datetime as _dt
    today = _dt.date.today().isoformat()
    on = bool(event) and (event.get("start_date") or "9") <= today <= (event.get("end_date") or event.get("start_date") or "0")
    age = time.time() - (doc.get("discovered_at") or 0)
    if age < (live_max_age if on else max_age): return False
    cache_p = os.path.join(data, "cache", "ytmeta_oembed.json")
    try: cache = json.load(open(cache_p))
    except (OSError, ValueError): cache = {}
    found = discover(ev, event or {}, matches or [], meta_fn, cache, full=True, log=log)
    os.makedirs(os.path.dirname(cache_p), exist_ok=True)
    try: _jsave(cache_p, cache)
    except OSError: pass
    d = merge(load(evd), found)
    save(evd, d)
    n_ls = sum(1 for x in found if x["kind"] == "livestream")
    log(f"{ev}: video sources discovered from TBA ({n_ls} webcast{'s' if n_ls != 1 else ''}, "
        f"{len(found) - n_ls} upload famil{'ies' if len(found) - n_ls != 1 else 'y'})")
    return True


def job_index_playlist(data, ev, p, log=print):
    """Pasted playlist / channel: list its videos (yt-dlp --flat-playlist) and map titles to match keys."""
    doc = load(_ev_dir(data, ev)); s = get(doc, p["source"])
    if s is None or s.get("kind") != "per_match" or not s.get("url"): raise ValueError("not a playlist source")
    url = safe_public_url(media_ok(s["url"]))
    r = subprocess.run([YTDLP, "-4", "--flat-playlist", "--no-warnings", "--print", "%(id)s\t%(title)s",
                        "--playlist-end", "600", "--", url], capture_output=True, text=True, timeout=600)
    vids = {}
    for line in r.stdout.strip().split("\n"):
        if "\t" not in line: continue
        vid, title = line.split("\t", 1)
        if not _YT_ID.match(vid): continue
        k = match_key_from_title(title, ev, p.get("_matches"))
        if k and k not in vids: vids[k] = vid
    doc = load(_ev_dir(data, ev)); s = get(doc, p["source"])
    s["videos"] = vids
    if vids:
        mk = sorted(vids, key=lambda k: _order({"key": k}))[len(vids) // 2]
        s["sample"] = dict(video=vids[mk], match=mk, t=60.0)
    save(_ev_dir(data, ev), doc)
    return dict(n=len(vids))


# ---------------------------------------------------------------- automatic set-up of a stream (dynpose.auto_setup)
# A pasted / discovered livestream sets itself up: once the recorder has a match clip (or a few minutes) of it, the
# worker's job thread runs dynpose.auto_setup on that local copy (graphics, the fixed wide camera re-fitted like the
# wizard, field box, clock box, view reference, program feed or not) and keeps the result as a CANDIDATE:
#   DATA/events/<ev>/auto_setup/<sid>/  state.json  camera_<sid>.json  layout.json  view_<sid>.png  overlay.jpg
#                                       auto_setup.json (dynpose report)  job.json (the latest job)
# The Video page shows it on the stream's card (projected field on the camera's picture) with one-click Accept
# (accept_auto: camera + layout + view_<sid>.png, like the wizard's accept).  `program_feed` is written to the
# source at once (a detection, not a setting); `dynamic` (null = auto) follows it unless an admin forced it.
AUTO_SETUP = os.environ.get("AUTO_SETUP", "1") != "0"           # 0 = no automatic runs (the button still works)
AUTO_SETUP_POLL = float(os.environ.get("AUTO_SETUP_POLL", "60"))
AUTO_SETUP_MIN_S = float(os.environ.get("AUTO_SETUP_MIN_S", "90"))   # recorded seconds needed without a match clip
AUTO_RETRY_S = 1800.0                                           # a failed automatic run is retried after this
AUTO_SETUP_DAYS = float(os.environ.get("AUTO_SETUP_DAYS", "2"))   # the sweep only looks at recordings this recent
SLOW_JOBS = {"auto_setup"}                                      # minutes of CPU: their own runner thread
AUTO_KINDS = ("livestream", "file")
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def auto_dir(ev_dir, sid):
    if not _SID_RE.match(sid or ""): raise ValueError("bad source id")
    return os.path.join(ev_dir, "auto_setup", sid)


def _jread(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def auto_state(ev_dir, sid, data=None):
    """The source's auto set-up: the last result (state.json) + the latest job's state -> dict or None.
    {status: ok | no_camera | error, message, at, media, media_kind, program_feed, feed, fixed, scoreboard, clock,
     vs_saved, accepted_at, accepted_by, job: {id, state, error?}, running: bool}."""
    try:
        d = auto_dir(ev_dir, sid)
    except ValueError:
        return None
    st = _jread(os.path.join(d, "state.json"))
    jr = _jread(os.path.join(d, "job.json"))
    if jr and data:
        j = get_job(data, jr.get("id")) or {}
        jr = dict(id=jr.get("id"), state=j.get("state") or "gone", created=j.get("created"), started=j.get("started"),
                  error=j.get("error"), auto=jr.get("auto"))
    if st is None and jr is None: return None
    out = dict(st or {}, source=sid)
    if jr:
        now = time.time()
        out["job"] = jr
        # a job file can stay "queued" (no runner) or "running" (the runner died): not running after a while
        out["running"] = (jr.get("state") == "queued" and now - (jr.get("created") or 0) < 3600) or \
                         (jr.get("state") == "running" and now - (jr.get("started") or jr.get("created") or 0) < 1800)
    return out


def _stream_dirs(ev, s, root=None):
    """Recorder output dirs of a livestream source (meta.json names it), newest first."""
    base = os.path.join(root or live_root(), ev)
    out = []
    for x in _ls(base):
        d = os.path.join(base, x)
        meta = _jread(os.path.join(d, "meta.json"))
        if not isinstance(meta, dict): continue
        if meta.get("source") != s.get("id") and meta.get("url") != s.get("url"): continue
        out.append((meta.get("started") or os.path.getmtime(d), d))
    return [d for _, d in sorted(out, reverse=True)]


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _recorded_s(d, last=400):
    """Seconds of real video among the last `last` chunks of a recorder dir."""
    try:
        with open(os.path.join(d, "chunks.jsonl")) as f:
            rows = f.readlines()[-last:]
    except OSError:
        return 0.0
    got = 0.0
    for x in rows:
        try: r = json.loads(x)
        except ValueError: continue
        if r.get("pts") is not None: got += r.get("dur") or 5.0
    return got


def setup_ready(ev, s, root=None):
    """Can the auto set-up run now?  Like setup_media without building anything (the API's media mount is
    read-only) -> (kind: clip | sample | file, None) or (None, why)."""
    if s.get("kind") != "livestream":
        p, why = setup_media(ev, s, root, build=False)
        return (why, None) if p else (None, why)
    dirs = _stream_dirs(ev, s, root)
    if not dirs: return None, "nothing recorded from this stream yet"
    for d in dirs:
        if any(f.endswith(".mp4") for f in _ls(os.path.join(d, "clips"))): return "clip", None
    got = max(_recorded_s(d) for d in dirs)
    if got >= AUTO_SETUP_MIN_S: return "sample", None
    return None, f"only {got:.0f} s recorded so far (needs {AUTO_SETUP_MIN_S:.0f} s)"


def setup_media(ev, s, root=None, build=True):
    """A local video of a source for the auto set-up -> (path, kind: clip | sample | file) or (None, why).
    Livestream: the recorder's newest match clip, else (build=True) the last ~3 min recorded (recent_sample;
    needs AUTO_SETUP_MIN_S of recording)."""
    if s.get("kind") == "file":
        p = s.get("url")
        return (p, "file") if p and file_allowed(p) and os.path.exists(p) else (None, "the file is not readable")
    if s.get("kind") != "livestream":
        return None, "automatic set-up works on recorded streams and files: use Auto-calibrate for this video"
    dirs = _stream_dirs(ev, s, root)
    if not dirs: return None, "nothing recorded from this stream yet"
    for d in dirs:
        clips = [os.path.join(d, "clips", f) for f in _ls(os.path.join(d, "clips")) if f.endswith(".mp4")]
        clips = [c for c in clips if os.path.getsize(c) > 100_000]
        if clips: return max(clips, key=os.path.getmtime), "clip"
    if not build: return None, "no match clip yet"
    got = 0.0
    for d in dirs:
        got = _recorded_s(d)
        if got < AUTO_SETUP_MIN_S: continue
        p = recent_sample(d)
        if p: return p, "sample"
    return None, f"only {got:.0f} s recorded so far (needs {AUTO_SETUP_MIN_S:.0f} s)"


def stream_calibration(ev_dir, sid):
    """The saved calibration of a source's camera in an event dir: camera_<sid>.json, else camera.json made on it
    (its "source") -> path or None (as liveingest.stream_camera, one dir)."""
    p = os.path.join(ev_dir, f"camera_{sid}.json")
    if os.path.exists(p): return p
    p = os.path.join(ev_dir, "camera.json")
    return p if (_jread(p, {}) or {}).get("source") == sid else None


def accept_target(ev_dir, sid):
    """Which file an accepted stream camera goes to: camera_<sid>.json when that exists (liveingest reads it first)
    or camera.json belongs to another video (the uploads' camera: never overwritten), else camera.json (with
    "source": the wizard's convention)."""
    if os.path.exists(os.path.join(ev_dir, f"camera_{sid}.json")): return f"camera_{sid}.json"
    c = _jread(os.path.join(ev_dir, "camera.json"))
    if c is not None and c.get("source") != sid: return f"camera_{sid}.json"
    return "camera.json"


AUTO_KINDS_LAYOUT = ("scoreboard", "mask", "clock", "field")    # what an accepted auto set-up replaces in layout.json


def accept_auto(ev_dir, sid, by=None, now=None):
    """Video page "Accept" of an auto set-up candidate (stdlib: the API runs it): the camera -> accept_target()
    (old file kept as .bak-<ts>; calib.accepted_by / accepted_at), its graphics / clock / field boxes -> this
    source in layout.json (the admin's station / inset boxes kept; old file kept as .bak-<ts>), the view reference
    -> view_<sid>.png, the carried pose (pose_latest_<sid>.json, + pose_latest.json for camera.json) moved aside.
    -> dict(camera=file, layout=True, view=file, at).  Raises ValueError when there is nothing to accept."""
    now = now or time.time()
    d = auto_dir(ev_dir, sid)
    st = _jread(os.path.join(d, "state.json"))
    cam = _jread(os.path.join(d, f"camera_{sid}.json"))
    if not st or cam is None: raise ValueError("no automatic set-up with a camera to accept: run it, or calibrate by hand")
    ts = int(now)

    def wjson(p, obj):
        tmp = f"{p}.tmp{os.getpid()}"
        with open(tmp, "w") as f: json.dump(obj, f, indent=1)
        os.replace(tmp, p)

    name = accept_target(ev_dir, sid)
    dst = os.path.join(ev_dir, name)
    if os.path.exists(dst): shutil_copy(dst, f"{dst}.bak-{ts}")
    cam = dict(cam, source=sid, which="overhead")
    cam.setdefault("calib", {})
    cam["calib"].update(accepted_by=by, accepted_at=now, method=cam["calib"].get("method") or "dynpose auto_setup")
    wjson(dst, cam)
    for pl in [f"pose_latest_{sid}.json"] + (["pose_latest.json"] if name == "camera.json" else []):
        p = os.path.join(ev_dir, pl)
        if os.path.exists(p): os.replace(p, f"{p}.bak-{ts}")
    # layout: this source's boxes from the candidate
    lay_c = (_jread(os.path.join(d, "layout.json"), {}) or {}).get("sources", {}).get(sid)
    lp = os.path.join(ev_dir, "layout.json")
    lay = _jread(lp) or {"version": 1, "sources": {}}
    if lay_c:
        if os.path.exists(lp): shutil_copy(lp, f"{lp}.bak-{ts}")
        old = (lay.get("sources") or {}).get(sid) or {}
        W, H = lay_c.get("W") or 1920, lay_c.get("H") or 1080
        sx, sy = W / (old.get("W") or W), H / (old.get("H") or H)
        kept = [dict(r, box=[r["box"][0] * sx, r["box"][1] * sy, r["box"][2] * sx, r["box"][3] * sy])
                for r in old.get("regions") or [] if r.get("kind") not in AUTO_KINDS_LAYOUT]
        regs = [dict(r, suggested=False) for r in lay_c.get("regions") or []] + kept
        doc = load(ev_dir); src = get(doc, sid) or {}
        lay.setdefault("sources", {})[sid] = dict(W=W, H=H, role=src.get("role") or old.get("role"), regions=regs,
                                                  frame=old.get("frame"), updated_at=now, updated_by=by,
                                                  from_auto_setup=st.get("at"))
        if src.get("primary") or not lay.get("processing_source") or lay.get("processing_source") == sid:
            lay["processing_source"] = sid
        wjson(lp, lay)
    view = None
    vp = os.path.join(d, f"view_{sid}.png")
    if os.path.exists(vp):
        view = f"view_{sid}.png"; shutil_copy(vp, os.path.join(ev_dir, view))
    st.update(accepted_at=now, accepted_by=by, accepted_file=name)
    wjson(os.path.join(d, "state.json"), st)
    return dict(camera=name, layout=bool(lay_c), view=view, at=now)


def shutil_copy(a, b):
    import shutil
    shutil.copy2(a, b)


def _draw_setup(su, W, H):
    """The card's proof image: the projected field (calibrate.overlay_lines) on the fixed camera's picture, the
    graphics boxes, the clock and the field box -> BGR (<= 1280 px wide)."""
    np, cv2, LY, CB = _live_imports()
    base = su.get("view_ref") if su.get("view_ref") is not None else su.get("frame")
    im = base.copy() if base is not None else np.full((H, W, 3), 40, np.uint8)
    over = im.copy()
    for b in su.get("overlays") or []:
        cv2.rectangle(over, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (43, 146, 255), -1)
    im = cv2.addWeighted(over, 0.28, im, 0.72, 0)
    lw = max(2, int(round(W / 800)))
    for b in su.get("overlays") or []:
        cv2.rectangle(im, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (43, 146, 255), lw)
    regs = ((su.get("layout") or {}).get("sources") or {})
    regs = next(iter(regs.values()), {}).get("regions", []) if regs else []
    for r in regs:
        if r.get("kind") in ("clock", "field"):
            b = [int(v) for v in r["box"]]
            cv2.rectangle(im, (b[0], b[1]), (b[2], b[3]), (102, 207, 81) if r["kind"] == "clock" else (238, 212, 45), lw)
    if su.get("p") is not None:
        col = dict(grid=(255, 255, 255), field=(59, 212, 255), line=(255, 255, 255), hub_red=(95, 90, 255),
                   hub_blue=(255, 139, 62), tag=(102, 207, 81))
        for L in CB.overlay_lines(np.array(su["p"], float), W, H):
            k = L.get("kind")
            seg = []
            for p in L["pts"] + [None]:
                if p is None:
                    if len(seg) > 1:
                        cv2.polylines(im, [np.int32(seg)], False, col.get(k, (255, 255, 255)),
                                      1 if k == "grid" else lw, cv2.LINE_AA)
                    seg = []
                else:
                    seg.append(p)
    if im.shape[1] > 1280:
        im = cv2.resize(im, (1280, int(round(im.shape[0] * 1280 / im.shape[1]))), interpolation=cv2.INTER_AREA)
    return im


def _vs_saved(ev_dir, sid, su):
    """The candidate camera vs the saved calibration of this source (if any): median / max px over the carpet grid,
    as saved and re-aimed on the candidate's picture (the camera may have been nudged since)."""
    p = stream_calibration(ev_dir, sid)
    if not p or su.get("camera") is None: return None
    import dynpose as DP
    np, cv2, LY, CB = _live_imports()
    acc = _jread(p)
    if not acc or (acc.get("W"), acc.get("H")) != (su["camera"]["W"], su["camera"]["H"]): return None
    a, b = DP.Pose.from_raw(su["camera"]), DP.Pose.from_raw(acc)
    W, H = su["camera"]["W"], su["camera"]["H"]
    Hp = DP._picture(W, H, su.get("overlays"))[3]
    md, mx = DP.pose_px_diff(a, b, W, Hp)
    out = dict(file=os.path.basename(p), median_px=round(md, 1) if md is not None else None,
               max_px=round(mx, 1) if mx is not None else None)
    if su.get("view_ref") is not None:
        try:
            tags = {int(k): np.array(v["corners"]) for k, v in
                    CB.detect_tags_frames([cv2.cvtColor(su["view_ref"], cv2.COLOR_BGR2GRAY)]).items()}
            P, uv, _ = DP._points(tags, DP.tag_layout())
            if len(P) >= 8:
                md2, mx2 = DP.pose_px_diff(a, DP.refine_rotation(b, P, uv), W, Hp)
                out.update(reaimed_median_px=round(md2, 1), reaimed_max_px=round(mx2, 1))
        except Exception:
            traceback.print_exc()
    return out


def job_auto_setup(data, ev, p, log=print):
    """Auto set-up of a stream from its recording (dynpose.auto_setup) -> the candidate in
    DATA/events/<ev>/auto_setup/<sid>/ (see above); the detected program feed -> the source in sources.json."""
    import shutil
    sid = p["source"]
    evd = _ev_dir(data, ev)
    d = auto_dir(evd, sid)
    os.makedirs(d, exist_ok=True)
    t0 = time.time()
    try:
        s = _source(data, ev, sid)
        media, kind = setup_media(ev, s)
        if not media: raise ValueError(kind)
        np, cv2, LY, CB = _live_imports()
        import dynpose as DP
        log(f"{ev}: auto set-up of {sid} from {media}")
        su = DP.auto_setup(media, log=lambda *a: log(f"{ev} {sid}:", *a), source_id=sid)
        tmp = d + ".new"
        shutil.rmtree(tmp, ignore_errors=True)
        DP.write_setup(su, tmp, sid)
        rep = su["report"]
        W, H = rep.get("W") or 1920, rep.get("H") or 1080
        cv2.imwrite(os.path.join(tmp, "overlay.jpg"), _draw_setup(su, W, H), [cv2.IMWRITE_JPEG_QUALITY, 82])
        fx = rep.get("fixed") or {}
        regs = next(iter(su["layout"]["sources"].values()))["regions"]
        st = dict(status="ok" if su["camera"] is not None else "no_camera", at=time.time(), seconds=round(time.time() - t0, 1),
                  media=os.path.basename(media), media_kind=kind, W=W, H=H, duration=rep.get("duration"),
                  message=None if su["camera"] is not None else rep.get("fixed_why"),
                  program_feed=(rep.get("feed") or {}).get("program_feed"), feed=rep.get("feed"),
                  fixed=dict(on_air=fx.get("on_air"), shots=fx.get("shots"), tags=fx.get("tags"), tag_rms_px=fx.get("tag_rms_px"),
                             line_med_px=fx.get("line_med_px"), warnings=fx.get("warnings")) if fx else None,
                  scoreboard=rep.get("scoreboard"), clock=rep.get("clock"),
                  field=next((r["box"] for r in regs if r["kind"] == "field"), None), graphics=len(rep.get("overlays") or []),
                  pose=dict((k, (rep.get("pose") or {}).get(k)) for k in ("pose_ok", "shots", "n_cameras", "anchor_rms_px")),
                  vs_saved=_vs_saved(evd, sid, su), by=p.get("_by"), auto=bool(p.get("auto")))
        with open(os.path.join(tmp, "state.json"), "w") as f: json.dump(st, f, indent=1, default=float)
        if os.path.exists(os.path.join(d, "job.json")): shutil.copy2(os.path.join(d, "job.json"), os.path.join(tmp, "job.json"))
        old = d + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.replace(d, old); os.replace(tmp, d); shutil.rmtree(old, ignore_errors=True)
        doc = load(evd)
        if get(doc, sid) is not None and st["program_feed"] is not None:
            set_program_feed(doc, sid, rep.get("feed")); save(evd, doc)
        log(f"{ev}: auto set-up of {sid}: {st['status']}{' (' + str(st['message']) + ')' if st['message'] else ''}, "
            f"program feed {st['program_feed']}, {st['seconds']} s")
        return st
    except Exception as e:
        st = dict(status="error", at=time.time(), message=str(e)[-300:], seconds=round(time.time() - t0, 1), auto=bool(p.get("auto")))
        os.makedirs(d, exist_ok=True)
        prev = _jread(os.path.join(d, "state.json")) or {}
        if prev.get("status") in ("ok", "no_camera"):
            prev.update(last_error=st); st = prev                  # keep the last good candidate
        with open(os.path.join(d, "state.json"), "w") as f: json.dump(st, f, indent=1, default=float)
        raise


def submit_auto_setup(data, ev, sid, by=None, auto=False, inline=None):
    """Queue an auto set-up job for a source (remembered in auto_setup/<sid>/job.json before it can start); run it
    in a thread here when this process has the vision stack (inline None) -- else the worker's job thread does."""
    d = auto_dir(_ev_dir(data, ev), sid)
    os.makedirs(d, exist_ok=True)
    j = new_job(data, ev, "auto_setup", {"source": sid, "auto": bool(auto), "_by": by}, by)
    _jsave(os.path.join(d, "job.json"), dict(id=j["id"], at=time.time(), auto=bool(auto), by=by))
    if (vision_available() if inline is None else inline) and _claim(data, j["id"]):
        threading.Thread(target=run_job, args=(data, j), daemon=True).start()
    return j


def auto_setup_sweep(data, log=print, root=None):
    """Worker job thread, every AUTO_SETUP_POLL s: queue the auto set-up of the first enabled livestream / file
    source that has a recording and no result yet -- or whose result came from a between-matches sample without a
    fixed camera / clock and now has a match clip, or failed AUTO_RETRY_S ago.  At most one job at a time.
    -> the source id queued, or None."""
    if not AUTO_SETUP: return None
    base = os.path.join(data, "events")
    try:
        evs = sorted(os.listdir(base))
    except OSError:
        return None
    for ev in evs:
        evd = os.path.join(base, ev)
        if not os.path.exists(os.path.join(evd, "sources.json")): continue
        seen = set()                                # one run per link (TBA can list the admin's stream again)
        for s in sorted(load(evd).get("sources", []), key=lambda s: s.get("origin") != "admin"):
            if s.get("kind") not in AUTO_KINDS or not s.get("enabled", True) or s.get("dismissed"): continue
            if not _SID_RE.match(s.get("id") or "") or s.get("url") in seen: continue
            seen.add(s.get("url"))
            if s.get("kind") == "livestream" and not any(
                    time.time() - _mtime(os.path.join(d, "chunks.jsonl")) < AUTO_SETUP_DAYS * 86400
                    for d in _stream_dirs(ev, s, root)):
                continue                            # nothing recorded lately: an old event
            st = auto_state(evd, s["id"], data) or {}
            if st.get("running"): return None                     # one at a time
            if st.get("accepted_at"): continue
            stt = st.get("status")
            if stt == "error" and time.time() - (st.get("at") or 0) < AUTO_RETRY_S: continue
            if stt in ("ok", "no_camera"):              # done: again only from a match clip after a sample
                if not (st.get("media_kind") == "sample" and (stt == "no_camera" or not st.get("clock"))): continue
                media, kind = setup_media(ev, s, root, build=False)
                if kind != "clip": continue
            else:
                media, kind = setup_media(ev, s, root)
                if not media: continue
            log(f"{ev}: auto set-up of {s['id']} queued ({kind} {os.path.basename(media)})")
            submit_auto_setup(data, ev, s["id"], by="auto set-up", auto=True, inline=False)
            return s["id"]
    return None


JOBS = dict(frame=job_frame, suggest=job_suggest, detect=job_detect, fit=job_fit, overlay=job_overlay,
            discover=job_discover, index_playlist=job_index_playlist, plan=job_plan, auto_setup=job_auto_setup)
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
