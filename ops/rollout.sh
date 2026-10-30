#!/usr/bin/env bash
# 앱 무중단 교체(G6). deploy.sh가 부른다. 수동: ops/rollout.sh
#   1) 지금 이미지(goteacher-app:latest)로 앱 컨테이너를 하나 더 띄운다(옛 것은 그대로).
#   2) 새 것이 healthy가 되면 Traefik이 같은 라벨의 두 컨테이너를 서비스 gt-staging의 서버로 함께 넣는다
#      (Traefik docker provider는 healthy가 아닌 컨테이너를 넣지 않는다 → 뜨는 중에는 요청이 가지 않음).
#   3) 옛 것을 비운다: /tmp/gt-drain을 만들면 /healthz가 503 → Traefik 서비스 health check(1초, compose.yml)가 뺀다.
#      바로 SIGTERM을 보내면 gunicorn worker가 아직 쥔 소켓으로 받은 연결이 응답 없이 멈춘다(로컬 실측).
#   4) 옛 것을 내린다(처리 중 요청은 gunicorn이 끝내고 닫는다).
#   새 것이 healthy가 안 되면 새 것만 지우고 실패로 끝낸다(옛 것이 계속 응답 — 끊김 없음).
#   이어서 worker를 새 이미지로 다시 띄운다(요청을 받지 않으므로 그냥 교체).
set -Eeuo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
cd "$(dirname "$0")"
svc=goteacher-app

old=$(docker compose ps -q "$svc")
if [ -z "$old" ]; then  # 처음 띄우기(또는 앱이 내려가 있음)
  docker compose up -d --wait "$svc"
else
  n=$(wc -l <<<"$old")
  docker compose up -d --no-deps --no-recreate --scale "$svc=$((n + 1))" "$svc"
  new=$(docker compose ps -q "$svc" | grep -vxF "$old" || true)
  [ -n "$new" ] && [ "$(grep -c . <<<"$new")" -eq 1 ] || { echo "새 컨테이너를 못 찾음"; exit 1; }
  # traefik 라벨이 바뀐 배포(compose.yml 라벨 수정)는 공존시키지 않는다: 설정이 다른 두 컨테이너가 같은 라우터·서비스를
  # 정의하면 Traefik이 둘 다 버려 404가 난다(로컬 실측 약 8초). 새 것이 healthy가 되기 전에 옛 것을 내린다 → 이때만 수 초 끊김.
  tl() { docker inspect -f '{{json .Config.Labels}}' "$1" | jq -cS 'with_entries(select(.key | startswith("traefik.")))'; }
  if [ "$(tl "$new")" != "$(tl "$(head -1 <<<"$old")")" ]; then
    echo "traefik 라벨이 바뀜 — 옛 앱을 먼저 내린다(이번 배포만 수 초 끊김)"
    # shellcheck disable=SC2086
    docker stop -t 30 $old >/dev/null
    # shellcheck disable=SC2086
    docker rm $old >/dev/null
    old=""
  fi
  st=""
  for _ in $(seq 90); do
    st=$(docker inspect -f '{{.State.Health.Status}}' "$new")
    [ "$st" = healthy ] || [ "$st" = unhealthy ] && break
    sleep 1
  done
  if [ "$st" != healthy ]; then
    echo "새 앱 컨테이너가 healthy가 안 됨($st) — 새 것만 지우고 옛 것을 둔다"
    docker logs --tail 30 "$new" 2>&1 || true
    docker rm -f "$new" >/dev/null
    exit 1
  fi
  if [ -n "$old" ]; then
    sleep 3  # Traefik이 health 이벤트를 받아 새 서버를 넣을 여유
    for c in $old; do docker exec "$c" touch /tmp/gt-drain || true; done
    sleep 4  # Traefik health check(1초 간격)가 503을 보고 옛 것을 뺄 여유
    # shellcheck disable=SC2086
    docker stop -t 30 $old >/dev/null
    # shellcheck disable=SC2086
    docker rm $old >/dev/null
  fi
fi
docker compose up -d --wait goteacher-worker
