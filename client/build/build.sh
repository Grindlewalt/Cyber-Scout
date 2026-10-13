#!/usr/bin/env bash
# Build the standalone Cyber-Scout Client for this OS (macOS or Linux) into client/dist/.
#   client/build/build.sh                 # native arch
#   CS_TARGET_ARCH=universal2 client/build/build.sh   # macOS, needs a universal2 Python (python.org installer)
#   PYTHON=/path/to/python3.11 client/build/build.sh
# Uses a throwaway venv under .work/client-build/ (nothing is installed globally).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT="$(dirname "$HERE")"
REPO="$(dirname "$CLIENT")"
WORK="$REPO/.work/client-build"
PY="${PYTHON:-python3}"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="${CS_TARGET_ARCH:-$(uname -m)}"

mkdir -p "$WORK"
if [ ! -x "$WORK/venv/bin/pyinstaller" ]; then
  "$PY" -m venv "$WORK/venv"
  "$WORK/venv/bin/pip" install --quiet --upgrade pip
  "$WORK/venv/bin/pip" install --quiet "pyinstaller>=6.6,<7"
fi
"$WORK/venv/bin/python" "$HERE/make_bundle.py"
"$WORK/venv/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$WORK/dist" --workpath "$WORK/work" "$HERE/cyberscout.spec"
mkdir -p "$CLIENT/dist"
OUT="$CLIENT/dist/cyberscout-client-$OS-$ARCH"
cp "$WORK/dist/cyberscout-client" "$OUT"
chmod +x "$OUT"
( cd "$CLIENT/dist" && shasum -a 256 "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
