#!/usr/bin/env bash
# 고티처 DB 백업(B2). 서버 crontab에서 매일 실행. var/backups/에 최근 7개만 둔다.
#   형식: pg_dump custom(-Fc, 압축). 복구 시험: ops/restore_test.sh [파일]
#   지우는 것은 새 백업이 성공한 뒤, 이 디렉터리의 goteacher-*.dump 중 오래된 것만.
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
umask 077

dir="${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
out="$dir/var/backups"
mkdir -p "$out"
f="$out/goteacher-$(date +%F_%H%M).dump"

cd "$dir/ops"
docker compose exec -T db pg_dump -U goteacher -d goteacher -Fc > "$f.part"
[ -s "$f.part" ] || { echo "$(date '+%F %T') 백업 실패: 빈 파일"; rm -f "$f.part"; exit 1; }
mv "$f.part" "$f"
ls -1t "$out"/goteacher-*.dump | tail -n +8 | xargs -r rm -f
echo "$(date '+%F %T') 백업 $(basename "$f") $(du -h "$f" | cut -f1), 보관 $(ls "$out"/goteacher-*.dump | wc -l)개"
