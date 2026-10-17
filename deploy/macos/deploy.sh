#!/bin/bash
# Deploy this repo's HEAD to a Mac server running deploy/macos/install-server.sh (default host: m2).
#   deploy/macos/deploy.sh [ssh-host]
set -euo pipefail
HOST="${1:-m2}"
cd "$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short HEAD)"
git archive HEAD | ssh "$HOST" "set -e; H=\$HOME/CyberScout/server; rm -rf \$H/app.new; mkdir -p \$H/app.new;
  tar -x -C \$H/app.new; echo $SHA > \$H/app.new/VERSION; rm -rf \$H/app.old; [ -d \$H/app ] && mv \$H/app \$H/app.old;
  mv \$H/app.new \$H/app; bash \$H/app/deploy/macos/install-server.sh"
