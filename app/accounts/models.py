"""기기 토큰: 이 PC를 1년 기억한다. 원문은 쿠키에만, DB에는 SHA-256만 둔다."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

DEVICE_COOKIE = "gt_device"
DEVICE_AGE = timedelta(days=365)


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
