#!/usr/bin/env bash
# 백업 복구 시험(B2·G6). 운영 db는 건드리지 않는다:
#   네트워크 없는 임시 PostgreSQL 17 컨테이너에 백업을 풀고, 표마다 행 수를 운영 db와 비교한 뒤 지운다.
#   사용: ops/restore_test.sh [백업 파일]   (생략하면 var/backups의 최신 파일)
#   주의: 백업 이후 운영 db에 쓰기가 있었다면 행 수가 다를 수 있다(직후 실행 권장).
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

dir="${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
f="${1:-$(ls -1t "$dir"/var/backups/goteacher-*.dump | head -1)}"
name=goteacher-restoretest
echo "복구 시험: $(basename "$f")"

docker rm -f "$name" >/dev/null 2>&1 || true
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --name "$name" --network none --memory 256m \
  -e POSTGRES_USER=goteacher -e POSTGRES_DB=goteacher -e POSTGRES_HOST_AUTH_METHOD=trust \
  postgres:17-alpine >/dev/null
for _ in $(seq 30); do docker exec "$name" pg_isready -U goteacher -d goteacher -q 2>/dev/null && break; sleep 1; done
sleep 2  # 초기화 스크립트 뒤 재시작을 기다린다
docker exec "$name" pg_isready -U goteacher -d goteacher -q

docker exec -i "$name" pg_restore -U goteacher -d goteacher --no-owner --exit-on-error < "$f"

q="select string_agg(format('%s=%s', relname, n), ' ' order by relname) from (
  select c.relname, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', c.relname), false, true, '')))[1]::text::int n
  from pg_class c join pg_namespace s on s.oid=c.relnamespace where s.nspname='public' and c.relkind='r') t"
restored=$(docker exec "$name" psql -U goteacher -d goteacher -tAc "$q")
live=$(cd "$dir/ops" && docker compose exec -T db psql -U goteacher -d goteacher -tAc "$q")
echo "복구본: $restored"
if [ "$restored" = "$live" ] && [ -n "$restored" ]; then
  echo "결과: 일치(표 $(echo "$restored" | wc -w)개)"
else
  echo "운영본: $live"
  echo "결과: 불일치"; exit 1
fi
