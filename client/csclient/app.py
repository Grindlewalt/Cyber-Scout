"""Wiring + command line: python cyberscout_client.py [--port 7600] [--data DIR] [--server URL] [--no-browser]"""
import argparse
import errno
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
import urllib.request
import webbrowser

from . import VERSION
from .install import Install
from .mirror import WebMirror, bundled_web_dir
from .net import NetError, Server
from .processor import Processor
from .store import Config, LocalDB, SessionStore, normalize_server
from .sync import SyncEngine
from .util import default_data_dir, ensure_dir, setup_logging
from .web import Handler, LocalHTTPServer

log = logging.getLogger("cyberscout")


class DataDirInUse(Exception):
    """Another Cyber-Scout Client runs on this data folder (two sync engines / outbox flushers / processors on one
    folder would fight over it, CLIENT-23). .info = what it wrote in the lock file ({pid, url, port}) or {}."""

    def __init__(self, data_dir, info):
        self.data_dir, self.info = data_dir, info or {}
        super().__init__(f"this data folder ({data_dir}) is in use by the Cyber-Scout Client at "
                         f"{self.info.get('url') or 'another window'}")


class DirLock:
    """An exclusive lock on <data>/client.lock for as long as this client runs (released by the OS if it dies).
    Windows locks are mandatory, so the byte locked there sits far past the "who holds it" note at the start of the
    file: a second start can still read the note."""
    NAME = "client.lock"
    NT_BYTE = 1 << 30

    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, self.NAME)
        self.f = None

    def acquire(self):
        f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(self.NT_BYTE)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            try:
                info = json.loads(f.read() or "{}")
            except (OSError, ValueError):
                info = {}
            f.close()
            raise DataDirInUse(os.path.dirname(self.path), info if isinstance(info, dict) else {})
        self.f = f
        return self

    def describe(self, **info):
        """Write who holds the lock (read by a second start: "in use by the client at <url>")."""
        if not self.f:
            return
        try:
            self.f.seek(0)
            self.f.truncate()
            self.f.write(json.dumps(dict(info, pid=os.getpid())))
            self.f.flush()
        except OSError:
            pass

    def release(self):
        f, self.f = self.f, None
        if not f:
            return
        try:
            f.seek(0)
            f.truncate()
            if os.name == "nt":
                import msvcrt
                f.seek(self.NT_BYTE)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()                                 # (closing drops the flock)


class App:
    def __init__(self, data_dir=None, host="127.0.0.1", port=7600, server_url=None, bundled_web=None,
                 alt_servers=None):
        self.data_dir = os.path.abspath(data_dir or default_data_dir())
        ensure_dir(self.data_dir, 0o700)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        self.lock = DirLock(self.data_dir).acquire()      # DataDirInUse: another client runs on this folder
        try:
            self._init(host, port, server_url, bundled_web, alt_servers)
        except BaseException:
            self.lock.release()
            raise
        self.lock.describe(url=self.url, port=self.port, version=VERSION)

    def _init(self, host, port, server_url, bundled_web, alt_servers):
        self.config = Config(self.data_dir)
        if server_url:
            self.config.update(server=normalize_server(server_url))
        if alt_servers is not None:
            self.config.update(alt_servers=[normalize_server(s) for s in alt_servers if s])
        self.session = SessionStore(self.data_dir)
        if self.session.server != self.config["server"]:
            self.session.set_server(self.config["server"])
        self.db = LocalDB(self.data_dir)
        self.db.prune_sent()
        self.server = Server(self.config, self.session)
        self.mirror = WebMirror(os.path.join(self.data_dir, "web"), bundled_web or bundled_web_dir())
        self.engine = SyncEngine(self)
        self.processor = Processor(self)
        self.httpd = LocalHTTPServer((host, port), Handler)
        self.httpd.app = self
        self.host = host
        self.port = self.httpd.server_address[1]
        loop = host in ("127.0.0.1", "localhost", "::1")
        names = [f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"]
        if self.port == 80:
            names += ["127.0.0.1", "localhost", "[::1]"]
        # DNS-rebinding defence: only our own loopback names (binding to a LAN address is an explicit opt-in)
        self.allowed_hosts = set(names) if loop else None
        self.allowed_origins = {f"http://{n}" for n in names}
        if not loop:
            self.allowed_origins |= {f"http://{host}:{self.port}"}
        self.url = f"http://127.0.0.1:{self.port}/" if loop else f"http://{host}:{self.port}/"
        self._threads = []
        self._down = False
        self.install = Install(self.data_dir)      # Stop / Uninstall: what the install scripts put where
        self.quit_ev = threading.Event()            # set by request_quit: main() shuts down
        self.after_quit = None

    # ------------------------------------------------------------------------------------------------ actions
    def start(self):
        t = threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True)
        t.start()
        self._threads.append(t)
        self.engine.start()
        self.processor.start()
        log.info("Cyber-Scout Client %s at %s (data: %s, server: %s)", VERSION, self.url, self.data_dir,
                 self.config["server"] or "not set")

    def shutdown(self):
        if self._down:
            return
        self._down = True
        self.processor.stop()
        self.engine.stop()
        if self._threads:                         # (shutdown() waits for serve_forever: only once it was started)
            self.httpd.shutdown()
        self.httpd.server_close()
        if self.engine.thread:
            self.engine.thread.join(timeout=10)
        self.lock.release()

    def request_quit(self, then=None, delay=0.3):
        """Stop / Uninstall in the app: main() wakes up and shuts down like Ctrl+C, then runs `then` (unload the
        LaunchAgent job, delete files). A little later than the answer that asked for it, so that reaches the page."""
        self.after_quit = then
        t = threading.Timer(delay, self.quit_ev.set)
        t.daemon = True
        t.start()

    def login(self, username, password):
        return self._signed_in(self.server.login(username, password), "")

    def login_key(self, key):
        """Sign in with a connection key (the server's Connect page; the installers' --key opens #/connect?k=...)."""
        return self._signed_in(self.server.login_key(key), " (connection key)")

    def _signed_in(self, sess, how):
        self.db.cache_clear()
        log.info("signed in to %s as %s%s", self.server.base, (sess.get("user") or {}).get("username"), how)
        self.engine.clear_errors("auth")
        self.engine.kick()
        self.processor.kick()
        return sess

    def logout(self):
        try:
            if self.session.cookie_header() and self.engine.online:
                self.server.logout()
        except NetError as e:
            log.warning("sign-out could not reach the server (%s); forgetting the session locally", e)
        finally:
            self.session.clear()
            self.db.cache_clear()
        log.info("signed out")

    def set_config(self, **ch):
        old = self.config["server"]
        self.config.update(**ch)
        if "server" in ch and ch["server"] != old:
            log.info("server changed: %s -> %s", old, ch["server"])
            self.session.set_server(ch["server"])
            self.db.cache_clear()
            self.server.configure(ch["server"])
            self.engine.set_online(False, "server changed")
            # the page just passed Test connection: say "Connecting…" until the engine's first probe (at once, below)
            # answers, not "Offline" (CLIENT-10). Changes queued for the old server stay queued for it (CLIENT-24).
            self.engine.connecting = bool(ch["server"])
        elif "alt_servers" in ch and self.server.base not in self.server.addresses():
            self.server.configure(self.config["server"])   # the alternate in use was removed
        self.engine.kick()

    def storage(self):
        def du(p):
            n = 0
            for d, _, fs in os.walk(p):
                for f in fs:
                    try:
                        n += os.path.getsize(os.path.join(d, f))
                    except OSError:
                        pass
            return n
        site = os.path.join(self.data_dir, "site")
        return {"data_dir": self.data_dir, "api_bytes": du(os.path.join(site, "api")),
                "media_bytes": du(os.path.join(site, "media")), "web_bytes": du(os.path.join(self.data_dir, "web")),
                "free_bytes": shutil.disk_usage(self.data_dir).free}

    def status(self):
        e = self.engine
        u = self.session.user if self.config["server"] else None
        info = e.server_info or {}
        return {
            "client": True, "version": VERSION, "server": self.config["server"],
            "active_server": self.server.base, "alt_servers": self.config["alt_servers"] or [],
            "online": bool(e.online), "signed_in": bool(u),
            "user": {"display_name": u.get("display_name"), "role": u.get("role")} if u else None,
            "last_sync": e.last_sync, "syncing": bool(e.syncing), "progress": dict(e.progress) if e.progress else None,
            "outbox": self.db.pending(self.server.addresses()),
            # changes written for another server (the client was pointed elsewhere since): kept, never sent here
            "outbox_held": [{"server": k, "n": n} for k, n in self.db.held(self.server.addresses()).items()],
            "connecting": bool(e.connecting),
            "events": e.status_events(),
            "errors": e.status_errors(),
            # additions (not in the v1 table; safe to ignore)
            "media": self.config["media"], "followed": self.config["events"],
            "available_events": e.events_list,
            "offline_reason": e.offline_reason,
            "server_info": {k: info.get(k) for k in ("name", "version", "api", "latency_ms")} if info else None,
            "web_version": self.mirror.version,
            "storage": self.storage(),
            "processing": self.processor.status(),       # processor mode ("Help process matches")
            "install": self.install.info(),              # {frozen, program, autostart, can_uninstall, data_dir}
        }


def _already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3) as r:
            return bool(r.headers.get("X-Cyber-Scout-Client"))
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cyberscout-client", description="Cyber-Scout Client: the scouting app with an "
                                 "offline copy of your server's data, at http://127.0.0.1:<port>/ (default 7600)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CYBERSCOUT_PORT", "7600")))
    ap.add_argument("--host", default=os.environ.get("CYBERSCOUT_HOST", "127.0.0.1"),
                    help="address to listen on (default 127.0.0.1: this computer only)")
    ap.add_argument("--data", default=os.environ.get("CYBERSCOUT_DATA"), help=f"data folder (default {default_data_dir()})")
    ap.add_argument("--server", help="server address, e.g. https://scout.example.dev (also settable in the app)")
    ap.add_argument("--alt-server", action="append", metavar="URL",
                    help="another address of the same server, used when the main one can't be reached (repeat for "
                         "more; e.g. the Tailscale address next to the tunnel). Saved; --alt-server '' clears them")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--process", choices=("on", "off"), help="Help process matches: track match video for the "
                    "server on this computer (saved; needs an admin account and a processing Python)")
    ap.add_argument("--process-python", metavar="PATH", help="the processing Python (one with onnxruntime, av, "
                    "opencv, numpy, scipy, pupil-apriltags; saved; '' = the one 'Set up processing' made)")
    ap.add_argument("--log-level", default=os.environ.get("CYBERSCOUT_LOG", "INFO"))
    ap.add_argument("--version", action="version", version=f"Cyber-Scout Client {VERSION}")
    a = ap.parse_args(argv)
    data = os.path.abspath(a.data or default_data_dir())
    ensure_dir(data, 0o700)
    setup_logging(os.path.join(data, "logs"), a.log_level)
    try:
        app = App(data, host=a.host, port=a.port, server_url=a.server, alt_servers=a.alt_server)
    except DataDirInUse as e:
        # the same install is already running (maybe on another port): use that one
        url = e.info.get("url")
        if url and not a.no_browser:
            log.info("Cyber-Scout Client is already running on this data folder at %s; opening it", url)
            webbrowser.open(url)
        else:
            log.info("Cyber-Scout Client is already running on this data folder (%s)%s", data,
                     f" at {url}" if url else "")
        return 0
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)) or "in use" in str(e).lower():
            # (this data folder is free, so whatever holds the port is another install or another program)
            if _already_running(a.port):
                log.error("port %d is in use by another Cyber-Scout Client with a different data folder (open "
                          "http://127.0.0.1:%d/ for that one): start this one with --port <other>", a.port, a.port)
            else:
                log.error("port %d is in use by another program: start with --port <other>", a.port)
            return 2
        raise
    except ValueError as e:
        log.error("%s", e)
        return 2
    if a.process_python is not None:
        app.config.update(process_python=os.path.abspath(a.process_python) if a.process_python else None)
    if a.process is not None:
        app.config.update(process=a.process == "on")
    app.start()
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(app.url)).start()
    def on_term(*_):                            # `kill` / a service manager: shut down like Ctrl+C (stops the
        raise KeyboardInterrupt()               # processing children, gives claims back)
    _on_signal(on_term)
    print(f"\n  Cyber-Scout Client is running: {app.url}\n  Data: {data}\n"
          f"  Help process matches: {app.url}client/processing\n  Press Ctrl+C to stop.\n", flush=True)
    try:
        while not app.quit_ev.is_set():          # (a short sleep: Ctrl+C interrupts it on Windows too)
            time.sleep(0.5)
        log.info("stopping (Stop in the app)")
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        app.shutdown()
    if app.after_quit:
        # everything is saved by now: unloading our own LaunchAgent job SIGTERMs this process, which then just ends
        _on_signal(lambda *_: os._exit(0))
        app.after_quit()
    return 0


def _on_signal(fn):
    try:
        signal.signal(signal.SIGTERM, fn)
    except (ValueError, OSError, AttributeError):
        pass


if __name__ == "__main__":
    sys.exit(main())
