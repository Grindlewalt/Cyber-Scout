"use strict";
// sync.js: the offline layer and Cyber-Scout Client mode (docs/SYNC.md §3). Loads right after setup.js.
//   CS.sync.ready   resolves to "client" when GET client/v1/status answers {client:true} (the page is served by the local
//                   Cyber-Scout Client), else "browser". setup.js waits for it before checking the session.
//   reads           api() reports every answer (CS.sync.seen / lost): the service worker (sw.js, browser mode only) marks
//                   answers it served from its cache, which drives the connection pill and the toolbar's "Saved copy · as of".
//                   CS.app GETs of scouting data (competitions, notes, picklists, identity fixes) fall back to a copy in
//                   IndexedDB when the server is slow (4 s) or unreachable.
//   writes          browser mode: notes, picklist and identity-fix writes carry an Idempotency-Key; when the server can't be
//                   reached they go to an IndexedDB outbox and are sent in order later (Background Sync where supported, else
//                   online / focus / visibility), back-off on failure, 409 picklist conflicts offer "keep mine / take theirs".
//                   Client mode: the Client queues them itself (202 {queued:true}).
//   offline copy    browser mode: "Save this event for offline" = app/v1/sync/manifest + sync/bundle (gzip tar, unpacked here)
//                   into the cache "cs-ev-<ev>" that sw.js reads.
//   UI              connection pill (sidebar footer, or the toolbar on phones / Top bar / Command / hidden sidebar), toasts
//                   (CS.toast), the Connection sheet (pill click), Settings sections, #/connect (Client first run).
// Storage: IndexedDB "cyberscout" {outbox, reads, kv}; localStorage cs:sync:user (last signed-in user, for offline use).
(function () {
  const ME = (document.currentScript && document.currentScript.src) || "";
  const VER = (() => { try { return new URL(ME, location.href).searchParams.get("v") || "dev"; } catch (e) { return "dev"; } })();
  const SW_OK = "serviceWorker" in navigator && window.isSecureContext;
  const CACHE_OK = "caches" in window && window.isSecureContext;
  const EVP = "cs-ev-", CHUNK = 8 << 20, EVK_RE = /^\d{4}[a-z0-9]{1,16}$/;
  const S = CS.sync = { mode: null, status: null, ver: VER };
  const NET = { state: "online", lastFresh: 0, lastSync: 0, toastAt: 0 };
  let OB = [], SAVED = {}, JOB = null, FLUSHING = false, asof = null;

  // ================================================================ small helpers
  const enc = encodeURIComponent;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const lsPut = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { } };
  const uuid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16)); b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
    const h = [...b].map(x => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  };
  const fmtB = b => b == null || isNaN(b) ? "–" : b < 1024 ? `${b} B` : b < 1 << 20 ? `${Math.round(b / 1024)} KB`
    : b < 1 << 30 ? `${(b / 1048576).toFixed(b < 10 << 20 ? 1 : 0)} MB` : `${(b / 1073741824).toFixed(1)} GB`;
  const agoTxt = ms => {
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    return s < 45 ? "just now" : s < 5400 ? `${Math.max(1, Math.round(s / 60))} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
  };
  const clock = ms => { const d = new Date(ms), t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return new Date().toDateString() === d.toDateString() ? t : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + t; };
  const who = u => (u && typeof u === "object" ? u.display_name || u.username : u) || "someone";
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const curEv = () => {
    const h = location.hash.replace(/^#\/?/, "").split("/")[0];
    if (EVK_RE.test(h) && !CS.globalRoutes[h]) return h;
    const l = lsGet("cs:lastev"); return EVK_RE.test(l || "") ? l : null;
  };
  const evName = ev => { const c = CS.compInfo && CS.compInfo(ev); return (c && c.name) || (SAVED[ev] && SAVED[ev].name) || ev; };
  const errText = (d, status) => { const x = d && (d.detail || d.error || d.message); return typeof x === "string" ? x : x && x.message ? x.message : status ? `HTTP ${status}` : "no answer"; };
  async function fetchT(url, opt, ms) {
    const ac = new AbortController(), t = setTimeout(() => ac.abort(), ms || 15000);
    if (opt && opt.signal) opt.signal.addEventListener("abort", () => ac.abort());
    try { return await fetch(url, Object.assign({}, opt, { signal: ac.signal })); } finally { clearTimeout(t); }
  }
  /** "scout.grantrindal.dev" -> https://scout.grantrindal.dev, "10.0.0.58:7560" -> http://10.0.0.58:7560; null if not an address */
  function normServer(v) {
    let s = String(v || "").trim();
    if (!s) return null;
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) {
      let host = ""; try { host = new URL("http://" + s).hostname.toLowerCase(); } catch (e) { return null; }
      // a bare address on the team network (IP, IPv6, localhost, no dot, .local / .lan / Tailscale) is plain http; names are https
      const local = /^(\d{1,3}\.){3}\d{1,3}$/.test(host) || host.startsWith("[") || host === "localhost" || !host.includes(".")
        || /\.(local|lan|home|internal|home\.arpa|ts\.net)$/.test(host);
      s = (local ? "http://" : "https://") + s;
    }
    let u; try { u = new URL(s); } catch (e) { return null; }
    if (!/^https?:$/.test(u.protocol) || !u.hostname || u.username || u.password) return null;
    const path = u.pathname.replace(/\/(index\.html|app\/v1(\/.*)?)$/, "").replace(/\/+$/, "");
    return `${u.protocol}//${u.host}${path}`;
  }
  S.normServer = normServer;
  S.host = url => { try { return new URL(url).host; } catch (e) { return url || ""; } };

  // ================================================================ IndexedDB (shared with sw.js: same name, version, stores)
  let dbP = null;
  function db() {
    if (!dbP) dbP = new Promise((res, rej) => {
      if (!window.indexedDB) return rej(new Error("no IndexedDB"));
      const q = indexedDB.open("cyberscout", 1);
      q.onupgradeneeded = () => {
        const d = q.result;
        if (!d.objectStoreNames.contains("outbox")) d.createObjectStore("outbox", { keyPath: "seq", autoIncrement: true });
        if (!d.objectStoreNames.contains("reads")) d.createObjectStore("reads", { keyPath: "k" });
        if (!d.objectStoreNames.contains("kv")) d.createObjectStore("kv", { keyPath: "k" });
      };
      q.onsuccess = () => { const d = q.result; d.onversionchange = () => { d.close(); dbP = null; }; res(d); };
      q.onerror = () => rej(q.error);
    }).catch(e => { dbP = null; throw e; });
    return dbP;
  }
  async function idb(store, mode, fn) {
    const d = await db();
    return new Promise((res, rej) => {
      const t = d.transaction(store, mode), r = fn(t.objectStore(store)); let out;
      if (r) r.onsuccess = () => { out = r.result; };
      t.oncomplete = () => res(out); t.onerror = () => rej(t.error); t.onabort = () => rej(t.error);
    });
  }
  const kvGet = k => idb("kv", "readonly", s => s.get(k)).then(r => r ? r.v : undefined, () => undefined);
  const kvSet = (k, v) => idb("kv", "readwrite", s => v === undefined ? s.delete(k) : s.put({ k, v })).catch(() => { });

  // ================================================================ mode
  async function clientStatus(ms) {
    try {
      const r = await fetchT("client/v1/status", { cache: "no-store", credentials: "same-origin" }, ms || 6000);
      if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) return null;
      const d = await r.json();
      return d && d.client === true ? d : null;
    } catch (e) { return null; }
  }
  S.ready = (async () => {
    await null;                       // let the rest of this file initialise first
    let st = null;
    if (SW_OK && navigator.serviceWorker.controller) {
      // this page was opened in browser mode before (only browser mode registers the worker): don't wait for the check
      clientStatus(4000).then(x => { if (x) navigator.serviceWorker.getRegistrations().then(rs => Promise.all(rs.map(r => r.unregister()))).then(() => location.reload()); });
    } else st = await clientStatus(2500);
    S.mode = st ? "client" : "browser";
    if (st) { S.status = st; S.statusAt = Date.now(); }
    document.documentElement.setAttribute("data-sync", S.mode);
    try { Promise.resolve(S.mode === "client" ? startClient() : startBrowser()).catch(e => console.error(e)); } catch (e) { console.error(e); }
    return S.mode;
  })();
  S.needsServer = () => S.mode === "client" && !!S.status && !S.status.server;
  S.isOffline = () => S.mode === "client" ? !S.status || !S.status.online : NET.state !== "online";

  // ---- the signed-in user, remembered so the app still opens (read-only data + outbox) when the server can't be reached
  S.rememberUser = u => { if (u && u.role) lsPut("cs:sync:user", JSON.stringify({ id: u.id, username: u.username, display_name: u.display_name, role: u.role })); };
  S.forget = () => lsPut("cs:sync:user", null);
  S.offlineSession = () => {
    let u = null; try { u = JSON.parse(lsGet("cs:sync:user") || "null"); } catch (e) { }
    if (S.mode === "client") {
      const su = S.status && S.status.user; if (!su) return null;
      u = Object.assign({}, u && u.display_name === su.display_name ? u : {}, su);
    }
    if (!u || !u.role) return null;
    return { user: u, configured: true, setup_required: false, offline: true, csrf: "" };
  };
  S.signedOut = () => {      // browser mode, Sign out: forget the user, drop what was cached while browsing (saved events stay)
    S.forget();
    idb("reads", "readwrite", s => s.clear()).catch(() => { });
    if (SW_OK && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ type: "clear-data" });
  };

  // ================================================================ connection state (browser mode)
  function setNet(st, quiet) {
    if (S.mode !== "browser" || NET.state === st) return;
    const was = NET.state; NET.state = st;
    if (st === "online") {
      probeN = 0; clearTimeout(probeT);
      const n = pendingMine().length;
      if (!quiet) toast(n ? `Back online. Sending ${plural(n, "change")}…` : "Back online.", { kind: "good" });
      if (CS.session && CS.session.offline) { if (window.CS && CS.reauth) CS.reauth(); }
      flush(true); setTimeout(refreshSaved, 3000);
    } else {
      if (!quiet && was === "online") toast(st === "offline" ? "You're offline. Showing saved data; changes you make wait on this device."
        : "Can't reach the Cyber-Scout server. Showing saved data; changes you make wait on this device.", { kind: "warn" });
      probeSoon();
    }
    paint();
  }
  function markOk(fresh) {
    if (fresh) { const was = NET.lastFresh; NET.lastFresh = Date.now(); if (NET.state === "online" && Date.now() - was > 20000) paintSoon(); }
    setNet("online");
  }
  function markFail() { if (S.mode === "browser") setNet(navigator.onLine === false ? "offline" : "unreachable"); else if (S.mode === "client") refresh(); }
  let probeT = 0, probeN = 0;
  function probeSoon(ms) {
    clearTimeout(probeT);
    probeT = setTimeout(probe, ms != null ? ms : Math.min(60000, 4000 * 2 ** Math.min(probeN, 4)) * (0.8 + Math.random() * 0.4));
  }
  async function probe() {
    if (S.mode !== "browser" || NET.state === "online") return;
    if (document.hidden || navigator.onLine === false) { if (navigator.onLine === false) setNet("offline", true); return probeSoon(); }
    probeN++;
    try {
      const r = await fetchT("app/v1/info", { cache: "no-store", credentials: "same-origin" }, 8000);
      if (r.status < 500) { setNet("online"); return; }
    } catch (e) { }
    setNet(navigator.onLine === false ? "offline" : "unreachable", true);
    probeSoon();
  }
  S.seen = (p, r) => {
    if (S.mode !== "browser") return;
    const src = r.headers.get("x-cs-src"), at = +(r.headers.get("x-cs-at") || 0);
    if (src === "offline" || src === "none") { markFail(); if (at) noteAsOf(at); }
    else if (src === "stale") { if (at) noteAsOf(at, true); }
    else if (r.status >= 502) markFail();
    else markOk(true);
  };
  S.lost = () => markFail();

  // ---- "Saved copy · as of …" in the toolbar subtitle, per page
  function noteAsOf(at, slowOnly) {
    if (slowOnly && Date.now() - at < 120000) return;
    if (!asof || at < asof) { asof = at; paintAsOf(); }
  }
  function paintAsOf() {
    let el = document.getElementById("asof");
    if (!el) { const sub = document.getElementById("tbsub"); if (!sub) return; el = document.createElement("span"); el.id = "asof"; sub.appendChild(el); }
    el.textContent = asof ? `Saved copy · as of ${clock(asof)}` : "";
    el.title = asof ? `This page shows data saved on this device ${agoTxt(asof)}: the server couldn't be reached (or was slow). It refreshes by itself when the connection is back.` : "";
  }
  // every page starts without one (setup.js wraps route() first; this wraps that)
  const prevRoute = route;
  route = function (scroll) { asof = null; paintAsOf(); return prevRoute(scroll); };

  // ================================================================ CS.app: cached reads, queued writes
  const rawApp = CS.app;
  const READ_OK = /^(events|sync\/events|events\/[^/?]+\/(notes\?[^#]*|picklists\/[\w-]+|overrides))$/;
  const QUEUE_OK = [/^events\/[^/]+\/notes$/, /^notes\/\d+(\/restore)?$/, /^events\/[^/]+\/picklists\/[\w-]+$/,
    /^events\/[^/]+\/overrides\/[^/]+$/, /^events\/[^/]+\/override-requests\/[^/]+\/(approve|reject)$/];
  const netErr = e => !e.status || e.status === 502 || e.status === 503 || e.status === 504 || !!(e.data && e.data.offline);
  const uidOf = u => u ? String(u.id ?? u.username ?? u.display_name ?? "") : "";
  CS.app = async function (method, path, body, opts) {
    if (method === "GET") return read(path, () => rawApp(method, path, body, opts));
    const queueable = S.mode === "browser" && CS.platform && CS.user && QUEUE_OK.some(r => r.test(path));
    if (!queueable) {
      try {
        const d = await rawApp(method, path, body, opts);
        if (S.mode === "client" && d && d.queued) { refresh(); toast("Saved on this computer. It's sent when the server is back.", { kind: "warn" }); }
        return d;
      } catch (e) { if (netErr(e) && !/^auth\//.test(path)) markFail(); throw e; }
    }
    const key = uuid();
    if (navigator.onLine === false || NET.state !== "online" || pendingMine().length) {      // keep the order: behind what's waiting
      const it = await enqueue(method, path, body, key); flush(); return { queued: true, id: it.seq };
    }
    try {
      const d = await rawApp(method, path, body, Object.assign({}, opts, { timeout: 20000, headers: Object.assign({ "Idempotency-Key": key }, opts && opts.headers) }));
      markOk(); return d;
    } catch (e) {
      if (!netErr(e)) throw e;                              // the server answered: a real error for the page to show
      markFail();
      const it = await enqueue(method, path, body, key);     // same key: if it did arrive, the server replays instead of applying twice
      return { queued: true, id: it.seq };
    }
  };
  async function read(path, go) {
    if (!READ_OK.test(path)) {
      try { return await go(); } catch (e) { if (netErr(e)) markFail(); throw e; }
    }
    const uid = uidOf(CS.user);
    const hit = await idb("reads", "readonly", s => s.get(path)).catch(() => null);
    const ok = hit && hit.uid === uid ? hit : null;
    const net = go().then(d => { if (S.mode === "browser") markOk(); idb("reads", "readwrite", s => s.put({ k: path, uid, d, at: Date.now() })).catch(() => { }); return d; });
    if (!ok) return net.catch(e => { if (netErr(e)) markFail(); throw e; });
    const page = /^events\/[^/?]+\//.test(path);     // the competition list (sidebar) doesn't make a page "as of"
    const asOf = slow => { if (page) noteAsOf(ok.at, slow); };
    if (S.isOffline() && (S.mode === "client" || navigator.onLine === false)) { net.catch(() => { }); asOf(); return ok.d; }
    const r = await Promise.race([net.then(d => ({ d }), e => ({ e })), sleep(4000).then(() => null)]);
    if (!r) { net.catch(() => { }); asOf(true); return ok.d; }
    if (r.e) { if (!netErr(r.e)) throw r.e; markFail(); asOf(); return ok.d; }
    return r.d;
  }

  // ================================================================ outbox (browser mode)
  const pendingMine = () => OB.filter(x => (x.state === "pending" || x.state === "sending") && x.uid === uidOf(CS.user));
  const attention = () => OB.filter(x => x.state === "failed" || x.state === "conflict");
  S.pending = f => S.mode === "browser" ? OB.filter(x => x.state !== "failed" && x.state !== "conflict" && x.uid === uidOf(CS.user) && (!f || f(x)))
    : COB.filter(x => !f || f(x));
  // client mode: the Client's queued changes (GET client/v1/outbox), in the same shape, so pages can show them
  let COB = [], cobN = -1;
  async function clientOutbox(st) {
    const n = st ? st.outbox || 0 : 0;
    if (n === cobN && !(n && Date.now() - (clientOutbox.at || 0) > 30000)) return;
    cobN = n; clientOutbox.at = Date.now();
    let l = [];
    if (n) { try { const r = await ctl("GET", "outbox"); l = Array.isArray(r) ? r : (r && r.items) || []; } catch (e) { return; } }
    COB = l.filter(x => x.state === "queued" || x.state === "sending").map(x => ({ id: x.id, method: x.method, path: String(x.path || "").replace(/^\/?app\/v1\//, "").split("?")[0],
      body: x.body, at: (x.created || Date.now() / 1000) * 1000, state: "pending" }));
    document.dispatchEvent(new CustomEvent("cs:outbox"));
  }
  const loadOB = () => idb("outbox", "readonly", s => s.getAll()).then(l => (l || []).sort((a, b) => a.seq - b.seq), () => OB);
  const saveIt = it => idb("outbox", "readwrite", s => s.put(Object.assign({}, it))).catch(() => { });
  const delIt = it => { OB = OB.filter(x => x.seq !== it.seq); return idb("outbox", "readwrite", s => s.delete(it.seq)).catch(() => { }); };
  function obChanged() { paint(); document.dispatchEvent(new CustomEvent("cs:outbox")); }
  async function enqueue(method, path, body, key) {
    const uid = uidOf(CS.user);
    // a picklist save replaces an earlier one that was never tried: only the latest list matters
    const prev = method === "PUT" && /\/picklists\//.test(path) && OB.find(x => x.state === "pending" && !x.tries && x.method === "PUT" && x.path === path && x.uid === uid);
    if (prev) { prev.body = body; prev.at = Date.now(); await saveIt(prev); obChanged(); return prev; }
    const it = { key: key || uuid(), method, path, body, at: Date.now(), uid, csrf: CS.csrf ? CS.csrf() : "", state: "pending", tries: 0, next: 0 };
    try { it.seq = await idb("outbox", "readwrite", s => s.add(it)); }
    catch (e) { it.seq = -Date.now(); }                     // no IndexedDB (private window): kept for this page only
    OB.push(it); obChanged();
    if (SW_OK) navigator.serviceWorker.ready.then(r => r.sync && r.sync.register("cs-outbox")).catch(() => { });
    return it;
  }
  let flushT = 0;
  function scheduleFlush() {
    clearTimeout(flushT);
    const due = pendingMine().map(x => x.next || 0); if (!due.length) return;
    flushT = setTimeout(flush, Math.max(1500, Math.min(...due) - Date.now()));
  }
  async function flush(manual) {
    if (S.mode !== "browser" || FLUSHING || !CS.user || (CS.session && CS.session.offline && NET.state !== "online")) return;
    if (!pendingMine().some(x => manual || (x.next || 0) <= Date.now())) return scheduleFlush();
    FLUSHING = true; paint();
    let sent = 0, conflicts = 0, failed = 0;
    const run = async () => {
      const fresh = await loadOB();                          // the service worker may have sent some already
      OB = fresh.map(x => Object.assign(OB.find(y => y.seq === x.seq) || {}, x));
      for (const it of pendingMine()) {
        if (!manual && (it.next || 0) > Date.now()) continue;
        it.state = "sending"; paint();
        let r, d = null;
        try {
          r = await fetchT("app/v1/" + it.path, { method: it.method, credentials: "same-origin", cache: "no-store",
            headers: Object.assign({ "Idempotency-Key": it.key, "X-CSRF-Token": (CS.csrf && CS.csrf()) || it.csrf || "" }, it.body !== undefined ? { "Content-Type": "application/json" } : {}),
            body: it.body !== undefined ? JSON.stringify(it.body) : undefined }, 25000);
        } catch (e) { backoff(it, "Couldn't reach the server"); markFail(); break; }
        if (r.status !== 204) { try { d = await r.json(); } catch (e) { } }
        if (r.ok) {
          await delIt(it); sent++; markOk();
          // later saves of the same picklist were based on the version before this one
          if (/\/picklists\//.test(it.path) && d && d.version != null)
            for (const x of OB) if (x.path === it.path && x.seq > it.seq && x.body && x.state === "pending") { x.body = Object.assign({}, x.body, { base_version: d.version }); await saveIt(x); }
          document.dispatchEvent(new CustomEvent("cs:sent", { detail: { path: it.path, method: it.method, data: d } }));
          continue;
        }
        if (r.status === 401) { it.state = "pending"; await saveIt(it); if (CS.reauth) CS.reauth(); break; }
        if (r.status >= 500 || r.status === 408 || r.status === 429) { backoff(it, errText(d, r.status)); if (r.status >= 502) markFail(); break; }
        const cur = r.status === 409 && d && d.detail && d.detail.current;
        if (cur && /\/picklists\//.test(it.path)) { it.state = "conflict"; it.current = cur; conflicts++; }
        else { it.state = "failed"; failed++; it.error = CS.writeErr ? CS.writeErr({ status: r.status, message: errText(d, r.status) }) : errText(d, r.status); }
        it.status = r.status; await saveIt(it);
      }
    };
    // no Web Lock with the service worker's Background Sync: both send with the same Idempotency-Key, so a change
    // sent twice is answered from the server's replay store (or 429 while the first is still running), never applied twice
    try { await run(); }
    catch (e) { console.warn("outbox:", e); }
    finally {
      FLUSHING = false;
      for (const x of OB) if (x.state === "sending") x.state = "pending";
    }
    if (sent) toast(`${plural(sent, "change")} sent.`, { kind: "good" });
    if (conflicts) toast("Someone else changed the picklist while you were offline. Pick which version to keep.", { kind: "warn", action: "Review", fn: openSheetFromToast });
    if (failed) toast(`${plural(failed, "change")} couldn't be sent. What you wrote is kept.`, { kind: "bad", action: "Review", fn: openSheetFromToast });
    obChanged(); scheduleFlush();
  }
  function backoff(it, why) {
    it.state = "pending"; it.tries = (it.tries || 0) + 1; it.error = why;
    it.next = Date.now() + Math.min(300000, 2000 * 2 ** it.tries) * (0.75 + Math.random() * 0.5);
    saveIt(it);
  }
  async function obAct(seq, act) {
    const it = OB.find(x => x.seq === seq); if (!it) return;
    if (act === "discard") {
      if (it.body && it.body.text && !confirm("Discard this change? What you wrote is deleted from this device.")) return;
      await delIt(it); obChanged(); return;
    }
    if (act === "theirs" && it.current) {
      const m = /^events\/([^/]+)\/picklists\//.exec(it.path), ev = m && decodeURIComponent(m[1]);
      if (ev && CS.picklist) CS.picklist.adopt(ev, it.current);
      await delIt(it); obChanged();
      toast(`Using the picklist ${who(it.current.updated_by)} saved.`);
      if (ev && new RegExp(`^#/${ev}/picklist`).test(location.hash)) route(false);
      return;
    }
    // keep mine (on top of their version) / retry: a new key, so the server doesn't replay its earlier answer
    if (act === "mine" && it.current) it.body = Object.assign({}, it.body, { base_version: it.current.version });
    Object.assign(it, { key: uuid(), state: "pending", tries: 0, next: 0, error: null, current: null, status: null });
    await saveIt(it); obChanged(); flush(true);
  }
  /** what a queued request is, in words */
  function obLabel(it) {
    const p = String(it.path || "").replace(/^\/?app\/v1\//, "").split("?")[0], b = it.body || {}, m = p.match(/^events\/([^/]+)\/(.+)$/), sub = m ? m[2] : p;
    const team = t => t ? `team ${num(t)}` : "", match = k => k ? shortKey(k) : "";
    if (/^notes$/.test(sub) && it.method === "POST") return { t: `Note${b.team || b.match ? " on " + [team(b.team), match(b.match)].filter(Boolean).join(", ") : ""}`, x: b.text };
    if (/^notes\/\d+$/.test(p)) return it.method === "PATCH" ? { t: "Note edit", x: b.text } : { t: "Delete a note" };
    if (/^notes\/\d+\/restore$/.test(p)) return { t: "Restore a note" };
    if (/^picklists\//.test(sub)) return { t: "Picklist", x: b.data ? `${plural((b.data.stars || []).length, "star")}, ${(b.data.dnp || []).length} do-not-pick` : "" };
    if (/^overrides\//.test(sub)) return { t: `${it.method === "DELETE" ? "Clear identity fixes" : "Identity fix"} · ${match(decodeURIComponent(sub.split("/")[1]))}`,
      x: it.method === "DELETE" ? "" : ["red", "blue"].map(a => b[a] && Object.keys(b[a]).length ? `${a}: ` + Object.entries(b[a]).map(([i, t]) => `robot #${i} → ${num(t)}`).join(", ") : "").filter(Boolean).join("; ") };
    if (/^override-requests\//.test(sub)) return { t: /approve$/.test(sub) ? "Approve an identity fix" : "Reject an identity fix" };
    return { t: `${it.method} ${p}` };
  }

  // ================================================================ "Save this event for offline" (browser mode)
  const absUrl = p => new URL(p, document.baseURI).href;
  const okPath = p => typeof p === "string" && /^api\/[\w\-./]+$/.test(p) && !p.includes("..");
  const ctype = p => /\.json$/.test(p) ? "application/json" : /\.jpe?g$/.test(p) ? "image/jpeg" : /\.png$/.test(p) ? "image/png" : /\.mp4$/.test(p) ? "video/mp4" : "application/octet-stream";
  async function sha256hex(buf) { return [...new Uint8Array(await crypto.subtle.digest("SHA-256", buf))].map(x => x.toString(16).padStart(2, "0")).join(""); }
  function httpErr(r, d) { const e = new Error(errText(d, r.status)); e.status = r.status; return e; }
  async function getJSON(url, signal) {
    const r = await fetchT(url, { credentials: "same-origin", cache: "no-store", signal }, 30000);
    let d = null; try { d = await r.json(); } catch (e) { }
    if (!r.ok) throw httpErr(r, d);
    return d;
  }
  /** gzip (magic 1f 8b) -> DecompressionStream; a plain tar passes through */
  async function unpackStream(body) {
    const rd = body.getReader(), first = await rd.read(), head = first.value || new Uint8Array(0);
    const s = new ReadableStream({
      start(c) { if (head.length) c.enqueue(head); if (first.done) c.close(); },
      async pull(c) { const { done, value } = await rd.read(); if (done) c.close(); else c.enqueue(value); },
      cancel(x) { return rd.cancel(x); },
    });
    if (head[0] === 0x1f && head[1] === 0x8b) {
      if (!window.DecompressionStream) throw new Error("this browser can't unpack the download (no DecompressionStream): update it");
      return s.pipeThrough(new DecompressionStream("gzip"));
    }
    return s;
  }
  /** a small tar reader: ustar / GNU long names (L) / pax (x) headers, regular files only */
  async function* untar(stream) {
    const rd = stream.getReader(), q = []; let len = 0, eof = false;
    const fill = async n => { while (len < n && !eof) { const { done, value } = await rd.read(); if (done) eof = true; else if (value.length) { q.push(value); len += value.length; } } return len >= n; };
    const take = n => {
      const out = new Uint8Array(n); let o = 0;
      while (o < n) { const c = q[0], k = Math.min(c.length, n - o); out.set(c.subarray(0, k), o); o += k; if (k === c.length) q.shift(); else q[0] = c.subarray(k); }
      len -= n; return out;
    };
    const td = new TextDecoder();
    const str = (b, o, n) => { const s = b.subarray(o, o + n), z = s.indexOf(0); return td.decode(z < 0 ? s : s.subarray(0, z)); };
    const oct = (b, o, n) => {
      if (b[o] & 0x80) { let v = 0; for (let i = o + 1; i < o + n; i++) v = v * 256 + b[i]; return v; }      // GNU base-256
      return parseInt(str(b, o, n).trim() || "0", 8) || 0;
    };
    let longName = null, pax = null;
    while (true) {
      if (!await fill(512)) return;
      const h = take(512);
      if (h.every(x => x === 0)) return;
      const size = oct(h, 124, 12), type = h[156], pad = Math.ceil(size / 512) * 512;
      if (!await fill(pad)) throw new Error("the download was cut short");
      const body = take(pad).slice(0, size);
      if (type === 76) { longName = str(body, 0, size); continue; }                                 // 'L'
      if (type === 120) { pax = {}; for (const ln of td.decode(body).split("\n")) { const m = ln.match(/^\d+ ([^=]+)=(.*)$/); if (m) pax[m[1]] = m[2]; } continue; }   // 'x'
      if (type === 103) continue;                                                                     // 'g'
      const magic = str(h, 257, 5), prefix = magic === "ustar" ? str(h, 345, 155) : "";
      const name = (pax && pax.path) || longName || (prefix ? prefix + "/" : "") + str(h, 0, 100);
      longName = null; pax = null;
      if (type === 48 || type === 0 || type === 55) yield { name: name.replace(/^\.?\//, ""), body };     // '0', NUL, '7'
    }
  }
  async function fetchBundle(ev, files, cache, have, signal) {
    const want = new Map(files.map(f => [f.path, f]));
    const r = await fetchT("app/v1/sync/bundle", { method: "POST", credentials: "same-origin", cache: "no-store", signal,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CS.csrf ? CS.csrf() : "" }, body: JSON.stringify({ ev, paths: files.map(f => f.path) }) }, 180000);
    if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) { } throw httpErr(r, d); }
    for await (const ent of untar(await unpackStream(r.body))) {
      const f = want.get(ent.name); if (!f) continue;
      if (f.sha256 && await sha256hex(ent.body) !== String(f.sha256).toLowerCase()) throw new Error(`${ent.name} arrived damaged (checksum)`);
      await cache.put(absUrl(ent.name), new Response(ent.body, { headers: { "content-type": ctype(ent.name), "x-cs-at": String(Date.now()) } }));
      have[ent.name] = { sha: f.sha256 || null, size: ent.body.length };
      if (JOB) { JOB.done += f.size || ent.body.length; paintSoon(); }
      want.delete(ent.name);
    }
    if (want.size) throw new Error(`the download was missing ${plural(want.size, "file")}`);
  }
  function chunks(files) {
    const out = []; let cur = [], sz = 0;
    for (const f of files) {
      if (cur.length && (sz + (f.size || 0) > CHUNK || cur.length >= 400)) { out.push(cur); cur = []; sz = 0; }
      cur.push(f); sz += f.size || 0;
    }
    if (cur.length) out.push(cur);
    return out;
  }
  async function saveEvent(ev, quiet) {
    if (!CACHE_OK) throw new Error("offline copies need this site's secure (https) address");
    if (JOB) throw new Error("already saving " + evName(JOB.ev));
    const ac = new AbortController();
    JOB = { ev, done: 0, total: 0, quiet: !!quiet, ac }; paint();
    try {
      const meta = SAVED[ev], files = (await kvGet("files:" + ev)) || {};      // also what an interrupted save already got
      const since = meta && meta.cursor && await caches.has(EVP + ev) ? meta.cursor : null;
      const man = await getJSON(`app/v1/sync/manifest?ev=${enc(ev)}${since ? "&since=" + enc(since) : ""}`, ac.signal);
      const cache = await caches.open(EVP + ev), list = (man.files || []).filter(f => okPath(f.path));
      const have = man.full || !since ? {} : files;    // (a full listing starts over, keeping files whose sha256 still matches)
      if (man.full || !since) {           // a full listing: anything not in it is gone
        const keep = new Set(list.map(f => absUrl(f.path)));
        for (const k of await cache.keys()) if (!keep.has(k.url)) await cache.delete(k);
      }
      for (const p of man.deleted || []) { if (okPath(p)) await cache.delete(absUrl(p)); delete have[p]; }
      const need = [];
      for (const f of list) {
        const o = files[f.path];
        if (o && f.sha256 && o.sha === f.sha256 && await cache.match(absUrl(f.path))) { have[f.path] = o; continue; }
        need.push(f);
      }
      JOB.total = need.reduce((a, f) => a + (f.size || 0), 0); paint();
      for (const part of chunks(need)) {
        for (let attempt = 0; ; attempt++) {
          const left = part.filter(f => !have[f.path] || have[f.path].sha !== (f.sha256 || null));
          if (!left.length) break;
          try { await fetchBundle(ev, left, cache, have, ac.signal); break; }
          catch (e) {
            if (ac.signal.aborted) throw new Error("cancelled");
            if (attempt >= 3 || (e.status && e.status < 500 && e.status !== 408 && e.status !== 429)) throw e;
            await sleep([2000, 5000, 12000][attempt] * (0.8 + Math.random() * 0.4));
          }
        }
        await kvSet("files:" + ev, have);
      }
      const all = Object.values(have);
      SAVED[ev] = { ev, name: evName(ev), cursor: man.cursor || null, bytes: all.reduce((a, f) => a + (f.size || 0), 0), count: all.length, at: Date.now() };
      await kvSet("files:" + ev, have); await kvSet("saved", SAVED);
      NET.lastSync = Date.now(); markOk(true);
      if (!quiet) {
        toast(`${evName(ev)} is saved on this device. Its pages open without a connection.`, { kind: "good" });
        if (navigator.storage && navigator.storage.persist) navigator.storage.persisted().then(p => p || navigator.storage.persist()).then(() => storageLine(true)).catch(() => { });
      }
      return need.length;
    } finally { JOB = null; paint(); storageLine(true); }
  }
  async function removeSaved(ev) {
    if (JOB && JOB.ev === ev) JOB.ac.abort();
    if (CACHE_OK) await caches.delete(EVP + ev);
    delete SAVED[ev]; await kvSet("files:" + ev, undefined); await kvSet("saved", SAVED);
    paint(); storageLine(true); toast(`Removed the offline copy of ${evName(ev)}.`);
  }
  let refreshing = false;
  async function refreshSaved() {          // keep saved events current: only what changed since the last cursor
    if (S.mode !== "browser" || refreshing || JOB || NET.state !== "online" || !CS.user || document.hidden) return;
    refreshing = true;
    try { for (const r of Object.values(SAVED)) if (Date.now() - (r.at || 0) > 5 * 60000) { try { await saveEvent(r.ev, true); } catch (e) { console.warn("offline copy refresh:", r.ev, e.message); } } }
    finally { refreshing = false; }
  }
  const MAN = {};       // manifest size estimate per event (sheet: "about 120 MB")
  function estimate(ev) {
    const m = MAN[ev]; if (m && (m.p || Date.now() - m.at < 300000)) return m;
    const e = MAN[ev] = { at: Date.now(), p: getJSON(`app/v1/sync/manifest?ev=${enc(ev)}`).then(d => {
      const l = (d.files || []).filter(f => okPath(f.path)); Object.assign(e, { bytes: l.reduce((a, f) => a + (f.size || 0), 0), count: l.length, p: null }); paint();
    }, err => { Object.assign(e, { err: err.status === 404 ? "This server can't make offline copies yet." : null, p: null }); paint(); }) };
    return e;
  }

  // ================================================================ client mode
  async function ctl(method, path, body, ms) {
    let r;
    try {
      r = await fetchT("client/v1/" + path, { method, credentials: "same-origin", cache: "no-store",
        headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined }, ms || 15000);
    } catch (e) { throw new Error(e.name === "AbortError" ? "the Cyber-Scout Client didn't answer in time" : "the Cyber-Scout Client isn't answering"); }
    let d = null; try { d = await r.json(); } catch (e) { }
    if (!r.ok) { const e = new Error(errText(d, r.status)); e.status = r.status; e.data = d; throw e; }
    return d;
  }
  S.ctl = ctl;
  S.clientLogin = async (username, password) => { await ctl("POST", "login", { username, password }, 30000); await refresh(); };
  S.clientLogout = async () => { await ctl("POST", "logout", {}); S.forget(); await refresh(); };
  let pollT = 0, lastErrs = "";
  async function refresh() {
    if (S.mode !== "client") return S.status;
    const st = await clientStatus(6000), prev = S.status;
    if (st || !prev || Date.now() - (S.statusAt || 0) > 20000) { S.status = st; if (st) S.statusAt = Date.now(); }
    clientNews(prev, S.status);
    paint(); pollSoon(); clientOutbox(S.status);
    return S.status;
  }
  S.refresh = refresh;
  function pollSoon() {
    clearTimeout(pollT);
    const st = S.status, watching = (sheet && sheet.open) || /^#\/(settings|connect)/.test(location.hash);
    pollT = setTimeout(refresh, document.hidden ? 60000 : !st || (st.syncing && watching) ? 2500 : st.syncing || watching ? 5000 : 15000);
  }
  function clientNews(a, b) {
    if (!a && !b) return;
    if (a && !b) { toast("The Cyber-Scout Client stopped answering. Is it still running?", { kind: "bad" }); return; }
    if (!a || !b) return;
    const was = a.server && a.signed_in && a.server === b.server;      // not the first connect / sign-in
    if (was && a.online && !b.online) toast("Offline. Working from this computer's copy; changes wait here until the server is back.", { kind: "warn" });
    else if (was && !a.online && b.online) toast("Back online.", { kind: "good" });
    if (a.outbox > 0 && b.outbox < a.outbox && b.online) toast(`${plural(a.outbox - b.outbox, "change")} sent.`, { kind: "good" });
    if (a.signed_in && !b.signed_in) { toast("Signed out of the server.", { kind: "warn" }); if (CS.user && CS.reauth) CS.reauth(); }
    if (!a.server !== !b.server || (a.signed_in !== b.signed_in && b.signed_in && !CS.user)) { if (CS.reauth) CS.reauth(); }
    const key = e => `${e.kind}:${e.id || ""}:${e.time || ""}:${e.message || ""}`;
    const errs = JSON.stringify((b.errors || []).map(key));
    if (errs !== lastErrs) {
      const old = new Set((a.errors || []).map(key));
      const fresh = (b.errors || []).filter(e => e && (e.kind === "conflict" || e.kind === "failed") && !old.has(key(e)));
      if (fresh.length) {
        const c = fresh.some(e => e.kind === "conflict");
        toast(c ? "A change conflicts with a newer one on the server. Pick which to keep." : `${plural(fresh.length, "change")} couldn't be sent. What you wrote is kept.`,
          { kind: c ? "warn" : "bad", action: "Review", fn: () => { location.hash = "#/settings"; } });
      }
      lastErrs = errs;
    }
  }
  function startClient() {
    lastErrs = JSON.stringify(((S.status && S.status.errors) || []).map(e => `${e.kind}:${e.id || ""}:${e.time || ""}:${e.message || ""}`));
    mountChrome();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    pollSoon(); setInterval(paint, 30000);
  }

  // ================================================================ browser mode start
  async function startBrowser() {
    mountChrome();
    if (navigator.onLine === false) setNet("offline", false);
    addEventListener("online", () => { probeN = 0; probe(); flush(); });
    addEventListener("offline", () => setNet("offline"));
    addEventListener("focus", () => { if (NET.state !== "online") probeSoon(0); else flush(); });
    document.addEventListener("visibilitychange", () => { if (document.hidden) return; if (NET.state !== "online") probeSoon(0); else { flush(); refreshSaved(); } });
    setInterval(() => { paint(); refreshSaved(); }, 60000);
    try { OB = await loadOB(); SAVED = (await kvGet("saved")) || {}; } catch (e) { }
    paint(); setTimeout(() => { flush(); refreshSaved(); }, 4000);
    if (SW_OK) {
      navigator.serviceWorker.addEventListener("message", e => {
        const m = e.data || {};
        if (m.type === "cs-net") m.ok ? markOk() : markFail();
        else if (m.type === "cs-outbox") loadOB().then(l => { OB = l; obChanged(); if (m.sent) toast(`${plural(m.sent, "change")} sent.`, { kind: "good" }); });
      });
      const warm = () => {
        const c = navigator.serviceWorker.controller; if (!c) return;
        const urls = performance.getEntriesByType("resource").map(e => e.name).filter(u => /\/fonts\/[\w.-]+\.woff2?$|\/field\.jpg$/.test(u));
        if (urls.length) c.postMessage({ type: "warm", urls });
      };
      navigator.serviceWorker.addEventListener("controllerchange", warm);
      const reg = () => navigator.serviceWorker.register("sw.js?v=" + enc(VER), { scope: "./" }).then(warm).catch(e => console.warn("service worker:", e.message));
      if (document.readyState === "complete") setTimeout(reg, 1500); else addEventListener("load", () => setTimeout(reg, 1500), { once: true });
    }
  }

  // ================================================================ UI: pill, toasts, sheet
  function pillState() {
    const signedOut = CS.platform && !CS.user && !(CS.session && CS.session.setup_required);
    if (S.mode === "client") {
      const st = S.status;
      if (!st) return { k: "down", t: "Server unreachable", s: "Unreachable", d: "The Cyber-Scout Client on this computer isn't answering." };
      const n = st.outbox || 0, w = n ? ` · ${plural(n, "change")} waiting` : "";
      if (!st.server) return { k: "signedout", t: "Not connected", s: "Not connected", d: "Pick your team's server to start." };
      if (st.syncing) { const p = st.progress, pc = p && p.total ? Math.min(99, Math.floor(p.done / p.total * 100)) : null;
        return { k: "sync", t: pc != null ? `Syncing ${pc}%` : "Syncing…", s: pc != null ? `Syncing ${pc}%` : "Syncing", d: `Downloading from ${S.host(st.server)}.`, pc }; }
      if (!st.online) return { k: "offline", t: "Offline" + w, s: n ? `Offline · ${n}` : "Offline", att: clientAtt(st),
        d: `Can't reach ${S.host(st.server)}${st.offline_reason ? ` (${st.offline_reason})` : ""}. Pages show this computer's copy${st.last_sync ? ` from ${clock(st.last_sync * 1000)}` : ""}.` };
      if (!st.signed_in) return { k: "signedout", t: "Signed out", s: "Signed out", d: `Sign in to sync with ${S.host(st.server)}.` };
      return { k: "online", t: `Online · ${n ? plural(n, "change") + " waiting" : st.last_sync ? "synced " + agoTxt(st.last_sync * 1000) : "not synced yet"}`, s: "Online", d: `Connected to ${S.host(st.server)}.`, att: clientAtt(st) };
    }
    const n = pendingMine().length, att = attention().length, w = n ? ` · ${plural(n, "change")} waiting` : "";
    if (JOB && !JOB.quiet) { const pc = JOB.total ? Math.min(99, Math.floor(JOB.done / JOB.total * 100)) : 0; return { k: "sync", t: `Syncing ${pc}%`, s: `Syncing ${pc}%`, d: `Saving ${evName(JOB.ev)} for offline.`, pc }; }
    if (NET.state === "offline") return { k: "offline", t: "Offline" + w, s: n ? `Offline · ${n}` : "Offline", d: "This device has no connection. Pages show saved data.", att };
    if (NET.state === "unreachable") return { k: "down", t: "Server unreachable" + w, s: n ? `Unreachable · ${n}` : "Unreachable", d: "The Cyber-Scout server isn't answering. Pages show saved data.", att };
    if (signedOut) return { k: "signedout", t: "Signed out", s: "Signed out", d: "Sign in to see your team's data." };
    if (FLUSHING) return { k: "sync", t: `Sending ${plural(n, "change")}…`, s: `Sending ${n}`, d: "Sending what you changed while offline.", att };
    if (n) return { k: "online", t: `Online · ${plural(n, "change")} waiting`, s: "Online", d: "Changes made offline are sent in a moment (or use Send now).", att };
    const last = Math.max(NET.lastFresh, NET.lastSync);
    return { k: "online", t: `Online${last ? " · synced " + agoTxt(last) : ""}`, s: "Online", d: `Connected to ${location.host}.`, att };
  }
  const clientAtt = st => (st.errors || []).filter(e => e && (e.kind === "conflict" || e.kind === "failed")).length;
  const pillHTML = st => `<i class="conn-dot" aria-hidden="true"></i><span class="conn-t">${esc(st.t)}</span><span class="conn-s">${esc(st.s)}</span>${st.att ? `<b class="conn-att" title="${plural(st.att, "change")} need${st.att === 1 ? "s" : ""} attention">${st.att}</b>` : ""}`;
  function mountChrome() {
    const sb = document.getElementById("sidebar");
    if (sb && !document.getElementById("sbconn")) {
      const f = document.createElement("div"); f.className = "sb-foot";
      f.innerHTML = `<button type="button" class="conn-pill" id="sbconn" aria-haspopup="dialog" aria-expanded="false"></button>`;
      sb.appendChild(f);
    }
    const ub = document.getElementById("userbox");
    if (ub && !document.getElementById("tbconn")) {
      const b = document.createElement("button"); b.type = "button"; b.className = "conn-pill"; b.id = "tbconn";
      b.setAttribute("aria-haspopup", "dialog"); b.setAttribute("aria-expanded", "false");
      ub.parentNode.insertBefore(b, ub);
    }
    for (const id of ["sbconn", "tbconn"]) { const b = document.getElementById(id); if (b) b.onclick = () => openSheet(b); }
    if (!document.getElementById("connlive")) {
      const l = document.createElement("div"); l.id = "connlive"; l.className = "vh"; l.setAttribute("role", "status"); l.setAttribute("aria-live", "polite");
      document.body.appendChild(l);
    }
    paint();
  }
  let lastKey = "", paintQ = 0;
  function paintSoon() { if (!paintQ) paintQ = requestAnimationFrame(() => { paintQ = 0; paint(); }); }
  function paint() {
    const st = pillState(), html = pillHTML(st);
    document.documentElement.setAttribute("data-net", S.isOffline() ? "off" : "on");
    for (const id of ["sbconn", "tbconn"]) {
      const b = document.getElementById(id); if (!b) continue;
      if (b.__h !== html) { b.innerHTML = html; b.__h = html; }
      b.dataset.st = st.k;
      b.setAttribute("aria-label", `Connection: ${st.t}. ${st.d} Open connection details.`);
      b.title = st.d;
    }
    if (st.k !== lastKey) {
      const l = document.getElementById("connlive");
      if (l && lastKey && Date.now() - NET.toastAt > 1500) l.textContent = `Connection: ${st.k === "sync" ? "syncing" : st.t}`;
      lastKey = st.k;
    }
    for (const u of [...UIS]) { if (!u.el.isConnected) UIS.delete(u); else u.render(); }
    if (asof) paintAsOf();
  }
  S.paint = paint;

  // ---- toasts
  function toast(text, o = {}) {
    let box = document.getElementById("cstoasts");
    if (!box) { box = document.createElement("div"); box.id = "cstoasts"; box.className = "toasts"; box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    NET.toastAt = Date.now();
    const t = document.createElement("div"); t.className = "toast" + (o.kind ? " " + o.kind : "");
    t.innerHTML = `<i class="conn-dot" aria-hidden="true"></i><span class="toast-t">${esc(text)}</span>${o.action ? `<button type="button" class="toast-a">${esc(o.action)}</button>` : ""}<button type="button" class="toast-x tb-btn" aria-label="Dismiss" title="Dismiss">${CS.icon("close")}</button>`;
    let tm = 0;
    const kill = () => { clearTimeout(tm); t.classList.add("out"); setTimeout(() => t.remove(), 200); };
    const arm = () => { clearTimeout(tm); tm = setTimeout(kill, o.ms || (o.action ? 10000 : 6000)); };
    t.querySelector(".toast-x").onclick = kill;
    const a = t.querySelector(".toast-a"); if (a) a.onclick = () => { kill(); o.fn && o.fn(); };
    t.onmouseenter = () => clearTimeout(tm); t.onmouseleave = arm; t.addEventListener("focusin", () => clearTimeout(tm)); t.addEventListener("focusout", arm);
    box.appendChild(t); while (box.children.length > 3) box.firstElementChild.remove();
    arm();
  }
  CS.toast = toast;

  // ---- the Connection sheet (pill click)
  let sheet = null, sheetTrig = null;
  function openSheetFromToast() { const b = [document.getElementById("sbconn"), document.getElementById("tbconn")].find(x => x && x.getClientRects().length); openSheet(b || null); }
  function openSheet(trig) {
    if (!sheet) {
      sheet = document.createElement("dialog"); sheet.className = "compsheet syncsheet"; sheet.setAttribute("aria-labelledby", "cxh");
      document.body.appendChild(sheet);
      sheet.addEventListener("click", e => { if (e.target === sheet || e.target.closest("[data-x]")) sheet.close(); else if (e.target.closest("a[href]")) sheet.close(); });
      sheet.addEventListener("close", () => { if (sheetTrig) { sheetTrig.setAttribute("aria-expanded", "false"); if (sheetTrig.isConnected) sheetTrig.focus(); } });
      addEventListener("resize", () => { if (sheet.open) placeSheet(); });
    }
    sheetTrig = trig && trig.getClientRects().length ? trig : null;
    sheet.innerHTML = `<div class="cmp-in"><div class="cmp-h"><h2 id="cxh">Connection</h2><button type="button" class="tb-btn" data-x aria-label="Close" title="Close">${CS.icon("close")}</button></div>
      <div class="cmp-body cx-body"></div></div>`;
    mountPanel(sheet.querySelector(".cx-body"));
    if (!sheet.open) { try { sheet.showModal(); } catch (e) { sheet.setAttribute("open", ""); } }
    if (sheetTrig) sheetTrig.setAttribute("aria-expanded", "true");
    placeSheet();
    if (S.mode === "client") refresh();
  }
  S.openSheet = openSheetFromToast;
  function placeSheet() {
    if (!sheet || !sheet.open) return;
    const a = sheetTrig && sheetTrig.isConnected && sheetTrig.getClientRects().length ? sheetTrig : null;
    if (matchMedia("(max-width: 760px)").matches || !a) { sheet.classList.remove("anchored"); sheet.style.left = sheet.style.top = sheet.style.maxHeight = ""; return; }
    const r = a.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = innerHeight, w = Math.min(420, vw - 32);
    sheet.classList.add("anchored");
    const left = Math.max(16, Math.min(r.left, vw - w - 16));
    if (r.top > vh / 2) { sheet.style.top = ""; sheet.style.maxHeight = `${Math.round(r.top - 22)}px`; sheet.style.left = left + "px"; sheet.style.top = Math.max(16, Math.round(r.top - 6 - sheet.offsetHeight)) + "px"; }
    else { const top = Math.round(r.bottom + 6); sheet.style.left = left + "px"; sheet.style.top = top + "px"; sheet.style.maxHeight = `calc(100vh - ${top + 16}px)`; }
  }

  // ---- live panels: the sheet body and the Settings sections re-render themselves on every state change
  const UIS = new Set();
  function live(el, render) { const u = { el, render }; UIS.add(u); render(); return u; }
  function mountPanel(el) {
    if (S.mode === "client") {
      el.onclick = async e => {
        const b = e.target.closest("[data-act]"); if (!b) return;
        if (b.dataset.act === "sync") { b.disabled = true; try { await ctl("POST", "sync", {}, 30000); await refresh(); } catch (x) { toast(x.message, { kind: "bad" }); } }
      };
      live(el, () => {
        const st = S.status || {}, p = pillState(), ev = st.events || [], bytes = ev.reduce((a, e) => a + (e.bytes || 0) + (e.media_bytes || 0), 0);
        el.innerHTML = `${stLine(p)}
          ${st.server ? `<dl class="cx-dl"><div><dt>Server</dt><dd>${esc(S.host(st.server))}</dd></div><div><dt>Last sync</dt><dd>${st.last_sync ? `${esc(clock(st.last_sync * 1000))} (${agoTxt(st.last_sync * 1000)})` : "not yet"}</dd></div>
            <div><dt>On this computer</dt><dd>${plural(ev.length, "competition")} · ${fmtB(bytes)}</dd></div><div><dt>Changes waiting</dt><dd>${st.outbox || 0}</dd></div></dl>` : ""}
          ${progress(p)}
          <div class="cx-acts">${st.server && st.signed_in ? `<button type="button" class="primary" data-act="sync"${st.syncing || !st.online ? " disabled" : ""}>Sync now</button>` : ""}
            <a class="cmp-btn" href="#/${st.server ? "settings" : "connect"}">${st.server ? "Connection settings" : "Connect to a server"}</a></div>`;
      });
      return;
    }
    el.onclick = e => panelClick(e, el);
    live(el, () => {
      if (el.contains(document.activeElement) && el.querySelector(":focus:not(button)")) return;
      const keep = document.activeElement && el.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
      el.innerHTML = browserPanel();
      if (keep) { const f = el.querySelector(`[data-focus="${keep}"]`); if (f) f.focus(); }
      storageLine();
    });
  }
  const stLine = p => `<div class="cx-st" data-st="${p.k}"><i class="conn-dot" aria-hidden="true"></i><div><b>${esc(p.t)}</b><span>${esc(p.d)}</span></div></div>`;
  const progress = p => p.k === "sync" && p.pc != null ? `<div class="progress cx-prog" role="progressbar" aria-label="Sync progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.pc}"><div style="width:${p.pc}%"></div></div>` : "";
  function browserPanel() {
    const p = pillState(), ev = curEv(), rec = ev && SAVED[ev], job = JOB && JOB.ev === ev ? JOB : null;
    const others = Object.values(SAVED).filter(r => r.ev !== ev);
    let copy;
    if (!CACHE_OK) copy = `<p class="cx-note">Offline copies need the secure address of this site (https://…). Open it that way to save competitions on this device.</p>`;
    else if (!ev) copy = `<p class="cx-note">Open a competition to save it for offline use.</p>`;
    else {
      const est = !rec && !job && NET.state === "online" && CS.user ? estimate(ev) : null;
      const pc = job && job.total ? Math.min(99, Math.floor(job.done / job.total * 100)) : 0;
      copy = `<div class="cx-ev"><b>${esc(evName(ev))}</b>
        ${job ? `<span class="cx-m">Saving… ${fmtB(job.done)}${job.total ? ` of ${fmtB(job.total)}` : ""}</span>
          <div class="progress cx-prog" role="progressbar" aria-label="Saving for offline" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pc}"><div style="width:${pc}%"></div></div>`
        : rec ? `<span class="cx-m">${CS.icon("check", "ico")} Saved · ${plural(rec.count, "file")} · ${fmtB(rec.bytes)} · updated ${agoTxt(rec.at)}</span>`
        : `<span class="cx-m">Save it to open every page of this competition without a connection${est && est.bytes != null ? ` (about ${fmtB(est.bytes)})` : ""}. Videos aren't included.</span>${est && est.err ? `<span class="cx-m warn">${esc(est.err)}</span>` : ""}`}
        <div class="cx-acts">${job ? `<button type="button" data-act="cancel" data-focus="cancel">Cancel</button>`
          : rec ? `<button type="button" data-act="save" data-ev="${esc(ev)}" data-focus="upd"${NET.state !== "online" ? " disabled" : ""}>Update now</button><button type="button" class="danger" data-act="remove" data-ev="${esc(ev)}" data-focus="rm">Remove offline copy</button>`
          : `<button type="button" class="primary" data-act="save" data-ev="${esc(ev)}" data-focus="save"${NET.state !== "online" || !CS.user || JOB ? " disabled" : ""}>Save this event for offline</button>`}</div></div>`;
    }
    const oth = others.length ? `<ul class="cx-saved">${others.map(r => `<li><span><b>${esc(evName(r.ev))}</b><span class="cx-m">${fmtB(r.bytes)} · updated ${agoTxt(r.at)}</span></span>
      <button type="button" class="danger" data-act="remove" data-ev="${esc(r.ev)}" data-focus="rm-${esc(r.ev)}">Remove</button></li>`).join("")}</ul>` : "";
    const mine = OB.filter(x => x.uid === uidOf(CS.user));
    const ob = mine.length ? `<section class="cx-sec"><h3>Changes on this device <span class="cx-n">${mine.length}</span></h3><ul class="cx-ob">${mine.map(obItem).join("")}</ul>
      ${pendingMine().length ? `<div class="cx-acts"><button type="button" data-act="send" data-focus="send"${FLUSHING || NET.state === "offline" ? " disabled" : ""}>Send now</button></div>` : ""}</section>` : "";
    return `${stLine(p)}${p.k === "sync" && !job ? progress(p) : ""}
      <section class="cx-sec"><h3>Offline copy</h3>${copy}${oth}<p class="cx-note cx-store" data-part="storage">${STORE.html}</p></section>${ob}`;
  }
  function obItem(it) {
    const L = obLabel(it), cls = it.state === "conflict" ? "warn" : it.state === "failed" ? "bad" : "";
    const tag = it.state === "conflict" ? "Conflict" : it.state === "failed" ? "Not sent" : it.state === "sending" ? "Sending…" : "Waiting";
    const why = it.state === "conflict" && it.current ? `${who(it.current.updated_by)} saved a newer picklist${it.current.updated_at ? " " + agoTxt(it.current.updated_at * 1000) : ""}. Keep yours on top of it, or take theirs?`
      : it.state === "failed" ? it.error || "" : it.tries ? `Tried ${plural(it.tries, "time")}; trying again ${it.next > Date.now() ? "in " + Math.max(1, Math.round((it.next - Date.now()) / 1000)) + " s" : "soon"}.` : "";
    const acts = it.state === "conflict" ? `<button type="button" class="primary" data-act="mine" data-seq="${it.seq}">Keep mine</button><button type="button" data-act="theirs" data-seq="${it.seq}">Take theirs</button>`
      : it.state === "failed" ? `<button type="button" data-act="retry" data-seq="${it.seq}">Try again</button><button type="button" class="danger" data-act="discard" data-seq="${it.seq}">Discard</button>`
      : it.state === "sending" ? "" : `<button type="button" class="danger" data-act="discard" data-seq="${it.seq}">Discard</button>`;
    return `<li class="cx-oi"><div class="cx-oh"><b>${esc(L.t)}</b><span class="cx-tag ${cls}">${tag}</span><time datetime="${esc(new Date(it.at).toISOString())}">${esc(agoTxt(it.at))}</time></div>
      ${L.x ? `<div class="cx-oc">${esc(L.x)}</div>` : ""}${why ? `<div class="cx-ow ${cls}">${esc(why)}</div>` : ""}${acts ? `<div class="cx-acts">${acts}</div>` : ""}</li>`;
  }
  const STORE = { html: "", at: 0 };      // "Using 45 MB of about 2 GB…", refreshed at most every 10 s
  async function storageLine(force) {
    if (!navigator.storage || !navigator.storage.estimate || (!force && Date.now() - STORE.at < 10000)) return;
    STORE.at = Date.now();
    try {
      const [e, kept] = await Promise.all([navigator.storage.estimate(), navigator.storage.persisted ? navigator.storage.persisted() : false]);
      STORE.html = `Using ${fmtB(e.usage)} of about ${fmtB(e.quota)} available to this site. ${kept ? "Kept until you remove it." : Object.keys(SAVED).length
        ? `The browser may clear it if the device runs low on space. <button type="button" class="cx-link" data-act="persist">Ask to keep it</button>` : ""}`;
      for (const p of document.querySelectorAll(".cx-store[data-part=storage]")) p.innerHTML = STORE.html;
    } catch (x) { }
  }
  async function panelClick(e, el) {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const act = b.dataset.act, ev = b.dataset.ev, seq = b.dataset.seq != null ? +b.dataset.seq : null;
    if (act === "save") { saveEvent(ev).catch(x => { if (x.message !== "cancelled") toast(`Couldn't save ${evName(ev)}: ${x.message}.`, { kind: "bad" }); }); paint(); }
    else if (act === "cancel") { if (JOB) JOB.ac.abort(); }
    else if (act === "remove") { if (confirm(`Remove the offline copy of ${evName(ev)} from this device?`)) await removeSaved(ev); }
    else if (act === "send") flush(true);
    else if (act === "persist") { try { const ok = await navigator.storage.persist(); toast(ok ? "Offline data is kept until you remove it." : "The browser didn't agree to keep it (it decides based on how often you use this site). Your copy stays for now."); } catch (x) { } storageLine(true); }
    else if (seq != null) obAct(seq, act);
  }

  // ================================================================ Settings: Connection (client mode) / Offline (browser mode)
  S.mountSettings = el => {
    if (!el) return;
    if (S.mode === "client") return clientSettings(el);
    el.innerHTML = `<fieldset class="st-sec" id="offline"><legend><h2>Offline</h2></legend>
      <p class="st-why">Pages you open are kept on this device and still show when the connection drops. Save a whole competition before you go to the venue; notes, picklist changes and identity fixes made offline are sent when the connection is back.</p>
      <div class="card cx-card"></div></fieldset>`;
    const c = el.querySelector(".cx-card"); c.onclick = e => panelClick(e, c);
    live(c, () => { if (c.contains(document.activeElement) && document.activeElement.tagName !== "BUTTON") return; c.innerHTML = browserPanel(); storageLine(); });
  };
  function clientSettings(el) {
    const st0 = S.status || {};
    const cfgMedia = () => (S.status || {}).media || "none";
    const cfgEvents = () => { const f = (S.status || {}).followed; return Array.isArray(f) ? f : null; };     // null = every followed competition
    const D = { events: null, media: null, evErr: null };
    el.innerHTML = `<fieldset class="st-sec cx-conn" id="connection"><legend><h2>Connection</h2></legend>
      <p class="st-why">This computer keeps its own copy of your team's data, so every page works without a connection. Robot tracking runs on the server, or on this computer if you turn on Settings → Help process matches.</p>
      <div class="cx-grid">
        <div class="cx-row"><div class="cx-k" id="cxk-srv">Server</div><div class="cx-v">
          <form class="cx-srv" novalidate><input id="cxsrv" class="csin" aria-labelledby="cxk-srv" value="${esc(st0.server || "")}" placeholder="scout.example.org or 10.0.0.58:7560" autocomplete="url" inputmode="url" spellcheck="false" autocapitalize="none">
            <button type="button" data-act="test">Test connection</button><button type="submit" class="primary">Save</button></form>
          <div class="cx-res" role="status" aria-live="polite"></div></div></div>
        <div class="cx-row"><div class="cx-k">Account</div><div class="cx-v" data-part="acct"></div></div>
        <div class="cx-row"><div class="cx-k">Sync</div><div class="cx-v" data-part="sync"></div></div>
        <div class="cx-row"><div class="cx-k" id="cxk-media">Videos</div><div class="cx-v" data-part="media"></div></div>
        <div class="cx-row"><div class="cx-k" id="cxk-evs">Competitions</div><div class="cx-v" data-part="events"></div></div>
        <div class="cx-row"><div class="cx-k">Storage</div><div class="cx-v" data-part="storage"></div></div>
        <div class="cx-row"><div class="cx-k">Changes waiting</div><div class="cx-v" data-part="outbox"></div></div>
      </div></fieldset>`;
    const $ = s => el.querySelector(s), res = $(".cx-res"), inp = $("#cxsrv");
    const say = (cls, html) => { res.className = "cx-res " + cls; res.innerHTML = html; };
    async function test() {
      const s = normServer(inp.value);
      if (!s) { say("bad", "That isn't a server address. Try something like <b>scout.example.org</b> or <b>10.0.0.58:7560</b>."); return null; }
      inp.value = s; say("", "Testing…");
      try {
        const r = await ctl("POST", "test", { server: s }, 30000) || {};
        const info = r.info || r, ms = r.latency_ms != null ? r.latency_ms : r.latency != null ? (r.latency < 20 ? r.latency * 1000 : r.latency) : null;
        if (r.ok === false || r.error) { say("bad", `${CS.icon("warn", "ico")}<span>${esc(errText({ error: r.error }, 0) || "Couldn't connect.")}</span>`); return null; }
        say("good", `${CS.icon("check", "ico")}<span>Connected${info.name ? ` to <b>${esc(info.name)}</b>` : ""}${ms != null ? ` · ${Math.round(ms)} ms` : ""}${info.version ? ` · version ${esc(info.version)}` : ""}</span>`);
        return s;
      } catch (x) { say("bad", `${CS.icon("warn", "ico")}<span>${esc(x.message)}</span>`); return null; }
    }
    async function putConfig(patch) { await ctl("PUT", "config", patch); await refresh(); }
    $(".cx-srv").onsubmit = async e => {
      e.preventDefault();
      const s = await test(); if (!s) return;
      const st = S.status || {};
      if (st.server && s !== st.server && st.signed_in && !confirm(`Switch to ${S.host(s)}? You'll sign in again there.`)) return;
      try { await putConfig({ server: s }); say("good", `${CS.icon("check", "ico")}<span>Saved. ${S.status && S.status.signed_in ? "Syncing…" : "Now sign in."}</span>`); if (S.status && !S.status.signed_in && CS.reauth) setTimeout(CS.reauth, 600); }
      catch (x) { say("bad", esc(x.message)); }
    };
    $("[data-act=test]").onclick = test;
    el.addEventListener("click", async e => {
      const b = e.target.closest("[data-act]"); if (!b || b.dataset.act === "test") return;
      const a = b.dataset.act;
      try {
        if (a === "sync") { b.disabled = true; await ctl("POST", "sync", {}, 30000); await refresh(); }
        else if (a === "logout") { if (!confirm("Sign this computer out of the server? Saved data stays; syncing stops until you sign in again.")) return; await S.clientLogout(); if (CS.reauth) CS.reauth(); }
        else if (a === "ob-del") { if (!confirm("Discard this change? It won't be sent.")) return; await ctl("DELETE", "outbox/" + enc(b.dataset.id)); await refresh(); obLoad(true); }
        else if (a === "ob-mine" || a === "ob-theirs") {
          const it = (OBL || []).find(x => String(x.id) === b.dataset.id), cur = it && it.response && it.response.detail && it.response.detail.current; if (!it || !cur) return;
          const path = String(it.path).replace(/^\/?app\/v1\//, ""), m = /^events\/([^/]+)\/picklists\//.exec(path), ev = m && decodeURIComponent(m[1]);
          b.disabled = true;
          if (a === "ob-mine") { const r = await CS.app("PUT", path, Object.assign({}, it.body, { base_version: cur.version })); if (ev && CS.picklist && r && r.version != null) CS.picklist.adopt(ev, r); }
          else if (ev && CS.picklist) CS.picklist.adopt(ev, cur);
          await ctl("DELETE", "outbox/" + enc(b.dataset.id)); await refresh(); obLoad(true);
          toast(a === "ob-mine" ? "Your picklist is saved on top of theirs." : `Using the picklist ${who(cur.updated_by)} saved.`);
        }
      } catch (x) { toast(x.message, { kind: "bad" }); b.disabled = false; }
    });
    el.addEventListener("change", async e => {
      const t = e.target;
      try {
        if (t.name === "cx-media") { await putConfig({ media: t.value }); toast("Saved. The next sync follows the new video setting."); }
        else if (t.name === "cx-ev") { const all = [...el.querySelectorAll("input[name=cx-ev]")], l = all.filter(x => x.checked).map(x => x.value);
          await putConfig({ events: l.length === all.length ? null : l }); }
      } catch (x) { toast(`Not saved: ${x.message}`, { kind: "bad" }); }
    });
    // competitions + video sizes come from the server (through the Client); offline: what's on this computer
    async function loadLists() {
      const st = S.status || {};
      if (!st.server || !st.signed_in) { D.events = null; drawLists(); return; }
      if (Array.isArray(st.available_events) && st.available_events.length) D.events = st.available_events;
      else { try { D.events = await CS.app("GET", "sync/events"); D.evErr = null; } catch (x) { D.events = null; D.evErr = netErr(x) ? "offline" : x.message; } }
      drawLists();
      if (D.events && D.events.length) {
        const tot = { proxy: 0, full: 0 }; let ok = 0;
        await Promise.all(D.events.map(async e => {
          try { const l = await CS.app("GET", "sync/media?ev=" + enc(e.key)); ok++; for (const m of l || []) { if (m.kind === "proxy") tot.proxy += m.size || 0; else tot.full += m.size || 0; } } catch (x) { }
        }));
        D.media = ok ? tot : null; drawLists();
      }
    }
    function drawLists() {
      const st = S.status || {}, m = cfgMedia(), kf = cfgEvents(), keep = new Set(kf || []), local = Object.fromEntries((st.events || []).map(e => [e.key, e]));
      const md = $("[data-part=media]"), ed = $("[data-part=events]");
      if (md && !md.contains(document.activeElement)) {
        const sz = k => D.media ? ` · ${fmtB(k === "proxy" ? D.media.proxy : D.media.proxy + D.media.full)}` : "";
        md.innerHTML = `<div class="cx-radios" role="radiogroup" aria-labelledby="cxk-media">
          ${[["none", "None", "Data only. Videos play from the server when you're online."], ["proxy", "Previews" + sz("proxy"), "A small copy of every match video (about 480p): enough to check what happened."],
            ["full", "Full videos" + sz("full"), "The original broadcast video and robot clips. Needs a lot of space."]].map(([v, t, d]) =>
            `<label class="cx-opt"><input type="radio" name="cx-media" value="${v}"${m === v ? " checked" : ""}><span><b>${esc(t)}</b><small>${esc(d)}</small></span></label>`).join("")}</div>
          ${D.media ? `<p class="cx-note">Sizes are for the competitions below.</p>` : st.signed_in && st.online ? `<p class="cx-note">Working out video sizes…</p>` : ""}`;
      }
      if (ed && !ed.contains(document.activeElement)) {
        const list = D.events || (st.events || []).map(e => ({ key: e.key, name: e.key }));
        ed.innerHTML = list.length ? `<div class="cx-checks" role="group" aria-labelledby="cxk-evs">${list.map(e => {
          const l = local[e.key];
          return `<label class="cx-opt"><input type="checkbox" name="cx-ev" value="${esc(e.key)}"${!kf || keep.has(e.key) ? " checked" : ""}><span><b>${esc(e.name || e.key)}</b>
            <small>${l ? `${fmtB((l.bytes || 0) + (l.media_bytes || 0))} on this computer${l.synced_at ? ` · synced ${agoTxt(l.synced_at * 1000)}` : ""}` : "not synced yet"}${e.progress && e.progress.total ? ` · ${e.progress.tracked}/${e.progress.total} matches tracked` : ""}</small></span></label>`; }).join("")}</div>
          <p class="cx-note">${kf ? "Only the ticked competitions are kept on this computer." : "Every competition the server follows is kept (new ones too). Untick one to leave it out."}</p>
          ${D.evErr === "offline" ? `<p class="cx-note">Offline: showing what's on this computer.</p>` : D.evErr ? `<p class="cx-note warn">${esc(D.evErr)}</p>` : ""}`
          : `<p class="cx-note">${st.signed_in ? "No competitions followed on the server yet." : "Sign in to choose which competitions to keep."}</p>`;
      }
    }
    let OBL = null, obAt = 0;
    async function obLoad(force) {
      if (!force && Date.now() - obAt < 4000) return;
      obAt = Date.now();
      try { const r = await ctl("GET", "outbox"); OBL = Array.isArray(r) ? r : (r && (r.items || r.outbox)) || []; } catch (x) { OBL = null; }
      drawStatus();
    }
    const errsOf = st => (st.errors || []).filter(e => typeof e === "string" || (e && e.kind !== "conflict" && e.kind !== "failed"));
    function drawStatus() {
      const st = S.status || {}, p = pillState();
      const acct = $("[data-part=acct]"), sy = $("[data-part=sync]"), sto = $("[data-part=storage]"), ob = $("[data-part=outbox]");
      if (!acct) return;
      acct.innerHTML = !st.server ? `<span class="muted">Set the server first.</span>` : st.signed_in && st.user
        ? `<span>Signed in as <b>${esc(st.user.display_name || "")}</b> · ${esc(st.user.role || "")}</span><button type="button" data-act="logout">Sign out</button>`
        : `<span class="muted">Not signed in.</span><a class="cmp-btn primary" href="#/login">Sign in</a>`;
      sy.innerHTML = `${stLine(p)}${progress(p)}<div class="cx-acts"><button type="button" class="primary" data-act="sync"${!st.server || !st.signed_in || st.syncing || !st.online ? " disabled" : ""}>Sync now</button>
        <span class="muted small">Last sync: ${st.last_sync ? `${esc(clock(st.last_sync * 1000))} (${agoTxt(st.last_sync * 1000)})` : "not yet"}</span></div>
        ${st.server_info && st.server_info.version ? `<span class="muted small">${esc(st.server_info.name || S.host(st.server))} · version ${esc(st.server_info.version)}${st.server_info.latency_ms != null ? ` · ${Math.round(st.server_info.latency_ms)} ms` : ""}</span>` : ""}
        ${errsOf(st).length ? `<ul class="cx-errs">${errsOf(st).slice(-4).map(x => `<li>${CS.icon("warn", "ico")}<span>${esc(typeof x === "string" ? x : x.message || x.error || x.detail || JSON.stringify(x))}${x.time ? ` <span class="muted">· ${esc(agoTxt(x.time * 1000))}</span>` : ""}</span></li>`).join("")}</ul>` : ""}`;
      const ev = st.events || [], g = st.storage || {};
      const data = g.api_bytes != null ? g.api_bytes : ev.reduce((a, e) => a + (e.bytes || 0), 0), media = g.media_bytes != null ? g.media_bytes : ev.reduce((a, e) => a + (e.media_bytes || 0), 0);
      sto.innerHTML = `<span>${fmtB(data + media + (g.web_bytes || 0))} on this computer</span><span class="muted small">data ${fmtB(data)} · videos ${fmtB(media)} · ${plural(ev.length, "competition")}${g.free_bytes != null ? ` · ${fmtB(g.free_bytes)} free on the disk` : ""}</span>
        ${g.data_dir ? `<span class="muted small">Folder: <span class="code">${esc(g.data_dir)}</span></span>` : ""}`;
      const items = OBL || [];
      const curOf = it => { const r = it.response; return r && r.detail && r.detail.current || null; };
      ob.innerHTML = !st.outbox && !items.length ? `<span class="muted">Nothing waiting. Changes made offline wait here until the server is back.</span>`
        : `<ul class="cx-ob">${items.map(it => {
            const L = obLabel(it), state = it.state || "", cur = curOf(it), pick = /\/picklists\//.test(it.path || "") && cur;
            const tag = state === "conflict" ? ["warn", "Conflict"] : state === "failed" ? ["bad", "Not sent"] : state === "sending" ? ["", "Sending…"] : ["", "Waiting"];
            const why = state === "conflict" && pick ? `${who(cur.updated_by)} saved a newer picklist${cur.updated_at ? " " + agoTxt(cur.updated_at * 1000) : ""}. Keep yours on top of it, or take theirs?`
              : it.error ? String(it.error) : it.attempts ? `Tried ${plural(it.attempts, "time")}.` : "";
            const t = it.created || it.at;
            return `<li class="cx-oi"><div class="cx-oh"><b>${esc(L.t)}</b><span class="cx-tag ${tag[0]}">${tag[1]}</span>${t ? `<time>${esc(agoTxt(t * 1000))}</time>` : ""}</div>
              ${L.x ? `<div class="cx-oc">${esc(L.x)}</div>` : ""}${why ? `<div class="cx-ow ${tag[0]}">${esc(why)}</div>` : ""}
              <div class="cx-acts">${pick ? `<button type="button" class="primary" data-act="ob-mine" data-id="${esc(String(it.id))}">Keep mine</button><button type="button" data-act="ob-theirs" data-id="${esc(String(it.id))}">Take theirs</button>`
                : state === "sending" ? "" : `<button type="button" class="danger" data-act="ob-del" data-id="${esc(String(it.id))}">Discard</button>`}</div></li>`; }).join("")}</ul>
          ${st.outbox && !items.length ? `<span class="muted">${plural(st.outbox, "change")} waiting.</span>` : ""}`;
      obLoad();
    }
    let was = JSON.stringify([st0.server, st0.signed_in, st0.online]);
    live($(".cx-grid"), () => {
      drawStatus();
      const st = S.status || {}, now = JSON.stringify([st.server, st.signed_in, st.online]);
      if (now !== was) { was = now; loadLists(); }
    });
    drawLists(); loadLists();
  }

  // ================================================================ #/connect: Cyber-Scout Client, first run (or "Change server")
  CS.globalRoute("connect", async () => {
    crumbs([["Connect"]]); $updated.textContent = "";
    if (S.mode !== "client") { location.replace("#/"); return; }
    const st = S.status || {};
    $app.innerHTML = `<div class="card authcard cx-onb">
      <div class="cx-hero">${document.querySelector(".sb-brand .logo") ? document.querySelector(".sb-brand .logo").outerHTML : ""}<div><h1>${st.server ? "Change server" : "Welcome to Cyber-Scout"}</h1>
        <div class="sub">${st.server ? `Connected to <b>${esc(S.host(st.server))}</b> now.` : "This computer keeps a copy of your team's scouting data, so every page works even when the venue Wi-Fi doesn't."}</div></div></div>
      <ol class="cx-steps"><li class="on"><span><b>Connect</b> to your team's Cyber-Scout server</span></li><li><span><b>Sign in</b> with your Cyber-Scout account</span></li><li><span><b>Sync</b>: the data downloads in the background</span></li></ol>
      <form class="csform" novalidate><label>Server address<input name="server" value="${esc(st.server || "")}" placeholder="scout.example.org" autocomplete="url" inputmode="url" spellcheck="false" autocapitalize="none" required></label>
        <p class="cx-note">The address you use for Cyber-Scout in a browser, like <span class="code">scout.example.org</span>, <span class="code">https://scout.example.org</span> or <span class="code">10.0.0.58:7560</span>. Ask your team's admin if you don't know it.</p>
        <div class="row"><button type="button" data-act="test">Test connection</button><button type="submit" class="primary">Connect</button></div>
        <div class="cx-res" role="status" aria-live="polite"></div></form>
      <p class="cx-note">The look of the app is in <a href="#/settings">Settings</a>.</p></div>`;
    const f = $app.querySelector("form"), inp = f.querySelector("input"), res = f.querySelector(".cx-res"), sub = f.querySelector("button[type=submit]");
    const say = (cls, html) => { res.className = "cx-res " + cls; res.innerHTML = html; };
    async function test() {
      const s = normServer(inp.value);
      if (!s) { say("bad", "That isn't a server address. Try something like <b>scout.example.org</b> or <b>10.0.0.58:7560</b>."); inp.focus(); return null; }
      inp.value = s; say("", "Testing…");
      try {
        const r = await ctl("POST", "test", { server: s }, 30000) || {};
        if (r.ok === false || r.error) { say("bad", `${CS.icon("warn", "ico")}<span>${esc(errText({ error: r.error }, 0) || "Couldn't connect.")}</span>`); return null; }
        const info = r.info || r, ms = r.latency_ms != null ? r.latency_ms : r.latency != null ? (r.latency < 20 ? r.latency * 1000 : r.latency) : null;
        say("good", `${CS.icon("check", "ico")}<span>Found${info.name ? ` <b>${esc(info.name)}</b>` : " the server"}${ms != null ? ` · ${Math.round(ms)} ms` : ""}${info.version ? ` · version ${esc(info.version)}` : ""}</span>`);
        return s;
      } catch (x) { say("bad", `${CS.icon("warn", "ico")}<span>${esc(x.message)}</span>`); return null; }
    }
    f.querySelector("[data-act=test]").onclick = test;
    f.onsubmit = async e => {
      e.preventDefault(); sub.disabled = true;
      try {
        const s = await test(); if (!s) return;
        const cur = S.status || {};
        if (cur.server !== s) await ctl("PUT", "config", { server: s });
        await refresh();
        if (CS.reauth) CS.reauth();
      } catch (x) { say("bad", esc(x.message)); }
      finally { sub.disabled = false; }
    };
    inp.focus();
  });
})();
