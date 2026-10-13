#!/bin/sh
# Worker entrypoint. yt-dlp is pinned (with hashes) in the image: no `pip install -U` at start (security audit
# finding 3); to update it, bump live/requirements-ytdlp.txt and rebuild.
set -e
# run as the host user (PUID/PGID) so everything in ./data stays editable on the host
PUID="${PUID:-1000}"; PGID="${PGID:-1000}"
mkdir -p "$DATA"
# -h / -P: change symlinks themselves and never follow them out of DATA (a planted link must not hand root's files
# to PUID; security audit finding 5)
chown -R -h -P "$PUID:$PGID" "$DATA"
export HOME=/tmp
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups --no-new-privs python -u /app/live/worker.py "$@"
