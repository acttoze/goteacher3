"""V10 QA 경계 사례(구현 테스트 tests.py에 없는 것만).

- 위조 입장권 기록은 IP당 1시간 20건까지, 응답은 넘어도 그대로(로그인 화면으로).
- 다른 계정 확인 화면 → 확인(POST)하면 switch_shown 다음 성공 한 건.
- 업체·TV·폰·정적 경로, JSON 응답, 오류 응답은 세지 않는다.
- 취합 보기처럼 key가 든 주소는 모양만 남는다.
- /analytics: 멈춘 운영자 404, 기간 버튼 표시, 회원 이름 없음.
- purge: 보관 기간 안의 기록은 남고, 미리 보기(dry-run)는 안 지운다.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.http import HttpResponse, JsonResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve
from django.utils import timezone

from accounts.test_legacy_sso import KEY, MB_NO, REDIS_STANDIN, ticket
from collect.models import Collection
from hwpfiles.retention import purge
from legacy.models import LegacyMap

from . import middleware
from .models import DailyUse, Entry
from .tests import school, seat

User = get_user_model()
FORGED = "other-key-0123456789abcdef0123456789"


@REDIS_STANDIN
@override_settings(LEGACY_SSO_KEY=KEY)
class EntryEdgeTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.space = school("새솔초", "1234567")
        self.m = seat(self.space, "김옛날", username="OldKim")
        LegacyMap.objects.create(kind="user", legacy_id=str(MB_NO), obj_id=self.m.user.pk)

    def test_forged_log_capped_20_per_ip_and_response_unchanged(self):
        for _ in range(25):
            r = self.client.get("/auth/legacy", {"t": ticket(key=FORGED)}, REMOTE_ADDR="10.0.0.1")
            self.assertRedirects(r, "/start", fetch_redirect_response=False)
        self.assertEqual(Entry.objects.filter(reason="bad_ticket").count(), 20)
        self.client.get("/auth/legacy", {"t": ticket(key=FORGED)}, REMOTE_ADDR="10.0.0.2")  # 다른 IP는 따로 센다
        self.assertEqual(Entry.objects.filter(reason="bad_ticket").count(), 21)

    def test_expired_and_garbage_tickets_are_bad_ticket(self):
        self.client.get("/auth/legacy", {"t": ticket(exp=1)})
        self.client.get("/auth/legacy", {"t": "not-a-ticket"})
        self.client.get("/auth/legacy")
        self.assertEqual(list(Entry.objects.values_list("ok", "reason", "user")), [(False, "bad_ticket", None)] * 3)

    def test_switch_then_confirm_records_both(self):
        c = Client()
        c.force_login(User.objects.create(username="google:other"))
        t = ticket()
        self.assertEqual(c.get("/auth/legacy", {"t": t}).status_code, 200)
        r = c.post("/auth/legacy", {"t": t})
        self.assertRedirects(r, f"/s/{self.space.token}/today", fetch_redirect_response=False)
        self.assertEqual(list(Entry.objects.order_by("pk").values_list("ok", "reason")), [(False, "switch_shown"), (True, "")])

    def test_success_without_school_space(self):
        self.m.delete()
        r = self.client.get("/auth/legacy", {"t": ticket()})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        e = Entry.objects.get()
        self.assertEqual((e.ok, e.space), (True, None))


class CountsFilterTests(TestCase):
    """미들웨어의 '세는가' 판정을 요청·응답 모양별로 직접 본다(로그인한 계정 회원 기준)."""

    def setUp(self):
        self.space = school("새솔초", "1234567")
        self.m = seat(self.space, "사일")

    def counts(self, path, response=None, method="get", **headers):
        req = getattr(RequestFactory(), method)(path, headers=headers)
        req.user = self.m.user
        try:
            req.resolver_match = resolve(path.split("?")[0])
        except Exception:
            req.resolver_match = None
        return middleware._counts(req, response or HttpResponse("<p>x</p>", content_type="text/html; charset=utf-8"))

    def test_base_case_counts(self):
        self.assertTrue(self.counts(f"/s/{self.space.token}/today"))

    def test_skipped_paths(self):
        for path in ("/v/", "/v/abc", "/tv/abc", "/m/abc", "/ops/feedback", "/analytics", "/healthz", "/static/x.css"):
            self.assertFalse(self.counts(path), path)

    def test_non_html_error_post_htmx(self):
        today = f"/s/{self.space.token}/today"
        self.assertFalse(self.counts(today, JsonResponse({"a": 1})))
        self.assertFalse(self.counts(today, HttpResponse("x", status=500, content_type="text/html")))
        self.assertFalse(self.counts(today, HttpResponse("x", status=302, content_type="text/html")))
        self.assertFalse(self.counts(today, method="post"))
        self.assertFalse(self.counts(today, HX_Request="true"))


class KeyRouteTests(TestCase):
    def test_collection_key_not_stored(self):
        space = school("새솔초", "1234567")
        m = seat(space, "사일")
        col = Collection.objects.create(space=space, created_by=m, title="연수 희망", source="table")
        test = self

        class C(Client):
            def request(self, **req):
                with test.captureOnCommitCallbacks(execute=True):
                    return super().request(**req)
        c = C()
        c.force_login(m.user)
        r = c.get(f"/s/{space.token}/c/{col.key}/?sort=name")
        self.assertEqual(r.status_code, 200)
        u = DailyUse.objects.get()
        self.assertEqual((u.route, u.space, u.n), ("/s/<공간>/c/<key>/", space, 1))
        for v in (col.key, col.link_token, space.token, "sort", "name"):
            self.assertNotIn(v, u.route)


class PageEdgeTests(TestCase):
    def setUp(self):
        self.a = school("가람초", "A1")
        self.names = ["홍길동", "김철수", "이영희"]
        self.members = [seat(self.a, n, "g1") for n in self.names]
        for i, m in enumerate(self.members):
            DailyUse.objects.create(day=timezone.localdate() - timedelta(days=i), user=m.user, space=self.a, route="/s/<공간>/today", n=1)
            Entry.objects.create(user=m.user, space=self.a, ok=True)
        self.staff = User.objects.create(username="google:ops", first_name="운영", is_staff=True)

    def get(self, user, **q):
        c = Client()
        c.force_login(user)
        return c.get("/analytics", q)

    def test_inactive_staff_404_and_post_rejected(self):
        off = User.objects.create(username="google:off", is_staff=True, is_active=False)
        c = Client()
        c.force_login(off)
        self.assertEqual(c.get("/analytics").status_code, 404)
        c.force_login(self.staff)
        self.assertEqual(c.post("/analytics").status_code, 405)

    def test_periods_and_no_names(self):
        for d, label in (("1", "오늘"), ("7", "7일"), ("30", "30일"), ("all", "전체")):
            r = self.get(self.staff, d=d)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, f'value="{d}" aria-pressed="true">{label}<', html=False)
            for n in self.names:
                self.assertNotContains(r, n)
        self.assertEqual(self.get(self.staff, d="1").context["tiles"][2][1], 1)
        self.assertEqual(self.get(self.staff, d="7").context["tiles"][2][1], 3)
        self.assertContains(self.get(self.staff, d="x"), 'value="7" aria-pressed="true"')  # 모르는 값은 7일

    def test_no_old_brand(self):
        self.assertNotContains(self.get(self.staff), "티로드")


class PurgeEdgeTests(TestCase):
    def setUp(self):
        self.m = seat(school("가람초", "A1"), "가1")
        self.now = timezone.now()

    def test_inside_keep_window_stays(self):
        e = Entry.objects.create(user=self.m.user, ok=True)
        Entry.objects.filter(pk=e.pk).update(at=self.now - timedelta(days=364))
        DailyUse.objects.create(day=timezone.localdate() - timedelta(days=364), user=self.m.user, route="/help", n=1)
        self.assertEqual(purge(self.now)["analytics"], 0)
        self.assertEqual((Entry.objects.count(), DailyUse.objects.count()), (1, 1))

    def test_dry_run_keeps(self):
        e = Entry.objects.create(ok=False, reason="bad_ticket")
        Entry.objects.filter(pk=e.pk).update(at=self.now - timedelta(days=400))
        self.assertEqual(purge(self.now, dry_run=True)["analytics"], 1)
        self.assertEqual(Entry.objects.count(), 1)

    @override_settings(ANALYTICS_KEEP_DAYS=30)
    def test_short_keep(self):
        DailyUse.objects.create(day=timezone.localdate() - timedelta(days=31), user=self.m.user, route="/help", n=1)
        self.assertEqual(purge(self.now)["analytics"], 1)
        self.assertFalse(DailyUse.objects.exists())

    def test_purge_command_prints_analytics(self):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        with mock.patch("hwpfiles.retention.timezone.now", return_value=self.now):
            call_command("purge", "--dry-run", stdout=out)
        self.assertIn("지난 이용 기록 0건", out.getvalue())
