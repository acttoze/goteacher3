"""기관 공간 플랜(운영자, T1). 기관은 시작 화면에서 바로 열리고 체험 기간(ORG_TRIAL_DAYS)이 지나면 읽기 전용이 된다.
그 뒤 신청 → 견적서 → 입금 확인 → 이 명령으로 켠다.

org_plan requests  (기관 대기 신청 목록. 첫 줄은 "대기 N건". 기관 번호·신청 번호·담당자 이름·메일을 보여 준다)
org_plan <기관 번호> --on --until YYYY-MM-DD | --off | --remove-manager=<회원 key> | --add-manager=<계정 아이디>
org_plan orphans [--delete]  (공간도 대기 신청도 없는 기관 행 목록. --delete면 지운다. 체험 기록(trial_by)이 있는 행은 남긴다)
org_plan --request=<신청 ID> --on --until YYYY-MM-DD --identity-checked  (공간 없이 신청한 기관: 공간을 만들고 신청자를 관리자로)

기관 번호는 School pk(운영자 셸에서만 쓰는 값, 외부 주소에 나오지 않는다). NEIS 학교는 school_plan으로 켠다.
--on: 플랜을 켜고 기한을 정한다(연장에도 쓴다). 이 기관의 대기 신청은 닫는다. 처음 켤 때는 --identity-checked가 필수이고,
      그때부터 '운영자 확인' 기관(School.org_checked_at)이 된다: 학교 저장 상한, 업체 후기 자격, '미확인' 표시 없음.
--request: 기관은 회원 대조가 안 되므로 --identity-checked(공문·전화로 재직 확인)가 필수다. 신청자가 다른 공간에 있으면 오류.
--off: 플랜만 끈다(기한도 오늘로 앞당긴다). --remove-manager: 마지막 관리자는 해제하지 않는다.
--add-manager: 이 기관 회원 중 그 계정(User.username, 구글 google:… 또는 아이디)을 관리자로. 이름만 들어온 손님은 안 된다.
requests 말고는 이름·토큰을 출력하지 않는다.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import is_guest
from spaces.merge import remove_manager
from spaces.models import Member, PlanRequest, School, Space
from spaces.views import ORG_NONE


class Command(BaseCommand):
    help = "기관 공간 플랜을 켜거나(--on) 끈다(--off). 대기 신청은 requests."

    def add_arguments(self, parser):
        parser.add_argument("org", nargs="?", help="기관 번호(School pk), 대기 신청 목록은 requests, 남은 기관 행은 orphans")
        parser.add_argument("--delete", action="store_true", help="orphans와 함께: 목록의 행을 지운다")
        parser.add_argument("--request", type=int, help="공간 없이 신청한 기관의 신청 ID(org_plan requests의 #번호)")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--on", action="store_true")
        mode.add_argument("--off", action="store_true")
        mode.add_argument("--remove-manager", help="관리자 해제할 회원 key(--remove-manager=<key>처럼 등호로)")
        mode.add_argument("--add-manager", help="관리자로 세울 계정 아이디(User.username, --add-manager=<아이디>처럼 등호로)")
        parser.add_argument("--until", help="결제 기한 YYYY-MM-DD(--on에 필수)")
        parser.add_argument("--identity-checked", action="store_true", help="운영자가 공문·전화로 기관과 신청자를 확인했다는 뜻(처음 켤 때 필수)")

    def _requests(self):
        rows = (PlanRequest.objects.filter(status=PlanRequest.Status.PENDING, school__neis_code="")
                .select_related("school").order_by("created_at", "pk"))
        self.stdout.write(f"대기 {len(rows)}건")
        for r in rows:
            opened = Space.objects.filter(school=r.school, kind=Space.Kind.ORG).exists()
            self.stdout.write(f"#{r.pk} 기관 {r.school.pk} {r.school.name} {r.school.neis_atpt} · {timezone.localtime(r.created_at):%Y-%m-%d} · "
                              f"{r.by_name}({r.position}) {r.email} · {'공간 있음' if opened else '공간 없음(--request로 켬)'}")

    def _orphans(self, delete):
        """공간도 대기 신청도 없는 기관 행(신청만 하고 켜지 않았거나 공간이 지워진 뒤). 체험 기록 행은 계정당 1회 판단에 쓰여 남긴다.
        지우면 처리한 옛 신청(견적서)도 함께 지워진다(CASCADE)."""
        rows = (School.objects.filter(neis_code="", trial_by__isnull=True, spaces__isnull=True)
                .exclude(plan_requests__status=PlanRequest.Status.PENDING).order_by("pk").distinct())
        self.stdout.write(f"남은 기관 행 {len(rows)}개")
        for school in rows:
            self.stdout.write(f"기관 {school.pk} {school.name} {school.neis_atpt}")
        if delete and rows:
            n = School.objects.filter(pk__in=[r.pk for r in rows]).delete()[1].get("spaces.School", 0)
            self.stdout.write(f"{n}개를 지웠습니다.")

    def handle(self, *args, **o):
        if o["org"] == "requests":
            return self._requests()
        if o["org"] == "orphans":
            return self._orphans(o["delete"])
        if not (o["on"] or o["off"] or o["remove_manager"] or o["add_manager"]):
            raise CommandError("--on, --off, --remove-manager, --add-manager 중 하나를 써 주세요.")
        req = None
        if o["request"] is not None:
            if not o["on"]:
                raise CommandError("--request는 --on과 함께 써 주세요.")
            req = PlanRequest.objects.filter(pk=o["request"], school__neis_code="").select_related("school", "user").first()
            if req is None:
                raise CommandError("기관 신청에서 그 번호를 찾지 못했습니다.")
            school = req.school
        else:
            if not (o["org"] or "").isdecimal():
                raise CommandError("기관 번호(숫자)나 --request=<신청 ID>를 써 주세요.")
            school = School.objects.filter(pk=int(o["org"]), neis_code="").first()
            if school is None:
                raise CommandError("기관을 찾지 못했습니다. NEIS 학교는 school_plan으로 켜 주세요.")

        if o["off"]:
            school.plan = School.Plan.FREE
            today = timezone.localdate()
            if school.paid_until and school.paid_until > today:
                school.paid_until = today
            school.save(update_fields=["plan", "paid_until"])
            self.stdout.write(f"기관 {school.pk}: 플랜을 껐습니다.")
            return

        if o["remove_manager"]:
            member = Member.objects.filter(key=o["remove_manager"], space__school=school, is_manager=True).first()
            if member is None:
                raise CommandError("이 기관의 관리자 중에서 그 회원을 찾지 못했습니다.")
            try:
                remove_manager(member)
            except ValueError as e:
                raise CommandError(str(e))
            self.stdout.write(f"기관 {school.pk}: 관리자를 해제했습니다.")
            return

        if o["add_manager"]:
            member = Member.objects.filter(space__school=school, user__username=o["add_manager"]).select_related("user").first()
            if member is None or is_guest(member.user):
                raise CommandError("이 기관 회원 중에서 그 계정을 찾지 못했습니다(구글·아이디 계정만 됩니다).")
            Member.objects.filter(pk=member.pk).update(is_manager=True)
            self.stdout.write(f"기관 {school.pk}: 관리자를 세웠습니다.")
            return

        if not o["until"]:
            raise CommandError("--on에는 --until YYYY-MM-DD가 필요합니다.")
        try:
            until = date.fromisoformat(o["until"])
        except ValueError:
            raise CommandError("--until은 YYYY-MM-DD 형식이어야 합니다.")
        if school.org_checked_at is None and not o["identity_checked"]:  # 처음 켤 때 = 운영자 확인(미확인 표시가 사라진다)
            raise CommandError("처음 켜는 기관은 공문·전화로 기관과 신청자를 확인한 뒤 --identity-checked를 붙여 주세요.")
        with transaction.atomic():
            School.objects.select_for_update().filter(pk=school.pk).first()
            space = Space.objects.filter(school=school, kind=Space.Kind.ORG).first()
            if space is None:
                if req is None:
                    raise CommandError("이 기관에는 공간이 없습니다. --request=<신청 ID>로 신청자를 관리자로 세워 켜 주세요.")
                self._open(school, req)
            school.plan, school.paid_until = School.Plan.SCHOOL, until
            school.org_checked_at = school.org_checked_at or timezone.now()  # 운영자 확인: 이때부터 학교 상한·'미확인' 표시 없음
            school.save(update_fields=["plan", "paid_until", "org_checked_at"])
            PlanRequest.objects.filter(school=school, status=PlanRequest.Status.PENDING).update(
                status=PlanRequest.Status.DONE, done_at=timezone.now())
        self.stdout.write(f"기관 {school.pk}: 플랜 켬(기한 {until}).")

    def _open(self, school, req):
        """신청자를 첫 관리자로 기관 공간을 만든다(운영자 확인은 handle이 먼저 요구했다)."""
        user = req.user
        if user is None or is_guest(user):
            raise CommandError("신청자가 계정 회원이 아닙니다.")
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()  # 시작 화면·입장과 직렬화
        if Member.objects.filter(user=user).exists():
            raise CommandError("신청자가 이미 다른 공간에 있습니다. 그 공간에서 나간 뒤 다시 켜 주세요.")
        space = Space.objects.create(school=school, kind=Space.Kind.ORG, grade=0, name=school.name)
        Member.objects.create(space=space, user=user, name=req.by_name, cls=ORG_NONE, position=req.position, is_manager=True)
