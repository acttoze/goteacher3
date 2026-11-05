"""보안 검토 반영: 내린 글 본인 지우기·90일 삭제, 운영자 delete·block, 전화·주민번호 거부, 차단, 문서."""
from datetime import timedelta
from io import StringIO

from django.core.management import CommandError, call_command
from django.utils import timezone

from accounts.models import new_guest
from hwpfiles import retention

from .models import Comment, CommunityProfile, Post, Report
from .tests import Base, _google, _nick, _seat


def run_cmd(*args):
    out = StringIO()
    call_command("community", *args, stdout=out)
    return out.getvalue()


class HiddenOwnDeleteTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Post.objects.create(author=self.a, title="내린 제목", body="본문")
        self.c = Comment.objects.create(post=self.p, author=self.b, body="내린 글의 댓글")

    def test_author_deletes_hidden_post(self):
        run_cmd("hide", self.p.key)
        Report.objects.create(kind="post", key=self.p.key, reporter=self.b)
        Report.objects.create(kind="comment", key=self.c.key, reporter=self.a)
        page = self.client.get("/community")
        self.assertContains(page, "운영자가 내린 내 글·댓글")
        self.assertContains(page, f'action="/community/{self.p.key}/delete"')
        self.assertEqual(self.as_b().post(f"/community/{self.p.key}/delete").status_code, 404)
        self.assertEqual(self.client.post(f"/community/{self.p.key}/delete").status_code, 303)
        self.assertFalse(Post.objects.exists())
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(Report.objects.exists())

    def test_author_deletes_hidden_comment(self):
        run_cmd("hide", self.c.key)
        Report.objects.create(kind="comment", key=self.c.key, reporter=self.a)
        b = self.as_b()
        self.assertContains(b.get("/community"), f'action="/community/c/{self.c.key}/delete"')
        self.assertEqual(self.client.post(f"/community/c/{self.c.key}/delete").status_code, 404)
        r = b.post(f"/community/c/{self.c.key}/delete")
        self.assertRedirects(r, f"/community/{self.p.key}", status_code=303, fetch_redirect_response=False)
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(Report.objects.exists())
        self.assertTrue(Post.objects.exists())

    def test_comment_on_hidden_post_is_reachable_and_deletable(self):
        run_cmd("hide", self.p.key)
        b = self.as_b()
        self.assertContains(b.get("/community"), f'action="/community/c/{self.c.key}/delete"')
        self.assertRedirects(b.post(f"/community/c/{self.c.key}/delete"), "/community", status_code=303, fetch_redirect_response=False)
        self.assertFalse(Comment.objects.exists())

    def test_nothing_hidden_no_section(self):
        self.assertNotContains(self.client.get("/community"), "운영자가 내린 내")


class CommandTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Post.objects.create(author=self.a, title="제목", body="본문")
        self.c = Comment.objects.create(post=self.p, author=self.b, body="댓글")

    def test_delete_comment(self):
        Report.objects.create(kind="comment", key=self.c.key, reporter=self.a)
        self.assertIn("지웠습니다", run_cmd("delete", self.c.key))
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(Report.objects.exists())
        self.assertTrue(Post.objects.exists())

    def test_delete_post_with_comments_and_reports(self):
        Report.objects.create(kind="post", key=self.p.key, reporter=self.b)
        Report.objects.create(kind="comment", key=self.c.key, reporter=self.a)
        run_cmd("delete", self.p.key)
        self.assertEqual((Post.objects.count(), Comment.objects.count(), Report.objects.count()), (0, 0, 0))

    def test_unknown_values_error(self):
        for args in (("delete", "없는값"), ("block", "없는닉"), ("unblock", "없는닉")):
            with self.assertRaises(CommandError):
                run_cmd(*args)


class HiddenRetentionTests(Base):
    def test_hidden_over_90_days_deleted(self):
        now = timezone.now()
        old = Post.objects.create(author=self.a, title="오래 내린 글", body="b", hidden_at=now - timedelta(days=91))
        under = Comment.objects.create(post=old, author=self.b, body="밑 댓글")
        recent = Post.objects.create(author=self.a, title="막 내린 글", body="b", hidden_at=now - timedelta(days=89))
        live = Post.objects.create(author=self.a, title="그대로", body="b")
        Post.objects.filter(pk=live.pk).update(created_at=now - timedelta(days=4000))
        old_c = Comment.objects.create(post=live, author=self.b, body="오래 내린 댓글", hidden_at=now - timedelta(days=91))
        keep_c = Comment.objects.create(post=live, author=self.b, body="남는 댓글")
        Report.objects.create(kind="post", key=old.key, reporter=self.b)
        Report.objects.create(kind="comment", key=under.key, reporter=self.a)
        Report.objects.create(kind="comment", key=old_c.key, reporter=self.a)
        keep_r = Report.objects.create(kind="comment", key=keep_c.key, reporter=self.a)
        self.assertEqual(retention.purge(now, dry_run=True)["community_hidden"], 2)
        self.assertTrue(Post.objects.filter(pk=old.pk).exists())
        self.assertEqual(retention.purge(now)["community_hidden"], 2)
        self.assertEqual(set(Post.objects.values_list("pk", flat=True)), {recent.pk, live.pk})
        self.assertEqual(list(Comment.objects.values_list("pk", flat=True)), [keep_c.pk])
        self.assertEqual(list(Report.objects.values_list("pk", flat=True)), [keep_r.pk])


class PrivateNumberTests(Base):
    BAD = ("연락 주세요 010-1234-5678", "01012345678", "02) 123 4567", "１５８８－１２３４", "010\u200b-1234-5678", "주민번호 900101-1234567", "900101 - 1234567", "９００１０１－１２３４５６７")
    OK = ("2026-10-29 회의", "100000원 입금", "교실 301호, 4-2반", "123456-12345", "20260101-1234")

    def test_post_refused_keeping_input(self):
        for bad in self.BAD:
            r = self.client.post("/community/new", {"title": "멀쩡한 제목", "body": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertContains(r, 'id="postDlg">', status_code=400)
            self.assertContains(r, "전화번호나 주민등록번호로 보이는", status_code=400)
            self.assertContains(r, 'value="멀쩡한 제목"', status_code=400)
            r = self.client.post("/community/new", {"title": bad, "body": "멀쩡한 본문"})
            self.assertEqual(r.status_code, 400, bad)
            self.assertContains(r, "멀쩡한 본문", status_code=400)
        self.assertFalse(Post.objects.exists())

    def test_comment_refused_keeping_input(self):
        p = Post.objects.create(author=self.a, title="t", body="b")
        for bad in self.BAD:
            r = self.client.post(f"/community/{p.key}/comment", {"body": bad + " 남는글"})
            self.assertEqual(r.status_code, 400, bad)
            self.assertContains(r, "남는글", status_code=400)
        self.assertFalse(Comment.objects.exists())

    def test_ordinary_numbers_pass(self):
        for i, good in enumerate(self.OK):
            self.assertEqual(self.client.post("/community/new", {"title": f"제목{i}", "body": good}).status_code, 303, good)


class BlockTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Post.objects.create(author=self.b, title="읽을 제목", body="읽을 본문")
        self.c = Comment.objects.create(post=self.p, author=self.b, body="읽을 댓글")
        self.mine = Post.objects.create(author=self.a, title="내 글", body="내 본문")
        self.assertIn("차단했습니다", run_cmd("block", "봄바람"))

    def test_reads_but_cannot_write(self):
        for path in ("/community", f"/community/{self.p.key}"):
            r = self.client.get(path)
            self.assertContains(r, "글쓰기를 막았어요")
        self.assertContains(self.client.get("/community"), "읽을 제목")
        detail = self.client.get(f"/community/{self.p.key}")
        self.assertContains(detail, "읽을 본문")
        self.assertNotContains(detail, f'action="/community/{self.p.key}/comment"')
        self.assertNotContains(detail, f'action="/community/{self.p.key}/report"')
        self.assertNotContains(self.client.get("/community"), 'id="postDlg"')
        self.assertNotContains(self.client.get("/community"), 'href="/community/nick"')
        for path, data in (("/community/new", {"title": "x", "body": "y"}), (f"/community/{self.p.key}/comment", {"body": "y"}),
                           (f"/community/{self.p.key}/report", {}), (f"/community/c/{self.c.key}/report", {})):
            r = self.client.post(path, data)
            self.assertContains(r, "글쓰기를 막았어요", status_code=403)
        self.assertEqual((Post.objects.count(), Comment.objects.count(), Report.objects.count()), (2, 1, 0))

    def test_nickname_locked(self):
        CommunityProfile.objects.filter(user=self.a).update(nickname_changed_at=timezone.now() - timedelta(days=3))
        self.assertNotContains(self.client.get("/community/nick"), 'name="nickname"')
        self.assertEqual(self.client.post("/community/nick", {"nickname": "새닉네임"}).status_code, 403)
        self.assertEqual(CommunityProfile.objects.get(user=self.a).nickname, "봄바람")

    def test_can_still_delete_own(self):
        self.assertEqual(self.client.post(f"/community/{self.mine.key}/delete").status_code, 303)
        self.assertFalse(Post.objects.filter(pk=self.mine.pk).exists())

    def test_unblock_restores(self):
        self.assertIn("풀었습니다", run_cmd("unblock", "봄 바람"))  # 닉네임 겹침 값으로 찾는다
        self.assertEqual(self.client.post("/community/new", {"title": "x", "body": "y"}).status_code, 303)

    def test_others_unaffected(self):
        self.assertEqual(self.as_b().post("/community/new", {"title": "x", "body": "y"}).status_code, 303)

    def test_blocked_guide_names_no_one(self):
        r = self.client.post("/community/new", {"title": "x", "body": "y"})
        self.assertNotContains(r, "kim.real@example.com", status_code=403)


class GuestStillGuestTests(Base):
    def test_guest_with_seat_still_guest_guide(self):
        g = new_guest("손님")
        _seat(g, "다솜초등학교", "손님", "3333333")
        c = self.client_class()
        c.force_login(g)
        self.assertContains(c.get("/community"), "구글로 이으면", status_code=403)

    def test_seated_member_passes_nick_step(self):
        u = _google("n", "n@example.com")
        _seat(u, "다솜초등학교", "새회원", "3333333")
        _nick(u, "새회원닉")
        c = self.client_class()
        c.force_login(u)
        self.assertEqual(c.get("/community").status_code, 200)


class LegalWordingTests(Base):
    def test_privacy_and_terms(self):
        privacy = self.client.get("/privacy").content.decode()
        terms = self.client.get("/terms").content.decode()
        self.assertIn("다른 회원에게는 닉네임만 보이고 이름·메일·학교는 보이지 않습니다", privacy)
        self.assertNotIn("커뮤니티 화면에 나오지 않습니다", privacy)
        self.assertIn("gt_sched_mode", privacy)
        for word in ("내린 날부터 90일", "차단"):
            self.assertIn(word, privacy)
            self.assertIn(word, terms)
