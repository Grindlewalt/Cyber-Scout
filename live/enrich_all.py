"""Backfill v2 enrichment (fuel -> shots -> analytics) for already-processed matches.

    python live/enrich_all.py DATA_DIR EVENT [--jobs 6] [--force]

Re-downloads each match's exact video (same id as its match JSON, so t_start and the
camera pose line up), enriches, deletes the video.  Matches that already have a shots
file are skipped unless --force.  CPU only; safe to run next to the worker.
"""
import argparse, json, os, sys, traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))


def one(data, ev, key, scratch, media=None):
    import enrich, videos, shots
    evd = os.path.join(data, "events", ev)
    res = json.load(open(os.path.join(evd, "matches", f"{key}.json")))
    tba = shots.find_tba_match(os.path.join(data, "tba", f"event__{ev}__matches.json"), key)
    cam = os.path.join(evd, "camera.json")
    if not os.path.exists(cam):
        cam = os.path.join(os.path.dirname(HERE), "events", ev, "camera.json")
    vp = os.path.join(scratch, f"{key}.mp4")
    logs = []
    try:
        if not os.path.exists(vp):
            videos.download(res["video"]["id"], vp)
        ev_ = enrich.enrich(evd, key, vp, cam, res, tba, log=lambda *a: logs.append(" ".join(map(str, a))))
        made = sum(1 for s in ev_.get("shots", []) if s.get("outcome") == "made")
        return key, f"ok {len(ev_.get('shots', []))} shots, {made} made, {ev_.get('timing')}"
    except Exception:
        return key, "FAILED " + traceback.format_exc()[-400:] + " | " + " / ".join(logs[-3:])
    finally:
        if os.path.exists(vp):
            if media:                                            # keep it for the in-browser player
                os.makedirs(os.path.join(media, ev), exist_ok=True)
                os.replace(vp, os.path.join(media, ev, f"{key}.mp4"))
            else:
                os.remove(vp)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("event")
    ap.add_argument("--jobs", type=int, default=6); ap.add_argument("--force", action="store_true")
    ap.add_argument("--scratch", default="/mnt/nas/autoscout-scratch/enrich-tmp")   # root disk is nearly full
    ap.add_argument("--only", default="", help="comma-separated match keys (e.g. a dev set)")
    ap.add_argument("--media", default="", help="keep videos here (MEDIA_DIR) instead of deleting them")
    a = ap.parse_args()
    os.makedirs(a.scratch, exist_ok=True)
    md = os.path.join(a.data, "events", a.event, "matches")
    keys = sorted(f[:-5] for f in os.listdir(md) if f.endswith(".json"))
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    todo = [k for k in keys if (not only or k in only) and
            (a.force or not os.path.exists(os.path.join(a.data, "events", a.event, "shots", f"{k}.json")))]
    print(f"{len(todo)} of {len(keys)} matches to enrich, {a.jobs} jobs", flush=True)
    with ProcessPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(one, a.data, a.event, k, a.scratch, a.media or None) for k in todo]
        for f in as_completed(futs):
            k, msg = f.result(); print(k, msg, flush=True)
