/* Cyber-Scout demo: loaded after every app script. Wraps the app's router so that pages whose data is not in this
 * frozen copy show a friendly "not in the demo" panel (in the app's own empty-state style) instead of an error,
 * swaps "Sign out" for "Leave demo", and adds one line to the footer. The app code itself is unchanged. */
(function () {
  "use strict";
  var C = window.DEMO || {};
  var EV = C.ev, HOME = "#/" + EV + "/dash/" + C.ts;
  var times = (C.times || []).map(String), teams = C.teams || [], matches = C.matches || [];
  var SITE = "../index.html";
  var parts = function () { return location.hash.replace(/^#\/?/, "").split("/").filter(Boolean); };
  var h = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var short = function (k) { var x = String(k || "").split("_").pop().match(/^([a-z]+)(\d+)(?:m(\d+))?$/); if (!x) return k; return ({ qm: "Qualification ", sf: "Playoff ", f: "Final " }[x[1]] || x[1].toUpperCase() + " ") + x[2]; };

  function allowed(p) {
    if (!p.length) return true;
    if (p[0] === "settings") return true;
    if (p[0] !== EV) return false;
    var s = p[1], a = (p[2] || "").split("?")[0];
    switch (s) {
      case undefined: return true;
      case "dash": return !a || a === "live" || times.indexOf(a) >= 0;
      case "selection": case "picklist": return !a || times.indexOf(a) >= 0;
      case "schedule": case "rankings": case "teams": case "compare": case "matches": case "defense": return true;
      case "specs": return !a || teams.indexOf(a) >= 0;
      case "t": return teams.indexOf(a) >= 0;
      case "m": case "a": return matches.indexOf(a) >= 0;
      default: return false;
    }
  }
  function what(p) {
    var s = p[1], a = (p[2] || "").split("?")[0];
    if (p[0] !== EV) return ({ admin: "Admin", activity: "Activity", account: "Your account", connect: "Connection", fixes: "Identity fixes" }[p[0]] || "This page");
    if (s === "m" || s === "a") return short(a);
    if (s === "t" || s === "specs") return "Team " + a.replace("frc", "");
    return ({ sources: "Video setup", fixes: "Identity fixes" }[s] || "This page");
  }
  function notInDemo(p, scroll) {
    try { cleanup.forEach(function (f) { f(); }); } catch (e) { }
    cleanup = [];
    renderTopNav(p);
    var tb = document.getElementById("timebar"); if (tb) tb.innerHTML = "";
    crumbs([["Not in the demo"]]); $updated.textContent = "";
    var isMatch = p[1] === "m" || p[1] === "a";
    var mlinks = matches.map(function (k) { return '<a href="#/' + h(EV) + "/m/" + h(k) + '">' + h(short(k).replace("Qualification ", "Q")) + "</a>"; }).join(", ");
    $app.innerHTML = '<div class="empty demo-gap" role="status"><b>' + h(what(p)) + " isn't in the demo</b>" +
      (isMatch
        ? "The demo keeps the match pages of " + mlinks + " (field view, no video). On your own server every tracked match has one, with the broadcast video and its overlay."
        : "The demo is a frozen, read-only copy of PNW District Sammamish 2026. Setup, video and account pages need a running server.") +
      '<div class="acts"><a class="cmp-btn primary" href="' + HOME + '">Open the Dashboard</a><a class="cmp-btn" href="' + SITE.replace("index.html", "download.html") + '">Run your own server</a></div></div>';
    if (scroll !== false) window.scrollTo(0, 0);
  }

  var inner = route;
  route = async function (scroll) {
    var p = parts();
    if (!p.length || (p.length === 1 && p[0] === EV) || p[0] === "login" || p[0] === "setup") { location.replace(HOME); return; }
    if (!allowed(p)) {
      if (!CS.session) { try { await CS.refreshSession(); } catch (e) { } }
      return notInDemo(p, scroll);
    }
    return inner(scroll);
  };

  function userBox() {
    var el = document.getElementById("userbox"); if (!el) return;
    el.innerHTML = '<span class="who demo-who" title="A demo account with the strategist role: it can edit the picklist (saved in this browser only).">Demo visitor · strategist</span><a href="' + SITE + '">Leave demo</a>';
  }
  var nav = renderTopNav;
  renderTopNav = function (p) { nav(p); userBox(); };

  // ?probe=1: write layout metrics into <html data-probe> for headless checks (no visual change)
  if (new URLSearchParams(location.search).has("probe")) setTimeout(function () {
    var d = document.documentElement;
    var over = [].slice.call(document.querySelectorAll("body *")).filter(function (e) { var r = e.getBoundingClientRect(); return r.width && r.right > d.clientWidth + 1 && getComputedStyle(e).position !== "fixed"; })
      .slice(0, 10).map(function (e) { return e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (typeof e.className === "string" && e.className ? "." + e.className.trim().split(/\s+/).join(".") : "") + ":" + Math.round(e.getBoundingClientRect().right); });
    d.setAttribute("data-probe", JSON.stringify({ sw: d.scrollWidth, cw: d.clientWidth, over: over }));
  }, 4000);

  var foot = document.querySelector(".foot");
  if (foot) foot.insertAdjacentHTML("afterbegin", '<p class="demo-foot"><b>Demo.</b> Frozen data from PNW District Sammamish 2026, opened at the Sunday 09:00 replay. Nothing you change leaves this browser. <a href="' + SITE + '">About Cyber-Scout</a></p>');
})();
