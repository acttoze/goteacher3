"""설정(design/settings.html): 영역마다 한 화면(?a=prefs|me|alerts|space|device). 기본은 내 정보.

설정(Q1): 표시 요일 7개 스위치. 켠 요일만 일정 주·월 보기에 나온다(사람마다). 하나는 켜 두어야 한다.

이 PC: '이 PC 1년 기억' 스위치와 '모든 기기에서 로그아웃'(G3 S2). 내 정보의 비밀번호 바꾸기는 아이디 회원만(P1).
켜짐 = 이 PC 기기 쿠키가 있고 DB 토큰이 살아 있음. 끄면 이 PC 토큰을 지우고 세션을 브라우저 닫을 때까지로 바꾼다.
내 정보 저장·동학년 나가기는 spaces.views(update_me·leave).
"""
from django.conf import settings
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.models import PushSubscription
from collect.push import SLOTS, off_keys
from partners.models import Review
from spaces.views import _invite, _space_ctx, class_choices

from .models import DEVICE_AGE, DEVICE_COOKIE, DeviceToken, hash_token, is_guest, is_id_member
from .views import LOGIN_LOCKED, PASSWORD_RULE, _count_fail, _locked, set_device_cookie


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
        guest=is_guest(request.user), id_member=is_id_member(request.user), **extra,
    )


def _slots(member):
    off = off_keys(member)
    return [(k, t.strftime("%H:%M"), name, k not in off) for k, t, _, name in SLOTS]


def _days(member):
    on = member.shown_days
    return [(str(i), w, i in on) for i, w in enumerate("월화수목금토일")]


AREAS = {"prefs": "설정", "me": "내 정보", "alerts": "알림", "space": "동학년", "device": "이 PC"}


@never_cache
@require_GET
def settings_page(request):
    area = request.GET.get("a")
    area = area if area in AREAS else "me"
    areas = {**AREAS, "space": request.space.word}
    ctx = _ctx(request, _this_pc(request), area=area, area_label=areas[area], areas=areas.items())
    if area == "prefs":
        ctx["days"] = _days(request.member)
        ctx["lines"] = _lines(request)
    elif area == "me":
        ctx["classes"] = class_choices(request.space)
    elif area == "alerts":
        ctx.update(slots=_slots(request.member), vapid=settings.VAPID_PUBLIC_KEY)
    elif area == "space":
        ctx.update(_invite(request.space, request.member))
        ctx["my_reviews"] = Review.objects.filter(member=request.member, partner__is_public=True).select_related("partner")
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
def days(request):
    """표시 요일 스위치: 체크된 요일(on=0~6)만 켠다. 모르는 값은 버린다. 다 끄면 저장하지 않고 안내. htmx면 패널만."""
    me = request.member
    on = "".join(c for c in "0123456" if c in request.POST.getlist("on"))
    if on:
        me.days_on = on
        me.save(update_fields=["days_on"])
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/settings?a=prefs")
    saved = "일정에 보일 요일을 바꿨어요" if on else "요일을 하나는 켜 두어야 해요"
    return render(request, "accounts/_days.html", _space_ctx(request, days=_days(me), saved=saved))


def _lines(request):
    """[(그룹 키, 이름, 켬)] 기관만(T2). 학교·동학년은 빈 목록."""
    space, off = request.space, request.member.off_lines
    return [(k, n, k not in off) for k, n in space.group_choices] if space.is_org else []


@never_cache
@require_POST
def lines(request):
    """보이는 그룹 줄(기관, T2): 체크된 그룹만 켠다. 저장은 끈 줄(새 그룹은 처음부터 보이게). 다 끄면 저장하지 않는다.
    보기 필터일 뿐 권한이 아니다(주소로 열면 보인다)."""
    space, me = request.space, request.member
    if not space.is_org:
        raise Http404
    on = set(request.POST.getlist("on"))
    keys = space.group_keys
    saved = "그룹 줄을 하나는 켜 두어야 해요"
    if on & set(keys):
        me.lines_off = ",".join(k for k in keys if k not in on)
        me.save(update_fields=["lines_off"])
        saved = "일정에 보일 그룹 줄을 바꿨어요"
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{space.token}/settings?a=prefs")
    return render(request, "accounts/_lines.html", _space_ctx(request, lines=_lines(request), saved=saved))


@never_cache
@require_POST
def logout_all(request):
    """내 기기 토큰 전부·웹 푸시 구독 전부를 지우고, 모든 세션을 끊는다(이 PC 포함)."""
    user = request.user
    DeviceToken.objects.filter(user=user).delete()
    PushSubscription.objects.filter(member__user=user).delete()
    if is_id_member(user):  # 비밀번호를 지우면 다시 못 들어온다: 세션을 찾아 지운다(드문 일이라 훑어도 된다)
        drop_sessions(user)
    else:
        # 세션 인증 해시는 비밀번호 해시에서 나온다. 새 '쓸 수 없는 비밀번호'로 바꾸면 다른 PC 세션이 모두 무효가 된다.
        user.set_unusable_password()
        user.save(update_fields=["password"])
    logout(request)
    response = redirect("/start")
    response.delete_cookie(DEVICE_COOKIE)
    return response


def drop_sessions(user):
    """user의 세션을 모두 지운다(DB 세션). 비밀번호를 그대로 두고 다른 PC 로그인을 끊을 때."""
    uid = str(user.pk)
    for s in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if s.get_decoded().get("_auth_user_id") == uid:
            s.delete()


@never_cache
@require_POST
def change_password(request):
    """설정 > 내 정보 > 비밀번호 바꾸기(P1, 아이디 회원만). 지금 비밀번호를 확인한다(틀리면 로그인 실패와 같이 센다).
    바꾸면 이 PC 로그인은 그대로, 다른 기기 토큰은 지운다(다른 세션은 비밀번호 해시가 바뀌어 끊긴다)."""
    user = request.user
    if not is_id_member(user):
        return HttpResponseForbidden()
    current, new, new2 = (request.POST.get(k, "") for k in ("current", "password", "password2"))
    errors = {}
    if _locked(request, user.username):
        errors["current"] = LOGIN_LOCKED
    elif not user.check_password(current):
        _count_fail(request, user.username)
        errors["current"] = "지금 비밀번호가 맞지 않아요."
    else:
        try:
            validate_password(new, user)
        except ValidationError:
            errors["password"] = PASSWORD_RULE
        if not errors and new != new2:
            errors["password2"] = "새 비밀번호가 서로 달라요. 다시 써 주세요."
    if not errors:
        user.set_password(new)
        user.save(update_fields=["password"])
        update_session_auth_hash(request, user)
        DeviceToken.objects.filter(user=user).exclude(token_hash=hash_token(request.COOKIES.get(DEVICE_COOKIE) or "")).delete()
    status = 400 if errors else 200
    if request.headers.get("HX-Request"):
        return render(request, "accounts/_password.html", _ctx(request, _this_pc(request), pw_errors=errors, pw_done=not errors), status=status)
    if errors:
        ctx = _ctx(request, _this_pc(request), area="me", area_label=AREAS["me"], areas={**AREAS, "space": request.space.word}.items(),
                   classes=class_choices(request.space), pw_errors=errors)
        return render(request, "accounts/settings.html", ctx, status=status)
    return redirect(f"/s/{request.space.token}/settings")
