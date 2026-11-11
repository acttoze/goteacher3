"""일정(일간·주간·월간, Q3). 모습은 design/schedule.html, 동작은 prototype/assets/app.js cal().

- 탭(학교 공간, QC 2026-11-06): 학교·그룹(내 소속, 없으면 탭 없음)·개인. ?tab=, 마지막 탭은 쿠키(gt_sched_tab). 탭을 바꿔도 모드·날짜는 그대로.
  - 학교 탭: 채널 줄 학교(scope=all + NEIS)·복무. 일간·주간·월간 모두 가로 줄이 채널, 칸 안은 종일 → 블록 순.
  - 그룹 탭(scope=내 소속)·개인 탭(scope=me, 내가 쓴 것): 일간·주간은 종일·1~3블록·방과후 줄, 월간은 달력 칸에 시간순.
  - 동학년 공간: 탭 없음. 채널 줄 동학년(일정 + NEIS) 하나.
- 칸이 곧 볼 사람: 학교 탭 all, 그룹 탭 내 소속, 개인 탭 me(today.views.add가 확인). 복무 칸은 누구나 '이름 유형 [시간]'(duty_add).
- 취합은 일정에 없다(U3, 2026-11-11 사용자 결정): 취합 줄·마감 카드·마감 수·마감 목록 모두 없음. 마감은 오늘 화면·취합 메뉴에서 본다.
- 필터(학교 탭·동학년 채널만)는 schedule.js가 data-ch를 보고 숨긴다(서버는 표시만).
- 모드는 ?mode=. 메뉴에서 열면 늘 주간(마지막 모드를 기억하지 않는다, 2026-11-11 사용자 요청). 보고 있는 날은 ?w=(일간 그날, 주간 월요일, 월간 1일).
- 칸 안 추가·고치기·지우기는 G1 뷰(today.views)를 그대로 쓴다. view=week로 부르면 지금 탭·모드·날짜의 판(_week.html)을 돌려준다.
- 바뀐 것 목록: 보고 있는 날·주·달의 것만(탭과 무관).
- 인쇄는 print_view, 주간계획 표 붙여넣기는 week_paste(둘 다 학교 공간만, H6). 블록 끌기는 R3.
조회는 모두 in_space()를 거친다.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import timedelta
from math import ceil

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.lists import _colors
from spaces import neis
from spaces.attendance import DAYS_MAX, LINE_ERROR, _one, _roster, read_line
from spaces.gates import no_org, school_required, school_write_required
from spaces.models import Attendance, Member
from spaces.views import _space_ctx

from .models import ALL, ME, Event, reachable_q, with_counts
from .parse import SLOT_KEYS, SLOTS

WD = "월화수목금토일"
MONTH_DOW = "일월화수목금토"
MODES = ("day", "week", "month")
TAB_COOKIE = "gt_sched_tab"
MODE_AGE = 365 * 86400


def _d(d):
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


def _channels(space, me):
    """학교 탭·동학년의 채널 줄. scope: 칸 추가가 저장할 볼 사람, post: 칸 추가 주소(복무).
    기관(T2): 그룹마다 줄(관리자 순서, 내가 끈 줄은 뺌). 그룹이 아직 없으면 '기관 전체' 한 줄. 복무 줄은 없다."""
    if space.is_org:
        groups = [{"k": k, "label": n, "scope": k} for k, n in space.group_choices if k not in me.off_lines]
        return groups if space.org_groups else [{"k": "school", "label": "기관 전체", "scope": ALL}]
    if not space.is_school:
        return [{"k": "school", "label": space.word, "scope": ALL}]
    return [{"k": "school", "label": space.word, "scope": ALL},
            {"k": "duty", "label": "복무", "scope": "duty", "post": f"/s/{space.token}/today/duty"}]


def tabs(request):
    """[(키, 이름)]. 학교 공간만, 소속이 없으면 그룹 탭 없음."""
    me = request.member
    if not request.space.is_school:
        return []
    if request.space.is_org:  # 그룹 줄이 학교 탭에 다 보여서 그룹 탭은 없다(T2)
        return [("school", request.space.name), ("me", "개인")]
    return [("school", "학교"), *([("group", request.space.group_label(me.group))] if me.group else []), ("me", "개인")]


def read_tab(request):
    raw = request.POST.get("tab") or request.GET.get("tab") or request.COOKIES.get(TAB_COOKIE)
    return raw if raw in [k for k, _ in tabs(request)] else "school"


def this_monday(today, last=4):
    """이번 주 월요일. 켠 마지막 요일(기본 금)이 지났으면 다음 주(다가올 주를 본다)."""
    mon = today - timedelta(days=today.weekday())
    return mon + timedelta(days=7) if today.weekday() > last else mon


def _shown_date(raw):
    """주소·폼의 w(YYYY-MM-DD) → 날짜. 형식이 틀리거나 범위 밖이면 None."""
    try:
        d = parse_date(raw or "")
    except ValueError:
        d = None
    return d if d and 2000 <= d.year <= 2100 else None


def shown_monday(raw, today, last=4):
    """w → 그 주 월요일. 형식이 틀리거나 범위 밖이면 이번 주."""
    d = _shown_date(raw)
    return d - timedelta(days=d.weekday()) if d else this_monday(today, last)


def month_grid(first):
    """달력(일요일 시작): (첫 칸 날짜, 주 수). 인쇄 월중행사표와 같다."""
    lead = (first.weekday() + 1) % 7
    return first - timedelta(days=lead), ceil((lead + monthrange(first.year, first.month)[1]) / 7)


def read_mode(request):
    raw = request.POST.get("mode") or request.GET.get("mode")
    return raw if raw in MODES else "week"


def _label(mon, on=range(5)):
    """주 이름: 켠 첫 요일 ~ 켠 마지막 요일(기본 월~금)."""
    a, b = mon + timedelta(days=min(on)), mon + timedelta(days=max(on))
    return f"{a.month}월 {a.day}일" + ("" if a == b else " ~ " + (f"{b.day}일" if b.month == a.month else f"{b.month}월 {b.day}일"))


def _heading(mon, this_mon, on):
    rel = (mon - this_mon).days // 7
    return {0: "이번 주 일정", -1: "지난주 일정", 1: "다음 주 일정"}.get(rel, f"{_label(mon, on)} 일정")


def _at(dt, today):
    at = timezone.localtime(dt)
    day = "오늘" if at.date() == today else "어제" if at.date() == today - timedelta(days=1) else _d(at.date())
    return f"{day} {at:%H:%M}"


def _day_view(raw, today, on):
    d = _shown_date(raw) or today
    label = f"{d.month}월 {d.day}일 {WD[d.weekday()]}요일"
    heading = {0: "오늘 일정", -1: "어제 일정", 1: "내일 일정"}.get((d - today).days, f"{label} 일정")
    return {"first": d, "last": d, "list_first": d, "list_last": d, "focus": d, "w": d, "heading": heading, "label": label,
            "is_this": d == today, "prev_w": d - timedelta(days=1), "this_w": today, "next_w": d + timedelta(days=1),
            "nav_words": ("전날", "오늘", "다음 날"), "period": "이 날"}


def _week_view(raw, today, on):
    """판에는 켠 요일 칸만(board_ctx가 거른다). 기간은 켠 첫 요일 ~ 마지막 요일."""
    mon, this_mon = shown_monday(raw, today, max(on)), this_monday(today, max(on))
    a, b = mon + timedelta(days=min(on)), mon + timedelta(days=max(on))
    return {"first": a, "last": b, "list_first": a, "list_last": b, "focus": today if a <= today <= b and today.weekday() in on else a, "w": mon,
            "heading": _heading(mon, this_mon, on), "label": _label(mon, on), "is_this": mon == this_mon,
            "prev_w": mon - timedelta(days=7), "this_w": this_mon, "next_w": mon + timedelta(days=7),
            "nav_words": ("지난주", "이번 주", "다음 주"), "period": "이번 주", "period_short": "이 주"}


def _month_view(raw, today, on):
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


def _duty_deletable(a, me):
    """복무 한 줄을 지울 수 있는 사람: 넣은 사람·당사자(명부로 맞춘 회원)·관리자·복무 입력자."""
    return me.is_manager or me.can_duty or me.pk in (a.by_id, a.member_id)


def _dialog_scopes(me, e):
    """수정 대화상자의 볼 사람: 학교 전체 · 내 그룹 · 개인(나만). 지금 값이 다른 그룹이면 그 값도 남긴다.
    기관(T2): 모든 그룹 줄 + 개인(줄을 옮길 수 있다)."""
    if me.space.is_org:
        return [*(me.space.group_choices or [(ALL, "기관 전체")]), (ME, "개인(나만)")]
    out = [(ALL, f"{me.space.word} 전체"), *([(me.group, me.space.group_label(me.group))] if me.group else []), (ME, "개인(나만)")]
    if e.scope not in [k for k, _ in out]:
        out.insert(-1, (e.scope, me.space.group_label(e.scope) or "지운 그룹"))
    return out


def board_ctx(request, mode, raw):
    """판 하나의 컨텍스트. 모드는 보는 기간(first~last, 빠진 날 없이 이어짐)과 칸 모양, 탭은 담는 것과 줄 모양만 다르다."""
    from .views import _conf_text, _ev_item, week_conflicts  # views가 이 모듈을 부르므로 안에서 가져온다

    space, me = request.space, request.member
    now = timezone.now()
    today = timezone.localdate(now)
    on = me.shown_days  # Q1 표시 요일: 주·월 보기 모두 켠 요일 칸만(기간 일정도 켠 요일 칸에만 이어진다)
    v = VIEWS[mode](raw, today, on)
    first, last, list_first, list_last = v["first"], v["last"], v["list_first"], v["list_last"]
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    if mode != "day":
        days = [d for d in days if d.weekday() in on]
    colors = _colors(space)
    hexes, names = space.group_hex, space.group_names  # U1 카드 점 색: 쓴 사람(복무는 당사자)의 소속
    tab_list = tabs(request)
    tab = read_tab(request)
    channeled = tab == "school"  # 학교 탭·동학년: 채널 줄. 그룹·개인 탭: 블록 줄(월간은 달력 칸)
    tab_scope = {"group": me.group, "me": ME}.get(tab, ALL)
    channels = _channels(space, me) if channeled else []
    shown_lines = {c["scope"] for c in channels}

    def on_tab(e):
        if not space.is_school:
            return True
        if tab == "me":
            return e.scope == ME and e.by_id == me.pk
        if space.is_org:  # 그룹 줄 = 일정 줄. 끈 줄은 내 판에서만 빠진다(보기 필터, T2)
            return e.scope in shown_lines
        return e.scope == tab_scope

    def line_of(e):  # 학교 탭 칸의 줄 키: 기관은 그룹 키, 그 밖은 학교(전체) 줄
        return e.scope if space.is_org and e.scope != ALL else "school"

    # 일정: 보고 있는 기간에 걸치는 것. 바뀐 것 목록은 탭과 무관하게 내가 볼 수 있는 전부.
    seen_evs = list(with_counts(Event.objects.in_space(space).filter(reachable_q(me), date__lte=last))
                    .filter(Q(end__gte=first) | Q(end__isnull=True, date__gte=first))
                    .select_related("by", "changed_by", "space").order_by("date", "slot", "created_at"))
    evs = [e for e in seen_evs if on_tab(e)]
    cells = defaultdict(list)  # (날짜, 채널, 블록) → 칸 안 항목. 블록 줄 화면은 채널이 ""
    dialogs = []
    clashes = week_conflicts(space, me, evs)
    for e in evs:
        x = _ev_item(e, me, today, hexes, names)
        ch = line_of(e) if channeled else ""
        x["kind"], x["ch"], x["scopes"] = "ev", ch, _dialog_scopes(me, e)
        x["g"] = e.by.group if e.by else ""  # U4 소속 필터: 쓴 사람 소속
        x["fresh"] = x["mine"] and not e.changed_from and now - e.created_at < timedelta(minutes=1)
        x["changes"] = [(w, a, b) for w, a, b in (e.changed_from or [])]
        conf = clashes.get(e.pk)
        x["conf"] = _conf_text({"place": e.place}, conf, me) if conf else ""
        # 줄로 블록을 알 수 없는 카드에 블록 이름. 제목에 이미 있으면('2블록 안전교육') 다시 쓰지 않는다.
        blk = x["slot_label"] if e.slot else ""
        if blk and blk in e.title:
            x["meta"] = " · ".join(m for m in x["meta"].split(" · ") if m != blk)
            blk = ""
        one_block = e.slot_end in ("", e.slot)
        x["tag"] = blk if mode == "month" or (channeled and one_block) else ""
        dialogs.append(x)
        for d in days:
            if e.date <= d <= e.last_day:
                cells[(d, ch, e.slot)].append(x)

    seen = set()
    sched = []
    duty_dialogs = []
    if channeled and not space.is_org:  # 기관은 NEIS가 없다(T1)
        # NEIS 학사일정(시간 없음 → 종일). 불러오기 실패와 '없음'을 나눠 알린다.
        sched = neis.schedule(space.school.neis_atpt, space.school.neis_code, f"{first:%Y%m%d}", f"{last:%Y%m%d}")
        for r in sched:
            d = parse_date(f"{r['ymd'][:4]}-{r['ymd'][4:6]}-{r['ymd'][6:8]}") if len(r["ymd"]) == 8 else None
            if d and first <= d <= last and (d, r["name"]) not in seen:
                seen.add((d, r["name"]))
                cells[(d, "school", "")].append({"kind": "neis", "name": r["name"], "ch": "school"})

    # 복무(J1, 학교 공간): '이름 유형'. 모든 회원에게 같게. 지우기는 넣은 사람·당사자·관리자·복무 입력자.
    # 남이 넣은 줄(넣은 사람 ≠ 당사자)에는 넣은 사람을 함께 보인다(이름 위조를 알아보게).
    if channeled and space.is_school and not space.is_org:
        for a in Attendance.objects.in_space(space).filter(date__range=(first, last)).select_related("by", "member").order_by("pk"):
            x = {"kind": "duty", "a": a, "title": a.title, "time": a.time_text, "ch": "duty", "can_del": _duty_deletable(a, me),
                 "by_other": a.by.name if a.by_id and a.by_id != a.member_id else "", "mine": a.by_id == me.pk,
                 "who": a.group_label, "gc": hexes.get(a.member.group, "") if a.member else "",  # 당사자 소속(명부로 못 맞추면 비움)
                 "g": a.member.group if a.member else ""}  # U4 소속 필터: 당사자 소속
            cells[(a.date, "duty", "")].append(x)
            if x["can_del"]:
                duty_dialogs.append(x)

    if mode == "month":  # 켠 요일이 모두 지난달·다음 달인 주는 뺀다
        weeks = [w for w in (days[i:i + len(on)] for i in range(0, len(days), len(on))) if any(list_first <= d <= list_last for d in w)]
        days = [d for w in weeks for d in w]
    shown = set(days)
    n_items = sum(len(v) for (d, _, _), v in cells.items() if d in shown)

    def by_slot(d, k):  # 한 칸 안: 나이스 학사일정, 종일, 그다음 블록 순
        return [x for s in ["", *SLOT_KEYS] for x in sorted(cells[(d, k, s)], key=lambda x: x["kind"] != "neis")]

    # 가로 줄: 채널(학교 탭·동학년) 또는 블록(그룹·개인 탭의 일간·주간). 그룹·개인 탭의 월간은 이름 없는 한 줄.
    if channeled:
        lines = [{"label": c["label"], "ch": c["k"], "slot": "", "scope": c["scope"], "post": c.get("post", ""),
                  "items": lambda d, k=c["k"]: by_slot(d, k)} for c in channels]
    elif mode == "month":
        lines = [{"label": "", "slot": "", "items": lambda d: by_slot(d, "")}]
    else:
        lines = [{"label": "종일", "slot": "", "items": lambda d: cells[(d, "", "")]},
                 *[{"label": s["label"], "slot": s["k"], "time": f"{s['from']}~" + ("" if s["k"] == "pm" else s["to"]),
                    "items": lambda d, k=s["k"]: cells[(d, "", k)]} for s in SLOTS]]

    def cell(d, line):
        scope = line.get("scope", tab_scope)
        name = line["label"] or "일정"
        return {"items": line["items"](d), "today": d == today, "date": d.isoformat(), "slot": line["slot"], "scope": scope,
                "post": line.get("post", ""), "when": f"{_d(d)} · {name}",
                "add": f"{_d(d)} 복무 추가" if line.get("post") else f"{_d(d)} " + (f"{line['label']}에 " if line["label"] else "") + "일정 추가"}

    if mode == "month":
        mweeks = []
        for week in weeks:
            flags = [{"n": d.day, "out": not list_first <= d <= list_last, "today": d == today, "weekend": d.weekday() >= 5} for d in week]
            mweeks.append({"heads": flags, "rows": [{"ch": ln.get("ch", ""), "label": ln["label"],
                                                    "cells": [{**f, **cell(d, ln)} for d, f in zip(week, flags)]} for ln in lines]})
        board = {"mweeks": mweeks, "dow": [(w, w in "일토") for i, w in enumerate(MONTH_DOW) if (i + 6) % 7 in on]}
    else:
        wrows = [{"label": ln["label"], "ch": ln.get("ch", ""), "time": ln.get("time", ""), "cells": [cell(d, ln) for d in days]} for ln in lines]
        for r in wrows:  # 기관: 일정이 하나도 없는 줄은 낮게 접는다(그룹 8줄을 한눈에, 리드 결정)
            r["empty"] = space.is_org and channeled and not any(c["items"] for c in r["cells"])
        board = {"wrows": wrows, "days": [{"md": f"{d.month}/{d.day}", "wd": WD[d.weekday()], "today": d == today} for d in days]}

    changed = sorted((e for e in seen_evs if e.changed_from and e.date <= list_last and e.last_day >= list_first),
                     key=lambda e: e.updated_at, reverse=True)
    chg = []
    for e in changed:
        who = e.last_by
        color, ink = colors.get(who.pk if who else None, ("var(--ink)", False))
        parts = e.changed_from
        chg.append({"e": e, "who": who.name if who else "누군가", "color": color, "ink": ink,
                    "what": " · ".join(p[0] for p in parts), "a": " · ".join(p[1] for p in parts),
                    "b": " · ".join(p[2] for p in parts), "at": _at(e.updated_at, today)})

    tab_label = dict(tab_list).get(tab, "")
    legend = []  # U1 범례 = U4 소속 필터 단추: 이 학교에 사람이 있는 소속의 (키, 이름, 점 색)(학교 탭·그룹 탭)
    if space.is_school and tab != "me":
        have = set(Member.objects.in_space(space).values_list("group", flat=True))
        legend = [(g, label, hexes[g]) for g, label in space.group_choices if g in have]
    return {"mode": mode, "w": v["w"].isoformat(), "heading": v["heading"], "label": v["label"], "is_this": v["is_this"],
            "prev_w": v["prev_w"].isoformat(), "this_w": v["this_w"].isoformat(), "next_w": v["next_w"].isoformat(),
            "nav_words": v["nav_words"], "period": v["period"], "period_short": v.get("period_short", v["period"]),
            "focus": v["focus"].isoformat(), "print_view": "month" if mode == "month" else "week",
            "paste_w": (v["focus"] if mode == "month" else v["w"]).isoformat(),
            "tabs": tab_list, "tab": tab, "tab_label": tab_label, "channeled": channeled, "my_group_label": names.get(me.group, ""),
            "board_title": tab_label if tab != "school" or space.is_org else space.label + ("" if space.is_school else " · 학교"),
            "n_items": n_items, "dialogs": dialogs, "duty_dialogs": duty_dialogs,
            "neis_empty": channeled and not seen and not space.is_org, "neis_fail": channeled and neis.failed(sched),
            "chg": chg, "slot_choices": SLOTS, "filter_channels": channels if len(channels) > 1 else [],  # 줄이 하나면 필터가 쓸모없다(U3: 동학년·그룹 없는 기관)
            "legend": legend, **board}


def _set_cookies(request, r):
    secure = settings.SITE_URL.startswith("https://")
    if request.GET.get("tab") in [k for k, _ in tabs(request)]:
        r.set_cookie(TAB_COOKIE, request.GET["tab"], max_age=MODE_AGE, httponly=True, samesite="Lax", secure=secure)


@never_cache
@require_GET
def index(request):
    mode = read_mode(request)
    r = render(request, "today/schedule.html",
               _space_ctx(request, page="schedule", toast=_paste_toast(request), **board_ctx(request, mode, request.GET.get("w"))))
    _set_cookies(request, r)
    return r


def _paste_toast(request):
    """표 붙여넣기(week_paste) 뒤 돌아온 주소의 added·same·weekend(주 보기에서 빠지는 건수) → 한 줄 알림. 주소를 고쳐도 500이 나지 않게 짧은 숫자만 받는다."""
    n = [request.GET.get(k, "") for k in ("added", "same", "weekend")]
    if not all(x.isdecimal() and len(x) <= 4 for x in n):
        return ""
    added, same, weekend = map(int, n)
    off = "주말" if request.member.shown_days == set(range(5)) else "꺼 둔 요일"  # Q1: 주 보기에서 빠지는 요일
    return (f"주간계획 {added}건을 넣었어요." + (f" 이미 있던 {same}건은 건너뛰었어요." if same else "")
            + (f" {off} {weekend}건은 월중행사표에만 나와요." if weekend else ""))


def reply(request, toast):
    """G1 쓰기 뷰가 view=week로 불렸을 때: 지금 탭·모드·날짜의 판을 다시 그린다."""
    ctx = _space_ctx(request, page="schedule", toast=toast, oob=True, **board_ctx(request, read_mode(request), request.POST.get("w")))
    return render(request, "today/_week.html", ctx)


def back_url(request):
    """htmx 없이 저장했을 때 돌아갈 주소: 지금 탭·모드·날짜."""
    mode = read_mode(request)
    w = VIEWS[mode](request.POST.get("w"), timezone.localdate(), request.member.shown_days)["w"]
    tail = f"&mode={mode}" if request.POST.get("mode") in MODES else ""
    tail += f"&tab={read_tab(request)}" if request.POST.get("tab") else ""
    return f"/s/{request.space.token}/schedule?w={w:%Y-%m-%d}{tail}"


def _done(request, toast):
    if request.headers.get("HX-Request"):
        return reply(request, toast)
    r = redirect(back_url(request))
    r.status_code = 303
    return r


@never_cache
@require_POST
@school_required
@no_org
@school_write_required
def duty_add(request):
    """학교 탭 복무 칸: '이름 유형 [시간]' 한 줄(누구나). 날짜는 칸, 오늘 ±31일만. 못 읽으면 원문을 돌려 보이지 않고 짧은 안내만."""
    from .views import LINE_MAX, _clean, _read_cell

    d, _ = _read_cell(request)
    if d is None:
        return HttpResponseBadRequest("칸의 날짜를 다시 확인해 주세요.")
    roster = _roster(request.space)
    if abs((d - timezone.localdate()).days) > DAYS_MAX:
        got, error = None, f"복무는 오늘 앞뒤 {DAYS_MAX}일 안에만 넣을 수 있어요."
    else:
        got = read_line(_clean(request.POST.get("line", ""), LINE_MAX), set(roster))
        error = "" if got else LINE_ERROR
    if got is None:
        r = render(request, "today/_cell_err.html", {"error": error})
        r["HX-Retarget"], r["HX-Reswap"] = "#cellErr", "outerHTML"
        return r
    a = Attendance.objects.create(space=request.space, date=d, name=got["name"], kind=got["kind"], time_text=got["time"],
                                  by=request.member, member=_one(roster, got["name"]))
    return _done(request, f"{_d(d)} 복무 「{a.title}{' ' + a.time_text if a.time_text else ''}」 올렸어요 · 사유는 저장하지 않아요")


@never_cache
@require_POST
@school_required
@no_org
@school_write_required
def duty_delete(request, key):
    a = get_object_or_404(Attendance.objects.in_space(request.space), key=key)
    if not _duty_deletable(a, request.member):
        return HttpResponseForbidden("복무 지우기는 넣은 선생님·본인·관리자·복무 입력자만 할 수 있어요.")
    title = a.title
    a.delete()
    return _done(request, f"복무 「{title}」 지웠어요.")
