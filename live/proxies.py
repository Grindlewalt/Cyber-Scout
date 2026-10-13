"""Small preview copies of kept match videos for client sync (docs/SYNC.md "Media proxies").

    MEDIA/<ev>/<match>.mp4  ->  MEDIA/<ev>/proxy/<match>.mp4   H.264 <=480p, ~1 Mb/s, faststart, no audio
    MEDIA/<ev>/proxy/index.json  {match: {size, mtime_ns, sha256, src_size, src_mtime_ns, encoder, seconds}}
                                 (the api's /app/v1/sync/media reports sha256 from it)

Worker: `submit(MEDIA, ev, key)` after a match's video is kept (live/worker.py); encodes on a background thread so
tracking the next match is not held up. MEDIA_PROXIES=0 turns it off (default 1).
Backfill (already processed matches):

    python -m live.proxies DATA MEDIA EV [--only KEY ...] [--force]

Encoder: NVENC (h264_nvenc) when ffmpeg has it and a test encode works on PROXY_GPU (default "1": GPU indices as
the container sees them, CUDA_DEVICE_ORDER=PCI_BUS_ID like nvidia-smi; the worker container needs
NVIDIA_DRIVER_CAPABILITIES to include "video"), else libx264 on the CPU with -threads 2 under `nice -n 10`.
PROXY_ENCODER=cpu|nvenc|auto forces one (auto default)."""
import argparse, fcntl, hashlib, json, os, queue, re, shutil, subprocess, sys, threading, time

KEY_RE = re.compile(r"^\d{4}[a-z0-9]{1,16}_(qm|ef|qf|sf|f)\d{1,3}(m\d{1,2})?$")
HEIGHT = int(os.environ.get("PROXY_HEIGHT", "480"))
KBPS = int(os.environ.get("PROXY_KBPS", "1000"))
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
_enc_cache = {}


def enabled():
    return os.environ.get("MEDIA_PROXIES", "1") == "1"


def _env():
    e = dict(os.environ)
    e.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    return e


def _nvenc_gpu():
    """GPU index that can run h264_nvenc, or None (cached per process)."""
    if "gpu" in _enc_cache:
        return _enc_cache["gpu"]
    gpu = None
    mode = os.environ.get("PROXY_ENCODER", "auto")
    if mode != "cpu" and shutil.which(FFMPEG):
        try:
            encs = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                  timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            encs = ""
        if "h264_nvenc" in encs:
            for g in [x.strip() for x in os.environ.get("PROXY_GPU", "1").split(",") if x.strip()]:
                try:
                    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                        "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-gpu", g,
                                        "-f", "null", "-"], capture_output=True, timeout=60, env=_env())
                    if r.returncode == 0:
                        gpu = g
                        break
                except (OSError, subprocess.SubprocessError):
                    pass
    _enc_cache["gpu"] = gpu
    return gpu


def command(src, dst, gpu=None):
    """ffmpeg argv: <=HEIGHT p (never upscaled), ~KBPS kb/s, <=30 fps, faststart, no audio / metadata."""
    vf = f"scale=-2:'min({HEIGHT},ih)',fps='min(30,source_fps)'"
    head = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    rate = ["-b:v", f"{KBPS}k", "-maxrate", f"{int(KBPS * 1.2)}k", "-bufsize", f"{KBPS * 2}k"]
    tail = ["-pix_fmt", "yuv420p", "-g", "60", "-an", "-sn", "-dn", "-map_metadata", "-1",
            "-movflags", "+faststart", "-f", "mp4", dst]
    if gpu is not None:
        return head + ["-i", src, "-vf", vf, "-c:v", "h264_nvenc", "-gpu", str(gpu), "-preset", "p5",
                       "-rc", "vbr", "-profile:v", "main"] + rate + tail
    return (["nice", "-n", "10"] if shutil.which("nice") else []) + head + \
        ["-threads", "2", "-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main",
         "-threads", "2"] + rate + tail


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _update_index(pdir, key, entry):
    """Read-modify-write of proxy/index.json under an flock (worker thread and a backfill may run together)."""
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, ".index.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        p = os.path.join(pdir, "index.json")
        try:
            with open(p) as f:
                idx = json.load(f)
            if not isinstance(idx, dict):
                idx = {}
        except (OSError, ValueError):
            idx = {}
        if entry is None:
            idx.pop(key, None)
        else:
            idx[key] = entry
        tmp = f"{p}.tmp{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(idx, f, indent=0, sort_keys=True)
        os.replace(tmp, p)


def up_to_date(media, ev, key):
    src = os.path.join(media, ev, f"{key}.mp4")
    dst = os.path.join(media, ev, "proxy", f"{key}.mp4")
    try:
        s, d = os.stat(src), os.stat(dst)
        with open(os.path.join(media, ev, "proxy", "index.json")) as f:
            e = json.load(f).get(key) or {}
    except (OSError, ValueError, AttributeError):
        return False
    return e.get("src_size") == s.st_size and e.get("src_mtime_ns") == s.st_mtime_ns and e.get("size") == d.st_size


def make(media, ev, key, force=False, log=print):
    """Encode one proxy. -> index entry, or None (no source / failed; logged, never raises)."""
    if not KEY_RE.match(key) or not re.match(r"^\d{4}[a-z0-9]{1,16}$", ev):
        log(f"{key}: bad key"); return None
    src = os.path.join(media, ev, f"{key}.mp4")
    if not os.path.isfile(src):
        log(f"{key}: no kept video"); return None
    if not force and up_to_date(media, ev, key):
        return None
    pdir = os.path.join(media, ev, "proxy")
    os.makedirs(pdir, exist_ok=True)
    dst = os.path.join(pdir, f"{key}.mp4")
    tmp = os.path.join(pdir, f".{key}.{os.getpid()}.part.mp4")
    gpu = _nvenc_gpu()
    t0 = time.time()
    st = os.stat(src)
    for g in ([gpu, None] if gpu is not None else [None]):
        try:
            r = subprocess.run(command(src, tmp, g), capture_output=True, text=True, timeout=1800, env=_env())
            ok = r.returncode == 0 and os.path.getsize(tmp) > 0
            err = (r.stderr or "").strip()[-300:]
        except (OSError, subprocess.SubprocessError) as e:
            ok, err = False, str(e)[-300:]
        if ok:
            break
        log(f"{key}: proxy encode failed ({'nvenc' if g is not None else 'cpu'}): {err}")
    if not ok:
        try: os.remove(tmp)
        except OSError: pass
        return None
    os.replace(tmp, dst)
    d = os.stat(dst)
    entry = {"size": d.st_size, "mtime_ns": d.st_mtime_ns, "sha256": _sha256(dst), "src_size": st.st_size,
             "src_mtime_ns": st.st_mtime_ns, "encoder": "h264_nvenc" if g is not None else "libx264",
             "seconds": round(time.time() - t0, 1)}
    _update_index(pdir, key, entry)
    log(f"{key}: proxy {d.st_size / 1e6:.1f} MB ({entry['encoder']}, {entry['seconds']}s)")
    return entry


# ------------------------------------------------------------------ worker hook (background thread)
_q: "queue.Queue" = queue.Queue()
_thr = [None]
_thr_lock = threading.Lock()


def submit(media, ev, key, log=print):
    """Queue a proxy for a just-kept video. The (non-daemon) thread exits when idle, so a ONCE=1 worker run still
    finishes its proxies before the process ends."""
    if not enabled():
        return
    _q.put((media, ev, key, log))
    with _thr_lock:
        if _thr[0] is None or not _thr[0].is_alive():
            _thr[0] = threading.Thread(target=_run, name="proxies", daemon=False)
            _thr[0].start()


def _run():
    while True:
        try:
            media, ev, key, log = _q.get(timeout=5)
        except queue.Empty:
            return
        try:
            make(media, ev, key, log=log)
        except Exception as e:                       # never take the worker down
            log(f"{key}: proxy failed: {e}")


# ------------------------------------------------------------------ backfill CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m live.proxies", description=__doc__.split("\n\n")[0])
    ap.add_argument("data", help="DATA dir (unused for now; kept for symmetry with the other tools)")
    ap.add_argument("media", help="MEDIA dir (kept videos: MEDIA/<ev>/<match>.mp4)")
    ap.add_argument("ev", help="event key, e.g. 2026wasam")
    ap.add_argument("--only", nargs="+", default=None, help="match keys (or comma lists); default: every kept video")
    ap.add_argument("--force", action="store_true", help="re-encode even if the proxy is up to date")
    a = ap.parse_args(argv)
    d = os.path.join(a.media, a.ev)
    if a.only:
        keys = [k.strip() for x in a.only for k in x.split(",") if k.strip()]
        keys = [k if k.startswith(a.ev + "_") else f"{a.ev}_{k}" for k in keys]
    else:
        keys = sorted(n[:-4] for n in os.listdir(d) if n.endswith(".mp4") and KEY_RE.match(n[:-4]))
    log = lambda *x: print(time.strftime("%H:%M:%S"), *x, flush=True)
    gpu = _nvenc_gpu()
    log(f"{a.ev}: {len(keys)} video(s), encoder {'h264_nvenc on GPU ' + gpu if gpu is not None else 'libx264 (cpu)'}")
    done = skipped = failed = 0
    t0 = time.time()
    for k in keys:
        if not a.force and up_to_date(a.media, a.ev, k):
            skipped += 1
            continue
        if make(a.media, a.ev, k, force=True, log=log):
            done += 1
        else:
            failed += 1
    log(f"{a.ev}: {done} made, {skipped} up to date, {failed} failed in {time.time() - t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
