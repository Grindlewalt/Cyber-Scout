"""Local copies: safe extraction of sync bundles into the data mirror, and the mirror of the server's web app."""
import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
import zlib

from .util import ensure_dir, read_json, safe_relpath, target_path, write_json, fsync_dir

log = logging.getLogger("cyberscout.mirror")

_KIND = {tarfile.SYMTYPE: "symlink", tarfile.LNKTYPE: "hard link", tarfile.CHRTYPE: "character device",
         tarfile.BLKTYPE: "block device", tarfile.FIFOTYPE: "fifo"}


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
            if limit and n > limit:
                return None
    return h.hexdigest()


def replace_retry(src, dst, tries=10):
    """os.replace; on Windows a file being read at that moment (served to the browser) can't be replaced: retry."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2)


def write_stream_verified(src, dst, size, sha256, tmp_dir):
    """Copy at most `size` bytes from a file object into dst atomically if the bytes hash to sha256. -> (ok, why)."""
    ensure_dir(tmp_dir)
    fd, tmp = tempfile.mkstemp(prefix=".cs-tmp-", dir=tmp_dir)
    h, n = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as f:
            while n <= size:
                b = src.read(min(1 << 20, size + 1 - n))
                if not b:
                    break
                h.update(b)
                f.write(b)
                n += len(b)
            f.flush()
            os.fsync(f.fileno())
        if n != size:
            return False, f"size {n} != {size}"
        if sha256 and h.hexdigest() != sha256.lower():
            return False, "sha256 mismatch"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.chmod(tmp, 0o644)
        replace_retry(tmp, dst)
        tmp = None
        fsync_dir(os.path.dirname(dst))
        return True, ""
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def extract_bundle(tar_path, root, expected, tmp_dir):
    """Extract the regular files of a gzip'd tar into root, *only* those listed in `expected` {relpath: (sha256,size)}
    and only if their bytes verify. Rejects absolute paths, '..', anything outside api/ or media/, symlinks, hard links,
    devices and fifos (logged, never written). Never calls TarFile.extract*.
    Returns (written {relpath: (sha256,size)}, problems [str], corrupt bool)."""
    written, problems, corrupt = {}, [], False
    try:
        with open(tar_path, "rb") as fobj, tarfile.open(fileobj=fobj, mode="r|gz") as tf:
            for m in tf:
                name = m.name
                if m.isdir():
                    continue
                if not m.isfile():
                    problems.append(f"rejected {_KIND.get(m.type, 'non-regular')} member {name!r}")
                    continue
                rel = safe_relpath(name)
                if rel is None:
                    problems.append(f"rejected unsafe path {name!r}")
                    continue
                if rel not in expected:
                    problems.append(f"ignored unrequested member {name!r}")
                    continue
                sha, size = expected[rel]
                if m.size != size:
                    problems.append(f"{rel}: size {m.size} in bundle != manifest {size} (changed on the server?)")
                    continue
                try:
                    dst = target_path(root, rel)
                except ValueError as e:
                    problems.append(str(e))
                    continue
                src = tf.extractfile(m)
                try:
                    ok, why = write_stream_verified(src, dst, size, sha, tmp_dir)
                except PermissionError as e:          # the target is locked (Windows); next sync retries it
                    ok, why = False, f"could not replace the local file: {e}"
                if ok:
                    written[rel] = (sha, size)
                else:
                    problems.append(f"{rel}: {why}")
    except (tarfile.TarError, EOFError, zlib.error, OSError) as e:
        corrupt = True
        problems.append(f"bundle unreadable: {e.__class__.__name__}: {e}")
    return written, problems, corrupt


def remove_file(root, rel):
    try:
        p = target_path(root, rel)
    except ValueError:
        return
    try:
        os.remove(p)
    except FileNotFoundError:
        pass
    # prune now-empty directories up to the mirror root
    d, r = os.path.dirname(p), os.path.realpath(root)
    while d.startswith(r + os.sep):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)


# ---------------------------------------------------------------------------------------------------- web mirror
_HTML_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#]+)["']""", re.I)
_CSS_REF = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.I)
_JS_REF = re.compile(r"""["'`]([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:js|css|jpe?g|png|svg|webp|gif|ico|woff2?|"""
                     r"""webmanifest))(?:\?[^"'`]*)?["'`]""")
_SKIP_PREFIX = ("api/", "app/", "media/", "client/")
MAX_WEB_FILES, MAX_WEB_BYTES = 400, 64 * 1024 * 1024


def _local_ref(ref):
    ref = ref.strip()
    if not ref or ref.startswith(("data:", "http:", "https:", "//", "mailto:", "blob:", "javascript:")):
        return None
    ref = ref.split("#")[0].split("?")[0]
    if ref.startswith("./"):
        ref = ref[2:]
    ref = ref.lstrip("/")
    if not ref or ref.startswith(_SKIP_PREFIX):
        return None
    return safe_relpath(ref, roots=None)


class WebMirror:
    """web/<version>/ = a copy of the server's web app (index.html + every file it references, transitively through
    CSS url() and JS string literals). web/current.json names the complete copy being served; a new copy is built
    next to it and switched atomically, so a half-downloaded app is never served."""

    def __init__(self, web_dir, bundled_dir):
        self.web_dir = ensure_dir(web_dir)
        self.bundled_dir = bundled_dir
        cur = read_json(os.path.join(web_dir, "current.json"), {}) or {}
        d = os.path.join(web_dir, cur.get("dir", "")) if cur.get("dir") else None
        self.current = cur if d and os.path.isfile(os.path.join(d, "index.html")) else {}

    @property
    def version(self):
        return self.current.get("version")

    def root(self, use_mirror=True):
        if use_mirror and self.current:
            return os.path.join(self.web_dir, self.current["dir"])
        return self.bundled_dir

    def refresh(self, server, version, sem=None):
        safe_v = re.sub(r"[^A-Za-z0-9._-]", "_", str(version or "unknown"))[:40] or "unknown"
        dirname = f"v-{safe_v}-{int(time.time())}"
        tmp = os.path.join(self.web_dir, ".building-" + dirname)
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        t0 = time.time()
        try:
            n, total = self._fetch_all(server, tmp, sem)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        final = os.path.join(self.web_dir, dirname)
        os.replace(tmp, final)
        old = self.current.get("dir")
        self.current = {"version": version, "dir": dirname, "fetched_at": time.time(), "files": n, "bytes": total}
        write_json(os.path.join(self.web_dir, "current.json"), self.current)
        for d in os.listdir(self.web_dir):            # drop older copies (and leftovers of failed builds)
            if d != dirname and (d.startswith("v-") or d.startswith(".building-")):
                shutil.rmtree(os.path.join(self.web_dir, d), ignore_errors=True)
        log.info("web app %s mirrored: %d files, %d KB in %.1f s%s", version, n, total // 1024, time.time() - t0,
                 f" (replaces {old})" if old else "")
        return self.current

    def _get(self, server, rel, sem):
        if sem:
            with sem:
                return server.call("GET", "/" + rel, auth=False, timeout=60)
        return server.call("GET", "/" + rel, auth=False, timeout=60)

    def _fetch_all(self, server, out, sem):
        """Breadth-first over references, two requests at a time (slow links: latency dominates)."""
        import concurrent.futures
        seen, required = {"index.html"}, {"index.html"}
        wave, n, total = ["index.html"], 0, 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            while wave:
                results = list(ex.map(lambda rel: (rel, self._get(server, rel, sem)), wave))
                wave = []
                for rel, r in results:
                    ctype = (r.header("Content-Type") or "").lower()
                    # nginx answers unknown paths with index.html (SPA fallback): "missing" for a non-html ref
                    if r.status != 200 or (not rel.endswith(".html") and ctype.startswith("text/html")):
                        if rel in required:
                            raise RuntimeError(f"web app file /{rel} could not be downloaded (HTTP {r.status})")
                        log.debug("web mirror: skipping /%s (HTTP %s %s)", rel, r.status, ctype)
                        continue
                    data = r.body or b""
                    n += 1
                    total += len(data)
                    if n > MAX_WEB_FILES or total > MAX_WEB_BYTES:
                        raise RuntimeError("web app is unexpectedly large; refusing to mirror it")
                    dst = target_path(out, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(data)
                    for lr, req in self._refs(rel, data):
                        if lr not in seen:
                            seen.add(lr)
                            wave.append(lr)
                            if req:
                                required.add(lr)
        return n, total

    @staticmethod
    def _refs(rel, data):
        if not rel.endswith((".html", ".css", ".js", ".webmanifest")):
            return []
        text = data.decode("utf-8", "replace")
        base = os.path.dirname(rel)
        refs = []
        if rel.endswith(".html"):
            refs = [(x, True) for x in _HTML_REF.findall(text)]
        elif rel.endswith(".css"):
            refs = [(x, False) for x in _CSS_REF.findall(text)]
        elif rel.endswith(".webmanifest"):
            try:
                man = json.loads(text)
                refs = [(i.get("src", ""), False) for i in man.get("icons", []) if isinstance(i, dict)]
            except ValueError:
                pass
        if rel.endswith((".js", ".html")):
            refs += [(x, False) for x in _JS_REF.findall(text)]
        out = []
        for ref, req in refs:
            lr = _local_ref(ref)
            if lr and base and rel.endswith(".css"):          # url() is relative to the stylesheet
                lr = safe_relpath(base + "/" + lr, roots=None)
            if lr:
                out.append((lr, req and lr.endswith((".js", ".css"))))
        return out


def bundled_web_dir():
    """The copy of web/ shipped with the client (client/web_bundle, made by build/make_bundle.py; inside a PyInstaller
    build it is in sys._MEIPASS). In a source checkout without a bundle: the repo's web/."""
    import sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = []
    if getattr(sys, "_MEIPASS", None):
        cands.append(os.path.join(sys._MEIPASS, "web_bundle"))
    cands += [os.path.join(here, "web_bundle"), os.path.join(os.path.dirname(here), "web")]
    for c in cands:
        if os.path.isfile(os.path.join(c, "index.html")):
            return c
    return None
