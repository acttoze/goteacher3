"""학교 플랜 켜고 끄기(운영자):
school_plan requests  (대기 신청 목록. 신청을 처리하려고 담당자 이름·메일을 보여 준다. 첫 줄은 "대기 N건")
school_plan <NEIS 학교 코드> --on --until YYYY-MM-DD [--manager=<회원 key> | --manager-request=<신청 ID>] | --off | --remove-manager=<회원 key>

--on: 학교 공간을 만들고 동학년을 합친다. 다시 돌려도 안전하다(기한 연장·관리자 추가에도 쓴다). 이 학교의 대기 신청은 닫는다.
      학교에 관리자가 없으면 --manager 또는 --manager-request가 필수다. 관리자는 구글 회원만 된다.
      --manager-request: 신청자를 관리자로. 회원 0명 학교도 켤 수 있다(경고를 낸다). 신청자가 다른 공간에 있으면 오류.
      회원이 있는 학교에 회원이 아닌 신청자를 세우려면 --identity-checked(운영자가 공문·전화로 재직을 확인했다는 뜻).
      끊겼던 플랜을 다시 켜면 옛 업체 링크는 해제한다(TV 주소는 유지).
--off: 플랜만 끈다(기한도 오늘로 앞당겨 보관 기간을 시작한다). 합친 것은 되돌리지 않는다.
--remove-manager: 관리자 해제. 마지막 관리자는 해제하지 않는다.
회원 key는 -로 시작할 수 있어 --manager=<key>처럼 등호로 붙여 쓴다.
requests 말고는 이름·토큰을 출력하지 않는다.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from repairs.models import VendorAccount
from spaces.merge import open_school, remove_manager
from spaces.models import Member, PlanRequest, School


class Command(BaseCommand):
    help = "학교 플랜을 켜거나(--on, 동학년 합침) 끈다(--off)."

    def add_arguments(self, parser):
        parser.add_argument("code", help="NEIS 학교 코드, 또는 대기 신청 목록을 볼 때 requests")
        parser.add_argument("--atpt", help="시도교육청 코드(학교 코드가 겹칠 때)")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--on", action="store_true")
        mode.add_argument("--off", action="store_true")
        mode.add_argument("--remove-manager", help="관리자 해제할 회원 key(--remove-manager=<key>처럼 등호로)")
        parser.add_argument("--until", help="결제 기한 YYYY-MM-DD(--on에 필수)")
        parser.add_argument("--identity-checked", action="store_true",
                            help="운영자가 공문·전화로 신청자의 재직을 확인했다는 뜻. 회원이 있는 학교에 회원이 아닌 신청자를 --manager-request로 세울 때 필요")
        who = parser.add_mutually_exclusive_group()
        who.add_argument("--manager", help="첫 관리자의 회원 key(--manager=<key>처럼 등호로)")
        who.add_argument("--manager-request", type=int, help="첫 관리자로 세울 신청 ID(school_plan requests의 #번호)")

    def _requests(self):
        rows = PlanRequest.objects.filter(status=PlanRequest.Status.PENDING).select_related("school", "member").order_by("created_at", "pk")
        self.stdout.write(f"대기 {len(rows)}건")
        for r in rows:
            size = " ".join(x for x in (f"학생 {r.students}" if r.students else "", f"학급 {r.classes}" if r.classes else "") if x)
            seat = "이 학교 회원" if r.member_id else "공간 없음" if r.user_id and not Member.objects.filter(user_id=r.user_id).exists() else "다른 공간 또는 계정 없음"
            self.stdout.write(f"#{r.pk} {r.school.name} {r.school.neis_atpt}/{r.school.neis_code} · {timezone.localtime(r.created_at):%Y-%m-%d} · "
                              f"{r.by_name}({r.position}) {r.email} · {size or '규모 안 씀'} · {seat}")

    def handle(self, *args, **o):
        if o["code"] == "requests":
            return self._requests()
        if not (o["on"] or o["off"] or o["remove_manager"]):
            raise CommandError("--on, --off, --remove-manager 중 하나를 써 주세요.")
        schools = School.objects.filter(neis_code=o["code"])
        if o["atpt"]:
            schools = schools.filter(neis_atpt=o["atpt"])
        if len(schools) != 1:
            raise CommandError(f"학교를 하나로 찾지 못했습니다({len(schools)}곳). --atpt로 좁혀 주세요.")
        school = schools[0]

        if o["off"]:
            school.plan = School.Plan.FREE
            today = timezone.localdate()
            if school.paid_until and school.paid_until > today:  # 공지 보관 기간은 플랜이 끝난 날부터 센다
                school.paid_until = today
            school.save(update_fields=["plan", "paid_until"])
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
        applicant = None
        if o["manager_request"] is not None:
            applicant = PlanRequest.objects.filter(pk=o["manager_request"], school=school).select_related("user").first()
            if applicant is None:
                raise CommandError("이 학교의 신청에서 그 번호를 찾지 못했습니다.")

        unverified = applicant is not None and not o["identity_checked"] and not Member.objects.filter(space__school=school).exists()
        old_links = 0 if school.writable else VendorAccount.objects.filter(space__school=school, revoked_at__isnull=True).count()
        try:
            hub = open_school(school, until, manager, applicant, o["identity_checked"])
        except ValueError as e:
            raise CommandError(str(e))
        if unverified:
            self.stderr.write("경고: 회원이 없는 학교라 신청자의 재직을 대조하지 못했습니다. 공문·전화로 학교에 확인해 주세요.")
        if old_links:
            self.stdout.write(f"옛 업체 링크 {old_links}개를 해제했습니다. 다시 쓰려면 학교에서 재발급해야 합니다. TV 주소는 그대로입니다.")
        counts = {
            "회원": hub.members.count(), "모으기": hub.collections.count(), "일정": hub.events.count(),
            "알림": hub.notifications.count(), "푸시 구독": hub.push_subscriptions.count(),
            "한글 파일": hub.hwp_files.count(), "한글 결과": hub.hwp_results.count(),
        }
        summary = " · ".join(f"{k} {v}" for k, v in counts.items())
        self.stdout.write(f"학교 {school.pk}: 플랜 켬(기한 {until}). 학교 공간: {summary}")
