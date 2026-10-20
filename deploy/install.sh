#!/usr/bin/env bash
# Cyber-Scout server installer for Linux and macOS (Windows: deploy/install.ps1). Re-run it any time to update:
# it pulls the code, rebuilds and restarts, and keeps data/ and .env.
#
#   curl -fsSL https://raw.githubusercontent.com/Grindlewalt/Cyber-Scout/main/deploy/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --cpu --port 8080        (options go after "bash -s --")
#   bash deploy/install.sh [options]                                 (from a checkout / downloaded copy)
#   bash install.sh --uninstall [--purge]                            (remove)
#
# Options (most can also be set as the environment variable in brackets):
#   --dir DIR             install folder [CS_DIR]  (Linux: /opt/cyber-scout, macOS: ~/CyberScout)
#   --port N              web port [CS_PORT]  (7560; if it's taken, the next free one is offered)
#   --bind ADDR           publish the port on this address only (LAN / Tailscale IP, 127.0.0.1) [CS_BIND] (0.0.0.0)
#   --repo URL            git repo [CS_REPO] (https://github.com/Grindlewalt/Cyber-Scout.git; a private GitHub repo
#                         is cloned over SSH when https fails and you have an SSH key)
#   --branch NAME         git branch [CS_BRANCH] (main)
#   --tarball PATH|URL    install from a source tarball (.tar.gz) instead of git, e.g. offline [CS_TARBALL]
#   --source DIR          copy the code from a local checkout instead of git [CS_SOURCE]
#   --model PATH|URL      robot detector to put at data/models/robot.onnx when it's missing [CS_MODEL_URL]
#   --model-sha256 HEX    refuse the detector unless its SHA-256 matches [CS_MODEL_SHA256]
#   --cpu | --gpu         force CPU-only / GPU mode (default: GPU when an NVIDIA GPU works in containers) [CS_CPU=1]
#   --gpu-ids IDS         GPU(s) for the worker, nvidia-smi numbering, e.g. 1 or 0,1 [CS_GPU_IDS] (0)
#   --prefix NAME         compose project / container / image prefix (a 2nd copy side by side) [CS_PREFIX]
#   --services LIST       start only these services now, e.g. api,web (skips the ~3.5 GB worker build) [CS_SERVICES]
#   --no-worker           same as --services api,web
#   --worker-image IMG    reuse an existing worker image (tagged <prefix>-worker:latest) instead of building it
#   --use-images          build nothing: start the existing <prefix>-worker/api/web images
#   --updater | --no-updater   install the host updater (Linux + systemd only) [CS_UPDATER=y|n]
#   --install-docker      (Linux) allowed to install Docker Engine with Docker's get.docker.com script
#   --yes, -y             no questions; use the defaults [CS_YES=1]
#   --dry-run             print what would be done; change nothing [CS_DRY_RUN=1]
#   --uninstall           stop and remove the containers, network and images; keep app/, data/ and .env
#   --purge               with --uninstall: also delete the whole install folder (all data!), after confirming
#   --keep-images         with --uninstall: leave the images
#   --replace-app         replace an app/ folder that isn't a git checkout (kept as app.bak-<time>); asked otherwise
#   --allow-root          allow running as root without sudo (files and containers would then run as root)
#   -h, --help            this help
set -euo pipefail

# Everything is inside main() so a half-downloaded `curl | bash` can't run a partial script.
main() {

DEFAULT_REPO="https://github.com/Grindlewalt/Cyber-Scout.git"
OS="$(uname -s)"
case "$OS" in
  Linux) DEFAULT_DIR="/opt/cyber-scout";;
  Darwin) DEFAULT_DIR="$HOME/CyberScout";;
  *) printf 'Unsupported OS: %s. On Windows use deploy/install.ps1 (PowerShell).\n' "$OS" >&2; exit 1;;
esac

CS_DIR="${CS_DIR:-}"; CS_PORT="${CS_PORT:-}"; CS_BIND="${CS_BIND:-}"
CS_REPO="${CS_REPO:-$DEFAULT_REPO}"; CS_BRANCH="${CS_BRANCH:-main}"
CS_TARBALL="${CS_TARBALL:-}"; CS_SOURCE="${CS_SOURCE:-}"; CS_MODEL_URL="${CS_MODEL_URL:-${CS_MODEL:-}}"
CS_PREFIX="${CS_PREFIX:-}"; CS_GPU_IDS="${CS_GPU_IDS:-}"; CS_SERVICES="${CS_SERVICES:-}"
CS_YES="${CS_YES:-0}"; CS_UPDATER="${CS_UPDATER:-}"; DRY="${CS_DRY_RUN:-0}"
MODE_CPU=""; [ "${CS_CPU:-0}" = "1" ] && MODE_CPU=cpu
WORKER_IMAGE="${CS_WORKER_IMAGE:-}"; USE_IMAGES=0; INSTALL_DOCKER=0
UNINSTALL=0; PURGE=0; KEEP_IMAGES=0; REPLACE_APP=0; ALLOW_ROOT=0; CS_MODEL_SHA256="${CS_MODEL_SHA256:-}"
need_arg() { if [ $# -lt 2 ] || [ -z "$2" ]; then printf 'option %s needs a value\n' "$1" >&2; exit 2; fi; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) need_arg "$@"; CS_DIR="$2"; shift 2;;
    --port) need_arg "$@"; CS_PORT="$2"; shift 2;;
    --bind) need_arg "$@"; CS_BIND="$2"; shift 2;;
    --repo) need_arg "$@"; CS_REPO="$2"; shift 2;;
    --branch) need_arg "$@"; CS_BRANCH="$2"; shift 2;;
    --tarball) need_arg "$@"; CS_TARBALL="$2"; shift 2;;
    --source) need_arg "$@"; CS_SOURCE="$2"; shift 2;;
    --model) need_arg "$@"; CS_MODEL_URL="$2"; shift 2;;
    --cpu) MODE_CPU=cpu; shift;;
    --gpu) MODE_CPU=gpu; shift;;
    --gpu-ids) need_arg "$@"; CS_GPU_IDS="$2"; shift 2;;
    --prefix) need_arg "$@"; CS_PREFIX="$2"; shift 2;;
    --services) need_arg "$@"; CS_SERVICES="$2"; shift 2;;
    --no-worker) CS_SERVICES="api,web"; shift;;
    --worker-image) need_arg "$@"; WORKER_IMAGE="$2"; shift 2;;
    --use-images) USE_IMAGES=1; shift;;
    --updater) CS_UPDATER=y; shift;;
    --no-updater) CS_UPDATER=n; shift;;
    --install-docker) INSTALL_DOCKER=1; shift;;
    --yes|-y) CS_YES=1; shift;;
    --dry-run) DRY=1; shift;;
    --uninstall) UNINSTALL=1; shift;;
    --purge) PURGE=1; shift;;
    --keep-images) KEEP_IMAGES=1; shift;;
    --model-sha256) need_arg "$@"; CS_MODEL_SHA256="$2"; shift 2;;
    --replace-app) REPLACE_APP=1; shift;;
    --allow-root) ALLOW_ROOT=1; shift;;
    -h|--help) usage; exit 0;;
    *) printf 'unknown option: %s (see --help)\n' "$1" >&2; exit 2;;
  esac
done
[ "$PURGE" = 1 ] && [ "$UNINSTALL" = 0 ] && { printf -- '--purge only goes with --uninstall\n' >&2; exit 2; }

# ---------------------------------------------------------------- helpers
if [ -t 1 ]; then B=$'\033[1m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'; else B=""; Y=""; R=""; D=""; N=""; fi
bold() { printf '%s%s%s\n' "$B" "$*" "$N"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '%s! %s%s\n' "$Y" "$*" "$N"; }
die()  { printf '%sx %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
# a missing prerequisite: stop (or, in --dry-run, note it and carry on so the rest can be shown)
need() { if [ "$DRY" = 1 ]; then warn "[dry-run] would stop here: $*"; else die "$*"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }
tty_ok() { { true </dev/tty; } 2>/dev/null; }
ask() {  # ask "question" default -> answer on stdout (reads the terminal even when piped from curl)
  local q="$1" d="$2" a=""
  if [ "$CS_YES" = "1" ] || ! tty_ok; then echo "$d"; return; fi
  printf '%s [%s]: ' "$q" "$d" >/dev/tty
  read -r a </dev/tty || true
  echo "${a:-$d}"
}
yes_no() { case "$(ask "$1 (y/n)" "$2")" in y|Y|yes|YES|Yes) return 0;; *) return 1;; esac; }
quote() { local out="" a; for a in "$@"; do out="$out $(printf '%q' "$a")"; done; printf '%s' "${out# }"; }
run() {  # run a command that changes something (printed instead under --dry-run)
  if [ "$DRY" = 1 ]; then printf '  %s[dry-run] %s%s\n' "$D" "$(quote "$@")" "$N"; else "$@"; fi
}
ver_ge() {  # ver_ge A B: version A >= B (no sort -V; works with macOS' bash 3.2)
  local IFS=. i a b; local -a x y
  read -r -a x <<<"${1%%[!0-9.]*}"; read -r -a y <<<"${2%%[!0-9.]*}"
  for i in 0 1 2 3; do
    a="${x[$i]:-0}"; b="${y[$i]:-0}"
    [ "$((10#$a))" -gt "$((10#$b))" ] && return 0
    [ "$((10#$a))" -lt "$((10#$b))" ] && return 1
  done
  return 0
}
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}'; }
env_get() { if [ -f "$CS_DIR/.env" ]; then { grep -E "^$1=" "$CS_DIR/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2-; fi; }

SUDO=""; [ "$(id -u)" -ne 0 ] && have sudo && SUDO="sudo"
HOST_UID="${SUDO_UID:-$(id -u)}"; HOST_GID="${SUDO_GID:-$(id -g)}"
HOST_USER="${SUDO_USER:-$(id -un)}"
if [ "$HOST_UID" = 0 ] && [ "$UNINSTALL" = 0 ] && [ "$ALLOW_ROOT" = 0 ]; then
  printf '! You are root (not via sudo): data/ would belong to root and the containers would run as root.\n'
  printf '  Better: run this as a normal user in the docker group (sudo is used only where needed).\n'
  if [ "$CS_YES" = 1 ] || ! { true </dev/tty; } 2>/dev/null; then
    [ "$DRY" = 1 ] || { printf 'x add --allow-root to install as root anyway\n' >&2; exit 1; }
  else
    printf 'Continue as root? (y/n) [n]: ' >/dev/tty; read -r a </dev/tty || a=""
    case "$a" in y|Y|yes) ;; *) exit 1;; esac
  fi
fi
as_user() {  # run as the invoking user (matters only when this script itself runs under sudo)
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then sudo -u "$SUDO_USER" -H "$@"; else "$@"; fi
}
ARCH="$(uname -m)"; case "$ARCH" in x86_64|amd64) ARCH=amd64;; arm64|aarch64) ARCH=arm64;; esac
IS_WSL=0; [ "$OS" = Linux ] && grep -qi microsoft /proc/version 2>/dev/null && IS_WSL=1

[ -n "$CS_DIR" ] || CS_DIR="$DEFAULT_DIR"
case "$CS_DIR" in "~"/*) CS_DIR="$HOME/${CS_DIR#\~/}";; /*) ;; *) CS_DIR="$(pwd)/$CS_DIR";; esac

bold "Cyber-Scout installer$([ "$DRY" = 1 ] && echo " (dry run: nothing is changed)")"
info "$( [ "$OS" = Darwin ] && echo "macOS $(sw_vers -productVersion 2>/dev/null || true)" || echo "Linux$([ "$IS_WSL" = 1 ] && echo " (WSL)")" ), $ARCH, user $HOST_USER ($HOST_UID:$HOST_GID)"

# ---------------------------------------------------------------- Docker + compose
DOCKER_OK=0; DOCKER=(docker); DOCKER_SUDO=0; CV=""
dk() { "${DOCKER[@]}" "$@"; }
docker_check() {
  have docker || return 1
  if docker info >/dev/null 2>&1; then DOCKER=(docker); DOCKER_SUDO=0; DOCKER_OK=1; return 0; fi
  if [ "$OS" = Linux ] && [ -n "$SUDO" ] && $SUDO -n docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker); DOCKER_SUDO=1; DOCKER_OK=1; return 0
  fi
  if [ "$OS" = Linux ] && [ -n "$SUDO" ] && tty_ok && [ "$CS_YES" != 1 ]; then
    info "Your user can't reach Docker directly (not in the 'docker' group), so Docker commands will use sudo."
    # shellcheck disable=SC2024  # stdin from the terminal is for sudo's password prompt
    if sudo docker info >/dev/null 2>&1 </dev/tty; then DOCKER=(sudo docker); DOCKER_SUDO=1; DOCKER_OK=1; return 0; fi
  fi
  return 2
}
set +e; docker_check; drc=$?; set -e
if [ "$drc" = 1 ]; then
  if [ "$OS" = Darwin ]; then
    warn "Docker isn't installed. On a Mac, Cyber-Scout runs in Docker Desktop:"
    info "1. Install Docker Desktop: https://docs.docker.com/desktop/setup/install/mac-install/  (or: brew install --cask docker)"
    info "2. Start it once (Applications -> Docker) and accept the terms."
    info "3. Settings -> Resources: give it at least 8 GB memory and 40 GB disk."
    [ "$ARCH" = arm64 ] && info "4. Settings -> General: turn on 'Use Rosetta for x86_64/amd64 emulation on Apple Silicon'."
    info "Then run this installer again. (OrbStack or Colima work too if 'docker compose' does.)"
    need "Docker Desktop is required"
  elif [ "$UNINSTALL" = 1 ]; then
    need "Docker isn't installed, so there are no containers to remove"
  else
    warn "Docker isn't installed."
    info "Cyber-Scout needs Docker Engine + the Compose plugin: https://docs.docker.com/engine/install/"
    if [ "$INSTALL_DOCKER" = 1 ] || { [ "$CS_YES" != 1 ] && tty_ok && \
         yes_no "Install Docker Engine now with Docker's official script (https://get.docker.com)? It runs as root via sudo" n; }; then
      [ "$(id -u)" -eq 0 ] || [ -n "$SUDO" ] || die "installing Docker needs root (sudo isn't available)"
      info "Installing Docker with sudo: installing system packages and the docker service needs root."
      tmp="$(mktemp)"; run curl -fsSL https://get.docker.com -o "$tmp"
      run $SUDO sh "$tmp"; rm -f "$tmp"
      run $SUDO systemctl enable --now docker || true
      if [ "$HOST_USER" != root ]; then
        run $SUDO usermod -aG docker "$HOST_USER" || true
        info "Added $HOST_USER to the 'docker' group (takes effect at your next login; until then sudo is used)."
      fi
      if [ "$DRY" = 0 ]; then set +e; docker_check; drc=$?; set -e; fi
    else
      info "Install it (e.g. curl -fsSL https://get.docker.com | sudo sh), or re-run with --install-docker."
      need "Docker is required"
    fi
  fi
fi
if [ "$DOCKER_OK" != 1 ] && have docker; then
  if [ "$OS" = Darwin ]; then
    warn "Docker Desktop isn't running."
    if [ "$DRY" = 0 ] && have open && yes_no "Start Docker Desktop now?" y; then
      open -a Docker || true
      info "waiting for Docker Desktop to start (up to 2 minutes)"
      for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && { DOCKER_OK=1; break; }; sleep 2; done
    fi
    [ "$DOCKER_OK" = 1 ] || need "start Docker Desktop (Applications -> Docker), wait until it says 'running', and re-run"
  else
    need "Docker is installed but not reachable. Is the daemon running (sudo systemctl start docker)? Is your user in the 'docker' group (sudo usermod -aG docker $HOST_USER, then log in again)?"
  fi
fi
if [ "$DOCKER_OK" = 1 ]; then
  dk compose version >/dev/null 2>&1 || need "Docker Compose v2 (the 'docker compose' plugin) is missing: https://docs.docker.com/compose/install/linux/ (Debian/Ubuntu: sudo apt install docker-compose-plugin)"
  CV="$(dk compose version --short 2>/dev/null | sed 's/^v//' || true)"
  info "Docker $(dk version --format '{{.Server.Version}}' 2>/dev/null || echo '?'), Compose ${CV:-?}$([ "$DOCKER_SUDO" = 1 ] && echo ' (via sudo)')"
  [ "$(dk info --format '{{.OSType}}' 2>/dev/null || echo linux)" = linux ] || need "Docker is in Windows-containers mode; switch it to Linux containers"
fi

dc() {  # docker compose in the stack folder (GIT_SHA labels the build with the code version)
  local e=(env); [ -n "${GIT_SHA:-}" ] && e+=("GIT_SHA=$GIT_SHA")
  (
    cd "$CS_DIR" 2>/dev/null || [ "$DRY" = 1 ] || die "no folder $CS_DIR"
    if [ "$DOCKER_SUDO" = 1 ]; then run sudo "${e[@]}" docker compose "$@"; else run "${e[@]}" docker compose "$@"; fi
  )
}

# ================================================================ uninstall
if [ "$UNINSTALL" = 1 ]; then
  [ -n "$CS_PREFIX" ] || CS_PREFIX="$(env_get CS_PREFIX)"; CS_PREFIX="${CS_PREFIX:-cyber-scout}"
  [ -f "$CS_DIR/docker-compose.yml" ] || [ "$DRY" = 1 ] || die "$CS_DIR doesn't look like a Cyber-Scout install (no docker-compose.yml). Use --dir DIR."
  bold "Removing Cyber-Scout ($CS_PREFIX) from $CS_DIR"
  # host updater (systemd units) first, so it can't restart anything
  UPD="$CS_DIR/app/deploy/updater/install-updater.sh"
  if [ -f "/etc/systemd/system/$CS_PREFIX-updater.service" ]; then
    info "removing the updater's systemd units (system units: needs sudo)"
    run $SUDO bash "$UPD" --stack "$CS_DIR" --uninstall --yes || warn "couldn't remove the updater; later: sudo $UPD --uninstall"
  elif [ -f "$HOME/.config/systemd/user/$CS_PREFIX-updater.service" ]; then
    run bash "$UPD" --stack "$CS_DIR" --user --uninstall --yes || warn "couldn't remove the updater; later: $UPD --user --uninstall"
  fi
  if [ "$DOCKER_OK" = 1 ] && [ -f "$CS_DIR/docker-compose.yml" ]; then
    if [ "$KEEP_IMAGES" = 1 ]; then dc down --remove-orphans; else dc down --remove-orphans --rmi all || dc down --remove-orphans; fi
    if [ "$KEEP_IMAGES" = 0 ]; then  # also the updater's rollback tags etc. of exactly this prefix
      for img in $(dk image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E "^${CS_PREFIX}-(worker|api|web):" || true); do
        run "${DOCKER[@]}" rmi "$img" >/dev/null 2>&1 || true
      done
    fi
  fi
  if [ "$PURGE" = 1 ]; then
    md="$(env_get MEDIA_DIR)"
    warn "--purge deletes $CS_DIR completely: accounts, settings, scouting notes, processed matches and kept videos."
    if [ "$CS_YES" = 1 ]; then info "(--yes given: not asking)"
    else
      tty_ok || die "--purge needs a terminal to confirm (or add --yes)"
      [ "$(ask "Type DELETE to confirm" "")" = DELETE ] || die "not confirmed; nothing deleted (containers are already removed)"
    fi
    if [ "$DRY" = 1 ]; then run rm -rf "$CS_DIR"
    elif ! rm -rf "$CS_DIR" 2>/dev/null; then
      # files written inside containers can belong to another uid: delete those through Docker (no sudo needed)
      if [ "$DOCKER_OK" = 1 ] && dk image inspect python:3.12-slim >/dev/null 2>&1; then
        dk run --rm -v "$CS_DIR:/stack" python:3.12-slim sh -c 'rm -rf /stack/* /stack/.[!.]* 2>/dev/null; true'
      fi
      rm -rf "$CS_DIR" 2>/dev/null || { info "some files need root to delete (sudo)"; $SUDO rm -rf "$CS_DIR"; }
    fi
    case "$md" in ""|./*|"$CS_DIR"/*) ;; *) info "MEDIA_DIR was $md (outside the folder): left alone, delete it yourself if you want.";; esac
    bold "Removed."
  else
    bold "Removed the containers$([ "$KEEP_IMAGES" = 0 ] && echo " and images"). Kept $CS_DIR (app/, data/, .env)."
    info "Reinstall: run this installer again.   Delete everything: --uninstall --purge"
  fi
  return 0
fi

# ================================================================ install / update
EXISTING=0; [ -f "$CS_DIR/.env" ] && EXISTING=1
if [ "$EXISTING" = 0 ]; then
  CS_DIR="$(ask "Install folder" "$CS_DIR")"
  case "$CS_DIR" in "~"/*) CS_DIR="$HOME/${CS_DIR#\~/}";; /*) ;; *) CS_DIR="$(pwd)/$CS_DIR";; esac
  [ -f "$CS_DIR/.env" ] && EXISTING=1
fi
[ "$EXISTING" = 1 ] && info "existing install in $CS_DIR: updating it (data/ and .env are kept)"
# what this run sets explicitly (everything else comes from an existing .env or the defaults)
SET_PORT="$CS_PORT"; SET_BIND="$CS_BIND"; SET_PREFIX="$CS_PREFIX"; SET_GPU_IDS="$CS_GPU_IDS"
[ -n "$CS_PREFIX" ] || CS_PREFIX="$(env_get CS_PREFIX)"; CS_PREFIX="${CS_PREFIX:-cyber-scout}"
[[ "$CS_PREFIX" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "--prefix: lowercase letters, digits and - only ($CS_PREFIX)"
[ -n "$CS_BIND" ] || CS_BIND="$(env_get WEB_BIND)"; CS_BIND="${CS_BIND:-0.0.0.0}"
[ -n "$CS_PORT" ] || CS_PORT="$(env_get WEB_PORT)"; CS_PORT="${CS_PORT:-7560}"
case "$CS_PORT" in ''|*[!0-9]*) die "bad port: $CS_PORT";; esac

# Never take over another stack: compose project names are global, so the same prefix from another folder would
# recreate *those* containers.
SAME_STACK=0
if [ "$DOCKER_OK" = 1 ]; then
  OTHER="$(dk ps -a --filter "label=com.docker.compose.project=$CS_PREFIX" \
            --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | sort -u | head -1 || true)"
  if [ -n "$OTHER" ]; then
    if [ "$(cd "$OTHER" 2>/dev/null && pwd -P)" = "$(cd "$CS_DIR" 2>/dev/null && pwd -P)" ]; then SAME_STACK=1
    else need "A Cyber-Scout stack named '$CS_PREFIX' already runs from $OTHER. Update that one (--dir $OTHER), or install a second copy next to it with --prefix NAME (and another --port)."; fi
  fi
fi

SERVICES=""
if [ -n "$CS_SERVICES" ]; then
  for s in $(echo "$CS_SERVICES" | tr ',' ' '); do
    case "$s" in worker|recorder|api|web) SERVICES="$SERVICES $s";; *) die "--services: unknown service '$s' (worker, recorder, api, web)";; esac
  done
  SERVICES="${SERVICES# }"
  case " $SERVICES " in *" web "*) case " $SERVICES " in *" api "*) ;; *) SERVICES="$SERVICES api";; esac;; esac
fi
WITH_WORKER=1
if [ -n "$SERVICES" ]; then case " $SERVICES " in *" worker "*|*" recorder "*) ;; *) WITH_WORKER=0;; esac; fi

# ---------------------------------------------------------------- GPU
GPU=0; GPU_COUNT=0; GPU_TESTED=""
gpu_test() { dk run --rm --gpus "\"device=$1\"" python:3.12-slim nvidia-smi -L >/dev/null 2>&1; }
if [ "$OS" = Darwin ]; then
  info "macOS: CPU mode (Docker Desktop on a Mac has no CUDA). Each match takes ~4x longer than on an NVIDIA GPU."
elif [ "$MODE_CPU" = cpu ]; then
  info "CPU mode (asked for)"
elif [ "$MODE_CPU" = "" ] && [ "$EXISTING" = 1 ] && grep -q '^COMPOSE_FILE=.*docker-compose.cpu.yml' "$CS_DIR/.env" 2>/dev/null; then
  info "CPU mode (from .env; re-run with --gpu to switch to the GPU)"
elif have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then
  GPU_COUNT="$(nvidia-smi -L | grep -c '^GPU' || true)"
  nvidia-smi -L | sed 's/ (UUID.*//; s/^/  found /'
  toolkit=0
  # shellcheck disable=SC2016  # a Go template, not shell
  if [ "$DOCKER_OK" = 1 ] && dk info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null | grep -q nvidia; then toolkit=1; fi
  { have nvidia-ctk || have nvidia-container-cli; } && toolkit=1
  if [ "$toolkit" = 1 ]; then
    if [ "$DRY" = 1 ] || [ "$DOCKER_OK" != 1 ]; then GPU=1
    else
      # the card(s) the worker will get, asked for the way compose does (device ids, not "all": on some hosts, e.g. a
      # hardened kernel with one card busy, --gpus all fails while each card works)
      T_IDS="$CS_GPU_IDS"; [ -n "$T_IDS" ] || [ "$EXISTING" != 1 ] || T_IDS="$(env_get GPU_IDS)"
      [[ "${T_IDS:-}" =~ ^[0-9]+(,[0-9]+)*$ ]] || T_IDS=0
      info "checking that containers can use GPU $T_IDS (docker run --gpus device=$T_IDS ... nvidia-smi)"
      if gpu_test "$T_IDS"; then GPU=1; GPU_TESTED="$T_IDS"; info "GPU works in containers"
      else warn "The NVIDIA Container Toolkit is installed but a test container couldn't use GPU $T_IDS."
           warn "Try: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"; fi
    fi
  else
    warn "An NVIDIA GPU is present but the NVIDIA Container Toolkit isn't installed, so containers can't use it."
    info "Install it for ~4x faster processing (needs root; then re-run this installer):"
    info "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
  fi
  if [ "$GPU" = 0 ]; then
    if [ "$MODE_CPU" = gpu ]; then need "--gpu was given but containers can't use the GPU"; fi
    yes_no "Continue in CPU-only mode for now?" y || die "Install the NVIDIA Container Toolkit, then re-run."
  fi
else
  [ "$MODE_CPU" = gpu ] && need "--gpu was given but nvidia-smi found no NVIDIA GPU / driver"
  info "No NVIDIA GPU found: CPU mode. Each match takes ~4x longer (~20 min instead of ~3-6). Everything else works."
fi
if [ "$GPU" = 1 ]; then
  if [ -z "$CS_GPU_IDS" ] && [ "$EXISTING" = 1 ]; then CS_GPU_IDS="$(env_get GPU_IDS)"; fi
  if [ -z "$CS_GPU_IDS" ] && [ "$GPU_COUNT" -gt 1 ]; then CS_GPU_IDS="$(ask "Which GPU(s) should the worker use (0, 1, 0,1 ...)" 0)"; SET_GPU_IDS="$CS_GPU_IDS"; fi
  CS_GPU_IDS="${CS_GPU_IDS:-0}"
  [[ "$CS_GPU_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]] || die "--gpu-ids: like 0 or 0,1 ($CS_GPU_IDS)"
  if [ -n "${GPU_TESTED:-}" ] && [ "$CS_GPU_IDS" != "$GPU_TESTED" ] && ! gpu_test "$CS_GPU_IDS"; then
    warn "GPU $CS_GPU_IDS didn't work in a test container (GPU $GPU_TESTED did): the worker may not start. Re-run with --gpu-ids $GPU_TESTED"
  fi
  info "GPU mode, GPU_IDS=$CS_GPU_IDS"
elif [ "$DOCKER_OK" = 1 ] && [ -n "$CV" ] && ! ver_ge "$CV" 2.24.4; then
  need "CPU-only mode needs Docker Compose >= 2.24.4 (you have $CV). Update Docker."
fi
PLATFORM=""
if [ "$ARCH" = arm64 ] && [ "$WITH_WORKER" = 1 ]; then
  PLATFORM="linux/amd64"
  info "ARM CPU: the worker's libraries (pupil-apriltags) only ship for x86-64, so the images are built for"
  info "linux/amd64 and run emulated (DOCKER_DEFAULT_PLATFORM in .env)."
  [ "$OS" = Darwin ] && info "Docker Desktop -> Settings -> General -> 'Use Rosetta for x86_64/amd64 emulation' makes this much faster."
  [ "$OS" = Linux ] && info "ARM Linux needs amd64 emulation: docker run --privileged --rm tonistiigi/binfmt --install amd64"
fi

# ---------------------------------------------------------------- disk + memory
NEED_GB=20; [ "$WITH_WORKER" = 0 ] || [ -n "$WORKER_IMAGE" ] || [ "$USE_IMAGES" = 1 ] && NEED_GB=2
if [ "$DOCKER_OK" = 1 ]; then
  FREE=""
  if [ "$OS" = Linux ]; then
    DROOT="$(dk info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
    FREE="$(free_gb "$DROOT" || true)"; [ -n "$FREE" ] || FREE="$(free_gb "$(dirname "$DROOT")" || true)"
    WHERE="$DROOT"
  elif [ "$DRY" = 0 ]; then  # Docker Desktop keeps images inside its VM disk: ask a container
    FREE="$(dk run --rm python:3.12-slim df -Pk / 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}' || true)"
    WHERE="Docker Desktop's disk (Settings -> Resources)"
  fi
  if [ -n "$FREE" ]; then
    info "free space for Docker images: ${FREE} GB ($WHERE)"
    if [ "$FREE" -lt "$NEED_GB" ]; then
      warn "Less than $NEED_GB GB free for Docker images ($WHERE). The worker image alone is ~3.5 GB and the"
      warn "build needs room on top; a full disk breaks the build and other containers."
      yes_no "Continue anyway?" y || die "Free some space (docker image ls / docker image rm <unused>), then re-run."
    fi
  fi
  MEM="$(dk info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
  if [ "${MEM:-0}" -gt 0 ] && [ "$MEM" -lt 7500000000 ] && [ "$WITH_WORKER" = 1 ]; then
    warn "Docker has only $((MEM / 1073741824)) GB of memory; the worker wants ~8 GB.$([ "$OS" = Darwin ] && echo " Raise it in Docker Desktop -> Settings -> Resources.")"
  fi
fi

# ---------------------------------------------------------------- port
DOCKER_PORTS=""; [ "$DOCKER_OK" = 1 ] && DOCKER_PORTS="$(dk ps --format '{{.Ports}}' 2>/dev/null || true)"
port_busy() {  # 0 = something listens on port $1 (or a container publishes it)
  local p="$1"
  case "$DOCKER_PORTS" in *":$p->"*) return 0;; esac
  if have ss; then ss -Hltn "sport = :$p" 2>/dev/null | grep -q . && return 0
  elif have lsof; then lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1 && return 0; fi
  (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && return 0
  return 1
}
our_port() {  # our own web container already publishes it (a re-run)
  [ "$SAME_STACK" = 1 ] && dk port "$CS_PREFIX-web" 80 2>/dev/null | grep -q ":$1\$"
}
if [ -z "$SET_PORT" ] && [ "$EXISTING" = 0 ]; then CS_PORT="$(ask "Web port" "$CS_PORT")"; SET_PORT="$CS_PORT"; fi
case "$CS_PORT" in ''|*[!0-9]*) die "bad port: $CS_PORT";; esac
if port_busy "$CS_PORT" && ! our_port "$CS_PORT"; then
  alt="$CS_PORT"; while [ "$alt" -lt $((CS_PORT + 100)) ]; do alt=$((alt + 1)); port_busy "$alt" || break; done
  warn "Port $CS_PORT is already in use on this machine."
  CS_PORT="$(ask "Use port" "$alt")"; SET_PORT="$CS_PORT"
  case "$CS_PORT" in ''|*[!0-9]*) die "bad port: $CS_PORT";; esac
  port_busy "$CS_PORT" && ! our_port "$CS_PORT" && need "port $CS_PORT is in use too (--port N)"
fi
info "web port $CS_PORT on $CS_BIND"

# ---------------------------------------------------------------- folder
if [ ! -d "$CS_DIR" ]; then
  if [ "$DRY" = 1 ]; then run mkdir -p "$CS_DIR"
  elif mkdir -p "$CS_DIR" 2>/dev/null; then :
  else
    [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ] || die "can't create $CS_DIR (no permission, no sudo). Use --dir somewhere you can write."
    info "Creating $CS_DIR needs root (sudo); it's then handed to $HOST_USER so nothing else needs root."
    run $SUDO mkdir -p "$CS_DIR"
    run $SUDO chown "$HOST_UID:$HOST_GID" "$CS_DIR"
  fi
fi
if [ -d "$CS_DIR" ] && [ ! -w "$CS_DIR" ]; then
  info "$CS_DIR isn't writable by $HOST_USER: taking ownership with sudo"
  run $SUDO chown "$HOST_UID:$HOST_GID" "$CS_DIR"
fi
if [ "$OS" = Darwin ]; then
  case "$CS_DIR" in /Users/*|/Volumes/*|/private/*|/tmp/*) ;;
    *) warn "Docker Desktop only shares /Users, /Volumes, /private and /tmp by default; add $CS_DIR in Settings -> Resources -> File sharing.";; esac
fi
bold "Installing into $CS_DIR"

# ---------------------------------------------------------------- code
GIT_SHA=""
gh_slug() {  # owner/repo of a GitHub URL (https or ssh), else empty
  echo "$1" | sed -nE 's#^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+)$#\2#p' | sed 's/\.git$//'
}
have_ssh_key() { ls "$HOME"/.ssh/id_* >/dev/null 2>&1 || { have ssh-add && ssh-add -l >/dev/null 2>&1; }; }
replace_app() {  # replace_app NEWDIR: swap in a fresh code tree (old one kept as app.prev until the stack is up)
  run rm -rf "$CS_DIR/app.prev"
  [ -e "$CS_DIR/app" ] && run mv "$CS_DIR/app" "$CS_DIR/app.prev"
  run mv "$1" "$CS_DIR/app"
}
from_tarball() {  # from_tarball PATH|URL
  local src="$1" tmp f top
  tmp="$(mktemp -d "$CS_DIR/.src.XXXXXX" 2>/dev/null || mktemp -d)"
  if [[ "$src" =~ ^https?:// ]]; then
    info "downloading $src"
    f="$tmp/src.tar.gz"
    if [ "$DRY" = 1 ]; then run curl -fsSL --retry 3 -o "$f" "$src"; rm -rf "$tmp"; return 0; fi
    curl -fsSL --retry 3 -o "$f" "$src" || { rm -rf "$tmp"; die "couldn't download $src"; }
  else
    f="$src"; [ -f "$f" ] || { rm -rf "$tmp"; need "no such tarball: $f"; return 0; }
  fi
  info "unpacking $(basename "$f")"
  if [ "$DRY" = 1 ]; then run tar -xzf "$f" -C "$tmp/x"; rm -rf "$tmp"; return 0; fi
  mkdir -p "$tmp/x"; tar -xzf "$f" -C "$tmp/x" || { rm -rf "$tmp"; die "couldn't unpack $f"; }
  # GitHub tarballs (and most others) have one top-level folder
  top="$tmp/x"; [ "$(find "$tmp/x" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" = 1 ] && [ -d "$(find "$tmp/x" -mindepth 1 -maxdepth 1)" ] && top="$(find "$tmp/x" -mindepth 1 -maxdepth 1)"
  [ -f "$top/deploy/docker-compose.yml" ] || { rm -rf "$tmp"; die "$f doesn't contain Cyber-Scout (no deploy/docker-compose.yml)"; }
  mv "$top" "$tmp/app"; replace_app "$tmp/app"; rm -rf "$tmp"
}
git_clone() {  # git_clone URL DEST
  GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    as_user git -c core.autocrlf=false clone --quiet --depth 1 --branch "$CS_BRANCH" "$1" "$2"
}
if [ -n "$CS_SOURCE" ]; then
  [ -d "$CS_SOURCE" ] || need "--source: no such folder: $CS_SOURCE"
  info "copying the code from $CS_SOURCE"
  run mkdir -p "$CS_DIR/app"
  if [ "$DRY" = 1 ]; then run tar -C "$CS_SOURCE" -cf - . "| tar -C $CS_DIR/app -xf -"
  else
    tar -C "$CS_SOURCE" --exclude=.git --exclude=.venv --exclude=.work --exclude=./data --exclude=node_modules \
        --exclude='__pycache__' --exclude=.env --exclude='*.key' --exclude=.roboflow_key -cf - . | tar -C "$CS_DIR/app" -xf -
  fi
  [ -d "$CS_SOURCE/.git" ] && GIT_SHA="$(git -C "$CS_SOURCE" rev-parse --short HEAD 2>/dev/null || true)"
elif [ -n "$CS_TARBALL" ]; then
  from_tarball "$CS_TARBALL"
elif [ -d "$CS_DIR/app/.git" ] && have git; then
  info "updating the code (git fetch, branch $CS_BRANCH)"
  [ -z "$(as_user git -C "$CS_DIR/app" status --porcelain --untracked-files=no 2>/dev/null)" ] \
    || need "$CS_DIR/app has local changes that an update would overwrite (git -C $CS_DIR/app status); commit/stash them or move them away"
  export GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
  run as_user git -C "$CS_DIR/app" fetch --quiet origin "$CS_BRANCH" \
    || need "git fetch failed (network? access to the repo?). The installed version keeps running."
  run as_user git -C "$CS_DIR/app" checkout --quiet -B "$CS_BRANCH" FETCH_HEAD
else
  slug="$(gh_slug "$CS_REPO")"
  if [ -e "$CS_DIR/app" ]; then
    warn "$CS_DIR/app exists but isn't a git checkout (copied by hand?), so it can't be updated in place."
    if [ "$REPLACE_APP" = 1 ] || { [ "$CS_YES" != 1 ] && yes_no "Replace it with a fresh copy from $CS_REPO (the old one is kept as app.bak-<time>)?" n; }; then
      run mv "$CS_DIR/app" "$CS_DIR/app.bak-$(date +%Y%m%d-%H%M%S)"
    else need "Left $CS_DIR/app alone. Re-run with --replace-app, or use --source DIR / --tarball to install that code."; fi
  fi
  if have git; then
    info "downloading the code: git clone $CS_REPO ($CS_BRANCH)"
    tmp="$CS_DIR/.app.new"
    if [ "$DRY" = 1 ]; then run git clone --depth 1 --branch "$CS_BRANCH" "$CS_REPO" "$CS_DIR/app"
    else
      rm -rf "$tmp"
      if ! git_clone "$CS_REPO" "$tmp" 2>/dev/null; then
        rm -rf "$tmp"
        if [ -n "$slug" ] && [[ "$CS_REPO" =~ ^https:// ]] && have_ssh_key; then
          info "https clone failed (private repo?); trying SSH with your key: git@github.com:$slug.git"
          git_clone "git@github.com:$slug.git" "$tmp" || { rm -rf "$tmp"; die "couldn't clone $CS_REPO over https or SSH. Check the URL/branch and your GitHub access (ssh -T git@github.com)."; }
        else
          die "couldn't clone $CS_REPO ($CS_BRANCH). Private repo? Add an SSH key to GitHub, or use --repo git@github.com:OWNER/REPO.git, or --tarball."
        fi
      fi
      as_user git -C "$tmp" config core.autocrlf false
      replace_app "$tmp"
    fi
  elif [ -n "$slug" ]; then
    info "git isn't installed: downloading a tarball of $slug ($CS_BRANCH) instead (updates work the same way)"
    from_tarball "https://codeload.github.com/$slug/tar.gz/refs/heads/$CS_BRANCH"
  else
    need "git is required for $CS_REPO (Debian/Ubuntu: sudo apt install git; macOS: xcode-select --install)"
  fi
fi
if [ -z "$GIT_SHA" ] && [ -d "$CS_DIR/app/.git" ] && [ "$DRY" = 0 ]; then
  GIT_SHA="$(git -C "$CS_DIR/app" rev-parse --short HEAD 2>/dev/null || true)"
fi
[ -n "$GIT_SHA" ] && info "code version $GIT_SHA"
if [ "$DRY" = 0 ] && [ ! -f "$CS_DIR/app/deploy/docker-compose.yml" ]; then die "$CS_DIR/app has no deploy/docker-compose.yml"; fi
for f in docker-compose.yml docker-compose.cpu.yml; do
  if [ "$DRY" = 1 ]; then run cp "$CS_DIR/app/deploy/$f" "$CS_DIR/$f"; continue; fi
  if [ -f "$CS_DIR/$f" ] && ! cmp -s "$CS_DIR/app/deploy/$f" "$CS_DIR/$f"; then cp "$CS_DIR/$f" "$CS_DIR/$f.bak"; info "previous $f saved as $f.bak"; fi
  cp "$CS_DIR/app/deploy/$f" "$CS_DIR/$f"
done

# ---------------------------------------------------------------- .env (infrastructure only; app settings live in the app)
# No secrets are needed here: the api creates the one-time setup code itself and stores everything else in data/.
CPU_FILES="docker-compose.yml:docker-compose.cpu.yml"
detect_tz() {
  local t=""
  t="$(cat /etc/timezone 2>/dev/null || true)"
  [ -n "$t" ] || t="$( (timedatectl show -p Timezone --value 2>/dev/null) || true)"
  [ -n "$t" ] || t="$(readlink /etc/localtime 2>/dev/null | sed -n 's#.*zoneinfo/##p' || true)"
  echo "${t:-UTC}"
}
set_env() {  # set_env KEY VALUE: replace or append one line, keep everything else
  local k="$1" v="$2" f="$CS_DIR/.env"
  if [ "$DRY" = 1 ]; then printf '  %s[dry-run] .env: %s=%s%s\n' "$D" "$k" "$v" "$N"; return; fi
  if grep -qE "^$k=" "$f"; then
    awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$f" > "$f.tmp" && cat "$f.tmp" > "$f" && rm -f "$f.tmp"
  else echo "$k=$v" >> "$f"; fi
}
del_env() { [ "$DRY" = 1 ] && { printf '  %s[dry-run] .env: remove %s%s\n' "$D" "$1" "$N"; return; }
            grep -vE "^$1=" "$CS_DIR/.env" > "$CS_DIR/.env.tmp" || true; cat "$CS_DIR/.env.tmp" > "$CS_DIR/.env"; rm -f "$CS_DIR/.env.tmp"; }
if [ "$EXISTING" = 0 ]; then
  info "writing .env"
  envtext="$(
    echo "# Cyber-Scout infrastructure settings (written by install.sh; re-running it keeps your edits)."
    echo "# App settings (TBA key, team, events, GPU options, accounts) live in the web app. See env.example."
    echo "CS_PREFIX=$CS_PREFIX"
    echo "WEB_PORT=$CS_PORT"
    echo "# publish the web port on this address only (e.g. a LAN or Tailscale IP); 0.0.0.0 = all"
    echo "WEB_BIND=$CS_BIND"
    echo "# files in ./data are owned by this host user"
    echo "PUID=$HOST_UID"
    echo "PGID=$HOST_GID"
    echo "TZ=$(detect_tz)"
    echo "MEDIA_DIR=./data/media"
    echo "YT_FORCE_IPV4=1"
    if [ "$GPU" = 1 ]; then echo "GPU_IDS=$CS_GPU_IDS"
    else echo "# no usable NVIDIA GPU: CPU-only override"; echo "COMPOSE_FILE=$CPU_FILES"; fi
    [ -n "$PLATFORM" ] && echo "DOCKER_DEFAULT_PLATFORM=$PLATFORM"
    true
  )"
  if [ "$DRY" = 1 ]; then printf '%s\n' "$envtext" | sed "s/^/  ${D}[dry-run] .env: /;s/\$/${N}/"
  else (umask 077; printf '%s\n' "$envtext" > "$CS_DIR/.env"); fi
else
  info "keeping .env (updating only what this run changes)"
  [ -n "$SET_PORT" ] && [ "$(env_get WEB_PORT)" != "$CS_PORT" ] && set_env WEB_PORT "$CS_PORT"
  [ -n "$SET_BIND" ] && [ "$(env_get WEB_BIND)" != "$CS_BIND" ] && set_env WEB_BIND "$CS_BIND"
  [ -n "$SET_PREFIX" ] && [ "$(env_get CS_PREFIX)" != "$CS_PREFIX" ] && set_env CS_PREFIX "$CS_PREFIX"
  if [ "$GPU" = 1 ]; then
    { [ -n "$SET_GPU_IDS" ] || [ -z "$(env_get GPU_IDS)" ]; } && [ "$(env_get GPU_IDS)" != "$CS_GPU_IDS" ] && set_env GPU_IDS "$CS_GPU_IDS"
    if env_get COMPOSE_FILE | grep -q docker-compose.cpu.yml; then
      if [ "$MODE_CPU" = gpu ]; then del_env COMPOSE_FILE; info "switched .env to GPU mode"
      else info "GPU available, but .env is CPU-only; re-run with --gpu to switch"; fi
    fi
  elif ! env_get COMPOSE_FILE | grep -q docker-compose.cpu.yml; then
    set_env COMPOSE_FILE "$CPU_FILES"; info "added the CPU-only override to .env"
  fi
  [ -n "$PLATFORM" ] && [ -z "$(env_get DOCKER_DEFAULT_PLATFORM)" ] && set_env DOCKER_DEFAULT_PLATFORM "$PLATFORM"
  true
fi
[ "$DRY" = 1 ] || chmod 600 "$CS_DIR/.env"

# ---------------------------------------------------------------- data folder + robot detector
run mkdir -p "$CS_DIR/data/models" "$CS_DIR/data/media" "$CS_DIR/data/site/api" "$CS_DIR/data/config"
if [ "$(id -u)" -eq 0 ] && [ "$HOST_UID" != 0 ]; then  # running under sudo: hand the folder to the invoking user
  run chown "$HOST_UID:$HOST_GID" "$CS_DIR" "$CS_DIR/.env" "$CS_DIR"/docker-compose*.yml "$CS_DIR/data" \
      "$CS_DIR/data/models" "$CS_DIR/data/media" "$CS_DIR/data/site" "$CS_DIR/data/site/api" "$CS_DIR/data/config"
  run chown -R "$HOST_UID:$HOST_GID" "$CS_DIR/app"
fi
MODEL="$CS_DIR/data/models/robot.onnx"
if [ ! -s "$MODEL" ]; then
  if [ -n "$CS_MODEL_URL" ]; then
    got=0
    if [[ "$CS_MODEL_URL" =~ ^https?:// ]]; then
      info "downloading the robot detector"
      if run curl -fsSL --retry 3 -o "$MODEL.part" "$CS_MODEL_URL"; then got=1
      else warn "couldn't download the detector from $CS_MODEL_URL"; fi
    elif [ -f "$CS_MODEL_URL" ] || [ "$DRY" = 1 ]; then
      info "copying the robot detector from $CS_MODEL_URL"; run cp "$CS_MODEL_URL" "$MODEL.part" && got=1
    else warn "--model: no such file: $CS_MODEL_URL"; fi
    if [ "$got" = 1 ] && [ -n "$CS_MODEL_SHA256" ] && [ "$DRY" = 0 ]; then
      sum="$( (sha256sum "$MODEL.part" 2>/dev/null || shasum -a 256 "$MODEL.part") | awk '{print $1}')"
      if [ "$(echo "$sum" | tr 'A-F' 'a-f')" != "$(echo "$CS_MODEL_SHA256" | tr 'A-F' 'a-f')" ]; then
        got=0; warn "the detector's SHA-256 ($sum) doesn't match --model-sha256: not using it"
      else info "SHA-256 checked"; fi
    fi
    if [ "$got" = 1 ]; then run mv "$MODEL.part" "$MODEL"; else rm -f "$MODEL.part"; fi
  fi
  if [ ! -s "$MODEL" ] && { [ "$DRY" = 0 ] || [ -z "$CS_MODEL_URL" ]; }; then
    warn "No robot detector yet ($MODEL)."
    info "The site, setup and accounts work without it, but matches won't be processed until it's there."
    info "Copy robot.onnx from another Cyber-Scout server (data/models/robot.onnx) or ask your team for it, then"
    info "  cp robot.onnx $MODEL     (or re-run with --model PATH|URL; no restart needed)"
  fi
else
  info "robot detector: $MODEL"
fi

# ---------------------------------------------------------------- build + start
BUILD=""
ALL="worker recorder api web"
for s in ${SERVICES:-$ALL}; do
  [ "$USE_IMAGES" = 1 ] && continue
  case "$s" in worker|recorder) [ -n "$WORKER_IMAGE" ] && continue;; esac
  BUILD="$BUILD $s"
done
if [ -n "$WORKER_IMAGE" ] && [ "$WITH_WORKER" = 1 ]; then
  [ "$DOCKER_OK" != 1 ] || dk image inspect "$WORKER_IMAGE" >/dev/null 2>&1 || [ "$DRY" = 1 ] || die "--worker-image: no image $WORKER_IMAGE"
  if [ "$WORKER_IMAGE" != "$CS_PREFIX-worker:latest" ]; then
    info "using the existing worker image $WORKER_IMAGE (no worker build)"
    run "${DOCKER[@]}" tag "$WORKER_IMAGE" "$CS_PREFIX-worker:latest"
  fi
fi
if [ "$USE_IMAGES" = 1 ] && [ "$DOCKER_OK" = 1 ] && [ "$DRY" = 0 ]; then
  for s in ${SERVICES:-$ALL}; do
    img="$CS_PREFIX-$s:latest"; [ "$s" = recorder ] && img="$CS_PREFIX-worker:latest"
    dk image inspect "$img" >/dev/null 2>&1 || die "--use-images: image $img doesn't exist (build it: drop --use-images)"
  done
fi
if [ "$WITH_WORKER" = 0 ]; then
  info "starting only: $SERVICES (no worker: matches aren't processed; a plain 'docker compose up -d' starts everything)"
fi
# shellcheck disable=SC2086  # word splitting of the service lists is intended
if [ -n "$BUILD" ]; then
  bold "Building$([ -n "$SERVICES" ] && echo "$BUILD") (the first build takes a few minutes$([ "$WITH_WORKER" = 1 ] && [ -z "$WORKER_IMAGE" ] && echo "; the worker image is ~3.5 GB"))"
  if [ "$BUILD" = " worker recorder api web" ]; then dc build; else dc build $BUILD; fi
fi
bold "Starting"
# shellcheck disable=SC2086
dc up -d --no-build $SERVICES

HOST="$CS_BIND"; case "$HOST" in ""|0.0.0.0|"::"|"[::]") HOST=127.0.0.1;; esac
BASE="http://$HOST:$CS_PORT"
OK=0
if [ "$DRY" = 1 ]; then OK=1; run curl -fsS "$BASE/app/v1/health"
else
  info "waiting for the app to answer on $BASE"
  for _ in $(seq 1 90); do
    if curl -fsS --max-time 5 "$BASE/app/v1/health" 2>/dev/null | grep -q '"ok": *true' \
       && curl -fsS --max-time 5 -o /dev/null "$BASE/" 2>/dev/null; then OK=1; break; fi
    sleep 2
  done
fi
if [ "$OK" = 1 ]; then
  run rm -rf "$CS_DIR/app.prev"
else
  warn "The app didn't answer on $BASE within 3 minutes. Look at: cd $CS_DIR && docker compose ps && docker compose logs api web"
fi

# ---------------------------------------------------------------- where to go
IP=""
if [ "$HOST" != 127.0.0.1 ] || [ "$CS_BIND" = 127.0.0.1 ]; then IP="$HOST"
elif [ "$OS" = Darwin ]; then
  IF="$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}' || true)"
  IP="$(ipconfig getifaddr "${IF:-en0}" 2>/dev/null || true)"
else
  IP="$( (ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src"){print $(i+1); exit}}') || true)"
  [ -n "$IP" ] || IP="$( (hostname -I 2>/dev/null | awk '{print $1}') || true)"
fi
TS=""; [ "$CS_BIND" = 0.0.0.0 ] && have tailscale && TS="$(tailscale ip -4 2>/dev/null | head -1 || true)"
echo
bold "Cyber-Scout is $([ "$DRY" = 1 ] && echo "(would be) ")$([ "$OK" = 1 ] && echo running || echo starting):"
info "http://${IP:-localhost}:$CS_PORT$([ "$CS_BIND" = 127.0.0.1 ] && echo "   (this machine only: --bind 127.0.0.1)")"
[ -n "$TS" ] && info "http://$TS:$CS_PORT   (Tailscale)"
CODEF="$CS_DIR/data/setup-code.txt"
CODE=""
if [ "$DRY" = 0 ]; then
  for _ in $(seq 1 10); do
    CODE="$(cat "$CODEF" 2>/dev/null || true)"; [ -n "$CODE" ] && break
    [ -e "$CODEF" ] || { [ "$OK" = 1 ] && break; }; sleep 1
  done
fi
if [ -n "$CODE" ]; then
  echo
  bold "First-time setup code: $CODE"
  info "Open the site, enter the code and create the admin account. (Later: cat $CODEF)"
elif [ "$OK" = 1 ] && [ "$EXISTING" = 1 ]; then
  info "(setup already done: sign in with your admin account)"
else
  info "The one-time setup code is in $CODEF (until the admin account is created)."
fi
# installed with get.sh from a downloads site: each laptop gets the Client with one line, already pointed at this server
SITE_RE='^https?://[][A-Za-z0-9.:_-]+$'                       # ] first: literal in a bracket expression
if [ -n "${CS_SITE:-}" ] && [ "$CS_BIND" != 127.0.0.1 ] && [[ "$CS_SITE" =~ $SITE_RE ]]; then
  SRV="http://${IP:-localhost}:$CS_PORT"
  echo
  bold "Laptops: the Cyber-Scout Client in one line (installs it, starts it at login, connects to this server):"
  info "macOS / Linux:  curl -fsSL $CS_SITE/downloads/get-client.sh | bash -s -- --server $SRV"
  info "Windows:        \$env:CS_SERVER='$SRV'; irm $CS_SITE/downloads/get-client.ps1 | iex"
  info "Phones: open $SRV in the browser and add it to the home screen."
fi

# ---------------------------------------------------------------- one-click updates (optional host updater, Linux)
UPD="$CS_DIR/app/deploy/updater/install-updater.sh"
UPDATER_OK=0
if [ "$OS" = Darwin ]; then
  echo; info "Updates: re-run this installer (the one-click updater in Admin -> Updates needs Linux + systemd)."
elif [ -f "$UPD" ] || [ "$DRY" = 1 ]; then
  if [ "$IS_WSL" = 0 ] && have systemctl && [ -d /run/systemd/system ]; then
    if [ "$CS_YES" = "1" ]; then def="${CS_UPDATER:-n}"; else def="${CS_UPDATER:-y}"; fi
    if [ "$EXISTING" = 1 ] && { [ -f "/etc/systemd/system/$CS_PREFIX-updater.service" ] || [ -f "$HOME/.config/systemd/user/$CS_PREFIX-updater.service" ]; }; then
      info "updater already installed"; UPDATER_OK=1
    elif [ "$CS_UPDATER" = n ]; then info "updater: skipped (later: sudo $UPD)"
    else
      echo
      if yes_no "Install the updater for one-click updates in Admin -> Updates (systemd units; runs as $HOST_USER, needs sudo to install)?" "$def"; then
        if [ "$(id -u)" -eq 0 ]; then run bash "$UPD" --stack "$CS_DIR" --yes && UPDATER_OK=1
        elif [ -n "$SUDO" ]; then info "installing systemd units in /etc/systemd/system needs sudo"; run $SUDO bash "$UPD" --stack "$CS_DIR" --yes && UPDATER_OK=1
        else run bash "$UPD" --stack "$CS_DIR" --user --yes && UPDATER_OK=1; fi
        [ "$UPDATER_OK" = 1 ] || warn "the updater wasn't installed; updates work by re-running this installer (or later: sudo $UPD)"
      else info "Skipped. Later: sudo $UPD   (remove: sudo $UPD --uninstall)"; fi
    fi
  else
    info "No systemd here: skipping the one-click updater; update by re-running this installer."
  fi
fi

echo
info "Logs:      cd $CS_DIR && docker compose logs -f worker"
if [ "$UPDATER_OK" = 1 ]; then info "Update:    Admin -> Updates in the web app (or re-run this installer)."
else info "Update:    re-run this installer."; fi
info "Backup:    the whole $CS_DIR folder (data/ holds everything)."
info "Uninstall: bash $CS_DIR/app/deploy/install.sh --uninstall --dir $CS_DIR   (add --purge to delete the data too)"
[ "$OK" = 1 ] || exit 1
}

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]:-install.sh}" 2>/dev/null | sed '$d; s/^# \{0,1\}//' || echo "see the top of install.sh"; }

main "$@"
