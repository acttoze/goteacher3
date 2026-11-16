"""V1 입장권 로그인 QA 보강: 구 사이트(PHP troad_preview_ticket)가 실제로 만든 입장권과의 형식 일치, 캐시 장애, 임시 비밀번호 회원.

PHP_TICKET은 ~/moon/dev/goteacher/lib/troad_preview.lib.php(구 저장소 b2f948f)의
troad_preview_ticket(['mb_id' => 'OldKim', 'mb_no' => 4321], 1800000000)을 키 PHP_KEY로 실제 php에서 돌린 결과다
(exp = 1800000060, 난수는 그때 값).
"""
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase, override_settings

from legacy.models import LegacyMap
from spaces.models import Member, School, Space

from .models import MUST_CHANGE, PasswordChangeRequired

User = get_user_model()
PHP_KEY = "qa-key-가나다-0123456789abcdefghijklmnop"  # 32바이트 이상
PHP_MB_NO = "4321"
PHP_NOW = 1800000000
PHP_TICKET = ("eyJ1IjoiT2xkS2ltIiwibSI6NDMyMSwiZXhwIjoxODAwMDAwMDYwLCJuIjoiYTlhYjY2NDMzMjM4OGY1MGY3OWEzMWFiMTcwMTZkNmIifQ"
              ".bMZcuL6CV_Dv6tLtit97QfS7BQuX7U-xMBCuuHuRSFA")


def at(ts):
    """views 안의 time.time()만 바꾼다."""
    return mock.patch("accounts.views.time.time", return_value=ts)


# 다시 쓰기 막기는 Redis일 때만 켜진다. 테스트는 프로세스 메모리 캐시를 Redis 자리에 둔다.
REDIS_STANDIN = mock.patch("accounts.views._replay_cache", new=lambda: caches["ratelimit"])


@REDIS_STANDIN
@override_settings(LEGACY_SSO_KEY=PHP_KEY)
class LegacySsoPhpCompatTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.user = User.objects.create(username="oldkim")  # 구 아이디와 대소문자가 다르게 저장된 경우
        school = School.objects.create(neis_atpt="E10", neis_code="7654321", name="한빛초")
        self.space = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name="한빛초")
        Member.objects.create(space=self.space, user=self.user, name="김옛날")
        LegacyMap.objects.create(kind="user", legacy_id=PHP_MB_NO, obj_id=self.user.pk)

    def go(self):
        return self.client.get("/auth/legacy", {"t": PHP_TICKET})

    def test_php_ticket_accepted_within_60s(self):
        with at(PHP_NOW + 59):
            r = self.go()
        self.assertRedirects(r, f"/s/{self.space.token}/today", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_php_ticket_refused_after_exp(self):
        with at(PHP_NOW + 60):
            r = self.go()
        self.assertRedirects(r, "/start", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(LEGACY_SSO_KEY="other-key-0123456789abcdefghijklmnop")
    def test_php_ticket_refused_with_other_key(self):
        with at(PHP_NOW + 1):
            r = self.go()
        self.assertRedirects(r, "/start", fetch_redirect_response=False)


@REDIS_STANDIN
@override_settings(LEGACY_SSO_KEY=PHP_KEY)
class LegacySsoEdgeTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.user = User.objects.create(username="OldKim")
        school = School.objects.create(neis_atpt="E10", neis_code="7654322", name="두리초")
        self.space = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name="두리초")
        Member.objects.create(space=self.space, user=self.user, name="김옛날")
        LegacyMap.objects.create(kind="user", legacy_id=PHP_MB_NO, obj_id=self.user.pk)

    def test_cache_down_refuses(self):
        with at(PHP_NOW + 1), mock.patch.object(caches["ratelimit"], "add", side_effect=ConnectionError):
            r = self.client.get("/auth/legacy", {"t": PHP_TICKET})
        self.assertRedirects(r, "/start", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_temp_password_member_must_change_first(self):
        PasswordChangeRequired.objects.create(user=self.user)
        with at(PHP_NOW + 1):
            r = self.client.get("/auth/legacy", {"t": PHP_TICKET})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.client.session.get(MUST_CHANGE))
        landing = self.client.get(r["Location"])
        self.assertEqual(landing.status_code, 302)
        self.assertIn("/account/password", landing["Location"])

    def test_ticket_does_not_open_other_school(self):
        other = Space.objects.create(school=School.objects.create(neis_atpt="B10", neis_code="1111111", name="남초"),
                                     kind=Space.Kind.SCHOOL, grade=0, name="남초")
        with at(PHP_NOW + 1):
            self.client.get("/auth/legacy", {"t": PHP_TICKET})
        self.assertIn(self.client.get(f"/s/{other.token}/today").status_code, (403, 404))

    def test_map_to_deleted_account_refused(self):
        LegacyMap.objects.filter(kind="user").update(obj_id=999999)
        with at(PHP_NOW + 1):
            r = self.client.get("/auth/legacy", {"t": PHP_TICKET})
        self.assertRedirects(r, "/start", fetch_redirect_response=False)


def redis_caches(url):
    return {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "ratelimit": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": url,
                          "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 1}}}


@override_settings(LEGACY_SSO_KEY=PHP_KEY)
class LegacySsoRealRedisTests(TestCase):
    """대역 없이 실제 RedisCache 경로. 닿지 않는 Redis는 늘 돌리고, 살아 있는 Redis는 QA_REDIS_URL이 있을 때만."""

    def setUp(self):
        self.user = User.objects.create(username="OldKim")
        LegacyMap.objects.create(kind="user", legacy_id=PHP_MB_NO, obj_id=self.user.pk)

    def test_unreachable_redis_refuses_not_404(self):
        with override_settings(CACHES=redis_caches("redis://127.0.0.1:1/1")), at(PHP_NOW + 1):
            r = self.client.get("/auth/legacy", {"t": PHP_TICKET})
        self.assertRedirects(r, "/start", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_live_redis_once_only(self):
        url = os.environ.get("QA_REDIS_URL")
        if not url:
            self.skipTest("QA_REDIS_URL 없음")
        with override_settings(CACHES=redis_caches(url)), at(PHP_NOW + 1):
            first, second = Client(), Client()
            self.assertEqual(first.get("/auth/legacy", {"t": PHP_TICKET}).status_code, 302)
            self.assertIn("_auth_user_id", first.session)
            r = second.get("/auth/legacy", {"t": PHP_TICKET})
            self.assertRedirects(r, "/start", fetch_redirect_response=False)
            self.assertNotIn("_auth_user_id", second.session)
