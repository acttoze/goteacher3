#!/usr/bin/env bash
# 고티처 백업(B2). 서버 crontab에서 매일 실행. var/backups/에 종류마다 최근 7개만 둔다(권한 600).
#   DB: pg_dump custom(-Fc, 압축) goteacher-*.dump
#   미디어: 볼륨 goteacher_media(올린 한글 원본·변환 결과)를 tar.gz로 goteacher-media-*.tar.gz
#   복구 시험: ops/restore_test.sh [DB 파일]  (미디어는 최신 tar.gz를 함께 확인)
#   지우는 것은 새 백업이 성공한 뒤, 같은 종류의 오래된 파일만.
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

# 미디어: 앱 이미지로 볼륨을 읽기 전용으로 붙여 묶는다(네트워크 없음, 새 이미지 내려받기 없음).
# 볼륨 안 파일은 gt 소유 600/700이라 root로 읽는다.
m="$out/goteacher-media-$(date +%F_%H%M).tar.gz"
docker run --rm --network none --user 0 -v goteacher_media:/m:ro --entrypoint tar goteacher-app:latest \
  -czf - -C /m . > "$m.part"
tar -tzf "$m.part" >/dev/null || { echo "$(date '+%F %T') 미디어 백업 실패: 묶음 손상"; rm -f "$m.part"; exit 1; }
mv "$m.part" "$m"
ls -1t "$out"/goteacher-media-*.tar.gz | tail -n +8 | xargs -r rm -f
echo "$(date '+%F %T') 백업 $(basename "$m") $(du -h "$m" | cut -f1), 파일 $(tar -tzf "$m" | grep -vc '/$'), 보관 $(ls "$out"/goteacher-media-*.tar.gz | wc -l)개"
