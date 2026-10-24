"""Admin → Jobs: everything running and queued, per processor.  Mounted under /app/v1.  Admin only.

  GET /admin/jobs -> {now, runners: [{id, kind: worker|processor|client|helper|recorder, label, where, alive, age_s, doing,
                       doing_since, log_role, gpu: {ids, device, cards: [{index, name, util_pct, mem_used_mb, mem_total_mb}]},
                       current: {ev, key, label, progress, stage, since} | None}],
                     running: [{type: match|video|stream, by (runner id), ev, key, label, kind, since, progress, stage}],
                     queued: {videos: [{id, kind, ev, created, age_s, by}],
                              matches: [{ev, key, label, wait, note, takers, pos}], counts: {ev: {wait reason: n}}},
                     recent: [{type: match|video, ev, key, label, kind, ok, at, took_s, error}]}

Sources, all files the containers write (untrusted: opened without following symlinks, size-capped, type-checked, text clipped):
  DATA/logs/<role>.json            worker / recorder / processor-<id>: doing, doing_since, gpus (live/runlog.py)
  DATA/processors/<id>.json        GPU processor heartbeat {id, at, gpu, state, current{ev,key,since}} (live/worker.py proc_beat)
  DATA/events/<ev>/claims/*.json   who holds which match (live/claims.py); app-u<N>-<id> = a Cyber-Scout Client, helper-* = the
                                   ssh helper, a processor id = that GPU processor, anything else = the server's own worker
  DATA/processing/<ev>/<key>/job.json   a Client's progress / stage
  DATA/processing/streams/<ev>/*.json  clip-only stream assignments (a Client recording a stream for the server)
  DATA/events/<ev>/status.json (+ proc_status/)  per-match state; DATA/events/<ev>/livestream.json + DATA/recorder-status.json
  DATA/jobs/sources/*.json         Video-page jobs (live/sources.py)
  DATA/site/api/<ev>/event.json    rows with `wait` / `state_note` (live/worker.py row_wait): why a match isn't tracked yet
Stale things (a claim older than CLAIM_S, a heartbeat over ALIVE_S old, a job "running" for over 2 h) are never shown as running."""
import json, os, re, stat, time

from fastapi import APIRouter, Depends

from .. import db as DB
from ..config import cfg
from ..deps import EVENT_RE, MATCH_RE, User, require_role

try:
    from live import claims as CL
except Exception:                        # the api image without live/: the order falls back to the site's own
    CL = None

router = APIRouter(tags=["admin"])

ALIVE_S = 90                   # logs: heartbeat older than this = stopped
PROC_ALIVE_S = 180             # processors/<id>.json (worker.PROC_ALIVE_S)
CLAIM_S = getattr(CL, "CLAIM_S", 1800)
VIDEO_STALE_S = 2 * 3600       # a video job "running" this long ago is a dead runner's leftover
QUEUED_STALE_S = 3600          # sources.JOB_EXPIRED: queued over an hour = dropped by the worker
FILE_MAX = 2_000_000
SITE_MAX = 30_000_000
RECENT = 20
MATCHES_MAX = 60               # matches listed in "queued" (the counts cover all)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
CLIENT_RE = re.compile(r"^app-u(\d{1,9})-(.+)$")
SLOW_WAITS = {"queued", "retry"}           # trackable now
SETTLED = ("not_played",)                  # not a queue item: nothing to track yet
TAKERS = {"queued": "worker, GPU processors, Clients", "retry": "worker, GPU processors, Clients",
          "stream": "worker, GPU processors, Clients (when a clip is cut)", "video": "none yet (needs match video)",
          "setup": "none yet (needs video setup)", "model": "none yet (needs the detector model)",
          "failed": "none (failed; clear it on the Matches page)"}


def _num(v, lo=0.0):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e13 and v >= lo else None


def _text(v, n=200):
    return v[:n] if isinstance(v, str) else None


def _read(path, cap=FILE_MAX):
    """-> parsed JSON of a regular file (not a symlink, <= cap bytes) | None."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap: return None
        with os.fdopen(fd, "rb") as f:
            fd = None
            return json.loads(f.read(cap + 1))
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None: os.close(fd)


def _names(d, suffix=".json", limit=400):
    try:
        return sorted(n for n in os.listdir(d) if n.endswith(suffix))[:limit]
    except OSError:
        return []


def _dict(v): return v if isinstance(v, dict) else {}


def _gpu(d):
    cards = []
    for g in (d.get("gpus") if isinstance(d.get("gpus"), list) else [])[:8]:
        g = _dict(g)
        if _num(g.get("index")) is None: continue
        cards.append({"index": int(g["index"]), "name": _text(g.get("name"), 60) or "GPU", "util_pct": _num(g.get("util_pct")),
                      "mem_used_mb": _num(g.get("mem_used_mb")), "mem_total_mb": _num(g.get("mem_total_mb"))})
    return {"ids": _text(d.get("gpu_ids"), 20), "device": _text(d.get("gpu_device"), 20), "cards": cards}


def _current(c):
    c = _dict(c)
    ev, key = _text(c.get("ev"), 24), _text(c.get("key"), 40)
    if not (ev and key and EVENT_RE.match(ev) and MATCH_RE.match(key)): return None
    return {"ev": ev, "key": key, "label": _label(key), "since": _num(c.get("since")), "progress": None, "stage": None}


def _label(key):
    """2026wasam_qm12 -> Q12 (same short names as the site); anything odd stays as the key's tail."""
    m = re.match(r"^[^_]+_(qm|ef|qf|sf|f)(\d+)(?:m(\d+))?$", key or "")
    if not m: return (key or "").split("_")[-1]
    lv, n, k = m.groups()
    return "Q" + n if lv == "qm" else f"F{k or n}" if lv == "f" else f"{lv.upper()}{n}-{k or 1}"


def _owner(by, procs):
    """claim owner -> (runner id, kind, label)."""
    by = by if isinstance(by, str) else ""
    m = CLIENT_RE.match(by)
    if m: return "client:" + by, "client", "Client " + m.group(2)
    if by.startswith("helper-"): return "helper:" + by, "helper", "Helper " + by[7:]
    if by in procs: return "processor:" + by, "processor", "Processor " + by
    return "worker", "worker", "Server worker"


def _where_proc(d, log):
    """"GPU 1 · RTX 3090" / "CPU" for a processor heartbeat (gpu null = CPU) and its log's card list."""
    if d.get("gpu") is None: return "CPU"
    dev = _text(d.get("gpu"), 10)
    card = next((c for c in _gpu(log or {})["cards"] if str(c["index"]) == dev), None)
    return f"GPU {dev}" + (f" · {card['name']}" if card else "")


@router.get("/admin/jobs", summary="Everything running and queued, per processor (admin)")
def get_jobs(user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    now = time.time()
    data = cfg.DATA
    followed = [r["key"] for r in db.execute("SELECT key FROM events ORDER BY start_date DESC, key").fetchall()
                if EVENT_RE.match(r["key"] or "")]
    runners, running, recent = {}, [], []

    # ---- logs: worker, recorder, processors (doing + GPU cards)
    logs = {}
    for n in _names(os.path.join(data, "logs"))[:40]:
        role = n[:-5]
        d = _read(os.path.join(data, "logs", n))
        at = _num(_dict(d).get("at"))
        if not ID_RE.match(role) or at is None: continue
        logs[role] = dict(d, _at=at)

    def log_info(role):
        d = logs.get(role)
        if not d: return dict(alive=False, age_s=None, doing="", doing_since=None, gpu=_gpu({}), log_role=None)
        return dict(alive=0 <= now - d["_at"] < ALIVE_S, age_s=max(0, round(now - d["_at"], 1)), doing=_text(d.get("doing")) or "",
                    doing_since=_num(d.get("doing_since")), gpu=_gpu(d), log_role=role)

    w = dict(id="worker", kind="worker", label="Server worker", where="the server (main worker)", current=None, **log_info("worker"))
    runners["worker"] = w

    # ---- processors
    procs = {}
    for n in _names(os.path.join(data, "processors")):
        d = _dict(_read(os.path.join(data, "processors", n)))
        pid, at = _text(d.get("id"), 80) or n[:-5], _num(d.get("at"))
        if not ID_RE.match(pid) or at is None or now - at > 86400: continue
        procs[pid] = dict(d, _at=at)
    for pid, d in sorted(procs.items()):
        li = log_info("processor-" + pid)
        alive = now - d["_at"] < PROC_ALIVE_S
        li.update(alive=alive, age_s=max(0, round(now - d["_at"], 1)))
        state = _text(d.get("state"), 20) or ""
        if not alive: li.update(doing="", doing_since=None)
        elif not li["doing"]: li["doing"] = {"tracking": "tracking a match", "idle": "idle: nothing to track", "waiting": "waiting",
                                              "unhealthy": "paused after a failure"}.get(state, state)
        runners["processor:" + pid] = dict(id="processor:" + pid, kind="processor", label="Processor " + pid,
                                           where=_where_proc(d, logs.get("processor-" + pid)), current=None,
                                           state=state, note=_text(d.get("note")), **li)

    # ---- claims -> who is tracking what
    seen_claims = {}
    for ev in followed:
        d = os.path.join(data, "events", ev, "claims")
        status = _dict(_read(os.path.join(data, "events", ev, "status.json"), SITE_MAX))
        for f in _names(d):
            key = f[:-5]
            if not MATCH_RE.match(key): continue
            c = _dict(_read(os.path.join(d, f), 20000))
            at = _num(c.get("at"))
            if at is None or now - at >= CLAIM_S or not isinstance(c.get("by"), str): continue
            rid, kind, label = _owner(c["by"], procs)
            job = _dict(_read(os.path.join(data, "processing", ev, key, "job.json"), 20000)) if kind == "client" else {}
            st = _dict(status.get(key))
            since = _num(job.get("started")) or (_num(st.get("at")) if st.get("state") == "processing" else None) or at
            prog = _num(job.get("progress")) if job.get("by") == c["by"] else None
            stage = _text(job.get("stage"), 32) if job.get("by") == c["by"] else None
            if rid not in runners:
                runners[rid] = dict(id=rid, kind=kind, label=label, where="this Client (helping process)" if kind == "client" else
                                    "the ssh helper", alive=True, age_s=round(now - at, 1), doing="", doing_since=None,
                                    gpu=_gpu({}), log_role=None, current=None)
            item = dict(type="match", by=rid, ev=ev, key=key, label=_label(key), kind="tracking", since=since,
                        progress=min(1.0, prog) if prog is not None else None, stage=stage)
            running.append(item); seen_claims[(ev, key)] = rid
            r = runners[rid]
            if r["current"] is None:
                r["current"] = dict(ev=ev, key=key, label=item["label"], since=since, progress=item["progress"], stage=stage)
            if kind in ("client", "helper"):
                r["doing"] = r["doing"] or f"tracking {item['label']}"; r["doing_since"] = r["doing_since"] or since
    # a processor's heartbeat names its match even before the claim file is read
    for rid, r in runners.items():
        if r["kind"] != "processor" or not r["alive"] or r["current"]: continue
        cur = _current(_dict(procs.get(rid[10:])).get("current"))
        if cur and _dict(procs.get(rid[10:])).get("state") == "tracking":
            r["current"] = cur
            running.append(dict(type="match", by=rid, ev=cur["ev"], key=cur["key"], label=cur["label"], kind="tracking",
                                since=cur["since"], progress=None, stage=None))
    # the server's own worker: matches it is processing without a foreign claim
    for ev in followed:
        status = _dict(_read(os.path.join(data, "events", ev, "status.json"), SITE_MAX))
        for key, st in status.items():
            st = _dict(st)
            if st.get("state") != "processing" or (ev, key) in seen_claims or not MATCH_RE.match(str(key)): continue
            at = _num(st.get("at"))
            if at is None or now - at >= CLAIM_S: continue
            running.append(dict(type="match", by="worker", ev=ev, key=key, label=_label(key), kind="tracking", since=at,
                                progress=None, stage=None))
            if w["current"] is None: w["current"] = dict(ev=ev, key=key, label=_label(key), since=at, progress=None, stage=None)

    # ---- video-page jobs
    queued_v = []
    jd = os.path.join(data, "jobs", "sources")
    jobs = []
    for n in _names(jd, limit=1000)[-400:]:
        j = _dict(_read(os.path.join(jd, n), 200000))
        jid, kind, st = _text(j.get("id"), 60), _text(j.get("kind"), 40), j.get("state")
        if not (jid and kind and st in ("queued", "running", "done", "error")): continue
        jobs.append((jid, kind, st, j))
    for jid, kind, st, j in jobs:
        ev = _text(j.get("ev"), 24)
        created, started, finished = _num(j.get("created")), _num(j.get("started")), _num(j.get("finished"))
        if st == "queued":
            if created is None or now - created > QUEUED_STALE_S: continue
            queued_v.append(dict(id=jid, kind=kind, ev=ev, created=created, age_s=round(now - created, 1), by=_text(j.get("by"), 60)))
        elif st == "running":
            if started is None or now - started > VIDEO_STALE_S: continue
            runner = _text(j.get("runner"), 80)
            running.append(dict(type="video", by="worker", ev=ev, key=jid, label=kind, kind=kind, since=started, progress=None,
                                stage=None, runner=runner))
        elif finished is not None:
            recent.append(dict(type="video", ev=ev, key=jid, label=kind, kind=kind, ok=(st == "done"), at=finished,
                               took_s=round(finished - started, 1) if started is not None and finished >= started else None,
                               error=_text(j.get("error"), 300) if st == "error" else None))
    queued_v.sort(key=lambda x: x["created"])

    # ---- livestream recorder + clip-only stream assignments
    rb = _dict(_read(os.path.join(data, "recorder-status.json")))
    rat = _num(rb.get("at"))
    rl = log_info("recorder")
    rec_alive = rl["alive"] or (rat is not None and 0 <= now - rat < ALIVE_S)
    rec = dict(id="recorder", kind="recorder", label="Livestream recorder", where="the server (recorder)", current=None,
               state=None, note=None, **rl)
    rec["alive"] = rec_alive
    runners["recorder"] = rec
    for ev in followed:
        ls = _dict(_read(os.path.join(data, "events", ev, "livestream.json"), 400000))
        upd = _num(ls.get("updated"))
        if ls.get("state") == "recording" and upd is not None and now - upd < max(120, (_num(ls.get("expect_next_s")) or 0) + 90):
            running.append(dict(type="stream", by="recorder", ev=ev, key=_text(ls.get("video_id"), 40) or "", label="recording the livestream",
                                kind="recording", since=_num(ls.get("started")) or upd, progress=None, stage=_text(ls.get("source"), 40)))
        sd = os.path.join(data, "processing", "streams", ev)
        for n in _names(sd, limit=50):
            a = _dict(_read(os.path.join(sd, n), 20000))
            at = _num(a.get("at"))
            if a.get("released") or at is None or now - at >= CLAIM_S or not isinstance(a.get("by"), str): continue
            rid, kind, label = _owner(a["by"], procs)
            if rid not in runners:
                runners[rid] = dict(id=rid, kind=kind, label=label, where="this Client (helping process)", alive=True,
                                    age_s=round(now - at, 1), doing="", doing_since=None, gpu=_gpu({}), log_role=None, current=None)
            running.append(dict(type="stream", by=rid, ev=ev, key=_text(a.get("aid"), 20) or "", label="recording a livestream (clip only)",
                                kind="clip-only", since=_num(a.get("started")) or at, progress=None, stage=_text(a.get("source"), 40)))
            if not runners[rid]["doing"]: runners[rid]["doing"] = "recording a livestream (clip only)"; runners[rid]["doing_since"] = _num(a.get("started"))

    # ---- queued matches (the site rows' wait reasons) + recently finished matches
    qm, counts = [], {}
    for ev in followed:
        site = _dict(_read(os.path.join(data, "site", "api", ev, "event.json"), SITE_MAX))
        rows = [r for r in (site.get("matches") if isinstance(site.get("matches"), list) else []) if isinstance(r, dict)
                and isinstance(r.get("key"), str) and MATCH_RE.match(r["key"])]
        if CL is not None:
            try: rows = CL.order(data, ev, rows)
            except Exception: pass
        n = 0
        for r in rows:
            wait = _text(r.get("wait"), 20)
            if not wait or wait in SETTLED or r.get("state") in ("done", "processing"): continue
            counts.setdefault(ev, {})[wait] = counts.get(ev, {}).get(wait, 0) + 1
            n += 1
            qm.append(dict(ev=ev, key=r["key"], label=_text(r.get("short"), 20) or _label(r["key"]), wait=wait,
                           note=_text(r.get("state_note"), 200), takers=TAKERS.get(wait, ""), pos=n, claimed=(ev, r["key"]) in seen_claims))
        status = _dict(_read(os.path.join(data, "events", ev, "status.json"), SITE_MAX))
        pd = os.path.join(data, "events", ev, "proc_status")
        for pn in _names(pd, limit=300):
            if pn[:-5] not in status and MATCH_RE.match(pn[:-5]):
                status[pn[:-5]] = _read(os.path.join(pd, pn), 20000)
        for key, st in status.items():
            st = _dict(st); at = _num(st.get("at"))
            if not MATCH_RE.match(str(key)) or at is None or st.get("state") not in ("done", "failed", "error"): continue
            if now - at > 7 * 86400: continue
            recent.append(dict(type="match", ev=ev, key=key, label=_label(key), kind="tracking", ok=st["state"] == "done", at=at,
                               took_s=_num(st.get("took_s")), error=None if st["state"] == "done" else
                               (_text(st.get("note") or st.get("error"), 300) or "tracking failed; see the worker log")))
    recent.sort(key=lambda x: -x["at"])
    running.sort(key=lambda x: x["since"] or 0)
    ordr = {"worker": 0, "processor": 1, "client": 2, "helper": 3, "recorder": 4}
    rl_ = sorted(runners.values(), key=lambda r: (ordr.get(r["kind"], 9), r["id"]))
    for r in rl_:
        if not r["alive"]: r["current"] = None
    running = [x for x in running if x["by"] in runners and (runners[x["by"]]["alive"] or x["type"] == "video")]
    return {"now": now, "runners": rl_, "running": running[:100],
            "queued": {"videos": queued_v[:100], "matches": qm[:MATCHES_MAX], "matches_total": len(qm), "counts": counts},
            "recent": recent[:RECENT]}
