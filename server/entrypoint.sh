#!/bin/sh
# Run as the host user (PUID/PGID) so ./data stays editable on the host and the worker (same ids) can read
# what we write. Only the API's own files are chowned; the worker chowns the rest of DATA itself.
set -e
PUID="${PUID:-1000}"; PGID="${PGID:-1000}"
mkdir -p "$DATA/config"
chown "$PUID:$PGID" "$DATA" "$DATA/config"
for f in "$DATA"/app.db "$DATA"/app.db-wal "$DATA"/app.db-shm "$DATA"/setup-code.txt "$DATA"/config/worker.json; do
  [ -e "$f" ] && chown "$PUID:$PGID" "$f"
done
export HOME=/tmp
# one process: the login rate limiter lives in memory. No access log: nginx already logs requests.
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups \
  python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --no-server-header --no-access-log --log-level info
