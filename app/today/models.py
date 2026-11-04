"""간이 '오늘'의 동학년 일정(27번 4-1 Event). 공간 격리는 SpaceQuerySet.in_space()로만 조회한다.

slot·slot_end: 블록(b1·b2·b3·pm). 비면 시간 미정(전일). 한 줄 입력의 '오전'(1~2블록)처럼 범위가 있어
장소 충돌(TR.conflicts)을 보려면 끝 블록이 필요하다.
by: 동학년은 마지막으로 쓴 사람(올린 사람, 바꾸면 바꾼 사람). 학교 공간은 올린 사람 그대로, 바꾼 사람은 changed_by에
(관리자가 고쳐도 쓴 사람의 권한과 '나' 일정이 넘어가지 않게). changed_from: 마지막 변경 [[무엇, 전, 후], …], 새로 올린 것은 비움.
scope: 볼 사람(H5). 학교 전체(all)·그룹(g4, office…)·나(me). 동학년 공간은 언제나 all이고 합칠 때 출처 학년으로 바뀐다.
Member.group은 본인이 고치는 값이라 보기 범위에만 쓴다. 쓰기 권한은 writable_by(쓴 사람과 관리자).
주소에는 key만 쓴다(순번 ID 없음).
"""
from django.db import models
from django.db.models import Q

from spaces.models import Member, Space, SpaceQuerySet, new_token

SLOT_CHOICES = [("b1", "1블록"), ("b2", "2블록"), ("b3", "3블록"), ("pm", "방과후")]
ALL, ME = "all", "me"


def my_range_q(me):
    """오늘 화면·바뀐 것·일반 회원의 일정: 학교 전체, 내 그룹, 내가 쓴 것."""
    q = Q(scope=ALL) | Q(by=me)
    return q | Q(scope=me.group) if me.group else q


def reachable_q(me):
    """주소와 일정 화면: 관리자는 남의 '나' 일정을 뺀 전부, 그 외는 내 범위."""
    return ~Q(scope=ME) | Q(by=me) if me.is_manager else my_range_q(me)


def scope_choices(me):
    """대상 고르기. 맨 앞이 기본값: 내 그룹, 그룹이 없으면 나."""
    first = [(me.group, f"{me.get_group_display()}(우리 소속)")] if me.group else []
    return [*first, (ME, "나만"), (ALL, "학교 전체"), *[g for g in Member.Group.choices if g[0] != me.group]]


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
    scope = models.CharField("볼 사람", max_length=10, default=ALL)
    by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    changed_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
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

    @property
    def last_by(self):
        """마지막으로 손댄 사람(바뀐 것·대화상자 표시)."""
        return self.changed_by or self.by

    @property
    def scope_label(self):
        return {ALL: "학교 전체", ME: "나만"}.get(self.scope) or Member.Group(self.scope).label

    def reachable_by(self, me):
        """reachable_q와 같은 판정을 이미 읽은 행에(장소 겹침 문구)."""
        if self.scope == ALL or self.by_id == me.pk or self.scope == me.group:
            return True
        return me.is_manager and self.scope != ME

    def writable_by(self, me):
        return not me.space.is_school or me.is_manager or self.by_id == me.pk
