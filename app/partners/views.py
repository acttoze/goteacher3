"""N1·N2 협력업체. 동작은 prototype app.js vendorList·vendorView·vInArea·reviewNew, 구조는 양식 자료실(library).

- 목록·상세는 로그인 없이. 로그인한 회원은 공통 틀, 그 밖에는 bare 틀.
- 추천은 구글 회원만. 운영자 확인을 기다릴 뿐 어디에도 공개되지 않는다.
- 후기 자격은 _can_write. 주소에 후기를 가리키는 값이 없다: 언제나 '내가 쓴 이 업체 후기' 하나만 다루므로 남의 후기는 고칠 길이 없다.
- 공개 응답에는 배지·운영자가 확인한 한 줄·`고티처 선생님 · 지역 · 학교급`·월 단위 날짜·신고 번호만 나간다.
"""
import re
import unicodedata
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Func, IntegerField, Q, Sum
from django.db.models.functions import Coalesce
from django.forms import ModelForm
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.encoding import iri_to_uri
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from accounts.models import GUEST_PREFIX, is_guest
from accounts.models import is_google_member as _can_suggest
from collect.paste import clean_text
from config.ratelimit import allow
from library.views import _frame, _url
from spaces.models import Member

from .models import BADGES, CATS, REVIEW_TEXT_MAX, Partner, Review, Suggestion, review_label, school_labels, school_names

AREAS = ["서울", "경기", "인천", "강원", "충청", "전라", "경상", "제주"]  # prototype V_AREAS('전체'는 빈 값)
AREA_ALSO = {"충청": ["충남", "충북", "대전", "세종"], "전라": ["전남", "전북", "광주"], "경상": ["경남", "경북", "부산", "대구", "울산"],
             "서울": ["수도권"], "경기": ["수도권"], "인천": ["수도권"]}
PER_PAGE = 18  # 세 칸씩 여섯 줄
Q_MAX = 40
SUGGEST_PER_DAY = 5
DAY = 86400
SUGGEST_PATH = "/partners/suggest"
LOGIN_HREF = f"/start?next={SUGGEST_PATH}"
GUEST_LOGIN_HREF = f"/auth/google/login?next={SUGGEST_PATH}"  # 손님은 구글을 바로 잇는다(settings·welcome과 같음)
SORT_MANY = "many"  # 기본은 이름 순(후기 순위가 조작 유인이 되지 않게). 좋은 점 많은 순은 고를 때만
REVIEWS_PER_PAGE = 10
REVIEW_BADGES_MAX = 5
_SEP = r"[\s().\-–—/~_·・ㆍ‧•]{0,3}"
# 휴대폰·지역·대표번호 앞자리로 시작하는 번호만. 날짜·금액(2026.10.29, 1000000원)은 통과
PHONE = re.compile(rf"(?<!\d)(?:(?:01[016789]|02|0[3-6][1-5]|070|080|050\d){_SEP}\d{{3,4}}{_SEP}\d{{4}}|1[5-9]\d\d[\s.\-–—/~_·・ㆍ‧•]{{1,3}}\d{{4}})(?!\d)")
NAME_REFUSED = "한 줄에 사람 이름이나 학교 이름이 들어 있어요. 빼고 써 주세요."
SHOWN = Q(is_hidden=False)


def _in_area(area):
    q = Q(area="전국") | Q(area__contains=area)
    for word in AREA_ALSO.get(area, []):
        q |= Q(area__contains=word)
    return q


def _render(request, template, ctx, status=200):
    r = render(request, template, {**_frame(request, page="partners"), **ctx}, status=status)
    if request.user.is_authenticated:  # 내 이름·공간 주소가 있는 틀(G3 S6). 익명 화면은 그대로
        add_never_cache_headers(r)
    return r


class _BadgeLen(Func):
    """저장된 배지 수. JSONField가 PostgreSQL에서는 jsonb다."""

    function = "JSON_ARRAY_LENGTH"
    output_field = IntegerField()

    def as_postgresql(self, compiler, connection, **extra):
        return self.as_sql(compiler, connection, function="jsonb_array_length", **extra)


def _badge_counts(badge_lists):
    counts = [0] * len(BADGES)
    for badges in badge_lists:
        for i in badges:
            counts[i] += 1
    return counts


def _add_badges(partners):
    """이 쪽 업체 카드에 상위 배지 3개·좋은 점 합계·후기 수를 붙인다. 쿼리 하나."""
    lists = {p.pk: [] for p in partners}
    for pk, badges in Review.objects.filter(SHOWN, partner__in=lists).values_list("partner_id", "badges"):
        lists[pk].append(badges)
    for p in partners:
        counts = _badge_counts(lists[p.pk])
        p.n_reviews = len(lists[p.pk])
        p.n_badges = sum(counts)
        top = sorted(((n, i) for i, n in enumerate(counts) if n), key=lambda x: (-x[0], x[1]))[:3]
        p.top_badges = [{"name": BADGES[i], "n": n} for n, i in top]


@require_safe
def index(request):
    q = " ".join(request.GET.get("q", "").replace("\x00", "").split())[:Q_MAX]
    cat = request.GET.get("cat", "")
    cat = cat if cat in CATS else ""
    area = request.GET.get("area", "")
    area = area if area in AREAS else ""
    sort = SORT_MANY if request.GET.get("sort") == SORT_MANY else ""
    public = Partner.objects.filter(is_public=True)
    qs = public
    if cat:
        qs = qs.filter(cat=cat)
    if area:
        qs = qs.filter(_in_area(area))
    for w in q.split():
        qs = qs.filter(name__icontains=w)
    if sort:
        score = Sum(_BadgeLen("reviews__badges"), filter=Q(reviews__is_hidden=False))
        qs = qs.annotate(score=Coalesce(score, 0)).order_by("-score", "name", "slug")
    page_obj = Paginator(qs, PER_PAGE).get_page(request.GET.get("page"))
    _add_badges(page_obj.object_list)
    keep = {"q": q, "cat": cat, "area": area, "sort": sort}
    return _render(request, "partners/index.html", {
        "page_obj": page_obj, "q": q, "cat": cat, "area": area, "sort": sort, "cats": CATS, "areas": AREAS,
        "any_public": public.exists(), "can_suggest": _can_suggest(request.user),
        "prev_href": _url("/partners", **keep, page=page_obj.previous_page_number()) if page_obj.has_previous() else "",
        "next_href": _url("/partners", **keep, page=page_obj.next_page_number()) if page_obj.has_next() else "",
        "login_href": GUEST_LOGIN_HREF if request.user.is_authenticated else LOGIN_HREF,
        "canonical": settings.SITE_URL + "/partners",
    })


@require_safe
def detail(request, slug):
    partner = get_object_or_404(Partner, slug=slug, is_public=True)
    shown = partner.reviews.filter(SHOWN)
    counts = _badge_counts(shown.values_list("badges", flat=True))
    top = max(counts + [1])
    page_obj = Paginator(shown, REVIEWS_PER_PAGE).get_page(request.GET.get("page"))
    mine = _my_review(partner, request.user) if request.user.is_authenticated and not is_guest(request.user) else None
    review_path = _review_path(partner)
    return _render(request, "partners/detail.html", {
        "p": partner, "canonical": iri_to_uri(f"{settings.SITE_URL}/partners/{partner.slug}"),
        "badge_rows": [{"name": BADGES[i], "n": n, "pct": n * 100 // top} for i, n in enumerate(counts)],
        "review_count": page_obj.paginator.count, "page_obj": page_obj, "mine": mine,
        "write_href": review_path if request.user.is_authenticated else _login_href(review_path),
        "can_suggest": _can_suggest(request.user),
        "login_href": ("/auth/google/login?" + urlencode({"next": f"/partners/{partner.slug}"})) if request.user.is_authenticated else _login_href(f"/partners/{partner.slug}"),
        "saved": request.user.is_authenticated and "saved" in request.GET,
        "deleted": request.user.is_authenticated and "deleted" in request.GET,
    })


def _review_path(partner):
    return f"/partners/{quote(partner.slug)}/review"


def _login_href(path):
    return "/start?" + urlencode({"next": path})


def _my_seat(user):
    """내 회원 자리(가장 최근). 사용자 기준 조회라 공간 필터 대상이 아니다(spaces.views._my_space와 같음)."""
    return Member.objects.filter(user=user).select_related("space__school").order_by("-joined_at").first()


def _my_review(partner, user):
    """내가 쓴 이 업체 후기. 자리를 옮겨도 찾고, 공간에서 나가 자리가 끊기면(member 없음) 내 것으로 다루지 않는다."""
    return partner.reviews.filter(author_user=user, member__isnull=False).first()


def _can_write(me):
    """쓰기 자격: 학교 플랜이 켜진 학교 공간 회원, 또는 구글 회원이 둘 이상인 공간에 정한 날수를 넘겨 있은 회원."""
    if me.space.is_school and me.space.school.writable:
        return True
    if me.joined_at > timezone.now() - timedelta(days=settings.PARTNER_REVIEW_MIN_DAYS):
        return False
    googles = Member.objects.in_space(me.space).filter(user__isnull=False).exclude(user__username__startswith=GUEST_PREFIX)
    return googles.count() >= settings.PARTNER_REVIEW_MIN_GOOGLE


def _text_error(text, school):
    """한 줄 글 검사(보조, 공개 전에 운영자가 본다). 값은 로그·오류 문구에 남기지 않고, 어느 이름인지 말하지 않는다. 명부는 그 학교 것만 본다."""
    if len(text) > REVIEW_TEXT_MAX:
        return f"한 줄은 {REVIEW_TEXT_MAX}자까지예요."
    plain = unicodedata.normalize("NFKC", text)
    if PHONE.search(plain):
        return "전화번호로 보이는 숫자가 있어요. 빼고 써 주세요."
    bare = _letters(plain)
    if any(_letters(name) in bare for name in school_names(school)):
        return NAME_REFUSED
    for name in Member.objects.filter(space__school=school).values_list("name", flat=True).distinct():
        name = _letters(unicodedata.normalize("NFKC", name))
        # 두 글자 이름은 원문 그대로만 비교한다('이 안전'이 '이안'으로 걸리지 않게)
        if len(name) >= 2 and (name in plain or (len(name) >= 3 and name in bare)):
            return NAME_REFUSED
    return ""


def _letters(s):
    return "".join(ch for ch in s if ch.isalnum())


def _parse_badges(raw):
    """고른 배지 번호(허용 목록, 겹침 없이 1~5개). 어긋나면 None."""
    allowed = {str(i): i for i in range(len(BADGES))}
    if not 1 <= len(raw) <= REVIEW_BADGES_MAX or len(set(raw)) != len(raw) or not all(x in allowed for x in raw):
        return None
    return sorted(allowed[x] for x in raw)


def _review_page(request, partner, status=200, **flags):
    ctx = {"p": partner, "review_path": _review_path(partner), "min_days": settings.PARTNER_REVIEW_MIN_DAYS, "per_day": settings.PARTNER_REVIEW_PER_DAY}
    return _render(request, "partners/review.html", {**ctx, **flags}, status=status)


def _review_gate(request, slug):
    """(업체, 내 자리, 내 후기, 막는 응답). 응답이 있으면 그대로 돌려준다."""
    partner = get_object_or_404(Partner, slug=slug, is_public=True)
    if not request.user.is_authenticated:
        return partner, None, None, redirect(_login_href(_review_path(partner)))
    if is_guest(request.user):
        login = "/auth/google/login?" + urlencode({"next": _review_path(partner)})
        return partner, None, None, _review_page(request, partner, 403, refused=True, login_href=login)
    me = _my_seat(request.user)
    if me is None:
        return partner, None, None, _review_page(request, partner, 403, no_space=True)
    return partner, me, _my_review(partner, request.user), None


def _see_other(to):
    r = redirect(to)
    r.status_code = 303
    return r


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def review(request, slug):
    partner, me, own, stop = _review_gate(request, slug)
    if stop:
        return stop
    posting = request.method == "POST"
    # 거부된 제출도 센다: 이름 거부로 남의 학교 명부를 맞춰 볼 수 없게. 다른 판단보다 먼저라 한도를 넘으면 아무것도 알려 주지 않는다
    if posting and not allow("partner-review", str(request.user.pk), settings.PARTNER_REVIEW_PER_DAY, DAY):
        return _review_page(request, partner, 429, too_many=True)
    if own and own.is_hidden:
        return _review_page(request, partner, 409 if posting else 200, hidden=True)
    if not own:
        if not _can_write(me):  # 이름 검사·'이미 남김' 판단보다 먼저. 자격이 없으면 안내만
            return _review_page(request, partner, 403, not_yet=True)
        flag = _blocked(partner, request.user, me.space.school)
        if flag:
            return _review_page(request, partner, 409 if posting else 200, **{flag: True})
    school = own.school if own else me.space.school
    ctx = {"existing": own, "label": own.by_label if own else review_label(*school_labels(school))}
    if not posting:
        return _review_page(request, partner, **ctx, **_form_ctx(own.badges if own else [], own.text if own else ""))
    raw = request.POST.getlist("badges")
    badges = _parse_badges(raw)
    text = clean_text(request.POST.get("text", ""))
    errors = {"text": _text_error(text, school)}
    if badges is None:
        errors["badges"] = f"좋았던 점 배지를 1개에서 {REVIEW_BADGES_MAX}개까지 골라 주세요."
    if any(errors.values()):
        return _review_page(request, partner, 400, **ctx, **_form_ctx(raw, text), errors=errors)
    if own:  # is_hidden 조건: 운영자가 그사이 내렸으면 고치지 않는다. 한 줄이 그대로면 확인 결과도 그대로(반려를 다시 저장해 대기로 돌리지 못하게)
        state = own.text_state if text == own.text else _new_text_state(text)
        partner.reviews.filter(pk=own.pk, is_hidden=False).update(badges=badges, text=text, text_state=state, updated_at=timezone.now())
    else:
        area, level = school_labels(school)
        try:
            with transaction.atomic():
                Review.objects.create(partner=partner, member=me, author_user=request.user, school=school, badges=badges, text=text,
                                      text_state=_new_text_state(text), area_label=area, level_label=level)
        except IntegrityError:  # 먼저 낸 후기가 있다(DB 유일 제약이 하나만 받는다)
            return _review_page(request, partner, 409, **{_blocked(partner, request.user, school) or "taken": True})
    return _see_other(_to_detail(partner, "saved"))


def _blocked(partner, user, school):
    """새로 쓸 수 없는 까닭: 이 계정이 이 업체에 쓴 적 있음(내렸거나 나간 자리 포함) / 같은 학교 후기가 있음."""
    if partner.reviews.filter(author_user=user).exists():
        return "already"
    return "taken" if partner.reviews.filter(school=school).exists() else ""


def _new_text_state(text):
    return Review.TextState.PENDING if text else Review.TextState.OK


def _to_detail(partner, note):
    return f"/partners/{quote(partner.slug)}?{note}=1"


def _form_ctx(picked, text):
    """배지 칸. picked는 번호나 제출된 글자(어긋난 값은 체크되지 않는다)."""
    picked = {str(x) for x in picked}
    return {"text": text, "text_max": REVIEW_TEXT_MAX, "badges_max": REVIEW_BADGES_MAX,
            "badge_opts": [{"i": i, "name": name, "on": str(i) in picked} for i, name in enumerate(BADGES)]}


@never_cache
@require_http_methods(["POST"])
def review_delete(request, slug):
    partner, me, own, stop = _review_gate(request, slug)
    if stop:
        return stop
    if own is None:  # 남의 후기가 있는지도 알려 주지 않는다
        return _review_page(request, partner, 403, nothing=True)
    if own.is_hidden:
        return _review_page(request, partner, 409, hidden=True)
    partner.reviews.filter(pk=own.pk, is_hidden=False).delete()
    to = f"/s/{me.space.token}/settings?a=space" if request.POST.get("back") == "settings" else _to_detail(partner, "deleted")
    if request.headers.get("HX-Request"):
        return HttpResponse(headers={"HX-Redirect": to})
    return _see_other(to)


class SuggestForm(ModelForm):
    class Meta:
        model = Suggestion
        fields = ["name", "cat", "contact", "note"]


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def suggest(request):
    if not request.user.is_authenticated:
        return redirect(LOGIN_HREF)
    if is_guest(request.user):
        return _render(request, "partners/suggest.html", {"refused": True, "login_href": GUEST_LOGIN_HREF}, status=403)
    ctx = {"cats": CATS, "sent": "sent" in request.GET}
    if request.method != "POST":
        return _render(request, "partners/suggest.html", {**ctx, "form": SuggestForm()})
    who = str(request.user.pk)
    form = SuggestForm(request.POST)
    if not form.is_valid():  # 잘못된 입력은 한도를 쓰지 않는다
        return _render(request, "partners/suggest.html", {**ctx, "form": form}, status=400)
    if not allow("partner-suggest", who, SUGGEST_PER_DAY, DAY):  # 저장 전에 한 번만 센다(요청이 겹쳐도 한도를 넘지 않게)
        return _render(request, "partners/suggest.html", {**ctx, "form": form, "too_many": True}, status=429)
    me = Member.objects.filter(user=request.user).select_related("space__school").order_by("-joined_at").first()  # 사용자 기준 조회
    s = form.save(commit=False)
    s.by_member = me
    s.by_name = me.name if me else request.user.first_name
    s.school = me.space.school.name if me else ""
    s.save()
    r = redirect(f"{SUGGEST_PATH}?sent=1")
    r.status_code = 303
    return r
