#!/usr/bin/env python3
"""Copy the repo's web/ app into client/web_bundle/ (served by the client until it has mirrored a server's copy).

    python3 client/build/make_bundle.py [--src web] [--out client/web_bundle]

Copies what web/Dockerfile ships (html, js, css, field image, fonts/, plus any PWA files such as sw.js / a
manifest / icons) and leaves out server-only files. client/web_bundle/ is build output (git-ignored)."""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.dirname(HERE)
REPO = os.path.dirname(CLIENT)
SKIP_NAMES = {"nginx.conf", "Dockerfile", ".DS_Store"}
SKIP_EXT = (".md", ".gz", ".conf", ".map", ".py", ".sh")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO, "web"))
    ap.add_argument("--out", default=os.path.join(CLIENT, "web_bundle"))
    a = ap.parse_args(argv)
    if not os.path.isfile(os.path.join(a.src, "index.html")):
        sys.exit(f"{a.src} has no index.html")
    tmp = a.out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    n = size = 0
    h = hashlib.sha256()
    for d, dirs, files in os.walk(a.src):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for f in sorted(files):
            if f.startswith(".") or f in SKIP_NAMES or f.endswith(SKIP_EXT):
                continue
            src = os.path.join(d, f)
            if os.path.islink(src):
                continue
            rel = os.path.relpath(src, a.src)
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            with open(src, "rb") as fh:
                b = fh.read()
            h.update(rel.encode() + b"\0" + b)
            n += 1
            size += len(b)
    try:
        sha = subprocess.check_output(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL,
                                      text=True).strip()
    except Exception:
        sha = "nogit"
    with open(os.path.join(tmp, "BUNDLE_VERSION"), "w") as f:
        f.write(f"{sha} src-{h.hexdigest()[:10]}\n")
    shutil.rmtree(a.out, ignore_errors=True)
    os.replace(tmp, a.out)
    print(f"web bundle: {n} files, {size / 1024:.0f} KB -> {a.out} ({sha})")


if __name__ == "__main__":
    main()
