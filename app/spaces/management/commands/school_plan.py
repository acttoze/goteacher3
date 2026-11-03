"""학교 플랜 켜고 끄기(운영자):
school_plan <NEIS 학교 코드> --on --until YYYY-MM-DD [--manager=<회원 key>] | --off | --remove-manager=<회원 key>

--on: 학교 공간을 만들고 동학년을 합친다. 다시 돌려도 안전하다(기한 연장·관리자 추가에도 쓴다).
      학교에 관리자가 없으면 --manager가 필수다. 관리자는 구글 회원만 된다.
--off: 플랜만 끈다. 합친 것은 되돌리지 않는다.
--remove-manager: 관리자 해제. 마지막 관리자는 해제하지 않는다.
회원 key는 -로 시작할 수 있어 --manager=<key>처럼 등호로 붙여 쓴다.
이름·토큰은 출력하지 않는다.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from spaces.merge import open_school, remove_manager
from spaces.models import Member, School


class Command(BaseCommand):
    help = "학교 플랜을 켜거나(--on, 동학년 합침) 끈다(--off)."

    def add_arguments(self, parser):
        parser.add_argument("code", help="NEIS 학교 코드")
        parser.add_argument("--atpt", help="시도교육청 코드(학교 코드가 겹칠 때)")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--on", action="store_true")
        mode.add_argument("--off", action="store_true")
        mode.add_argument("--remove-manager", help="관리자 해제할 회원 key(--remove-manager=<key>처럼 등호로)")
        parser.add_argument("--until", help="결제 기한 YYYY-MM-DD(--on에 필수)")
        parser.add_argument("--manager", help="첫 관리자의 회원 key(--manager=<key>처럼 등호로)")

    def handle(self, *args, **o):
        schools = School.objects.filter(neis_code=o["code"])
        if o["atpt"]:
            schools = schools.filter(neis_atpt=o["atpt"])
        if len(schools) != 1:
            raise CommandError(f"학교를 하나로 찾지 못했습니다({len(schools)}곳). --atpt로 좁혀 주세요.")
        school = schools[0]

        if o["off"]:
            school.plan = School.Plan.FREE
            school.save(update_fields=["plan"])
            self.stdout.write(f"학교 {school.pk}: 플랜을 껐습니다. 합친 공간은 그대로입니다.")
            return

        if o["remove_manager"]:
            member = Member.objects.filter(key=o["remove_manager"], space__school=school, is_manager=True).first()
            if member is None:
                raise CommandError("이 학교의 관리자 중에서 그 회원을 찾지 못했습니다.")
            try:
                remove_manager(member)
            except ValueError as e:
                raise CommandError(str(e))
            self.stdout.write(f"학교 {school.pk}: 관리자를 해제했습니다.")
            return

        if not o["until"]:
            raise CommandError("--on에는 --until YYYY-MM-DD가 필요합니다.")
        try:
            until = date.fromisoformat(o["until"])
        except ValueError:
            raise CommandError("--until은 YYYY-MM-DD 형식이어야 합니다.")
        manager = None
        if o["manager"]:
            manager = Member.objects.filter(key=o["manager"], space__school=school).first()
            if manager is None:
                raise CommandError("이 학교에서 그 회원을 찾지 못했습니다.")
        elif not Member.objects.filter(space__school=school, is_manager=True).exists():
            raise CommandError("이 학교에는 관리자가 없습니다. --manager=<회원 key>로 첫 관리자를 정해 주세요.")

        try:
            hub = open_school(school, until, manager)
        except ValueError as e:
            raise CommandError(str(e))
        counts = {
            "회원": hub.members.count(), "모으기": hub.collections.count(), "일정": hub.events.count(),
            "알림": hub.notifications.count(), "푸시 구독": hub.push_subscriptions.count(),
            "한글 파일": hub.hwp_files.count(), "한글 결과": hub.hwp_results.count(),
        }
        summary = " · ".join(f"{k} {v}" for k, v in counts.items())
        self.stdout.write(f"학교 {school.pk}: 플랜 켬(기한 {until}). 학교 공간: {summary}")
