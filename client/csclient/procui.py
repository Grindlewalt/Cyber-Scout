"""The client's own "Help process matches" page: http://127.0.0.1:7600/client/processing (served by web.py; the
page talks to GET/PUT /client/v1/processing and POST /client/v1/processing/{setup,bench,netcheck,gpu}).  Plain HTML
+ one script file (the shell CSP allows scripts from 'self' only); the app's Archivo comes from /fonts.css.

Top: three readouts with a verdict each (can this computer's GPU track, minutes per match, Wi-Fi to the server),
then the switch with Track matches / Clip only, the status line, Clip only's stream, Set up, and Advanced."""

PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Help process matches</title>
<link rel="stylesheet" href="/fonts.css">
<style>
:root{--bg:#eef1f4;--sheet:#fff;--ink:#14181d;--mute:#5b6571;--line:#d6dce3;--soft:#e6eaef;--acc:#4338ca;
--acc-ink:#fff;--go:#0e7a5a;--warn:#a85b06;--stop:#b3261e;--goT:#e3f4ee;--warnT:#fbf0e1;--stopT:#fbe9e7;
--font:"Archivo",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#0f1215;--sheet:#171b20;--ink:#e9edf1;--mute:#9aa4af;--line:#2b3138;
--soft:#21262c;--acc:#8b8cf7;--acc-ink:#0f1215;--go:#45c99a;--warn:#f0a548;--stop:#ff8676;--goT:#14302a;
--warnT:#33270f;--stopT:#3a1a17}}
*{box-sizing:border-box}html{background:var(--bg)}[hidden]{display:none!important}
body{margin:0;color:var(--ink);font:15px/1.5 var(--font);-webkit-font-smoothing:antialiased}
main{max-width:860px;margin:0 auto;padding:32px 16px 64px}
header{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:30px;line-height:1.1;margin:0;font-stretch:80%;font-weight:700;letter-spacing:-.01em}
header p{margin:6px 0 0;color:var(--mute);max-width:60ch}
a{color:var(--acc)}h2{font-size:17px;margin:0 0 10px;font-weight:650}
.strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));background:var(--sheet);border:1px solid var(--line);
border-radius:6px}
.ro{padding:16px 18px 18px;min-width:0}.ro+.ro{border-left:1px solid var(--line)}
.ro .k{color:var(--mute);font-size:13px}
.ro .v{font-size:40px;line-height:1.05;font-weight:700;font-stretch:75%;font-variant-numeric:tabular-nums;margin:4px 0 2px;
overflow-wrap:anywhere}
.ro .v small{font-size:20px;font-weight:600;margin-left:2px}
.ro .s{color:var(--mute);font-size:13px;min-height:2.6em}
.verdict{display:inline-block;margin-top:8px;font-weight:650;font-size:13px;padding:2px 9px;border-radius:999px;
background:var(--soft);color:var(--mute)}
.verdict.go{background:var(--goT);color:var(--go)}.verdict.warn{background:var(--warnT);color:var(--warn)}
.verdict.stop{background:var(--stopT);color:var(--stop)}
.lnk{background:var(--sheet);border:1px solid var(--line);border-radius:6px;padding:3px 10px;margin:8px 0 0 10px;font:inherit;
font-size:13px;color:var(--acc);cursor:pointer}.lnk:hover{border-color:var(--acc)}
.lnk:disabled{color:var(--mute);cursor:not-allowed;border-style:dashed;background:transparent}
.ro.off .v,.ro.off .k{color:var(--mute)}#role{margin-top:14px}
.verdict[hidden]+.lnk{margin-left:0}
.notes{margin:14px 0 0;display:grid;gap:8px}
.note{border-left:4px solid var(--warn);background:var(--sheet);padding:10px 14px;border-radius:0 6px 6px 0}
.note.bad{border-color:var(--stop)}.note.info{border-color:var(--line)}
.note .btns{margin-top:8px}
.panel{background:var(--sheet);border:1px solid var(--line);border-radius:6px;padding:18px;margin-top:18px}
.top{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.sw{appearance:none;border:0;width:56px;height:32px;border-radius:16px;background:var(--line);position:relative;
cursor:pointer;flex:none}.sw::after{content:"";position:absolute;top:4px;left:4px;width:24px;height:24px;border-radius:50%;
background:#fff;box-shadow:0 1px 2px #0005;transition:left .15s}
.sw[aria-checked=true]{background:var(--acc)}.sw[aria-checked=true]::after{left:28px}
.top .t{font-weight:650;font-size:17px;margin-right:auto}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:0;background:transparent;color:var(--ink);font:inherit;padding:7px 14px;cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button[aria-pressed=true]{background:var(--ink);color:var(--sheet);font-weight:650}
.line{font-size:20px;font-weight:650;margin:16px 0 10px;font-stretch:90%}.line.bad{color:var(--stop)}
.bar{height:6px;border-radius:3px;background:var(--soft);overflow:hidden}.bar>i{display:block;height:100%;width:0;
background:var(--acc);transition:width .4s}
.stats{display:flex;gap:26px;margin-top:14px;color:var(--mute);flex-wrap:wrap;font-size:14px}
.stats b{color:var(--ink);font-weight:650}
button.b{border:1px solid var(--line);background:var(--sheet);color:var(--ink);border-radius:6px;padding:7px 14px;
font:inherit;cursor:pointer}button.b.p{background:var(--acc);border-color:var(--acc);color:var(--acc-ink);font-weight:650}
button.b:disabled{opacity:.55;cursor:not-allowed}.sw:disabled{opacity:.5;cursor:not-allowed}.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.sw:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--acc);
outline-offset:2px}
.hint{color:var(--mute);font-size:13px;margin:8px 0 0;max-width:72ch}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 18px;margin:4px 0 0;font-size:14px}
.kv dt{color:var(--mute)}.kv dd{margin:0;overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:14px}
td{padding:6px 0;border-top:1px solid var(--soft)}td:first-child{font-weight:650;width:5.5em}
td.up{color:var(--go)}td.skip{color:var(--mute)}
pre{white-space:pre-wrap;font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;color:var(--mute);margin:12px 0 0;
max-height:200px;overflow:auto;background:var(--bg);padding:10px;border-radius:6px}
details{margin-top:18px}summary{cursor:pointer;font-weight:650}
label{display:block;font-weight:600;margin:14px 0 6px;font-size:14px}
input[type=text]{width:100%;padding:8px 10px;border-radius:6px;border:1px solid var(--line);background:var(--bg);
color:var(--ink);font:13px ui-monospace,Menlo,Consolas,monospace}
input[type=number]{width:96px;padding:6px 8px;border-radius:6px;border:1px solid var(--line);background:var(--bg);
color:var(--ink);font:inherit}
@media (max-width:640px){.strip{grid-template-columns:1fr}.ro+.ro{border-left:0;border-top:1px solid var(--line)}
.ro .v{font-size:34px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><main>
<header><div><h1>Help process matches</h1>
<p>This computer tracks match video on its GPU next to the server's worker, or records the stream and sends the server
match clips (Clip only). Needs an admin account.</p></div><a href="/">Back to Cyber-Scout</a></header>

<section class="strip" aria-label="This computer">
 <div class="ro"><div class="k">GPU</div><div class="v" id="gv">&hellip;</div><div class="s" id="gs"></div>
  <span class="verdict" id="gj"></span><button class="lnk" id="gpuagain">Check again</button></div>
 <div class="ro"><div class="k">Tracking one match</div><div class="v" id="sv">&ndash;</div><div class="s" id="ss"></div>
  <span class="verdict" id="sj"></span><button class="lnk" id="bench">Run benchmark</button></div>
 <div class="ro"><div class="k">Wi-Fi to the server</div><div class="v" id="nv">&ndash;</div><div class="s" id="ns"></div>
  <span class="verdict" id="nj"></span><button class="lnk" id="netcheck">Check connection</button></div>
</section>
<div class="note bad" id="role" hidden></div>
<div class="notes" id="notes" aria-live="polite"></div>

<section class="panel" aria-labelledby="swl">
 <div class="top"><button class="sw" id="sw" role="switch" aria-checked="false" aria-labelledby="swl"></button>
  <span class="t" id="swl">Help process matches</span>
  <span class="seg" role="group" aria-label="What this computer does"><button id="m-track" aria-pressed="true">Track matches</button><button id="m-clip" aria-pressed="false">Clip only</button></span></div>
 <div class="line" id="line" role="status" aria-live="polite">&hellip;</div>
 <div class="bar" aria-hidden="true"><i id="pct"></i></div>
 <div class="stats"><span>Done <b id="done">0</b></span><span>Failed <b id="failed">0</b></span>
 <span>Last <b id="last">&ndash;</b></span><span>Data this session <b id="data">0 MB</b></span></div>
 <div class="note" id="large" hidden><div id="largemsg"></div>
  <div class="btns"><button class="b p" id="allow">Allow large clips (until the client restarts)</button></div></div>
 <p class="hint" id="modehint"></p>
</section>

<section class="panel" id="clip" hidden aria-labelledby="clipt"><h2 id="clipt">Clip only</h2>
 <dl class="kv" id="clipkv"></dl><table id="cliptab" aria-label="Matches of this stream"><tbody></tbody></table></section>

<section class="panel" aria-labelledby="setupt"><h2 id="setupt">Set up processing</h2>
 <p class="hint" id="plan" style="margin-top:0"></p>
 <div class="btns"><button class="b p" id="setup">Set up processing</button></div>
 <div class="bar" id="setupbar" hidden style="margin-top:12px" aria-hidden="true"><i id="setuppct"></i></div>
 <pre id="log" hidden></pre></section>

<details class="panel"><summary>Advanced</summary>
 <p class="hint"><b>Data cost:</b> each match downloads its livestream clip (typically 150&ndash;250 MB) and uploads
 1&ndash;3 MB of results; the first run also downloads the code (~10 MB) and the detector model (~40 MB). Clip only
 downloads the stream (about 1 MB/s) and uploads the clips the server is missing.</p>
 <label for="warn">Ask before downloading a clip over (MB, 0 = never ask)</label>
 <input type="number" id="warn" min="0" max="100000" step="10"> <button class="b" id="savewarn">Save</button>
 <label for="py">Processing Python</label>
 <input type="text" id="py" placeholder="blank = the one Set up made" spellcheck="false">
 <p class="hint">Any Python with onnxruntime, av, opencv, numpy, scipy and pupil-apriltags, e.g. a Cyber-Scout
 checkout's <code>.venv/bin/python</code>.</p>
 <div class="btns"><button class="b" id="save">Save</button></div>
 <p class="hint" id="who"></p>
</details>
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
    if (!r.ok && r.status !== 409) throw new Error((d && d.detail) || ("HTTP " + r.status));
    return d;
  }
  const gb = b => b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round(b / 1e6) + " MB";
  const mb = b => b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(b < 1e7 ? 1 : 0) + " MB";
  const mbs = v => v >= 10e6 ? Math.round(v / 1e6) : (v / 1e6).toFixed(1);
  const mins = m => m >= 1.5 ? Math.round(m) + " min" : m * 60 < 1 ? "under a second" : Math.round(m * 60) + " s";
  const ago = t => { const s = Date.now() / 1000 - t; return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago"; };
  function verdict(id, cls, text) { const e = $(id); e.className = "verdict" + (cls ? " " + cls : ""); e.textContent = text || ""; e.hidden = !text; }
  function readouts(s) {
    const g = s.gpu, sp = s.speed || {}, b = sp.bench, n = s.net || {}, run = sp.running;
    if (!g) { $("gv").textContent = "\\u2026"; $("gs").textContent = "Checking"; verdict("gj", "", ""); }
    else {
      $("gv").textContent = g.ok ? g.name.replace(/ \\(.*\\)$/, "") : "No GPU";
      $("gs").textContent = g.ok ? (g.name.match(/\\((.*)\\)$/) ? g.name.match(/\\((.*)\\)$/)[1] + ", " : "") + g.detail
        : (g.name && !/^no /i.test(g.name) ? "Found " + g.name + (g.detail ? " (" + g.detail + ")" : "") + ": the tracker can't use it." : "The tracker needs an NVIDIA GPU or Apple silicon.");
      if (g.ok && b && b.v && !b.ok) verdict("gj", "stop", "GPU failed in the benchmark");
      else verdict("gj", g.ok ? "go" : "warn", g.ok ? "Can track" : "Clip only");
    }
    const m = sp.per_match_min;
    const clipOnly = s.mode === "clip";
    $("bench").disabled = !!(run && run.state === "running") || !s.python || !!(g && !g.ok) || clipOnly;
    // a disabled button says why (CLIENT-17)
    $("bench").textContent = !s.python && !clipOnly ? "Run benchmark (after Set up)" : "Run benchmark";
    $("bench").title = clipOnly ? "Clip only doesn't track: no benchmark needed" : !s.python ? "Set up processing first (below)" : "";
    $("sv").closest(".ro").classList.toggle("off", clipOnly);
    if (clipOnly && !(run && run.state === "running")) { $("sv").textContent = "\\u2013"; $("ss").textContent = "Not used in Clip only: this computer records and uploads, it doesn't track."; verdict("sj", "", ""); }
    else if (g && !g.ok && !(run && run.state === "running")) { $("sv").textContent = "\\u2013"; $("ss").textContent = "Tracking needs a usable GPU."; verdict("sj", "", ""); }
    else if (run && run.state === "running") { $("sv").textContent = (run.pct || 0) + "%"; $("ss").textContent = "Benchmarking: " + (run.message || ""); verdict("sj", "", ""); }
    else if (m == null) { $("sv").textContent = "\\u2013";
      $("ss").textContent = (s.python ? "Not measured yet." : "Measured after Set up.") + (sp.typical_min ? " " + sp.typical_of + " takes about " + sp.typical_min + " min per match; the server about " + sp.server_min + "." : "");
      verdict("sj", "", ""); }
    else {
      $("sv").innerHTML = (m < 10 ? m.toFixed(1) : Math.round(m)) + "<small>min</small>";
      const src = sp.from === "measured" ? "Measured on real matches here" : "Benchmark estimate (" + (b && b.provider ? b.provider.replace("ExecutionProvider", "") : "") + ")";
      $("ss").textContent = src + "; the server takes about " + sp.server_min + " min.";
      verdict("sj", sp.slow ? "stop" : "go", sp.slow ? "Too slow to track" : "Fast enough");
    }
    if (run && run.state === "error") $("ss").textContent = "Benchmark failed: " + run.message;
    $("netcheck").disabled = !!n.probing;
    if (n.probing) { $("nv").textContent = "\\u2026"; $("ns").textContent = "Checking the connection"; verdict("nj", "", ""); }
    else if (!n.down_bps) { $("nv").textContent = "\\u2013"; $("ns").textContent = n.error ? "Couldn't check: " + n.error : "Not checked yet."; verdict("nj", "", ""); }
    else {
      const clipUp = s.mode === "clip" && n.up_bps;           // Clip only uploads: lead with that
      $("nv").innerHTML = mbs(clipUp ? n.up_bps : n.down_bps) + "<small>MB/s " + (clipUp ? "up" : "down") + "</small>";
      const other = clipUp ? mbs(n.down_bps) + " MB/s down" : n.up_bps ? mbs(n.up_bps) + " MB/s up" : "";
      const t = clipUp ? "a " + Math.round(n.clip_bytes / 1e6) + " MB clip uploads in " + mins(n.clip_up_min)
        : "a " + Math.round(n.clip_bytes / 1e6) + " MB clip downloads in " + mins(n.clip_down_min);
      $("ns").textContent = other ? other + "; " + t + "." : t[0].toUpperCase() + t.slice(1) + ".";
      verdict("nj", n.slow ? "warn" : "go", n.slow ? "Slow Wi-Fi" : "Good");
    }
  }
  function notes(s) {
    const box = $("notes"), list = (s.notices || []).filter(x => !(x.kind === "no_gpu" && s.mode === "clip"));
    box.textContent = "";
    for (const x of list) {
      const d = document.createElement("div"); d.className = "note" + (x.level === "bad" ? " bad" : x.level === "info" ? " info" : "");
      const p = document.createElement("div"); p.textContent = x.text; d.appendChild(p);
      if ((x.kind === "slow_gpu" || x.kind === "no_gpu" || x.kind === "gpu_failed") && s.mode !== "clip") {
        const bs = document.createElement("div"); bs.className = "btns";
        const c = document.createElement("button"); c.className = "b p"; c.textContent = "Switch to Clip only"; c.dataset.act = "clip"; bs.appendChild(c);
        if (x.kind === "slow_gpu" && !s.allow_slow) { const t = document.createElement("button"); t.className = "b"; t.textContent = "Track anyway"; t.dataset.act = "slow"; bs.appendChild(t); }
        d.appendChild(bs);
      }
      box.appendChild(d);
    }
  }
  function clip(s) {
    const c = s.clip, sec = $("clip");
    sec.hidden = !(c && s.mode === "clip");
    if (sec.hidden) return;
    const r = c.rec || {}, kv = $("clipkv"); kv.textContent = "";
    const row = (k, v) => { const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = k; dd.textContent = v; kv.append(dt, dd); };
    row("Stream", (c.label || c.source) + (c.url ? " (" + c.url + ")" : ""));
    row("Why this one", c.role === "backup" ? "The server records this stream too: clips go up only for matches it misses." : "The server doesn't record this stream: every match it lacks goes up.");
    row("Recorder", (r.state || "starting") + (r.msg ? ": " + r.msg : "") + (r.behind_s != null ? ", " + Math.round(r.behind_s) + " s behind live" : ""));
    row("On disk", r.bytes != null ? mb(r.bytes) + " of recording (the last 2 h are kept)" : "\\u2013");
    row("Clips", c.uploaded + " uploaded, " + c.skipped + " the server had already");
    const tb = $("cliptab").tBodies[0]; tb.textContent = "";
    for (const m of (c.matches || []).slice().reverse()) {
      const tr = document.createElement("tr"), a = document.createElement("td"), b = document.createElement("td");
      a.textContent = m.match; b.textContent = m.state; b.className = m.state === "uploaded" ? "up" : m.state === "the server has it" ? "skip" : "";
      tr.append(a, b); tb.appendChild(tr);
    }
  }
  function setup(s) {
    const pl = s.plan, st = s.setup, lg = $("log");
    let t = "";
    if (pl) {
      const what = pl.runtime === "clip" ? "the " + pl.label + " (this computer can't track)" : "the " + pl.label;
      t = pl.up_to_date ? "Installed: " + what + "." : (pl.installed ? "Updates " : "Installs ") + what + ": about " + gb(pl.download) + " to download, " + gb(pl.disk) + " on disk (" + gb(pl.free) + " free).";
      if (!pl.ok) t += " " + pl.why;
    }
    if (s.python_setting) t = "Using the Processing Python set under Advanced (" + s.python_setting + "). " + (pl && !pl.installed ? "Set up would install its own instead: " + t.replace(/^Installs /, "") : "");
    $("plan").textContent = t || "Checking what this computer needs\\u2026";
    const running = !!(st && st.state === "running");
    const u = s.user, admin = !!(u && u.role === "admin");
    $("setup").disabled = running || !!(pl && !pl.ok) || !admin;
    $("setup").title = admin ? "" : "Needs an admin account";
    $("setup").textContent = pl && pl.installed ? "Set up again" : "Set up processing";
    const pr = st && st.progress;
    $("setupbar").hidden = !(running && pr); if (pr) $("setuppct").style.width = pr.pct + "%";
    if (st) { lg.hidden = false; lg.textContent = (st.log || []).join("\\n") + "\\n" + st.state + ": " + (st.message || ""); }
  }
  function paint(s) {
    last = s;
    // the server gives processing jobs to admins only: say who is signed in, and don't offer what can't run (CLIENT-16)
    const u = s.user, admin = !!(u && u.role === "admin"), role = $("role");
    role.hidden = admin;
    role.textContent = admin ? "" : u ? "Signed in as " + (u.display_name || "a user") + " (" + (u.role || "no role") + "): helping process matches needs an admin account. Sign in as an admin in Cyber-Scout on this computer to use this."
      : "Not signed in: sign in with an admin account in Cyber-Scout on this computer first.";
    $("sw").disabled = !admin && !s.enabled;          // (it can always be switched off)
    $("sw").setAttribute("aria-checked", s.enabled ? "true" : "false");
    $("m-track").setAttribute("aria-pressed", s.mode === "track" ? "true" : "false");
    $("m-clip").setAttribute("aria-pressed", s.mode === "clip" ? "true" : "false");
    const l = $("line"); l.textContent = s.enabled ? s.line : "Off";
    l.className = "line" + (/^(error|needs_setup|no_gpu)/.test(s.state) && s.enabled ? " bad" : "");
    $("pct").style.width = (s.pct == null ? 0 : s.pct) + "%";
    $("done").textContent = s.done || 0; $("failed").textContent = s.failed || 0;
    const d = s.data || {};
    $("data").textContent = mb(d.down || 0) + " down, " + mb(d.up || 0) + " up";
    const lgc = s.state === "confirm" && s.large;
    $("large").hidden = !lgc; if (lgc) $("largemsg").textContent = s.message;
    if (document.activeElement !== $("warn")) $("warn").value = s.warn_mb;
    const L = s.last; $("last").textContent = L ? (L.key.split("_").pop() + (L.ok ? " ok" : " failed")) : "\\u2013";
    $("who").textContent = "Processor " + s.processor + (s.pipeline ? ", pipeline " + s.pipeline : "") + (s.python ? ", Python " + s.python : "");
    if (!typing && document.activeElement !== $("py")) $("py").value = s.python_setting || "";
    $("modehint").textContent = s.mode === "clip"
      ? "Clip only records a livestream the server assigns, cuts each match by its TBA start time and uploads the clips the server is missing. No GPU needed."
      : "Track matches claims the next match the server would track, downloads its clip, runs the tracker on this GPU and uploads the results.";
    readouts(s); notes(s); clip(s); setup(s);
  }
  async function refresh() { try { paint(await call("GET", "processing")); } catch (e) { $("line").textContent = e.message; } }
  async function act(method, path, body) { try { const d = await call(method, path, body); if (d && d.enabled !== undefined) paint(d); else refresh(); } catch (e) { alert(e.message); } }
  $("sw").addEventListener("click", () => act("PUT", "processing", { enabled: !(last && last.enabled) }));
  $("m-track").addEventListener("click", () => act("PUT", "processing", { mode: "track" }));
  $("m-clip").addEventListener("click", () => act("PUT", "processing", { mode: "clip" }));
  $("notes").addEventListener("click", e => { const b = e.target.closest("button[data-act]"); if (!b) return;
    act("PUT", "processing", b.dataset.act === "clip" ? { mode: "clip" } : { allow_slow: true }); });
  $("gpuagain").addEventListener("click", () => act("POST", "processing/gpu", {}));
  $("bench").addEventListener("click", () => act("POST", "processing/bench", {}));
  $("netcheck").addEventListener("click", () => act("POST", "processing/netcheck", {}));
  $("py").addEventListener("input", () => { typing = true; });
  $("save").addEventListener("click", () => { typing = false; act("PUT", "processing", { python: $("py").value.trim() || null }); });
  $("allow").addEventListener("click", () => act("PUT", "processing", { allow_large: true }));
  $("savewarn").addEventListener("click", () => { const v = parseInt($("warn").value, 10); act("PUT", "processing", { warn_mb: isNaN(v) ? 0 : v }); });
  $("setup").addEventListener("click", () => {
    const pl = last && last.plan;           // a big download: ask first (a metered hotspot, CLIENT-17)
    if (pl && pl.download && !confirm("Download about " + gb(pl.download) + " now (" + gb(pl.disk) + " on disk, " + gb(pl.free) + " free)? On venue Wi-Fi this can take a while.")) return;
    act("POST", "processing/setup", {});
  });
  refresh(); setInterval(refresh, 2000);
})();
"""
