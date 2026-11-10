"""T1 독립 검증(gt-qa): test_org.py에 없는 경계만. expectedFailure는 확인된 결함이다(고치면 표시를 지운다).

- 체험 동시 요청(같은 계정 두 요청을 동시에): PostgreSQL에서만 의미가 있어 그때만 돈다(sqlite는 건너뜀).
- 공간 격리: 학교 회원 → 기관 주소, 기관 회원 → 학교·다른 기관 주소·초대 링크 바꿔 끼우기.
- 푸시 묶음: 기관 회원에게 보내도 NEIS 요청 0번.
- 체험 만료: 학교 플랜 만료와 같은 읽기 전용(게시판 쓰기 막힘), 새 가입 막힘.
- 남의 기관에 초대로 들어갔다 나온 계정은 자기 체험을 한 번 열 수 있다(체험 기록은 trial_by).
- 기관 화면에 남은 학교 전용 주소·문구(결함).
"""
import threading
from datetime import datetime, timedelta
from io import StringIO
from unittest import mock, skipUnless
from zoneinfo import ZoneInfo

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from collect.models import Notification, PushSubscription
from collect.test_push import KEYS, VAPID
from notices.models import Notice
from today.models import Event

from . import neis
from .models import INVITE, Member, PlanRequest, School, Space
from .test_org import ORG, UNTIL, Base, as_, google


class IsolationTests(Base):
    def setUp(self):
        super().setUp()
        self.org, self.boss = self.make_org()
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        self.t = Member.objects.create(space=self.hub, user=google("t", "교사"), name="교사", cls="4-1", group="g4", is_manager=True)

    def test_school_member_cannot_open_org_and_back(self):
        with mock.patch.object(neis, "urlopen"):
            for path in ("/today", "/schedule", "/board", "/settings?a=space", "/c/new"):
                self.assertEqual(as_(self.t.user).get(f"/s/{self.org.token}{path}").status_code, 404, path)
                self.assertEqual(as_(self.boss.user).get(f"/s/{self.hub.token}{path}").status_code, 404, path)

    def test_write_into_other_space_is_404_and_nothing_made(self):
        r = as_(self.t.user).post(f"/s/{self.org.token}/today/events", {"line": "내일 회의"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Event.objects.filter(space=self.org).exists())

    def test_swapped_or_bad_invite_token(self):
        self.assertEqual(Client().get("/j/" + "x" * 32).status_code, 404)
        # 학교 회원이 기관 초대 링크로 들어오려 하면 자기 공간으로 가고 자리는 생기지 않는다
        c = as_(self.t.user)
        c.get(f"/j/{self.org.invite_token}")
        c.post("/join", {"name": "교사"})
        self.assertFalse(Member.objects.filter(space=self.org, user=self.t.user).exists())
        # 다른 기관 회원도 같다
        _, other = self.make_org(user=google("o2", "남"))
        c = as_(other.user)
        s = c.session
        s[INVITE] = self.org.invite_token
        s.save()
        c.post("/join", {"name": "남"})
        self.assertFalse(Member.objects.filter(space=self.org, user=other.user).exists())


@override_settings(**VAPID)
class PushTests(Base):
    def test_digest_to_org_member_makes_no_neis_request(self):
        space, boss = self.make_org()
        PushSubscription.objects.create(space=space, member=boss, endpoint="https://fcm.googleapis.com/fcm/send/org1",
                                        p256dh=KEYS["p256dh"], auth=KEYS["auth"])
        now = datetime(2026, 11, 4, 8, 25, tzinfo=ZoneInfo("Asia/Seoul"))  # 수요일 아침 묶음
        Notification.objects.create(space=space, member=boss, kind=Notification.Kind.JOIN, day=now.date(), text="김 선생님이 들어왔어요",
                                    link=f"/s/{space.token}/settings?a=space")
        with mock.patch.object(neis, "urlopen") as net, mock.patch("django.utils.timezone.now", return_value=now), \
                mock.patch("collect.push.webpush") as wp:
            call_command("push_digest", verbosity=0)
        net.assert_not_called()
        self.assertEqual(wp.call_count, 1)


class ExpiredTests(Base):
    def setUp(self):
        super().setUp()
        self.space, self.boss = self.make_org()
        School.objects.filter(pk=self.space.school_id).update(paid_until=timezone.localdate() - timedelta(days=1))

    def test_board_write_blocked_like_school_plan_expiry(self):
        r = as_(self.boss.user).post(f"/s/{self.space.token}/board/new", {"title": "공지", "body": "내용"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Notice.objects.exists())

    def test_expired_trial_user_cannot_open_second_trial_but_can_apply(self):
        c = as_(self.boss.user)
        School.objects.filter(pk=self.space.school_id).update(trial_by=self.boss.user)
        Member.objects.filter(pk=self.boss.pk).delete()
        self.assertEqual(c.post("/new/org", {**ORG, "org": "학교마을협력과".upper()}).status_code, 409)
        r = c.post("/school-plan", {"by_name": "관리자", "position": "장학사", "email": "b@example.com", "kind": "org",
                                    "atpt": "E10", "org": "학교마을협력과"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(PlanRequest.objects.count(), 1)


class TrialRuleTests(Base):
    def test_invited_member_who_left_can_open_own_trial(self):
        """체험 1회는 '내가 연 체험'으로 센다. 남의 기관에 초대로 들어간 것은 세지 않는다."""
        space, _ = self.make_org()
        Member.objects.create(space=space, user=self.user, name="이수빈", cls="미배정")
        Member.objects.filter(space=space, user=self.user).delete()
        r = self.client.post("/new/org", ORG)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(School.objects.filter(trial_by=self.user).count(), 1)

    def test_org_plan_request_refuses_school_request(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초")
        req = PlanRequest.objects.create(school=school, user=self.user, by_name="이수빈", position="교감", email="a@example.com")
        with self.assertRaises(CommandError):
            call_command("org_plan", f"--request={req.pk}", "--on", "--until", "2099-02-28", "--identity-checked",
                         stdout=StringIO(), stderr=StringIO())
        self.assertFalse(Space.objects.filter(school=school).exists())


class LeftoverSchoolBitsTests(Base):
    """기관 화면에 남은 학교 전용 주소·문구(결함 기록)."""

    def test_collect_new_has_no_link_to_school_page(self):
        """결함: 취합 만들기의 '줄 순서' 안내가 /school?tab=duty(기관에서 404)로 건다."""
        space, boss = self.make_org()
        html = as_(boss.user).get(f"/s/{space.token}/c/new").content.decode()
        self.assertNotIn(f"/s/{space.token}/school", html)

    def test_print_has_no_duty_legend(self):
        """결함: 기관 인쇄 범례에 '○ 복무'가 남아 있다(기관은 복무가 없다)."""
        space, boss = self.make_org()
        with mock.patch.object(neis, "urlopen"):
            html = as_(boss.user).get(f"/s/{space.token}/schedule/print").content.decode()
        self.assertNotIn("복무", html)


@skipUnless(connection.vendor == "postgresql", "동시 요청 직렬화(select_for_update)는 PostgreSQL에서만 확인된다")
class ConcurrentTrialTests(TransactionTestCase):
    def test_two_requests_at_once_make_one_org(self):
        user = google("race")
        names = ["학교마을협력과", "학교마을협력과 ", "ABC과", "abc과"]
        gate = threading.Barrier(len(names))
        codes = []

        def go(org):
            c = as_(user)
            gate.wait()
            codes.append(c.post("/new/org", {**ORG, "org": org}).status_code)
            connection.close()

        threads = [threading.Thread(target=go, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(School.objects.filter(trial_by=user).count(), 1, codes)
        self.assertEqual(Member.objects.filter(user=user).count(), 1)
        self.assertEqual(sorted(codes).count(303), 1, codes)
