/* Label review client: queue -> frame -> edit boxes on a canvas -> save labels/review/<id>.json. No framework. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const cv = $("cv"), stage = $("stage"), ctx = cv.getContext("2d");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: not remembered */ } },
  };
  const CONF0 = 0.25;              // the slider's start: auto boxes at or above it are "the auto labels" the diff compares to
  const QNAME = { uncertain: "Uncertain", gold: "the Gold set", random: "Random", mine: "Mine", all: "All" };
  const S = {
    proj: null, q: "uncertain", seed: Math.floor(Math.random() * 1e9), filters: { event: "", match: "", split: "" },
    list: [], pos: -1, seen: new Set(), more: true, loading: false, total: null,
    frame: null, img: null, boxes: [], base: [], baseKind: "none", prevStatus: null, prevDiff: null, note: "",
    viewFix: null, view0: null,
    sel: -1, cls: 0, conf: CONF0, hist: [], fut: [], view: { s: 1, x: 0, y: 0, fit: 1 },
    hideAll: false, space: false, ptr: { x: 0, y: 0 }, name: "", busy: false, uid: 1,
    drafts: new Map(), last: null, confirm: false, session: 0, nudged: false, zoomHint: false,
  };
  let labelFont = "";
  const HANDLE = () => (matchMedia("(pointer: coarse)").matches ? 16 : 9);
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
  const fmtConf = (p) => p.toFixed(2).replace(/^0/, "");
  // the server keeps only these characters of a name, so the UI uses the same rule (Mine matches what was stored)
  const cleanName = (s) => String(s || "").replace(/[^\p{L}\p{N}_ .@'-]/gu, "").trim().slice(0, 40);

  // ---------- network
  async function api(path, opt) {
    const r = await fetch(path, Object.assign({ credentials: "same-origin" }, opt || {}));
    let d = null;
    try { d = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) throw new Error((d && d.error) || ("HTTP " + r.status));
    return d;
  }
  const imgUrl = (id) => "/img/" + encodeURIComponent(id);
  const post = (id, body) => api("/api/review/" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ---------- toast (top of the stage; a save's toast carries Undo)
  let toastT = 0;
  function toast(msg, err, undo) {
    const t = $("toast");
    $("toastMsg").textContent = msg; $("toastUndo").hidden = !undo;
    t.className = "toast show" + (err ? " err" : "") + (undo ? " act" : "");
    clearTimeout(toastT);
    toastT = setTimeout(hideToast, err ? 4000 : undo ? 6000 : 1600);
  }
  function hideToast() { $("toast").className = "toast"; $("toastUndo").hidden = true; }

  // ---------- project, filters, classes
  async function boot() {
    applyTheme(store.get("csreview.theme") || "auto");
    S.name = cleanName(store.get("csreview.name"));
    $("who").value = S.name;
    confLabel();                                   // the slider is not remembered: every visit starts at 0.25
    const q = store.get("csreview.q");
    if (q && document.querySelector('.q[data-q="' + q + '"]')) S.q = q;
    if (!store.get("csreview.guide")) $("helpBtn").classList.add("new");
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
  const evName = (e) => (e.startsWith("pub_") ? "Public set: " + e.slice(4) : e || "(none)");
  const natural = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
  function opt(sel, v, label) { const o = document.createElement("option"); o.value = v; o.textContent = label; sel.append(o); }
  function buildFilters() {
    const ev = $("fEvent"), sp = $("fSplit");
    Object.keys(S.proj.events).forEach((e) => opt(ev, e, evName(e)));
    S.proj.splits.filter((x) => x).forEach((s) => opt(sp, s, s));
    fillMatches();
    // a select keeps focus after a mouse pick and would swallow the shortcut keys: give it back to the page
    ev.addEventListener("change", () => { S.filters.event = ev.value; S.filters.match = ""; fillMatches(); ev.blur(); restartQueue(); });
    $("fMatch").addEventListener("change", (e) => { S.filters.match = e.target.value; e.target.blur(); restartQueue(); });
    sp.addEventListener("change", () => { S.filters.split = sp.value; sp.blur(); restartQueue(); });
  }
  function fillMatches() {
    const m = $("fMatch"); m.textContent = ""; opt(m, "", "All");
    const ev = S.filters.event;
    const ms = ev ? (S.proj.events[ev] || []) : [...new Set(Object.values(S.proj.events).flat())].sort(natural);
    ms.forEach((x) => opt(m, x, x || "(none)"));
    m.value = S.filters.match;
    m.disabled = ms.length === 0;
  }
  function clearFilters() {
    S.filters = { event: "", match: "", split: "" };
    $("fEvent").value = ""; $("fSplit").value = ""; fillMatches();
    restartQueue();
  }
  function markQueue() {
    document.querySelectorAll(".q").forEach((b) => { b.classList.toggle("on", b.dataset.q === S.q); b.setAttribute("aria-pressed", b.dataset.q === S.q); });
  }

  // ---------- queue
  const shrinking = () => S.q !== "mine" && S.q !== "all";      // unreviewed queues: a save takes the frame out
  async function fetchPage(offset) {
    const p = new URLSearchParams({ q: S.q, seed: S.seed, offset: offset, limit: 1000 });
    for (const k in S.filters) if (S.filters[k]) p.set(k, S.filters[k]);
    if (S.q === "mine") p.set("by", S.name);
    return api("/api/queue?" + p);
  }
  async function restartQueue() {
    leaveFrame();
    S.list = []; S.pos = -1; S.seen = new Set(); S.more = true; S.total = null;
    if (S.q === "mine" && !S.name) { showEmpty("Type your name (Reviewer, top right) to see the frames you reviewed."); return; }
    await loadMore();
    if (S.list.length) await go(0); else showEmpty(...emptyText());
  }
  function emptyText() {
    if (S.filters.event || S.filters.match || S.filters.split) return ["No frames in " + QNAME[S.q] + " match these filters.", true];
    if (S.list.length) {                                         // walked past the end of the queue
      const left = S.list.filter((it) => !it.status).length;
      return ["End of " + QNAME[S.q] + "." + (shrinking() && left ? " The " + plural(left, "frame", "frames") +
        " you skipped come back when you reload the page." : ""), false];
    }
    return [{ uncertain: "Nothing left to review here. Nice.", gold: "The gold set is fully reviewed.",
      random: "Nothing left to review here.", mine: "You have not reviewed anything yet as " + S.name + ".",
      all: "No frames in this project yet." }[S.q], false];
  }
  // Unreviewed queues shrink as you save, so they are re-read from the top and frames already visited are dropped.
  async function loadMore() {
    if (S.loading || !S.more) return;
    S.loading = true;
    try {
      let offset = shrinking() ? 0 : S.list.length, added = 0;
      for (let guard = 0; guard < 50; guard++) {
        const d = await fetchPage(offset);
        if (offset === 0 || !shrinking()) S.total = d.total;
        for (const it of d.items) if (!S.seen.has(it.id)) { S.seen.add(it.id); S.list.push(it); added++; }
        offset += d.items.length;
        if (added || offset >= d.total || !d.items.length) { S.more = offset < d.total; break; }
      }
      if (!added) S.more = false;
    } catch (e) { toast("Queue: " + e.message, true); } finally { S.loading = false; }
  }

  // the frame on screen, once it is the one at S.pos (while the next one loads, nothing may edit or save)
  const cur = () => (S.frame && S.list[S.pos] && S.frame.frame.id === S.list[S.pos].id ? S.frame : null);
  async function go(pos) {
    if (pos < 0 || S.confirm) return;
    if (pos >= S.list.length - 3) await loadMore();
    leaveFrame();
    if (pos >= S.list.length) { S.pos = S.list.length; showEmpty(...emptyText()); return; }
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
  function showEmpty(msg, withClear) {
    leaveFrame();
    S.frame = null; S.img = null; S.boxes = []; S.base = []; S.sel = -1; S.note = "";
    $("emptyMsg").textContent = msg; $("clearFilters").hidden = !withClear; $("empty").hidden = false;
    draw(); updateInfo();
  }

  // ---------- drafts: edits are never thrown away by moving on; they wait in memory until saved or the page closes
  function editorState() {
    return { boxes: S.boxes.map(copyBox), base: S.base.map(copyBox), baseKind: S.baseKind, prevStatus: S.prevStatus,
      prevDiff: S.prevDiff, hist: S.hist.slice(), viewFix: S.viewFix, view0: S.view0 };
  }
  const dirty = () => !!(S.frame && S.img && (editCount() || S.viewFix !== S.view0));
  function leaveFrame() {
    const f = S.frame;
    if (!f || f.left) return;
    f.left = true;
    const id = f.frame.id;
    if (dirty()) {
      const item = S.list.find((it) => it.id === id) || { id: id };
      S.drafts.set(id, { item: item, state: editorState(), title: frameTitle(f.frame) });
      toast("Not saved: your edits on " + frameTitle(f.frame) + " wait as a draft (Drafts, side panel)");
    }
    updateDrafts();
  }
  function updateDrafts() {
    const el = $("drafts"); el.textContent = "";
    const ids = [...S.drafts.keys()].filter((id) => !(cur() && cur().frame.id === id));
    el.hidden = !ids.length;
    if (!ids.length) return;
    const t = document.createElement("span");
    t.textContent = "Unsaved drafts (lost when you close the page):";
    el.append(t);
    for (const id of ids.slice(0, 6)) {
      const b = document.createElement("button"); b.className = "chip"; b.textContent = S.drafts.get(id).title;
      b.title = "Open " + id; b.addEventListener("click", () => openDraft(id));
      el.append(b);
    }
    if (ids.length > 6) { const m = document.createElement("span"); m.textContent = "+" + (ids.length - 6) + " more"; el.append(m); }
  }
  function openDraft(id) {
    const dr = S.drafts.get(id);
    if (!dr) return;
    let at = S.list.findIndex((it) => it.id === id);
    if (at < 0) { at = Math.min(S.pos + 1, S.list.length); S.list.splice(at, 0, dr.item); S.seen.add(id); }
    go(at);
  }
  window.addEventListener("beforeunload", (e) => {
    if (S.drafts.size || dirty()) { e.preventDefault(); e.returnValue = ""; }
  });

  // ---------- boxes
  function mk(b, kind) {
    const src = b.src || kind;
    return { c: b.c | 0, b: b.b.map(Number), p: typeof b.p === "number" ? b.p : null, src: src, kind: kind, uid: S.uid++,
      guess: kind === "auto" && String(src).startsWith("bglabel") };       // a bumper-colour proposal, not a label
  }
  const copyBox = (b) => Object.assign({}, b, { b: b.b.slice() });
  // what the frame starts from when it has no usable review: imported human labels, then auto, as build picks them
  const origin = (f) => (f.import ? [f.import.boxes || [], "import"] : f.auto ? [f.auto.boxes || [], "auto"] : [[], "none"]);
  function loadBoxes() {
    const f = S.frame, id = f.frame.id, dr = S.drafts.get(id);
    S.sel = -1; S.fut = [];
    if (dr) {
      Object.assign(S, dr.state); S.drafts.delete(id); updateDrafts();
      S.note = editCount() || S.viewFix !== S.view0 ? "Your unsaved edits are back. Accept to save them." : noteFor(f);
      return;
    }
    S.prevStatus = f.review ? f.review.status : null;
    S.prevDiff = f.review && f.review.diff ? f.review.diff : null;
    S.view0 = S.viewFix = f.review && f.review.view ? f.review.view : null;
    const kept = f.review && (f.review.status === "ok" || f.review.status === "fixed");
    if (kept) { S.baseKind = "review"; S.boxes = (f.review.boxes || []).map((b) => mk(b, "review")); }
    else { const [bx, kind] = origin(f); S.baseKind = kind; S.boxes = bx.map((b) => mk(b, kind)); }
    S.base = S.boxes.map(copyBox);
    S.hist = [];
    S.note = noteFor(f);
  }
  function noteFor(f) {
    const rv = f.review;
    if (rv && (rv.status === "bad" || rv.status === "skip")) {
      return (rv.status === "bad" ? "Marked bad" : "Skipped (kept out)") + " by " + rv.by + ". Shown: the " +
        (S.baseKind === "none" ? "empty frame" : { auto: "auto", import: "imported" }[S.baseKind] + " boxes") +
        ". Accept saves them as labels; Bad keeps the frame out.";
    }
    if (S.baseKind === "none") return "No labels yet: draw a box around every robot. Nothing to box? Accept, then N.";
    if (!store.get("csreview.guide")) return "New here? Open the Guide (?) first: what to box, and what the buttons do.";
    return "";
  }
  // shown on the canvas: guesses always; teacher boxes from the slider up
  const shown = (b) => b.guess || !(b.kind === "auto" && b.p != null && b.p < S.conf);
  const kept = (b) => shown(b) && !b.guess;                                  // what a save writes
  const baseKept = (b) => !b.guess && !(b.kind === "auto" && b.p != null && b.p < CONF0);
  function snapshot() { return { boxes: S.boxes.map(copyBox), sel: S.sel }; }
  function push() { S.hist.push(snapshot()); if (S.hist.length > 200) S.hist.shift(); S.fut = []; }
  function undo() { if (!S.hist.length) return; S.fut.push(snapshot()); const s = S.hist.pop(); S.boxes = s.boxes; S.sel = s.sel; changed(); }
  function redo() { if (!S.fut.length) return; S.hist.push(snapshot()); const s = S.fut.pop(); S.boxes = s.boxes; S.sel = s.sel; changed(); }
  function changed() { updateBoxes(); updateDiff(); draw(); }
  const movedFrom = (o, b) => o.c !== b.c || o.b.some((v, k) => Math.abs(v - b.b[k]) > 0.5);
  // the label a save writes vs the frame's starting label (auto boxes from 0.25 up): what "ok" / "fixed" mean
  function diff() {
    const base = new Map(S.base.filter(baseKept).map((b) => [b.uid, b]));
    const now = S.boxes.filter(kept);
    let added = 0, moved = 0;
    for (const b of now) {
      const o = base.get(b.uid);
      if (!o) added++;
      else if (movedFrom(o, b)) moved++;
    }
    const ids = new Set(now.map((b) => b.uid));
    let removed = 0;
    for (const uid of base.keys()) if (!ids.has(uid)) removed++;
    return { added, removed, moved };
  }
  // the reviewer's own edits, whatever the slider shows (a draft is kept when this is non-zero)
  function editCount() {
    const base = new Map(S.base.map((b) => [b.uid, b]));
    let n = 0;
    for (const b of S.boxes) { const o = base.get(b.uid); if (!o || o.guess !== b.guess || movedFrom(o, b)) n++; }
    const ids = new Set(S.boxes.map((b) => b.uid));
    for (const uid of base.keys()) if (!ids.has(uid)) n++;
    return n;
  }
  const hiddenAuto = () => S.boxes.filter((b) => !b.guess && !shown(b));     // left out of a save by the slider
  const guesses = () => S.boxes.filter((b) => b.guess);
  function updateDiff() {
    const el = $("diffLine");
    if (!cur()) { el.textContent = ""; return; }
    const d = diff(), hid = hiddenAuto(), bySlider = hid.filter(baseKept).length, faint = hid.length - bySlider;
    const parts = [];
    if (d.added) parts.push("<b>+" + d.added + "</b> added");
    if (d.removed) parts.push("<b>−" + d.removed + "</b> removed" + (bySlider ? " (" + bySlider + " hidden by the slider)" : ""));
    if (d.moved) parts.push("<b>" + d.moved + "</b> changed");
    let html = parts.length ? parts.join(" · ") : "Unchanged from " + ({ auto: "the auto labels", import: "the imported labels", review: "your saved labels", none: "an empty frame" }[S.baseKind]);
    const notes = [];
    if (faint) notes.push(plural(faint, "faint auto box", "faint auto boxes") + " (under " + fmtConf(Math.min(S.conf, CONF0)) + ") hidden and not saved");
    const g = guesses().length;
    if (g) notes.push(plural(g, "bumper guess", "bumper guesses") + " not saved unless you keep one (select it, K)");
    if (S.viewFix !== S.view0) notes.push("view: " + viewName(S.viewFix || viewOf(S.frame)) + " (changed)");
    if (notes.length) html += '<span class="sub">' + notes.join(" · ") + "</span>";
    el.innerHTML = html;
  }
  function clip(b) {
    const W = S.img.naturalWidth, H = S.img.naturalHeight;
    let [x0, y0, x1, y1] = b;
    if (x0 > x1) [x0, x1] = [x1, x0];
    if (y0 > y1) [y0, y1] = [y1, y0];
    return [Math.max(0, Math.min(W, x0)), Math.max(0, Math.min(H, y0)), Math.max(0, Math.min(W, x1)), Math.max(0, Math.min(H, y1))];
  }
  function keepGuess(b) { b.guess = false; b.kind = "new"; b.src = "review"; b.p = null; }

  // ---------- saving
  async function save(status, noRobots) {
    const f = cur();
    if (!f || S.busy || S.confirm) return;
    if (!S.name) { $("who").focus(); toast("Type your name first: it goes on every label", true); return; }
    const keep = S.boxes.filter(kept);
    if (status === "accept" && !keep.length && !noRobots) { askNoRobots(); return; }
    const d = diff(), edited = d.added + d.removed + d.moved > 0;
    if (status === "accept") status = edited ? "fixed" : (S.baseKind === "review" && S.prevStatus === "fixed" ? "fixed" : "ok");
    if (S.prevDiff && S.baseKind === "review") for (const k in d) d[k] += S.prevDiff[k] || 0;
    const boxes = keep.map((b) => ({ c: b.c, b: clip(b.b).map((v) => Math.round(v * 10) / 10) }));
    const body = { boxes, status, by: S.name, conf: S.conf, diff: d, hidden: hiddenAuto().length };
    if (S.viewFix) body.view = S.viewFix;
    const id = f.frame.id, pos = S.pos, it = S.list[pos], prev = f.review, before = editorState();
    S.busy = true;
    try {
      await post(id, body);
      it.status = status; S.session++;
      if (!prev && shrinking() && S.total) S.total--;
      S.hist = []; S.base = S.boxes.map(copyBox); S.view0 = S.viewFix;        // saved: nothing left to keep as a draft
      S.last = { id, it, prev, before, title: frameTitle(f.frame) };
      toast(noRobots ? "Saved: no robots in this frame" : { ok: "Accepted", fixed: "Saved with fixes", bad: "Marked bad: kept out of training" }[status], false, true);
      refreshStats();
      S.busy = false;
      await go(pos + 1);
    } catch (e) { toast("Not saved: " + e.message, true); } finally { S.busy = false; }
  }
  // Undo of the last save: the review file goes back to what it was (or away), the frame reopens with the edits
  async function undoSave() {
    const L = S.last;
    if (!L || S.busy || S.confirm) return;
    S.last = null; hideToast(); S.busy = true;
    try {
      const p = L.prev;
      if (p) await post(L.id, { status: p.status, by: p.by, boxes: p.boxes || [], diff: p.diff, conf: p.conf, hidden: p.hidden, view: p.view });
      else await api("/api/review/" + encodeURIComponent(L.id), { method: "DELETE" });
      L.it.status = p ? p.status : null; S.session = Math.max(0, S.session - 1);
      if (!p && shrinking() && S.total != null) S.total++;
      refreshStats();
      S.busy = false;
      leaveFrame();                                        // the frame on screen keeps its own edits as a draft
      S.drafts.set(L.id, { item: L.it, state: L.before, title: L.title });
      let at = S.list.indexOf(L.it);
      if (at < 0) { at = Math.max(0, S.pos); S.list.splice(at, 0, L.it); }
      await go(at);
      toast("Undone: " + L.title + " is not saved");
    } catch (e) { toast("Undo failed: " + e.message, true); } finally { S.busy = false; }
  }
  function askNoRobots() {
    const hid = hiddenAuto().length, g = guesses().length, extra = [];
    if (hid) extra.push(plural(hid, "auto box is", "auto boxes are") + " hidden by the slider (under " + fmtConf(S.conf) + ") and would not be saved.");
    if (g) extra.push(plural(g, "bumper guess is", "bumper guesses are") + " not kept.");
    $("cfExtra").textContent = extra.join(" "); $("cfExtra").hidden = !extra.length;
    S.confirm = true; $("confirm").hidden = false; $("confirm").focus();
  }
  function closeConfirm() { S.confirm = false; $("confirm").hidden = true; cv.focus({ preventScroll: true }); }

  // ---------- info panels
  const fmtT = (t) => (typeof t === "number" ? t.toFixed(1) + " s" : "–");
  const frameTitle = (r) => (r.event && r.event.startsWith("pub_") ? "Public set " + r.event.slice(4) : [r.match || r.video || r.id].filter(Boolean).join(" ") || r.id) +
    (typeof r.t === "number" && !(r.event || "").startsWith("pub_") ? " · " + fmtT(r.t) : "");
  const viewOf = (f) => (f && f.frame.view) || "?";
  const viewName = (v) => ({ F: "fixed camera", M: "moving camera", "?": "unknown" }[v] || v);
  function teacherText(t) {                          // "robot+new@tta@1280" -> "2 models (robot, new)"
    const m = String(t || "").split("@")[0].split("+").filter(Boolean);
    return m.length ? plural(m.length, "model", "models") + " (" + m.join(", ") + ")" : "–";
  }
  function uText(u) { return (u >= 0.75 ? "high" : u >= 0.4 ? "medium" : "low") + " (" + u.toFixed(2) + ")"; }
  function updateInfo() {
    const it = S.list[S.pos], f = cur();
    const pos = [];
    if (S.list.length && S.total != null) {
      pos.push(shrinking() ? S.total.toLocaleString() + " left" : Math.min(S.pos + 1, S.list.length).toLocaleString() + " / " + S.total.toLocaleString());
    }
    if (S.session) pos.push(S.session + " saved this session");
    $("fPos").textContent = pos.join(" · ") || "–";
    const st = $("fStatus"), status = f && f.review ? f.review.status : (f ? "new" : "");
    st.textContent = status; st.className = "badge " + status; st.hidden = !status;
    for (const id of ["accept", "skip", "bad", "reset"]) $(id).disabled = !f;
    $("prev").disabled = S.pos <= 0;
    const meta = $("fMeta"); meta.textContent = "";
    note();
    if (!f) { $("fTitle").textContent = "–"; updateBoxes(); updateDiff(); updateDrafts(); return; }
    const r = f.frame;
    $("fTitle").textContent = frameTitle(r);
    const row = (k, v, cls, tip) => {
      if (v == null || v === "") return null;
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = k; if (v instanceof Node) dd.append(v); else dd.textContent = v;
      if (cls) dd.className = cls;
      if (tip) dd.title = tip;
      meta.append(dt, dd); return dd;
    };
    row("Event", r.event ? evName(r.event) : null);
    if (!(r.event || "").startsWith("pub_")) row("Time", fmtT(r.t));
    row("View", viewControl(r));
    row("Split", r.split, null, r.split === "test" || r.split === "val" ? "Gold set: the model is scored on this frame" : null);
    row("Source", { upload: "uploaded video", livestream: "livestream recording", public: "public data set" }[r.src] || r.src);
    row("Size", r.w && r.h ? r.w + " × " + r.h : null);
    if (f.import) row("Labels", "imported (human)", null, f.import.boxes ? plural(f.import.boxes.length, "box", "boxes") : null);
    if (f.auto) {
      row("Auto labels", teacherText(f.auto.teacher), null, "Fused from " + (f.auto.teacher || "?"));
      const u = typeof f.auto.u === "number" ? f.auto.u : (it && it.u != null ? it.u : null);
      if (u != null) row("Uncertainty", uText(u), null, "How unsure the models are about this frame, 0 (sure) to 1 (very unsure). The Uncertain queue shows the most unsure frames first.");
    }
    if (f.review) row("Reviewed by", f.review.by + " · " + new Date(f.review.at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }));
    row("Frame id", r.id, "id");
    updateBoxes(); updateDiff(); updateDrafts();
  }
  // F / M toggle: the reviewer can correct a wrong camera tag; it is saved with the review (eval reports per view)
  function viewControl(r) {
    const wrap = document.createElement("span"); wrap.className = "seg";
    const now = S.viewFix || r.view || "?";
    for (const [v, label] of [["F", "Fixed"], ["M", "Moving"]]) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = label;
      b.setAttribute("aria-pressed", now === v ? "true" : "false"); if (now === v) b.className = "on";
      b.title = v === "F" ? "The event's fixed overhead camera" : "A moving / broadcast camera (program feed, close-ups)";
      b.addEventListener("click", () => { S.viewFix = v === (r.view || "?") ? null : v; updateInfo(); });
      wrap.append(b);
    }
    if (now === "?") { const q = document.createElement("span"); q.className = "seg-q"; q.textContent = "unknown"; wrap.append(q); }
    if (S.viewFix && S.viewFix !== r.view) { const c = document.createElement("span"); c.className = "seg-q"; c.textContent = "was " + viewName(r.view || "?"); wrap.append(c); }
    return wrap;
  }
  function note() {
    const el = $("note"), msg = cur() && !S.hideAll ? S.note : "";
    if (el.textContent !== msg) el.textContent = msg;
    el.hidden = !msg;
  }
  function updateBoxes() {
    const ol = $("boxList"); ol.textContent = "";
    $("nBoxes").textContent = S.boxes.filter(kept).length;
    const order = S.boxes.map((b, i) => i).sort((a, b) => S.boxes[a].b[0] - S.boxes[b].b[0]);
    for (const i of order) {
      const b = S.boxes[i], li = document.createElement("li");
      li.className = (i === S.sel ? "on" : "") + (shown(b) ? "" : " hid") + (b.guess ? " guess" : "");
      const sw = document.createElement("i"); sw.style.background = b.guess ? "var(--guess)" : color(b.c);
      const k = document.createElement("span"); k.className = "k"; k.textContent = S.proj.classes[b.c] || "class " + b.c;
      const s = document.createElement("span"); s.className = "s";
      s.textContent = (b.guess ? "guess" : b.kind === "auto" ? "auto " + (b.p != null ? b.p.toFixed(2) : "") + (shown(b) ? "" : ", hidden") : b.kind) +
        " · " + Math.round(b.b[2] - b.b[0]) + "×" + Math.round(b.b[3] - b.b[1]);
      li.append(sw, k, s);
      if (b.guess) {
        li.title = "Bumper-colour guess: not saved unless you keep it";
        const kb = document.createElement("button"); kb.className = "keep"; kb.textContent = "Keep";
        kb.title = "Keep this guess as a robot box (K)";
        kb.addEventListener("click", (e) => { e.stopPropagation(); push(); keepGuess(b); S.sel = i; changed(); });
        li.append(kb);
      }
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
      const n = document.createElement("span"); n.className = "n"; n.textContent = s.reviewed.toLocaleString() + " / " + s.total.toLocaleString();
      row.append(l, t, n); bars.append(row);
    }
    const chips = $("pStatus"); chips.textContent = "";
    for (const [k, v] of Object.entries(st.status)) {
      if (!v) continue;
      const c = document.createElement("span"); c.className = "badge " + k; c.textContent = k + " " + v; chips.append(c);
    }
    const mine = S.name && st.people ? st.people[S.name] || 0 : 0;
    $("pMine").textContent = S.name ? "By you (" + S.name + "), all sessions: " + mine.toLocaleString() : "";
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
  const GUESS = "#d4d4d8";

  let tags = [];                                     // tag rectangles placed so far in this draw (overlap check)
  function draw() {
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
    note();
    if (!S.img) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = S.view.s / S.view.fit < 3;
    ctx.drawImage(S.img, S.view.x, S.view.y, S.img.naturalWidth * S.view.s, S.img.naturalHeight * S.view.s);
    if (S.hideAll) return;
    tags = [];
    const sb = S.sel >= 0 ? S.boxes[S.sel] : null, at = sb ? reserveTag(sb) : null;   // the selected box's tag wins its spot
    S.boxes.forEach((b, i) => { if (shown(b) && i !== S.sel) drawBox(b, false); });
    if (sb) drawBox(sb, true, at);
    if (drag && drag.mode === "draw" && drag.box) drawBox(drag.box, true);
  }
  function tagText(b, edited) {
    const multi = S.proj.classes.length > 1, name = S.proj.classes[b.c] || "?";
    if (b.guess) return "guess?";
    if (b.kind === "auto" && !edited) return (multi ? name + " " : "") + (b.p != null ? fmtConf(b.p) : "auto");
    return multi ? name : "";                        // one class: a reviewed box needs no tag
  }
  function reserveTag(b) {
    const tag = tagText(b, b.kind === "new" || isEdited(b));
    if (!tag) return null;
    ctx.font = labelFont || (labelFont = "600 10px " + getComputedStyle(document.body).fontFamily);
    const [x0, y0] = toScr(b.b[0], b.b[1]);
    return placeTag(x0, y0, ctx.measureText(tag).width + 7);
  }
  function drawBox(b, sel, tagAt) {
    const [x0, y0] = toScr(b.b[0], b.b[1]), [x1, y1] = toScr(b.b[2], b.b[3]);
    const w = x1 - x0, h = y1 - y0, col = b.guess ? GUESS : color(b.c);
    const edited = b.kind === "new" || isEdited(b);
    const tag = tagText(b, edited);
    ctx.save();
    ctx.font = labelFont || (labelFont = "600 10px " + getComputedStyle(document.body).fontFamily);
    ctx.lineWidth = sel ? 3 : (b.kind === "review" || edited ? 2.6 : 2);
    ctx.setLineDash(b.guess ? [3, 4] : b.kind === "auto" && !edited ? [7, 4] : []);
    ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth += 2; ctx.strokeRect(x0, y0, w, h);
    ctx.lineWidth -= 2; ctx.strokeStyle = col; ctx.strokeRect(x0, y0, w, h);
    if (sel) { ctx.fillStyle = hexA(col, 0.16); ctx.fillRect(x0, y0, w, h); }
    ctx.setLineDash([]);
    if (tag) {
      const tw = ctx.measureText(tag).width + 7;
      const [tx, ty] = tagAt || placeTag(x0, y0, tw);
      ctx.fillStyle = col; ctx.fillRect(tx, ty, tw, 13);
      ctx.fillStyle = "#000"; ctx.fillText(tag, tx + 3.5, ty + 10);
    }
    if (sel) for (const [hx, hy] of handles(b)) {
      const s = HANDLE() * 0.8;
      ctx.fillStyle = "#fff"; ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.fillRect(hx - s / 2, hy - s / 2, s, s); ctx.strokeRect(hx - s / 2, hy - s / 2, s, s);
    }
    ctx.restore();
  }
  // a tag sits above its box; when that spot is taken (a neighbour's tag) or off screen it goes inside the box
  function placeTag(x0, y0, tw) {
    const H = 13, over = (x, y) => tags.some((t) => x < t[0] + t[2] && x + tw > t[0] && y < t[1] + H && y + H > t[1]);
    let x = x0 - 1, y = y0 - H;
    if (y < 0 || over(x, y)) { x = x0 + 1; y = y0 + 1; }
    tags.push([x, y, tw]);
    return [x, y];
  }
  function isEdited(b) {
    const o = S.base.find((x) => x.uid === b.uid);
    return !o || movedFrom(o, b);
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
    if (S.sel >= 0 && shown(S.boxes[S.sel])) {
      const hs = handles(S.boxes[S.sel]);
      for (let k = 0; k < 8; k++) if (Math.abs(sx - hs[k][0]) <= R && Math.abs(sy - hs[k][1]) <= R) return { i: S.sel, h: k };
    }
    let best = -1, area = Infinity;
    S.boxes.forEach((b, i) => {
      if (!shown(b)) return;
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
    if (!cur() || S.confirm) return;
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
    const h = e.shiftKey ? null : hit(sx, sy);              // Shift: always draw (a robot whose corner is in a neighbour's box)
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
    if (!drag) { hover(sx, sy, e.shiftKey); return; }
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
      drag.box = { c: S.cls, b: clip([drag.ix, drag.iy, ix, iy]), kind: "new", p: null, src: "review", uid: -1, guess: false };
      draw(); return;
    }
    if (!far && !drag.moved) return;
    const b = S.boxes[S.sel];
    if (!drag.pushed) {                                     // snapshot before the first change
      S.hist.push(snapshot()); S.fut = []; drag.pushed = true;
      if (b.guess) { keepGuess(b); updateBoxes(); }         // adjusting a guess to fit a robot keeps it
    }
    drag.moved = true;
    const o = drag.orig, dx = (sx - drag.sx) / S.view.s, dy = (sy - drag.sy) / S.view.s;
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
  function hover(sx, sy, shift) {
    const h = S.img && !shift ? hit(sx, sy) : null;
    stage.classList.toggle("over-handle", !!(h && h.h >= 0));
    stage.classList.toggle("over-box", !!(h && h.h < 0));
  }
  // Pinch (ctrl+wheel) zooms. A trackpad's two-finger scroll pans when zoomed in; a mouse wheel zooms.
  let padUntil = 0;
  function trackpadLike(e) {
    if (e.deltaMode !== 0) return false;                          // lines / pages: a mouse wheel (Firefox)
    if (performance.now() < padUntil) return true;                // the rest of the gesture, momentum included
    if (e.deltaX !== 0) return true;
    const w = e.wheelDeltaY;                                      // Chrome / Safari: -3 × deltaY on a trackpad
    if (typeof w === "number" && w !== 0) { if (Math.abs(w + 3 * e.deltaY) < 1.5) return true; if (w % 120 === 0) return false; }
    return Math.abs(e.deltaY) < 50 || !Number.isInteger(e.deltaY);
  }
  cv.addEventListener("wheel", (e) => {
    if (!S.img) return;
    e.preventDefault();
    const [sx, sy] = local(e);
    if (e.ctrlKey) { zoomAt(sx, sy, Math.exp(-Math.max(-60, Math.min(60, e.deltaY)) * 0.012)); return; }
    if (trackpadLike(e)) {
      padUntil = performance.now() + 250;
      if (S.view.s > S.view.fit * 1.01) { S.view.x -= e.deltaX; S.view.y -= e.deltaY; draw(); }
      else if (!S.zoomHint) { S.zoomHint = true; toast("Pinch (or Ctrl+scroll) to zoom; scrolling pans once you are zoomed in"); }
      return;
    }
    zoomAt(sx, sy, Math.exp(-e.deltaY * 0.0022));
  }, { passive: false });

  // ---------- keyboard
  const typing = (t) => t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && !/^(range|checkbox|radio|button)$/.test(t.type)));
  const nativeKey = (t, k) => t && ((t.tagName === "SELECT" || (t.tagName === "INPUT" && t.type === "range")) &&
    /^(Arrow|Home|End|Page| $)/.test(k) || ((t.tagName === "BUTTON" || t.tagName === "A") && (k === "Enter" || k === " ")));
  function trap(e, box) {                                         // Tab stays inside an open dialog
    const f = [...box.querySelectorAll("button, [href], input, select, [tabindex]:not([tabindex='-1'])")].filter((x) => !x.hidden && x.offsetParent);
    if (!f.length) { e.preventDefault(); return; }
    const i = f.indexOf(document.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && (i < 0 || i === f.length - 1)) { e.preventDefault(); f[0].focus(); }
  }
  function nudge(k, big) {
    const st = big ? 10 : 1, b = S.boxes[S.sel];
    const dx = k === "ArrowLeft" ? -st : k === "ArrowRight" ? st : 0, dy = k === "ArrowUp" ? -st : k === "ArrowDown" ? st : 0;
    push(); if (b.guess) keepGuess(b);
    const W = S.img.naturalWidth, H = S.img.naturalHeight, w = b.b[2] - b.b[0], h = b.b[3] - b.b[1];
    const x0 = Math.max(0, Math.min(W - w, b.b[0] + dx)), y0 = Math.max(0, Math.min(H - h, b.b[1] + dy));
    b.b = [x0, y0, x0 + w, y0 + h]; changed();
    if (!S.nudged) { S.nudged = true; toast("Arrows nudge the selected box. Esc deselects; then ← → change frames."); }
  }
  document.addEventListener("keydown", (e) => {
    const k = e.key, mod = e.ctrlKey || e.metaKey;
    if (!$("help").hidden) {
      if (k === "Escape" || k === "?") { e.preventDefault(); help(false); } else if (k === "Tab") trap(e, $("help"));
      return;
    }
    if (S.confirm) {                                              // only N (or the button) confirms "no robots"
      if (k === "n" || k === "N") { e.preventDefault(); if (!e.repeat) { closeConfirm(); save("accept", true); } }
      else if (k === "Escape") { e.preventDefault(); closeConfirm(); }
      else if (k === "Tab") trap(e, $("confirm"));
      else if (!(e.target.tagName === "BUTTON" && (k === "Enter" || k === " "))) e.preventDefault();
      return;
    }
    if (typing(e.target)) { if (k === "Enter" || k === "Escape") e.target.blur(); return; }
    if (nativeKey(e.target, k)) return;                           // a focused select / slider / button keeps its keys
    if (mod && (k === "z" || k === "Z")) {
      e.preventDefault();
      if (e.shiftKey) redo(); else if (S.hist.length) undo(); else if (S.last && $("toast").classList.contains("show")) undoSave();
      return;
    }
    if (mod && (k === "y" || k === "Y")) { e.preventDefault(); redo(); return; }
    if (mod) return;
    if (e.repeat && /^(a|A|Enter|s|S|b|B|n|N|k|K)$/.test(k)) { e.preventDefault(); return; }   // held keys never save unseen frames
    if (k.startsWith("Arrow") && S.sel >= 0 && S.boxes[S.sel] && cur()) { e.preventDefault(); nudge(k, e.shiftKey); return; }
    if (k === " ") { S.space = true; e.preventDefault(); return; }
    if (/^[1-9]$/.test(k)) { pickClass(+k - 1); return; }
    switch (k) {
      case "a": case "A": case "Enter": e.preventDefault(); save("accept"); break;
      case "s": case "S": case "ArrowRight": e.preventDefault(); skip(); break;
      case "b": case "B": save("bad"); break;
      case "ArrowLeft": e.preventDefault(); go(S.pos - 1); break;
      case "Delete": case "Backspace": e.preventDefault(); delSel(); break;
      case "[": case "]": {
        const vis = S.boxes.map((b, i) => i).filter((i) => shown(S.boxes[i]));
        if (vis.length) { const at = vis.indexOf(S.sel); S.sel = vis[at < 0 ? (k === "]" ? 0 : vis.length - 1) : (at + (k === "[" ? vis.length - 1 : 1)) % vis.length]; changed(); }
        break;
      }
      case "k": case "K": {
        const b = S.boxes[S.sel];
        if (b && b.guess) { push(); keepGuess(b); changed(); toast("Kept as a robot box"); }
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
  // the canvas shows a focus ring only when Tab brought the focus there (a click plus a shortcut key would light it up)
  let tabAt = 0;
  document.addEventListener("keydown", (e) => { if (e.key === "Tab") tabAt = performance.now(); }, true);
  cv.addEventListener("focus", () => stage.classList.toggle("kfocus", performance.now() - tabAt < 300));
  cv.addEventListener("blur", () => stage.classList.remove("kfocus"));
  // a button clicked with the mouse or a finger gives focus back, so Space (pan) and Enter (accept) don't press it again
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("button");
    if (b && e.detail > 0 && document.activeElement === b) b.blur();
  });
  function skip() { if (!S.confirm) go(S.pos + 1); }               // Skip = come back later: nothing is saved
  function delSel() { if (S.sel < 0 || !cur()) return; push(); S.boxes.splice(S.sel, 1); S.sel = -1; changed(); }
  function resetBoxes() {
    const f = cur();
    if (!f) return;
    push();
    const [bx, kind] = origin(f);
    S.boxes = bx.map((b) => mk(b, kind));
    S.base = S.boxes.map(copyBox);
    S.baseKind = kind; S.prevDiff = null; S.prevStatus = null; S.sel = -1; S.note = noteFor(Object.assign({}, f, { review: null }));
    changed(); toast("Back to the " + (kind === "none" ? "empty frame" : { auto: "auto", import: "imported" }[kind] + " boxes"));
  }
  let helpFrom = null;
  function help(on) {
    $("help").hidden = !on;
    if (on) {
      helpFrom = document.activeElement; $("helpClose").focus();
      store.set("csreview.guide", "1"); $("helpBtn").classList.remove("new");
      if (S.note.startsWith("New here?")) { S.note = ""; note(); }
    } else if (helpFrom && helpFrom.focus) helpFrom.focus({ preventScroll: true });
  }

  // ---------- theme (the button shows the current one)
  const ICONS = {
    auto: '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10 3a7 7 0 0 1 0 14z" fill="currentColor"/></svg>',
    light: '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><circle cx="10" cy="10" r="3.6" fill="currentColor"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 1.5v2.2M10 16.3v2.2M1.5 10h2.2M16.3 10h2.2M4 4l1.6 1.6M14.4 14.4 16 16M4 16l1.6-1.6M14.4 5.6 16 4"/></g></svg>',
    dark: '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M15.5 12.6A6.5 6.5 0 0 1 7.4 4.5 6.5 6.5 0 1 0 15.5 12.6z" fill="currentColor"/></svg>',
  };
  function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    const b = $("themeBtn");
    b.innerHTML = ICONS[t] || ICONS.auto;
    b.title = "Theme: " + ({ auto: "same as the system", light: "light", dark: "dark" }[t] || t) + " (click to change)";
    b.setAttribute("aria-label", b.title);
  }
  function confLabel() {
    $("conf").value = S.conf; $("confOut").textContent = S.conf.toFixed(2);
    $("confWrap").classList.toggle("moved", Math.abs(S.conf - CONF0) > 1e-6);
  }

  // ---------- wiring
  document.querySelectorAll(".q").forEach((b) => b.addEventListener("click", () => {
    if (S.q === b.dataset.q) return;
    S.q = b.dataset.q; store.set("csreview.q", S.q); markQueue(); restartQueue();
  }));
  $("who").addEventListener("input", (e) => {                     // show what will be stored while typing
    const c = e.target.value.replace(/[^\p{L}\p{N}_ .@'-]/gu, "");
    if (c !== e.target.value) e.target.value = c;
  });
  $("who").addEventListener("change", (e) => {
    S.name = cleanName(e.target.value); e.target.value = S.name; store.set("csreview.name", S.name);
    refreshStats();
    if (S.q === "mine") restartQueue();
  });
  $("conf").addEventListener("input", (e) => {
    S.conf = parseFloat(e.target.value); confLabel();
    if (S.sel >= 0 && !shown(S.boxes[S.sel])) S.sel = -1;
    changed();
  });
  $("conf").addEventListener("pointerup", (e) => e.target.blur());   // after a drag, → is "next frame" again
  $("accept").addEventListener("click", () => save("accept"));
  $("skip").addEventListener("click", skip);
  $("bad").addEventListener("click", () => save("bad"));
  $("prev").addEventListener("click", () => go(S.pos - 1));
  $("undo").addEventListener("click", undo);
  $("reset").addEventListener("click", resetBoxes);
  $("del").addEventListener("click", delSel);
  $("toastUndo").addEventListener("click", undoSave);
  $("clearFilters").addEventListener("click", clearFilters);
  $("cfYes").addEventListener("click", () => { closeConfirm(); save("accept", true); });
  $("cfNo").addEventListener("click", closeConfirm);
  $("fitBtn").addEventListener("click", () => { fit(); draw(); });
  $("zoomIn").addEventListener("click", () => zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1.5));
  $("zoomOut").addEventListener("click", () => zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1 / 1.5));
  $("helpBtn").addEventListener("click", () => help(true));
  $("helpClose").addEventListener("click", () => help(false));
  $("help").addEventListener("click", (e) => { if (e.target === $("help")) help(false); });
  $("themeBtn").addEventListener("click", () => {
    const order = ["auto", "light", "dark"], t = order[(order.indexOf(document.documentElement.dataset.theme) + 1) % 3];
    applyTheme(t); store.set("csreview.theme", t); toast("Theme: " + ({ auto: "same as the system", light: "light", dark: "dark" }[t]));
  });
  new ResizeObserver(() => resize()).observe(stage);
  boot();
})();
