"""autoscout-live worker: poll TBA, process each newly published match, publish JSON.

Environment:
  TBA_KEY        TBA read key (required)
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
"""
import json, os, sys, time, traceback, shutil
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from tba import TBA, match_label, short_label, sort_key
import videos, identity

DATA = os.environ.get("DATA", "/data")
MODEL = os.environ.get("MODEL", os.path.join(DATA, "models", "robot.onnx"))
POLL = int(os.environ.get("POLL_SECONDS", "120"))
TAG_CSV = os.path.join(HERE, "field", "2026-rebuilt-welded.csv")
API = os.path.join(DATA, "site", "api")
RETRY_NO_VIDEO = 300
MAX_ATTEMPTS = 3


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
def process_match(ev, m, cfg, status, meta_cache):
    st = status.setdefault(m["key"], {})
    if st.get("state") == "no_video" and time.time() - st.get("at", 0) < RETRY_NO_VIDEO:
        return False
    cands = videos.candidates(m, cfg, meta_cache)
    if not cands:
        st.update(state="no_video", at=time.time()); return False
    cache = os.path.join(DATA, "cache"); os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, f"{m['key']}.mp4")
    st.update(state="processing", at=time.time(), video=cands[0]["id"], attempts=st.get("attempts", 0) + 1)
    jsave(os.path.join(ev_dir(ev), "status.json"), status)
    import process
    best, errors = None, []
    try:
        # a broadcast upload can be missing camera footage (feed dropped out);
        # try the next acceptable upload of the same match and keep the better one
        for v in cands[:3]:
            try:
                log(f"{m['key']}: downloading {v['id']} ({v['title']})")
                videos.download(v["id"], path)
                log(f"{m['key']}: processing")
                t0 = time.time()
                pose_p = os.path.join(ev_dir(ev), "pose_latest.json")
                res = process.process(path, ev_file(ev, "camera.json"), MODEL, TAG_CSV,
                                      log=lambda *a: log(m["key"], *a), start_hint=start_hint(ev, v),
                                      pose_guess=jload(pose_p))
                pc = res.get("pose", {})
                if pc.get("tags", 0) >= 3 and (pc.get("applied") or (pc.get("moved_px") or 99) < 2.0):
                    jsave(pose_p, res["camera_pose"])          # verified pose: next match starts here
                res.update(key=m["key"], video=dict(id=v["id"], title=v["title"], channel=v["channel"]),
                           processed_at=time.time(), seconds=round(time.time() - t0))
                log(f"{m['key']}: {v['id']} camera coverage {res['feed_ok']:.0%} in {time.time() - t0:.0f}s")
                if best is None or res["feed_ok"] > best["feed_ok"] + 0.02:
                    best = res
                if res["feed_ok"] >= 0.9:
                    break
            except Exception as e:
                traceback.print_exc(); errors.append(str(e)[-300:])
            finally:
                if os.path.exists(path): os.remove(path)
        if best is None:
            raise RuntimeError("; ".join(errors) or "no usable video")
        jsave(os.path.join(ev_dir(ev), "matches", f"{m['key']}.json"), best)
        st.update(state="done", at=time.time(), error=None, video=best["video"]["id"])
        log(f"{m['key']}: done ({best['video']['id']}, coverage {best['feed_ok']:.0%})")
        return True
    except Exception as e:
        st.update(state="failed" if st["attempts"] >= MAX_ATTEMPTS else "error", at=time.time(),
                  error=str(e)[-500:])
        return False
    finally:
        jsave(os.path.join(ev_dir(ev), "status.json"), status)


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


# ------------------------------------------------------------------ site JSON
GRID = 0.5   # heatmap cell, metres


def team_num(k): return int(k[3:])


def build_site(ev, tba):
    info = tba.event(ev); ms = sorted(tba.matches(ev), key=sort_key)
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
    per_team = {k: dict(matches=[], autos=[], heat=None) for k in teams}
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
        if r:
            lab = ids.get(k, {})
            row["id_conf"] = round(min([lab.get(a, {}).get("confidence", 0) for a in ("red", "blue")]), 2)
            row["feed_ok"] = r.get("feed_ok", 1.0)
            tracks = []
            for t in r["tracks"]:
                a = lab.get(t["alliance"], {})
                team = a.get("labels", {}).get(t["idx"])
                tracks.append(dict(alliance=t["alliance"], idx=t["idx"], team=team,
                                   id_conf=a.get("confidence"), manual=a.get("manual", False),
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
            sb = m.get("score_breakdown") or {}
            jsave(os.path.join(out_dir, "m", f"{k}.json"), dict(
                key=k, label=row["label"], short=row["short"], event=ev, hz=r["hz"], frames=r["frames"],
                t_start=r["t_start"], start_detected=r.get("start_detected"),
                t_auto_end=r["t_auto_end"], t_teleop_start=r["t_teleop_start"], video=r["video"],
                feed_ok=r.get("feed_ok", 1.0), feed_gaps=r.get("feed_gaps", []),
                field=r["field"], red=row["red"], blue=row["blue"], winner=row["winner"],
                breakdown={a: {kk: vv for kk, vv in (sb.get(a) or {}).items() if not isinstance(vv, dict)}
                           for a in ("red", "blue")},
                tracks=tracks))
        match_rows.append(row)

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
        team_rows.append(row)
        heat = pt["heat"]
        jsave(os.path.join(out_dir, "t", f"{tk}.json"), dict(
            **row, event=ev, matches=mm, autos=pt["autos"], grid=GRID,
            heat=None if heat is None else (heat / max(heat.sum(), 1)).round(5).tolist()))
    team_rows.sort(key=lambda r: (r["rank"] or 999, r["number"]))
    n_done = sum(1 for r in match_rows if r["state"] == "done")
    jsave(os.path.join(out_dir, "event.json"), dict(
        key=ev, name=info.get("name"), short_name=info.get("short_name"), year=info.get("year"),
        start=info.get("start_date"), end=info.get("end_date"), city=info.get("city"),
        state_prov=info.get("state_prov"), updated=time.time(), n_done=n_done, n_total=len(match_rows),
        calibrated=os.path.exists(ev_file(ev, "camera.json")), matches=match_rows, teams=team_rows))
    evs = jload(os.path.join(API, "events.json"), [])
    evs = [e for e in evs if e["key"] != ev] + [dict(key=ev, name=info.get("name"), year=info.get("year"),
                                                      start=info.get("start_date"), n_done=n_done,
                                                      n_total=len(match_rows))]
    jsave(os.path.join(API, "events.json"), sorted(evs, key=lambda e: e.get("start") or "", reverse=True))
    log(f"{ev}: site rebuilt ({n_done}/{len(match_rows)} matches tracked)")


# ------------------------------------------------------------------ loop
def sync(ev, tba, max_new=None):
    ms = sorted(tba.matches(ev), key=lambda m: (m.get("actual_time") or m.get("time") or 0, sort_key(m)))
    status = jload(os.path.join(ev_dir(ev), "status.json"), {})
    cfg = jload(ev_file(ev, "event.json"), {}) or {}
    meta_cache = jload(os.path.join(DATA, "cache", "ytmeta.json"), {}) or {}
    has_cam = os.path.exists(ev_file(ev, "camera.json"))
    done_any = False; n = 0
    for m in ms:
        if not has_cam: break
        played = m["alliances"]["red"]["score"] is not None and m["alliances"]["red"]["score"] >= 0
        if not played: continue
        if os.path.exists(os.path.join(ev_dir(ev), "matches", f"{m['key']}.json")): continue
        if status.get(m["key"], {}).get("state") == "failed": continue
        if process_match(ev, m, cfg, status, meta_cache):
            done_any = True; n += 1
            jsave(os.path.join(DATA, "cache", "ytmeta.json"), meta_cache)
            build_site(ev, tba)
            if max_new and n >= max_new: break
    jsave(os.path.join(DATA, "cache", "ytmeta.json"), meta_cache)
    if not done_any:
        build_site(ev, tba)


def main():
    key = os.environ.get("TBA_KEY")
    if not key: sys.exit("TBA_KEY is not set")
    events = [e.strip() for e in os.environ.get("EVENTS", "").split(",") if e.strip()]
    if not events: sys.exit("EVENTS is empty")
    tba = TBA(key, os.path.join(DATA, "tba"))
    if not os.path.exists(MODEL): sys.exit(f"model not found: {MODEL}")
    while True:
        for ev in events:
            try:
                sync(ev, tba, int(os.environ["MAX_NEW"]) if os.environ.get("MAX_NEW") else None)
            except Exception:
                traceback.print_exc()
        if os.environ.get("ONCE") == "1": break
        time.sleep(POLL)


if __name__ == "__main__":
    main()
