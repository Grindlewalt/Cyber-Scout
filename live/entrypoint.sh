#!/bin/sh
# YouTube changes often; an old yt-dlp is the most common way this breaks.
pip install -q -U "yt-dlp[default]" 2>/dev/null || echo "yt-dlp update failed, continuing"
# run as the host user (PUID/PGID) so everything in ./data stays editable on the host
PUID="${PUID:-1000}"; PGID="${PGID:-1000}"
mkdir -p "$DATA" && chown -R "$PUID:$PGID" "$DATA"
export HOME=/tmp
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups python -u /app/live/worker.py "$@"
