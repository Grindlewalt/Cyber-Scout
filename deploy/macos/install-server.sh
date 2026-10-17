#!/bin/bash
# Cyber-Scout server natively on a Mac (no Docker, no sudo): api (uvicorn) + web (nginx) + worker + recorder as
# launchd LaunchAgents, everything under $CS_HOME (default ~/CyberScout/server):
#   app/      the code (git archive of the repo)            data/   DATA (app.db, site JSON, events, models/)
#   media/    MEDIA (kept videos, livestream recordings)    venv/   Python 3.12 (uv) with server + worker packages
#   bin/      ffmpeg, ffprobe, deno, uv                     nginx/  nginx built from source (prefix)
#   web/      the served web root (built from app/web)      logs/   service logs
# Re-run after every code update (deploy/macos/deploy.sh does that): rebuilds web/, nginx.conf, the service
# definitions, and restarts the services.
#   CS_LISTEN="100.64.153.104 127.0.0.1"  addresses nginx listens on (default: this Mac's Tailscale IP + loopback)
#   CS_PORT=7560                          port
set -euo pipefail
H="${CS_HOME:-$HOME/CyberScout/server}"
APP="$H/app"; DATA="$H/data"; MEDIA="$H/media"; WEB="$H/web"; NG="$H/nginx"; LOGS="$H/logs"
PORT="${CS_PORT:-7560}"
TSIP="$("$H/../bin/tailscale" ip -4 2>/dev/null || /usr/local/bin/tailscale ip -4 2>/dev/null || true)"
LISTEN="${CS_LISTEN:-${TSIP:+$TSIP }127.0.0.1}"
UIDN="$(id -u)"; LA="$HOME/Library/LaunchAgents"
mkdir -p "$DATA/config" "$DATA/models" "$MEDIA" "$LOGS" "$NG/cache" "$NG/temp" "$LA"
chmod 700 "$DATA"

# ---- version (GET /app/v1/info): clients re-mirror the web app when it changes
[ -s "$APP/VERSION" ] || echo "src-$(cd "$APP" && find server web src live -type f ! -name '*.pyc' ! -path '*/__pycache__/*' \
  ! -path 'server/tests/*' -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-10)" > "$APP/VERSION"

# ---- web root (= web/Dockerfile): shell files, ?v=<hash> cache-bust, precompressed .gz; api/ media/ link to data
rm -rf "$WEB.new"; mkdir -p "$WEB.new"
cp "$APP"/web/*.html "$APP"/web/*.js "$APP"/web/*.css "$APP"/web/field.jpg "$APP"/web/manifest.webmanifest "$WEB.new"/
cp -R "$APP/web/fonts" "$APP/web/icons" "$WEB.new"/
( cd "$WEB.new" && v=$(cat *.js *.css | md5 -q | cut -c1-10) \
  && sed -i '' -E "s#(src|href)=\"([A-Za-z0-9_.-]+\.(js|css))\"#\1=\"\2?v=${v}\"#g" index.html \
  && for f in *.html *.js *.css; do gzip -9 -n -c "$f" > "$f.gz" && touch -r "$f" "$f.gz"; done )
mkdir -p "$DATA/site/api"
ln -sfn "$DATA/site/api" "$WEB.new/api"
ln -sfn "$MEDIA" "$WEB.new/media"
rm -rf "$WEB.old"; [ -d "$WEB" ] && mv "$WEB" "$WEB.old"; mv "$WEB.new" "$WEB"; rm -rf "$WEB.old"

# ---- nginx.conf from the repo's (paths, port, api on loopback; no Docker DNS)
{
  echo "worker_processes 2; worker_rlimit_nofile 8192; pid $NG/nginx.pid; error_log $LOGS/nginx-error.log warn;"
  echo "events { worker_connections 1024; }"
  echo "http {"
  echo "  include $NG/conf/mime.types; default_type application/octet-stream; sendfile on; keepalive_timeout 75;"
  echo "  access_log $LOGS/nginx-access.log; client_body_temp_path $NG/temp/body; proxy_temp_path $NG/temp/proxy;"
  echo "  client_max_body_size 64m;"
  sed -e "s#/var/cache/nginx/cs_auth#$NG/cache/cs_auth#" \
      -e "s#root /usr/share/nginx/html;#root $WEB;#" \
      -e "s#resolver 127.0.0.11 valid=10s ipv6=off;#\# (native: no resolver needed, the api is on loopback)#" \
      -e "s#set \$api http://api:8000;#set \$api http://127.0.0.1:8000;#" \
      "$APP/web/nginx.conf" | awk -v L="$LISTEN" -v P="$PORT" '
        /^[[:space:]]*listen 80;/ { n=split(L, a, " "); for (i=1;i<=n;i++) printf "    listen %s:%s;\n", a[i], P; next }
        { print }'
  echo "}"
} > "$NG/conf/cyberscout.conf"
"$NG/sbin/nginx" -t -c "$NG/conf/cyberscout.conf" -p "$NG/"

# ---- run scripts
PATHS="$H/bin:$H/venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
COMMON="export PATH=\"$PATHS\" DATA=\"$DATA\" MEDIA=\"$MEDIA\" TZ=America/Los_Angeles PYTHONUNBUFFERED=1 HOME=\"$HOME\"
[ -f \"$H/server.env\" ] && set -a && . \"$H/server.env\" && set +a
cd \"$APP\""
cat > "$H/run-api.sh" <<EOF
#!/bin/bash
$COMMON
export COOKIE_SECURE=auto SESSION_IDLE_DAYS=7 SESSION_MAX_DAYS=30 PASSWORD_MIN=10 MEMBER_PASSWORD_MIN=4 \
  SETUP_CODE_REQUIRED=1 PYTHONDONTWRITEBYTECODE=1 SYNC_CACHE_MB=256 SERVER_NAME="\${SERVER_NAME:-Cyber-Scout (M2)}"
exec python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --no-server-header --no-access-log --log-level info
EOF
cat > "$H/run-worker.sh" <<EOF
#!/bin/bash
$COMMON
export FORCE_CPU=\${FORCE_CPU:-1} ORT_COREML=\${ORT_COREML:-1} MEDIA_PROXIES=\${MEDIA_PROXIES:-1} PROXY_GPU=\${PROXY_GPU:-0} YTDLP="$H/venv/bin/yt-dlp"
exec python -u live/worker.py "\$@"
EOF
cat > "$H/run-nginx.sh" <<EOF
#!/bin/bash
# wait for the Tailscale address (nginx binds to it) instead of crash-looping right after boot
for i in \$(seq 1 60); do "$NG/sbin/nginx" -t -q -c "$NG/conf/cyberscout.conf" -p "$NG/" 2>/dev/null && break; sleep 2; done
exec "$NG/sbin/nginx" -g "daemon off;" -c "$NG/conf/cyberscout.conf" -p "$NG/"
EOF
chmod +x "$H"/run-*.sh

# ---- launchd services (KeepAlive: restarted if they ever exit)
svc() {  # label, script, args...
  local L="$1" S="$2"; shift 2
  local A=""; for x in "$@"; do A="$A<string>$x</string>"; done
  cat > "$LA/$L.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$L</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$S</string>$A</array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOGS/$L.log</string><key>StandardErrorPath</key><string>$LOGS/$L.log</string>
</dict></plist>
EOF
  launchctl bootout "gui/$UIDN/$L" 2>/dev/null || true
  # bootout returns before the old job is gone; bootstrapping too early fails with "Input/output error"
  for i in $(seq 1 30); do launchctl print "gui/$UIDN/$L" >/dev/null 2>&1 || break; sleep 0.5; done
  for i in 1 2 3; do launchctl bootstrap "gui/$UIDN" "$LA/$L.plist" 2>/dev/null && return 0; sleep 2; done
  echo "could not start $L (launchctl bootstrap gui/$UIDN $LA/$L.plist)" >&2; return 1
}
svc com.cyberscout.api "$H/run-api.sh"
svc com.cyberscout.web "$H/run-nginx.sh"
svc com.cyberscout.worker "$H/run-worker.sh"
svc com.cyberscout.recorder "$H/run-worker.sh" --recorder
sleep 6
for L in api web worker recorder; do
  printf "%-10s %s\n" "$L" "$(launchctl print "gui/$UIDN/com.cyberscout.$L" 2>/dev/null | awk '/^\tstate =/{print $3}')"
done
curl -s -o /dev/null -w "web+api: GET /app/v1/info -> %{http_code}\n" "http://127.0.0.1:$PORT/app/v1/info" || true
