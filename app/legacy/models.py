"""P3 구버전(goteacher.xyz) 이관 기록. 다시 돌릴 때 이미 넣은 것을 건너뛰는 데만 쓴다(화면에서 읽지 않는다).

kind: user(구 mb_no) · school(학교 코드) · member(mb_no) · event·attendance(일정 wr_id) · comment(댓글 wr_id) ·
file(bo_table:wr_id:bf_no) · notice(게시글 wr_id). 복사로 생긴 중복 일정은 여러 wr_id가 같은 새 pk를 가리킨다.
"""
from django.db import models


class LegacyMap(models.Model):
    kind = models.CharField(max_length=12)
    legacy_id = models.CharField(max_length=64)
    obj_id = models.BigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "legacy_id"], name="uniq_legacy_map")]

    def __str__(self):
        return f"{self.kind}:{self.legacy_id}"
