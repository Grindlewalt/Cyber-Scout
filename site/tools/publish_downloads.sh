#!/usr/bin/env bash
# Publish the downloads site: everything needed to install Cyber-Scout from one URL (no GitHub access needed).
#
#   bash site/tools/publish_downloads.sh SITE_URL [HOST] [REMOTE_DIR] [PORT]
#   e.g. bash site/tools/publish_downloads.sh http://10.0.0.58:7580 atomosserver /mnt/nas/autoscout-scratch/demo-site 7580
#   bash site/tools/publish_downloads.sh --listing-only [SITE_URL]     only rewrite site/downloads/README.txt
#
# SITE_URL is the address other computers will use to reach this site (scheme, host, optional port; no path). It is
# baked into get.sh / get.ps1, so the one-line installs only work on computers that can reach it: a LAN address means
# the shop network (or a VPN into it). PORT defaults to SITE_URL's port. HOST defaults to atomosserver, REMOTE_DIR to
# /mnt/nas/autoscout-scratch/demo-site.
#
# CS_SERVER_URL (optional, e.g. http://10.0.0.58:7560): the team's server. The Client one-liners preset it on a first
# install, so a laptop only has to sign in. Unset: the Client asks for it (Settings, Connection).
#
# Builds site/downloads/ (gitignored, except README.txt) on this machine:
#   install.sh, install.ps1                     server installers (Linux/macOS, Windows)
#   get.sh, get.ps1                             one-line wrappers: download install.sh / install.ps1 and the code from
#                                               SITE_URL, check their SHA-256 (baked in here), install with the model
#   get-client.sh, get-client.ps1               one-line Client installs (from site/tools/get-client.*.in): this
#                                               computer's build, SHA-256 checked, started at login, opened
#   cyberscout-src.tar.gz                       the code at the current commit (git archive HEAD)
#   cyberscout-client-*                         Client builds found in client/dist/ (and client/dist/ci/)
#   cyberscout-android.apk                      if android/build/ has it
#   SHA256SUMS, VERSION ("<commit> <commit date> <SITE_URL>"), README.txt (written last: lists what is there)
# then rsyncs site/ to HOST:REMOTE_DIR, copies the detector model (robot.onnx) there from the server's own stack
# (CS_MODEL_SRC on HOST, else /opt/cyber-scout or /opt/stacks/cyber-scout; else the copy already published stays),
# and (re)starts a read-only nginx container serving it on PORT with site/tools/nginx.conf. It refuses a PORT that
# something other than its own container is listening on.
set -euo pipefail
die() { echo "publish_downloads: $*" >&2; exit 2; }
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; DL="$ROOT/site/downloads"
LISTING_ONLY=0
if [ "${1:-}" = "--listing-only" ]; then LISTING_ONLY=1; shift; fi
SITE="${1:-}"; HOST="${2:-atomosserver}"; RDIR="${3:-/mnt/nas/autoscout-scratch/demo-site}"; PORT="${4:-}"

# ---- SITE: required (no default: a wrong address gets baked into every installer), checked, private ones flagged
if [ -n "$SITE" ] || [ "$LISTING_ONLY" = 0 ]; then
  [ -n "$SITE" ] || die "SITE_URL is required, e.g. http://10.0.0.58:7580 (see the top of this script)"
  SITE="${SITE%/}"
  [[ "$SITE" =~ ^https?://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:([0-9]{1,5}))?$ ]] ||
    die "SITE_URL must be http(s)://host[:port] with no path, got: $SITE"
  SITE_HOST="${BASH_REMATCH[1]}"; SITE_PORT="${BASH_REMATCH[3]}"
  case "$SITE_HOST" in
    10.*|192.168.*|127.*|localhost|*.local|*.lan|*.home|*.internal|\[*) PRIVATE=1 ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) PRIVATE=1 ;;
    100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) PRIVATE=1 ;;     # CGNAT / Tailscale
    *.*) PRIVATE=0 ;;
    *) PRIVATE=1 ;;                                                                # a bare LAN hostname
  esac
  if [ "$PRIVATE" = 1 ]; then
    echo "NOTE: $SITE is a private address. get.sh / get.ps1 download from it, so the one-line installs only work on" >&2
    echo "      computers that can reach $SITE_HOST (the same network, or a VPN into it). The page tells visitors so." >&2
  fi
fi

# README.txt: what is in the folder (only the files that are there), the one-line installs, first-start steps for the
# unsigned client builds, and what "Help process matches" needs (client/README.md has the details)
write_listing() {
  local d="$1" sha="${2:-}" f desc s="${SITE:-<this site>}"
  {
    echo "Cyber-Scout downloads${sha:+ (code $sha)}. Install notes: client/README.md and deploy/README.md in the source."
    echo
    echo "One-line server install (works where $s is reachable; downloads the code and the model from it):"
    echo "  Linux / macOS:        curl -fsSL $s/downloads/get.sh | bash"
    echo "  Windows (PowerShell): irm $s/downloads/get.ps1 | iex"
    echo
    echo "One-line Client install (installs, starts at login, opens it${CS_SERVER_URL:+; connects to $CS_SERVER_URL}):"
    echo "  macOS / Linux:        curl -fsSL $s/downloads/get-client.sh | bash"
    echo "  Windows (PowerShell): irm $s/downloads/get-client.ps1 | iex"
    echo
    for f in cyberscout-client-windows-x64.exe cyberscout-client-macos-arm64 cyberscout-client-macos-x64 \
             cyberscout-client-linux-x64 cyberscout-android.apk install.sh install.ps1 get.sh get.ps1 get-client.sh \
             get-client.ps1 robot.onnx \
             cyberscout-src.tar.gz SHA256SUMS; do
      case "$f" in
        *windows*) desc="Cyber-Scout Client, Windows 10/11 x64 (unsigned: the browser may ask to Keep it; SmartScreen > More info > Run anyway)" ;;
        *macos-arm64) desc="Cyber-Scout Client, macOS Apple silicon (unsigned: see macOS below)" ;;
        *macos-x64) desc="Cyber-Scout Client, macOS Intel (unsigned: see macOS below)" ;;
        *linux-x64) desc="Cyber-Scout Client, Linux x86_64 (chmod +x, then run it)" ;;
        *.apk) desc="Android app (debug-signed: allow this source; Play Protect > More details > Install anyway)" ;;
        install.sh) desc="server installer, Linux and macOS" ;;
        install.ps1) desc="server installer, Windows" ;;
        get.sh) desc="one-line server install, Linux and macOS (checks the installer and code SHA-256)" ;;
        get.ps1) desc="one-line server install, Windows (checks the installer and code SHA-256)" ;;
        get-client.sh) desc="one-line Client install, macOS and Linux (checks the build's SHA-256; starts at login)" ;;
        get-client.ps1) desc="one-line Client install, Windows (checks the build's SHA-256; starts at sign-in)" ;;
        robot.onnx) desc="the robot detector model (the installers download it with --model / -Model)" ;;
        SHA256SUMS) desc="SHA-256 of the files above" ;;
        *) desc="the source code at this version" ;;
      esac
      { [ -z "$sha" ] || [ -f "$d/$f" ] || { [ "$f" = robot.onnx ] && [ -n "${MODEL_SHA:-}" ]; }; } && printf '  %-36s%s\n' "$f" "$desc"
    done
    echo
    echo "macOS Client by hand (the one-liner above skips all of this): in Terminal (Intel Macs: -x64, not -arm64):"
    echo "  cd ~/Downloads"
    echo "  xattr -d com.apple.quarantine cyberscout-client-macos-arm64"
    echo "  chmod +x cyberscout-client-macos-arm64"
    echo "  ./cyberscout-client-macos-arm64"
    echo "If macOS still blocks it: System Settings > Privacy & Security > Open Anyway (right-click > Open no longer"
    echo "does this on macOS 15)."
    echo
    echo "Help process matches (in the client: http://127.0.0.1:7600/client/processing, admins): tracking needs an"
    echo "NVIDIA GPU (Windows / Linux; GTX 16xx / RTX 20xx or newer, driver 580+) or Apple silicon (macOS 14+)."
    echo "\"Set up processing\" downloads its runtime once into the client's data folder: about 1.8 GB for NVIDIA,"
    echo "0.25 GB on a Mac. Any computer can run Clip only (record the stream, send the server match clips)."
    echo
    echo "download.html links to exactly these names; SHA256SUMS has their checksums."
  } > "$d/README.txt"
}
if [ "$LISTING_ONLY" = 1 ]; then mkdir -p "$DL"; write_listing "$DL"; echo "wrote $DL/README.txt"; exit 0; fi

# ---- CS_SERVER_URL: optional team server for the Client one-liners
CS_SERVER_URL="${CS_SERVER_URL:-}"; CS_SERVER_URL="${CS_SERVER_URL%/}"
[ -z "$CS_SERVER_URL" ] || [[ "$CS_SERVER_URL" =~ ^https?://[A-Za-z0-9.:\[\]_-]+(/[A-Za-z0-9._~/-]*)?$ ]] ||
  die "CS_SERVER_URL must look like http://10.0.0.58:7560, got: $CS_SERVER_URL"

# ---- PORT: SITE's own port unless given; 8790 on atomosserver is another app (7580 is the site's)
PORT="${PORT:-$SITE_PORT}"
[ -n "$PORT" ] || die "no PORT: give it as the 4th argument (SITE_URL has no port)"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "bad PORT: $PORT"
[ -z "$SITE_PORT" ] || [ "$SITE_PORT" = "$PORT" ] ||
  echo "NOTE: SITE_URL says port $SITE_PORT but the container will listen on $PORT (fine behind a proxy)." >&2

cd "$ROOT"
SHA="$(git rev-parse --short HEAD)"; DAY="$(git log -1 --format=%cs HEAD)"
rm -rf "$DL"; mkdir -p "$DL"

cp deploy/install.sh deploy/install.ps1 "$DL/"
git archive --format=tar.gz --prefix=cyber-scout/ -o "$DL/cyberscout-src.tar.gz" HEAD
for f in client/dist/cyberscout-client-* client/dist/ci/cyberscout-client-*; do
  [ -f "$f" ] && case "$f" in *.sha256) ;; *) cp "$f" "$DL/";; esac
done
[ -f android/build/cyberscout-android.apk ] && cp android/build/cyberscout-android.apk "$DL/"
echo "$SHA $DAY $SITE" > "$DL/VERSION"

sum() { (sha256sum "$1" 2>/dev/null || shasum -a 256 "$1") | cut -d' ' -f1; }
INSTALL_SH="$(sum "$DL/install.sh")"; INSTALL_PS1="$(sum "$DL/install.ps1")"; TARBALL="$(sum "$DL/cyberscout-src.tar.gz")"
# model: the server's own copy (copied server-side below), wherever the stack lives; else the one already published
MODEL_SRC="$(ssh "$HOST" bash -s -- "$RDIR" "${CS_MODEL_SRC:-}" <<'FIND'
for p in "$2" /opt/cyber-scout/data/models/robot.onnx /opt/stacks/cyber-scout/data/models/robot.onnx \
         "$1/downloads/robot.onnx"; do [ -n "$p" ] && [ -f "$p" ] && { echo "$p"; exit 0; }; done
FIND
)"
[ -n "$MODEL_SRC" ] || die "no robot.onnx on $HOST (set CS_MODEL_SRC=<path on $HOST>)"
MODEL_SHA="$(ssh "$HOST" sha256sum "$MODEL_SRC" | cut -d' ' -f1)"
[[ "$MODEL_SHA" =~ ^[0-9a-f]{64}$ ]] || die "couldn't read the model's SHA-256 on $HOST ($MODEL_SRC)"
echo "model: $HOST:$MODEL_SRC"

cat > "$DL/get.sh" <<EOF
#!/usr/bin/env bash
# Cyber-Scout server install from $SITE (code $SHA, $DAY).
#   curl -fsSL $SITE/downloads/get.sh | bash [-s -- installer options]
# Downloads install.sh and the code from the site, checks both against the SHA-256 below (written when the site was
# published), then runs the installer, which downloads the detector model and checks it the same way.
# Works where the site is reachable ($SITE: same network, or a VPN into it). Reached it at another address?
#   curl -fsSL <that address>/downloads/get.sh | CS_SITE=<that address> bash
set -euo pipefail
SITE="\${CS_SITE:-$SITE}"; SITE="\${SITE%/}"
d="\$(mktemp -d)"; trap 'rm -rf "\$d"' EXIT
fetch() {  # fetch NAME SHA256
  curl -fsSL --retry 3 "\$SITE/downloads/\$1" -o "\$d/\$1" ||
    { echo "couldn't download \$SITE/downloads/\$1: is this computer on a network that can reach \$SITE?" >&2; exit 1; }
  local got; got="\$( (sha256sum "\$d/\$1" 2>/dev/null || shasum -a 256 "\$d/\$1") | cut -d' ' -f1)"
  [ "\$got" = "\$2" ] || { echo "\$1: SHA-256 \$got isn't the published \$2: not running it" >&2; exit 1; }
}
fetch install.sh $INSTALL_SH
fetch cyberscout-src.tar.gz $TARBALL
CS_SITE="\$SITE" bash "\$d/install.sh" --tarball "\$d/cyberscout-src.tar.gz" --model "\$SITE/downloads/robot.onnx" --model-sha256 $MODEL_SHA "\$@"
EOF
cat > "$DL/get.ps1" <<EOF
# Cyber-Scout server install from $SITE (code $SHA, $DAY).
#   irm $SITE/downloads/get.ps1 | iex
# Downloads install.ps1 and the code from the site, checks both against the SHA-256 below (written when the site was
# published), then runs the installer, which downloads the detector model and checks it the same way.
# Works where the site is reachable ($SITE: same network, or a VPN into it). Reached it at another address?
#   \$env:CS_SITE = '<that address>'; irm <that address>/downloads/get.ps1 | iex
# Extra installer options: download install.ps1 and run it yourself (see download.html).
& {
  \$ErrorActionPreference = 'Stop'; \$ProgressPreference = 'SilentlyContinue'
  \$site = if (\$env:CS_SITE) { \$env:CS_SITE.TrimEnd('/') } else { '$SITE' }
  \$d = Join-Path ([IO.Path]::GetTempPath()) ('cyberscout-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Path \$d | Out-Null
  try {
    \$want = [ordered]@{ 'install.ps1' = '$INSTALL_PS1'; 'cyberscout-src.tar.gz' = '$TARBALL' }
    foreach (\$f in \$want.Keys) {
      \$p = Join-Path \$d \$f
      try { Invoke-WebRequest -UseBasicParsing -Uri "\$site/downloads/\$f" -OutFile \$p }
      catch { throw "couldn't download \$site/downloads/\$f\`: is this computer on a network that can reach \$site?" }
      \$h = (Get-FileHash -LiteralPath \$p -Algorithm SHA256).Hash.ToLowerInvariant()
      if (\$h -ne \$want[\$f]) { throw "\$f\`: SHA-256 \$h isn't the published \$(\$want[\$f]): not running it" }
    }
    \$env:CS_SITE = \$site                    # the installer's summary prints the Client one-liner from it
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path \$d 'install.ps1') -Tarball (Join-Path \$d 'cyberscout-src.tar.gz') -Model "\$site/downloads/robot.onnx" -ModelSha256 '$MODEL_SHA'
  } finally { Remove-Item -LiteralPath \$d -Recurse -Force -ErrorAction SilentlyContinue }
}
EOF
csum() { [ -f "$DL/$1" ] && sum "$DL/$1" || true; }
fill() {   # fill TEMPLATE OUT
  sed -e "s|@SITE@|$SITE|g" -e "s|@SHA@|$SHA|g" -e "s|@DAY@|$DAY|g" -e "s|@SERVER@|$CS_SERVER_URL|g" \
      -e "s|@SERVER_SHOWN@|${CS_SERVER_URL:-none, the Client asks}|g" \
      -e "s|@SHA_macos_arm64@|$(csum cyberscout-client-macos-arm64)|g" -e "s|@SHA_macos_x64@|$(csum cyberscout-client-macos-x64)|g" \
      -e "s|@SHA_linux_x64@|$(csum cyberscout-client-linux-x64)|g" \
      -e "s|@SHA_windows_x64@|$(csum cyberscout-client-windows-x64.exe)|g" "$1" > "$2"
  ! grep -q '@[A-Za-z_0-9]*@' "$2" || die "unfilled placeholder in $2"
}
fill site/tools/get-client.sh.in "$DL/get-client.sh"
fill site/tools/get-client.ps1.in "$DL/get-client.ps1"
( cd "$DL" && shasum -a 256 * > SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS && echo "$MODEL_SHA  robot.onnx" >> SHA256SUMS )
write_listing "$DL" "$SHA"                       # last: it lists only what is really there (and the model)

rsync -a --delete --exclude /downloads/robot.onnx "$ROOT/site/" "$HOST:$RDIR/"
ssh "$HOST" bash -s -- "$RDIR" "$PORT" "$MODEL_SRC" <<'REMOTE'
set -e
RDIR="$1"; PORT="$2"; MODEL_SRC="$3"
[ "$MODEL_SRC" = "$RDIR/downloads/robot.onnx" ] || cp "$MODEL_SRC" "$RDIR/downloads/robot.onnx"
chmod 644 "$RDIR/downloads/robot.onnx"
mine="$(docker port cyber-scout-demo 80/tcp 2>/dev/null | sed -n 's/.*:\([0-9][0-9]*\)$/\1/p' | head -1)"
if [ "$mine" != "$PORT" ] && ss -Hltn "sport = :$PORT" 2>/dev/null | grep -q .; then
  echo "port $PORT is already in use by something else on $(hostname): not touching it (the site keeps its port ${mine:-none})" >&2
  exit 3
fi
docker rm -f cyber-scout-demo >/dev/null 2>&1 || true
docker run -d --name cyber-scout-demo --restart unless-stopped --security-opt no-new-privileges:true --read-only \
  --tmpfs /var/cache/nginx --tmpfs /var/run --tmpfs /tmp -p "$PORT:80" \
  -v "$RDIR":/usr/share/nginx/html:ro -v "$RDIR/tools/nginx.conf":/etc/nginx/conf.d/default.conf:ro \
  nginx:1.29-alpine >/dev/null
sleep 1; curl -s -o /dev/null -w 'site %{http_code}\n' "localhost:$PORT/"
REMOTE
echo "published $SHA ($DAY) to $SITE  ($(ls "$DL" | wc -l | tr -d ' ') files + robot.onnx)"
