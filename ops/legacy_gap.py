# G7 전환 관문(읽기 전용): 동기화가 다루지 않는 '이관 밖 학교 회원' 계정 차이를 센다. 숫자만 출력한다.
# legacy_sync는 이관 학교 회원만 본다. 이관 밖 회원(D10, 계정만)은 11/16 재적재 이후 새 가입·비밀번호 변경·탈퇴가 반영되지 않는다.
# 실행: cd ops && docker compose exec -T goteacher-legacy-sync python manage.py shell -v0 < legacy_gap.py
# 마지막 줄 GAP_TOTAL=0이면 import_legacy 재적재가 필요 없다. 0이 아니면 runbook(meeting/39) 3-b.
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.functions import Lower

from legacy.importer import _gone, _system
from legacy.models import LegacyMap
from legacy.rules import password_of
from legacy.sync import MySQL

src = MySQL.connect(settings.LEGACY_DB_URL, settings.LEGACY_FILES_DIR)
try:
    rows = src.select("SELECT CAST(m.mb_no AS CHAR) AS no, m.mb_id, m.mb_password, m.mb_1, m.mb_leave_date, "
                      "m.mb_intercept_date, m.mb_level FROM g5_member m")
finally:
    src.close()
codes = set(LegacyMap.objects.filter(kind="school").values_list("legacy_id", flat=True))
umap = dict(LegacyMap.objects.filter(kind="user").values_list("legacy_id", "obj_id"))
U = get_user_model()
users = {u.pk: u for u in U.objects.filter(pk__in=umap.values()).only("pk", "password", "is_active")}
taken = set(U.objects.annotate(u=Lower("username")).values_list("u", flat=True))
missing = collide = stale = left = 0
for r in rows:
    if _system(r):
        continue
    u = users.get(int(umap[r["no"]])) if r["no"] in umap else None
    if _gone(r):
        left += bool(u and u.is_active and r["mb_1"] not in codes)  # 이관 밖 회원이 구에서 탈퇴·차단됐는데 새 계정은 활성
    elif u is None:
        missing += 1
        collide += r["mb_id"].lower() in taken
    elif r["mb_1"] not in codes and u.password.startswith("g5pbkdf2$") and u.password != password_of(r["mb_password"]):
        stale += 1  # 새 쪽에 아직 로그인 안 해 옛 해시인데, 구에서 비밀번호를 바꿈
print(f"구 회원 {len(rows)} · 이관 학교 {len(codes)}곳")
print(f"새 쪽 계정 없음 {missing}(그중 같은 아이디 이미 있음 {collide}) · 비밀번호 뒤처짐 {stale} · 탈퇴 미반영 {left}")
print(f"GAP_TOTAL={missing + stale + left}")
