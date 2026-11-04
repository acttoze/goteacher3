#!/usr/bin/env bash
# 고티처 최소 감시(G6). 서버 crontab에서 5분마다. 새 컨테이너·외부 서비스 없이 이 스크립트 하나.
#   보는 것: Traefik 경유 /healthz · worker 실행 중 · db healthy · 디스크 여유 · 백업(DB·미디어) 26시간 안
#            · 최근 5분 500 오류(config/logsafe.py가 남긴 줄 수) · 배포 실패(var/deploy-skip)
#            · purge 성공 26시간 안(cron이 var/purge-ok를 갱신) · Redis 메모리 80% 이하
#   문제 목록이 바뀔 때(생김·풀림)만 알린다. 같은 문제가 이어지면 6시간마다 다시.
#   문제와 별도로 건수만 알린다: 학교 플랜 신청(늘었을 때 한 번) · 후기 한 줄 대기(있으면 하루 한 번, 09시 뒤).
#   이름·메일·후기 글은 명령 출력에 있지만 여기서 숫자만 꺼내고 나머지는 버린다.
#   알림: var/alerts.log + 텔레그램(ops/notify.sh, ops/.env의 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID).
#   확인: GT_DRY=1 ops/monitor.sh  (상태 파일·알림 없이 지금 문제 목록만 출력)
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

dir="${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
var="$dir/var"
host=t.goteacher.xyz
disk_min_gb="${GT_DISK_MIN_GB:-20}"  # 안전 여유 15GB + 다음 미디어 백업 1벌 최대 5GB(HWP_TOTAL_QUOTA). 근거는 README
remind_sec=21600
cd "$dir/ops" || exit 1

problems=()
add() { problems+=("$1: $2"); }

# 1) 앱: Traefik을 거쳐 본다(라우터·인증서·앱 모두). 교체 중 잠깐 503일 수 있어 3번까지
ok=""
for _ in 1 2 3; do
  [ "$(curl -s -m 10 --resolve "$host:443:127.0.0.1" "https://$host/healthz" || true)" = ok ] && { ok=1; break; }
  sleep 5
done
[ -n "$ok" ] || add 앱 "https://$host/healthz 응답 없음"

# 2) worker·db
w=$(docker compose ps -q goteacher-worker)
[ -n "$w" ] && [ "$(docker inspect -f '{{.State.Status}}' "$w")" = running ] || add worker "goteacher-worker가 실행 중이 아님"
d=$(docker compose ps -q db)
[ -n "$d" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$d")" = healthy ] || add db "db가 healthy가 아님"

# 3) 디스크(공용 서버 /)
free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
[ "$free_gb" -ge "$disk_min_gb" ] || add 디스크 "/ 여유 ${free_gb}GB(기준 ${disk_min_gb}GB)"

# 4) 백업: 매일 04:30. 가장 최근 DB·미디어 묶음이 26시간 안에 있어야
for spec in 'DB:goteacher-2*.dump' '미디어:goteacher-media-*.tar.gz'; do
  [ -n "$(find "$var/backups" -maxdepth 1 -name "${spec#*:}" -mmin -1560 2>/dev/null | head -1)" ] \
    || add "백업 ${spec%%:*}" "26시간 안의 ${spec#*:} 없음(var/backup.log 확인)"
done

# 5) 500 오류(값 없이 경로·종류만 남는 줄)
n=$(docker compose logs --since 5m --no-log-prefix goteacher-app 2>/dev/null | grep -c "ERROR django.request Internal Server Error" || true)
[ "${n:-0}" -eq 0 ] || add "500 오류" "최근 5분 ${n}건 — docker compose logs goteacher-app 확인"

# 6) 배포 실패(deploy.sh가 실패한 커밋을 적는다). 그 뒤 성공한 배포가 없을 때만. 상태 키에 커밋을 넣는다
skip=$(cat "$var/deploy-skip" 2>/dev/null || true)
if [ -n "$skip" ] && [ "$var/deploy-skip" -nt "$var/deployed" ]; then
  add "배포 실패 ${skip:0:7}" "var/deploy.log 확인(직전 버전으로 운영 중)"
fi

# 7) purge(매일 03:40): crontab 줄이 성공했을 때만 var/purge-ok를 갱신한다. 복무 7일 삭제 등 개인정보 약속이 걸린다
[ -n "$(find "$var/purge-ok" -mmin -1560 2>/dev/null)" ] || add purge "26시간 안에 성공 없음(var/purge.log 확인)"

# 8) Redis: noeviction이라 maxmemory가 차면 큐 쓰기가 실패한다
redis_pct=$(timeout 20 docker compose exec -T redis redis-cli info memory 2>/dev/null | tr -d '\r' \
  | awk -F: '$1=="used_memory"{u=$2} $1=="maxmemory"{m=$2} END{if(m>0)print int((u*100+m-1)/m)}')  # 올림: 80.4%도 넘은 것
if [ -z "$redis_pct" ]; then
  add Redis "메모리 사용량을 읽지 못함"
elif [ "$redis_pct" -gt 80 ]; then
  add Redis "메모리 ${redis_pct}%(차면 큐가 멈춤)"
fi

# 9) 운영자가 처리할 건수. 앱이 살아 있을 때만 묻는다(앱 장애는 1번이 이미 알린다).
# 출력의 나머지(이름·메일·후기 글)는 변수에 담지 않고 버린다. 오류 출력도 버린다.
manage() { timeout 60 docker compose exec -T goteacher-app python app/manage.py "$@" 2>/dev/null; }
plan_n=""
review_n=""
if [ -n "$ok" ]; then
  plan_n=$(manage school_plan requests | sed -n '1s/^대기 \([0-9][0-9]*\)건$/\1/p')
  [ -n "$plan_n" ] || add "플랜 신청 확인" "school_plan requests 실패"
  if [ "$(date +%H)" -ge 9 ] && [ "$(cat "$var/review-day" 2>/dev/null)" != "$(date +%F)" ]; then
    review_n=$(manage partners reviews --pending | awk 'END{print NR}') \
      || { review_n=""; add "후기 대기 확인" "partners reviews --pending 실패"; }
  fi
fi

if [ "${GT_DRY:-0}" = 1 ]; then
  [ ${#problems[@]} -eq 0 ] && echo "문제 없음" || printf '%s\n' "${problems[@]}"
  [ -z "$plan_n" ] || echo "(건수) 플랜 신청 대기 ${plan_n}건"
  [ -z "$review_n" ] || echo "(건수) 후기 한 줄 대기 ${review_n}건"
  exit 0
fi

notify() {  # $1 제목, 표준입력 본문 → var/alerts.log + 텔레그램(ops/notify.sh)
  GT_DIR="$dir" "$dir/ops/notify.sh" "$1"
}

if [ -n "$plan_n" ]; then
  plan_prev=$(cat "$var/plan-pending" 2>/dev/null || true)
  [ "$plan_n" -le "${plan_prev:-0}" ] || echo "대기 ${plan_n}건" | notify "고티처 학교 플랜 신청"
  echo "$plan_n" > "$var/plan-pending"
fi
if [ -n "$review_n" ]; then
  [ "$review_n" -eq 0 ] || echo "대기 ${review_n}건" | notify "고티처 후기 한 줄 확인"
  date +%F > "$var/review-day"
fi

state="$var/monitor.state"
keys=$(for p in "${problems[@]}"; do echo "${p%%:*}"; done | sort)
prev=$(sed 1d "$state" 2>/dev/null || true)
last=$(head -1 "$state" 2>/dev/null || echo 0)
now=$(date +%s)
if [ "$keys" != "$prev" ]; then
  if [ -z "$keys" ]; then
    echo "모든 항목 정상으로 돌아옴" | notify "고티처 복구"
  else
    printf '%s\n' "${problems[@]}" | notify "고티처 문제 ${#problems[@]}건"
  fi
  printf '%s\n%s\n' "$now" "$keys" > "$state"
elif [ -n "$keys" ] && [ $((now - last)) -ge $remind_sec ]; then
  printf '%s\n' "${problems[@]}" | notify "고티처 문제 계속 ${#problems[@]}건"
  printf '%s\n%s\n' "$now" "$keys" > "$state"
fi
