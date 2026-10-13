#!/bin/bash
# Screenshot every theme x mode x layout with headless Chrome (no remote debugging).
#   lab/tools/shoot.sh [base_url] [out_dir] [filter]
# Defaults: http://127.0.0.1:8794  .work/lab/shots   (serve lab/ first: python3 -m http.server 8794 --bind 127.0.0.1)
# filter: optional grep pattern on the shot name, e.g. "earth" or "sidebar".
set -u
BASE="${1:-http://127.0.0.1:8794}"
OUT="${2:-$(cd "$(dirname "$0")/../.." && pwd)/.work/lab/shots}"
FILTER="${3:-.}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
mkdir -p "$OUT"
JOBS=$(mktemp)
enc() { python3 -c "import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=''))" "$1"; }
# theme:modes
for tm in harbor:light,dark earth:light,dark jet:dark,light fieldday:light,dark pine:dark anodized:light,dark sandstone:light; do
  t=${tm%%:*}; modes=${tm#*:}
  for m in ${modes//,/ }; do
    for l in sidebar topbar compact focus; do
      echo "desk-$t-$m-$l 1440,1000 $BASE/index.html?theme=$t&layout=$l&mode=$m" >> "$JOBS"
    done
    echo "phone-$t-$m-sidebar 500,844 $BASE/phone.html?src=$(enc "index.html?theme=$t&layout=sidebar&mode=$m")" >> "$JOBS"
  done
done
echo "panel-earth-light-sidebar 1440,1000 $BASE/index.html?theme=earth&layout=sidebar&mode=light&open=1" >> "$JOBS"
echo "panel-jet-dark-sidebar 1440,1000 $BASE/index.html?theme=jet&layout=sidebar&mode=dark&open=1" >> "$JOBS"
echo "panelphone-fieldday-light-sidebar 500,844 $BASE/phone.html?src=$(enc "index.html?theme=fieldday&layout=sidebar&mode=light&open=1")" >> "$JOBS"
grep -E "$FILTER" "$JOBS" | while read -r name size url; do echo "$name $size $url"; done |
  xargs -P 4 -L 1 bash -c 'n=$0; s=$1; u=$2; d=$(mktemp -d); "'"$CHROME"'" --headless=new --disable-gpu --hide-scrollbars --no-first-run --user-data-dir="$d" --window-size=$s --virtual-time-budget=12000 --screenshot="'"$OUT"'/$n.png" "$u" >/dev/null 2>&1; rm -rf "$d"; echo "$n"'
rm -f "$JOBS"
# phone shots were taken in a 500px window (headless minimum) around a 390px frame: crop to the frame
for f in "$OUT"/phone-*.png "$OUT"/panelphone-*.png; do [ -f "$f" ] || continue; w=$(sips -g pixelWidth "$f" | awk '/pixelWidth/{print $2}'); [ "$w" = "390" ] && continue; magick "$f" -crop 390x844+0+0 +repage "$f"; done
