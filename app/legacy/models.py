"""P3 구버전(goteacher.xyz) 이관 기록. 다시 돌릴 때 이미 넣은 것을 건너뛰는 데만 쓴다(화면에서 읽지 않는다).

kind: user(구 mb_no) · school(학교 코드) · member(mb_no) · event·attendance(일정 wr_id) · comment(댓글 wr_id) ·
file(bo_table:wr_id:bf_no) · notice(게시글 wr_id). 복사로 생긴 중복 일정은 여러 wr_id가 같은 새 pk를 가리킨다.
여러 날 복무는 첫날이 attendance(wr_id), 나머지 날이 attendance(wr_id:1·2…).
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


class LegacyPrint(models.Model):
    """V2 동기화가 마지막으로 반영한 구 행의 지문(CRC32). 다음 대조에서 바뀐 행·사라진 행을 찾는 데만 쓴다.

    kind: goteacher·free_school(wr_id, 댓글 포함) · file(bo_table:wr_id:bf_no) · member(mb_no) · admin(go_admin wr_id).
    school: 그 행이 속한 이관 학교 코드(사라진 행을 어느 학교에서 지울지).
    """

    kind = models.CharField(max_length=12)
    legacy_id = models.CharField(max_length=64)
    school = models.CharField(max_length=20)
    crc = models.BigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "legacy_id"], name="uniq_legacy_print")]
