"""J1 나이스 복무현황 붙여넣기(prototype dutyPaste). 쓰는 사람은 관리자와 지정 입력자(Member.can_duty), 학교 공간만.

사유는 건강정보일 수 있는 민감정보다(26번 3-1 ③). 그래서
- 이름·유형·시간·날짜 칸만 골라 읽고 나머지 칸은 쳐다보지 않는다. 시간 칸에서도 시각 낱말만 뽑는다(원문을 잘라 쓰지 않는다).
- 원문은 이 요청의 메모리에만 있다. 응답·입력 칸·세션·캐시·로그·오류 문구에 싣지 않는다(업무분장과 달리 TSV를 돌려주지 않는다).
- 바꾸기는 미리보기가 만든 값(이름·유형·시간·날짜)을 서명해 다시 받는다. 서버에 임시 저장하지 않는다.
- 머리글이 있어도 사유·비고 같은 낱말이 든 칸은 어떤 칸으로도 읽지 않는다. 신청일·결재일도 복무 날짜가 아니고, 시간 칸의 값은 날짜로 읽지 않는다.
- 이름 칸 값이 명부 회원·업무분장 담당자 이름과 정확히(공백 무시, 호칭 꼬리만 떼고) 맞는 줄만 읽는다(리드 결정). 맞지 않는 줄은 글자를 보이지 않고 줄 수만 알린다.
  이름 칸에 사유가 밀려 들어와도 저장·표시될 길이 없다. 승인해서 넣는 길도 없다.
- 머리글 없는 표는 칸 단위로 판단한다: 유형 낱말이 가장 많은 칸, 명부 이름이 가장 많이 맞는 칸. 동수면 거부한다. 칸 순서가 다른 머리글이 다시 나와도 거부한다.
- 가족·건강이 드러나는 유형(육아시간 등)은 '부재'로만, 날짜는 오늘 ±31일만(7일 삭제 우회 방지).

한 번 붙여넣으면 표에 나온 날짜들을 통째로 바꾼다. 날짜가 없으면 오늘, 여러 날 기간은 표의 그날 하루로 줄인다.
"""
import re
from collections import Counter
from datetime import date
from functools import wraps

from django.core import signing
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import is_guest
from collect.paste import FILLER, MAX_GRID_COLS, MAX_ROWS, MAX_TEXT, TOTAL, PasteError, html_to_tsv, tsv_lines

from .gates import manager_required, school_required, school_write_required
from .models import Attendance, Duty, Member, MemberLog, Space, get_in_space_or_404
from .school import TOAST, _person, _school_ctx, school_body, split_people
from .views import _space_ctx

HEAD_LINES = 5  # 머리글을 찾는 범위(표 위 제목 줄)
SIGNED_AGE = 3600  # 미리보기 뒤 바꾸기까지
PAREN = re.compile(r"[(（][^)）]*[)）]?")
NAME_HEAD = re.compile(r"성명|이름|교직원|교직원명|직원명|교사명|교원명|복무자|신청자")
KIND_HEAD = re.compile(r"복무구분|복무유형|복무종류|복무|근무상황|근무상황구분|근태|근태구분|휴가구분|휴가종류|휴가유형")
KIND_HEAD_LOOSE = re.compile(r"구분|유형|종류")  # 부서 '구분'과 함께 있으면 위 이름이 먼저
WHEN_HEAD = re.compile(r"기간|일시|일자|날짜|시작|종료|부터|까지|복무일|근무일")
NOT_READ = re.compile(r"사유|비고|내용|목적|행선지|용무|메모|출장지|장소|신청|결재|승인|등록|작성|처리|상신|일수")
KINDS = ((re.compile(r"(육아|모성|임신|출산|여성보건|가족돌봄|자녀돌봄|돌봄)[가-힣]{0,8}|유연근무|탄력근무|[가-힣]{0,8}휴직"), "부재"),
         (re.compile(r"(관내|관외|국내|국외)?출장"), "출장"), (re.compile(r"병?외출"), "외출"), (re.compile(r"병?조퇴"), "조퇴"),
         (re.compile(r"병?지각"), "지각"), (re.compile(r"연가"), "연가"), (re.compile(r"병가|공가|[가-힣]{0,8}휴가"), "휴가"))
PART_DAY = {"조퇴", "외출", "지각", "부재"}  # 시간을 못 읽으면 '종일'이라 쓰면 틀린다
TIME_HEAD = re.compile(r"(시간|시각)$")  # 이 칸의 값은 날짜로 읽지 않는다('1.5')
TAIL = re.compile(r"\s*(교사|선생님|선생|쌤)$")
HANGUL = re.compile(r"[가-힣]+")
DAYS_MAX = 31  # 오늘에서 이만큼 벗어난 날짜는 읽지 않는다
WHEN = re.compile(
    r"(?P<y>20\d\d)\s*[-./년]\s*(?P<m>\d{1,2})\s*[-./월]\s*(?P<d>\d{1,2})\s*일?\.?"
    r"|(?<![\d:./-])(?P<y2>\d\d)[-./](?P<m4>\d{1,2})[-./](?P<d4>\d{1,2})(?![\d:])\.?"
    r"|(?<![\d:.])(?P<m2>\d{1,2})\s*월\s*(?P<d2>\d{1,2})\s*일"
    r"|(?<![\d:.-])(?P<m5>\d\d)-(?P<d5>\d\d)(?![\d:])"  # 두 자리씩만: '4-1'은 학년 반이다
    r"|(?<![\d:.])(?P<m3>\d{1,2})\s*[/.]\s*(?P<d3>\d{1,2})(?![\d일시:])\.?"
    r"|(?:(?P<ap>오전|오후)\s*)?(?<!\d)(?P<h>\d{1,2})(?::(?P<mi>\d{2})|시(?!간)(?:\s*(?P<mi2>\d{1,2})분)?)"
    r"|(?P<word>온종일|종일|전일|하루|오전|오후)")
WHEN_REST = re.compile(r"[\s~∼\-–—().,월화수목금토일요]*")  # 날짜·시각을 빼고 남아도 되는 글자(머리글 없는 표의 시간 칸 판단)
EMPTY = "표를 붙여넣어 주세요. 나이스 일일 복무현황 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요."
FLAT = "칸 구분이 없어요. 표를 다시 드래그해 복사하거나, 엑셀에 붙였다가 복사해 붙여넣어 주세요."
NO_COLUMNS = "이름과 복무 유형 칸을 찾지 못했어요. 표 첫 줄에 성명·복무구분이 있는지 확인해 주세요."
NO_ROSTER = "명부나 업무분장에 있는 이름을 찾지 못했어요. 표 첫 줄에 성명·복무구분 머리글을 함께 복사해 주세요."
AMBIGUOUS = "이름이나 유형 칸을 하나로 정하지 못했어요. 표 첫 줄의 머리글을 함께 복사해 주세요."
HEAD_MOVED = "칸 순서가 다른 머리글이 표 중간에 또 나와요. 머리글이 한 번만 들어가게 나눠 복사해 주세요."
ONLY_UNKNOWN = "명부에 없는 이름 {n}줄뿐이라 읽은 줄이 없어요. 아직 안 들어온 선생님은 관리자가 업무분장을 먼저 붙여 넣으면 이름을 읽어요."
NO_ROWS = "이름과 복무 유형(출장·외출·조퇴·연가·병가…)이 있는 줄을 찾지 못했어요."
TOO_BIG = "표가 너무 커요. 500줄 안으로 나눠 붙여넣어 주세요."
NO_TABLE = "붙여넣은 내용에서 표를 찾지 못했어요. 표를 드래그해 다시 복사해 주세요."
STALE = "미리보기가 오래됐어요. 표를 다시 붙여넣어 주세요. 바뀐 것은 없어요."


def _bare(cell):
    return re.sub(r"\s+", "", cell)


def _at(row, c):
    return row[c] if c is not None and c < len(row) else ""


def _kind(cell):
    bare = _bare(PAREN.sub("", cell))
    return next((k for pat, k in KINDS if pat.fullmatch(bare)), None)


def _match(cell, known):
    """이름 칸 값이 명부·업무분장 이름과 맞으면 저장할 이름, 아니면 ''. 공백은 무시하고 호칭 꼬리만 뗀다. 다른 글자가 붙어 있으면 이름이 아니다."""
    s = " ".join(cell.split())
    for v in (s, TAIL.sub("", s)):
        if _bare(v) in known:
            return _bare(v) if HANGUL.fullmatch(_bare(v)) else v
    return ""


def _columns(line):
    """머리글 줄에서 {name, kind, when: [열들], time: [열들]}. 사유·비고 칸은 어떤 칸으로도 고르지 않는다."""
    cols, loose = {"when": [], "time": []}, None
    for c, cell in enumerate(line):
        bare = _bare(cell)
        if NAME_HEAD.fullmatch(bare):
            cols.setdefault("name", c)
        elif NOT_READ.search(bare):
            continue
        elif KIND_HEAD.fullmatch(bare):
            cols.setdefault("kind", c)
        elif KIND_HEAD_LOOSE.fullmatch(bare):
            loose = c if loose is None else loose
        elif TIME_HEAD.search(bare):
            cols["time"].append(c)
        elif WHEN_HEAD.search(bare):
            cols["when"].append(c)
    if "kind" not in cols and loose is not None:
        cols["kind"] = loose
    return cols


def _complete(cols):
    return "name" in cols and "kind" in cols


def _when_column(data, c):
    cells = [_at(r, c) for r in data if _at(r, c)]
    whole = sum(bool(WHEN.search(v)) and bool(WHEN_REST.fullmatch(WHEN.sub("", v))) for v in cells)
    return whole > 0 and whole * 2 >= len(cells)


def _guess_columns(data, known):
    """머리글 없는 표: 칸 단위로 고른다. 어느 줄의 사유 칸에 '연가'가 있어도 유형 칸은 흔들리지 않는다. 후보가 동수면 거부한다(어느 쪽이 사유인지 모른다)."""
    width = max(len(r) for r in data)
    if width > MAX_GRID_COLS:
        raise PasteError(TOO_BIG)
    counts = [sum(bool(_kind(_at(r, c))) for r in data) for c in range(width)]
    if not any(counts):
        raise PasteError(NO_COLUMNS)
    kind = counts.index(max(counts))
    when = [c for c in range(width) if c != kind and _when_column(data, c)]
    hits = [0 if c == kind or c in when else sum(bool(_match(_at(r, c), known)) for r in data) for c in range(width)]
    if not any(hits):
        raise PasteError(NO_ROSTER)
    if counts.count(max(counts)) > 1 or hits.count(max(hits)) > 1:
        raise PasteError(AMBIGUOUS)
    return {"name": hits.index(max(hits)), "kind": kind, "when": when, "time": []}


def _nearest_date(m, d, today):
    """해가 없는 날짜: 오늘에서 가장 가까운 해. 없는 날짜면 None."""
    try:
        return min((date(y, m, d) for y in (today.year - 1, today.year, today.year + 1) if m != 2 or d != 29 or y % 4 == 0),
                   key=lambda x: abs((x - today).days))
    except ValueError:
        return None


def _read_when(text, today, dates_too=True):
    """(날짜들, 시각들, 낱말). 없는 날짜가 있으면 날짜들은 None(그 줄은 못 읽은 줄). dates_too=False: 시간 칸이라 날짜는 보지 않는다."""
    dates, times, word, half = [], [], "", ""
    for m in WHEN.finditer(text):
        g = m.groupdict()
        month, day = g["m"] or g["m2"] or g["m3"] or g["m4"] or g["m5"], g["d"] or g["d2"] or g["d3"] or g["d4"] or g["d5"]
        if month:
            if not dates_too:
                continue
            year = g["y"] or (f"20{g['y2']}" if g["y2"] else None)
            try:
                found = date(int(year), int(month), int(day)) if year else _nearest_date(int(month), int(day), today)
            except ValueError:
                found = None
            if found is None:
                return None, [], ""
            dates.append(found)
        elif g["h"]:
            half = g["ap"] or half  # '오후 2시~4시'의 뒤 시각은 앞의 오전·오후를 잇는다
            hour, minute = int(g["h"]), int(g["mi"] or g["mi2"] or 0)
            hour += 12 if half == "오후" and hour < 12 else 0
            hour += 12 if half == "오전" and not g["ap"] and times and f"{hour:02d}" < times[-1] else 0  # '오전 11시~1시'
            if hour <= 24 and minute < 60:
                times.append(f"{hour:02d}:{minute:02d}")
        else:
            word = g["word"] if g["word"] in ("오전", "오후") else "종일"
    return dates, times, word


def _time_text(kind, day, dates, times, word):
    if dates and dates[0] != dates[-1]:  # 여러 날: 그날 하루 몫만
        if times and day == dates[0]:
            return f"{times[0]}~"
        return f"~{times[-1]}" if len(times) > 1 and day == dates[-1] else "종일"
    if times:
        return f"{times[0]}~{times[-1]}" if len(times) > 1 and times[0] != times[-1] else f"{times[0]}~"
    return word or ("" if kind in PART_DAY else "종일")


def _is_head(cols):
    return "name" in cols or "kind" in cols


def read_attendance(text, today, known):
    """{"rows": [{name, kind, time, date(ISO)}], "skipped": 못 읽은 줄 전부, "unknown": 그중 명부에 없는 이름, "totals": 건너뛴 합계 줄}.
    문제가 있으면 PasteError(붙여넣은 값은 담지 않는다). known: 명부·업무분장 이름들(붙인 글자)."""
    if len(text) > MAX_TEXT:
        raise PasteError(TOO_BIG)
    lines = tsv_lines(text)
    if not lines:
        raise PasteError(EMPTY)
    if all(len(r) == 1 for r in lines):
        raise PasteError(FLAT)

    head = next((i for i, r in enumerate(lines[:HEAD_LINES]) if _complete(_columns(r))), None)
    if head is not None:
        cols, titles, data = _columns(lines[head]), lines[:head], lines[head + 1:]
    else:
        if any(_is_head(_columns(r)) for r in lines[:HEAD_LINES]):  # 머리글은 있는데 이름이나 유형이 없다
            raise PasteError(NO_COLUMNS)
        n = 0
        while n < len(lines) and sum(bool(c) for c in lines[n]) == 1:
            n += 1
        titles, data = lines[:n], lines[n:]
        if not data:
            raise PasteError(NO_ROWS)
    if len(data) > MAX_ROWS:
        raise PasteError(TOO_BIG)
    if head is None:
        cols = _guess_columns(data, known)

    read, skipped, unknown, totals, last = [], 0, 0, 0, ""
    for r in data:
        here = _columns(r)
        if _complete(here):  # 쪽이 넘어가며 다시 나온 머리글
            if here != cols:
                raise PasteError(HEAD_MOVED)
            continue
        if all(FILLER.match(v) for v in r if v):
            continue
        raw, kind_cell = _at(r, cols["name"]), _at(r, cols["kind"])
        if TOTAL.match(_bare(raw)) or (not kind_cell and any(TOTAL.match(_bare(v)) for v in r)):  # 사유 칸의 '계'는 합계 줄이 아니다
            totals += 1
            continue
        name = _match(raw, known) if raw else last  # 엑셀의 병합 칸은 아래 줄 이름이 빈다
        last = name
        if raw and not name:
            unknown += 1
            continue
        kind = _kind(kind_cell)
        dates, times, word = _read_when(" ".join(_at(r, c) for c in cols["when"]), today)
        if not name or not kind or dates is None or (dates and dates[0] > dates[-1]):
            skipped += 1
            continue
        _, more, word2 = _read_when(" ".join(_at(r, c) for c in cols["time"]), today, dates_too=False)
        read.append((name, kind, dates, times + more, word or word2))

    in_title = _read_when(" ".join(c for r in titles for c in r), today)[0] or []
    singles = Counter(x[2][0] for x in read if x[2] and x[2][0] == x[2][-1])
    base = in_title[0] if len(set(in_title)) == 1 else singles.most_common(1)[0][0] if singles else today  # 검색 조건의 기간은 그날이 아니다
    rows = {}
    for name, kind, dates, times, word in read:
        day = base if not dates or dates[0] <= base <= dates[-1] else dates[0]
        if abs((day - today).days) > DAYS_MAX:
            skipped += 1
            continue
        row = {"name": name[:40], "kind": kind, "time": _time_text(kind, day, dates, times, word), "date": day.isoformat()}
        rows[tuple(row.values())] = row
    if not rows:
        raise PasteError(ONLY_UNKNOWN.format(n=unknown) if unknown else NO_ROWS)
    return {"rows": list(rows.values()), "skipped": skipped + unknown, "unknown": unknown, "totals": totals}


LINE_ERROR = f"명부에 있는 이름과 유형({'·'.join(Attendance.KINDS)})으로 써 주세요."


def read_line(text, known):
    """일정 화면 복무 칸의 한 줄 '이름 유형 [시간]' → {name, kind, time}, 못 읽으면 None(부르는 쪽은 원문을 돌려 보이지 않는다).
    표 붙여넣기와 같은 원칙: 이름은 명부 이름과 정확히(공백 무시, 호칭 꼬리만 떼고), 유형은 KINDS로만(가족·건강 유형은 부재),
    시간은 유형 뒤 글자에서 시각 낱말만 뽑는다. 나머지 글자(사유 등)는 어디에도 남기지 않는다. known: 명부 이름들(붙인 글자)."""
    words = text.split()
    at = next((i for i, w in enumerate(words) if w in Attendance.KINDS or _kind(w)), None)
    if not at:  # 유형이 없거나 이름 없이 유형부터
        return None
    name = _match(" ".join(words[:at]), known)
    if not name:
        return None
    kind = words[at] if words[at] in Attendance.KINDS else _kind(words[at])
    _, times, word = _read_when(" ".join(words[at + 1:]), timezone.localdate(), dates_too=False)
    return {"name": name[:40], "kind": kind, "time": _time_text(kind, None, [], times, word)}


def _roster(space):
    """{붙인 이름: [회원들]}. 이름 맞추기와 소속 표시에만 쓴다(권한 아님)."""
    found = {}
    for m in Member.objects.in_space(space):
        found.setdefault(_person(m.name), []).append(m)
    return found


def _known(space, roster):
    """읽어도 되는 이름: 명부 회원 + 업무분장 담당자(아직 안 들어온 선생님)."""
    known = set(roster)
    for who in Duty.objects.in_space(space).values_list("who", flat=True):
        known.update(split_people(who, known))
    return known


def _one(roster, name):
    """이름이 명부에서 하나로 맞으면 그 회원. 동명이인·명부에 없으면 None."""
    seats = roster.get(_bare(name), [])
    return seats[0] if len(seats) == 1 else None


def _salt(request):
    return f"attendance:{request.space.pk}:{request.member.pk}"


def _writer_required(view):
    """관리자 또는 지정 입력자. 직위 글자는 보지 않는다."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not (request.member.is_manager or request.member.can_duty):
            return HttpResponseForbidden()
        return view(request, *args, **kwargs)
    return inner


def _page(request, error="", status=200):
    ctx = _space_ctx(request, page="school", writable=request.space.school.writable, error=error, days=_days(request.space))
    return render(request, "spaces/attendance.html", ctx, status=status)


def _days(space):
    """넣어 둔 복무의 날짜별 줄 수(지우기 목록)."""
    return Attendance.objects.in_space(space).values("date").annotate(n=Count("pk")).order_by("-date")


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@_writer_required
@school_write_required
def page(request):
    """붙여넣기 화면과 바꾸기. 바꾸기가 받는 것은 미리보기가 서명한 값뿐이다."""
    if request.method == "GET":
        return _page(request)
    try:
        signed = signing.loads(request.POST.get("rows", ""), salt=_salt(request), max_age=SIGNED_AGE)
    except signing.BadSignature:
        return _page(request, STALE, 400)
    space, roster, rows = request.space, _roster(request.space), signed["rows"]
    with transaction.atomic():
        Space.objects.select_for_update(no_key=True).filter(pk=space.pk).first()  # 둘이 동시에 바꿔도 한 표만 남게
        Attendance.objects.in_space(space).filter(date__in={r["date"] for r in rows}).delete()
        Attendance.objects.bulk_create(Attendance(
            space=space, date=r["date"], name=r["name"], kind=r["kind"], time_text=r["time"], by=request.member,
            member=_one(roster, r["name"])) for r in rows)
    people = len({r["name"] for r in rows})
    day = "오늘 복무" if {r["date"] for r in rows} == {timezone.localdate().isoformat()} else "복무"
    request.session[TOAST] = (f"{day} {people}명을 바꿨어요." + (f" 읽지 못한 줄 {signed['skipped']}개는 뺐어요." if signed["skipped"] else "")
                              + " 사유는 저장하지 않았어요.")
    return redirect(f"/s/{space.token}/school")


@never_cache
@require_POST
@school_required
@_writer_required
@school_write_required
def preview(request):
    """미리보기(htmx). 저장하지 않고, 원문을 돌려주지 않는다. html이 오면(한글·엑셀 클립보드) 그 안의 표를 읽는다."""
    html, today, space = request.POST.get("html", ""), timezone.localdate(), request.space
    roster = _roster(space)
    try:
        tsv = html_to_tsv(html, too_big=TOO_BIG) if html else ""
        if html and not tsv:
            raise PasteError(NO_TABLE)
        d = read_attendance(tsv or request.POST.get("paste", ""), today, _known(space, roster))
    except PasteError as e:
        return render(request, "spaces/_attendance_out.html", {"error": str(e)})
    rows = []
    for r in d["rows"]:
        seat = _one(roster, r["name"])
        rows.append({**r, "day": date.fromisoformat(r["date"]), "group": seat.get_group_display() if seat else "",
                     "note": "동명이인" if _bare(r["name"]) in roster and not seat else ""})
    days = {r["date"] for r in d["rows"]}
    return render(request, "spaces/_attendance_out.html", {
        "rows": rows, "skipped": d["skipped"] - d["unknown"], "unknown": d["unknown"], "totals": d["totals"], "only_today": days == {today.isoformat()},
        "signed": signing.dumps({"rows": d["rows"], "skipped": d["skipped"]}, salt=_salt(request), compress=True), "n_people": len({r["name"] for r in d["rows"]}),
        "n_now": Attendance.objects.in_space(space).filter(date__in=days).count()})


@never_cache
@require_POST
@school_required
@_writer_required
@school_write_required
def clear(request):
    """그날 복무 지우기(잘못 넣은 날짜 되돌리기). 확인은 화면의 hx-confirm."""
    try:
        day = parse_date(request.POST.get("date", ""))
    except ValueError:
        day = None
    if day is None:
        return HttpResponseBadRequest()
    n, _ = Attendance.objects.in_space(request.space).filter(date=day).delete()
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/school/attendance")
    return render(request, "spaces/_attendance_days.html", {"space": request.space, "days": _days(request.space),
                                                            "toast": f"{day.month}월 {day.day}일 복무 {n}줄을 지웠어요."})


@never_cache
@require_POST
@school_required
@manager_required
@school_write_required
def set_writer(request, key):
    """복무 입력자 지정·해제(관리자만). 계정 회원만 지정한다(손님 자리는 반·이름으로 찾기 쉬워서, 관리자와 같은 이유)."""
    m = get_in_space_or_404(Member, request, key=key)
    on, htmx = request.POST.get("on") == "1", request.headers.get("HX-Request")
    if on and (m.user_id is None or is_guest(m.user)):
        error = "이름만 들어온 선생님은 복무 입력자가 될 수 없어요. 구글이나 아이디로 이으면 돼요."
        if htmx:
            return school_body(request, status=400, error=error)
        return render(request, "spaces/school.html", _school_ctx(request, error=error), status=400)
    if on != m.can_duty:
        Member.objects.filter(pk=m.pk).update(can_duty=on)
        MemberLog.objects.create(space=request.space, by=request.member.name, name=m.name,
                                 before="복무 입력자 아님" if on else "복무 입력자", after="복무 입력자" if on else "복무 입력자 아님")
    if not htmx:
        return redirect(f"/s/{request.space.token}/school")
    return school_body(request, toast=f"{m.name} 선생님을 복무 입력자로 지정했어요." if on else f"{m.name} 선생님을 복무 입력자에서 뺐어요.")
