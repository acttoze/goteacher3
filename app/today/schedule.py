"""일정(일간·주간·월간, Q3). 모습은 design/schedule.html, 동작은 prototype/assets/app.js cal().

- 탭(학교 공간, QC 2026-11-06): 학교·그룹(내 소속, 없으면 탭 없음)·개인. ?tab=, 마지막 탭은 쿠키(gt_sched_tab). 탭을 바꿔도 모드·날짜는 그대로.
  - 학교 탭: 채널 줄 학교(scope=all + NEIS)·취합(이 공간의 모든 마감)·복무. 일간·주간·월간 모두 가로 줄이 채널, 칸 안은 종일 → 블록 순.
  - 그룹 탭(scope=내 소속)·개인 탭(scope=me, 내가 쓴 것): 일간·주간은 종일·1~3블록·방과후 줄, 월간은 달력 칸에 시간순.
  - 동학년 공간: 탭 없음. 채널 줄 동학년(일정 + NEIS)·취합.
- 칸이 곧 볼 사람: 학교 탭 all, 그룹 탭 내 소속, 개인 탭 me(today.views.add가 확인). 복무 칸은 누구나 '이름 유형 [시간]'(duty_add).
- 취합(QC 일정 취합 범위 2026-11-06): 받는 사람이 전 교직원(audience all)이면 학교 탭 취합 줄, 소속 고르기·소속별 1칸이면
  고른 소속의 그룹 탭 맨 위 취합 줄(월간은 칸 맨 위)에만. 부서·사람 고르기·범위 모름은 일정 탭에 없고 아래 마감 목록에만.
  취합 줄 칸의 ＋는 입력 카드 대신 취합 만들기로 간다: 그 날 마감, 학교 탭은 전 교직원·그룹 탭은 내 소속(QC 2026-11-06).
  카드는 제목·마감·만든 사람. 링크는 만든 사람(관리), 내 줄(받는 링크), 관리자(관리)에게만. 받는 링크를 대상 아닌 사람에게 싣지 않는다.
- 필터(학교 탭·동학년 채널만)는 schedule.js가 data-ch를 보고 숨긴다(서버는 표시만).
- 모드는 ?mode=, 마지막 모드는 쿠키(gt_sched_mode). 보고 있는 날은 ?w=(일간 그날, 주간 월요일, 월간 1일).
- 칸 안 추가·고치기·지우기는 G1 뷰(today.views)를 그대로 쓴다. view=week로 부르면 지금 탭·모드·날짜의 판(_week.html)을 돌려준다.
- 바뀐 것·마감 목록: 보고 있는 날·주·달의 것만(탭과 무관). 마감 목록은 내가 만든 것·내 줄만.
- 인쇄는 print_view, 주간계획 표 붙여넣기는 week_paste(둘 다 학교 공간만, H6). 블록 끌기는 R3.
조회는 모두 in_space()를 거친다.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, time, timedelta
from math import ceil

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.access import can_open
from collect.lists import _colors, _mine_q
from collect.models import Collection, Row
from spaces import neis
from spaces.attendance import DAYS_MAX, LINE_ERROR, _one, _roster, read_line
from spaces.gates import school_required, school_write_required
from spaces.models import Attendance
from spaces.views import _space_ctx

from .models import ALL, ME, Event, reachable_q
from .parse import SLOT_KEYS, SLOTS

WD = "월화수목금토일"
MONTH_DOW = "일월화수목금토"
MODES = ("day", "week", "month")
MODE_COOKIE = "gt_sched_mode"
TAB_COOKIE = "gt_sched_tab"
MODE_AGE = 365 * 86400


def _d(d):
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


def _channels(space):
    """학교 탭·동학년의 채널 줄. scope: 칸 추가가 저장할 볼 사람(비면 칸 추가 없음), post: 칸 추가 주소(복무).
    go: 칸 추가가 대신 여는 취합 만들기 주소(그 날을 마감으로, 학교 탭은 전 교직원)."""
    go = f"/s/{space.token}/c/new?" + ("aud=all&" if space.is_school else "")
    if not space.is_school:
        return [{"k": "school", "label": space.word, "scope": ALL}, {"k": "collect", "label": "취합", "scope": "", "go": go}]
    return [{"k": "school", "label": space.word, "scope": ALL}, {"k": "collect", "label": "취합", "scope": "", "go": go},
            {"k": "duty", "label": "복무", "scope": "duty", "post": f"/s/{space.token}/today/duty"}]


def tabs(request):
    """[(키, 이름)]. 학교 공간만, 소속이 없으면 그룹 탭 없음."""
    me = request.member
    if not request.space.is_school:
        return []
    return [("school", "학교"), *([("group", me.get_group_display())] if me.group else []), ("me", "개인")]


def read_tab(request):
    raw = request.POST.get("tab") or request.GET.get("tab") or request.COOKIES.get(TAB_COOKIE)
    return raw if raw in [k for k, _ in tabs(request)] else "school"


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


def _duty_deletable(a, me):
    """복무 한 줄을 지울 수 있는 사람: 넣은 사람·당사자(명부로 맞춘 회원)·관리자·복무 입력자."""
    return me.is_manager or me.can_duty or me.pk in (a.by_id, a.member_id)


def _collect_tab(space, c):
    """취합 카드가 놓일 탭: 'school'(전 교직원, 동학년은 언제나), 고른 소속 목록(소속 고르기·소속별 1칸), 그 밖에는 None(일정에 없음)."""
    if not space.is_school:
        return "school"
    aud = c.audience if isinstance(c.audience, dict) else {}
    mode = aud.get("mode")
    if mode == "all":
        return "school"
    if mode in ("groups", "pergroup") and isinstance(aud.get("groups"), list):
        return aud["groups"]
    return None


def _dialog_scopes(me, e):
    """수정 대화상자의 볼 사람: 학교 전체 · 내 그룹 · 개인(나만). 지금 값이 다른 그룹이면 그 값도 남긴다."""
    out = [(ALL, "학교 전체"), *([(me.group, me.get_group_display())] if me.group else []), (ME, "개인(나만)")]
    if e.scope not in [k for k, _ in out]:
        out.insert(-1, (e.scope, e.scope_label))
    return out


def board_ctx(request, mode, raw):
    """판 하나의 컨텍스트. 모드는 보는 기간(first~last, 빠진 날 없이 이어짐)과 칸 모양, 탭은 담는 것과 줄 모양만 다르다."""
    from .views import _conf_text, _ev_item, week_conflicts  # views가 이 모듈을 부르므로 안에서 가져온다

    space, me = request.space, request.member
    now = timezone.now()
    today = timezone.localdate(now)
    v = VIEWS[mode](raw, today)
    first, last, list_first, list_last = v["first"], v["last"], v["list_first"], v["list_last"]
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    colors = _colors(space)
    tab_list = tabs(request)
    tab = read_tab(request)
    channeled = tab == "school"  # 학교 탭·동학년: 채널 줄. 그룹·개인 탭: 블록 줄(월간은 달력 칸)
    tab_scope = {"group": me.group, "me": ME}.get(tab, ALL)
    channels = _channels(space) if channeled else []

    def on_tab(e):
        if not space.is_school:
            return True
        if tab == "me":
            return e.scope == ME and e.by_id == me.pk
        return e.scope == tab_scope

    # 일정: 보고 있는 기간에 걸치는 것. 바뀐 것 목록은 탭과 무관하게 내가 볼 수 있는 전부.
    seen_evs = list(Event.objects.in_space(space).filter(reachable_q(me), date__lte=last)
                    .filter(Q(end__gte=first) | Q(end__isnull=True, date__gte=first))
                    .select_related("by", "changed_by").order_by("date", "slot", "created_at"))
    evs = [e for e in seen_evs if on_tab(e)]
    cells = defaultdict(list)  # (날짜, 채널, 블록) → 칸 안 항목. 블록 줄 화면은 채널이 ""
    ch = "school" if channeled else ""
    dialogs = []
    clashes = week_conflicts(space, me, evs)
    for e in evs:
        x = _ev_item(e, me, today)
        x["kind"], x["ch"], x["scopes"] = "ev", ch, _dialog_scopes(me, e)
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
        for d in days:  # 월간의 기간 일정은 이어지는 주말을 뺀다(prototype)
            if e.date <= d <= e.last_day and (mode != "month" or e.date == d or d.weekday() < 5):
                cells[(d, ch, e.slot)].append(x)

    seen = set()
    sched = []
    duty_dialogs = []
    if channeled:
        # NEIS 학사일정(시간 없음 → 종일). 불러오기 실패와 '없음'을 나눠 알린다.
        sched = neis.schedule(space.school.neis_atpt, space.school.neis_code, f"{first:%Y%m%d}", f"{last:%Y%m%d}")
        for r in sched:
            d = parse_date(f"{r['ymd'][:4]}-{r['ymd'][4:6]}-{r['ymd'][6:8]}") if len(r["ymd"]) == 8 else None
            if d and first <= d <= last and (d, r["name"]) not in seen:
                seen.add((d, r["name"]))
                cells[(d, "school", "")].append({"kind": "neis", "name": r["name"], "ch": "school"})

    # 복무(J1, 학교 공간): '이름 유형'. 모든 회원에게 같게. 지우기는 넣은 사람·당사자·관리자·복무 입력자.
    # 남이 넣은 줄(넣은 사람 ≠ 당사자)에는 넣은 사람을 함께 보인다(이름 위조를 알아보게).
    if channeled and space.is_school:
        for a in Attendance.objects.in_space(space).filter(date__range=(first, last)).select_related("by").order_by("pk"):
            x = {"kind": "duty", "a": a, "title": a.title, "time": a.time_text, "ch": "duty", "can_del": _duty_deletable(a, me),
                 "by_other": a.by.name if a.by_id and a.by_id != a.member_id else ""}
            cells[(a.date, "duty", "")].append(x)
            if x["can_del"]:
                duty_dialogs.append(x)

    # 취합 마감(첫날 0시 ~ 끝날 다음 날 0시). 칸은 학교 탭에 모두, 아래 목록은 내가 만든 것·내 줄만.
    tz = timezone.get_current_timezone()
    start = datetime.combine(first, time(0), tzinfo=tz)
    cs = list(Collection.objects.in_space(space).filter(due__gte=start, due__lt=datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz))
              .select_related("created_by").order_by("due"))
    dues = []
    my_rows = {}
    for r in Row.objects.in_space(space).filter(collection__in=[c.pk for c in cs if c.created_by_id != me.pk]).filter(_mine_q(me)).order_by("-pos"):
        my_rows[r.collection_id] = r  # 내림차순이라 맨 앞 줄이 남는다
    for c in cs:
        at = timezone.localtime(c.due)
        mine, row = c.created_by_id == me.pk, my_rows.get(c.pk)
        by = c.created_by.name if c.created_by else "알 수 없음"
        if mine:
            href = f"/s/{space.token}/c/{c.key}/"
        elif row:
            href = f"/c/{c.link_token}/u/{row.key}"
        elif can_open(request, c.created_by_id) and space.is_school:  # 관리자
            href = f"/s/{space.token}/c/{c.key}/"
        else:
            href = "" if space.is_school else f"/c/{c.link_token}"  # 학교 공간의 대상 아닌 사람에게는 받는 링크를 싣지 않는다
        x = {"kind": "due", "c": c, "href": href, "who": by,
             "meta": "내가 걷는 것" if mine else f"내가 낼 것 · {by}",
             "day": _d(at.date()), "today": at.date() == today, "ch": "collect" if channeled else ""}
        if list_first <= at.date() <= list_last and (mine or row or not space.is_school):
            dues.append(x)
        where = _collect_tab(space, c)
        if (channeled and where == "school") or (tab == "group" and isinstance(where, list) and me.group in where):
            cells[(at.date(), "collect", "")].append(x)  # 마감은 날짜만(종일)

    n_items = sum(len(v) for v in cells.values())

    def by_slot(d, k):  # 한 칸 안: 종일 먼저, 그다음 블록 순
        return [x for s in ["", *SLOT_KEYS] for x in cells[(d, k, s)]]

    # 가로 줄: 채널(학교 탭·동학년) 또는 블록(그룹·개인 탭의 일간·주간). 그룹·개인 탭의 월간은 이름 없는 한 줄.
    if channeled:
        lines = [{"label": c["label"], "ch": c["k"], "slot": "", "scope": c["scope"], "post": c.get("post", ""), "go": c.get("go", ""),
                  "items": lambda d, k=c["k"]: by_slot(d, k)} for c in channels]
    elif mode == "month":
        lines = [{"label": "", "slot": "", "items": lambda d: by_slot(d, "collect") + by_slot(d, "")}]  # 취합 카드가 칸 맨 위
    else:
        lines = [*([{"label": "취합", "slot": "", "scope": "", "go": f"/s/{space.token}/c/new?aud=pergroup&aud_pgroups={me.group}&",
                     "items": lambda d: by_slot(d, "collect")}] if tab == "group" else []),
                 {"label": "종일", "slot": "", "items": lambda d: cells[(d, "", "")]},
                 *[{"label": s["label"], "slot": s["k"], "time": f"{s['from']}~" + ("" if s["k"] == "pm" else s["to"]),
                    "items": lambda d, k=s["k"]: cells[(d, "", k)]} for s in SLOTS]]

    def cell(d, line):
        scope = line.get("scope", tab_scope)
        name = line["label"] or "일정"
        return {"items": line["items"](d), "today": d == today, "date": d.isoformat(), "slot": line["slot"], "scope": scope,
                "post": line.get("post", ""), "when": f"{_d(d)} · {name}",
                "go": f"{line['go']}due_d={d.isoformat()}" if line.get("go") else "",
                "add": f"{_d(d)} 복무 추가" if line.get("post") else f"{_d(d)} 마감 취합 만들기" if line.get("go") else f"{_d(d)} " + (f"{line['label']}에 " if line["label"] else "") + "일정 추가"}

    if mode == "month":
        mweeks = []
        for i in range(0, len(days), 7):
            week = days[i:i + 7]
            flags = [{"n": d.day, "out": not list_first <= d <= list_last, "today": d == today, "weekend": d.weekday() >= 5} for d in week]
            mweeks.append({"heads": flags, "rows": [{"ch": ln.get("ch", ""), "label": ln["label"],
                                                    "cells": [{**f, **cell(d, ln)} for d, f in zip(week, flags)]} for ln in lines]})
        board = {"mweeks": mweeks, "dow": MONTH_DOW}
    else:
        wrows = [{"label": ln["label"], "ch": ln.get("ch", ""), "time": ln.get("time", ""), "cells": [cell(d, ln) for d in days]} for ln in lines]
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
    return {"mode": mode, "w": v["w"].isoformat(), "heading": v["heading"], "label": v["label"], "is_this": v["is_this"],
            "prev_w": v["prev_w"].isoformat(), "this_w": v["this_w"].isoformat(), "next_w": v["next_w"].isoformat(),
            "nav_words": v["nav_words"], "period": v["period"], "period_short": v.get("period_short", v["period"]),
            "focus": v["focus"].isoformat(), "print_view": "month" if mode == "month" else "week",
            "paste_w": (v["focus"] if mode == "month" else v["w"]).isoformat(),
            "tabs": tab_list, "tab": tab, "tab_label": tab_label, "channeled": channeled,
            "board_title": tab_label if tab != "school" else space.label + ("" if space.is_school else " · 학교"),
            "n_items": n_items, "dialogs": dialogs, "duty_dialogs": duty_dialogs,
            "neis_empty": channeled and not seen, "neis_fail": channeled and neis.failed(sched),
            "chg": chg, "dues": dues, "slot_choices": SLOTS, "filter_channels": channels, **board}


def _set_cookies(request, r):
    secure = settings.SITE_URL.startswith("https://")
    if request.GET.get("mode") in MODES:
        r.set_cookie(MODE_COOKIE, request.GET["mode"], max_age=MODE_AGE, httponly=True, samesite="Lax", secure=secure)
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
    """표 붙여넣기(week_paste) 뒤 돌아온 주소의 added·same·weekend → 한 줄 알림. 주소를 고쳐도 500이 나지 않게 짧은 숫자만 받는다."""
    n = [request.GET.get(k, "") for k in ("added", "same", "weekend")]
    if not all(x.isdecimal() and len(x) <= 4 for x in n):
        return ""
    added, same, weekend = map(int, n)
    return (f"주간계획 {added}건을 넣었어요." + (f" 이미 있던 {same}건은 건너뛰었어요." if same else "")
            + (f" 주말 {weekend}건은 월중행사표에만 나와요." if weekend else ""))


def reply(request, toast):
    """G1 쓰기 뷰가 view=week로 불렸을 때: 지금 탭·모드·날짜의 판을 다시 그린다."""
    ctx = _space_ctx(request, page="schedule", toast=toast, oob=True, **board_ctx(request, read_mode(request), request.POST.get("w")))
    return render(request, "today/_week.html", ctx)


def back_url(request):
    """htmx 없이 저장했을 때 돌아갈 주소: 지금 탭·모드·날짜."""
    mode = read_mode(request)
    w = VIEWS[mode](request.POST.get("w"), timezone.localdate())["w"]
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
@school_write_required
def duty_delete(request, key):
    a = get_object_or_404(Attendance.objects.in_space(request.space), key=key)
    if not _duty_deletable(a, request.member):
        return HttpResponseForbidden("복무 지우기는 넣은 선생님·본인·관리자·복무 입력자만 할 수 있어요.")
    title = a.title
    a.delete()
    return _done(request, f"복무 「{title}」 지웠어요.")
