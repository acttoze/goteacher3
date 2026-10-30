"""G2 웹 푸시 묶음 알림(08:20·12:20·방과후 14:40).

불변식
- push_digest는 묶음 시각 이후 첫 실행에 한 번만 보낸다. 묶음 창(08:20~08:40·12:20~12:50·14:40~16:40) 밖(수업 중·밤)과
  주말·NEIS 휴업일·방학에는 보내지 않는다. cron이 5분마다 불러도 같은 묶음은 한 번.
- 회원별 한 건: 그 사이 쌓인 안 읽은 🔔(아직 푸시 안 한 것)을 묶는다. 본문은 모으기 제목·건수만(제출 값 없음).
- 푸시 서비스가 404·410이면 그 구독을 지운다. 발송은 목(pywebpush.webpush).
- 구독 저장·끄기는 POST(CSRF)·캐시 금지. 내 구독만 끌 수 있다. 다른 공간이면 404. 푸시 서비스 주소만 받는다(SSRF 방지).
"""
import json
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from pywebpush import WebPushException

from spaces import neis
from spaces.models import Member, School, Space

from .models import Collection, Notification, PushSubscription, Row

SEOUL = ZoneInfo("Asia/Seoul")
# 실제 P-256 공개키(곡선 위 점)·16바이트 auth. 형식만 맞는 가짜 점은 저장 단계에서 거부한다.
KEYS = {"p256dh": "BMW0QqyDicZ7kiwWQf0SK1JJ6-T7X4Nzh4kzQNIm6D8SF1EybvBvC8ObhN9DPjYsm7n4-cIAvWoLX7dwr4503aI",
        "auth": "x3xW7pY8-rEeqYgB9z3m1Q"}
VAPID = {"VAPID_PUBLIC_KEY": "pub", "VAPID_PRIVATE_KEY": "priv", "VAPID_SUBJECT": "mailto:admin@goteacher.xyz"}


def at(day, hh, mm):
    """2026-11-day. 11/2(월)~11/6(금), 11/7(토)·11/8(일)."""
    return datetime(2026, 11, day, hh, mm, tzinfo=SEOUL)


class Resp:
    def __init__(self, code):
        self.status_code = code


@override_settings(**VAPID)
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        cls.c = Collection.objects.create(space=cls.a, title="학예회 참가 인원", source=Collection.Source.SHEET,
                                          unit_head=["학년반", "담임"])
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)

    def setUp(self):
        p = mock.patch("collect.push.neis.day_off", return_value=False)
        self.day_off = p.start()
        self.addCleanup(p.stop)

    def note(self, member, row, kind="d1", day=None):
        return Notification.objects.create(space=member.space, member=member, row=row, kind=kind,
                                           day=day or at(4, 8, 0).date(), text=f"{row.collection.title} — 내일 17:00 마감",
                                           link=f"/c/{row.collection.link_token}/u/{row.key}")

    def sub(self, member, n=1):
        return PushSubscription.objects.create(space=member.space, member=member,
                                               endpoint=f"https://fcm.googleapis.com/fcm/send/{member.name}{n}",
                                               p256dh=KEYS["p256dh"], auth=KEYS["auth"])

    def run_at(self, now, resp=None):
        with mock.patch("django.utils.timezone.now", return_value=now), \
                mock.patch("collect.push.webpush", side_effect=resp) as wp:
            call_command("push_digest", verbosity=0)
        return wp


class DigestTimingTests(Base):
    def setUp(self):
        super().setUp()
        self.sub(self.m2)
        self.note(self.m2, self.r2)

    def test_0819_not_sent(self):
        self.assertEqual(self.run_at(at(4, 8, 19)).call_count, 0)

    def test_0820_sent(self):
        wp = self.run_at(at(4, 8, 20))
        self.assertEqual(wp.call_count, 1)
        kw = wp.call_args.kwargs
        self.assertEqual(kw["subscription_info"]["endpoint"], "https://fcm.googleapis.com/fcm/send/박서준1")
        self.assertEqual(kw["vapid_private_key"], "priv")
        self.assertEqual(kw["vapid_claims"], {"sub": "mailto:admin@goteacher.xyz"})
        self.assertLessEqual(kw["ttl"], 20 * 60)  # 묶음 창이 끝나면 푸시 서비스도 버린다(수업 중에 늦게 뜨지 않게)
        body = json.loads(kw["data"])
        self.assertEqual(body["title"], "고티처 · 아침 묶음")
        self.assertIn("1건", body["body"])
        self.assertIn("학예회 참가 인원", body["body"])
        self.assertTrue(body["url"].endswith(f"/s/{self.a.token}/today"))

    def test_twice_in_same_bundle_once(self):
        self.run_at(at(4, 8, 20))
        self.note(self.m2, self.r2, kind="d0")  # 그 뒤 쌓인 것도 같은 묶음 창에서는 다시 안 보낸다
        self.assertEqual(self.run_at(at(4, 8, 25)).call_count, 0)

    def test_late_first_run_inside_window(self):
        self.assertEqual(self.run_at(at(4, 8, 35)).call_count, 1)

    def test_during_class_not_sent(self):
        for t in [at(4, 8, 40), at(4, 10, 0), at(4, 12, 50), at(4, 13, 30), at(4, 16, 40), at(4, 22, 0)]:
            self.assertEqual(self.run_at(t).call_count, 0, t)

    def test_noon_and_after_school(self):
        self.assertEqual(json.loads(self.run_at(at(4, 12, 20)).call_args.kwargs["data"])["title"], "고티처 · 점심 묶음")
        self.note(self.m2, self.r2, kind="d0")
        self.assertEqual(json.loads(self.run_at(at(4, 14, 40)).call_args.kwargs["data"])["title"], "고티처 · 방과후 묶음")

    def test_weekend_not_sent(self):
        self.assertEqual(self.run_at(at(7, 8, 20)).call_count, 0)
        self.assertEqual(self.run_at(at(8, 12, 20)).call_count, 0)

    def test_neis_day_off_not_sent_then_next_school_day(self):
        self.day_off.return_value = True
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 0)
        self.day_off.return_value = False
        self.assertEqual(self.run_at(at(5, 8, 20)).call_count, 1)  # 쉬는 날 쌓인 것은 다음 등교일 아침에


class DigestContentTests(Base):
    def test_one_per_member_bundled(self):
        self.sub(self.m2)
        self.sub(self.owner)
        self.note(self.m2, self.r2, kind="d1")
        self.note(self.m2, self.r2, kind="d0")
        self.note(self.owner, self.r1)
        wp = self.run_at(at(4, 8, 20))
        self.assertEqual(wp.call_count, 2)
        bodies = {c.kwargs["subscription_info"]["endpoint"]: json.loads(c.kwargs["data"])["body"] for c in wp.call_args_list}
        self.assertIn("2건", bodies["https://fcm.googleapis.com/fcm/send/박서준1"])

    def test_each_device_of_member(self):
        self.sub(self.m2, 1)
        self.sub(self.m2, 2)
        self.note(self.m2, self.r2)
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 2)

    def test_read_or_already_pushed_not_sent(self):
        self.sub(self.m2)
        n = self.note(self.m2, self.r2)
        n.read = True
        n.save()
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 0)
        self.note(self.m2, self.r2, kind="d0")
        self.assertEqual(self.run_at(at(4, 12, 20)).call_count, 1)
        self.assertEqual(self.run_at(at(4, 14, 40)).call_count, 0)  # 점심에 보낸 것은 방과후에 다시 안 보냄

    def test_no_values_in_body(self):
        from .models import Cell, Field
        f = Field.objects.create(collection=self.c, pos=0, name="참가(명)", kind="number")
        Cell.objects.create(row=self.r2, field=f, value="27")
        self.sub(self.m2)
        self.note(self.m2, self.r2)
        data = self.run_at(at(4, 8, 20)).call_args.kwargs["data"]
        self.assertNotIn("27", data)
        self.assertNotIn("박서준", data)

    def test_gone_subscription_deleted(self):
        s410, s404, ok = self.sub(self.m2, 1), self.sub(self.m2, 2), self.sub(self.m2, 3)
        self.note(self.m2, self.r2)

        def push(subscription_info, **kw):
            code = {s410.endpoint: 410, s404.endpoint: 404}.get(subscription_info["endpoint"])
            if code:
                raise WebPushException("gone", response=Resp(code))
        self.run_at(at(4, 8, 20), resp=push)
        self.assertEqual(list(PushSubscription.objects.values_list("pk", flat=True)), [ok.pk])

    def test_other_error_keeps_subscription(self):
        s = self.sub(self.m2)
        self.note(self.m2, self.r2)
        self.run_at(at(4, 8, 20), resp=WebPushException("busy", response=Resp(503)))
        self.assertTrue(PushSubscription.objects.filter(pk=s.pk).exists())

    @override_settings(VAPID_PRIVATE_KEY="")
    def test_no_key_no_send(self):
        self.sub(self.m2)
        self.note(self.m2, self.r2)
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 0)


class DayOffTests(TestCase):
    def rows(self, *rows):
        return mock.patch("spaces.neis._get", return_value=(list(rows), len(rows)))

    def test_holiday_and_vacation(self):
        d = at(4, 8, 0).date()
        with self.rows({"AA_YMD": "20261104", "EVENT_NM": "개교기념일", "SBTR_DD_SC_NM": "휴업일"}):
            self.assertTrue(neis.day_off("S10", "1", d))
        with self.rows({"AA_YMD": "20261104", "EVENT_NM": "추석", "SBTR_DD_SC_NM": "공휴일"}):
            self.assertTrue(neis.day_off("S10", "1", d))
        with self.rows({"AA_YMD": "20261104", "EVENT_NM": "겨울방학", "SBTR_DD_SC_NM": "해당없음"}):
            self.assertTrue(neis.day_off("S10", "1", d))
        with self.rows({"AA_YMD": "20261104", "EVENT_NM": "학부모 상담주간", "SBTR_DD_SC_NM": "해당없음"}):
            self.assertFalse(neis.day_off("S10", "1", d))
        with self.rows():
            self.assertFalse(neis.day_off("S10", "1", d))


@override_settings(**VAPID)
class SubscribeViewTests(Base):
    def client_for(self, user):
        c = Client(enforce_csrf_checks=True)
        c.force_login(user)
        c.get(f"/s/{self.a.token}/c/")  # csrftoken 쿠키(공통 틀의 로그아웃 폼)
        return c

    def post(self, c, path, body):
        return c.post(path, json.dumps(body), content_type="application/json",
                      HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)

    def body(self, endpoint="https://fcm.googleapis.com/fcm/send/abc"):
        return {"endpoint": endpoint, "keys": KEYS}

    def test_subscribe_saves_mine(self):
        c = self.client_for(self.u2)
        r = self.post(c, f"/s/{self.a.token}/push", self.body())
        self.assertEqual(r.status_code, 204)
        self.assertIn("no-store", r["Cache-Control"])
        s = PushSubscription.objects.get()
        self.assertEqual((s.member, s.space, s.p256dh, s.auth), (self.m2, self.a, KEYS["p256dh"], KEYS["auth"]))
        self.post(c, f"/s/{self.a.token}/push", self.body())  # 같은 기기 다시 → 하나
        self.assertEqual(PushSubscription.objects.count(), 1)

    def test_same_browser_moves_to_current_member(self):
        old = self.sub(self.owner)  # 같은 PC를 다른 선생님이 이어 쓰면 그 브라우저 구독은 지금 회원 것
        self.post(self.client_for(self.u2), f"/s/{self.a.token}/push", self.body(old.endpoint))
        self.assertEqual(PushSubscription.objects.get().member, self.m2)

    def test_csrf_required(self):
        c = self.client_for(self.u2)
        r = c.post(f"/s/{self.a.token}/push", json.dumps(self.body()), content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())

    def test_get_not_allowed(self):
        self.assertEqual(self.client_for(self.u2).get(f"/s/{self.a.token}/push").status_code, 405)

    def test_other_space_404(self):
        c = self.client_for(self.u2)
        self.assertEqual(self.post(c, f"/s/{self.b.token}/push", self.body()).status_code, 404)
        self.assertEqual(self.post(c, f"/s/{self.b.token}/push/off", self.body()).status_code, 404)
        self.assertFalse(PushSubscription.objects.exists())

    def test_only_push_services(self):
        c = self.client_for(self.u2)
        for bad in ["http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://evil.example/fcm.googleapis.com",
                    "https://fcm.googleapis.com.evil.example/x", "https://localhost:8000/x", "x" * 2000]:
            self.assertEqual(self.post(c, f"/s/{self.a.token}/push", self.body(bad)).status_code, 400, bad)
        bad_keys = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "<", "auth": ""}}
        self.assertEqual(self.post(c, f"/s/{self.a.token}/push", bad_keys).status_code, 400)
        for ok in ["https://updates.push.services.mozilla.com/wpush/v2/x", "https://wns2-sg2p.notify.windows.com/w/?token=x",
                   "https://web.push.apple.com/x"]:
            self.assertEqual(self.post(c, f"/s/{self.a.token}/push", self.body(ok)).status_code, 204, ok)

    def test_off_deletes_mine_only(self):
        mine = self.sub(self.m2)
        theirs = self.sub(self.owner)
        c = self.client_for(self.u2)
        r = self.post(c, f"/s/{self.a.token}/push/off", {"endpoint": theirs.endpoint})
        self.assertEqual(r.status_code, 204)
        self.assertTrue(PushSubscription.objects.filter(pk=theirs.pk).exists())
        self.post(c, f"/s/{self.a.token}/push/off", {"endpoint": mine.endpoint})
        self.assertFalse(PushSubscription.objects.filter(pk=mine.pk).exists())

    def test_in_space_isolation(self):
        self.sub(self.m2)
        self.sub(self.mb)
        self.assertEqual(PushSubscription.objects.in_space(self.a).count(), 1)


@override_settings(**VAPID)
class BellNextTests(Base):
    def page(self, now):
        c = Client()
        c.force_login(self.u2)
        with mock.patch("django.utils.timezone.now", return_value=now):
            return c.get(f"/s/{self.a.token}/c/").content.decode()

    def test_next_bundle_label(self):
        self.assertIn("다음 알림 <b class=\"num\">08:20</b>", self.page(at(4, 7, 0)))
        self.assertIn("다음 알림 <b class=\"num\">12:20</b>", self.page(at(4, 8, 20)))
        self.assertIn("다음 알림 <b class=\"num\">14:40</b>", self.page(at(4, 13, 0)))
        self.assertIn("다음 알림 <b class=\"num\">08:20</b>", self.page(at(4, 15, 0)))

    def test_bell_has_push_toggle(self):
        c = Client()
        c.force_login(self.u2)
        html = c.get(f"/s/{self.a.token}/bell").content.decode()
        self.assertIn('data-vapid="pub"', html)
        self.assertIn("이 PC에서 알림 받기", html)


@override_settings(**VAPID)
class HardeningTests(SubscribeViewTests):
    """G2 검증·보안 후속: 가짜 키, 발송 예외 격리, 공용 PC 로그아웃, 끊긴 회원, 옮김 기록."""

    def test_point_not_on_curve_rejected(self):
        c = self.client_for(self.u2)
        fake = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "B" + "x" * 86, "auth": KEYS["auth"]}}
        self.assertEqual(self.post(c, f"/s/{self.a.token}/push", fake).status_code, 400)
        short_auth = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": KEYS["p256dh"], "auth": "a" * 20}}
        self.assertEqual(self.post(c, f"/s/{self.a.token}/push", short_auth).status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_one_bad_subscription_does_not_stop_others(self):
        bad = self.sub(self.owner)
        good = self.sub(self.m2)
        self.note(self.owner, self.r1)
        self.note(self.m2, self.r2)

        def push(subscription_info, **kw):
            if subscription_info["endpoint"] == bad.endpoint:
                raise ValueError("Invalid EC key.")
        with self.assertLogs("collect.push", "WARNING") as logs:
            wp = self.run_at(at(4, 8, 20), resp=push)
        self.assertEqual(wp.call_count, 2)
        self.assertEqual(list(PushSubscription.objects.values_list("pk", flat=True)), [good.pk])
        self.assertNotIn(bad.endpoint, "".join(logs.output))

    def test_network_error_keeps_subscription(self):
        import requests
        s = self.sub(self.m2)
        self.note(self.m2, self.r2)
        self.run_at(at(4, 8, 20), resp=requests.ConnectionError("down"))
        self.assertTrue(PushSubscription.objects.filter(pk=s.pk).exists())

    def test_logout_removes_this_device_subscription(self):
        from accounts.models import DEVICE_COOKIE, DeviceToken
        c = self.client_for(self.u2)
        c.cookies[DEVICE_COOKIE] = DeviceToken.issue(self.u2)
        self.post(c, f"/s/{self.a.token}/push", self.body())
        other = self.sub(self.m2, 9)  # 다른 PC 구독은 남는다
        c.post("/auth/logout", HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
        self.assertEqual(list(PushSubscription.objects.values_list("pk", flat=True)), [other.pk])

    def test_mine_check(self):
        mine, theirs = self.sub(self.m2), self.sub(self.owner)
        c = self.client_for(self.u2)
        r = self.post(c, f"/s/{self.a.token}/push/mine", {"endpoint": mine.endpoint})
        self.assertEqual((r.status_code, r.json()), (200, {"mine": True}))
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(self.post(c, f"/s/{self.a.token}/push/mine", {"endpoint": theirs.endpoint}).json(), {"mine": False})
        self.assertEqual(self.post(c, f"/s/{self.b.token}/push/mine", {"endpoint": mine.endpoint}).status_code, 404)

    def test_member_without_user_not_sent(self):
        self.sub(self.m2)
        self.note(self.m2, self.r2)
        Member.objects.filter(pk=self.m2.pk).update(user=None)
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 0)

    def test_inactive_user_not_sent(self):
        self.sub(self.m2)
        self.note(self.m2, self.r2)
        get_user_model().objects.filter(pk=self.u2.pk).update(is_active=False)
        self.assertEqual(self.run_at(at(4, 8, 20)).call_count, 0)

    def test_move_is_logged_without_endpoint(self):
        old = self.sub(self.owner)
        with self.assertLogs("collect.push", "INFO") as logs:
            self.post(self.client_for(self.u2), f"/s/{self.a.token}/push", self.body(old.endpoint))
        self.assertIn("옮김", "".join(logs.output))
        self.assertNotIn(old.endpoint, "".join(logs.output))

    def test_logout_form_has_push_off(self):
        html = self.client_for(self.u2).get(f"/s/{self.a.token}/c/").content.decode()
        self.assertIn(f'data-push-off="/s/{self.a.token}/push/off"', html)
