"""G1 간이 '오늘'. 모습은 design/today.html, 동작은 prototype/assets/app.js today()·한 줄 입력(TR.parse·TR.conflicts).

- 오늘 일과: 블록(prototype 기본 시간표) × 동학년 일정, NEIS 학사일정·점심(메뉴·알레르기·칼로리), 시간 미정.
- 동학년 바뀐 것: 어제부터 올리거나 바꾼 일정(오늘 이후 것만), 누가 무엇을 바꿨는지.
- 내가 낼 것·내가 걷는 것: collect.lists의 판정을 그대로 쓴다. 학교 공간은 확인 필수 안 본 공지도 함께(notices.seen, I2).
- 학교 공간(H5): 일정 대상(models.scope), 고치기·지우기는 쓴 사람과 관리자만, 나에게 온 것 / 학교 전체 토글.
- 한 줄 입력은 서버가 파싱한다(today.parse). 미리보기(칩·장소 충돌)도 서버가 그려 htmx로 바꾼다.
조회는 모두 in_space()를 거친다. 일정 주소는 key만 쓴다.
"""
import unicodedata
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.access import can_open
from collect.lists import _colors, _past, _sent_item, _todo_items, _urgent
from collect.models import Collection
from collect.submit import _head, unit_rows
from collect.views import _due_text
from notices import seen as notice_seen
from notices.models import Notice
from spaces import neis
from spaces.models import Attendance, Member
from spaces.views import _josa, _space_ctx

from . import schedule
from .models import ALL, ME, Event, my_range_q, reachable_q, scope_choices
from .parse import ROOMS, SLOT_KEYS, SLOTS, parse, slot_idx, slot_text

WD = "월화수목금토일"
# meal.js M.allergens — NEIS 알레르기 번호 1~19
ALLERGENS = ["난류", "우유", "메밀", "땅콩", "대두", "밀", "고등어", "게", "새우", "돼지고기", "복숭아", "토마토", "아황산류",
             "호두", "닭고기", "쇠고기", "오징어", "조개류", "잣"]
LINE_MAX = 200
PLACE_MAX = 40


def _d(d):
    """TR.fmt: 10/30(금)."""
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


def _dates(e_date, e_end):
    return _d(e_date) + (f"~{_d(e_end)}" if e_end and e_end != e_date else "")


def _clean(raw, limit):
    s = "".join(ch for ch in str(raw or "") if unicodedata.category(ch) not in {"Cc", "Cf", "Zl", "Zp"})
    return " ".join(s.split())[:limit]


def _on_day(qs, d):
    return qs.filter(date__lte=d).filter(Q(end__gte=d) | Q(end__isnull=True, date=d))


def _clash(pool, d, end, s, e, exclude=None):
    """pool(같은 장소, 블록 있는 일정) 중 날짜와 블록이 겹치는 것."""
    a, b = slot_idx(s), slot_idx(e or s)
    return [x for x in pool if x != exclude and x.date <= (end or d) and x.last_day >= d
            and slot_idx(x.slot) <= b and a <= slot_idx(x.slot_end or x.slot)]


def _conflicts(space, d, end, s, e, place):
    """TR.conflicts: 같은 장소, 날짜가 겹치고 블록이 겹치는 일정."""
    if not (place and d and s):
        return []
    qs = (Event.objects.in_space(space).filter(place=place).exclude(slot="").select_related("by")
          .filter(date__lte=end or d).filter(Q(end__gte=d) | Q(end__isnull=True, date__gte=d)))
    return _clash(qs, d, end, s, e)


def week_conflicts(space, evs):
    """일정 여러 건의 겹침을 쿼리 한 번으로: {일정 pk: 겹치는 일정들}."""
    evs = [x for x in evs if x.place and x.slot]
    if not evs:
        return {}
    pool = {}
    first, last = min(x.date for x in evs), max(x.last_day for x in evs)
    for x in (Event.objects.in_space(space).filter(place__in={e.place for e in evs}, date__lte=last).exclude(slot="")
              .filter(Q(end__gte=first) | Q(end__isnull=True, date__gte=first)).select_related("by")):
        pool.setdefault(x.place, []).append(x)
    return {x.pk: _clash(pool[x.place], x.date, x.end, x.slot, x.slot_end, exclude=x) for x in evs}


def _now_slot(now):
    """지금 블록과 블록 안 진행(%)."""
    if now.weekday() >= 5:
        return None, None
    hm = f"{now:%H:%M}"
    for s in SLOTS:
        if s["from"] <= hm < s["to"]:
            if s["k"] == "pm":
                return s, None
            m = lambda x: int(x[:2]) * 60 + int(x[3:])  # noqa: E731
            return s, round((m(hm) - m(s["from"])) / (m(s["to"]) - m(s["from"])) * 100)
    return None, None


def _ev_item(e, me, today):
    multi_day = e.end and e.end != e.date
    meta = [_dates(e.date, e.end) if multi_day else "",
            slot_text(e.slot, e.slot_end) if e.slot and e.slot_end and e.slot_end != e.slot else "",
            e.place, e.target, e.scope_label if me.space.is_school else "", e.by.name if e.by else ""]
    return {"e": e, "mine": e.by_id == me.pk, "meta": " · ".join(x for x in meta if x), "can_write": e.writable_by(me),
            "slot_label": slot_text(e.slot, e.slot_end) if e.slot else "시간 미정", "dates": _dates(e.date, e.end)}


def _feed(space, me, now, colors):
    today = timezone.localdate(now)
    since = datetime.combine(today - timedelta(days=1), time(0), tzinfo=timezone.get_current_timezone())
    qs = (Event.objects.in_space(space).filter(my_range_q(me), updated_at__gte=since)
          .filter(Q(end__gte=today) | Q(end__isnull=True, date__gte=today))
          .select_related("by", "changed_by").order_by("-updated_at")[:6])
    out = []
    for e in qs:
        at = timezone.localtime(e.updated_at)
        day = "오늘" if at.date() == today else "어제" if at.date() == today - timedelta(days=1) else _d(at.date())
        who = e.last_by
        color, ink = colors.get(who.pk if who else None, ("var(--ink)", False))
        changes = [(w + _josa(w, "을", "를"), a, b) for w, a, b in (e.changed_from or [])]
        out.append({"e": e, "who": who.name if who else "누군가", "color": color, "ink": ink, "changes": changes,
                    "ro": _josa(changes[-1][2], "으로", "로") if changes else "",
                    "eul": _josa(e.title, "을", "를"),
                    "when_ev": _dates(e.date, e.end) + " " + (slot_text(e.slot, e.slot_end) if e.slot else "시간 미정"),
                    "at": f"{day} {at:%H:%M}", "new": (now - e.updated_at) < timedelta(minutes=1), "ts": e.updated_at})
    if space.is_school:  # 실질 변경된 공지(대상이고 앞 판을 본 사람에게만)
        for x in notice_seen.change_feed(space, me, since):
            at = timezone.localtime(x["ver"].at)
            day = "오늘" if at.date() == today else "어제" if at.date() == today - timedelta(days=1) else _d(at.date())
            n = x["ver"].notice
            out.append({"notice": n, "who": x["ver"].by, "color": "var(--ink)", "ink": False, "summary": x["summary"],
                        "eul": _josa(n.title, "을", "를"), "at": f"{day} {at:%H:%M}", "new": False, "ts": x["ver"].at})
        out = sorted(out, key=lambda f: f["ts"], reverse=True)[:6]
    return out


def _meal(space, today):
    ymd = f"{today:%Y%m%d}"
    rows = neis.meals(space.school.neis_atpt, space.school.neis_code, ymd, ymd)
    if not rows:
        return {"fail": True} if neis.failed(rows) else None
    m = rows[0]
    nums = sorted({n for d in m["dishes"] for n in d["allergy"]})
    return {"dishes": [d["name"] for d in m["dishes"]], "kcal": m["kcal"],
            "allergy": [f"{n}.{ALLERGENS[n - 1]}" for n in nums]}


def _live_ctx(request, now):
    """오늘 일과 + 동학년 바뀐 것(htmx로 다시 그리는 부분)."""
    space, me = request.space, request.member
    today = timezone.localdate(now)
    events = [_ev_item(e, me, today) for e in
              _on_day(Event.objects.in_space(space).filter(my_range_q(me)), today)
              .select_related("by", "changed_by").order_by("slot", "created_at")]
    now_slot, pct = _now_slot(timezone.localtime(now))
    cols = [{**s, "now": now_slot is not None and s["k"] == now_slot["k"],
             "time": f"{s['from']}~{'' if s['k'] == 'pm' else s['to']}",
             "events": [x for x in events if x["e"].slot == s["k"]]} for s in SLOTS]
    sched = neis.schedule(space.school.neis_atpt, space.school.neis_code, f"{today:%Y%m%d}", f"{today:%Y%m%d}")
    return {"cols": cols, "now_slot": now_slot, "pct": pct, "now_hm": f"{timezone.localtime(now):%H:%M}",
            "events": events, "untimed": [x for x in events if not x["e"].slot],
            "n_events": len(events), "meal": _meal(space, today),
            "neis_names": [x["name"] for x in sched], "neis_fail": neis.failed(sched),
            "feed": _feed(space, me, now, _colors(space)), "slot_choices": SLOTS, "today_iso": today.isoformat(),
            "scope_choices": scope_choices(me)}


def _school_wide(request, now):
    """학교 전체 토글: 진행 중인 취합의 제목·마감·진행 수만(prototype schoolWideHtml, 마감순). 받는 링크와 명단은 싣지 않는다.
    상세 링크는 열 수 있는 사람(만든 사람·관리자)에게만."""
    cs = list(Collection.objects.in_space(request.space).filter(closed_at__isnull=True).filter(Q(due__isnull=True) | Q(due__gt=now))
              .select_related("space__school", "created_by"))
    rows = unit_rows(cs)
    far = now + timedelta(days=36500)
    out = []
    for c in sorted(cs, key=lambda c: (c.due or far, c.created_at)):
        h = _head(c, rows[c.pk])
        out.append({"title": c.title, "due": _due_text(c), "urgent": _urgent(c, now), "n_done": h["n_done"], "n_total": h["n_total"],
                    "per": h["per"], "pct": h["pct"], "all": h["n_total"] > 0 and h["n_done"] == h["n_total"],
                    "href": f"/s/{request.space.token}/c/{c.key}/" if can_open(request, c.created_by_id) else ""})
    return out


def _notices_wide(request):
    """학교 전체 토글의 확인 필수 공지: 내게 보이는 것만 제목·쓴 사람·수정 시각. 비율은 쓴 사람·관리자에게만."""
    me = request.member
    rows = list(Notice.objects.in_space(request.space).visible_to(me).filter(must=True).order_by("-updated_at", "-pk")[:10])
    stats = notice_seen.statuses(request.space, [n for n in rows if notice_seen.can_see_status(n, me)])
    acked = notice_seen.acked_ids(me, rows)
    return [{"n": n, "stat": stats.get(n.pk), "acked": n.pk in acked} for n in rows]


def _page_ctx(request):
    now = timezone.now()
    local = timezone.localtime(now)
    todo = _todo_items(request, now)
    colors = _colors(request.space)
    mine = (Collection.objects.in_space(request.space).filter(created_by=request.member)
            .select_related("space__school", "created_by").order_by("-created_at"))
    sent = [_sent_item(c, now, colors) for c in [c for c in mine if not _past(c, now)][:3]]
    wide = request.space.is_school and request.GET.get("show") == "school"
    must = list(notice_seen.must_todo(request.space, request.member)) if request.space.is_school else []
    return _space_ctx(request, page="today", quick=True, todo=todo[:4], n_todo=len(todo),
                      must_list=must[:4], n_must=len(must), n_mine=len(todo) + len(must),
                      show_school=wide, school_wide=_school_wide(request, now) if wide else [],
                      notices_wide=_notices_wide(request) if wide else [],
                      attendance_wide=list(Attendance.objects.in_space(request.space).filter(date=local.date()).select_related("member").order_by("pk")) if wide else [],
                      urgent=next((x for x in todo if x["urgent"]), None), sent=sent,
                      date_label=f"{local.month}월 {local.day}일 {WD[local.weekday()]}요일",
                      weekend=local.weekday() >= 5, first=SLOTS[0], **_live_ctx(request, now))


@never_cache
@require_GET
def index(request):
    return render(request, "today/index.html", _page_ctx(request))


def _live(request, toast, status=200):
    if request.POST.get("view") == "week":  # 일정 화면에서 불렀으면 일정 판으로 돌려준다
        return schedule.reply(request, toast)
    ctx = _space_ctx(request, toast=toast, oob=True, **_live_ctx(request, timezone.now()))
    return render(request, "today/_live.html", ctx, status=status)


def _read_line(request):
    """(파싱 결과, 오류). 날짜를 안 쓰면 오늘(prototype qParsed)."""
    line = _clean(request.POST.get("line", ""), LINE_MAX + 1)
    if not line:
        return None, ""
    if len(line) > LINE_MAX:
        return None, f"{LINE_MAX}자 안으로 써 주세요."
    today = timezone.localdate()
    p = parse(line, today)
    p["date_given"] = p["date"] is not None
    p["date"] = p["date"] or today
    if not p["title"]:
        return None, "일정 이름도 함께 써 주세요. 예: 내일 2블록 과학실 안전교육"
    # 참석자(@이름)는 R1 모델에 칸이 없어 대상 뒤에 붙여 남긴다(prototype은 복무 외에는 버림).
    p["target"] = " · ".join(([p["target"]] if p["target"] else []) + [f"@{w}" for w in p["who"]])[:80]
    p["title"] = p["title"][:200]
    return p, None


def _read_cell(request):
    """일정 칸의 날짜·블록(칸 안 추가). 잘못된 값이면 (None, "")."""
    raw = request.POST.get("date", "")
    try:
        d = parse_date(raw) if len(raw) == 10 else None
    except ValueError:
        d = None
    slot = request.POST.get("slot", "")
    return (d, slot) if d and slot in ["", *SLOT_KEYS] else (None, "")


def _read_scope(request):
    """한 줄 입력의 볼 사람. 동학년은 언제나 모두, 학교 공간은 고른 값(없거나 틀리면 내 그룹, 그룹이 없으면 나)."""
    me = request.member
    if not request.space.is_school:
        return ALL
    raw = request.POST.get("scope")
    return raw if _scope_ok(raw) else (me.group or ME)


def _scope_ok(raw):
    return raw in (ALL, ME, *Member.Group.values)


def _conf_text(p, conf, me):
    """장소 겹침은 학교 전체에서 보되, 볼 수 없는 일정은 제목·이름을 말하지 않는다."""
    return f"{p['place']}{_josa(p['place'], '이', '가')} 겹쳐요: " + ", ".join(
        (f"{c.title} ({slot_text(c.slot, c.slot_end)}, {c.by.name if c.by else '알 수 없음'})" if c.reachable_by(me)
         else f"다른 일정 ({slot_text(c.slot, c.slot_end)})") for c in conf)


@never_cache
@require_POST
def preview(request):
    """한 줄 입력 미리보기: 칩(날짜·시간·대상·장소·참석)과 장소 충돌."""
    p, error = _read_line(request)
    if p is None and not error:
        return render(request, "today/_qpop.html", {"empty": True})
    ctx = {"error": error}
    if p:
        conf = _conflicts(request.space, p["date"], p["end"], p["s"], p["e"], p["place"])
        ctx.update(p=p, dates=_dates(p["date"], p["end"]), slot=slot_text(p["s"], p["e"]) if p["s"] else "",
                   conf=_conf_text(p, conf, request.member) if conf else "", n_rooms=len(ROOMS))
    return render(request, "today/_qpop.html", ctx)


@never_cache
@require_POST
def add(request):
    week = request.POST.get("view") == "week"
    cell = week and "date" in request.POST  # 일정 칸 안 추가: 날짜·블록은 칸이 이긴다
    if cell:
        cell_date, cell_slot = _read_cell(request)
        if cell_date is None:
            return HttpResponseBadRequest("칸의 날짜·블록을 다시 확인해 주세요.")
    p, error = _read_line(request)
    if p is None:
        r = render(request, "today/_cell_err.html" if week else "today/_qpop.html",
                   {"error": error or "일정을 한 줄로 써 주세요. 예: 내일 2블록 과학실 안전교육"})
        r["HX-Retarget"], r["HX-Reswap"] = "#cellErr" if week else "#qpop", "outerHTML"
        return r
    if cell:
        p.update(date=cell_date, end=None, s=cell_slot or None, e=cell_slot or None)
    conf = _conflicts(request.space, p["date"], p["end"], p["s"], p["e"], p["place"])
    e = Event.objects.create(space=request.space, by=request.member, date=p["date"], end=p["end"],
                             slot=p["s"] or "", slot_end=p["e"] or "", title=p["title"],
                             place=(p["place"] or "")[:PLACE_MAX], target=p["target"], scope=_read_scope(request))
    if not request.headers.get("HX-Request"):
        r = redirect(schedule.back_url(request) if week else f"/s/{request.space.token}/today")
        r.status_code = 303
        return r
    toast = f"{_dates(e.date, e.end)} {slot_text(e.slot, e.slot_end) if e.slot else '시간 미정'} 「{e.title}」 올렸어요"
    if conf:
        toast += " · " + _conf_text(p, conf, request.member)
    return _live(request, toast)


def _writable_event(request, key):
    """(일정, 거절 응답). 볼 수 없는 일정은 404, 보이지만 내가 쓴 게 아니면 403(쓴 사람과 관리자만)."""
    e = get_object_or_404(Event.objects.in_space(request.space).filter(reachable_q(request.member)), key=key)
    if not e.writable_by(request.member):
        return e, HttpResponseForbidden("고치기·지우기는 쓴 선생님과 관리자만 할 수 있어요.")
    return e, None


@never_cache
@require_POST
def edit(request, key):
    """날짜·블록·장소 바꾸기(prototype setWhen). 이미 정한 값을 바꾸면 '바뀐 것'으로 남기고 바꾼 사람을 by에."""
    e, denied = _writable_event(request, key)
    if denied:
        return denied
    raw = request.POST.get("date", "")
    try:  # 형식은 맞아도 없는 날짜(2026-02-30)는 ValueError, 기간 끝을 옮기다 9999년을 넘으면 OverflowError
        d = parse_date(raw) if len(raw) == 10 else None
        end = e.end + (d - e.date) if d and e.end else None
    except (ValueError, OverflowError):
        d = None
    slot = request.POST.get("slot", "")
    place = _clean(request.POST.get("place", ""), PLACE_MAX + 1)
    scope = request.POST.get("scope", e.scope) if request.space.is_school else e.scope
    if d is None or slot not in ["", *SLOT_KEYS] or len(place) > PLACE_MAX or not _scope_ok(scope):
        return HttpResponseBadRequest("날짜·블록·장소를 다시 확인해 주세요. 날짜는 달력에서 골라 주세요.")
    b_dates, b_slot, b_place = _dates(e.date, e.end), slot_text(e.slot, e.slot_end) if e.slot else None, e.place
    e.date, e.end = d, end
    if slot != e.slot:
        e.slot, e.slot_end = slot, slot
    e.place = place
    a_dates, a_slot = _dates(e.date, e.end), slot_text(e.slot, e.slot_end) if e.slot else "시간 미정"
    parts = []
    if b_dates != a_dates:
        parts.append(["기간" if e.end else "날짜", b_dates, a_dates])
    if b_slot and b_slot != a_slot:
        parts.append(["시간", b_slot, a_slot])
    if b_place and b_place != place:
        parts.append(["장소", b_place, place or "없음"])
    if (b_dates, b_slot or "시간 미정", b_place, e.scope) == (a_dates, a_slot, place, scope):
        return _live(request, "바뀐 것이 없어요.")
    e.scope = scope
    if scope == ME and e.by_id is None:  # 쓴 사람 없는 '나' 일정은 아무도 못 보므로 저장하는 사람이 쓴 사람이 된다
        e.by = request.member
    if parts:
        e.changed_from = parts
        if request.space.is_school:  # 쓴 사람(by)은 권한 근거라 그대로 둔다
            e.changed_by = request.member
        else:
            e.by = request.member
    e.save()
    return _live(request, f"「{e.title}」 {a_dates} {a_slot}" + (f" · {request.space.word} 「바뀐 것」에 떠요" if parts else ""))


@never_cache
@require_POST
def delete(request, key):
    e, denied = _writable_event(request, key)
    if denied:
        return denied
    title = e.title
    e.delete()
    return _live(request, f"「{title}」 일정을 지웠어요.")
