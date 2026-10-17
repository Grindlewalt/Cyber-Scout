#!/bin/bash
# Deploy this repo's HEAD to a Mac server running deploy/macos/install-server.sh (default host: m2).
#   deploy/macos/deploy.sh [ssh-host]
# Made for a flaky link (venue Wi-Fi / hotspot over Tailscale): every ssh step is retried, the upload resumes
# where it stopped (checked by sha256), and the install runs DETACHED on the server (nohup), so a dropped
# connection can't stop it halfway through restarting the services; this script then polls its log.
set -uo pipefail
HOST="${1:-m2}"
cd "$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short HEAD)"
TAR="$(mktemp -t cs-deploy)"; trap 'rm -f "$TAR"' EXIT
git archive -o "$TAR" --format=tar HEAD -- . ':!lab' ':!android'      # (lab/ android/: not needed on a server)
SUM="$(shasum -a 256 "$TAR" | cut -c1-64)"; SIZE=$(wc -c < "$TAR" | tr -d ' ')
SSH=(ssh -o ConnectTimeout=20 -o ServerAliveInterval=5 -o ServerAliveCountMax=6 -o BatchMode=yes "$HOST")
R="CyberScout/server/deploy-$SHA.tar"; LOG="CyberScout/server/logs/deploy-$SHA.log"
retry() {  # tries, command...
  local n="$1" i; shift
  for i in $(seq 1 "$n"); do "$@" && return 0; echo "  (connection problem, retry $i/$n)" >&2; sleep 5; done
  return 1
}

echo "deploy $SHA to $HOST: upload $((SIZE / 1000000)) MB"
up() {
  local have
  have=$("${SSH[@]}" "f=\$HOME/$R; if [ -f \$f ]; then wc -c < \$f; else echo 0; fi" | tr -d ' ') || return 1
  [ -n "$have" ] || return 1
  if [ "$have" -gt "$SIZE" ]; then "${SSH[@]}" "rm -f \$HOME/$R" || return 1; have=0; fi
  if [ "$have" -lt "$SIZE" ]; then
    echo "  sending from byte $have"
    tail -c +$((have + 1)) "$TAR" | "${SSH[@]}" "cat >> \$HOME/$R" || return 1
  fi
  [ "$("${SSH[@]}" "shasum -a 256 \$HOME/$R | cut -c1-64")" = "$SUM" ] && return 0
  echo "  checksum mismatch: starting over"; "${SSH[@]}" "rm -f \$HOME/$R"; return 1
}
retry 20 up || { echo "upload failed"; exit 1; }
retry 10 "${SSH[@]}" "rm -f \$HOME/$LOG" || { echo "could not reach $HOST"; exit 1; }

echo "install (detached on $HOST; log ~/$LOG)"
# idempotent: a retry after a dropped connection must not start a second install (the log exists by then)
retry 10 "${SSH[@]}" "if [ -e \$HOME/$LOG ]; then echo 'already started'; else nohup bash -c '
    set -e; H=\$HOME/CyberScout/server; rm -rf \$H/app.new; mkdir -p \$H/app.new
    tar -x -f \$HOME/$R -C \$H/app.new; echo $SHA > \$H/app.new/VERSION; rm -rf \$H/app.old
    if [ -d \$H/app ]; then mv \$H/app \$H/app.old; fi; mv \$H/app.new \$H/app; rm -f \$HOME/$R
    bash \$H/app/deploy/macos/install-server.sh; echo DEPLOY-DONE' > \$HOME/$LOG 2>&1 < /dev/null & echo started; fi" \
  || { echo "could not start the install"; exit 1; }
for i in $(seq 1 60); do
  sleep 5
  out=$("${SSH[@]}" "cat \$HOME/$LOG; pgrep -f 'deploy-$SHA[.]tar' >/dev/null && echo INSTALL-RUNNING; true" 2>/dev/null) || continue
  if grep -q DEPLOY-DONE <<<"$out"; then grep -v "DEPLOY-DONE\|INSTALL-RUNNING" <<<"$out" | tail -8; echo "deployed $SHA"; exit 0; fi
  if ! grep -q INSTALL-RUNNING <<<"$out"; then tail -15 <<<"$out"; echo "install FAILED (log ~/$LOG on $HOST)"; exit 1; fi
done
echo "install still running after 5 min: check ~/$LOG on $HOST"; exit 1
