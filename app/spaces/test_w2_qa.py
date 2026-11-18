"""W2 QA: test_w2_entry가 다루지 않은 경계. 열린 학교의 비대표·손님, 종료일 경계, 값 이스케이프, 3단계 예외 제출, 화면 전체 문구 검사."""
import html
import re
from datetime import date, timedelta
from unittest import mock

from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import new_guest

from .merge import open_free
from .models import Member, PlanRequest, School, Space
from .test_pitch import PICK, Base, User, grade_space, using_school
from .test_w2_entry import CTA_CLASS, day, login, open_hub, paid_hub, start_url
from .views import PICKED, PROOF_MIN, school_pitch

AMOUNT = r"\d[\d,]*\s*(만\s*)?원"
BANNED = ("특가", "상당", "할인", "인증 학교", "공식 인증", "학교 인증", "재직 인증")
EVIL = '"><script>alert(1)</script>'
FORM = {"by_name": "김대표", "position": "교사", "email": "lead@school.example", "students": "", "classes": ""}


def text_of(response):
    return html.unescape(re.sub(r"<(script|style)\b.*?</\1>|<[^>]+>", " ", response.content.decode(), flags=re.S))


def data_texts(response):
    return [html.unescape(v) for v in re.findall(r'data-text="([^"]*)"', response.content.decode())]


class Seated(Base):
    def setUp(self):
        super().setUp()
        self.space, (self.lead, self.peer) = grade_space(self.home, 4, "김대표", "이동료")
        self.guest = Member.objects.create(space=self.space, user=new_guest("손님"), name="손님", cls="4-3")
        self.c = login(self.lead.user)
        self.base = f"/s/{self.space.token}"
        self.url = start_url(self.space)

    def hubs(self):
        return Space.objects.filter(school=self.home, grade=0).count()


class OpenedForEveryone(Seated):
    def test_peer_and_guest_see_opened_and_cannot_post(self):
        paid_hub(self.home)
        for user in (self.lead.user, self.peer.user, self.guest.user):
            c = login(user)
            r = c.get(self.url)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "이미 학교 공간")
            self.assertNotContains(r, 'name="agree"')
            self.assertNotContains(r, "계정 회원만")
            self.assertNotContains(r, "보낼 문구 복사")
            self.assertEqual(c.post(self.url, {"agree": "1"}).status_code, 409)
        self.assertEqual(self.hubs(), 1)
        self.assertTrue(Space.objects.filter(pk=self.space.pk, merged_into__isnull=True).exists())

    def test_expired_self_opened_school_still_says_opened(self):
        open_hub(self.home)
        School.objects.filter(pk=self.home.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        r = self.c.get(self.base + "/today")
        self.assertContains(r, "우리 학교 공간이 열렸어요")
        self.assertNotContains(r, "학교 이용 시작")

    def test_paid_school_grade_space_says_opened_without_band(self):
        School.objects.filter(pk=self.home.pk).update(plan=School.Plan.SCHOOL, paid_until=date(2099, 1, 1))
        Space.objects.create(school=self.home, kind=Space.Kind.SCHOOL, grade=0, name=self.home.name)
        r = self.c.get(self.base + "/")
        self.assertContains(r, "우리 학교 공간이 열렸어요")
        self.assertNotContains(r, 'data-pitch="band"')

    def test_other_grade_flips_right_after_real_open(self):
        other, (lead5,) = grade_space(self.home, 5, "오대표")
        open_free(self.space, self.lead)
        c = login(lead5.user)
        self.assertContains(c.get(f"/s/{other.token}/today"), "우리 학교 공간이 열렸어요")
        r = c.get(start_url(other))
        self.assertContains(r, "우리 학년 합류")  # W4: 시작 대신 합류 확인
        self.assertNotContains(r, "1년 무료로 시작해요")
        self.assertEqual(Member.objects.filter(space=other).count(), 1)  # 누르기 전까지 다른 학년은 그대로


class EndDate(Seated):
    def surfaces(self):
        peer = login(self.peer.user)
        return (self.c.get(self.url).content.decode(), self.c.get("/school-plan").content.decode(),
                html.unescape(school_pitch(space=self.space, me=self.lead)["msg"]),
                data_texts(peer.get(self.url))[0])

    def assert_all_say(self, today, end):
        with mock.patch("django.utils.timezone.localdate", return_value=today):
            for i, page in enumerate(self.surfaces()):
                self.assertIn(day(end), page, i)

    def test_last_cutoff_day(self):
        self.assert_all_say(date(2027, 2, 28), date(2028, 2, 29))

    def test_day_after_cutoff(self):
        self.assert_all_say(date(2027, 3, 1), date(2028, 3, 1))


class Confirm(Seated):
    def test_error_page_keeps_every_line(self):
        r = self.c.post(self.url, {})
        for text in ("카드·결제 정보", "관리자가 돼요", "다른 학년은 그대로", "연장하지 않으면", 'name="agree"', "체크해 주세요"):
            self.assertContains(r, text, status_code=400)

    def test_guest_line_only_when_guests_exist(self):
        self.assertContains(self.c.get(self.url), "이름만 들어온 1명")
        self.guest.delete()
        self.assertNotContains(self.c.get(self.url), "이름만 들어온")

    def test_guest_lead_blocks_start_and_peer_is_told_who_leads(self):
        space, (first,) = grade_space(self.home, 6, "먼저")
        first.delete()
        lead = Member.objects.create(space=space, user=new_guest("손대표"), name="손대표", cls="6-1")
        peer = Member.objects.create(space=space, user=User.objects.create(username="google:p6", first_name="뒤"), name="뒤", cls="6-2")
        url = start_url(space)
        self.assertContains(login(lead.user).get(url), "계정 회원만")
        self.assertEqual(login(lead.user).post(url, {"agree": "1"}).status_code, 403)
        r = login(peer.user).get(url)
        self.assertContains(r, "손대표 선생님께 제안")
        self.assertEqual(login(peer.user).post(url, {"agree": "1"}).status_code, 403)
        self.assertEqual(self.hubs(), 0)

    def test_no_amount_on_any_state(self):
        for user in (self.lead.user, self.peer.user, self.guest.user):
            page = text_of(login(user).get(self.url))
            self.assertNotRegex(page, AMOUNT)
            for word in BANNED:
                self.assertNotIn(word, page)


class Messages(Seated):
    def test_lead_message_is_plain_paragraphs(self):
        msg = data_texts(login(self.peer.user).get(self.url))[0]
        self.assertTrue(msg.startswith("김대표 선생님,"))
        self.assertNotRegex(msg, AMOUNT)
        for line in msg.splitlines():
            self.assertEqual(line, line.strip())
            self.assertFalse(line[:1] in tuple("-*•·>#▶"), line)

    def test_values_are_escaped_everywhere(self):
        School.objects.filter(pk=self.home.pk).update(name="새솔" + EVIL)
        Member.objects.filter(pk=self.lead.pk).update(name="대표" + EVIL)
        Member.objects.filter(pk=self.peer.pk).update(name="동료" + EVIL)
        for user, path in ((self.lead.user, self.url), (self.peer.user, self.url), (self.lead.user, self.base + "/"),
                           (self.lead.user, self.base + "/made"), (self.lead.user, "/school-plan")):
            body = login(user).get(path).content.decode()
            self.assertNotIn("<script>alert(1)", body, path)
        texts = data_texts(login(self.peer.user).get(self.url))
        self.assertIn("대표" + EVIL, texts[0])  # 깨지지 않고 원문 그대로 복사된다


class Step3Edges(Base):
    FORM = {"grade": "4", "cls": "4-1", "name": "신규"}

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="google:new", first_name="신규")
        self.client.force_login(self.user)
        self.pick()

    def pick(self):
        s = self.client.session
        s[PICKED] = PICK
        s.save()

    def test_htmx_on_opened_school_has_no_second_submit(self):
        open_hub(self.home)
        r = self.client.get("/new/class?grade=3", HTTP_HX_REQUEST="true")
        self.assertContains(r, 'name="cls"')
        self.assertNotContains(r, 'value="school"')

    def test_htmx_to_taken_grade_hides_both_submits(self):
        grade_space(self.home, 3, "먼저")
        r = self.client.get("/new/class?grade=3", HTTP_HX_REQUEST="true")
        self.assertContains(r, "이미 있어요")
        self.assertNotContains(r, 'type="submit"')

    def test_unknown_then_value_goes_to_made(self):
        r = self.client.post("/new/class", {**self.FORM, "then": "x"})
        self.assertTrue(r["Location"].endswith("/made"))

    def test_then_school_on_opened_school_lands_on_join_confirm(self):
        open_hub(self.home)
        r = self.client.post("/new/class", {**self.FORM, "then": "school"}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "우리 학년 합류")  # W4: 직접 연 학교면 시작 대신 합류 확인
        self.assertNotContains(r, "1년 무료로 시작해요")

    def test_error_redraw_keeps_second_submit_and_school(self):
        r = self.client.post("/new/class", {"grade": "4", "cls": "4-1", "name": "", "then": "school"})
        self.assertContains(r, "이름을 써 주세요", status_code=400)
        self.assertContains(r, 'value="school"', status_code=400)
        ok = self.client.post("/new/class", {**self.FORM, "then": "school"})
        self.assertEqual(ok["Location"], start_url(Space.objects.get(school=self.home, grade=4)))

    def test_moved_grade_is_refused_with_then_school(self):
        space, (lead,) = grade_space(self.home, 4, "먼저")
        open_free(space, lead)
        r = self.client.post("/new/class", {**self.FORM, "then": "school"})
        self.assertContains(r, "이미 학교 공간으로 옮겨졌어요", status_code=409)


class Surfaces(Seated):
    @override_settings(SCHOOL_PLAN_PRICE="390000")
    def test_no_discount_or_certification_claim_anywhere(self):
        self.proof()
        new = login(User.objects.create(username="google:s", first_name="새"))
        picked = login(User.objects.create(username="google:t", first_name="셋"))
        s = picked.session
        s[PICKED] = PICK
        s.save()
        pages = {"소개": Client().get("/"), "시작": Client().get("/start"), "2단계": new.get("/"), "3단계": picked.get("/new/class"),
                 "4단계": self.c.get(self.base + "/made"), "동학년": self.c.get(self.base + "/"), "오늘": self.c.get(self.base + "/today"),
                 "플랜": self.c.get("/school-plan")}
        for name, r in pages.items():
            self.assertEqual(r.status_code, 200, name)
            page = text_of(r)
            for word in BANNED:
                self.assertNotIn(word, page, name)
            if name != "플랜":  # 금액은 유료 신청이 있는 플랜 화면에만
                self.assertNotRegex(page, AMOUNT, name)

    @override_settings(SCHOOL_PLAN_PRICE="390000")
    def test_plan_top_leads_with_free_and_price_stays_with_paid_part(self):
        body = self.c.get("/school-plan").content.decode()
        top = body[body.index('<div class="title">'):body.index('<div class="plan">')]
        self.assertNotRegex(html.unescape(re.sub(r"<[^>]+>", "", top)), AMOUNT)
        self.assertRegex(html.unescape(re.sub(r"<[^>]+>", "", body[body.index("연장·정식 도입"):])), AMOUNT)

    def test_grade_screens_do_not_send_to_plan_page(self):
        for path in ("/", "/today", "/made", "/school-start"):
            self.assertNotContains(self.c.get(self.base + path), 'href="/school-plan"', msg_prefix=path)

    def test_paid_application_and_quote_work_from_grade_member(self):
        r = self.c.post("/school-plan", FORM)
        pr = PlanRequest.objects.get()
        self.assertEqual((r.status_code, r["Location"]), (303, f"/school-plan/q/{pr.key}"))
        self.assertEqual(pr.school, self.home)
        self.assertEqual(self.c.get(r["Location"]).status_code, 200)
        self.assertEqual(self.hubs(), 0)  # 유료 신청은 전환이 아니다
        self.assertContains(self.c.get(self.url), 'name="agree"')  # 신청 뒤에도 무료 시작은 그대로

    def test_paid_application_still_open_after_self_open(self):
        hub = open_free(self.space, self.lead)
        r = login(self.lead.user).post("/school-plan", FORM)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(PlanRequest.objects.get().school_id, hub.school_id)


class ProofRule(Seated):
    NINE = f"지금 {PROOF_MIN - 1}개 학교"
    TEN = f"지금 {PROOF_MIN}개 학교"

    def add_self_opened(self):
        using_school(99, self_opened_at=timezone.now(), paid_until=date(2030, 1, 1))

    def pages(self):
        return [Client().get("/"), self.c.get("/school-plan"), self.c.get(self.base + "/"), self.c.get(self.base + "/made")]

    def test_self_opened_school_does_not_reach_threshold(self):
        self.proof(PROOF_MIN - 1)
        self.add_self_opened()
        for r in self.pages():
            self.assertNotContains(r, "개 학교가 학교 전체로")
        self.assertNotIn("개 학교", school_pitch(space=self.space, me=self.lead)["msg"])

    def test_self_opened_school_does_not_inflate_count(self):
        self.proof(PROOF_MIN)
        self.add_self_opened()
        for r in self.pages():
            self.assertContains(r, self.TEN)

    def test_my_own_opening_is_not_counted(self):
        self.proof(PROOF_MIN - 1)
        open_free(self.space, self.lead)
        self.assertNotIn("n", school_pitch())


class SideButtonScope(Seated):
    def test_exactly_one_button_per_screen(self):
        for path in ("/", "/today", "/schedule", "/c/", "/settings", "/school-start"):
            r = self.c.get(self.base + path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.content.decode().count(CTA_CLASS), 1, path)
