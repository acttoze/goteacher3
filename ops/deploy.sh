#!/usr/bin/env bash
# 스테이징 자동 배포(B2). 서버 crontab에서 1분마다 실행한다.
#   origin/main이 마지막 배포와 다르고, 그 커밋의 GitHub 체크 "test"(.github/workflows/ci.yml)가
#   success일 때만 반영한다: reset → build → migrate → up.
#   실패·취소된 커밋은 var/deploy-skip에 적고 다시 시도하지 않는다(다음 푸시를 기다림).
#   공개 저장소라 체크 조회에 토큰이 필요 없다(비인증 60회/시간 — 새 커밋이 있을 때만 호출).
# 수동 1회 반영(CI 결과 없이, 예: 이 스크립트 도입 전 커밋): GT_SKIP_CI=1 ops/deploy.sh
set -Eeuo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

main() {
  local dir="${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
  local repo=acttoze/goteacher3
  local var="$dir/var"
  mkdir -p "$var"
  exec 9>"$var/deploy.lock"
  flock -n 9 || exit 0

  cd "$dir"
  git fetch -q origin main
  local new deployed
  new=$(git rev-parse origin/main)
  deployed=$(cat "$var/deployed" 2>/dev/null || true)
  [ "$new" = "$deployed" ] && exit 0
  [ "$new" = "$(cat "$var/deploy-skip" 2>/dev/null || true)" ] && exit 0

  if [ "${GT_SKIP_CI:-0}" != 1 ]; then
    local st
    st=$(curl -fsS -m 10 -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$repo/commits/$new/check-runs?check_name=test" \
      | jq -r '.check_runs[0] // {} | "\(.status) \(.conclusion)"') || { log "체크 조회 실패 ${new:0:7}"; exit 0; }
    case "$st" in
      "completed success") ;;
      completed*) log "CI 실패(${st#completed }) ${new:0:7} — 반영 안 함"; echo "$new" > "$var/deploy-skip"; exit 0 ;;
      *) exit 0 ;;  # 아직 진행 중이거나 체크가 없음: 다음 분에 다시 본다
    esac
  fi

  log "반영 시작 ${deployed:0:7} → ${new:0:7}"
  trap 'log "반영 실패 ${new:0:7} — var/deploy-skip에 기록"; echo "$new" > "$var/deploy-skip"' ERR
  git reset -q --hard "$new"
  cd ops
  local old_img
  old_img=$(docker image inspect -f '{{.Id}}' goteacher-app:latest 2>/dev/null || true)
  docker compose build -q goteacher-app
  # 임시 컨테이너가 Traefik 백엔드로 잡히지 않게 라벨을 끈다
  docker compose run --rm -T -l traefik.enable=false goteacher-app python app/manage.py migrate --noinput
  docker compose up -d --wait goteacher-app goteacher-worker  # worker도 같은 이미지로 새로 띄운다
  echo "$new" > "$var/deployed"
  trap - ERR
  # 이전 이미지는 이 프로젝트 것만 지운다(prune 쓰지 않음)
  local cur_img
  cur_img=$(docker image inspect -f '{{.Id}}' goteacher-app:latest)
  if [ -n "$old_img" ] && [ "$old_img" != "$cur_img" ]; then docker rmi "$old_img" >/dev/null 2>&1 || true; fi
  log "반영 끝 ${new:0:7}"
}

log() { echo "$(date '+%F %T') $*"; }

main "$@"
exit
