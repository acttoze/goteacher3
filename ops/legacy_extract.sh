#!/usr/bin/env bash
# 이관 M1 — 구버전 goteacher.xyz 운영 데이터 추출(읽기 전용). 맥에서 실행한다.
#   ops/legacy_extract.sh            → ~/.config/troad/legacy/<YYYYMMDD-HHMM>/
# 운영 DB에는 SELECT만 한다(한 세션, 일관된 스냅샷, 잠금 없음).
# 비밀번호는 서버 dbconfig.php에서 읽어 파이프로 컨테이너에 넘긴다(인자·파일·출력에 남지 않음).
# 서버에는 임시 파일을 만들지 않는다. 결과물은 저장소 밖에 두고 커밋하지 않는다.
set -euo pipefail

HOST=oci_tailscale
CFG=/home/ubuntu/prod/goteacher_prod/data/dbconfig.php
FILES=/home/ubuntu/prod/goteacher_prod/data/file
OUT=${OUT:-$HOME/.config/troad/legacy/$(date +%Y%m%d-%H%M)}
SECONDS=0
umask 077
mkdir -p "$OUT/files"; chmod 700 "$HOME/.config/troad/legacy" "$OUT"

# 표준입력의 SQL을 운영 DB에서 실행해 탭 구분으로 돌려준다.
run_sql() {
  ssh "$HOST" "{ sed -n \"s/^define('G5_MYSQL_PASSWORD', '\\(.*\\)');.*/\\1/p\" $CFG | head -1; cat; } \
    | docker exec -i mysql sh -c 'read -r MYSQL_PWD; export MYSQL_PWD; exec mysql -ugoteacher --default-character-set=utf8mb4 --quick --raw -N -B goteacher'"
}

# 1) 칸 목록(전체 칸을 받는 표) → JSON_OBJECT 식. 날짜 칸은 0000-00-00 그대로 남기려고 문자로 바꾼다.
cols_sql="SELECT table_name, GROUP_CONCAT(CONCAT('''', column_name, ''',', IF(data_type IN ('date','datetime','timestamp'), CONCAT('CAST(\`', column_name, '\` AS CHAR)'), CONCAT('\`', column_name, '\`'))) ORDER BY ordinal_position SEPARATOR ',') FROM information_schema.columns WHERE table_schema='goteacher' AND table_name IN ('contact_group','g5_write_goteacher','g5_write_free_school','g5_board_file') GROUP BY table_name;"
while IFS=$'\t' read -r t expr; do printf -v "J_$t" 'JSON_OBJECT(%s)' "$expr"; done < <(printf '%s\n' "SET SESSION group_concat_max_len=1000000;" "$cols_sql" | run_sql)

# 2) g5_member는 정해진 칸만(전화·메일·FCM·세션·mb_ch 등 제외)
M="mb_no mb_id mb_password mb_name mb_datetime mb_today_login mb_leave_date mb_intercept_date mb_level mb_1 mb_2 mb_3 mb_4 mb_5 mb_addr2"
mexpr=""; for c in $M; do
  case $c in mb_datetime|mb_today_login) v="CAST($c AS CHAR)";; *) v=$c;; esac
  mexpr+="${mexpr:+,}'$c',$v"; done

SCHOOL_WHERE="id IN (SELECT mb_1 FROM g5_member)"
FILE_WHERE="bo_table IN ('goteacher','free_school')"

# 3) 한 세션·한 스냅샷에서 행 수와 모든 표를 받는다. 줄 앞에 표 이름을 붙이고 맥에서 나눈다.
run_sql > "$OUT/.raw.tsv" <<SQL
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY;
SELECT '_count', JSON_OBJECT(
  'g5_member',(SELECT COUNT(*) FROM g5_member),
  'contact_group',(SELECT COUNT(*) FROM contact_group),
  'school',(SELECT COUNT(*) FROM school WHERE $SCHOOL_WHERE),
  'g5_write_goteacher',(SELECT COUNT(*) FROM g5_write_goteacher),
  'g5_write_free_school',(SELECT COUNT(*) FROM g5_write_free_school),
  'g5_write_go_admin',(SELECT COUNT(*) FROM g5_write_go_admin),
  'g5_write_channel',(SELECT COUNT(*) FROM g5_write_channel),
  'g5_board_file',(SELECT COUNT(*) FROM g5_board_file WHERE $FILE_WHERE));
SELECT 'g5_member', JSON_OBJECT($mexpr) FROM g5_member ORDER BY mb_no;
SELECT 'contact_group', ${J_contact_group} FROM contact_group ORDER BY group_id;
SELECT 'school', JSON_OBJECT('id',id,'code_sido',code_sido,'name',name,'grade',grade) FROM school WHERE $SCHOOL_WHERE ORDER BY id;
SELECT 'g5_write_goteacher', ${J_g5_write_goteacher} FROM g5_write_goteacher ORDER BY wr_id;
SELECT 'g5_write_free_school', ${J_g5_write_free_school} FROM g5_write_free_school ORDER BY wr_id;
SELECT 'g5_write_go_admin', JSON_OBJECT('wr_id',wr_id,'mb_id',mb_id,'wr_3',wr_3,'wr_is_comment',wr_is_comment) FROM g5_write_go_admin ORDER BY wr_id;
SELECT 'g5_write_channel', JSON_OBJECT('wr_id',wr_id,'wr_subject',wr_subject,'wr_3',wr_3) FROM g5_write_channel ORDER BY wr_id;
SELECT 'g5_board_file', ${J_g5_board_file} FROM g5_board_file WHERE $FILE_WHERE ORDER BY bo_table, wr_id, bf_no;
COMMIT;
SQL
awk -F'\t' -v d="$OUT" '{ f=d"/"$1".jsonl"; sub(/^[^\t]*\t/, ""); print > f }' "$OUT/.raw.tsv"
rm -f "$OUT/.raw.tsv"
mv "$OUT/_count.jsonl" "$OUT/db_counts.json"

# 4) 첨부: g5_board_file 목록에 있는 파일만. 디스크에 없는 것은 세어 따로 적는다.
python3 -I -c '
import json, sys
for line in open(sys.argv[1], encoding="utf-8"):
    r = json.loads(line)
    if r["bf_file"]: print(r["bo_table"] + "/" + r["bf_file"])
' "$OUT/g5_board_file.jsonl" | sort -u > "$OUT/files_wanted.txt"
ssh "$HOST" "cd $FILES && while IFS= read -r f; do [ -f \"\$f\" ] || printf '%s\n' \"\$f\"; done" \
  < "$OUT/files_wanted.txt" > "$OUT/files_missing.txt"
grep -vxF -f "$OUT/files_missing.txt" "$OUT/files_wanted.txt" > "$OUT/.files_present.txt" || true
rsync -a --files-from="$OUT/.files_present.txt" "$HOST:$FILES/" "$OUT/files/"
rm -f "$OUT/.files_present.txt"

# 5) 행 수 대조·요약·manifest
python3 -I - "$OUT" <<'PY'
import json, os, sys
d = sys.argv[1]
want = json.load(open(f"{d}/db_counts.json"))
got, ok = {}, True
for t, n in want.items():
    p = f"{d}/{t}.jsonl"
    got[t] = sum(1 for _ in open(p, encoding="utf-8")) if os.path.exists(p) else 0
    for line in open(p, encoding="utf-8") if os.path.exists(p) else []: json.loads(line)
    ok &= got[t] == n
    print(f"{t:22} db={n:>7} file={got[t]:>7} {'OK' if got[t]==n else 'MISMATCH'}")
wanted = sum(1 for _ in open(f"{d}/files_wanted.txt"))
missing = sum(1 for _ in open(f"{d}/files_missing.txt"))
fs = [os.path.join(r, f) for r, _, fl in os.walk(f"{d}/files") for f in fl]
size = sum(os.path.getsize(f) for f in fs)
print(f"첨부 목록 {wanted} / 받음 {len(fs)} / 디스크에 없음 {missing} / {size/1e6:.1f} MB")
json.dump({"rows_db": want, "rows_file": got, "rows_match": ok, "files_wanted": wanted,
           "files_received": len(fs), "files_missing": missing, "files_bytes": size},
          open(f"{d}/summary.json", "w"), ensure_ascii=False, indent=1)
sys.exit(0 if ok and len(fs) == wanted - missing else 1)
PY
( cd "$OUT" && find . -type f ! -name manifest.sha256 -print0 | sort -z | xargs -0 shasum -a 256 > manifest.sha256 )
find "$OUT" -type f -exec chmod 600 {} +; find "$OUT" -type d -exec chmod 700 {} +
echo "저장: $OUT  ($((SECONDS/60))분 $((SECONDS%60))초)"
