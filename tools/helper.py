#!/usr/bin/env python3
"""Processing helper: track livestream match clips on THIS machine for a Mac server (deploy/macos), in parallel
with the server's own worker.  Per match: claim it on the server (events/<ev>/claims/<key>.json, the worker's own
claim rule: live/worker.py claim()), download the clip, run the worker's process_match here, upload the results
(fuel/ shots/ analytics/ then matches/<key>.json: the server's worker treats the match as done and rebuilds the
site), copy the clip into the server's MEDIA/<ev>/<key>.mp4, release the claim.  Picks matches in the server's
priority order (events/<ev>/priority.json, tools/priority.py), so both machines work down the same list.  The
next clip downloads while the current one is tracked.

  .venv/bin/python tools/helper.py [--host m2] [--event 2026wagg] [--id macbook]
Needs this repo's .venv (onnxruntime with CoreML, av, cv2, pupil_apriltags).  Local mirror: ~/CyberScout/helper/.
Stop with Ctrl-C (claims are released; a killed helper's claims go stale after the worker's CLAIM_S).
"""
import argparse, json, os, queue, shlex, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--host", default="m2")
ap.add_argument("--event", default="2026wagg")
ap.add_argument("--id", default="helper-" + os.uname().nodename.split(".")[0])
ap.add_argument("--remote", default="CyberScout/server", help="server dir, relative to the server user's home")
ap.add_argument("--local", default=os.path.expanduser("~/CyberScout/helper"))
args = ap.parse_args()
EV, BY, R = args.event, args.id, args.remote
L = args.local
os.environ.update(DATA=os.path.join(L, "data"), MEDIA=os.path.join(L, "media"), KEEP_VIDEO="0", FORCE_CPU="1",
                  ORT_COREML="1", WORKER_ID=BY, MODEL=os.path.join(L, "data", "models", "robot.onnx"))
SSH = ["ssh", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=6", "-o", "BatchMode=yes",
       args.host]


def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)


def ssh(cmd, inp=None, tries=8, binary=False):
    for k in range(tries):
        p = subprocess.run(SSH + [cmd], input=inp, capture_output=True, text=not binary)
        if p.returncode == 0: return p.stdout
        if p.returncode != 255 and k >= 1:              # the command failed (not the connection)
            raise RuntimeError(f"{cmd[:60]}: {(p.stderr or b'')[-300:]}")
        time.sleep(4 + 3 * k)
    raise RuntimeError(f"ssh to {args.host} keeps failing")


def rpy(code):
    """Run Python on the server (its venv, its app dir) -> stdout."""
    return ssh(f"cd $HOME/{R}/app && DATA=$HOME/{R}/data MEDIA=$HOME/{R}/media ../venv/bin/python -", inp=code)


def fetch(rel, dst, tries=6):
    """Server file $HOME/<R>/<rel> -> local dst (atomic; retried whole)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for k in range(tries):
        with open(dst + ".part", "wb") as f:
            p = subprocess.run(SSH + [f"cat $HOME/{R}/{shlex.quote(rel)}"], stdout=f, stderr=subprocess.PIPE)
        if p.returncode == 0:
            os.replace(dst + ".part", dst); return dst
        time.sleep(4 + 3 * k)
    raise RuntimeError(f"could not fetch {rel}")


def push(src, rel):
    """Local file -> server $HOME/<R>/<rel> (written to .tmp, then renamed)."""
    q = shlex.quote(rel)
    with open(src, "rb") as f: data = f.read()
    ssh(f"mkdir -p $(dirname $HOME/{R}/{q}) && cat > $HOME/{R}/{q}.tmp && mv $HOME/{R}/{q}.tmp $HOME/{R}/{q}", inp=data, binary=True)


PICK = r'''
import glob, json, os, time, sys
EV, BY, CLAIM_S, SKIP = %r, %r, 1800.0, set(%r)
D = os.environ["DATA"]; M = os.environ["MEDIA"]; evd = os.path.join(D, "events", EV)
sys.path.insert(0, "live")
from tba import TBA
key = json.load(open(os.path.join(D, "config", "worker.json"))).get("tba_key")
ms = {m["key"]: m for m in TBA(key, os.path.join(D, "tba")).matches(EV)}
order = (json.load(open(os.path.join(evd, "priority.json"))) if os.path.exists(os.path.join(evd, "priority.json")) else {}).get("order") or []
rest = sorted((k for k in ms if k not in order), key=lambda k: (ms[k].get("actual_time") or ms[k].get("time") or 0))
def claim(k):
    p = os.path.join(evd, "claims", k + ".json"); os.makedirs(os.path.dirname(p), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w") as f: json.dump(dict(by=BY, at=time.time()), f)
            return True
        except FileExistsError:
            try: c = json.load(open(p))
            except ValueError: c = {}
            if c.get("by") != BY and time.time() - (c.get("at") or 0) < CLAIM_S: return False
            try: os.remove(p)
            except OSError: pass
    return False
for k in order + rest:
    if k in SKIP or k not in ms or os.path.exists(os.path.join(evd, "matches", k + ".json")): continue
    clips = glob.glob(os.path.join(M, "live", EV, "*", "clips", "tba-" + k.split("_", 1)[1] + ".mp4"))
    if not clips: continue
    if not claim(k): continue
    vd = os.path.dirname(os.path.dirname(clips[0]))
    print(json.dumps(dict(key=k, match=ms[k], dir=os.path.relpath(vd, os.path.dirname(D)), clip=os.path.relpath(clips[0], os.path.dirname(D)))))
    break
'''


def pick(skip):
    out = rpy(PICK % (EV, BY, sorted(skip))).strip()
    return json.loads(out.splitlines()[-1]) if out else None


def release(key):
    q = shlex.quote(f"{R}/data/events/{EV}/claims/{key}.json")
    ssh(f"f=$HOME/{q}; grep -q '\"by\": \"{BY}\"' $f 2>/dev/null && rm -f $f; true")


def sync_setup():
    """Calibration, layout, sources, view reference, model (refreshed every pick)."""
    for rel in ("camera.json", "layout.json", "sources.json", "calib_wizard/overhead.json", "frames/calib_overhead_plate.png"):
        fetch(f"data/events/{EV}/{rel}", os.path.join(L, "data", "events", EV, rel))
    mp = os.path.join(L, "data", "models", "robot.onnx")
    size = int(ssh(f"wc -c < $HOME/{R}/data/models/robot.onnx").strip())
    if not os.path.exists(mp) or os.path.getsize(mp) != size:
        log("downloading the detector model"); fetch("data/models/robot.onnx", mp)


def prepare(job):
    """Mirror the record dir's meta + records and this clip."""
    d = job["dir"]                                      # media/live/<ev>/<video>
    os.makedirs(os.path.join(L, d), exist_ok=True)
    tar = ssh(f"cd $HOME/{R}/{shlex.quote(d)} && tar -cf - meta.json matches", binary=True)
    subprocess.run(["tar", "-xf", "-", "-C", os.path.join(L, d)], input=tar, check=True)
    fetch(job["clip"], os.path.join(L, job["clip"]))
    return job


def producer(q, stop, skip):
    while not stop.is_set():
        try:
            sync_setup()
            job = pick(skip)
            if job is None:
                log("nothing to do; checking again in 60 s"); time.sleep(60); continue
            log(f"{job['key']}: claimed, downloading the clip")
            q.put(prepare(job))
        except Exception as e:
            log(f"producer: {e!r:.300}"); time.sleep(20)


def main():
    sys.path[:0] = [os.path.join(ROOT, "live"), os.path.join(ROOT, "src")]
    import worker as W, liveingest as LI
    q, stop, skip = queue.Queue(maxsize=1), threading.Event(), set()
    threading.Thread(target=producer, args=(q, stop, skip), daemon=True).start()
    evd = os.path.join(L, "data", "events", EV)
    try:
        while True:
            job = q.get()
            k, m = job["key"], job["match"]
            t0 = time.time()
            try:
                lv = LI.LiveView(EV)
                sc = m["alliances"]["red"]["score"]; played = sc is not None and sc >= 0
                live = lv.for_processing(m, played)
                if live is None:
                    log(f"{k}: not trackable here ({lv.wait_reason(m)}); skipped"); skip.add(k); continue
                st = {}
                ok = W.process_match(EV, m, {"video_mode": "livestream"}, st, {}, live=live)
                if not ok or not os.path.exists(os.path.join(evd, "matches", f"{k}.json")):
                    log(f"{k}: failed here ({(st.get(k) or {}).get('error') or (st.get(k) or {}).get('note')}); the server will retry it")
                    skip.add(k); continue
                for sub in ("fuel", "shots", "analytics", "matches"):   # matches/ last: then the server sees it done
                    p = os.path.join(evd, sub, f"{k}.json")
                    if os.path.exists(p): push(p, f"{R}/data/events/{EV}/{sub}/{k}.json")
                ssh(f"cd $HOME/{R}/media && mkdir -p {EV} && [ -e {EV}/{k}.mp4 ] || cp {shlex.quote(job['clip'][len('media/'):])} {EV}/{k}.mp4")
                log(f"{k}: done in {time.time() - t0:.0f}s, uploaded")
            except Exception as e:
                log(f"{k}: error {e!r:.300}"); skip.add(k)
            finally:
                try: release(k)
                except Exception as e: log(f"{k}: release failed ({e!r:.100}); the claim goes stale in 30 min")
                try: os.remove(os.path.join(L, job["clip"]))
                except OSError: pass
    except KeyboardInterrupt:
        stop.set(); log("stopped")


if __name__ == "__main__":
    main()
