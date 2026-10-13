#!/usr/bin/env python3
"""A stand-in Cyber-Scout server for the Android instrumented smoke test (and manual tries):

    python3 android/tools/mock_server.py [port]          # default 8765, listens on 127.0.0.1

GET /app/v1/info   -> {"app":"cyber-scout","api":1,"version":"mock","name":"Cyber-Scout mock",...} (docs/SYNC.md)
GET /              -> a tiny page with <h1 id="mock-app">Cyber-Scout mock web app</h1>
The emulator reaches it as 10.0.2.2:<port>. Standard library only."""
import http.server
import json
import sys
import time

PAGE = b"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Cyber-Scout mock</title></head>
<body><h1 id="mock-app">Cyber-Scout mock web app</h1><p id="js">no script</p>
<script>document.getElementById("js").textContent = "script ran; localStorage " +
  (function () { try { localStorage.setItem("cs-mock", "1"); return localStorage.getItem("cs-mock") === "1" ? "works" : "broken"; } catch (e) { return "blocked"; } })();</script>
</body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/app/v1/info":
            body, ctype, code = json.dumps({"app": "cyber-scout", "api": 1, "version": "mock", "name": "Cyber-Scout mock",
                                            "time": int(time.time())}).encode(), "application/json", 200
        elif path in ("/", "/index.html"):
            body, ctype, code = PAGE, "text/html; charset=utf-8", 200
        else:
            body, ctype, code = b'{"detail":"not found"}', "application/json", 404
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *a):
        sys.stderr.write("mock: " + (fmt % a) + "\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"mock Cyber-Scout server on http://127.0.0.1:{port}/", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
