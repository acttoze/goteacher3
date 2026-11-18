"""W1 QA: test_free_open.py에 없는 경계 사례(구현자 테스트와 겹치지 않게)."""
import threading
from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock, skipUnless
from zoneinfo import ZoneInfo

from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import PasswordChangeRequired, new_guest
from collect.bell import _plan_notices
from collect.models import Notification, PushRound, PushSubscription
from collect.push import digest
from legacy.models import LegacyMap
from meals.models import MealSettings, PhotoLink
from partners.models import Partner
from repairs.models import VendorAccount
from today.models import ALL, Event

from .merge import open_free, open_school
from .models import Member, MemberLog, PlanRequest, School, Space
from .test_free_open import PICK, Base, account, client, seat, stuff
from .views import GRADE_TAKEN, PICKED, SCHOOL_TAKEN

HUB_PAGES = ("/today", "/schedule", "/c/", "/c/new", "/bell", "/settings", "/members", "/school", "/board")


def hub_of(school):
    return Space.objects.get(school=school, grade=0)


class EndDateServerTests(Base):
    """종료일은 한국 날짜 기준이고 화면 표시와 저장값이 같다."""

    def at(self, *args):
        return mock.patch("django.utils.timezone.now", return_value=datetime(*args, tzinfo=dt_timezone.utc))

    def test_cutoff_uses_korean_date(self):
        with self.at(2027, 2, 28, 14, 59):  # 한국 2/28 23:59
            open_free(self.g4, self.lead)
        self.school.refresh_from_db()
        self.assertEqual(self.school.paid_until, date(2028, 2, 29))

    def test_first_minute_after_cutoff_in_korea(self):
        with self.at(2027, 2, 28, 15, 0):  # 한국 3/1 00:00
            open_free(self.g4, self.lead)
        self.school.refresh_from_db()
        self.assertEqual(self.school.paid_until, date(2028, 3, 1))


class EligibilityTests(Base):
    def test_csrf_is_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.lead.user)
        self.assertEqual(c.post(self.url, {"agree": "1"}).status_code, 403)
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_only_exact_agree_value_counts(self):
        c = client(self.lead.user)
        for value in ("", "0", "on", "true"):
            self.assertEqual(c.post(self.url, {"agree": value}).status_code, 400, value)
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_get_never_opens(self):
        client(self.lead.user).get(self.url, {"agree": "1"})
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_lead_of_other_school_gets_404_with_swapped_token(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7539999", name="남의초")
        g = Space.objects.create(school=other, grade=4, name="남의 4학년")
        stranger = seat(g, "남의대표", "4-1")
        c = client(stranger.user)
        self.assertEqual(c.get(self.url).status_code, 404)
        self.assertEqual(c.post(self.url, {"agree": "1"}).status_code, 404)
        with self.assertRaises(ValueError):
            open_free(self.g4, stranger)
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_invite_token_is_not_a_space_address(self):
        r = client(self.lead.user).post(f"/s/{self.g4.invite_token}/school-start", {"agree": "1"})
        self.assertEqual(r.status_code, 404)

    def test_anonymous_goes_to_login(self):
        r = Client().post(self.url, {"agree": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/start?", r["Location"])
        self.assertFalse(Space.objects.filter(school=self.school, grade=0).exists())

    def test_ordinary_paid_school_is_not_rewritten(self):
        until = date(2030, 1, 1)
        hub = open_school(self.school, until, manager=self.lead)
        fresh = Space.objects.create(school=self.school, grade=6, name="6학년")
        six = seat(fresh, "육대표", "6-1")
        r = client(six.user).post(f"/s/{fresh.token}/school-start", {"agree": "1"})
        self.assertEqual(r.status_code, 409)
        self.school.refresh_from_db(), fresh.refresh_from_db()
        self.assertEqual((self.school.paid_until, self.school.self_opened_at, fresh.merged_into), (until, None, None))
        self.assertEqual(hub_of(self.school), hub)

    def test_expired_paid_school_cannot_be_restarted_free(self):
        open_school(self.school, date(2020, 1, 1), manager=self.lead)
        fresh = Space.objects.create(school=self.school, grade=6, name="6학년")
        six = seat(fresh, "육대표", "6-1")
        self.assertEqual(client(six.user).post(f"/s/{fresh.token}/school-start", {"agree": "1"}).status_code, 409)
        self.school.refresh_from_db()
        self.assertEqual(self.school.paid_until, date(2020, 1, 1))

    def test_hub_address_lands_on_a_working_page(self):
        hub = open_free(self.g4, self.lead)
        r = client(self.lead.user).get(f"/s/{hub.token}/school-start", follow=True)
        self.assertEqual(r.status_code, 200)


class OtherGradeIsolationTests(Base):
    def setUp(self):
        super().setUp()
        self.c4, self.r4, self.e4 = stuff(self.g4, self.peer, "4학년")
        self.c5, self.r5, self.e5 = stuff(self.g5, self.other_lead, "5학년")
        self.hub = open_free(self.g4, self.lead)

    def test_hub_and_other_grade_cannot_read_each_other(self):
        hub_user, g5_user = client(self.lead.user), client(self.other_lead.user)
        self.assertEqual(hub_user.get(f"/s/{self.g5.token}/").status_code, 404)
        self.assertEqual(hub_user.get(f"/s/{self.hub.token}/c/{self.c5.key}/").status_code, 404)
        self.assertEqual(hub_user.get(f"/s/{self.g5.token}/c/{self.c5.key}/").status_code, 404)
        self.assertEqual(g5_user.get(f"/s/{self.hub.token}/").status_code, 404)
        self.assertEqual(g5_user.get(f"/s/{self.g5.token}/c/{self.c4.key}/").status_code, 404)
        self.assertEqual(g5_user.get(f"/s/{self.g4.token}/").status_code, 404)  # 옮겨진 옛 주소도 남에게는 없다

    def test_hub_lists_do_not_show_other_grade(self):
        c = client(self.lead.user)
        for path in ("/c/", "/today", "/school"):
            r = c.get(f"/s/{self.hub.token}{path}")
            self.assertNotContains(r, "5학년 취합")
            self.assertNotContains(r, "박옆반")

    def test_other_grade_collection_works_after_open(self):
        c = client(self.other_lead.user)
        self.assertContains(c.get(f"/s/{self.g5.token}/c/{self.c5.key}/"), "5학년 취합")
        link = f"/c/{self.c5.link_token}"
        self.assertEqual(Client().get(link).status_code, 200)
        self.assertContains(Client().post(f"{link}/u/{self.r5.key}", {"f0": "7", "by": "대리"}), "제출했어요")
        self.assertEqual(self.r5.cells.get().value, "7")

    def test_moved_collection_link_works_for_anonymous(self):
        link = f"/c/{self.c4.link_token}"
        self.assertEqual(Client().get(link).status_code, 200)
        r = Client().post(f"{link}/find", {"q": "이동료"})
        self.assertEqual((r.status_code, r["Location"]), (303, f"{link}/u/{self.r4.key}"))
        self.assertContains(Client().post(f"{link}/u/{self.r4.key}", {"f0": "5", "by": "이동료"}), "제출했어요")

    def test_moved_grade_old_invite_is_retired(self):
        r = Client().get(f"/j/{self.g4.invite_token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Member.objects.in_space(self.hub).count(), 2)


class CoexistTests(Base):
    def setUp(self):
        super().setUp()
        self.c4, _, _ = stuff(self.g4, self.lead, "4학년")
        stuff(self.g5, self.other_lead, "5학년")
        self.note = Notification.objects.create(space=self.g4, member=self.peer, kind=Notification.Kind.SEAT,
                                                day=timezone.localdate(), text="옛 알림", link=f"/s/{self.g4.token}/today")
        self.other_note = Notification.objects.create(space=self.g5, member=self.other_lead, kind=Notification.Kind.SEAT,
                                                      day=timezone.localdate(), text="옆 알림", link=f"/s/{self.g5.token}/today")
        self.hub = open_free(self.g4, self.lead)

    def test_moved_members_can_use_every_hub_page(self):
        for who in (self.lead, self.peer):
            c = client(who.user)
            for path in HUB_PAGES:
                self.assertEqual(c.get(f"/s/{self.hub.token}{path}").status_code, 200, (who.name, path))
        for path in ("/school/duties", "/school/attendance"):  # 관리자 전용 화면
            self.assertEqual(client(self.lead.user).get(f"/s/{self.hub.token}{path}").status_code, 200, path)

    def test_old_grade_address_forwards_member(self):
        r = client(self.peer.user).get(f"/s/{self.g4.token}/today")
        self.assertEqual((r.status_code, r["Location"]), (302, f"/s/{self.hub.token}/today"))

    def test_home_goes_to_hub_for_mover_and_grade_for_other(self):
        self.assertEqual(client(self.lead.user).get("/")["Location"], f"/s/{self.hub.token}/today")
        self.assertEqual(client(self.other_lead.user).get("/")["Location"], f"/s/{self.g5.token}/today")

    def test_notifications_follow_their_space(self):
        self.note.refresh_from_db(), self.other_note.refresh_from_db()
        self.assertEqual((self.note.space, self.note.link), (self.hub, f"/s/{self.hub.token}/today"))
        self.assertEqual((self.other_note.space, self.other_note.link), (self.g5, f"/s/{self.g5.token}/today"))
        self.assertContains(client(self.peer.user).get(f"/s/{self.hub.token}/bell"), "옛 알림")
        r = client(self.other_lead.user).get(f"/s/{self.g5.token}/bell")
        self.assertContains(r, "옆 알림")
        self.assertNotContains(r, "옛 알림")

    def test_plan_end_notice_goes_only_to_hub_managers(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() + timedelta(days=3))
        notes = _plan_notices(timezone.localdate())
        self.assertEqual({(n.space_id, n.member_id) for n in notes}, {(self.hub.pk, self.lead.pk)})

    def test_batch_commands_survive_and_keep_other_grade(self):
        for name in ("remind", "push_digest", "purge"):
            call_command(name, stdout=StringIO(), stderr=StringIO())
        self.assertTrue(self.g5.collections.filter(title="5학년 취합").exists())
        self.assertTrue(self.hub.collections.filter(title="4학년 취합").exists())

    def test_expired_self_opened_school_keeps_everyone_working(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(client(self.lead.user).get(f"/s/{self.hub.token}/today").status_code, 200)
        self.assertEqual(client(self.other_lead.user).get(f"/s/{self.g5.token}/today").status_code, 200)
        self.assertEqual(client(self.other_lead.user).post(f"/s/{self.g5.token}/school-start", {"agree": "1"}).status_code, 409)


class OperatorRegressionTests(Base):
    def run_plan(self, *args):
        out = StringIO()
        call_command("school_plan", PICK["code"], *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_command_on_absorbs_remaining_grades(self):
        hub = open_free(self.g4, self.lead)
        c, _, _ = stuff(self.g5, self.other_lead, "5학년")
        self.run_plan("--on", "--until", "2030-03-01", f"--manager={self.other_lead.key}", "--identity-checked")
        self.school.refresh_from_db(), self.g5.refresh_from_db(), c.refresh_from_db()
        self.assertEqual((self.school.paid_until, self.school.self_opened_at), (date(2030, 3, 1), None))
        self.assertEqual((self.g5.merged_into, c.space), (hub, hub))
        self.assertEqual(Member.objects.in_space(hub).count(), 3)

    def test_paid_open_merges_duplicate_seat_of_presser(self):
        extra = Member.objects.create(space=self.g5, user=self.lead.user, name="김대표", cls="5-2")
        hub = open_free(self.g4, self.lead)
        open_school(self.school, date(2030, 1, 1), manager=self.lead, identity_checked=True)
        self.assertEqual(Member.objects.filter(user=self.lead.user).count(), 1)
        self.assertFalse(Member.objects.filter(pk=extra.pk).exists())
        self.assertTrue(Member.objects.get(pk=self.lead.pk).is_manager)
        self.assertEqual(self.lead.__class__.objects.get(pk=self.lead.pk).space, hub)

    def test_applicant_from_other_grade_becomes_manager(self):
        hub = open_free(self.g4, self.lead)
        req = PlanRequest.objects.create(school=self.school, user=self.other_lead.user, member=self.other_lead,
                                         by_name="박옆반", position="교무부장", email="a@example.com")
        self.run_plan("--on", "--until", "2030-03-01", f"--manager-request={req.pk}", "--identity-checked")
        self.other_lead.refresh_from_db(), req.refresh_from_db()
        self.assertEqual((self.other_lead.space, self.other_lead.is_manager), (hub, True))
        self.assertEqual(req.status, PlanRequest.Status.DONE)

    def test_after_paid_open_new_grade_is_blocked(self):
        open_free(self.g4, self.lead)
        self.school.refresh_from_db()  # 운영 명령처럼 직접 연 표시가 있는 값으로 부른다
        open_school(self.school, date(2030, 1, 1), manager=self.lead, identity_checked=True)
        c = client(account("새내"))
        s = c.session
        s[PICKED] = PICK
        s.save()
        with mock.patch("spaces.views.neis.class_names", return_value=["1"]):
            r = c.post("/new/class", {"grade": 3, "cls": "3-1", "name": "최새내"})
        self.assertContains(r, SCHOOL_TAKEN, status_code=409)

    def test_command_off_keeps_merged_hub_and_refuses_reopen(self):
        hub = open_free(self.g4, self.lead)
        self.run_plan("--off")
        self.school.refresh_from_db()
        self.assertFalse(self.school.writable)
        self.assertEqual(client(self.lead.user).get(f"/s/{hub.token}/today").status_code, 200)
        self.assertEqual(client(self.other_lead.user).post(f"/s/{self.g5.token}/school-start", {"agree": "1"}).status_code, 409)

    def test_events_of_other_grade_not_rescoped_until_they_move(self):
        e = Event.objects.create(space=self.g5, by=self.other_lead, date=timezone.localdate(), title="옆 일정", scope=ALL)
        open_free(self.g4, self.lead)
        e.refresh_from_db()
        self.assertEqual(e.scope, ALL)


class PitchPageTests(TestCase):
    def test_public_pages_do_not_count_self_opened_schools(self):
        caches["default"].clear()
        for i in range(12):
            s = School.objects.create(neis_atpt="J10", neis_code=f"Q{i:05d}", name=f"직접{i}", plan=School.Plan.SCHOOL,
                                      self_opened_at=timezone.now())
            Space.objects.create(school=s, kind=Space.Kind.SCHOOL, grade=0, name=s.name)
        for path in ("/", "/school-plan"):
            r = Client().get(path)
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, "개 학교가")


class GuestOutsideSpaceTests(Base):
    """손님 자리의 학교 공간 차단: /s/ 밖 공통 틀과 실제 입장·계정 잇기 흐름."""

    def join_as_guest(self):
        c = Client()
        with mock.patch("spaces.views.neis.class_names", return_value=["1", "2", "3"]):
            c.get(f"/j/{self.g4.invite_token}")
            self.assertEqual(c.post("/join", {"cls": "4-3", "name": "손님"}).status_code, 302)
        return c, Member.objects.in_space(self.g4).get(name="손님")

    def test_real_guest_is_gated_then_linking_opens_same_seat(self):
        c, guest = self.join_as_guest()
        hub = open_free(self.g4, self.lead)
        home = f"/s/{hub.token}/school"
        self.assertEqual(c.get("/", follow=True).status_code, 403)
        self.assertEqual(c.get(home).status_code, 403)
        r = c.post("/signup", {"username": "linked_guest", "password": "linked-pass-77", "password2": "linked-pass-77",
                               "name": "손님", "next": home})
        self.assertEqual((r.status_code, r["Location"]), (302, home))
        self.assertEqual(c.get(home).status_code, 200)
        self.assertEqual(Member.objects.in_space(hub).get(user__username="linked_guest").pk, guest.pk)

    def test_shared_pages_do_not_leak_roster_to_guest(self):
        c, _ = self.join_as_guest()
        open_free(self.g4, self.lead)
        for path in ("/forms", "/partners", "/school-plan", "/help"):
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            body = r.content.decode()
            self.assertNotIn("김대표", body, path)  # 공통 메뉴는 보여도 명부는 새지 않는다


class DemotedPasswordEdgeTests(TestCase):
    def setUp(self):
        caches["default"].clear()
        self.school = School.objects.create(neis_atpt="J10", neis_code="7530004", name="경계초", plan=School.Plan.SCHOOL)
        self.hub = Space.objects.create(school=self.school, kind=Space.Kind.SCHOOL, grade=0, name="경계초")
        self.boss = Member.objects.create(space=self.hub, user=account("boss2"), name="관리", cls="교무실", group="office", is_manager=True)
        self.user = account("y", "demoted_one")
        self.user.set_password("original-pass-2")
        self.user.save()
        self.target = Member.objects.create(space=self.hub, user=self.user, name="둘째", cls="교무실", group="office", is_manager=True)
        self.c = client(self.boss.user)
        self.base = f"/s/{self.hub.token}/school/members/{self.target.key}"

    def assert_refused(self):
        r = self.c.post(f"{self.base}/password")
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("original-pass-2"))

    def test_refused_after_demoted_person_renames(self):
        self.c.post(f"{self.base}/manager", {"on": "0"})
        client(self.user).post(f"/s/{self.hub.token}/settings/me", {"name": "둘째샘"})
        self.assert_refused()

    def test_refused_after_operator_command_demotion(self):
        call_command("school_plan", "7530004", f"--remove-manager={self.target.key}", stdout=StringIO())
        self.assert_refused()

    def test_settings_page_cannot_keep_temp_password(self):
        Member.objects.filter(pk=self.target.pk).update(is_manager=False)
        temp = self.c.post(f"{self.base}/password").context["temp"]
        c = Client()
        c.post("/auth/login", {"username": "demoted_one", "password": temp})
        r = c.post(f"/s/{self.hub.token}/settings/password", {"current": temp, "password": temp, "password2": temp})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/account/password", r["Location"])
        self.assertTrue(PasswordChangeRequired.objects.filter(user=self.user).exists())

    def test_demotion_log_of_other_school_does_not_block(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7530005", name="옆초", plan=School.Plan.SCHOOL)
        hub2 = Space.objects.create(school=other, kind=Space.Kind.SCHOOL, grade=0, name="옆초")
        MemberLog.objects.create(space=hub2, by="누구", name="둘째", before="관리자", after="관리자 아님")
        Member.objects.filter(pk=self.target.pk).update(is_manager=False)
        self.assertEqual(self.c.post(f"{self.base}/password").status_code, 200)


@skipUnless(connection.vendor == "postgresql", "동시 요청은 PostgreSQL에서만 확인된다")
class ConcurrentWithOperatorTests(TransactionTestCase):
    def test_free_open_races_operator_open(self):
        for round_no in range(5):
            school = School.objects.create(neis_atpt="J10", neis_code=f"R{round_no:05d}", name=f"경쟁{round_no}")
            g4 = Space.objects.create(school=school, grade=4, name="4학년")
            g5 = Space.objects.create(school=school, grade=5, name="5학년")
            a, b = seat(g4, "가", "4-1"), seat(g5, "나", "5-1")
            gate, errors = threading.Barrier(2), []

            def free():
                try:
                    gate.wait()
                    open_free(g4, a)
                except ValueError:
                    pass
                except Exception as e:
                    errors.append(repr(e))
                finally:
                    connection.close()

            def paid():
                try:
                    gate.wait()
                    open_school(School.objects.get(pk=school.pk), date(2030, 1, 1), manager=b, identity_checked=True)
                except Exception as e:
                    errors.append(repr(e))
                finally:
                    connection.close()
            threads = [threading.Thread(target=free), threading.Thread(target=paid)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(errors, [])
            school.refresh_from_db()
            hub = Space.objects.get(school=school, grade=0)
            self.assertEqual(school.paid_until, date(2030, 1, 1))  # 운영자 값이 언제나 남는다
            self.assertIsNone(school.self_opened_at)
            self.assertEqual(Space.objects.filter(school=school, kind=Space.Kind.GRADE, merged_into=hub).count(), 2)


@override_settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", VAPID_SUBJECT="mailto:admin@goteacher.xyz")
class RecheckLimitTests(Base):
    """W1 재검증 (a)~(f): test_free_open.py에 없는 경계만."""
    NOW = datetime(2026, 11, 18, 12, 40, tzinfo=ZoneInfo("Asia/Seoul"))

    def setUp(self):
        super().setUp()
        self.id_user = account("아이디", "id_member_qa")
        self.id_user.set_password("original-pass-4")
        self.id_user.save()
        self.id_seat = seat(self.g4, "아이디", "4-3", self.id_user)
        User = type(self.lead.user)
        User.objects.filter(pk=self.lead.user_id).update(username="lead_id_qa")
        self.lead.user.refresh_from_db()

    def pw_url(self, hub, m):
        return f"/s/{hub.token}/school/members/{m.key}/password"

    def plan(self, *args):
        call_command("school_plan", PICK["code"], *args, stdout=StringIO(), stderr=StringIO())

    def managers(self, hub):
        return set(Member.objects.in_space(hub).filter(is_manager=True).values_list("pk", flat=True))

    def assert_temp_refused_403(self, hub, **headers):
        self.assertEqual(client(self.lead.user).post(self.pw_url(hub, self.id_seat), headers=headers).status_code, 403)
        self.id_user.refresh_from_db()
        self.assertTrue(self.id_user.check_password("original-pass-4"))

    # (a)
    def test_a_refused_by_htmx_after_expiry_and_after_off(self):
        hub = open_free(self.g4, self.lead)
        self.assert_temp_refused_403(hub, **{"HX-Request": "true"})
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assert_temp_refused_403(hub)
        self.plan("--off")
        self.assert_temp_refused_403(hub)
        self.assertNotContains(client(self.lead.user).get(f"/s/{hub.token}/school"), f"/members/{self.id_seat.key}/password")

    # (b)
    def test_b_every_existing_manager_goes_down(self):
        hub = open_free(self.g4, self.lead)
        Member.objects.filter(pk=self.peer.pk).update(is_manager=True)
        self.plan("--on", "--until", "2030-03-01", f"--manager={self.other_lead.key}", "--identity-checked")
        self.assertEqual(self.managers(hub), {self.other_lead.pk})

    def test_b_naming_current_manager_leaves_only_him(self):
        hub = open_free(self.g4, self.lead)
        Member.objects.filter(pk=self.peer.pk).update(is_manager=True)
        self.plan("--on", "--until", "2030-03-01", f"--manager={self.lead.key}", "--identity-checked")
        self.assertEqual(self.managers(hub), {self.lead.pk})

    def test_b_bad_manager_rolls_everything_back(self):
        hub = open_free(self.g4, self.lead)
        guest = seat(self.g5, "손님", "5-3", new_guest("손님"))
        other = School.objects.create(neis_atpt="J10", neis_code="7538888", name="옆")
        stranger = seat(Space.objects.create(school=other, grade=1, name="1학년"), "남", "1-1")
        for bad in (guest, stranger):
            with self.assertRaises(ValueError):
                open_school(self.school, date(2030, 1, 1), manager=bad, identity_checked=True)
            self.school.refresh_from_db(), self.g5.refresh_from_db(), self.lead.refresh_from_db()
            self.assertIsNotNone(self.school.self_opened_at)
            self.assertEqual((self.g5.merged_into, self.lead.demoted_at), (None, None))
            self.assertEqual(self.managers(hub), {self.lead.pk})

    def test_b_outside_applicant_cannot_take_over_without_identity_check(self):
        hub = open_free(self.g4, self.lead)
        req = PlanRequest.objects.create(school=self.school, user=account("밖"), by_name="밖", position="교사", email="a@example.com")
        with self.assertRaises(ValueError):
            open_school(self.school, date(2030, 1, 1), applicant=req)
        self.assertEqual(self.managers(hub), {self.lead.pk})

    def test_b_former_manager_cannot_be_reset_by_new_manager(self):
        hub = open_free(self.g4, self.lead)
        self.plan("--on", "--until", "2030-03-01", f"--manager={self.other_lead.key}", "--identity-checked")
        r = client(self.other_lead.user).post(self.pw_url(hub, self.lead))
        self.assertContains(r, "7일", status_code=400)

    # (c)
    def test_c_window_ends_after_seven_days_and_is_per_seat(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7537777", name="창초", plan=School.Plan.SCHOOL)
        hub = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name="창초")
        boss = seat(hub, "관리", "교무실")
        Member.objects.filter(pk=boss.pk).update(is_manager=True)
        demoted = seat(hub, "같은이름", "교무실", self.id_user_for("demoted_qa"))
        same_name = seat(hub, "같은이름", "행정실", self.id_user_for("samename_qa"))
        Member.objects.filter(pk=demoted.pk).update(is_manager=True)
        call_command("school_plan", "7537777", f"--remove-manager={demoted.key}", stdout=StringIO())
        c = client(boss.user)
        self.assertEqual(c.post(self.pw_url(hub, same_name)).status_code, 200)  # 이름이 같은 다른 자리는 막지 않는다
        self.assertEqual(c.post(self.pw_url(hub, demoted)).status_code, 400)
        Member.objects.filter(pk=demoted.pk).update(demoted_at=timezone.now() - timedelta(days=6, hours=23))
        self.assertEqual(c.post(self.pw_url(hub, demoted)).status_code, 400)
        Member.objects.filter(pk=demoted.pk).update(demoted_at=timezone.now() - timedelta(days=7, minutes=1))
        self.assertEqual(c.post(self.pw_url(hub, demoted)).status_code, 200)

    def id_user_for(self, username):
        u = account(username, username)
        u.set_password("original-pass-5")
        u.save()
        return u

    # (d)
    def test_d_review_page_follows_free_rule_over_http(self):
        partner = Partner.objects.create(name="업체", slug="qa-partner", cat=Partner._meta.get_field("cat").choices[0][0],
                                         area="전국", desc="소개", is_public=True)
        hub = open_free(self.g4, self.lead)
        url = f"/partners/{partner.slug}/review"
        self.assertEqual(client(self.lead.user).get(url).status_code, 403)
        Member.objects.in_space(hub).update(joined_at=timezone.now() - timedelta(days=15))
        self.assertEqual(client(self.lead.user).get(url).status_code, 200)
        Member.objects.in_space(hub).update(joined_at=timezone.now())
        open_school(self.school, date(2030, 1, 1), manager=self.lead, identity_checked=True)
        self.assertEqual(client(self.lead.user).get(url).status_code, 200)  # 일반 유료 학교는 바로

    # (e)
    def subscribe(self, space, member):
        PushSubscription.objects.create(space=space, member=member, endpoint=f"https://push.example/{member.pk}", p256dh="p", auth="a")
        Notification.objects.create(space=space, member=member, kind="seat", day=self.NOW.date(), text="알림", link=f"/s/{space.token}/")

    def sent(self, now=None):
        PushRound.objects.all().delete()
        with mock.patch("collect.push.neis.day_off", return_value=False), mock.patch("collect.push._send") as send:
            digest(now or self.NOW)
        return {c.args[0].member_id for c in send.call_args_list}

    def test_e_guest_gets_held_notes_after_linking(self):
        guest = seat(self.g4, "손님", "4-4", new_guest("손님"))
        hub = open_free(self.g4, self.lead)
        self.subscribe(hub, guest)
        self.assertEqual(self.sent(), set())
        type(guest.user).objects.filter(pk=guest.user_id).update(username="linked_qa")
        self.assertEqual(self.sent(), {guest.pk})

    def test_e_guest_of_ordinary_merged_school_still_gets_push(self):
        guest = seat(self.g4, "손님", "4-4", new_guest("손님"))
        hub = open_school(self.school, date(2030, 1, 1), manager=self.lead)
        self.subscribe(hub, guest)
        self.assertEqual(self.sent(), {guest.pk})

    # (f)
    def test_f_routes_only_on_moved_grade(self):
        open_free(self.g4, self.lead)
        c = client(account("새내"))
        s = c.session
        s[PICKED] = PICK
        s.save()
        with mock.patch("spaces.views.neis.class_names", return_value=["1"]):
            r = c.get("/new/class", {"grade": 5})
        self.assertContains(r, GRADE_TAKEN)
        self.assertNotContains(r, "mailto:admin@goteacher.xyz")


class OperatorResetEdgeTests(Base):
    """W1 최종 (g)~(j): test_free_open.py의 OperatorCleanupTests에 없는 경계만."""

    def setUp(self):
        super().setUp()
        caches["default"].clear()
        self.hub = open_free(self.g4, self.lead)
        Member.objects.filter(space__school=self.school).update(can_duty=True, can_repair=True, can_meal=True)
        Member.objects.filter(pk=self.peer.pk).update(is_manager=True)
        self.lead.refresh_from_db()
        self.vendor, self.vendor_token = VendorAccount.issue(self.hub, "가짜업체", "김대표", self.lead)
        self.tv = MealSettings.objects.create(space=self.hub)
        self.photo, self.phone = PhotoLink.issue(self.hub, timezone.localdate(), self.lead)
        self.old_invite, self.old_tv = self.hub.invite_token, self.tv.tv_token

    def plan(self, *args):
        call_command("school_plan", PICK["code"], *args, stdout=StringIO(), stderr=StringIO())

    def take_over(self):
        self.plan("--on", "--until", "2030-03-01", f"--manager={self.other_lead.key}", "--identity-checked")

    def snapshot(self):
        self.hub.refresh_from_db(), self.tv.refresh_from_db(), self.vendor.refresh_from_db(), self.g5.refresh_from_db()
        self.school.refresh_from_db()
        seats = sorted(Member.objects.filter(space__school=self.school).values_list(
            "pk", "space_id", "is_manager", "can_duty", "can_repair", "can_meal", "demoted_at"))
        return (self.hub.invite_token, self.tv.tv_token, self.vendor.revoked_at, self.g5.merged_into_id, self.school.paid_until,
                self.school.self_opened_at, seats, PhotoLink.objects.filter(pk=self.photo.pk).exists(),
                Notification.objects.filter(space=self.hub, kind=Notification.Kind.PLAN).count())

    # (g)
    def test_g_every_seat_loses_rights_including_named_and_other_grade(self):
        self.take_over()
        rows = Member.objects.filter(space__school=self.school)
        self.assertEqual(rows.count(), 3)
        self.assertFalse(rows.filter(can_duty=True).exists() or rows.filter(can_repair=True).exists() or rows.filter(can_meal=True).exists())
        self.assertEqual(list(rows.filter(is_manager=True)), [self.other_lead])

    def test_g_former_manager_loses_screens(self):
        self.take_over()
        c = client(self.lead.user)
        for path in ("/school/duties", "/school/attendance"):
            self.assertEqual(c.get(f"/s/{self.hub.token}{path}").status_code, 403, path)
        self.assertEqual(c.post(f"/s/{self.hub.token}/repairs/vendor", {"name": "또", "contact": "또"}).status_code, 403)
        self.assertEqual(client(self.other_lead.user).get(f"/s/{self.hub.token}/school/duties").status_code, 200)

    # (h)
    def test_h_vendor_already_inside_is_cut(self):
        v = Client()
        self.assertEqual(v.get(f"/v/{self.vendor_token}").status_code, 302)
        self.assertEqual(v.get("/v/").status_code, 200)
        self.assertContains(v.get("/v/"), "가짜업체")
        self.take_over()
        self.assertNotContains(v.get("/v/", follow=True), "가짜업체", status_code=v.get("/v/").status_code)
        self.assertNotEqual(Client().get(f"/v/{self.vendor_token}").status_code, 302)

    def test_h_every_tv_address_moves(self):
        self.take_over()
        self.tv.refresh_from_db()
        day = timezone.localdate().strftime("%Y%m%d")
        for path in (f"/tv/{self.old_tv}", f"/tv/{self.old_tv}/data", f"/tv/{self.old_tv}/photo/{day}"):
            self.assertEqual(Client().get(path).status_code, 404, path)
        self.assertEqual(Client().get(f"/tv/{self.tv.tv_token}").status_code, 200)

    def test_h_invite_held_in_session_cannot_enter_and_new_one_works(self):
        c = client(account("밖사람"))
        self.assertEqual(c.get(f"/j/{self.old_invite}").status_code, 302)
        self.take_over()
        before = Member.objects.in_space(self.hub).count()
        self.assertEqual(c.post("/join", {"group": "office", "role_custom": "교사", "name": "밖"}).status_code, 404)
        self.assertEqual(Member.objects.in_space(self.hub).count(), before)
        self.hub.refresh_from_db()
        self.assertEqual(c.get(f"/j/{self.hub.invite_token}").status_code, 302)
        self.assertEqual(c.post("/join", {"group": "office", "role_custom": "교사", "name": "밖"}).status_code, 302)

    def test_h_only_new_manager_is_told_and_can_act_on_it(self):
        self.take_over()
        notes = Notification.objects.filter(space=self.hub, kind=Notification.Kind.PLAN)
        self.assertEqual([n.member_id for n in notes], [self.other_lead.pk])
        c = client(self.other_lead.user)
        self.assertContains(c.get(f"/s/{self.hub.token}/bell"), "초기화")
        self.assertEqual(c.get(notes[0].link).status_code, 200)
        self.assertNotContains(client(self.lead.user).get(f"/s/{self.hub.token}/bell"), "초기화")

    def test_h_second_run_is_plain_renewal(self):
        self.take_over()
        Member.objects.filter(pk=self.other_lead.pk).update(can_repair=True)
        before_invite = Space.objects.get(pk=self.hub.pk).invite_token
        self.plan("--on", "--until", "2031-03-01")
        self.other_lead.refresh_from_db()
        self.assertEqual((self.other_lead.is_manager, self.other_lead.can_repair), (True, True))
        self.assertEqual(Space.objects.get(pk=self.hub.pk).invite_token, before_invite)
        self.assertEqual(Notification.objects.filter(space=self.hub, kind=Notification.Kind.PLAN).count(), 1)

    # (i)
    def assert_refused_and_untouched(self, *args):
        before = self.snapshot()
        with self.assertRaises(CommandError):
            self.plan("--on", "--until", "2030-03-01", *args)
        self.assertEqual(self.snapshot(), before)

    def request_by(self, member_or_user):
        user = getattr(member_or_user, "user", member_or_user)
        return PlanRequest.objects.create(school=self.school, user=user, by_name="신청", position="교무부장", email="a@example.com")

    def test_i_all_refusals_leave_everything_as_it_was(self):
        req = self.request_by(self.other_lead)
        self.assert_refused_and_untouched(f"--manager={self.other_lead.key}")
        self.assert_refused_and_untouched(f"--manager-request={req.pk}")
        self.assert_refused_and_untouched("--identity-checked")
        self.assert_refused_and_untouched()

    def test_i_failure_after_reset_step_also_rolls_back(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7536666", name="딴초")
        elsewhere = seat(Space.objects.create(school=other, grade=1, name="1학년"), "딴사람", "1-1")
        req = self.request_by(elsewhere)
        self.assert_refused_and_untouched(f"--manager-request={req.pk}", "--identity-checked")

    def test_i_request_route_works_with_identity_check(self):
        req = self.request_by(self.other_lead)
        self.plan("--on", "--until", "2030-03-01", f"--manager-request={req.pk}", "--identity-checked")
        self.assertEqual(list(Member.objects.in_space(self.hub).filter(is_manager=True)), [self.other_lead])
        self.assertFalse(PhotoLink.objects.filter(pk=self.photo.pk).exists())


class OrdinaryAndLegacyUnchangedTests(TestCase):
    """W1 최종 (j): 일반 유료·이관 학교는 재직 확인 없이 기존대로 켜지고 아무것도 초기화되지 않는다."""

    def build(self, code, **school):
        s = School.objects.create(neis_atpt="J10", neis_code=code, name=f"학교{code}", plan=School.Plan.SCHOOL, **school)
        hub = Space.objects.create(school=s, kind=Space.Kind.SCHOOL, grade=0, name=s.name)
        boss = Member.objects.create(space=hub, user=account(f"boss{code}"), name="관리", cls="교무실", group="office",
                                     is_manager=True, can_duty=True, can_repair=True, can_meal=True)
        return s, hub, boss, MealSettings.objects.create(space=hub)

    def assert_kept(self, hub, boss, tv, invite, tv_token):
        hub.refresh_from_db(), boss.refresh_from_db(), tv.refresh_from_db()
        self.assertEqual((hub.invite_token, tv.tv_token), (invite, tv_token))
        self.assertEqual((boss.is_manager, boss.can_duty, boss.can_repair, boss.can_meal, boss.demoted_at), (True, True, True, True, None))
        self.assertFalse(Notification.objects.in_space(hub).exists())

    def test_legacy_school_command_without_identity_check(self):
        s, hub, boss, tv = self.build("7535551", paid_until=date(2028, 1, 1))
        LegacyMap.objects.create(kind="school", legacy_id="qa-1", obj_id=s.pk)
        invite, tv_token = hub.invite_token, tv.tv_token
        call_command("school_plan", s.neis_code, "--on", "--until", "2031-01-01", stdout=StringIO())
        s.refresh_from_db()
        self.assertEqual(s.paid_until, date(2031, 1, 1))
        self.assert_kept(hub, boss, tv, invite, tv_token)

    def test_expired_school_resumes_as_before(self):
        s, hub, boss, tv = self.build("7535552", paid_until=date(2020, 1, 1))
        vendor, _ = VendorAccount.issue(hub, "업체", "관리", boss)
        link, _ = PhotoLink.issue(hub, timezone.localdate(), boss)
        invite, tv_token = hub.invite_token, tv.tv_token
        open_school(s, date(2031, 1, 1))
        vendor.refresh_from_db()
        self.assertIsNotNone(vendor.revoked_at)  # 끊겼던 플랜을 다시 켤 때의 기존 동작
        self.assertTrue(PhotoLink.objects.filter(pk=link.pk).exists())
        self.assert_kept(hub, boss, tv, invite, tv_token)

    def test_first_open_keeps_grade_rights_and_member_manager_needs_no_check(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7535553", name="첫초")
        g = Space.objects.create(school=s, grade=2, name="2학년")
        m = seat(g, "가", "2-1")
        Member.objects.filter(pk=m.pk).update(can_duty=True)
        hub = open_school(s, date(2031, 1, 1), manager=m)
        m.refresh_from_db()
        self.assertEqual((m.space, m.is_manager, m.can_duty, m.demoted_at), (hub, True, True, None))
