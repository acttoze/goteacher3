"""C2·C3 새로 모으기 — 표 붙여넣기·직접 입력·자주 쓰는 양식. 동작은 prototype collectNew, 모습은 design/collect-new.html.
C6 합본 — detail(전체 화면)·live(표·진행·미제출만, htmx 5초 폴링). 동작은 prototype collectDetail, 모습은 design/collect-detail.html.

붙여넣은 HTML은 미리보기 요청에서 한 번 TSV로 바꿔 돌려줄 뿐 저장·렌더하지 않는다(paste.py).
만들 때는 TSV를 다시 읽어 Collection(source=paste) + Field + Row를 만든다. Cell은 만들지 않는다(낸 시각 계산).
공간 소속은 SpaceMiddleware가 확인하고, 조회는 모두 in_space()를 거친다.
"""
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from spaces.models import get_in_space_or_404
from spaces.views import _space_ctx

from .models import Cell, Collection, Field, Row
from .paste import YES_NO, PasteError, clean_text, guess_kind, html_to_tsv, parse, student_info
from .presets import PRESETS
from .recipients import roster_rows
from .submit import NA, _head, _matrix, _need

# design TYPES(받는 방식 고르기). 자동 계산(calc)·링크(url)는 여기서 고르지 않는다.
KINDS = [("number", "숫자"), ("text", "글자"), ("select", "고르기"), ("date", "날짜")]
SHEET_COLS = 6  # 직접 입력 칸 수(design: 더 많으면 표 붙여넣기)
SHEET_HEAD = ["학년반", "담임"]  # prototype lockCols(담임만·반별 1칸)


def _read(text, how=""):
    """(파싱 결과, 오류 문구). 빈 글자는 둘 다 None. how: 학생 정보 칸 정리(design data-pii: count·drop)."""
    if not text.strip():
        return None, None
    try:
        return _pii(parse(text), how), None
    except PasteError as e:
        return None, str(e)


PII_COUNT = "참가 인원(명)"  # design '인원 수로 바꾸기'가 넣는 칸
PII_MSG = "학생 개인정보로 보이는 열을 먼저 정리해 주세요."  # design check()


def _pii(p, how):
    """C7: 학생 정보 칸을 p["pii"]에 적는다. how=count면 빼고 인원 칸 하나로, drop이면 빼기만(design resolved)."""
    pii = [f["name"] for f in p["fields"] if student_info(f["name"])]
    if pii and how in ("count", "drop"):
        p["fields"] = [f for f in p["fields"] if not student_info(f["name"])]
        if how == "count" and PII_COUNT not in [f["name"] for f in p["fields"]]:
            p["fields"].append({"name": PII_COUNT, "kind": "number", "options": []})
        if not p["fields"]:
            raise PasteError("받을 칸이 없어요. 학생 열을 빼고 나니 남은 칸이 없어요. 「인원 수로 바꾸기」를 눌러 주세요.")
        p["resolved"], pii = how, []
    for f in p["fields"]:
        f["pii"] = f["name"] in pii
    p["pii"], p["ask"] = pii, [f["name"] for f in p["fields"] if not f["pii"]]
    return p


def _paste_ctx(p, error):
    return {"p": p, "error": error, "kinds": KINDS, "pii_how": (p or {}).get("resolved", "")}


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
            pii = [n for n, _, _ in cols if student_info(n)]
            if not cols:
                errors["sheet"] = "받을 칸 이름을 하나 이상 적어 주세요."
            elif pii:  # C7: 브라우저 경고만 믿지 않는다
                errors["sheet"] = (f"학생 개인정보로 보이는 칸이 있어요: {', '.join(pii)}. "
                                   "고티처는 교직원 정보만 받아요. 이 칸은 빼거나 반별 인원 수(예: 참가 인원(명))로 바꿔 주세요.")
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
            text, how = request.POST.get("paste", ""), request.POST.get("pii", "")
            p, error = _read(text, how)
            if not title:
                errors["title"] = "제목을 적어 주세요."
            if not p:
                errors["paste"] = error or "표를 붙여넣어 주세요. 한글·엑셀에서 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요."
            elif p["pii"]:  # C7: 학생 정보 칸이 남아 있으면 만들지 않는다
                errors["pii"] = PII_MSG
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
    p, error = _read(tsv or request.POST.get("paste", ""), request.POST.get("pii", ""))
    if html and not tsv:
        error = "붙여넣은 내용에서 표를 찾지 못했어요. 표를 드래그해 다시 복사해 주세요."
    ctx = _paste_ctx(p, error)
    ctx.update(tsv=tsv, oob=True, roster=roster_rows(request.space, "class"))
    return render(request, "collect/_paste_out.html", ctx)


def _num(v):
    """숫자 칸 값 → Decimal(아니면 None)."""
    try:
        return Decimal(v.replace(",", "")) if v else None
    except InvalidOperation:
        return None


def _fmt(n):
    return "" if n is None else f"{n.normalize():,f}" if n != n.to_integral() else f"{int(n):,}"


def _board(c):
    """합본 표·진행·미제출. 냈는지는 받는 사람 화면과 같은 판정(submit._units)을 쓴다. 값은 템플릿이 이스케이프한다."""
    ctx = _head(c)
    fields = list(c.fields.all())
    nums = [f for f in fields if f.kind == Field.Kind.NUMBER]
    matrix, need, now = _matrix(c), _need(c), timezone.now()
    vals, last = {}, {}
    for rid, fid, v, by, at in Cell.objects.in_space(c.space).filter(row__collection=c).values_list("row_id", "field_id", "value", "by", "at"):
        vals.setdefault(rid, {})[fid] = v
        if rid not in last or at > last[rid][0]:
            last[rid] = (at, by)
    groups = {}
    for r in c.rows.all():
        groups.setdefault((r.unit, r.who, r.member_id) if c.multi else r.pk, []).append(r)
    sums = {f.pk: Decimal(0) for f in fields if f.kind in (Field.Kind.NUMBER, Field.Kind.CALC)}
    for u in ctx["units"]:
        r0 = u["row"]
        group = groups[(r0.unit, r0.who, r0.member_id) if c.multi else r0.pk]
        lines = [r for r in group if r.pk in vals] or group[:1]
        u["lines"] = []
        for r in lines:
            rv, cells = vals.get(r.pk, {}), []
            for f in fields:
                v = rv.get(f.pk, "")
                if f.kind == Field.Kind.CALC:  # prototype itemAmount: 숫자 칸이 둘 이상이면 곱
                    ns = [_num(rv.get(n.pk, "")) or 0 for n in nums]
                    n = Decimal(1)
                    for x in ns:
                        n *= x
                    n = n if len(ns) >= 2 and r.pk in vals else None
                elif f.kind == Field.Kind.NUMBER:
                    n = _num(v)
                else:
                    n = None
                if n is not None and f.pk in sums:
                    sums[f.pk] += n
                cell = {"v": v, "r": f.pk in sums, "show": _fmt(n) if f.pk in sums else v}
                if f.kind == Field.Kind.URL:
                    cell["href"] = v if re.match(r"https?://", v) else None  # 다른 스킴은 글자로만
                if matrix:
                    cell["mark"] = "na" if v == NA else "ok" if v else "no"
                cells.append(cell)
            u["lines"].append(cells)
        at_by = max((last[r.pk] for r in group if r.pk in last), default=None, key=lambda x: x[0])
        u["by"] = at_by[1] if at_by else ""
        u["proxy"] = bool(u["by"]) and u["by"] != r0.who
        u["n_cells"] = sum(len(vals.get(r.pk, {})) for r in group)
        u["just"] = bool(u["at"]) and (now - u["at"]).total_seconds() < 6  # 방금 들어온 줄 한 번 깜빡임
    ctx.update(fields=fields, matrix=matrix, need=need, sums=[_fmt(sums[f.pk]) if f.pk in sums else "" for f in fields],
               has_sum=bool(sums) and not matrix, miss=[u for u in ctx["units"] if not u["done"]])
    return ctx


@never_cache  # 공용 PC: 로그아웃 뒤 '뒤로 가기'로 합본 값이 보이지 않게
@require_GET
def detail(request, key):
    """합본 화면(design/collect-detail.html). 표·진행·미제출은 live로 5초마다 다시 받는다."""
    c = get_in_space_or_404(Collection, request, key=key)
    link = f"{settings.SITE_URL}/c/{c.link_token}"
    me = request.member
    msg = (f"[고티처] {c.title}\n\n아래 링크에서 우리 반 칸만 채워 주세요. 로그인 없이 30초면 돼요.\n"
           f"받는 칸: {', '.join(f.name for f in c.fields.all())}\n{link}\n\n보낸 사람: {me.name} ({me.cls})")
    ctx = _space_ctx(request, page="collect", link=link, msg=msg, **_board(c))
    return render(request, "collect/detail.html", ctx)


@never_cache
@require_GET
def live(request, key):
    """합본 카드만(htmx 5초 폴링). 공간 확인은 detail과 같다."""
    c = get_in_space_or_404(Collection, request, key=key)
    return render(request, "collect/_board.html", {"space": request.space, **_board(c)})
