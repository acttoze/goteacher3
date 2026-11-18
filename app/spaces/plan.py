"""O1 학교 플랜 소개·신청·견적서. PG 없음: 신청 → 견적서 인쇄 → 계좌이체·계산서는 사람 → 운영자가 school_plan --on으로 켠다.

신청에는 공간이 없을 수 있어 신청자(user)로 좁혀 찾는다(남의 견적서는 404). 가격·공급자 값은 설정에서만 읽고 없으면 손으로 적을 빈칸으로 둔다.
학교 값은 내 공간 또는 서버가 받은 NEIS 검색 결과에서만 가져온다(브라우저가 보낸 이름은 믿지 않는다). 받은 값은 로그에 남기지 않는다.
W1 school_start: 동학년 대표가 학교 공간을 1년 무료로 직접 연다. 신청·견적과 별개이고, 직접 연 학교도 위 신청은 그대로 할 수 있다.
"""
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from accounts.models import is_guest
from config.ratelimit import too_many

from . import neis
from .merge import free_until, open_free
from .models import Member, PlanRequest, School, Space, leads
from .views import HITS, NEIS_DOWN, NEIS_PER_MIN, OFFICES, _clean_name, _josa, _my_space, _space_ctx, read_org, school_pitch, space_pitch

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
    if not school.writable or school.self_opened_at:  # 직접 연 학교는 정식 신청을 막지 않는다(W1)
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


def _pitch(request):
    """제목 아래 근거(P1). 기관 탭·기관 회원에게는 없고, 학교 플랜을 안 쓰는 동학년 회원에게는 같은 학교 수와 보낼 문구까지."""
    if request.GET.get("kind", request.POST.get("kind")) == "org":
        return {}
    seat = None
    if request.user.is_authenticated:
        seat = Member.objects.filter(user=request.user).select_related("space__school").order_by("-joined_at").first()
    if seat and seat.space.is_org:
        return {}
    return (seat and space_pitch(seat.space, seat)) or school_pitch()


def _ctx(request, **extra):
    user, ctx = request.user, {"price": _price(), "login_href": LOGIN_HREF, "pitch": _pitch(request), **extra}
    if not user.is_authenticated or is_guest(user):
        ctx["guest"] = user.is_authenticated
        return ctx
    mine = _my_space(user)
    # T1 기관: 체험이 끝난 기관 회원, 또는 공간 없이 기관으로 신청(시도·이름 직접 입력, 견적서)
    org = mine.school.is_org if mine else request.GET.get("kind", request.POST.get("kind")) == "org"
    ctx.update(can_apply=True, my_school=mine.school if mine else None, kind="org" if org else "school", offices=OFFICES,
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
    if is_guest(user):  # 관리자는 계정 회원만 된다
        return render(request, "spaces/plan.html", _ctx(request), status=403)
    values, errors = _read(request.POST)
    mine = _my_space(user)
    org = None
    if not mine and request.POST.get("kind") == "org":  # 기관: NEIS 검색 대신 시도·이름(T1)
        org, org_errors = read_org(request.POST)
        errors.update(org_errors)
    hit = None if mine or org else _picked(request)
    if not mine and not org and hit is None:
        errors["school"] = "학교를 찾아 골라 주세요."
    school = mine.school if mine else School.objects.filter(neis_atpt=hit["atpt"], neis_code=hit["code"]).first() if hit else None
    if org and not errors:  # 같은 사람이 같은 기관으로 대기 신청을 다시 보내면 그 신청을 고친다
        same = PlanRequest.objects.filter(user=user, status=PlanRequest.Status.PENDING, school__neis_code="",
                                          school__neis_atpt=org["atpt"], school__name=org["org"]).select_related("school").first()
        school = same.school if same else None

    def again(status, **extra):
        if org:
            return render(request, "spaces/plan.html", _ctx(request, values={**values, **org}, errors=errors, **extra), status=status)
        ctx = _search(request, _ctx(request, values=values, errors=errors, **extra))
        return render(request, "spaces/plan.html", ctx, status=status)

    if errors:
        return again(400)
    if school and _already_on(school):
        return again(409, already=ALREADY)
    if too_many("plan-req", str(user.pk), REQUESTS_PER_HOUR, 3600):
        return again(429, limited="신청을 너무 자주 보냈어요. 1시간 뒤에 다시 해 주세요.")
    with transaction.atomic():
        if school is None and org:
            school = School.objects.create(neis_atpt=org["atpt"], neis_code="", name=org["org"])
        elif school is None:
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
    if ctx.get("can_apply") and not ctx["my_school"] and ctx["kind"] != "org":
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


def _to_school(hub):
    resp = redirect(f"/s/{hub.token}/school")
    resp.status_code = 303
    return resp


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def school_start(request):
    """학교 공간 1년 무료로 열기(W1): 확인 화면 → 체크 → 이 동학년만 옮긴다. 자격은 open_free가 잠근 뒤 다시 본다.
    이미 옮겨진 뒤의 두 번째 POST는 SpaceMiddleware가 학교 공간으로 넘긴다."""
    space, me = request.space, request.member
    if space.is_school:
        return redirect(f"/s/{space.token}/")
    if is_guest(request.user):
        state, status = "guest", 403
    elif not leads(space, me):
        state, status = "not_lead", 403
    elif Space.objects.filter(school=space.school, grade=0).exists():
        state, status = "opened", 409
    else:
        state, status = "ready", 400
    error = ""
    if request.method == "POST" and state == "ready":
        if request.POST.get("agree") != "1":
            error = "내용을 확인했다고 체크해 주세요."
        else:
            try:
                return _to_school(open_free(space, me))
            except ValueError:  # 같이 누른 요청이나 다른 학년 대표가 먼저 열었다
                seat = Member.objects.filter(pk=me.pk).select_related("space").first()
                if seat is None:
                    raise Http404
                if seat.space.is_school:
                    return _to_school(seat.space)
                state, status = "opened", 409
    ctx = _space_ctx(request, state=state, error=error, until=free_until(timezone.localdate()))
    ctx["n_guests"] = sum(r["guest"] for r in ctx["rows"])
    return render(request, "spaces/school_start.html", ctx, status=status if request.method == "POST" else 200)
