"""O1 학교 플랜 소개·신청·견적서. PG 없음: 신청 → 견적서 인쇄 → 계좌이체·계산서는 사람 → 운영자가 school_plan --on으로 켠다.

신청에는 공간이 없을 수 있어 신청자(user)로 좁혀 찾는다(남의 견적서는 404). 가격·공급자 값은 설정에서만 읽고 없으면 손으로 적을 빈칸으로 둔다.
학교 값은 내 공간 또는 서버가 받은 NEIS 검색 결과에서만 가져온다(브라우저가 보낸 이름은 믿지 않는다). 받은 값은 로그에 남기지 않는다.
"""
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from accounts.models import is_guest
from config.ratelimit import too_many

from . import neis
from .models import Member, PlanRequest, School
from .views import HITS, NEIS_DOWN, NEIS_PER_MIN, _clean_name, _josa, _my_space

LOGIN_HREF = "/start?next=/school-plan"
RENEW_DAYS = 30  # 기한이 이만큼 남으면 다시 신청할 수 있다(만료 안내와 같은 날)
REQUESTS_PER_HOUR = 5
ALREADY = "이 학교는 이미 학교 플랜을 쓰고 있어요."


def _won(raw):
    """쉼표 넣은 금액. 숫자가 아니거나 비었으면 빈 값(지어내지 않는다)."""
    raw = raw.replace(",", "")
    return f"{int(raw):,}원" if raw.isdecimal() else ""


def _price():
    return _won(settings.SCHOOL_PLAN_PRICE)


def _already_on(school):
    if not school.writable:
        return False
    return school.days_left is None or school.days_left > RENEW_DAYS


def _count(raw):
    """선택 칸: 빈 값은 None, 1~99999만. 그 밖은 ValueError."""
    raw = raw.strip()
    if not raw:
        return None
    if not raw.isdecimal() or not 0 < int(raw) < 100000:
        raise ValueError
    return int(raw)


def _read(data):
    values, errors = {}, {}
    values["by_name"] = _clean_name(data.get("by_name"))
    values["position"] = _clean_name(data.get("position"))
    values["email"] = data.get("email", "").strip()[:254]
    if not values["by_name"]:
        errors["by_name"] = "담당자 이름을 써 주세요."
    if not values["position"]:
        errors["position"] = "직위를 써 주세요. 예: 교무부장"
    try:
        validate_email(values["email"])
    except ValidationError:
        errors["email"] = "받을 수 있는 메일 주소를 써 주세요."
    for key, label in (("students", "학생 수"), ("classes", "학급 수")):
        try:
            values[key] = _count(data.get(key, ""))
        except ValueError:
            errors[key] = f"{label}는 숫자만 써 주세요. 안 써도 돼요."
    return values, errors


def _picked(request):
    """검색 결과에서 고른 학교(서버가 받은 값). 없으면 None."""
    hits = request.session.get(HITS) or []
    raw = request.POST.get("i", "")
    return hits[int(raw)] if raw.isdecimal() and len(raw) < 4 and int(raw) < len(hits) else None


def _ctx(request, **extra):
    user, ctx = request.user, {"price": _price(), "login_href": LOGIN_HREF, **extra}
    if not user.is_authenticated or is_guest(user):
        ctx["guest"] = user.is_authenticated
        return ctx
    mine = _my_space(user)
    ctx.update(can_apply=True, my_school=mine.school if mine else None,
               sent=list(PlanRequest.objects.filter(user=user).select_related("school").order_by("-created_at")[:3]))
    ctx["pending"] = next((r for r in ctx["sent"] if r.status == PlanRequest.Status.PENDING), None)
    ctx["sent"] = [r for r in ctx["sent"] if r is not ctx["pending"]]
    if mine and _already_on(mine.school):
        ctx["already"] = ALREADY
    if "values" not in ctx:  # 처음 열 때: 아는 값으로 채운다
        seat = Member.objects.filter(user=user).order_by("-joined_at").first()
        ctx["values"] = {"by_name": seat.name if seat else user.first_name, "position": seat.position if seat else "", "email": user.email}
    return ctx


def _search(request, ctx):
    q = request.GET.get("q", "").strip()[:40]
    ctx["q"] = q
    if not q:
        ctx["schools"] = request.session.get(HITS) if ctx.get("errors") else None
    elif len(q) < 2:
        ctx["search_error"] = "두 글자 이상 써 주세요."
    elif too_many("neis", str(request.user.pk), NEIS_PER_MIN):
        ctx["search_error"] = "검색을 너무 자주 했어요. 1분 뒤에 다시 찾아 주세요."
    else:
        ctx["schools"], total = neis.search_schools(q)
        request.session[HITS] = ctx["schools"]
        ctx.update(more=total > len(ctx["schools"]), total=total, josa=_josa(q, "으로", "로"))
        if neis.failed(ctx["schools"]):
            ctx["search_error"] = NEIS_DOWN
    ctx["checked"] = request.POST.get("i", "0" if len(ctx.get("schools") or ()) == 1 else "")
    return ctx


def _apply(request):
    user = request.user
    if not user.is_authenticated:
        return redirect(LOGIN_HREF)
    if is_guest(user):  # 관리자는 구글 회원만 된다
        return render(request, "spaces/plan.html", _ctx(request), status=403)
    values, errors = _read(request.POST)
    mine = _my_space(user)
    hit = None if mine else _picked(request)
    if not mine and hit is None:
        errors["school"] = "학교를 찾아 골라 주세요."
    school = mine.school if mine else School.objects.filter(neis_atpt=hit["atpt"], neis_code=hit["code"]).first() if hit else None

    def again(status, **extra):
        ctx = _search(request, _ctx(request, values=values, errors=errors, **extra))
        return render(request, "spaces/plan.html", ctx, status=status)

    if errors:
        return again(400)
    if school and _already_on(school):
        return again(409, already=ALREADY)
    if too_many("plan-req", str(user.pk), REQUESTS_PER_HOUR, 3600):
        return again(429, limited="신청을 너무 자주 보냈어요. 1시간 뒤에 다시 해 주세요.")
    with transaction.atomic():
        if school is None:
            school, _ = School.objects.get_or_create(neis_atpt=hit["atpt"], neis_code=hit["code"], defaults={"name": hit["name"]})
        seat = Member.objects.filter(user=user, space__school=school).first()
        req, _ = PlanRequest.objects.update_or_create(  # 같은 학교 대기 신청은 하나로 고친다
            school=school, user=user, status=PlanRequest.Status.PENDING, defaults={**values, "member": seat})
    resp = redirect(f"/school-plan/q/{req.key}")
    resp.status_code = 303
    return resp


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def page(request):
    if request.method == "POST":
        return _apply(request)
    ctx = _ctx(request)
    if ctx.get("can_apply") and not ctx["my_school"]:
        _search(request, ctx)
        if request.headers.get("HX-Request"):
            return render(request, "spaces/_plan_schools.html", ctx)
    return render(request, "spaces/plan.html", ctx)


@never_cache
@login_required
@require_GET
def quote(request, key):
    """견적서 A4 한 장. 신청한 사람만 연다."""
    req = get_object_or_404(PlanRequest.objects.select_related("school"), key=key, user=request.user)
    return render(request, "spaces/plan_quote.html", _quote_ctx(req))


def _quote_ctx(req):
    """값이 없는 칸은 빈칸(손으로 적는다). 문서 번호는 주소 토큰을 드러내지 않게 해시 앞자리."""
    days = settings.QUOTE_VALID_DAYS
    ctx = {"price": _price(), "vat": settings.SCHOOL_PLAN_VAT, "total": _won(settings.SCHOOL_PLAN_TOTAL),
           "valid_until": req.created_at + timedelta(days=int(days)) if days.isdecimal() else None,
           "supplier_name": settings.SUPPLIER_NAME, "supplier_ceo": settings.SUPPLIER_CEO, "supplier_biz_no": settings.SUPPLIER_BIZ_NO,
           "supplier_address": settings.SUPPLIER_ADDRESS, "supplier_account": settings.SUPPLIER_ACCOUNT}
    return {**ctx, "r": req, "has_blank": not all(ctx.values()),
            "doc_no": f"GT-{timezone.localtime(req.created_at):%Y%m%d}-{hashlib.sha256(req.key.encode()).hexdigest()[:6].upper()}"}
