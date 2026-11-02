"""G3 빈도 제한(config/ratelimit.py): 고정 창 카운터, Traefik 뒤 실제 IP.

불변식
- 한도까지는 통과, 넘으면 막힌다. 창이 바뀌면 다시 센다. 대상(scope·ident)마다 따로 센다.
- 키는 만료가 있고, 원문 IP·토큰을 담지 않는다(해시).
- 캐시가 죽으면 막지 않는다(교사 실사용 우선).
- IP는 X-Forwarded-For의 마지막 값(Traefik이 붙인 값). 앞쪽은 사용자가 마음대로 넣을 수 있다.
"""
from unittest import mock

from django.core.cache import caches
from django.test import RequestFactory, SimpleTestCase, override_settings

from . import ratelimit

RL_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "ratelimit": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "rl-test"},
}


@override_settings(CACHES=RL_CACHES)
class AllowTests(SimpleTestCase):
    def setUp(self):
        caches["ratelimit"].clear()

    def test_limit_then_block(self):
        self.assertEqual([ratelimit.allow("t", "x", 3, 60) for _ in range(5)], [True, True, True, False, False])

    def test_separate_targets(self):
        for _ in range(3):
            ratelimit.allow("t", "x", 3, 60)
        self.assertTrue(ratelimit.allow("t", "y", 3, 60))
        self.assertTrue(ratelimit.allow("u", "x", 3, 60))
        self.assertFalse(ratelimit.allow("t", "x", 3, 60))

    def test_new_window_resets(self):
        with mock.patch.object(ratelimit.time, "time", return_value=1000.0):
            for _ in range(3):
                ratelimit.allow("t", "x", 3, 60)
            self.assertFalse(ratelimit.allow("t", "x", 3, 60))
        with mock.patch.object(ratelimit.time, "time", return_value=1060.0):
            self.assertTrue(ratelimit.allow("t", "x", 3, 60))

    def test_key_expires_and_hides_ident(self):
        c = caches["ratelimit"]
        with mock.patch.object(c, "add", wraps=c.add) as add:
            ratelimit.allow("t", "203.0.113.9:secret-link", 3, 60)
        key, _, timeout = add.call_args.args
        self.assertTrue(0 < timeout <= 120)
        self.assertNotIn("203.0.113.9", key)
        self.assertNotIn("secret-link", key)

    def test_cache_down_allows(self):
        with mock.patch.object(caches["ratelimit"], "add", side_effect=ConnectionError):
            self.assertTrue(ratelimit.allow("t", "x", 0, 60))


class ClientIpTests(SimpleTestCase):
    rf = RequestFactory()

    def test_remote_addr_without_proxy(self):
        self.assertEqual(ratelimit.client_ip(self.rf.get("/", REMOTE_ADDR="198.51.100.7")), "198.51.100.7")

    def test_last_forwarded_hop(self):
        # 사용자가 앞에 가짜 값을 넣어도 Traefik이 마지막에 붙인 실제 주소를 쓴다
        r = self.rf.get("/", REMOTE_ADDR="172.18.0.5", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9")
        self.assertEqual(ratelimit.client_ip(r), "203.0.113.9")

    def test_garbage_forwarded_falls_back(self):
        r = self.rf.get("/", REMOTE_ADDR="172.18.0.5", HTTP_X_FORWARDED_FOR="nonsense")
        self.assertEqual(ratelimit.client_ip(r), "172.18.0.5")
