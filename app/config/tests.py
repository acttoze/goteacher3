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
