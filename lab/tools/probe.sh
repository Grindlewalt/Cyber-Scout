#!/bin/bash
# Checks every theme x layout at phone width (390px, via phone.html) for horizontal overflow.
#   lab/tools/probe.sh [base_url]     prints: name scrollWidth clientWidth [overflowing elements]
BASE="${1:-http://127.0.0.1:8794}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
enc() { python3 -c "import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=''))" "$1"; }
for t in harbor earth jet fieldday pine anodized sandstone; do for l in sidebar topbar compact focus; do
  echo "$t-$l $BASE/phone.html?src=$(enc "index.html?theme=$t&layout=$l&probe=1")"
done; done | xargs -P 4 -L 1 bash -c 'd=$(mktemp -d); p=$("'"$CHROME"'" --headless=new --disable-gpu --user-data-dir="$d" --window-size=500,844 --virtual-time-budget=9000 --dump-dom "$1" 2>/dev/null | grep -o "data-probe=\"[^\"]*\"" | head -1 | sed "s/&quot;/\"/g"); rm -rf "$d"; echo "$0 $p"' | sort
