"""기기 토큰: 이 PC를 1년 기억한다. 원문은 쿠키에만, DB에는 SHA-256만 둔다."""
import hashlib
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

DEVICE_COOKIE = "gt_device"
DEVICE_AGE = timedelta(days=365)
# 초대 링크로 이름만 고르고 들어온 사람(B7). 구글 없는 User로 만들어 기기 토큰 흐름을 그대로 쓴다.
GUEST_PREFIX = "guest:"
# P1 S5: 이 세션이 손님을 처음 만들었다(초대 입장). 손님을 계정으로 잇는 것은 이 세션이나 손님의 첫 기기 토큰 PC에서만.
GUEST_ORIGIN = "guest_origin"
# P1 S6: 임시 비밀번호로 들어온 세션. 바꾸기 전에는 바꾸기 화면만(accounts.middleware).
MUST_CHANGE = "must_change_password"


def is_guest(user):
    return getattr(user, "username", "").startswith(GUEST_PREFIX)


# P1 아이디 회원: username이 아이디 그대로(접두사 없음). ':'를 못 쓰므로 구글(google:)·손님(guest:)과 겹치지 않는다.
ID_RE = re.compile(r"[A-Za-z0-9_]{3,20}")


def is_account_member(user):
    """고티처 계정 회원(구글 또는 아이디). 손님·비로그인은 아니다. 관리자 자격·업체 추천·양식 추천·커뮤니티가 같은 기준을 쓴다."""
    return user.is_authenticated and not is_guest(user)


def is_id_member(user):
    """아이디·비밀번호로 가입한 회원(비밀번호 바꾸기·임시 비밀번호 대상)."""
    return user.is_authenticated and ":" not in user.username


def new_guest(name):
    from django.contrib.auth import get_user_model

    user = get_user_model()(username=GUEST_PREFIX + secrets.token_urlsafe(24), first_name=name[:150])
    user.set_unusable_password()
    user.save()
    return user


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class DeviceToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # P1 S5: 손님을 처음 만든 세션(GUEST_ORIGIN)에서 받은 토큰. 이 표시가 있는 PC만 손님을 계정으로 잇는다.
    # 같은 반·이름으로 다시 들어온 다른 PC의 토큰에는 붙지 않는다.
    origin = models.BooleanField(default=False)

    @classmethod
    def issue(cls, user, origin=False):
        raw = secrets.token_urlsafe(32)
        cls.objects.create(user=user, token_hash=hash_token(raw), origin=origin)
        return raw

    @classmethod
    def find_user(cls, raw):
        if not raw or len(raw) > 128:
            return None
        t = (
            cls.objects.filter(token_hash=hash_token(raw), created_at__gt=timezone.now() - DEVICE_AGE)
            .select_related("user")
            .first()
        )
        return t.user if t and t.user.is_active else None


class PasswordChangeRequired(models.Model):
    """P1 임시 비밀번호를 받은 아이디 회원: 다음 로그인 뒤 비밀번호를 바꾸기 전에는 바꾸기 화면만 쓴다. 바꾸면 지운다."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
