"""G2 웹 푸시(VAPID) 묶음 알림. 🔔(D3 Notification)을 하루 세 번만 브라우저 알림으로 묶어 보낸다.

- 서버 cron이 5분마다 `manage.py push_digest`(remind 다음)를 부른다. 묶음 시각 이후 첫 실행에 한 번만 보낸다.
- 묶음 창(시작~끝) 밖은 수업 중·퇴근 뒤라 보내지 않는다. 창 끝을 TTL로 줘서 꺼진 PC에 늦게 뜨지도 않게 한다.
  시각은 design 블록(1블록 08:40~, 2블록 끝 12:20, 방과후 14:40~). 주말·NEIS 휴업일·방학은 건너뛴다.
- 회원별 한 건: 안 읽은 🔔 가운데 아직 푸시 안 한 것. 본문은 모으기 제목·건수만(제출 값·이름 없음).
- 구독 저장·끄기는 이 PC 브라우저가 누를 때만(gt.js). endpoint는 알려진 푸시 서비스 주소만 받는다(서버가 그 주소로 요청하므로).
"""
import base64
import binascii
import json
import logging
from datetime import datetime, time
from itertools import groupby
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from pywebpush import WebPushException, webpush

from accounts.models import DEVICE_COOKIE, DeviceToken, hash_token
from spaces import neis

from .models import Notification, PushRound, PushSubscription

log = logging.getLogger(__name__)

# (키, 시작, 창 끝, 이름)
SLOTS = [
    ("am", time(8, 20), time(8, 40), "아침 묶음"),
    ("noon", time(12, 20), time(12, 50), "점심 묶음"),
    ("pm", time(14, 40), time(16, 40), "방과후 묶음"),
]
# 브라우저 푸시 서비스: Chrome·Whale(FCM), Edge(WNS), Firefox, Safari. 앞에 점이 있으면 하위 도메인.
PUSH_HOSTS = ("fcm.googleapis.com", ".notify.windows.com", ".push.services.mozilla.com", ".push.apple.com")


def current_slot(now):
    t = timezone.localtime(now).time()
    return next((s for s in SLOTS if s[1] <= t < s[2]), None)


def next_at(now):
    """다음 묶음 시각(🔔 옆 '다음 알림 08:20')."""
    t = timezone.localtime(now).time()
    return next((s[1] for s in SLOTS if t < s[1]), SLOTS[0][1])


def digest(now=None):
    """지금이 묶음 창이면 한 번만 보내고, 보낸 회원 수를 돌려준다."""
    if not (settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY):
        return 0
    now = timezone.localtime(now or timezone.now())
    slot = current_slot(now)
    if slot is None or now.weekday() >= 5:
        return 0
    if not PushRound.objects.get_or_create(day=now.date(), slot=slot[0])[1]:
        return 0
    end = datetime.combine(now.date(), slot[2], tzinfo=now.tzinfo)
    ttl = max(60, int((end - now).total_seconds()))
    off = {}
    sent = 0
    # 사용자와 끊긴 회원(손님 합치기·탈퇴)·막힌 사용자에게는 보내지 않는다
    subs = (PushSubscription.objects.filter(member__user__isnull=False, member__user__is_active=True)
            .select_related("space__school").order_by("member_id", "pk"))
    for member_id, group in groupby(subs, key=lambda s: s.member_id):
        group = list(group)
        space = group[0].space
        school = space.school
        if school.pk not in off:
            off[school.pk] = neis.day_off(school.neis_atpt, school.neis_code, now.date())
        if off[school.pk]:
            continue  # 쌓인 것은 다음 등교일 묶음으로
        notes = list(Notification.objects.in_space(space).filter(member_id=member_id, read=False, pushed=False)
                     .select_related("row__collection").order_by("created_at", "id"))
        if not notes:
            continue
        titles = list(dict.fromkeys(n.row.collection.title[:60] for n in notes))
        body = f"안 읽은 알림 {len(notes)}건 · {titles[0]}" + (f" 외 {len(titles) - 1}개" if len(titles) > 1 else "")
        data = json.dumps({"title": f"고티처 · {slot[3]}", "body": body,
                           "url": f"{settings.SITE_URL}/s/{space.token}/today"}, ensure_ascii=False)
        for s in group:
            _send(s, data, ttl)
        Notification.objects.filter(pk__in=[n.pk for n in notes]).update(pushed=True)
        sent += 1
    return sent


def _send(sub, data, ttl):
    try:
        webpush(subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=data, vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},  # 매번 새로: pywebpush가 endpoint별 aud를 써 넣는다
                ttl=ttl, timeout=10)
    except WebPushException as e:
        code = getattr(e.response, "status_code", None)
        if code in (404, 410):  # 브라우저가 구독을 버림
            sub.delete()
        else:  # endpoint는 기기 식별자라 남기지 않는다
            log.warning("웹 푸시 실패: HTTP %s", code)
    except requests.RequestException as e:  # 네트워크 문제는 잠깐일 수 있어 구독을 남긴다
        log.warning("웹 푸시 실패: %s", type(e).__name__)
    except Exception as e:  # 키가 깨진 구독 등: 그 구독만 버리고 다른 회원 발송은 계속한다
        log.warning("웹 푸시 실패, 구독 삭제: %s", type(e).__name__)
        sub.delete()


def _push_service(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    u = urlparse(endpoint)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or u.port not in (None, 443) or u.username or u.password:
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


def _b64(v):
    if not isinstance(v, str) or len(v) > 200:
        return None
    try:
        return base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
    except (binascii.Error, ValueError):
        return None


def _keys_ok(p256dh, auth):
    """p256dh는 P-256 곡선 위 점(비압축 65바이트), auth는 16바이트. 형식만 맞는 가짜 점은 발송 중 예외를 낸다."""
    pub, secret = _b64(p256dh), _b64(auth)
    if pub is None or len(pub) != 65 or secret is None or len(secret) != 16:
        return False
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
    except ValueError:
        return False
    return True


def _device(request):
    raw = request.COOKIES.get(DEVICE_COOKIE)
    if not raw or len(raw) > 128:
        return None
    return DeviceToken.objects.filter(token_hash=hash_token(raw), user=request.user).first()


def _read(request):
    try:
        d = json.loads(request.body)
        return d.get("endpoint"), d.get("keys") or {}
    except (ValueError, AttributeError):
        return None, {}


@never_cache
@require_POST
def subscribe(request):
    """이 PC 브라우저 구독 저장(JSON, CSRF 헤더). 같은 브라우저 구독이 오면 지금 회원 것으로 옮긴다(같은 PC를 넘겨 쓴 경우).

    이 PC의 기기 토큰이 있으면 함께 묶어, 로그아웃(기기 토큰 삭제) 때 구독도 지워지게 한다.
    """
    endpoint, keys = _read(request)
    if not isinstance(keys, dict):
        return HttpResponseBadRequest()
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (_push_service(endpoint) and _keys_ok(p256dh, auth)):
        return HttpResponseBadRequest()
    before = PushSubscription.objects.filter(endpoint=endpoint).values_list("member_id", flat=True).first()
    if before is not None and before != request.member.pk:
        log.info("웹 푸시 구독을 다른 회원으로 옮김")  # endpoint는 기기 식별자라 남기지 않는다
    PushSubscription.objects.update_or_create(
        endpoint=endpoint, defaults={"space": request.space, "member": request.member, "p256dh": p256dh, "auth": auth,
                                     "device": _device(request)})
    return HttpResponse(status=204)


@never_cache
@require_POST
def mine(request):
    """이 브라우저 구독이 지금 회원 것인가(🔔 단추 상태). 아니면 화면은 '꺼짐'으로 보이고, 켜면 옮긴다."""
    endpoint, _ = _read(request)
    ok = isinstance(endpoint, str) and PushSubscription.objects.in_space(request.space).filter(
        member=request.member, endpoint=endpoint).exists()
    return JsonResponse({"mine": ok})


@never_cache
@require_POST
def unsubscribe(request):
    """끄기: 내 구독만 지운다(남의 endpoint를 보내도 아무것도 안 지운다)."""
    endpoint, _ = _read(request)
    if isinstance(endpoint, str):
        PushSubscription.objects.in_space(request.space).filter(member=request.member, endpoint=endpoint).delete()
    return HttpResponse(status=204)
