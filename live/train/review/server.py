"""Label review / labelling web tool for a training project (stdlib only).

    python live/train/review/server.py PROJECT_DIR [--host 127.0.0.1] [--port 7590] [--token T]

or `serve(project_dir, ...)` (cstrain.py review). Reads frames/index.jsonl, labels/auto, labels/import; writes only
labels/review/<id>.json (temp file + rename). Every request needs the token: `?token=` on the first visit sets a
cookie, then the URL is clean. Meant for the LAN / Tailscale, never the public tunnel.
"""
import argparse, hmac, http.cookies, json, os, random, re, secrets, socket, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.normpath(os.path.join(HERE, "..", "..", "..", "web", "fonts", "instrument-sans.woff2"))
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
STATUSES = ("ok", "fixed", "skip", "bad")
QUEUES = ("uncertain", "gold", "random", "mine", "all")
MID = (0.25, 0.6)              # "mid-confidence" teacher boxes, for the uncertain queue's tie-break
MAX_BOXES = 500
MAX_BODY = 1 << 20
PALETTE = ["#ff7a00", "#00c2b2", "#ff3ea5", "#8b6cff", "#b5d800", "#0a9cff", "#ff4d4d", "#ffc400", "#2fbf71"]
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/review.js": ("review.js", "text/javascript; charset=utf-8"),
          "/review.css": ("review.css", "text/css; charset=utf-8")}
IMG_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
HEADERS = {
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
                               "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json_atomic(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_rel(rel):
    """A frame path from the index, normalised, if it stays inside the project and is an image; else None."""
    if not isinstance(rel, str) or not rel or "\x00" in rel or os.path.isabs(rel) or rel.startswith("\\"):
        return None
    norm = os.path.normpath(rel)
    if norm == ".." or norm.startswith(".." + os.sep) or os.path.splitext(norm)[1].lower() not in IMG_TYPES:
        return None
    return norm


def _auto_summary(d):
    """(u, bglabel-only boxes, mid-confidence teacher boxes, n boxes) of an auto label file."""
    boxes = d.get("boxes") or []
    n_bg = sum(1 for b in boxes if str(b.get("src", "")).startswith("bglabel"))
    ps = [b.get("p") for b in boxes if isinstance(b.get("p"), (int, float)) and not str(b.get("src", "")).startswith("bglabel")]
    n_mid = sum(1 for p in ps if MID[0] <= p < MID[1])
    u = d.get("u")
    if not isinstance(u, (int, float)):
        u = max([1 - abs(2 * p - 1) for p in ps] + ([0.5] if n_bg else [0.0]))
    return float(u), n_bg, n_mid, len(boxes)


class DirCache:
    """Summaries of the <id>.json files in one directory, re-read only when a file's mtime/size changes."""

    def __init__(self, path, summarise):
        self.path, self.summarise = path, summarise
        self.items, self.stamp, self.checked = {}, {}, 0.0

    def refresh(self, max_age=1.0):
        now = time.monotonic()
        if now - self.checked < max_age:
            return self.items
        self.checked = now
        seen = set()
        try:
            it = os.scandir(self.path)
        except OSError:
            self.items, self.stamp = {}, {}
            return self.items
        with it:
            for e in it:
                if not e.name.endswith(".json") or e.name.startswith("."):
                    continue
                k = e.name[:-5]
                try:
                    st = e.stat()
                except OSError:
                    continue
                seen.add(k)
                sig = (st.st_mtime_ns, st.st_size)
                if self.stamp.get(k) != sig:
                    d = _read_json(e.path)
                    if not isinstance(d, dict):
                        continue
                    self.items[k] = self.summarise(d)
                    self.stamp[k] = sig
        for k in list(self.items):
            if k not in seen:
                self.items.pop(k, None)
                self.stamp.pop(k, None)
        return self.items

    def put(self, k, d):
        self.items[k] = self.summarise(d)
        try:
            st = os.stat(os.path.join(self.path, k + ".json"))
            self.stamp[k] = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass


def _review_summary(d):
    diff = d.get("diff") if isinstance(d.get("diff"), dict) else {}
    return {"status": d.get("status"), "by": d.get("by") or "", "at": d.get("at") or 0,
            "n": len(d.get("boxes") or []), "diff": {k: int(diff.get(k) or 0) for k in ("added", "removed", "moved")}}


class Project:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.lock = threading.Lock()
        self.frames, self.order, self._idx_sig = {}, [], None
        self.auto = DirCache(os.path.join(self.root, "labels", "auto"), _auto_summary)
        self.review = DirCache(os.path.join(self.root, "labels", "review"), _review_summary)
        self.imported = DirCache(os.path.join(self.root, "labels", "import"), lambda d: len(d.get("boxes") or []))
        self.meta = {}
        self.refresh(force=True)

    # ---- loading
    def refresh(self, force=False):
        with self.lock:
            self.meta = _read_json(os.path.join(self.root, "project.json")) or {}
            p = os.path.join(self.root, "frames", "index.jsonl")
            try:
                st = os.stat(p)
                sig = (st.st_mtime_ns, st.st_size)
            except OSError:
                sig = None
            if force or sig != self._idx_sig:
                frames, order = {}, []
                if sig:
                    with open(p) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                r = json.loads(line)
                            except ValueError:
                                continue
                            i = str(r.get("id", "")) if isinstance(r, dict) else ""
                            if not ID_RE.match(i) or i in frames or not _safe_rel(r.get("path")):
                                continue                                    # unservable frames never show up
                            frames[i] = r
                            order.append(i)
                self.frames, self._idx_sig = frames, sig
                self.order = sorted(order, key=lambda i: self._pos(frames[i]))
            self.auto.refresh()
            self.review.refresh()
            self.imported.refresh()

    @staticmethod
    def _pos(r):
        t = r.get("t")
        return (str(r.get("event") or ""), str(r.get("match") or ""), str(r.get("video") or ""),
                float(t) if isinstance(t, (int, float)) else 0.0, str(r.get("id")))

    def classes(self):
        c = self.meta.get("classes") or ["robot"]
        return [str(x) for x in c] if isinstance(c, list) else ["robot"]

    def colors(self):
        n = len(self.classes())
        col = self.meta.get("colors")
        out = []
        for i, name in enumerate(self.classes()):
            v = None
            if isinstance(col, list) and i < len(col):
                v = col[i]
            elif isinstance(col, dict):
                v = col.get(name)
            out.append(v if isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{3,8}$", v) else PALETTE[i % len(PALETTE)])
        return out[:n]

    def image_path(self, fid):
        r = self.frames.get(fid)
        rel = _safe_rel(r.get("path")) if r else None
        return os.path.join(self.root, rel) if rel else None

    # ---- queues
    def queue(self, q, event="", match="", split="", view="", by="", seed=0, offset=0, limit=500):
        self.refresh()
        auto, rev = self.auto.items, self.review.items
        ids = self.order
        if event or match or split or view:
            ids = [i for i in ids if (not event or str(self.frames[i].get("event") or "") == event)
                   and (not match or str(self.frames[i].get("match") or "") == match)
                   and (not split or str(self.frames[i].get("split") or "") == split)
                   and (not view or str(self.frames[i].get("view") or "") == view)]
        if q == "uncertain":
            ids = [i for i in ids if i not in rev]
            imp = self.imported.items
            def key(i):
                a = auto.get(i)
                # no teacher output = most uncertain, unless the frame has imported human labels (a public set): last
                u, n_bg, n_mid = (a[0], a[1], a[2]) if a else (-1.0, 0, 0) if i in imp else (1.0, 0, 0)
                return (-u, -n_bg, -n_mid)
            ids = sorted(ids, key=key)                                      # stable: ties keep match/time order
        elif q == "gold":
            rank = {"test": 0, "val": 1}
            ids = [i for i in ids if i not in rev and self.frames[i].get("split") in rank]
            ids = sorted(ids, key=lambda i: rank[self.frames[i]["split"]])
        elif q == "random":
            ids = [i for i in ids if i not in rev]
            random.Random(int(seed)).shuffle(ids)
        elif q == "mine":
            ids = [i for i in ids if i in rev and (not by or rev[i]["by"] == by)]
            ids = sorted(ids, key=lambda i: -rev[i]["at"])
        total = len(ids)
        page = ids[max(0, offset):max(0, offset) + max(1, min(int(limit), 2000))]
        return {"q": q, "total": total, "offset": offset, "items": [self.item(i) for i in page]}

    def item(self, i):
        r = self.frames[i]
        a, rv = self.auto.items.get(i), self.review.items.get(i)
        return {"id": i, "event": r.get("event"), "match": r.get("match"), "t": r.get("t"), "split": r.get("split"),
                "view": r.get("view"), "u": round(a[0], 3) if a else None, "status": rv["status"] if rv else None}

    def frame(self, i):
        self.refresh()
        if i not in self.frames:
            return None
        d = lambda sub: _read_json(os.path.join(self.root, "labels", sub, i + ".json"))
        return {"frame": self.frames[i], "auto": d("auto"), "import": d("import"), "review": d("review")}

    def stats(self):
        self.refresh()
        rev = self.review.items
        splits, status, diff, people = {}, {s: 0 for s in STATUSES}, {"added": 0, "removed": 0, "moved": 0}, {}
        for i in self.order:
            sp = str(self.frames[i].get("split") or "none")
            s = splits.setdefault(sp, {"total": 0, "reviewed": 0})
            s["total"] += 1
            r = rev.get(i)
            if r:
                s["reviewed"] += 1
                if r["status"] in status:
                    status[r["status"]] += 1
                for k in diff:
                    diff[k] += r["diff"][k]
                people[r["by"] or "?"] = people.get(r["by"] or "?", 0) + 1
        return {"total": len(self.order), "reviewed": sum(1 for i in self.order if i in rev), "splits": splits,
                "status": status, "diff": diff, "people": people, "auto": len(self.auto.items),
                "imported": len(self.imported.items)}

    def info(self):
        self.refresh()
        events = {}
        for i in self.order:
            r = self.frames[i]
            ev = str(r.get("event") or "")
            events.setdefault(ev, set()).add(str(r.get("match") or ""))
        return {"name": self.meta.get("name") or os.path.basename(self.root), "classes": self.classes(),
                "colors": self.colors(), "events": {e: sorted(m) for e, m in sorted(events.items())},
                "splits": sorted({str(self.frames[i].get("split") or "") for i in self.order}),
                "views": sorted({str(self.frames[i].get("view") or "") for i in self.order})}

    # ---- writing
    def save_review(self, i, body):
        r = self.frames.get(i)
        if r is None:
            raise KeyError(i)
        status = body.get("status")
        if status not in STATUSES:
            raise ValueError("status must be one of " + ", ".join(STATUSES))
        by = re.sub(r"[^\w .@'-]", "", str(body.get("by") or "")).strip()[:40]
        if not by:
            raise ValueError("reviewer name required")
        raw = body.get("boxes") or []
        if not isinstance(raw, list) or len(raw) > MAX_BOXES:
            raise ValueError("boxes must be a list of at most %d" % MAX_BOXES)
        nc = len(self.classes())
        W = r.get("w") if isinstance(r.get("w"), (int, float)) and r.get("w") > 0 else None
        H = r.get("h") if isinstance(r.get("h"), (int, float)) and r.get("h") > 0 else None
        boxes = []
        for bx in raw:
            if not isinstance(bx, dict):
                raise ValueError("bad box")
            c, b = bx.get("c"), bx.get("b")
            if not isinstance(c, int) or isinstance(c, bool) or not 0 <= c < nc:
                raise ValueError("bad class %r" % (c,))
            if (not isinstance(b, list) or len(b) != 4
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == v for v in b)):
                raise ValueError("bad box coordinates")
            x0, x1 = sorted((float(b[0]), float(b[2])))
            y0, y1 = sorted((float(b[1]), float(b[3])))
            if W:
                x0, x1 = min(max(x0, 0.0), W), min(max(x1, 0.0), W)
            if H:
                y0, y1 = min(max(y0, 0.0), H), min(max(y1, 0.0), H)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            boxes.append({"c": c, "b": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]})
        if status in ("skip", "bad"):
            boxes = []                                                     # never training data
        rec = {"id": i, "boxes": boxes, "status": status, "by": by, "at": round(time.time(), 3)}
        d = body.get("diff")
        if isinstance(d, dict):
            rec["diff"] = {k: max(0, min(int(v), 10 * MAX_BOXES)) if isinstance(v, int) and not isinstance(v, bool) else 0
                           for k, v in ((k, d.get(k) or 0) for k in ("added", "removed", "moved"))}
        if isinstance(body.get("conf"), (int, float)) and 0 <= body["conf"] <= 1:
            rec["conf"] = round(float(body["conf"]), 3)
        path = os.path.join(self.root, "labels", "review", i + ".json")
        with self.lock:
            write_json_atomic(path, rec)
            self.review.put(i, rec)
        return rec


def make_handler(project, token, cookie_name):
    class H(BaseHTTPRequestHandler):
        server_version = "cs-review"
        sys_version = ""

        def log_message(self, fmt, *args):
            if os.environ.get("REVIEW_LOG"):
                sys.stderr.write("review: " + fmt % args + "\n")

        # ---- helpers
        def _send(self, code, body=b"", ctype="application/json", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, separators=(",", ":")).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in list(HEADERS.items()) + list((headers or {}).items()):
                self.send_header(k, v)
            if ctype.startswith("application/json"):
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _err(self, code, msg):
            self._send(code, {"error": msg})

        def _authed(self, qs):
            c = http.cookies.SimpleCookie()
            try:
                c.load(self.headers.get("Cookie") or "")
            except http.cookies.CookieError:
                pass
            got = c[cookie_name].value if cookie_name in c else ""
            hdr = self.headers.get("X-Review-Token") or ""
            for cand in (got, hdr):
                if cand and hmac.compare_digest(cand.encode(), token.encode()):
                    return True
            return False

        def _route(self):
            u = urlsplit(self.path)
            return u.path, parse_qs(u.query)

        # ---- GET
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path, qs = self._route()
            t = (qs.get("token") or [""])[0]
            if t and hmac.compare_digest(t.encode(), token.encode()):
                ck = "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" % (cookie_name, token)
                return self._send(302, b"", "text/plain", {"Location": path or "/", "Set-Cookie": ck,
                                                             "Cache-Control": "no-store"})
            if not self._authed(qs):
                if path == "/":
                    return self._send(401, "<!doctype html><meta charset=utf-8><title>Label review</title>"
                                      "<p style='font:16px system-ui;margin:3em'>Open the link with <code>?token=</code> "
                                      "that the review server printed when it started.</p>", "text/html; charset=utf-8")
                return self._err(401, "token required")
            try:
                if path in STATIC:
                    fn, ctype = STATIC[path]
                    with open(os.path.join(HERE, fn), "rb") as f:
                        return self._send(200, f.read(), ctype, {"Cache-Control": "no-cache"})
                if path == "/font.woff2":
                    if not os.path.exists(FONT):
                        return self._err(404, "no font")
                    with open(FONT, "rb") as f:
                        return self._send(200, f.read(), "font/woff2", {"Cache-Control": "max-age=604800"})
                if path == "/api/project":
                    return self._send(200, project.info())
                if path == "/api/stats":
                    return self._send(200, project.stats())
                if path == "/api/queue":
                    g = lambda k, d="": (qs.get(k) or [d])[0]
                    q = g("q", "uncertain")
                    if q not in QUEUES:
                        return self._err(400, "unknown queue")
                    try:
                        seed, offset, limit = int(g("seed", "0")), int(g("offset", "0")), int(g("limit", "500"))
                    except ValueError:
                        return self._err(400, "bad number")
                    return self._send(200, project.queue(q, g("event"), g("match"), g("split"), g("view"), g("by"),
                                                         seed, offset, limit))
                m = re.match(r"^/api/frame/([^/]+)$", path)
                if m:
                    i = m.group(1)
                    if not ID_RE.match(i):
                        return self._err(400, "bad id")
                    d = project.frame(i)
                    return self._send(200, d) if d else self._err(404, "no such frame")
                m = re.match(r"^/img/([^/]+)$", path)
                if m:
                    return self._img(m.group(1))
                return self._err(404, "not found")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _img(self, i):
            if not ID_RE.match(i):
                return self._err(400, "bad id")
            p = project.image_path(i)
            if not p:
                return self._err(404, "no such frame")
            try:
                st = os.stat(p)
            except OSError:
                return self._err(404, "image missing")
            etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
            hdr = {"Cache-Control": "private, max-age=86400", "ETag": etag}
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                for k, v in hdr.items():
                    self.send_header(k, v)
                self.end_headers()
                return
            with open(p, "rb") as f:
                data = f.read()
            self._send(200, data, IMG_TYPES[os.path.splitext(p)[1].lower()], hdr)

        # ---- POST
        def do_POST(self):
            path, qs = self._route()
            if not self._authed(qs):
                return self._err(401, "token required")
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                return self._err(415, "json only")
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != (self.headers.get("Host") or ""):
                return self._err(403, "cross-origin")
            m = re.match(r"^/api/review/([^/]+)$", path)
            if not m or not ID_RE.match(m.group(1)):
                return self._err(404, "not found")
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._err(400, "bad length")
            if n <= 0 or n > MAX_BODY:
                return self._err(413 if n > MAX_BODY else 400, "bad body size")
            try:
                body = json.loads(self.rfile.read(n))
            except ValueError:
                return self._err(400, "bad json")
            if not isinstance(body, dict):
                return self._err(400, "bad json")
            try:
                rec = project.save_review(m.group(1), body)
            except KeyError:
                return self._err(404, "no such frame")
            except ValueError as e:
                return self._err(400, str(e))
            self._send(200, rec)

    return H


def make_server(project_dir, host="127.0.0.1", port=7590, token=None):
    proj = Project(project_dir)
    token = token or secrets.token_urlsafe(18)
    srv = ThreadingHTTPServer((host, port), make_handler(proj, token, "csreview_%d" % port))
    srv.daemon_threads = True
    return srv, proj, token


def serve(project_dir, host="127.0.0.1", port=7590, token=None):
    srv, proj, token = make_server(project_dir, host, port, token)
    shown = host
    if host in ("0.0.0.0", "::", ""):
        shown = socket.gethostname()
    st = proj.stats()
    print("label review: %s (%d frames, %d reviewed, %d with auto labels)" % (proj.root, st["total"], st["reviewed"],
                                                                               st["auto"]), flush=True)
    print("  open  http://%s:%d/?token=%s" % (shown, srv.server_address[1], token), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7590)
    ap.add_argument("--token", default=os.environ.get("REVIEW_TOKEN"))
    a = ap.parse_args(argv)
    if not os.path.isdir(a.project):
        sys.exit("review: %s is not a directory" % a.project)
    serve(a.project, a.host, a.port, a.token)


if __name__ == "__main__":
    main()
