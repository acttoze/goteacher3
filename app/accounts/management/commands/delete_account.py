"""계정 삭제(운영자): delete_account <이메일 또는 구글 sub> [--dry-run]. 처리방침 '요청하면 지체 없이 파기'의 이행.

- 지운다: 회원 자리(남은 사람이 없는 동학년은 공간째), 기기 토큰, 웹 푸시 구독, 세션, 대기 중인 플랜 신청, 그 사람의 복무 줄, 계정.
- 이름을 바꾼다: 공지·공지 판·수리 요청·식판 사진·업체 추천의 쓴 사람 표시를 '나간 선생님'으로.
- 남긴다(처리방침 2항의 기간대로): 후기(자리·계정 연결만 끊긴다), 이미 낸 값의 이름, 업무분장표, 명부 고친 기록, 처리한 플랜 신청.
- 마지막 관리자면 거부한다. 자리를 지우므로 합치지 않은 동학년에서 같은 반·이름으로 들어와도 옛 자리의 대표 권한은 이어지지 않는다.
출력은 건수만(이름·메일 없음).
"""
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from accounts.models import DeviceToken
from collect.models import PushSubscription
from meals.models import MealPhoto
from notices.models import Notice, NoticeVersion
from partners.models import Review, Suggestion
from repairs.models import Repair
from spaces.merge import retry_once, vacate
from spaces.models import Attendance, Member, PlanRequest, Space, is_last_manager

GONE_NAME = "나간 선생님"


def _sessions_of(user_pk):
    return [s.pk for s in Session.objects.all() if s.get_decoded().get("_auth_user_id") == str(user_pk)]


def _forget_names(seat):
    n = {
        "공지": Notice.objects.in_space(seat.space).filter(by=seat).update(by_name=GONE_NAME),
        # 판의 쓴 사람은 이름 글자뿐이라 같은 공간의 같은 이름까지 바뀔 수 있다(내용은 그대로, 표시만)
        "공지 판": NoticeVersion.objects.in_space(seat.space).filter(by=seat.name).update(by=GONE_NAME),
        "수리 요청": Repair.objects.in_space(seat.space).filter(by=seat).update(by_name=GONE_NAME),
        "식판 사진": MealPhoto.objects.in_space(seat.space).filter(by=seat).update(by_name=GONE_NAME),
        "업체 추천": Suggestion.objects.filter(by_member=seat).update(by_name=GONE_NAME),
    }
    n["복무"] = Attendance.objects.in_space(seat.space).filter(member=seat).delete()[0]
    return n


class Command(BaseCommand):
    help = ("계정 삭제(개인정보처리방침의 파기 요청). 회원 자리·기기 토큰·푸시 구독·세션을 지우고 쓴 사람 표시는 '나간 선생님'으로 바꾼다. "
            "마지막 관리자는 거부한다. 건수만 출력한다.")

    def add_arguments(self, parser):
        parser.add_argument("who", help="이메일 또는 구글 sub")
        parser.add_argument("--dry-run", action="store_true", help="지우지 않고 건수만 센다")

    def _find(self, who):
        who = who.strip()
        users = list(get_user_model().objects.filter(username__startswith="google:").filter(Q(username=f"google:{who}") | Q(email__iexact=who))) if who else []
        if len(users) != 1:
            raise CommandError(f"구글 계정을 하나로 찾지 못했습니다({len(users)}개). sub로 다시 써 주세요.")
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
                      "후기 연결 끊음": Review.objects.filter(Q(author_user=user) | Q(member__in=seats)).count()}
            sessions = _sessions_of(user_pk)
            counts["세션"] = len(sessions)
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
        line = " · ".join(f"{k} {v}" for k, v in counts.items())
        self.stdout.write(f"{'(지우지 않음) ' if dry else ''}계정 1 · {line}")
