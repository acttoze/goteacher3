"""C7 교직원 정보만: 학생 이름·연락처로 보이는 칸 감지(규칙 표), 붙여넣기 경고·정리, 만들기 POST 거부."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from spaces.models import Member, School, Space

from .models import Collection
from .paste import student_info
from .presets import PRESETS

# (칸 이름, 학생 정보인가). 연락처·전화·주소·이메일은 학생 쪽 수식어가 붙을 때만(리드 결정).
RULES = [
    ("학생 이름", True), ("학생이름", True), ("학생명", True), ("학생 성명", True), ("참가 학생 명단", True),
    ("보호자 연락처", True), ("보호자 성명", True), ("학부모 휴대폰", True), ("학부모 전화", True),
    ("학생 전화번호", True), ("학생 주소", True), ("부모 이메일", True), ("학부모 성명", True),
    ("주민등록번호", True), ("교직원 주민번호", True), ("생년월일", True), ("아동명", True), ("아동 이름", True),
    ("비상 연락처", False), ("이메일 주소", False), ("휴대전화", False), ("연락처", False), ("집 주소", False),
    ("학부모 상담 횟수", False), ("보호자 동의 여부", False),
    ("학생 수", False), ("참가 학생 수", False), ("학생 참가 인원", False), ("불참 학생 수", False),
    ("학생(명)", False), ("담임", False), ("성명", False), ("교사명", False), ("담임 이름", False),
    ("아동학대 신고의무자 교육", False), ("개인정보보호 교육", False), ("학생자치 담당", False),
    ("참가(명)", False), ("희망 여부", False), ("이수번호", False), ("비고", False),
]


class StudentInfoRuleTests(SimpleTestCase):
    def test_presets_have_no_student_fields(self):
        for spec in PRESETS.values():
            for name, _ in spec["fields"]:
                self.assertFalse(student_info(name), name)


def _rule_test(name, want):
    def t(self):
        self.assertIs(student_info(name), want, name)
    return t


for _i, (_name, _want) in enumerate(RULES):
    setattr(StudentInfoRuleTests, f"test_rule_{_i:02d}_{'student' if _want else 'ok'}", _rule_test(_name, _want))

TSV = "반\t담임\t학생 이름\t보호자 연락처\t참가 여부\n4-1\t\t\t\t\n4-2\t\t\t\t\n"  # design SAMPLE


@override_settings(SITE_URL="https://t.goteacher.xyz")
class StudentInfoScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        cls.ua = get_user_model().objects.create(username="google:a")
        Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)

    def setUp(self):
        self.client.force_login(self.ua)

    def post(self, tail, space=None, **data):
        return self.client.post(f"/s/{(space or self.a).token}/c/{tail}", data)

    # ---- 붙여넣기 미리보기: design 경고
    def test_preview_warns(self):
        r = self.post("new/preview", paste=TSV)
        self.assertContains(r, "학생 개인정보로 보이는 열이 2개 있어요: 학생 이름, 보호자 연락처")
        self.assertContains(r, "학생 정보 · 받을 수 없어요", count=2)
        self.assertContains(r, 'data-pii="count"')
        self.assertContains(r, 'data-pii="drop"')
        self.assertContains(r, '<span class="warn">학생 이름</span>', html=True)
        self.assertNotContains(r, 'name="kind_0"')  # 학생 칸에는 형식 고르기가 없다
        self.assertContains(r, "받는 칸: 참가 여부<")  # 쪽지에도 학생 칸은 빠진다

    def test_preview_no_warning_for_counts(self):
        r = self.post("new/preview", paste="반\t담임\t참가 학생 수\t학생 참가 인원\n4-1\t\t\t\n")
        self.assertNotContains(r, "학생 개인정보")
        self.assertNotContains(r, "받을 수 없어요")

    def test_preview_resolved_count(self):
        r = self.post("new/preview", paste=TSV, pii="count")
        self.assertNotContains(r, 'role="alert"')
        self.assertContains(r, "「참가 인원(명)」 칸으로 바꿨어요")
        self.assertContains(r, "받는 칸: 참가 여부, 참가 인원(명)<")

    def test_preview_other_space_404(self):
        self.assertEqual(self.post("new/preview", self.b, paste=TSV).status_code, 404)

    # ---- 만들기 POST: 서버가 거부
    def test_create_rejected_with_student_cols(self):
        r = self.post("new", src="paste", title="학예회", paste=TSV)
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "학생 개인정보로 보이는 열을 먼저 정리해 주세요.", status_code=400)
        self.assertContains(r, 'data-pii="count"', status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_create_rejected_unknown_choice(self):
        self.assertEqual(self.post("new", src="paste", title="학예회", paste=TSV, pii="keep").status_code, 400)
        self.assertFalse(Collection.objects.exists())

    def test_create_count(self):
        r = self.post("new", src="paste", title="학예회", paste=TSV, pii="count", kind_1="number")
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.get()
        self.assertEqual([(f.name, f.kind) for f in c.fields.all()], [("참가 여부", "select"), ("참가 인원(명)", "number")])

    def test_create_drop(self):
        self.assertEqual(self.post("new", src="paste", title="학예회", paste=TSV, pii="drop").status_code, 302)
        self.assertEqual([f.name for f in Collection.objects.get().fields.all()], ["참가 여부"])

    def test_create_drop_nothing_left(self):
        r = self.post("new", src="paste", title="명단", paste="반\t학생 이름\n4-1\t\n", pii="drop")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Collection.objects.exists())

    def test_sheet_rejected(self):
        r = self.post("new", src="sheet", title="명단", col_name_0="참가(명)", col_name_1="학부모 연락처")
        self.assertContains(r, "학생 개인정보로 보이는 칸이 있어요: 학부모 연락처", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_sheet_counts_allowed(self):
        self.assertEqual(self.post("new", src="sheet", title="인원", col_name_0="참가 학생 수").status_code, 302)

    def test_other_space_create_404(self):
        self.assertEqual(self.post("new", self.b, src="paste", title="x", paste=TSV, pii="count").status_code, 404)
        self.assertFalse(Collection.objects.exists())


# ---- QA(C7) 학교 현장 칸 이름 경계
QA_MISSED = ["대상 학생", "수상 학생", "학생", "보호자 성함", "학부모 성함", "학생(이름)", "학생 메일", "학생 E-mail"]
QA_FALSE_ALARM = ["학생명찰 구입 수량", "학부모 전화상담(건)", "지역 주민 참여 인원", "학생명부 제출 여부"]


class StudentInfoQAEdgeTests(SimpleTestCase):
    def test_missed_student_columns(self):
        missed = [n for n in QA_MISSED if not student_info(n)]
        self.assertEqual(missed, [])

    def test_false_alarms_on_staff_fields(self):
        flagged = [n for n in QA_FALSE_ALARM if student_info(n)]
        self.assertEqual(flagged, [])

    def test_staff_name_and_class_ok(self):
        for n in ["성명", "이름", "교사 성명", "학년반", "학년 반", "반", "담임 성명", "수상자", "번호"]:
            self.assertFalse(student_info(n), n)

    def test_any_birth_or_rrn(self):
        for n in ["생년 월 일", "주민번호 앞자리", "생년월일(6자리)"]:
            self.assertTrue(student_info(n), n)
