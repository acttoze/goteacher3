"""G4 양식 자료실. 모습은 design/forms.html(목록 7 / 자세히 5), 동작은 prototype app.js formList·formView.

- 로그인 없이 보기·검색·내려받기. 로그인한 동학년 회원은 공통 틀(shell.html), 그 밖에는 bare 틀.
- /forms 와 /forms/<slug> 는 같은 화면이다. slug가 있으면 오른쪽에 그 양식을 펴고 제목·설명 meta를 그 양식으로 쓴다.
- 검색은 제목·설명에서 낱말마다(AND) 찾는다. 분류는 학교·교육청.
- 내려받기는 FileResponse(Content-Disposition RFC 5987). 수를 세므로 캐시에 두지 않게 한다(private, no-cache).
- 조회·내려받기 수는 봇 User-Agent·HEAD를 빼고 F()+1만 한다(단순하게).
- Q9 추천은 구글 회원만(accounts.is_google_member, 업체 추천과 같은 기준). 양식마다 1표, 누르면 켜고 끈다. 비회원에게는 단추 대신 안내와 로그인 링크.
- 디자인의 '이 양식으로 모으기'·쪽 수·표 칸·첫 쪽 미리보기는 한글 양식 모으기(E5~F)와 표 분석이 붙을 때 넣는다.
"""
import re
from urllib.parse import quote

from django.conf import settings
from django.db.models import Count, F, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.encoding import iri_to_uri
from django.utils.cache import add_never_cache_headers
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from accounts.models import is_google_member
from config.ratelimit import TOO_MANY, allow, client_ip
from partners.models import Partner
from spaces.models import Member
from spaces.views import _josa, _my_space, _space_ctx

from .models import CATEGORIES, FormDoc, FormLike

BOT = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|curl|wget|python", re.I)
Q_MAX = 40
SORT_LIKES = "likes"
LIKES_PER_HOUR = 60
LIKE_ONLY_MEMBER = "추천은 고티처 회원만 할 수 있어요."
DL_PER_HOUR = 3  # 같은 IP가 같은 양식을 받을 때 시간당 세는 수(받기는 막지 않는다, G3 ⑥)
TYPES = {"hwpx": "application/hwp+zip", "hwp": "application/x-hwp"}


def _human(request):
    return request.method == "GET" and not BOT.search(request.headers.get("User-Agent", ""))


def _url(path, **params):
    qs = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{qs}" if qs else path


def _frame(request, page="forms"):
    """로그인한 동학년 회원이면 공통 틀 컨텍스트. 공간은 내 동학년만(사용자 기준 조회)."""
    if not request.user.is_authenticated:
        return {"frame": "bare.html"}
    space = _my_space(request.user)
    if space is None:
        return {"frame": "bare.html"}
    request.space = space
    request.member = Member.objects.in_space(space).filter(user=request.user).first()
    if request.member is None:
        return {"frame": "bare.html"}
    return {"frame": "shell.html", **_space_ctx(request, page=page)}


def _like_ctx(request, doc):
    """추천 칸(library/_like.html) 값. 손님은 구글을 바로 잇고, 비로그인은 시작 화면으로 간다(협력업체와 같음)."""
    user = request.user
    start = "/auth/google/login?" if user.is_authenticated else "/start?"
    return {"n": doc.likes.count(), "can": is_google_member(user),
            "on": is_google_member(user) and doc.likes.filter(user=user).exists(),
            "login": start + urlencode({"next": f"/forms/{doc.slug}"})}


@require_safe
def index(request, slug=None):
    doc = get_object_or_404(FormDoc, slug=slug) if slug else None
    q = " ".join(request.GET.get("q", "").split())[:Q_MAX]
    ca = request.GET.get("ca", "")
    if ca not in CATEGORIES:
        ca = ""
    sort = SORT_LIKES if request.GET.get("sort") == SORT_LIKES else ""
    qs = FormDoc.objects.all()
    counts = {c: 0 for c in CATEGORIES}
    for c in qs.values_list("category", flat=True):
        counts[c] += 1
    total = sum(counts.values())
    if ca:
        qs = qs.filter(category=ca)
    for w in q.split():
        qs = qs.filter(Q(title__icontains=w) | Q(desc__icontains=w))
    qs = qs.annotate(n_likes=Count("likes"))
    qs = qs.order_by("-n_likes", "-created_at", "-src") if sort else qs.order_by("-created_at", "-src")  # 집계하면 Meta.ordering이 빠진다
    docs = list(qs.only("slug", "category", "title", "desc", "filename", "created_at"))
    if doc is not None and _human(request) and not request.headers.get("HX-Request"):
        FormDoc.objects.filter(pk=doc.pk).update(views=F("views") + 1)
    shown = doc or (docs[0] if docs else None)
    if shown is not None and shown.pk != getattr(doc, "pk", None):
        shown = FormDoc.objects.get(pk=shown.pk)
    keep = _url("", q=q, ca=ca, sort=sort)
    ctx = {
        **_frame(request),
        "doc": doc, "shown": shown, "docs": docs, "q": q, "ca": ca, "total": total, "sort": sort,
        "like": _like_ctx(request, shown) if shown else None,
        "cats": [{"name": name, "n": n, "on": k == ca,
                  "href": _url("/forms", q=q, ca=k, sort=sort)}
                 for k, name, n in [("", "전체", total)] + [(c, c, counts[c]) for c in CATEGORIES]],
        "ca_name": ca or "전체", "keep": keep, "josa": _josa(q, "과", "와") if q else "",
        "canonical": iri_to_uri(settings.SITE_URL + (f"/forms/{doc.slug}" if doc else "/forms")),
    }
    r = render(request, "library/index.html", ctx)
    if request.user.is_authenticated:  # 로그인한 틀에는 내 이름·주소·추천 단추가 있다(G3 S6). 공개 화면은 그대로
        add_never_cache_headers(r)
    return r


@require_safe
def download(request, slug):
    doc = get_object_or_404(FormDoc, slug=slug)
    try:
        fh = doc.file.open("rb")
    except FileNotFoundError:
        raise Http404 from None
    if _human(request) and allow("dl", f"{client_ip(request)}:{doc.slug}", DL_PER_HOUR, 3600):
        FormDoc.objects.filter(pk=doc.pk).update(downloads=F("downloads") + 1)
    r = FileResponse(fh, as_attachment=True, filename=doc.filename, content_type=TYPES.get(doc.ext))
    r["Cache-Control"] = "private, no-cache"
    return r


@never_cache
@require_POST
def like(request, slug):
    """켜고 끄기. htmx는 추천 칸 조각, 스크립트가 없으면 그 양식 화면으로 돌아간다."""
    doc = get_object_or_404(FormDoc, slug=slug)
    if not is_google_member(request.user):
        return HttpResponse(LIKE_ONLY_MEMBER, status=403, content_type="text/plain; charset=utf-8")
    htmx = request.headers.get("HX-Request")
    if not allow("form-like", str(request.user.pk), LIKES_PER_HOUR, 3600):
        if htmx:
            return render(request, "library/_like.html", {"shown": doc, "like": _like_ctx(request, doc), "error": TOO_MANY}, status=429)
        return HttpResponse(TOO_MANY, status=429, content_type="text/plain; charset=utf-8")
    deleted, _ = doc.likes.filter(user=request.user).delete()
    if not deleted:
        FormLike.objects.get_or_create(form=doc, user=request.user)  # 두 번 눌러 겹쳐도 유일 제약이 한 표만 남긴다
    if htmx:
        return render(request, "library/_like.html", {"shown": doc, "like": _like_ctx(request, doc), "oob": True})
    r = redirect(f"/forms/{quote(doc.slug)}")
    r.status_code = 303
    return r


@require_safe
def sitemap(request):
    docs = FormDoc.objects.only("slug", "created_at")
    partners = Partner.objects.filter(is_public=True).only("slug", "created_at")
    return render(request, "library/sitemap.xml", {"site": settings.SITE_URL, "docs": docs, "partners": partners},
                  content_type="application/xml; charset=utf-8")
