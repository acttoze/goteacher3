#!/usr/bin/env bash
# 고티처 운영 알림(G6): var/alerts.log에 남기고, ops/.env에 텔레그램 값이 있으면 텔레그램으로도 보낸다.
#   쓰는 법: printf '본문' | ops/notify.sh "제목"
#   ops/.env: TELEGRAM_BOT_TOKEN=…, TELEGRAM_CHAT_ID=…  (헤르메스 에이전트의 홈 채널과 같은 봇·대화)
#   토큰은 명령줄에 싣지 않는다(curl 설정을 표준입력으로). 보내기 실패해도 기록은 남고 종료 코드는 0.
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

dir="${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
title="${1:-고티처 알림}"
body=$(cat)
printf '%s [%s]\n%s\n\n' "$(date '+%F %T')" "$title" "$body" >> "$dir/var/alerts.log"

env="$dir/ops/.env"
token=$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' "$env" 2>/dev/null | tail -1)
chat=$(sed -n 's/^TELEGRAM_CHAT_ID=//p' "$env" 2>/dev/null | tail -1)
[ -n "$token" ] && [ -n "$chat" ] || exit 0

printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
  | curl -s -m 10 -o /dev/null -K - \
      --data-urlencode "chat_id=$chat" \
      --data-urlencode "text=[$title]
$body" \
  || printf '%s [텔레그램 보내기 실패]\n\n' "$(date '+%F %T')" >> "$dir/var/alerts.log"
exit 0
