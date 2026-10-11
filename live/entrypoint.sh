#!/bin/sh
# YouTube changes often; an old yt-dlp is the most common way this breaks.
pip install -q -U "yt-dlp[default]" 2>/dev/null || echo "yt-dlp update failed, continuing"
exec python -u /app/live/worker.py "$@"
