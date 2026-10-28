#!/bin/sh
# 사용법: sh svg2png.sh <in.svg> <out.png>  — Chrome 헤드리스로 SVG를 PNG로
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
IN=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
"$CH" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --user-data-dir=/tmp/chrome-hwp-poc --window-size=820,1160 --screenshot="$2" "file://$IN" >/dev/null 2>&1
