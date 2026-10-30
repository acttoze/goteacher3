"""간이 '오늘'의 동학년 일정(27번 4-1 Event). 공간 격리는 SpaceQuerySet.in_space()로만 조회한다.

slot·slot_end: 블록(b1·b2·b3·pm). 비면 시간 미정(전일). 한 줄 입력의 '오전'(1~2블록)처럼 범위가 있어
장소 충돌(TR.conflicts)을 보려면 끝 블록이 필요하다.
by: 마지막으로 쓴 사람(올린 사람, 바꾸면 바꾼 사람). changed_from: 마지막 변경 [[무엇, 전, 후], …], 새로 올린 것은 비움.
주소에는 key만 쓴다(순번 ID 없음).
"""
from django.db import models

from spaces.models import Member, Space, SpaceQuerySet, new_token

SLOT_CHOICES = [("b1", "1블록"), ("b2", "2블록"), ("b3", "3블록"), ("pm", "방과후")]


class Event(models.Model):
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="events")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    date = models.DateField("날짜")
    end = models.DateField("끝 날짜", null=True, blank=True)
    slot = models.CharField("블록", max_length=2, choices=SLOT_CHOICES, blank=True)
    slot_end = models.CharField("끝 블록", max_length=2, choices=SLOT_CHOICES, blank=True)
    title = models.CharField("제목", max_length=200)
    place = models.CharField("장소", max_length=40, blank=True)
    target = models.CharField("대상", max_length=80, blank=True)
    by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    changed_from = models.JSONField("바뀐 부분", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["space", "date"])]

    def __str__(self):
        return self.title

    @property
    def last_day(self):
        return self.end or self.date
