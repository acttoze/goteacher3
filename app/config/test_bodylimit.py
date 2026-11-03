"""요청 본문 전역 상한: CSRF 미들웨어가 본문을 풀기 전에 선언 크기로 413."""
import io
import logging
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase

from .logsafe import SafeErrorFormatter


class BodyLimitTests(TestCase):
    def post(self, length):
        with mock.patch("django.http.multipartparser.MultiPartParser.parse") as parse:
            r = Client(enforce_csrf_checks=True).post(
                "/join", data=b"x", content_type="multipart/form-data; boundary=b", CONTENT_LENGTH=length)
        return r, parse

    def test_anonymous_over_limit_413_without_reading(self):
        r, parse = self.post(str(settings.MAX_REQUEST_BYTES + 1))
        self.assertEqual(r.status_code, 413)
        parse.assert_not_called()
        self.assertIn("Content-Security-Policy", r)
        self.assertContains(r, "너무 커서", status_code=413)

    def test_at_limit_and_bad_values_pass_through(self):
        for length in (str(settings.MAX_REQUEST_BYTES), "abc", ""):
            r, _ = self.post(length)
            self.assertNotEqual(r.status_code, 413, length)

    def test_first_middleware(self):
        self.assertEqual(settings.MIDDLEWARE[0], "config.bodylimit.BodyLimitMiddleware")


class LogMaskTests(SimpleTestCase):
    def test_notice_tokens_masked(self):
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(SafeErrorFormatter())
        log = logging.getLogger("i2.mask")
        log.addHandler(h)
        log.error("Internal Server Error: /n/KEYONE")
        log.error("Internal Server Error: /s/TOK/board/KEYTWO/seen")
        log.removeHandler(h)
        out = buf.getvalue()
        for secret in ("KEYONE", "KEYTWO", "TOK"):
            self.assertNotIn(secret, out)
