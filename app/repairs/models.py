"""K1 수리 요청(prototype repairTab·TR.seedRepairs). 교내 한 곳, 시설·정보. 조회는 SpaceQuerySet.in_space()로만.

- by: 고치기·지우기 권한의 근거(나가면 비고, 관리자만 지운다). by_name: 요청 때의 표시 값.
- photo: 서버가 재인코딩한 JPEG만(repairs.photo). 경로에 올린 이름을 쓰지 않고, 내려받기는 회원 확인 뷰로만.
  저장량(stored)은 한글 파일·공지 첨부와 같은 한도(SourceFile.used_bytes), 행이 지워지면 파일도 지운다(hwpfiles.retention).
- 보관: 완료 90일 뒤, 끝나지 않은 요청도 마지막 변경 1년 뒤 행·사진을 지운다(hwpfiles.retention, 사진에 교실 모습이 찍힌다).
"""
from django.db import models

from spaces.models import Space, SpaceQuerySet, new_token


def _photo_path(instance, filename):
    """올린 파일 이름은 경로에 쓰지 않는다. 사진을 바꾸면 새 이름(옛 파일은 커밋 뒤 지운다)."""
    return f"repair/{instance.space_id}/{new_token()}.jpg"


class Repair(models.Model):
    class Cat(models.TextChoices):
        FACILITY = "facility", "시설"
        INFO = "info", "정보"

    class Status(models.TextChoices):
        RECEIVED = "received", "접수"
        VISIT = "visit", "방문 예정"
        DOING = "doing", "처리 중"
        DONE = "done", "완료"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="repairs")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    cat = models.CharField("분류", max_length=10, choices=Cat.choices)
    place = models.CharField("위치", max_length=100, blank=True)
    title = models.CharField("무엇이 안 되나요", max_length=200)
    note = models.CharField("메모", max_length=1000, blank=True)
    urgent = models.BooleanField("급함", default=False)
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.RECEIVED)
    reply = models.CharField("답변", max_length=500, blank=True)
    by = models.ForeignKey("spaces.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    by_name = models.CharField("요청한 사람", max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    photo = models.FileField(upload_to=_photo_path, blank=True)
    stored = models.PositiveBigIntegerField("디스크에 둔 크기(바이트)", default=0)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return self.key

    @property
    def is_done(self):
        return self.status == self.Status.DONE

    @property
    def status_pill(self):
        return {self.Status.RECEIVED: "neg", self.Status.DONE: "pos"}.get(self.status, "acc")
