"""H6 주간계획 표 붙여넣기. 동작은 prototype/assets/app.js parseWeek·weekPaste, 화면은 업무분장 붙여넣기(H3)와 같은 길.

학교 공간의 회원 누구나(일정 쓰기와 같은 권한), 만료 학교에서도 된다(일정은 계속). 표에서 쓰는 칸은 날짜·시간·내용·장소·담당뿐, 나머지는 버린다.
prototype은 담당이 명부에 있으면 그 이름으로 등록하지만, by는 H5부터 권한 근거('올린 사람 그대로')라 붙여넣은 사람으로 두고 담당은 target에 글자로 남긴다.
날짜만 '수요일'로 쓴 줄은 보고 있는 주(w)의 수요일이고, '10/30'의 해는 그 주에서 가장 가까운 해다. C7 학생 정보 규칙은 칸 이름용이라 제목에 쓰지 않는다('학생 명단 제출'은 업무 이름).
"""
import re
from datetime import date, timedelta

from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from collect.paste import FILLER, MAX_ROWS, MAX_TEXT, TOTAL, PasteError, html_to_tsv, tsv_lines
from spaces.gates import school_required
from spaces.models import Space
from spaces.views import _space_ctx

from .models import ALL, Event, reachable_q, scope_choices
from .parse import parse, slot_text
from .schedule import shown_monday
from .views import PLACE_MAX, _dates, _scope_ok

COLUMNS = {
    "date": re.compile(r"^(날짜|일자|일시|월일|월/일)$"),
    "dow": re.compile(r"^요일$"),  # 날짜 칸이 함께 있으면 날짜 칸이 우선
    "time": re.compile(r"^(시간|시각|블록|교시)$"),
    "title": re.compile(r"^(내용|행사|행사명|행사내용|주요행사|교육내용|교육활동|주요내용|활동|활동내용|제목|일정|계획)$"),
    "place": re.compile(r"^(장소|위치)$"),
    "who": re.compile(r"^(담당|담당자|담당교사|담당부서|주관|부서)$"),
}
ORDER = ("date", "time", "title", "place", "who")  # 머리글 없는 표의 칸 순서
HEAD_LINES = 5  # 머리글을 찾는 범위(표 위 제목 줄)
TITLE_MAX, TARGET_MAX = 200, 80
EMPTY = "표를 붙여넣어 주세요. 한글·엑셀에서 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요."
TOO_BIG = "표가 너무 커요. 500줄 안으로 나눠 붙여넣어 주세요."
NO_ROWS = "읽을 수 있는 줄이 없어요. 날짜와 내용 칸이 있는지 확인해 주세요."


def _column(cell):
    """칸 이름 → 어느 칸인가. 2줄 머리글이 합쳐진 '계획 내용'은 마지막 낱말로 본다."""
    for name in (re.sub(r"\s+", "", cell), cell.split(" ")[-1]):
        for key, pat in COLUMNS.items():
            if pat.match(name):
                return key
    return None


def _columns(line):
    found = {}
    for c, cell in enumerate(line):
        key = _column(cell)
        if key:
            found.setdefault(key, c)
    return found


def _complete(cols):
    return "title" in cols and ("date" in cols or "dow" in cols)


def _find_head(lines):
    """(칸 위치, 자료가 시작되는 줄). 합치지 않고 온 2줄 머리글은 아래 줄을 위 줄에 덮어 본다. 머리글이 없으면 (None, 0)."""
    for i, r in enumerate(lines[:HEAD_LINES]):
        below = lines[i + 1] if i + 1 < len(lines) else []
        extra = _columns(below)
        if len(extra) != sum(bool(c) for c in below):  # 아래 줄에 칸 이름 아닌 글자가 있으면 자료 줄
            extra = {}
        cols = {**_columns(r), **extra}
        if _complete(cols):
            return cols, i + (2 if extra else 1)
    return None, 0


def _nearest_year(mon, mo, dd):
    """'10/30' → 보고 있는 주에서 가장 가까운 해의 날짜. 그해에 없는 날짜(평년 2/29)면 None."""
    try:
        year = min((mon.year - 1, mon.year, mon.year + 1), key=lambda y: abs((date(y, int(mo), min(int(dd), 28)) - mon).days))
        return date(year, int(mo), int(dd))
    except ValueError:
        return None


def _tidy_time(raw):
    """'1블록~2블록' → '1~2블록', '14:00~15:00' → '14:00'(시작 시각으로)."""
    s = re.sub(r"(\d)\s*블록\s*[~–-]\s*(\d)\s*블록", r"\1~\2블록", raw)
    return re.sub(r"(\d{1,2}:\d{2})\s*[~–-]\s*(?:\d{1,2}:\d{2})?", r"\1", s)


def _read_date(raw, mon):
    """날짜 칸 → parse 결과. 10/28·10/28(수)·10/28 수·10.28.·2026-10-28·수요일·수·10/28~30·월~수.
    해석이 갈리는 것(거꾸로·없는 끝 날짜·날짜 두 개, 적힌 해와 다른 해)은 하루로 줄이지 않고 None."""
    s = _tidy_time(re.sub(r"\([월화수목금토일]\)", " ", raw))
    s = re.sub(r"(?<=[\d일.])\s+[월화수목금토일](?:요일)?(?=\s|[~–]|$)", " ", s)
    year = re.search(r"(?:^|[~–\s])(20\d\d)\s*[-./년]", s)
    s = re.sub(r"(?:^|(?<=[~–\s]))20\d\d\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", r"\1/\2", s)
    s = re.sub(r"\.(?=\s*(?:[~–]|$))", "", s).strip()
    if re.fullmatch("[월화수목금]", s):
        s += "요일"
    p = parse(s, mon, md=_nearest_year)
    if not p["date"] or (year and p["date"].year != int(year.group(1))):
        return None
    if re.search("[~–]", re.sub(r"\d\s*[~–]\s*\d\s*(블록|교시)", " ", s)) and not p["end"]:
        return None
    return None if re.search(r"\d\s*[/.월]\s*\d|요일", p["title"]) else p


def _read_slot(raw, mon):
    """시간 칸 → (시작 블록, 끝 블록). 못 읽으면 전일."""
    p = parse(_tidy_time(raw), mon)
    return p["s"], p["e"]


def read_week(text, mon):
    """{"rows": [{date, end, s, e, title, place, target, who}], "skipped": 못 읽은 줄 수}. 문제가 있으면 PasteError(붙여넣은 값은 담지 않는다)."""
    if len(text) > MAX_TEXT:
        raise PasteError(TOO_BIG)
    lines = tsv_lines(text)
    if not lines:
        raise PasteError(EMPTY)
    if all(len(r) == 1 for r in lines):
        raise PasteError("칸 구분이 없어요. 한글에서 표를 다시 드래그해 복사하거나, 엑셀에 붙였다가 복사해 붙여넣어 주세요.")

    cols, start = _find_head(lines)
    data = lines[start:]
    if len(data) > MAX_ROWS:
        raise PasteError("줄이 너무 많아요. 500줄 안으로 나눠 붙여넣어 주세요.")

    rows, skipped, last = [], 0, None
    for r in data:
        if _complete(_columns(r)) or any(TOTAL.match(re.sub(r"\s+", "", v)) for v in r) or all(FILLER.match(v) for v in r if v):
            continue  # 쪽이 넘어가며 다시 나온 머리글, 합계·'…' 줄
        if cols:
            cell = {k: r[c] if c < len(r) else "" for k, c in cols.items()}
        elif len(r) < 3:
            cell = {"date": r[0], "title": " ".join(r[1:])}
        else:
            cell = dict(zip(ORDER, r))
        raw_date, title = cell.get("date") or cell.get("dow", ""), cell.get("title", "")
        for k in ("time", "place", "who"):  # 제목 칸과 합친(colspan) 칸에 복사된 글자
            if cell.get(k) == title:
                cell[k] = ""
        got = _read_date(raw_date, mon) if raw_date else None
        if raw_date and not got:  # 아래의 빈 날짜 줄도 앞 날짜를 잇지 않는다(엉뚱한 날에 들어간다)
            skipped += 1
            last = None
            continue
        if got:
            last = got
        if not title:  # 날짜만 있는 줄은 그날 일정이 없다는 뜻
            skipped += bool(cell.get("time") or cell.get("place"))
            continue
        if last is None or len(title) > TITLE_MAX:  # 제목은 자르지 않는다
            skipped += 1
            continue
        s, e = _read_slot(cell.get("time", ""), mon)
        if not s and got:  # '일시' 한 칸에 날짜와 시각
            s, e = got["s"], got["e"]
        in_title = parse(title, mon)
        who = cell.get("who", "")
        target = " · ".join(x for x in (in_title["target"], f"담당 {who}" if who else "") if x)
        rows.append({"date": last["date"], "end": last["end"], "s": s, "e": e, "title": title,
                     "place": (cell.get("place") or in_title["place"] or "")[:PLACE_MAX], "target": target[:TARGET_MAX], "who": who})
    return {"rows": rows, "skipped": skipped}


def _scope(request):
    """대상은 기본 학교 전체(주간계획은 학교 공용)."""
    raw = request.POST.get("scope")
    return raw if _scope_ok(raw) else ALL


def _mark_new(rows, space, me, scope):
    """같은 날짜·제목·대상이 내게 보이는 일정에 있거나 표 안에서 겹치면 dup. 숨은 일정은 비교하지 않는다('이미 있음'으로 드러나면 안 된다)."""
    qs = Event.objects.in_space(space).filter(reachable_q(me), scope=scope, date__in={r["date"] for r in rows}, title__in={r["title"] for r in rows})
    seen = set(qs.values_list("date", "title"))
    for r in rows:
        r["dup"] = (r["date"], r["title"]) in seen
        seen.add((r["date"], r["title"]))


def _weekend_only(r):
    """토·일에만 걸치는 일정: 주간판·주간 인쇄(월~금)에 안 나온다."""
    return r["date"].weekday() >= 5 and ((r["end"] or r["date"]) - r["date"]).days <= 6 - r["date"].weekday()


def _ctx(request, **extra):
    mon = shown_monday(request.POST.get("w") or request.GET.get("w"), timezone.localdate())
    chosen = _scope(request)
    choices = sorted(scope_choices(request.member), key=lambda c: c[0] != ALL)
    return _space_ctx(request, page="schedule", w=mon.isoformat(), mon=mon, chosen=chosen, choices=choices, **extra)


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
def page(request):
    """붙여넣기 화면과 넣기. 값은 넣을 때 서버가 다시 읽는다."""
    space, me = request.space, request.member
    if request.method == "GET":
        return render(request, "today/paste.html", _ctx(request, paste="", error=""))
    ctx, text = _ctx(request), request.POST.get("paste", "")
    try:
        rows = read_week(text, ctx["mon"])["rows"]
        if not rows:
            raise PasteError(NO_ROWS)
    except PasteError as e:
        return render(request, "today/paste.html", {**ctx, "paste": text[:MAX_TEXT], "error": str(e)}, status=400)
    with transaction.atomic():
        Space.objects.select_for_update().filter(pk=space.pk).first()  # 둘이 같은 표를 동시에 넣어도 한 벌만
        _mark_new(rows, space, me, ctx["chosen"])
        new = [r for r in rows if not r["dup"]]
        Event.objects.bulk_create(Event(space=space, by=me, date=r["date"], end=r["end"], slot=r["s"] or "", slot_end=r["e"] or "",
                                        title=r["title"], place=r["place"], target=r["target"], scope=ctx["chosen"]) for r in new)
    first = (new or rows)[0]["date"]
    w = first - timedelta(days=first.weekday())
    weekend = sum(map(_weekend_only, new))
    r = redirect(f"/s/{space.token}/schedule?w={w:%Y-%m-%d}&added={len(new)}&same={len(rows) - len(new)}&weekend={weekend}")
    r.status_code = 303
    return r


@never_cache
@require_POST
@school_required
def preview(request):
    """미리보기(htmx). 저장하지 않는다. html이 오면(한글·엑셀 클립보드) TSV로 바꿔 입력 칸에 넣도록 함께 돌려준다."""
    html, ctx = request.POST.get("html", ""), {}
    mon = shown_monday(request.POST.get("w"), timezone.localdate())
    try:
        tsv = html_to_tsv(html, too_big=TOO_BIG) if html else ""
        if html and not tsv:
            raise PasteError("붙여넣은 내용에서 표를 찾지 못했어요. 표를 드래그해 다시 복사해 주세요.")
        d = read_week(tsv or request.POST.get("paste", ""), mon)
        if not d["rows"]:
            raise PasteError(NO_ROWS)
    except PasteError as e:
        ctx["error"] = str(e)
    else:
        rows = d["rows"]
        _mark_new(rows, request.space, request.member, _scope(request))
        for r in rows:
            r["dates"], r["slot"] = _dates(r["date"], r["end"]), slot_text(r["s"], r["e"]) if r["s"] else ""
        n_new = sum(not r["dup"] for r in rows)
        ctx.update(tsv=tsv, rows=rows, skipped=d["skipped"], n_new=n_new, n_dup=len(rows) - n_new,
                   n_allday=sum(not r["s"] and not r["dup"] for r in rows), me=request.member)
    return render(request, "today/_paste_out.html", ctx)
