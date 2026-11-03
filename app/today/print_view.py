"""H6 일정 인쇄(주간·월중행사표). 동작은 prototype/assets/app.js cal(), A4 가로만(A3는 R3). 학교 공간만.

인쇄에는 이 사람이 일정 화면에서 보는 것만 나간다(reachable_q). 제목은 자르지 않고, 색 없이 기호로 유형을 나눈다.
주간은 넘치면 블록 줄 단위로 쪽을 넘어가고 머리글 줄이 다시 나온다(한 줄이 한 쪽보다 길면 일정 사이에서 나뉜다). 월중은 격자가 한 쪽 높이로 고정이고, 칸에 안 들어가는 일정은 '+N건 별지'로 다음 쪽 목록에 적는다.
유형은 저장하지 않고 제목에서 읽는다(parse.event_type, prototype과 같은 규칙).
"""
from calendar import monthrange
from datetime import timedelta
from math import ceil

from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from spaces import neis
from spaces.gates import school_required

from .models import ALL, Event, reachable_q
from .parse import SLOTS, event_type, slot_text
from .schedule import _d, _label, this_monday

SYM = {"event": "◆", "work": "■", "meet": "▲", "duty": "○"}
ROWS = [{"k": "", "label": "전일", "time": "시간 미정"},
        *[{"k": s["k"], "label": s["label"], "time": f"{s['from']}~" + ("" if s["k"] == "pm" else s["to"])} for s in SLOTS]]
MONTH_DOW = "일월화수목금토"
DENSE_LINES = 30  # 블록 줄마다 가장 긴 칸의 줄 수를 더해 이보다 많으면(한 쪽이 찰 만큼) 글자를 줄인다
LONG_LINES, WEEK_CHARS = 14, 15  # 한 칸이 이 줄 수를 넘으면(쪽의 절반쯤) 블록 줄을 쪽 사이에서 나눈다(통째로 옮기면 앞 쪽이 빈다), 주간 칸 한 줄 글자 수
# 월중 격자의 치수. print.html의 .mo와 같은 값이어야 한다. 넘치면 쪽이 늘어나므로 넉넉히 적게 잡는다.
GRID_MM, DOW_MM, DATE_MM, LINE_MM = 160, 6, 5, 3.8  # 격자 높이, 요일 줄, 날짜 줄, 8.5pt 한 줄
CELL_CHARS = 12  # 칸 폭 37mm에 8.5pt 한글이 12자 남짓, 띄어쓰기가 섞이면 더 들어간다


def _shown(raw, today):
    try:
        d = parse_date(raw or "")
    except ValueError:
        d = None
    return d if d and 2000 <= d.year <= 2100 else today


def _events(request, first, last):
    return list(Event.objects.in_space(request.space).filter(reachable_q(request.member), date__lte=last)
                .filter(Q(end__gte=first) | Q(end__isnull=True, date__gte=first)).order_by("date", "slot", "created_at"))


def _neis(space, first, last):
    """(날짜·이름 목록, 실패 여부). 학사일정은 행사(◆)로."""
    rows = neis.schedule(space.school.neis_atpt, space.school.neis_code, f"{first:%Y%m%d}", f"{last:%Y%m%d}")
    seen = {}
    for r in rows:
        d = parse_date(f"{r['ymd'][:4]}-{r['ymd'][4:6]}-{r['ymd'][6:8]}") if len(r["ymd"]) == 8 else None
        if d and first <= d <= last:
            seen[(d, r["name"])] = {"sym": SYM["event"], "text": r["name"], "meta": "", "cont": False}
    return seen, neis.failed(rows)


def _item(e, day):
    meta = [slot_text(e.slot, e.slot_end) if e.slot_end and e.slot_end != e.slot else "", e.place, e.target]
    return {"sym": SYM[event_type(e.title)], "text": ("" if e.scope == ALL else f"[{e.scope_label}] ") + e.title,
            "meta": " · ".join(x for x in meta if x), "cont": day != e.date, "pk": e.pk,
            "dates": _d(e.date) + (f"~{_d(e.end)}" if e.end and e.end != e.date else "")}


def _lines(items):
    return sum(ceil((len(x["text"]) + len(x["meta"]) + 2) / WEEK_CHARS) for x in items)


def _week(request, d):
    mon = d - timedelta(days=d.weekday())
    days = [mon + timedelta(days=i) for i in range(5)]
    cells = {(day, r["k"]): [] for day in days for r in ROWS}
    extra, failed = _neis(request.space, mon, days[-1])
    for (day, _name), x in extra.items():
        if day in days:
            cells[(day, "")].append(x)
    for e in _events(request, mon, days[-1]):
        for day in days:
            if e.date <= day <= e.last_day:
                cells[(day, e.slot)].append(_item(e, day))
    return {"view": "week", "heading": f"주간교육계획 · {_label(mon)}", "failed": failed,
            "dense": sum(max(_lines(cells[(day, r["k"])]) for day in days) for r in ROWS) > DENSE_LINES,
            "prev": (mon - timedelta(days=7)).isoformat(), "next": (mon + timedelta(days=7)).isoformat(),
            "other_d": (mon + timedelta(days=3)).isoformat(),  # 월중으로 바꾸면 그 주가 더 많이 걸친 달
            "days": [_d(x) for x in days],
            "wrows": [{**r, "cells": [cells[(day, r["k"])] for day in days],
                       "long": max(_lines(cells[(day, r["k"])]) for day in days) > LONG_LINES} for r in ROWS]}


def month_cells(items, budget):
    """(칸에 적을 것, 별지로 보낼 것). budget은 칸에 들어가는 줄 수. 넘치면 '+N건 별지' 한 줄을 남긴다. 제목은 자르지 않는다."""
    need = [ceil((len(x["text"]) + 2) / CELL_CHARS) for x in items]
    if sum(need) <= budget:
        return items, []
    shown, used = [], 1
    for x, n in zip(items, need):
        if used + n > budget:
            break
        shown.append(x)
        used += n
    return shown, items[len(shown):]


def _month(request, d):
    first = d.replace(day=1)
    n_days = monthrange(first.year, first.month)[1]
    lead = (first.weekday() + 1) % 7  # 일요일 시작
    n_weeks = ceil((lead + n_days) / 7)
    start = first - timedelta(days=lead)
    last = start + timedelta(days=n_weeks * 7 - 1)
    budget = int(((GRID_MM - DOW_MM) / n_weeks - DATE_MM) / LINE_MM)

    extra, failed = _neis(request.space, start, last)
    events = _events(request, start, last)
    cells, spill = [], {}
    for i in range(n_weeks * 7):
        day = start + timedelta(days=i)
        weekend = i % 7 in (0, 6)
        items = [x for (dd, _name), x in extra.items() if dd == day]
        # 기간 일정은 날마다 다시 적되 이어지는 주말은 뺀다(prototype)
        items += [_item(e, day) for e in events if e.date <= day <= e.last_day and (e.date == day or not weekend)]
        shown, over = month_cells(items, budget)
        for x in over:
            key = x.get("pk") or (day, x["text"])
            spill.setdefault(key, {**x, "dates": x.get("dates") or _d(day)})
        cells.append({"n": day.day, "out": day.month != first.month, "items": shown, "over": len(over)})
    today = timezone.localdate()
    return {"view": "month", "heading": f"{first.year}년 {first.month}월 월중행사", "failed": failed, "cells": cells, "n_weeks": n_weeks,
            "dow": MONTH_DOW, "spill": list(spill.values()),
            "prev": (first - timedelta(days=1)).replace(day=1).isoformat(), "next": (first + timedelta(days=n_days)).isoformat(),
            "other_d": (today if (today.year, today.month) == (first.year, first.month) else first).isoformat()}


@never_cache
@require_GET
@school_required
def sheet(request):
    today = timezone.localdate()
    view = request.GET.get("view")
    d = _shown(request.GET.get("d"), this_monday(today) if view != "month" else today)
    ctx = _month(request, d) if view == "month" else _week(request, d)
    return render(request, "today/print.html", {"space": request.space, "me": request.member, "stamp": f"{today:%Y-%m-%d}", **ctx})
