"""G2 QA 보강: test_push.py에 없는 경계만.

- 창 끝 1분 전(12:49·16:39)은 보낸다. 창 시작 전 1분(12:19·14:39)은 안 보낸다.
- 금요일 방과후 뒤·토·일에 쌓인 것은 월요일 아침 묶음 한 건에 다 들어간다.
- 끄기 뒤에는 그 브라우저로 보내지 않는다.
- 다른 동학년 회원이 같은 브라우저 구독을 올리면 공간째 옮겨 가고, 이전 회원 알림은 그 브라우저로 안 간다.
- VAPID 공개키가 비면 🔔에 '이 PC에서 알림 받기' 상자가 없다.
"""
import json

from django.test import Client, override_settings

from .models import PushSubscription
from .test_push import KEYS, Base, at


class WindowEdgeTests(Base):
    def setUp(self):
        super().setUp()
        self.sub(self.m2)
        self.note(self.m2, self.r2)

    def test_last_minute_inside_window_sent(self):
        self.assertEqual(self.run_at(at(4, 12, 49)).call_count, 1)
        self.note(self.m2, self.r2, kind="d0")
        self.assertEqual(self.run_at(at(4, 16, 39)).call_count, 1)

    def test_minute_before_start_not_sent(self):
        for t in [at(4, 12, 19), at(4, 14, 39)]:
            self.assertEqual(self.run_at(t).call_count, 0, t)


class WeekendCarryTests(Base):
    def test_weekend_notes_go_monday_morning(self):
        self.sub(self.m2)
        self.run_at(at(6, 14, 40))  # 금 방과후: 첫 알림 보냄
        self.note(self.m2, self.r2, kind="d0", day=at(7, 8, 0).date())
        self.note(self.m2, self.r2, kind="d1", day=at(8, 8, 0).date())
        for t in [at(7, 8, 20), at(7, 14, 40), at(8, 12, 20)]:
            self.assertEqual(self.run_at(t).call_count, 0, t)
        wp = self.run_at(at(9, 8, 20))  # 월 아침
        self.assertEqual(wp.call_count, 1)
        self.assertIn("2건", json.loads(wp.call_args.kwargs["data"])["body"])


class OffAndMoveTests(Base):
    def client_for(self, user):
        c = Client(enforce_csrf_checks=True)
        c.force_login(user)
        space = self.a if user != self.ub else self.b
        c.get(f"/s/{space.token}/c/")
        return c

    def post(self, c, path, body):
        return c.post(path, json.dumps(body), content_type="application/json",
                      HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)

    def test_off_then_no_send(self):
        s = self.sub(self.m2)
        self.note(self.m2, self.r2)
        self.post(self.client_for(self.u2), f"/s/{self.a.token}/push/off", {"endpoint": s.endpoint})
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 0)

    def test_move_to_member_of_other_space(self):
        old = self.sub(self.m2)
        self.note(self.m2, self.r2)
        r = self.post(self.client_for(self.ub), f"/s/{self.b.token}/push", {"endpoint": old.endpoint, "keys": KEYS})
        self.assertEqual(r.status_code, 204)
        s = PushSubscription.objects.get()
        self.assertEqual((s.member, s.space), (self.mb, self.b))
        self.assertEqual(PushSubscription.objects.in_space(self.a).count(), 0)
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 0)  # 박서준 알림은 이제 이 브라우저로 안 간다


class NoKeyBellTests(Base):
    @override_settings(VAPID_PUBLIC_KEY="")
    def test_no_public_key_no_toggle(self):
        c = Client()
        c.force_login(self.u2)
        html = c.get(f"/s/{self.a.token}/bell").content.decode()
        self.assertNotIn("pushBox", html)
        self.assertNotIn("이 PC에서 알림 받기", html)
