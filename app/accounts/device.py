"""설정(design/settings.html): 영역마다 한 화면(?a=me|alerts|space|device). 기본은 내 정보.

이 PC: '이 PC 1년 기억' 스위치와 '모든 기기에서 로그아웃'(G3 S2).
켜짐 = 이 PC 기기 쿠키가 있고 DB 토큰이 살아 있음. 끄면 이 PC 토큰을 지우고 세션을 브라우저 닫을 때까지로 바꾼다.
내 정보 저장·동학년 나가기는 spaces.views(update_me·leave).
"""
from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.models import PushSubscription
from collect.push import SLOTS, off_keys
from spaces.views import _invite, _space_ctx, class_choices

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


def _slots(member):
    off = off_keys(member)
    return [(k, t.strftime("%H:%M"), name, k not in off) for k, t, _, name in SLOTS]


AREAS = {"me": "내 정보", "alerts": "알림", "space": "동학년", "device": "이 PC"}


@never_cache
@require_GET
def settings_page(request):
    area = request.GET.get("a")
    area = area if area in AREAS else "me"
    areas = {**AREAS, "space": request.space.word}
    ctx = _ctx(request, _this_pc(request), area=area, area_label=areas[area], areas=areas.items())
    if area == "me":
        ctx["classes"] = class_choices(request.space)
    elif area == "alerts":
        ctx.update(slots=_slots(request.member), vapid=settings.VAPID_PUBLIC_KEY)
    elif area == "space":
        ctx.update(_invite(request.space, request.member))
    return render(request, "accounts/settings.html", ctx)


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
def slots(request):
    """알림 묶음 스위치: 체크된 묶음(on=am·noon·pm)만 켜고 나머지는 끈다. 모르는 키는 버린다. htmx면 패널만."""
    me = request.member
    on = set(request.POST.getlist("on"))
    me.push_off = ",".join(k for k, *_ in SLOTS if k not in on)
    me.save(update_fields=["push_off"])
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/settings?a=alerts")
    n = len(SLOTS) - len(off_keys(me))
    saved = f"브라우저 알림을 하루 {n}번 묶어 보내요" if n else "묶음 알림을 모두 껐어요. 종 단추에는 그대로 쌓여요"
    return render(request, "accounts/_slots.html", _space_ctx(request, slots=_slots(me), saved=saved))


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
