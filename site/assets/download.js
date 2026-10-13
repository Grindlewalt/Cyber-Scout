/* Download page: fill the one-line install commands with this site's own address, and show the published version. */
(function () {
  "use strict";
  var site = location.origin;
  document.querySelectorAll("code.js-cmd").forEach(function (el) {
    el.textContent = el.getAttribute("data-cmd").replace("{SITE}", site);
  });
  var v = document.getElementById("dl-ver");
  if (v) fetch("downloads/VERSION", { cache: "no-store" }).then(function (r) { return r.ok ? r.text() : ""; })
    .then(function (t) { v.textContent = t.trim() || "unknown"; }).catch(function () { v.textContent = "unknown"; });
  // grey out download links whose file isn't published yet
  document.querySelectorAll('a.file[href^="downloads/"]').forEach(function (a) {
    fetch(a.getAttribute("href"), { method: "HEAD", cache: "no-store" }).then(function (r) {
      if (!r.ok) { a.classList.add("missing"); a.setAttribute("aria-disabled", "true"); a.title = "Not published yet"; a.removeAttribute("href"); }
    }).catch(function () {});
  });
})();
