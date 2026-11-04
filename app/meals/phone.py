"""M3 폰으로 식판 사진 올리기: /m/<token> 로그인 없는 한 화면(PC 전용 원칙의 유일한 예외). 추측 불가 토큰이 곧 권한이다.

- 토큰으로 할 수 있는 일은 그 학교 그날 사진 교체뿐. 날짜·학교는 토큰 행에서만 읽고 폼 값을 보지 않는다. 사진을 내주는 주소가 없다.
- 없는·끝난·횟수를 다 쓴 토큰은 응답이 똑같다(404). 빈도 제한은 TV와 같다: 이런 토큰은 IP로, 맞는 토큰은 토큰 단위로 센다(학교 와이파이의 학생이 /m/x를 많이 불러도 담당자 폰이 막히지 않게).
- 첫 업로드에 성공하면 링크가 10분 안으로 줄어든다(다시 찍기만). 기존 사진을 폰으로 바꾸면 만든 담당자와 관리자에게 🔔(첫 업로드는 매일 소음이라 없음, 웹 푸시 없음).
- 학교 이름과 날짜 외에는 그리지 않는다. 공통 컨텍스트(회원·알림)가 끼지 않게 request 없이 그린다(TV와 같다).
- Referrer-Policy는 same-origin: no-referrer면 폼의 Origin이 null이 되어 CSRF가 막는다(K2). 이 화면은 밖으로 나가는 링크가 없고, same-origin은 밖으로 Referer를 보내지 않는다.
- 사진 규칙은 입력 화면과 같은 코드(views.clean_photo·store_photo). 횟수는 저장에 성공한 것만 센다(HEIC로 거부돼도 토큰이 닳지 않게). 실패 시도는 토큰당 시간 제한으로 묶는다.
"""
from functools import wraps

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_http_methods

from accounts.models import hash_token
from collect.models import Notification
from config.ratelimit import TOO_MANY, allow, client_ip
from repairs import photo as repair_photo
from spaces.models import Member

from .models import AFTER_UPLOAD, MAX_UPLOADS, MealPhoto, PhotoLink
from .tv import _long_label
from .views import PHOTO_GONE, check_size, clean_photo, store_photo

HITS_PER_MINUTE = 60  # 맞는 토큰 하나당
MISSES_PER_IP = 60  # 분당, 없거나 끝난 토큰 요청만
UPLOADS_PER_IP = 30  # IP당 시간당 올리기 시도(맞는 토큰으로)
TRIES_PER_HOUR = 20  # 토큰당 시간당 올리기 시도(실패 포함)
NOT_IMAGE = "이 사진은 올릴 수 없어요. JPG·PNG 사진만 받아요. 아이폰 HEIC 사진이면 설정 > 카메라 > 포맷에서 ‘높은 호환성’으로 바꾼 뒤 다시 찍어 주세요."
NO_FILE = "사진을 찍은 뒤 올리기를 눌러 주세요."
PLAN_ENDED = "이 학교의 고티처 플랜 기간이 끝나 사진을 올릴 수 없어요."


def _draw(state, status=200, **ctx):
    return HttpResponse(render_to_string("meals/phone.html", {"state": state, **ctx}), status=status)


def _find(token):
    """입장 지점: 토큰 자체가 공간을 정한다. 이 행 밖의 학교·날짜·회원을 쓰지 않는다."""
    if len(token) > 128:
        return None
    link = PhotoLink.objects.select_related("space__school", "by").filter(token_hash=hash_token(token)).first()
    return link if link and link.is_open else None


def phone_view(view):
    """모든 응답: 검색 제외, 캐시 금지, Referer 정책. 뷰에는 살아 있는 링크만 넘긴다."""
    @wraps(view)
    @require_http_methods(["GET", "POST"])
    def inner(request, token):
        link = _find(token)
        if link is None:
            resp = _draw("closed", status=404) if allow("meal-m-miss", client_ip(request), MISSES_PER_IP, 60) else None
        else:
            resp = view(request, link, token) if allow("meal-m", link.pk, HITS_PER_MINUTE, 60) else None
        if resp is None:
            resp = HttpResponse(TOO_MANY, status=429, content_type="text/plain; charset=utf-8")
        resp["X-Robots-Tag"] = "noindex, nofollow"
        resp["Referrer-Policy"] = "same-origin"
        add_never_cache_headers(resp)
        return resp
    return inner


def _ring(link):
    """기존 사진이 폰으로 바뀐 것을 만든 담당자와 관리자에게. 🔔만(pushed=True로 웹 푸시 묶음에서 뺀다). 사진·토큰은 담지 않는다."""
    space = link.space
    Notification.objects.bulk_create(
        Notification(space=space, member=m, kind=Notification.Kind.MEAL, day=link.date, pushed=True, link=f"/s/{space.token}/meals",
                     text=f"{link.date.month}/{link.date.day} 식판 사진이 폰 QR로 바뀌었어요 (QR 만든 사람: {link.by.name})")
        for m in Member.objects.in_space(space).filter(Q(is_manager=True) | Q(pk=link.by_id)))


@phone_view
def page(request, link, token):
    school = link.space.school
    head = {"school": school.name, "label": _long_label(link.date)}
    if not school.writable:
        return _draw("ended", status=403 if request.method == "POST" else 200, message=PLAN_ENDED, **head)

    def form(error="", status=200):
        return _draw("form", status=status, error=error, token=token, csrf=get_token(request), **head)

    if request.method == "GET":
        return form()
    f = request.FILES.get("photo")
    if f is None:
        return form(NO_FILE, 400)
    if not (allow("meal-m-up", client_ip(request), UPLOADS_PER_IP, 3600) and allow("meal-m-try", link.pk, TRIES_PER_HOUR, 3600)):
        return form(TOO_MANY, 429)
    try:
        check_size(f)
        data = clean_photo(link.space, link.date, f)
    except repair_photo.BadPhoto as e:
        return form(NOT_IMAGE if str(e) == repair_photo.NOT_IMAGE else str(e), 400)
    with transaction.atomic():  # 재인코딩하는 사이 끝났거나 끊겼을 수 있고, 동시에 보내도 횟수를 넘지 않게 잠그고 다시 본다. 저장에 성공했을 때만 센다
        now = timezone.now()
        locked = PhotoLink.objects.select_for_update().filter(pk=link.pk, uses__lt=MAX_UPLOADS, expires_at__gt=now).first()
        if locked is None:
            return _draw("closed", status=404)
        replaced = MealPhoto.objects.in_space(link.space).filter(date=link.date).exclude(photo="").exists()
        if not store_photo(link.space, link.date, data, link.by, MealPhoto.Source.PHONE, link.pk):
            return form(PHOTO_GONE, 400)
        locked.uses += 1
        locked.expires_at = min(locked.expires_at, now + AFTER_UPLOAD)
        locked.save(update_fields=["uses", "expires_at"])
        if replaced:
            _ring(link)
    return _draw("done", token=token if locked.uses < MAX_UPLOADS else "", **head)
