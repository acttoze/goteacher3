"""V7 도움말 캡처용 가상 학교 '고티처초등학교'를 만든다(이미 있으면 지우고 다시). DEBUG가 아니면 거부한다.

    cd app && DEBUG=1 python manage.py seed_help_demo

이름·일정은 모두 지어낸 것이다. 로그인 아이디는 LOGIN(예: haneul_kim 관리자, gyeoul_jung 4학년 1반), 비밀번호 PASSWORD.
날짜는 오늘 기준 이번 주(월~금)에 놓는다. 급식 메뉴는 NEIS에서 오므로 여기서 넣지 않는다(ops/help/serve.py가 가짜 메뉴를 준다).
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from collect.models import Cell, Collection, Field
from collect.recipients import audience_head, audience_rows, make_rows, stored_audience
from notices.models import Notice
from notices.views import _add_version
from spaces.models import Attendance, Duty, Member, School, Space
from today.models import ME, Event

NAME = "고티처초등학교"
ATPT, CODE = "Z99", "9999999"  # 실제 시도·학교 코드에 없는 값
PASSWORD = "help-demo-2026"

# (아이디 꼬리, 이름, 반, 직위, 소속, 권한)
PEOPLE = [
    ("admin", "김하늘", "교무실", "교무부장", "office", {"is_manager": True, "can_duty": True}),
    ("11", "이봄", "1-1", "", "g1", {}),
    ("21", "박여름", "2-1", "", "g2", {}),
    ("31", "최가을", "3-1", "", "g3", {}),
    ("41", "정겨울", "4-1", "", "g4", {}),
    ("42", "한별", "4-2", "", "g4", {}),
    ("51", "윤솔", "5-1", "", "g5", {}),
    ("61", "강바다", "6-1", "", "g6", {}),
    ("office", "문다온", "교무실", "교무실무사", "office", {"can_duty": True}),
    ("adm", "오누리", "행정실", "행정실장", "admin", {"can_repair": True}),
    ("meal", "서다솜", "급식소", "영양교사", "meal", {"can_meal": True}),
]
# 화면(설정의 내 정보 등)에 찍히는 로그인 아이디. 가상 이름을 로마자로 쓴 것(ops/help/capture.py LOGIN과 같게)
LOGIN = {"admin": "haneul_kim", "11": "bom_lee", "21": "yeoreum_park", "31": "gaeul_choi", "41": "gyeoul_jung",
         "42": "byeol_han", "51": "sol_yoon", "61": "bada_kang", "office": "daon_moon", "adm": "nuri_oh", "meal": "dasom_seo"}


def wipe():
    """지난번에 만든 가상 학교·계정만 지운다."""
    for school in School.objects.filter(neis_atpt=ATPT, neis_code=CODE):
        Space.objects.filter(school=school).delete()
        school.delete()
    get_user_model().objects.filter(username__in=LOGIN.values()).delete()


class Command(BaseCommand):
    help = "도움말 캡처용 가상 학교(고티처초등학교)를 지우고 다시 만든다. DEBUG에서만."

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("DEBUG=1인 개발·캡처용 DB에서만 돌립니다.")
        with transaction.atomic():
            wipe()
            self.build()
        self.stdout.write(f"{NAME}: 아이디 {LOGIN['admin']} / 비밀번호 {PASSWORD}")

    def build(self):
        today = timezone.localdate()
        mon = today - timedelta(days=today.weekday())
        day = [mon + timedelta(days=i) for i in range(5)]
        school = School.objects.create(neis_atpt=ATPT, neis_code=CODE, name=NAME, plan=School.Plan.SCHOOL,
                                       paid_until=today + timedelta(days=365))
        hub = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name=NAME)
        User = get_user_model()
        m = {}
        for i, (tail, name, cls, pos, group, perm) in enumerate(PEOPLE):
            u = User(username=LOGIN[tail], first_name=name)
            u.set_password(PASSWORD)
            u.save()
            m[tail] = Member.objects.create(space=hub, user=u, name=name, cls=cls, position=pos, group=group, order=i, **perm)

        for i, (dept, task, who) in enumerate([
            ("교무부", "교육과정·학사", "김하늘"), ("교무부", "복무·출장", "문다온"), ("연구부", "교원 연수", "한별"),
            ("생활부", "안전교육", "윤솔"), ("행정실", "물품·예산", "오누리"), ("급식", "급식 운영", "서다솜"),
        ]):
            Duty.objects.create(space=hub, dept=dept, task=task, who=who, order=i)

        def ev(d, title, by, scope="all", **kw):
            Event.objects.create(space=hub, date=d, title=title, by=by, scope=scope, **kw)

        ev(day[0], "안전교육 주간", m["51"], end=day[4], place="각 교실")
        ev(day[0], "학년 협의회", m["41"], scope="g4", slot="pm", place="4학년 연구실")
        ev(day[1], "교직원 연수(디지털 교과서)", m["42"], slot="pm", place="시청각실", target="전 교직원")
        ev(day[1], "1학년 받아쓰기 평가", m["11"], scope="g1", slot="b1")
        ev(day[2], "5학년 현장체험학습", m["51"], scope="g5", place="과학관", target="5학년")
        ev(day[2], "학교운영위원회", m["admin"], slot="pm", place="회의실")
        ev(day[3], "학부모 공개수업", m["admin"], slot="b2", place="각 교실", target="전 학년")
        ev(day[4], "교내 독서 행사", m["31"], slot="b3", place="도서실")
        ev(day[0], "주간 교무회의", m["admin"], scope="office", slot="b1", place="교무실")
        ev(day[0], "연수 자료 정리", m["admin"], scope=ME, slot="b1")

        for d, who, kind, t in [(day[0], "박여름", "출장", "13:00~16:30"), (day[0], "윤솔", "조퇴", "15:00"),
                                (day[1], "최가을", "연가", ""), (day[3], "오누리", "외출", "10:00~11:30")]:
            Attendance.objects.create(space=hub, date=d, name=who, member=next(x for x in m.values() if x.name == who),
                                      kind=kind, time_text=t, by=m["office"])

        kst = timezone.get_current_timezone()
        self.collection(hub, m["admin"], "교실 기자재 점검 조사", datetime.combine(day[4], time(16, 30), kst), None,
                        [("노트북 대수", Field.Kind.NUMBER, []), ("프로젝터 상태", Field.Kind.SELECT, ["좋음", "점검 필요"]), ("비고", Field.Kind.TEXT, [])],
                        {"41": ["24", "좋음", ""], "11": ["20", "점검 필요", "전원이 자주 꺼져요"], "61": ["26", "좋음", ""]})
        last_fri = mon - timedelta(days=3)
        due = datetime.combine(last_fri, time(16, 30), kst)
        self.collection(hub, m["42"], "11월 교원 연수 이수 확인", due, due,
                        [("이수 시간", Field.Kind.NUMBER, []), ("연수 이름", Field.Kind.TEXT, [])],
                        {k: ["15", "디지털 교과서 활용"] for k in m})

        self.notice(hub, m["admin"], "학부모 공개수업 안내", True,
                    {"일시": f"{day[3].month}/{day[3].day} 2교시", "장소": "각 교실", "대상": "전 학년 학부모"},
                    "공개수업 지도안은 하루 전까지 학년 부장에게 보내 주세요.\n주차는 운동장 쪽 문을 이용해요.")
        self.notice(hub, m["42"], "교직원 연수 장소 안내", False,
                    {"일시": f"{day[1].month}/{day[1].day} 15:00", "장소": "시청각실", "준비물": "개인 노트북"},
                    "디지털 교과서 연수예요. 노트북 충전기를 꼭 챙겨 주세요.")

    def collection(self, hub, by, title, due, closed, fields, answers):
        aud = {"mode": "all"}
        rows = audience_rows(hub, aud)
        c = Collection.objects.create(space=hub, created_by=by, title=title, source=Collection.Source.SHEET, due=due, closed_at=closed,
                                      unit_head=audience_head(aud), audience=stored_audience(aud, rows, hub))
        make_rows(c, rows)
        fs = [Field.objects.create(collection=c, pos=i, name=n, kind=k, options=o) for i, (n, k, o) in enumerate(fields)]
        by_member = {r.member_id: r for r in c.rows.all()}
        tails = {mm.pk: t for t, mm in ((t, Member.objects.get(user__username=LOGIN[t])) for t in answers)}
        for pk, t in tails.items():
            row = by_member[pk]
            Cell.objects.bulk_create([Cell(row=row, field=f, value=v, by=row.who) for f, v in zip(fs, answers[t]) if v])

    def notice(self, hub, by, title, must, items, body):
        n = Notice.objects.create(space=hub, title=title, by=by, by_name=by.name, group=by.group, must=must)
        form = {"title": title, "body": body, "items": [{"name": k, "value": items.get(k, "")} for k in ("일시", "장소", "대상", "준비물")]}
        _add_version(n, form, by.name)
