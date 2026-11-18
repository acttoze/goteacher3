"""V10 운영자 고객 분석의 기록 두 가지. 이름·입장권 원문·난수·IP·화면 내용·주소 값은 저장하지 않는다.

- Entry: 구 고티처 '새 고티처 이용해 보기'(입장권 로그인) 시도 한 건과 결과.
- DailyUse: 날짜·사람·화면 경로 모양별로 연 횟수(feedback.views.route_shape: '/s/<공간>/c/<key>/').
보관은 settings.ANALYTICS_KEEP_DAYS(hwpfiles.retention이 지운다).
"""
import logging

from django.conf import settings
from django.db import models, transaction

from spaces.models import Space

log = logging.getLogger(__name__)


class Entry(models.Model):
    class Reason(models.TextChoices):
        OK = "", "들어옴"
        BAD_TICKET = "bad_ticket", "입장권이 맞지 않음"  # 서명·기한·이관 계정이 안 맞음(계정 모름)
        LOCKED = "locked", "시도 제한"
        SWITCH_SHOWN = "switch_shown", "다른 계정 바꾸기 화면"  # 중간 단계: 아직 실패가 아니다
        REPLAYED = "replayed", "이미 쓴 입장권"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    space = models.ForeignKey(Space, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")  # 들어간 학교 공간
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    ok = models.BooleanField(default=False)
    reason = models.CharField(max_length=20, choices=Reason.choices, blank=True, default="")


class DailyUse(models.Model):
    day = models.DateField(db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    space = models.ForeignKey(Space, on_delete=models.CASCADE, null=True, blank=True, related_name="+")  # 공간 주소일 때만
    route = models.CharField(max_length=200)
    n = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "user", "route", "space"], name="uniq_daily_use")]


def record_entry(user, ok, reason="", space=None):
    """입장 시도 한 건. 기록이 실패해도 로그인 흐름은 그대로 간다(내용 없이 한 줄만 남긴다)."""
    try:
        with transaction.atomic():
            Entry.objects.create(user=user, ok=ok, reason=reason, space=space)
    except Exception:
        log.warning("analytics: entry not recorded")
