"""G1 간이 '오늘'. 모습은 design/today.html, 동작은 prototype/assets/app.js today()·한 줄 입력(TR.parse·TR.conflicts).

- 오늘 일과: 블록(prototype 기본 시간표) × 동학년 일정, NEIS 학사일정·점심(메뉴·알레르기·칼로리), 시간 미정.
- 동학년 바뀐 것: 어제부터 올리거나 바꾼 일정(오늘 이후 것만), 누가 무엇을 바꿨는지.
- 내가 낼 것·내가 걷는 것: collect.lists의 판정을 그대로 쓴다.
- 한 줄 입력은 서버가 파싱한다(today.parse). 미리보기(칩·장소 충돌)도 서버가 그려 htmx로 바꾼다.
조회는 모두 in_space()를 거친다. 일정 주소는 key만 쓴다.
"""
import unicodedata
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.lists import _colors, _past, _sent_item, _todo_items
from collect.models import Collection
from spaces import neis
from spaces.models import get_in_space_or_404
from spaces.views import _josa, _space_ctx

from .models import Event
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


def _conflicts(space, d, end, s, e, place, exclude=None):
    """TR.conflicts: 같은 장소, 날짜가 겹치고 블록이 겹치는 일정."""
    if not (place and d and s):
        return []
    a, b = slot_idx(s), slot_idx(e or s)
    qs = (Event.objects.in_space(space).filter(place=place).exclude(slot="").select_related("by")
          .filter(date__lte=end or d).filter(Q(end__gte=d) | Q(end__isnull=True, date__gte=d)))
    if exclude:
        qs = qs.exclude(pk=exclude.pk)
    return [x for x in qs if slot_idx(x.slot) <= b and a <= slot_idx(x.slot_end or x.slot)]


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
            e.place, e.target, e.by.name if e.by else ""]
    return {"e": e, "mine": e.by_id == me.pk, "meta": " · ".join(x for x in meta if x),
            "slot_label": slot_text(e.slot, e.slot_end) if e.slot else "시간 미정", "dates": _dates(e.date, e.end)}


def _feed(space, now, colors):
    today = timezone.localdate(now)
    since = datetime.combine(today - timedelta(days=1), time(0), tzinfo=timezone.get_current_timezone())
    qs = (Event.objects.in_space(space).filter(updated_at__gte=since)
          .filter(Q(end__gte=today) | Q(end__isnull=True, date__gte=today))
          .select_related("by").order_by("-updated_at")[:6])
    out = []
    for e in qs:
        at = timezone.localtime(e.updated_at)
        day = "오늘" if at.date() == today else "어제" if at.date() == today - timedelta(days=1) else _d(at.date())
        color, ink = colors.get(e.by_id, ("var(--ink)", False))
        changes = [(w + _josa(w, "을", "를"), a, b) for w, a, b in (e.changed_from or [])]
        out.append({"e": e, "who": e.by.name if e.by else "누군가", "color": color, "ink": ink, "changes": changes,
                    "ro": _josa(changes[-1][2], "으로", "로") if changes else "",
                    "eul": _josa(e.title, "을", "를"),
                    "when_ev": _dates(e.date, e.end) + " " + (slot_text(e.slot, e.slot_end) if e.slot else "시간 미정"),
                    "at": f"{day} {at:%H:%M}", "new": (now - e.updated_at) < timedelta(minutes=1)})
    return out


def _meal(space, today):
    ymd = f"{today:%Y%m%d}"
    rows = neis.meals(space.school.neis_atpt, space.school.neis_code, ymd, ymd)
    if not rows:
        return None
    m = rows[0]
    nums = sorted({n for d in m["dishes"] for n in d["allergy"]})
    return {"dishes": [d["name"] for d in m["dishes"]], "kcal": m["kcal"],
            "allergy": [f"{n}.{ALLERGENS[n - 1]}" for n in nums]}


def _live_ctx(request, now):
    """오늘 일과 + 동학년 바뀐 것(htmx로 다시 그리는 부분)."""
    space, me = request.space, request.member
    today = timezone.localdate(now)
    events = [_ev_item(e, me, today) for e in
              _on_day(Event.objects.in_space(space), today).select_related("by").order_by("slot", "created_at")]
    now_slot, pct = _now_slot(timezone.localtime(now))
    cols = [{**s, "now": now_slot is not None and s["k"] == now_slot["k"],
             "time": f"{s['from']}~{'' if s['k'] == 'pm' else s['to']}",
             "events": [x for x in events if x["e"].slot == s["k"]]} for s in SLOTS]
    sched = neis.schedule(space.school.neis_atpt, space.school.neis_code, f"{today:%Y%m%d}", f"{today:%Y%m%d}")
    return {"cols": cols, "now_slot": now_slot, "pct": pct, "now_hm": f"{timezone.localtime(now):%H:%M}",
            "events": events, "untimed": [x for x in events if not x["e"].slot],
            "n_events": len(events), "meal": _meal(space, today), "neis_names": [x["name"] for x in sched],
            "feed": _feed(space, now, _colors(space)), "slot_choices": SLOTS, "today_iso": today.isoformat()}


def _page_ctx(request):
    now = timezone.now()
    local = timezone.localtime(now)
    todo = _todo_items(request, now)
    colors = _colors(request.space)
    mine = (Collection.objects.in_space(request.space).filter(created_by=request.member)
            .select_related("space__school", "created_by").order_by("-created_at"))
    sent = [_sent_item(c, now, colors) for c in mine if not _past(c, now)][:3]
    return _space_ctx(request, page="today", quick=True, todo=todo[:4], n_todo=len(todo),
                      urgent=next((x for x in todo if x["urgent"]), None), sent=sent,
                      date_label=f"{local.month}월 {local.day}일 {WD[local.weekday()]}요일",
                      weekend=local.weekday() >= 5, first=SLOTS[0], **_live_ctx(request, now))


@never_cache
@require_GET
def index(request):
    return render(request, "today/index.html", _page_ctx(request))


def _live(request, toast, status=200):
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


def _conf_text(p, conf):
    return f"{p['place']}{_josa(p['place'], '이', '가')} 겹쳐요: " + ", ".join(
        f"{c.title} ({slot_text(c.slot, c.slot_end)}, {c.by.name if c.by else '알 수 없음'})" for c in conf)


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
                   conf=_conf_text(p, conf) if conf else "", n_rooms=len(ROOMS))
    return render(request, "today/_qpop.html", ctx)


@never_cache
@require_POST
def add(request):
    p, error = _read_line(request)
    if p is None:
        r = render(request, "today/_qpop.html", {"error": error or "일정을 한 줄로 써 주세요. 예: 내일 2블록 과학실 안전교육"})
        r["HX-Retarget"], r["HX-Reswap"] = "#qpop", "outerHTML"
        return r
    conf = _conflicts(request.space, p["date"], p["end"], p["s"], p["e"], p["place"])
    e = Event.objects.create(space=request.space, by=request.member, date=p["date"], end=p["end"],
                             slot=p["s"] or "", slot_end=p["e"] or "", title=p["title"],
                             place=(p["place"] or "")[:PLACE_MAX], target=p["target"])
    if not request.headers.get("HX-Request"):
        r = redirect(f"/s/{request.space.token}/today")
        r.status_code = 303
        return r
    toast = f"{_dates(e.date, e.end)} {slot_text(e.slot, e.slot_end) if e.slot else '시간 미정'} 「{e.title}」 올렸어요"
    if conf:
        toast += " · " + _conf_text(p, conf)
    return _live(request, toast)


@never_cache
@require_POST
def edit(request, key):
    """날짜·블록·장소 바꾸기(prototype setWhen). 이미 정한 값을 바꾸면 '바뀐 것'으로 남기고 바꾼 사람을 by에."""
    e = get_in_space_or_404(Event, request, key=key)
    raw = request.POST.get("date", "")
    try:  # 형식은 맞아도 없는 날짜(2026-02-30)는 ValueError, 기간 끝을 옮기다 9999년을 넘으면 OverflowError
        d = parse_date(raw) if len(raw) == 10 else None
        end = e.end + (d - e.date) if d and e.end else None
    except (ValueError, OverflowError):
        d = None
    slot = request.POST.get("slot", "")
    place = _clean(request.POST.get("place", ""), PLACE_MAX + 1)
    if d is None or slot not in ["", *SLOT_KEYS] or len(place) > PLACE_MAX:
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
    if (b_dates, b_slot or "시간 미정", b_place) == (a_dates, a_slot, place):
        return _live(request, "바뀐 것이 없어요.")
    if parts:
        e.changed_from, e.by = parts, request.member
    e.save()
    return _live(request, f"「{e.title}」 {a_dates} {a_slot}" + (" · 동학년 「바뀐 것」에 떠요" if parts else ""))


@never_cache
@require_POST
def delete(request, key):
    e = get_in_space_or_404(Event, request, key=key)
    title = e.title
    e.delete()
    return _live(request, f"「{title}」 일정을 지웠어요.")
