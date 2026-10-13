#!/usr/bin/env bash
# Install / remove the Cyber-Scout host updater behind Admin -> Updates (see README.md next to this file).
#
#   sudo ./app/deploy/updater/install-updater.sh              systemd system units, run as the stack folder's owner
#   ./app/deploy/updater/install-updater.sh --user            systemd --user units instead (no sudo; needs linger)
#   sudo ./app/deploy/updater/install-updater.sh --uninstall  remove the units (add --user if installed that way)
#
# Options: --stack DIR   the stack folder (default: the folder this app/ lives in, e.g. /opt/cyber-scout)
#          --yes         don't ask
# First install writes <stack>/updater/updater.conf; these preset it: CS_UPD_REMOTE, CS_UPD_BRANCH, CS_UPD_KEY.
# The updater never runs as root: it runs as the owner of the stack folder, who must be in the docker group.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK="" MODE="" UNINSTALL=0 YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) STACK="$2"; shift 2;;
    --stack=*) STACK="${1#*=}"; shift;;
    --user) MODE=user; shift;;
    --system) MODE=system; shift;;
    --uninstall) UNINSTALL=1; shift;;
    --yes|-y) YES=1; shift;;
    -h|--help) sed -n '2,13p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31mx %s\033[0m\n' "$*" >&2; exit 1; }

[ -n "$STACK" ] || STACK="$(cd "$HERE/../../.." && pwd)"
STACK="$(cd "$STACK" 2>/dev/null && pwd -P)" || die "no such folder: $STACK"
[[ "$STACK" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "the stack path may only contain letters, digits and . _ - / : $STACK"
[ -f "$STACK/docker-compose.yml" ] && [ -d "$STACK/app" ] && [ -d "$STACK/data" ] \
  || die "$STACK isn't a Cyber-Scout stack folder (needs docker-compose.yml, app/, data/). Use --stack DIR."
command -v systemctl >/dev/null && [ -d /run/systemd/system ] || die "systemd is required for the updater units"

OWNER="$(stat -c %U "$STACK")"; GROUP="$(stat -c %G "$STACK")"; OWNER_UID="$(stat -c %u "$STACK")"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
PREFIX="$( (grep -E '^CS_PREFIX=' "$STACK/.env" 2>/dev/null || true) | tail -1 | cut -d= -f2 | tr -cd 'a-z0-9-')"
NAME="${PREFIX:-cyber-scout}-updater"
if [ -z "$MODE" ]; then MODE=system; [ "$(id -u)" -ne 0 ] && MODE=user_or_sudo; fi
if [ "$MODE" = user_or_sudo ]; then
  die "run it with sudo (system units, recommended), or add --user for systemd --user units"
fi
if [ "$MODE" = system ]; then
  [ "$(id -u)" -eq 0 ] || die "system install needs root: sudo $0 (or --user)"
  UNIT_DIR=/etc/systemd/system
  SC() { systemctl "$@"; }
else
  [ "$(id -u)" = "$OWNER_UID" ] || die "--user: run this as $OWNER (the owner of $STACK), without sudo"
  UNIT_DIR="$OWNER_HOME/.config/systemd/user"
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$OWNER_UID}"
  SC() { systemctl --user "$@"; }
fi
as_owner() {
  if [ "$(id -u)" = "$OWNER_UID" ]; then "$@"
  elif command -v runuser >/dev/null; then runuser -u "$OWNER" -- "$@"
  else sudo -u "$OWNER" -- "$@"; fi
}

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  bold "Removing the Cyber-Scout updater units ($NAME, $MODE)"
  SC disable --now "$NAME.path" "$NAME.timer" 2>/dev/null || true
  SC stop "$NAME.service" 2>/dev/null || true
  rm -f "$UNIT_DIR/$NAME.service" "$UNIT_DIR/$NAME.path" "$UNIT_DIR/$NAME.timer"
  SC daemon-reload
  info "Done. Admin -> Updates now shows 'updater not installed' (after ~3 minutes)."
  info "Its state, log, database backups and deploy key are kept in $STACK/updater/ (rm -rf it to remove them)."
  exit 0
fi

# ---------------------------------------------------------------- checks
bold "Cyber-Scout updater for $STACK"
[ "$OWNER_UID" != 0 ] || [ "${CS_UPDATER_ALLOW_ROOT:-0}" = 1 ] \
  || die "$STACK is owned by root. The updater runs as the folder's owner and must not be root: chown -R <user>: $STACK (a user in the docker group), then re-run."
SOCK_GROUP="$(stat -c %G /var/run/docker.sock 2>/dev/null || echo docker)"
id -nG "$OWNER" | tr ' ' '\n' | grep -qx "$SOCK_GROUP" \
  || die "$OWNER isn't in the '$SOCK_GROUP' group, so it can't run docker: sudo usermod -aG $SOCK_GROUP $OWNER (then re-run)"
PY="$(command -v python3 || true)"
[ -n "$PY" ] && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "python3 (3.8+) is required on the host"
command -v git >/dev/null || die "git is required on the host (apt install git / dnf install git / pacman -S git)"
as_owner docker compose version >/dev/null 2>&1 || die "$OWNER can't run 'docker compose' (Docker Compose v2 plugin)"
info "runs as: $OWNER ($MODE units: $NAME.service / .path / .timer)"

# ---------------------------------------------------------------- files in <stack>/updater (not visible to the containers)
U="$STACK/updater"
as_owner mkdir -p "$U"
as_owner chmod 750 "$U"
as_owner install -m 0755 "$HERE/cs-updater.py" "$U/cs-updater.py"
if [ ! -f "$U/updater.conf" ]; then
  REMOTE="${CS_UPD_REMOTE:-}"
  [ -n "$REMOTE" ] || REMOTE="$(git -C "$STACK/app" config --get remote.origin.url 2>/dev/null || true)"
  [ -n "$REMOTE" ] || REMOTE="https://github.com/Grindlewalt/Cyber-Scout.git"
  as_owner tee "$U/updater.conf" >/dev/null <<EOF
# Cyber-Scout updater settings. Read by updater/cs-updater.py on the host only (the containers can't see this
# folder). The web app can't change any of this: it can only ask for check / apply / rollback.
REMOTE=$REMOTE
BRANCH=${CS_UPD_BRANCH:-main}
# private repository: REMOTE=git@github.com:<owner>/<repo>.git and a read-only deploy key (README.md, "Private repo")
SSH_KEY=${CS_UPD_KEY:-}
# refuse to update when the docker disk would drop below this (GB); estimates for what a rebuild adds
#MIN_FREE_GB=1.0
#HEAVY_BUILD_GB=3.5
#LIGHT_BUILD_GB=0.3
#HEALTH_TIMEOUT=300
#AUTO_CHECK_HOURS=24
#DB_BACKUPS_KEEP=5
# 0 = refuse the "also update yt-dlp" option
#YTDLP=1
EOF
  as_owner chmod 640 "$U/updater.conf"
  info "wrote $U/updater.conf (REMOTE=$REMOTE)"
else
  info "keeping $U/updater.conf"
fi
if [ ! -f "$U/known_hosts" ]; then
  # GitHub's published SSH host key (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
  echo "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" | as_owner tee "$U/known_hosts" >/dev/null
fi

# ---------------------------------------------------------------- units
mkdir -p "$UNIT_DIR"
render() {   # template -> unit file with the placeholders filled in
  sed -e "s|@STACK@|$STACK|g" -e "s|@USER@|$OWNER|g" -e "s|@GROUP@|$GROUP|g" -e "s|@PYTHON@|$PY|g" \
      -e "s|@NAME@|$NAME|g" "$HERE/$1"
}
if [ "$MODE" = system ]; then
  render cs-updater.service > "$UNIT_DIR/$NAME.service"
else   # user manager: no User=/Group=, and the sandboxing options need privileges it doesn't have
  render cs-updater.service | grep -vE '^(User|Group|NoNewPrivileges|ProtectSystem|PrivateTmp|ProtectKernel|ProtectControlGroups|RestrictSUIDSGID|LockPersonality|# defence)' \
    > "$UNIT_DIR/$NAME.service"
fi
render cs-updater.path > "$UNIT_DIR/$NAME.path"
render cs-updater.timer > "$UNIT_DIR/$NAME.timer"
if [ "$MODE" = user ]; then chown -R "$OWNER:$GROUP" "$OWNER_HOME/.config/systemd" 2>/dev/null || true; fi
SC daemon-reload
SC enable --now "$NAME.path" "$NAME.timer" >/dev/null
SC start --no-block "$NAME.service"
info "installed: $UNIT_DIR/$NAME.{service,path,timer}"
if [ "$MODE" = user ] && [ "$(loginctl show-user "$OWNER" -p Linger --value 2>/dev/null)" != yes ]; then
  warn "user units only run while $OWNER is logged in. Make them run always: sudo loginctl enable-linger $OWNER"
fi

REMOTE_NOW="$(grep -E '^REMOTE=' "$U/updater.conf" | tail -1 | cut -d= -f2-)"
KEY_NOW="$(grep -E '^SSH_KEY=' "$U/updater.conf" | tail -1 | cut -d= -f2-)"
echo
bold "Done. Admin -> Updates shows the version within a minute."
case "$REMOTE_NOW" in
  git@*|ssh://*) [ -n "$KEY_NOW" ] || warn "REMOTE is ssh but SSH_KEY is empty in $U/updater.conf: see README.md, 'Private repo'";;
esac
J="journalctl"; [ "$MODE" = user ] && J="journalctl --user"
info "Status:     $PY $U/cs-updater.py --stack $STACK status"
info "From shell: $PY $U/cs-updater.py --stack $STACK check | apply | rollback"
info "Logs:       $U/update.log   ($J -u $NAME)"
info "Remove:     $([ "$MODE" = system ] && echo "sudo ")$0 --uninstall$([ "$MODE" = user ] && echo " --user")"
