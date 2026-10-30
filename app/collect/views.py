"""C2 새로 모으기 — 표 붙여넣기. 동작은 prototype collectNew(붙여넣기), 모습은 design/collect-new.html.

붙여넣은 HTML은 미리보기 요청에서 한 번 TSV로 바꿔 돌려줄 뿐 저장·렌더하지 않는다(paste.py).
만들 때는 TSV를 다시 읽어 Collection(source=paste) + Field + Row를 만든다. Cell은 만들지 않는다(낸 시각 계산).
공간 소속은 SpaceMiddleware가 확인하고, 조회는 모두 in_space()를 거친다.
"""
from django.conf import settings
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from spaces.models import get_in_space_or_404
from spaces.views import _space_ctx

from .models import Collection, Field, Row
from .paste import YES_NO, PasteError, clean_text, html_to_tsv, parse

# design TYPES(받는 방식 고르기). 자동 계산(calc)·링크(url)는 여기서 고르지 않는다.
KINDS = [("number", "숫자"), ("text", "글자"), ("select", "고르기"), ("date", "날짜")]
SHOW_ROWS = 40  # 미리보기에 보이는 받는 사람 줄


def _read(text):
    """(파싱 결과, 오류 문구). 빈 글자는 둘 다 None."""
    if not text.strip():
        return None, None
    try:
        return parse(text), None
    except PasteError as e:
        return None, str(e)


def _paste_ctx(p, error):
    rows = p["rows"] if p else []
    return {"p": p, "error": error, "kinds": KINDS, "shown_rows": rows[:SHOW_ROWS],
            "more_rows": max(0, len(rows) - SHOW_ROWS)}


@require_http_methods(["GET", "POST"])
def new(request):
    space = request.space
    title, text, errors = "", "", {}
    p = error = None
    if request.method == "POST":
        title = clean_text(request.POST.get("title", ""))[:200]
        text = request.POST.get("paste", "")
        p, error = _read(text)
        if not title:
            errors["title"] = "제목을 적어 주세요."
        if not p:
            errors["paste"] = error or "표를 붙여넣어 주세요. 한글·엑셀에서 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요."
        if not errors:
            c = _create(request, title, p)
            return redirect(f"/s/{space.token}/c/{c.key}/")
    ctx = _space_ctx(request, page="collect", title=title, paste=text, errors=errors)
    ctx.update(_paste_ctx(p, error))
    return render(request, "collect/new.html", ctx, status=400 if errors else 200)


def _create(request, title, p):
    valid = {k for k, _ in KINDS}
    with transaction.atomic():
        c = Collection.objects.create(space=request.space, created_by=request.member, title=title,
                                      source=Collection.Source.PASTE, unit_head=p["unit_head"])
        fields = []
        for i, f in enumerate(p["fields"]):
            kind = request.POST.get(f"kind_{i}")
            kind = kind if kind in valid else f["kind"]
            options = f["options"] if kind == f["kind"] else []
            if kind == "select" and not options:
                options = YES_NO
            fields.append(Field(collection=c, pos=i, name=f["name"], kind=kind, options=options if kind == "select" else []))
        Field.objects.bulk_create(fields)
        Row.objects.bulk_create([Row(collection=c, pos=i, unit=r["unit"], who=r["who"]) for i, r in enumerate(p["rows"])])
    return c


@require_POST
def preview(request):
    """붙여넣기 미리보기(htmx). html이 오면(한글·엑셀 클립보드) TSV로 바꿔 입력 칸에 넣도록 함께 돌려준다."""
    html = request.POST.get("html", "")
    try:
        tsv = html_to_tsv(html) if html else ""
    except PasteError as e:  # 너무 큰 표: 바꾼 글자를 싣지 않는다
        return render(request, "collect/_paste_out.html", {**_paste_ctx(None, str(e)), "oob": True})
    p, error = _read(tsv or request.POST.get("paste", ""))
    if html and not tsv:
        error = "붙여넣은 내용에서 표를 찾지 못했어요. 표를 드래그해 다시 복사해 주세요."
    ctx = _paste_ctx(p, error)
    ctx.update(tsv=tsv, oob=True)
    return render(request, "collect/_paste_out.html", ctx)


@require_GET
def made(request, key):
    """만든 뒤 확인(합본 화면은 C6). 제목·칸·줄 수와 받는 사람에게 보낼 쪽지."""
    c = get_in_space_or_404(Collection, request, key=key)
    fields = list(c.fields.all())
    n_rows = c.rows.count()
    link = f"{settings.SITE_URL}/c/{c.link_token}"
    me = request.member
    msg = (f"[고티처] {c.title}\n\n아래 링크에서 우리 반 칸만 채워 주세요. 로그인 없이 30초면 돼요.\n"
           f"받는 칸: {', '.join(f.name for f in fields)}\n{link}\n\n보낸 사람: {me.name} ({me.cls})")
    labels = dict(KINDS)
    ctx = _space_ctx(request, page="collect", c=c, n_rows=n_rows, link=link, msg=msg,
                     fields=[(f, labels.get(f.kind, f.get_kind_display())) for f in fields])
    return render(request, "collect/made.html", ctx)
