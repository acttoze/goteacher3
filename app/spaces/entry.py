"""W4 자리 입장 코드: 관리자가 주인 없는 자리(user 없음)에 1회용 코드를 주고, 받은 분이 /enter에서 로그인·가입한 뒤 그 자리를 계정에 잇는다.

원문은 발급 응답에 한 번만 나오고, DB·세션에는 SECRET_KEY로 만든 해시만 둔다. 계정 회원·손님·관리자 자리에는 쓸 수 없다
(손님 자리에는 그 사람의 '나만' 일정·알림·낸 값 연결이 있어, 코드를 주면 관리자가 자리를 빼앗을 수 있다).
issue·claim은 화면과 분리해 둔다(W7 일괄 등록이 issue를 쓴다).
"""
import re
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import is_account_member, is_guest
from accounts.views import remember_pc
from config.ratelimit import allow, client_ip

from .gates import manager_required, no_org, school_required, school_write_required
from .models import ENTRY, EntryCode, Member, MemberLog, Space, get_in_space_or_404
from .views import _welcome_alert

CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 불러 주고 받아 적기 쉽게 I·L·O·0·1을 뺐다
CODE_LEN = 12  # 약 59비트
CODE_DAYS = 14
WINDOW = 15 * 60
HOLD_SECONDS = 30 * 60  # 확인한 코드를 세션에 들고 있는 시간(공용 PC에서 다음 사람이 잇지 못하게)
FAILS_IP = 300  # 학교는 IP 하나(NAT)를 같이 쓴다: 한 분의 오타로 학교가 막히지 않게 상한만 둔다. 틀린 시도만 센다
FAILS_SESSION = 10
FAILS_USER = 10
BAD_CODE = "입장 코드가 맞지 않거나 더 쓸 수 없어요. 관리자에게 새 코드를 받아 주세요."
HAS_SEAT = "이 계정은 이미 다른 자리가 있어요. 한 선생님은 공간 하나만 쓸 수 있어요."
LIMITED = "입장 코드를 너무 많이 틀렸어요. 15분 뒤 다시 해 주세요."
GUEST_HERE = "이름만 들어온 상태에서는 입장 코드를 쓸 수 없어요. 이 PC에서 로그아웃한 뒤 다시 넣어 주세요."


def _plain(raw):
    return re.sub(r"[\s-]", "", raw[:64]).upper()


def code_hash(raw):
    return salted_hmac("spaces.entry.code", _plain(raw), algorithm="sha256").hexdigest()


def _open_seat(seat):
    """코드를 줄 수 있는 자리: 학교 공간(기관·동학년 아님, 플랜 유효, 이관 동시 사용 중 아님)의 주인 없는 자리, 관리자 아님."""
    space = seat.space
    return (space.kind == Space.Kind.SCHOOL and space.school.writable and not space.legacy_readonly
            and seat.user_id is None and not seat.is_manager)


def _usable(code):
    return code.used_at is None and code.expires_at > timezone.now() and _open_seat(code.member)


def issue(member, by):
    """이 자리의 새 코드: (원문, 만료 시각). 안 쓴 옛 코드는 지운다. 줄 수 없는 자리이거나 by가 이 공간 관리자가 아니면 ValueError.
    관리자 확인은 공간 행을 잠근 뒤에 한다(remove_manager와 같은 잠금: 해제와 겹쳐도 코드가 남지 않는다)."""
    raw = "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LEN))
    with transaction.atomic():
        space = Space.objects.select_for_update(no_key=True).get(pk=member.space_id)  # 재발급·사용과 직렬화
        if not Member.objects.in_space(space).filter(user=by, is_manager=True).exists():
            raise ValueError("학교 관리자만 입장 코드를 줄 수 있어요.")
        seat = Member.objects.in_space(space).filter(pk=member.pk).first()
        if seat is not None:
            seat.space = space
        if seat is None or not _open_seat(seat):
            raise ValueError("아직 주인이 없는 자리에만 입장 코드를 줄 수 있어요.")
        EntryCode.objects.in_space(space).filter(member=seat, used_at__isnull=True).delete()
        code = EntryCode.objects.create(member=seat, code_hash=code_hash(raw), created_by=by,
                                        expires_at=timezone.now() + timedelta(days=CODE_DAYS))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LEN, 4)), code.expires_at


def _find(hashed):
    """지금 쓸 수 있는 코드. 없으면 None."""
    code = EntryCode.objects.select_related("member__space__school").filter(code_hash=hashed or "-").first()
    return code if code and _usable(code) else None


def claim(hashed, user):
    """코드의 자리를 user에게 잇고 그 자리를 돌려준다(1회용). 잠근 뒤에도 주인 없는 자리일 때만. 거절은 ValueError(안내 문구)."""
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()  # 입장·동학년 만들기와 직렬화(교사 한 명은 공간 하나)
        found = EntryCode.objects.filter(code_hash=hashed or "-").select_related("member").first()
        if found is None or is_guest(user):
            raise ValueError(BAD_CODE)
        space = Space.objects.select_for_update(no_key=True).get(pk=found.member.space_id)
        code = EntryCode.objects.select_for_update().filter(pk=found.pk).first()
        seat = code and Member.objects.select_for_update(no_key=True).filter(pk=code.member_id, space=space).first()
        if not seat:
            raise ValueError(BAD_CODE)
        if seat.user_id == user.pk:  # 두 번 눌렀다
            return seat
        seat.space, code.member = space, seat
        if not _usable(code):
            raise ValueError(BAD_CODE)
        if Member.objects.filter(user=user).exists():
            raise ValueError(HAS_SEAT)
        seat.user = user
        seat.save(update_fields=["user"])
        code.used_at = timezone.now()
        code.save(update_fields=["used_at"])
        MemberLog.objects.create(space=space, by=seat.name, name=seat.name, before="입장 코드", after="계정 연결")
        _welcome_alert(space, seat)
    return seat


@never_cache
@require_POST
@school_required
@no_org
@manager_required
@school_write_required
def issue_code(request, key):
    """명부 관리 창의 입장 코드 만들기. 코드는 이 응답에만 나온다."""
    m = get_in_space_or_404(Member, request, key=key)
    ctx = {"space": request.space, "m": m}
    try:
        code, until = issue(m, request.user)
    except ValueError as e:
        ctx["error"] = str(e)
    else:
        MemberLog.objects.create(space=request.space, by=request.member.name, name=m.name, before="입장 코드", after="입장 코드 발급")
        url, end = f"{settings.SITE_URL}/enter", timezone.localtime(until)
        ctx.update(code=code, until=until, enter_url=url, msg=(
            f"[고티처] {m.name} 선생님 입장 코드예요.\n{code}\n\n"
            "아래 주소에서 코드를 넣고 로그인하거나 가입하면 학교 공간의 내 자리로 이어져요. "
            f"{end.month}월 {end.day}일까지 한 번만 쓸 수 있어요.\n{url}"))
    tpl = "spaces/_entry_code.html" if request.headers.get("HX-Request") else "spaces/entry_code.html"
    return render(request, tpl, ctx, status=400 if "error" in ctx else 200)


def _form(request, status=200, error=""):
    return render(request, "spaces/enter.html", {"stage": "code", "error": error}, status=status)


def hold(session, hashed):
    """확인한 코드의 해시를 세션에 잠깐 둔다. 세션은 브라우저를 닫으면 끝난다."""
    session[ENTRY] = {"hash": hashed, "until": int(time.time()) + HOLD_SECONDS}
    session.set_expiry(0)


def _held(request):
    """세션에 든 코드 해시. 손님이거나 시간이 지났으면 None."""
    value = request.session.get(ENTRY)
    if not value or is_guest(request.user):
        return None
    if value["until"] <= time.time():
        request.session.pop(ENTRY, None)
        return None
    return value["hash"]


def _see_code(request):
    """입력한 코드 확인. 맞으면 해시만 세션에 두고 다음 단계로. 시도 제한: 세션·계정은 촘촘히, IP는 학교 전체가 막히지 않게 높은 상한만."""
    if is_guest(request.user):
        return _form(request, 403, GUEST_HERE)
    ip = client_ip(request)
    if not allow("entry-ip", ip, FAILS_IP, WINDOW, count=False, fallback=True):
        return _form(request, 429, LIMITED)
    if not request.session.session_key:
        request.session.create()
    limits = [("entry-ip", ip, FAILS_IP), ("entry-session", request.session.session_key, FAILS_SESSION)]
    if request.user.is_authenticated:
        limits.append(("entry-user", str(request.user.pk), FAILS_USER))
    if not all(allow(scope, ident, limit, WINDOW, count=False, fallback=True) for scope, ident, limit in limits):
        return _form(request, 429, LIMITED)
    plain = _plain(request.POST.get("code", ""))
    hashed = code_hash(plain)
    if len(plain) != CODE_LEN or not set(plain) <= set(CODE_CHARS) or _find(hashed) is None:
        for scope, ident, limit in limits:
            allow(scope, ident, limit, WINDOW, fallback=True)
        return _form(request, 400, BAD_CODE)
    hold(request.session, hashed)
    response = redirect("/enter")
    response.status_code = 303
    return response


def _claim(request, hashed):
    try:
        seat = claim(hashed, request.user)
    except ValueError as e:
        code = _find(hashed) if str(e) == HAS_SEAT else None
        if code is None:
            request.session.pop(ENTRY, None)
            return _form(request, 400, BAD_CODE)
        return render(request, "spaces/enter.html", {"stage": "confirm", "m": code.member, "error": HAS_SEAT}, status=409)
    remember = request.session.pop(ENTRY, {}).get("remember", False)
    response = redirect(f"/s/{seat.space.token}/school")
    response.status_code = 303
    return remember_pc(request, response, request.user, True) if remember else response


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def enter(request):
    """코드 넣기 → (로그인·가입) → 이 자리를 내 계정에 잇기. 로그인·구글·가입은 기존 화면을 쓰고 next=/enter로 돌아온다."""
    user = request.user
    hashed = _held(request)
    if request.method == "POST":
        if "code" in request.POST:
            return _see_code(request)
        return _claim(request, hashed) if hashed and is_account_member(user) else redirect("/enter")
    code = _find(hashed)
    if code is None:
        request.session.pop(ENTRY, None)
        return _form(request)
    ctx = {"m": code.member, "next": "/enter"}
    if not user.is_authenticated:
        return render(request, "spaces/enter.html", {**ctx, "stage": "login"})
    error = HAS_SEAT if Member.objects.filter(user=user).exists() else ""
    return render(request, "spaces/enter.html", {**ctx, "stage": "confirm", "error": error})
