"""Q10 QA가 더한 경계: 오류 응답의 표시, NUL 문자, 같은 닉네임 동시 신청(PostgreSQL)."""
import threading

from django.core.cache import caches
from django.db import connection
from django.test import Client, TransactionTestCase, skipUnlessDBFeature

from .models import Comment, CommunityProfile, Post
from .tests import Base, _google, _seat


class ErrorResponseTests(Base):
    """정상 화면 말고 400·403 응답도 같은 틀을 그리므로 쓴 사람 정보가 새지 않아야 한다."""

    SECRETS = ("김진짜", "kim.real@example.com", "가람초등학교", "google:a")

    def test_error_screens_show_no_author_identity(self):
        post = Post.objects.create(author=self.a, title="제목", body="본문")
        Comment.objects.create(post=post, author=self.a, body="댓글")
        b = self.as_b()
        for res in (b.post(f"/community/{post.key}/comment", {"body": ""}),
                    b.post("/community/new", {"title": "", "body": ""}),
                    b.post("/community/nick", {"nickname": "봄바람"})):
            self.assertEqual(res.status_code, 400)
            html = res.content.decode()
            for word in self.SECRETS:
                self.assertNotIn(word, html)

    def test_nul_characters_do_not_break_saving(self):
        """PostgreSQL은 NUL을 저장하지 못한다. 500이 아니라 글자를 지우고 저장해야 한다."""
        post = Post.objects.create(author=self.a, title="제목", body="본문")
        self.assertEqual(self.client.post("/community/new", {"title": "널\x00제목", "body": "널\x00본문"}).status_code, 303)
        self.assertEqual(self.client.post(f"/community/{post.key}/comment", {"body": "널\x00댓글"}).status_code, 303)
        self.assertTrue(Post.objects.filter(title="널제목", body="널본문").exists())
        self.assertTrue(Comment.objects.filter(body="널댓글").exists())


@skipUnlessDBFeature("has_select_for_update")
class NicknameRaceTests(TransactionTestCase):
    def test_same_nickname_at_once_one_wins_no_500(self):
        caches["ratelimit"].clear()
        users = [_google(f"r{i}", f"r{i}@example.com") for i in range(4)]
        for i, u in enumerate(users):
            _seat(u, f"학교{i}", f"이름{i}", f"999000{i}")
        codes, barrier = [], threading.Barrier(len(users))

        def claim(user):
            try:
                c = Client(raise_request_exception=False)
                c.force_login(user)
                barrier.wait(5)
                codes.append(c.post("/community/nick", {"nickname": "같은닉"}).status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=claim, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(codes), [303, 400, 400, 400])
        self.assertEqual(CommunityProfile.objects.filter(nickname="같은닉").count(), 1)
