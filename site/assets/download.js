/* Install + downloads (home page and install guide): fill the install commands with this site's own address (and
 * the model's SHA-256 from SHA256SUMS), pick the visitor's OS (?os=linux|macos|windows|android|ios overrides), OS
 * tabs, copy buttons, the published version, file sizes, and grey out downloads that aren't published yet.
 * No tracking: the OS is only read, never sent. */
(function () {
  "use strict";
  var site = location.origin, baked = "", modelSha = "";
  // get.sh / get.ps1 download from the address they were published with; reached at another one (a VPN address,
  // another name), the one-liners pass this one along (CS_SITE)
  function fill() {
    document.querySelectorAll("code.js-cmd").forEach(function (el) {
      var c = el.getAttribute("data-cmd").split("{SITE}").join(site)
        .split("{MODEL_SHA}").join(modelSha || "<robot.onnx SHA-256 from SHA256SUMS>");
      var one = el.getAttribute("data-one");
      if (one && baked && baked !== site) {
        c = one === "sh" ? c.replace("| bash", "| CS_SITE=" + site + " bash") : "$env:CS_SITE='" + site + "'; " + c;
      }
      el.textContent = c;
    });
    document.querySelectorAll(".js-origin").forEach(function (el) { el.textContent = site; });
  }
  fill();

  function detect() {
    var q = /[?&]os=(linux|macos|windows|android|ios)\b/.exec(location.search);
    if (q) return q[1];
    var ua = navigator.userAgent || "", p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/Android/i.test(ua)) return "android";
    if (/iPhone|iPad|iPod/i.test(ua) || (/Mac/i.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
    if (/Windows/i.test(ua)) return "windows";
    if (/Macintosh|Mac OS X/i.test(ua)) return "macos";
    if (/Linux|X11|CrOS/i.test(ua)) return "linux";
    return /Win/i.test(p) ? "windows" : /Mac/i.test(p) ? "macos" : /Linux/i.test(p) ? "linux" : null;
  }
  var os = detect();

  // OS tabs, one group per .oses: the server command and the Client command (a phone gets each group's first tab)
  [].forEach.call(document.querySelectorAll(".oses"), function (group) {
    var tabs = [].slice.call(group.querySelectorAll('[role="tab"]'));
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t); });
      t.addEventListener("keydown", function (e) {
        var j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : null;
        if (j === null) return;
        e.preventDefault(); select(tabs[(j + tabs.length) % tabs.length], true);
      });
    });
    var start = tabs.filter(function (t) { return t.getAttribute("data-os") === os; })[0] || tabs[0];
    if (start) select(start);
  });

  // highlight the Client build for this device. Macs: only Chromium browsers say which chip; Safari and Firefox
  // don't, so there it is "likely" Apple silicon (every Mac sold since 2021) and the note says how to check
  function mark(arch, sure) {
    var picked = null;
    document.querySelectorAll(".cl[data-os]").forEach(function (a) {
      if (picked || a.getAttribute("data-os") !== os) return;
      var aa = a.getAttribute("data-arch");
      if (aa && arch && aa !== arch) return;
      picked = a;
    });
    if (!picked) return;
    picked.classList.add("mine");
    if (!sure) picked.classList.add("likely");
    picked.setAttribute("aria-description", sure ? "Matches this device" : "Likely matches this Mac");
  }
  if (os === "macos" && navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
    navigator.userAgentData.getHighEntropyValues(["architecture"]).then(function (v) {
      if (v.architecture) mark(v.architecture === "x86" ? "x86" : "arm", true); else mark("arm", false);
    }, function () { mark("arm", false); });
  } else mark(os === "macos" ? "arm" : null, os !== "macos");

  // copy buttons
  var status = document.getElementById("copy-status");
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).catch(function () { return legacyCopy(text); });
    return legacyCopy(text);
  }
  function legacyCopy(text) {
    return new Promise(function (ok, fail) {   // plain-http LAN address: fall back to a hidden textarea
      var ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      var done = false; try { done = document.execCommand("copy"); } catch (e) { }
      document.body.removeChild(ta); done ? ok() : fail();
    });
  }
  document.querySelectorAll(".cmd .copy").forEach(function (b) {
    var label = b.querySelector("span"), timer;
    b.addEventListener("click", function () {
      var code = b.parentNode.querySelector("code");
      copyText(code.textContent).then(function () {
        b.classList.add("done"); label.textContent = "Copied";
        if (status) status.textContent = "Install command copied";
      }, function () {
        var r = document.createRange(); r.selectNodeContents(code);
        var s = getSelection(); s.removeAllRanges(); s.addRange(r);
        label.textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "Press \u2318C" : "Press Ctrl+C";
      });
      clearTimeout(timer);
      timer = setTimeout(function () { b.classList.remove("done"); label.textContent = "Copy"; }, 2200);
    });
  });

  // VERSION: "<commit> <commit date> <site it was published for>" -> "2026-10-19 (26791f4)"
  var v = document.getElementById("dl-ver");
  fetch("downloads/VERSION", { cache: "no-store" }).then(function (r) { return r.ok ? r.text() : ""; }).then(function (t) {
    var p = t.trim().split(/\s+/);
    if (v) v.textContent = p[1] ? p[1] + " (" + p[0] + ")" : p[0] || "unknown";
    if (p[2]) { baked = p[2].replace(/\/$/, ""); fill(); }
  }).catch(function () { if (v) v.textContent = "unknown"; });
  if (document.querySelector('code.js-cmd[data-cmd*="{MODEL_SHA}"]')) {
    fetch("downloads/SHA256SUMS", { cache: "no-store" }).then(function (r) { return r.ok ? r.text() : ""; }).then(function (t) {
      var m = /^([0-9a-f]{64})\s+\*?robot\.onnx$/m.exec(t);
      if (m) { modelSha = m[1]; fill(); }
    }).catch(function () { });
  }
  // grey out download links whose file isn't published yet; show the size of the ones that are
  function size(n) { return n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : n >= 1e6 ? Math.round(n / 1e6) + " MB" : Math.max(1, Math.round(n / 1e3)) + " kB"; }
  document.querySelectorAll('a.file[href^="downloads/"], a.cl[href^="downloads/"]').forEach(function (a) {
    fetch(a.getAttribute("href"), { method: "HEAD", cache: "no-store" }).then(function (r) {
      if (!r.ok) { a.classList.add("missing"); a.classList.remove("mine"); a.setAttribute("aria-disabled", "true"); a.title = "Not published yet"; a.removeAttribute("href"); return; }
      var n = +r.headers.get("Content-Length"), span = a.querySelector("span");
      if (n > 0 && span) span.textContent += ", " + size(n);
    }).catch(function () { });
  });
})();
