"""O2 앱 쪽 점검 수정. 항목 번호는 작업 O2의 번호."""
import io
import logging
import re
import threading
import time
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import URLResolver, get_resolver, resolve
from django.utils import timezone
from PIL import Image

from accounts.models import DeviceToken, new_guest
from collect.models import Collection, Field, Notification, PushSubscription, Row
from collect.paste import MAX_HTML
from hwpfiles import retention
from hwpfiles.models import SourceFile, storage_room
from meals.models import MealPhoto
from notices.models import Notice, NoticeVersion
from notices.views import _quota_error
from partners.models import Partner, Review
from repairs import photo
from repairs.models import Repair, VendorAccount
from spaces import neis
from spaces.merge import open_school, retry_once
from spaces.models import Attendance, Member, PlanRequest, School, Space

from .logsafe import SafeErrorFormatter

U = get_user_model()
MB = 1024 * 1024
UNTIL = "2027-02-28"


def google(sub, **kw):
    return U.objects.create(username=f"google:{sub}", email=f"{sub}@x.kr", first_name=sub, **kw)


def school(code="1"):
    return School.objects.create(neis_atpt="J10", neis_code=code, name=f"학교{code}")


def png():
    b = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(b, "PNG")
    return io.BytesIO(b.getvalue())


# ---------------------------------------------------------------- 1. 사진 재인코딩 동시 제한
class PhotoSlotTests(SimpleTestCase):
    def test_one_slot_per_process(self):
        slots = photo._slots
        self.assertIsInstance(slots, type(threading.BoundedSemaphore(1)))
        got = [slots.acquire(blocking=False) for _ in range(2)]
        self.assertEqual(got, [True, False])
        slots.release()

    def test_busy_message_not_500_and_slot_given_back(self):
        self.assertTrue(photo._slots.acquire(timeout=5))
        try:
            with mock.patch.object(photo, "WAIT_SECONDS", 0.01), self.assertRaisesMessage(photo.BadPhoto, "잠시 뒤 다시 올려 주세요"):
                photo.reencode(png())
        finally:
            photo._slots.release()
        self.assertTrue(photo.reencode(png()).startswith(b"\xff\xd8"))
        with self.assertRaises(photo.BadPhoto):  # 못 읽는 파일이어도 자리를 돌려준다
            photo.reencode(io.BytesIO(b"not image"))
        self.assertTrue(photo._slots.acquire(blocking=False))
        photo._slots.release()


class PhotoBusyScreenTests(TestCase):
    def test_repair_form_shows_message(self):
        hub = Space.objects.create(school=school(), kind="school", grade=0, name="학교")
        School.objects.filter(pk=hub.school_id).update(plan="school", paid_until=timezone.localdate() + timedelta(days=30))
        u = google("m")
        Member.objects.create(space=hub, user=u, name="교사", cls="교무실", group="office")
        self.client.force_login(u)
        f = ContentFile(png().getvalue(), name="a.png")
        with mock.patch("repairs.photo.reencode", side_effect=photo.BadPhoto(photo.BUSY)):
            r = self.client.post(f"/s/{hub.token}/repairs/new", {"cat": "facility", "title": "t", "place": "p", "photo": f})
        self.assertContains(r, "잠시 뒤 다시 올려 주세요", status_code=400)


# ---------------------------------------------------------------- 2. 신청자 관리자 검증
class ApplicantCheckTests(TestCase):
    ON = ("--on", "--until", UNTIL)

    def setUp(self):
        self.s = school()
        self.user = google("apply")
        self.pr = PlanRequest.objects.create(school=self.s, user=self.user, by_name="신청", position="교무", email="a@x.kr")

    def run_cmd(self, *extra):
        out, err = StringIO(), StringIO()
        call_command("school_plan", "1", *self.ON, f"--manager-request={self.pr.pk}", *extra, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def members(self):
        g = Space.objects.create(school=self.s, grade=4, name="4학년")
        return Member.objects.create(space=g, user=google("t"), name="교사", cls="4-1")

    def test_outsider_refused_when_school_has_members(self):
        self.members()
        with self.assertRaisesMessage(CommandError, "--identity-checked"):
            self.run_cmd()
        self.s.refresh_from_db()
        self.assertEqual(self.s.plan, "free")
        self.assertFalse(Member.objects.filter(user=self.user).exists())

    def test_outsider_allowed_with_identity_checked(self):
        self.members()
        self.run_cmd("--identity-checked")
        self.assertTrue(Member.objects.get(user=self.user).is_manager)

    def test_existing_member_needs_no_option(self):
        g = Space.objects.create(school=self.s, grade=4, name="4학년")
        seat = Member.objects.create(space=g, user=self.user, name="교사", cls="4-1")
        Member.objects.create(space=g, user=google("t"), name="동료", cls="4-2")
        self.run_cmd()
        seat.refresh_from_db()
        self.assertTrue(seat.is_manager)

    def test_empty_school_warns(self):
        _, err = self.run_cmd()
        self.assertIn("확인", err)
        self.assertTrue(Member.objects.get(user=self.user).is_manager)

    def test_empty_school_no_warning_when_checked(self):
        _, err = self.run_cmd("--identity-checked")
        self.assertEqual(err, "")

    def test_help_says_what_it_means(self):
        from spaces.management.commands.school_plan import Command
        self.assertRegex(Command().create_parser("m", "school_plan").format_help(), r"공문")

    def test_page_says_operator_calls_school(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/school-plan"), "운영자가 학교로 확인 연락을 드려요")


# ---------------------------------------------------------------- 3. 무료·유료 저장 한도
class QuotaTests(TestCase):
    def setUp(self):
        self.a = school("1")
        self.g1 = Space.objects.create(school=self.a, grade=4, name="4")
        self.b = school("2")
        self.g2 = Space.objects.create(school=self.b, grade=4, name="4")
        self.c = school("3")
        self.hub = Space.objects.create(school=self.c, kind="school", grade=0, name="학교")
        School.objects.filter(pk=self.c.pk).update(plan="school", paid_until=timezone.localdate() + timedelta(days=30))
        self.hub.school.refresh_from_db()
        SourceFile.objects.create(space=self.g1, name="x", kind="hwpx", size=1, stored=300)
        SourceFile.objects.create(space=self.g2, name="x", kind="hwpx", size=1, stored=300)
        SourceFile.objects.create(space=self.hub, name="x", kind="hwpx", size=1, stored=5000)

    def test_defaults(self):
        self.assertEqual(settings.HWP_FREE_TOTAL_QUOTA, 2 * 1024**3)
        self.assertEqual(settings.HWP_SPACE_QUOTA, 500 * MB)
        self.assertEqual(settings.HWP_TOTAL_QUOTA, 5 * 1024**3)

    def test_used_free_counts_only_grade_spaces(self):
        self.assertEqual(SourceFile.used_bytes(free=True), 600)
        self.assertEqual(SourceFile.used_bytes(), 5600)

    @override_settings(HWP_FREE_TOTAL_QUOTA=700, HWP_TOTAL_QUOTA=100_000, HWP_SPACE_QUOTA=100_000, HWP_SCHOOL_QUOTA=100_000)
    def test_free_cap_stops_free_not_paid(self):
        self.assertEqual(storage_room(self.g1), (100, "FreeQuota"))
        self.assertTrue(_quota_error(self.g1, [{"size": 101}]))
        self.assertEqual(_quota_error(self.g1, [{"size": 100}]), "")
        self.assertEqual(_quota_error(self.hub, [{"size": 90_000}]), "")

    @override_settings(HWP_FREE_TOTAL_QUOTA=100_000, HWP_TOTAL_QUOTA=100_000, HWP_SPACE_QUOTA=350, HWP_SCHOOL_QUOTA=100_000)
    def test_space_cap(self):
        self.assertEqual(storage_room(self.g1), (50, "Quota"))

    @override_settings(HWP_FREE_TOTAL_QUOTA=100_000, HWP_TOTAL_QUOTA=5700, HWP_SPACE_QUOTA=100_000, HWP_SCHOOL_QUOTA=100_000)
    def test_total_cap_stops_paid(self):
        self.assertEqual(storage_room(self.hub), (100, "TotalQuota"))
        self.assertTrue(_quota_error(self.hub, [{"size": 101}]))

    @override_settings(HWP_FREE_TOTAL_QUOTA=1, HWP_TOTAL_QUOTA=1, HWP_SPACE_QUOTA=1, HWP_SCHOOL_QUOTA=1)
    def test_zero_size_never_refused(self):
        self.assertEqual(_quota_error(self.g1, [{"size": 0}]), "")

    def test_over_cap_space_can_still_read_and_delete(self):
        u = google("t")
        Member.objects.create(space=self.g1, user=u, name="교사", cls="4-1")
        self.client.force_login(u)
        c = Collection.objects.create(space=self.g1, title="모으기", source="sheet")
        with override_settings(HWP_SPACE_QUOTA=1, HWP_FREE_TOTAL_QUOTA=1):
            self.assertEqual(self.client.get(f"/s/{self.g1.token}/c/{c.key}/").status_code, 200)
            self.assertEqual(self.client.post(f"/s/{self.g1.token}/c/{c.key}/delete").status_code, 302)
        self.assertFalse(Collection.objects.filter(pk=c.pk).exists())


# ---------------------------------------------------------------- 4. NEIS 멈춤
def _resp(payload=b'{"RESULT":{"CODE":"INFO-200"}}'):
    m = mock.MagicMock()
    m.__enter__.return_value.read.return_value = payload
    return m


class NeisBreakerTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_two_seconds_no_retry(self):
        self.assertEqual(neis.TIMEOUT, 2)
        with self.assertLogs("spaces.neis", "WARNING"), mock.patch.object(neis, "urlopen", side_effect=OSError) as m:
            neis.class_names("A", "1", 1)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(m.call_args.kwargs["timeout"], 2)

    def test_three_failures_open_breaker(self):
        with self.assertLogs("spaces.neis", "WARNING"), mock.patch.object(neis, "urlopen", side_effect=OSError) as m:
            for grade in range(1, 4):
                neis.class_names("A", "1", grade)
            self.assertEqual(m.call_count, 3)
            out = neis.schedule("A", "1", "20261101", "20261102")
            self.assertEqual(m.call_count, 3)
        self.assertTrue(neis.failed(out))

    def test_breaker_result_not_remembered(self):
        with self.assertLogs("spaces.neis", "WARNING"), mock.patch.object(neis, "urlopen", side_effect=OSError):
            for grade in range(1, 4):
                neis.class_names("A", "1", grade)
        cache.delete(neis.BREAKER)
        with mock.patch.object(neis, "urlopen", return_value=_resp()):
            self.assertFalse(neis.failed(neis.schedule("A", "1", "20261101", "20261102")))

    def test_success_resets_count(self):
        with self.assertLogs("spaces.neis", "WARNING"):
            with mock.patch.object(neis, "urlopen", side_effect=OSError):
                neis.class_names("A", "1", 1)
                neis.class_names("A", "1", 2)
            with mock.patch.object(neis, "urlopen", return_value=_resp()):
                neis.class_names("A", "1", 3)
            with mock.patch.object(neis, "urlopen", side_effect=OSError) as m:
                neis.class_names("A", "1", 4)
                neis.class_names("A", "1", 5)
                neis.class_names("A", "1", 6)
        self.assertEqual(m.call_count, 2 + 1)

    def test_api_error_code_does_not_trip(self):
        """NEIS가 답은 하고 오류 코드를 준 것은 멈춤이 아니다."""
        bad = _resp(b'{"RESULT":{"CODE":"ERROR-290"}}')
        with self.assertLogs("spaces.neis", "WARNING"), mock.patch.object(neis, "urlopen", return_value=bad) as m:
            for grade in range(1, 6):
                neis.class_names("A", "1", grade)
        self.assertEqual(m.call_count, 5)

    def test_same_key_shares_one_call(self):
        calls = []

        def slow(*a, **k):
            calls.append(1)
            time.sleep(0.2)
            return _resp()

        out = []
        with mock.patch.object(neis, "urlopen", side_effect=slow):
            ts = [threading.Thread(target=lambda: out.append(neis.schedule("A", "1", "20261101", "20261102"))) for _ in range(6)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(len(calls), 1)
        self.assertEqual(out, [[]] * 6)

    def test_search_marks_failure(self):
        with self.assertLogs("spaces.neis", "WARNING"), mock.patch.object(neis, "urlopen", side_effect=OSError):
            rows, total = neis.search_schools("봉황")
        self.assertTrue(neis.failed(rows))
        self.assertEqual((rows, total), ([], 0))


class NeisScreenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(google("t"))

    def test_signup_search_kind_words(self):
        with self.assertLogs("spaces.neis", "WARNING"), mock.patch.object(neis, "urlopen", side_effect=OSError):
            r = self.client.get("/?q=봉황", headers={"HX-Request": "true"})
        self.assertContains(r, "불러오지 못했어요")
        self.assertNotContains(r, "찾은 학교가 없어요")

    def test_plan_search_kind_words(self):
        with self.assertLogs("spaces.neis", "WARNING"), mock.patch.object(neis, "urlopen", side_effect=OSError):
            r = self.client.get("/school-plan?q=봉황", headers={"HX-Request": "true"})
        self.assertContains(r, "불러오지 못했어요")
        self.assertNotContains(r, "찾은 학교가 없어요")


# ---------------------------------------------------------------- 5. 알림 보관
class NotificationRetentionTests(TestCase):
    def setUp(self):
        self.g = Space.objects.create(school=school(), grade=4, name="4")
        self.lead = Member.objects.create(space=self.g, user=google("l"), name="대표", cls="4-1")

    def bell(self, days, read):
        n = Notification.objects.create(space=self.g, member=self.lead, kind="seat", day=timezone.localdate(), text="t", link="/", read=read)
        Notification.objects.filter(pk=n.pk).update(created_at=timezone.now() - timedelta(days=days))
        return n.pk

    def test_periods(self):
        gone = [self.bell(91, True), self.bell(181, False)]
        kept = [self.bell(89, True), self.bell(179, False)]
        n = retention.purge(timezone.now(), dry_run=True)
        self.assertEqual(n["notifications"], 2)
        self.assertEqual(Notification.objects.count(), 4)
        retention.purge(timezone.now())
        left = set(Notification.objects.values_list("pk", flat=True))
        self.assertEqual(left, set(kept))
        self.assertFalse(left & set(gone))

    def test_command_prints_count(self):
        self.bell(181, False)
        out = StringIO()
        call_command("purge", stdout=out)
        self.assertIn("지난 알림 1건", out.getvalue())


class JoinBellGoesWithMemberTests(TestCase):
    def setUp(self):
        self.hub = Space.objects.create(school=school(), kind="school", grade=0, name="학교")
        School.objects.filter(pk=self.hub.school_id).update(plan="school", paid_until=timezone.localdate() + timedelta(days=30))
        self.boss = Member.objects.create(space=self.hub, user=google("b"), name="관리", cls="교무실", group="office", is_manager=True)
        self.other = Member.objects.create(space=self.hub, user=google("c"), name="둘째", cls="교무실", group="office", is_manager=True)
        self.t = Member.objects.create(space=self.hub, user=google("t"), name="이나간", cls="4-1", group="g4")

    def welcome(self):
        from spaces.views import _welcome_alert
        _welcome_alert(self.hub, self.t)
        self.assertTrue(Notification.objects.filter(text__contains="이나간").exists())

    def test_removed_member(self):
        self.welcome()
        self.client.force_login(self.boss.user)
        self.client.post(f"/s/{self.hub.token}/members/{self.t.key}/out")
        self.assertFalse(Notification.objects.filter(text__contains="이나간").exists())

    def test_member_who_left_even_after_rename(self):
        self.welcome()
        Member.objects.filter(pk=self.t.pk).update(name="새이름")
        self.client.force_login(self.t.user)
        self.client.post(f"/s/{self.hub.token}/settings/leave", {"confirm": self.hub.name})
        self.assertFalse(Notification.objects.filter(text__contains="이나간").exists())

    def test_other_bells_stay(self):
        self.welcome()
        n = Notification.objects.create(space=self.hub, member=self.boss, kind="plan", day=timezone.localdate(), text="만료", link="/")
        self.t.delete()
        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())


class SeatBellGoesWithMemberTests(TestCase):
    def test_new_device_bell(self):
        from spaces.views import _seat_alert
        g = Space.objects.create(school=school(), grade=4, name="4")
        Member.objects.create(space=g, user=google("l"), name="대표", cls="4-1")
        guest = Member.objects.create(space=g, user=new_guest("손님"), name="손님", cls="4-2")
        DeviceToken.issue(guest.user)
        _seat_alert(g, guest)
        self.assertTrue(Notification.objects.filter(text__contains="손님").exists())
        guest.delete()
        self.assertFalse(Notification.objects.filter(text__contains="손님").exists())


# ---------------------------------------------------------------- 6. 계정 삭제
class DeleteAccountTests(TestCase):
    def setUp(self):
        self.s = school()
        self.g = Space.objects.create(school=self.s, grade=4, name="4")
        self.lead = Member.objects.create(space=self.g, user=google("lead"), name="김대표", cls="4-1")
        self.next = Member.objects.create(space=self.g, user=google("next"), name="박다음", cls="4-2")

    def run_cmd(self, who, *extra):
        out = StringIO()
        call_command("delete_account", who, *extra, stdout=out)
        return out.getvalue()

    def hub(self):
        s = school("9")
        hub = Space.objects.create(school=s, kind="school", grade=0, name="학교")
        School.objects.filter(pk=s.pk).update(plan="school", paid_until=timezone.localdate() + timedelta(days=30))
        boss = Member.objects.create(space=hub, user=google("boss"), name="관리", cls="교무실", group="office", is_manager=True)
        t = Member.objects.create(space=hub, user=google("t"), name="이교사", cls="4-1", group="g4")
        return hub, boss, t

    def test_finds_by_email_or_sub_and_removes_everything(self):
        for who in ("lead@x.kr", "lead"):
            with self.subTest(who=who):
                u = U.objects.get(username="google:lead") if U.objects.filter(username="google:lead").exists() else google("lead")
                seat, _ = Member.objects.get_or_create(space=self.g, user=u, defaults={"name": "김대표", "cls": "4-1"})
                DeviceToken.issue(u)
                PushSubscription.objects.create(space=self.g, member=seat, endpoint=f"https://x/{who}", p256dh="p", auth="a")
                s = SessionStore()
                s["_auth_user_id"] = str(u.pk)
                s.save()
                other = SessionStore()
                other["_auth_user_id"] = str(self.next.user_id)
                other.save()
                out = self.run_cmd(who)
                self.assertFalse(U.objects.filter(pk=u.pk).exists())
                self.assertFalse(Member.objects.filter(pk=seat.pk).exists())
                self.assertFalse(DeviceToken.objects.filter(user_id=u.pk).exists())
                self.assertFalse(PushSubscription.objects.filter(endpoint=f"https://x/{who}").exists())
                self.assertFalse(Session.objects.filter(pk=s.session_key).exists())
                self.assertTrue(Session.objects.filter(pk=other.session_key).exists())
                self.assertNotIn("김대표", out)
                self.assertNotIn("lead@x.kr", out)

    def test_unknown_or_guest_refused(self):
        g = new_guest("손님")
        for who in ("no@x.kr", g.username, ""):
            with self.assertRaises(CommandError):
                self.run_cmd(who)
        self.assertTrue(U.objects.filter(pk=g.pk).exists())

    def test_two_accounts_one_email_refused(self):
        google("dup1", )
        U.objects.filter(username="google:dup1").update(email="d@x.kr")
        google("dup2")
        U.objects.filter(username="google:dup2").update(email="d@x.kr")
        with self.assertRaises(CommandError):
            self.run_cmd("d@x.kr")

    def test_seat_cannot_be_taken_over_in_grade(self):
        """비는 자리를 반·이름으로 다시 차지해도 옛 자리·대표 권한이 넘어오지 않는다."""
        from spaces.models import lead_member, leads
        self.run_cmd("lead")
        self.assertFalse(Member.objects.filter(name="김대표").exists())
        self.assertEqual(lead_member(self.g), self.next)
        c = Client()
        session = c.session
        session["invite"] = self.g.invite_token
        session.save()
        with mock.patch("spaces.neis.class_names", return_value=["1", "2"]):
            r = c.post("/join", {"cls": "4-1", "name": "김대표"})
        self.assertEqual(r.status_code, 302)
        seat = Member.objects.get(space=self.g, name="김대표")
        self.assertFalse(leads(self.g, seat))
        self.assertTrue(leads(self.g, self.next))

    def test_collection_goes_to_heir(self):
        c = Collection.objects.create(space=self.g, created_by=self.lead, title="x", source="sheet")
        self.run_cmd("lead")
        c.refresh_from_db()
        self.assertEqual(c.created_by, self.next)

    def test_only_member_removes_grade_space(self):
        self.run_cmd("next")
        out = self.run_cmd("lead")
        self.assertFalse(Space.objects.filter(pk=self.g.pk).exists())
        self.assertIn("지운 공간 1", out)

    def test_last_manager_refused_nothing_changes(self):
        hub, boss, _ = self.hub()
        with self.assertRaisesMessage(CommandError, "마지막 관리자"):
            self.run_cmd("boss")
        self.assertTrue(Member.objects.filter(pk=boss.pk).exists())

    def test_names_anonymized(self):
        hub, boss, t = self.hub()
        n = Notice.objects.create(space=hub, title="제목", by=t, by_name=t.name)
        NoticeVersion.objects.create(notice=n, v=1, fields={}, body="b", by=t.name)
        r = Repair.objects.create(space=hub, cat="facility", title="요청", by=t, by_name=t.name)
        a = Attendance.objects.create(space=hub, date=timezone.localdate(), name=t.name, kind="출장", member=t)
        mp = MealPhoto.objects.create(space=hub, date=timezone.localdate(), by=t, by_name=t.name)
        kept_n = Notice.objects.create(space=hub, title="다른", by=boss, by_name=boss.name)
        out = self.run_cmd("t")
        for row in (n, r, mp):
            row.refresh_from_db()
            self.assertNotEqual(row.by_name, "이교사")
        self.assertEqual(NoticeVersion.objects.get(notice=n).by, "나간 선생님")
        self.assertFalse(Attendance.objects.filter(pk=a.pk).exists())
        kept_n.refresh_from_db()
        self.assertEqual(kept_n.by_name, "관리")
        self.assertRegex(out, r"공지 1")
        self.assertNotIn("이교사", out)

    def test_review_stays_without_link(self):
        p = Partner.objects.create(slug="p", name="업체", cat=list(dict(Partner._meta.get_field("cat").choices))[0], area="전국", desc="d", is_public=True)
        rv = Review.objects.create(partner=p, member=self.lead, author_user=self.lead.user, school=self.s, badges=[0])
        self.run_cmd("lead")
        rv.refresh_from_db()
        self.assertIsNone(rv.member)
        self.assertIsNone(rv.author_user)

    def test_pending_request_removed_done_kept_without_user(self):
        u = self.lead.user
        pending = PlanRequest.objects.create(school=self.s, user=u, by_name="김대표", position="교무", email="a@x.kr")
        done = PlanRequest.objects.create(school=self.s, user=u, by_name="김대표", position="교무", email="a@x.kr", status="done", done_at=timezone.now())
        self.run_cmd("lead")
        self.assertFalse(PlanRequest.objects.filter(pk=pending.pk).exists())
        done.refresh_from_db()
        self.assertIsNone(done.user)

    def test_dry_run_changes_nothing(self):
        s = SessionStore()
        s["_auth_user_id"] = str(self.lead.user_id)
        s.save()
        out = self.run_cmd("lead", "--dry-run")
        self.assertIn("지우지 않음", out)
        self.assertIn("회원 자리 1", out)
        self.assertTrue(Member.objects.filter(pk=self.lead.pk).exists())
        self.assertTrue(U.objects.filter(username="google:lead").exists())
        self.assertTrue(Session.objects.filter(pk=s.session_key).exists())


# ---------------------------------------------------------------- 7. 앱 본문 상한
UPLOADS = {
    "/s/T/board/new": "MAX_NOTICE_REQUEST_BYTES", "/s/T/board/K/edit": "MAX_NOTICE_REQUEST_BYTES",
    "/s/T/hwp/upload": "MAX_HWP_REQUEST_BYTES",
    "/s/T/repairs/new": "MAX_PHOTO_REQUEST_BYTES", "/s/T/repairs/K/edit": "MAX_PHOTO_REQUEST_BYTES",
    "/s/T/meals/20261104/upload": "MAX_PHOTO_REQUEST_BYTES", "/m/T": "MAX_PHOTO_REQUEST_BYTES",
}


class BodyLimitTests(SimpleTestCase):
    def status(self, path, length):
        """미들웨어만 본다(본문·DB 없음)."""
        from django.http import HttpResponse
        from django.test import RequestFactory

        from .bodylimit import BodyLimitMiddleware
        req = RequestFactory().post(path, data=b"", content_type="application/octet-stream", CONTENT_LENGTH=str(length))
        return BodyLimitMiddleware(lambda r: HttpResponse("ok")).__call__(req).status_code

    def test_same_as_traefik(self):
        self.assertEqual(settings.MAX_REQUEST_BYTES, 3 * MB)
        self.assertEqual(settings.MAX_NOTICE_REQUEST_BYTES, 101 * MB)
        self.assertEqual(settings.MAX_HWP_REQUEST_BYTES, 31 * MB)
        self.assertEqual(settings.MAX_PHOTO_REQUEST_BYTES, 9 * MB)

    def test_upload_paths(self):
        for path, name in UPLOADS.items():
            limit = getattr(settings, name)
            self.assertEqual(self.status(path, limit), 200, path)
            self.assertEqual(self.status(path, limit + 1), 413, path)

    def test_other_paths_default(self):
        for path in ("/join", "/s/T/c/new", "/s/T/c/new/preview", "/s/T/board/K/delete", "/s/T/repairs/K/state",
                     "/s/T/hwp/K/pick", "/s/T/school/attendance", "/m/T/x"):
            self.assertEqual(self.status(path, settings.MAX_REQUEST_BYTES), 200, path)
            self.assertEqual(self.status(path, settings.MAX_REQUEST_BYTES + 1), 413, path)

    def test_upload_paths_are_real_routes(self):
        for path in UPLOADS:
            resolve(path)

    def test_every_file_reader_is_an_upload_path(self):
        """request.FILES를 읽는 코드가 새로 생기면 라우터 목록(Traefik·config.bodylimit)을 함께 고치라고 걸린다."""
        found = set()
        for f in Path(settings.BASE_DIR).rglob("*.py"):
            if "request.FILES" in f.read_text() and not re.search(r"(^|/)tests?(_|\.)", f.name):
                found.add(f.relative_to(settings.BASE_DIR).as_posix())
        self.assertEqual(found, {"repairs/views.py", "notices/views.py", "meals/phone.py", "meals/views.py", "hwpfiles/views.py"})

    def test_paste_fits(self):
        """표본의 URL 인코딩 비율은 1.54~1.86배. 받을 수 있는 가장 큰 HTML도 Django 상한 안, 그 상한은 일반 본문 상한과 같다."""
        self.assertLess(MAX_HTML * 1.9, settings.DATA_UPLOAD_MAX_MEMORY_SIZE)
        self.assertEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, settings.MAX_REQUEST_BYTES)


class EarlyResponseDrainTests(SimpleTestCase):
    """본문을 읽기 전에 응답해도 본문을 버린 뒤에 돌려준다(프록시가 끊긴 연결로 보지 않게)."""

    def request(self, path, body):
        from django.test import RequestFactory
        return RequestFactory().post(path, data=body, content_type="application/octet-stream")

    def test_unread_body_is_consumed_in_pieces(self):
        from django.http import HttpResponse

        from .bodylimit import CHUNK, BodyLimitMiddleware
        req = self.request("/join", b"x" * (CHUNK * 2 + 5))
        sizes = []
        real = req.read
        req.read = lambda n: (sizes.append(n), real(n))[1]
        self.assertEqual(BodyLimitMiddleware(lambda r: HttpResponse("redir", status=302))(req).status_code, 302)
        self.assertEqual(real(1), b"")
        self.assertLessEqual(max(sizes), CHUNK)

    def test_rejected_body_consumed_too(self):
        from .bodylimit import BodyLimitMiddleware
        req = self.request("/join", b"x" * (settings.MAX_REQUEST_BYTES + 1))
        self.assertEqual(BodyLimitMiddleware(lambda r: None)(req).status_code, 413)
        self.assertEqual(req.read(1), b"")

    def test_read_failure_does_not_change_response(self):
        from django.http import HttpResponse

        from .bodylimit import BodyLimitMiddleware
        req = self.request("/join", b"xx")
        req.read = mock.Mock(side_effect=OSError)
        self.assertEqual(BodyLimitMiddleware(lambda r: HttpResponse("ok"))(req).status_code, 200)

    def test_no_read_without_body(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from .bodylimit import BodyLimitMiddleware
        req = RequestFactory().get("/")
        req.read = mock.Mock()
        BodyLimitMiddleware(lambda r: HttpResponse("ok"))(req)
        req.read.assert_not_called()

    def test_very_large_declared_stops_at_cap(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from . import bodylimit
        req = RequestFactory().post("/join", data=b"", content_type="application/octet-stream",
                                    CONTENT_LENGTH=str(bodylimit.DRAIN_MAX + 1))
        req.read = mock.Mock(return_value=b"x")
        bodylimit.BodyLimitMiddleware(lambda r: HttpResponse("ok"))(req)
        req.read.assert_not_called()


# ---------------------------------------------------------------- 8. 교착
class LockSourceTests(SimpleTestCase):
    def test_every_space_lock_is_no_key(self):
        """FOR UPDATE는 자식 행을 넣는 커밋의 FK 확인(KEY SHARE)까지 막아 잠금 순서가 꼬인다."""
        bad = []
        for f in Path(settings.BASE_DIR).rglob("*.py"):
            if f.name.startswith("test") or f.name == "tests.py":
                continue
            if re.search(r"\bSpace\.objects\.select_for_update\(\)", f.read_text()):
                bad.append(f.name)
        self.assertEqual(bad, [])


class RetryOnceTests(TestCase):
    def test_second_try_runs_after_integrity_error(self):
        calls = []

        def work():
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError("fk")
            return "ok"

        self.assertEqual(retry_once(work), "ok")
        self.assertEqual(len(calls), 2)

    def test_remove_member_survives_late_fk_error(self):
        hub = Space.objects.create(school=school(), kind="school", grade=0, name="학교")
        School.objects.filter(pk=hub.school_id).update(plan="school", paid_until=timezone.localdate() + timedelta(days=30))
        boss = Member.objects.create(space=hub, user=google("b"), name="관리", cls="교무실", group="office", is_manager=True)
        t = Member.objects.create(space=hub, user=google("t"), name="교사", cls="4-1", group="g4")
        self.client.force_login(boss.user)
        real, seen = Member.delete, []

        def flaky(self_, *a, **k):
            result = real(self_, *a, **k)  # 지운 뒤 커밋에서 FK 오류가 나는 경우와 같다: 인스턴스 pk는 이미 비었다
            if not seen:
                seen.append(1)
                raise IntegrityError("fk")
            return result

        with mock.patch.object(Member, "delete", flaky):
            r = self.client.post(f"/s/{hub.token}/members/{t.key}/out")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Member.objects.filter(pk=t.pk).exists())

    def test_leave_survives_late_fk_error(self):
        g = Space.objects.create(school=school(), grade=4, name="4")
        Member.objects.create(space=g, user=google("a"), name="먼저", cls="4-1")
        me = Member.objects.create(space=g, user=google("b"), name="나감", cls="4-2")
        self.client.force_login(me.user)
        real, seen = Member.delete, []

        def flaky(self_, *a, **k):
            result = real(self_, *a, **k)
            if not seen:
                seen.append(1)
                raise IntegrityError("fk")
            return result

        with mock.patch.object(Member, "delete", flaky):
            r = self.client.post(f"/s/{g.token}/settings/leave", {"confirm": g.name})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Member.objects.filter(pk=me.pk).exists())


# ---------------------------------------------------------------- 9. 로그 가림
class LogMaskTests(SimpleTestCase):
    def line(self, msg):
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(SafeErrorFormatter())
        log = logging.getLogger("o2.mask")
        log.propagate = False
        log.addHandler(h)
        log.error("Internal Server Error: %s", msg)
        log.removeHandler(h)
        return buf.getvalue()

    def test_named_paths(self):
        for path in ("/v/r/SECRET/state", "/c/SECRET/u/SECRET", "/s/SECRET/repairs/SECRET/edit", "/s/SECRET/members/SECRET/out",
                     "/s/SECRET/hwp/SECRET/status", "/s/SECRET/hwp/out/SECRET/file", "/s/SECRET/school/members/SECRET/manager",
                     "/s/SECRET/bell/SECRET", "/s/SECRET/today/e/SECRET", "/s/SECRET/board/SECRET/f/SECRET"):
            self.assertNotIn("SECRET", self.line(path), path)

    def test_shape_stays(self):
        out = self.line("/s/AAA/repairs/BBB/state")
        self.assertIn("/s/***/repairs/***/state", out)
        self.assertIn("Internal Server Error", out)

    def test_every_route_masks_secret_params(self):
        """주소에 값이 들어가는 칸은 모두 가려진다(새 주소가 생겨도 여기서 걸린다). 공개 이름·날짜·종류는 비밀이 아니다."""
        public = {"slug", "ymd", "which", "v"}

        def walk(patterns, prefix=""):
            for p in patterns:
                r = prefix + str(p.pattern)
                if isinstance(p, URLResolver):
                    yield from walk(p.url_patterns, r)
                else:
                    yield r
        for route in walk(get_resolver().url_patterns):
            path = "/" + re.sub(r"<(?:\w+:)?(\w+)>", lambda m: m[1] if m[1] in public else "SECRET", route)
            if "SECRET" in path:
                self.assertNotIn("SECRET", self.line(path), route)


# ---------------------------------------------------------------- 10. 플랜 재개
class ResumeTests(TestCase):
    ON = ("--on", "--until", UNTIL)

    def setUp(self):
        self.s = school()
        g = Space.objects.create(school=self.s, grade=4, name="4")
        self.m = Member.objects.create(space=g, user=google("m"), name="관리", cls="4-1")
        self.hub = open_school(self.s, timezone.localdate() + timedelta(days=30), manager=self.m)
        self.s.refresh_from_db()
        self.acct, _ = VendorAccount.issue(self.hub, "업체", "담당", timezone.localdate() + timedelta(days=30), self.m)

    def run_cmd(self):
        out = StringIO()
        call_command("school_plan", "1", *self.ON, stdout=out)
        self.acct.refresh_from_db()
        return out.getvalue()

    def test_resume_after_off_revokes_vendor_and_keeps_tv(self):
        from meals.models import MealSettings
        ms = MealSettings.objects.create(space=self.hub)
        tv = ms.tv_token
        call_command("school_plan", "1", "--off", stdout=StringIO())
        out = self.run_cmd()
        self.assertIsNotNone(self.acct.revoked_at)
        self.assertIn("업체 링크 1개", out)
        self.assertIn("TV 주소는 그대로", out)
        ms.refresh_from_db()
        self.assertEqual(ms.tv_token, tv)

    def test_resume_after_expiry_revokes(self):
        School.objects.filter(pk=self.s.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.run_cmd()
        self.assertIsNotNone(self.acct.revoked_at)

    def test_extension_while_on_keeps_vendor(self):
        out = self.run_cmd()
        self.assertIsNone(self.acct.revoked_at)
        self.assertNotIn("업체 링크", out)


# ---------------------------------------------------------------- 보내기 전 크기 검사·앞단 글자 응답 안내
class UploadGuardMarkupTests(TestCase):
    def test_file_inputs_carry_limit(self):
        root = Path(settings.BASE_DIR)
        for path in ("notices/templates/notices/form.html", "repairs/templates/repairs/form.html", "meals/templates/meals/_main.html",
                     "collect/templates/collect/new.html"):
            text = (root / path).read_text()
            self.assertRegex(text, r'type="file"[^>]*data-max-mb=', path)

    def test_js_words(self):
        js = Path(settings.BASE_DIR).parent / "web/static/js"
        gt = (js / "gt.js").read_text()
        self.assertIn("파일이 너무 커요", gt)
        self.assertIn("지금 올리는 사람이 많아요", gt)
        self.assertIn("파일이 너무 커요", (js / "collect_new.js").read_text())
        self.assertIn("지금 올리는 사람이 많아요", (js / "collect_new.js").read_text())
        self.assertIn("표가 너무 커요", (js / "collect_new.js").read_text())


# 공간 격리: 다른 공간의 같은 이름은 삭제 명령과 무관하게 그대로
class DeleteAccountIsolationTests(TestCase):
    def test_other_space_untouched(self):
        a = Space.objects.create(school=school("1"), grade=4, name="4")
        b = Space.objects.create(school=school("2"), grade=4, name="4")
        gone = Member.objects.create(space=a, user=google("a"), name="같은이름", cls="4-1")
        Member.objects.create(space=a, user=google("a2"), name="남음", cls="4-2")
        stay = Member.objects.create(space=b, user=google("b"), name="같은이름", cls="4-1")
        c = Collection.objects.create(space=b, created_by=stay, title="t", source="sheet")
        Field.objects.create(collection=c, pos=0, name="값", kind="text")
        Row.objects.create(collection=c, pos=0, unit="4-1", who="같은이름", member=stay)
        n = Notice.objects.create(space=b, title="t", by=stay, by_name="같은이름")
        NoticeVersion.objects.create(notice=n, v=1, fields={}, body="", by="같은이름")
        call_command("delete_account", "a", stdout=StringIO())
        self.assertFalse(Member.objects.filter(pk=gone.pk).exists())
        n.refresh_from_db()
        self.assertEqual(n.by_name, "같은이름")
        self.assertEqual(NoticeVersion.objects.get(notice=n).by, "같은이름")
        self.assertTrue(Member.objects.filter(pk=stay.pk).exists())
