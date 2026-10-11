"""Re-run the full pipeline (tracking + identity + fuel/shots/analytics) for matches whose
videos are already kept in MEDIA_DIR -- no YouTube needed.

    python live/reprocess.py DATA_DIR MEDIA_DIR EVENT --only key1,key2 [--jobs 2] [--gpus 0,1]

Each job gets one GPU (round-robin).  Writes the same files the worker does.
"""
import argparse, json, os, sys, time, traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(REPO, "src"))


def one(data, media, ev, key, gpu, model):
    os.environ["GPU_DEVICE"] = str(gpu); os.environ.setdefault("OCR_GPU", str(gpu))
    import process, enrich, shots
    evd = os.path.join(data, "events", ev)
    old = json.load(open(os.path.join(evd, "matches", f"{key}.json")))
    tba = shots.find_tba_match(os.path.join(data, "tba", f"event__{ev}__matches.json"), key)
    cam = os.path.join(evd, "camera.json")
    if not os.path.exists(cam): cam = os.path.join(REPO, "events", ev, "camera.json")
    video = os.path.join(media, ev, f"{key}.mp4")
    logs = []; L = lambda *a: logs.append(" ".join(map(str, a)))
    try:
        t0 = time.time()
        res = process.process(video, cam, model, os.path.join(HERE, "field", "2026-rebuilt-welded.csv"),
                              log=L, pose_guess=old.get("camera_pose"),
                              teams={a: tba["alliances"][a]["team_keys"] for a in ("red", "blue")})
        res.update(key=key, video=old["video"], processed_at=time.time(), seconds=round(time.time() - t0))
        enrich._jsave(os.path.join(evd, "matches", f"{key}.json"), res)
        t1 = time.time()
        ev_ = enrich.enrich(evd, key, video, cam, res, tba, log=L)
        idn = res.get("identity") or {}
        made = sum(1 for s in ev_.get("shots", []) if s.get("outcome") == "made")
        return key, (f"ok track {t1 - t0:.0f}s enrich {time.time() - t1:.0f}s | identity {idn.get('method')} "
                     f"reads {idn.get('n_reads')} corrections {len(idn.get('corrections') or [])} | "
                     f"team_conf {[round(t.get('team_conf') or 0, 2) for t in res['tracks']]} | {made} made")
    except Exception:
        return key, "FAILED " + traceback.format_exc()[-600:] + " | " + " / ".join(logs[-4:])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("media"); ap.add_argument("event")
    ap.add_argument("--only", required=True); ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--gpus", default="0,1"); ap.add_argument("--model", default=None)
    a = ap.parse_args()
    model = a.model or os.path.join(a.data, "models", "robot.onnx")
    keys = [k.strip() for k in a.only.split(",") if k.strip()]
    gpus = [int(g) for g in a.gpus.split(",")]
    print(f"{len(keys)} matches, {a.jobs} jobs, GPUs {gpus}", flush=True)
    import multiprocessing as mp
    with ProcessPoolExecutor(a.jobs, mp_context=mp.get_context("spawn")) as ex:
        futs = [ex.submit(one, a.data, a.media, a.event, k, gpus[i % len(gpus)], model) for i, k in enumerate(keys)]
        for f in as_completed(futs):
            k, msg = f.result(); print(k, msg, flush=True)
