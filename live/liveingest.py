"""Live-stream ingest: the managed recorder service, and the worker's view of what it recorded.

Why: at an event the per-match YouTube uploads arrive minutes to hours after a match (and only once TBA has
posted the result).  live/livestream.py records the event's live stream gap-free and cuts each match out of
it (teams, scores, start/end read off the broadcast overlay) ~20 s after the final buzzer.  This module runs
it for every followed event and hands its match records to the worker.

SERVICE  (compose service `recorder`, worker image: `python live/worker.py --recorder`; or
          `python live/liveingest.py serve`)
  Every LIVE_POLL s: for each followed event (DATA/config/worker.json "events", else $EVENTS) that has an
  enabled livestream source in events/<ev>/sources.json (Video page; TBA webcasts are discovered as
  livestream sources) and whose dates include today (TBA; LIVE_ANY_DATE=1 skips the check), resolve the URL
  (yt-dlp: video id + live status) and keep ONE `live/livestream.py URL --event EV --source SID` process
  (recorder + analyser) running with its output in
      LIVE_DIR/<ev>/<video id>/   seg/ chunks.jsonl clips/ matches/ evidence/ livestream.log meta.json
  (LIVE_DIR default $MEDIA/live -- the NAS media disk, so clips are playable at /media/live/...).
  * stream drops / expired URLs / restarts: the recorder addresses 5 s chunks by sequence number and resumes
    after the last one it has (gap-free, also across a service restart); a crashed child is restarted with
    back-off (30 s .. 10 min); a channel URL that moves to a new video id -> the child exits (code 3) and a
    new output dir is started for the new video at once; not live yet / upcoming -> re-checked every
    LIVE_RESOLVE_S; stream over (was_live) -> stops, re-checked every 10 min (a channel /live URL can go live
    again with a new video).
  * disk: each child keeps its dir under LIVE_BUDGET_GB (analysed non-match segments go after --keep-hours,
    match segments after --keep-match-hours, oldest first over budget; clips are kept); the service deletes
    segments of finished dirs after LIVE_KEEP_SEG_DAYS and, when the LIVE_DIR disk has less than
    LIVE_MIN_FREE_GB free, oldest segments of finished dirs first.
  * status for the UI: DATA/events/<ev>/livestream.json (served by GET /app/v1/events/<ev>/livestream),
    rewritten every poll: state, source, video, recorder/analyser progress and lag, last matches, disk, errors.

WORKER  (live/worker.py hooks; LiveView below)
  Records LIVE_DIR/<ev>/*/matches/*.json -> TBA match keys (qm<n>, sf<set>m1, f1m<n>).  Per key the LATEST
  record that is a real match (no not_a_match / aborted_* / clock_lost_* / timeout_countdown /
  zero_score_no_auto / no_results_screen / clip_failed) wins.
  * fast results (always): teams, scores, start/end, clip link -> the site's match rows ("live"), also for
    matches TBA has not posted / not listed yet;
  * tracking from the clip only when the stream is TRACKABLE: the stream source's layout (layout.json) has a
    field or overhead box AND a calibration of that camera exists: events/<ev>/camera_<source id>.json, or
    events/<ev>/camera.json when it was calibrated on that source ("source" key, calibration wizard).
    Otherwise the match is tracked from the per-match uploads once they appear (as before).
"""
import glob, json, os, re, shutil, signal, subprocess, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

MATCH_S = 163.0
BAD_FLAGS = ("aborted_", "clock_lost_", "timeout_countdown", "zero_score_no_auto", "no_results_screen", "clip_failed")


def data_dir(): return os.environ.get("DATA", "/data")
def media_dir(): return os.environ.get("MEDIA", "/media")
def live_dir(): return os.environ.get("LIVE_DIR") or os.path.join(media_dir(), "live")
def status_path(ev, data=None): return os.path.join(data or data_dir(), "events", ev, "livestream.json")


def _env_f(k, d): return float(os.environ.get(k, d))


def _jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, p)


# ================================================================ records (worker side)
def record_key(ev, rec):
    """Stream record -> TBA match key (2023+ double elimination: playoff match n = sf<n>m1)."""
    lv, s, n = rec.get("comp_level"), rec.get("set"), rec.get("number")
    if lv == "qm" and n: return f"{ev}_qm{n}"
    if lv == "sf" and s: return f"{ev}_sf{s}m1"
    if lv == "f" and n: return f"{ev}_f1m{n}"
    return None


def problems(rec):
    """Why a record is not a played match result ([] = it is)."""
    out = ["not_a_match"] if rec.get("not_a_match") else []
    return out + [f for f in rec.get("flags") or [] if f.startswith(BAD_FLAGS)]


def clip_of(rec):
    """The record's clip (absolute path as written; else the same name in its own clips/ dir)."""
    c = rec.get("clip")
    if not c: return None
    if os.path.exists(c): return c
    alt = os.path.join(rec.get("_dir") or "", "clips", os.path.basename(c))
    return alt if os.path.exists(alt) else None


def clip_ok(rec):
    a = rec.get("auto_start_in_clip")
    return clip_of(rec) is not None and a is not None and 0.0 <= a <= 60.0


def team_keys(rec):
    """{"red": [3 frc keys], "blue": [...]} in station order, or None if any slot is unread."""
    t = rec.get("teams") or {}
    out = {a: [f"frc{x}" for x in (t.get(a) or []) if x] for a in ("red", "blue")}
    return out if all(len(out[a]) == 3 for a in out) else None


def wall(rec):
    s = rec.get("t_start_utc")
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load_records(ev, root=None):
    out = []
    for d in sorted(glob.glob(os.path.join(root or live_dir(), ev, "*"))):
        md = os.path.join(d, "matches")
        if not os.path.isdir(md): continue
        meta = _jload(os.path.join(d, "meta.json"), {}) or {}
        for p in sorted(glob.glob(os.path.join(md, "*.json"))):
            r = _jload(p)
            if not isinstance(r, dict) or "id" not in r: continue
            r.update(_dir=d, _path=p, _meta=meta, _key=record_key(ev, r))
            out.append(r)
    return out


def _order(r):
    return (wall(r) or 0.0, (r.get("_meta") or {}).get("started") or 0.0, r.get("t_start_stream") or 0.0)


def latest(records, need_clip=True):
    """key -> the latest record that is a real match (with a usable clip if need_clip)."""
    best = {}
    for r in sorted(records, key=_order):
        if not r.get("_key") or problems(r): continue
        if need_clip and not clip_ok(r): continue
        best[r["_key"]] = r
    return best


def records_signature(ev, root=None):
    """Changes when a new match record appears (not when one is rewritten: results-screen updates are picked
    up at the next regular poll) -- the worker wakes up on it."""
    return tuple(sorted(glob.glob(os.path.join(root or live_dir(), ev, "*", "matches", "*.json"))))


def media_url(p):
    """'media/...' URL of a file under MEDIA (nginx serves MEDIA at /media/), else None."""
    if not p: return None
    rp, m = os.path.realpath(p), os.path.realpath(media_dir())
    return "media/" + os.path.relpath(rp, m) if rp.startswith(m.rstrip("/") + "/") else None


def lineup_common(rec, m):
    """How many of the record's 6 team slots are in TBA's alliances (None if TBA has no lineup)."""
    tk = {a: (m.get("alliances", {}).get(a) or {}).get("team_keys") or [] for a in ("red", "blue")}
    if not all(len(tk[a]) == 3 for a in tk): return None
    t = rec.get("teams") or {}
    return sum(1 for a in tk for x in (t.get(a) or []) if x and f"frc{x}" in tk[a])


def winner(score):
    r, b = (score or {}).get("red"), (score or {}).get("blue")
    if r is None or b is None: return None
    return "red" if r > b else "blue" if b > r else ""


def synth_match(ev, rec):
    """A TBA-shaped match for a record whose match TBA does not list yet (playoffs created late)."""
    lv, sc, tk = rec["comp_level"], rec.get("score") or {}, team_keys(rec) or {"red": [], "blue": []}
    return dict(key=rec["_key"], event_key=ev, comp_level=lv, set_number=rec.get("set") if lv == "sf" else 1,
                match_number=1 if lv == "sf" else rec.get("number"),
                alliances={a: dict(team_keys=tk[a], score=sc.get(a) if sc.get(a) is not None else -1,
                                   surrogate_team_keys=[], dq_team_keys=[]) for a in ("red", "blue")},
                winning_alliance=winner(sc), time=wall(rec), actual_time=None, predicted_time=None, videos=[],
                score_breakdown=None, livestream=True)


def posted(m):
    """TBA has posted a result for this match (a synthesized live match never counts)."""
    s = (m.get("alliances", {}).get("red") or {}).get("score")
    return not m.get("livestream") and s is not None and s >= 0


# ---------------------------------------------------------------- trackability
def stream_camera(ev, sid):
    """Calibration of a stream source's camera: events/<ev>/camera_<sid>.json, else camera.json when it was
    calibrated on this source (calibration wizard writes "source").  Data dir first, then the repo."""
    import layout as LY
    if not sid: return None
    for d in LY.event_dirs(ev):
        p = os.path.join(d, f"camera_{sid}.json")
        if os.path.exists(p): return p
    for d in LY.event_dirs(ev):
        p = os.path.join(d, "camera.json")
        if os.path.exists(p) and (_jload(p, {}) or {}).get("source") == sid: return p
    return None


def trackable(ev, sid):
    """-> dict(ok, reason, camera, layout): can the tracker run on this stream's clips?"""
    import layout as LY
    if not sid:
        return dict(ok=False, reason="record has no source id (meta.json)")
    L = LY.find(ev=ev)
    sl = L.sources.get(sid) if L else None
    if sl is None or not (sl.has("field") or sl.has("overhead")):
        return dict(ok=False, reason="the stream's layout has no field/overhead box")
    cam = stream_camera(ev, sid)
    if not cam:
        return dict(ok=False, reason=f"no calibration for the stream camera (camera_{sid}.json)")
    lay = {"version": 1, "processing_source": sid, "sources": {sid: L.doc["sources"][sid]}}
    return dict(ok=True, reason="trackable", camera=cam, layout=lay)


class LiveView:
    """One sync's view of an event's livestream records (the worker builds one per poll)."""

    def __init__(self, ev, root=None):
        self.ev = ev
        self.recs = load_records(ev, root)
        self.fast = latest(self.recs, need_clip=False)
        self.clips = latest(self.recs, need_clip=True)
        self._tr = {}

    def __bool__(self):
        return bool(self.recs)

    def track(self, sid):
        if sid not in self._tr:
            try:
                self._tr[sid] = trackable(self.ev, sid)
            except Exception as e:
                self._tr[sid] = dict(ok=False, reason=f"layout/calibration check failed: {e!r}"[:200])
        return self._tr[sid]

    def extra_matches(self, tba_keys):
        """Synthesized matches for records whose key TBA does not list."""
        return [synth_match(self.ev, r) for k, r in self.fast.items() if k not in tba_keys]

    def for_processing(self, m, is_posted):
        """-> the live input for process_match (or None): usable clip, trackable stream, known teams."""
        r = self.clips.get(m["key"])
        if r is None: return None
        sid = (r.get("_meta") or {}).get("source")
        tr = self.track(sid)
        if not tr["ok"]: return None
        common = lineup_common(r, m)
        if common is not None and common < 4:          # stream title/teams disagree with TBA's lineup
            return None
        tba_t = {a: (m["alliances"][a] or {}).get("team_keys") or [] for a in ("red", "blue")}
        if all(len(tba_t[a]) == 3 for a in tba_t) and not m.get("livestream"):
            teams, src = tba_t, "tba"
        else:
            teams, src = team_keys(r), "stream"
        if not teams: return None
        vid = (r.get("_meta") or {}).get("video_id") or os.path.basename(r["_dir"])
        info = dict(record=r["id"], record_path=r["_path"], source=sid, video_id=vid, clip=clip_of(r),
                    auto_start_in_clip=r["auto_start_in_clip"], t_start_utc=r.get("t_start_utc"),
                    teams=teams, teams_from=src, tba_posted=bool(is_posted), stream_score=r.get("score"),
                    stream_teams=r.get("teams"), flags=r.get("flags"))
        return dict(clip=clip_of(r), start=float(r["auto_start_in_clip"]), camera=tr["camera"], layout=tr["layout"],
                    teams=teams, teams_from=src, source=sid, tba_posted=bool(is_posted), info=info,
                    video=dict(id=f"live-{re.sub(r'[^A-Za-z0-9_-]', '', vid)[:24]}-{r['id']}",
                               title=f"Livestream: {r.get('match_name') or r['id']}", channel="livestream"))

    def site_row(self, key):
        r = self.fast.get(key)
        if r is None: return None
        sid = (r.get("_meta") or {}).get("source")
        tr = self.track(sid)
        w = wall(r)
        tk = {a: [f"frc{x}" if x else None for x in ((r.get("teams") or {}).get(a) or [])] for a in ("red", "blue")}
        return dict(source="livestream", record=r["id"], source_id=sid,
                    video_id=(r.get("_meta") or {}).get("video_id") or os.path.basename(r["_dir"]),
                    name=r.get("match_name"), teams=tk, score=r.get("score"), score_source=r.get("score_source"),
                    breakdown=r.get("breakdown"), t_start_utc=r.get("t_start_utc"),
                    t_end_utc=datetime.fromtimestamp(w + MATCH_S, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z"
                    if w else None,
                    clip=media_url(clip_of(r)) if clip_ok(r) else None, auto_start_in_clip=r.get("auto_start_in_clip"),
                    confidence=r.get("confidence"), flags=r.get("flags"),
                    tracking="from_stream" if tr["ok"] else "uploads", tracking_note=tr["reason"])

    def decorate_row(self, row, m):
        """Site match row: add row["live"]; when TBA has not posted, the stream's teams/score/winner."""
        lr = self.site_row(m["key"])
        if lr is None: return
        row["live"] = lr
        if posted(m):
            row["score_source"] = "tba"; return
        sc = lr.get("score") or {}
        for a in ("red", "blue"):
            if sc.get(a) is not None: row[a]["score"] = sc[a]
            if len(row[a].get("teams") or []) != 3 and all(lr["teams"].get(a) or [None]):
                row[a]["teams"] = lr["teams"][a]
        row["winner"] = row.get("winner") or winner(sc)
        row["score_source"] = "livestream"
        if row.get("state") not in ("done", "processing"):
            row["state"] = "live"


def status_summary(ev, data=None):
    """Small subset of the service status for the site's event.json."""
    s = _jload(status_path(ev, data))
    if not s: return None
    return {k: s.get(k) for k in ("state", "message", "updated", "video_id", "behind_live_s", "last_match")}


# ================================================================ service
def worker_config(data):
    """(tba key, events, options) the same way live/worker.py load_config reads them."""
    c = _jload(os.path.join(data, "config", "worker.json"), {}) or {}
    events = c.get("events")
    if events is None:
        events = [e.strip() for e in os.environ.get("EVENTS", "").split(",") if e.strip()]
    return c.get("tba_key") or os.environ.get("TBA_KEY") or "", list(events), c.get("options") or {}


def pick_source(doc, today=None):
    """The livestream source to record: an enabled livestream marked primary / wide, else the webcast dated
    today, else an admin-added one, else the first."""
    ls = [s for s in doc.get("sources", []) if s.get("enabled", True) and s.get("kind") == "livestream" and s.get("url")]
    if not ls: return None
    for s in ls:
        if s.get("primary") or s.get("role") == "wide": return s
    if today:
        d = [s for s in ls if _norm_date((s.get("webcast") or {}).get("date")) in today]
        if d: return d[0]
    adm = [s for s in ls if s.get("origin") == "admin"]
    return (adm or ls)[0]


def _norm_date(s):
    try:
        y, m, d = (int(x) for x in str(s).split("-"))
        return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        return None


def resolve(url, timeout=90):
    """-> (video id, live_status) via yt-dlp (IPv4); raises when nothing is live / reachable."""
    if url.startswith("/"):
        return re.sub(r"[^A-Za-z0-9_-]", "_", os.path.basename(url))[:40], "is_live"
    import sources as SRC
    try:
        SRC.safe_public_url(url)                 # http(s) to a public host only (SSRF guard)
    except ValueError as e:
        raise RuntimeError(f"livestream URL refused: {e}")
    ytdlp = os.environ.get("YTDLP", "yt-dlp")
    net = ["-4"] if os.environ.get("YT_FORCE_IPV4", "1") != "0" else []
    r = subprocess.run([ytdlp, *net, "--no-warnings", "--skip-download", "--print", "%(id)s\t%(live_status)s", "--", url],
                       capture_output=True, text=True, timeout=timeout)
    lines = [l for l in r.stdout.strip().split("\n") if "\t" in l]
    if r.returncode != 0 or not lines:
        raise RuntimeError((r.stderr.strip().split("\n") or [""])[-1][-200:] or "yt-dlp failed")
    vid, st = lines[0].split("\t")[:2]
    return re.sub(r"[^A-Za-z0-9_-]", "_", vid)[:40], st


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _tail(p, n=4096):
    """Last lines of a log (ANSI colours and onnxruntime provider warnings dropped)."""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz - n))
            lines = f.read().decode("utf-8", "replace").split("\n")
    except OSError:
        return []
    return [_ANSI.sub("", l) for l in lines if "[W:onnxruntime" not in l]


def _dir_bytes(d):
    tot = 0
    try:
        for e in os.scandir(d):
            if e.is_file(follow_symlinks=False): tot += e.stat().st_size
    except OSError:
        pass
    return tot


ERR_RE = re.compile(r"error|GAP|failed|Traceback|unavailable|Exception", re.I)


def dir_status(out, ev=None):
    """Progress of one livestream.py output dir (recorder, analyser, matches, disk, recent errors)."""
    now = time.time()
    rec = _jload(os.path.join(out, "recorder.json"), {}) or {}
    an = _jload(os.path.join(out, "analyzer.json"), {}) or {}
    last = None
    for line in reversed(_tail(os.path.join(out, "chunks.jsonl"))):
        try:
            last = json.loads(line); break
        except ValueError:
            continue
    behind = round(now - (last["wall"] + last.get("dur", 5.0)), 1) if last and last.get("wall") else None
    recs = load_records(ev, os.path.dirname(os.path.dirname(out))) if ev else []
    recs = [r for r in recs if r["_dir"] == out]
    recs.sort(key=_order)
    ok = [r for r in recs if not problems(r)]

    def brief(r):
        return dict(id=r["id"], key=r.get("_key"), name=r.get("match_name"), t_start_utc=r.get("t_start_utc"),
                    teams=r.get("teams"), score=r.get("score"), score_source=r.get("score_source"),
                    flags=r.get("flags"), usable=not problems(r), clip=media_url(clip_of(r)) if clip_ok(r) else None)
    log_lines = _tail(os.path.join(out, "livestream.log"), 64 * 1024)
    errs = [l for l in log_lines if l and ERR_RE.search(l)][-8:]
    seg_b, clip_b = _dir_bytes(os.path.join(out, "seg")), _dir_bytes(os.path.join(out, "clips"))
    return dict(dir=out, video_id=rec.get("video_id") or os.path.basename(out),
                recorder=dict(last_sq=rec.get("last_sq"), at=rec.get("at"),
                              idle_s=round(now - rec["at"], 1) if rec.get("at") else None),
                last_chunk_utc=datetime.fromtimestamp(last["wall"], timezone.utc).isoformat() if last and last.get("wall") else None,
                behind_live_s=behind,
                analyser=dict(last_sq=an.get("last_sq"), at=an.get("at"),
                              behind_chunks=(rec["last_sq"] - an["last_sq"]) if rec.get("last_sq") is not None
                              and an.get("last_sq") is not None else None),
                matches=dict(n=len(recs), n_matches=len(ok), recent=[brief(r) for r in recs[-12:]]),
                last_match=brief(ok[-1]) if ok else None,
                disk=dict(seg_gb=round(seg_b / 1e9, 2), clips_gb=round(clip_b / 1e9, 2)),
                errors=errs, log_tail=[l for l in log_lines if l][-5:])


class Service:
    """Supervises one livestream.py child per followed event with a livestream source."""

    def __init__(self, data=None, log=None):
        self.data = data or data_dir()
        self.log = log or (lambda *a: print(time.strftime("%H:%M:%S"), "recorder:", *a, flush=True))
        self.kids = {}          # ev -> dict(proc, out, vid, source, url, started, fh)
        self.ev_state = {}      # ev -> dict(state, message, next_try, restarts, live_status, video_id, ...)
        self.stopping = False
        self._ret_at = 0.0
        self._dates = {}        # ev -> (fetched, start, end, tz)

    # ------------------------------------------------------------ config
    def wanted(self):
        key, events, opts = worker_config(self.data)
        out = {}
        if os.environ.get("LIVESTREAM", "1") == "0":
            return out, "livestream recording disabled (LIVESTREAM=0)", opts, key
        import sources as SRC
        for ev in events:
            doc = SRC.load(os.path.join(self.data, "events", ev))
            today = self.today(ev, key)
            s = pick_source(doc, today)
            if s is None: continue
            ok, why = self.in_dates(ev, key)
            out[ev] = dict(source=s, active=ok, why=why)
        return out, None, opts, key

    def _event_info(self, ev, key):
        c = self._dates.get(ev)
        if c and time.time() - c[0] < 6 * 3600: return c[1]
        info = None
        if key:
            try:
                import tba as TBAM
                info = TBAM.TBA(key, os.path.join(self.data, "cache", "tba_live")).event(ev)
            except Exception as e:
                self.log(f"{ev}: TBA event lookup failed ({e!r:.120}); date check off")
        self._dates[ev] = (time.time(), info)
        return info

    def today(self, ev, key):
        """Today's date(s) for webcast matching: event-local (TBA timezone) and UTC."""
        now = datetime.now(timezone.utc)
        out = {now.strftime("%Y-%m-%d")}
        tz = (self._event_info(ev, key) or {}).get("timezone") if key else None
        if tz:
            try:
                from zoneinfo import ZoneInfo
                out.add(now.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d"))
            except Exception:
                pass
        return out

    def in_dates(self, ev, key):
        if os.environ.get("LIVE_ANY_DATE") == "1": return True, "date check off (LIVE_ANY_DATE=1)"
        info = self._event_info(ev, key)
        if not info or not info.get("start_date"): return True, "event dates unknown"
        d0 = datetime.fromisoformat(info["start_date"]).date() - timedelta(days=1)
        d1 = datetime.fromisoformat(info.get("end_date") or info["start_date"]).date() + timedelta(days=1)
        today = datetime.now(timezone.utc).date()
        return d0 <= today <= d1, f"event dates {info['start_date']}..{info.get('end_date')}"

    # ------------------------------------------------------------ children
    def out_dir(self, ev, vid):
        return os.path.join(live_dir(), ev, vid)

    def start(self, ev, src, vid, opts, key):
        out = self.out_dir(ev, vid)
        fresh = not os.path.exists(os.path.join(out, "recorder.json"))
        os.makedirs(out, exist_ok=True)
        gpu = opts.get("gpu_device") if opts.get("gpu_device") is not None else os.environ.get("GPU_DEVICE", "0")
        cmd = [sys.executable, "-u", os.path.join(HERE, "livestream.py"), "--out", out,
               "--event", ev, "--source", src["id"], "--data", self.data, "--gpu", str(int(gpu)),
               "--max-hours", os.environ.get("LIVE_MAX_HOURS", "16"),
               "--budget-gb", os.environ.get("LIVE_BUDGET_GB", "60"),
               "--keep-hours", os.environ.get("LIVE_KEEP_HOURS", "2"),
               "--keep-match-hours", os.environ.get("LIVE_KEEP_MATCH_HOURS", "12"),
               "--backfill-min", os.environ.get("LIVE_BACKFILL_MIN", "60") if fresh else "0",
               "--exit-on-new-video", "--tba-env", "/nonexistent", "--", src["url"]]   # "--": the URL is never an option
        env = dict(os.environ)
        if key: env["TBA_KEY"] = key                        # optional cross-check in the records
        if opts.get("cpu_only"): env["FORCE_CPU"] = "1"
        meta = _jload(os.path.join(out, "meta.json"), {}) or {}
        meta.update(event=ev, source=src["id"], url=src["url"], video_id=vid, label=src.get("label"),
                    managed=True, started=meta.get("started") or time.time())
        _jsave(os.path.join(out, "meta.json"), meta)
        fh = open(os.path.join(out, "service.out"), "ab")
        p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=os.path.dirname(HERE),
                             start_new_session=True)
        self.kids[ev] = dict(proc=p, out=out, vid=vid, source=src, url=src["url"], started=time.time(), fh=fh)
        self.log(f"{ev}: recording {src['url']} (video {vid}, source {src['id']}) -> {out} pid {p.pid}"
                 f"{' backfill ' + cmd[cmd.index('--backfill-min') + 1] + ' min' if fresh else ' (resume)'}")

    def stop(self, ev, why):
        k = self.kids.pop(ev, None)
        if not k: return
        p = k["proc"]
        if p.poll() is None:
            self.log(f"{ev}: stopping recorder pid {p.pid} ({why})")
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                p.terminate()
            try:
                p.wait(20)
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid, signal.SIGKILL)
                except OSError: p.kill()
        k["fh"].close()

    def _ended(self, out):
        """The child's log says the stream is over (not a crash)."""
        lines = _tail(os.path.join(out, "livestream.log"), 16 * 1024)
        pat = re.compile(r"stream status (was_live|not_live|post_live)|ENDLIST|post_live: \d+ chunks fetched|"
                         r"nothing was recorded|reached end sq")
        return any(pat.search(l) for l in lines[-40:])

    # ------------------------------------------------------------ main step
    def step(self):
        want, why_off, opts, key = self.wanted()
        now = time.time()
        for ev in list(self.kids):
            if ev not in want or not want[ev]["active"]:
                self.stop(ev, "event unfollowed / source removed / outside event dates" if ev not in want else want[ev]["why"])
                self.ev_state.setdefault(ev, {}).update(state="idle", message="stopped")
            elif want[ev]["source"]["url"] != self.kids[ev]["url"]:
                self.stop(ev, "livestream source changed")
        for ev, w in want.items():
            es = self.ev_state.setdefault(ev, dict(state="starting", restarts=0, next_try=0.0))
            src = w["source"]
            es["source"] = {k: src.get(k) for k in ("id", "url", "label", "role", "origin")}
            if not w["active"]:
                es.update(state="idle", message=f"outside the event dates ({w['why']})"); continue
            k = self.kids.get(ev)
            if k is not None:
                code = k["proc"].poll()
                if code is None:
                    es.update(state="recording", message=None, pid=k["proc"].pid, video_id=k["vid"], out=k["out"])
                    continue
                self.kids.pop(ev); k["fh"].close()
                es["last_exit"] = dict(code=code, at=now, tail=[l for l in _tail(os.path.join(k["out"], "service.out"))[-6:] if l])
                if code == 3:                                       # the URL moved to a new video: start it now
                    es.update(state="starting", message="stream moved to a new video", next_try=0.0)
                elif self._ended(k["out"]):
                    _jsave(os.path.join(k["out"], "service.json"), dict(ended=True, at=now))
                    es.update(state="stream_over", message="stream ended; re-checking every 10 min",
                              next_try=now + 600, restarts=0)
                    self.log(f"{ev}: stream {k['vid']} over")
                else:
                    es["restarts"] = es.get("restarts", 0) + 1
                    wait = min(600, 30 * 2 ** min(es["restarts"] - 1, 5))
                    es.update(state="error", message=f"recorder exited with code {code}; restart in {wait}s",
                              next_try=now + wait)
                    self.log(f"{ev}: recorder exited ({code}); restarting in {wait}s")
            if now < es.get("next_try", 0): continue
            try:
                vid, ls = resolve(src["url"])
            except Exception as e:
                es.update(state="waiting", message=f"not live: {str(e)[:160]}",
                          next_try=now + _env_f("LIVE_RESOLVE_S", "120"), live_status=None)
                continue
            es.update(video_id=vid, live_status=ls)
            out = self.out_dir(ev, vid)
            done = (_jload(os.path.join(out, "service.json"), {}) or {}).get("ended")
            if ls in ("is_live", "is_upcoming") or (ls == "post_live" and not done and os.path.exists(out)):
                try:
                    self.start(ev, src, vid, opts, key)
                    es.update(state="recording", message=None, pid=self.kids[ev]["proc"].pid, out=out)
                except Exception as e:
                    es.update(state="error", message=f"could not start: {e!r}"[:200], next_try=now + 120)
            else:
                es.update(state="stream_over", next_try=now + 600,
                          message=f"stream {vid} is {ls}" + ("" if os.path.exists(out) else " (not recorded)"))
        self.write_status(want, why_off)
        if now - self._ret_at > 300:
            self._ret_at = now
            try:
                self.retention()
            except Exception as e:
                self.log(f"retention failed: {e!r}")

    def write_status(self, want, why_off):
        evs = set(want) | set(self.ev_state)
        _, followed, _ = worker_config(self.data)
        for ev in evs:
            if ev not in followed and ev not in self.kids: continue
            es = dict(self.ev_state.get(ev) or {})
            if self.stopping:
                es.update(state="stopped", message="recorder service stopped")
            elif ev not in want:
                es.update(state="disabled" if why_off else "no_source",
                          message=why_off or "no enabled livestream source in sources.json")
            st = dict(event=ev, updated=time.time(), service="recorder", live_dir=live_dir(), **{
                k: es.get(k) for k in ("state", "message", "source", "video_id", "live_status", "pid", "restarts",
                                       "last_exit")})
            st["next_check_in_s"] = max(0, round(es.get("next_try", 0) - time.time())) if es.get("next_try") else None
            out = (self.kids.get(ev) or {}).get("out") or es.get("out")
            if not out and es.get("video_id"):
                o = self.out_dir(ev, es["video_id"])
                out = o if os.path.isdir(o) else None
            if out and os.path.isdir(out):
                try:
                    ds = dir_status(out, ev)
                    st.update(ds)
                except Exception as e:
                    st["dir_error"] = repr(e)[:200]
            try:
                du = shutil.disk_usage(live_dir() if os.path.isdir(live_dir()) else media_dir())
                st.setdefault("disk", {}).update(free_gb=round(du.free / 1e9, 1),
                                                 budget_gb=_env_f("LIVE_BUDGET_GB", "60"),
                                                 min_free_gb=_env_f("LIVE_MIN_FREE_GB", "30"))
            except OSError:
                pass
            if src := es.get("source"):
                try:
                    tr = trackable(ev, src.get("id"))
                    st["tracking"] = dict(ok=tr["ok"], note=tr["reason"])
                except Exception as e:
                    st["tracking"] = dict(ok=False, note=repr(e)[:160])
            try:
                _jsave(status_path(ev, self.data), st)
            except OSError as e:
                self.log(f"{ev}: cannot write status ({e!r})")

    def retention(self):
        """Segments of finished output dirs: gone after LIVE_KEEP_SEG_DAYS, oldest first while the disk is low.
        Active dirs are trimmed by their own recorder (--budget-gb / --keep-hours)."""
        root = live_dir()
        if not os.path.isdir(root): return               # nothing recorded yet (fresh install)
        active = {k["out"] for k in self.kids.values()}
        segs = []
        for d in glob.glob(os.path.join(root, "*", "*")):
            if d in active or not os.path.isdir(os.path.join(d, "seg")): continue
            for e in os.scandir(os.path.join(d, "seg")):
                if e.is_file(): segs.append((e.stat().st_mtime, e.path, e.stat().st_size))
        segs.sort()
        keep_s = _env_f("LIVE_KEEP_SEG_DAYS", "2") * 86400
        min_free = _env_f("LIVE_MIN_FREE_GB", "30") * 1e9
        free = shutil.disk_usage(root).free
        n = 0
        for mt, p, sz in segs:
            if time.time() - mt > keep_s or free < min_free:
                try:
                    os.remove(p); free += sz; n += 1
                except OSError:
                    pass
        if n: self.log(f"retention: deleted {n} old segment files of finished streams (free {free / 1e9:.0f} GB)")
        if free < min_free:
            self.log(f"WARNING: only {free / 1e9:.0f} GB free on {root} (LIVE_MIN_FREE_GB {min_free / 1e9:.0f})")

    def shutdown(self, *_):
        self.stopping = True

    def run(self):
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        self.log(f"service up: DATA={self.data} LIVE_DIR={live_dir()}")
        poll = _env_f("LIVE_POLL", "10")
        while not self.stopping:
            try:
                self.step()
            except Exception as e:
                import traceback; traceback.print_exc()
                self.log(f"step failed: {e!r}")
            t = time.time() + poll
            while time.time() < t and not self.stopping:
                time.sleep(0.5)
        for ev in list(self.kids):
            self.stop(ev, "service stopping")
        try:
            self.write_status({}, None)
        except Exception:
            pass
        self.log("service stopped")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="live-stream ingest service / status")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the recorder service (compose service `recorder`)")
    a = sub.add_parser("status", help="print an event's status file"); a.add_argument("ev")
    a = sub.add_parser("records", help="list an event's livestream records and their match keys"); a.add_argument("ev")
    g = ap.parse_args(argv)
    if g.cmd == "status":
        print(json.dumps(_jload(status_path(g.ev)), indent=1))
    elif g.cmd == "records":
        lv = LiveView(g.ev)
        for r in sorted(lv.recs, key=_order):
            use = "clip" if lv.clips.get(r["_key"]) is r else "fast" if lv.fast.get(r["_key"]) is r else "-"
            print(f"{r['id']:10s} {str(r['_key']):22s} {use:4s} {problems(r) or ''} {r.get('teams')} {r.get('score')}")
    else:
        Service().run()


if __name__ == "__main__":
    main()
