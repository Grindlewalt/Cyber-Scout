#!/usr/bin/env python3
"""Install the processing runtime the way the client's "Set up processing" does (csclient/procsetup.py: uv,
downloaded from its GitHub release when missing, a Python 3.12 venv, the runtime's packages) and check it works.
For CI (.github/workflows/client.yml, job runtime) and for trying a machine by hand:

    python3 client/build/runtime_check.py --runtime auto|cuda|coreml|clip [--root DIR] [--keep]

The pins come from server/routers/processing.py (REQUIREMENTS, CLIP_EXTRAS, PYTHON) without importing it (no
fastapi needed); the per-runtime lists are made by procsetup.requirements() (the same rule the server's RUNTIMES
uses).  Checks: the runtime's imports, onnxruntime's execution providers (CUDA for cuda, Core ML for coreml), the
pipeline's own modules (live/worker.py, detector, livestream, liveingest) importing with these packages, and the
Clip only tools (yt-dlp, deno, ffmpeg).  A machine without the GPU still installs and checks the cuda runtime (the
provider is listed; it needs a GPU only to run).  Exit 0 = pass."""
import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "client"))

from csclient import gpu as G            # noqa: E402
from csclient import procsetup as PS     # noqa: E402


def pins():
    vals = {}
    with open(os.path.join(REPO, "server", "routers", "processing.py")) as f:
        tree = ast.parse(f.read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and \
                n.targets[0].id in ("REQUIREMENTS", "CLIP_EXTRAS", "PYTHON", "YTDLP_REQ"):
            vals[n.targets[0].id] = ast.literal_eval(n.value)
    return vals


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default="auto", choices=("auto", "cuda", "coreml", "clip"))
    ap.add_argument("--root", help="install here (default: a temp dir, removed afterwards)")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    g = G.detect()
    print(f"GPU check: {g['name']}: " + (f"usable ({g['kind']})" if g["ok"] else g["why"]), flush=True)
    runtime = g["runtime"] if a.runtime == "auto" else a.runtime
    v = pins()
    pipe = {"requirements": v["REQUIREMENTS"], "clip_extras": v["CLIP_EXTRAS"], "python": v["PYTHON"],
            "ytdlp_requirements": v.get("YTDLP_REQ"), "dir": REPO, "version": "ci"}
    root = a.root or tempfile.mkdtemp(prefix="cs-runtime-")
    t0 = time.time()
    plan = PS.plan(root, pipe, {"runtime": runtime})
    print(f"plan: {json.dumps(plan)}", flush=True)
    inst = PS.Installer(root, pipe, {"runtime": runtime},
                        lambda m: print(f"[{time.time() - t0:6.1f}] {m}", flush=True), PS.uv_env(root, os.environ))
    failures = []
    try:
        py, info = inst.install()
        print(f"installed {runtime} in {time.time() - t0:.0f} s: {info['disk_bytes'] / 2 ** 30:.2f} GB", flush=True)
        mods = list(PS.CLIP_IMPORTS) + ([] if runtime == "clip" else list(PS.TRACK_IMPORTS))
        want = {"cuda": "CUDAExecutionProvider", "coreml": "CoreMLExecutionProvider"}.get(runtime)
        code = "\n".join([
            "import json, os, sys, shutil, subprocess",
            *[f"import {m}" for m in dict.fromkeys(mods)],
            "out = {'python': sys.version.split()[0]}",
            "try:",
            "    import onnxruntime as ort",
            "    if hasattr(ort, 'preload_dlls'):",
            "        try: ort.preload_dlls()",
            "        except Exception as e: out['preload'] = repr(e)[:200]",
            "    out['providers'] = ort.get_available_providers()",
            "except ImportError: out['providers'] = None",
            "import imageio_ffmpeg",
            "ff = imageio_ffmpeg.get_ffmpeg_exe()",
            "out['ffmpeg'] = subprocess.run([ff, '-version'], capture_output=True, text=True).stdout.split('\\n')[0]",
            "b = os.path.dirname(sys.executable)",
            "for t in ('yt-dlp', 'deno'):",
            "    exe = os.path.join(b, t + ('.exe' if os.name == 'nt' else ''))",
            "    out[t] = subprocess.run([exe, '--version'], capture_output=True, text=True).stdout.strip().split('\\n')[0]",
            f"sys.path[:0] = [{os.path.join(REPO, 'live')!r}, {os.path.join(REPO, 'src')!r}]",
            "os.environ.setdefault('DATA', os.path.join(os.getcwd(), 'data'))",
            "import livestream, liveingest",
            "out['clip_modules'] = True",
            *([] if runtime == "clip" else ["import detector, process, worker", "out['track_modules'] = True"]),
            "print(json.dumps(out))"])
        p = subprocess.run([py, "-c", code], capture_output=True, text=True, cwd=root, timeout=600)
        if p.returncode != 0:
            failures.append("imports")
            print(p.stdout[-2000:], p.stderr[-4000:])
        else:
            out = json.loads(p.stdout.strip().splitlines()[-1])
            print("check: " + json.dumps(out, indent=1), flush=True)
            if want and want not in (out.get("providers") or []):
                failures.append(f"{want} not available")
            for t in ("yt-dlp", "deno", "ffmpeg"):
                if not out.get(t):
                    failures.append(f"{t} missing")
    except Exception as e:                                   # noqa: BLE001 - report as a failed check
        failures.append(f"install: {e}")
    finally:
        if not a.keep and not a.root:
            shutil.rmtree(root, ignore_errors=True)
    print(f"runtime {runtime}: " + ("OK" if not failures else "FAILED: " + "; ".join(failures)), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
