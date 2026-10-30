"""C2·C3 새로 모으기 — 표 붙여넣기·직접 입력·자주 쓰는 양식. 동작은 prototype collectNew, 모습은 design/collect-new.html.

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
from .paste import YES_NO, PasteError, guess_kind, clean_text, html_to_tsv, parse
from .presets import PRESETS
from .recipients import roster_rows

# design TYPES(받는 방식 고르기). 자동 계산(calc)·링크(url)는 여기서 고르지 않는다.
KINDS = [("number", "숫자"), ("text", "글자"), ("select", "고르기"), ("date", "날짜")]
SHEET_COLS = 6  # 직접 입력 칸 수(design: 더 많으면 표 붙여넣기)
SHEET_HEAD = ["학년반", "담임"]  # prototype lockCols(담임만·반별 1칸)


def _read(text):
    """(파싱 결과, 오류 문구). 빈 글자는 둘 다 None."""
    if not text.strip():
        return None, None
    try:
        return parse(text), None
    except PasteError as e:
        return None, str(e)


def _paste_ctx(p, error):
    return {"p": p, "error": error, "kinds": KINDS}


def _sheet_cols(post):
    """직접 입력 칸: [(이름, 형식, 보기)]. 형식이 비면 이름으로 추정(design guess), 고르기 보기가 비면 예·아니오(prototype)."""
    valid, out, seen = {k for k, _ in KINDS}, [], {}
    for i in range(SHEET_COLS):
        name = clean_text(post.get(f"col_name_{i}", ""))[:90]
        if not name:
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name} ({seen[name]})"
        kind = post.get(f"col_kind_{i}", "")
        kind = kind if kind in valid else guess_kind(name, [])[0]
        opts = [clean_text(o)[:100] for o in post.get(f"col_opts_{i}", "").split(",")] if kind == "select" else []
        opts = [o for o in opts if o][:20] or (YES_NO if kind == "select" else [])
        out.append((name, kind, opts))
    return out


@require_http_methods(["GET", "POST"])
def new(request):
    space = request.space
    title, text, errors, src = "", "", {}, "paste"
    p = error = None
    if request.method == "POST":
        src = request.POST.get("src", "paste")
        title = clean_text(request.POST.get("title", ""))[:200]
        no_one = "받을 사람이나 그룹을 골라 주세요. 동학년 명부에 반을 고른 선생님이 없어요."  # prototype #create
        if src == "preset":
            spec = PRESETS.get(request.POST.get("preset", ""))
            if spec and not roster_rows(space, spec["roster"]):
                errors["aud"] = no_one
            elif spec:
                c = _create_cols(request, spec["title"], Collection.Source.PRESET, spec["fields"],
                                 spec["unit_head"], spec["multi"], roster=spec["roster"])
                return redirect(f"/s/{space.token}/c/{c.key}/")
            errors["preset"] = "양식을 하나 골라 주세요."
        elif src == "sheet":
            cols = _sheet_cols(request.POST)
            multi = request.POST.get("multi") == "on"
            if not cols:
                errors["sheet"] = "받을 칸 이름을 하나 이상 적어 주세요."
            elif not roster_rows(space, "class"):
                errors["aud"] = no_one
            else:
                # prototype: 여러 줄이고 숫자 칸이 2개 이상이면 '금액'(자동 계산) 칸을 덧붙인다
                if multi and sum(k == "number" for _, k, _ in cols) >= 2:
                    cols.append(("금액", "calc", []))
                c = _create_cols(request, title or f"{cols[0][0]} 조사", Collection.Source.SHEET,
                                 [(n, k) for n, k, _ in cols], SHEET_HEAD, multi, options=[o for _, _, o in cols])
                return redirect(f"/s/{space.token}/c/{c.key}/")
        elif src == "paste":
            text = request.POST.get("paste", "")
            p, error = _read(text)
            if not title:
                errors["title"] = "제목을 적어 주세요."
            if not p:
                errors["paste"] = error or "표를 붙여넣어 주세요. 한글·엑셀에서 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요."
            if not errors and not roster_rows(space, "class"):
                errors["aud"] = no_one
            if not errors:
                c = _create(request, title, p)
                return redirect(f"/s/{space.token}/c/{c.key}/")
        else:
            src, errors["src"] = "paste", "가져오는 방식을 골라 주세요."
    ctx = _space_ctx(request, page="collect", title=title, paste=text, errors=errors, src=src,
                     srcs=[("paste", "표 붙여넣기"), ("sheet", "직접 입력"), ("preset", "자주 쓰는 양식")],
                     sheet_cols=[{"i": i, "name": request.POST.get(f"col_name_{i}", "")} for i in range(SHEET_COLS)],
                     presets=PRESETS.items(), multi=request.POST.get("multi") == "on", outside=_outside(request),
                     roster=roster_rows(space, "class"), roster_all=len(roster_rows(space, "people")))
    ctx.update(_paste_ctx(p, error))
    return render(request, "collect/new.html", ctx, status=400 if errors else 200)


def _create_cols(request, title, source, fields, unit_head, multi, options=None, roster="class"):
    """직접 입력·양식: 칸 + 동학년 명부 줄(C4). 칸 값(Cell)은 내는 사람이 만든다."""
    options = options or [[] for _ in fields]
    with transaction.atomic():
        c = Collection.objects.create(space=request.space, created_by=request.member, title=title[:200],
                                      source=source, unit_head=unit_head, multi=multi, allow_outside=_outside(request))
        Field.objects.bulk_create([Field(collection=c, pos=i, name=n, kind=k, options=o)
                                   for i, ((n, k), o) in enumerate(zip(fields, options))])
        _roster_rows(c, request.space, roster)
    return c


def _outside(request):
    return request.POST.get("outside") == "on"


def _roster_rows(c, space, mode):
    Row.objects.bulk_create([Row(collection=c, pos=i, unit=r["unit"], who=r["who"], member=r["member"])
                             for i, r in enumerate(roster_rows(space, mode))])


def _create(request, title, p):
    valid = {k for k, _ in KINDS}
    with transaction.atomic():
        # 명부 우선(prototype TR.audRows·design): 표에서는 칸만 가져오고 줄은 동학년 명부(반별)로 만든다.
        c = Collection.objects.create(space=request.space, created_by=request.member, title=title,
                                      source=Collection.Source.PASTE, allow_outside=_outside(request), unit_head=SHEET_HEAD)
        fields = []
        for i, f in enumerate(p["fields"]):
            kind = request.POST.get(f"kind_{i}")
            kind = kind if kind in valid else f["kind"]
            options = f["options"] if kind == f["kind"] else []
            if kind == "select" and not options:
                options = YES_NO
            fields.append(Field(collection=c, pos=i, name=f["name"], kind=kind, options=options if kind == "select" else []))
        Field.objects.bulk_create(fields)
        _roster_rows(c, request.space, "class")
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
    ctx.update(tsv=tsv, oob=True, roster=roster_rows(request.space, "class"))
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
