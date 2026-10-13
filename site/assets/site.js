/* Footer appearance switch (Auto / Light / Dark) and closing the phone menu after a jump. No tracking, no network. */
(function () {
  var h = document.documentElement;
  // ?eager: load every image now (headless screenshots of the whole page)
  if (/[?&]eager\b/.test(location.search)) document.querySelectorAll('img[loading="lazy"]').forEach(function (i) { i.loading = "eager"; });
  document.querySelectorAll(".mode button").forEach(function (b) {
    var sync = function () { b.setAttribute("aria-pressed", String(h.getAttribute("data-mode") === b.value)); };
    sync();
    b.addEventListener("click", function () {
      h.setAttribute("data-mode", b.value);
      try { if (b.value === "auto") localStorage.removeItem("cs:mode"); else localStorage.setItem("cs:mode", b.value); } catch (e) { }
      document.querySelectorAll(".mode button").forEach(function (x) { x.setAttribute("aria-pressed", String(x.value === b.value)); });
    });
  });
  var menu = document.querySelector(".menu");
  if (menu) {
    menu.addEventListener("click", function (e) { if (e.target.closest("a")) menu.open = false; });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
    document.addEventListener("click", function (e) { if (menu.open && !menu.contains(e.target)) menu.open = false; });
  }
  // ?probe: write horizontal-overflow metrics into <html data-probe> for headless checks
  if (/[?&]probe\b/.test(location.search)) setTimeout(function () {
    var over = [].slice.call(document.querySelectorAll("body *")).filter(function (e) { var r = e.getBoundingClientRect(); return r.width && r.right > h.clientWidth + 1 && getComputedStyle(e).position !== "fixed" && !e.closest(".scroll-x, pre"); })
      .slice(0, 8).map(function (e) { return e.tagName.toLowerCase() + (e.className && typeof e.className === "string" ? "." + e.className.split(" ")[0] : ""); });
    h.setAttribute("data-probe", JSON.stringify({ sw: h.scrollWidth, cw: h.clientWidth, over: over }));
  }, 1500);
})();
