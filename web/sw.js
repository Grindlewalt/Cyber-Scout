"use strict";
/* sw.js: the browser-mode service worker (docs/SYNC.md §3). Registered by sync.js as sw.js?v=<asset hash> (the ?v= that
 * web/Dockerfile stamps into index.html; "dev" when unstamped), scope = the app's own directory. Never registered when
 * the page is served by the Cyber-Scout Client (client mode).
 *   app shell   index.html: network-first (4 s), cached copy when the server can't be reached.
 *               name.js?v=<hash> / name.css?v=<hash> (+ field.jpg): precached at install from the links in index.html,
 *               cache-first (immutable under their URL). Unversioned shell files (dev) are network-first; fonts/ cache-first.
 *   site JSON   api/...: stale-while-revalidate with a short network grace: the network answer is used if it arrives
 *               within 1.5 s, else the cached copy is shown at once and the cache is updated in the background.
 *               Saved events (cs-ev-<ev>, written by sync.js from /app/v1/sync/bundle) are read the same way.
 *   media       media/*.jpg|png|webp: a small LRU of posters. Videos (and every Range request) pass straight through.
 *   app/v1, client/v1   never touched: auth / session answers are never cached; writes go through the page's outbox.
 * Cached responses carry x-cs-at (ms when fetched); answers served from the cache carry x-cs-src: offline | stale
 * so the page can say "as of …". Background Sync (tag cs-outbox) flushes the IndexedDB outbox shared with sync.js. */
const V = new URL(self.location.href).searchParams.get("v") || "dev";
const SCOPE = self.registration.scope;
const SHELL = "cs-shell-" + V, DATA = "cs-data", POSTERS = "cs-posters", EVP = "cs-ev-";
const GRACE = 1500, NAV_WAIT = 4000, DATA_MAX = 500, POSTER_MAX = 40;
const KNOWN = c => c === SHELL || c === DATA || c === POSTERS || c.startsWith(EVP);

// ------------------------------------------------------------------ install / activate
self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL);
    const r = await fetch(SCOPE, { cache: "reload", credentials: "same-origin" });
    if (!r.ok) throw new Error("index.html: " + r.status);
    const html = await r.clone().text();
    const urls = new Set([new URL("field.jpg", SCOPE).href]);
    for (const m of html.matchAll(/\s(?:src|href)="([A-Za-z0-9_.-]+\.(?:js|css)(?:\?v=[A-Za-z0-9]+)?)"/g)) urls.add(new URL(m[1], SCOPE).href);
    await c.put(SCOPE, r);
    // versioned files never change under their URL: reuse an older shell cache's copy when it has one
    await Promise.all([...urls].map(async u => {
      const have = /\?v=/.test(u) && await caches.match(u);
      const res = have || await fetch(u, { cache: "no-cache", credentials: "same-origin" });
      if (res.ok) await c.put(u, res);
    }));
    await self.skipWaiting();
  })());
});
self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("cs-") && !KNOWN(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

// ------------------------------------------------------------------ fetch
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || !req.url.startsWith(SCOPE)) return;
  const path = req.url.slice(SCOPE.length).split("#")[0];
  if (req.mode === "navigate") return e.respondWith(navigate(req, path));
  if (/^(app|client)\/v1\//.test(path)) return;                                // never cached
  if (path.startsWith("api/")) return e.respondWith(data(e, req));
  if (path.startsWith("media/")) {
    if (!req.headers.has("range") && /^[^?]+\.(?:jpe?g|png|webp)(?:\?|$)/i.test(path)) e.respondWith(poster(req));
    return;
  }
  if (path.startsWith("fonts/")) return e.respondWith(cacheFirst(req, SHELL));
  if (/^[A-Za-z0-9_.-]+\.(?:js|css|jpg|png|svg)(?:\?v=[A-Za-z0-9]+)?$/.test(path) && path.split("?")[0] !== "sw.js")
    return e.respondWith(/\?v=/.test(path) ? cacheFirst(req, SHELL) : networkFirst(req, SHELL));
});

const timeout = ms => new Promise(res => setTimeout(() => res(null), ms));
function mark(r, src) {
  const h = new Headers(r.headers); h.set("x-cs-src", src);
  return new Response(r.body, { status: r.status, statusText: r.statusText, headers: h });
}
async function stamp(r) {
  const h = new Headers(r.headers); h.set("x-cs-at", String(Date.now())); h.delete("x-cs-src");
  return new Response(await r.blob(), { status: r.status, statusText: r.statusText, headers: h });
}

async function navigate(req, path) {
  const shellPath = path.split("?")[0];
  const isIndex = shellPath === "" || shellPath === "index.html";
  const net = fetch(req).then(async r => {
    if (r.ok && isIndex && (r.headers.get("content-type") || "").includes("html")) {
      const c = await caches.open(SHELL); await c.put(SCOPE, r.clone());
    }
    return r;
  });
  const fallback = async () => (await caches.match(SCOPE, { cacheName: SHELL })) || (await anyShell());
  try {
    const r = await Promise.race([net, timeout(NAV_WAIT)]);
    if (r && r.status < 500) return r;
    const c = await fallback();
    if (c) { if (!r) net.catch(() => { }); return c; }
    return r || await net;
  } catch (err) {
    const c = await fallback(); if (c) return c;
    throw err;
  }
}
async function anyShell() {      // an older version's index.html beats nothing (first offline load after a deploy)
  for (const k of (await caches.keys()).filter(k => k.startsWith("cs-shell-")).reverse()) {
    const r = await (await caches.open(k)).match(SCOPE); if (r) return r;
  }
  return null;
}
async function cacheFirst(req, name) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok && r.type === "basic") { const c = await caches.open(name); await c.put(req, r.clone()); }
  return r;
}
async function networkFirst(req, name) {
  try {
    const r = await fetch(req);
    if (r.ok) { const c = await caches.open(name); await c.put(req, r.clone()); }
    return r;
  } catch (err) {
    const hit = await caches.match(req); if (hit) return hit;
    throw err;
  }
}

// ---- site JSON
async function evCacheName(url) {
  const m = url.slice(SCOPE.length).match(/^api\/([0-9]{4}[a-z0-9]{1,16})\//);
  return m && (await caches.has(EVP + m[1])) ? EVP + m[1] : null;
}
/** the newest copy across the runtime cache and the event's saved copy */
async function best(req, evName) {
  const a = await (await caches.open(DATA)).match(req, { ignoreVary: true });
  const b = evName ? await (await caches.open(evName)).match(req, { ignoreVary: true }) : null;
  if (!a || !b) return a || b;
  return +(a.headers.get("x-cs-at") || 0) >= +(b.headers.get("x-cs-at") || 0) ? a : b;
}
let puts = 0;
async function store(req, r, evName) {
  const s = await stamp(r);
  await (await caches.open(DATA)).put(req, s.clone());
  if (evName) { const c = await caches.open(evName); if (await c.match(req, { ignoreVary: true })) await c.put(req, s.clone()); }
  if (++puts % 25 === 0) trim(DATA, DATA_MAX);
}
async function trim(name, max) {
  const c = await caches.open(name), keys = await c.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - max))) await c.delete(k);
}
let lastNet = null, lastNetAt = 0;
async function notify(ok) {
  const now = Date.now();
  if (ok === lastNet && now - lastNetAt < 5000) return;
  lastNet = ok; lastNetAt = now;
  for (const c of await self.clients.matchAll({ type: "window" })) c.postMessage({ type: "cs-net", ok, at: now });
}
const offline = () => new Response(JSON.stringify({ offline: true, detail: "not saved on this device" }),
  { status: 503, headers: { "content-type": "application/json", "x-cs-src": "none", "cache-control": "no-store" } });
async function data(e, req) {
  const evName = await evCacheName(req.url);
  const cached = await best(req, evName);
  const net = fetch(req).then(async r => {
    if (r.ok) { await store(req, r.clone(), evName); notify(true); }
    else if (r.status >= 500) notify(false);
    else notify(true);
    return r;
  }, err => { notify(false); throw err; });
  if (!cached) return net.catch(() => offline());
  e.waitUntil(net.then(() => { }, () => { }));                                      // keep revalidating after we answer
  if (self.navigator && self.navigator.onLine === false) return mark(cached, "offline");
  try {
    const r = await Promise.race([net, timeout(GRACE)]);
    if (!r) return mark(cached, "stale");                                              // slow link: cached now, fresh next time
    if (r.status >= 500) return mark(cached, "offline");                                // server / API down
    return r;                                                                           // fresh (or 401 / 404: the truth)
  } catch (err) {
    return mark(cached, "offline");
  }
}

// ---- posters
async function poster(req) {
  const c = await caches.open(POSTERS), hit = await c.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok && r.type === "basic" && +(r.headers.get("content-length") || 0) < 1.5e6) { await c.put(req, r.clone()); trim(POSTERS, POSTER_MAX); }
  return r;
}

// ------------------------------------------------------------------ messages from the page
self.addEventListener("message", e => {
  const m = e.data || {};
  if (m.type === "warm" && Array.isArray(m.urls)) {           // fonts / images the page loaded before this worker controlled it
    e.waitUntil((async () => {
      const c = await caches.open(SHELL);
      for (const u of m.urls.slice(0, 40)) {
        if (typeof u !== "string" || !u.startsWith(SCOPE) || !/^(fonts\/[\w.-]+\.woff2?|field\.jpg)$/.test(u.slice(SCOPE.length))) continue;
        if (await c.match(u)) continue;
        try { const r = await fetch(u, { credentials: "same-origin" }); if (r.ok) await c.put(u, r); } catch (x) { }
      }
    })());
  } else if (m.type === "clear-data") {                       // sign-out: drop what was cached while browsing
    e.waitUntil(Promise.all([caches.delete(DATA), caches.delete(POSTERS)]));
  } else if (m.type === "flush") {
    e.waitUntil(flush().catch(() => { }));
  }
});

// ------------------------------------------------------------------ Background Sync: send the outbox (same rules as sync.js)
self.addEventListener("sync", e => { if (e.tag === "cs-outbox") e.waitUntil(flush()); });
function idb() {
  return new Promise((res, rej) => {
    const q = indexedDB.open("cyberscout", 1);
    q.onupgradeneeded = () => {
      const db = q.result;
      if (!db.objectStoreNames.contains("outbox")) db.createObjectStore("outbox", { keyPath: "seq", autoIncrement: true });
      if (!db.objectStoreNames.contains("reads")) db.createObjectStore("reads", { keyPath: "k" });
      if (!db.objectStoreNames.contains("kv")) db.createObjectStore("kv", { keyPath: "k" });
    };
    q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error);
  });
}
const tx = (db, mode, fn) => new Promise((res, rej) => {
  const t = db.transaction("outbox", mode), s = t.objectStore("outbox"); let out;
  const r = fn(s); if (r) r.onsuccess = () => { out = r.result; };
  t.oncomplete = () => res(out); t.onerror = () => rej(t.error); t.onabort = () => rej(t.error);
});
async function flush() {
  const run = async () => {
    const db = await idb();
    try {
      const all = (await tx(db, "readonly", s => s.getAll())) || [];
      let sent = 0, retry = false;
      for (const it of all.sort((a, b) => a.seq - b.seq)) {
        if (it.state !== "pending") continue;
        let r;
        try {
          r = await fetch(new URL("app/v1/" + it.path, SCOPE).href, {
            method: it.method, credentials: "same-origin", cache: "no-store",
            headers: Object.assign({ "Idempotency-Key": it.key, "X-CSRF-Token": it.csrf || "" }, it.body !== undefined ? { "Content-Type": "application/json" } : {}),
            body: it.body !== undefined ? JSON.stringify(it.body) : undefined,
          });
        } catch (err) { retry = true; break; }
        if (r.ok || r.status === 202) { await tx(db, "readwrite", s => s.delete(it.seq)); sent++; continue; }
        if (r.status === 401 || r.status === 403 || r.status === 429 || r.status >= 500) { retry = r.status !== 401 && r.status !== 403; break; }   // the page sorts these out
        let d = null; try { d = await r.json(); } catch (x) { }
        const cur = r.status === 409 && d && d.detail && d.detail.current;
        Object.assign(it, cur ? { state: "conflict", current: cur } : { state: "failed" }, { status: r.status, error: (d && typeof d.detail === "string" && d.detail) || "HTTP " + r.status });
        await tx(db, "readwrite", s => s.put(it));
      }
      for (const c of await self.clients.matchAll({ type: "window" })) c.postMessage({ type: "cs-outbox", sent });
      if (retry) throw new Error("server unreachable: retry later");
    } finally { db.close(); }
  };
  return run();     // the page may send the same change at the same time: same Idempotency-Key, so the server replays
}
