# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: one self-contained executable `cyberscout-client` (.exe on Windows) with the bundled web app.
# Run through build/build.sh or build/build.ps1 (they create the venv and the web bundle first).
#   CS_TARGET_ARCH = arm64 | x86_64 | universal2   (macOS only; x86_64 / universal2 from arm64 need a universal2 Python,
#                    e.g. python.org's)
import os
import sys

CLIENT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
WEB = os.path.join(CLIENT, "web_bundle")
if not os.path.isfile(os.path.join(WEB, "index.html")):
    raise SystemExit("client/web_bundle is missing: run python3 client/build/make_bundle.py first")

a = Analysis(
    [os.path.join(CLIENT, "cyberscout_client.py")],
    pathex=[CLIENT],
    datas=[(WEB, "web_bundle")],
    hiddenimports=["csclient", "csclient.app", "csclient.web", "csclient.sync", "csclient.net", "csclient.mirror",
                   "csclient.store", "csclient.util"],
    excludes=["tkinter", "unittest", "pydoc", "doctest", "pdb", "lib2to3", "test", "idlelib", "turtle"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="cyberscout-client",
    console=True,                 # a terminal window shows the address + log; closing it stops the client
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    target_arch=os.environ.get("CS_TARGET_ARCH") or None,
    codesign_identity=os.environ.get("CS_CODESIGN_ID") or None,
    # Windows: the brand mark as the .exe icon (macOS/Linux one-file console binaries have no icon)
    icon=os.path.join(CLIENT, "build", "icon.ico") if sys.platform == "win32" else None,
)
