"""G6 만료 세션·기기 토큰·주인 없는 손님 정리(cleanup_sessions). 시각은 NOW 로 고정한다.

불변식
- 만료된 세션은 지우고 살아 있는 세션은 남긴다.
- 1년(DEVICE_AGE) 지난 기기 토큰, 비활성 사용자의 기기 토큰은 지운다. 살아 있는 토큰은 남긴다.
- 손님(guest:)이면서 회원 자리가 없고, 30일 넘게 들어오지 않았고, 살아 있는 기기 토큰도 없으면 지운다.
- 회원 자리가 있는 손님, 구글 회원은 아무리 오래돼도 지우지 않는다. 낸 값(Cell.by 문자열)은 그대로다.
- 출력은 건수만. --dry-run 은 아무것도 지우지 않는다.
"""
import io
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import TestCase

from collect.models import Cell, Collection, Field, Row
from spaces.models import Member, School, Space

from .models import DeviceToken, new_guest

User = get_user_model()
NOW = datetime(2027, 12, 1, 9, 0, tzinfo=dt_tz.utc)
DAY = timedelta(days=1)


class CleanupTests(TestCase):
    def setUp(self):
        p = mock.patch("django.utils.timezone.now", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        self.space = Space.objects.create(school=school, grade=4, name="새솔초 4학년")

    def run_cmd(self, *args):
        out = io.StringIO()
        call_command("cleanup_sessions", *args, stdout=out)
        return out.getvalue()

    def token(self, user, age_days):
        DeviceToken.issue(user)
        t = DeviceToken.objects.filter(user=user).latest("pk")
        DeviceToken.objects.filter(pk=t.pk).update(created_at=NOW - age_days * DAY)
        return t

    def guest(self, name, age_days, seat=False):
        g = new_guest(name)
        User.objects.filter(pk=g.pk).update(date_joined=NOW - age_days * DAY, last_login=NOW - age_days * DAY)
        if seat:
            Member.objects.create(space=self.space, user=g, name=name, cls="4-2")
        return g

    def test_sessions(self):
        Session.objects.create(session_key="old" * 10, session_data="", expire_date=NOW - DAY)
        Session.objects.create(session_key="new" * 10, session_data="", expire_date=NOW + DAY)
        self.run_cmd()
        self.assertEqual(list(Session.objects.values_list("session_key", flat=True)), ["new" * 10])

    def test_device_tokens_expired_and_inactive(self):
        u = User.objects.create(username="google:1")
        old, live = self.token(u, 366), self.token(u, 364)
        off = User.objects.create(username="google:2", is_active=False)
        self.token(off, 1)
        self.run_cmd()
        self.assertEqual(list(DeviceToken.objects.values_list("pk", flat=True)), [live.pk])
        self.assertNotEqual(old.pk, live.pk)
        self.assertTrue(User.objects.filter(pk=off.pk).exists())  # 사용자는 그대로

    def test_orphan_guest_rules(self):
        gone = self.guest("지울손님", 31)
        recent = self.guest("최근손님", 29)
        with_token = self.guest("토큰손님", 100)
        self.token(with_token, 10)
        expired_token = self.guest("만료손님", 400)
        self.token(expired_token, 366)
        seated = self.guest("자리손님", 400, seat=True)
        google = User.objects.create(username="google:3", date_joined=NOW - 400 * DAY)
        self.run_cmd()
        left = set(User.objects.values_list("pk", flat=True))
        self.assertNotIn(gone.pk, left)
        self.assertNotIn(expired_token.pk, left)
        self.assertTrue({recent.pk, with_token.pk, seated.pk, google.pk} <= left)

    def test_submitted_values_survive_guest_cleanup(self):
        g = self.guest("정하은", 400, seat=True)
        m = Member.objects.get(user=g)
        c = Collection.objects.create(space=self.space, title="체험학습", source="paste")
        f = Field.objects.create(collection=c, pos=0, name="인원")
        row = Row.objects.create(collection=c, pos=0, unit="4-2", who="정하은", member=m)
        Cell.objects.create(row=row, field=f, value="24", by="정하은")
        m.delete()  # 자리가 사라져 주인 없는 손님이 된다(예: 공간이 지워짐)
        self.run_cmd()
        self.assertFalse(User.objects.filter(pk=g.pk).exists())
        cell = Cell.objects.get(row=row)
        self.assertEqual((cell.value, cell.by), ("24", "정하은"))

    def test_dry_run_deletes_nothing_and_prints_counts_only(self):
        Session.objects.create(session_key="old" * 10, session_data="", expire_date=NOW - DAY)
        u = User.objects.create(username="google:1")
        self.token(u, 400)
        self.guest("지울손님", 31)
        out = self.run_cmd("--dry-run")
        self.assertEqual((Session.objects.count(), DeviceToken.objects.count(), User.objects.count()), (1, 1, 2))
        self.assertIn("세션 1건", out)
        self.assertIn("기기 토큰 1건", out)
        self.assertIn("손님 1명", out)
        self.assertNotIn("지울손님", out)
        out = self.run_cmd()
        self.assertEqual((Session.objects.count(), DeviceToken.objects.count(), User.objects.count()), (0, 0, 1))
