"""T1 기관 공간: 시작 화면 고르기 → 바로 열기(체험) → 초대 → 미배정 가입. 학교 전용 화면 404, NEIS 요청 0번, 학교 공간 회귀.
구글은 force_login으로 대체한다. NEIS는 urlopen을 목으로 바꿔 부른 횟수만 센다."""
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from collect.models import Notification
from today.models import Event

from . import neis
from .models import INVITE, Member, PlanRequest, School, Space

User = get_user_model()
UNTIL = date(2099, 2, 28)
ORG = {"atpt": "E10", "org": "인천광역시교육청 학교마을협력과", "name": "이수빈", "position": "장학사"}


def google(name, first="이수빈"):
    return User.objects.create(username=f"google:{name}", first_name=first, email=f"{name}@example.com")


def as_(user):
    c = Client()
    c.force_login(user)
    return c


class Base(TestCase):
    def setUp(self):
        cache.clear()
        caches["ratelimit"].clear()
        self.user = google("1")
        self.client.force_login(self.user)

    def make_org(self, user=None, **over):
        user = user or google("boss", "관리자")
        school = School.objects.create(neis_atpt="E10", neis_code="", name="학교마을협력과", plan="school", paid_until=UNTIL)
        space = Space.objects.create(school=school, kind=Space.Kind.ORG, grade=0, name="학교마을협력과")
        boss = Member.objects.create(space=space, user=user, name="관리자", cls="미배정", is_manager=True, **over)
        return space, boss


class ModelTests(Base):
    def test_org_is_school_board_and_labels(self):
        space, _ = self.make_org()
        self.assertTrue(space.is_school)
        self.assertTrue(space.is_org)
        self.assertTrue(space.school.is_org)
        self.assertEqual(space.label, "학교마을협력과")
        self.assertEqual(space.word, "기관")
        self.assertEqual(space.group_choices, [])

    def test_school_is_not_org(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초")
        hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        self.assertFalse(hub.is_org)
        self.assertFalse(school.is_org)
        self.assertEqual(hub.label, "학교")

    def test_many_orgs_in_one_office_but_neis_school_still_unique(self):
        School.objects.create(neis_atpt="E10", neis_code="", name="가")
        School.objects.create(neis_atpt="E10", neis_code="", name="나")
        School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초")
        with self.assertRaises(IntegrityError), transaction.atomic():
            School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초 둘")

    def test_org_must_be_grade_zero(self):
        school = School.objects.create(neis_atpt="E10", neis_code="", name="가")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Space.objects.create(school=school, kind=Space.Kind.ORG, grade=3, name="가")


class NeisTests(Base):
    """기관(학교 코드 없음)은 NEIS를 한 번도 부르지 않는다."""

    def test_empty_code_makes_no_request(self):
        with mock.patch.object(neis, "urlopen") as m:
            self.assertEqual(neis.schedule("E10", "", "20261110", "20261110"), [])
            self.assertFalse(neis.failed(neis.schedule("E10", "", "20261110", "20261110")))
            self.assertEqual(neis.meals("E10", "", "20261110", "20261110"), [])
            self.assertEqual(neis.class_names("E10", "", 4), [])
            self.assertFalse(neis.day_off("E10", "", date(2026, 11, 10)))
        m.assert_not_called()

    def test_org_pages_make_no_request(self):
        space, boss = self.make_org()
        c = as_(boss.user)
        base = f"/s/{space.token}"
        with mock.patch.object(neis, "urlopen") as m:
            for path in ("/today", "/today?show=school", "/schedule", "/schedule?mode=day", "/schedule?mode=month",
                         "/schedule?tab=me", "/schedule/print", "/schedule/print?view=month", "/settings", "/settings?a=me"):
                self.assertEqual(c.get(base + path).status_code, 200, path)
        m.assert_not_called()


class GateTests(Base):
    """학교에만 있는 화면은 기관에서 404. 학교 공간은 그대로 열린다."""

    # T2: /school(= 채널 화면)·/school/me(직위)·배정(arrange)·그룹 색·관리자 지정·임시 비밀번호는 기관에서도 연다
    GET_404 = ("/school/summary", "/school/attendance", "/school/duties", "/meals", "/meals/screens",
               "/it", "/repairs", "/repairs/new")
    POST_404 = ("/school/attendance/preview", "/school/duties/preview",
                "/today/duty", "/repairs/vendor", "/meals/tv/reissue")

    def test_school_only_pages_are_404_in_org(self):
        space, boss = self.make_org()
        c = as_(boss.user)
        base = f"/s/{space.token}"
        for path in self.GET_404:
            self.assertEqual(c.get(base + path).status_code, 404, path)
        for path in self.POST_404:
            self.assertEqual(c.post(base + path, {"date": "2026-11-10", "line": "김민수 출장"}).status_code, 404, path)
        other = Member.objects.create(space=space, user=google("2", "김"), name="김", cls="미배정")
        for tail in ("attendance", "repair", "meal"):
            self.assertEqual(c.post(f"{base}/school/members/{other.key}/{tail}").status_code, 404, tail)

    def test_duty_delete_is_404_in_org(self):
        from .models import Attendance
        space, boss = self.make_org()
        a = Attendance.objects.create(space=space, date=timezone.localdate(), name="관리자", kind="출장", by=boss)
        self.assertEqual(as_(boss.user).post(f"/s/{space.token}/today/duty/{a.key}/delete").status_code, 404)

    def test_space_root_goes_to_today(self):
        space, boss = self.make_org()
        r = as_(boss.user).get(f"/s/{space.token}/")
        self.assertRedirects(r, f"/s/{space.token}/today", fetch_redirect_response=False)

    def test_menu_has_no_school_items(self):
        space, boss = self.make_org()
        with mock.patch.object(neis, "urlopen"):
            html = as_(boss.user).get(f"/s/{space.token}/today").content.decode()
        base = f"/s/{space.token}"
        for href in ("/today", "/c/", "/schedule", "/board", "/forms", "/partners", "/community"):
            self.assertIn(f'href="{base if href.startswith("/") and href not in ("/forms", "/partners", "/community") else ""}{href}"', html)
        for href in ("/school", "/meals", "/it"):
            self.assertNotIn(f'href="{base}{href}"', html)
        self.assertIn(">기</span>", html)
        self.assertIn("기관 공간", html)
        self.assertNotIn("점심 12:20", html)
        self.assertNotIn("학사일정", html)

    def test_board_has_no_repair_tab(self):
        space, boss = self.make_org()
        html = as_(boss.user).get(f"/s/{space.token}/board").content.decode()
        self.assertNotIn(f"/s/{space.token}/repairs", html)

    def test_schedule_has_org_line_and_no_duty_or_neis(self):
        space, boss = self.make_org()
        Event.objects.create(space=space, by=boss, date=timezone.localdate(), title="팀 회의", scope="all")
        with mock.patch.object(neis, "urlopen"):
            html = as_(boss.user).get(f"/s/{space.token}/schedule").content.decode()
        self.assertIn("팀 회의", html)
        self.assertIn("학교마을협력과", html)  # 탭·판 제목
        self.assertIn("기관 전체", html)  # 줄 이름
        self.assertNotIn("복무", html)
        self.assertNotIn("학사일정이 없어요", html)
        self.assertNotIn("학교 전체", html)

    def test_member_of_another_org_gets_404(self):
        space, _ = self.make_org()
        _, stranger = self.make_org(user=google("x", "남"))
        self.assertEqual(as_(stranger.user).get(f"/s/{space.token}/today").status_code, 404)


class StartTests(Base):
    def test_start_screen_shows_school_and_org_choice(self):
        html = self.client.get("/").content.decode()
        self.assertIn('href="/?kind=org"', html)
        self.assertIn("교육청·지원청 부서 등", html)
        form = self.client.get("/?kind=org").content.decode()
        self.assertIn('action="/new/org"', form)
        self.assertIn("인천광역시교육청", form)

    def test_create_org_opens_space_with_trial_and_manager(self):
        with mock.patch.object(neis, "urlopen") as m:
            r = self.client.post("/new/org", ORG)
        m.assert_not_called()
        space = Space.objects.get(kind=Space.Kind.ORG)
        self.assertRedirects(r, f"/s/{space.token}/settings?a=space", status_code=303, fetch_redirect_response=False)
        school = space.school
        self.assertEqual((school.neis_atpt, school.neis_code, school.name), ("E10", "", ORG["org"]))
        self.assertEqual(school.plan, "school")
        self.assertEqual(school.paid_until, timezone.localdate() + timedelta(days=settings.ORG_TRIAL_DAYS))
        self.assertTrue(school.writable)
        self.assertEqual(school.trial_by, self.user)
        me = Member.objects.get(space=space)
        self.assertEqual((me.user, me.name, me.position, me.group, me.cls, me.is_manager), (self.user, "이수빈", "장학사", "", "미배정", True))
        page = self.client.get(f"/s/{space.token}/settings?a=space").content.decode()
        self.assertIn(f"/j/{space.invite_token}", page)

    def test_trial_once_per_account(self):
        self.client.post("/new/org", ORG)
        Member.objects.filter(user=self.user).delete()  # 나간 뒤 다시 체험하려 함
        r = self.client.post("/new/org", {**ORG, "org": "다른 과"})
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, "이미 기관 체험을 썼어요", status_code=409)
        self.assertEqual(School.objects.filter(neis_code="").count(), 1)
        self.assertContains(self.client.get("/?kind=org"), "이미 기관 체험을 썼어요")

    def test_member_of_another_space_keeps_old_rule(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초")
        grade = Space.objects.create(school=school, grade=4, name="새솔 4학년")
        Member.objects.create(space=grade, user=self.user, name="이수빈", cls="4-1")
        r = self.client.post("/new/org", ORG)
        self.assertRedirects(r, f"/s/{grade.token}/", fetch_redirect_response=False)
        self.assertFalse(Space.objects.filter(kind=Space.Kind.ORG).exists())

    def test_bad_input_is_400_and_nothing_made(self):
        for bad in ({"atpt": "Z99"}, {"org": ""}, {"name": ""}):
            r = self.client.post("/new/org", {**ORG, **bad})
            self.assertEqual(r.status_code, 400, bad)
        self.assertFalse(School.objects.exists())

    def test_guest_and_anonymous_cannot_create(self):
        from accounts.models import new_guest
        self.assertEqual(Client().post("/new/org", ORG).status_code, 302)
        as_(new_guest("손님")).post("/new/org", ORG)
        self.assertFalse(School.objects.exists())

    def test_expired_trial_is_read_only_banner(self):
        space, boss = self.make_org()
        School.objects.filter(pk=space.school_id).update(paid_until=timezone.localdate() - timedelta(days=1))
        with mock.patch.object(neis, "urlopen"):
            self.assertContains(as_(boss.user).get(f"/s/{space.token}/today"), "기관 공간 기간이 끝났어요")


class JoinTests(Base):
    def open_invite(self, c, space):
        s = c.session
        s[INVITE] = space.invite_token
        s.save()

    def test_invite_join_is_name_only_and_unassigned(self):
        space, boss = self.make_org()
        c = as_(google("2", "김민수"))
        self.open_invite(c, space)
        form = c.get("/join").content.decode()
        self.assertIn('name="position"', form)
        self.assertNotIn("교무실", form)
        r = c.post("/join", {"name": "김민수", "position": "주무관", "group": "g4", "cls": "4-1"})
        self.assertRedirects(r, f"/s/{space.token}/welcome", fetch_redirect_response=False)
        m = Member.objects.get(space=space, name="김민수")
        self.assertEqual((m.group, m.cls, m.position, m.is_manager), ("", "미배정", "주무관", False))
        bell = Notification.objects.get(member=boss)
        self.assertEqual(bell.link, f"/s/{space.token}/channels")

    def test_guest_cannot_join_org(self):
        from accounts.models import new_guest
        space, _ = self.make_org()
        c = as_(new_guest("손님"))
        self.open_invite(c, space)
        self.assertEqual(c.post("/join", {"name": "손님"}).status_code, 403)
        self.assertEqual(Member.objects.in_space(space).count(), 1)

    def test_expired_org_blocks_new_members(self):
        space, _ = self.make_org()
        School.objects.filter(pk=space.school_id).update(paid_until=timezone.localdate() - timedelta(days=1))
        c = as_(google("3", "박"))
        self.open_invite(c, space)
        self.assertEqual(c.post("/join", {"name": "박"}).status_code, 403)

    def test_settings_me_has_no_class_picker(self):
        space, boss = self.make_org()
        html = as_(boss.user).get(f"/s/{space.token}/settings?a=me").content.decode()
        self.assertNotIn('name="cls"', html)
        self.assertNotIn("/school/me", html)


class PlanTests(Base):
    FORM = {"by_name": "이수빈", "position": "장학사", "email": "soo@example.com"}

    def test_org_apply_without_space_makes_org_school_and_one_pending(self):
        page = self.client.get("/school-plan?kind=org").content.decode()
        self.assertIn('name="atpt"', page)
        with mock.patch.object(neis, "urlopen") as m:
            r = self.client.post("/school-plan", {**self.FORM, "kind": "org", "atpt": "E10", "org": "학교마을협력과"})
            self.assertEqual(r.status_code, 303)
            self.client.post("/school-plan", {**self.FORM, "kind": "org", "atpt": "E10", "org": "학교마을협력과", "position": "주무관"})
        m.assert_not_called()
        req = PlanRequest.objects.get()
        self.assertEqual((req.school.neis_code, req.school.name, req.position), ("", "학교마을협력과", "주무관"))
        self.assertEqual(School.objects.count(), 1)

    def test_org_member_applies_for_own_org(self):
        space, boss = self.make_org()
        School.objects.filter(pk=space.school_id).update(paid_until=timezone.localdate() + timedelta(days=3))
        c = as_(boss.user)
        self.assertContains(c.get("/school-plan"), "기관 플랜 신청")
        r = c.post("/school-plan", self.FORM)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(PlanRequest.objects.get().school_id, space.school_id)


class CommandTests(Base):
    def run_cmd(self, name, *args):
        out = StringIO()
        call_command(name, *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_school_plan_refuses_org(self):
        space, _ = self.make_org()
        with self.assertRaises(CommandError):
            self.run_cmd("school_plan", "", "--on", "--until", "2099-02-28")
        self.assertFalse(Space.objects.filter(school=space.school, kind=Space.Kind.SCHOOL).exists())

    def test_org_plan_extend_off_and_remove_manager(self):
        space, boss = self.make_org()
        pk = str(space.school_id)
        with self.assertRaises(CommandError):  # 처음 켤 때는 운영자 확인이 필수
            self.run_cmd("org_plan", pk, "--on", "--until", "2100-03-01")
        self.run_cmd("org_plan", pk, "--on", "--until", "2100-03-01", "--identity-checked")
        self.run_cmd("org_plan", pk, "--on", "--until", "2100-03-01")  # 연장은 확인 없이
        self.assertEqual(School.objects.get(pk=pk).paid_until, date(2100, 3, 1))
        with self.assertRaises(CommandError):  # 마지막 관리자
            self.run_cmd("org_plan", pk, f"--remove-manager={boss.key}")
        self.run_cmd("org_plan", pk, "--off")
        self.assertFalse(School.objects.get(pk=pk).writable)

    def test_org_plan_refuses_neis_school(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초")
        with self.assertRaises(CommandError):
            self.run_cmd("org_plan", str(school.pk), "--on", "--until", "2099-02-28")

    def test_org_plan_request_opens_space_only_with_identity_check(self):
        self.client.post("/school-plan", {**PlanTests.FORM, "kind": "org", "atpt": "E10", "org": "학교마을협력과"})
        req = PlanRequest.objects.get()
        listing = self.run_cmd("org_plan", "requests")
        self.assertIn("대기 1건", listing)
        self.assertNotIn("학교마을협력과", self.run_cmd("school_plan", "requests"))
        with self.assertRaises(CommandError):
            self.run_cmd("org_plan", f"--request={req.pk}", "--on", "--until", "2099-02-28")
        self.run_cmd("org_plan", f"--request={req.pk}", "--on", "--until", "2099-02-28", "--identity-checked")
        space = Space.objects.get(kind=Space.Kind.ORG)
        me = Member.objects.get(space=space)
        self.assertEqual((me.user, me.is_manager, me.cls, me.group), (self.user, True, "미배정", ""))
        req.refresh_from_db()
        self.assertEqual(req.status, "done")


class SchoolRegressionTests(TestCase):
    """설계 4절 회귀 위험 ①③: 학교 공간 화면·범위는 그대로."""

    def setUp(self):
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        self.hub = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        self.t4 = Member.objects.create(space=self.hub, user=google("4", "사일"), name="사일", cls="4-1", group="g4", is_manager=True)
        self.t5 = Member.objects.create(space=self.hub, user=google("5", "오일"), name="오일", cls="5-1", group="g5")
        p = [mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def test_group_event_is_not_shown_to_other_group(self):
        Event.objects.create(space=self.hub, by=self.t4, date=timezone.localdate(), title="사학년협의", scope="g4")
        c = as_(self.t5.user)
        self.assertNotContains(c.get(f"/s/{self.hub.token}/today"), "사학년협의")
        for tab in ("school", "group", "me"):
            self.assertNotContains(c.get(f"/s/{self.hub.token}/schedule", {"tab": tab}), "사학년협의")

    def test_school_pages_stay_open_and_menu_unchanged(self):
        c = as_(self.t4.user)
        base = f"/s/{self.hub.token}"
        for path in ("/school", "/school/duties", "/school/attendance", "/it", "/meals"):
            self.assertEqual(c.get(base + path).status_code, 200, path)
        html = c.get(base + "/today").content.decode()
        for href in ("/school", "/meals", "/it"):
            self.assertIn(f'href="{base}{href}"', html)
        self.assertIn(">학</span>", html)
        self.assertIn("점심 12:20", html)
        self.assertIn("학교 전체", c.get(base + "/schedule").content.decode())


class UnconfirmedOrgTests(Base):
    """운영자가 켜기 전 기관(School.org_checked_at 없음): 무료 공간 한도, 업체 후기 없음, '운영자 미확인' 표시, 이름 사칭 거부."""

    def confirm(self, space):
        School.objects.filter(pk=space.school_id).update(org_checked_at=timezone.now())
        space.refresh_from_db()
        space.school.refresh_from_db()

    def test_quota_is_free_until_operator_turns_on(self):
        from hwpfiles.models import SourceFile, space_quota, storage_room
        from today.models import EventFile
        space, boss = self.make_org()
        self.assertTrue(space.school.org_unconfirmed)
        self.assertTrue(space.is_free)
        self.assertEqual(space_quota(space), settings.HWP_SPACE_QUOTA)
        ev = Event.objects.create(space=space, by=boss, date=timezone.localdate(), title="회의")
        EventFile.objects.create(event=ev, name="a.pdf", kind="pdf", stored=1000, file="event/x.pdf")
        self.assertEqual(SourceFile.used_bytes(free=True), 1000)
        with self.settings(HWP_FREE_TOTAL_QUOTA=1500, HWP_SPACE_QUOTA=10**9, HWP_TOTAL_QUOTA=10**9):
            self.assertEqual(storage_room(space), (500, "FreeQuota"))
        self.confirm(space)
        self.assertFalse(space.is_free)
        self.assertEqual(space_quota(space), settings.HWP_SCHOOL_QUOTA)
        self.assertEqual(SourceFile.used_bytes(free=True), 0)

    def test_no_partner_review_until_confirmed(self):
        from partners.views import _can_write
        space, boss = self.make_org()
        Member.objects.filter(pk=boss.pk).update(joined_at=timezone.now() - timedelta(days=400))
        for i in range(5):
            Member.objects.create(space=space, user=google(f"r{i}", "회원"), name=f"회원{i}", cls="미배정")
        boss.refresh_from_db()
        self.assertFalse(_can_write(boss))
        self.confirm(space)
        boss.refresh_from_db()
        self.assertTrue(_can_write(boss))

    def test_office_name_alone_is_refused(self):
        for name in ("경기도교육청", "경기교육청", "경기 도 교육청", "서울시교육청", "인천광역시교육청", "전라북도교육청", "강원교육청"):
            r = self.client.post("/new/org", {**ORG, "org": name})
            self.assertEqual(r.status_code, 400, name)
            self.assertContains(r, "부서 이름까지", status_code=400)
        r = self.client.post("/school-plan", {**PlanTests.FORM, "kind": "org", "atpt": "J10", "org": "경기도교육청"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(School.objects.exists())
        self.assertEqual(self.client.post("/new/org", {**ORG, "org": "경기도교육청 민주시민교육과"}).status_code, 303)

    def test_unconfirmed_mark_on_invite_and_join(self):
        space, boss = self.make_org()
        page = as_(boss.user).get(f"/s/{space.token}/settings?a=space").content.decode()
        self.assertIn("기관 공간(체험 중 · 운영자 미확인 기관)이에요", page)
        c = Client()
        c.get(f"/j/{space.invite_token}")
        self.assertContains(c.get("/join"), "체험 중 · 운영자 미확인 기관")
        c2 = as_(google("j", "김"))
        c2.get(f"/j/{space.invite_token}")
        self.assertContains(c2.get("/join"), "체험 중 · 운영자 미확인 기관")
        self.confirm(space)
        self.assertNotContains(c2.get("/join"), "운영자 미확인")
        self.assertNotIn("운영자 미확인", as_(boss.user).get(f"/s/{space.token}/settings?a=space").content.decode())

    def test_trial_end_bell_only_in_last_week(self):
        from collect.bell import _plan_notices
        space, boss = self.make_org()
        today = timezone.localdate()
        School.objects.filter(pk=space.school_id).update(paid_until=today + timedelta(days=20))
        self.assertEqual(_plan_notices(today), [])
        School.objects.filter(pk=space.school_id).update(paid_until=today + timedelta(days=5))
        [n] = _plan_notices(today)
        self.assertEqual((n.member_id, n.link), (boss.pk, "/school-plan"))
        self.assertIn("체험", n.text)
        self.confirm(space)
        [n] = _plan_notices(today)
        self.assertIn("기관 플랜", n.text)

    def test_org_wording_in_settings_and_collect(self):
        space, boss = self.make_org()
        Member.objects.create(space=space, user=google("w", "김"), name="김", cls="미배정")
        c = as_(boss.user)
        self.assertContains(c.get(f"/s/{space.token}/settings?a=space"), "기관 공간은")
        html = c.get(f"/s/{space.token}/c/new").content.decode()
        self.assertNotIn("학교 명부", html)
        self.assertNotIn("학교 일정에 보여요", html)


class OrgPlanExtraTests(Base):
    run_cmd = CommandTests.run_cmd
    def test_add_manager_only_member_account(self):
        from accounts.models import new_guest
        space, _ = self.make_org()
        pk = str(space.school_id)
        m = Member.objects.create(space=space, user=google("am", "김"), name="김", cls="미배정")
        g = Member.objects.create(space=space, user=new_guest("손님"), name="손님", cls="미배정")
        outsider = google("out", "남")
        for bad in (g.user.username, outsider.username, "없는아이디"):
            with self.assertRaises(CommandError):
                self.run_cmd("org_plan", pk, f"--add-manager={bad}")
        self.run_cmd("org_plan", pk, f"--add-manager={m.user.username}")
        m.refresh_from_db()
        self.assertTrue(m.is_manager)

    def test_orphans_lists_and_deletes_only_spaceless_rows_without_pending_or_trial(self):
        space, _ = self.make_org()  # 공간 있음: 남김
        lone = School.objects.create(neis_atpt="E10", neis_code="", name="남은 과")
        pending = School.objects.create(neis_atpt="E10", neis_code="", name="대기 과")
        PlanRequest.objects.create(school=pending, user=self.user, by_name="이", position="장학사", email="a@example.com")
        School.objects.create(neis_atpt="E10", neis_code="", name="체험 과", trial_by=self.user)
        School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초")
        out = self.run_cmd("org_plan", "orphans")
        self.assertIn("남은 기관 행 1개", out)
        self.assertIn(f"기관 {lone.pk} 남은 과", out)
        self.run_cmd("org_plan", "orphans", "--delete")
        self.assertFalse(School.objects.filter(pk=lone.pk).exists())
        self.assertEqual(School.objects.count(), 4)
