"""V10 고객 분석 집계(화면 /analytics·manage.py analytics가 같이 쓴다).

대상: 이관 학교(LegacyMap kind=school)의 학교 공간에 자리가 있는 계정 회원. 손님·운영자(is_staff)는 뺀다.
사람 이름은 내지 않는다(학교·소속까지만). 쿼리는 사람·날짜·화면별 집계로 스무 개 안팎.
"""
from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Count, Max, Min, Sum
from django.db.models.functions import ExtractHour
from django.utils import timezone

from accounts.models import GUEST_PREFIX
from feedback.models import Feedback
from legacy.models import LegacyMap
from spaces.models import SCHOOL_GROUPS, Member, Space

from .models import DailyUse, Entry

ROUTE_NAMES = {
    "/s/<공간>/today": "오늘", "/s/<공간>/schedule": "일정", "/s/<공간>/c/": "취합 목록", "/s/<공간>/c/new": "취합 만들기",
    "/s/<공간>/c/<key>/": "취합 보기", "/s/<공간>/board": "게시판", "/s/<공간>/board/<key>/": "공지 보기", "/s/<공간>/board/new": "공지 쓰기",
    "/s/<공간>/school": "학교", "/s/<공간>/school/attendance": "복무", "/s/<공간>/settings": "설정", "/s/<공간>/meals": "급식",
    "/s/<공간>/meals/screens": "급식소", "/s/<공간>/it": "정보수리방", "/s/<공간>/repairs": "수리 요청", "/s/<공간>/": "동학년",
    "/help": "도움말", "/forms": "양식 자료실", "/forms/<slug>": "양식 보기", "/partners": "협력업체", "/community": "커뮤니티",
    "/community/<key>": "커뮤니티 글",
}
TOP_ROUTES = 15
RECENT = 10


def _pct(a, b):
    return round(a * 100 / b) if b else 0


def _bar(v, top):
    return round(v * 100 / top) if top else 0


def build(days):
    """days: 1·7·30 같은 최근 일수(오늘 포함), None이면 기록 시작부터."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1) if days else None
    since = timezone.make_aware(datetime.combine(start, time.min)) if start else None

    spaces = list(Space.objects.filter(kind=Space.Kind.SCHOOL, school_id__in=LegacyMap.objects.filter(kind="school").values("obj_id")))
    seat_qs = Member.objects.filter(space__in=spaces, user__isnull=False, user__is_staff=False).exclude(user__username__startswith=GUEST_PREFIX)
    seats = list(seat_qs.values_list("space_id", "user_id", "group"))
    targets = {u for _, u, _ in seats}
    target_q = seat_qs.values("user_id")

    uses = DailyUse.objects.filter(user_id__in=target_q)
    people = {r["user_id"]: r for r in uses.values("user_id").annotate(first=Min("day"), last=Max("day"), days=Count("day", distinct=True))}
    entered = set(people)
    again = {u for u in entered if people[u]["days"] >= 2}
    period = uses.filter(day__gte=start) if start else uses
    active = set(period.values_list("user_id", flat=True).distinct())

    fb = Feedback.objects.filter(space__in=spaces)
    fbp = fb.filter(created_at__gte=since) if since else fb
    by_space_kind = list(fbp.values("space_id", "kind").annotate(n=Count("id")))
    kinds = Counter()
    for r in by_space_kind:
        kinds[r["kind"]] += r["n"]
    said = set(fb.exclude(kind__startswith="back_").filter(member__user_id__in=target_q).values_list("member__user_id", flat=True).distinct())
    ent = Entry.objects.filter(at__gte=since) if since else Entry.objects.all()
    entry_n = Counter()
    for r in ent.values("ok", "reason").annotate(n=Count("id")):
        entry_n["ok" if r["ok"] else r["reason"]] += r["n"]
    fails = sum(n for k, n in entry_n.items() if k not in ("ok", Entry.Reason.SWITCH_SHOWN))  # 실패는 계정을 몰라 전체로 센다
    ent_ok = ent.filter(ok=True, user_id__in=target_q)  # 성공·시간대는 다른 타일처럼 대상 회원만
    ok_n = ent_ok.count()

    feedback_n = sum(n for k, n in kinds.items() if not k.startswith("back_"))
    back_n = sum(n for k, n in kinds.items() if k.startswith("back_"))
    tiles = [("대상 회원", len(targets), "이관 학교 계정 회원"), ("들어와 본 사람", len(entered), f"대상의 {_pct(len(entered), len(targets))}% · 누적"),
             ("기간 이용자", len(active), "이 기간에 한 번 이상"), ("다시 온 사람", len(again), "2일 이상 이용 · 누적"),
             ("받은 의견", feedback_n, "이 기간"), ("예전 고티처로", back_n, "돌아감 · 이 기간"),
             ("입장 실패", fails, f"전체 기준 · 대상 성공 {ok_n}번 · 이 기간")]
    funnel = [(label, n, _pct(n, len(targets))) for label, n in
              (("대상 회원", len(targets)), ("들어와 봄", len(entered)), ("다시 옴", len(again)), ("의견 남김", len(said)))]

    # 날짜별: 하루 이용자와 그날 처음 온 사람
    first_days = Counter(people[u]["first"] for u in entered)
    daily = dict(period.values("day").annotate(n=Count("user", distinct=True)).values_list("day", "n"))
    d0 = start or min(first_days, default=today)
    day_list = [d0 + timedelta(days=i) for i in range((today - d0).days + 1)]
    top = max([daily.get(d, 0) for d in day_list] + [1])
    trend = [{"day": d, "n": daily.get(d, 0), "new": first_days.get(d, 0), "h": _bar(daily.get(d, 0), top),
              "hn": _bar(first_days.get(d, 0), top)} for d in day_list]

    hours = dict(ent_ok.annotate(h=ExtractHour("at")).values("h").annotate(n=Count("id")).values_list("h", "n"))
    top_h = max(list(hours.values()) + [1])
    by_hour = [{"h": h, "n": hours.get(h, 0), "bar": _bar(hours.get(h, 0), top_h)} for h in range(24)]

    # 학교별·소속별: 자리(공간·사람·소속) 기준
    school_rows = []
    fb_school = Counter()
    back_school = Counter()
    for r in by_space_kind:
        (back_school if r["kind"].startswith("back_") else fb_school)[r["space_id"]] += r["n"]
    names = {s.pk: s.name for s in spaces}
    per = {pk: {"name": name, "target": set(), "entered": set(), "active": set(), "last": None} for pk, name in names.items()}
    groups = {}
    for space_id, u, g in seats:
        row = per[space_id]
        row["target"].add(u)
        grow = groups.setdefault(g, {"label": SCHOOL_GROUPS.get(g, "소속 없음"), "target": 0, "active": 0})
        grow["target"] += 1
        if u in entered:
            row["entered"].add(u)
            last = people[u]["last"]
            row["last"] = max(row["last"] or last, last)
        if u in active:
            row["active"].add(u)
            grow["active"] += 1
    for pk, row in per.items():
        t, e = len(row["target"]), len(row["entered"])
        school_rows.append({"name": row["name"], "target": t, "entered": e, "pct": _pct(e, t), "active": len(row["active"]),
                            "feedback": fb_school[pk], "back": back_school[pk], "last": row["last"]})
    school_rows.sort(key=lambda r: (-r["pct"], -r["entered"], r["name"]))
    order = {k: i for i, k in enumerate(Member.Group.values)}
    group_rows = sorted(groups.items(), key=lambda kv: order.get(kv[0], len(order)))
    group_rows = [{**g, "pct": _pct(g["active"], g["target"])} for _, g in group_rows]

    routes = list(period.values("route").annotate(n=Sum("n"), people=Count("user", distinct=True)).order_by("-n", "route")[:TOP_ROUTES])
    top_r = max([r["n"] for r in routes] + [1])
    for r in routes:
        r["name"] = ROUTE_NAMES.get(r["route"], r["route"])
        r["bar"] = _bar(r["n"], top_r)

    kind_rows = [(Feedback.Kind(k).label, kinds[k]) for k in Feedback.Kind.values if kinds[k]]
    recent = list(fbp.select_related("space", "member").order_by("-created_at", "-pk")[:RECENT])
    return {"tiles": tiles, "funnel": funnel, "trend": trend, "by_hour": by_hour, "schools": school_rows, "groups": group_rows,
            "routes": routes, "kind_rows": kind_rows, "recent": recent, "start": start, "today": today, "axis_step": max(1, len(trend) // 6),
            "has_use": bool(people), "entries": sum(entry_n.values())}
