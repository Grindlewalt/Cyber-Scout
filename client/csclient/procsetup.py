"""One-time "Set up processing": a Python 3.12 environment for the pipeline, made with uv, outside this client
(the PyInstaller bundle stays small and stdlib-only):

    <data>/processing/tools/uv-<ver>/uv      uv itself, downloaded from its GitHub release when it isn't installed
                                             (pinned version + sha256)
    <data>/processing/python/                the Python uv downloads (python-build-standalone 3.12)
    <data>/processing/uv-cache/              uv's package cache (same disk as the venv: hard links / clones)
    <data>/processing/venv/                  the processing Python + the runtime's packages; cs-runtime.json
                                             says which runtime it holds

The runtime follows the GPU check (csclient/gpu.py): cuda (NVIDIA: onnxruntime-gpu + the pip CUDA 13 / cuDNN
wheels), coreml (Apple silicon: plain onnxruntime) or clip (no GPU: just what Clip only needs).  The server sends
the pins (GET /app/v1/processing/pipeline "runtimes"); every runtime also gets the Clip only tools (imageio-ffmpeg's
ffmpeg, deno for yt-dlp's YouTube support) and yt-dlp from live/requirements-ytdlp.txt (hash-pinned).

plan() says what an install will download and how much disk it needs (measured on real installs, below) and
checks the free space first; install() runs it and reports progress through a log callback."""
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile

from .gpu import apple_silicon

UV_VERSION = "0.12.21"
UV_URL = "https://github.com/astral-sh/uv/releases/download/{v}/{f}"
UV_ASSETS = {     # (os, machine) -> (file, sha256) from the release's .sha256 files
    ("darwin", "arm64"): ("uv-aarch64-apple-darwin.tar.gz",
                          "b88bda573e566ef9bced66b155fe0408626fbbc053aee1c30ba686f0728c9447"),
    ("darwin", "x86_64"): ("uv-x86_64-apple-darwin.tar.gz",
                           "2b336763b396ec6afa20c5a8b083538ca7402445b868311979d740a4344c17d8"),
    ("win32", "x86_64"): ("uv-x86_64-pc-windows-msvc.zip",
                          "5d223efa0bf00208c3853246af09420419dfbd352536aa6bb8163d6170e23890"),
    ("linux", "x86_64"): ("uv-x86_64-unknown-linux-gnu.tar.gz",
                          "23f02075b652bb1df64178cfae41b5caf160822e720e2663568f3f5d63bc52c0"),
    ("linux", "arm64"): ("uv-aarch64-unknown-linux-gnu.tar.gz",
                         "030b69227b40af8c1981b7301793dc66e71ed3c796ea8688209dd268bd91ec51"),
}
UV_BYTES = 18 << 20
GB = 1 << 30
MB = 1 << 20
# Download / disk per runtime, measured on real installs (uv 0.12.21, Python 3.12 + every package incl. the Clip
# only tools): NVIDIA on Linux x86_64 (2026-10-18: 1.75 GB down, venv 3.0 GB + Python 0.1 GB), Apple M1 (0.23 GB
# down, 0.6 GB). The download lands in uv's cache first; the cache is removed after the install, so the peak is up
# to twice the disk figure where hard links / clones don't work (network drives).
SIZES = {
    "cuda": {"download": int(1.8 * GB), "disk": int(3.2 * GB), "label": "NVIDIA CUDA runtime"},
    "coreml": {"download": 240 * MB, "disk": 620 * MB, "label": "Apple silicon (Core ML) runtime"},
    "clip": {"download": 230 * MB, "disk": 480 * MB, "label": "Clip only tools (no tracking)"},
}
HEADROOM = 3 * GB          # work space next to the install: a match clip, results, model, partial downloads
TRACK_IMPORTS = ("onnxruntime", "av", "cv2", "numpy", "scipy", "pupil_apriltags")
CLIP_IMPORTS = ("av", "cv2", "numpy", "imageio_ffmpeg", "yt_dlp", "certifi")


def machine():
    """(os, machine) for uv: an Apple-silicon Mac gets the native arm64 uv also when this client runs through
    Rosetta (the processing Python then runs natively too)."""
    osn = "darwin" if sys.platform == "darwin" else "win32" if os.name == "nt" else "linux"
    m = platform.machine().lower()
    m = "arm64" if m in ("arm64", "aarch64") else "x86_64" if m in ("x86_64", "amd64", "x64") else m
    if osn == "darwin" and m == "x86_64" and apple_silicon():
        m = "arm64"
    return osn, m


def venv_python(venv):
    return os.path.join(venv, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv, "bin", "python")


def venv_bin(venv):
    return os.path.join(venv, "Scripts" if os.name == "nt" else "bin")


def find_uv():
    cands = [shutil.which("uv")]
    home = os.path.expanduser("~")
    exe = "uv.exe" if os.name == "nt" else "uv"
    cands += [os.path.join(home, ".local", "bin", exe), os.path.join(home, ".cargo", "bin", exe),
              "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _fetch(url, timeout=60):
    from .net import make_ssl_context
    req = urllib.request.Request(url, headers={"User-Agent": "CyberScoutClient"})
    with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as r:
        return r.read(64 << 20)


def ensure_uv(root, log, fetch=_fetch):
    """A uv to use: installed already, else the pinned release downloaded into <root>/tools (sha256-checked)."""
    have = find_uv()
    if have:
        return have
    exe = "uv.exe" if os.name == "nt" else "uv"
    dst = os.path.join(root, "tools", f"uv-{UV_VERSION}", exe)
    if os.path.isfile(dst):
        return dst
    key = machine()
    if key not in UV_ASSETS:
        raise RuntimeError(f"no uv build for {key[0]} {key[1]}: install uv yourself (https://docs.astral.sh/uv/)")
    name, sha = UV_ASSETS[key]
    log(f"downloading uv {UV_VERSION} ({UV_BYTES >> 20} MB, from github.com/astral-sh/uv)")
    data = fetch(UV_URL.format(v=UV_VERSION, f=name))
    if hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError("the uv download does not match its published checksum; not used")
    blob = None
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.replace("\\", "/").split("/")[-1] == exe:
                    blob = z.read(n)
                    break
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            for ti in t:
                if ti.isfile() and ti.name.split("/")[-1] == exe:
                    blob = t.extractfile(ti).read()
                    break
    if not blob:
        raise RuntimeError("the uv download has no uv executable")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dst)
    return dst


def requirements(pipe, runtime):
    """-> (pip requirement lines, yt-dlp hash-pinned requirements file or None) for a runtime."""
    rts = pipe.get("runtimes") or {}
    base = list(pipe.get("requirements") or [])
    if runtime in rts:
        reqs = list(rts[runtime])
    elif runtime == "cuda":         # an older server without "runtimes": swap in the GPU build of the same version
        reqs = [("onnxruntime-gpu[cuda,cudnn]==" + r.split("==", 1)[1]) if r.startswith("onnxruntime==") else r
                for r in base]
    elif runtime == "clip":
        reqs = [r for r in base if r.split("==")[0] in ("numpy", "opencv-python-headless", "av")]
    else:
        reqs = base
    reqs += [r for r in (pipe.get("clip_extras") or ["imageio-ffmpeg==0.6.0", "deno==2.5.6"]) if r not in reqs]
    yt = pipe.get("ytdlp_requirements") or "live/requirements-ytdlp.txt"
    ytp = os.path.join(pipe["dir"], *yt.split("/")) if pipe.get("dir") else None
    return reqs, ytp if ytp and os.path.isfile(ytp) else None


def marker(venv):
    try:
        with open(os.path.join(venv, "cs-runtime.json")) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def plan(root, pipe, gpu):
    """What Set up would do here: {runtime, label, download, disk, free, need, ok, why, installed}."""
    runtime = (gpu or {}).get("runtime") or "clip"
    s = SIZES[runtime]
    venv = os.path.join(root, "venv")
    m = marker(venv)
    reqs, _ = requirements(pipe, runtime) if pipe else ([], None)
    same = bool(m and m.get("runtime") == runtime and os.path.isfile(venv_python(venv)))
    up_to_date = bool(same and (not reqs or m.get("requirements") == _req_hash(reqs)))   # no pipeline info yet: trust it
    os.makedirs(root, exist_ok=True)
    free = shutil.disk_usage(root).free
    need = (0 if same else 2 * s["disk"]) + HEADROOM
    out = {"runtime": runtime, "label": s["label"], "download": 0 if up_to_date else s["download"],
           "disk": s["disk"], "free": free, "need": need, "installed": same, "up_to_date": up_to_date,
           "packages": len(reqs), "uv": find_uv() is not None or os.path.isfile(
               os.path.join(root, "tools", f"uv-{UV_VERSION}", "uv.exe" if os.name == "nt" else "uv")),
           "ok": free >= need, "why": None}
    if not out["ok"]:
        out["why"] = (f"not enough free disk space: Set up needs about {need / GB:.1f} GB on this disk, "
                      f"{free / GB:.1f} GB free. Free some space and try again.")
    return out


def _req_hash(reqs):
    return hashlib.sha256("\n".join(sorted(reqs)).encode()).hexdigest()[:16]


def _du(p):
    n = 0
    for d, _, fs in os.walk(p):
        for f in fs:
            try:
                n += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return n


def uv_env(root, base_env):
    env = dict(base_env)
    env.update(UV_CACHE_DIR=os.path.join(root, "uv-cache"), UV_PYTHON_INSTALL_DIR=os.path.join(root, "python"),
               UV_NO_CONFIG="1", UV_PYTHON_PREFERENCE="only-managed", UV_HTTP_TIMEOUT="300", NO_COLOR="1",
               UV_NO_PROGRESS="1")
    env.pop("VIRTUAL_ENV", None)
    return env


class Installer:
    """install() in steps with a log; `run_cmd` and `fetch` are injectable (tests)."""

    def __init__(self, root, pipe, gpu, log, env, run_cmd=None, fetch=_fetch, stop=None):
        self.root, self.pipe, self.gpu, self.log, self.env = root, pipe, gpu, log, env
        self.run_cmd = run_cmd or self._run
        self.fetch, self.stop = fetch, stop or threading.Event()
        self.progress = None                 # {"bytes", "expect"} while packages download

    def _run(self, cmd, watch=None, expect=0, timeout=3600):
        """Run uv, streaming its output lines to the log; `watch`: a folder whose growth shows progress."""
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.env,
                             cwd=self.root, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t0, base = time.time(), (_du(watch) if watch and os.path.isdir(watch) else 0)
        done = threading.Event()

        def poll():
            while not done.wait(3):
                if watch and expect:
                    self.progress = {"bytes": max(0, _du(watch) - base), "expect": expect}
                if self.stop.is_set() or time.time() - t0 > timeout:
                    p.kill()
        th = threading.Thread(target=poll, daemon=True)
        th.start()
        tail = []
        try:
            for ln in p.stdout:
                ln = ln.rstrip()
                if ln:
                    tail.append(ln)
                    if not ln.startswith(" + ") or len(tail) < 40:
                        self.log(ln[:200])
            code = p.wait()
        finally:
            done.set()
            self.progress = None
        if code != 0:
            raise RuntimeError(f"{os.path.basename(cmd[0])} {cmd[1]} failed (exit {code}): "
                               + (tail[-1] if tail else "")[:300])

    def install(self):
        root = self.root
        runtime = (self.gpu or {}).get("runtime") or "clip"
        uv = ensure_uv(root, self.log, self.fetch)
        venv = os.path.join(root, "venv")
        py = venv_python(venv)
        m = marker(venv)
        if os.path.isfile(py) and m and m.get("runtime") not in (None, runtime):
            self.log(f"the environment holds the {m.get('runtime')} runtime; making a new one for {runtime}")
            shutil.rmtree(venv, ignore_errors=True)
        if not os.path.isfile(py):
            self.log(f"creating a Python {self.pipe.get('python') or '3.12'} environment (uv downloads Python, "
                     "about 30 MB)")
            self.run_cmd([uv, "venv", "--python", self.pipe.get("python") or "3.12", venv])
        reqs, ytdlp = requirements(self.pipe, runtime)
        rq = os.path.join(root, f"requirements-{runtime}.txt")
        with open(rq, "w") as f:
            f.write("\n".join(reqs) + "\n")
        s = SIZES[runtime]
        self.log(f"installing the {s['label']}: {len(reqs)} packages, about {s['download'] / GB:.1f} GB to "
                 "download" if s["download"] >= GB else
                 f"installing the {s['label']}: {len(reqs)} packages, about {s['download'] >> 20} MB to download")
        self.run_cmd([uv, "pip", "install", "--python", py, "-r", rq], watch=os.path.join(root, "uv-cache"),
                     expect=s["disk"])
        if ytdlp:
            self.log("installing yt-dlp (hash-pinned, for Clip only)")
            try:
                self.run_cmd([uv, "pip", "install", "--python", py, "--require-hashes", "--no-deps", "-r", ytdlp])
            except RuntimeError as e:          # tracking still works; Clip only will say what's missing
                self.log(f"yt-dlp: {e}")
        shutil.rmtree(os.path.join(root, "uv-cache"), ignore_errors=True)     # its files are in the venv now
        info = {"runtime": runtime, "requirements": _req_hash(reqs), "at": time.time(), "uv": uv,
                "pipeline": self.pipe.get("version"), "disk_bytes": _du(venv)}
        with open(os.path.join(venv, "cs-runtime.json"), "w") as f:
            json.dump(info, f)
        self.log(f"installed: {info['disk_bytes'] / GB:.2f} GB in {venv}")
        return py, info
