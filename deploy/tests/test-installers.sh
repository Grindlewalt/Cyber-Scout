#!/usr/bin/env bash
# shellcheck disable=SC2015,SC2016  # "a && pass || fail" and single-quoted $BASH_VERSION are intended
# Checks for deploy/install.sh that change nothing on the machine: syntax, shellcheck (when installed) and
# --dry-run scenarios (with or without Docker present). Linux + macOS (also runs under macOS' bash 3.2).
#   bash deploy/tests/test-installers.sh
# The real end-to-end recipe (build + start + uninstall on a throwaway prefix) is in docs/guide/deploy.md, section 1.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/../install.sh"
BASH_BIN="${BASH_BIN:-bash}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fails=0
pass() { printf 'ok    %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }
expect() {  # expect NAME OUTPUT PATTERN...
  local name="$1" out="$2"; shift 2
  local p; for p in "$@"; do
    if printf '%s' "$out" | grep -qE -- "$p"; then :; else fail "$name: output lacks /$p/"; printf '%s\n' "$out" | tail -25; return; fi
  done
  pass "$name"
}

"$BASH_BIN" -n "$INSTALL" && pass "bash -n ($("$BASH_BIN" -c 'echo $BASH_VERSION'))" || fail "bash -n"
if command -v shellcheck >/dev/null; then
  shellcheck "$INSTALL" && pass "shellcheck" || fail "shellcheck"
else
  printf 'skip  shellcheck (not installed: brew install shellcheck / apt install shellcheck)\n'
fi

out="$("$BASH_BIN" "$INSTALL" --help)"; expect "--help" "$out" "--dry-run" "--uninstall" "--no-worker"

dir="$TMP/stack"
out="$(CS_YES=1 "$BASH_BIN" "$INSTALL" --dry-run --dir "$dir" --port 7599 2>&1)" || { fail "dry-run exit code $?"; printf '%s\n' "$out"; }
expect "dry-run fresh install" "$out" "dry run" "\.env: WEB_PORT=7599" "\.env: PUID=$(id -u)" "\.env: CS_PREFIX=cyber-scout" \
  "docker compose (build|up)" "up -d --no-build" "robot detector"
[ -e "$dir" ] && fail "dry-run created $dir" || pass "dry-run changed nothing"
case "$(uname -s)" in
  Darwin) expect "macOS: CPU override + no updater" "$out" "COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml" "needs Linux \+ systemd";;
  Linux)  expect "Linux: GPU or CPU decided" "$out" "(GPU_IDS=|COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml)";;
esac

out="$("$BASH_BIN" "$INSTALL" --dry-run --yes --dir "$dir" --cpu --no-worker --prefix cs-test --bind 127.0.0.1 --port 7598 \
        --tarball https://example.invalid/src.tar.gz --model https://example.invalid/robot.onnx 2>&1)" || fail "dry-run 2 exit code"
expect "dry-run --cpu --no-worker --tarball --model" "$out" "COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml" \
  "CS_PREFIX=cs-test" "WEB_BIND=127.0.0.1" "build api web|compose build" "up -d --no-build (api web|web api)" \
  "curl .*example.invalid/src.tar.gz" "curl .*example.invalid/robot.onnx" "starting only"
[ -e "$dir" ] && fail "dry-run 2 created $dir" || pass "dry-run 2 changed nothing"

mkdir -p "$dir"; printf 'services: {}\n' > "$dir/docker-compose.yml"; printf 'CS_PREFIX=cs-test\n' > "$dir/.env"
out="$("$BASH_BIN" "$INSTALL" --dry-run --uninstall --purge --yes --dir "$dir" 2>&1)" || fail "dry-run uninstall exit code"
expect "dry-run --uninstall --purge" "$out" "Removing Cyber-Scout \(cs-test\)" "rm -rf .*stack"
[ -f "$dir/.env" ] && pass "dry-run uninstall kept the files" || fail "dry-run uninstall deleted files"

# GPU choice against stand-ins for docker and nvidia-smi (Linux only: macOS is always CPU mode): GPU 0 works through
# the NVIDIA hook, GPU 1 only through CDI (atomosserver's hardened kernel), and `compose up` fails like the hook does.
if [ "$(uname -s)" = Linux ]; then
  fake="$TMP/fakebin"; src="$TMP/src"; mkdir -p "$fake" "$src/deploy"
  cp "$HERE/../docker-compose.yml" "$HERE/../docker-compose.cpu.yml" "$src/deploy/"
  cat > "$fake/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
case "$*" in -L) printf 'GPU 0: Fake A (UUID: GPU-a)\nGPU 1: Fake B (UUID: GPU-b)\n';; *query-gpu=name*) printf 'Fake A\nFake B\n';; esac
EOF
  cat > "$fake/docker" <<'EOF'
#!/usr/bin/env bash
hook_err() { echo "nvidia-container-cli: mount error: failed to add device rules: unable to generate new device filter program" >&2; exit 125; }
case "$*" in
  "info --format "*Runtimes*) echo "nvidia runc";;
  "info --format "*OSType*) echo linux;;
  "info --format "*DockerRootDir*) echo /tmp;;
  "info --format "*MemTotal*) echo 16000000000;;
  "compose version --short") echo 2.30.0;;
  "version --format "*) echo 27.0.0;;
  *'--gpus "device=0" '*) echo "GPU 0: Fake A";;
  *--gpus*) hook_err;;
  *"--device nvidia.com/gpu=1 "*) echo "GPU 0: Fake B";;
  *"--device nvidia.com/gpu="*) echo "CDI device injection failed: unresolvable CDI devices nvidia.com/gpu=9" >&2; exit 125;;
  "compose up"*) echo " Container cs-test-worker Starting"; hook_err;;
esac
exit 0
EOF
  chmod +x "$fake/docker" "$fake/nvidia-smi"
  gdir="$TMP/gpustack"
  out="$(PATH="$fake:$PATH" CS_YES=1 "$BASH_BIN" "$INSTALL" --dir "$gdir" --source "$src" --prefix cs-test --port 7597 \
          --no-updater --gpu-ids 0,1 2>&1)" && fail "GPU 0,1 with a CDI-only card accepted" || \
    expect "GPU list with a CDI-only card refused" "$out" "GPU 1 +Fake B +.*works in containers.* \(through CDI\)" \
      "is 0,1, which won't work" "only through CDI" "Nothing was changed"
  [ -e "$gdir" ] && fail "refused GPU choice created $gdir" || pass "refused GPU choice changed nothing"
  out="$(PATH="$fake:$PATH" CS_YES=1 "$BASH_BIN" "$INSTALL" --dir "$gdir" --source "$src" --prefix cs-test --port 7597 \
          --no-updater --gpu-ids 1 2>&1)" && fail "failed start exited 0" || \
    expect "CDI card + failed start: summary" "$out" "worker \+ recorder: GPU 1 \(through CDI" "starting the containers failed" \
      "Cyber-Scout didn't come up" "Problem .*NVIDIA hook" "Fix .*Re-run the installer" "Address +http://"
  grep -qx 'GPU_DRIVER=cdi' "$gdir/.env" && grep -qx 'GPU_IDS=1' "$gdir/.env" && pass ".env: GPU_IDS=1 + GPU_DRIVER=cdi" \
    || { fail ".env lacks GPU_IDS=1 / GPU_DRIVER=cdi"; cat "$gdir/.env"; }
  grep -q 'failed to add device rules' "$gdir/install.log" && pass "install.log has the start output" || fail "install.log"
fi

out="$("$BASH_BIN" "$INSTALL" --bogus 2>&1)" && fail "unknown option accepted" || expect "unknown option rejected" "$out" "unknown option"
out="$("$BASH_BIN" "$INSTALL" --purge 2>&1)" && fail "--purge alone accepted" || expect "--purge needs --uninstall" "$out" "only goes with"

echo
[ "$fails" = 0 ] && echo "all installer checks passed" || { echo "$fails check(s) failed"; exit 1; }
