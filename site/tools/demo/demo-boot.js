/* Cyber-Scout demo: runs in <head> right after theme.js and demo-config.js, before any app script.
 * The demo is the real web app (copied from web/ by site/tools/build_demo.py) reading frozen JSON from ./api/.
 * There is no server, so this file answers the app's own API calls (app/v1/...) in the browser:
 *   - a signed-in "Demo visitor" with the strategist role (no sign-in page),
 *   - the competition list, an empty notes list, a picklist that saves in this browser only,
 *   - every other change is refused with a plain explanation (nothing is sent anywhere).
 * ?mode=light|dark|auto shows that appearance without remembering it (used for screenshots and links). */
(function () {
  "use strict";
  var C = window.DEMO || {};
  var q = new URLSearchParams(location.search);
  var m = q.get("mode");
  if (m === "light" || m === "dark" || m === "auto") document.documentElement.setAttribute("data-mode", m);
  document.documentElement.setAttribute("data-demo", "");
  if (q.has("shot")) document.documentElement.setAttribute("data-shot", "");   // product screenshots: no demo chrome

  var USER = { id: 1, username: "demo", display_name: "Demo visitor", role: "strategist" };
  var READ_ONLY = "This is a read-only demo, so changes aren't saved. On your own Cyber-Scout server they are shared with your team.";
  var realFetch = window.fetch.bind(window);
  var reply = function (status, body) {
    return Promise.resolve(new Response(status === 204 ? null : JSON.stringify(body), { status: status, headers: { "content-type": "application/json" } }));
  };
  var store = {
    get: function (k) { try { return JSON.parse(sessionStorage.getItem("demo:" + k)); } catch (e) { return null; } },
    set: function (k, v) { try { sessionStorage.setItem("demo:" + k, JSON.stringify(v)); } catch (e) { } }
  };

  function handle(method, path, body) {
    var p = path.split("?")[0];
    if (method === "GET") {
      if (p === "session") return reply(200, { user: USER, configured: true, setup_required: false, setup_code_required: false, lockdown: false, csrf: "demo" });
      if (p === "info") return reply(200, { app: "cyber-scout", api: 1, version: "demo", name: "Cyber-Scout demo", time: Math.floor(Date.now() / 1000) });
      if (p === "events") return reply(200, C.events || []);
      if (/^events\/[^/]+\/notes$/.test(p)) return reply(200, []);
      var pk = p.match(/^events\/([^/]+)\/picklists\/([^/]+)$/);
      if (pk) { var saved = store.get("pick:" + pk[1]); return saved ? reply(200, saved) : reply(404, { detail: "No picklist yet." }); }
      return reply(404, { detail: "Not in the demo." });
    }
    if (p === "auth/logout" || p === "auth/login") return reply(200, { ok: true, user: USER });
    var pw = p.match(/^events\/([^/]+)\/picklists\/([^/]+)$/);
    if (pw && method === "PUT") {
      var data = null; try { data = JSON.parse(body || "{}").data; } catch (e) { }
      var prev = store.get("pick:" + pw[1]);
      var rec = { version: ((prev && prev.version) || 0) + 1, data: data || {}, updated_by: USER.display_name, updated_at: Math.floor(Date.now() / 1000) };
      store.set("pick:" + pw[1], rec);
      return reply(200, rec);
    }
    return reply(403, { detail: READ_ONLY });
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || String(input);
    var u;
    try { u = new URL(url, location.href); } catch (e) { return realFetch(input, init); }
    if (u.origin === location.origin) {
      var i = u.pathname.indexOf("/app/v1/");
      if (i >= 0) {
        var method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
        return handle(method, u.pathname.slice(i + 8) + u.search, init && init.body);
      }
      if (u.pathname.indexOf("/client/v1/") >= 0) return reply(404, { detail: "not found" });
      // match files that aren't in the demo: answer 404 here instead of asking the static server
      var mk = u.pathname.match(/\/api\/[^/]+\/m\/([A-Za-z0-9_]+)(?:\.fuel)?\.json$/);
      if (mk && (C.matches || []).indexOf(mk[1]) < 0) return reply(404, { detail: "Not in the demo." });
    }
    return realFetch(input, init);
  };
})();
