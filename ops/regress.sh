#!/usr/bin/env bash
# 한글 회귀(F4). 수동: ops/regress.sh quick|full [이미지]
#   표본 100개(ops/regress_pick.py, seed 1030)는 실제 학교 문서라 저장소·GitHub에 두지 않는다 →
#   서버 $GT_SAMPLES(기본 /home/ubuntu/goteacher-samples, 700/600)에만 있다.
#   100개 전체는 서버 실측 약 4분(read 26초+fill 218초)이라 배포 3분 기준(CI 약 1.5분 포함) 안에 못 끝난다. 그래서 둘로 나눈다.
#     quick: deploy.sh가 build 뒤·migrate/교체 전에 부른다. 표본 중 빠른 묶음($GT_SAMPLES/quick.txt)만. 미달이면 반영 안 함.
#     full : deploy.sh가 반영 끝난 뒤 뒤에서 부른다. 100개 전부. 미달이면 경보만(var/alerts.log) — 되돌리기는 사람이.
#            이미 도는 중이면 pending만 남기고 끝낸다 → 도는 쪽이 끝난 뒤 그때의 latest로 한 번 더 돈다.
#   각각 그 이미지로 네트워크 없는 임시 컨테이너에서 hwp.bench read(판별·변환·표 분석)와 fill(1순위 표 채우기→검증
#   →hwp면 HWP 다시 쓰기·무변경 왕복)을 돌리고, 통과 수를 var/regress-baseline-<단계>.json(직전 통과 결과)과 비교한다.
#   기준: 통과 수가 하나라도 줄면 미달, 걸린 시간이 기준의 2배를 넘으면 미달. 통과하면 이번 결과가 새 기준.
#   기준 파일이 없으면 이번 결과를 기준으로 삼고 통과.
#   종료 코드: 0 통과 · 1 미달(사유는 표준출력) · 2 표본 없음(생략) · 3 full이 이미 도는 중(pending)
#   결과는 var/regress/<단계>/last.json(수치)·read.jsonl·fill.jsonl(파일 이름=해시·통과 여부·시간만, 문서 내용 없음).
set -Eeuo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin

mode="${1:?quick|full}"
img="${2:-goteacher-app:latest}"
dir="${GT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
samples="${GT_SAMPLES:-/home/ubuntu/goteacher-samples}"
jobs="${GT_REGRESS_JOBS:-2}"
base="$dir/var/regress-baseline-$mode.json"
out="$dir/var/regress/$mode"
name="goteacher-regress-$mode"
case "$mode" in
  quick) limit="${GT_REGRESS_TIMEOUT:-90}"; mem=1536m; names=/samples/quick.txt
         [ -s "$samples/quick.txt" ] || { echo "회귀 생략: $samples/quick.txt 없음"; exit 2; } ;;
  full)  limit="${GT_REGRESS_TIMEOUT:-900}"; mem=3g; names="" ;;
  *) echo "quick|full"; exit 2 ;;
esac
n=$(find "$samples" -maxdepth 1 -type f \( -iname '*.hwp' -o -iname '*.hwpx' \) 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -gt 0 ] || { echo "회귀 생략: 표본 없음($samples)"; exit 2; }
[ "$mode" = quick ] && n=$(grep -c . "$samples/quick.txt")

mkdir -p "$out"
if [ "$mode" = full ]; then
  exec 8>"$dir/var/regress/full.lock"
  flock -n 8 || { touch "$dir/var/regress/full.pending"; echo "전체 회귀가 이미 도는 중 — 끝난 뒤 latest로 다시 돈다"; exit 3; }
fi

paused=""
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  [ -z "$paused" ] || { docker unpause goteacher-regress-full >/dev/null 2>&1 || true; paused=""; }
}
trap cleanup EXIT

run() {  # 한 번 돌려 $out/last.json을 만든다. 실패하면 사유를 찍고 1
  rm -f "$out"/raw-*.jsonl
  docker rm -f "$name" >/dev/null 2>&1 || true
  local t0 rc=0 sec
  # 관문(quick)이 도는 동안 뒤에서 도는 full을 멈춘다(CPU 2개를 나눠 쓰면 quick 시간이 흔들림)
  if [ "$mode" = quick ] && docker pause goteacher-regress-full >/dev/null 2>&1; then paused=1; fi
  # 공용 서버: 메모리 상한(스왑 없음), CPU 우선순위 낮춤(다른 서비스가 먼저), 네트워크 없음, 표본은 읽기 전용
  t0=$(date +%s)
  docker run --rm --name "$name" --network none -l traefik.enable=false \
    --memory "$mem" --memory-swap "$mem" --cpu-shares 256 --pids-limit 256 \
    --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$samples:/samples:ro" -v "$out:/out" \
    "$img" timeout "$limit" sh -c "
      python -m hwp.bench read /samples ${names:+--names $names} --jobs $jobs --out /out/raw-read.jsonl >/dev/null &&
      python -m hwp.bench fill /samples ${names:+--names $names} --jobs $jobs --out /out/raw-fill.jsonl >/dev/null" || rc=$?
  sec=$(( $(date +%s) - t0 ))
  cleanup
  if [ "$rc" -ne 0 ]; then
    [ "$rc" -eq 124 ] && echo "$mode 회귀 미달: ${limit}초 안에 끝나지 않음" || echo "$mode 회귀 미달: 벤치 실행 실패(코드 $rc)"
    return 1
  fi
  # 필요한 필드만 남긴다(오류 문구·칸 주소는 버림)
  jq -c '{file, kind, ok, tables, ms}' "$out/raw-read.jsonl" > "$out/read.jsonl"
  jq -c '{file, kind, status, hwpx, hwp, roundtrip, total_ms}' "$out/raw-fill.jsonl" > "$out/fill.jsonl"
  rm -f "$out"/raw-*.jsonl
  jq -n --slurpfile r "$out/read.jsonl" --slurpfile f "$out/fill.jsonl" --argjson sec "$sec" --argjson n "$n" \
    --arg at "$(date '+%F %T')" --arg img "$(docker image inspect -f '{{.Id}}' "$img" | cut -c8-19)" '{
      at: $at, image: $img, samples: $n, sec: $sec,
      pass: {
        read_ok:     [$r[] | select(.ok)] | length,
        read_tables: [$r[] | select(.ok and .tables > 0)] | length,
        fill_hwpx:   [$f[] | select(.hwpx == "ok")] | length,
        fill_hwp:    [$f[] | select(.hwp == "ok")] | length,
        roundtrip:   [$f[] | select(.roundtrip == "ok")] | length
      }}' > "$out/last.json"
}

judge() {  # last.json을 기준과 비교. 통과면 기준 갱신
  echo "$mode 회귀 결과: $(jq -c '{sec, pass}' "$out/last.json")"
  if [ ! -s "$base" ]; then
    cp "$out/last.json" "$base"
    echo "기준 없음 → 이번 결과를 기준으로 삼음"
    return 0
  fi
  local bad
  bad=$(jq -r --slurpfile b "$base" '
    . as $n | $b[0] as $b |
    ([$b.pass | to_entries[] | select(($n.pass[.key] // 0) < .value) | "\(.key) \(.value)→\($n.pass[.key] // 0)"]
     + (if $n.samples != $b.samples then ["표본 수 \($b.samples)→\($n.samples) (표본을 바꿨으면 기준 파일을 지울 것)"] else [] end)
     + (if $n.sec > 2 * $b.sec then ["시간 \($b.sec)초→\($n.sec)초(2배 초과)"] else [] end)) | join(", ")' "$out/last.json")
  if [ -n "$bad" ]; then
    echo "$mode 회귀 미달: $bad"
    return 1
  fi
  cp "$out/last.json" "$base"
  echo "$mode 회귀 통과(기준 갱신)"
}

if [ "$mode" = quick ]; then
  run && judge
  exit
fi
# full: 끝난 뒤 pending이 있으면(그사이 새 배포) 그때의 latest로 다시
rc=0
while :; do
  rm -f "$dir/var/regress/full.pending"
  if run && judge; then rc=0; else rc=1; fi
  [ -e "$dir/var/regress/full.pending" ] || break
  img=goteacher-app:latest
  [ "$rc" -eq 0 ] || echo "(위 미달은 직전 이미지 것 — 이어서 latest로 다시 돈다)"
done
exit "$rc"
