/* Label review client: queue -> frame -> edit boxes on a canvas -> save labels/review/<id>.json. No framework. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const cv = $("cv"), stage = $("stage"), ctx = cv.getContext("2d");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: not remembered */ } },
  };
  const S = {
    proj: null, q: "uncertain", seed: Math.floor(Math.random() * 1e9), filters: { event: "", match: "", split: "" },
    list: [], pos: -1, seen: new Set(), more: true, loading: false,
    frame: null, img: null, boxes: [], base: [], baseKind: "none", prevStatus: null, prevDiff: null,
    sel: -1, cls: 0, conf: 0.25, hist: [], fut: [], view: { s: 1, x: 0, y: 0, fit: 1 },
    hideAll: false, space: false, ptr: { x: 0, y: 0 }, name: "", busy: false, uid: 1,
  };
  let labelFont = "";
  const HANDLE = () => (matchMedia("(pointer: coarse)").matches ? 16 : 9);

  // ---------- network
  async function api(path, opt) {
    const r = await fetch(path, Object.assign({ credentials: "same-origin" }, opt || {}));
    let d = null;
    try { d = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) throw new Error((d && d.error) || ("HTTP " + r.status));
    return d;
  }
  const imgUrl = (id) => "/img/" + encodeURIComponent(id);

  // ---------- toast
  let toastT = 0;
  function toast(msg, err) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
    clearTimeout(toastT); toastT = setTimeout(() => { t.className = "toast" + (err ? " err" : ""); }, err ? 3500 : 1400);
  }

  // ---------- project, filters, classes
  async function boot() {
    applyTheme(store.get("csreview.theme") || "auto");
    S.name = store.get("csreview.name") || "";
    $("who").value = S.name;
    const c = parseFloat(store.get("csreview.conf"));
    if (c >= 0 && c <= 0.95) S.conf = c;
    $("conf").value = S.conf; $("confOut").textContent = S.conf.toFixed(2);
    const q = store.get("csreview.q");
    if (q && document.querySelector('.q[data-q="' + q + '"]')) S.q = q;
    try { S.proj = await api("/api/project"); } catch (e) { return showEmpty("Could not load the project: " + e.message); }
    $("projName").textContent = S.proj.name;
    document.title = S.proj.name + " · Label review";
    buildClasses(); buildFilters(); markQueue();
    resize(); refreshStats(); await restartQueue();
  }

  function buildClasses() {
    const el = $("classes"); el.textContent = "";
    S.proj.classes.forEach((name, i) => {
      const b = document.createElement("button");
      b.className = "cls" + (i === S.cls ? " on" : ""); b.style.setProperty("--c", S.proj.colors[i]);
      b.setAttribute("role", "radio"); b.setAttribute("aria-checked", i === S.cls ? "true" : "false");
      b.title = "Class " + (i + 1) + " (key " + (i + 1) + ")";
      const dot = document.createElement("i"); dot.textContent = i < 9 ? String(i + 1) : "";
      b.append(dot, document.createTextNode(name));
      b.addEventListener("click", () => pickClass(i));
      el.append(b);
    });
  }
  function pickClass(i) {
    if (i < 0 || i >= S.proj.classes.length) return;
    S.cls = i;
    [...$("classes").children].forEach((b, k) => { b.classList.toggle("on", k === i); b.setAttribute("aria-checked", k === i ? "true" : "false"); });
    if (S.sel >= 0 && S.boxes[S.sel].c !== i) { push(); S.boxes[S.sel].c = i; changed(); }
  }
  function opt(sel, v, label) { const o = document.createElement("option"); o.value = v; o.textContent = label; sel.append(o); }
  function buildFilters() {
    const ev = $("fEvent"), sp = $("fSplit");
    Object.keys(S.proj.events).forEach((e) => opt(ev, e, e || "(none)"));
    S.proj.splits.filter((x) => x).forEach((s) => opt(sp, s, s));
    fillMatches();
    ev.addEventListener("change", () => { S.filters.event = ev.value; S.filters.match = ""; fillMatches(); restartQueue(); });
    $("fMatch").addEventListener("change", (e) => { S.filters.match = e.target.value; restartQueue(); });
    sp.addEventListener("change", () => { S.filters.split = sp.value; restartQueue(); });
  }
  function fillMatches() {
    const m = $("fMatch"); m.textContent = ""; opt(m, "", "All");
    const ev = S.filters.event;
    const ms = ev ? (S.proj.events[ev] || []) : [...new Set(Object.values(S.proj.events).flat())].sort();
    ms.forEach((x) => opt(m, x, x || "(none)"));
    m.disabled = ms.length === 0;
  }
  function markQueue() {
    document.querySelectorAll(".q").forEach((b) => b.classList.toggle("on", b.dataset.q === S.q));
  }

  // ---------- queue
  async function fetchPage(offset) {
    const p = new URLSearchParams({ q: S.q, seed: S.seed, offset: offset, limit: 1000 });
    for (const k in S.filters) if (S.filters[k]) p.set(k, S.filters[k]);
    if (S.q === "mine" && S.name) p.set("by", S.name);
    return api("/api/queue?" + p);
  }
  async function restartQueue() {
    S.list = []; S.pos = -1; S.seen = new Set(); S.more = true;
    await loadMore();
    if (S.list.length) await go(0); else showEmpty(emptyText());
  }
  function emptyText() {
    return { uncertain: "Nothing left to review here. Nice.", gold: "The gold set is fully reviewed.",
      random: "Nothing left to review here.", mine: S.name ? "You have not reviewed anything yet." : "Set your name first.",
      all: "No frames in this project yet." }[S.q];
  }
  // Unreviewed queues shrink as you save, so they are re-read from the top and frames already visited are dropped.
  async function loadMore() {
    if (S.loading || !S.more) return;
    S.loading = true;
    try {
      const shrinking = S.q !== "mine" && S.q !== "all";
      let offset = shrinking ? 0 : S.list.length, added = 0;
      for (let guard = 0; guard < 50; guard++) {
        const d = await fetchPage(offset);
        for (const it of d.items) if (!S.seen.has(it.id)) { S.seen.add(it.id); S.list.push(it); added++; }
        offset += d.items.length;
        if (added || offset >= d.total || !d.items.length) { S.more = offset < d.total; break; }
      }
      if (!added) S.more = false;
    } catch (e) { toast("Queue: " + e.message, true); } finally { S.loading = false; }
  }

  async function go(pos) {
    if (pos < 0) return;
    if (pos >= S.list.length - 3) await loadMore();
    if (pos >= S.list.length) { showEmpty(emptyText()); S.pos = S.list.length; updateInfo(); return; }
    S.pos = pos;
    const it = S.list[pos];
    $("spin").hidden = false;
    try {
      const [fr, img] = await Promise.all([api("/api/frame/" + encodeURIComponent(it.id)), loadImg(it.id)]);
      if (S.list[S.pos] !== it) return;                       // user moved on meanwhile
      S.frame = fr; S.img = img; $("empty").hidden = true;
      loadBoxes(); fit(); updateInfo(); draw(); prefetch();
    } catch (e) { toast("Frame " + it.id + ": " + e.message, true); } finally { $("spin").hidden = true; }
  }
  function loadImg(id) {
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im); im.onerror = () => rej(new Error("image did not load"));
      im.src = imgUrl(id);
    });
  }
  function prefetch() {
    for (let k = 1; k <= 3; k++) { const it = S.list[S.pos + k]; if (it) { const im = new Image(); im.src = imgUrl(it.id); } }
  }
  function showEmpty(msg) {
    S.frame = null; S.img = null; S.boxes = []; S.sel = -1;
    const e = $("empty"); e.textContent = msg; e.hidden = false; draw(); updateInfo();
  }

  // ---------- boxes
  function mk(b, kind) {
    return { c: b.c | 0, b: b.b.map(Number), p: typeof b.p === "number" ? b.p : null, src: b.src || kind, kind: kind, uid: S.uid++ };
  }
  function loadBoxes() {
    const f = S.frame;
    S.prevStatus = f.review ? f.review.status : null;
    S.prevDiff = f.review && f.review.diff ? f.review.diff : null;
    if (f.review) { S.baseKind = "review"; S.boxes = (f.review.boxes || []).map((b) => mk(b, "review")); }
    else if (f.import) { S.baseKind = "import"; S.boxes = (f.import.boxes || []).map((b) => mk(b, "import")); }
    else if (f.auto) { S.baseKind = "auto"; S.boxes = (f.auto.boxes || []).map((b) => mk(b, "auto")); }
    else { S.baseKind = "none"; S.boxes = []; }
    S.base = S.boxes.map((b) => ({ uid: b.uid, c: b.c, b: b.b.slice(), p: b.p, kind: b.kind }));
    S.sel = -1; S.hist = []; S.fut = [];
  }
  const visible = (b) => !(b.kind === "auto" && b.p != null && b.p < S.conf);
  function snapshot() { return { boxes: S.boxes.map((b) => Object.assign({}, b, { b: b.b.slice() })), sel: S.sel }; }
  function push() { S.hist.push(snapshot()); if (S.hist.length > 200) S.hist.shift(); S.fut = []; }
  function undo() { if (!S.hist.length) return; S.fut.push(snapshot()); const s = S.hist.pop(); S.boxes = s.boxes; S.sel = s.sel; changed(); }
  function redo() { if (!S.fut.length) return; S.hist.push(snapshot()); const s = S.fut.pop(); S.boxes = s.boxes; S.sel = s.sel; changed(); }
  function changed() { updateBoxes(); updateDiff(); draw(); }
  function diff() {
    const base = new Map(S.base.filter(visible).map((b) => [b.uid, b]));
    const now = S.boxes.filter(visible);
    let added = 0, moved = 0;
    for (const b of now) {
      const o = base.get(b.uid);
      if (!o) added++;
      else if (o.c !== b.c || o.b.some((v, k) => Math.abs(v - b.b[k]) > 0.5)) moved++;
    }
    const ids = new Set(now.map((b) => b.uid));
    let removed = 0;
    for (const uid of base.keys()) if (!ids.has(uid)) removed++;
    return { added, removed, moved };
  }
  function updateDiff() {
    const d = diff(), el = $("diffLine");
    if (!S.frame) { el.textContent = ""; return; }
    const parts = [];
    if (d.added) parts.push("<b>+" + d.added + "</b> added");
    if (d.removed) parts.push("<b>−" + d.removed + "</b> removed");
    if (d.moved) parts.push("<b>" + d.moved + "</b> changed");
    el.innerHTML = parts.length ? parts.join(" · ") : "Unchanged from " + ({ auto: "the auto labels", import: "the imported labels", review: "your saved labels", none: "an empty frame" }[S.baseKind]);
  }
  function clip(b) {
    const W = S.img.naturalWidth, H = S.img.naturalHeight;
    let [x0, y0, x1, y1] = b;
    if (x0 > x1) [x0, x1] = [x1, x0];
    if (y0 > y1) [y0, y1] = [y1, y0];
    return [Math.max(0, Math.min(W, x0)), Math.max(0, Math.min(H, y0)), Math.max(0, Math.min(W, x1)), Math.max(0, Math.min(H, y1))];
  }

  // ---------- saving
  async function save(status) {
    if (!S.frame || S.busy) return;
    if (!S.name) { $("who").focus(); toast("Type your name first: it goes on every label", true); return; }
    const d = diff(), edited = d.added + d.removed + d.moved > 0;
    if (status === "accept") status = edited ? "fixed" : (S.baseKind === "review" && S.prevStatus === "fixed" ? "fixed" : "ok");
    if (S.prevDiff && S.baseKind === "review") for (const k in d) d[k] += S.prevDiff[k] || 0;
    const boxes = S.boxes.filter(visible).map((b) => ({ c: b.c, b: clip(b.b).map((v) => Math.round(v * 10) / 10) }));
    S.busy = true;
    try {
      const id = S.list[S.pos].id;
      await api("/api/review/" + encodeURIComponent(id), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ boxes, status, by: S.name, conf: S.conf, diff: d }),
      });
      S.list[S.pos].status = status;
      toast({ ok: "Accepted", fixed: "Saved with fixes", skip: "Skipped", bad: "Marked bad" }[status]);
      refreshStats();
      await go(S.pos + 1);
    } catch (e) { toast("Not saved: " + e.message, true); } finally { S.busy = false; }
  }

  // ---------- info panels
  const fmtT = (t) => (typeof t === "number" ? t.toFixed(1) + " s" : "–");
  function updateInfo() {
    const it = S.list[S.pos], f = S.frame;
    $("fPos").textContent = S.list.length ? Math.min(S.pos + 1, S.list.length) + " / " + S.list.length + (S.more ? "+" : "") : "–";
    const st = $("fStatus"), status = f && f.review ? f.review.status : (f ? "new" : "–");
    st.textContent = status; st.className = "badge " + status;
    const meta = $("fMeta"); meta.textContent = "";
    if (!f) { $("fTitle").textContent = "–"; updateBoxes(); updateDiff(); return; }
    const r = f.frame;
    $("fTitle").textContent = [r.match || r.video || r.id].filter(Boolean).join(" ") || r.id;
    const rows = [["Event", r.event], ["Time", fmtT(r.t)], ["View", { F: "fixed camera", M: "moving camera", "?": "unknown" }[r.view] || r.view],
      ["Split", r.split], ["Source", r.src], ["Size", r.w && r.h ? r.w + " × " + r.h : null]];
    if (f.auto) rows.push(["Teacher", f.auto.teacher || "–"], ["Uncertainty", typeof f.auto.u === "number" ? f.auto.u.toFixed(2) : (it && it.u != null ? it.u.toFixed(2) : "–")]);
    if (f.review) rows.push(["Reviewed by", f.review.by + " · " + new Date(f.review.at * 1000).toLocaleString()]);
    rows.push(["Frame id", r.id]);
    for (const [k, v] of rows) {
      if (v == null || v === "") continue;
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = k; dd.textContent = v; meta.append(dt, dd);
    }
    updateBoxes(); updateDiff();
  }
  function updateBoxes() {
    const ol = $("boxList"); ol.textContent = "";
    $("nBoxes").textContent = S.boxes.filter(visible).length;
    const order = S.boxes.map((b, i) => i).sort((a, b) => S.boxes[a].b[0] - S.boxes[b].b[0]);
    for (const i of order) {
      const b = S.boxes[i], li = document.createElement("li");
      li.className = (i === S.sel ? "on" : "") + (visible(b) ? "" : " hid");
      const sw = document.createElement("i"); sw.style.background = color(b.c);
      const k = document.createElement("span"); k.className = "k"; k.textContent = S.proj.classes[b.c] || "class " + b.c;
      const s = document.createElement("span"); s.className = "s";
      s.textContent = (b.kind === "auto" ? (b.src === "bglabel" ? "bumper guess" : "auto " + (b.p != null ? b.p.toFixed(2) : "")) : b.kind) +
        " · " + Math.round(b.b[2] - b.b[0]) + "×" + Math.round(b.b[3] - b.b[1]);
      li.append(sw, k, s);
      li.addEventListener("click", () => { S.sel = i; changed(); });
      ol.append(li);
    }
    $("del").disabled = S.sel < 0;
    $("undo").disabled = !S.hist.length;
  }
  async function refreshStats() {
    let st;
    try { st = await api("/api/stats"); } catch (e) { return; }
    $("pReviewed").textContent = st.reviewed.toLocaleString(); $("pTotal").textContent = st.total.toLocaleString();
    const bars = $("pSplits"); bars.textContent = "";
    const cols = { train: "#0a9cff", val: "#8b6cff", test: "#e85d04", none: "#909094" };
    for (const [name, s] of Object.entries(st.splits)) {
      const row = document.createElement("div"); row.className = "bar";
      const l = document.createElement("span"); l.textContent = name;
      const t = document.createElement("span"); t.className = "t";
      const fill = document.createElement("i"); fill.style.width = (s.total ? (100 * s.reviewed / s.total) : 0).toFixed(1) + "%";
      fill.style.setProperty("--c", cols[name] || "#00c2b2"); t.append(fill);
      const n = document.createElement("span"); n.className = "n"; n.textContent = s.reviewed + " / " + s.total;
      row.append(l, t, n); bars.append(row);
    }
    const chips = $("pStatus"); chips.textContent = "";
    for (const [k, v] of Object.entries(st.status)) {
      if (!v) continue;
      const c = document.createElement("span"); c.className = "badge " + k; c.textContent = k + " " + v; chips.append(c);
    }
    const d = st.diff;
    $("pDiff").innerHTML = st.reviewed ? "vs auto: <b>+" + d.added + "</b> added · <b>−" + d.removed + "</b> removed · <b>" + d.moved + "</b> changed" : "";
  }

  // ---------- view / drawing
  function resize() {
    const r = stage.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(1, Math.round(r.width * dpr)); cv.height = Math.max(1, Math.round(r.height * dpr));
    const keep = S.view.s / (S.view.fit || 1);
    if (S.img) { const near = Math.abs(keep - 1) < 1e-3; fit(); if (!near) zoomAt(r.width / 2, r.height / 2, keep); }
    draw();
  }
  function fit() {
    if (!S.img) return;
    const r = stage.getBoundingClientRect(), W = S.img.naturalWidth, H = S.img.naturalHeight;
    const s = Math.min(r.width / W, r.height / H);
    S.view = { s: s, fit: s, x: (r.width - W * s) / 2, y: (r.height - H * s) / 2 };
    zoomLabel();
  }
  function zoomAt(sx, sy, factor) {
    if (!S.img) return;
    const v = S.view, ns = Math.max(v.fit, Math.min(v.fit * 12, v.s * factor));
    const ix = (sx - v.x) / v.s, iy = (sy - v.y) / v.s;
    v.s = ns; v.x = sx - ix * ns; v.y = sy - iy * ns; zoomLabel(); draw();
  }
  function zoomLabel() { $("zoomOut2").textContent = Math.round(100 * S.view.s / (S.view.fit || 1)) + "%"; }
  const toImg = (sx, sy) => [(sx - S.view.x) / S.view.s, (sy - S.view.y) / S.view.s];
  const toScr = (ix, iy) => [S.view.x + ix * S.view.s, S.view.y + iy * S.view.s];
  const color = (c) => (S.proj && S.proj.colors[c]) || "#ff7a00";

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
    if (!S.img) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = S.view.s / S.view.fit < 3;
    ctx.drawImage(S.img, S.view.x, S.view.y, S.img.naturalWidth * S.view.s, S.img.naturalHeight * S.view.s);
    if (S.hideAll) return;
    S.boxes.forEach((b, i) => { if (visible(b) && i !== S.sel) drawBox(b, false); });
    if (S.sel >= 0 && S.boxes[S.sel]) drawBox(S.boxes[S.sel], true);
    if (drag && drag.mode === "draw" && drag.box) drawBox(drag.box, true);
  }
  function drawBox(b, sel) {
    const [x0, y0] = toScr(b.b[0], b.b[1]), [x1, y1] = toScr(b.b[2], b.b[3]);
    const w = x1 - x0, h = y1 - y0, col = color(b.c);
    const edited = b.kind === "new" || isEdited(b);
    ctx.save();
    ctx.lineWidth = sel ? 3 : (b.kind === "review" || edited ? 2.6 : 2);
    ctx.setLineDash(b.kind === "auto" && !edited ? [7, 4] : []);
    ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth += 2; ctx.strokeRect(x0, y0, w, h);
    ctx.lineWidth -= 2; ctx.strokeStyle = col; ctx.strokeRect(x0, y0, w, h);
    if (sel) { ctx.fillStyle = hexA(col, 0.16); ctx.fillRect(x0, y0, w, h); }
    ctx.setLineDash([]);
    let tag = (S.proj.classes[b.c] || "?");
    if (b.kind === "auto" && !edited) tag += b.src === "bglabel" ? " · guess" : (b.p != null ? " " + b.p.toFixed(2).replace(/^0/, "") : "");
    ctx.font = labelFont || (labelFont = "600 11px " + getComputedStyle(document.body).fontFamily);
    const tw = ctx.measureText(tag).width + 8, ty = y0 - 16 < 0 ? y0 : y0 - 16;
    ctx.fillStyle = col; ctx.fillRect(x0 - 1, ty, tw, 16);
    ctx.fillStyle = "#000"; ctx.fillText(tag, x0 + 3, ty + 12);
    if (sel) for (const [hx, hy] of handles(b)) {
      const s = HANDLE() * 0.8;
      ctx.fillStyle = "#fff"; ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.fillRect(hx - s / 2, hy - s / 2, s, s); ctx.strokeRect(hx - s / 2, hy - s / 2, s, s);
    }
    ctx.restore();
  }
  function isEdited(b) {
    const o = S.base.find((x) => x.uid === b.uid);
    return !o || o.c !== b.c || o.b.some((v, k) => Math.abs(v - b.b[k]) > 0.5);
  }
  function hexA(hex, a) {
    let h = hex.replace("#", ""); if (h.length === 3) h = h.split("").map((x) => x + x).join("");
    const n = parseInt(h.slice(0, 6), 16);
    return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
  }
  // handle order: nw, n, ne, e, se, s, sw, w  (in screen space)
  function handles(b) {
    const [x0, y0] = toScr(b.b[0], b.b[1]), [x1, y1] = toScr(b.b[2], b.b[3]), mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
    return [[x0, y0], [mx, y0], [x1, y0], [x1, my], [x1, y1], [mx, y1], [x0, y1], [x0, my]];
  }
  function hit(sx, sy) {
    const R = HANDLE();
    if (S.sel >= 0 && visible(S.boxes[S.sel])) {
      const hs = handles(S.boxes[S.sel]);
      for (let k = 0; k < 8; k++) if (Math.abs(sx - hs[k][0]) <= R && Math.abs(sy - hs[k][1]) <= R) return { i: S.sel, h: k };
    }
    let best = -1, area = Infinity;
    S.boxes.forEach((b, i) => {
      if (!visible(b)) return;
      const [x0, y0] = toScr(b.b[0], b.b[1]), [x1, y1] = toScr(b.b[2], b.b[3]);
      if (sx >= x0 - 2 && sx <= x1 + 2 && sy >= y0 - 2 && sy <= y1 + 2) {
        const a = (x1 - x0) * (y1 - y0); if (a < area) { area = a; best = i; }
      }
    });
    return best >= 0 ? { i: best, h: -1 } : null;
  }

  // ---------- pointer input (mouse, pen, touch; two fingers = pinch / pan)
  let drag = null;
  const pts = new Map();
  const local = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  cv.addEventListener("contextmenu", (e) => e.preventDefault());
  cv.addEventListener("pointerdown", (e) => {
    if (!S.img) return;
    try { cv.setPointerCapture(e.pointerId); } catch (err) { /* synthetic or already released pointer */ }
    const [sx, sy] = local(e);
    pts.set(e.pointerId, [sx, sy]);
    if (pts.size === 2) {                                    // second finger: abandon the draw, start a pinch
      if (drag && drag.pushed && drag.mode === "draw") S.hist.pop();
      const [a, b] = [...pts.values()];
      drag = { mode: "pinch", d: Math.hypot(a[0] - b[0], a[1] - b[1]), c: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] };
      draw(); return;
    }
    if (pts.size > 2) return;
    if (e.button === 1 || e.button === 2 || S.space) { drag = { mode: "pan", sx, sy, vx: S.view.x, vy: S.view.y }; stage.classList.add("panning"); return; }
    const h = hit(sx, sy);
    if (h) {
      S.sel = h.i;
      drag = { mode: h.h >= 0 ? "resize" : "move", h: h.h, sx, sy, orig: S.boxes[h.i].b.slice(), moved: false, pushed: false };
      changed();
    } else {
      const [ix, iy] = toImg(sx, sy);
      drag = { mode: "draw", sx, sy, ix, iy, box: null, pushed: false };
      if (S.sel >= 0) { S.sel = -1; changed(); }
    }
  });
  cv.addEventListener("pointermove", (e) => {
    const [sx, sy] = local(e);
    S.ptr = { x: sx, y: sy };
    if (pts.has(e.pointerId)) pts.set(e.pointerId, [sx, sy]);
    if (!drag) { hover(sx, sy); return; }
    if (drag.mode === "pinch") {
      if (pts.size < 2) return;
      const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]), c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      S.view.x += c[0] - drag.c[0]; S.view.y += c[1] - drag.c[1];
      zoomAt(c[0], c[1], d / (drag.d || d)); drag.d = d; drag.c = c; return;
    }
    if (drag.mode === "pan") { S.view.x = drag.vx + sx - drag.sx; S.view.y = drag.vy + sy - drag.sy; draw(); return; }
    const far = Math.hypot(sx - drag.sx, sy - drag.sy) > 4;
    if (drag.mode === "draw") {
      if (!far && !drag.box) return;
      const [ix, iy] = toImg(sx, sy);
      drag.box = { c: S.cls, b: clip([drag.ix, drag.iy, ix, iy]), kind: "new", p: null, src: "review", uid: -1 };
      draw(); return;
    }
    if (!far && !drag.moved) return;
    if (!drag.pushed) { S.hist.push(snapshot()); S.fut = []; drag.pushed = true; }   // snapshot before the first change
    drag.moved = true;
    const b = S.boxes[S.sel], o = drag.orig, dx = (sx - drag.sx) / S.view.s, dy = (sy - drag.sy) / S.view.s;
    if (drag.mode === "move") {
      const W = S.img.naturalWidth, H = S.img.naturalHeight, w = o[2] - o[0], h = o[3] - o[1];
      const x0 = Math.max(0, Math.min(W - w, o[0] + dx)), y0 = Math.max(0, Math.min(H - h, o[1] + dy));
      b.b = [x0, y0, x0 + w, y0 + h];
    } else {
      const n = o.slice(), k = drag.h;
      if (k === 0 || k === 6 || k === 7) n[0] = o[0] + dx;
      if (k === 2 || k === 3 || k === 4) n[2] = o[2] + dx;
      if (k === 0 || k === 1 || k === 2) n[1] = o[1] + dy;
      if (k === 4 || k === 5 || k === 6) n[3] = o[3] + dy;
      b.b = clip(n);
    }
    draw(); updateDiff();
  });
  function endPointer(e) {
    pts.delete(e.pointerId);
    if (!drag) return;
    if (drag.mode === "pinch") { if (pts.size === 0) drag = null; return; }
    if (drag.mode === "draw" && drag.box) {
      const b = drag.box.b;
      if (b[2] - b[0] >= 3 && b[3] - b[1] >= 3) {
        push(); const nb = Object.assign({}, drag.box, { uid: S.uid++ }); S.boxes.push(nb); S.sel = S.boxes.length - 1;
      }
    }
    stage.classList.remove("panning");
    drag = null; changed();
  }
  cv.addEventListener("pointerup", endPointer);
  cv.addEventListener("pointercancel", endPointer);
  function hover(sx, sy) {
    const h = S.img ? hit(sx, sy) : null;
    stage.classList.toggle("over-handle", !!(h && h.h >= 0));
    stage.classList.toggle("over-box", !!(h && h.h < 0));
  }
  cv.addEventListener("wheel", (e) => {
    if (!S.img) return;
    e.preventDefault();
    const [sx, sy] = local(e);
    if (!e.ctrlKey && Math.abs(e.deltaX) > Math.abs(e.deltaY) * 0.6 && S.view.s > S.view.fit * 1.01) {   // trackpad two-finger pan
      S.view.x -= e.deltaX; S.view.y -= e.deltaY; draw(); return;
    }
    zoomAt(sx, sy, Math.exp(-e.deltaY * (e.ctrlKey ? 0.012 : 0.0022)));
  }, { passive: false });

  // ---------- keyboard
  function inField(e) { const t = e.target; return t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA"); }
  document.addEventListener("keydown", (e) => {
    if (!$("help").hidden) { if (e.key === "Escape" || e.key === "?") { e.preventDefault(); help(false); } return; }
    if (inField(e)) { if (e.key === "Enter" || e.key === "Escape") e.target.blur(); return; }
    const k = e.key, mod = e.ctrlKey || e.metaKey;
    if (mod && (k === "z" || k === "Z")) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if (mod && (k === "y" || k === "Y")) { e.preventDefault(); redo(); return; }
    if (mod) return;
    if (e.altKey && S.sel >= 0 && k.startsWith("Arrow")) {
      e.preventDefault(); const st = e.shiftKey ? 10 : 1, b = S.boxes[S.sel];
      const dx = k === "ArrowLeft" ? -st : k === "ArrowRight" ? st : 0, dy = k === "ArrowUp" ? -st : k === "ArrowDown" ? st : 0;
      push(); const W = S.img.naturalWidth, H = S.img.naturalHeight, w = b.b[2] - b.b[0], h = b.b[3] - b.b[1];
      const x0 = Math.max(0, Math.min(W - w, b.b[0] + dx)), y0 = Math.max(0, Math.min(H - h, b.b[1] + dy));
      b.b = [x0, y0, x0 + w, y0 + h]; changed(); return;
    }
    if (k === " ") { S.space = true; e.preventDefault(); return; }
    if (/^[1-9]$/.test(k)) { pickClass(+k - 1); return; }
    switch (k) {
      case "a": case "A": case "Enter": e.preventDefault(); save("accept"); break;
      case "s": case "S": save("skip"); break;
      case "b": case "B": save("bad"); break;
      case "ArrowRight": e.preventDefault(); go(S.pos + 1); break;
      case "ArrowLeft": e.preventDefault(); go(S.pos - 1); break;
      case "Delete": case "Backspace": e.preventDefault(); delSel(); break;
      case "Tab": {
        if (document.activeElement && document.activeElement !== document.body && document.activeElement !== cv) return;
        e.preventDefault();
        const vis = S.boxes.map((b, i) => i).filter((i) => visible(S.boxes[i]));
        if (vis.length) { const at = vis.indexOf(S.sel); S.sel = vis[(at + (e.shiftKey ? vis.length - 1 : 1)) % vis.length]; changed(); }
        break;
      }
      case "Escape": if (S.sel >= 0) { S.sel = -1; changed(); } break;
      case "z": case "Z":
        if (S.view.s > S.view.fit * 1.01) { fit(); draw(); } else zoomAt(S.ptr.x || cv.clientWidth / 2, S.ptr.y || cv.clientHeight / 2, 2.5);
        break;
      case "f": case "F": fit(); draw(); break;
      case "h": case "H": if (!S.hideAll) { S.hideAll = true; draw(); } break;
      case "r": case "R": resetBoxes(); break;
      case "?": help(true); break;
      default: return;
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.key === " ") S.space = false;
    if ((e.key === "h" || e.key === "H") && S.hideAll) { S.hideAll = false; draw(); }
  });
  function delSel() { if (S.sel < 0) return; push(); S.boxes.splice(S.sel, 1); S.sel = -1; changed(); }
  function resetBoxes() {
    if (!S.frame) return;
    push();
    const f = S.frame, src = f.auto ? [f.auto.boxes, "auto"] : f.import ? [f.import.boxes, "import"] : [[], "none"];
    S.boxes = (src[0] || []).map((b) => mk(b, src[1]));
    S.base = S.boxes.map((b) => ({ uid: b.uid, c: b.c, b: b.b.slice(), p: b.p, kind: b.kind }));
    S.baseKind = src[1]; S.prevDiff = null; S.prevStatus = null; S.sel = -1;
    changed(); toast("Back to the " + (src[1] === "none" ? "empty frame" : src[1] + " boxes"));
  }
  function help(on) { $("help").hidden = !on; if (on) $("helpClose").focus(); }

  // ---------- theme
  function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    $("themeBtn").title = "Theme: " + t + " (click to change)";
  }

  // ---------- wiring
  document.querySelectorAll(".q").forEach((b) => b.addEventListener("click", () => {
    if (S.q === b.dataset.q) return;
    S.q = b.dataset.q; store.set("csreview.q", S.q); markQueue(); restartQueue();
  }));
  $("who").addEventListener("change", (e) => {
    S.name = e.target.value.trim().slice(0, 40); store.set("csreview.name", S.name);
    if (S.q === "mine") restartQueue();
  });
  $("conf").addEventListener("input", (e) => {
    S.conf = parseFloat(e.target.value); $("confOut").textContent = S.conf.toFixed(2); store.set("csreview.conf", String(S.conf));
    if (S.sel >= 0 && !visible(S.boxes[S.sel])) S.sel = -1;
    changed();
  });
  $("accept").addEventListener("click", () => save("accept"));
  $("skip").addEventListener("click", () => save("skip"));
  $("bad").addEventListener("click", () => save("bad"));
  $("prev").addEventListener("click", () => go(S.pos - 1));
  $("next").addEventListener("click", () => go(S.pos + 1));
  $("undo").addEventListener("click", undo);
  $("reset").addEventListener("click", resetBoxes);
  $("del").addEventListener("click", delSel);
  $("fitBtn").addEventListener("click", () => { fit(); draw(); });
  $("zoomIn").addEventListener("click", () => zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1.5));
  $("zoomOut").addEventListener("click", () => zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1 / 1.5));
  $("helpBtn").addEventListener("click", () => help(true));
  $("helpClose").addEventListener("click", () => help(false));
  $("help").addEventListener("click", (e) => { if (e.target === $("help")) help(false); });
  $("themeBtn").addEventListener("click", () => {
    const order = ["auto", "light", "dark"], t = order[(order.indexOf(document.documentElement.dataset.theme) + 1) % 3];
    applyTheme(t); store.set("csreview.theme", t); toast("Theme: " + t);
  });
  new ResizeObserver(() => resize()).observe(stage);
  boot();
})();
