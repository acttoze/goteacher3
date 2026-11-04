"""K2 업체 계정. 업체 쪽 /v/(기기 쿠키)와 교내 쪽 발급·해제·기록·미리보기.

업체 쿠키는 이 파일의 /v/ 뷰만 읽는다. Django 로그인·Member를 만들지 않으므로 /s/ 주소는 SpaceMiddleware가 그대로 막고,
쿠키 경로도 /v/라 브라우저가 교내 주소에는 보내지 않는다. 거꾸로 /v/ 뷰는 request.user를 보지 않는다(회원 권한이 섞이지 않게).
조회: 입장·쿠키 확인만 해시로 계정을 찾고, 그 뒤 요청은 전부 VendorAccount.repairs()(그 공간 + 정보 + 발급 뒤)로만 찾는다.
요청자 이름이 화면에 갈 수 없도록 _rows는 보여 줄 칸만 꺼내고(by·by_name을 읽지 않는다), 제목·위치·메모·답변의 명부 이름은 가린다.
Referrer-Policy: 토큰이 주소에 든 /v/<token>만 no-referrer. /v/ 화면은 same-origin이어야 한다(no-referrer면 폼 제출의
Origin이 null이 되어 CSRF가 막는다). 업체 화면 주소에는 비밀이 없고 same-origin은 밖으로 Referer를 보내지 않는다.
"""
import re
from datetime import datetime, time, timedelta
from functools import wraps

from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, IntegerField, When
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.models import hash_token
from collect.models import Notification
from config.ratelimit import TOO_MANY, allow, client_ip
from notices.views import _clean
from spaces.gates import school_required, school_write_required
from spaces.models import Member, MemberLog, Space
from spaces.views import _space_ctx

from .models import Repair, VendorAccess, VendorAccount
from .views import MAX_REPLY, PER_PAGE, _can_handle, change_state, list_page, photo_response, VENDOR_MAX_DAYS

COOKIE = "gt_vendor"
COOKIE_AGE = timedelta(days=30)  # 공용 PC에 오래 남지 않게. 화면을 열 때마다 늘어나고 사용 기간을 넘지 않는다
ENTER_PER_MIN, STATE_PER_MIN, PHOTO_PER_MIN = 20, 60, 300
VIEW_GAP = timedelta(minutes=10)  # 새로 고침마다 쌓지 않는다: 화면 열기만, 10분 안에 다시 연 것은 기록하지 않음
LOG_PER_PAGE, LOG_AGE = 100, timedelta(days=30)
MAX_NAME, MAX_CONTACT = 100, 40
CLOSED = "이 링크로는 들어올 수 없어요."
PLAIN = "text/plain; charset=utf-8"
MASK = "○○○"


def _vendor_page(referrer="same-origin"):
    """업체 쪽 모든 응답: 검색 제외, Referer 정책, 캐시 금지. 없는 요청(지워졌거나 범위 밖)도 같은 헤더로 404."""
    def wrap(view):
        @wraps(view)
        def inner(request, *args, **kwargs):
            try:
                resp = view(request, *args, **kwargs)
            except Http404:
                resp = _refresh() if request.headers.get("HX-Request") else HttpResponseNotFound("이 요청을 찾을 수 없어요.", content_type=PLAIN)
            resp["X-Robots-Tag"] = "noindex, nofollow"
            resp["Referrer-Policy"] = referrer
            add_never_cache_headers(resp)
            return resp
        return inner
    return wrap


def _refresh():
    """열어 둔 화면에서 누른 경우: 조각을 끼우지 않고 페이지를 다시 연다."""
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    return resp


def _closed(request, message=CLOSED, status=404):
    """없는 토큰·재발급·해제·기간 만료·플랜 만료가 모두 같은 화면이다(학교 이름도, 이유도 알려 주지 않는다)."""
    if request.headers.get("HX-Request"):
        return _refresh()
    return render(request, "repairs/vendor/closed.html", {"message": message}, status=status)


def _from_cookie(request):
    raw = request.COOKIES.get(COOKIE)
    if not raw or len(raw) > 128:
        return None
    account = VendorAccount.objects.select_related("space__school").filter(cookie_hash=hash_token(raw)).first()
    return account if account and account.is_open else None


def _set_cookie(resp, value, account):
    """사용 기간 마지막 날 자정과 30일 중 빠른 쪽까지."""
    end = timezone.make_aware(datetime.combine(account.until + timedelta(days=1), time.min))
    age = min(COOKIE_AGE, end - timezone.now())
    resp.set_cookie(COOKIE, value, max_age=int(age.total_seconds()), path="/v/", httponly=True, samesite="Lax",
                    secure=settings.SITE_URL.startswith("https://"))


def _record(account, action, repair_key=""):
    now = timezone.now()
    if action == VendorAccess.Action.VIEW and account.last_seen_at and now - account.last_seen_at < VIEW_GAP:
        return
    VendorAccess.objects.create(account=account, action=action, repair_key=repair_key)
    VendorAccount.objects.filter(pk=account.pk).update(last_seen_at=now)


def _masker(space):
    """제목·위치·메모·답변에 든 명부 이름(나간 요청자 포함)을 가리는 함수. 한 글자 이름은 낱말과 섞여 가리지 않는다."""
    names = set(Member.objects.in_space(space).values_list("name", flat=True))
    names.update(Repair.objects.in_space(space).values_list("by_name", flat=True))
    names = sorted((n for n in names if len(n) >= 2), key=len, reverse=True)
    if not names:
        return lambda text: text
    rx = re.compile("|".join(map(re.escape, names)))
    return lambda text: rx.sub(MASK, text)


def _rows(rows):
    """보여 줄 칸만."""
    return rows.values("key", "title", "place", "note", "urgent", "status", "reply", "photo", "created_at", "updated_at")


def _masked(row, mask):
    return {**row, **{k: mask(row[k]) for k in ("title", "place", "note", "reply")}}


def _body_ctx(space, rows, get_page, photo_base, readonly):
    """업체 화면과 미리보기가 같은 답을 쓴다. 완료는 뒤로."""
    page = Paginator(_rows(rows).annotate(done=Case(When(status=Repair.Status.DONE, then=1), default=0, output_field=IntegerField()))
                     .order_by("done", "-created_at", "-pk"), PER_PAGE).get_page(get_page)
    mask = _masker(space)
    page.object_list = [_masked(r, mask) for r in page.object_list]
    return {"reqs": page, "n_open": rows.exclude(status=Repair.Status.DONE).count(), "school_name": space.school.name,
            "statuses": Repair.Status.choices, "photo_base": photo_base, "readonly": readonly, "max_reply": MAX_REPLY}


# ---------------------------------------------------------------- 업체 쪽 /v/
@_vendor_page("no-referrer")
@require_GET
def enter(request, token):
    """/v/<token>: 토큰을 쿠키로 바꾸고 곧바로 /v/로 옮긴다(토큰이 주소·Referer에 남지 않게)."""
    if not allow("vendor-in", client_ip(request), ENTER_PER_MIN, 60):
        return _closed(request, TOO_MANY, status=429)
    account = None
    if len(token) <= 128:  # 입장 지점: 토큰 자체가 계정·공간을 정한다
        account = VendorAccount.objects.select_related("space__school").filter(token_hash=hash_token(token)).first()
    if account is None or not account.is_open:
        return _closed(request)
    _record(account, VendorAccess.Action.ENTER)
    resp = redirect("/v/")
    _set_cookie(resp, VendorAccount.cookie_value(token), account)
    return resp


@_vendor_page()
@require_GET
def home(request):
    account = _from_cookie(request)
    if account is None:
        return _closed(request)
    _record(account, VendorAccess.Action.VIEW)
    resp = render(request, "repairs/vendor/home.html",
                  {"account": account, **_body_ctx(account.space, account.repairs(), request.GET.get("page"), "/v/r", False)})
    _set_cookie(resp, request.COOKIES[COOKIE], account)
    return resp


@_vendor_page()
@require_POST
def state(request, key):
    """상태·답변. CSRF는 전역 미들웨어가 본다(쿠키 인증이라 그대로 필요하다). 요청자 🔔은 change_state가 보낸다."""
    account = _from_cookie(request)
    if account is None:
        return _closed(request)
    if not allow("vendor-state", account.pk, STATE_PER_MIN, 60):
        return HttpResponse(TOO_MANY, status=429, content_type=PLAIN)
    status, reply = request.POST.get("status"), _clean(request.POST, "reply")
    if status not in Repair.Status.values or len(reply) > MAX_REPLY:
        return HttpResponseBadRequest(f"상태를 고르고, 답변은 {MAX_REPLY}자까지 적어 주세요.")
    rows, mask = account.repairs(), _masker(account.space)
    old = get_object_or_404(rows, key=key).reply
    if reply == mask(old):  # 가려서 보여 준 답변이 그대로 돌아온 것: 원문을 ○○○로 덮어쓰지 않는다
        reply = old
    r = change_state(account.space, key, status, reply, actor=None, rows=rows)
    _record(account, VendorAccess.Action.STATE, r.key)
    if request.headers.get("HX-Request"):
        return render(request, "repairs/vendor/_row_swap.html", {
            "r": _masked(_rows(rows).get(pk=r.pk), mask), "statuses": Repair.Status.choices, "photo_base": "/v/r", "readonly": False, "max_reply": MAX_REPLY,
            "n_open": rows.exclude(status=Repair.Status.DONE).count(),
            "toast": f"{r.get_status_display()}(으)로 저장했어요. 상태가 바뀌면 요청한 선생님께 알림이 가요."})
    resp = redirect("/v/")
    resp.status_code = 303
    return resp


@_vendor_page("no-referrer")
@require_GET
def photo(request, key):
    """정보 요청의 사진만. 끊긴 뒤 브라우저 캐시로 보이지 않게 캐시 금지. 열어 본 기록을 남긴다."""
    account = _from_cookie(request)
    if account is None:
        return _closed(request)
    if not allow("vendor-photo", account.pk, PHOTO_PER_MIN, 60):
        return HttpResponse(TOO_MANY, status=429, content_type=PLAIN)
    resp = photo_response(get_object_or_404(account.repairs(), key=key))
    _record(account, VendorAccess.Action.PHOTO, key)
    return resp


@_vendor_page()
@require_POST
def out(request):
    """나가기(공용 PC): 이 브라우저의 쿠키만 지운다. 링크는 그대로 살아 있다."""
    resp = render(request, "repairs/vendor/closed.html", {"message": "나왔어요.", "left": True})
    resp.delete_cookie(COOKIE, path="/v/", samesite="Lax")
    return resp


# ---------------------------------------------------------------- 교내 쪽 /s/<token>/repairs/vendor
def _handler_required(view):
    """관리자·수리 담당자만(수리 처리와 같은 권한). 그 밖의 회원은 403."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not _can_handle(request.member):
            return HttpResponseForbidden()
        return view(request, *args, **kwargs)
    return inner


def _read_form(data):
    form = {"name": _clean(data, "name"), "contact_name": _clean(data, "contact_name"), "until": _clean(data, "until")}
    errors = {}
    if not form["name"] or len(form["name"]) > MAX_NAME:
        errors["name"] = f"업체 이름을 {MAX_NAME}자 안으로 적어 주세요."
    if not form["contact_name"] or len(form["contact_name"]) > MAX_CONTACT:
        errors["contact_name"] = f"담당자 이름을 {MAX_CONTACT}자 안으로 적어 주세요."
    try:
        until = parse_date(form["until"])
    except ValueError:
        until = None
    today = timezone.localdate()
    if until is None or not today <= until <= today + VENDOR_MAX_DAYS:
        errors["until"] = "사용 기간은 오늘부터 1년 안의 날짜로 골라 주세요."
    return form, until, errors


def _audit(request, account, before, after):
    """발급·재발급·해제: 권한 기록 + 관리자 모두에게 🔔(링크가 몰래 만들어지지 않게). 부르는 쪽 트랜잭션 안."""
    space, me = request.space, request.member
    MemberLog.objects.create(space=space, by=me.name, name=account.name[:40], before=before, after=after)
    Notification.objects.bulk_create(
        Notification(space=space, member=m, kind=Notification.Kind.VENDOR, day=timezone.localdate(), pushed=True,
                     text=f"정보 업체 {account.name}: {after} ({me.name})"[:300], link=f"/s/{space.token}/repairs")
        for m in Member.objects.in_space(space).filter(is_manager=True))


@never_cache
@require_POST
@school_required
@_handler_required
@school_write_required
def issue(request):
    """발급·재발급: 옛 계정을 해제하고 새 행. 옛 링크·쿠키는 커밋과 동시에 끊긴다. 입력이 틀리면 옛 계정은 그대로.
    새 링크는 이 응답에서 한 번만 보인다. 목록 화면 전체를 돌려줘 '누가 받나요'·담당 칸도 함께 바뀐다."""
    form, until, errors = _read_form(request.POST)
    if errors:
        return list_page(request, status=400, vendor_form=form, errors=errors)
    with transaction.atomic():
        Space.objects.select_for_update().get(pk=request.space.pk)  # 동시에 눌러도 살아 있는 계정은 하나
        old = VendorAccount.current(request.space)
        if old:  # 재발급(담당자 교체): 보이는 범위를 이어받는다. 해제한 뒤 새로 발급하면 그 시각부터
            VendorAccount.objects.filter(pk=old.pk).update(revoked_at=timezone.now())
        account, token = VendorAccount.issue(request.space, form["name"], form["contact_name"], until, request.member,
                                             visible_from=old.visible_from if old else None)
        _audit(request, account, "링크 있음" if old else "없음", "새 링크 발급" if old else "링크 발급")
    return list_page(request, link=f"{settings.SITE_URL}/v/{token}")


@never_cache
@require_POST
@school_required
@_handler_required
def revoke(request):
    """해제: 끊는 일이라 플랜이 끝난 학교도 받는다(플랜을 다시 켰을 때 살아나지 않게)."""
    with transaction.atomic():
        Space.objects.select_for_update().get(pk=request.space.pk)
        old = VendorAccount.current(request.space)
        if old:
            VendorAccount.objects.filter(pk=old.pk).update(revoked_at=timezone.now())
            _audit(request, old, "링크 있음", "접근 끊음")
    return list_page(request, toast="업체 접근을 끊었어요.")


@never_cache
@require_GET
@school_required
@_handler_required
def access_log(request):
    """누가 언제 무엇을. 탈취를 알아볼 수 있게 상태 변경·사진 열람은 어느 요청인지 제목으로 보여 준다."""
    entries = (VendorAccess.objects.in_space(request.space).filter(at__gte=timezone.now() - LOG_AGE)
               .select_related("account").order_by("-at", "-pk"))
    page = Paginator(entries, LOG_PER_PAGE).get_page(request.GET.get("page"))
    shown = list(page.object_list)
    titles = dict(Repair.objects.in_space(request.space).filter(key__in={a.repair_key for a in shown if a.repair_key}).values_list("key", "title"))
    for a in shown:
        a.title = titles.get(a.repair_key, "지워진 요청") if a.repair_key else ""
    page.object_list = shown
    return render(request, "repairs/vendor/log.html", _space_ctx(request, page="board", entries=page))


@never_cache
@require_GET
@school_required
@_handler_required
def preview(request):
    """업체가 보는 화면 그대로(읽기 전용). 사진은 회원 주소로, 접속 기록은 남기지 않는다. 계정이 없으면 보일 요청도 없다."""
    account = VendorAccount.current(request.space)
    rows = account.repairs() if account else Repair.objects.none()
    body = _body_ctx(request.space, rows, request.GET.get("page"), f"/s/{request.space.token}/repairs", True)
    return render(request, "repairs/vendor/preview.html", _space_ctx(request, page="board", account=account, **body))
