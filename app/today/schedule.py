"""일정(일간·주간·월간, Q3). 모습은 design/schedule.html, 동작은 prototype/assets/app.js cal().

- 일간·주간: 날 × 블록 4칸 + 시간 미정. 월간: 일~토 달력(prototype). 칸마다 동학년 일정(Event), NEIS 학사일정(시간 미정), 복무(학교), 취합 마감(마감 시각의 블록).
- 모드는 ?mode=, 마지막 모드는 쿠키(gt_sched_mode). 보고 있는 날은 ?w=(일간 그날, 주간 월요일, 월간 1일).
- 칸 안 추가·고치기·지우기는 G1 뷰(today.views)를 그대로 쓴다. view=week로 부르면 지금 모드·지금 날짜의 판(_week.html)을 돌려준다.
- 바뀐 것·마감 목록: 보고 있는 날·주·달의 것만.
- 학교 공간(H5): 일정은 reachable_q(관리자는 남의 '나'를 뺀 전부), 마감은 관리자도 내가 만든 것·내 줄·내 그룹 칸만.
- 인쇄는 print_view, 주간계획 표 붙여넣기는 week_paste(둘 다 학교 공간만, H6). 블록 끌기는 R3.
조회는 모두 in_space()를 거친다.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, time, timedelta
from math import ceil

from django.conf import settings
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from collect.lists import _colors, _mine_q
from collect.models import Collection, Row
from spaces import neis
from spaces.models import Attendance
from spaces.views import _space_ctx

from .models import Event, reachable_q, scope_choices
from .parse import SLOT_KEYS, SLOTS, block_of_time

WD = "월화수목금토일"
MONTH_DOW = "일월화수목금토"
MODES = ("day", "week", "month")
MODE_COOKIE = "gt_sched_mode"
MODE_AGE = 365 * 86400


def _d(d):
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


def this_monday(today):
    """이번 주 월요일. 주말이면 다음 주(다가올 주를 본다)."""
    mon = today - timedelta(days=today.weekday())
    return mon + timedelta(days=7) if today.weekday() >= 5 else mon


def _shown_date(raw):
    """주소·폼의 w(YYYY-MM-DD) → 날짜. 형식이 틀리거나 범위 밖이면 None."""
    try:
        d = parse_date(raw or "")
    except ValueError:
        d = None
    return d if d and 2000 <= d.year <= 2100 else None


def shown_monday(raw, today):
    """w → 그 주 월요일. 형식이 틀리거나 범위 밖이면 이번 주."""
    d = _shown_date(raw)
    return d - timedelta(days=d.weekday()) if d else this_monday(today)


def month_grid(first):
    """달력(일요일 시작): (첫 칸 날짜, 주 수). 인쇄 월중행사표와 같다."""
    lead = (first.weekday() + 1) % 7
    return first - timedelta(days=lead), ceil((lead + monthrange(first.year, first.month)[1]) / 7)


def read_mode(request):
    raw = request.POST.get("mode") or request.GET.get("mode") or request.COOKIES.get(MODE_COOKIE)
    return raw if raw in MODES else "week"


def _label(mon):
    fri = mon + timedelta(days=4)
    return f"{mon.month}월 {mon.day}일 ~ " + (f"{fri.day}일" if fri.month == mon.month else f"{fri.month}월 {fri.day}일")


def _heading(mon, this_mon):
    rel = (mon - this_mon).days // 7
    return {0: "이번 주 일정", -1: "지난주 일정", 1: "다음 주 일정"}.get(rel, f"{_label(mon)} 일정")


def _at(dt, today):
    at = timezone.localtime(dt)
    day = "오늘" if at.date() == today else "어제" if at.date() == today - timedelta(days=1) else _d(at.date())
    return f"{day} {at:%H:%M}"


def _day_view(raw, today):
    d = _shown_date(raw) or today
    label = f"{d.month}월 {d.day}일 {WD[d.weekday()]}요일"
    heading = {0: "오늘 일정", -1: "어제 일정", 1: "내일 일정"}.get((d - today).days, f"{label} 일정")
    return {"first": d, "last": d, "list_first": d, "list_last": d, "focus": d, "w": d, "heading": heading, "label": label,
            "is_this": d == today, "prev_w": d - timedelta(days=1), "this_w": today, "next_w": d + timedelta(days=1),
            "nav_words": ("전날", "오늘", "다음 날"), "period": "이 날"}


def _week_view(raw, today):
    mon, this_mon = shown_monday(raw, today), this_monday(today)
    fri = mon + timedelta(days=4)
    return {"first": mon, "last": fri, "list_first": mon, "list_last": fri, "focus": today if mon <= today <= fri else mon, "w": mon,
            "heading": _heading(mon, this_mon), "label": _label(mon), "is_this": mon == this_mon,
            "prev_w": mon - timedelta(days=7), "this_w": this_mon, "next_w": mon + timedelta(days=7),
            "nav_words": ("지난주", "이번 주", "다음 주"), "period": "이번 주", "period_short": "이 주"}


def _month_view(raw, today):
    d = _shown_date(raw) or today
    first, this_first = d.replace(day=1), today.replace(day=1)
    end = first.replace(day=monthrange(first.year, first.month)[1])
    start, n_weeks = month_grid(first)
    next_first, prev_first = end + timedelta(days=1), (first - timedelta(days=1)).replace(day=1)
    label = f"{first.year}년 {first.month}월"
    heading = ({this_first: "이번 달 일정", (this_first - timedelta(days=1)).replace(day=1): "지난달 일정",
                (this_first + timedelta(days=32)).replace(day=1): "다음 달 일정"}.get(first, f"{label} 일정"))
    weekday = first + timedelta(days=7 - first.weekday()) if first.weekday() >= 5 else first
    return {"first": start, "last": start + timedelta(days=n_weeks * 7 - 1), "list_first": first, "list_last": end,
            "focus": today if first == this_first else weekday, "w": first, "heading": heading, "label": label,
            "is_this": first == this_first, "prev_w": prev_first, "this_w": this_first, "next_w": next_first,
            "nav_words": ("지난달", "이번 달", "다음 달"), "period": "이 달"}


VIEWS = {"day": _day_view, "week": _week_view, "month": _month_view}


def board_ctx(request, mode, raw):
    """판 하나의 컨텍스트. 모드는 보는 기간(first~last, 빠진 날 없이 이어짐)과 칸 모양만 다르다."""
    from .views import _conf_text, _ev_item, week_conflicts  # views가 이 모듈을 부르므로 안에서 가져온다

    space, me = request.space, request.member
    now = timezone.now()
    today = timezone.localdate(now)
    v = VIEWS[mode](raw, today)
    first, last, list_first, list_last = v["first"], v["last"], v["list_first"], v["list_last"]
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    colors = _colors(space)

    # 일정: 보고 있는 기간에 걸치는 것
    evs = list(Event.objects.in_space(space).filter(reachable_q(me), date__lte=last)
               .filter(Q(end__gte=first) | Q(end__isnull=True, date__gte=first))
               .select_related("by", "changed_by").order_by("date", "slot", "created_at"))
    cells = defaultdict(list)  # (날짜, 블록) → 칸 안 항목
    dialogs = []
    clashes = week_conflicts(space, evs)
    for e in evs:
        x = _ev_item(e, me, today)
        x["kind"] = "ev"
        x["fresh"] = x["mine"] and not e.changed_from and now - e.created_at < timedelta(minutes=1)
        x["changes"] = [(w, a, b) for w, a, b in (e.changed_from or [])]
        conf = clashes.get(e.pk)
        x["conf"] = _conf_text({"place": e.place}, conf, me) if conf else ""
        dialogs.append(x)
        for d in days:  # 월간의 기간 일정은 이어지는 주말을 뺀다(prototype)
            if e.date <= d <= e.last_day and (mode != "month" or e.date == d or d.weekday() < 5):
                cells[(d, e.slot)].append(x)

    # NEIS 학사일정(시간 없음 → 시간 미정 줄). 불러오기 실패와 '없음'을 나눠 알린다.
    sched = neis.schedule(space.school.neis_atpt, space.school.neis_code, f"{first:%Y%m%d}", f"{last:%Y%m%d}")
    seen = set()
    for r in sched:
        d = parse_date(f"{r['ymd'][:4]}-{r['ymd'][4:6]}-{r['ymd'][6:8]}") if len(r["ymd"]) == 8 else None
        if d and first <= d <= last and (d, r["name"]) not in seen:
            seen.add((d, r["name"]))
            cells[(d, "")].append({"kind": "neis", "name": r["name"]})

    # 복무(J1, 학교 공간): 시간 미정 줄에 '이름 유형'. 모든 회원에게 같게.
    if space.is_school:
        for a in Attendance.objects.in_space(space).filter(date__range=(first, last)).order_by("pk"):
            cells[(a.date, "")].append({"kind": "duty", "title": a.title, "time": a.time_text})

    # 취합 마감(첫날 0시 ~ 끝날 다음 날 0시)
    tz = timezone.get_current_timezone()
    start = datetime.combine(first, time(0), tzinfo=tz)
    cs = (Collection.objects.in_space(space).filter(due__gte=start, due__lt=datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz))
          .select_related("created_by").order_by("due"))
    if space.is_school:
        cs = cs.filter(Q(created_by=me) | Q(pk__in=Row.objects.in_space(space).filter(_mine_q(me)).values("collection")))
    dues = []
    my_rows = {}
    for r in Row.objects.in_space(space).filter(collection__in=[c.pk for c in cs if c.created_by_id != me.pk]).filter(_mine_q(me)).order_by("-pos"):
        my_rows[r.collection_id] = r  # 내림차순이라 맨 앞 줄이 남는다
    for c in cs:
        at = timezone.localtime(c.due)
        mine = c.created_by_id == me.pk
        if mine:
            href = f"/s/{space.token}/c/{c.key}/"
        else:
            row = my_rows.get(c.pk)
            href = f"/c/{c.link_token}/u/{row.key}" if row else f"/c/{c.link_token}"
        by = c.created_by.name if c.created_by else "알 수 없음"
        x = {"kind": "due", "c": c, "href": href, "hm": f"{at:%H:%M}", "who": by,
             "meta": "내가 걷는 것" if mine else f"내가 낼 것 · {by}",
             "day": f"{_d(at.date())} {at:%H:%M}", "today": at.date() == today}
        if list_first <= at.date() <= list_last:
            dues.append(x)
        cells[(at.date(), block_of_time(f"{at:%H:%M}"))].append(x)

    n_items = sum(len(v) for v in cells.values())
    if mode == "month":
        board = {"mcells": [{"n": d.day, "out": not list_first <= d <= list_last, "today": d == today, "date": d.isoformat(), "slot": "",
                             "weekend": d.weekday() >= 5, "when": _d(d), "add": f"{_d(d)}에 일정 추가",
                             "items": [x for k in ["", *SLOT_KEYS] for x in cells[(d, k)]]} for d in days],
                 "dow": MONTH_DOW}
    else:
        wrows = []
        for s in [*SLOTS, {"k": "", "label": "시간 미정"}]:
            t = "" if not s["k"] else f"{s['from']}~" + ("" if s["k"] == "pm" else s["to"])
            wrows.append({"label": s["label"], "time": t,
                          "cells": [{"items": cells[(d, s["k"])], "today": d == today, "date": d.isoformat(), "slot": s["k"],
                                     "when": f"{_d(d)} · {s['label']}", "add": f"{_d(d)} {s['label']}에 일정 추가"} for d in days]})
        board = {"wrows": wrows, "days": [{"md": f"{d.month}/{d.day}", "wd": WD[d.weekday()], "today": d == today} for d in days]}

    changed = sorted((e for e in evs if e.changed_from and e.date <= list_last and e.last_day >= list_first),
                     key=lambda e: e.updated_at, reverse=True)
    chg = []
    for e in changed:
        who = e.last_by
        color, ink = colors.get(who.pk if who else None, ("var(--ink)", False))
        parts = e.changed_from
        chg.append({"e": e, "who": who.name if who else "누군가", "color": color, "ink": ink,
                    "what": " · ".join(p[0] for p in parts), "a": " · ".join(p[1] for p in parts),
                    "b": " · ".join(p[2] for p in parts), "at": _at(e.updated_at, today)})

    return {"mode": mode, "w": v["w"].isoformat(), "heading": v["heading"], "label": v["label"], "is_this": v["is_this"],
            "prev_w": v["prev_w"].isoformat(), "this_w": v["this_w"].isoformat(), "next_w": v["next_w"].isoformat(),
            "nav_words": v["nav_words"], "period": v["period"], "period_short": v.get("period_short", v["period"]),
            "focus": v["focus"].isoformat(), "print_view": "month" if mode == "month" else "week",
            "paste_w": (v["focus"] if mode == "month" else v["w"]).isoformat(),
            "n_items": n_items, "dialogs": dialogs, "neis_empty": not seen, "neis_fail": neis.failed(sched),
            "chg": chg, "dues": dues, "slot_choices": SLOTS, "scope_choices": scope_choices(me), **board}


@never_cache
@require_GET
def index(request):
    mode = read_mode(request)
    r = render(request, "today/schedule.html",
               _space_ctx(request, page="schedule", toast=_paste_toast(request), **board_ctx(request, mode, request.GET.get("w"))))
    if request.GET.get("mode") in MODES:
        r.set_cookie(MODE_COOKIE, mode, max_age=MODE_AGE, httponly=True, samesite="Lax", secure=settings.SITE_URL.startswith("https://"))
    return r


def _paste_toast(request):
    """표 붙여넣기(week_paste) 뒤 돌아온 주소의 added·same·weekend → 한 줄 알림. 주소를 고쳐도 500이 나지 않게 짧은 숫자만 받는다."""
    n = [request.GET.get(k, "") for k in ("added", "same", "weekend")]
    if not all(x.isdecimal() and len(x) <= 4 for x in n):
        return ""
    added, same, weekend = map(int, n)
    return (f"주간계획 {added}건을 넣었어요." + (f" 이미 있던 {same}건은 건너뛰었어요." if same else "")
            + (f" 주말 {weekend}건은 월중행사표에만 나와요." if weekend else ""))


def reply(request, toast):
    """G1 쓰기 뷰가 view=week로 불렸을 때: 지금 모드·지금 날짜의 판을 다시 그린다."""
    ctx = _space_ctx(request, page="schedule", toast=toast, oob=True, **board_ctx(request, read_mode(request), request.POST.get("w")))
    return render(request, "today/_week.html", ctx)


def back_url(request):
    """htmx 없이 저장했을 때 돌아갈 주소: 지금 모드·지금 날짜."""
    mode = read_mode(request)
    w = VIEWS[mode](request.POST.get("w"), timezone.localdate())["w"]
    tail = f"&mode={mode}" if request.POST.get("mode") in MODES else ""
    return f"/s/{request.space.token}/schedule?w={w:%Y-%m-%d}{tail}"
