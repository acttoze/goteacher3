#!/usr/bin/env bash
# G7 운영 도메인 전환(t.goteacher.xyz 동시 사용 → goteacher.xyz). 서버에서 실행한다. 절차·사람 할 일은 meeting/39_cutover_runbook.md.
#   기본은 마른 실행: 읽기 전용 점검만 하고 할 일을 출력한다(아무것도 바꾸지 않음).
#   실제 실행: ops/cutover.sh --execute   (터미널에서 확인 문구 "goteacher.xyz 전환"을 직접 입력해야 진행)
#   되돌리기:  ops/cutover.sh --rollback <ops/.env.before-g7-… 파일>   (확인 문구 "전환 되돌리기")
# 하는 일(--execute): 0 점검 → 1 운영 DB 백업 → 2 동기화 최종 대조 2번·정지 → 3 계정 차이 관문 → 4 .env(SITE_URL·HSTS·동시 사용 끔)
#   → 5 운영 라우터 링크(compose.override.yml → compose.prod.yml) + 앱 교체(rollout.sh, 라벨 변경이라 수 초 끊김) → 6 확인.
# 하지 않는 일: 구 사이트 쓰기 중지(사람 승인 뒤 구 서버 data/readonly.on — 0단계가 있는지만 본다), 공용 Traefik·다른 컨테이너 변경,
#   구 컨테이너 라벨 변경(old.goteacher.xyz는 이쪽 라벨이 구 서비스로 잇는다), 백업 삭제, 전역 prune.
# 비밀 값은 출력하지 않는다(.env는 키 이름과 공개 값 SITE_URL만 보인다).
set -Eeuo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

dir="${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
ops="$dir/ops"
old_dir="${GT_OLD_DIR:-/home/ubuntu/prod/goteacher_prod}"
prod=goteacher.xyz
peers=(shamar-prod-web lecture2-web memos)  # 전환 전후로 그대로인지 볼 다른 서비스 3개(이름만 읽는다)
mode="${1:---dry-run}"
ts=$(date +%Y%m%d-%H%M%S)

say() { echo "$(date '+%T') $*"; }
die() { echo "멈춤: $*" >&2; exit 1; }
confirm() {  # $1 문구
  [ -t 0 ] || die "확인 문구는 터미널에서 직접 입력해야 한다(ssh -t)"
  local a; read -r -p "계속하려면 '$1' 을(를) 그대로 입력: " a
  [ "$a" = "$1" ] || die "확인 문구가 다름"
}
envget() { sed -n "s/^$1=//p" "$ops/.env" | tail -1; }
envset() {  # $1 키 $2 값(값은 출력하지 않는다). 있으면 바꾸고 없으면 붙인다
  local tmp; tmp=$(mktemp "$ops/.env.tmp.XXXX"); chmod 600 "$tmp"
  awk -v k="$1" -v v="$2" 'BEGIN{d=0} $0 ~ "^"k"=" {print k"="v; d=1; next} {print} END{if(!d) print k"="v}' "$ops/.env" > "$tmp"
  mv "$tmp" "$ops/.env"
}
code() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$@" || true; }
peer_state() {  # 다른 서비스: 컨테이너 ID·시작 시각·첫 Host로 받은 응답 코드
  local c h
  for c in "${peers[@]}"; do
    h=$(docker inspect -f '{{json .Config.Labels}}' "$c" 2>/dev/null | jq -r '[to_entries[] | select(.key|test("^traefik.http.routers.[^.]+.rule$")) | .value | capture("Host\\(`(?<h>[^`]+)`").h][0] // ""')
    echo "$c $(docker inspect -f '{{.Id}} {{.State.StartedAt}}' "$c" 2>/dev/null | cut -c1-12,65-) ${h:-?} $( [ -n "$h" ] && code --resolve "$h:443:127.0.0.1" "https://$h/" )"
  done
}

cd "$ops"
[ -f compose.yml ] && [ -f compose.prod.yml ] || die "ops/compose.yml·compose.prod.yml이 없음"

# ---------------------------------------------------------------- 되돌리기
if [ "$mode" = --rollback ]; then
  bak="${2:-}"; [ -f "$bak" ] || die "되돌릴 .env 백업 파일을 주세요(ops/.env.before-g7-…)"
  say "되돌리기: 운영 라우터 링크 삭제 → .env 복원 → 앱 교체 → 동기화 다시 켬"
  confirm "전환 되돌리기"
  exec 9>"$dir/var/deploy.lock"; flock -w 180 9 || die "배포 잠금을 못 얻음"
  rm -f compose.override.yml
  cp -p "$bak" .env && chmod 600 .env
  bash ./rollout.sh
  [ -n "$(envget LEGACY_DB_URL)" ] && docker compose up -d --no-deps goteacher-legacy-sync
  say "goteacher.xyz → 구 컨테이너: $(code --resolve "$prod:443:127.0.0.1" "https://$prod/") · t: $(code --resolve "t.$prod:443:127.0.0.1" "https://t.$prod/healthz")"
  say "남은 사람 일: 구 사이트 쓰기를 다시 열려면 구 서버에서 rm $old_dir/data/readonly.on"
  exit 0
fi
case "$mode" in --dry-run|--execute) ;; *) die "사용: $0 [--dry-run|--execute|--rollback <파일>]" ;; esac

# ---------------------------------------------------------------- 0. 점검(읽기 전용)
say "0. 점검"
fail=()
[ "$(stat -c %a .env)" = 600 ] || fail+=(".env 권한이 600이 아님")
[ ! -e compose.override.yml ] || fail+=("compose.override.yml이 이미 있음(이미 전환했나?)")
[ "$(envget SITE_URL)" = "https://t.$prod" ] || fail+=("SITE_URL이 https://t.$prod 가 아님")
[ -f "$old_dir/extend/zz_readonly.extend.php" ] || fail+=("구 사이트 쓰기 중지 코드가 배포되지 않음(runbook 1단계)")
[ -f "$old_dir/data/readonly.on" ] || fail+=("구 사이트 쓰기 중지 플래그 없음 — 🙋 승인 뒤 touch $old_dir/data/readonly.on")
docker inspect goteacher-prod >/dev/null 2>&1 || fail+=("구 컨테이너 goteacher-prod 없음(old.$prod가 이을 서비스)")
[ -n "$(docker compose ps -q goteacher-legacy-sync)" ] || fail+=("동기화가 돌고 있지 않음")
free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9); [ "$free_gb" -ge 20 ] || fail+=("디스크 여유 ${free_gb}GB < 20GB")
[ "$(code --resolve "t.$prod:443:127.0.0.1" "https://t.$prod/healthz")" = 200 ] || fail+=("스테이징 healthz가 200이 아님")
say "   디스크 여유 ${free_gb}GB · 최근 동기화: $(docker compose logs --since 2m --no-log-prefix goteacher-legacy-sync 2>/dev/null | grep '대조' | tail -1)"
say "   다른 서비스(전):"; peer_state | sed 's/^/     /' | tee "/tmp/gt-cutover-peers-$ts"
if [ "$mode" = --dry-run ]; then
  [ ${#fail[@]} -eq 0 ] && say "   점검 통과" || printf '   걸림: %s\n' "${fail[@]}"
  cat <<EOF
마른 실행 — 바꾼 것 없음. --execute면 이 순서로 한다:
  1. var/backups/before-g7-$ts.dump (pg_dump, 지우지 않음)
  2. 동기화 정지 → legacy_sync --once 2번(2번째는 바뀐 행 0·실패 0이어야) → 컨테이너 제거
  3. legacy_gap.py: GAP_TOTAL=0이어야(아니면 멈춤 → runbook 3-b)
  4. .env 백업 ops/.env.before-g7-$ts → SITE_URL=https://$prod · SECURE_HSTS_SECONDS=31536000 · LEGACY_READONLY= · LEGACY_DB_URL= · LEGACY_SITE_URL=https://old.$prod
  5. ln -s compose.prod.yml compose.override.yml → bash rollout.sh (라벨 변경: 수 초 끊김)
  6. https://$prod/healthz 200 · t.$prod → $prod 이동 · old.$prod 응답 · 접근 로그 0줄 · 다른 서비스 그대로 · GT_DRY=1 monitor.sh
되돌리기: $0 --rollback ops/.env.before-g7-$ts
EOF
  rm -f "/tmp/gt-cutover-peers-$ts"
  exit 0
fi
[ ${#fail[@]} -eq 0 ] || { printf '걸림: %s\n' "${fail[@]}"; die "점검 실패"; }
confirm "goteacher.xyz 전환"
started=$(date +%s); since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
exec 9>"$dir/var/deploy.lock"; flock -w 180 9 || die "배포 잠금을 못 얻음(배포 중)"  # 이 사이 cron 배포가 끼어들지 않게

# ---------------------------------------------------------------- 1. 백업
say "1. 운영 DB 백업"
umask 077
b="$dir/var/backups/before-g7-$ts.dump"
docker compose exec -T db pg_dump -U goteacher -d goteacher -Fc > "$b.part" && [ -s "$b.part" ] || { rm -f "$b.part"; die "백업 실패"; }
mv "$b.part" "$b"; docker compose exec -T db pg_restore -l < "$b" >/dev/null || die "백업 목록 읽기 실패"
say "   $(basename "$b") $(du -h "$b" | cut -f1)"

# ---------------------------------------------------------------- 2. 동기화 최종 반영·정지
say "2. 동기화 최종 대조"
docker compose stop goteacher-legacy-sync >/dev/null
for i in 1 2; do
  out=$(docker compose run --rm --no-deps -T -l traefik.enable=false goteacher-legacy-sync python manage.py legacy_sync --once 2>&1 | tail -1)
  say "   $i번째: $out"
done
grep -q '바뀐 행 0 · 반영 0 · 실패 0' <<<"$out" || die "마지막 대조에 바뀐 행·실패가 남음(구 사이트 쓰기 중지 확인). 동기화는 멈춘 상태 — 다시 켜기: docker compose up -d --no-deps goteacher-legacy-sync"

# ---------------------------------------------------------------- 3. 계정 차이 관문
say "3. 이관 밖 회원 계정 차이"
gap=$(docker compose run --rm --no-deps -T -l traefik.enable=false goteacher-legacy-sync python manage.py shell -v0 < legacy_gap.py 2>&1)
sed 's/^/   /' <<<"$gap"
grep -qx 'GAP_TOTAL=0' <<<"$gap" || die "계정 차이가 있음 → runbook 3-b(최신 덤프로 import_legacy) 뒤 다시"
docker compose rm -sf goteacher-legacy-sync >/dev/null

# ---------------------------------------------------------------- 4. .env
say "4. .env(SITE_URL·HSTS·동시 사용 끔)"
cp -p .env ".env.before-g7-$ts"; chmod 600 ".env.before-g7-$ts"
envset SITE_URL "https://$prod"
envset SECURE_HSTS_SECONDS 31536000
envset LEGACY_READONLY ""
envset LEGACY_DB_URL ""
envset LEGACY_SITE_URL "https://old.$prod"
say "   백업 ops/.env.before-g7-$ts · SITE_URL=$(envget SITE_URL)"

# ---------------------------------------------------------------- 5. 운영 라우터
say "5. 운영 라우터 켜기 + 앱 교체"
ln -s compose.prod.yml compose.override.yml
bash ./rollout.sh

# ---------------------------------------------------------------- 6. 확인
say "6. 확인"
bad=0
chk() { local d="$1" want="$2" got="$3"; if [[ "$got" =~ ^($want)$ ]]; then say "   ok  $d $got"; else say "   실패 $d $got (기대 $want)"; bad=1; fi; }
for _ in $(seq 20); do [ "$(curl -s -m 5 --resolve "$prod:443:127.0.0.1" "https://$prod/healthz")" = ok ] && break; sleep 2; done
chk "https://$prod/healthz" 200 "$(code --resolve "$prod:443:127.0.0.1" "https://$prod/healthz")"
chk "https://$prod/ (curl -I)" '200|302' "$(curl -sI -o /dev/null -m 10 -w '%{http_code}' --resolve "$prod:443:127.0.0.1" "https://$prod/")"
chk "http://$prod → https" '301|308' "$(code --resolve "$prod:80:127.0.0.1" "http://$prod/")"
chk "t.$prod → $prod" "https://$prod/healthz" "$(curl -s -o /dev/null -m 10 -w '%{redirect_url}' --resolve "t.$prod:443:127.0.0.1" "https://t.$prod/healthz")"
chk "old.$prod(인증서 발급 전이면 -k)" 200 "$(code -k --resolve "old.$prod:443:127.0.0.1" "https://old.$prod/")"
chk "구글 콜백 주소 응답(400·302 = 앱이 받음)" '302|400' "$(code --resolve "$prod:443:127.0.0.1" "https://$prod/auth/google/callback")"
n=$(docker logs --since "$since" traefik 2>&1 | grep -cE '"gt-(prod|t|old)[a-z0-9-]*@docker"' || true)
chk "공용 Traefik 접근 로그의 고티처 운영 라우터 줄" 0 "$n"
say "   다른 서비스(후):"; peer_state | sed 's/^/     /' > "/tmp/gt-cutover-peers-$ts.after"; cat "/tmp/gt-cutover-peers-$ts.after"
if cmp -s "/tmp/gt-cutover-peers-$ts" "/tmp/gt-cutover-peers-$ts.after"; then say "   ok  다른 서비스 3개 그대로"; else say "   실패 다른 서비스 상태가 다름"; bad=1; fi
rm -f "/tmp/gt-cutover-peers-$ts" "/tmp/gt-cutover-peers-$ts.after"
say "   감시: $(GT_DRY=1 bash ./monitor.sh | head -3 | tr '\n' ' ')"
say "끝 $(( $(date +%s) - started ))초 · 되돌리기: $0 --rollback ops/.env.before-g7-$ts"
[ "$bad" -eq 0 ] || { say "확인 실패가 있음 — 되돌릴지 판단"; exit 1; }
