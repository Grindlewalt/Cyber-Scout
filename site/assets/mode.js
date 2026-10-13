/* Appearance before first paint: ?mode=light|dark|auto (not remembered), else the choice saved on this device
 * (localStorage cs:mode, the same key the Cyber-Scout app uses), else follow the system. */
(function () {
  var m = null, q = /[?&]mode=(light|dark|auto)\b/.exec(location.search);
  try { m = localStorage.getItem("cs:mode"); } catch (e) { }
  if (q) m = q[1];
  document.documentElement.setAttribute("data-mode", m === "light" || m === "dark" ? m : "auto");
})();
