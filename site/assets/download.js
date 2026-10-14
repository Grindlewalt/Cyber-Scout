/* Install + downloads (home page and install guide): fill the one-line commands with this site's own address,
 * pick the visitor's OS (?os=linux|macos|windows|android|ios overrides), OS tabs, copy buttons, the published
 * version, and grey out downloads that aren't published yet. No tracking: the OS is only read, never sent. */
(function () {
  "use strict";
  var site = location.origin;
  document.querySelectorAll("code.js-cmd").forEach(function (el) {
    el.textContent = el.getAttribute("data-cmd").replace("{SITE}", site);
  });

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

  // OS tabs for the server command (a phone gets the Linux tab: the server goes on a computer)
  var tabs = [].slice.call(document.querySelectorAll('.oses [role="tab"]'));
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

  // highlight the Client build for this device (Macs: Apple silicon unless the browser says x86)
  function mark(arch) {
    var picked = null;
    document.querySelectorAll(".cl[data-os]").forEach(function (a) {
      if (picked || a.getAttribute("data-os") !== os) return;
      var aa = a.getAttribute("data-arch");
      if (aa && arch && aa !== arch) return;
      picked = a;
    });
    if (picked) { picked.classList.add("mine"); picked.setAttribute("aria-description", "Matches this device"); }
  }
  if (os === "macos" && navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
    navigator.userAgentData.getHighEntropyValues(["architecture"]).then(function (v) { mark(v.architecture === "x86" ? "x86" : "arm"); }, function () { mark("arm"); });
  } else mark(os === "macos" ? "arm" : null);

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

  var v = document.getElementById("dl-ver");
  if (v) fetch("downloads/VERSION", { cache: "no-store" }).then(function (r) { return r.ok ? r.text() : ""; })
    .then(function (t) { v.textContent = t.trim() || "unknown"; }).catch(function () { v.textContent = "unknown"; });
  // grey out download links whose file isn't published yet
  document.querySelectorAll('a.file[href^="downloads/"], a.cl[href^="downloads/"]').forEach(function (a) {
    fetch(a.getAttribute("href"), { method: "HEAD", cache: "no-store" }).then(function (r) {
      if (!r.ok) { a.classList.add("missing"); a.classList.remove("mine"); a.setAttribute("aria-disabled", "true"); a.title = "Not published yet"; a.removeAttribute("href"); }
    }).catch(function () { });
  });
})();
