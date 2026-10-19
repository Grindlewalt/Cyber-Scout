"""Can this computer track matches?  Processing runs on a GPU only (a CPU-only laptop takes far longer per match
than the server, so it would slow the event down):

  * NVIDIA on Windows / Linux: CUDA through onnxruntime-gpu with the pip CUDA 13 + cuDNN wheels.  Needs driver
    >= 580 (CUDA 13), compute capability >= 7.5 (CUDA 13 dropped Maxwell / Pascal / Volta: GTX 16xx, RTX 20xx and
    newer) and >= 2 GB of video memory.  Found with nvidia-smi.
  * Apple silicon (M1 and later), macOS 14+: Core ML through the plain onnxruntime build (ORT_COREML=1).
  * anything else (Intel Mac, AMD / Intel graphics, no driver): no tracking; "Clip only" still works.

detect() -> {ok, kind: "cuda" | "coreml" | None, runtime: "cuda" | "coreml" | "clip", name, detail, why, device,
             gpus: [...], os, arch}.  CS_FAKE_GPU (tests / screenshots): "none", "cuda", "coreml", or a JSON object
merged over the result."""
import json
import os
import platform
import re
import shutil
import subprocess
import sys

MIN_DRIVER = 580
MIN_CC = (7, 5)
MIN_VRAM_MB = 2048
MIN_MACOS = (14, 0)
QUERY = "index,name,memory.total,memory.used,driver_version,compute_cap"


def _run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return -1, "", f"{e.__class__.__name__}: {e}"


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def os_name():
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0] or ''}".strip()
    if os.name == "nt":
        return f"Windows {platform.release()}"
    return f"Linux {platform.release()}"


def apple_silicon(run=_run):
    """True on an M-series Mac, also when this program runs as Intel code through Rosetta."""
    if platform.machine() == "arm64":
        return True
    code, out, _ = run(["sysctl", "-n", "hw.optional.arm64"], 5)
    return code == 0 and out.strip() == "1"


def _result(ok, kind, name, detail="", why=None, **kw):
    r = {"ok": bool(ok), "kind": kind if ok else None, "runtime": kind if ok else "clip", "name": name,
         "detail": detail, "why": why, "device": kw.pop("device", None), "gpus": kw.pop("gpus", []),
         "os": os_name(), "arch": platform.machine()}
    r.update(kw)
    return r


def _mac(run):
    if not apple_silicon(run):
        return _result(False, None, "Intel Mac", why="Intel Macs have no GPU the tracker can use (it needs an "
                       "NVIDIA GPU or Apple silicon). Clip only works here.")
    code, chip, _ = run(["sysctl", "-n", "machdep.cpu.brand_string"], 5)
    chip = chip.strip() if code == 0 and chip.strip() and not chip.startswith("VirtualApple") else "Apple silicon"
    code, mem, _ = run(["sysctl", "-n", "hw.memsize"], 5)
    gb = round(int(mem) / 2 ** 30) if code == 0 and mem.strip().isdigit() else None
    cores = None
    code, out, _ = run(["system_profiler", "SPDisplaysDataType", "-json"], 15)
    if code == 0:
        try:
            for g in json.loads(out).get("SPDisplaysDataType") or []:
                if g.get("sppci_cores"):
                    cores = str(g["sppci_cores"])
                    break
        except ValueError:
            pass
    ver = platform.mac_ver()[0]
    if not ver:
        code, out, _ = run(["sw_vers", "-productVersion"], 5)
        ver = out.strip() if code == 0 else ""
    name = chip + (f" ({cores}-core GPU)" if cores else "")
    detail = ", ".join(x for x in (f"{gb} GB memory" if gb else None, f"macOS {ver}" if ver else None,
                                    "Core ML (GPU + Neural Engine)") if x)
    if ver and _ver(ver) < MIN_MACOS:
        return _result(False, None, name, detail, why=f"macOS {ver} is too old for the tracker's Core ML build: "
                       "update to macOS 14 Sonoma or later. Clip only works here.", memory_gb=gb)
    return _result(True, "coreml", name, detail, memory_gb=gb)


def find_smi():
    cands = [shutil.which("nvidia-smi")]
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        cands += [os.path.join(root, "System32", "nvidia-smi.exe"),
                  os.path.join(pf, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")]
    else:
        cands += ["/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def parse_smi(text):
    """CSV lines of QUERY (or without compute_cap) -> [{index, name, memory_mb, used_mb, driver, cc}]."""
    out = []
    for line in (text or "").splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 5 or not f[0].isdigit():
            continue
        cc = None
        if len(f) > 5 and re.match(r"^\d+\.\d+$", f[5]):
            a, b = f[5].split(".")
            cc = (int(a), int(b))
        out.append({"index": int(f[0]), "name": f[1], "memory_mb": _int(f[2]), "used_mb": _int(f[3]),
                    "driver": f[4], "cc": cc})
    return out


def _other_gpus(run):
    """Names of the graphics adapters when there is no NVIDIA driver (for a clear 'why')."""
    names = []
    if os.name == "nt":
        code, out, _ = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "(Get-CimInstance Win32_VideoController).Name"], 20)
        if code == 0:
            names = [x.strip() for x in out.splitlines() if x.strip()]
    else:
        vendors = {"0x10de": "NVIDIA", "0x8086": "Intel", "0x1002": "AMD"}
        try:
            for d in sorted(os.listdir("/sys/class/drm")):
                p = os.path.join("/sys/class/drm", d, "device", "vendor")
                if re.match(r"^card\d+$", d) and os.path.isfile(p):
                    with open(p) as f:
                        v = f.read().strip()
                    names.append(f"{vendors.get(v, 'other')} graphics")
        except OSError:
            pass
    return list(dict.fromkeys(names))


def _why_not(g):
    drv = _ver(g.get("driver"))
    if drv[0] < MIN_DRIVER:
        return (f"the NVIDIA driver ({g.get('driver')}) is too old: the tracker needs {MIN_DRIVER} or newer "
                "(CUDA 13). Update it from nvidia.com and check again.")
    if g.get("cc") is not None and g["cc"] < MIN_CC:
        return (f"{g['name']} (compute capability {g['cc'][0]}.{g['cc'][1]}) is too old for CUDA 13: the tracker "
                "needs a GeForce GTX 16xx / RTX 20xx or newer.")
    if g.get("memory_mb") is not None and g["memory_mb"] < MIN_VRAM_MB:
        return f"{g['name']} has {g['memory_mb']} MB of video memory; the tracker needs {MIN_VRAM_MB} MB."
    return None


def _nvidia(run):
    smi = find_smi()
    if not smi:
        names = _other_gpus(run)
        if any("NVIDIA" in n.upper() for n in names):
            why = ("there is an NVIDIA GPU but no NVIDIA driver (nvidia-smi not found): install the driver from "
                   "nvidia.com and check again.")
        elif names:
            why = (f"this computer's graphics ({', '.join(names[:2])}) can't run the tracker: it needs an NVIDIA "
                   "GPU with CUDA. Clip only works here.")
        else:
            why = "no NVIDIA GPU found (the tracker needs one with CUDA on Windows and Linux). Clip only works here."
        return _result(False, None, names[0] if names else "no NVIDIA GPU", why=why)
    code, out, err = run([smi, f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"])
    if code != 0 and "compute_cap" in (err + out):              # drivers before ~510 don't know the field
        code, out, err = run([smi, "--query-gpu=index,name,memory.total,memory.used,driver_version",
                              "--format=csv,noheader,nounits"])
    gpus = parse_smi(out) if code == 0 else []
    if not gpus:
        msg = (err or out).strip().splitlines()
        return _result(False, None, "NVIDIA GPU", why="nvidia-smi didn't answer (" + (msg[-1] if msg else
                       f"exit {code}")[:160] + "): check the NVIDIA driver.")
    ok = [g for g in gpus if not _why_not(g)]
    free = lambda g: (g["memory_mb"] or 0) - (g.get("used_mb") or 0)
    pick = max(ok or gpus, key=lambda g: (free(g), -g["index"]))         # the one with the most free memory
    view = [dict(g, cc=f"{g['cc'][0]}.{g['cc'][1]}" if g.get("cc") else None) for g in gpus]
    busy = pick.get("used_mb") is not None and pick.get("memory_mb") and free(pick) < 1536
    detail = ", ".join(x for x in (f"{round(pick['memory_mb'] / 1024)} GB" if pick.get("memory_mb") else None,
                                    f"{pick['used_mb'] / 1024:.1f} GB in use by other programs" if busy else None,
                                    f"driver {pick['driver']}", "CUDA") if x)
    if not ok:
        return _result(False, None, pick["name"], detail, why=_why_not(pick) + " Clip only works here.", gpus=view,
                       device=pick["index"])
    return _result(True, "cuda", pick["name"], detail, gpus=view, device=pick["index"],
                   memory_mb=pick.get("memory_mb"), driver=pick["driver"], busy=bool(busy))


def _fake(spec):
    if spec == "none":
        return _result(False, None, "Intel(R) UHD Graphics 620", why="this computer's graphics (Intel(R) UHD "
                       "Graphics 620) can't run the tracker: it needs an NVIDIA GPU with CUDA. Clip only works here.")
    if spec == "cuda":
        return _result(True, "cuda", "NVIDIA GeForce RTX 3060", "12 GB, driver 610.43.03, CUDA", device=0,
                       gpus=[{"index": 0, "name": "NVIDIA GeForce RTX 3060", "memory_mb": 12288,
                              "driver": "610.43.03", "cc": "8.6"}])
    if spec == "coreml":
        return _result(True, "coreml", "Apple M1 (8-core GPU)", "16 GB memory, Core ML (GPU + Neural Engine)")
    try:
        d = json.loads(spec)
    except ValueError:
        d = {}
    base = _result(bool(d.get("ok")), d.get("kind"), d.get("name") or "fake GPU", d.get("detail") or "",
                   why=d.get("why"))
    base.update(d)
    return base


def detect(run=_run):
    fake = os.environ.get("CS_FAKE_GPU")
    if fake:
        return _fake(fake)
    try:
        if sys.platform == "darwin":
            return _mac(run)
        if os.name == "nt" or sys.platform.startswith("linux"):
            return _nvidia(run)
    except Exception as e:                                   # never let a probe break the client
        return _result(False, None, "unknown", why=f"the GPU check failed ({e.__class__.__name__}: {e})"[:200])
    return _result(False, None, sys.platform, why=f"{sys.platform} is not supported for tracking.")


def pipeline_env(gpu):
    """Detector settings for the pipeline child: Core ML on a Mac (like tools/helper.py), the chosen CUDA device."""
    if gpu and gpu.get("kind") == "coreml":
        return {"FORCE_CPU": "1", "ORT_COREML": "1"}
    if gpu and gpu.get("kind") == "cuda":
        return {"GPU_DEVICE": str(int(gpu.get("device") or 0))}
    return {"FORCE_CPU": "1"}
