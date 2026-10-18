"""The client's own "Help process matches" page: http://127.0.0.1:7600/client/processing (served by web.py; the
page talks to GET/PUT /client/v1/processing and POST /client/v1/processing/setup).  Plain HTML + one script file
(the shell CSP allows scripts from 'self' only)."""

PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Help process matches</title>
<style>
:root{--bg:#f6f4ef;--card:#fffdf8;--ink:#1d1b18;--mute:#6b665c;--line:#e3ddd0;--acc:#0b7a75;--acc2:#e0602b;
--ok:#2f8f46;--bad:#c2410c;--bar:#e9e3d6}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1d2024;--ink:#ece8df;--mute:#a19b90;--line:#30343a;
--acc:#3cc6bd;--acc2:#ff8a57;--ok:#5fcf7a;--bad:#ff8a57;--bar:#2a2e34}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,
"Segoe UI",sans-serif}main{max-width:640px;margin:0 auto;padding:28px 16px 48px}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.01em}p.sub{margin:0 0 20px;color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:0 0 14px}
.row{display:flex;align-items:center;gap:14px;justify-content:space-between}
.sw{appearance:none;border:0;width:58px;height:32px;border-radius:16px;background:var(--bar);position:relative;
cursor:pointer;flex:none}.sw::after{content:"";position:absolute;top:4px;left:4px;width:24px;height:24px;
border-radius:50%;background:#fff;box-shadow:0 1px 3px #0004;transition:left .15s}
.sw[aria-checked=true]{background:var(--acc)}.sw[aria-checked=true]::after{left:30px}
.sw:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid var(--acc2);outline-offset:2px}
.line{font-size:18px;font-weight:600;margin:6px 0 10px}.line.bad{color:var(--bad)}
.bar{height:8px;border-radius:4px;background:var(--bar);overflow:hidden}.bar>i{display:block;height:100%;width:0;
background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .4s}
.stats{display:flex;gap:22px;margin-top:12px;color:var(--mute);flex-wrap:wrap}.stats b{color:var(--ink);font-size:17px}
label{display:block;font-weight:600;margin:0 0 6px}input[type=text]{width:100%;padding:9px 10px;border-radius:9px;
border:1px solid var(--line);background:var(--bg);color:var(--ink);font:13px ui-monospace,Menlo,monospace}
button.b{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:9px;padding:8px 14px;
font:inherit;cursor:pointer}button.b.p{background:var(--acc);border-color:var(--acc);color:#fff}
.hint{color:var(--mute);font-size:13px;margin:6px 0 0}.warn{background:color-mix(in srgb,var(--acc2) 14%,var(--card));
border:1px solid var(--acc2);border-radius:10px;padding:10px 12px;margin:12px 0 0}input[type=number]{width:90px;
padding:7px 8px;border-radius:9px;border:1px solid var(--line);background:var(--bg);color:var(--ink);font:inherit}.btns{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
pre{white-space:pre-wrap;font:12px/1.4 ui-monospace,Menlo,monospace;color:var(--mute);margin:10px 0 0;max-height:180px;
overflow:auto}a{color:var(--acc)}code{font:12px ui-monospace,Menlo,monospace}
</style></head><body><main>
<h1>Help process matches</h1>
<p class="sub">This computer tracks match video for the server, next to the server's own worker.
Needs an admin account. <a href="/">Back to Cyber-Scout</a></p>
<section class="card">
 <div class="row"><div><b>Help process matches</b><div class="hint" id="who"></div></div>
 <button class="sw" id="sw" role="switch" aria-checked="false" aria-label="Help process matches"></button></div>
 <div class="line" id="line" role="status" aria-live="polite">&hellip;</div>
 <div class="bar" aria-hidden="true"><i id="pct"></i></div>
 <div class="stats"><span>Done <b id="done">0</b></span><span>Failed <b id="failed">0</b></span>
 <span>Last <b id="last">&ndash;</b></span><span>Data this session <b id="data">0 MB</b></span></div>
 <div class="warn" id="large" hidden><div id="largemsg"></div>
 <div class="btns"><button class="b p" id="allow">Allow large clips (until the client restarts)</button></div></div>
 <p class="hint"><b>Data cost:</b> each match downloads its livestream clip (typically 150&ndash;250 MB) and uploads
 1&ndash;3 MB of results; the first run also downloads the code (~10 MB) and the detector model (~40 MB). On a metered
 link (phone hotspot) leave this off.</p>
 <p class="hint"><label for="warn" style="display:inline;font-weight:400">Ask before downloading a clip over</label>
 <input type="number" id="warn" min="0" max="100000" step="10"> MB (0 = never ask)
 <button class="b" id="savewarn">Save</button></p>
</section>
<section class="card">
 <label for="py">Processing Python</label>
 <input type="text" id="py" placeholder="/path/to/.venv/bin/python (blank = the one Set up made)" spellcheck="false">
 <p class="hint">A Python with onnxruntime, av, opencv, numpy, scipy and pupil-apriltags, e.g. a Cyber-Scout
 checkout's <code>.venv/bin/python</code>. Or let <b>Set up processing</b> make one (needs
 <a href="https://docs.astral.sh/uv/" rel="noopener">uv</a>; downloads ~400 MB once).</p>
 <div class="btns"><button class="b" id="save">Save</button><button class="b p" id="setup">Set up processing</button></div>
 <pre id="log" hidden></pre>
</section>
</main><script src="/client/processing.js"></script></body></html>
"""

SCRIPT = b"""\"use strict\";
(function () {
  const $ = id => document.getElementById(id);
  const csrf = () => (document.cookie.match(/(?:^|; )cs_csrf=([^;]*)/) || [])[1] || "";
  let typing = false, last = null;
  async function call(method, path, body) {
    const o = { method, credentials: "same-origin", cache: "no-store", headers: {} };
    if (method !== "GET") { o.headers["X-CSRF-Token"] = csrf(); o.headers["Content-Type"] = "application/json";
      o.body = JSON.stringify(body || {}); }
    const r = await fetch("/client/v1/" + path, o);
    let d = null; try { d = await r.json(); } catch (e) { }
    if (!r.ok) throw new Error((d && d.detail) || ("HTTP " + r.status));
    return d;
  }
  function paint(s) {
    last = s;
    $("sw").setAttribute("aria-checked", s.enabled ? "true" : "false");
    const l = $("line"); l.textContent = s.enabled ? s.line : "Off";
    l.className = "line" + (/^(error|needs_setup)/.test(s.state) ? " bad" : "");
    $("pct").style.width = (s.pct == null ? 0 : s.pct) + "%";
    $("done").textContent = s.done || 0; $("failed").textContent = s.failed || 0;
    const d = s.data || {}, mb = b => (b / 1048576).toFixed(b < 10485760 ? 1 : 0) + " MB";
    $("data").textContent = mb(d.down || 0) + " down \\u00b7 " + mb(d.up || 0) + " up";
    const lg = s.state === "confirm" && s.large;
    $("large").hidden = !lg; if (lg) $("largemsg").textContent = s.message;
    if (document.activeElement !== $("warn")) $("warn").value = s.warn_mb;
    const L = s.last; $("last").textContent = L ? (L.key.split("_").pop() + (L.ok ? " ok" : " failed")) : "\\u2013";
    $("who").textContent = "Processor " + s.processor + (s.pipeline ? " \\u00b7 pipeline " + s.pipeline : "");
    if (!typing && document.activeElement !== $("py")) $("py").value = s.python_setting || "";
    const st = s.setup, lgo = $("log");
    if (st) { lgo.hidden = false; lgo.textContent = (st.log || []).join("\\n") + "\\n" + st.state + ": " + (st.message || ""); }
    $("setup").disabled = !!(st && st.state === "running");
  }
  async function refresh() { try { paint(await call("GET", "processing")); } catch (e) { $("line").textContent = e.message; } }
  $("sw").addEventListener("click", async () => {
    try { paint(await call("PUT", "processing", { enabled: !(last && last.enabled) })); } catch (e) { alert(e.message); }
  });
  $("py").addEventListener("input", () => { typing = true; });
  $("save").addEventListener("click", async () => {
    typing = false;
    try { paint(await call("PUT", "processing", { python: $("py").value.trim() || null })); } catch (e) { alert(e.message); }
  });
  $("allow").addEventListener("click", async () => {
    try { paint(await call("PUT", "processing", { allow_large: true })); } catch (e) { alert(e.message); }
  });
  $("savewarn").addEventListener("click", async () => {
    const v = parseInt($("warn").value, 10);
    try { paint(await call("PUT", "processing", { warn_mb: isNaN(v) ? 0 : v })); } catch (e) { alert(e.message); }
  });
  $("setup").addEventListener("click", async () => {
    try { await call("POST", "processing/setup", {}); refresh(); } catch (e) { alert(e.message); }
  });
  refresh(); setInterval(refresh, 2000);
})();
"""
