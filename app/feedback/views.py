"""V6 의견 보내기·예전 고티처로 돌아가는 이유·운영자 의견함.

- 보내기·돌아가기는 공간 주소(/s/<토큰>/feedback…)라 SpaceMiddleware가 회원 확인을 끝낸 뒤다. 손님은 403.
- 저장하는 값: 종류·글·경로 모양·화면 크기. 화면 내용은 받지 않고, 글은 로그·오류 문구에 남기지 않는다.
- 운영자에게 따로 알리지 않는다(메일·메신저 없음). 의견함(/ops/feedback)과 manage.py feedback으로 읽는다.
"""
import re
from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.urls import Resolver404, resolve
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from accounts.models import is_account_member
from config.ratelimit import TOO_MANY, allow
from library.views import _frame
from spaces.models import Space

from .models import TEXT_MAX, Feedback

PER_WINDOW = 5  # 한 사람이 10분에 보내는 의견·돌아가는 이유 합
WINDOW = 600
THANKS = "고마워요, 꼭 읽을게요."
SAY = {Feedback.Kind.BAD, Feedback.Kind.BUG, Feedback.Kind.WISH, Feedback.Kind.GOOD}
BACK = {k for k in Feedback.Kind.values if k.startswith("back_")} - {Feedback.Kind.BACK_SKIP}
SCREEN = re.compile(r"\d{3,5}x\d{3,5}")
PARAM = re.compile(r"<(?:\w+:)?(\w+)>")
PAGE = 50


def keep_text(days=None):
    """처리방침의 보관 기간 글자(기본 의견, V10 이용 기록도): 365의 배수면 'n년', 아니면 'n일'."""
    days = days or settings.FEEDBACK_KEEP_DAYS
    return f"{days // 365}년" if days % 365 == 0 else f"{days}일"


def path_shape(raw):
    """화면 주소 → 주소 규칙 모양('/s/<공간>/c/<key>/'). 토큰·키·쿼리·조각은 남지 않는다. 모르는 주소는 빈 값."""
    path = urlsplit(str(raw)[:500]).path
    if not path.startswith("/"):
        return ""
    try:
        return route_shape(resolve(path).route)
    except Resolver404:
        return ""


def route_shape(route):
    """주소 규칙('s/<str:space_token>/c/<str:key>/') → 화면에 보일 모양('/s/<공간>/c/<key>/'). V10 이용 기록도 같이 쓴다."""
    return ("/" + PARAM.sub(lambda m: "<공간>" if m.group(1) == "space_token" else f"<{m.group(1)}>", route))[:200]


def _text(request):
    return request.POST.get("text", "").replace("\x00", "").replace("\r\n", "\n").strip()


def _save(request, kind, text):
    screen = request.POST.get("screen", "")
    Feedback.objects.create(space=request.space, member=request.member, kind=kind, text=text,
                            path=path_shape(request.POST.get("path", "")), screen=screen if SCREEN.fullmatch(screen) else "")


def account_only(view):
    """계정 회원(구글·아이디)만. 손님은 403."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not is_account_member(request.user):
            return HttpResponseForbidden()
        return view(request, *args, **kwargs)
    return inner


def _msg(text, status):
    return HttpResponse(f'<p class="field-err" role="alert">{text}</p>', status=status, content_type="text/html; charset=utf-8")


@never_cache
@require_POST
@account_only
def send(request):
    """의견 한 건. 성공하면 고마움 토스트(gt.js toastNow), 오류는 대화상자 안 한 줄."""
    kind, text = request.POST.get("kind", ""), _text(request)
    if kind and kind not in SAY:
        return _msg("종류를 다시 골라 주세요.", 400)
    if not text:
        return _msg("의견을 한 줄 써 주세요.", 400)
    if len(text) > TEXT_MAX:
        return _msg(f"의견은 {TEXT_MAX:,}자까지예요.", 400)
    if not allow("feedback", request.user.pk, PER_WINDOW, WINDOW, fallback=True):
        return _msg(TOO_MANY, 429)
    _save(request, kind, text)
    return HttpResponse(f'<span data-toast-now="{THANKS}"></span>', content_type="text/html; charset=utf-8")


@never_cache
@require_POST
@account_only
def back(request):
    """'예전 고티처로' 이유 하나(고르지 않거나 건너뛰면 건너뜀). 동시 사용 중인 공간에서만. 막혀도 이동은 화면이 한다."""
    if not request.space.legacy_readonly:
        raise Http404
    kind, text = request.POST.get("kind", ""), _text(request)[:TEXT_MAX]
    if "skip" in request.POST or kind not in BACK:
        kind, text = Feedback.Kind.BACK_SKIP, ""
    if allow("feedback", request.user.pk, PER_WINDOW, WINDOW, fallback=True):
        _save(request, kind, text)
    return HttpResponse(status=204)


def staff_only(view):
    """운영자(is_staff)만. 그 외(비로그인 포함)는 주소가 없는 것처럼 404."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_staff and request.user.is_active):
            raise Http404
        return view(request, *args, **kwargs)
    return inner


@never_cache
@require_safe
@staff_only
def inbox(request):
    """운영자 의견함: 최근순, 종류(하나·돌아감 전체)·공간 이름으로 거르기."""
    k, s = request.GET.get("k", ""), request.GET.get("s", "")
    rows = Feedback.objects.select_related("space", "member").order_by("-created_at", "-pk")
    if k == "back":
        rows = rows.filter(kind__startswith="back_")
    elif k == "none":
        rows = rows.filter(kind="")
    elif k in Feedback.Kind.values and k:
        rows = rows.filter(kind=k)
    else:
        k = ""
    if s:
        rows = rows.filter(space__name=s)
    spaces = Space.objects.filter(feedback__isnull=False).values_list("name", flat=True).distinct().order_by("name")
    kinds = [("", "전체"), ("none", "고르지 않음"), *[(x, Feedback.Kind(x).label) for x in sorted(SAY, key=Feedback.Kind.values.index)], ("back", "돌아감 전체"),
             *[(x, Feedback.Kind(x).label) for x in Feedback.Kind.values if x.startswith("back_")]]
    page = Paginator(rows, PAGE).get_page(request.GET.get("p"))
    return render(request, "feedback/inbox.html", {**_frame(request, page="feedback"), "page_obj": page, "kinds": kinds, "k": k, "s": s, "spaces": list(spaces)})
