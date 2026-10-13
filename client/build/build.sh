#!/usr/bin/env bash
# Build the standalone Cyber-Scout Client for this OS (macOS or Linux) into client/dist/.
#   client/build/build.sh                              # native arch
#   CS_TARGET_ARCH=x86_64 client/build/build.sh        # macOS: Intel build (needs a universal2 Python, e.g. python.org)
#   CS_TARGET_ARCH=universal2 client/build/build.sh    # macOS: one binary for both (universal2 Python)
#   PYTHON=/path/to/python3.12 client/build/build.sh
#   CS_SMOKE=1 client/build/build.sh                   # also run build/smoke_test.py on the result
# Output: client/dist/cyberscout-client-<macos|linux>-<arm64|x64|universal2> (+ .sha256)
# Uses a throwaway venv per interpreter under .work/client-build/ (nothing is installed globally).
# macOS: prefers the python.org Python (universal2, runs on macOS 10.13+) over Homebrew's (single-arch, and its
# binaries need the macOS version they were built on). Linux: use a Python built against an old glibc (e.g.
# `uv python install 3.12`, python-build-standalone) or the binary only runs on distros as new as the build host.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT="$(dirname "$HERE")"
REPO="$(dirname "$CLIENT")"
WORK="${CS_BUILD_WORK:-$REPO/.work/client-build}"
case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux) OS=linux ;;
  *) echo "build.sh is for macOS / Linux; use build.ps1 on Windows" >&2; exit 2 ;;
esac
if [ -z "${PYTHON:-}" ]; then
  PYTHON=python3
  if [ "$OS" = macos ]; then
    for v in 3.13 3.12 3.11; do
      p="/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
      if [ -x "$p" ]; then PYTHON="$p"; break; fi
    done
  fi
fi
export CS_TARGET_ARCH="${CS_TARGET_ARCH:-}"
case "${CS_TARGET_ARCH:-$(uname -m)}" in
  x86_64|amd64|x64) ARCH=x64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  universal2) ARCH=universal2 ;;
  *) ARCH="$(uname -m)" ;;
esac
if [ "$OS" = macos ]; then
  CS_TARGET_ARCH="${CS_TARGET_ARCH:-$(uname -m)}"   # thin build for this Mac unless asked otherwise
else
  CS_TARGET_ARCH=                                   # target_arch is a macOS-only PyInstaller option
fi

TAG="$("$PYTHON" -c 'import sys, platform; print("py%d%d-%s" % (sys.version_info[0], sys.version_info[1], platform.machine()))')"
VENV="$WORK/venv-$TAG"
if [ ! -x "$VENV/bin/pyinstaller" ]; then
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet "pyinstaller>=6.10,<7"
fi
"$VENV/bin/python" "$HERE/make_bundle.py"
B="$WORK/$OS-$ARCH"
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$B/dist" --workpath "$B/work" "$HERE/cyberscout.spec"
mkdir -p "$CLIENT/dist"
OUT="$CLIENT/dist/cyberscout-client-$OS-$ARCH"
cp "$B/dist/cyberscout-client" "$OUT"
chmod +x "$OUT"
( cd "$CLIENT/dist" && { command -v sha256sum >/dev/null && sha256sum "$(basename "$OUT")" || shasum -a 256 "$(basename "$OUT")"; } > "$(basename "$OUT").sha256" )
echo "built $OUT ($(du -h "$OUT" | cut -f1); $("$VENV/bin/python" -c 'import sys; print(sys.version.split()[0])'))"
if [ -n "${CS_SMOKE:-}" ]; then
  "$VENV/bin/python" "$HERE/smoke_test.py" "$OUT" ${CS_SMOKE_SERVER:+--server "$CS_SMOKE_SERVER"}
fi
