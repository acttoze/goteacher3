"""M1 급식 입력(prototype meal-admin): 이번 주 월~금 칸에 식판 사진, TV 화면 켜고 끄기·순서. 메뉴는 NEIS 자동, 위생 주제·표어는 날짜로 자동.

권한은 Member.can_meal·is_manager로만. Member.group은 본인이 고치는 값이라 '급식소'로 바꿔도 권한이 생기지 않는다.
보기 사진은 학교 회원이면 된다(화면은 지정자만). 지우기는 개인정보 조치라 만료된 학교도 받는다(수리 사진과 같다).
"""
from datetime import datetime, timedelta
from functools import wraps
from math import ceil

import segno
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from PIL import Image

from accounts.models import is_guest
from config.ratelimit import TOO_MANY, allow
from notices.views import _quota_error
from repairs import photo as repair_photo
from repairs.views import _drop_after_commit, photo_response
from spaces import neis
from spaces.gates import manager_required, no_org, school_required, school_write_required
from spaces.models import Member, MemberLog, get_in_space_or_404
from spaces.school import school_body
from spaces.views import _space_ctx

from . import content
from .models import MealPhoto, MealSettings, PhotoLink, new_tv_token, next_midnight

WD = "월화수목금토일"
UPLOAD_PER_HOUR = 30
RETRY_PER_MINUTE = 6
EXIF_IFD, DATE_ORIGINAL = 0x8769, 36867
QR_SCALE = 4  # 폰 QR 모듈 하나 4px: 1배율 모니터에서도 폰으로 읽힌다. 축소·확대하지 않는다(버전 5 + 여백 4모듈 = 180px)
PHOTO_GONE = "방금 다른 선생님이 이 칸 사진을 지웠어요. 사진을 다시 올려 주세요."


def meal_required(view):
    """관리자 또는 지정된 급식 담당자가 아니면 403."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not (request.member.is_manager or request.member.can_meal):
            return HttpResponseForbidden()
        return view(request, *args, **kwargs)
    return inner


def _week(today):
    """이번 주 월~금. 주말이면 다음 주(prototype M.monday)."""
    wd = today.weekday()
    monday = today + timedelta(days=7 - wd if wd >= 5 else -wd)
    return [monday + timedelta(days=i) for i in range(5)]


def _label(day):
    return f"{WD[day.weekday()]} {day.month}/{day.day}"


def _day_or_404(ymd):
    try:
        return datetime.strptime(ymd, "%Y%m%d").date()
    except ValueError:
        raise Http404 from None


def _taken_day(f):
    """사진의 EXIF 촬영일 또는 None(재인코딩이 지우므로 그 전에 읽는다). JPEG 머리만 읽는다: 픽셀을 풀지 않아 폭탄 검사 전에 불러도 된다.
    PNG는 EXIF를 읽으려면 픽셀을 전부 풀어야 해서(Pillow) 보지 않는다."""
    try:
        with Image.open(f, formats=("JPEG",)) as im:
            value = im.getexif().get_ifd(EXIF_IFD).get(DATE_ORIGINAL)
        return datetime.strptime(str(value)[:10], "%Y:%m:%d").date()
    except Exception:  # 없거나 깨진 EXIF는 '모름'. 사진을 받을지는 재인코딩이 정한다
        return None
    finally:
        f.seek(0)


def _warnings(day, taken, today):
    out = []
    if day != today:
        out.append(f"{_label(day)} 칸에 올렸어요. 오늘은 {today.month}/{today.day} {WD[today.weekday()]}요일이에요. 칸이 맞는지 확인해 주세요.")
    if taken and taken != day:
        out.append(f"다른 날 찍은 사진 같아요. 사진에 적힌 촬영일이 {taken.month}/{taken.day}이에요. 맞는 사진인지 확인해 주세요.")
    return out


def _today_version(space):
    """폴링이 비교하는 값: 오늘 사진이 바뀌면 달라진다."""
    row = MealPhoto.objects.in_space(space).filter(date=timezone.localdate()).exclude(photo="").first()
    return row.version if row else ""


def _main_ctx(request, retry=False, **extra):
    """이번 주 칸 + NEIS 메뉴. NEIS 실패(fail)와 그날 급식 없음(meal 없음)은 다르게 보여 준다. 폰 QR 토큰은 여기서 만들지 않는다(qr 뷰만)."""
    space, today = request.space, timezone.localdate()
    days = _week(today)
    span = (space.school.neis_atpt, space.school.neis_code, f"{days[0]:%Y%m%d}", f"{days[-1]:%Y%m%d}")
    if retry and allow("meal-retry", request.member.key, RETRY_PER_MINUTE, 60):
        neis.forget_failed_meals(*span)
    rows = neis.meals(*span)
    menus = {r["ymd"]: r for r in rows}
    photos = {p.date: p for p in MealPhoto.objects.in_space(space).filter(date__in=days).select_related("by")}
    cells = []
    for d in days:
        meal = menus.get(f"{d:%Y%m%d}")
        cells.append({"ymd": f"{d:%Y%m%d}", "label": _label(d), "today": d == today, "meal": meal, "photo": photos.get(d),
                      "allergy_n": len({n for x in meal["dishes"] for n in x["allergy"]}) if meal else 0})
    fail = neis.failed(rows)
    for c in cells:
        c["need"] = c["today"] and not c["photo"] and bool(c["meal"] or fail)
    return {"space": space, "cells": cells, "fail": fail, "writable": space.school.writable, "weekend": today.weekday() >= 5, "max_mb": repair_photo.MAX_BYTES // repair_photo.MB,
            "live_v": _today_version(space), "live_until": int(next_midnight().timestamp()),
            "need_today": any(c["need"] for c in cells), "today_done": any(c["today"] and c["photo"] for c in cells), **extra}


def _page(request, status=200, **extra):
    """htmx면 본문 조각만, 아니면 화면 전체."""
    ctx = _main_ctx(request, **extra)
    if request.headers.get("HX-Request"):
        return render(request, "meals/_main.html", ctx, status=status)
    return render(request, "meals/index.html", _space_ctx(request, page="meals", **ctx), status=status)


@never_cache
@require_GET
@school_required
@no_org
@meal_required
def index(request):
    return _page(request, retry="retry" in request.GET)


@never_cache
@require_GET
@school_required
@no_org
@meal_required
def live(request):
    """열어 둔 입력 화면의 폴링(M3): 오늘 사진이 바뀌거나(QR도 내려간다) 날짜가 바뀌면 다시 그린다. 그대로면 204."""
    try:
        fresh = timezone.now().timestamp() < int(request.GET.get("until", 0))
    except ValueError:
        fresh = False
    if fresh and request.GET.get("v", "") == _today_version(request.space):
        return HttpResponse(status=204)
    return render(request, "meals/_main.html", _main_ctx(request))


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@no_org
@meal_required
@school_write_required
def qr(request):
    """'폰으로 올리기'. POST(누를 때)만 토큰을 만든다. GET은 끝난 QR을 내리고 단추로 돌아가는 것(자동으로 새로 만들지 않는다)."""
    space, today, ctx = request.space, timezone.localdate(), {}
    if request.method == "POST":
        if today not in _week(today):
            return HttpResponseBadRequest()
        link, raw = PhotoLink.issue(space, today, request.member)
        url = f"{settings.SITE_URL}/m/{raw}"
        ctx["qr"] = {"url": url, "img": segno.make(url, error="m").svg_data_uri(border=4, scale=QR_SCALE, dark="#0d0d0d", encode_minimal=True),
                     "seconds": ceil((link.expires_at - timezone.now()).total_seconds())}
    if request.headers.get("HX-Request"):
        return render(request, "meals/_qr.html", {"space": space, "writable": space.school.writable, **ctx})
    return _page(request, **ctx)


def check_size(f):
    """머리를 읽기 전에 부른다. 넘으면 BadPhoto."""
    if f.size > repair_photo.MAX_BYTES:
        raise repair_photo.BadPhoto(f"사진은 {repair_photo.MAX_BYTES // repair_photo.MB}MB까지 올릴 수 있어요. 크기를 줄여 다시 올려 주세요.")


def clean_photo(space, day, f):
    """check_size를 지난 파일 → 저장할 JPEG 바이트. 입력 화면과 폰 화면이 같은 규칙(재인코딩·화소·스캔·저장 한도)을 쓴다. 못 받으면 BadPhoto(안내 한 줄)."""
    data = repair_photo.reencode(f)
    old_size = MealPhoto.objects.in_space(space).filter(date=day).values_list("stored", flat=True).first() or 0
    quota = _quota_error(space, [{"size": max(0, len(data) - old_size)}])
    if quota:
        raise repair_photo.BadPhoto(quota)
    return data


def store_photo(space, day, data, by, source=MealPhoto.Source.PC, link_ref=None):
    """그 날짜 칸에 저장. 같은 날 다시 올리면 교체(옛 파일은 커밋 뒤 삭제). 잠그기 전에 다른 분이 칸을 지웠으면 저장하지 않고 False."""
    with transaction.atomic():
        made, _ = MealPhoto.objects.get_or_create(space=space, date=day)
        row = MealPhoto.objects.in_space(space).select_for_update().filter(pk=made.pk).first()
        if row is None:
            return False
        if row.photo:
            _drop_after_commit(row.photo.storage, row.photo.name)
        row.photo.save("photo.jpg", ContentFile(data), save=False)
        row.stored, row.by, row.by_name, row.source, row.link_ref, row.created_at = len(data), by, by.name, source, link_ref, timezone.now()
        row.save()
    return True


@never_cache
@require_POST
@school_required
@no_org
@meal_required
@school_write_required
def upload(request, ymd):
    """그 날짜 칸에 사진 저장. 오늘이 아닌 칸·다른 날 사진은 막지 않고 알려 준다."""
    day, today, me = _day_or_404(ymd), timezone.localdate(), request.member
    if day not in _week(today):
        return _page(request, status=400, error="이번 주 칸에만 올릴 수 있어요. 화면을 새로 고쳐 주세요.")
    f = request.FILES.get("photo")
    if f is None:
        return _page(request, status=400, error="사진을 골라 주세요.")
    if not allow("meal-up", me.key, UPLOAD_PER_HOUR, 3600):
        return _page(request, status=429, error=TOO_MANY)
    try:
        check_size(f)
        taken = _taken_day(f)
        data = clean_photo(request.space, day, f)
    except repair_photo.BadPhoto as e:
        return _page(request, status=400, error=str(e))
    if not store_photo(request.space, day, data, me):
        return _page(request, error=PHOTO_GONE)
    return _page(request, toast="사진을 올렸어요.", warnings=_warnings(day, taken, today))


@never_cache
@require_POST
@school_required
@no_org
@meal_required
def delete(request, ymd):
    day = _day_or_404(ymd)
    with transaction.atomic():  # 잠그고 읽는다: 잠그기 전 값으로 지우면 그사이 교체된 새 파일이 디스크에 남는다
        PhotoLink.objects.in_space(request.space).filter(date=day).delete()  # 지우면 그날 폰 QR도 끊는다. 폰 올리기와 잠그는 순서를 맞췄다(QR 먼저, 사진 다음)
        get_object_or_404(MealPhoto.objects.in_space(request.space).select_for_update(), date=day).delete()  # 파일은 커밋 뒤 hwpfiles.retention
    return _page(request, toast="사진을 지웠어요.")


@require_GET
@school_required
@no_org
def photo_file(request, ymd):
    """사진: 학교 회원만(SpaceMiddleware). 화면의 ?v=가 바꿀 때마다 달라 브라우저 캐시를 써도 옛 사진이 안 나온다."""
    resp = photo_response(get_in_space_or_404(MealPhoto, request, date=_day_or_404(ymd)))
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


def _move(items, key, step):
    i = next(n for n, x in enumerate(items) if x["key"] == key)
    if 0 <= i + step < len(items):
        items[i], items[i + step] = items[i + step], items[i]


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@no_org
@meal_required
@school_write_required
def screens(request):
    """TV 화면 켜고 끄기·순서(단추 하나에 한 번 저장). 식판 사진 화면은 끌 수 없다."""
    space, toast = request.space, ""
    if request.method == "POST":
        if "theme" in request.POST:
            no = request.POST["theme"]
            if no not in {str(n) for n in content.THEME_NOS}:  # "1"~"5" 글자 그대로만(²·３ 같은 숫자 꼴은 거절)
                return HttpResponseBadRequest()
            MealSettings.objects.get_or_create(space=space)
            MealSettings.objects.in_space(space).update(theme=int(no))
            name = next(t["name"] for t in content.THEMES if t["no"] == int(no))
            return _screens_page(request, f"TV 테마를 '{name}'(으)로 바꿨어요. 켜 둔 TV에는 5분 안에 바뀌어요.")
        action, key = next(((a, request.POST[a]) for a in ("up", "down", "toggle") if a in request.POST), (None, None))
        if key not in content.SCREEN_KEYS or (action == "toggle" and key == content.PHOTO):
            return HttpResponseBadRequest()
        with transaction.atomic():
            MealSettings.objects.get_or_create(space=space)
            saved = MealSettings.objects.in_space(space).select_for_update().get()
            items = content.screens(saved.screens)
            if action == "toggle":
                row = next(x for x in items if x["key"] == key)
                row["on"] = not row["on"]
                toast = f"{row['name']} 화면을 {'켰어요' if row['on'] else '껐어요'}."
            else:
                _move(items, key, -1 if action == "up" else 1)
                toast = "화면 순서를 바꿨어요."
            saved.screens = [{"key": x["key"], "on": x["on"]} for x in items]
            saved.save(update_fields=["screens"])
    return _screens_page(request, toast)


def _screens_page(request, toast=""):
    space, today = request.space, timezone.localdate()
    row, _ = MealSettings.objects.get_or_create(space=space)  # 처음 열면 TV 주소가 생긴다
    tv_url = f"{settings.SITE_URL}/tv/{row.tv_token}"
    items = MealSettings.screens_of(space)
    on = [x for x in items if x["on"]]
    ctx = {"space": space, "screens": items, "n_on": len(on), "loop": sum(x["sec"] * x.get("pages", 1) for x in on), "last": len(items) - 1,
           "writable": space.school.writable, "hygiene": content.hygiene_of(today), "slogan": content.slogan_of(today), "toast": toast,
           "tv_url": tv_url, "tv_path": f"/tv/{row.tv_token}", "themes": content.THEMES, "theme": row.theme, "tv_qr": segno.make(tv_url, error="m").svg_data_uri(border=0, dark="#0d0d0d", encode_minimal=True)}
    if request.headers.get("HX-Request"):
        return render(request, "meals/_screens.html", ctx)
    return render(request, "meals/screens.html", _space_ctx(request, page="meals", **ctx))


@never_cache
@require_POST
@school_required
@no_org
@meal_required
def reissue_tv(request):
    """TV 주소 새로 만들기: 옛 주소는 바로 끊긴다. 보안 조치라 플랜이 끝난 학교도 받는다(초대 재발급과 같다)."""
    MealSettings.objects.get_or_create(space=request.space)
    MealSettings.objects.in_space(request.space).update(tv_token=new_tv_token())
    return _screens_page(request, "TV 주소를 새로 만들었어요. 옛 주소는 이제 열리지 않아요. TV에서 새 주소를 열어 주세요.")


@never_cache
@require_POST
@school_required
@no_org
@manager_required
@school_write_required
def set_writer(request, key):
    """급식 담당자 지정·해제(관리자만). 계정 회원만(손님 자리는 반·이름으로 찾기 쉬워서, 수리 담당자와 같은 이유)."""
    m = get_in_space_or_404(Member, request, key=key)
    on, htmx = request.POST.get("on") == "1", request.headers.get("HX-Request")
    if on and (m.user_id is None or is_guest(m.user)):
        error = "이름만 들어온 선생님은 급식 담당자가 될 수 없어요. 구글이나 아이디로 이으면 돼요."
        if htmx:
            return school_body(request, status=400, error=error)
        return HttpResponse(error, status=400, content_type="text/plain; charset=utf-8")
    if on != m.can_meal:
        Member.objects.filter(pk=m.pk).update(can_meal=on)
        MemberLog.objects.create(space=request.space, by=request.member.name, name=m.name,
                                 before="급식 담당 아님" if on else "급식 담당", after="급식 담당" if on else "급식 담당 아님")
    if not htmx:
        return redirect(f"/s/{request.space.token}/school")
    return school_body(request, toast=f"{m.name} 선생님을 급식 담당자로 지정했어요." if on else f"{m.name} 선생님을 급식 담당자에서 뺐어요.")
