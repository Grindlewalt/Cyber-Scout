"""Client sync (docs/SYNC.md): per-event file index, bundles and the media list.

Index: one `EventIndex` per event covers `DATA/site/api/<ev>/**` plus the global files directly in
`DATA/site/api/` (e.g. events.json). A refresh is one directory walk (stat only, ~5k files in a few ms); a file
is re-hashed only when its (size, mtime) changed, and its change sequence number moves only when the *content*
changed -- the worker rewrites every site file on each build, so mtimes alone would make every delta "everything".
State (sha256, seq per file, deletions) persists in SYNC_CACHE_DIR/index/<ev>.json so cursors survive restarts.
Cursor = "<generation>.<seq>"; an unknown generation, a seq from the future or one older than the trimmed
deletion history -> full manifest.

Bundles: deterministic gzip tars (gzip mtime 0, tar headers from the index), cached under
SYNC_CACHE_DIR/bundles/<hash of the path set + their sha256>.tar.gz and served with FileResponse (Range / If-Range
with the ETag = that hash), so a resumed download gets the same bytes. Old bundles go after BUNDLE_TTL, and the
oldest first while the cache is over SYNC_CACHE_MB.
"""
import gzip, hashlib, io, json, os, re, secrets, tarfile, threading, time

from .config import cfg
from .deps import MATCH_RE

SCAN_TTL = 2.0                  # s: concurrent manifest / bundle calls share one directory walk
DELETED_KEEP = 20000            # remembered deletions; older ones -> clients with older cursors get full:true
BUNDLE_TTL = 6 * 3600           # s: cached bundle files older than this are removed
PATH_RE = re.compile(r"^api/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


def _skip(name: str) -> bool:
    # dotfiles, and the worker's / API's atomic-write temp files (x.json.tmp, x.json.tmp1234)
    return name.startswith(".") or ".tmp" in name or name.endswith(".part")


def _sha256(path):
    h = hashlib.sha256()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class EventIndex:
    def __init__(self, ev, site, cache_dir):
        self.ev, self.site = ev, site
        self.api = os.path.join(site, "api")
        self.state_path = os.path.join(cache_dir, "index", f"{ev}.json")
        self.lock = threading.Lock()
        self.scanned = -1e9
        self.st = self._load()

    def _load(self):
        try:
            with open(self.state_path) as f:
                d = json.load(f)
            if d.get("v") == 1 and isinstance(d.get("files"), dict):
                return d
        except (OSError, ValueError):
            pass
        return {"v": 1, "gen": secrets.token_hex(4), "seq": 0, "floor": 0, "files": {}, "deleted": {}}

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = f"{self.state_path}.tmp{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(self.st, f, separators=(",", ":"))
        os.replace(tmp, self.state_path)

    def _walk(self):
        """-> {path relative to the site root: os.stat_result} for regular files (symlinks are never followed)."""
        out = {}
        try:
            with os.scandir(self.api) as it:
                for e in it:
                    if not _skip(e.name) and e.is_file(follow_symlinks=False):
                        out["api/" + e.name] = e.stat(follow_symlinks=False)
        except OSError:
            return out
        stack = [os.path.join(self.api, self.ev)]
        pre = len(self.api) + 1
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    if _skip(e.name):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        try:
                            out["api/" + e.path[pre:].replace(os.sep, "/")] = e.stat(follow_symlinks=False)
                        except OSError:
                            pass
        return out

    def refresh(self, force=False):
        with self.lock:
            if not force and time.monotonic() - self.scanned < SCAN_TTL:
                return
            cur = self._walk()
            files, deleted = self.st["files"], self.st["deleted"]
            nseq = self.st["seq"] + 1
            changed = dirty = False
            for p, s in cur.items():
                old = files.get(p)
                if old and old[0] == s.st_size and old[1] == s.st_mtime_ns:
                    continue
                sha = _sha256(os.path.join(self.site, p))
                if sha is None:                       # vanished mid-scan: next refresh decides
                    continue
                dirty = True
                if old and old[2] == sha:             # rewritten with the same bytes: not a change
                    old[0], old[1] = s.st_size, s.st_mtime_ns
                    continue
                files[p] = [s.st_size, s.st_mtime_ns, sha, nseq]
                deleted.pop(p, None)
                changed = True
            for p in [p for p in files if p not in cur]:
                del files[p]
                deleted[p] = nseq
                changed = True
            if changed:
                self.st["seq"] = nseq
                if len(deleted) > DELETED_KEEP:
                    old = sorted(deleted.items(), key=lambda kv: kv[1])[:len(deleted) - DELETED_KEEP]
                    for p, q in old:
                        del deleted[p]
                    self.st["floor"] = max(self.st["floor"], max(q for _, q in old))
            if dirty or changed:
                self._save()
            self.scanned = time.monotonic()

    def cursor(self):
        return f"{self.st['gen']}.{self.st['seq']}"

    def _since_seq(self, since):
        """seq for a usable cursor, else None (-> full)."""
        if not since:
            return None
        gen, _, n = since.partition(".")
        if gen != self.st["gen"] or not n.isdigit():
            return None
        n = int(n)
        return n if self.st["floor"] <= n <= self.st["seq"] else None

    def manifest(self, since=None):
        self.refresh()
        with self.lock:
            s = self._since_seq(since)
            full = s is None
            files = [{"path": p, "size": f[0], "sha256": f[2], "mtime": round(f[1] / 1e9, 3)}
                     for p, f in sorted(self.st["files"].items()) if full or f[3] > s]
            deleted = [] if full else sorted(p for p, q in self.st["deleted"].items() if q > s)
            return {"ev": self.ev, "cursor": self.cursor(), "full": full, "files": files, "deleted": deleted}

    def entries(self, paths):
        """-> {path: (size, mtime_ns, sha)} for the given paths; raises KeyError(path) for one not in the index."""
        with self.lock:
            out = {}
            for p in paths:
                f = self.st["files"].get(p)
                if f is None:
                    raise KeyError(p)
                out[p] = (f[0], f[1], f[2])
            return out

    def inside(self, path) -> bool:
        """realpath check: the file is in this event's directory, or a global file directly in api/."""
        full = os.path.realpath(os.path.join(self.site, path))
        api = os.path.realpath(self.api)
        evd = os.path.realpath(os.path.join(self.api, self.ev))
        if full.startswith(evd + os.sep):
            return path.startswith(f"api/{self.ev}/")
        return os.path.dirname(full) == api and path.count("/") == 1


_reg_lock = threading.Lock()
_reg: dict = {}


def index(ev) -> EventIndex:
    k = (cfg.SITE, cfg.SYNC_CACHE_DIR, ev)
    with _reg_lock:
        i = _reg.get(k)
        if i is None:
            i = _reg[k] = EventIndex(ev, cfg.SITE, cfg.SYNC_CACHE_DIR)
        return i


# ------------------------------------------------------------------ bundles
class BundleError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


_build_locks: dict = {}


def _key_lock(key):
    with _reg_lock:
        return _build_locks.setdefault(key, threading.Lock())


def bundle_dir():
    return os.path.join(cfg.SYNC_CACHE_DIR, "bundles")


def bundle(ev, paths):
    """Validate `paths` against the event's manifest and return (file path, etag key, n files, raw bytes, cached?).
    The file is a cache entry, or (index changed while building) a one-off temp file the caller deletes."""
    idx = index(ev)
    idx.refresh()
    want = sorted(set(paths))
    for p in want:
        if not isinstance(p, str) or not PATH_RE.match(p) or {".", ".."} & set(p.split("/")):
            raise BundleError(422, f"bad path: {str(p)[:200]}")
        if not idx.inside(p):
            raise BundleError(403, f"not a file of {ev}: {p[:200]}")
    try:
        ents = idx.entries(want)
    except KeyError as e:
        raise BundleError(404, f"not in the {ev} manifest (refresh the manifest): {str(e.args[0])[:200]}")
    raw = sum(e[0] for e in ents.values())
    if raw > cfg.SYNC_BUNDLE_MAX:
        raise BundleError(413, f"bundle too large: {raw} bytes > {cfg.SYNC_BUNDLE_MAX} (request fewer paths)")
    h = hashlib.sha256(f"v1\n{ev}\n".encode())
    for p in want:
        size, mt, sha = ents[p]
        h.update(f"{p}\0{sha}\0{mt // 10**9}\n".encode())
    key = h.hexdigest()[:40]
    d = bundle_dir()
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, key + ".tar.gz")
    with _key_lock(key):
        if os.path.exists(dst):
            try:
                os.utime(dst)                           # recently used: last to be evicted
            except OSError:
                pass
            return dst, key, len(want), raw, True
        tmp = os.path.join(d, f"{key}.{secrets.token_hex(4)}.part")
        stale = _write_tar(tmp, idx.site, want, ents)
        if stale:                                     # a file changed since the index: serve once, don't cache
            idx.refresh(force=True)
            return tmp, None, len(want), raw, False
        os.replace(tmp, dst)
    _clean(keep=dst)
    return dst, key, len(want), raw, True


def _write_tar(out, site, paths, ents) -> bool:
    stale = False
    with open(out, "wb") as raw:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0)
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
            for p in paths:
                size, mt, sha = ents[p]
                fd = os.open(os.path.join(site, p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as f:
                    data = f.read()
                if hashlib.sha256(data).hexdigest() != sha:
                    stale = True
                ti = tarfile.TarInfo(p)
                ti.size, ti.mtime, ti.mode = len(data), mt // 10**9, 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                tf.addfile(ti, io.BytesIO(data))
        gz.close()
    return stale


def _clean(keep=None):
    d = bundle_dir()
    now = time.time()
    try:
        ents = []
        for e in os.scandir(d):
            if not e.is_file(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
            if e.path != keep and (now - st.st_mtime > BUNDLE_TTL or
                                   (e.name.endswith(".part") and now - st.st_mtime > 3600)):
                try:
                    os.remove(e.path)
                except OSError:
                    pass
                continue
            if e.name.endswith(".tar.gz"):
                ents.append((st.st_mtime, st.st_size, e.path))
        budget = cfg.SYNC_CACHE_MB * 1024 * 1024
        total = sum(s for _, s, _ in ents)
        for mt, s, p in sorted(ents):
            if total <= budget:
                break
            if p == keep:
                continue
            try:
                os.remove(p)
                total -= s
            except OSError:
                pass
    except OSError:
        pass


# ------------------------------------------------------------------ media
_media_cache: dict = {}
MEDIA_TTL = 10.0


def _record_key(ev, rec):
    """Livestream match record -> TBA match key (same rule as live/liveingest.record_key)."""
    lv, s, n = rec.get("comp_level"), rec.get("set"), rec.get("number")
    if lv == "qm" and n: return f"{ev}_qm{n}"
    if lv == "sf" and s: return f"{ev}_sf{s}m1"
    if lv == "f" and n: return f"{ev}_f1m{n}"
    return None


def _mp4s(d):
    try:
        with os.scandir(d) as it:
            return sorted((e.name, e.stat()) for e in it
                          if e.name.endswith(".mp4") and not e.name.startswith(".") and e.is_file())
    except OSError:
        return []


def media(ev):
    """[{path, size, sha256?, kind, match}] for MEDIA/<ev>/ (wide, insets/, proxy/) and MEDIA/live/<ev>/*/clips/."""
    base = cfg.MEDIA
    k = (base, ev)
    c = _media_cache.get(k)
    if c and time.monotonic() - c[0] < MEDIA_TTL:
        return c[1]
    out = []
    d = os.path.join(base, ev)
    for name, st in _mp4s(d):
        m = name[:-4]
        out.append({"path": f"media/{ev}/{name}", "size": st.st_size, "kind": "wide",
                    "match": m if MATCH_RE.match(m) else None})
    for name, st in _mp4s(os.path.join(d, "insets")):
        m = name[:-4]
        out.append({"path": f"media/{ev}/insets/{name}", "size": st.st_size, "kind": "inset",
                    "match": m if MATCH_RE.match(m) else None})
    pidx = {}
    try:
        with open(os.path.join(d, "proxy", "index.json")) as f:
            pidx = json.load(f) or {}
    except (OSError, ValueError):
        pass
    for name, st in _mp4s(os.path.join(d, "proxy")):
        m = name[:-4]
        it = {"path": f"media/{ev}/proxy/{name}", "size": st.st_size, "kind": "proxy",
              "match": m if MATCH_RE.match(m) else None}
        e = pidx.get(m) if isinstance(pidx, dict) else None
        if isinstance(e, dict) and e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns and e.get("sha256"):
            it["sha256"] = e["sha256"]
        out.append(it)
    live = os.path.join(base, "live", ev)
    try:
        vids = sorted(e.name for e in os.scandir(live) if e.is_dir() and not e.name.startswith("."))
    except OSError:
        vids = []
    for vid in vids:
        names = {}
        try:
            for e in sorted(os.scandir(os.path.join(live, vid, "matches")), key=lambda e: e.name):
                if not e.name.endswith(".json"):
                    continue
                try:
                    with open(e.path) as f:
                        r = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(r, dict) and r.get("clip") and not r.get("not_a_match"):
                    mk = _record_key(ev, r)
                    if mk:
                        names[os.path.basename(str(r["clip"]))] = mk
        except OSError:
            pass
        for name, st in _mp4s(os.path.join(live, vid, "clips")):
            out.append({"path": f"media/live/{ev}/{vid}/clips/{name}", "size": st.st_size, "kind": "clip",
                        "match": names.get(name)})
    _media_cache[k] = (time.monotonic(), out)
    return out
