#!/usr/bin/env bash
# Cyber-Scout installer / updater.
#
#   curl -fsSL https://raw.githubusercontent.com/Grindlewalt/Cyber-Scout/main/deploy/install.sh | bash
#
# Re-run it any time to update (pulls the code, rebuilds, keeps data/ and .env).
# Options (environment variables; all optional):
#   CS_DIR=/opt/cyber-scout      where the stack lives (app/ = code, data/ = all state, .env)
#   CS_PORT=7560                 web port on this machine
#   CS_REPO=https://github.com/Grindlewalt/Cyber-Scout.git   CS_BRANCH=main
#   CS_SOURCE=/path/to/checkout  copy code from a local checkout instead of git (offline / testing)
#   CS_MODEL_URL=https://...     download the robot detector (data/models/robot.onnx) if it's missing
#   CS_CPU=1                     force CPU-only even if a GPU is present
#   CS_PREFIX=cyber-scout        compose project / container / image name prefix (run a 2nd copy side by side)
#   CS_YES=1                     don't ask questions (use the defaults above)
#   CS_UPDATER=y|n               install the host updater for one-click updates in Admin -> Updates (systemd units;
#                                default: asks, y; with CS_YES=1: n unless CS_UPDATER=y). See deploy/updater/README.md
set -euo pipefail

CS_DIR="${CS_DIR:-/opt/cyber-scout}"
CS_PORT="${CS_PORT:-7560}"
CS_REPO="${CS_REPO:-https://github.com/Grindlewalt/Cyber-Scout.git}"
CS_BRANCH="${CS_BRANCH:-main}"
CS_PREFIX="${CS_PREFIX:-cyber-scout}"
CS_YES="${CS_YES:-0}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31mx %s\033[0m\n' "$*" >&2; exit 1; }
ask() {  # ask "question" default -> echo answer (reads the terminal even when piped from curl)
  local q="$1" d="$2" a=""
  if [ "$CS_YES" = "1" ] || ! { true </dev/tty; } 2>/dev/null; then echo "$d"; return; fi
  read -r -p "$q [$d]: " a </dev/tty || true
  echo "${a:-$d}"
}

bold "Cyber-Scout installer"

# ---------------------------------------------------------------- privileges
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null && SUDO="sudo" || true
fi
HOST_UID="${SUDO_UID:-$(id -u)}"; HOST_GID="${SUDO_GID:-$(id -g)}"

# ---------------------------------------------------------------- Docker + compose
command -v docker >/dev/null || die "Docker is not installed. Install it first: https://docs.docker.com/engine/install/ (Linux: curl -fsSL https://get.docker.com | sh), then re-run."
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then DOCKER="$SUDO docker"
  else die "Can't talk to Docker (is the daemon running? is your user in the 'docker' group?)"; fi
fi
$DOCKER compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing (the 'docker compose' plugin). See https://docs.docker.com/compose/install/"
CV="$($DOCKER compose version --short 2>/dev/null | sed 's/^v//')"
info "Docker $($DOCKER version --format '{{.Server.Version}}' 2>/dev/null || echo '?'), Compose $CV"
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }

# ---------------------------------------------------------------- GPU
GPU=0
if [ "${CS_CPU:-0}" != "1" ]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
    if $DOCKER info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia || command -v nvidia-container-cli >/dev/null || command -v nvidia-ctk >/dev/null; then
      GPU=1; info "NVIDIA GPU + container toolkit found: $(nvidia-smi -L | head -1)"
    else
      warn "An NVIDIA GPU is present but the NVIDIA Container Toolkit isn't installed, so containers can't use it."
      warn "Install it for ~4x faster processing: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
    fi
  fi
fi
if [ "$GPU" = "0" ]; then
  warn "Running CPU-only: each match takes ~4x longer to process (~20 min instead of ~3-6). Everything else works."
  ver_ge "$CV" "2.24.4" || die "CPU-only mode needs Docker Compose >= 2.24.4 (you have $CV). Update Docker, or install the NVIDIA Container Toolkit."
fi

# ---------------------------------------------------------------- where
CS_DIR="$(ask "Install directory" "$CS_DIR")"
CS_PORT="$(ask "Web port" "$CS_PORT")"
case "$CS_PORT" in ''|*[!0-9]*) die "bad port: $CS_PORT";; esac
if [ ! -d "$CS_DIR" ]; then
  mkdir -p "$CS_DIR" 2>/dev/null || $SUDO mkdir -p "$CS_DIR"
fi
[ -w "$CS_DIR" ] || $SUDO chown "$HOST_UID:$HOST_GID" "$CS_DIR"
cd "$CS_DIR"
bold "Installing into $CS_DIR"

# ---------------------------------------------------------------- code
if [ -n "${CS_SOURCE:-}" ]; then
  info "copying code from $CS_SOURCE"
  mkdir -p app
  tar -C "$CS_SOURCE" --exclude=.git --exclude=.venv --exclude=.work --exclude=data --exclude=node_modules \
      --exclude='__pycache__' --exclude=.env --exclude='*.key' --exclude=.roboflow_key -cf - . | tar -C app -xf -
elif [ -d app/.git ]; then
  command -v git >/dev/null || die "git is required to update (apt install git)"
  info "updating code (git pull)"
  git -C app fetch --quiet origin "$CS_BRANCH" && git -C app checkout --quiet "$CS_BRANCH" && git -C app pull --quiet --ff-only origin "$CS_BRANCH"
else
  command -v git >/dev/null || die "git is required (apt install git / dnf install git)"
  [ -e app ] && die "$CS_DIR/app exists but isn't a git checkout; move it away first"
  info "cloning $CS_REPO ($CS_BRANCH)"
  git clone --quiet --depth 1 --branch "$CS_BRANCH" "$CS_REPO" app
fi
for f in docker-compose.yml docker-compose.cpu.yml; do
  if [ -f "$f" ] && ! cmp -s "app/deploy/$f" "$f"; then cp "$f" "$f.bak"; info "previous $f saved as $f.bak"; fi
  cp "app/deploy/$f" "$f"
done

# ---------------------------------------------------------------- .env (infra only; app settings live in the app)
if [ ! -f .env ]; then
  info "writing .env"
  umask 077
  {
    echo "# Cyber-Scout infrastructure settings (written by install.sh). App settings live in the web app."
    echo "WEB_PORT=$CS_PORT"
    echo "PUID=$HOST_UID"
    echo "PGID=$HOST_GID"
    echo "TZ=$(cat /etc/timezone 2>/dev/null || (timedatectl show -p Timezone --value 2>/dev/null) || echo UTC)"
    echo "MEDIA_DIR=./data/media"
    echo "YT_FORCE_IPV4=1"
    [ "$CS_PREFIX" != "cyber-scout" ] && echo "CS_PREFIX=$CS_PREFIX"
    [ "$GPU" = "0" ] && echo "COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml"
    true
  } > .env
  umask 022
else
  info "keeping existing .env"
  if [ "$GPU" = "0" ] && ! grep -q '^COMPOSE_FILE=' .env; then
    echo "COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml" >> .env; info "added the CPU-only override to .env"
  fi
fi
CS_PORT="$(grep -E '^WEB_PORT=' .env | tail -1 | cut -d= -f2)"; CS_PORT="${CS_PORT:-7560}"

# ---------------------------------------------------------------- data dir + model
mkdir -p data/models data/media data/site/api data/config
if [ "$(stat -c %u data 2>/dev/null || stat -f %u data)" != "$HOST_UID" ]; then $SUDO chown "$HOST_UID:$HOST_GID" data data/models data/media data/site data/site/api data/config 2>/dev/null || true; fi
if [ ! -s data/models/robot.onnx ]; then
  if [ -n "${CS_MODEL_URL:-}" ]; then
    info "downloading the robot detector"
    curl -fL --retry 3 -o data/models/robot.onnx.part "$CS_MODEL_URL" && mv data/models/robot.onnx.part data/models/robot.onnx
  else
    warn "No robot detector at $CS_DIR/data/models/robot.onnx. The site and setup work without it, but matches"
    warn "won't be processed until it's there (re-run with CS_MODEL_URL=... or copy the file in; no restart needed)."
  fi
fi

# ---------------------------------------------------------------- start
bold "Building and starting (first build takes a few minutes)"
$DOCKER compose up -d --build
info "waiting for the app to come up"
ok=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$CS_PORT/app/v1/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ "$ok" = "1" ] || { warn "the app didn't answer on port $CS_PORT yet; check: cd $CS_DIR && docker compose logs api web"; }

IP="$( (ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src"){print $(i+1); exit}}') || true)"
[ -n "$IP" ] || IP="$( (hostname -I 2>/dev/null | awk '{print $1}') || true)"
[ -n "$IP" ] || IP="$(hostname 2>/dev/null || echo localhost)"
TS="$(command -v tailscale >/dev/null && tailscale ip -4 2>/dev/null | head -1 || true)"
echo
bold "Cyber-Scout is running:"
info "http://$IP:$CS_PORT"
[ -n "$TS" ] && info "http://$TS:$CS_PORT   (Tailscale)"
CODE=""
for _ in $(seq 1 10); do
  CODE="$(cat data/setup-code.txt 2>/dev/null || $SUDO cat data/setup-code.txt 2>/dev/null || true)"
  [ -n "$CODE" ] && break; sleep 1
done
if [ -n "$CODE" ]; then
  echo
  bold "First-time setup code: $CODE"
  info "Open the site, enter this code and create the admin account. (Later: cat $CS_DIR/data/setup-code.txt)"
fi

# ---------------------------------------------------------------- one-click updates (optional host updater)
# A small systemd service on this machine (NOT in a container: no container gets the docker socket) that does
# check / update / roll back when an admin asks in Admin -> Updates. Runs as the folder's owner (docker group).
UPD="$CS_DIR/app/deploy/updater/install-updater.sh"
UPDATER_OK=0
if [ -f "$UPD" ] && command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
  if [ "$CS_YES" = "1" ]; then def="${CS_UPDATER:-n}"; else def="${CS_UPDATER:-y}"; fi
  echo
  a="$(ask "Install the updater for one-click updates in Admin -> Updates (systemd units, runs as $(stat -c %U "$CS_DIR"))? y/n" "$def")"
  case "$a" in
    y|Y|yes|YES)
      if [ "$(id -u)" -eq 0 ]; then bash "$UPD" --stack "$CS_DIR" --yes && UPDATER_OK=1
      elif [ -n "$SUDO" ]; then $SUDO bash "$UPD" --stack "$CS_DIR" --yes && UPDATER_OK=1
      else bash "$UPD" --stack "$CS_DIR" --user --yes && UPDATER_OK=1; fi
      [ "$UPDATER_OK" = "1" ] || warn "the updater wasn't installed; updates work by re-running this installer (or later: sudo $UPD)";;
    *) info "Skipped. Later: sudo $UPD   (remove: sudo $UPD --uninstall)";;
  esac
fi

echo
info "Logs:    cd $CS_DIR && docker compose logs -f worker"
if [ "$UPDATER_OK" = "1" ]; then info "Update:  Admin -> Updates in the web app (or re-run this installer)."
else info "Update:  re-run this installer."; fi
info "Backup:  the whole $CS_DIR folder (data/ holds everything)."
