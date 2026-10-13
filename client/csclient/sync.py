"""The sync engine (docs/SYNC.md section 2): connectivity probe, events -> manifest?since -> bundles (<= 32 MB, sha256
verified, atomic, resumable) -> media -> outbox flush. At most 2 concurrent background requests, back-off with jitter."""
import collections
import concurrent.futures
import hashlib
import json
import logging
import os
import random
import threading
import time
from urllib.parse import quote, urlencode

from .mirror import extract_bundle, remove_file, sha256_file, write_stream_verified
from .net import HTTPError, NetError, detail_text
from .store import EventState, list_event_states
from .util import ensure_dir, read_json, safe_relpath, target_path, write_json

log = logging.getLogger("cyberscout.sync")

CHUNK_BYTES = 32 * 1024 * 1024        # client-side bundle size (server max is 64 MB)
CHUNK_FILES = 4000
BIG_FILE = 48 * 1024 * 1024           # a data file this big is fetched on its own with GET (bundle max is 64 MB)
MAX_CONCURRENT = 2
_SHA = set("0123456789abcdef")


class AuthLost(Exception):
    pass


class Stopped(Exception):
    pass


def jitter(base, n, cap):
    return min(cap, base * (2 ** n)) * random.uniform(0.5, 1.5)


class SyncEngine:
    def __init__(self, app):
        self.app = app
        self.cfg, self.server, self.db, self.session = app.config, app.server, app.db, app.session
        self.site_root = ensure_dir(os.path.join(app.data_dir, "site"))
        self.state_dir = ensure_dir(os.path.join(app.data_dir, "state"))
        self.parts_dir = ensure_dir(os.path.join(app.data_dir, "parts"))
        self.tmp_dir = ensure_dir(os.path.join(app.data_dir, "tmp"))
        self.online = False
        self.offline_reason = None
        self.server_info = None
        self.syncing = False
        self.progress = None
        self.errors = collections.deque(maxlen=30)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop_ev = threading.Event()
        self.requests = []
        self.bg = threading.BoundedSemaphore(MAX_CONCURRENT)
        self.flush_lock = threading.Lock()
        self.flush_pending = False
        self.waiters = {}
        self.failures = 0
        self.next_sync = 0.0
        self.retry_at = None
        self.next_probe = 0.0
        self.events_list = read_json(os.path.join(self.state_dir, "_events.json"), []) or []
        sts = list_event_states(self.state_dir)
        self.last_sync = max([s.synced_at for s in sts if s.synced_at] or [0]) or None
        self.thread = None
        self.cycles = self.cycles_started = 0         # completed / started sync cycles (tests, diagnostics)
        for f in os.listdir(self.tmp_dir):           # leftovers of an interrupted write
            try:
                os.remove(os.path.join(self.tmp_dir, f))
            except OSError:
                pass
        for f in os.listdir(self.parts_dir):         # partial downloads older than 2 days won't be resumed
            p = os.path.join(self.parts_dir, f)
            try:
                if time.time() - os.path.getmtime(p) > 2 * 86400:
                    os.remove(p)
            except OSError:
                pass

    # ------------------------------------------------------------------------------------------------ lifecycle
    def start(self):
        self.thread = threading.Thread(target=self.run, name="sync", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        self.wake.set()

    def kick(self, ev=None, media=None, probe=True):
        with self.lock:
            self.requests.append({"ev": ev, "media": media})
            if probe:
                self.next_probe = 0
        self.wake.set()

    def note_error(self, kind, message, **extra):
        e = {"time": time.time(), "kind": kind, "message": str(message)}
        e.update(extra)
        repeat = False
        with self.lock:
            # one entry per (kind, ev): the newest message replaces the old one
            for old in list(self.errors):
                if old["kind"] == kind and old.get("ev") == extra.get("ev"):
                    repeat = repeat or old["message"].split(" (retrying")[0] == e["message"].split(" (retrying")[0]
                    self.errors.remove(old)
            self.errors.append(e)
        log.log(logging.DEBUG if repeat else logging.WARNING, "%s%s: %s", kind,
                f" [{extra['ev']}]" if extra.get("ev") else "", message)

    def clear_errors(self, *kinds):
        with self.lock:
            for e in list(self.errors):
                if e["kind"] in kinds:
                    self.errors.remove(e)

    def set_online(self, v, reason=None):
        with self.lock:
            was = self.online
            self.online = bool(v)
            self.offline_reason = None if v else reason
        if v and not was:
            log.info("server reachable: %s", self.server.base)
            self.clear_errors("network")
            self.kick(probe=False)                    # connectivity returned: sync (and flush) right away
        elif was and not v:
            log.warning("offline: %s", reason)
            self.next_probe = time.time() + 3
            self.wake.set()

    # ------------------------------------------------------------------------------------------------ main loop
    def run(self):
        log.info("sync engine started")
        while not self.stop_ev.is_set():
            nxt = min(self.next_probe, self.next_sync if self.online else self.next_probe)
            self.wake.wait(max(0.05, nxt - time.time()))
            self.wake.clear()
            if self.stop_ev.is_set():
                break
            try:
                self.tick()
            except Stopped:
                break
            except Exception:
                log.exception("sync loop error")
                time.sleep(1)
        log.info("sync engine stopped")

    def tick(self):
        nowt = time.time()
        if not self.server.base:
            self.set_online(False, "no server configured")
            self.next_probe = nowt + 5
            with self.lock:
                self.requests = []
            return
        with self.lock:
            asked = bool(self.requests)
        if nowt >= self.next_probe or asked:
            self.probe()
        with self.lock:                       # (a probe that finds the server back adds its own request)
            reqs, self.requests = self.requests, []
        if not self.online:
            if reqs and not asked:
                # "Sync now" arrived while this tick was already past its probe: probe again for it right away
                # instead of dropping it until the next (offline back-off) probe
                with self.lock:
                    self.requests = reqs + self.requests
                self.next_probe = time.time()
                return
            if reqs:
                self.note_error("network", f"can't reach the server ({self.offline_reason}); will sync when back")
            self.next_probe = time.time() + jitter(self.cfg["probe_interval"] or 15, min(self.failures, 2), 60)
            return
        self.next_probe = time.time() + (self.cfg["probe_interval"] or 15)
        if reqs or time.time() >= self.next_sync:
            self.cycles_started += 1
            try:
                self.cycle(reqs)
                self.failures = 0
                with self.lock:
                    self.next_sync = min(time.time() + max(10, self.cfg["interval"] or 60),
                                         self.retry_at or float("inf"))
                    self.retry_at = None
            except Stopped:
                raise
            except AuthLost as e:
                self.note_error("auth", str(e))
                self.next_sync = time.time() + max(10, self.cfg["interval"] or 60)
            except Exception as e:
                self.failures += 1
                delay = jitter(5, self.failures, 600)
                self.next_sync = time.time() + delay
                if isinstance(e, NetError):
                    self.set_online(False, str(e))
                    self.note_error("network", f"sync interrupted ({e}); retrying in {delay:.0f} s")
                else:
                    if not isinstance(e, (HTTPError, RuntimeError)):
                        log.exception("sync failed")
                    self.note_error("sync", f"{e} (retrying in {delay:.0f} s)")
            finally:
                self.cycles += 1

    def probe(self):
        t0 = time.time()
        try:
            info = self.server.info(timeout=5)
        except (NetError, HTTPError) as e:
            self.set_online(False, str(e))
            return False
        self.server_info = dict(info, latency_ms=round((time.time() - t0) * 1000))
        came_back = not self.online
        self.set_online(True)
        if came_back and self.session.cookie_header():
            try:
                self.server.refresh_session()
            except (NetError, HTTPError) as e:
                log.warning("session check failed: %s", e)
        v = info.get("version")
        if v and v != self.app.mirror.version:
            try:
                self.app.mirror.refresh(self.server, v, sem=self.bg)
                self.clear_errors("web")
            except Exception as e:
                self.note_error("web", f"could not update the app files: {e}")
        return True

    # ------------------------------------------------------------------------------------------------ one cycle
    def cycle(self, reqs):
        want_evs, media_req, everything = set(), None, not reqs
        for r in reqs:
            if r.get("ev"):
                want_evs.add(r["ev"])
            else:
                everything = True
            if r.get("media") is not None:
                media_req = bool(r["media"]) if media_req is None else (media_req or bool(r["media"]))
        if not self.session.user:
            if self.session.cookie_header():
                self.server.refresh_session()
            if not self.session.user:
                raise AuthLost("sign in to sync with " + self.server.base)
        self.syncing = True
        t0 = time.time()
        try:
            self.flush_outbox()
            r = self.server.call("GET", "/app/v1/sync/events", timeout=30)
            self._check(r, "the event list")
            evs = r.json() or []
            self.events_list = [e for e in evs if isinstance(e, dict) and isinstance(e.get("key"), str)]
            write_json(os.path.join(self.state_dir, "_events.json"), self.events_list)
            names = {e["key"]: e.get("name") for e in self.events_list}
            chosen = self.cfg["events"]
            keys = [e["key"] for e in self.events_list] if chosen is None else [k for k in chosen if isinstance(k, str)]
            if not everything:
                keys = [k for k in keys if k in want_evs] + [k for k in want_evs if k not in keys and k in names]
            problems = []
            for ev in keys:
                self._stopcheck()
                try:
                    self.sync_event(ev, names.get(ev))
                    self.clear_errors_for(ev)
                except (NetError, AuthLost, Stopped):
                    raise
                except (HTTPError, RuntimeError, ValueError) as e:
                    problems.append(ev)
                    self.note_error("sync", str(e), ev=ev)
            mode = self.cfg["media"] or "none"
            if media_req is not False and (mode != "none" or media_req):
                for ev in keys:
                    self._stopcheck()
                    try:
                        self.sync_media(ev, mode if mode != "none" else "proxy")
                    except (NetError, AuthLost, Stopped):
                        raise
                    except (HTTPError, RuntimeError, ValueError) as e:
                        problems.append(ev)
                        self.note_error("media", str(e), ev=ev)
            self.flush_outbox()
            if not problems:
                self.last_sync = time.time()
                self.clear_errors("sync", "network", "auth")
            log.info("sync done in %.1f s (%d events%s)", time.time() - t0, len(keys),
                     f", problems: {', '.join(problems)}" if problems else "")
        finally:
            self.syncing = False
            self.progress = None

    def clear_errors_for(self, ev):
        with self.lock:
            for e in list(self.errors):
                if e.get("ev") == ev and e["kind"] == "sync":
                    self.errors.remove(e)

    def _stopcheck(self):
        if self.stop_ev.is_set():
            raise Stopped()

    def _check(self, r, what):
        if r.status == 401:
            try:
                self.server.refresh_session()
            except (NetError, HTTPError):
                pass
            raise AuthLost("signed out by the server: sign in again to sync")
        if r.status in (502, 503, 504):
            raise NetError(f"server unavailable (HTTP {r.status}) fetching {what}")
        if r.status != 200:
            raise HTTPError(r.status, r.body, f"{what}: HTTP {r.status} {detail_text(r.body)}".strip())

    # ------------------------------------------------------------------------------------------------ data files
    def _progress(self, **kv):
        with self.lock:
            if self.progress is None:
                self.progress = {"done": 0, "total": 0, "bytes": 0, "done_bytes": 0, "total_bytes": 0}
            for k, v in kv.items():
                if k.startswith("+"):
                    self.progress[k[1:]] = self.progress.get(k[1:], 0) + v
                else:
                    self.progress[k] = v

    def _have(self, st_files, rel, sha, size, other_index):
        try:
            p = target_path(self.site_root, rel)
        except ValueError:
            return False
        try:
            on_disk = os.path.getsize(p)
        except OSError:
            return False
        if on_disk != size:
            return False
        if st_files.get(rel) == [sha, size] or other_index.get(rel) == [sha, size]:
            return True
        if size <= 64 * 1024 * 1024 and sha256_file(p) == sha:     # e.g. the index was lost: don't re-download
            return True
        return False

    def sync_event(self, ev, name=None):
        st = EventState(self.state_dir, ev)
        if st.server != self.server.base:
            st.cursor, st.server = None, self.server.base
        if name:
            st.name = name
        q = {"ev": ev}
        if st.cursor:
            q["since"] = st.cursor
        t0 = time.time()
        with self.bg:
            r = self.server.call("GET", "/app/v1/sync/manifest?" + urlencode(q), timeout=60)
        self._check(r, f"the {ev} manifest")
        m = r.json()
        if not isinstance(m, dict) or not isinstance(m.get("files"), list):
            raise HTTPError(r.status, r.body, f"{ev}: malformed manifest")
        full = bool(m.get("full")) or not st.cursor
        files, bad = {}, 0
        for f in m["files"]:
            rel = safe_relpath(f.get("path")) if isinstance(f, dict) else None
            sha = str(f.get("sha256") or "").lower() if rel else ""
            size = f.get("size") if rel else None
            if not rel or len(sha) != 64 or not set(sha) <= _SHA or not isinstance(size, int) or size < 0:
                bad += 1
                continue
            files[rel] = (sha, size)
        if bad:
            log.warning("%s: ignored %d unsafe/malformed manifest entries", ev, bad)
        others = {}
        for o in list_event_states(self.state_dir):
            if o.ev != ev:
                others.update(o.files)
        need = sorted(p for p, (sha, size) in files.items() if not self._have(st.files, p, sha, size, others))
        for p, (sha, size) in files.items():          # already present (e.g. shared global files): index them
            if p not in need:
                st.files[p] = [sha, size]
        if full:
            dels = [p for p in st.files if p not in files]
        else:
            dels = [p for p in (safe_relpath(x) for x in (m.get("deleted") or []) if isinstance(x, str)) if p]
        total_bytes = sum(files[p][1] for p in need)
        self._progress(ev=ev, phase="data", **{"+total": len(need), "+total_bytes": total_bytes})
        log.info("%s: manifest %s, %d files listed, %d to download (%.1f MB), %d to delete",
                 ev, "full" if full else f"since {st.cursor}", len(files), len(need), total_bytes / 1e6, len(dels))
        failed = self._download_files(ev, need, files, st)
        for p in dels:
            st.files.pop(p, None)
            if p not in others and p not in files:
                remove_file(self.site_root, p)
        if failed:
            st.save()
            raise RuntimeError(f"{ev}: {len(failed)} file(s) could not be downloaded/verified "
                               f"(e.g. {sorted(failed)[0]}); will retry")
        st.cursor = m.get("cursor")
        st.synced_at = time.time()
        st.save()
        log.info("%s: synced in %.1f s", ev, time.time() - t0)

    def _chunks(self, need, files):
        out, cur, cur_b = [], [], 0
        for p in need:
            s = files[p][1]
            if cur and (cur_b + s > CHUNK_BYTES or len(cur) >= CHUNK_FILES):
                out.append(cur)
                cur, cur_b = [], 0
            cur.append(p)
            cur_b += s
        if cur:
            out.append(cur)
        return out

    def _download_files(self, ev, need, files, st):
        if not need:
            return set()
        failed = set()
        big = [p for p in need if files[p][1] > BIG_FILE]
        chunks = self._chunks([p for p in need if files[p][1] <= BIG_FILE], files)
        lock = threading.Lock()

        def one_big(rel):            # too big for a bundle: plain GET, Range-resumable
            sha, size = files[rel]
            part, meta = self._part_paths("file", rel)
            self._resumable("GET", "/" + quote(rel), None, part, meta, rel, expect_size=size)
            with open(part, "rb") as src:
                ok, why = write_stream_verified(src, target_path(self.site_root, rel), size, sha, self.tmp_dir)
            self._rm(part)
            self._rm(meta)
            if not ok:
                log.warning("%s: %s", rel, why)
                return {rel}
            with lock:
                st.files[rel] = [sha, size]
                st.save()
            self._progress(**{"+done": 1, "+done_bytes": size})
            return set()

        def one(paths):
            exp = {p: files[p] for p in paths}
            for attempt in range(2):
                self._stopcheck()
                part = self.fetch_bundle(ev, paths)
                written, problems, corrupt = extract_bundle(part, self.site_root, exp, self.tmp_dir)
                for pr in problems:
                    log.warning("%s bundle: %s", ev, pr)
                with lock:
                    for p, v in written.items():
                        st.files[p] = list(v)
                    st.save()
                self._progress(**{"+done": len(written), "+done_bytes": sum(v[1] for v in written.values())})
                for p in written:
                    exp.pop(p)
                self._discard_part(ev, paths)
                if not exp or not corrupt:
                    break                 # a clean bundle with a mismatch: the server changed meanwhile -> next sync
                log.warning("%s: bundle was corrupt, downloading it again", ev)
            return set(exp)

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as ex:
            futs = [ex.submit(one, c) for c in chunks] + [ex.submit(one_big, b) for b in big]
            err = None
            for f in futs:
                try:
                    failed |= f.result()
                except Exception as e:    # NetError/AuthLost: let the others finish (or fail) then re-raise
                    err = err or e
            if err:
                raise err
        return failed

    def _part_paths(self, kind, key):
        bid = hashlib.sha256(json.dumps([self.server.base, kind, key], sort_keys=True).encode()).hexdigest()[:24]
        return os.path.join(self.parts_dir, bid + ".part"), os.path.join(self.parts_dir, bid + ".json")

    def _discard_part(self, ev, paths):
        for p in self._part_paths("bundle", [ev, paths]):
            try:
                os.remove(p)
            except OSError:
                pass

    def fetch_bundle(self, ev, paths):
        body = json.dumps({"ev": ev, "paths": paths}).encode()
        part, meta = self._part_paths("bundle", [ev, paths])
        return self._resumable("POST", "/app/v1/sync/bundle", body, part, meta, f"{ev} bundle ({len(paths)} files)")

    def _resumable(self, method, path, body, part, meta_path, what, expect_size=None):
        """Download to `part`, resuming with Range after a broken connection (and across restarts)."""
        attempts = 0
        while True:
            self._stopcheck()
            meta = read_json(meta_path, {}) or {}
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have and (meta.get("path") != path):
                have = 0
            if expect_size is not None and have >= expect_size:
                return part
            h = {"Accept": "*/*"}
            if have:
                h["Range"] = f"bytes={have}-"
                if meta.get("etag"):
                    h["If-Range"] = meta["etag"]
            try:
                with self.bg:
                    r = self.server.call(method, path, body, headers=h, stream=True, timeout=60,
                                         ctype="application/json" if body else None)
                    try:
                        st = r.status
                        if st == 416:
                            cr = r.header("Content-Range") or ""
                            r.read(65536)
                            r.close()
                            tot = cr.rsplit("/", 1)[-1]
                            if have and tot.isdigit() and int(tot) == have:
                                return part
                            self._rm(part)
                            raise NetError("range not satisfiable; restarting the download")
                        if st not in (200, 206):
                            data = r.read(1 << 20)
                            r.close()
                            if st == 401:
                                raise AuthLost("signed out by the server: sign in again to sync")
                            if st in (502, 503, 504, 429):
                                raise NetError(f"server busy/unavailable (HTTP {st}) for {what}")
                            raise HTTPError(st, data, f"{what}: HTTP {st} {detail_text(data)}".strip())
                        mode = "wb"
                        if st == 206:
                            cr = r.header("Content-Range") or ""
                            try:
                                start = int(cr.split()[1].split("-")[0])
                            except (IndexError, ValueError):
                                start = -1
                            if start != have:
                                r.close(broken=True)
                                self._rm(part)
                                raise NetError(f"server resumed at the wrong offset ({cr!r}); restarting")
                            mode = "ab"
                        elif have:
                            log.info("%s: server sent the whole file again (no resume)", what)
                        clen = r.header("Content-Length")
                        clen = int(clen) if clen and clen.isdigit() else None
                        write_json(meta_path, {"path": path, "etag": r.header("ETag"), "started": time.time()})
                        got = 0
                        with open(part, mode) as f:
                            while True:
                                if self.stop_ev.is_set():
                                    r.close(broken=True)
                                    raise Stopped()
                                b = r.read(256 * 1024)
                                if not b:
                                    break
                                f.write(b)
                                got += len(b)
                                self._progress(**{"+bytes": len(b)})
                        r.close()
                        if clen is not None and got < clen:
                            raise NetError(f"connection closed after {got} of {clen} bytes")
                        if have and mode == "ab":
                            log.info("%s: resumed at %d bytes, +%d", what, have, got)
                        return part
                    except BaseException:
                        r.close(broken=True)
                        raise
            except NetError as e:
                attempts += 1
                if attempts > 5:
                    raise
                delay = jitter(1, attempts - 1, 20)
                log.warning("%s: %s; retry %d in %.1f s (resuming)", what, e, attempts, delay)
                if self.stop_ev.wait(delay):
                    raise Stopped()

    @staticmethod
    def _rm(p):
        try:
            os.remove(p)
        except OSError:
            pass

    # ------------------------------------------------------------------------------------------------ media
    def sync_media(self, ev, mode):
        with self.bg:
            r = self.server.call("GET", "/app/v1/sync/media?" + urlencode({"ev": ev}), timeout=60)
        self._check(r, f"the {ev} media list")
        items = r.json()
        if isinstance(items, dict):
            items = items.get("files") or items.get("media") or []
        if not isinstance(items, list):
            raise HTTPError(r.status, r.body, f"{ev}: malformed media list")
        st = EventState(self.state_dir, ev)
        want = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if mode == "proxy" and it.get("kind") != "proxy":
                continue
            rel = safe_relpath(it.get("path"), roots=("media",))
            size = it.get("size")
            sha = str(it.get("sha256") or "").lower()
            if not rel or not isinstance(size, int) or size < 0 or (sha and (len(sha) != 64 or not set(sha) <= _SHA)):
                continue
            try:
                p = target_path(self.site_root, rel)
            except ValueError:
                continue
            if st.media.get(rel) == [sha, size] and os.path.exists(p) and os.path.getsize(p) == size:
                continue
            want.append((rel, size, sha))
        if not want:
            return
        self._progress(ev=ev, phase="media", **{"+total": len(want), "+total_bytes": sum(w[1] for w in want)})
        log.info("%s: %d media files to download (%.1f MB, mode %s)", ev, len(want), sum(w[1] for w in want) / 1e6,
                 mode)
        lock, failed = threading.Lock(), []

        def one(w):
            rel, size, sha = w
            part, meta = self._part_paths("media", rel)
            self._resumable("GET", "/" + quote(rel), None, part, meta, rel, expect_size=size)
            with open(part, "rb") as src:
                ok, why = write_stream_verified(src, target_path(self.site_root, rel), size, sha, self.tmp_dir)
            self._rm(part)
            self._rm(meta)
            if not ok:
                failed.append(rel)
                log.warning("%s: %s", rel, why)
                return
            with lock:
                st.media[rel] = [sha, size]
                st.save()
            self._progress(**{"+done": 1, "+done_bytes": size})

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as ex:
            err = None
            for f in [ex.submit(one, w) for w in want]:
                try:
                    f.result()
                except Exception as e:
                    err = err or e
            if err:
                raise err
        if failed:
            raise RuntimeError(f"{ev}: {len(failed)} media file(s) failed verification")

    # ------------------------------------------------------------------------------------------------ outbox
    def flush_outbox(self):
        """Send queued writes in order (one at a time, same Idempotency-Key on every retry). Stops on network/auth
        errors; 409s are kept as conflicts for the user; other 4xx are kept as failed."""
        self.flush_pending = True
        while True:
            if not self.flush_lock.acquire(blocking=False):
                return
            try:
                while self.flush_pending:
                    self.flush_pending = False
                    self._flush_all()
            finally:
                self.flush_lock.release()
            if not (self.flush_pending and self.db.count("queued")):
                return

    def _notify(self, oid):
        ev = self.waiters.get(oid)
        if ev:
            ev.set()

    def _flush_all(self):
        while True:
            row = self.db.next_queued()
            if row is None or not self.server.base:
                return
            if not self.session.cookie_header():
                self.note_error("auth", f"sign in to send {self.db.count('queued')} queued change(s)")
                return
            oid = row["id"]
            self.db.set_state(oid, "sending", bump=True)
            try:
                r = self._send(row)
                if r.status == 403 and "csrf" in detail_text(r.body).lower():
                    self.server.refresh_session()
                    r = self._send(row)
            except NetError as e:
                self.db.set_state(oid, "queued", error=str(e))
                self.set_online(False, str(e))
                self._notify(oid)
                return
            except Exception as e:
                self.db.set_state(oid, "queued", error=str(e))
                self._notify(oid)
                raise
            self.set_online(True)
            st, body = r.status, r.body or b""
            what = f"{row['method']} {row['path']}"
            if 200 <= st < 300:
                self.db.set_state(oid, "sent", status=st, response=body, error=None)
                log.info("outbox #%d sent: %s -> %d", oid, what, st)
            elif st == 409 and (r.header("Retry-After") or "still being processed" in detail_text(body)):
                # the server is still running the first copy of this request (same Idempotency-Key): retry later
                self.db.set_state(oid, "queued", status=st, error=detail_text(body) or "busy")
                self._notify(oid)
                self._retry_soon(r.header("Retry-After"))
                return
            elif st == 409:
                self.db.set_state(oid, "conflict", status=st, response=body, error=detail_text(body) or "conflict")
                log.warning("outbox #%d conflict: %s: %s", oid, what, detail_text(body))
            elif st == 401:
                self.db.set_state(oid, "queued", status=st, error="signed out")
                try:
                    self.server.refresh_session()
                except (NetError, HTTPError):
                    pass
                self.note_error("auth", f"signed out by the server: sign in again to send "
                                        f"{self.db.count('queued')} queued change(s)")
                self._notify(oid)
                return
            elif st == 429 or st >= 500:
                self.db.set_state(oid, "queued", status=st, error=f"HTTP {st} {detail_text(body)}".strip())
                self._notify(oid)
                if st >= 502:
                    self.set_online(False, f"server unavailable (HTTP {st})")
                else:
                    self._retry_soon(r.header("Retry-After"))
                return
            else:
                self.db.set_state(oid, "failed", status=st, response=body,
                                  error=detail_text(body) or f"HTTP {st}")
                log.warning("outbox #%d rejected: %s -> %d %s", oid, what, st, detail_text(body))
            self._notify(oid)

    def _retry_soon(self, retry_after=None):
        try:
            d = float(retry_after) if retry_after else 5.0
        except ValueError:
            d = 5.0
        t = time.time() + min(300.0, max(1.0, d)) * random.uniform(1.0, 1.5)
        with self.lock:
            self.retry_at = min(self.retry_at or t, t)
            self.next_sync = min(self.next_sync, t)
        self.wake.set()

    def _send(self, row):
        return self.server.call(row["method"], row["path"], body=bytes(row["body"]) if row["body"] else None,
                                ctype=row["ctype"], headers={"Idempotency-Key": row["key"]}, timeout=60)

    def flush_async(self):
        threading.Thread(target=self._flush_safe, name="flush", daemon=True).start()

    def _flush_safe(self):
        try:
            self.flush_outbox()
        except Exception:
            log.exception("outbox flush failed")

    # ------------------------------------------------------------------------------------------------ status
    def status_events(self):
        names = {e["key"]: e.get("name") for e in self.events_list}
        out = []
        for st in list_event_states(self.state_dir):
            s = st.summary()
            s["name"] = s.get("name") or names.get(st.ev)
            out.append(s)
        return out

    def status_errors(self):
        with self.lock:
            errs = list(self.errors)
        for it in self.db.items():
            if it["state"] in ("conflict", "failed"):
                errs.append({"time": it["updated"], "kind": it["state"], "id": it["id"],
                             "message": f"{it['method']} {it['path'].replace('/app/v1/', '')}: {it['error']}",
                             "response": it["response"]})
        return errs
