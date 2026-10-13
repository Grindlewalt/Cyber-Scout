"""End-to-end: the real API (repo .venv, throwaway DATA with a copy of staging site data) behind an nginx-like front,
and the Cyber-Scout Client syncing a real event over a LAN-speed and a venue-like slow link.

    .venv/bin/python client/tests/e2e/run_e2e.py [--keep] [--only lan|slow]

Work dir: .work/client-e2e/ (data copied once from .work/site_staging). Prints a JSON summary at the end."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(CLIENT)
sys.path.insert(0, CLIENT)
sys.path.insert(0, HERE)

from csclient.app import App            # noqa: E402
from csclient.util import setup_logging  # noqa: E402
from front import Front                  # noqa: E402

WORK = os.path.join(REPO, ".work", "client-e2e")
STAGING = os.path.join(REPO, ".work", "site_staging")
EV = "2026wasam"
API_PORT = 8831


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def prepare():
    data = os.path.join(WORK, "data")
    site_api = os.path.join(data, "site", "api")
    media = os.path.join(WORK, "media")
    if not os.path.isdir(site_api):
        log("copying staging site data (dereferencing symlinks) ...")
        os.makedirs(os.path.dirname(site_api), exist_ok=True)
        subprocess.check_call(["cp", "-RL", os.path.join(STAGING, "api"), site_api])
    if not os.path.isdir(media):
        log("copying staging media ...")
        subprocess.check_call(["cp", "-RL", os.path.join(STAGING, "media"), media])
        # a stand-in "proxy" (small preview copy) so media mode 'proxy' has something to fetch
        os.makedirs(os.path.join(media, EV, "proxy"), exist_ok=True)
        with open(os.path.join(media, EV, f"{EV}_qm17.mp4"), "rb") as s, \
                open(os.path.join(media, EV, "proxy", f"{EV}_qm17.mp4"), "wb") as d:
            d.write(s.read(12 * 1024 * 1024))
    return data, media


def start_api(data, media):
    env = dict(os.environ, DATA=data, MEDIA=media, SETUP_CODE_REQUIRED="0", COOKIE_SECURE="0", EVENTS=EV,
               TBA_KEY="e2e" + "x" * 30, TBA_BASE="http://127.0.0.1:9/tba", CS_VERSION="e2e-1", SERVER_NAME="E2E",
               PYTHONPATH=REPO, SYNC_CACHE_MB="512")
    logf = open(os.path.join(WORK, "api.log"), "ab")
    p = subprocess.Popen([os.path.join(REPO, ".venv", "bin", "python"), "-m", "uvicorn", "server.main:app",
                          "--host", "127.0.0.1", "--port", str(API_PORT), "--log-level", "warning"],
                         cwd=REPO, env=env, stdout=logf, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/app/v1/health", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.2)
    p.kill()
    raise SystemExit("API did not start; see " + os.path.join(WORK, "api.log"))


def http(method, url, body=None, headers=None, cookies=None):
    h = dict(headers or {})
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        resp = urllib.request.urlopen(r, timeout=60)
    except urllib.error.HTTPError as e:
        resp = e
    for sc in resp.headers.get_all("Set-Cookie") or []:
        k, _, v = sc.split(";")[0].partition("=")
        if cookies is not None:
            cookies[k.strip()] = v.strip()
    b = resp.read()
    try:
        return resp.status if hasattr(resp, "status") else resp.code, json.loads(b) if b else None
    except ValueError:
        return resp.code, b


def ensure_admin(front_url):
    ck = {}
    code, s = http("GET", front_url + "/app/v1/session", cookies=ck)
    if s.get("setup_required"):
        code, d = http("POST", front_url + "/app/v1/setup", {"username": "admin", "password": "e2e-admin-password",
                                                              "display_name": "E2E Admin"},
                       headers={"X-CSRF-Token": ck["cs_csrf"]}, cookies=ck)
        assert code == 200, (code, d)
        log("created admin account")


class Local:
    def __init__(self, app):
        self.app, self.base = app, f"http://127.0.0.1:{app.port}"

    def req(self, method, path, body=None, headers=None):
        h = {"Origin": self.base, "X-CSRF-Token": self.app.config["local_csrf"]}
        h.update(headers or {})
        return http(method, self.base + path, body, h, {"cs_client": self.app.config["token"],
                                                        "cs_csrf": self.app.config["local_csrf"]})


def wait(cond, what, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return time.time() - t0
        time.sleep(0.05)
    raise SystemExit("timed out: " + what)


def sync(app, lc, front, **kw):
    e = app.engine
    n = e.cycles
    b0, r0, t0 = front.link.bytes_out, len(front.requests), time.time()
    code, d = lc.req("POST", "/client/v1/sync", kw)
    assert code == 202, (code, d)
    wait(lambda: e.cycles > n and not e.syncing, "sync")
    code, st = lc.req("GET", "/client/v1/status")
    return {"seconds": round(time.time() - t0, 2), "bytes": front.link.bytes_out - b0,
            "requests": len(front.requests) - r0, "errors": st["errors"]}, st


def mutate(site_api, tag):
    """A typical between-matches change: a few JSON files rewritten, one added, one removed."""
    d = os.path.join(site_api, EV)
    changed = []
    for rel in ("event.json", "rankings.json", "schedule.json"):
        p = os.path.join(d, rel)
        with open(p) as f:
            txt = f.read()
        obj = json.loads(txt)
        if isinstance(obj, dict):
            obj["_e2e"] = tag
        txt = json.dumps(obj)
        with open(p, "w") as f:
            f.write(txt)
        changed.append(rel)
    with open(os.path.join(d, f"e2e-{tag}.json"), "w") as f:
        json.dump({"tag": tag}, f)
    victims = sorted(x for x in os.listdir(os.path.join(d, "m")) if x.endswith(".json"))
    os.remove(os.path.join(d, "m", victims[-1]))
    return changed + [f"e2e-{tag}.json", "-m/" + victims[-1]]


def scenario(name, front, site_api, latency, rate, media_mode, cut_first_bundle=None):
    log(f"=== scenario {name}: latency {latency * 1000:.0f} ms, rate {rate or 'unlimited'} B/s, media {media_mode}")
    front.link.latency, front.link.rate = latency, rate
    cdir = os.path.join(WORK, f"client-{name}")
    shutil.rmtree(cdir, ignore_errors=True)
    out = {"latency_ms": latency * 1000, "rate_Bps": rate}
    b0, t0 = front.link.bytes_out, time.time()
    app = App(cdir, port=0, server_url=front.url, bundled_web=os.path.join(REPO, "web"))
    app.config.update(interval=3600, probe_interval=3600, media=media_mode)
    app.start()
    lc = Local(app)
    try:
        wait(lambda: app.mirror.version == "e2e-1", "web mirror", 300)
        out["web_mirror"] = {"seconds": round(time.time() - t0, 2), "bytes": front.link.bytes_out - b0,
                             "files": app.mirror.current.get("files")}
        wait(lambda: app.engine.cycles >= 1 and not app.engine.syncing, "startup cycle (not signed in yet)", 60)
        if cut_first_bundle:
            front.cut_bundle_after = cut_first_bundle
        # first sync = sign in (which starts a sync) until the data is all here
        n, r_before, b1, t1 = app.engine.cycles, len(front.requests), front.link.bytes_out, time.time()
        code, d = lc.req("POST", "/client/v1/login", {"username": "admin", "password": "e2e-admin-password"})
        assert code == 200, (code, d)
        wait(lambda: app.engine.last_sync and not app.engine.syncing, "first sync", 1800)
        code, st = lc.req("GET", "/client/v1/status")
        first = {"seconds": round(time.time() - t1, 2), "bytes": front.link.bytes_out - b1,
                 "requests": len(front.requests) - r_before, "errors": st["errors"]}
        ev = [e for e in st["events"] if e["key"] == EV][0]
        first.update(files=ev["files"], data_bytes=ev["bytes"], media_bytes=ev["media_bytes"])
        bundles = [r for r in front.requests[r_before:] if r[1].startswith("/app/v1/sync/bundle")]
        first["bundle_requests"] = len(bundles)
        first["media_requests"] = len([r for r in front.requests[r_before:] if r[1].startswith("/media/")])
        out["first_sync"] = first
        log("first sync:", json.dumps(first))
        # local copy == server copy
        bad = 0
        for dp, _, fs in os.walk(os.path.join(site_api, EV)):
            for fn in fs:
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, os.path.dirname(site_api))
                q = os.path.join(cdir, "site", rel)
                if not os.path.exists(q) or open(p, "rb").read() != open(q, "rb").read():
                    bad += 1
        out["first_sync"]["mismatched_files"] = bad
        # delta
        what = mutate(site_api, f"{name}-{int(time.time())}")
        time.sleep(2.2)                           # the server re-scans the site at most every 2 s
        delta, st = sync(app, lc, front, media=False)
        delta["changes"] = what
        ok = all(os.path.exists(os.path.join(cdir, "site", "api", EV, w)) for w in what if not w.startswith("-"))
        gone = [w for w in what if w.startswith("-")]
        ok = ok and all(not os.path.exists(os.path.join(cdir, "site", "api", EV, g[1:])) for g in gone)
        delta["applied_correctly"] = ok
        out["delta_sync"] = delta
        log("delta sync:", json.dumps(delta))
        idle, st = sync(app, lc, front, media=False)
        out["no_change_sync"] = idle
        log("no-change sync:", json.dumps(idle))
        # offline writes -> outbox -> flush exactly once
        tag = f"e2e {name} {int(time.time())} offline note"
        front.down = True
        tq = time.time()
        codes = []
        for i in range(5):
            code, d = lc.req("POST", f"/app/v1/events/{EV}/notes", {"text": f"{tag} {i}"})
            codes.append(code)
        queued_s = time.time() - tq
        code, st = lc.req("GET", "/client/v1/status")
        assert st["outbox"] == 5 and not st["online"], st
        front.lose_next_write_answer = True       # first replayed write: applied, answer lost -> must not duplicate
        front.down = False
        tf = time.time()
        app.engine.kick()                          # what the probe does when the link comes back
        wait(lambda: app.db.pending() == 0, "flush", 300)
        flush_s = time.time() - tf
        code, after = lc.req("GET", f"/app/v1/events/{EV}/notes")
        mine = [n for n in after if n.get("text", "").startswith(tag)]
        out["offline_flush"] = {"queued_status_codes": codes, "queue_seconds_total": round(queued_s, 2),
                                "flush_seconds": round(flush_s, 2), "notes_on_server": len(mine),
                                "distinct": len({n["text"] for n in mine}),
                                "order_ok": [n["text"] for n in sorted(mine, key=lambda n: n["id"])] ==
                                            [f"{tag} {i}" for i in range(5)]}
        log("offline flush:", json.dumps(out["offline_flush"]))
        out["log_tail"] = None
    finally:
        app.shutdown()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=("lan", "slow"))
    ap.add_argument("--rate", type=int, default=1_000_000)
    ap.add_argument("--latency", type=float, default=0.6)
    ap.add_argument("--serve", type=int, metavar="PORT", help="just run the API + front on PORT (manual testing)")
    a = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    setup_logging(os.path.join(WORK, "logs"), "INFO")
    import logging
    for h in logging.getLogger("cyberscout").handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(logging.WARNING)
    data, media = prepare()
    api = start_api(data, media)
    front = Front(API_PORT, os.path.join(data, "site", "api"), media, os.path.join(REPO, "web"),
                  port=a.serve or 0).start()
    if a.serve:
        ensure_admin(front.url)
        log(f"serving {front.url} (admin / e2e-admin-password); Ctrl+C to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            api.terminate()
        return
    res = {}
    try:
        ensure_admin(front.url)
        site_api = os.path.join(data, "site", "api")
        if a.only in (None, "lan"):
            res["lan"] = scenario("lan", front, site_api, 0.0, None, "proxy")
        if a.only in (None, "slow"):
            res["slow"] = scenario("slow", front, site_api, a.latency, a.rate, "none", cut_first_bundle=3_000_000)
    finally:
        front.stop()
        api.terminate()
        api.wait(10)
    print(json.dumps(res, indent=1))
    with open(os.path.join(WORK, "results.json"), "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
