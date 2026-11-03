"""R1 일정(주간). 모습은 design/schedule.html, 동작은 prototype/assets/app.js cal() 주간 보기.

- 주간판: 월~금 × 블록 4칸 + 시간 미정. 동학년 일정(Event), NEIS 학사일정(시간 미정 줄), 모으기 마감(마감 시각의 블록).
- 한 줄 입력·고치기·지우기는 G1 뷰(today.views)를 그대로 쓴다. view=week로 부르면 주간판(_week.html)을 돌려준다.
- 이번 주 바뀐 것: 보고 있는 주의 일정 중 changed_from이 있는 것. 이번 주 마감: 보고 있는 주(월~금)에 마감인 모으기.
- 학교 공간(H5): 일정은 reachable_q(관리자는 남의 '나'를 뺀 전부), 마감은 관리자도 내가 만든 것·내 줄·내 그룹 칸만.
- R1에서 뺀 것: 인쇄(R2), 블록 끌기(R3), 월간·주간계획 표 붙여넣기(design에 없음).
조회는 모두 in_space()를 거친다.
"""
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from collect.lists import _colors, _mine_q
from collect.models import Collection, Row
from spaces import neis
from spaces.views import _space_ctx

from .models import Event, reachable_q, scope_choices
from .parse import SLOTS, block_of_time, slot_idx

WD = "월화수목금토일"


def _d(d):
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


def this_monday(today):
    """이번 주 월요일. 주말이면 다음 주(다가올 주를 본다)."""
    mon = today - timedelta(days=today.weekday())
    return mon + timedelta(days=7) if today.weekday() >= 5 else mon


def shown_monday(raw, today):
    """주소·폼의 w(YYYY-MM-DD) → 그 주 월요일. 형식이 틀리거나 범위 밖이면 이번 주."""
    try:
        d = parse_date(raw or "")
    except ValueError:
        d = None
    if d is None or not 2000 <= d.year <= 2100:
        return this_monday(today)
    return d - timedelta(days=d.weekday())


def _label(mon):
    fri = mon + timedelta(days=4)
    return f"{mon.month}월 {mon.day}일 ~ " + (f"{fri.day}일" if fri.month == mon.month else f"{fri.month}월 {fri.day}일")


def _heading(mon, this_mon):
    rel = (mon - this_mon).days // 7
    return {0: "이번 주 일정", -1: "지난주 일정", 1: "다음 주 일정"}.get(rel, f"{_label(mon)} 일정")


def where_text(p, mon, today):
    """한 줄 입력이 판의 어디에 놓이나(design .parsed .to)."""
    d = p["date"]
    if d.weekday() >= 5:
        return f"{_d(d)}은 주말이라 주간판에는 보이지 않아요"
    if mon <= d <= mon + timedelta(days=4):
        name = "이번 주" if mon == this_monday(today) else "이 주"
        return f"{name} {WD[d.weekday()]}요일 · {SLOTS[slot_idx(p['s'])]['label'] if p['s'] else '시간 미정'} 칸에 놓여요"
    return f"{_d(d)} 주에 놓여요"


def _at(dt, today):
    at = timezone.localtime(dt)
    day = "오늘" if at.date() == today else "어제" if at.date() == today - timedelta(days=1) else _d(at.date())
    return f"{day} {at:%H:%M}"


def week_ctx(request, mon):
    from .views import _conf_text, _ev_item, week_conflicts  # views가 이 모듈을 부르므로 안에서 가져온다

    space, me = request.space, request.member
    now = timezone.now()
    today = timezone.localdate(now)
    this_mon = this_monday(today)
    days = [mon + timedelta(days=i) for i in range(5)]
    fri = days[-1]
    colors = _colors(space)

    # 일정: 보고 있는 주에 걸치는 것
    evs = list(Event.objects.in_space(space).filter(reachable_q(me), date__lte=fri)
               .filter(Q(end__gte=mon) | Q(end__isnull=True, date__gte=mon))
               .select_related("by", "changed_by").order_by("date", "slot", "created_at"))
    cells = {(i, s["k"]): [] for i in range(5) for s in [*SLOTS, {"k": ""}]}
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
        for i, d in enumerate(days):
            if e.date <= d <= e.last_day:
                cells[(i, e.slot)].append(x)

    # NEIS 학사일정(시간 없음 → 시간 미정 줄). 불러오기 실패와 '없음'을 나눠 알린다.
    sched = neis.schedule(space.school.neis_atpt, space.school.neis_code, f"{mon:%Y%m%d}", f"{fri:%Y%m%d}")
    seen = set()
    for r in sched:
        d = parse_date(f"{r['ymd'][:4]}-{r['ymd'][4:6]}-{r['ymd'][6:8]}") if len(r["ymd"]) == 8 else None
        if d and mon <= d <= fri and (d, r["name"]) not in seen:
            seen.add((d, r["name"]))
            cells[(d.weekday(), "")].append({"kind": "neis", "name": r["name"]})

    # 모으기 마감(월 0시 ~ 토 0시)
    tz = timezone.get_current_timezone()
    start = datetime.combine(mon, time(0), tzinfo=tz)
    cs = (Collection.objects.in_space(space).filter(due__gte=start, due__lt=start + timedelta(days=5))
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
        dues.append(x)
        cells[(at.weekday(), block_of_time(f"{at:%H:%M}"))].append(x)

    wrows = []
    for s in [*SLOTS, {"k": "", "label": "시간 미정"}]:
        t = "" if not s["k"] else f"{s['from']}~" + ("" if s["k"] == "pm" else s["to"])
        wrows.append({"label": s["label"], "time": t,
                     "cells": [{"items": cells[(i, s["k"])], "today": d == today,
                                "when": f"{_d(d)} · {s['label']}"} for i, d in enumerate(days)]})

    changed = sorted((e for e in evs if e.changed_from), key=lambda e: e.updated_at, reverse=True)
    chg = []
    for e in changed:
        who = e.last_by
        color, ink = colors.get(who.pk if who else None, ("var(--ink)", False))
        parts = e.changed_from
        chg.append({"e": e, "who": who.name if who else "누군가", "color": color, "ink": ink,
                    "what": " · ".join(p[0] for p in parts), "a": " · ".join(p[1] for p in parts),
                    "b": " · ".join(p[2] for p in parts), "at": _at(e.updated_at, today)})

    n_items = sum(len(v) for v in cells.values())
    return {"mon": mon, "w": mon.isoformat(), "heading": _heading(mon, this_mon), "label": _label(mon),
            "is_this": mon == this_mon, "prev_w": (mon - timedelta(days=7)).isoformat(),
            "this_w": this_mon.isoformat(), "next_w": (mon + timedelta(days=7)).isoformat(),
            "days": [{"d": d, "md": f"{d.month}/{d.day}", "wd": WD[d.weekday()], "today": d == today} for d in days],
            "wrows": wrows, "n_items": n_items, "dialogs": dialogs, "neis_empty": not seen, "neis_fail": neis.failed(sched),
            "chg": chg, "dues": dues, "slot_choices": SLOTS, "scope_choices": scope_choices(me)}


@never_cache
@require_GET
def index(request):
    today = timezone.localdate()
    mon = shown_monday(request.GET.get("w"), today)
    return render(request, "today/schedule.html", _space_ctx(request, page="schedule", **week_ctx(request, mon)))


def reply(request, toast):
    """G1 쓰기 뷰가 view=week로 불렸을 때: 보고 있는 주의 주간판을 다시 그린다."""
    mon = shown_monday(request.POST.get("w"), timezone.localdate())
    ctx = _space_ctx(request, page="schedule", toast=toast, oob=True, **week_ctx(request, mon))
    return render(request, "today/_week.html", ctx)
