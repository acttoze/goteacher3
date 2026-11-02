"""설정 > 이 PC(design/settings.html #device): '이 PC 1년 기억' 스위치와 '모든 기기에서 로그아웃'(G3 S2).

켜짐 = 이 PC 기기 쿠키가 있고 DB 토큰이 살아 있음. 끄면 이 PC 토큰을 지우고 세션을 브라우저 닫을 때까지로 바꾼다.
"""
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.models import PushSubscription
from spaces.views import _space_ctx

from .models import DEVICE_AGE, DEVICE_COOKIE, DeviceToken, hash_token, is_guest
from .views import set_device_cookie


def _this_pc(request):
    raw = request.COOKIES.get(DEVICE_COOKIE)
    if not raw or len(raw) > 128:
        return None
    return DeviceToken.objects.filter(
        user=request.user, token_hash=hash_token(raw), created_at__gt=timezone.now() - DEVICE_AGE
    ).first()


def _ctx(request, token, **extra):
    """token: 이 PC 기기 토큰(없으면 None). 막 켰을 때는 아직 쿠키가 요청에 없어 새로 만든 것을 넘긴다."""
    return _space_ctx(
        request, page="settings", remember=token is not None,
        until=timezone.localtime(token.created_at + DEVICE_AGE) if token else None,
        n_devices=DeviceToken.objects.filter(user=request.user, created_at__gt=timezone.now() - DEVICE_AGE).count(),
        guest=is_guest(request.user), **extra,
    )


@never_cache
@require_GET
def settings_page(request):
    return render(request, "accounts/settings.html", _ctx(request, _this_pc(request)))


@never_cache
@require_POST
def remember(request):
    """스위치: remember=1이면 켜기, 없으면 끄기. htmx면 패널만 돌려준다."""
    response = redirect(f"/s/{request.space.token}/settings")
    if request.POST.get("remember") == "1":
        request.session.set_expiry(None)
        set_device_cookie(request, response, request.user)
        token = DeviceToken.objects.get(token_hash=hash_token(response.cookies[DEVICE_COOKIE].value))
    else:
        mine = _this_pc(request)
        if mine:
            mine.delete()  # 이 PC의 웹 푸시 구독도 함께(공용 PC에 앞사람 알림이 뜨지 않게)
        request.session.set_expiry(0)
        response.delete_cookie(DEVICE_COOKIE)
        token = None
    if not request.headers.get("HX-Request"):
        return response
    panel = render(request, "accounts/_remember.html", _ctx(request, token, saved=True))
    for k, v in response.cookies.items():
        panel.cookies[k] = v
    return panel


@never_cache
@require_POST
def logout_all(request):
    """내 기기 토큰 전부·웹 푸시 구독 전부를 지우고, 모든 세션을 끊는다(이 PC 포함)."""
    user = request.user
    DeviceToken.objects.filter(user=user).delete()
    PushSubscription.objects.filter(member__user=user).delete()
    # 세션 인증 해시는 비밀번호 해시에서 나온다. 새 '쓸 수 없는 비밀번호'로 바꾸면 다른 PC 세션이 모두 무효가 된다.
    user.set_unusable_password()
    user.save(update_fields=["password"])
    logout(request)
    response = redirect("/start")
    response.delete_cookie(DEVICE_COOKIE)
    return response
