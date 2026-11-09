"""계정 삭제(운영자): delete_account <이메일·구글 sub·아이디> [--dry-run]. 처리방침 '요청하면 지체 없이 파기'의 이행.

- 지운다: 회원 자리(남은 사람이 없는 동학년은 공간째), 기기 토큰, 웹 푸시 구독, 세션, 대기 중인 플랜 신청, 그 사람의 복무 줄, 계정.
- 이름을 바꾼다: 공지·공지 판·수리 요청·식판 사진·업체 추천의 쓴 사람 표시를 '나간 선생님'으로.
- 이름을 지운다: 아직 안 낸 취합 줄(줄 자리는 남고 이름만 빈다. 화면·합본·독촉 문구에 나오지 않는다).
- 커뮤니티: 닉네임(프로필)과 양식 추천은 CASCADE로 지운다. 글·댓글은 남고 작성 계정 연결만 끊겨(SET_NULL) 화면에 '나간 선생님'으로 나온다. 낸 신고는 신고자 연결만 끊긴다.
- 남긴다(처리방침 2항의 기간대로): 후기(자리·계정 연결만 끊긴다), 이미 낸 값의 이름, 업무분장표, 명부 고친 기록, 처리한 플랜 신청.
- 수리 제목·메모에 직접 쓴 이름은 고치지 않고 건수만 안내한다(그 글을 쓴 사람의 내용이다).
- 마지막 관리자면 거부한다. 자리를 지우므로 합치지 않은 동학년에서 같은 반·이름으로 들어와도 옛 자리의 대표 권한은 이어지지 않는다.
출력은 건수만(이름·메일 없음).
"""
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from accounts.models import GUEST_PREFIX, DeviceToken
from collect.models import PushSubscription, Row
from community.models import Comment, Post
from meals.models import MealPhoto
from notices.models import Notice, NoticeVersion
from partners.models import Review, Suggestion
from repairs.models import Repair
from spaces.merge import retry_once, vacate
from spaces.models import Attendance, Member, PlanRequest, Space, is_last_manager
from today.models import EventComment

GONE_NAME = "나간 선생님"
GONE_UNIT = "나간 분"  # 줄 머리가 이름이던 줄. 화면은 뒤에 '선생님'을 붙인다


def _sessions_of(user_pk):
    return [s.pk for s in Session.objects.all() if s.get_decoded().get("_auth_user_id") == str(user_pk)]


def _forget_rows(seat):
    """안 낸 줄의 이름을 지운 건수. 붙여넣어 이름 글자만 있는 줄도 같은 사람으로 본다(같은 이름이 공간에 또 있으면 그 자리 줄만).
    여러 줄 칸에서 이미 낸 줄이 있으면 빈 줄은 없앤다: 이름을 지우면 낸 줄과 다른 사람으로 묶인다."""
    mine = Q(member=seat)
    if not Member.objects.in_space(seat.space).filter(name=seat.name).exclude(pk=seat.pk).exists():
        mine |= Q(member__isnull=True, who=seat.name)
    n = 0
    for r in Row.objects.in_space(seat.space).filter(mine, cells__isnull=True).select_related("collection"):
        n += 1
        if r.collection.multi and r.collection.rows.filter(unit=r.unit, who=r.who, member=r.member_id, cells__isnull=False).exists():
            r.delete()
            continue
        Row.objects.filter(pk=r.pk).update(who="", unit=GONE_UNIT if r.unit == seat.name else r.unit)
    return n


def _forget_names(seat):
    n = {
        "공지": Notice.objects.in_space(seat.space).filter(by=seat).update(by_name=GONE_NAME),
        # 판의 쓴 사람은 이름 글자뿐이라 같은 공간의 같은 이름까지 바뀔 수 있다(내용은 그대로, 표시만)
        "공지 판": NoticeVersion.objects.in_space(seat.space).filter(by=seat.name).update(by=GONE_NAME),
        "수리 요청": Repair.objects.in_space(seat.space).filter(by=seat).update(by_name=GONE_NAME),
        "식판 사진": MealPhoto.objects.in_space(seat.space).filter(by=seat).update(by_name=GONE_NAME),
        "일정 댓글": EventComment.objects.in_space(seat.space).filter(by=seat).update(by_name=GONE_NAME),
        "업체 추천": Suggestion.objects.filter(by_member=seat).update(by_name=GONE_NAME),
    }
    n["안 낸 취합 줄"] = _forget_rows(seat)
    n["복무"] = Attendance.objects.in_space(seat.space).filter(member=seat).delete()[0]
    return n


class Command(BaseCommand):
    help = ("계정 삭제(개인정보처리방침의 파기 요청). 회원 자리·기기 토큰·푸시 구독·세션을 지우고, 쓴 사람 표시는 '나간 선생님'으로, 안 낸 취합 줄의 이름은 지운다. "
            "이미 낸 값의 이름은 기간까지 남는다. 마지막 관리자는 거부한다. 건수만 출력한다.")

    def add_arguments(self, parser):
        parser.add_argument("who", help="이메일, 구글 sub 또는 아이디")
        parser.add_argument("--dry-run", action="store_true", help="지우지 않고 건수만 센다")

    def _find(self, who):
        who = who.strip()
        # 계정 회원만(손님 guest: 제외): 구글은 sub·메일, 아이디 회원은 아이디(대소문자 무시)
        users = list(get_user_model().objects.exclude(username__startswith=GUEST_PREFIX).filter(
            Q(username=f"google:{who}") | Q(username__startswith="google:", email__iexact=who)
            | (Q(username__iexact=who) & ~Q(username__contains=":")))) if who else []
        if len(users) != 1:
            raise CommandError(f"계정을 하나로 찾지 못했습니다({len(users)}개). 구글은 sub, 아이디 회원은 아이디로 다시 써 주세요.")
        return users[0].pk

    def handle(self, *args, **o):
        user_pk, dry = self._find(o["who"]), o["dry_run"]

        def work():
            user = get_user_model().objects.select_for_update().get(pk=user_pk)
            seats = list(Member.objects.filter(user=user).select_related("space").order_by("space_id"))
            list(Space.objects.select_for_update(no_key=True).filter(pk__in=[s.space_id for s in seats]).order_by("pk"))
            if any(is_last_manager(s) for s in seats):
                raise CommandError("이 계정은 마지막 관리자입니다. 학교에서 다른 관리자를 세운 뒤 다시 해 주세요.")
            counts = {"회원 자리": len(seats), "지운 공간": 0, "기기 토큰": DeviceToken.objects.filter(user=user).count(),
                      "푸시 구독": PushSubscription.objects.filter(member__in=seats).count(),
                      "후기 연결 끊음": Review.objects.filter(Q(author_user=user) | Q(member__in=seats)).count(),
                      "커뮤니티 글·댓글 연결 끊음": Post.objects.filter(author=user).count() + Comment.objects.filter(author=user).count()}
            sessions = _sessions_of(user_pk)
            counts["세션"] = len(sessions)
            counts["typed"] = sum(
                Repair.objects.in_space(s.space).filter(Q(title__contains=s.name) | Q(note__contains=s.name) | Q(reply__contains=s.name)).count() for s in seats)
            for seat in seats:
                for key, value in _forget_names(seat).items():
                    counts[key] = counts.get(key, 0) + value
                counts["지운 공간"] += vacate(seat)
            pending = PlanRequest.objects.filter(user=user, status=PlanRequest.Status.PENDING)
            counts["대기 신청"] = pending.delete()[0]
            counts["처리한 신청(기간까지 보관)"] = PlanRequest.objects.filter(user=user).count()
            Session.objects.filter(pk__in=sessions).delete()
            user.delete()
            if dry:
                transaction.set_rollback(True)
            return counts

        counts = retry_once(work)
        typed = counts.pop("typed")
        line = " · ".join(f"{k} {v}" for k, v in counts.items())
        self.stdout.write(f"{'(지우지 않음) ' if dry else ''}계정 1 · {line}")
        if typed:
            self.stdout.write(f"안내: 제목·메모에 이름이 든 수리 요청 {typed}건은 고치지 않았습니다. 삭제를 요청받았으면 학교 담당자와 확인해 주세요.")
