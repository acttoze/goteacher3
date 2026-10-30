"""G4 양식 자료실. 모습은 design/forms.html(목록 7 / 자세히 5), 동작은 prototype app.js formList·formView.

- 로그인 없이 보기·검색·내려받기. 로그인한 동학년 회원은 공통 틀(shell.html), 그 밖에는 bare 틀.
- /forms 와 /forms/<slug> 는 같은 화면이다. slug가 있으면 오른쪽에 그 양식을 펴고 제목·설명 meta를 그 양식으로 쓴다.
- 검색은 제목·설명에서 낱말마다(AND) 찾는다. 분류는 학교·교육청.
- 내려받기는 FileResponse(Content-Disposition RFC 5987). 수를 세므로 캐시에 두지 않게 한다(private, no-cache).
- 조회·내려받기 수는 봇 User-Agent·HEAD를 빼고 F()+1만 한다(단순하게).
- 디자인의 '이 양식으로 모으기'·쪽 수·표 칸·첫 쪽 미리보기는 한글 양식 모으기(E5~F)와 표 분석이 붙을 때 넣는다.
"""
import re

from django.conf import settings
from django.db.models import F, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils.encoding import iri_to_uri
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_safe

from spaces.models import Member
from spaces.views import _josa, _my_space, _space_ctx

from .models import CATEGORIES, FormDoc

BOT = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|curl|wget|python", re.I)
Q_MAX = 40
TYPES = {"hwpx": "application/hwp+zip", "hwp": "application/x-hwp"}


def _human(request):
    return request.method == "GET" and not BOT.search(request.headers.get("User-Agent", ""))


def _url(path, **params):
    qs = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{qs}" if qs else path


def _frame(request):
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
    return {"frame": "shell.html", **_space_ctx(request, page="forms")}


@require_GET
def index(request, slug=None):
    doc = get_object_or_404(FormDoc, slug=slug) if slug else None
    q = " ".join(request.GET.get("q", "").split())[:Q_MAX]
    ca = request.GET.get("ca", "")
    if ca not in CATEGORIES:
        ca = ""
    qs = FormDoc.objects.all()
    counts = {c: 0 for c in CATEGORIES}
    for c in qs.values_list("category", flat=True):
        counts[c] += 1
    total = sum(counts.values())
    if ca:
        qs = qs.filter(category=ca)
    for w in q.split():
        qs = qs.filter(Q(title__icontains=w) | Q(desc__icontains=w))
    docs = list(qs.only("slug", "category", "title", "desc", "filename", "created_at"))
    if doc is not None and _human(request) and not request.headers.get("HX-Request"):
        FormDoc.objects.filter(pk=doc.pk).update(views=F("views") + 1)
    shown = doc or (docs[0] if docs else None)
    if shown is not None and shown.pk != getattr(doc, "pk", None):
        shown = FormDoc.objects.get(pk=shown.pk)
    keep = _url("", q=q, ca=ca)
    ctx = {
        **_frame(request),
        "doc": doc, "shown": shown, "docs": docs, "q": q, "ca": ca, "total": total,
        "cats": [{"name": name, "n": n, "on": k == ca,
                  "href": _url("/forms", q=q, ca=k)}
                 for k, name, n in [("", "전체", total)] + [(c, c, counts[c]) for c in CATEGORIES]],
        "ca_name": ca or "전체", "keep": keep, "josa": _josa(q, "과", "와") if q else "",
        "canonical": iri_to_uri(settings.SITE_URL + (f"/forms/{doc.slug}" if doc else "/forms")),
    }
    return render(request, "library/index.html", ctx)


@require_safe
def download(request, slug):
    doc = get_object_or_404(FormDoc, slug=slug)
    try:
        fh = doc.file.open("rb")
    except FileNotFoundError:
        raise Http404 from None
    if _human(request):
        FormDoc.objects.filter(pk=doc.pk).update(downloads=F("downloads") + 1)
    r = FileResponse(fh, as_attachment=True, filename=doc.filename, content_type=TYPES.get(doc.ext))
    r["Cache-Control"] = "private, no-cache"
    return r


@require_GET
def sitemap(request):
    docs = FormDoc.objects.only("slug", "created_at")
    return render(request, "library/sitemap.xml", {"site": settings.SITE_URL, "docs": docs},
                  content_type="application/xml; charset=utf-8")
