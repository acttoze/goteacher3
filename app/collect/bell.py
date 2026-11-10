"""D3 자동 리마인드와 🔔(앱 안 알림 목록).

remind(): 서버 cron이 5분마다 `manage.py remind`로 부른다. 보낼 때가 된 것만, 한 번씩만 만든다.
- 마감 전날 08:20 이후 첫 실행 → D-1, 마감 당일 08:20 이후 첫 실행 → 당일(마감 시각 전까지).
- 대상: 잠그지 않은 취합의 아직 안 낸 줄(submit._units)의 회원. 그룹 칸은 그 그룹 구성원마다. 명부 밖 줄(회원 없음)은 뺀다(합본에 표시).
- 중복은 DB 유일 조건(줄·회원·종류·날짜)이 막는다. 웹 푸시 묶음(08:20·12:20·방과후)은 G2 collect.push.
- 학교 플랜 만료 안내(O1): 기한 30일·7일 안에 들면 관리자마다 한 번(cron이 그날 멈췄어도 다음 날 보낸다). 🔔만.
"""
from datetime import time, timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from spaces.models import Member, School, Space

from .models import Collection, Notification
from .push import next_at, off_keys
from .submit import _units

SEND_AT = time(8, 20)
PLAN_STAGES = (7, 30)  # 만료 며칠 전 안내(가까운 단계부터 본다)


def remind(now=None):
    """지금 보낼 리마인드를 만들고, 새로 만든 수를 돌려준다."""
    now = timezone.localtime(now or timezone.now())
    if now.time() < SEND_AT:
        return 0
    today = now.date()
    plan = {today: Notification.Kind.D0, today + timedelta(days=1): Notification.Kind.D1}
    new = []
    for c in Collection.objects.filter(closed_at__isnull=True, due__gt=now, due__date__in=list(plan)):
        kind = plan[timezone.localtime(c.due).date()]
        text = f"{c.title} — {'오늘' if kind == Notification.Kind.D0 else '내일'} 마감"
        rows = [u["row"] for u in _units(c) if not u["done"]]
        people = dict(Member.objects.in_space(c.space).values_list("pk", "group"))
        for r in rows:
            who = [pk for pk, g in people.items() if g == r.group] if r.group else [r.member_id] if r.member_id in people else []
            new += [Notification(space_id=c.space_id, member_id=pk, row=r, kind=kind, day=today, text=text,
                                 link=f"/c/{c.link_token}/u/{r.key}") for pk in who]
    new += _plan_notices(today)
    before = Notification.objects.count()
    Notification.objects.bulk_create(new, ignore_conflicts=True)
    return Notification.objects.count() - before


def _plan_notices(today):
    """기한이 가까운 학교 플랜·기관(체험 포함, T1)의 관리자 🔔. 문구에 단계와 기한이 들어 있어 유일 조건이 단계마다 한 번으로 막는다(기한을 늘리면 새로)."""
    out = []
    hubs = Space.objects.filter(kind__in=[Space.Kind.SCHOOL, Space.Kind.ORG], school__plan=School.Plan.SCHOOL, school__paid_until__gte=today,
                                school__paid_until__lte=today + timedelta(days=PLAN_STAGES[-1])).select_related("school")
    for hub in hubs:
        until = hub.school.paid_until
        stage = next(s for s in PLAN_STAGES if (until - today).days <= s)
        if hub.school.org_unconfirmed:  # 기관 체험(T1): 끝나기 7일 전에만(체험을 연 날 바로 뜨지 않게), 신청 화면으로
            if stage != PLAN_STAGES[0]:
                continue
            text = f"기관 공간 체험이 {stage}일 안에 끝나요({until:%Y-%m-%d}까지). 이어 쓰려면 기관 플랜을 신청해 주세요."
        elif hub.is_org:
            text = f"기관 플랜이 {stage}일 안에 끝나요({until:%Y-%m-%d}까지). 이어 쓰려면 다시 신청해 주세요."
        else:
            text = f"학교 플랜이 {stage}일 안에 끝나요({until:%Y-%m-%d}까지). 이어 쓰려면 다시 신청해 주세요."
        out += [Notification(space=hub, member=m, kind=Notification.Kind.PLAN, day=today, text=text, link="/school-plan", pushed=True)
                for m in Member.objects.in_space(hub).filter(is_manager=True)]
    return out


def _mine(request):
    return Notification.objects.in_space(request.space).filter(member=request.member)


def unread(request):
    """컨텍스트 프로세서: 공통 틀(shell.html) 🔔의 안 읽은 수(쓰는 화면에서만 센다)와 다음 묶음 시각(G2)."""
    if getattr(request, "member", None) is None:
        return {}
    return {"bell_n": SimpleLazyObject(lambda: _mine(request).filter(read=False).count()),
            "bell_next": next_at(timezone.now(), off_keys(request.member))}


@never_cache
@require_GET
def index(request):
    """🔔 목록(htmx 부분 뷰). 내 알림만, 최근 20건."""
    mine = _mine(request)
    return render(request, "collect/_bell.html", {"space": request.space, "items": mine[:20],
                                                  "unread_n": mine.filter(read=False).count(),
                                                  "vapid": settings.VAPID_PUBLIC_KEY})


@never_cache
@require_POST
def read(request, key):
    """누르면 읽음으로 하고 그 화면(받는 사람 제출 화면)으로 간다. 남의 알림·다른 공간이면 404."""
    n = get_object_or_404(_mine(request), key=key)
    if not n.read:
        n.read = True
        n.save(update_fields=["read"])
    return redirect(n.link)
