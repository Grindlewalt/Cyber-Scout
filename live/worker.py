"""Cyber-Scout worker: poll TBA, process each newly published match, publish JSON.

Configuration comes from the web app (DATA/config/worker.json, written by the api service; see load_config);
the environment is the fallback for anything not set there:
  TBA_KEY        TBA read key
  EVENTS         comma-separated event keys, e.g. 2026wasam
  DATA           state directory (default /data)
  MODEL          ONNX detector (default $DATA/models/robot.onnx)
  POLL_SECONDS   seconds between TBA polls (default 120)
  ONCE=1         run one sync pass and exit

Everything stateful lives under $DATA:
  events/<key>/camera.json        calibration (falls back to the repo's events/<key>/)
  events/<key>/event.json         optional per-event video config
  events/<key>/overrides.json     manual robot->team fixes
  events/<key>/matches/<m>.json   processed matches (the expensive part; back this up)
  events/<key>/status.json        per-match state
  site/api/...                    generated JSON the web container serves
  tba/ cache/                     disposable caches

Livestream (live/liveingest.py; recorded by the `recorder` service = this file with --recorder): match records
in $MEDIA/live/<ev>/*/matches/ give fast results (site rows "live": teams, score, clip) before TBA posts, and --
when the stream's layout has a field box and its camera is calibrated (camera_<source>.json) -- the match is
tracked from the stream clip at once (process_match live=...), reconciled with TBA when it posts.
"""
import json, os, sys, time, traceback, shutil
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from tba import TBA, match_label, short_label, sort_key
import videos, identity, sitev2, integrity
import analytics as AN

DATA = os.environ.get("DATA", "/data")
MODEL = os.environ.get("MODEL", os.path.join(DATA, "models", "robot.onnx"))
POLL = int(os.environ.get("POLL_SECONDS", "120"))
TAG_CSV = os.path.join(HERE, "field", "2026-rebuilt-welded.csv")
API = os.path.join(DATA, "site", "api")
RETRY_NO_VIDEO = 300
V2 = os.environ.get("V2", "1") == "1"            # fuel/shot/analytics enrichment
MEDIA = os.environ.get("MEDIA", "/media")         # kept match videos, served at /media/ (NAS-backed)
KEEP_VIDEO = os.environ.get("KEEP_VIDEO", "1") == "1"
ID_CONF_MIN = float(os.environ.get("ID_CONF_MIN", "0.5"))   # below: shots/frames are not credited to a team
HUBCAM = os.environ.get("HUBCAM", "0") == "1"   # optional station-camera pass (~1-2 min/match extra)
MAX_ATTEMPTS = 3
VPLAN_S = float(os.environ.get("VPLAN_SECONDS", "60"))   # per poll: time for checking new candidate uploads (video plan)
VPLAN_N = int(os.environ.get("VPLAN_MAX", "12"))         # ... and at most this many uploads


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def jload(p, default=None):
    try:
        return json.load(open(p))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, p)


def ev_dir(ev): return os.path.join(DATA, "events", ev)


def ev_file(ev, name):
    """Data dir first, then the repo's events/<key>/ (shipped calibrations)."""
    p = os.path.join(ev_dir(ev), name)
    return p if os.path.exists(p) else os.path.join(REPO, "events", ev, name)


# ------------------------------------------------------------------ processing
def process_match(ev, m, cfg, status, meta_cache, live=None):
    """live: livestream input (liveingest.LiveView.for_processing) -> its clip is tried first (copied: the
    finally below deletes the working copies), then the per-match uploads as before."""
    st = status.setdefault(m["key"], {})
    if live is None and st.get("state") == "no_video" and time.time() - st.get("at", 0) < RETRY_NO_VIDEO:
        return False
    if live is not None and (not live["tba_posted"] or not os.path.exists(ev_file(ev, "camera.json"))):
        cands = []                                     # nothing uploaded yet / no upload calibration
    else:
        cands = videos.candidates(m, cfg, meta_cache)
    tries = ([("live", live["video"])] if live is not None else []) + [("upload", v) for v in cands[:3]]
    if not tries:
        st.update(state="no_video", at=time.time()); return False
    cache = os.environ.get("VIDEO_CACHE") or os.path.join(DATA, "cache"); os.makedirs(cache, exist_ok=True)
    st.update(state="processing", at=time.time(), video=tries[0][1]["id"], attempts=st.get("attempts", 0) + 1)
    jsave(os.path.join(ev_dir(ev), "status.json"), status)
    import process
    best, best_path, best_cam, errors, paths = None, None, None, [], []
    try:
        # a broadcast upload can be missing camera footage (feed dropped out);
        # try the next acceptable upload of the same match and keep the better one
        for kind, v in tries:
            vpath = os.path.join(cache, f"{m['key']}_{v['id']}.mp4"); paths.append(vpath)
            try:
                if kind == "live":                     # livestream clip: measured auto start, stream camera
                    log(f"{m['key']}: livestream clip {live['clip']} (auto start {live['start']:.2f}s, "
                        f"teams from {live['teams_from']})")
                    shutil.copyfile(live["clip"], vpath)
                    cam_p = live["camera"]
                    pose_p = os.path.join(ev_dir(ev), f"pose_latest_{live['source']}.json")
                    kw = dict(start_hint=live["start"], start_trusted=True, teams=live["teams"], layout=live["layout"],
                              event=None if m.get("livestream") else event_ctx(ev, m))
                else:
                    log(f"{m['key']}: downloading {v['id']} ({v['title']}"
                        f"{', ' + v['type'] + (' -- limited tracking' if v.get('limited') else '') if v.get('type') else ''})")
                    videos.download(v["id"], vpath)
                    cam_p, pose_p = ev_file(ev, "camera.json"), os.path.join(ev_dir(ev), "pose_latest.json")
                    kw = dict(start_hint=start_hint(ev, v), teams=match_teams(m), event=event_ctx(ev, m))
                    lay = upload_layout(ev, v)
                    if lay: kw["layout"] = lay
                log(f"{m['key']}: processing")
                t0 = time.time()
                extra = station_reads(ev, m, vpath, meta_cache, (cfg.get("video_plan") or {}).get(m["key"])) \
                    if HUBCAM and kind != "live" else None
                res = process.process(vpath, cam_p, MODEL, TAG_CSV, log=lambda *a: log(m["key"], *a),
                                      pose_guess=jload(pose_p), extra_reads=extra, **kw)
                pc = res.get("pose", {})
                if pc.get("tags", 0) >= 3 and (pc.get("applied") or (pc.get("moved_px") or 99) < 2.0):
                    jsave(pose_p, res["camera_pose"])          # verified pose: next match starts here
                res.update(key=m["key"], video=dict(id=v["id"], title=v["title"], channel=v["channel"],
                                                    **{k_: v[k_] for k_ in ("type", "limited") if v.get(k_)}),
                           processed_at=time.time(), seconds=round(time.time() - t0))
                if kind == "live":
                    res.update(source="livestream", camera_file=os.path.basename(cam_p), live=dict(live["info"]))
                log(f"{m['key']}: {v['id']} camera coverage {res['feed_ok']:.0%} in {time.time() - t0:.0f}s")
                ig = (res.get("identity") or {}).get("integrity")
                wrong = bool(ig) and integrity.should_retry(ig)
                if wrong:                                   # bumper reads say this is another match's video
                    fl = (ig.get("flags") or [{}])[0]
                    log(f"{m['key']}: {v['id']} looks like the wrong video (likely {fl.get('likely_match')}) -> trying next upload")
                    res["wrong_video"] = fl
                if best is None or (best.get("wrong_video") and not wrong) or \
                        (bool(best.get("wrong_video")) == wrong and res["feed_ok"] > best["feed_ok"] + 0.02):
                    best, best_path, best_cam = res, vpath, cam_p
                if res["feed_ok"] >= 0.9 and not wrong:
                    break
            except Exception as e:
                traceback.print_exc(); errors.append(str(e)[-300:])
        if best is not None and best.get("source") == "livestream" and best.get("wrong_video"):
            # the stream clip's bumpers say it is another match: never use this record again; uploads later
            st["live_rejected"] = live["info"]["record_path"]
            log(f"{m['key']}: livestream clip rejected (bumper reads: {best['wrong_video'].get('likely_match')})")
            st.update(state="no_video", at=time.time(), attempts=max(0, st.get("attempts", 1) - 1))
            return False
        if best is None and live is not None and not cands:
            st["live_failed"] = st.get("live_failed", 0) + 1     # 2 failures -> wait for the uploads instead
            st.update(state="no_video", at=time.time(), error="; ".join(errors)[-500:] or "livestream clip failed",
                      attempts=max(0, st.get("attempts", 1) - 1))
            return False
        if best is None:
            raise RuntimeError("; ".join(errors) or "no usable video")
        if best.get("source") == "livestream" and not live["tba_posted"]:
            st["live_reconcile"] = True                   # re-check teams / refresh shots once TBA posts
        jsave(os.path.join(ev_dir(ev), "matches", f"{m['key']}.json"), best)
        if V2:
            try:                                          # v2: fuel -> shots -> analytics
                import enrich
                # (before TBA posts, its match has score_breakdown: null, which shots.reconcile can't take)
                em = m if best.get("source") != "livestream" else \
                    {k_: v_ for k_, v_ in m.items() if not (k_ == "score_breakdown" and v_ is None)}
                enrich.enrich(ev_dir(ev), m["key"], best_path, best_cam, best, em,
                              log=lambda *a: log(m["key"], *a))
            except Exception:
                traceback.print_exc()                     # v1 data still published
        if KEEP_VIDEO and os.path.isdir(MEDIA):
            try:
                dst = os.path.join(MEDIA, ev, f"{m['key']}.mp4"); os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(best_path, dst)
                import proxies                            # MEDIA_PROXIES=1: small preview copy for client sync
                proxies.submit(MEDIA, ev, m["key"], log=lambda *a: log(m["key"], *a))
            except Exception as e:
                log(f"{m['key']}: could not keep video: {e}")
        if HUBCAM and KEEP_VIDEO and V2 and best.get("source") != "livestream":
            try:                                          # optional: driver-station cams (hardware/time permitting)
                import enrich
                enrich.station_pass(DATA, MEDIA, ev, m["key"], best, m, meta_cache, log=lambda *a: log(m["key"], *a))
            except Exception:
                traceback.print_exc()
        st.update(state="done", at=time.time(), error=None, video=best["video"]["id"], source=best.get("source", "upload"))
        log(f"{m['key']}: done ({best['video']['id']}, coverage {best['feed_ok']:.0%})")
        return True
    except Exception as e:
        st.update(state="failed" if st["attempts"] >= MAX_ATTEMPTS else "error", at=time.time(),
                  error=str(e)[-500:])
        return False
    finally:
        for vp in paths:
            if os.path.exists(vp): os.remove(vp)
        jsave(os.path.join(ev_dir(ev), "status.json"), status)


def station_reads(ev, m, wide_path, meta_cache, plan=None):
    """HUBCAM: fetch the "Match N" inset upload (kept in MEDIA/<ev>/insets) and return the station-camera
    bumper-OCR callable for the tracker -- ~3x more reads, 95.6% -> 98.5% answered identity on the dev set.
    Never fails the match: no inset -> no extra reads."""
    try:
        import stationcam, station_ocr
        inset = os.path.join(MEDIA, ev, "insets", f"{m['key']}.mp4")
        if not os.path.exists(inset):
            v = stationcam.find_inset_video(m, meta_cache, plan=plan)
            if not v:
                return None
            os.makedirs(os.path.dirname(inset), exist_ok=True)
            videos.download(v["id"], inset)
        cal = os.path.dirname(ev_file(ev, "station_red.json"))
        return station_ocr.extra_reads_fn(cal, inset, wide_video=wide_path)
    except Exception:
        traceback.print_exc(); return None


def event_ctx(ev, m):
    """Event context for open-set OCR + match integrity (wrong video / substitution checks)."""
    try:
        t = TBA(os.environ["TBA_KEY"], os.path.join(DATA, "tba"))
        return {"teams": [x["key"] for x in t.teams(ev)], "matches": t.matches(ev), "key": m["key"]}
    except Exception:
        return None


def match_teams(m):
    """TBA station order: red 1-3, blue 1-3 (the tracker labels its paths with these)."""
    return {a: m["alliances"][a]["team_keys"] for a in ("red", "blue")}


def start_hint(ev, v):
    """Median detected match start of earlier uploads from the same channel with the
    same kind of title ("Wide Match" vs "Qualification"): cuts are consistent per source."""
    ts = []
    d = os.path.join(ev_dir(ev), "matches")
    for f in os.listdir(d) if os.path.isdir(d) else []:
        r = jload(os.path.join(d, f), {})
        vv = r.get("video", {})
        if r.get("start_source") in ("clock", "motion") and vv.get("channel") == v["channel"] \
                and vv.get("title", "").split()[:1] == v["title"].split()[:1]:
            ts.append(r["t_start"])
    return float(np.median(ts)) if len(ts) >= 3 else None


def is_calibrated(ev):
    """The competition can be tracked: an upload camera (camera.json, data dir or the repo's shipped one) or a
    livestream camera (camera_<source>.json).  Without one, matches wait ("needs Video setup") -- never an error."""
    if os.path.exists(ev_file(ev, "camera.json")):
        return True
    try:
        return any(f.startswith("camera_") and f.endswith(".json") for f in os.listdir(ev_dir(ev)))
    except OSError:
        return False


# ------------------------------------------------------------------ site JSON
GRID = 0.5   # heatmap cell, metres


def team_num(k): return int(k[3:])


def build_site(ev, tba):
    info = tba.event(ev); ms = sorted(tba.matches(ev), key=sort_key)
    lv = live_view(ev)                                 # livestream records: fast results + not-yet-listed matches
    if lv:
        ms = sorted(ms + lv.extra_matches({m["key"] for m in ms}), key=sort_key)
    teams = {t["key"]: t for t in tba.teams(ev)}
    status = jload(os.path.join(ev_dir(ev), "status.json"), {})
    mdir = os.path.join(ev_dir(ev), "matches")
    results = {}
    for m in ms:
        r = jload(os.path.join(mdir, f"{m['key']}.json"))
        if r: results[m["key"]] = r
    ids = identity.assign_event(results, ms, jload(os.path.join(ev_dir(ev), "overrides.json"), {}))
    try:
        oprs = tba.oprs(ev) or {}
    except Exception:
        oprs = {}
    try:
        rk = {r["team_key"]: r["rank"] for r in (tba.rankings(ev) or {}).get("rankings", [])}
    except Exception:
        rk = {}

    out_dir = os.path.join(API, ev)
    per_team = {k: dict(matches=[], autos=[], heat=None, an=[], an_web=[], shots=[], intakes=[], auto_paths=[])
                for k in teams}
    rules = jload(os.path.join(HERE, "field", "rebuilt_rules.json"), {}) or {}
    L, W = 16.541, 8.069
    nx, ny = int(np.ceil(L / GRID)), int(np.ceil(W / GRID))
    match_rows = []
    for m in ms:
        k = m["key"]; r = results.get(k); st = status.get(k, {})
        row = dict(key=k, label=match_label(m), short=short_label(m), level=m["comp_level"],
                   time=m.get("actual_time") or m.get("time"),
                   red=dict(teams=m["alliances"]["red"]["team_keys"], score=m["alliances"]["red"]["score"]),
                   blue=dict(teams=m["alliances"]["blue"]["team_keys"], score=m["alliances"]["blue"]["score"]),
                   winner=m.get("winning_alliance"), state="done" if r else st.get("state", "pending"))
        if lv:
            lv.decorate_row(row, m)                    # row["live"]; stream teams/score until TBA posts
        if r:
            lab = ids.get(k, {})
            row["id_conf"] = round(min([lab.get(a, {}).get("confidence", 0) for a in ("red", "blue")]), 2)
            row["feed_ok"] = r.get("feed_ok", 1.0)
            tracks = []
            for t in r["tracks"]:
                a = lab.get(t["alliance"], {})
                team = a.get("labels", {}).get(t["idx"])
                tracks.append(dict(alliance=t["alliance"], idx=t["idx"], team=team,
                                   id_conf=t.get("team_conf", a.get("confidence")), manual=a.get("manual", False),
                                   team_conf=t.get("team_conf"), conf=t.get("conf"),
                                   reads=((r.get("identity") or {}).get("per_team_reads") or {}).get(team),
                                   xy=t["xy"], obs=t["obs"], stats=t["stats"],
                                   height=t["feat"].get("height")))
                if team in per_team:
                    # team pages: everything from the team's own end (red end = left);
                    # the field is point-symmetric, so blue is a 180 degree rotation
                    xy = np.array(t["xy"], float) / 100.0
                    if t["alliance"] == "blue":
                        xy = np.c_[L - xy[:, 0], W - xy[:, 1]]
                    hz = r["hz"]; fa = int(r["t_auto_end"] * hz); ft = int(r["t_teleop_start"] * hz)
                    if r.get("auto_feed_ok", 1.0) >= 0.7:        # skip autos the camera didn't film
                        per_team[team]["autos"].append(dict(
                            match=k, short=row["short"], alliance=t["alliance"],
                            xy=np.round(xy[:fa + 1:2] * 100).astype(int).tolist(),
                            obs=t["obs"][:fa + 1:2], conf=a.get("confidence")))
                    live = np.ones(len(xy), bool)
                    for g0, g1 in r.get("feed_gaps", []):
                        live[int(g0 * hz):int(g1 * hz) + 1] = False
                    H = np.histogram2d(xy[ft:][live[ft:], 0], xy[ft:][live[ft:], 1], bins=[nx, ny], range=[[0, L], [0, W]])[0]
                    per_team[team]["heat"] = H if per_team[team]["heat"] is None else per_team[team]["heat"] + H
                    per_team[team]["matches"].append(dict(match=k, short=row["short"], alliance=t["alliance"],
                                                          conf=a.get("confidence"), **t["stats"]))
            # ---- v2: shots + per-robot analytics (when the match has been enriched)
            evs = jload(os.path.join(ev_dir(ev), "shots", f"{k}.json"))
            an = jload(os.path.join(ev_dir(ev), "analytics", f"{k}.json"))
            team_of = {t["idx"]: t["team"] for t in tracks}
            conf_of = {t["idx"]: t.get("conf") for t in tracks}
            alli_of = {t["idx"]: t["alliance"] for t in tracks}
            v2 = {}
            if evs:
                shots_out = []
                for sh in evs.get("shots", []):
                    who = sh.get("shooter") or {}
                    ti = who.get("track_idx")
                    team = team_of.get(ti) if who.get("kind") == "robot" else None
                    unattr = None
                    if team is not None:
                        cf = conf_of.get(ti)
                        if cf:                                   # per-frame identity conf at launch
                            fi = int(round((sh["t"] - (r["t_start"] - 0.5)) * r["hz"]))
                            if cf[max(0, min(len(cf) - 1, fi))] < ID_CONF_MIN:
                                team, unattr = None, "low_identity_conf"
                    shots_out.append({kk: sh.get(kk) for kk in ("t", "match_t", "period", "shooter", "origin", "v0", "acc", "drag",
                                                               "landing", "target", "outcome", "hub_active", "conf",
                                                               "apex_z", "t_entry", "t_obs", "outcome_conf")}
                                     | {"team": team, "unattributed": unattr})
                    if team in per_team and sh.get("origin"):
                        al = alli_of.get(ti, "red")
                        o = sitev2.team_frame(sh["origin"], al); ld = sitev2.team_frame(sh.get("landing") or sh["origin"], al)
                        per_team[team]["shots"].append({"xy": [round(o[0] * 100), round(o[1] * 100)],
                                                        "land": [round(ld[0] * 100), round(ld[1] * 100)],
                                                        "o": sh.get("outcome"), "p": sh.get("period"), "m": row["short"]})
                for it in evs.get("intakes", []) or []:
                    team = team_of.get(it.get("track_idx"))
                    if team in per_team and it.get("xy"):
                        x, y = sitev2.team_frame(it["xy"], alli_of.get(it.get("track_idx"), "red"))
                        per_team[team]["intakes"].append([round(x * 100), round(y * 100)])
                v2["shots"] = shots_out
                v2["intakes"] = evs.get("intakes", []) or []
                v2["reconcile"] = evs.get("reconcile")
                act = evs.get("hub_active") or {}
                per = rules.get("periods") or []
                v2["periods"] = [{"name": pp["name"], "t0": pp["t0"], "t1": pp["t1"],
                                  "active": {a_: (act.get(a_) or {}).get(pp["name"]) for a_ in ("red", "blue")}}
                                 for pp in per]
            if an:
                at = an.get("tracks") or {}
                web = []
                for t in tracks:
                    a_ = at.get(str(t["idx"])) or at.get(t["idx"]) or {}
                    if a_:
                        a_.setdefault("meta", {})["team"] = t["team"]
                    w = sitev2.web_track_analytics(a_) if a_ else None
                    web.append(w)
                    if a_ and t["team"] in per_team:
                        per_team[t["team"]]["an"].append(a_); per_team[t["team"]]["an_web"].append(w)
                        au = a_.get("auto") or {}
                        if au.get("path"):
                            per_team[t["team"]]["auto_paths"].append({"match": k, "alliance": t["alliance"],
                                                                      "path": au["path"], "coverage": au.get("coverage") or 0})
                    for mm_ in per_team.get(t["team"], {}).get("matches", [])[-1:]:
                        if mm_["match"] == k and w: mm_["analytics"] = w
                v2["analytics"] = web
            cal = jload(ev_file(ev, r.get("camera_file") or "camera.json"), {}) or {}   # stream camera if tracked live
            if cal:
                pose = r.get("camera_pose") or {}
                v2["camera"] = {"W": cal["W"], "H": cal["H"], "K": cal["K"], "dist": cal["dist"],
                                "rvec": pose.get("rvec", cal["rvec"]), "tvec": pose.get("tvec", cal["tvec"])}
            fu = jload(os.path.join(ev_dir(ev), "fuel", f"{k}.json"))
            if fu is not None:
                jsave(os.path.join(out_dir, "m", f"{k}.fuel.json"), fu)
                v2["fuel_url"] = f"{k}.fuel.json"
            if os.path.exists(os.path.join(MEDIA, ev, f"{k}.mp4")):
                v2["video_local"] = f"media/{ev}/{k}.mp4"
            stn = jload(os.path.join(ev_dir(ev), "stations", f"{k}.json"))
            if stn and os.path.exists(os.path.join(MEDIA, ev, "insets", f"{k}.mp4")):
                v2["stations"] = stn                      # station cams for the Analyze camera switcher
            sb = m.get("score_breakdown") or {}
            if row.get("live"):
                v2["live"] = row["live"]
                v2["score_source"] = row.get("score_source")
            jsave(os.path.join(out_dir, "m", f"{k}.json"), dict(**v2,
                key=k, label=row["label"], short=row["short"], event=ev, hz=r["hz"], frames=r["frames"],
                t_start=r["t_start"], start_detected=r.get("start_detected"),
                t_auto_end=r["t_auto_end"], t_teleop_start=r["t_teleop_start"], video=r["video"],
                feed_ok=r.get("feed_ok", 1.0), feed_gaps=r.get("feed_gaps", []),
                field=r["field"], red=row["red"], blue=row["blue"], winner=row["winner"],
                breakdown={a: {kk: vv for kk, vv in (sb.get(a) or {}).items() if not isinstance(vv, dict)}
                           for a in ("red", "blue")},
                tracks=tracks, identity=r.get("identity")))
        match_rows.append(row)

    try:
        routines = AN.cluster_autos({tk: pt["auto_paths"] for tk, pt in per_team.items() if pt["auto_paths"]})
    except Exception:
        traceback.print_exc(); routines = {}
    team_rows = []
    for tk, t in teams.items():
        pt = per_team[tk]; mm = pt["matches"]
        def avg(f):
            v = [x[f] for x in mm if x.get(f) is not None]
            return round(float(np.mean(v)), 2) if v else None
        row = dict(key=tk, number=team_num(tk), nickname=t.get("nickname"), rank=rk.get(tk),
                   opr=round(oprs.get("oprs", {}).get(tk, 0), 1) if oprs.get("oprs") else None,
                   tracked=len(mm), distance_m=avg("distance_m"), auto_distance_m=avg("auto_distance_m"),
                   max_speed=avg("max_speed"), time_own_zone=avg("time_own_zone"),
                   time_neutral=avg("time_neutral"), time_opp_zone=avg("time_opp_zone"),
                   id_conf=round(float(np.mean([x["conf"] or 0 for x in mm])), 2) if mm else None)
        heat = pt["heat"]
        tv2 = {}
        if pt["an"]:
            try:
                agg = AN.team_aggregate(pt["an"])
                tv2["analytics"] = sitev2.web_team_analytics(agg, pt["an_web"], row["id_conf"] or 0.0)
            except Exception:
                traceback.print_exc()
        if pt["shots"]: tv2["shots_map"] = pt["shots"]
        if pt["intakes"]: tv2["intakes_map"] = pt["intakes"]
        rt = routines.get(tk)
        if rt:
            short = {x["match"]: x["short"] for x in pt["autos"]}
            for au in pt["autos"]:
                au["routine"] = rt["labels"].get(au["match"])
            tv2["routines"] = [{"id": r_["id"], "name": f"Auto {r_['id']}", "n": r_["n"], "of": rt.get("n_autos"),
                                "freq": r_.get("freq"), "matches": [short.get(x, x) for x in r_.get("matches", [])],
                                "xy": [[round(a_ * 100), round(b_ * 100)] for a_, b_ in r_.get("rep_path_red_frame", [])],
                                "start_xy": r_.get("start"), "end_xy": r_.get("end"),
                                "kind": r_.get("kind"), "consistency": r_.get("mean_intra_dist")}
                               for r_ in rt.get("routines", [])]
        row_q = dict(row, **tv2)
        row["q"] = sitev2.event_q(row_q)
        team_rows.append(row)
        jsave(os.path.join(out_dir, "t", f"{tk}.json"), dict(
            **row, **tv2, event=ev, matches=mm, autos=pt["autos"], grid=GRID,
            heat=None if heat is None else (heat / max(heat.sum(), 1)).round(5).tolist()))
    team_rows.sort(key=lambda r: (r["rank"] or 999, r["number"]))
    n_done = sum(1 for r in match_rows if r["state"] == "done")
    calibrated = is_calibrated(ev)
    now = time.time()
    jsave(os.path.join(out_dir, "event.json"), dict(
        key=ev, name=info.get("name"), short_name=info.get("short_name"), year=info.get("year"),
        start=info.get("start_date"), end=info.get("end_date"), city=info.get("city"),
        state_prov=info.get("state_prov"), updated=now, n_done=n_done, n_total=len(match_rows),
        calibrated=calibrated, needs_setup=not calibrated, matches=match_rows, teams=team_rows,
        livestream=live_summary(ev)))
    evs = jload(os.path.join(API, "events.json"), [])
    evs = [e for e in evs if e["key"] != ev] + [dict(key=ev, name=info.get("name"), short_name=info.get("short_name"),
                                                      year=info.get("year"),
                                                      start=info.get("start_date"), end=info.get("end_date"),
                                                      city=info.get("city"), state_prov=info.get("state_prov"),
                                                      n_done=n_done, n_total=len(match_rows), updated=now,
                                                      calibrated=calibrated)]
    jsave(os.path.join(API, "events.json"), sorted(evs, key=lambda e: e.get("start") or "", reverse=True))
    if os.environ.get("DASHBOARD", "1") == "1":       # strategy dashboard (EPA prior + video), replay snapshots
        try:
            import subprocess
            subprocess.run([sys.executable, "-m", "live.strategy.dashboard", DATA, ev, "--site",
                            "--snapshots", "--only-missing"], cwd=REPO, timeout=900, check=False)
        except Exception:
            traceback.print_exc()
    try:                                               # team spec sheets, defense library, scout.json (before goals:
        import scouting                                # the Selection pitch cards read scout.json)
        scouting.site_build_extra(DATA, ev, log=log)
        import subprocess                              # scout.json replay snapshots (as-of picklist), ~3 s each
        subprocess.run([sys.executable, "-m", "live.scouting", DATA, ev, "--snapshots"], cwd=REPO, timeout=900, check=False)
    except Exception:
        traceback.print_exc()
    try:                                               # rankings, district points, season goal, schedule, selection
        sys.path.insert(0, REPO)
        from live.strategy import goals
        goals.site_build(DATA, ev, team=os.environ.get("DASH_TEAM", "frc4911"))
        import subprocess                              # selection replay snapshots (only the missing ones, ~4 s each)
        subprocess.run([sys.executable, "-m", "live.strategy.goals", DATA, ev, "--selection-snapshots"],
                       cwd=REPO, timeout=900, check=False)
    except Exception:
        traceback.print_exc()
    log(f"{ev}: site rebuilt ({n_done}/{len(match_rows)} matches tracked)")


# ------------------------------------------------------------------ livestream (live/liveingest.py)
def live_view(ev):
    """The event's livestream records (None when there are none / the module is unavailable)."""
    try:
        import liveingest
        v = liveingest.LiveView(ev)
        return v if v else None
    except Exception:
        traceback.print_exc(); return None


def live_summary(ev):
    try:
        import liveingest
        return liveingest.status_summary(ev, DATA)
    except Exception:
        return None


def live_input(lv, m, played, status):
    """Livestream clip input for process_match, unless this record was rejected / failed before."""
    if lv is None: return None
    try:
        live = lv.for_processing(m, played)
    except Exception:
        traceback.print_exc(); return None
    if live is None: return None
    st = status.get(m["key"], {})
    if st.get("live_rejected") == live["info"]["record_path"] or st.get("live_failed", 0) >= 2:
        return None
    return live


def live_reconcile(ev, m, status):
    """TBA posted a match that was tracked from the livestream before it did: if TBA's lineup differs from
    the teams used (stream OCR / substitution), re-track (the result moves to <key>.live-teams.json); else
    re-run the v2 enrichment with TBA's score breakdown (hub-active shifts, scoring reconciliation)."""
    st = status.setdefault(m["key"], {})
    p = os.path.join(ev_dir(ev), "matches", f"{m['key']}.json")
    r = jload(p)
    st.pop("live_reconcile", None)
    if not r or r.get("source") != "livestream":
        return
    li = r.setdefault("live", {})
    tba_teams = match_teams(m)
    li["tba_score"] = {a: m["alliances"][a]["score"] for a in ("red", "blue")}
    if li.get("teams") and li["teams"] != tba_teams:
        log(f"{m['key']}: TBA lineup {tba_teams} != stream teams {li['teams']}: re-tracking")
        os.replace(p, p[:-5] + ".live-teams.json")
        st.update(state="pending", at=time.time())
        return
    if V2:
        vid = os.path.join(MEDIA, ev, f"{m['key']}.mp4")
        vid = vid if os.path.exists(vid) else li.get("clip")
        if vid and os.path.exists(vid):
            try:
                import enrich
                enrich.enrich(ev_dir(ev), m["key"], vid, ev_file(ev, r.get("camera_file") or "camera.json"), r, m,
                              log=lambda *a: log(m["key"], *a))
            except Exception:
                traceback.print_exc()
    li.update(tba_posted=True, reconciled_at=time.time())
    jsave(p, r)
    jsave(os.path.join(ev_dir(ev), "status.json"), status)
    log(f"{m['key']}: reconciled with TBA (stream {li.get('stream_score')} / TBA {li['tba_score']})")


# ------------------------------------------------------------------ loop
def video_plan(ev, tba, tba_ms, cfg):
    """TBA discovery on its own (webcasts -> livestream sources, upload families; throttled) + the per-match
    video plan (live/videoplan.py: a few new candidate uploads checked per poll, time-boxed) -> cfg["video_plan"]
    for videos.candidates.  Never fails the sync: without a plan the title rules apply."""
    try:
        import sources, videoplan
        try:
            sources.auto_discover(DATA, ev, tba.event(ev), tba_ms, log=log)
        except Exception:
            traceback.print_exc()
        md = os.path.join(ev_dir(ev), "matches")
        done = {f[:-5] for f in os.listdir(md) if f.endswith(".json")} if os.path.isdir(md) else set()
        plan = videoplan.refresh(DATA, ev, tba_ms, budget_s=VPLAN_S, max_n=VPLAN_N, log=log, done_keys=done)
        cfg["video_plan"] = plan.get("matches") or {}
    except Exception:
        traceback.print_exc()
    return cfg


def upload_layout(ev, v):
    """process(layout=...) for a plan-chosen upload (videoplan.layout_for: its type's drawn layout, or detected
    station insets masked); None = the event's default layout lookup."""
    if not (isinstance(v, dict) and v.get("plan")): return None
    try:
        import videoplan
        return videoplan.layout_for(ev_dir(ev), v)
    except Exception:
        traceback.print_exc(); return None


def sync(ev, tba, max_new=None):
    tba_ms = tba.matches(ev)
    ms = sorted(tba_ms, key=lambda m: (m.get("actual_time") or m.get("time") or 0, sort_key(m)))
    lv = live_view(ev)                                 # livestream records (live/liveingest.py)
    if lv:
        ms = sorted(ms + lv.extra_matches({m["key"] for m in ms}),
                    key=lambda m: (m.get("actual_time") or m.get("time") or 0, sort_key(m)))
    status = jload(os.path.join(ev_dir(ev), "status.json"), {})
    cfg = jload(ev_file(ev, "event.json"), {}) or {}
    try:                                               # admin-configured video sources (Video page) -> which uploads to use
        import sources
        cfg = sources.video_config(sources.load(ev_dir(ev)), cfg, jload(os.path.join(ev_dir(ev), "layout.json"), None))
    except Exception:
        traceback.print_exc()
    cfg = video_plan(ev, tba, tba_ms, cfg)
    meta_cache = jload(os.path.join(DATA, "cache", "ytmeta.json"), {}) or {}
    has_cam = os.path.exists(ev_file(ev, "camera.json"))
    if not has_cam and ev not in _NEEDS_SETUP_LOGGED:            # once per event: not an error, just not set up yet
        _NEEDS_SETUP_LOGGED.add(ev)
        log(f"{ev}: needs Video setup (no camera calibration): match tracking waits; results/rankings still published")
    elif has_cam:
        _NEEDS_SETUP_LOGGED.discard(ev)
    done_any = False; n = 0; more = False
    for m in ms:
        played = m["alliances"]["red"]["score"] is not None and m["alliances"]["red"]["score"] >= 0 \
            and not m.get("livestream")
        live = live_input(lv, m, played, status)       # trackable livestream clip: process now, TBA or not
        if not has_cam and live is None: continue
        if not played and live is None: continue
        if played and status.get(m["key"], {}).get("live_reconcile"):
            live_reconcile(ev, m, status)              # TBA posted a match tracked from the stream
        if os.path.exists(os.path.join(ev_dir(ev), "matches", f"{m['key']}.json")): continue
        if status.get(m["key"], {}).get("state") == "failed": continue
        if process_match(ev, m, cfg, status, meta_cache, live=live):
            done_any = True; n += 1
            jsave(os.path.join(DATA, "cache", "ytmeta.json"), meta_cache)
            build_site(ev, tba)
            if max_new and n >= max_new:
                more = True; break
    jsave(os.path.join(DATA, "cache", "ytmeta.json"), meta_cache)
    if not done_any:
        build_site(ev, tba)
    return more                                        # True: stopped at max_new (there may be more to do)


_NEEDS_SETUP_LOGGED = set()


# ------------------------------------------------------------------ several competitions
def comp_dates(ev, tba):
    """(start, end) ISO dates of an event from TBA (cached an hour), (None, None) when unknown."""
    try:
        e = tba.event(ev) or {}
        return e.get("start_date"), e.get("end_date") or e.get("start_date")
    except Exception:
        return None, None


def comp_order(events, tba, today=None):
    """-> (ordered events, live set). Processing order: competitions on now first (earliest start first), then
    upcoming ones (soonest first), then finished ones (most recent first), unknown dates last. Each competition keeps
    its own data, calibration and site; the order only decides who gets the GPU first."""
    import datetime as _dt
    today = today or _dt.date.today().isoformat()
    live, upcoming, done, unknown = [], [], [], []
    for ev in events:
        s, e = comp_dates(ev, tba)
        if not s: unknown.append(ev)
        elif s <= today <= (e or s): live.append((s, ev))
        elif today < s: upcoming.append((s, ev))
        else: done.append((e or s, ev))
    order = [ev for _, ev in sorted(live)] + [ev for _, ev in sorted(upcoming)] + \
            [ev for _, ev in sorted(done, reverse=True)] + unknown
    return order, {ev for _, ev in live}


# while a competition is on, others get at most this many new matches per pass, so the loop comes back to the
# live one within a match or two (a backlog at a finished event never holds up today's matches)
BACKLOG_BATCH = int(os.environ.get("COMP_BACKLOG_BATCH", "2"))


def sync_all(events, tba, max_new=None):
    """One pass over every followed competition (live first). True when work was left over (don't sleep long)."""
    order, live = comp_order(events, tba)
    more = False
    for ev in order:
        cap = max_new
        if live and ev not in live:
            cap = min(cap, BACKLOG_BATCH) if cap else BACKLOG_BATCH
        try:
            more = bool(sync(ev, tba, cap)) or more
        except Exception:
            traceback.print_exc()
    return more


# ------------------------------------------------------------------ config (platform agent)
WORKER_CONF = os.path.join(DATA, "config", "worker.json")   # written by the api service on every settings change
_ENV0 = {k: os.environ.get(k) for k in ("TBA_KEY", "GPU_DEVICE", "FORCE_CPU", "DASH_TEAM", "DASH_GOAL")}


def engine_goal(season_goal):
    """App season goal (setup wizard / Admin: "DCMP", "Worlds", "Einstein", "Win an event", "Scouting", free text) ->
    the goals engine's goal (DCMP | Worlds | rank:N), or None when it has no equivalent (env / DCMP default then)."""
    g = str(season_goal or "").strip().lower()
    if g in ("dcmp", "district championship") or "dcmp" in g or "district champ" in g: return "DCMP"
    if g.startswith(("worlds", "einstein", "champ")) or "worlds" in g or "einstein" in g: return "Worlds"
    if g.startswith("rank:") and g[5:].isdigit() and int(g[5:]) > 0: return f"rank:{int(g[5:])}"
    return None


def _setenv(k, v):
    if v is None: os.environ.pop(k, None)
    else: os.environ[k] = v


def load_config():
    """-> (tba_key, events). App settings (DATA/config/worker.json, see server/workerconf.py) win; anything unset
    there falls back to the .env (TBA_KEY, EVENTS, POLL_SECONDS, HUBCAM, GPU_DEVICE). Called every poll, so a
    settings change applies on the next poll without a restart. Also updates POLL / HUBCAM and the env that
    downstream code reads (TBA_KEY for event_ctx + strategy, GPU_DEVICE / FORCE_CPU for the detector)."""
    global POLL, HUBCAM
    c = jload(WORKER_CONF, {}) or {}
    o = c.get("options") or {}
    key = c.get("tba_key") or _ENV0["TBA_KEY"] or ""
    events = c.get("events")
    if events is None:
        events = [e.strip() for e in os.environ.get("EVENTS", "").split(",") if e.strip()]
    POLL = int(o["poll_seconds"]) if o.get("poll_seconds") else int(os.environ.get("POLL_SECONDS", "120"))
    HUBCAM = bool(o["hubcam"]) if o.get("hubcam") is not None else os.environ.get("HUBCAM", "0") == "1"
    _setenv("TBA_KEY", key or None)
    _setenv("GPU_DEVICE", str(int(o["gpu_device"])) if o.get("gpu_device") is not None else _ENV0["GPU_DEVICE"])
    _setenv("FORCE_CPU", ("1" if o["cpu_only"] else None) if o.get("cpu_only") is not None else _ENV0["FORCE_CPU"])
    try:                                     # team number + season goal (app settings) -> dashboard / goals perspective
        tn = int(c["team_number"]) if c.get("team_number") else None
    except (TypeError, ValueError):
        tn = None
    _setenv("DASH_TEAM", f"frc{tn}" if tn else _ENV0["DASH_TEAM"])
    _setenv("DASH_GOAL", engine_goal(c.get("season_goal")) or _ENV0["DASH_GOAL"])   # app setting wins, env = fallback
    return key, list(events)


def _conf_mtime():
    try: return os.path.getmtime(WORKER_CONF)
    except OSError: return None


def _live_sig(events):
    try:
        import liveingest
        return tuple(liveingest.records_signature(ev) for ev in events or [])
    except Exception:
        return None


def _sleep(seconds, events=None):
    """Sleep, but wake early when the app settings change (e.g. an event was just added) or the livestream
    recorder wrote a new match record (process it now, not at the next poll)."""
    m0, l0, t_end = _conf_mtime(), _live_sig(events), time.time() + seconds
    while time.time() < t_end:
        time.sleep(min(5, max(0.1, t_end - time.time())))
        if _conf_mtime() != m0: return
        if events and _live_sig(events) != l0: return


def main():
    if "--recorder" in sys.argv[1:]:                  # compose service `recorder` (same image): live streams
        import liveingest
        return liveingest.Service(DATA, log=lambda *a: log("recorder:", *a)).run()
    try:                                               # Video page jobs: frame grabs, layout suggestions, calibration fits
        import threading, sources
        threading.Thread(target=sources.job_loop, args=(DATA,), kwargs={"log": log}, daemon=True).start()
    except Exception:
        traceback.print_exc()
    tba, waiting = None, None
    while True:
        key, events = load_config()
        why = ("match processing paused (PAUSE_PROCESSING=1)" if os.environ.get("PAUSE_PROCESSING") == "1" else
               "no TBA key (finish setup in the web app, or set TBA_KEY)" if not key else
               "no events followed (add one in the web app, or set EVENTS)" if not events else
               f"model not found: {MODEL}" if not os.path.exists(MODEL) else None)
        if why:
            if os.environ.get("ONCE") == "1": sys.exit(why)
            if why != waiting: log("waiting:", why)
            waiting = why; _sleep(30); continue
        if waiting: log("configured: following", ", ".join(events)); waiting = None
        if tba is None or tba.key != key:
            tba = TBA(key, os.path.join(DATA, "tba"))
        more = sync_all(events, tba, int(os.environ["MAX_NEW"]) if os.environ.get("MAX_NEW") else None)
        if os.environ.get("ONCE") == "1": break
        _sleep(5 if more and not os.environ.get("MAX_NEW") else POLL, events)   # backlog left: next pass soon


if __name__ == "__main__":
    main()
