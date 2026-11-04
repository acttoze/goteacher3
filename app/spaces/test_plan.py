"""O1 학교 플랜: 소개·신청 → 견적서 → 운영자 명령 → 학교 공간 → 신청자가 관리자로 입장. 구글은 force_login으로 대체한다."""
import re
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import new_guest
from config.logsafe import _TOKEN

from .merge import open_school
from .models import Member, PlanRequest, School, Space

User = get_user_model()
UNTIL = date(2099, 2, 28)
HIT = {"atpt": "J10", "code": "7531234", "name": "새솔초등학교", "office": "경기도교육청", "address": "경기"}
FORM = {"by_name": "이수빈", "position": "교무부장", "email": "soo@school.example", "students": "320", "classes": "18"}
ON = ("--on", "--until", "2099-02-28")


def google(name="1", first="이수빈"):
    return User.objects.create(username=f"google:{name}", first_name=first, email=f"{name}@example.com")


class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        p = mock.patch("spaces.neis.search_schools", return_value=([HIT], 1))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2"])
        p.start()
        self.addCleanup(p.stop)
        self.user = google()
        self.client.force_login(self.user)

    def apply(self, client=None, **over):
        """학교 검색 뒤 첫 결과를 골라 신청한다."""
        c = client or self.client
        c.get("/school-plan?q=새솔")
        return c.post("/school-plan", {**FORM, "i": "0", **over})

    def seat(self, school, user=None, grade=4, manager=False):
        space, _ = Space.objects.get_or_create(school=school, grade=grade, defaults={"name": f"{grade}학년"})
        return Member.objects.create(space=space, user=user or google("9", "김도윤"), name="김도윤", cls=f"{grade}-1", is_manager=manager)


class IntroTests(Base):
    def test_open_without_login_and_lists_only_real_features(self):
        body = Client().get("/school-plan").content.decode()
        for word in ("명부", "업무분장", "공지", "일정", "복무", "수리", "업무 요약", "급식", "동학년"):
            self.assertIn(word, body)
        for word in ("SSO", "무제한", "인수인계", "CSAP"):
            self.assertNotIn(word, body)
        self.assertIn("/start?next=/school-plan", body)
        self.assertNotIn('name="by_name"', body)  # 신청 폼은 구글 회원에게만

    def test_landing_links_school_plan(self):
        body = Client().get("/").content.decode()
        self.assertIn('href="/school-plan"', body)
        self.assertNotIn("CSAP", body)

    def test_google_member_form_fills_my_school(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="가람초등학교")
        self.seat(s, self.user)
        r = self.client.get("/school-plan")
        self.assertContains(r, "가람초등학교")
        self.assertContains(r, 'name="by_name"')
        self.assertNotContains(r, 'name="q"')  # 내 학교가 있으면 검색하지 않는다

    def test_no_space_searches_neis(self):
        r = self.client.get("/school-plan?q=새솔")
        self.assertContains(r, "새솔초등학교")
        self.assertContains(self.client.get("/school-plan?q=새솔", headers={"HX-Request": "true"}), 'name="i"')


class ApplyTests(Base):
    def test_request_saved_and_redirects_to_quote(self):
        r = self.apply()
        pr = PlanRequest.objects.get()
        self.assertEqual((r.status_code, r["Location"]), (303, f"/school-plan/q/{pr.key}"))
        self.assertEqual((pr.school.name, pr.by_name, pr.position, pr.email, pr.students, pr.classes, pr.status, pr.user, pr.member),
                         ("새솔초등학교", "이수빈", "교무부장", "soo@school.example", 320, 18, "pending", self.user, None))
        self.assertGreaterEqual(len(pr.key), 32)
        self.assertNotIn(str(pr.pk) + "/", r["Location"])

    def test_student_and_class_counts_are_optional(self):
        self.assertEqual(self.apply(students="", classes="").status_code, 303)
        self.assertEqual(PlanRequest.objects.get().students, None)

    def test_bad_fields(self):
        for over in ({"by_name": " "}, {"position": ""}, {"email": "x"}, {"students": "많음"}, {"students": "-3"}, {"i": ""}, {"i": "7"}):
            self.assertEqual(self.apply(**over).status_code, 400, over)
        self.assertFalse(PlanRequest.objects.exists())
        self.assertFalse(School.objects.exists())  # 잘못된 신청으로 학교 행을 만들지 않는다

    def test_anon_redirected_guest_refused(self):
        r = Client().post("/school-plan", FORM)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/start", r["Location"])
        guest = Client()
        guest.force_login(new_guest("손님"))
        self.assertContains(guest.get("/school-plan"), "구글")
        self.assertEqual(guest.post("/school-plan", {**FORM, "i": "0"}).status_code, 403)
        self.assertFalse(PlanRequest.objects.exists())

    def test_guest_form_hidden(self):
        guest = Client()
        guest.force_login(new_guest("손님"))
        self.assertNotContains(guest.get("/school-plan"), 'name="by_name"')

    def test_my_space_school_ignores_posted_index(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="가람초등학교")
        seat = self.seat(s, self.user)
        self.assertEqual(self.client.post("/school-plan", {**FORM, "i": "0"}).status_code, 303)
        pr = PlanRequest.objects.get()
        self.assertEqual((pr.school, pr.member), (s, seat))

    def test_same_school_pending_updates_one_row(self):
        self.apply()
        self.apply(position="교감")
        self.assertEqual(PlanRequest.objects.get().position, "교감")

    def test_rate_limited(self):
        for _ in range(5):
            self.assertEqual(self.apply().status_code, 303)
        self.assertEqual(self.apply().status_code, 429)

    def test_already_on_school_told_and_refused(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=s, kind="school", grade=0, name="새솔")
        self.assertContains(self.apply(), "이미 학교 플랜", status_code=409)
        self.assertFalse(PlanRequest.objects.exists())
        Member.objects.create(space=hub, user=self.user, name="이수빈", cls="교무실", is_manager=True)
        r = self.client.get("/school-plan")
        self.assertContains(r, "이미 학교 플랜")
        self.assertNotContains(r, 'name="by_name"')

    def test_renewal_allowed_when_near_end_or_expired(self):
        for left in (30, -5):
            s = School.objects.create(neis_atpt="J10", neis_code=f"8{left}", name="새솔", plan="school", paid_until=timezone.localdate() + timedelta(days=left))
            Member.objects.all().delete()
            self.seat(s, self.user)
            self.assertEqual(self.client.post("/school-plan", FORM).status_code, 303, left)

    def test_submitted_values_not_in_operator_error_log_path(self):
        self.assertEqual(_TOKEN.sub(r"\1***", "/school-plan/q/SECRETKEY"), "/school-plan/q/***")


class QuoteTests(Base):
    KEYS = dict(SCHOOL_PLAN_PRICE="", SUPPLIER_NAME="", SUPPLIER_BIZ_NO="", SUPPLIER_ADDRESS="", SUPPLIER_ACCOUNT="")

    def quote(self):
        self.apply()
        return self.client.get(f"/school-plan/q/{PlanRequest.objects.get().key}")

    @override_settings(**KEYS)
    def test_without_settings_no_fake_values(self):
        r = self.quote()
        self.assertContains(r, "새솔초등학교")
        self.assertContains(r, "빈칸은 담당자에게 확인해 손으로 적어 주세요")
        body = r.content.decode()
        self.assertNotRegex(body, r"\d{3}-\d{2}-\d{5}")  # 사업자번호 모양
        self.assertNotRegex(body, r"\d[\d,]*\s*원")  # 금액
        self.assertContains(r, "size: A4")

    @override_settings(SCHOOL_PLAN_PRICE="390000", SUPPLIER_NAME="공급사", SUPPLIER_BIZ_NO="BIZ-MARK", SUPPLIER_ADDRESS="주소표시", SUPPLIER_ACCOUNT="계좌표시")
    def test_settings_appear(self):
        r = self.quote()
        for word in ("390,000원", "공급사", "BIZ-MARK", "주소표시", "계좌표시"):
            self.assertContains(r, word)
        self.assertContains(r, "빈칸은 담당자에게 확인해")  # 대표자·부가세·합계·유효 기간은 설정이 없어 아직 빈칸
        self.assertContains(self.client.get("/school-plan"), "390,000원")

    @override_settings(SCHOOL_PLAN_PRICE="협의")
    def test_price_not_a_number_is_treated_as_unset(self):
        r = self.quote()
        self.assertNotContains(r, "협의")
        self.assertContains(r, "빈칸은 담당자에게 확인해")

    def test_only_requester_opens(self):
        self.apply()
        url = f"/school-plan/q/{PlanRequest.objects.get().key}"
        other = Client()
        other.force_login(google("2"))
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(Client().get(url).status_code, 302)
        self.assertEqual(self.client.get("/school-plan/q/nope").status_code, 404)

    def test_no_fake_values_in_source(self):
        """저장소의 템플릿·설정에 사업자번호·계좌 모양의 값이나 가격 숫자가 박혀 있지 않다."""
        root = Path(settings.BASE_DIR)
        files = [*root.glob("spaces/templates/spaces/plan*.html"), root / "spaces/plan.py", root / "config/settings.py"]
        self.assertGreaterEqual(len(files), 4)
        for f in files:
            text = f.read_text()
            self.assertNotRegex(text, r"\d{3}-\d{2}-\d{5}", f.name)
            self.assertNotRegex(text, r"\d{2,3}-\d{2,6}-\d{2,6}", f.name)
            self.assertNotRegex(text, r"\d+\s*(만\s*)?원", f.name)


class CommandTests(Base):
    def run_cmd(self, *args):
        out = StringIO()
        call_command("school_plan", *args, stdout=out)
        return out.getvalue()

    def test_requests_lists_pending_only(self):
        self.apply()
        pr = PlanRequest.objects.get()
        out = self.run_cmd("requests")
        self.assertIn("대기 1건", out)
        for word in (f"#{pr.pk}", "새솔초등학교", "7531234", "J10", "soo@school.example"):
            self.assertIn(word, out)
        self.assertNotIn(pr.key, out)
        PlanRequest.objects.update(status="done")
        self.assertIn("대기 0건", self.run_cmd("requests"))

    def test_zero_member_school_opens_with_requester_as_manager(self):
        self.apply()
        pr = PlanRequest.objects.get()
        self.run_cmd("7531234", *ON, f"--manager-request={pr.pk}")
        hub = Space.objects.get(school=pr.school, kind="school")
        seat = Member.objects.in_space(hub).get()
        self.assertEqual((seat.user, seat.name, seat.position, seat.group, seat.cls, seat.is_manager),
                         (self.user, "이수빈", "교무부장", "office", "교무실", True))
        pr.refresh_from_db()
        self.assertEqual(pr.status, "done")
        self.assertIsNotNone(pr.done_at)

    def test_requester_in_other_space_refused_and_nothing_changes(self):
        self.apply()
        pr = PlanRequest.objects.get()
        other = School.objects.create(neis_atpt="J10", neis_code="2", name="다른초등학교")
        self.seat(other, self.user)
        with self.assertRaisesMessage(CommandError, "다른 공간"):
            self.run_cmd("7531234", *ON, f"--manager-request={pr.pk}")
        pr.school.refresh_from_db()
        self.assertEqual(pr.school.plan, "free")
        self.assertFalse(Space.objects.filter(school=pr.school).exists())
        pr.refresh_from_db()
        self.assertEqual(pr.status, "pending")

    def test_requester_already_in_school_becomes_manager_same_seat(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        seat = self.seat(s, self.user)
        self.client.post("/school-plan", FORM)
        pr = PlanRequest.objects.get()
        self.run_cmd("7531234", *ON, f"--manager-request={pr.pk}")
        seat.refresh_from_db()
        self.assertTrue(seat.is_manager)
        self.assertEqual(Member.objects.filter(user=self.user).count(), 1)

    def test_request_from_other_school_refused(self):
        self.apply()
        pr = PlanRequest.objects.get()
        School.objects.create(neis_atpt="J10", neis_code="2", name="다른초등학교")
        with self.assertRaises(CommandError):
            self.run_cmd("2", *ON, f"--manager-request={pr.pk}")

    def test_missing_request_refused(self):
        School.objects.create(neis_atpt="J10", neis_code="2", name="다른초등학교")
        with self.assertRaises(CommandError):
            self.run_cmd("2", *ON, "--manager-request=999")

    def test_on_with_member_key_closes_school_requests(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        boss = self.seat(s)
        self.apply()
        self.run_cmd("7531234", *ON, f"--manager={boss.key}")
        self.assertEqual(PlanRequest.objects.get().status, "done")

    def test_off_ends_paid_until_today_so_retention_starts(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교", plan="school", paid_until=UNTIL)
        self.run_cmd("7531234", "--off")
        s.refresh_from_db()
        self.assertEqual((s.plan, s.paid_until), ("free", timezone.localdate()))


class OpenSchoolTests(TestCase):
    def test_requires_manager_in_function(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔")
        with self.assertRaises(ValueError):
            open_school(s, UNTIL)
        s.refresh_from_db()
        self.assertEqual(s.plan, "free")
        self.assertFalse(Space.objects.filter(school=s).exists())

    def test_members_without_manager_also_fails(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔")
        space = Space.objects.create(school=s, grade=4, name="4학년")
        Member.objects.create(space=space, user=google("5"), name="가", cls="4-1")
        with self.assertRaises(ValueError):
            open_school(s, UNTIL)
        self.assertIsNone(Member.objects.get().space.merged_into)

    def test_reopen_with_existing_manager_ok(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔")
        space = Space.objects.create(school=s, grade=4, name="4학년")
        m = Member.objects.create(space=space, user=google("5"), name="가", cls="4-1")
        open_school(s, UNTIL, m)
        open_school(s, UNTIL + timedelta(days=1))
        s.refresh_from_db()
        self.assertEqual(s.paid_until, UNTIL + timedelta(days=1))


class EndToEnd(Base):
    @override_settings(SCHOOL_PLAN_PRICE="", SUPPLIER_NAME="", SUPPLIER_BIZ_NO="", SUPPLIER_ADDRESS="", SUPPLIER_ACCOUNT="")
    def test_request_quote_command_enter(self):
        self.assertEqual(self.client.get("/").status_code, 200)  # 공간 없음: 학교 찾기
        r = self.apply()
        self.assertEqual(self.client.get(r["Location"]).status_code, 200)
        pr = PlanRequest.objects.get()
        call_command("school_plan", "requests", stdout=StringIO())
        call_command("school_plan", pr.school.neis_code, *ON, f"--manager-request={pr.pk}", stdout=StringIO())
        hub = Space.objects.get(school=pr.school, kind="school")
        self.assertEqual(self.client.get("/")["Location"], f"/s/{hub.token}/today")
        page = self.client.get(f"/s/{hub.token}/school")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "선생님 초대하기")  # 관리자 화면
        self.assertContains(self.client.get("/school-plan"), "이미 학교 플랜")


class LeftLineTests(Base):
    def hub(self, left, manager=True):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔", plan="school", paid_until=timezone.localdate() + timedelta(days=left))
        hub = Space.objects.create(school=s, kind="school", grade=0, name="새솔")
        Member.objects.create(space=hub, user=self.user, name="이수빈", cls="교무실", group="office", is_manager=manager)
        return hub

    def test_manager_sees_days_left(self):
        self.assertContains(self.client.get(f"/s/{self.hub(12).token}/school"), "학교 플랜 12일 남음")

    def test_last_day(self):
        self.assertContains(self.client.get(f"/s/{self.hub(0).token}/school"), "학교 플랜 오늘까지")

    def test_others_do_not(self):
        self.assertNotContains(self.client.get(f"/s/{self.hub(12, manager=False).token}/school"), "일 남음")

    def test_source_has_no_hard_price(self):
        self.assertFalse(re.search(r"\d+\s*만\s*원", Client().get("/school-plan").content.decode()))


class FollowUpTests(Base):
    def test_duty_big_table_message_is_about_duties(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=s, kind="school", grade=0, name="새솔")
        Member.objects.create(space=hub, user=self.user, name="이수빈", cls="교무실", group="office", is_manager=True)
        with mock.patch("collect.paste.MAX_HTML", 10):
            r = self.client.post(f"/s/{hub.token}/school/duties/preview", {"html": "<table>" + "x" * 50})
        self.assertContains(r, "업무 500줄")
        self.assertNotContains(r, "받는 사람")

    def test_scrim_excluded_from_narrow_width(self):
        css = (Path(settings.REPO_DIR) / "web/static/css/gt.css").read_text()
        self.assertIn(".main.narrow > .scrim { max-width: none; }", css)
