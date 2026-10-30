#!/usr/bin/env bash
# 이미지 기동 확인(CI "test" 작업, 로컬에서도 같은 명령): ops/smoke.sh [이미지]
#   앱: DEBUG=0·더미 비밀·DB 없이(sqlite) 띄워 /healthz 200, /static/css/gt.css 200
#   worker: import·django.setup()까지 죽지 않는지(REDIS_URL 없이 실행 → 정해 둔 안내로 끝나야 함)
set -euo pipefail
img="${1:-goteacher-app:ci}"
name="gt-smoke-$$"
port="${SMOKE_PORT:-18000}"
env=(-e DEBUG=0 -e SECRET_KEY=smoke-dummy-not-secret -e SITE_URL=http://localhost:8000 -e DATABASE_URL= -e REDIS_URL=)

docker run -d --name "$name" -p "127.0.0.1:$port:8000" "${env[@]}" "$img" >/dev/null
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

check() {  # 경로가 200을 줄 때까지 최대 30초
  local path="$1" code=""
  for _ in $(seq 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: localhost' "http://127.0.0.1:$port$path" || true)
    [ "$code" = 200 ] && { echo "ok $path"; return 0; }
    [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ] || break  # 이미 죽었으면 기다리지 않는다
    sleep 1
  done
  echo "실패 $path (마지막 응답 $code)"; docker logs "$name" 2>&1 | tail -40; return 1
}
check /healthz
check /static/css/gt.css

out=$(docker run --rm "${env[@]}" -w /srv/app "$img" python -m config.worker default hwp 2>&1 || true)
if grep -q "REDIS_URL 환경변수가 필요합니다" <<<"$out" && ! grep -q Traceback <<<"$out"; then
  echo "ok worker import"
else
  echo "실패 worker"; echo "$out" | tail -40; exit 1
fi
