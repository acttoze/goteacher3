"""W2 학교 이용 상시 버튼·확인 화면·유도 목적지: 어디서 눌러도 1년 무료 전환(school-start)으로 간다."""
from datetime import date
from unittest import mock

from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import new_guest

from .merge import free_until
from .models import Member, School, Space
from .test_pitch import PICK, Base, User, grade_space, using_school
from .views import PICKED, school_pitch

CTA_CLASS = 'class="school-cta"'
FORBIDDEN = ("상당", "특가", "인증", "학교 확인", "재직 확인")


def start_url(space):
    return f"/s/{space.token}/school-start"


def login(user):
    c = Client()
    c.force_login(user)
    return c


def day(d):
    return f"{d.year}년 {d.month}월 {d.day}일"


def open_hub(school):
    School.objects.filter(pk=school.pk).update(plan=School.Plan.SCHOOL, paid_until=date(2030, 1, 1), self_opened_at=timezone.now())
    return Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name=school.name)


def paid_hub(school):
    """일반 유료 학교의 학교 공간(합류 경로가 없다, W4)."""
    hub = open_hub(school)
    School.objects.filter(pk=school.pk).update(self_opened_at=None)
    return hub


class Seated(Base):
    def setUp(self):
        super().setUp()
        self.space, (self.lead, self.peer) = grade_space(self.home, 4, "김대표", "이동료")
        self.c = login(self.lead.user)
        self.base = f"/s/{self.space.token}"
        self.url = start_url(self.space)


class SideButton(Seated):
    def test_on_every_grade_screen_right_above_my_card(self):
        for path in ("/", "/today", "/schedule", "/c/", "/c/new", "/settings", "/school-start"):
            r = self.c.get(self.base + path)
            self.assertEqual(r.status_code, 200, path)
            html = r.content.decode()
            cta = html.index(CTA_CLASS)
            self.assertIn(f'href="{self.url}"', html[cta - 80:cta + 200], path)
            self.assertIn("학교 이용 시작", html[cta:html.index('id="meCard"')], path)
            self.assertIn("1년 무료", html[cta:html.index('id="meCard"')], path)
            self.assertLess(html.rindex("</nav>", 0, html.index('id="meCard"')), cta, path)

    def test_old_menu_item_is_gone(self):
        self.assertNotContains(self.c.get(self.base + "/today"), "학교 전체로 쓰기")

    def test_non_lead_and_guest_see_it_too(self):
        guest = Member.objects.create(space=self.space, user=new_guest("손님"), name="손님", cls="4-3")
        for user in (self.peer.user, guest.user):
            self.assertContains(login(user).get(self.base + "/today"), CTA_CLASS)

    def test_says_opened_when_school_space_exists(self):
        open_hub(self.home)
        r = self.c.get(self.base + "/today")
        self.assertContains(r, "우리 학교 공간이 열렸어요")
        self.assertNotContains(r, "학교 이용 시작")
        self.assertContains(r, f'href="{self.url}"')

    def test_not_on_school_or_org_space(self):
        hub = using_school(1)
        org = School.objects.create(neis_atpt="B10", neis_code="", name="기관", plan=School.Plan.SCHOOL)
        for school, kind in ((hub, None), (org, Space.Kind.ORG)):
            space = school.spaces.first() or Space.objects.create(school=school, kind=kind, grade=0, name="기관")
            user = User.objects.create(username=f"google:s{school.pk}", first_name="관리")
            Member.objects.create(space=space, user=user, name="관리", cls="교무", is_manager=True)
            r = login(user).get(f"/s/{space.token}/today")
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, CTA_CLASS)
            self.assertNotContains(r, "school-start")


class ConfirmPage(Seated):
    def test_title_and_four_lines(self):
        r = self.c.get(self.url)
        end = free_until(timezone.localdate())
        self.assertContains(r, "새솔초등학교를 1년 무료로 시작해요")
        for text in (day(end), "카드·결제 정보", "자동 결제", "김대표 선생님이 관리자", "넘길 수", "우리 4학년 2명",
                     "다른 학년은 그대로", "원할 때 각자 합류", "연장하지 않으면", "읽기만", "취합·오늘·일정은 계속",
                     "이 학교 교직원이에요. 우리 학년이 학교 공간으로 옮겨지는 걸 확인했어요", "1년 무료로 시작</button>",
                     "교무부장 선생님께 먼저 물어보기"):
            self.assertContains(r, text)
        for word in FORBIDDEN:
            self.assertNotContains(r, word)

    def test_cannot_start_without_check(self):
        r = self.c.post(self.url, {})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "체크해 주세요", status_code=400)
        self.assertFalse(Space.objects.filter(school=self.home, grade=0).exists())

    def test_start_with_check(self):
        r = self.c.post(self.url, {"agree": "1"})
        hub = Space.objects.get(school=self.home, grade=0)
        self.assertEqual((r.status_code, r["Location"]), (303, f"/s/{hub.token}/school"))

    @override_settings(SITE_URL="https://t.example")
    def test_non_lead_copies_message_to_lead(self):
        r = login(self.peer.user).get(self.url)
        self.assertContains(r, "대표 선생님께 보낼 문구 복사")
        self.assertContains(r, "김대표 선생님이 관리자")
        self.assertContains(r, 'data-text="김대표 선생님,')
        self.assertContains(r, f"https://t.example{self.url}")
        self.assertNotContains(r, 'name="agree"')
        self.assertNotContains(r, "교무부장 선생님께 먼저")

    def test_guest_links_account(self):
        guest = Member.objects.create(space=self.space, user=new_guest("손님"), name="손님", cls="4-3")
        r = login(guest.user).get(self.url)
        self.assertContains(r, f"/auth/google/login?next={self.url}")
        self.assertContains(r, f"/signup?next={self.url}")
        self.assertNotContains(r, 'name="agree"')

    def test_opened_state(self):
        paid_hub(self.home)
        r = self.c.get(self.url)
        self.assertContains(r, "우리 학교 공간이 열렸어요")
        self.assertContains(r, "이미 학교 공간")
        self.assertNotContains(r, 'name="agree"')
        self.assertNotContains(r, "1년 무료로 시작해요")

    def test_other_space_is_404(self):
        other, (stranger,) = grade_space(self.home, 5, "남")
        self.assertEqual(login(stranger.user).get(self.url).status_code, 404)
        self.assertEqual(login(stranger.user).post(self.url, {"agree": "1"}).status_code, 404)
        self.assertFalse(Space.objects.filter(school=self.home, grade=0).exists())


class Message(Seated):
    def msg(self):
        return school_pitch(space=self.space, me=self.lead)["msg"]

    def test_one_year_free_terms(self):
        msg = self.msg()
        for text in (day(free_until(timezone.localdate())), "비용이 없", "자동 결제", "다른 학년은 지금 쓰는 그대로"):
            self.assertIn(text, msg)

    def test_no_amount_quote_or_bullets(self):
        self.proof()
        msg = self.msg()
        self.assertNotRegex(msg, r"\d[\d,]*\s*(만\s*)?원")
        self.assertNotIn("견적", msg)
        for word in FORBIDDEN:
            self.assertNotIn(word, msg)
        for line in msg.splitlines():
            self.assertEqual(line, line.strip())
            self.assertFalse(line[:1] in tuple("-*•·>#▶") if line else False, line)

    def test_no_proposal_when_already_open(self):
        open_hub(self.home)
        pitch = school_pitch(space=Space.objects.get(pk=self.space.pk), me=self.lead)
        self.assertTrue(pitch["opened"])
        self.assertNotIn("msg", pitch)


class StartStep3(Base):
    FORM = {"grade": "4", "cls": "4-1", "name": "신규"}

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="google:new", first_name="신규")
        self.client.force_login(self.user)
        s = self.client.session
        s[PICKED] = PICK
        s.save()

    def test_second_submit_shown_in_form_and_htmx(self):
        for r in (self.client.get("/new/class"), self.client.get("/new/class?grade=3", HTTP_HX_REQUEST="true")):
            self.assertContains(r, 'name="then" value="school"')
            self.assertContains(r, "학교로 시작 · 1년 무료")

    def test_card_has_no_button_but_says_free(self):
        self.proof()
        html = self.client.get("/new/class").content.decode()
        card = html[html.index('<aside class="card pitch-card"'):]
        self.assertIn("1년 무료", card)
        self.assertIn("지금 10개 학교가", card)
        self.assertNotIn("btn", card[:card.index("</aside>")])

    def test_then_school_creates_grade_and_goes_to_confirm(self):
        r = self.client.post("/new/class", {**self.FORM, "then": "school"})
        space = Space.objects.get(school=self.home, grade=4)
        self.assertEqual(r["Location"], start_url(space))
        self.assertEqual(space.members.get().user, self.user)
        self.assertContains(self.client.get(r["Location"]), 'name="agree"')
        self.assertFalse(Space.objects.filter(school=self.home, grade=0).exists())  # 만들기만으로 전환되지 않는다

    def test_plain_submit_still_goes_to_made(self):
        r = self.client.post("/new/class", self.FORM)
        self.assertEqual(r["Location"], f"/s/{Space.objects.get(school=self.home, grade=4).token}/made")

    def test_error_keeps_input(self):
        r = self.client.post("/new/class", {"grade": "4", "cls": "", "name": "입력값", "then": "school"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "반을 하나 골라 주세요", status_code=400)
        self.assertContains(r, 'value="입력값"', status_code=400)
        self.assertFalse(Space.objects.filter(school=self.home, grade=4).exists())

    def test_taken_grade_same_as_before(self):
        grade_space(self.home, 4, "먼저")
        r = self.client.post("/new/class", {**self.FORM, "then": "school"})
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, "이미 있어요", status_code=409)

    def test_opened_school_has_no_second_submit(self):
        open_hub(self.home)
        r = self.client.get("/new/class")
        self.assertNotContains(r, 'value="school"')
        self.assertNotContains(r, "1년 무료")
        self.assertContains(r, "학교 공간이 열렸어요")


class Destinations(Seated):
    def test_made_lead_button_and_secondary_copy(self):
        r = self.c.get(self.base + "/made")
        self.assertContains(r, f'href="{self.url}">학교로 시작 · 1년 무료')
        self.assertContains(r, "교무부장 선생님께 보낼 문구 복사")

    def test_made_non_lead_has_copy_only(self):
        r = login(self.peer.user).get(self.base + "/made")
        self.assertNotContains(r, "학교로 시작 · 1년 무료")
        self.assertContains(r, "교무부장 선생님께 보낼 문구 복사")

    def test_grade_band_goes_to_confirm(self):
        html = self.c.get(self.base + "/").content.decode()
        band = html[html.index('data-pitch="band"'):]
        band = band[:band.index("</div>\n</div>")]
        self.assertIn(f'href="{self.url}"', band)
        self.assertIn("1년 무료", band)
        self.assertNotIn('href="/school-plan"', band)

    def test_today_band_goes_to_confirm(self):
        grade_space(self.home, 5, "가")
        html = self.c.get(self.base + "/today").content.decode()
        self.assertIn(f'href="{self.url}"', html[html.index('data-pitch="band"'):])

    def test_step2_line_says_free(self):
        c = login(User.objects.create(username="google:s2", first_name="새"))
        html = c.get("/").content.decode()
        self.assertIn("1년 무료", html[html.index('data-pitch="band"'):])


class PlanPage(Seated):
    def top(self, client):
        html = client.get("/school-plan").content.decode()
        return html[html.index('<div class="title">'):html.index('<div class="plan">')]

    def test_grade_member_goes_to_confirm(self):
        for user in (self.lead.user, self.peer.user):
            self.assertIn(f'href="{self.url}"', self.top(login(user)))

    def test_anonymous_goes_to_start(self):
        top = self.top(Client())
        self.assertIn('href="/start"', top)
        self.assertIn("1년 무료로 시작", top)

    def test_signed_in_without_space_goes_home(self):
        self.assertIn('href="/"', self.top(login(User.objects.create(username="google:n", first_name="새"))))

    def test_school_and_org_members_have_no_button(self):
        hub = using_school(1).spaces.get()
        user = User.objects.create(username="google:h", first_name="관리")
        Member.objects.create(space=hub, user=user, name="관리", cls="교무", is_manager=True)
        self.assertNotIn("1년 무료로 시작", self.top(login(user)))
        self.assertNotIn("1년 무료로 시작", Client().get("/school-plan?kind=org").content.decode())

    def test_no_free_start_when_school_already_open(self):
        open_hub(self.home)
        self.assertNotIn("1년 무료로 시작", self.top(self.c))

    def test_paid_application_stays_as_extension(self):
        r = self.c.get("/school-plan")
        self.assertContains(r, "연장·정식 도입")
        self.assertContains(r, 'name="by_name"')


class Landing(Base):
    def test_says_school_year_free(self):
        r = Client().get("/")
        self.assertContains(r, "학교 전체 1년 무료")
        self.assertNotContains(r, "동학년판")


class Proof(Seated):
    @mock.patch("spaces.views.PROOF_MIN", 3)
    def test_self_opened_schools_are_not_counted(self):
        for i in range(2):
            using_school(i)
        using_school(9, self_opened_at=timezone.now())
        self.assertNotIn("n", school_pitch())
