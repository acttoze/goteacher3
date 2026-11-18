"""W1 내 동학년만 합치는 1년 무료 학교 전환: 종료일·자격·다른 동학년 불변·직접 연 학교의 독점 금지·손님 차단·임시 비밀번호."""
import threading
from datetime import date, datetime, timedelta
from io import StringIO
from unittest import mock, skipUnless
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import PasswordChangeRequired, new_guest
from collect.models import Collection, Field, Notification, PushSubscription, Row
from collect.push import digest
from meals.models import MealSettings, PhotoLink
from partners.views import _can_write
from repairs.models import VendorAccount
from today.models import ALL, Event

from . import plan
from .merge import free_until, open_free, open_school
from .models import Member, PlanRequest, School, Space
from .views import GRADE_MOVED, GRADE_TAKEN, PICKED, PROOF_MIN, SCHOOL_TAKEN, school_pitch, space_pitch

User = get_user_model()
PICK = {"atpt": "J10", "code": "7530001", "name": "새솔초등학교", "office": "경기도교육청", "address": "경기"}


def account(name, username=None):
    return User.objects.create(username=username or f"google:{name}", first_name=name)


def seat(space, name, cls, user=None):
    return Member.objects.create(space=space, user=user or account(f"{space.pk}-{name}"), name=name, cls=cls)


def client(user):
    c = Client()
    c.force_login(user)
    return c


def stuff(space, member, tag):
    c = Collection.objects.create(space=space, created_by=member, title=f"{tag} 취합", source="sheet", unit_head=["학년반", "담임"])
    Field.objects.create(collection=c, pos=0, name="인원", kind="number")
    row = Row.objects.create(collection=c, pos=0, unit=member.cls, who=member.name, member=member)
    event = Event.objects.create(space=space, by=member, date=timezone.localdate(), title=f"{tag} 일정", scope=ALL)
    return c, row, event


class Base(TestCase):
    def setUp(self):
        caches["default"].clear()
        self.school = School.objects.create(neis_atpt=PICK["atpt"], neis_code=PICK["code"], name=PICK["name"])
        self.g4 = Space.objects.create(school=self.school, grade=4, name="새솔 4학년")
        self.g5 = Space.objects.create(school=self.school, grade=5, name="새솔 5학년")
        self.lead = seat(self.g4, "김대표", "4-1")
        self.peer = seat(self.g4, "이동료", "4-2")
        self.other_lead = seat(self.g5, "박옆반", "5-1")
        self.url = f"/s/{self.g4.token}/school-start"

    def open(self):
        return open_free(self.g4, self.lead)

    def press(self, user=None, **data):
        return client(user or self.lead.user).post(self.url, {"agree": "1", **data})


class EndDateTests(TestCase):
    def test_until_cutoff_gets_fixed_end(self):
        self.assertEqual(free_until(date(2026, 11, 18)), date(2028, 2, 29))
        self.assertEqual(free_until(date(2027, 2, 28)), date(2028, 2, 29))

    def test_after_cutoff_is_one_year(self):
        self.assertEqual(free_until(date(2027, 3, 1)), date(2028, 3, 1))
        self.assertEqual(free_until(date(2028, 2, 28)), date(2029, 2, 28))

    def test_leap_day_becomes_feb_28(self):
        self.assertEqual(free_until(date(2028, 2, 29)), date(2029, 2, 28))


class OpenFreeTests(Base):
    def test_sets_plan_and_marks_school(self):
        hub = self.open()
        self.school.refresh_from_db()
        self.assertEqual(self.school.plan, School.Plan.SCHOOL)
        self.assertEqual(self.school.paid_until, free_until(timezone.localdate()))
        self.assertIsNotNone(self.school.self_opened_at)
        self.assertEqual((hub.kind, hub.grade, hub.name), (Space.Kind.SCHOOL, 0, PICK["name"]))

    def test_server_decides_end_date(self):
        with mock.patch("django.utils.timezone.localdate", return_value=date(2027, 3, 1)):
            r = self.press(paid_until="2099-01-01")
        self.assertEqual(r.status_code, 303)
        self.school.refresh_from_db()
        self.assertEqual(self.school.paid_until, date(2028, 3, 1))

    def test_my_grade_moves_with_presser_as_manager(self):
        c, row, event = stuff(self.g4, self.lead, "4학년")
        hub = self.open()
        self.g4.refresh_from_db()
        self.assertEqual(self.g4.merged_into, hub)
        self.assertEqual(set(Member.objects.in_space(hub)), {self.lead, self.peer})
        c.refresh_from_db(), event.refresh_from_db()
        self.assertEqual(c.space, hub)
        self.assertEqual((event.space, event.scope), (hub, "g4"))
        self.lead.refresh_from_db(), self.peer.refresh_from_db()
        self.assertTrue(self.lead.is_manager)
        self.assertFalse(self.peer.is_manager)
        self.assertEqual(self.lead.group, "g4")

    def test_other_grade_is_untouched(self):
        c, row, event = stuff(self.g5, self.other_lead, "5학년")
        before = Space.objects.values().get(pk=self.g5.pk)
        self.open()
        self.assertEqual(Space.objects.values().get(pk=self.g5.pk), before)  # 토큰·초대 링크·merged_into 그대로
        self.other_lead.refresh_from_db(), c.refresh_from_db(), event.refresh_from_db()
        self.assertEqual((self.other_lead.space, self.other_lead.group, self.other_lead.is_manager), (self.g5, "", False))
        self.assertEqual((c.space, event.space, event.scope), (self.g5, self.g5, ALL))
        self.assertEqual(client(self.other_lead.user).get(f"/s/{self.g5.token}/").status_code, 200)

    def test_same_user_seat_in_other_grade_stays(self):
        extra = Member.objects.create(space=self.g5, user=self.lead.user, name="김대표", cls="5-2")
        hub = self.open()
        self.assertTrue(Member.objects.filter(pk=extra.pk, space=self.g5).exists())
        self.assertTrue(Member.objects.filter(pk=self.lead.pk, space=hub).exists())

    def test_refused_when_hub_exists(self):
        self.open()
        with self.assertRaises(ValueError):
            open_free(self.g5, self.other_lead)
        self.g5.refresh_from_db()
        self.assertIsNone(self.g5.merged_into)

    def test_refused_for_paid_school_and_org(self):
        Space.objects.create(school=self.school, kind=Space.Kind.SCHOOL, grade=0, name="학교")
        with self.assertRaises(ValueError):
            self.open()
        org = School.objects.create(neis_atpt="J10", neis_code="", name="기관")
        space = Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="기관")
        with self.assertRaises(ValueError):
            open_free(space, seat(space, "가", "미배정"))

    def test_refused_for_non_lead_and_stranger_and_guest(self):
        with self.assertRaises(ValueError):
            open_free(self.g4, self.peer)
        with self.assertRaises(ValueError):
            open_free(self.g4, self.other_lead)
        self.lead.user = new_guest("김대표")
        self.lead.save(update_fields=["user"])
        with self.assertRaises(ValueError):
            self.open()
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())


class StartPageTests(Base):
    def test_page_says_what_happens(self):
        r = client(self.lead.user).get(self.url)
        end = free_until(timezone.localdate())
        self.assertContains(r, f"{end.year}년 {end.month}월 {end.day}일")
        for text in ("카드", "자동 결제", "우리 4학년", "다른 학년", "관리자", "넘길 수", "읽기만", 'name="agree"', "1년 무료로 시작"):
            self.assertContains(r, text)
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())  # 여는 것만으로 바뀌지 않는다

    def test_guest_warning_only_when_grade_has_guests(self):
        self.assertNotContains(client(self.lead.user).get(self.url), "계정을 이어야")
        seat(self.g4, "손님", "4-3", new_guest("손님"))
        self.assertContains(client(self.lead.user).get(self.url), "계정을 이어야")

    def test_success_goes_to_school_page(self):
        r = self.press()
        hub = Space.objects.get(school=self.school, grade=0)
        self.assertEqual((r.status_code, r["Location"]), (303, f"/s/{hub.token}/school"))
        self.assertEqual(client(self.lead.user).get(r["Location"]).status_code, 200)

    def test_checkbox_required(self):
        r = client(self.lead.user).post(self.url, {})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_non_lead(self):
        c = client(self.peer.user)
        r = c.get(self.url)
        self.assertContains(r, "김대표 선생님께")
        self.assertNotContains(r, 'name="agree"')
        self.assertEqual(c.post(self.url, {"agree": "1"}).status_code, 403)
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_guest_lead_gets_link_account(self):
        self.lead.user = new_guest("김대표")
        self.lead.save(update_fields=["user"])
        c = client(self.lead.user)
        r = c.get(self.url)
        self.assertContains(r, "/auth/google/login?next=")
        self.assertContains(r, "/signup?next=")
        self.assertNotContains(r, 'name="agree"')
        self.assertEqual(c.post(self.url, {"agree": "1"}).status_code, 403)
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_outsider_404(self):
        self.assertEqual(client(self.other_lead.user).get(self.url).status_code, 404)
        self.assertEqual(client(self.other_lead.user).post(self.url, {"agree": "1"}).status_code, 404)
        self.assertEqual(client(account("밖")).post(self.url, {"agree": "1"}).status_code, 404)

    def test_other_grade_when_school_already_open(self):
        self.open()
        School.objects.filter(pk=self.school.pk).update(self_opened_at=None)  # 직접 연 학교는 합류 화면이다(W4)
        c = client(self.other_lead.user)
        url = f"/s/{self.g5.token}/school-start"
        r = c.get(url)
        self.assertContains(r, "이미 학교 공간")
        self.assertNotContains(r, 'name="agree"')
        self.assertEqual(c.post(url, {"agree": "1"}).status_code, 409)
        self.g5.refresh_from_db()
        self.assertIsNone(self.g5.merged_into)

    def test_double_post_is_safe(self):
        c = client(self.lead.user)
        self.assertEqual(c.post(self.url, {"agree": "1"}).status_code, 303)
        again = c.post(self.url, {"agree": "1"})
        self.assertEqual(again.status_code, 303)
        self.assertEqual(Space.objects.filter(school=self.school, grade=0).count(), 1)

    def test_lost_race_goes_to_school_page(self):
        """두 요청이 함께 미들웨어를 지나고 먼저 끝난 쪽이 합친 경우: 뒤 요청도 학교 화면으로."""
        hub = None

        def beaten(space, member):
            nonlocal hub
            hub = open_free(space, member)
            return open_free(space, member)
        with mock.patch("spaces.plan.open_free", side_effect=beaten):
            r = self.press()
        self.assertEqual((r.status_code, r["Location"]), (303, f"/s/{hub.token}/school"))

    def test_hub_address_just_goes_to_school(self):
        hub = self.open()
        r = client(self.lead.user).get(f"/s/{hub.token}/school-start")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(client(self.lead.user).post(f"/s/{hub.token}/school-start", {"agree": "1"}).status_code, 302)
        self.assertEqual(Space.objects.filter(school=self.school, grade=0).count(), 1)


@skipUnless(connection.vendor == "postgresql", "동시 요청 직렬화(select_for_update)는 PostgreSQL에서만 확인된다")
class ConcurrentOpenTests(TransactionTestCase):
    def test_two_leads_and_double_click(self):
        school = School.objects.create(neis_atpt=PICK["atpt"], neis_code=PICK["code"], name=PICK["name"])
        g4, g5 = (Space.objects.create(school=school, grade=g, name=f"{g}학년") for g in (4, 5))
        a, b = seat(g4, "가", "4-1"), seat(g5, "나", "5-1")
        codes, gate = [], threading.Barrier(3)

        def go(space, user):
            try:
                c = client(user)
                gate.wait()
                codes.append(c.post(f"/s/{space.token}/school-start", {"agree": "1"}).status_code)
            finally:
                connection.close()
        threads = [threading.Thread(target=go, args=x) for x in ((g4, a.user), (g4, a.user), (g5, b.user))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(Space.objects.filter(school=school, grade=0).count(), 1)
        self.assertEqual(Space.objects.filter(school=school, merged_into__isnull=False).count(), 1)
        self.assertEqual(len(codes), 3)
        self.assertLessEqual(set(codes), {303, 409})


@override_settings(SITE_URL="https://t.example")
class NoMonopolyTests(Base):
    def post_class(self, user, grade):
        c = client(user)
        s = c.session
        s[PICKED] = PICK
        s.save()
        with mock.patch("spaces.views.neis.class_names", return_value=["1", "2"]):
            return c, c.post("/new/class", {"grade": grade, "cls": f"{grade}-1", "name": "최새내"})

    def test_new_grade_can_be_made_in_self_opened_school(self):
        self.open()
        _, r = self.post_class(account("새내"), 3)
        self.assertEqual(r.status_code, 302)
        made = Space.objects.get(school=self.school, grade=3)
        self.assertEqual(made.kind, Space.Kind.GRADE)

    def test_moved_grade_explains_where_to_go(self):
        self.open()
        _, r = self.post_class(account("새내"), 4)
        self.assertContains(r, GRADE_MOVED, status_code=409)
        self.assertNotContains(r, GRADE_TAKEN, status_code=409)
        self.assertNotContains(r, "반과 이름만 고르면", status_code=409)

    def test_existing_grade_keeps_its_message(self):
        self.open()
        _, r = self.post_class(account("새내"), 5)
        self.assertContains(r, GRADE_TAKEN, status_code=409)

    def test_ordinary_paid_school_still_blocks(self):
        open_school(self.school, date(2030, 1, 1), manager=self.lead)
        _, r = self.post_class(account("새내"), 3)
        self.assertContains(r, SCHOOL_TAKEN, status_code=409)
        self.assertFalse(Space.objects.filter(school=self.school, grade=3).exists())

    def test_other_grade_keeps_working_as_a_free_grade(self):
        self.open()
        c = client(self.other_lead.user)
        base = f"/s/{self.g5.token}"
        fresh = Space.objects.select_related("school").get(pk=self.g5.pk)
        self.assertTrue(fresh.school.writable)
        for path, code in (("/", 200), ("/today", 200), ("/schedule", 200), ("/c/", 200), ("/c/new", 200), ("/settings", 200), ("/members", 200)):
            self.assertEqual(c.get(base + path).status_code, code, path)
        for path in ("/school", "/board", "/school/duties", "/school/attendance", "/meals"):
            self.assertEqual(c.get(base + path).status_code, 404, path)  # 학교 플랜이 켜진 학교여도 동학년에는 없는 화면
        self.assertContains(c.get(base + "/"), f'href="{base}/school-start"')  # W2: 유도는 이미 열림 안내로
        self.assertNotIn("msg", space_pitch(fresh, self.other_lead))

    def test_other_grade_invite_keeps_working(self):
        self.open()
        c = Client()
        with mock.patch("spaces.views.neis.class_names", return_value=["1", "2"]):
            self.assertEqual(c.get(f"/j/{self.g5.invite_token}").status_code, 302)
            r = c.post("/join", {"cls": "5-2", "name": "새동료"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Member.objects.in_space(self.g5).filter(name="새동료").exists())

    def test_paid_application_allowed_from_hub_and_other_grade(self):
        hub = self.open()
        self.school.refresh_from_db()
        self.assertFalse(plan._already_on(self.school))
        data = {"by_name": "김대표", "position": "교사", "email": "a@example.com"}
        for user, seated in ((self.lead.user, hub), (self.other_lead.user, self.g5)):
            self.assertEqual(client(user).post("/school-plan", data).status_code, 303, seated)
        self.assertEqual(PlanRequest.objects.filter(school=self.school).count(), 2)

    def test_ordinary_paid_school_cannot_apply(self):
        open_school(self.school, timezone.localdate() + timedelta(days=200), manager=self.lead)
        self.school.refresh_from_db()
        self.assertTrue(plan._already_on(self.school))

    def test_paid_open_school_turns_it_into_ordinary_school(self):
        hub = self.open()
        same = open_school(self.school, date(2030, 1, 1), manager=self.other_lead, identity_checked=True)
        self.school.refresh_from_db()
        self.g5.refresh_from_db()
        self.assertEqual(same, hub)
        self.assertIsNone(self.school.self_opened_at)
        self.assertEqual((self.school.paid_until, self.g5.merged_into), (date(2030, 1, 1), hub))

    def test_open_school_on_plain_school_unchanged(self):
        hub = open_school(self.school, date(2030, 1, 1), manager=self.lead)
        self.school.refresh_from_db(), self.g4.refresh_from_db(), self.g5.refresh_from_db()
        self.assertIsNone(self.school.self_opened_at)
        self.assertEqual((self.g4.merged_into, self.g5.merged_into), (hub, hub))


class PitchCountTests(TestCase):
    def school(self, i, atpt="J10", **over):
        s = School.objects.create(neis_atpt=atpt, neis_code=f"W{i:05d}", name=f"학교{i}", plan=School.Plan.SCHOOL, **over)
        Space.objects.create(school=s, kind=Space.Kind.SCHOOL, grade=0, name=s.name)
        return s

    def test_self_opened_not_counted(self):
        for i in range(PROOF_MIN - 1):
            self.school(i)
        self.school(100, self_opened_at=timezone.now())
        p = school_pitch("J10")
        self.assertNotIn("n", p)
        self.assertEqual(p["near"], PROOF_MIN - 1)
        self.school(101)
        self.assertEqual(school_pitch("J10")["n"], PROOF_MIN)


class GuestGateTests(Base):
    def setUp(self):
        super().setUp()
        self.guest_seat = seat(self.g4, "손님", "4-3", new_guest("손님"))
        self.c, self.row, _ = stuff(self.g4, self.guest_seat, "4학년")
        self.field = self.c.fields.get()
        self.hub = self.open()
        self.gc = client(self.guest_seat.user)

    def test_blocked_from_every_hub_screen_with_link_account(self):
        for path in ("/school", "/", "/today", "/school/attendance", "/school/duties", "/c/", "/settings", "/members"):
            r = self.gc.get(f"/s/{self.hub.token}{path}")
            self.assertEqual(r.status_code, 403, path)
            self.assertContains(r, "/auth/google/login?next=", status_code=403)
            self.assertContains(r, "/signup?next=", status_code=403)
            self.assertNotContains(r, "김대표", status_code=403)  # 명부가 새지 않는다

    def test_write_blocked(self):
        r = self.gc.post(f"/s/{self.hub.token}/settings/me", {"name": "바꿈"})
        self.assertEqual(r.status_code, 403)
        self.guest_seat.refresh_from_db()
        self.assertEqual(self.guest_seat.name, "손님")

    def test_htmx_poll_reloads_instead_of_inserting_page(self):
        r = self.gc.get(f"/s/{self.hub.token}/members", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["HX-Redirect"], f"/s/{self.hub.token}/school")

    def test_old_grade_address_ends_at_gate(self):
        r = self.gc.get(f"/s/{self.g4.token}/", follow=True)
        self.assertEqual(r.status_code, 403)

    def test_submit_link_still_works(self):
        link = f"/c/{self.c.link_token}/u/{self.row.key}"
        self.assertEqual(self.gc.get(link).status_code, 200)
        r = self.gc.post(link, {"f0": "3", "by": "손님"})
        self.assertContains(r, "제출했어요")
        self.assertEqual(self.row.cells.get(field=self.field).value, "3")
        self.assertContains(Client().post(link, {"f0": "4", "by": "대리"}), "제출했어요")  # 로그인 없이·대리 제출

    def test_account_member_not_blocked(self):
        self.assertEqual(client(self.peer.user).get(f"/s/{self.hub.token}/school").status_code, 200)

    def test_linking_account_opens_the_hub(self):
        u = self.guest_seat.user
        u.username = "linked_now"
        u.save(update_fields=["username"])
        self.assertEqual(client(u).get(f"/s/{self.hub.token}/school").status_code, 200)

    def test_grade_space_guest_not_blocked(self):
        g = seat(self.g5, "옆손님", "5-3", new_guest("옆손님"))
        self.assertEqual(client(g.user).get(f"/s/{self.g5.token}/").status_code, 200)

    def test_ordinary_merged_school_guest_unchanged(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7530002", name="옛학교")
        g = Space.objects.create(school=school, grade=2, name="2학년")
        boss, guest = seat(g, "관리", "2-1"), seat(g, "손", "2-2", new_guest("손"))
        hub = open_school(school, date(2030, 1, 1), manager=boss)
        self.assertEqual(client(guest.user).get(f"/s/{hub.token}/school").status_code, 200)


class TempPasswordTests(TestCase):
    def setUp(self):
        caches["default"].clear()
        self.school = School.objects.create(neis_atpt="J10", neis_code="7530003", name="비번초", plan=School.Plan.SCHOOL)
        self.hub = Space.objects.create(school=self.school, kind=Space.Kind.SCHOOL, grade=0, name="비번초")
        self.boss = Member.objects.create(space=self.hub, user=account("boss"), name="관리", cls="교무실", group="office", is_manager=True)
        self.other = account("x", "second_boss")
        self.other.set_password("original-pass-1")
        self.other.save()
        self.target = Member.objects.create(space=self.hub, user=self.other, name="둘째", cls="교무실", group="office", is_manager=True)
        self.c = client(self.boss.user)
        self.base = f"/s/{self.hub.token}/school/members/{self.target.key}"

    def demote(self):
        return self.c.post(f"{self.base}/manager", {"on": "0"})

    def test_demoted_manager_cannot_be_reset_right_away(self):
        self.demote()
        r = self.c.post(f"{self.base}/password")
        self.assertEqual(r.status_code, 400)
        self.other.refresh_from_db()
        self.assertTrue(self.other.check_password("original-pass-1"))
        self.assertFalse(PasswordChangeRequired.objects.filter(user=self.other).exists())

    def test_allowed_after_wait(self):
        self.demote()
        Member.objects.filter(pk=self.target.pk).update(demoted_at=timezone.now() - timedelta(days=8))
        self.assertEqual(self.c.post(f"{self.base}/password").status_code, 200)

    def test_never_manager_still_works(self):
        Member.objects.filter(pk=self.target.pk).update(is_manager=False)
        self.assertEqual(self.c.post(f"{self.base}/password").status_code, 200)

    def test_temp_password_cannot_be_kept(self):
        Member.objects.filter(pk=self.target.pk).update(is_manager=False)
        temp = self.c.post(f"{self.base}/password").context["temp"]
        c = Client()
        self.assertEqual(c.post("/auth/login", {"username": "second_boss", "password": temp}).status_code, 302)
        r = c.post("/account/password", {"password": temp, "password2": temp})
        self.assertContains(r, "임시 비밀번호와 다른", status_code=400)
        self.assertTrue(PasswordChangeRequired.objects.filter(user=self.other).exists())
        new = "brand-new-pass-7"
        self.assertEqual(c.post("/account/password", {"password": new, "password2": new}).status_code, 302)
        self.assertFalse(PasswordChangeRequired.objects.filter(user=self.other).exists())


class OperatorTakeoverTests(Base):
    """운영자가 켜면 버튼으로 생긴 관리자는 남지 않는다."""

    def plan(self, *args):
        out = StringIO()
        call_command("school_plan", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_manager_required_and_nothing_changes(self):
        stale = School.objects.get(pk=self.school.pk)  # 버튼을 누르기 전에 읽은 값이어도 잠근 뒤의 값으로 본다
        hub = self.open()
        with self.assertRaises(ValueError):
            open_school(stale, date(2030, 1, 1))
        with self.assertRaises(CommandError):
            self.plan(PICK["code"], "--on", "--until", "2030-01-01")
        self.school.refresh_from_db(), self.lead.refresh_from_db(), self.g5.refresh_from_db()
        self.assertIsNotNone(self.school.self_opened_at)
        self.assertTrue(self.lead.is_manager)
        self.assertIsNone(self.g5.merged_into)
        self.assertEqual(hub.pk, Space.objects.get(school=self.school, grade=0).pk)

    def test_only_named_manager_remains(self):
        hub = self.open()
        open_school(self.school, date(2030, 1, 1), manager=self.other_lead, identity_checked=True)
        self.assertEqual(list(Member.objects.in_space(hub).filter(is_manager=True)), [self.other_lead])
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.demoted_at)

    def test_applicant_becomes_only_manager(self):
        hub = self.open()
        req = PlanRequest.objects.create(school=self.school, user=self.other_lead.user, member=self.other_lead,
                                         by_name="박옆반", position="교무부장", email="a@example.com")
        open_school(self.school, date(2030, 1, 1), applicant=req, identity_checked=True)
        self.assertEqual(list(Member.objects.in_space(hub).filter(is_manager=True)), [self.other_lead])

    def test_ordinary_school_keeps_managers_on_renewal(self):
        hub = open_school(self.school, date(2030, 1, 1), manager=self.lead)
        open_school(self.school, date(2031, 1, 1))
        self.assertEqual(list(Member.objects.in_space(hub).filter(is_manager=True)), [self.lead])

    def test_requests_list_does_not_read_as_proof(self):
        PlanRequest.objects.create(school=self.school, user=self.lead.user, member=self.lead, by_name="김대표", position="교사", email="a@example.com")
        self.assertIn("· 이 학교 회원", self.plan("requests"))
        self.open()
        out = self.plan("requests")
        self.assertIn("직접 연 학교 회원", out)
        self.assertNotIn("· 이 학교 회원", out)


class SelfOpenedLimitTests(Base):
    def setUp(self):
        super().setUp()
        u = account("x", "id_teacher")
        self.id_seat = seat(self.g4, "아이디", "4-3", u)
        self.hub = self.open()
        self.c = client(self.lead.user)
        self.pw = f"/s/{self.hub.token}/school/members/{self.id_seat.key}/password"

    def test_temp_password_refused_and_button_hidden(self):
        before = User.objects.get(pk=self.id_seat.user_id).password
        self.assertEqual(self.c.post(self.pw).status_code, 403)
        self.assertEqual(User.objects.get(pk=self.id_seat.user_id).password, before)
        self.assertNotContains(self.c.get(f"/s/{self.hub.token}/school"), "임시 비밀번호 주기")

    def test_temp_password_back_after_operator_opens(self):
        open_school(self.school, date(2030, 1, 1), manager=self.lead, identity_checked=True)
        self.assertContains(self.c.get(f"/s/{self.hub.token}/school"), "임시 비밀번호 주기")
        Member.objects.filter(pk=self.lead.pk).update(demoted_at=None)
        self.assertEqual(self.c.post(self.pw).status_code, 200)

    def test_partner_review_uses_free_space_rule(self):
        me = Member.objects.get(pk=self.lead.pk)
        self.assertFalse(_can_write(me))  # 방금 들어온 자리
        Member.objects.filter(pk=me.pk).update(joined_at=timezone.now() - timedelta(days=15))
        self.assertTrue(_can_write(Member.objects.get(pk=me.pk)))  # 14일 + 계정 회원 2명 이상
        Member.objects.in_space(self.hub).exclude(pk=me.pk).delete()
        self.assertFalse(_can_write(Member.objects.get(pk=me.pk)))  # 계정 회원 1명

    def test_paid_school_keeps_partner_shortcut(self):
        open_school(self.school, date(2030, 1, 1), manager=self.lead, identity_checked=True)
        self.assertTrue(_can_write(Member.objects.get(pk=self.lead.pk)))

    def test_moved_grade_message_offers_other_routes(self):
        c = client(account("새내"))
        s = c.session
        s[PICKED] = PICK
        s.save()
        with mock.patch("spaces.views.neis.class_names", return_value=["1"]):
            r = c.get("/new/class", {"grade": 4})
        self.assertContains(r, GRADE_MOVED)
        self.assertContains(r, 'href="/school-plan"')
        self.assertContains(r, "mailto:admin@goteacher.xyz")
        self.assertNotContains(r, "관리자 선생님께")


@override_settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", VAPID_SUBJECT="mailto:admin@goteacher.xyz")
class GuestPushTests(Base):
    NOW = datetime(2026, 11, 18, 12, 40, tzinfo=ZoneInfo("Asia/Seoul"))

    def subscribe(self, space, member):
        PushSubscription.objects.create(space=space, member=member, endpoint=f"https://push.example/{member.pk}", p256dh="p", auth="a")
        Notification.objects.create(space=space, member=member, kind="seat", day=self.NOW.date(), text="알림", link=f"/s/{space.token}/")

    def sent(self):
        with mock.patch("collect.push.neis.day_off", return_value=False), mock.patch("collect.push._send") as send:
            n = digest(self.NOW)
        return n, {c.args[0].member_id for c in send.call_args_list}

    def test_guest_seat_of_self_opened_hub_gets_no_push(self):
        guest, grade_guest = seat(self.g4, "손님", "4-3", new_guest("손님")), seat(self.g5, "옆손님", "5-3", new_guest("옆손님"))
        hub = self.open()
        self.subscribe(hub, guest), self.subscribe(hub, self.peer), self.subscribe(self.g5, grade_guest)
        self.assertEqual(self.sent(), (2, {self.peer.pk, grade_guest.pk}))
        guest.refresh_from_db()
        self.assertTrue(Notification.objects.filter(member=guest, pushed=False).exists())  # 이은 뒤에 받을 수 있게 남긴다


class OperatorCleanupTests(Base):
    """외부인이 버튼으로 연 학교를 운영자가 켜면, 그가 쥐었던 권한과 외부 주소가 남지 않는다."""

    def setUp(self):
        super().setUp()
        self.hub = self.open()
        self.old_invite = self.hub.invite_token
        Member.objects.filter(pk=self.lead.pk).update(can_repair=True, can_duty=True, can_meal=True)
        Member.objects.filter(pk=self.peer.pk).update(can_duty=True)
        self.lead.refresh_from_db()
        self.vendor, self.vendor_token = VendorAccount.issue(self.hub, "가짜업체", "김대표", self.lead)
        self.tv = MealSettings.objects.create(space=self.hub)
        _, self.phone = PhotoLink.issue(self.hub, timezone.localdate(), self.lead)
        self.old_tv = self.tv.tv_token

    def take_over(self, **kw):
        return open_school(self.school, date(2030, 1, 1), **{"manager": self.other_lead, "identity_checked": True, **kw})

    def test_powers_are_gone(self):
        self.take_over()
        self.assertFalse(Member.objects.filter(space=self.hub, can_repair=True).exists())
        self.assertFalse(Member.objects.filter(space=self.hub, can_duty=True).exists())
        self.assertFalse(Member.objects.filter(space=self.hub, can_meal=True).exists())
        self.assertEqual(list(Member.objects.in_space(self.hub).filter(is_manager=True)), [self.other_lead])

    def test_vendor_link_cut_and_new_manager_told(self):
        self.take_over()
        self.vendor.refresh_from_db()
        self.assertIsNotNone(self.vendor.revoked_at)
        self.assertTrue(self.vendor.plan_cut)
        self.assertNotEqual(Client().get(f"/v/{self.vendor_token}").status_code, 302)
        note = Notification.objects.in_space(self.hub).get(kind=Notification.Kind.PLAN)
        self.assertEqual(note.member_id, self.other_lead.pk)
        self.assertIn("업체 링크", note.text)
        self.assertEqual(note.link, f"/s/{self.hub.token}/it")

    def test_old_invite_cannot_reenter(self):
        self.take_over()
        self.hub.refresh_from_db()
        self.assertNotEqual(self.hub.invite_token, self.old_invite)
        self.assertEqual(Client().get(f"/j/{self.old_invite}").status_code, 404)

    def test_tv_and_phone_links_cut(self):
        self.take_over()
        self.tv.refresh_from_db()
        self.assertNotEqual(self.tv.tv_token, self.old_tv)
        self.assertEqual(Client().get(f"/tv/{self.old_tv}").status_code, 404)
        self.assertEqual(Client().get(f"/m/{self.phone}").status_code, 404)

    def test_own_application_alone_does_not_make_manager(self):
        req = PlanRequest.objects.create(school=self.school, user=self.lead.user, member=self.lead,
                                         by_name="김대표", position="교사", email="a@example.com")
        with self.assertRaises(ValueError):
            open_school(self.school, date(2030, 1, 1), applicant=req)
        self.lead.refresh_from_db(), self.school.refresh_from_db(), self.vendor.refresh_from_db()
        self.assertIsNotNone(self.school.self_opened_at)  # 모두 되돌린다
        self.assertTrue(self.lead.can_repair)
        self.assertIsNone(self.vendor.revoked_at)

    def test_member_named_without_identity_check_refused(self):
        with self.assertRaises(ValueError):
            open_school(self.school, date(2030, 1, 1), manager=self.lead)
        out = StringIO()
        with self.assertRaisesMessage(CommandError, "--identity-checked"):
            call_command("school_plan", PICK["code"], "--on", "--until", "2030-01-01", f"--manager={self.lead.key}", stdout=out)

    def test_command_with_identity_checked_works(self):
        call_command("school_plan", PICK["code"], "--on", "--until", "2030-01-01", f"--manager={self.other_lead.key}",
                     "--identity-checked", stdout=StringIO())
        self.other_lead.refresh_from_db()
        self.assertTrue(self.other_lead.is_manager)


class OrdinarySchoolUnchangedTests(TestCase):
    """self_opened_at이 없는 학교(이관·일반 유료)는 다시 켜도 권한·링크가 그대로다."""

    def test_renewal_keeps_everything(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7530009", name="유료초", plan=School.Plan.SCHOOL,
                                       paid_until=date(2030, 1, 1))
        hub = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name="유료초")
        boss = Member.objects.create(space=hub, user=account("유료관리"), name="관리", cls="교무실", group="office",
                                     is_manager=True, can_repair=True, can_duty=True, can_meal=True)
        vendor, _ = VendorAccount.issue(hub, "업체", "관리", boss)
        tv = MealSettings.objects.create(space=hub)
        link, _ = PhotoLink.issue(hub, timezone.localdate(), boss)
        token, invite, tv_token = hub.token, hub.invite_token, tv.tv_token
        open_school(school, date(2031, 1, 1))
        hub.refresh_from_db(), boss.refresh_from_db(), vendor.refresh_from_db(), tv.refresh_from_db()
        self.assertEqual((hub.token, hub.invite_token, tv.tv_token), (token, invite, tv_token))
        self.assertEqual((boss.is_manager, boss.can_repair, boss.can_duty, boss.can_meal, boss.demoted_at), (True, True, True, True, None))
        self.assertIsNone(vendor.revoked_at)
        self.assertTrue(PhotoLink.objects.filter(pk=link.pk).exists())
        self.assertFalse(Notification.objects.in_space(hub).exists())
