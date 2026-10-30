"""기기 토큰: 이 PC를 1년 기억한다. 원문은 쿠키에만, DB에는 SHA-256만 둔다."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

DEVICE_COOKIE = "gt_device"
DEVICE_AGE = timedelta(days=365)
# 초대 링크로 이름만 고르고 들어온 사람(B7). 구글 없는 User로 만들어 기기 토큰 흐름을 그대로 쓴다.
GUEST_PREFIX = "guest:"


def is_guest(user):
    return getattr(user, "username", "").startswith(GUEST_PREFIX)


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

    @classmethod
    def issue(cls, user):
        raw = secrets.token_urlsafe(32)
        cls.objects.create(user=user, token_hash=hash_token(raw))
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
