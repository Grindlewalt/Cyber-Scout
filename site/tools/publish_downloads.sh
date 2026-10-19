#!/usr/bin/env bash
# Publish the downloads site: everything needed to install Cyber-Scout from one URL (no GitHub access needed).
#
#   bash site/tools/publish_downloads.sh [SITE_URL] [HOST] [REMOTE_DIR] [PORT]
#   defaults: http://10.0.0.58:8790  atomosserver  /mnt/nas/autoscout-scratch/demo-site  8790
#
# Builds site/downloads/ (gitignored) on this machine:
#   install.sh, install.ps1                     server installers (Linux/macOS, Windows)
#   get.sh, get.ps1                             one-line wrappers: installer + code + model all from SITE_URL
#   cyberscout-src.tar.gz                       the code at the current commit (git archive HEAD)
#   cyberscout-client-*                         Client builds found in client/dist/ (and client/dist/ci/)
#   cyberscout-android.apk                      if android/build/ has it
#   SHA256SUMS, VERSION
# then rsyncs site/ to HOST:REMOTE_DIR, copies the detector model (robot.onnx) there from the server's own stack,
# and (re)starts a read-only nginx container serving it on PORT.
set -euo pipefail
SITE="${1:-http://10.0.0.58:8790}"; HOST="${2:-atomosserver}"; RDIR="${3:-/mnt/nas/autoscout-scratch/demo-site}"; PORT="${4:-8790}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; DL="$ROOT/site/downloads"

# README.txt: what is in the folder (only the files that are there), first-start steps for the unsigned client
# builds, and what "Help process matches" needs (client/README.md has the details)
write_listing() {
  local d="$1" sha="${2:-}" f desc
  {
    echo "Cyber-Scout downloads${sha:+ (code $sha)}. Install notes: client/README.md and deploy/README.md in the source."
    echo
    for f in cyberscout-client-windows-x64.exe cyberscout-client-macos-arm64 cyberscout-client-macos-x64 \
             cyberscout-client-linux-x64 cyberscout-android.apk install.sh install.ps1 get.sh get.ps1 cyberscout-src.tar.gz; do
      case "$f" in
        *windows*) desc="Cyber-Scout Client, Windows 10/11 x64 (unsigned: SmartScreen > More info > Run anyway)" ;;
        *macos-arm64) desc="Cyber-Scout Client, macOS Apple silicon (unsigned: chmod +x, then right-click > Open once)" ;;
        *macos-x64) desc="Cyber-Scout Client, macOS Intel (unsigned: chmod +x, then right-click > Open once)" ;;
        *linux-x64) desc="Cyber-Scout Client, Linux x86_64 (chmod +x)" ;;
        *.apk) desc="Android app" ;;
        install.sh) desc="server installer, Linux and macOS" ;;
        install.ps1) desc="server installer, Windows" ;;
        get.sh|get.ps1) desc="one-line server install from this site" ;;
        *) desc="the source code at this version" ;;
      esac
      [ -z "$sha" ] || [ -f "$d/$f" ] && printf '  %-36s%s\n' "$f" "$desc"
    done
    echo
    echo "Help process matches (in the client: http://127.0.0.1:7600/client/processing, admins): tracking needs an"
    echo "NVIDIA GPU (Windows / Linux; GTX 16xx / RTX 20xx or newer, driver 580+) or Apple silicon (macOS 14+)."
    echo "\"Set up processing\" downloads its runtime once into the client's data folder: about 1.8 GB for NVIDIA,"
    echo "0.25 GB on a Mac. Any computer can run Clip only (record the stream, send the server match clips)."
    echo
    echo "download.html links to exactly these names; SHA256SUMS has their checksums."
  } > "$d/README.txt"
}
if [ "${1:-}" = "--listing-only" ]; then write_listing "$DL"; echo "wrote $DL/README.txt"; exit 0; fi
cd "$ROOT"
SHA="$(git rev-parse --short HEAD)"
rm -rf "$DL"; mkdir -p "$DL"

cp deploy/install.sh deploy/install.ps1 "$DL/"
git archive --format=tar.gz --prefix=cyber-scout/ -o "$DL/cyberscout-src.tar.gz" HEAD
for f in client/dist/cyberscout-client-* client/dist/ci/cyberscout-client-*; do
  [ -f "$f" ] && case "$f" in *.sha256) ;; *) cp "$f" "$DL/";; esac
done
[ -f android/build/cyberscout-android.apk ] && cp android/build/cyberscout-android.apk "$DL/"
echo "$SHA" > "$DL/VERSION"
write_listing "$DL" "$SHA"

# model checksum comes from the server copy (the model itself is copied server-side below)
MODEL_SHA="$(ssh "$HOST" 'sha256sum /opt/stacks/cyber-scout/data/models/robot.onnx' | cut -d' ' -f1)"

cat > "$DL/get.sh" <<EOF
#!/usr/bin/env bash
# Cyber-Scout server install from $SITE (code $SHA). Usage: curl -fsSL $SITE/downloads/get.sh | bash [-s -- options]
set -euo pipefail
tmp="\$(mktemp)"; trap 'rm -f "\$tmp"' EXIT
curl -fsSL --retry 3 "$SITE/downloads/install.sh" -o "\$tmp"
bash "\$tmp" --tarball "$SITE/downloads/cyberscout-src.tar.gz" --model "$SITE/downloads/robot.onnx" --model-sha256 "$MODEL_SHA" "\$@"
EOF
cat > "$DL/get.ps1" <<EOF
# Cyber-Scout server install from $SITE (code $SHA).
# Usage (PowerShell): irm $SITE/downloads/get.ps1 | iex     (extra options: download install.ps1 and run it yourself)
\$ErrorActionPreference = 'Stop'
\$tmp = Join-Path \$env:TEMP 'cyberscout-install.ps1'
Invoke-WebRequest -UseBasicParsing -Uri '$SITE/downloads/install.ps1' -OutFile \$tmp
& powershell -NoProfile -ExecutionPolicy Bypass -File \$tmp -Tarball '$SITE/downloads/cyberscout-src.tar.gz' -Model '$SITE/downloads/robot.onnx' -ModelSha256 '$MODEL_SHA'
EOF
( cd "$DL" && shasum -a 256 * > SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS && echo "$MODEL_SHA  robot.onnx" >> SHA256SUMS )

rsync -a --delete "$ROOT/site/" "$HOST:$RDIR/"
ssh "$HOST" "cp /opt/stacks/cyber-scout/data/models/robot.onnx '$RDIR/downloads/robot.onnx' && chmod 644 '$RDIR/downloads/robot.onnx'
  docker rm -f cyber-scout-demo >/dev/null 2>&1 || true
  docker run -d --name cyber-scout-demo --restart unless-stopped --security-opt no-new-privileges:true --read-only \
    --tmpfs /var/cache/nginx --tmpfs /var/run --tmpfs /tmp -p $PORT:80 \
    -v '$RDIR':/usr/share/nginx/html:ro nginx:1.29-alpine >/dev/null
  sleep 1; curl -s -o /dev/null -w 'site %{http_code}\n' localhost:$PORT/"
echo "published $SHA to $SITE  ($(ls "$DL" | wc -l | tr -d ' ') files + robot.onnx)"
