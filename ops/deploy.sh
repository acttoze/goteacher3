#!/usr/bin/env bash
# 스테이징 자동 배포(B2). 서버 crontab에서 1분마다 실행한다.
#   origin/main이 마지막 배포와 다르고, 그 커밋의 GitHub 체크 "test"(.github/workflows/ci.yml)가
#   success일 때만 반영한다: reset → build → 한글 회귀(regress.sh, F4) → migrate → up.
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
  # 한글 회귀 관문(F4): 교체 전에 새 이미지로 표본 중 빠른 묶음 read·fill(regress.sh quick, 약 1분 안).
  # 미달이면 반영하지 않는다(새 이미지를 버리고 latest를 직전 이미지로 — 돌고 있는 컨테이너는 그대로).
  # 표본이 없으면 경보만 남기고 진행. 100개 전체는 반영 뒤 뒤에서 돈다(아래 full).
  local rg rc=0
  rg=$(bash ./regress.sh quick goteacher-app:latest) || rc=$?
  log "$rg"
  if [ "$rc" -eq 1 ]; then
    local bad_img
    bad_img=$(docker image inspect -f '{{.Id}}' goteacher-app:latest)
    [ -n "$old_img" ] && [ "$bad_img" != "$old_img" ] && { docker tag "$old_img" goteacher-app:latest; docker rmi "$bad_img" >/dev/null 2>&1 || true; }
    trap - ERR
    echo "$new" > "$var/deploy-skip"
    alert "한글 회귀 미달 ${new:0:7} — 반영 안 함" "$rg"
    log "한글 회귀 미달 ${new:0:7} — 반영 안 함(var/deploy-skip, var/regress/)"
    exit 0
  elif [ "$rc" -ne 0 ]; then
    alert "한글 회귀 생략 ${new:0:7}" "$rg"
  fi
  # 임시 컨테이너가 Traefik 백엔드로 잡히지 않게 라벨을 끈다
  docker compose run --rm -T -l traefik.enable=false goteacher-app python app/manage.py migrate --noinput
  # 앱은 새 컨테이너가 healthy가 된 뒤 옛 것을 내린다(무중단, rollout.sh). worker도 같은 이미지로 새로 띄운다.
  # 못 뜨면 직전 이미지를 latest로 다시 붙여 그것으로 되돌린다(앱이 못 떴으면 옛 앱 컨테이너가 계속 응답 중).
  # (migrate는 이미 적용됐다 — 되돌릴 수 없는 마이그레이션이면 직전 코드가 새 스키마로 뜬다. 스테이징 한정 허용)
  if ! bash ./rollout.sh; then
    if [ -n "$old_img" ]; then
      log "기동 실패 ${new:0:7} — 직전 이미지로 되돌림"
      local bad_img
      bad_img=$(docker image inspect -f '{{.Id}}' goteacher-app:latest)
      docker tag "$old_img" goteacher-app:latest
      bash ./rollout.sh || log "되돌리기도 실패 — 수동 확인 필요"
      [ "$bad_img" != "$old_img" ] && docker rmi "$bad_img" >/dev/null 2>&1 || true
    fi
    false  # ERR 트랩: deploy-skip에 기록, deployed는 그대로(직전 커밋)
  fi
  echo "$new" > "$var/deployed"
  trap - ERR
  # 이전 이미지는 이 프로젝트 것만 지운다(prune 쓰지 않음)
  local cur_img
  cur_img=$(docker image inspect -f '{{.Id}}' goteacher-app:latest)
  if [ -n "$old_img" ] && [ "$old_img" != "$cur_img" ]; then docker rmi "$old_img" >/dev/null 2>&1 || true; fi
  log "반영 끝 ${new:0:7}"
  # 한글 전체 회귀(F4, 100개 — 수십 분): 뒤에서 돌리고 미달이면 경보만. 배포 잠금(fd 9)은 넘기지 않는다
  (
    exec 9>&-
    r=$(bash ./regress.sh full goteacher-app:latest 2>&1) && c=0 || c=$?
    log "$r"
    [ "$c" -eq 1 ] && alert "한글 전체 회귀 미달 ${new:0:7} — 운영 중(되돌리기 판단 필요)" "$r"
    true
  ) >> "$var/regress.log" 2>&1 < /dev/null &
}

log() { echo "$(date '+%F %T') $*"; }
# monitor.sh notify()와 같은 형식으로 var/alerts.log에 남긴다($1 제목, $2 본문)
alert() { printf '%s [%s]\n%s\n\n' "$(date '+%F %T')" "$1" "$2" >> "${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}/var/alerts.log"; }

main "$@"
exit
