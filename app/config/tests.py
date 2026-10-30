from unittest import mock

from django.test import SimpleTestCase, override_settings

from config import queue


class QueueTests(SimpleTestCase):
    @override_settings(REDIS_URL="")
    def test_no_redis_runs_inline(self):
        func = mock.Mock()
        self.assertIsNone(queue.enqueue(func, 1, a=2))
        func.assert_called_once_with(1, a=2)

    @override_settings(REDIS_URL="redis://example.invalid:6379/0")
    def test_with_redis_uses_queue_with_timeout(self):
        q = queue.get_queue()  # 연결은 명령을 보낼 때 맺으므로 여기선 접속하지 않는다
        self.assertEqual(q.name, "default")
        self.assertEqual(q._default_timeout, 300)
        with mock.patch.object(queue, "get_queue") as get_queue:
            queue.enqueue(queue.ping)
        get_queue.return_value.enqueue.assert_called_once_with(queue.ping)

    def test_ping(self):
        self.assertEqual(queue.ping(), "pong")


class HealthzTests(SimpleTestCase):
    def test_ok_and_drain(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        with mock.patch("config.urls.os.path.exists", return_value=True):
            self.assertEqual(self.client.get("/healthz").status_code, 503)  # 교체 중 Traefik이 빼도록


class SafeErrorLogTests(SimpleTestCase):
    def test_500_log_hides_values_and_tokens(self):
        import io
        import logging

        from config.logsafe import SafeErrorFormatter

        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(SafeErrorFormatter())
        log = logging.getLogger("django.request")
        log.addHandler(h)
        try:
            try:
                raise ValueError("Key (email)=(kim@school.kr) already exists")
            except ValueError:
                log.error("%s: %s", "Internal Server Error", "/s/AbCdTok123/c/xyz", exc_info=True)
        finally:
            log.removeHandler(h)
        out = buf.getvalue()
        self.assertIn("Internal Server Error: /s/***/c/***", out)
        self.assertIn("builtins.ValueError (메시지 생략)", out)
        self.assertIn("test_500_log_hides_values_and_tokens", out)
        self.assertNotIn("kim@school.kr", out)
        self.assertNotIn("AbCdTok123", out)

    @override_settings(DEBUG=False)
    def test_unhandled_view_error_is_logged(self):
        from unittest import mock

        with mock.patch("config.urls.os.path.exists", side_effect=RuntimeError("boom-secret")), \
                self.assertLogs("django.request", "ERROR") as cm:
            self.client.raise_request_exception = False
            self.assertEqual(self.client.get("/healthz").status_code, 500)
        self.assertIn("Internal Server Error: /healthz", cm.output[0])
