"""I2 확인 현황과 공지 🔔. 현황(status)은 쓴 사람·관리자 화면에서만 부른다. 받는 사람에게는 자기 상태(acked_ids·must_todo)만.

- 대상: 지금 Member.group 기준(받는 곳 'all'이면 공간 전원). total은 지금 대상 수이고, 쓴 사람이 대상이면 total에 들고 본 것으로 친다.
- 확인 수: 일반 공지는 열어 본 사람, 확인 필수는 '확인했어요'를 누른 사람.
- 재확인: 마지막 실질 변경 판 R에 대해, R 앞 판을 본 대상(쓴 사람 제외) m명 중 R 이후 판을 본 n명.
"""
from collections import defaultdict

from django.db.models import Exists, Max, OuterRef, Q
from django.utils import timezone

from collect.models import Notification
from spaces.models import Member

from .models import ALL, FILE_LIST, FILES, ORDER, Notice, NoticeSeen, NoticeVersion


def _in(audience, group):
    return audience == ALL or (bool(group) and f",{group}," in audience)


def in_audience(n, member):
    return _in(n.audience, member.group)


def can_see_status(n, member):
    return member.is_manager or n.by_id == member.pk


def _audience_q(n):
    return Q() if n.audience == ALL else Q(group__in=[g for g in n.audience.split(",") if g])


def _pct(a, b):
    return round(a / b * 100) if b else 0


def statuses(space, notices):
    """{공지 pk: 현황}. 공지·사람 수와 무관하게 쿼리 3번."""
    if not notices:
        return {}
    members = list(Member.objects.in_space(space).order_by("joined_at", "pk").values("pk", "name", "cls", "group"))
    rows = defaultdict(list)
    for nid, mid, v, acked in (NoticeSeen.objects.in_space(space).filter(notice__in=notices)
                               .values_list("notice_id", "member_id", "v", "acked")):
        rows[nid].append((mid, v, acked))
    real = dict(NoticeVersion.objects.in_space(space).filter(notice__in=notices, real=True)
                .values_list("notice_id").annotate(Max("v")))
    return {n.pk: _status(n, members, rows[n.pk], real.get(n.pk)) for n in notices}


def _status(n, members, rows, real_v):
    aud = [m for m in members if _in(n.audience, m["group"])]
    ids = {m["pk"] for m in aud}
    opened = {mid for mid, _, _ in rows} | {n.by_id}
    done = {mid for mid, _, acked in rows if acked or not n.must} | {n.by_id}
    seen = len(ids & done)
    st = {"total": len(ids), "seen": seen, "pct": _pct(seen, len(ids)), "opened": len(ids & opened),
          "missing": [m for m in aud if m["pk"] not in done], "recheck": None}
    if real_v:
        before = {mid for mid, v, _ in rows if v < real_v} & ids - {n.by_id}
        after = {mid for mid, v, _ in rows if v >= real_v}
        if before:  # 앞 판을 본 사람이 없으면 다시 볼 사람도 없다
            st["recheck"] = {"v": real_v, "n": len(before & after), "m": len(before), "pct": _pct(len(before & after), len(before))}
    return st


def acked_ids(member, notices):
    return set(NoticeSeen.objects.filter(member=member, notice__in=notices, acked=True).values_list("notice_id", flat=True))


def must_todo(space, member):
    """내가 대상인 확인 필수 공지 중 아직 안 누른 것. 내가 쓴 것은 뺀다."""
    aud = Q(audience=ALL)
    if member.group:
        aud |= Q(audience__contains=f",{member.group},")
    return (Notice.objects.in_space(space).filter(aud, must=True).exclude(by=member)
            .exclude(pk__in=NoticeSeen.objects.filter(member=member, acked=True).values("notice"))
            .order_by("-updated_at", "-pk"))


def changes(prev, cur):
    """바뀐 항목만 [{name, old, new}]."""
    changed = [k for k in ORDER if prev.get(k, "") != cur.get(k, "")]
    if FILES not in changed and prev.get(FILE_LIST) != cur.get(FILE_LIST):  # 같은 이름의 다른 파일로 바꿈
        changed.append(FILES)
    return [{"name": k, "old": prev.get(k, ""), "new": cur.get(k, "")} for k in changed]


def summary(prev, ver):
    """바뀐 부분 한 줄: 장소 강당 → 체육관, 본문."""
    parts = [f"{c['name']} {c['old'] or '없음'} → {c['new'] or '없음'}" for c in changes(prev.fields, ver.fields)]
    return ", ".join(parts + (["본문"] if prev.body != ver.body else []))


def change_feed(space, member, since):
    """오늘 '나와 관련된 바뀐 것'의 공지: 내가 대상이고 앞 판을 본 공지의 실질 변경. 지운 판과 맞닿은 것은 내용을 싣지 않으려 뺀다."""
    aud = Q(notice__audience=ALL)
    if member.group:
        aud |= Q(notice__audience__contains=f",{member.group},")
    saw = NoticeSeen.objects.filter(notice=OuterRef("notice"), member=member, v__lt=OuterRef("v"))
    vers = list(NoticeVersion.objects.in_space(space).filter(aud, Exists(saw), real=True, erased=False, at__gte=since)
                .select_related("notice").order_by("-at")[:6])
    prevs = {(p.notice_id, p.v): p for p in NoticeVersion.objects.filter(
        notice__in=[x.notice_id for x in vers], v__in=[x.v - 1 for x in vers], erased=False)}
    return [{"ver": x, "summary": summary(prevs[(x.notice_id, x.v - 1)], x)} for x in vers if (x.notice_id, x.v - 1) in prevs]


def _bells(space, n, member_ids, kind, text):
    today = timezone.localdate()
    Notification.objects.bulk_create(
        [Notification(space=space, member_id=m, notice=n, kind=kind, day=today, text=text[:300],
                      link=f"/s/{space.token}/board/{n.key}/") for m in member_ids],
        batch_size=500, ignore_conflicts=True)


def _can_view_q(n):
    """대상·관리자·쓴 사람. 받는 곳 '전체'면 조건 없음(빈 Q는 OR에서 사라지므로 따로)."""
    return Q() if n.audience == ALL else _audience_q(n) | Q(is_manager=True) | Q(pk=n.by_id)


def _must_targets(space, n, by):
    """확인 필수 🔔을 새로 받을 사람: 대상 중 이미 받았거나 이미 누른 사람, 고친 사람, 쓴 사람을 뺀다."""
    return set(Member.objects.in_space(space).filter(_audience_q(n)).exclude(pk=by.pk).exclude(pk=n.by_id)
               .exclude(pk__in=NoticeSeen.objects.filter(notice=n, acked=True).values("member"))
               .exclude(pk__in=Notification.objects.filter(notice=n, kind=Notification.Kind.NOTICE).values("member"))
               .values_list("pk", flat=True))


def _change_targets(space, n, ver, by):
    """실질 변경 🔔: 앞 판을 본 사람과 쓴 사람 중 지금도 볼 수 있는 사람(고친 사람 제외)."""
    saw = NoticeSeen.objects.filter(notice=n, v__lt=ver.v).values("member")
    return set(Member.objects.in_space(space).filter(Q(pk__in=saw) | Q(pk=n.by_id)).filter(_can_view_q(n))
               .exclude(pk=by.pk).values_list("pk", flat=True))


def bells_for_save(space, n, by, ver=None, prev=None):
    """한 번 저장의 🔔. 확인 필수와 실질 변경을 함께 받을 사람은 한 건으로 합친다. 확인 필수 🔔은 공지·회원당 한 번."""
    must_ids = _must_targets(space, n, by) if n.must else set()
    change_ids = _change_targets(space, n, ver, by) if ver and ver.real else set()
    must_text = f"「{n.title}」 확인 필수 공지예요."
    change_text = f"「{n.title}」 v{ver.v} 바뀐 부분: {summary(prev, ver)}" if change_ids else ""
    _bells(space, n, must_ids - change_ids, Notification.Kind.NOTICE, must_text)
    _bells(space, n, change_ids - must_ids, Notification.Kind.CHANGE, change_text)
    _bells(space, n, must_ids & change_ids, Notification.Kind.NOTICE, f"{must_text} {change_text}")


def drop_unviewable_bells(space, n):
    """대상을 줄였을 때: 이제 볼 수 없는 사람의 안 읽은 🔔을 지운다(누르면 404, 묶음 알림에 제목이 나가므로)."""
    if n.audience != ALL:
        ok = Member.objects.in_space(space).filter(_can_view_q(n)).values("pk")
        Notification.objects.in_space(space).filter(notice=n, read=False).exclude(member__in=ok).delete()


def blank_bells(space, n):
    """판 내용을 지웠을 때: 이 공지 🔔의 옛값·새값 문구를 지금 제목만 남긴 문구로 바꾼다."""
    bells = Notification.objects.in_space(space).filter(notice=n)
    bells.filter(kind=Notification.Kind.NOTICE).update(text=f"「{n.title}」 확인 필수 공지예요.")
    bells.filter(kind=Notification.Kind.CHANGE).update(text=f"「{n.title}」 공지가 바뀌었어요.")
