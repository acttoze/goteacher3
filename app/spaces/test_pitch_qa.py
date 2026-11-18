"""P1 QA: test_pitch.py가 다루지 않는 경계(이스케이프, 화면별 숨김 기준, 안내 화면의 POST·htmx, 약속 문구, 만들기 흐름)."""
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client

from .models import Member, School, Space
from .test_pitch import BAND, CARD, PICK, Base, grade_space, n_text, using_school
from .views import PICKED, PROOF_MIN, school_pitch

User = get_user_model()
NASTY = 'A"><script>alert(1)</script>\'&'
FORBIDDEN = ("체험", "인증", "할인", "보장", "환불", "1위", "공문", "지원금", "약정")


class Attrs(HTMLParser):
    """화면이 깨지지 않았는지: 실제 파서가 읽은 data-text 값과 script 태그."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.texts, self.inline = [], []
        self._in_script = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-text" in attrs:
            self.texts.append(attrs["data-text"])
        self._in_script = tag == "script"

    def handle_endtag(self, tag):
        self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.inline.append(data)


def body(response):
    return response.content.decode()


class Seated(Base):
    def seat(self, grade=4, name="이수빈", school=None):
        space, (me,) = grade_space(school or self.home, grade, name)
        self.client.force_login(me.user)
        return space, me

    def newcomer(self, pick=PICK):
        self.client.force_login(User.objects.create(username="google:newcomer", first_name="신규"))
        s = self.client.session
        s[PICKED] = pick
        s.save()


class Escaping(Seated):
    def test_quotes_and_brackets_in_names_survive_on_every_copy_button(self):
        School.objects.filter(pk=self.home.pk).update(name=f"학교{NASTY}")
        self.home.refresh_from_db()
        space, me = self.seat(name=f"교사{NASTY}")
        want = school_pitch(space=space, me=me)["msg"]
        self.assertIn(NASTY, want)
        for path in (f"/s/{space.token}/", f"/s/{space.token}/made", "/school-plan"):
            html = body(self.client.get(path))
            self.assertNotIn("<script>alert(1)", html, path)
            self.assertEqual(Attrs(html).texts, [want], path)
            self.assertFalse([s for s in Attrs(html).inline if "alert(1)" in s], path)

    def test_today_band_copy_is_escaped(self):
        grade_space(self.home, 5, "가")
        space, me = self.seat(name=f"교사{NASTY}")
        html = body(self.client.get(f"/s/{space.token}/today"))
        self.assertNotIn("<script>alert(1)", html)
        self.assertEqual(Attrs(html).texts, [school_pitch(space=space, me=me)["msg"]])

    def test_step3_school_name_is_escaped(self):
        self.newcomer({**PICK, "code": "7000001", "name": f"학교{NASTY}"})
        self.assertNotIn("<script>alert(1)", body(self.client.get("/new/class")))


class HiddenBelowThreshold(Seated):
    def surfaces(self, space):
        anon = Client()
        yield "소개", anon.get("/")
        yield "플랜(비로그인)", anon.get("/school-plan")
        yield "made", self.client.get(f"/s/{space.token}/made")
        yield "동학년", self.client.get(f"/s/{space.token}/")
        yield "플랜(회원)", self.client.get("/school-plan")

    def test_nine_schools_never_show_a_count_anywhere_and_ten_do(self):
        self.proof(PROOF_MIN - 1)
        space, _ = self.seat()
        for name, r in self.surfaces(space):
            self.assertEqual(r.status_code, 200, name)
            self.assertNotRegex(body(r).replace("&quot;", '"'), r"\d+개 학교", name)
        using_school(500)
        for name, r in self.surfaces(space):
            self.assertIn(n_text(PROOF_MIN), body(r), name)

    def test_office_count_on_step3_card_boundary(self):
        for i in range(2):
            using_school(i, atpt=PICK["atpt"])
        self.newcomer()
        self.assertNotContains(self.client.get("/new/class"), "경기도교육청에서는")
        using_school(2, atpt=PICK["atpt"])
        self.assertContains(self.client.get("/new/class"), "경기도교육청에서는 3개 학교가 쓰고 있어요.")


class Counting(Base):
    def test_expired_and_free_schools_do_not_count_for_office(self):
        for i in range(2):
            using_school(i, atpt=PICK["atpt"])
        using_school(3, atpt=PICK["atpt"], plan=School.Plan.FREE)
        self.assertNotIn("near", school_pitch(PICK["atpt"]))

    def test_same_code_in_another_office_is_another_school(self):
        mine, _ = grade_space(self.home, 4, "나")
        twin = School.objects.create(neis_atpt="B10", neis_code=PICK["code"], name="동명")
        grade_space(twin, 5, "가")
        self.assertNotIn("peers", school_pitch(space=mine))

    def test_empty_grade_space_is_not_a_peer(self):
        mine, _ = grade_space(self.home, 4, "나")
        Space.objects.create(school=self.home, grade=5, name="빈 동학년")
        self.assertNotIn("peers", school_pitch(space=mine))

    def test_my_other_members_are_excluded_with_my_space(self):
        mine, _ = grade_space(self.home, 4, "나", "다", "라")
        other, _ = grade_space(self.home, 5, "가")
        pitch = school_pitch(space=mine)
        self.assertEqual((pitch["peers"], pitch["peer_people"]), (1, 1))
        self.assertEqual(school_pitch(space=other)["peer_people"], 3)


class NoLeak(Seated):
    def test_names_stay_hidden_on_start_and_plan_screens(self):
        self.proof()
        grade_space(self.home, 5, "숨은이름")
        self.newcomer()
        for path in ("/", "/new/class", "/school-plan"):
            r = self.client.get(path)
            self.assertNotContains(r, "숨은이름")
            self.assertNotContains(r, "비공개")

    def test_copy_text_has_no_peer_name_or_school_list(self):
        self.proof()
        grade_space(self.home, 5, "숨은이름")
        space, me = self.seat()
        msg = school_pitch(space=space, me=me)["msg"]
        self.assertNotIn("숨은이름", msg)
        self.assertNotIn("비공개", msg)


class NoticeAndOrgPaths(Seated):
    def assert_clean(self, r):
        for word in (CARD, BAND, n_text(PROOF_MIN), "학교 전체가 함께", "교무부장 선생님께"):
            self.assertNotContains(r, word, status_code=r.status_code)

    def test_school_notice_on_post_and_htmx(self):
        self.proof()
        Space.objects.create(school=self.home, kind=Space.Kind.SCHOOL, grade=0, name="허브")
        self.newcomer()
        r = self.client.post("/new/class", {"grade": "4", "cls": "4-1", "name": "신규"})
        self.assertEqual(r.status_code, 409)
        self.assert_clean(r)
        self.newcomer_session_again()
        self.assert_clean(self.client.get("/new/class", HTTP_HX_REQUEST="true"))

    def newcomer_session_again(self):
        s = self.client.session
        s[PICKED] = PICK
        s.save()

    def test_notice_stays_clean_after_plan_expired(self):
        self.proof()
        School.objects.filter(pk=self.home.pk).update(plan=School.Plan.FREE)
        Space.objects.create(school=self.home, kind=Space.Kind.SCHOOL, grade=0, name="허브")
        self.newcomer()
        self.assert_clean(self.client.get("/new/class"))

    def test_org_form_error_has_none(self):
        self.proof()
        self.client.force_login(User.objects.create(username="google:o2", first_name="기관"))
        r = self.client.post("/new/org", {"atpt": "", "org": "", "name": ""})
        self.assertEqual(r.status_code, 400)
        self.assert_clean(r)

    def test_plan_org_post_error_has_no_count(self):
        self.proof()
        self.client.force_login(User.objects.create(username="google:o3", first_name="기관"))
        r = self.client.post("/school-plan", {"kind": "org"})
        self.assertEqual(r.status_code, 400)
        self.assertNotContains(r, n_text(PROOF_MIN), status_code=400)

    def test_org_member_screens_have_no_menu_or_copy(self):
        self.proof()
        org = School.objects.create(neis_atpt="B10", neis_code="", name="기관", plan=School.Plan.SCHOOL)
        space = Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="기관")
        user = User.objects.create(username="google:o4", first_name="관리")
        Member.objects.create(space=space, user=user, name="관리", cls="부서", is_manager=True)
        self.client.force_login(user)
        for path in (f"/s/{space.token}/today", f"/s/{space.token}/c/", "/school-plan"):
            r = self.client.get(path)
            self.assert_clean(r)
            self.assertNotContains(r, "학교 전체로 쓰기")
            self.assertFalse(Attrs(body(r)).texts, path)


class Promises(Seated):
    def test_pitch_copy_promises_nothing_unbuilt(self):
        self.proof()
        for i in range(3):
            using_school(100 + i, atpt=PICK["atpt"])
        grade_space(self.home, 5, "가")
        space, me = self.seat()
        pitch = school_pitch(space=space, me=me)
        pieces = [pitch["msg"], pitch["n_text"], pitch["near_text"], pitch["peer_text"],
                  render_to_string("spaces/_pitch_card.html", {"pitch": pitch, "top": 0}),
                  render_to_string("spaces/_pitch_card.html", {"pitch": {}, "top": 0, "school": PICK}),
                  render_to_string("spaces/_pitch_band.html", {"pitch": pitch}),
                  render_to_string("spaces/_pitch_band.html", {"pitch": {}, "lead": "학교 전체가 함께 쓰려면",
                                                               "tail": "동학년으로 시작해도 쓰던 내용은 나중에 그대로 옮겨 와요."})]
        for piece in pieces:
            for word in FORBIDDEN:
                self.assertNotIn(word, piece)


class CreateFlow(Seated):
    def test_htmx_grade_switch_returns_only_the_form(self):
        self.newcomer()
        r = self.client.get("/new/class?grade=3", HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        html = body(r)
        self.assertIn('id="classForm"', html)
        self.assertIn('data-v="3-1"', html)
        self.assertNotIn("<html", html.lower())
        self.assertNotIn("st-cols", html)

    def test_errors_keep_input_and_session_then_create_lands_on_made_card(self):
        self.newcomer()
        r = self.client.post("/new/class", {"grade": "4", "cls": "", "name": "신규"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "반을 하나 골라 주세요", status_code=400)
        r = self.client.post("/new/class", {"grade": "4", "cls": "4-1", "name": "   "})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "이름을 써 주세요", status_code=400)
        r = self.client.post("/new/class", {"grade": "4", "cls": "4-1", "name": "신규"})
        space = Space.objects.get(school=self.home, grade=4)
        self.assertRedirects(r, f"/s/{space.token}/made", fetch_redirect_response=False)
        made = self.client.get(r["Location"])
        self.assertContains(made, "동학년을 만들었어요")
        self.assertContains(made, CARD)
        self.assertEqual(Attrs(body(made)).texts, [school_pitch(space=space, me=space.members.get())["msg"]])

    def test_taken_grade_is_409_with_message_and_no_creator_name(self):
        grade_space(self.home, 4, "숨은이름")
        self.newcomer()
        r = self.client.post("/new/class", {"grade": "4", "cls": "4-1", "name": "신규"})
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, "이미 있어요", status_code=409)
        self.assertNotContains(r, "숨은이름", status_code=409)
        self.assertEqual(Space.objects.filter(school=self.home, grade=4).count(), 1)


class SchoolMemberOnPlanPage(Seated):
    def test_school_space_member_gets_no_proposal_on_plan_page(self):
        self.proof()
        School.objects.filter(pk=self.home.pk).update(plan=School.Plan.SCHOOL)
        space = Space.objects.create(school=self.home, kind=Space.Kind.SCHOOL, grade=0, name="허브")
        user = User.objects.create(username="google:s1", first_name="교사")
        Member.objects.create(space=space, user=user, name="교사", cls="4-1")
        self.client.force_login(user)
        r = self.client.get("/school-plan")
        self.assertFalse(Attrs(body(r)).texts)
        self.assertNotContains(r, "교무부장 선생님께")
        self.assertNotContains(r, "우리 학교 다른 동학년")
