"""K1 수리 요청(prototype board 수리 탭·repairNew·repairView): 요청·목록·상세·처리, 사진, 담당자 지정.

권한은 Member.can_repair·is_manager·by로만. Member.group은 본인이 고치는 값이라 '누가 받나요'를 나눌 때만 쓴다
(이미 지정된 담당자 안에서 행정실 그룹이면 시설, 나머지는 정보). 행정실로 옮긴다고 담당이 되거나 이름이 뜨지 않는다.
K2 업체 화면·발급은 repairs.vendor. 업체가 있어도 🔔은 교내 담당자가 받는다(업체에는 문자·알림이 없다).
"""
import logging
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, When
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import is_guest
from collect.models import Notification
from config.ratelimit import TOO_MANY, allow
from notices.models import Notice
from notices.views import _clean, _quota_error
from spaces.gates import manager_required, school_required, school_write_required
from spaces.models import Member, MemberLog, get_in_space_or_404
from spaces.school import school_body
from spaces.views import _space_ctx

from . import photo
from .models import Repair, VendorAccount

log = logging.getLogger(__name__)
PER_PAGE = 20
MAX_TITLE, MAX_PLACE, MAX_NOTE, MAX_REPLY = 200, 100, 1000, 500
UPLOAD_PER_HOUR = 30
DESK_NAMES = 3  # 담당 칸에 보이는 이름 수
TOAST = "repair_toast"  # 세션: 목록으로 옮긴 뒤 한 번 띄울 저장 결과
VENDOR_MAX_DAYS = timedelta(days=366)  # 업체 사용 기간은 한 번에 1년까지(계약이 이어지면 다시 발급)


def _vendor(space):
    """정보 요청을 받는 외부 업체 계정(업체가 지금 들어올 수 있을 때만) 또는 None."""
    account = VendorAccount.current(space)
    return account if account and account.is_open else None


def _vendor_sees(vendor, r):
    """이 요청이 업체 화면에 있나(VendorAccount.repairs와 같은 답)."""
    return bool(vendor) and r.cat == Repair.Cat.INFO and r.created_at >= vendor.visible_from


def _vendor_desk(vendor):
    return f"{vendor.name} (계약 업체)"


def _can_handle(member):
    return member.is_manager or member.can_repair


def _can_edit(member, r):
    return r.by_id == member.pk and r.status == Repair.Status.RECEIVED


def _can_delete(member, r):
    return member.is_manager or _can_edit(member, r)


def _desks(space):
    """분류별 받는 회원 {cat: (이름표, [회원])}. 🔔과 화면이 같은 답을 쓴다. 담당자가 없으면 관리자가 받는다."""
    people = list(Member.objects.in_space(space).filter(Q(can_repair=True) | Q(is_manager=True)).order_by("joined_at", "pk"))
    handlers = [m for m in people if m.can_repair]
    if not handlers:
        return {Repair.Cat.FACILITY: ("관리자", people), Repair.Cat.INFO: ("관리자", people)}
    office = [m for m in handlers if m.group == Member.Group.ADMIN]
    rest = [m for m in handlers if m.group != Member.Group.ADMIN]
    return {Repair.Cat.FACILITY: ("행정실", office) if office else ("담당", handlers),  # 없는 소속을 이름 앞에 붙이지 않는다
            Repair.Cat.INFO: ("정보 담당", rest) if rest else ("담당", handlers)}


def _desk_text(desk):
    label, people = desk
    names = [m.name for m in people[:DESK_NAMES]]
    more = f" 외 {len(people) - DESK_NAMES}명" if len(people) > DESK_NAMES else ""
    return f"{label} · {'·'.join(names)}{more}"


def _bell_text(r):
    place = f" — {r.place}" if r.place else ""
    return f"{r.title}{place}"[:300]


def _ring_desk(space, r, actor):
    """요청이 오면 그 분류의 담당자들에게 🔔 한 건씩(본인이 낸 요청은 뺀다). 수업 중 창 알림은 없고 웹 푸시 묶음으로만. 받은 사람 수를 돌려준다."""
    _, people = _desks(space)[r.cat]
    return len(Notification.objects.bulk_create(
        Notification(space=space, member=m, repair=r, kind=Notification.Kind.REPAIR, day=timezone.localdate(),
                     text=_bell_text(r), link=f"/s/{space.token}/repairs/{r.key}")
        for m in people if m.pk != actor.pk))


def _ring_up(space, r, member_id, text):
    """상태 🔔. 안 읽은 게 있으면 쌓지 않고 그 한 건을 고쳐 묶음에 다시 넣는다. 부르는 쪽이 r 행을 잠갔다."""
    mine = Notification.objects.in_space(space).filter(repair=r, member_id=member_id, kind=Notification.Kind.REPAIR_UP, read=False)
    if not mine.update(text=text[:300], pushed=False, created_at=timezone.now()):
        Notification.objects.create(space=space, member_id=member_id, repair=r, kind=Notification.Kind.REPAIR_UP,
                                    day=timezone.localdate(), text=text[:300], link=f"/s/{space.token}/repairs/{r.key}")


def change_state(space, key, status, reply, actor=None, rows=None):
    """상태·답변 저장. 트랜잭션 안에서 행을 잠근다. actor: 바꾼 회원(업체는 None). 요청자 본인이 바꾸면 🔔 없음.
    rows: 업체가 건드릴 수 있는 범위(VendorAccount.repairs). 잠그며 보므로 요청자가 그사이 분류를 시설로 고쳐도 건드리지 않는다.
    업체가 바꾸면 담당자의 '요청 옴'을 읽음 처리하지 않고(업체 쪽 움직임을 교내가 모르게 되지 않도록), 완료면 담당자에게도 🔔."""
    with transaction.atomic():
        r = get_object_or_404((Repair.objects.in_space(space) if rows is None else rows).select_for_update(), key=key)
        changed = r.status != status
        if r.reply != reply:
            r.reply_vendor = actor is None
        r.status, r.reply = status, reply
        r.save(update_fields=["status", "reply", "reply_vendor", "updated_at"])
        if changed:
            if actor is not None and status != Repair.Status.RECEIVED:  # 누가 맡았으니 다른 담당자의 '요청 옴'은 묶음 알림에 나가지 않게
                (Notification.objects.in_space(space).filter(repair=r, kind=Notification.Kind.REPAIR, read=False)
                 .update(read=True))
            if r.by_id and (actor is None or r.by_id != actor.pk):
                _ring_up(space, r, r.by_id, f"{r.title} — {r.get_status_display()}")
            if actor is None and status == Repair.Status.DONE:
                for m in _desks(space)[r.cat][1]:
                    if m.pk != r.by_id:  # 요청자는 위에서 받았다
                        _ring_up(space, r, m.pk, f"{r.title} — 업체가 완료로 바꿨어요")
    return r


# ---------------------------------------------------------------- 폼
def _default_place(me):
    """'4-2' → '4-2 교실', 교무실·행정실은 그 이름, 전담·기타는 정해진 자리가 없으니 비운다."""
    if not me.in_grade:
        return "" if me.group == Member.Group.SUPPORT else me.cls
    return f"{me.cls} 교실" if me.cls.partition("-")[2].isdigit() else ""


def _read_form(data):
    form = {"cat": data.get("cat", ""), "place": _clean(data, "place"), "title": _clean(data, "title"),
            "note": _clean(data, "note"), "urgent": "urgent" in data}
    errors = {}
    if form["cat"] not in Repair.Cat.values:
        errors["cat"] = "시설과 정보 중에서 골라 주세요."
    if not form["title"]:
        errors["title"] = "무엇이 안 되는지 한 줄만 적어 주세요."
    elif len(form["title"]) > MAX_TITLE:
        errors["title"] = f"{MAX_TITLE}자까지 적을 수 있어요."
    if len(form["place"]) > MAX_PLACE:
        errors["place"] = f"위치는 {MAX_PLACE}자까지예요."
    if len(form["note"]) > MAX_NOTE:
        errors["note"] = f"메모는 {MAX_NOTE:,}자까지예요."
    return form, errors


def _read_photo(request):
    """(재인코딩한 바이트 또는 None, 오류, 오류일 때 상태 코드). 빈도는 파일을 읽기 전에 센다. 큰 파일은 픽셀을 풀기 전에 거부."""
    f = request.FILES.get("photo")
    if f is None:
        return None, "", 400
    if not allow("repair-up", request.member.key, UPLOAD_PER_HOUR, 3600):
        return None, TOO_MANY, 429
    if f.size > photo.MAX_BYTES:
        return None, f"사진은 {photo.MAX_BYTES // photo.MB}MB까지 올릴 수 있어요. 크기를 줄여 다시 올려 주세요.", 400
    try:
        data = photo.reencode(f)
    except photo.BadPhoto as e:
        return None, str(e), 400
    return data, _quota_error(request.space, [{"size": len(data)}]), 400


def _keep_photo(r, data):
    """새 사진을 r에 담는다(저장은 부르는 쪽). 파일이 먼저 디스크에 가고, 되돌려지면 다음 purge의 고아 정리가 줍는다."""
    r.photo.save("photo.jpg", ContentFile(data), save=False)
    r.stored = len(data)


def _drop_after_commit(storage, name):
    def run():
        try:
            storage.delete(name)
        except OSError as e:  # 지우지 못한 파일은 다음 purge의 고아 정리가 줍는다
            log.warning("repair photo delete failed error=%s", type(e).__name__)
    transaction.on_commit(run)


def _form_page(request, form, status=200, **extra):
    ctx = _space_ctx(request, page="board", form=form, writable=request.space.school.writable, vendor=_vendor(request.space),
                     desks={c: "·".join(m.name for m in d[1][:DESK_NAMES]) for c, d in _desks(request.space).items()},
                     max_mb=photo.MAX_BYTES // photo.MB, **extra)
    return render(request, "repairs/form.html", ctx, status=status)


# ---------------------------------------------------------------- 화면
def list_page(request, status=200, **extra):
    """목록: 완료는 뒤로. 담당 칸은 분류별로 한 번만 구해 줄마다 붙인다(건수와 무관). 업체 발급·해제도 이 화면을 그대로 돌려준다."""
    all_rows = Repair.objects.in_space(request.space)
    counts = all_rows.aggregate(total=Count("pk"), open=Count("pk", filter=~Q(status=Repair.Status.DONE)))
    rows = all_rows.annotate(done=Case(When(status=Repair.Status.DONE, then=1), default=0, output_field=IntegerField())
                             ).order_by("done", "-created_at", "-pk")
    page = Paginator(rows, PER_PAGE).get_page(request.GET.get("page"))
    desks = _desks(request.space)
    texts = {c: _desk_text(d) for c, d in desks.items()}
    account = VendorAccount.current(request.space)
    vendor = account if account and account.is_open else None
    if vendor:
        texts[Repair.Cat.INFO] = _vendor_desk(vendor)
    shown = list(page.object_list)
    page.object_list = shown
    for r in shown:  # 업체가 못 보는 옛 정보 요청은 교내 담당 그대로
        r.desk = _desk_text(desks[r.cat]) if r.cat == Repair.Cat.INFO and not _vendor_sees(vendor, r) else texts[r.cat]
    ctx = _space_ctx(request, page="board", repairs=page, counts=counts, writable=request.space.school.writable,
                     desk_facility=texts[Repair.Cat.FACILITY], desk_info=texts[Repair.Cat.INFO],
                     no_handler=desks[Repair.Cat.FACILITY][0] == "관리자", vendor=vendor,
                     can_vendor=_can_handle(request.member), account=account,
                     account_until_text=account.until.isoformat() if account else "",
                     until_max=timezone.localdate() + VENDOR_MAX_DAYS,
                     repair_n=counts["open"],
                     notice_n=Notice.objects.in_space(request.space).visible_to(request.member).count(), **extra)
    return render(request, "repairs/list.html", ctx, status=status)


@never_cache
@require_GET
@school_required
def index(request):
    return list_page(request, toast=request.session.pop(TOAST, ""))


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@school_write_required
def new(request):
    me = request.member
    if request.method == "GET":
        return _form_page(request, {"cat": Repair.Cat.FACILITY, "place": _default_place(me), "title": "", "note": "", "urgent": False})
    form, errors = _read_form(request.POST)
    data, error, status = _read_photo(request)
    if error:
        errors["photo"] = error
    if errors:
        return _form_page(request, form, status=status, errors=errors)
    r = Repair(space=request.space, by=me, by_name=me.name, **form)
    with transaction.atomic():
        if data:
            _keep_photo(r, data)
        r.save()
        rung = _ring_desk(request.space, r, me)
    request.session[TOAST] = "접수했어요. 담당 선생님 🔔에 떴어요." if rung else "접수했어요."
    return redirect(f"/s/{request.space.token}/repairs")


def _detail_ctx(request, r, **extra):
    me, writable = request.member, request.space.school.writable
    vendor = _vendor(request.space)
    sees = _vendor_sees(vendor, r)
    return {"space": request.space, "r": r, "writable": writable, "statuses": Repair.Status.choices, "vendor_sees": sees,
            "desk": _vendor_desk(vendor) if sees else _desk_text(_desks(request.space)[r.cat]),
            "can_handle": writable and _can_handle(me), "can_edit": writable and _can_edit(me, r),
            "can_delete": _can_delete(me, r), **extra}


@never_cache
@require_GET
@school_required
def detail(request, key):
    r = get_in_space_or_404(Repair, request, key=key)
    return render(request, "repairs/detail.html", _space_ctx(request, page="board", **_detail_ctx(request, r)))


@never_cache
@require_POST
@school_required
@school_write_required
def state(request, key):
    """처리: 담당자·관리자만 상태·답변을 바꾼다. 잘못 만든 값은 저장하지 않는다."""
    get_in_space_or_404(Repair, request, key=key)
    if not _can_handle(request.member):
        return HttpResponseForbidden()
    status, reply = request.POST.get("status"), _clean(request.POST, "reply")
    if status not in Repair.Status.values or len(reply) > MAX_REPLY:
        return HttpResponseBadRequest(f"상태를 고르고, 답변은 {MAX_REPLY}자까지 적어 주세요.")
    r = change_state(request.space, key, status, reply, actor=request.member)
    if request.headers.get("HX-Request"):
        return render(request, "repairs/_detail.html", _detail_ctx(request, r, toast=f"{r.get_status_display()}(으)로 저장했어요."))
    resp = redirect(f"/s/{request.space.token}/repairs/{r.key}")
    resp.status_code = 303
    return resp


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@school_write_required
def edit(request, key):
    """요청자가 처리 전(접수)에만 고친다. 저장 때 행을 잠가 담당자가 먼저 시작했으면 거부한다."""
    r = get_in_space_or_404(Repair, request, key=key)
    if not _can_edit(request.member, r):
        return HttpResponseForbidden()
    if request.method == "GET":
        return _form_page(request, {k: getattr(r, k) for k in ("cat", "place", "title", "note", "urgent")}, repair=r)
    form, errors = _read_form(request.POST)
    data, error, status = _read_photo(request)
    if error:
        errors["photo"] = error
    if errors:
        return _form_page(request, form, status=status, errors=errors, repair=r)
    with transaction.atomic():
        r = get_object_or_404(Repair.objects.in_space(request.space).select_for_update(), key=key)
        if not _can_edit(request.member, r):
            return HttpResponseForbidden()
        moved = r.cat != form["cat"]
        for name, value in form.items():
            setattr(r, name, value)
        old = (r.photo.storage, r.photo.name) if r.photo and (data or "remove_photo" in request.POST) else None
        if data:
            _keep_photo(r, data)
        elif old:
            r.photo, r.stored = "", 0
        r.save()
        if old:
            _drop_after_commit(*old)
        bells = Notification.objects.in_space(request.space).filter(repair=r, kind=Notification.Kind.REPAIR)
        bells.update(text=_bell_text(r))  # 실수로 쓴 이름을 지워도 🔔 문구에 남지 않게
        if moved:  # 분류를 고치면 받는 곳이 바뀐다: 옛 담당자의 안 읽은 🔔은 거두고 새 담당자에게
            bells.filter(read=False).delete()
            _ring_desk(request.space, r, request.member)
    return redirect(f"/s/{request.space.token}/repairs/{r.key}")


@never_cache
@require_POST
@school_required
def delete(request, key):
    """지우기는 개인정보 조치라 만료된 학교도 받는다(공지 판 내용 지우기와 같다)."""
    r = get_in_space_or_404(Repair, request, key=key)
    if not _can_delete(request.member, r):
        return HttpResponseForbidden()
    r.delete()  # 🔔 CASCADE, 사진 파일은 커밋 뒤 hwpfiles.retention
    return redirect(f"/s/{request.space.token}/repairs")


@never_cache
@require_POST
@school_required
def delete_photo(request, key):
    """사진만 지우기: 지우기와 같은 권한, 만료된 학교도 받는다. 요청 내용은 남는다."""
    get_in_space_or_404(Repair, request, key=key)
    with transaction.atomic():
        r = get_object_or_404(Repair.objects.in_space(request.space).select_for_update(), key=key)
        if not _can_delete(request.member, r):
            return HttpResponseForbidden()
        if r.photo:
            _drop_after_commit(r.photo.storage, r.photo.name)
            r.photo, r.stored = "", 0
            r.save(update_fields=["photo", "stored", "updated_at"])
    resp = redirect(f"/s/{request.space.token}/repairs/{r.key}")
    resp.status_code = 303
    return resp


def photo_response(r):
    """재인코딩한 JPEG 그대로. 캐시 헤더는 부르는 쪽이 정한다."""
    if not r.photo:
        raise Http404
    try:
        fh = r.photo.open("rb")
    except FileNotFoundError:
        log.warning("repair photo missing repair=%s", r.pk)
        raise Http404
    resp = FileResponse(fh, content_type="image/jpeg")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


@require_GET
@school_required
def photo_file(request, key):
    """사진: 회원만(SpaceMiddleware). 화면의 ?v=가 바꿀 때마다 달라 브라우저 캐시를 써도 옛 사진이 안 나온다."""
    resp = photo_response(get_in_space_or_404(Repair, request, key=key))
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


@never_cache
@require_POST
@school_required
@manager_required
@school_write_required
def set_handler(request, key):
    """수리 담당자 지정·해제(관리자만). 구글 회원만(손님 자리는 반·이름으로 찾기 쉬워서, 관리자·복무 입력자와 같은 이유)."""
    m = get_in_space_or_404(Member, request, key=key)
    on, htmx = request.POST.get("on") == "1", request.headers.get("HX-Request")
    if on and (m.user_id is None or is_guest(m.user)):
        error = "이름만 들어온 선생님은 수리 담당자가 될 수 없어요. 구글 계정을 이으면 돼요."
        if htmx:
            return school_body(request, status=400, error=error)
        return HttpResponse(error, status=400, content_type="text/plain; charset=utf-8")
    if on != m.can_repair:
        Member.objects.filter(pk=m.pk).update(can_repair=on)
        MemberLog.objects.create(space=request.space, by=request.member.name, name=m.name,
                                 before="수리 담당 아님" if on else "수리 담당", after="수리 담당" if on else "수리 담당 아님")
    if not htmx:
        return redirect(f"/s/{request.space.token}/school")
    return school_body(request, toast=f"{m.name} 선생님을 수리 담당자로 지정했어요." if on else f"{m.name} 선생님을 수리 담당자에서 뺐어요.")
