"""C2·C3 새 취합 — 표 붙여넣기·직접 입력·자주 쓰는 양식. 동작은 prototype collectNew, 모습은 design/collect-new.html.
C6 합본 — detail(전체 화면)·live(표·진행·미제출만, htmx 5초 폴링). 동작은 prototype collectDetail, 모습은 design/collect-detail.html.

붙여넣은 HTML은 미리보기 요청에서 한 번 TSV로 바꿔 돌려줄 뿐 저장·렌더하지 않는다(paste.py).
만들 때는 TSV를 다시 읽어 Collection(source=paste) + Field + Row를 만든다. Cell은 만들지 않는다(낸 시각 계산).
공간 소속은 SpaceMiddleware가 확인하고, 조회는 모두 in_space()를 거친다.
"""
import re
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Value
from django.db.models.functions import Replace
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from hwpfiles.models import SourceFile
from hwpfiles.views import PREVIEW_CSP, pick_ctx, result_ctx
from spaces.gates import school_required
from spaces.models import Member, leads, new_token
from spaces.views import _space_ctx

from .access import can_open, owner_view
from .models import Cell, Collection, Field, Notification
from . import hwpform
from .paste import YES_NO, PasteError, clean_text, guess_kind, html_to_tsv, parse, student_info
from .presets import PRESETS
from .recipients import (GROUPS, audience_form, audience_head, audience_rows, audience_text, audience_where, make_rows,
                         read_audience, roster_rows, stored_audience, unmatched_count)
from .submit import NA, _head, _matrix, _need

# design TYPES(받는 방식 고르기). 자동 계산(calc)·링크(url)는 여기서 고르지 않는다.
KINDS = [("number", "숫자"), ("text", "글자"), ("select", "고르기"), ("date", "날짜")]
SHEET_COLS = 6  # 직접 입력 칸 수(design: 더 많으면 표 붙여넣기)


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


@never_cache  # 개인 데이터(명부·붙여넣은 값)가 나오는 화면은 모두 캐시 금지(G3 S6)
@require_http_methods(["GET", "POST"])
def new(request):
    space = request.space
    title, text, errors, src = "", "", {}, "paste"
    p = error = sf = hwpf = None
    data = request.POST if request.method == "POST" else request.GET  # 일정 취합 칸 ＋: ?due_d=날짜&aud=…로 미리 채운다
    aud = read_audience(space, data)
    due_d, due_at = _read_new_due(data)

    def made(c):
        if due_at:
            c.due = due_at
            c.save(update_fields=["due"])
        return redirect(f"/s/{space.token}/c/{c.key}/")

    budget, budget_raw = None, ""
    if request.method == "POST":
        src = request.POST.get("src", "paste")
        if request.POST.get("budget_go") or (src == "preset" and request.POST.get("preset") == "supplies"):
            src = "budget"  # S1: 옛 학습준비물 양식 자리(preset=supplies)도 예산 품목 취합으로
        title = clean_text(request.POST.get("title", ""))[:200]
        rows = audience_rows(space, aud)
        no_one = ("받을 사람이나 소속을 골라 주세요." if space.is_school else
                  f"받을 사람이나 그룹을 골라 주세요. {request.space.word} 명부에 반을 고른 선생님이 없어요.")  # prototype #create
        if src == "budget":
            # S1 예산 품목 취합(강조 카드). 받는 사람은 옛 학습준비물 양식과 같이 담임 반별.
            spec, budget_raw = PRESETS["supplies"], request.POST.get("budget", "").strip()[:20]
            budget, bad = _read_budget(budget_raw)
            if bad:
                errors["budget"] = bad
            elif not roster_rows(space, spec["roster"]):
                errors["budget"] = f"받을 반이 없어요. {space.word} 명부에 반을 고른 담임 선생님이 없어요."
            else:
                c = _create_cols(request, clean_text(request.POST.get("budget_title", ""))[:200] or spec["title"],
                                 Collection.Source.BUDGET, spec["fields"], spec["unit_head"], spec["multi"],
                                 roster_rows(space, spec["roster"]))
                c.budget = budget
                c.save(update_fields=["budget"])
                return made(c)
            src = request.POST.get("src", "paste")
        elif src == "preset":
            spec = PRESETS.get(request.POST.get("preset", ""))
            if spec and not roster_rows(space, spec["roster"]):
                errors["aud"] = no_one
            elif spec:
                c = _create_cols(request, spec["title"], Collection.Source.PRESET, spec["fields"],
                                 spec["unit_head"], spec["multi"], roster_rows(space, spec["roster"]))
                return made(c)
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
            elif not rows:
                errors["aud"] = no_one
            else:
                # prototype: 여러 줄이고 숫자 칸이 2개 이상이면 '금액'(자동 계산) 칸을 덧붙인다
                if multi and sum(k == "number" for _, k, _ in cols) >= 2:
                    cols.append(("금액", "calc", []))
                c = _create_cols(request, title or f"{cols[0][0]} 조사", Collection.Source.SHEET,
                                 [(n, k) for n, k, _ in cols], audience_head(aud), multi, rows, options=[o for _, _, o in cols],
                                 audience=stored_audience(aud, rows))
                return made(c)
        elif src == "hwp":
            # E6 칸 확인: 고른 표의 자동 라벨을 다듬은 값(POST)을 다시 검사해 취합을 만든다(hwpform).
            sf = SourceFile.objects.in_space(space).filter(key=request.POST.get("hwp", "")[:64].replace("\x00", "")).first()
            if sf is not None and not can_open(request, sf.created_by_id):
                sf = None
            if not title:
                errors["title"] = "제목을 적어 주세요."
            if sf is None or sf.status != SourceFile.Status.DONE:
                sf, errors["hwp"] = None, "한글 파일을 올려 주세요. 다 읽으면 표를 고를 수 있어요."
            elif sf.picked is None:
                errors["hwp"] = "취합할 표를 미리보기에서 한 번 눌러 골라 주세요."
            elif request.POST.get("hwp_table") != f"{sf.table_sec}/{sf.table_index}":
                hwpf = _hwp_form(request, sf, None)
                errors["hwp"] = hwpform.STALE
            else:
                hwpf = _hwp_form(request, sf, request.POST)
                if hwpf["error"]:
                    errors["hwp"] = hwpf["error"]
                elif sf.collection_id:
                    errors["hwp"] = ("이 한글 파일로는 이미 취합을 만들었어요. 같은 양식으로 또 걷으려면 그 취합에서 "
                                     "「다시 걷기」를 쓰거나 파일을 다시 올려 주세요.")
                elif not rows:
                    errors["aud"] = no_one
                elif not errors:
                    c = _create_hwp(request, title, sf, hwpf, aud, rows)
                    if c is not None:
                        return made(c)
                    errors["hwp"] = "다른 창에서 이 파일로 취합을 먼저 만들었어요. 취합 목록을 확인해 주세요."
        elif src == "paste":
            text, how = request.POST.get("paste", ""), request.POST.get("pii", "")
            p, error = _read(text, how)
            if not title:
                errors["title"] = "제목을 적어 주세요."
            if not p:
                errors["paste"] = error or "표를 붙여넣어 주세요. 한글·엑셀에서 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요."
            elif p["pii"]:  # C7: 학생 정보 칸이 남아 있으면 만들지 않는다
                errors["pii"] = PII_MSG
            if not errors and not rows:
                errors["aud"] = no_one
            if not errors:
                c = _create(request, title, p, aud, rows)
                return made(c)
        else:
            src, errors["src"] = "paste", "가져오는 방식을 골라 주세요."
    ctx = _space_ctx(request, page="collect", title=title, paste=text, errors=errors, src=src, hwp_max_mb=settings.HWP_MAX_UPLOAD // (1024 * 1024), max_body=settings.MAX_REQUEST_BYTES,
                     srcs=[("hwp", "한글 양식 올리기"), ("paste", "표 붙여넣기"), ("sheet", "직접 입력"),
                           ("preset", "자주 쓰는 양식")],
                     hwp=pick_ctx(sf) if sf else None, hwpf=hwpf,
                     sheet_cols=[{"i": i, "name": request.POST.get(f"col_name_{i}", "")} for i in range(SHEET_COLS)],
                     presets=[(k, p) for k, p in PRESETS.items() if k != "supplies"], budget_spec=PRESETS["supplies"],
                     budget_raw=budget_raw, budget_title=request.POST.get("budget_title", "")[:200], multi=request.POST.get("multi") == "on", outside=_outside(request),
                     roster=roster_rows(space, "class"), roster_all=len(roster_rows(space, "people")),
                     due_d=due_d)
    ctx.update(_paste_ctx(p, error))
    if space.is_school:
        ctx.update(audience_form(space, aud), **_aud_sum(space, aud))
    resp = render(request, "collect/new.html", ctx, status=400 if errors else 200)
    resp["Content-Security-Policy"] = PREVIEW_CSP  # 한글 쪽 미리보기(WASM)를 이 화면에서 그린다
    return resp


BUDGET_MAX = 1_000_000_000  # 한 곳당 예산 상한(원). 학급 예산은 이보다 훨씬 작다


def _read_budget(raw):
    """(예산 원 또는 None, 오류 문구). 비우면 예산 없이(합계만). 쉼표·'원'은 떼고 읽는다."""
    n = raw.replace(",", "").replace(" ", "").removesuffix("원")
    if not n:
        return None, None
    if not n.isdigit() or not 0 < int(n) <= BUDGET_MAX:
        return None, "예산은 원 단위 숫자로 써 주세요. 예: 150000"
    return int(n), None


def due_at(d):
    """마감은 날짜만 받는다(QC 2026-11-06). 저장은 그날 끝(23:59, Asia/Seoul): 그날 안에 내면 늦은 게 아니다."""
    return timezone.make_aware(datetime.combine(d, time(23, 59)))


def _read_new_due(data):
    """만들기 화면의 마감 날짜(기본 비움 = 마감 없음, 일정 취합 칸에서 오면 그 날). (날짜 글자, 저장할 시각 또는 None)."""
    try:
        d = datetime.strptime(data.get("due_d", ""), "%Y-%m-%d").date()
    except ValueError:
        return "", None
    if not 2000 <= d.year <= 2100:
        return "", None
    return d.isoformat(), due_at(d)


def _aud_sum(space, aud):
    """받는 곳 요약 줄(_aud_sum.html)과 받는 사람 칸 머리글·쪽지 문구."""
    rows = audience_rows(space, aud)
    head = audience_head(aud)
    return {"aud_n": len(rows), "aud_text": audience_text(aud, len(rows)), "aud_head_a": "소속" if head[0] == "그룹" else head[0], "aud_head_b": head[1],
            "aud_where": audience_where(aud), "aud_names": [r["who"] for r in rows] if aud.get("mode") == "people" else [],
            "aud_unmatched": unmatched_count(aud, rows)}


@never_cache
@require_POST
@school_required
def aud_summary(request):
    """받는 사람을 바꿀 때마다 요약 줄(htmx). 저장하지 않는다. part=box면 상자 안 전체(정렬을 바꿔 개인 목록 묶음이 바뀔 때)."""
    aud = read_audience(request.space, request.POST)
    if request.POST.get("part") == "box":
        return render(request, "collect/_aud_school_in.html",
                      {"space": request.space, "oob": True, **audience_form(request.space, aud), **_aud_sum(request.space, aud)})
    return render(request, "collect/_aud_sum.html", {"oob": True, **_aud_sum(request.space, aud)})


def _create_cols(request, title, source, fields, unit_head, multi, rows, options=None, audience=None):
    """직접 입력·양식: 칸 + 명부 줄(C4, 학교 공간은 고른 범위). 칸 값(Cell)은 내는 사람이 만든다."""
    options = options or [[] for _ in fields]
    with transaction.atomic():
        c = Collection.objects.create(space=request.space, created_by=request.member, title=title[:200],
                                      source=source, unit_head=unit_head, multi=multi, allow_outside=_outside(request),
                                      audience=audience or {})
        Field.objects.bulk_create([Field(collection=c, pos=i, name=n, kind=k, options=o)
                                   for i, ((n, k), o) in enumerate(zip(fields, options))])
        make_rows(c, rows)
    return c


def _outside(request):
    return request.POST.get("outside") == "on"


def _create(request, title, p, aud, rows):
    valid = {k for k, _ in KINDS}
    with transaction.atomic():
        # 명부 우선(prototype TR.audRows·design): 표에서는 칸만 가져오고 줄은 명부(동학년은 반별, 학교 공간은 고른 범위)로 만든다.
        c = Collection.objects.create(space=request.space, created_by=request.member, title=title,
                                      source=Collection.Source.PASTE, allow_outside=_outside(request), unit_head=audience_head(aud),
                                      audience=stored_audience(aud, rows))
        fields = []
        for i, f in enumerate(p["fields"]):
            kind = request.POST.get(f"kind_{i}")
            kind = kind if kind in valid else f["kind"]
            options = f["options"] if kind == f["kind"] else []
            if kind == "select" and not options:
                options = YES_NO
            fields.append(Field(collection=c, pos=i, name=f["name"], kind=kind, options=options if kind == "select" else []))
        Field.objects.bulk_create(fields)
        make_rows(c, rows)
    return c


def _hwp_form(request, sf, post):
    """칸 확인 값(hwpform.read) + 명부 ↔ 표 줄 짝. 화면(_hwp_fields.html)과 만들기가 같이 쓴다."""
    form = hwpform.read(sf.picked_schema(), post)
    roster = audience_rows(request.space, read_audience(request.space, request.POST))
    pairs, spare = hwpform.plan(form, roster, request.space.grade)
    form.update(f=sf, pairs=pairs, spare=spare, short=sum(r is None for _, r in pairs) if form["mode"] == "rows" else 0,
                table=f"{sf.table_sec}/{sf.table_index}", picked=sf.picked, roles=hwpform.ROLES, kinds=KINDS,
                adds=[m for m, r in pairs if r is None] if form["mode"] == "rows" else [])
    who = {r: m for m, r in pairs if r is not None}
    for x in form["rows"]:
        x["who"] = who.get(x["r"])
    form["on"] = sum(x["on"] for x in form["rows"])
    return form


@never_cache
@require_http_methods(["GET", "POST"])
@owner_view(SourceFile)
def hwp_fields(request, sf):
    """E6 칸 확인 부분 화면(htmx). GET: 표를 고른 직후 자동 추정 그대로. POST: 고친 값으로 다시 그림(저장 안 함).
    회원·같은 공간·완료되고 표를 고른 파일만. 다른 공간 파일 키는 404."""
    if sf.status != SourceFile.Status.DONE or sf.picked is None:
        return render(request, "collect/_hwp_fields.html", {"gone": True}, status=409)
    post = request.POST if request.method == "POST" else None
    if post is not None and post.get("hwp_table") != f"{sf.table_sec}/{sf.table_index}":
        post = None  # 그 사이 다른 표를 골랐으면 새 표의 자동 추정으로
    form = _hwp_form(request, sf, post)
    return render(request, "collect/_hwp_fields.html", {"hwpf": form, "space": request.space, "oob": True})


def _create_hwp(request, title, sf, form, aud, roster):
    """한글 양식: 켠 입력 칸만 Field + 명부 줄(C4, 학교 공간은 고른 범위) + SourceFile 연결·칸 매핑. 동시에 두 번 만들면 None."""
    s = sf.picked_schema()
    with transaction.atomic():
        locked = SourceFile.objects.select_for_update().get(pk=sf.pk)
        if locked.collection_id:
            return None
        c = Collection.objects.create(space=request.space, created_by=request.member, title=title,
                                      source=Collection.Source.HWP, allow_outside=_outside(request), unit_head=audience_head(aud),
                                      audience=stored_audience(aud, roster))
        Field.objects.bulk_create([Field(collection=c, pos=i, name=it["name"], kind=it["kind"],
                                         options=it["options"] if it["kind"] == "select" else [])
                                   for i, it in enumerate(form["inputs"])])
        make_rows(c, roster)
        rows = list(c.rows.order_by("pos"))
        got = hwpform.assign(form["rows"], roster, request.space.grade) if form["mode"] == "rows" else []
        locked.collection = c
        locked.cellmap = hwpform.cellmap(s, locked.table_sec, form, rows, got)
        locked.save(update_fields=["collection", "cellmap"])
    return c


@never_cache
@require_POST
def preview(request):
    """붙여넣기 미리보기(htmx). html이 오면(한글·엑셀 클립보드) TSV로 바꿔 입력 칸에 넣도록 함께 돌려준다."""
    html = request.POST.get("html", "")
    try:
        tsv = html_to_tsv(html) if html else ""
    except PasteError as e:  # 너무 큰 표: 바꾼 글자를 싣지 않는다
        return render(request, "collect/_paste_out.html", {**_paste_ctx(None, str(e)), "oob": True, "is_school": request.space.is_school})
    p, error = _read(tsv or request.POST.get("paste", ""), request.POST.get("pii", ""))
    if html and not tsv:
        error = "붙여넣은 내용에서 표를 찾지 못했어요. 표를 드래그해 다시 복사해 주세요."
    ctx = _paste_ctx(p, error)
    ctx.update(tsv=tsv, oob=True, roster=roster_rows(request.space, "class"), is_school=request.space.is_school)
    return render(request, "collect/_paste_out.html", ctx)


def _num(v):
    """숫자 칸 값 → Decimal(아니면 None)."""
    try:
        return Decimal(v.replace(",", "")) if v else None
    except InvalidOperation:
        return None


def _fmt(n):
    return "" if n is None else f"{n.normalize():,f}"  # int를 거치지 않는다(아주 큰 곱은 정수 글자 변환 한도를 넘는다)


def _board(c):
    """합본 표·진행·미제출. 냈는지는 받는 사람 화면과 같은 판정(submit._units)을 쓴다. 값은 템플릿이 이스케이프한다."""
    ctx = {**_head(c), "stamp": _stamp(c)}  # 값을 읽기 전에: 읽는 사이 들어온 제출은 다음 폴링에 잡힌다
    if c.due and not c.closed_at:  # 회원 없는 줄(부서·명부 밖)에는 마감 🔔이 못 간다(bell.remind): 합본에 알린다
        ctx["no_bell"] = sum(not u["done"] and not u["row"].member_id and not u["row"].group for u in ctx["units"])
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
    budget = c.source == Collection.Source.BUDGET
    sums = {f.pk: Decimal(0) for f in fields if f.kind in (Field.Kind.NUMBER, Field.Kind.CALC)}
    for u in ctx["units"]:
        r0 = u["row"]
        group = groups[(r0.unit, r0.who, r0.member_id) if c.multi else r0.pk]
        lines = [r for r in group if r.pk in vals] or group[:1]
        u["lines"], total = [], Decimal(0)
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
                if n is not None and f.kind == Field.Kind.CALC:
                    total += n
                # x: 내보내기 값(D4). 자동 계산은 쉼표 없는 수(prototype itemVals), 나머지는 낸 값 그대로
                x = f"{n.normalize():f}" if f.kind == Field.Kind.CALC and n is not None else "" if f.kind == Field.Kind.CALC else v
                cell = {"v": v, "x": x, "r": f.pk in sums, "show": _fmt(n) if f.pk in sums else v}
                if f.kind == Field.Kind.URL:
                    cell["href"] = v if re.match(r"https?://", v) else None  # 다른 스킴은 글자로만
                if matrix:
                    cell["mark"] = "na" if v == NA else "ok" if v else "no"
                cells.append(cell)
            if budget:  # S1 합본 화면: 상품 주소 칸 대신 내용을 그 주소로 잇는다(칸이 많아 폭이 모자라다). 내보내기 값은 그대로
                link = next((x.get("href") for x in cells if "href" in x), None)
                cells[0]["link"] = link
                for x in cells:
                    x["hide"] = "href" in x
            u["lines"].append(cells)
        if budget:  # S1 곳별 합계·남은 금액·예산 초과
            u["total"] = _fmt(total) if u["done"] else ""
            left = c.budget - total if c.budget and u["done"] else None
            u["left"], u["over"] = (_fmt(abs(left)), left < 0) if left is not None else ("", False)
        at_by = max((last[r.pk] for r in group if r.pk in last), default=None, key=lambda x: x[0])
        u["by"] = at_by[1] if at_by else ""
        u["proxy"] = bool(u["by"]) and u["by"] != r0.who and not r0.group
        u["n_cells"] = sum(len(vals.get(r.pk, {})) for r in group)
        u["just"] = bool(u["at"]) and (now - u["at"]).total_seconds() < 6  # 방금 들어온 줄 한 번 깜빡임
    miss = [u for u in ctx["units"] if not u["done"]]
    sum_row = [_fmt(sums[f.pk]) if f.pk in sums and (not budget or f.kind == Field.Kind.CALC) else "" for f in fields]  # 예산: 금액만 더한다
    ctx.update(budget=budget, budget_text=f"{c.budget:,}" if c.budget else "",
               n_over=sum(u.get("over", False) for u in ctx["units"]),
               cols=[(f, s) for f, s in zip(fields, sum_row) if not (budget and f.kind == Field.Kind.URL)],
               fields=fields, matrix=matrix, need=need, sums=sum_row,
               has_sum=bool(sums) and not matrix, miss=miss[:MISS_SHOW], miss_more=len(miss) - len(miss[:MISS_SHOW]),
               **_nudge(c, miss, vals, fields, ctx["per"]))
    ctx["miss_groups"] = _miss_groups(c, miss) if c.space.is_school and len(miss) > MISS_SHOW else []
    return ctx


MISS_SHOW = 18  # prototype missShow


def _nudge_text(c, units):
    return (f"[고티처] 「{c.title}」 아직 다 못 채운 분: {', '.join(u['label'] for u in units)}\n"
            f"{_due_text(c)} · 링크에서 {_where(c)}만 채워 주세요.\n{settings.SITE_URL}/c/{c.link_token}")


def _miss_groups(c, miss):
    """학교 규모(H4): 미제출이 많으면 사람 칩 대신 그룹별 수. 누르면 그 그룹 독촉 문구. 폴링마다 다시 그려도 접힘 상태가 없다."""
    group_of = dict(Member.objects.in_space(c.space).values_list("pk", "group"))
    fold = {}
    for u in miss:
        r = u["row"]
        fold.setdefault(r.group or group_of.get(r.member_id, ""), []).append(u)
    order = [g for g, _ in c.space.group_choices] + [""]  # 학교 소속순(Q2), 명부 밖 줄은 끝
    return [{"label": GROUPS.get(g, "명부 밖"), "n": len(fold[g]), "text": _nudge_text(c, fold[g])} for g in order if g in fold]


def _nudge(c, miss, vals, fields, per):
    """D2 독촉 문구(prototype collectDetail .miss button·#remindAll). 칩 문구는 그 줄로 바로 가는 링크.
    받는 사람 모드: 담임(학년반) = 'hr', 그 밖 = 사람. 연수 이수는 안 한 연수만 적는다."""
    matrix, hr, link, dt = _matrix(c), c.unit_head[:1] == ["학년반"], f"{settings.SITE_URL}/c/{c.link_token}", _due_text(c)
    for u in miss:
        r, rv = u["row"], vals.get(u["row"].pk, {})
        u["label"] = f"{r.unit} {r.who}".strip() if hr or c.multi else r.who or r.unit
        if matrix:
            what = "아직 이수번호가 없는 연수: " + ", ".join(f.name for f in fields if f.kind != Field.Kind.CALC and not rv.get(f.pk))
        else:
            what = f"{r.unit if hr or r.group else '선생님'} 칸이 아직 비어 있어요."
        u["nudge"] = f"{r.who or r.unit} 선생님, 「{c.title}」 {what} {dt}\n{link}/u/{r.key}"
    label = "덜 마친 사람" if matrix else "미제출 반" if c.multi else "미제출"
    if not miss:
        return {"nudge_all": "", "nudge_done": f"모두 냈어요. 독촉할 {'반' if per == '반' else '분'}이 없어요.",
                "miss_label": label, "miss_unit": ""}
    return {"nudge_all": _nudge_text(c, miss),
            "nudge_done": f"{label} {len(miss)} 독촉 문구를 복사했어요.", "miss_label": label,
            "miss_unit": "개 반" if per == "반" else "곳" if per == "곳" else "명"}


@never_cache  # 공용 PC: 로그아웃 뒤 '뒤로 가기'로 합본 값이 보이지 않게
@require_GET
@owner_view(Collection)
def detail(request, c):
    """합본 화면(design/collect-detail.html). 표·진행·미제출은 live로 5초마다 다시 받는다."""
    ctx = _space_ctx(request, page="collect", can_delete=_owner(request, c),
                     **_link_ctx(request, c), **_board(c), **_due_ctx(request, c),
                     hwp_result=result_ctx(request.space, c) if c.source == Collection.Source.HWP else None)
    resp = render(request, "collect/detail.html", ctx)
    if ctx["hwp_result"]:
        resp["Content-Security-Policy"] = PREVIEW_CSP  # 채운 결과 쪽 미리보기(WASM, F2)
    return resp


def _link_ctx(request, c):
    """받는 링크 카드: 링크와 처음 보낼 쪽지 문구(design에 쪽지 카드가 없어 이 카드의 단추로 복사)."""
    link = f"{settings.SITE_URL}/c/{c.link_token}"
    me = request.member
    where = _where(c) if request.space.is_school else "우리 반 칸"
    msg = (f"[고티처] {c.title}\n\n아래 링크에서 {where}만 채워 주세요. 로그인 없이 30초면 돼요.\n"
           f"받는 칸: {', '.join(f.name for f in c.fields.all())}\n{link}\n\n보낸 사람: {me.name} ({me.cls})")
    return {"link": link, "msg": msg, "can_relink": _owner(request, c)}


def _owner(request, c):
    """prototype c.mine: 만든 사람만 마감을 정하고 잠근다.
    만든 사람이 없으면(내보낸 회원) 대표(동학년은 가장 먼저 들어온 회원, 학교 공간은 관리자)가 넘겨받는다."""
    if c.created_by_id is not None:
        return c.created_by_id == request.member.pk
    return leads(request.space, request.member)


def _can_close(request, c):
    """마감 정하기·잠그기(H4): 만든 사람, 학교 공간은 관리자도. 지우기·링크 새로 만들기·다시 걷기는 _owner 그대로."""
    return _owner(request, c) or (request.space.is_school and request.member.is_manager)


def _due_text(c):
    """마감 표기. 날짜만(QC 2026-11-06): 오늘이면 '오늘', 아니면 '11/3(화)', 없으면 '마감 미정'."""
    if not c.due:
        return "마감 미정"
    due = timezone.localtime(c.due)
    if due.date() == timezone.localdate():
        return "오늘"
    return c.due_day


def _where(c):
    head = c.unit_head[:1]
    return "내 줄" if _matrix(c) else "우리 반 칸" if head == ["학년반"] else "우리 소속 칸" if head == ["그룹"] else "내 칸"


def _due_ctx(request, c, saved=None, error=None):
    """마감 카드(design 마감 카드 + prototype dueBox). 바꾸기는 만든 사람만(prototype c.mine).
    saved: 방금 정함(first)·바꿈 — 안내 문구의 '안내/변경'과 알림 문구에 쓴다."""
    now = timezone.localtime()
    ctx = {"can_due": _can_close(request, c), "due_saved": saved, "due_error": error}
    if not c.due:
        d = (now + timedelta(days=4)).date()  # prototype 기본값: 오늘+4일
        return {**ctx, "due_d": d.isoformat()}
    due = timezone.localtime(c.due)
    days = (due.date() - now.date()).days
    short = _due_text(c)
    notice = (f"[고티처] 「{c.title}」 마감 {'안내' if saved != 'changed' else '변경'}: {short}\n"
              f"아직 안 채운 분은 링크에서 {_where(c)}만 채워 주세요.\n{settings.SITE_URL}/c/{c.link_token}")
    return {**ctx, "due_d": due.date().isoformat(), "due_short": short,
            "due_label": c.due_day,
            "due_left": "마감일이 지났어요" if days < 0 else "오늘 마감이에요" if days == 0 else f"{days}일 남았어요",
            "notice": notice}


@never_cache
@require_POST
@owner_view(Collection)
def due(request, c):
    """마감 정하기·바꾸기(htmx). 날짜만(Asia/Seoul, 그날 끝까지). 지난 날짜도 받는다(prototype). 카드와 제목 줄을 돌려준다."""
    if not _can_close(request, c):
        return HttpResponseForbidden("마감은 만든 선생님만 바꿀 수 있어요.")
    if c.closed_at:  # design: 잠근 동안 '마감 바꾸기' 꺼짐
        return HttpResponseForbidden("잠근 동안은 마감을 바꿀 수 없어요. 다시 연 뒤 바꿔 주세요.")
    try:
        d = datetime.strptime(request.POST.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        ctx = {"c": c, "space": request.space, **_due_ctx(request, c, error="마감 날짜를 골라 주세요.")}
        return render(request, "collect/_due.html", ctx)
    saved = "changed" if c.due else "first"
    c.due = due_at(d)
    c.save(update_fields=["due"])
    return render(request, "collect/_due.html", {"c": c, "space": request.space, "oob": True,
                                                  **_due_ctx(request, c, saved=saved)})


@never_cache
@require_POST
@owner_view(Collection)
def lock(request, c):
    """D5 마감하고 잠그기·다시 열기(htmx, 만든 사람과 학교 관리자). 잠그면 받는 링크가 더 받지 않는다(submit은 잠금 안에서 다시 확인).
    마감 카드를 바꾸고, 합본('마감됨'·'안 냄')과 받는 링크 카드는 함께 바꾼다(oob)."""
    if not _can_close(request, c):
        return HttpResponseForbidden("마감하고 잠그기는 만든 선생님만 할 수 있어요.")
    reopen = request.POST.get("open") == "1"
    with transaction.atomic():  # 들어오던 제출과 순서를 정한다(submit.unit도 같은 줄을 잠근다)
        c = Collection.objects.select_for_update().get(pk=c.pk)
        if reopen:
            c.closed_at = None
        elif not c.closed_at:
            c.closed_at = timezone.now()
        c.save(update_fields=["closed_at"])
    toast = "다시 열었어요. 링크로 다시 낼 수 있어요." if reopen else "마감하고 잠갔어요. 링크로는 더 낼 수 없어요."
    ctx = {"space": request.space, "oob": True, "lock_toast": toast,
           **_link_ctx(request, c), **_board(c), **_due_ctx(request, c)}
    return render(request, "collect/_lock_out.html", ctx)


@never_cache
@require_POST
@owner_view(Collection)
def relink(request, c):
    """받는 링크 새로 만들기(G3 S5, 만든 사람만). 옛 링크(줄 화면 포함)는 바로 404, 낸 값은 그대로.
    🔔 알림의 링크도 새 링크로 바꾼다. 응답은 잠그기와 같은 묶음(마감 카드 + 합본·받는 링크 카드 oob):
    마감 안내 문구에도 링크가 들어 있어서."""
    if not _owner(request, c):
        return HttpResponseForbidden("받는 링크는 만든 선생님만 새로 만들 수 있어요.")
    old, new = c.link_token, new_token()
    with transaction.atomic():
        c.link_token = new
        c.save(update_fields=["link_token"])
        Notification.objects.in_space(request.space).filter(row__collection=c).update(
            link=Replace("link", Value(f"/c/{old}/"), Value(f"/c/{new}/")))
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/c/{c.key}/")
    ctx = {"space": request.space, "oob": True, "relinked": True,
           **_link_ctx(request, c), **_board(c), **_due_ctx(request, c)}
    return render(request, "collect/_lock_out.html", ctx)


@require_POST
@owner_view(Collection)
def delete(request, c):
    """취합 지우기(처리방침 3항 '요청자가 지우면 파기', 만든 사람만). 칸·줄·제출 값·알림·결과 파일은 CASCADE.
    올린 한글 파일(SourceFile)은 SET_NULL이라 E3 purge처럼 따로 지운다. 디스크 파일은 E3 post_delete가 커밋 뒤 지운다."""
    if not _owner(request, c):
        return HttpResponseForbidden("취합은 만든 선생님만 지울 수 있어요.")
    with transaction.atomic():
        SourceFile.objects.in_space(request.space).filter(collection=c).delete()
        c.delete()
    return redirect(f"/s/{request.space.token}/c/")


@never_cache
@require_GET
@owner_view(Collection)
def live(request, c):
    """합본 카드만(htmx 5초 폴링). 공간 확인은 detail과 같다.
    바뀐 것이 없으면 204(H4): 200줄 표를 4초마다 보내고 다시 그리지 않는다. 낸 값·잠금·마감·제목이 바뀌면 표시 값이 달라진다."""
    if request.GET.get("v") == _stamp(c):
        return HttpResponse(status=204)
    return render(request, "collect/_board.html", {"space": request.space, **_board(c)})


def _stamp(c):
    cells = Cell.objects.in_space(c.space).filter(row__collection=c).aggregate(n=Count("pk"), at=Max("at"))
    return f"{c.updated_at.timestamp()}-{c.rows.count()}-{cells['n']}-{cells['at'].timestamp() if cells['at'] else 0}"
