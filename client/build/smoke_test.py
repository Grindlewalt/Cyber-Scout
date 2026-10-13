#!/usr/bin/env python3
"""Smoke-test a built Cyber-Scout Client executable (or the source entry point) on this machine.

    python3 client/build/smoke_test.py client/dist/cyberscout-client-macos-arm64
    python3 client/build/smoke_test.py client/dist/cyberscout-client-linux-x64 --server https://scout.grantrindal.dev

Starts it with --no-browser on 127.0.0.1:<free port> with a throwaway data folder, then checks, like the page does:
  GET  /                    the bundled web app is served (and sets the cs_client cookie the control API requires)
  GET  /client/v1/status    {"client": true, "version": ...}
  POST /client/v1/test      (only with --server) the server's public GET /app/v1/info answers: read-only, no sign-in.
                            --server mock starts a tiny stand-in server here (GET /app/v1/info only), for CI.
and stops it. Exit status 0 = pass. Pure standard library; works on Windows, macOS and Linux."""
import argparse
import http.cookiejar
import http.server
import threading
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockInfo(http.server.BaseHTTPRequestHandler):
    """Answers GET /app/v1/info like a Cyber-Scout server (docs/SYNC.md section 1); everything else 404."""
    def do_GET(self):
        if self.path.split("?")[0] == "/app/v1/info":
            body = json.dumps({"app": "cyber-scout", "api": 1, "version": "mock", "name": "Smoke-test mock",
                               "time": int(time.time())}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = b'{"detail":"not found"}'
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_mock():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockInfo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", help="the built executable, or client/cyberscout_client.py")
    ap.add_argument("--server", help="also POST /client/v1/test against this server (read-only /app/v1/info); "
                    "'mock' = a stand-in started here")
    ap.add_argument("--timeout", type=float, default=60, help="seconds to wait for the client to come up")
    a = ap.parse_args(argv)

    if a.server == "mock":
        _mock, a.server = start_mock()
    exe = os.path.abspath(a.exe)
    cmd = [sys.executable, exe] if exe.endswith(".py") else [exe]
    port = free_port()
    data = tempfile.mkdtemp(prefix="cs-smoke-")
    log_path = os.path.join(data, "stdout.log")
    base = f"http://127.0.0.1:{port}"
    print(f"smoke: starting {os.path.basename(exe)} on {base} (data {data})", flush=True)
    logf = open(log_path, "wb")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(cmd + ["--no-browser", "--host", "127.0.0.1", "--port", str(port), "--data", data],
                            stdout=logf, stderr=subprocess.STDOUT, env=env, creationflags=flags)
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    failures = []

    def check(name, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""), flush=True)
        if not ok:
            failures.append(name)

    try:
        t0 = time.time()
        page = None
        while time.time() - t0 < a.timeout:
            if proc.poll() is not None:
                break
            try:
                with op.open(base + "/", timeout=5) as r:
                    page = (r.status, r.headers.get("X-Cyber-Scout-Client"), r.read())
                break
            except OSError:
                time.sleep(0.5)
        if page is None:
            check("client starts and serves /", False, f"exit code {proc.poll()}" if proc.poll() is not None
                  else f"no answer within {a.timeout:.0f} s")
            return 1
        status, ver_hdr, body = page
        check("GET / serves the web app", status == 200 and b"Cyber-Scout" in body,
              f"{status}, {len(body)} bytes, {time.time() - t0:.1f} s after start")
        check("cs_client cookie set", any(c.name == "cs_client" for c in jar))

        with op.open(base + "/client/v1/status", timeout=10) as r:
            st = json.loads(r.read())
        check("GET /client/v1/status", r.status == 200 and st.get("client") is True and bool(st.get("version")),
              f"version={st.get('version')} server={st.get('server')} online={st.get('online')} "
              f"web_version={st.get('web_version')}")

        if a.server:
            req = urllib.request.Request(base + "/client/v1/test", data=json.dumps({"server": a.server}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with op.open(req, timeout=30) as r:
                t = json.loads(r.read())
            check(f"POST /client/v1/test {a.server}", t.get("ok") is True,
                  f"name={t.get('name')!r} version={t.get('version')} api={t.get('api')} "
                  f"latency_ms={t.get('latency_ms')}" if t.get("ok") else f"error={t.get('error')}")
        check("process still running", proc.poll() is None)
    except Exception as e:  # noqa: BLE001 - report any failure as a failed check
        check("smoke test", False, f"{type(e).__name__}: {e}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(10)
        logf.close()
        if failures:
            print("---- client output ----")
            with open(log_path, "rb") as f:
                print(f.read().decode("utf-8", "replace")[-4000:])
        shutil.rmtree(data, ignore_errors=True)
    print("smoke: " + ("OK" if not failures else "FAILED: " + ", ".join(failures)), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
