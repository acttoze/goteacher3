"""N1 협력업체. 동작은 prototype app.js vendorList·vendorView·vInArea, 구조는 양식 자료실(library).

- 목록·상세는 로그인 없이. 로그인한 회원은 공통 틀, 그 밖에는 bare 틀.
- 추천은 구글 회원만. 운영자 확인을 기다릴 뿐 어디에도 공개되지 않는다.
- 후기(N2)는 아직 없다. 정렬은 이름 순.
"""
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.forms import ModelForm
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import add_never_cache_headers
from django.utils.encoding import iri_to_uri
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from accounts.models import is_guest
from config.ratelimit import allow
from library.views import _frame, _url
from spaces.models import Member

from .models import CATS, Partner, Suggestion

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


def _can_suggest(user):
    return user.is_authenticated and not is_guest(user)


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


@require_safe
def index(request):
    q = " ".join(request.GET.get("q", "").replace("\x00", "").split())[:Q_MAX]
    cat = request.GET.get("cat", "")
    cat = cat if cat in CATS else ""
    area = request.GET.get("area", "")
    area = area if area in AREAS else ""
    public = Partner.objects.filter(is_public=True)
    qs = public
    if cat:
        qs = qs.filter(cat=cat)
    if area:
        qs = qs.filter(_in_area(area))
    for w in q.split():
        qs = qs.filter(name__icontains=w)
    page_obj = Paginator(qs, PER_PAGE).get_page(request.GET.get("page"))
    keep = {"q": q, "cat": cat, "area": area}
    return _render(request, "partners/index.html", {
        "page_obj": page_obj, "q": q, "cat": cat, "area": area, "cats": CATS, "areas": AREAS,
        "any_public": public.exists(), "can_suggest": _can_suggest(request.user),
        "prev_href": _url("/partners", **keep, page=page_obj.previous_page_number()) if page_obj.has_previous() else "",
        "next_href": _url("/partners", **keep, page=page_obj.next_page_number()) if page_obj.has_next() else "",
        "login_href": GUEST_LOGIN_HREF if request.user.is_authenticated else LOGIN_HREF,
        "canonical": settings.SITE_URL + "/partners",
    })


@require_safe
def detail(request, slug):
    partner = get_object_or_404(Partner, slug=slug, is_public=True)
    return _render(request, "partners/detail.html", {
        "p": partner, "canonical": iri_to_uri(f"{settings.SITE_URL}/partners/{partner.slug}"),
    })


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
