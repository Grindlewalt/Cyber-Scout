"""Wiring + command line: python cyberscout_client.py [--port 7600] [--data DIR] [--server URL] [--no-browser]"""
import argparse
import errno
import logging
import os
import shutil
import sys
import threading
import time
import urllib.request
import webbrowser

from . import VERSION
from .mirror import WebMirror, bundled_web_dir
from .net import NetError, Server
from .store import Config, LocalDB, SessionStore, normalize_server
from .sync import SyncEngine
from .util import default_data_dir, ensure_dir, setup_logging
from .web import Handler, LocalHTTPServer

log = logging.getLogger("cyberscout")


class App:
    def __init__(self, data_dir=None, host="127.0.0.1", port=7600, server_url=None, bundled_web=None,
                 alt_servers=None):
        self.data_dir = os.path.abspath(data_dir or default_data_dir())
        ensure_dir(self.data_dir, 0o700)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
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

    # ------------------------------------------------------------------------------------------------ actions
    def start(self):
        t = threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True)
        t.start()
        self._threads.append(t)
        self.engine.start()
        log.info("Cyber-Scout Client %s at %s (data: %s, server: %s)", VERSION, self.url, self.data_dir,
                 self.config["server"] or "not set")

    def shutdown(self):
        self.engine.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.engine.thread:
            self.engine.thread.join(timeout=10)

    def login(self, username, password):
        sess = self.server.login(username, password)
        self.db.cache_clear()
        log.info("signed in to %s as %s", self.server.base, (sess.get("user") or {}).get("username"))
        self.engine.clear_errors("auth")
        self.engine.kick()
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
            "outbox": self.db.pending(),
            "events": e.status_events(),
            "errors": e.status_errors(),
            # additions (not in the v1 table; safe to ignore)
            "media": self.config["media"], "followed": self.config["events"],
            "available_events": e.events_list,
            "offline_reason": e.offline_reason,
            "server_info": {k: info.get(k) for k in ("name", "version", "api", "latency_ms")} if info else None,
            "web_version": self.mirror.version,
            "storage": self.storage(),
        }


def _already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3) as r:
            return bool(r.headers.get("X-Cyber-Scout-Client"))
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cyberscout-client", description="Cyber-Scout Client: the scouting app with an "
                                 "offline copy of your server's data, at http://127.0.0.1:7600/")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CYBERSCOUT_PORT", "7600")))
    ap.add_argument("--host", default=os.environ.get("CYBERSCOUT_HOST", "127.0.0.1"),
                    help="address to listen on (default 127.0.0.1: this computer only)")
    ap.add_argument("--data", default=os.environ.get("CYBERSCOUT_DATA"), help=f"data folder (default {default_data_dir()})")
    ap.add_argument("--server", help="server address, e.g. https://scout.example.dev (also settable in the app)")
    ap.add_argument("--alt-server", action="append", metavar="URL",
                    help="another address of the same server, used when the main one can't be reached (repeat for "
                         "more; e.g. the Tailscale address next to the tunnel). Saved; --alt-server '' clears them")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--log-level", default=os.environ.get("CYBERSCOUT_LOG", "INFO"))
    ap.add_argument("--version", action="version", version=f"Cyber-Scout Client {VERSION}")
    a = ap.parse_args(argv)
    data = os.path.abspath(a.data or default_data_dir())
    ensure_dir(data, 0o700)
    setup_logging(os.path.join(data, "logs"), a.log_level)
    try:
        app = App(data, host=a.host, port=a.port, server_url=a.server, alt_servers=a.alt_server)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)) or "in use" in str(e).lower():
            if _already_running(a.port):
                log.info("Cyber-Scout Client is already running; opening it")
                if not a.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{a.port}/")
                return 0
            log.error("port %d is in use by another program: start with --port <other>", a.port)
            return 2
        raise
    except ValueError as e:
        log.error("%s", e)
        return 2
    app.start()
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(app.url)).start()
    print(f"\n  Cyber-Scout Client is running: {app.url}\n  Data: {data}\n  Press Ctrl+C to stop.\n", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
