"""D5 마감하고 잠그기·다시 열기, 합본의 지각·안 냄 표시(prototype #lock, 모습은 design/collect-detail.html 마감 카드).

불변식
- 잠그기·다시 열기는 만든 사람만(prototype c.mine). 다른 회원 403, 다른 공간 404, CSRF 필요, GET 405.
- 잠그면 받는 링크로 더 낼 수 없다(줄 고르기·내기 모두 '마감됐어요'). 다시 열면 다시 낼 수 있다.
- 잠그는 순간 들어오던 제출은 잠금 확인을 다시 거쳐 거부된다(값이 남지 않는다).
- 잠근 동안 마감은 바꿀 수 없다(design: 마감 바꾸기 꺼짐).
- 마감 뒤에 낸 반은 합본에 '지각', 잠근 뒤 안 낸 반은 '안 냄'.
"""
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from spaces.models import Member, School, Space

from . import submit
from .models import Cell, Collection, Field, Row
from .submit import save_cell

SEOUL = ZoneInfo("Asia/Seoul")


@override_settings(SITE_URL="https://t.goteacher.xyz")
class LockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        cls.c = Collection.objects.create(space=cls.a, created_by=cls.owner, title="학예회 참가 인원",
                                          source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        cls.f = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)
        cls.other = Collection.objects.create(space=cls.b, title="옆 학년", source=Collection.Source.SHEET,
                                              unit_head=["학년반", "담임"])

    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, c=None, space=None, tail="lock"):
        c = c or self.c
        return f"/s/{(space or c.space).token}/c/{c.key}/{tail}"

    def lock(self, client=None, c=None, space=None, **data):
        return (client or self.client).post(self.url(c, space), data, HTTP_HX_REQUEST="true")

    def closed(self):
        self.c.refresh_from_db()
        return self.c.closed_at

    def send(self, row=None, v="21"):
        return Client().post(f"/c/{self.c.link_token}/u/{(row or self.r2).key}", {"f0": v, "by": ""})

    # ---- 잠그기·다시 열기
    def test_owner_locks(self):
        r = self.lock()
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(self.closed())
        body = r.content.decode()
        self.assertIn("마감하고 잠갔어요. 링크로는 더 낼 수 없어요.", body)
        self.assertIn("다시 열기", body)
        self.assertIn('data-toast-now="마감하고 잠갔어요. 링크로는 더 낼 수 없어요."', body)
        self.assertIn('id="board" hx-swap-oob="true"', body)  # 합본 '마감됨'도 바로
        self.assertIn(">마감됨<", body)

    def test_owner_reopens(self):
        self.lock()
        r = self.lock(open="1")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.closed())
        self.assertIn("다시 열었어요. 링크로 다시 낼 수 있어요.", r.content.decode())
        self.assertIn("마감하고 잠그기", r.content.decode())

    def test_lock_twice_keeps_first_time(self):
        self.lock()
        first = self.closed()
        self.lock()
        self.assertEqual(self.closed(), first)

    def test_other_member_forbidden(self):
        c2 = Client()
        c2.force_login(self.u2)
        self.assertEqual(self.lock(client=c2).status_code, 403)
        self.assertIsNone(self.closed())
        body = c2.get(self.url(tail="")).content.decode()
        self.assertNotIn("마감하고 잠그기", body)
        Collection.objects.filter(pk=self.c.pk).update(closed_at=timezone.now())
        self.assertEqual(self.lock(client=c2, open="1").status_code, 403)
        self.assertIsNotNone(self.closed())

    def test_owner_sees_lock_button(self):
        self.assertContains(self.client.get(self.url(tail="")), "마감하고 잠그기")

    def test_other_space_404(self):
        c2 = Client()
        c2.force_login(self.ub)
        self.assertEqual(self.lock(client=c2).status_code, 404)
        self.assertEqual(self.lock(c=self.other, space=self.a).status_code, 404)
        self.assertIsNone(self.closed())

    def test_anonymous_redirected(self):
        self.assertIn(self.lock(client=Client()).status_code, (204, 302))  # htmx면 HX-Redirect
        self.assertIsNone(self.closed())

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        self.assertEqual(self.lock(client=c).status_code, 403)
        self.assertIsNone(self.closed())

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    def test_due_locked_while_closed(self):
        self.lock()
        r = self.client.post(self.url(tail="due"), {"date": "2027-11-03", "time": "17:00"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 403)
        self.c.refresh_from_db()
        self.assertIsNone(self.c.due)
        body = self.client.get(self.url(tail="")).content.decode()
        self.assertIn('data-open="#dueDlg" disabled', body)

    # ---- 받는 사람
    def test_locked_rejects_submit(self):
        self.lock()
        r = self.send()
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "마감됐어요", status_code=403)
        self.assertFalse(Cell.objects.exists())
        self.assertContains(Client().get(f"/c/{self.c.link_token}"), "마감됐어요")
        self.assertContains(Client().get(f"/c/{self.c.link_token}/u/{self.r2.key}"), "마감됐어요")

    def test_reopen_accepts_again(self):
        self.lock()
        self.lock(open="1")
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(Cell.objects.count(), 1)

    def test_lock_during_submit_rejected(self):
        """폼 검사를 마친 뒤 잠금 직전에 마감되면(동시성) 잠금 안의 재확인이 거부한다."""
        real = submit._clean

        def lock_midway(field, raw):
            Collection.objects.filter(pk=self.c.pk).update(closed_at=timezone.now())
            return real(field, raw)

        with mock.patch.object(submit, "_clean", side_effect=lock_midway):
            r = self.send()
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Cell.objects.exists())

    # ---- 합본: 지각·안 냄
    def test_late_mark(self):
        due = timezone.now() - timedelta(hours=1)
        Collection.objects.filter(pk=self.c.pk).update(due=due)
        save_cell(self.r1, self.f, "20", "")
        Cell.objects.filter(row=self.r1).update(at=due - timedelta(minutes=5))
        save_cell(self.r2, self.f, "21", "")  # 마감 뒤
        body = self.client.get(self.url(tail="live")).content.decode()
        self.assertEqual(body.count("<small class=\"late\">지각</small>"), 1)
        i = body.index("4-2")
        self.assertIn("지각", body[i:])
        self.assertNotIn("지각", body[body.index("4-1"):i])

    def test_no_late_without_due(self):
        save_cell(self.r2, self.f, "21", "")
        self.assertNotIn("지각", self.client.get(self.url(tail="live")).content.decode())

    def test_locked_missing_shows_not_sent(self):
        save_cell(self.r1, self.f, "20", "")
        self.lock()
        body = self.client.get(self.url(tail="live")).content.decode()
        self.assertIn('<span class="st late">안 냄</span>', body)
        self.assertNotIn(">미제출<", body)

    def test_recv_late_label_unchanged(self):
        Collection.objects.filter(pk=self.c.pk).update(due=datetime(2020, 1, 1, tzinfo=SEOUL))
        self.send()
        self.assertContains(Client().get(f"/c/{self.c.link_token}"), "늦게 냈어요")


# ---- QA: 실제 동시 요청(PostgreSQL에서만). 잠그기 vs 제출이 같은 Collection 줄 잠금으로 순서가 정해지는지.
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402

from django.db import connection, connections  # noqa: E402
from django.test import TransactionTestCase  # noqa: E402


@unittest.skipUnless(connection.vendor == "postgresql", "select_for_update 경쟁은 PostgreSQL에서만 본다")
@override_settings(SITE_URL="https://t.goteacher.xyz")
class LockRaceTests(TransactionTestCase):
    HOLD = 1.0

    def setUp(self):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        self.a = Space.objects.create(school=school, grade=4, name="4학년")
        self.ua = get_user_model().objects.create(username="google:a")
        self.owner = Member.objects.create(space=self.a, name="김가람", cls="4-1", user=self.ua)
        self.c = Collection.objects.create(space=self.a, created_by=self.owner, title="학예회",
                                           source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        self.f = Field.objects.create(collection=self.c, pos=0, name="참가(명)", kind="number")
        self.r = Row.objects.create(collection=self.c, pos=0, unit="4-2", who="박서준")

    def _run(self, fn, out, k):
        try:
            t0 = time.monotonic()
            out[k] = (fn().status_code, time.monotonic() - t0)
        finally:
            connections.close_all()

    def _lock(self):
        cl = Client()
        cl.force_login(self.ua)
        return cl.post(f"/s/{self.a.token}/c/{self.c.key}/lock", HTTP_HX_REQUEST="true")

    def _send(self):
        return Client().post(f"/c/{self.c.link_token}/u/{self.r.key}", {"f0": "21", "by": ""})

    def test_lock_first_then_submit_rejected(self):
        """잠그기가 줄 잠금을 쥔 채 커밋 전 → 제출은 첫 확인을 지나도 잠금 안 재확인에서 403."""
        real = Collection.save

        def slow_save(obj, *a, **kw):
            real(obj, *a, **kw)
            if kw.get("update_fields") == ["closed_at"]:
                time.sleep(self.HOLD)  # 아직 커밋 전(트랜잭션 안)

        out = {}
        with mock.patch.object(Collection, "save", slow_save):
            t1 = threading.Thread(target=self._run, args=(self._lock, out, "lock"))
            t1.start()
            time.sleep(0.3)
            t2 = threading.Thread(target=self._run, args=(self._send, out, "send"))
            t2.start()
            t1.join()
            t2.join()
        self.assertEqual(out["lock"][0], 200)
        self.assertEqual(out["send"][0], 403)
        self.assertGreater(out["send"][1], self.HOLD - 0.4)  # 실제로 잠금을 기다렸다
        self.assertFalse(Cell.objects.exists())

    def test_submit_first_then_lock_waits(self):
        """제출이 줄 잠금을 쥔 채 저장 중 → 잠그기는 기다렸다가 잠근다. 제출 값은 남는다(마감 전 제출)."""
        real = submit.save_cell

        def slow_cell(*a, **kw):
            r = real(*a, **kw)
            time.sleep(self.HOLD)
            return r

        out = {}
        with mock.patch.object(submit, "save_cell", slow_cell):
            t1 = threading.Thread(target=self._run, args=(self._send, out, "send"))
            t1.start()
            time.sleep(0.3)
            t2 = threading.Thread(target=self._run, args=(self._lock, out, "lock"))
            t2.start()
            t1.join()
            t2.join()
        self.assertEqual(out["send"][0], 200)
        self.assertEqual(out["lock"][0], 200)
        self.assertGreater(out["lock"][1], self.HOLD - 0.4)
        self.assertEqual(Cell.objects.count(), 1)
        self.c.refresh_from_db()
        self.assertIsNotNone(self.c.closed_at)
        self.assertEqual(self._send().status_code, 403)
