"""P1 학교 전체 사용 유도: 숫자는 DB에서만, 기준 미달이면 숨김, 학교 공간·기관·school_taken에는 나오지 않는다."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import new_guest

from .models import Member, School, Space
from .views import NEAR_MIN, PICKED, PROOF_MIN, school_pitch, space_pitch

User = get_user_model()
SITE = "https://t.example"
PICK = {"atpt": "J10", "code": "7531234", "name": "새솔초등학교", "office": "경기도교육청", "address": "경기"}
CARD, BAND = "학교 전체로 쓰면 더 편해요", 'data-pitch="band"'
LINK = 'href="/school-plan"'


def n_text(n):
    return f"지금 {n}개 학교가 학교 전체로 쓰고 있어요"


def using_school(i, atpt="B10", **over):
    """학교 플랜을 쓰는 학교 하나(학교 공간 포함). 이름에 번호를 넣어 목록 노출을 잡는다."""
    school = School.objects.create(neis_atpt=atpt, neis_code=f"U{i:06d}", name=f"비공개{i}초등학교",
                                   **{"plan": School.Plan.SCHOOL, **over})
    Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name=school.name)
    return school


def grade_space(school, grade=4, *names):
    space = Space.objects.create(school=school, grade=grade, name=f"{grade}학년")
    seats = []
    for i, name in enumerate(names or ("이수빈",), 1):
        user = User.objects.create(username=f"google:{school.pk}-{grade}-{i}", first_name=name)
        seats.append(Member.objects.create(space=space, user=user, name=name, cls=f"{grade}-{i}"))
    return space, seats


class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        for name, value in (("search_schools", ([PICK], 1)), ("class_names", ["1", "2"]), ("meals", []), ("schedule", [])):
            p = mock.patch(f"spaces.neis.{name}", return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.home = School.objects.create(neis_atpt=PICK["atpt"], neis_code=PICK["code"], name=PICK["name"])

    def proof(self, n=PROOF_MIN):
        for i in range(n):
            using_school(i)


class Numbers(Base):
    def test_n_boundary(self):
        self.proof(PROOF_MIN - 1)
        self.assertNotIn("n", school_pitch())
        using_school(99)
        self.assertEqual(school_pitch()["n"], PROOF_MIN)

    def test_n_counts_only_schools_really_using_it(self):
        self.proof(PROOF_MIN - 1)
        yesterday = timezone.localdate() - timedelta(days=1)
        using_school(50, paid_until=yesterday)  # 만료
        using_school(51, plan=School.Plan.FREE)  # 무료
        org = School.objects.create(neis_atpt="B10", neis_code="", name="기관", plan=School.Plan.SCHOOL)  # 기관
        Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="기관")
        School.objects.create(neis_atpt="B10", neis_code="NOSPACE", name="공간 없음", plan=School.Plan.SCHOOL)
        merged = using_school(52)  # 합쳐진 공간뿐인 학교
        hub = Space.objects.create(school=self.home, kind=Space.Kind.SCHOOL, grade=0, name="허브")
        Space.objects.filter(school=merged).update(merged_into=hub)
        self.assertNotIn("n", school_pitch())

    def test_paid_until_today_counts(self):
        self.proof(PROOF_MIN - 1)
        using_school(60, paid_until=timezone.localdate())
        self.assertEqual(school_pitch()["n"], PROOF_MIN)

    def test_near_boundary_and_office(self):
        for i in range(NEAR_MIN - 1):
            using_school(i, atpt="J10")
        using_school(70, atpt="B10")
        self.assertNotIn("near", school_pitch("J10"))
        using_school(71, atpt="J10")
        pitch = school_pitch("J10")
        self.assertEqual((pitch["near"], pitch["office"]), (NEAR_MIN, "경기도교육청"))
        self.assertIn("경기도교육청", pitch["near_text"])

    def test_peers_exclude_my_space_and_merged(self):
        mine, _ = grade_space(self.home, 4, "나")
        other, _ = grade_space(self.home, 5, "가", "나", "다")
        gone, _ = grade_space(self.home, 6, "라")
        hub = Space.objects.create(school=self.home, kind=Space.Kind.SCHOOL, grade=0, name="허브")
        Space.objects.filter(pk=gone.pk).update(merged_into=hub)
        far = School.objects.create(neis_atpt="B10", neis_code="9", name="다른 학교")
        grade_space(far, 4, "마")
        pitch = school_pitch(space=mine)
        self.assertEqual((pitch["peers"], pitch["peer_people"]), (1, 3))
        self.assertEqual(pitch["peer_text"], "우리 학교 다른 동학년 1곳, 선생님 3명도 고티처를 쓰고 있어요.")
        self.assertEqual(school_pitch(PICK["atpt"], PICK["code"])["peers"], 2)  # 아직 공간이 없는 사람에게는 전부

    def test_no_peers_no_key(self):
        mine, _ = grade_space(self.home)
        self.assertNotIn("peers", school_pitch(space=mine))


@override_settings(SITE_URL=SITE)
class Message(Base):
    def test_plain_paragraphs_with_link_and_no_price_or_other_schools(self):
        self.proof()
        mine, (me,) = grade_space(self.home)
        msg = school_pitch(space=mine, me=me)["msg"]
        self.assertIn("새솔초등학교 4학년 이수빈", msg)
        self.assertIn(n_text(PROOF_MIN), msg)
        self.assertIn("견적서", msg)
        self.assertTrue(msg.endswith(f"{SITE}/school-plan"))
        self.assertNotIn("비공개", msg)
        self.assertNotRegex(msg, r"\d[\d,]*\s*원")
        self.assertNotIn("체험", msg)
        for line in msg.splitlines():
            self.assertEqual(line, line.strip())
            self.assertFalse(line[:1] in tuple("-*•·>#▶") if line else False, line)

    def test_message_without_proof_has_no_number(self):
        mine, (me,) = grade_space(self.home)
        self.assertNotIn("개 학교", school_pitch(space=mine, me=me)["msg"])

    def test_no_message_without_space_and_member(self):
        self.assertNotIn("msg", school_pitch(PICK["atpt"], PICK["code"]))


class SpacePitch(Base):
    def test_only_free_grade_spaces(self):
        mine, (me,) = grade_space(self.home)
        self.assertIn("msg", space_pitch(mine, me))
        school = using_school(2)
        self.assertEqual(space_pitch(school.spaces.get(), me), {})
        self.home.plan = School.Plan.SCHOOL
        self.home.save()
        self.assertEqual(space_pitch(Space.objects.get(pk=mine.pk), me), {})


class StartSteps(Base):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="google:new", first_name="신규")
        self.client.force_login(self.user)

    def pick(self):
        s = self.client.session
        s[PICKED] = PICK
        s.save()

    def test_step2_line_with_and_without_number(self):
        r = self.client.get("/")
        self.assertContains(r, "학교 전체가 함께 쓰려면")
        self.assertContains(r, "나중에 그대로 옮겨 와요")
        self.assertContains(r, LINK)
        self.assertNotContains(r, "개 학교가")
        self.proof()
        self.assertContains(self.client.get("/"), n_text(PROOF_MIN))

    def test_step2_org_tab_and_htmx_have_none(self):
        self.proof()
        for r in (self.client.get("/?kind=org"), self.client.get("/?q=새솔", HTTP_HX_REQUEST="true")):
            self.assertNotContains(r, "학교 전체가 함께 쓰려면")
            self.assertNotContains(r, n_text(PROOF_MIN))

    def test_step3_card_is_outside_the_swapped_form(self):
        self.proof()
        grade_space(self.home, 5, "가", "나")
        self.pick()
        r = self.client.get("/new/class")
        html = r.content.decode()
        self.assertContains(r, CARD)
        self.assertContains(r, "학교 전체로 시작하기")
        self.assertContains(r, "/school-plan?q=%EC%83%88%EC%86%94")
        self.assertContains(r, "우리 학교 다른 동학년 1곳, 선생님 2명도")
        self.assertContains(r, n_text(PROOF_MIN))
        self.assertGreater(html.index(CARD), html.index("</form>", html.index('id="classForm"')))
        self.assertLess(html.index('id="classForm"'), html.index("동학년 만들기</button>"))
        self.assertNotContains(self.client.get("/new/class?grade=3", HTTP_HX_REQUEST="true"), CARD)

    def test_step3_school_taken_has_none(self):
        self.proof()
        Space.objects.create(school=self.home, kind=Space.Kind.SCHOOL, grade=0, name="허브")
        self.pick()
        r = self.client.get("/new/class")
        self.assertContains(r, "학교 공간을 씁니다")
        self.assertNotContains(r, CARD)
        self.assertNotContains(r, n_text(PROOF_MIN))
        self.assertNotContains(r, "/school-plan")

    def test_step3_error_keeps_card(self):
        self.pick()
        r = self.client.post("/new/class", {"grade": "4", "cls": "", "name": "신규"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, CARD, status_code=400)


class GradeScreens(Base):
    def setUp(self):
        super().setUp()
        self.space, (self.me,) = grade_space(self.home, 4, "이수빈")
        self.client.force_login(self.me.user)
        self.base = f"/s/{self.space.token}"

    def test_made_card_with_copy(self):
        self.proof()
        r = self.client.get(f"{self.base}/made")
        self.assertContains(r, CARD)
        self.assertContains(r, "교무부장 선생님께 보낼 문구 복사")
        self.assertContains(r, "data-done=\"문구를 복사했어요. 쿨메신저에 붙여넣으세요.\"")
        self.assertContains(r, "data-text=\"안녕하세요.")
        self.assertContains(r, LINK)

    def test_grade_band_and_menu(self):
        r = self.client.get(f"{self.base}/")
        self.assertContains(r, BAND)
        self.assertContains(r, "학교 전체로 쓰기")
        self.assertContains(r, 'data-text="안녕하세요.')

    def test_today_band_only_with_peers_but_menu_always(self):
        self.proof()  # 전국 숫자가 있어도 같은 학교 근거가 없으면 띠는 없다
        r = self.client.get(f"{self.base}/today")
        self.assertNotContains(r, BAND)
        self.assertContains(r, "학교 전체로 쓰기")
        grade_space(self.home, 5, "가", "나")
        r = self.client.get(f"{self.base}/today")
        self.assertContains(r, BAND)
        self.assertContains(r, "우리 학교 다른 동학년 1곳, 선생님 2명도")

    def test_other_grade_creator_name_and_school_list_not_shown(self):
        self.proof()
        grade_space(self.home, 5, "숨은이름")
        for path in ("/", "/today", "/made"):
            r = self.client.get(self.base + path)
            self.assertNotContains(r, "숨은이름")
            self.assertNotContains(r, "비공개")

    def test_guest_member_sees_band_too(self):
        guest = new_guest("손님")
        Member.objects.create(space=self.space, user=guest, name="손님", cls="4-2")
        c = Client()
        c.force_login(guest)
        self.assertContains(c.get(f"{self.base}/"), BAND)

    def test_other_space_is_404(self):
        far = School.objects.create(neis_atpt="B10", neis_code="9", name="다른 학교")
        other, _ = grade_space(far)
        self.assertEqual(self.client.get(f"/s/{other.token}/made").status_code, 404)


class NoPitchInSchoolOrOrg(Base):
    def seat(self, kind, school):
        space = Space.objects.create(school=school, kind=kind, grade=0, name=school.name)
        user = User.objects.create(username=f"google:{kind}", first_name="관리")
        Member.objects.create(space=space, user=user, name="관리", cls="교무실", is_manager=True)
        self.client.force_login(user)
        return space

    def check(self, space):
        for path in ("today", "settings"):
            r = self.client.get(f"/s/{space.token}/{path}")
            self.assertEqual(r.status_code, 200)
            for word in (BAND, CARD, "학교 전체로 쓰기", "교무부장 선생님께", n_text(PROOF_MIN)):
                self.assertNotContains(r, word)

    def test_school_space(self):
        self.proof()
        self.home.plan = School.Plan.SCHOOL
        self.home.save()
        self.check(self.seat(Space.Kind.SCHOOL, self.home))
        self.assertNotContains(self.client.get("/school-plan"), "data-text=")

    def test_org_space(self):
        self.proof()
        org = School.objects.create(neis_atpt="B10", neis_code="", name="교육청 과", plan=School.Plan.SCHOOL)
        self.check(self.seat(Space.Kind.ORG, org))
        r = self.client.get("/school-plan")
        self.assertNotContains(r, n_text(PROOF_MIN + 1))
        self.assertNotContains(r, n_text(PROOF_MIN))


class PlanAndLanding(Base):
    def test_anonymous_plan_page_shows_number(self):
        r = Client().get("/school-plan")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "개 학교가")
        self.proof()
        r = Client().get("/school-plan")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, n_text(PROOF_MIN))
        self.assertNotContains(r, "data-text=")
        self.assertNotContains(r, "비공개")

    def test_org_tab_has_no_number(self):
        self.proof()
        self.assertNotContains(Client().get("/school-plan?kind=org"), n_text(PROOF_MIN))
        c = Client()
        c.force_login(User.objects.create(username="google:o", first_name="기관"))
        self.assertNotContains(c.get("/school-plan?kind=org"), n_text(PROOF_MIN))

    def test_grade_member_gets_peers_and_copy(self):
        self.proof()
        space, (me,) = grade_space(self.home)
        grade_space(self.home, 5, "가")
        self.client.force_login(me.user)
        r = self.client.get("/school-plan")
        self.assertContains(r, n_text(PROOF_MIN))
        self.assertContains(r, "우리 학교 다른 동학년 1곳, 선생님 1명도")
        self.assertContains(r, 'data-text="안녕하세요.')

    def test_school_already_on_has_no_copy(self):
        space, (me,) = grade_space(self.home)
        School.objects.filter(pk=self.home.pk).update(plan=School.Plan.SCHOOL)
        self.client.force_login(me.user)
        self.assertNotContains(self.client.get("/school-plan"), "data-text=")

    def test_landing_line(self):
        self.assertNotContains(Client().get("/"), "개 학교가")
        self.proof()
        r = Client().get("/")
        self.assertContains(r, n_text(PROOF_MIN) + ".")
        self.assertNotContains(r, "비공개")
