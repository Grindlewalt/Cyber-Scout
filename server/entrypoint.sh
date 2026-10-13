#!/bin/sh
# Run as the host user (PUID/PGID) so ./data stays editable on the host and the worker (same ids) can read
# what we write. Only the API's own files are chowned; the worker chowns the rest of DATA itself.
# The worker runs as that same uid and shares DATA, so nothing here may follow a symlink it could plant
# (security audit finding 5): refuse a symlinked config dir, skip symlinked files, chown -h.
set -e
PUID="${PUID:-1000}"; PGID="${PGID:-1000}"
for d in "$DATA" "$DATA/config"; do
  if [ -L "$d" ]; then
    echo "refusing to start: $d is a symlink" >&2
    exit 1
  fi
done
mkdir -p "$DATA/config"
chown -h "$PUID:$PGID" "$DATA" "$DATA/config"
for f in "$DATA"/app.db "$DATA"/app.db-wal "$DATA"/app.db-shm "$DATA"/setup-code.txt "$DATA"/config/worker.json; do
  if [ -L "$f" ]; then
    echo "warning: $f is a symlink; not touching it" >&2
  elif [ -e "$f" ]; then
    chown -h "$PUID:$PGID" "$f"
  fi
done
export HOME=/tmp
# one process: the login / write rate limiters live in memory. No access log: nginx already logs requests.
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups --no-new-privs \
  python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --no-server-header --no-access-log --log-level info
