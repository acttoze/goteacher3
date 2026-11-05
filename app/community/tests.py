"""Q10 커뮤니티: 게이트·닉네임·표시(실명·메일·학교 없음)·지우기·내림·신고·빈도 제한·계정 삭제·운영 명령."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import new_guest
from config.test_ratelimit import RL_CACHES
from hwpfiles import retention
from spaces.models import Member, School, Space

from .models import Comment, CommunityProfile, Post, Report, nickname_key


def _google(sub, email, first=""):
    return get_user_model().objects.create(username=f"google:{sub}", email=email, first_name=first)


def _seat(user, school_name, name, code):
    school = School.objects.create(neis_atpt="S10", neis_code=code, name=school_name)
    space = Space.objects.create(school=school, grade=4, name=f"{school_name} 4학년")
    Member.objects.create(space=space, name=name, cls="4-1", user=user)
    return space


def _nick(user, nickname):
    return CommunityProfile.objects.create(user=user, nickname=nickname, nickname_key=nickname_key(nickname),
                                           nickname_changed_at=timezone.now())


class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.a = _google("a", "kim.real@example.com", "김진짜")
        self.b = _google("b", "lee.real@example.com", "이진짜")
        self.space_a = _seat(self.a, "가람초등학교", "김진짜", "1111111")
        self.space_b = _seat(self.b, "나래초등학교", "이진짜", "2222222")
        _nick(self.a, "봄바람")
        _nick(self.b, "가을비")
        self.client.force_login(self.a)

    def as_b(self):
        c = self.client_class()
        c.force_login(self.b)
        return c


class GateTests(Base):
    PATHS = ["/community", "/community/nick", "/community/new", "/community/zzz"]

    def test_anonymous_redirects_to_login(self):
        c = self.client_class()
        for path in self.PATHS:
            r = c.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertEqual(r["Location"], "/start?next=/community")

    def test_anonymous_post_refused(self):
        p = Post.objects.create(author=self.a, title="t", body="b")
        c = self.client_class()
        for path in ["/community/new", f"/community/{p.key}/comment", f"/community/{p.key}/delete", f"/community/{p.key}/report", "/community/nick"]:
            self.assertEqual(c.post(path, {"title": "x", "body": "y", "nickname": "닉네임"}).status_code, 302, path)
        self.assertEqual((Post.objects.count(), Comment.objects.count(), Report.objects.count()), (1, 0, 0))

    def test_guest_gets_guide_without_content(self):
        Post.objects.create(author=self.a, title="비밀 제목", body="비밀 본문")
        c = self.client_class()
        c.force_login(new_guest("손님"))
        for path in ("/community", "/community/new"):
            r = c.get(path)
            self.assertEqual(r.status_code, 403)
            self.assertContains(r, "구글로 이으면 쓸 수 있어요", status_code=403)
            self.assertContains(r, "/auth/google/login?next=/community", status_code=403)
            self.assertNotContains(r, "비밀 제목", status_code=403)
        self.assertEqual(c.post("/community/new", {"title": "x", "body": "y"}).status_code, 403)
        self.assertEqual(Post.objects.count(), 1)

    def test_member_without_nickname_gets_entry_on_every_path(self):
        u = _google("c", "c@example.com")
        _seat(u, "다솜초등학교", "박진짜", "3333333")
        p = Post.objects.create(author=self.a, title="비밀 제목", body="비밀 본문")
        c = self.client_class()
        c.force_login(u)
        for path in ("/community", f"/community/{p.key}", "/community/new", "/community/nick"):
            r = c.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "여기서는 이름·학교 대신 닉네임으로만 보여요")
            self.assertNotContains(r, "비밀 제목")
        c.post("/community/new", {"title": "x", "body": "y"})
        self.assertEqual(Post.objects.count(), 1)

    def test_noindex_and_no_store_everywhere(self):
        p = Post.objects.create(author=self.a, title="t", body="b")
        guest = self.client_class()
        guest.force_login(new_guest("손님"))
        for client, path in [(self.client, "/community"), (self.client, f"/community/{p.key}"), (self.client, "/community/nick"), (guest, "/community"), (self.client_class(), "/community")]:
            r = client.get(path)
            self.assertIn("noindex", r["X-Robots-Tag"], path)
            self.assertIn("nofollow", r["X-Robots-Tag"], path)
            self.assertIn("no-store", r["Cache-Control"], path)
        for path in ("/community", f"/community/{p.key}", "/community/nick"):
            self.assertContains(self.client.get(path), '<meta name="robots" content="noindex, nofollow">')

    def test_spaceless_google_member_gets_guide_without_content(self):
        p = Post.objects.create(author=self.a, title="비밀 제목", body="비밀 본문")
        for has_nick in (False, True):  # 공간을 모두 나간 뒤 닉네임만 남은 회원도 같다
            u = _google(f"c{has_nick}", f"c{has_nick}@example.com")
            if has_nick:
                _nick(u, "구름")
            c = self.client_class()
            c.force_login(u)
            for path in ("/community", f"/community/{p.key}", "/community/nick", "/community/new"):
                r = c.get(path)
                self.assertContains(r, "동학년이나 학교 공간에 들어오면 쓸 수 있어요", status_code=403)
                self.assertContains(r, 'href="/start"', status_code=403)
                self.assertContains(r, "고티처 회원만, 닉네임으로 이야기해요", status_code=403)  # 안내 띠는 그대로
                self.assertContains(r, 'class="out-nav"', status_code=403)
                self.assertNotContains(r, "비밀 제목", status_code=403)
                self.assertIn("noindex", r["X-Robots-Tag"])
            self.assertEqual(c.post("/community/new", {"title": "x", "body": "y"}).status_code, 403)
            self.assertEqual(c.post(f"/community/{p.key}/comment", {"body": "y"}).status_code, 403)
            self.assertEqual(c.post(f"/community/{p.key}/report").status_code, 403)
        self.assertEqual((Post.objects.count(), Comment.objects.count(), Report.objects.count()), (1, 0, 0))

    def test_shell_frame_marks_menu(self):
        r = self.client.get("/community")
        self.assertContains(r, 'class="side"')
        self.assertContains(r, 'href="/community" aria-current="page"')


class NicknameTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.u = _google("u", "u@example.com")
        _seat(self.u, "가람초등학교", "유진짜", "1111111")
        self.client.force_login(self.u)

    def post(self, value):
        return self.client.post("/community/nick", {"nickname": value})

    def test_enter(self):
        r = self.post("  새싹쌤 ")
        self.assertRedirects(r, "/community", status_code=303)
        self.assertEqual(self.u.community_profile.nickname, "새싹쌤")
        self.assertEqual(self.client.get("/community").status_code, 200)

    def test_length(self):
        for bad in ("가", " 가 ", "", "가" * 13, "ㅤㅤㅤ", "​​"):
            self.assertEqual(self.post(bad).status_code, 400, repr(bad))
        self.assertFalse(CommunityProfile.objects.exists())
        self.assertEqual(self.post("가" * 12).status_code, 303)

    def test_banned_words(self):
        for bad in ("관리자", "시설관리", "운영자님", "운영", "고티처팀", "고 티처", "운·영·자", "ＧＴ고티처", "나간 선생님", "Admin", "ad min", "ＡＤＭＩＮ", "GoTeacher1", "go teacher"):
            r = self.post(bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertFalse(CommunityProfile.objects.exists())

    def test_allowlist_only(self):
        """NFKC 뒤 완성형 한글·영문·숫자와 가운데 공백 한 칸만."""
        for bad in ("ㅋㅋㅋㅋ", "가ㅏ나", "\u1100\u1100", "漢字쌤", "새싹\u200b쌤", "새싹\u2060쌤", "새\ufeff싹", "새\u00ad싹", "새싹\u3164쌤", "새싹\u2800쌤",
                    "새싹_쌤", "새싹-쌤", "새싹!", "쌤😀쌤", "новый", "а1", "새 싹 쌤", "새  싹", "새\t싹", "새\n싹", "<b>쌤</b>"):
            caches["ratelimit"].clear()
            self.assertEqual(self.post(bad).status_code, 400, repr(bad))
        self.assertFalse(CommunityProfile.objects.exists())

    def test_allowed_shapes(self):
        for good, saved in (("새싹 쌤", "새싹 쌤"), ("Kim 3", "Kim 3"), ("ｋｉｍ１２", "kim12"), ("새\u3000싹", "새 싹")):
            CommunityProfile.objects.filter(user=self.u).delete()
            self.assertEqual(self.post(good).status_code, 303, repr(good))
            self.assertEqual(CommunityProfile.objects.get(user=self.u).nickname, saved)

    def test_duplicate_after_normalizing(self):
        _nick(_google("o", "o@example.com"), "Kim Teacher")
        for same in ("Kim Teacher", "kim teacher", "KIMTEACHER", "ｋｉｍ ｔｅａｃｈｅｒ"):
            r = self.post(same)
            self.assertEqual(r.status_code, 400, same)
            self.assertContains(r, "이미 쓰는 닉네임", status_code=400)
        self.assertFalse(CommunityProfile.objects.filter(user=self.u).exists())

    def test_db_enforces_unique(self):
        _nick(self.u, "하나")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _nick(_google("o", "o@example.com"), "하나")

    def test_change_once_a_day(self):
        self.post("처음닉")
        r = self.post("다음닉")
        self.assertContains(r, "하루에 한 번", status_code=400)
        self.assertEqual(self.client.get("/community/nick").status_code, 200)
        CommunityProfile.objects.update(nickname_changed_at=timezone.now() - timedelta(days=1, minutes=1))
        self.assertEqual(self.post("다음닉").status_code, 303)
        p = CommunityProfile.objects.get(user=self.u)
        self.assertEqual(p.nickname, "다음닉")
        self.assertEqual(self.post("또다른닉").status_code, 400)

    def test_renamed_nick_shows_on_old_posts(self):
        self.post("처음닉")
        post = Post.objects.create(author=self.u, title="t", body="b")
        CommunityProfile.objects.update(nickname_changed_at=timezone.now() - timedelta(days=2))
        self.post("다음닉")
        r = self.client.get(f"/community/{post.key}")
        self.assertContains(r, "다음닉")
        self.assertNotContains(r, "처음닉")

    @override_settings(CACHES=RL_CACHES)
    @mock.patch("config.ratelimit.time.time", return_value=1_800_000_030.0)
    def test_attempts_limited(self, _clock):
        caches["ratelimit"].clear()
        _nick(_google("o", "o@example.com"), "맞춰볼닉")
        for _ in range(20):
            self.assertEqual(self.post("맞춰볼닉").status_code, 400)
        self.assertEqual(self.post("맞춰볼닉").status_code, 429)


class ScreenTests(Base):
    def test_post_comment_flow(self):
        r = self.client.post("/community/new", {"title": "  첫 글\n제목 ", "body": "본문\n둘째 줄"})
        post = Post.objects.get()
        self.assertRedirects(r, f"/community/{post.key}", status_code=303)
        self.assertEqual((post.author, post.title), (self.a, "첫 글 제목"))
        self.assertContains(self.client.get("/community"), "첫 글 제목")
        r = self.client.post(f"/community/{post.key}/comment", {"body": "댓글이에요"})
        self.assertEqual(r.status_code, 303)
        r = self.as_b().get(f"/community/{post.key}")
        self.assertContains(r, "둘째 줄")
        self.assertContains(r, "댓글이에요")
        self.assertContains(self.client.get("/community"), "댓글 <span class=\"num\">1</span>")

    def test_new_get_opens_dialog(self):
        self.assertNotContains(self.client.get("/community"), 'id="postDlg">')
        self.assertContains(self.client.get("/community/new"), 'id="postDlg">')
        self.assertContains(self.client.get("/community"), 'id="postDlg" hidden')

    def test_errors_keep_input_in_open_dialog(self):
        r = self.client.post("/community/new", {"title": "남는 제목", "body": ""})
        self.assertContains(r, 'id="postDlg">', status_code=400)
        self.assertContains(r, 'value="남는 제목"', status_code=400)
        self.assertContains(r, "본문을 써 주세요", status_code=400)
        self.assertEqual(self.client.post("/community/new", {"title": "가" * 101, "body": "b"}).status_code, 400)
        self.assertEqual(self.client.post("/community/new", {"title": "t", "body": "가" * 5001}).status_code, 400)
        self.assertFalse(Post.objects.exists())
        self.assertEqual(self.client.post("/community/new", {"title": "가" * 100, "body": "가" * 5000}).status_code, 303)
        p = Post.objects.get()
        self.assertEqual(self.client.post(f"/community/{p.key}/comment", {"body": "가" * 501}).status_code, 400)
        self.assertEqual(self.client.post(f"/community/{p.key}/comment", {"body": "  "}).status_code, 400)
        self.assertFalse(Comment.objects.exists())

    def test_escapes_html(self):
        x = '"><script>alert(1)</script>'
        self.client.post("/community/new", {"title": x, "body": x})
        p = Post.objects.get()
        self.client.post(f"/community/{p.key}/comment", {"body": x})
        for path in ("/community", f"/community/{p.key}"):
            self.assertNotContains(self.client.get(path), "<script>alert(1)")

    def test_no_real_name_mail_or_school_of_author(self):
        """B가 보는 화면(공통 틀)에 A의 이름·메일·학교·공간이 없다."""
        post = Post.objects.create(author=self.a, title="제목", body="본문")
        Comment.objects.create(post=post, author=self.a, body="댓글")
        secrets = ["김진짜", "kim.real@example.com", "가람초등학교", self.space_a.token, "google:a"]
        b = self.as_b()
        for path in ("/community", f"/community/{post.key}"):
            html = b.get(path).content.decode()
            self.assertIn("봄바람", html)
            for word in secrets:
                self.assertNotIn(word, html, path)

    def test_my_school_name_only_in_my_own_frame(self):
        """내 틀의 내 이름은 나만 본다. 본문 칸에는 나와도 이름이 들어가지 않는다."""
        post = Post.objects.create(author=self.a, title="제목", body="본문")
        html = self.client.get(f"/community/{post.key}").content.decode()
        main = html.split('id="main"', 1)[1]
        for word in ("김진짜", "kim.real@example.com", "가람초등학교"):
            self.assertNotIn(word, main)

    def test_buttons_mine_and_others(self):
        post = Post.objects.create(author=self.a, title="제목", body="본문")
        Comment.objects.create(post=post, author=self.b, body="댓글")
        r = self.client.get(f"/community/{post.key}")
        self.assertContains(r, f'action="/community/{post.key}/delete"')
        self.assertNotContains(r, f'action="/community/{post.key}/report"')
        c = Comment.objects.get()
        self.assertContains(r, f'action="/community/c/{c.key}/report"')
        self.assertNotContains(r, f'action="/community/c/{c.key}/delete"')

    def test_pagination(self):
        for i in range(21):
            Post.objects.create(author=self.a, title=f"글{i:02}", body="b")
        r = self.client.get("/community")
        self.assertContains(r, "글20")
        self.assertNotContains(r, "글00")
        self.assertContains(self.client.get("/community", {"page": 2}), "글00")

    def test_get_on_post_only_urls_405(self):
        p = Post.objects.create(author=self.a, title="t", body="b")
        for path in (f"/community/{p.key}/comment", f"/community/{p.key}/delete", f"/community/{p.key}/report"):
            self.assertEqual(self.client.get(path).status_code, 405, path)
        self.assertEqual(self.client.post("/community").status_code, 405)


class DeleteTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Post.objects.create(author=self.a, title="제목", body="본문")
        self.c = Comment.objects.create(post=self.p, author=self.a, body="댓글")

    def test_others_cannot_delete(self):
        b = self.as_b()
        self.assertEqual(b.post(f"/community/{self.p.key}/delete").status_code, 404)
        self.assertEqual(b.post(f"/community/c/{self.c.key}/delete").status_code, 404)
        self.assertTrue(Post.objects.filter(pk=self.p.pk).exists())
        self.assertTrue(Comment.objects.filter(pk=self.c.pk).exists())

    def test_author_deletes_for_real(self):
        Report.objects.create(kind="comment", key=self.c.key, reporter=self.b)
        self.assertEqual(self.client.post(f"/community/c/{self.c.key}/delete").status_code, 303)
        self.assertFalse(Comment.objects.filter(pk=self.c.pk).exists())
        self.assertFalse(Report.objects.exists())
        c2 = Comment.objects.create(post=self.p, author=self.b, body="남의 댓글")
        Report.objects.create(kind="post", key=self.p.key, reporter=self.b)
        Report.objects.create(kind="comment", key=c2.key, reporter=self.a)
        self.assertEqual(self.client.post(f"/community/{self.p.key}/delete").status_code, 303)
        self.assertFalse(Post.objects.exists())
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(Report.objects.exists())
        self.assertEqual(self.client.get(f"/community/{self.p.key}").status_code, 404)

    def test_author_gone_post_cannot_be_deleted_by_anyone(self):
        Post.objects.filter(pk=self.p.pk).update(author=None)
        self.assertEqual(self.client.post(f"/community/{self.p.key}/delete").status_code, 404)


class HiddenTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Post.objects.create(author=self.a, title="내릴 제목", body="본문")
        self.c = Comment.objects.create(post=self.p, author=self.b, body="내릴 댓글")

    def run_cmd(self, *args):
        out = StringIO()
        call_command("community", *args, stdout=out)
        return out.getvalue()

    def test_hidden_post_404_everywhere(self):
        self.run_cmd("hide", self.p.key)
        b = self.as_b()
        self.assertNotContains(b.get("/community"), "내릴 제목")
        for path, method in [(f"/community/{self.p.key}", "get"), (f"/community/{self.p.key}/comment", "post"), (f"/community/{self.p.key}/report", "post"),
                             (f"/community/c/{self.c.key}/report", "post")]:
            self.assertEqual(getattr(b, method)(path, {"body": "x"}).status_code, 404, path)
        self.assertEqual(self.client.get(f"/community/{self.p.key}").status_code, 404)  # 쓴 사람도 주소로는 못 본다(목록 위 칸에서 지운다)
        self.assertEqual(b.post(f"/community/{self.p.key}/delete").status_code, 404)  # 남의 글
        self.assertEqual(Comment.objects.count(), 1)
        self.run_cmd("show", self.p.key)
        self.assertEqual(b.get(f"/community/{self.p.key}").status_code, 200)

    def test_hidden_comment_gone(self):
        self.run_cmd("hide", self.c.key)
        self.assertNotContains(self.client.get(f"/community/{self.p.key}"), "내릴 댓글")
        self.assertContains(self.client.get("/community"), 'num">0</span>')
        self.assertEqual(self.client.post(f"/community/c/{self.c.key}/delete").status_code, 404)  # 남의 댓글
        self.assertEqual(self.client.post(f"/community/c/{self.c.key}/report").status_code, 404)

    def test_unknown_key_errors(self):
        with self.assertRaises(CommandError):
            self.run_cmd("hide", "없는값")


class ReportTests(Base):
    def test_reason_choices_are_fixed(self):
        """까닭은 고른 값만 저장한다(자유 글 칸이 없다)."""
        p = Post.objects.create(author=self.a, title="제목", body="본문")
        self.assertNotContains(self.as_b().get(f"/community/{p.key}"), '<textarea name="reason"')
        self.as_b().post(f"/community/{p.key}/report", {"reason": "010-1234-5678"})
        self.assertEqual(Report.objects.get().reason, "")

    def setUp(self):
        super().setUp()
        self.p = Post.objects.create(author=self.a, title="제목", body="본문")
        self.c = Comment.objects.create(post=self.p, author=self.a, body="댓글")

    def test_same_person_counts_once(self):
        b = self.as_b()
        for _ in range(3):
            self.assertEqual(b.post(f"/community/{self.p.key}/report", {"reason": "abuse"}).status_code, 303)
            self.assertEqual(b.post(f"/community/c/{self.c.key}/report").status_code, 303)
        self.assertEqual(Report.objects.filter(kind="post").count(), 1)
        self.assertEqual(Report.objects.filter(kind="comment").count(), 1)
        self.assertEqual(Report.objects.get(kind="post").reason, "abuse")
        r = b.get(f"/community/{self.p.key}")
        self.assertContains(r, "신고했어요")
        self.assertNotContains(r, f'action="/community/{self.p.key}/report"')

    def test_bad_reason_dropped_and_own_refused(self):
        self.as_b().post(f"/community/{self.p.key}/report", {"reason": "<x>"})
        self.assertEqual(Report.objects.get().reason, "")
        self.assertEqual(self.client.post(f"/community/{self.p.key}/report").status_code, 404)
        self.assertEqual(Report.objects.count(), 1)

    def test_three_reports_do_not_hide(self):
        for i in range(3):
            Report.objects.create(kind="post", key=self.p.key, reporter=_google(f"r{i}", f"r{i}@example.com"))
        out = StringIO()
        call_command("community", "list-reports", stdout=out)
        text = out.getvalue()
        self.assertIn(self.p.key, text)
        self.assertIn("신고 3", text)
        self.assertNotIn("kim.real", text)
        self.assertEqual(self.client.get(f"/community/{self.p.key}").status_code, 200)
        self.assertIsNone(Post.objects.get().hidden_at)

    def test_list_reports_strips_terminal_escapes(self):
        p = Post.objects.create(author=self.a, title="제\x1b[2J목", body="본문")
        Report.objects.create(kind="post", key=p.key, reporter=self.b)
        out = StringIO()
        call_command("community", "list-reports", stdout=out)
        self.assertNotIn("\x1b", out.getvalue())

    def test_old_reports_purged(self):
        r = Report.objects.create(kind="post", key=self.p.key, reporter=self.b)
        now = timezone.now()
        self.assertEqual(retention.purge(now, dry_run=True)["community_reports"], 0)
        Report.objects.filter(pk=r.pk).update(created_at=now - timedelta(days=366))
        self.assertEqual(retention.purge(now)["community_reports"], 1)
        self.assertFalse(Report.objects.exists())


@override_settings(CACHES=RL_CACHES)
@mock.patch("config.ratelimit.time.time", return_value=1_800_000_030.0)
class RateTests(Base):
    def setUp(self):
        super().setUp()
        caches["ratelimit"].clear()

    def test_posts(self, _clock):
        for i in range(10):
            self.assertEqual(self.client.post("/community/new", {"title": f"t{i}", "body": "b"}).status_code, 303)
        r = self.client.post("/community/new", {"title": "넘침", "body": "b"})
        self.assertContains(r, "너무 자주", status_code=429)
        self.assertContains(r, 'value="넘침"', status_code=429)
        self.assertEqual(Post.objects.count(), 10)

    def test_comments(self, _clock):
        p = Post.objects.create(author=self.a, title="t", body="b")
        for i in range(30):
            self.assertEqual(self.client.post(f"/community/{p.key}/comment", {"body": "c"}).status_code, 303)
        self.assertContains(self.client.post(f"/community/{p.key}/comment", {"body": "남는 글"}), "남는 글", status_code=429)
        self.assertEqual(Comment.objects.count(), 30)

    def test_reports(self, _clock):
        p = Post.objects.create(author=self.a, title="t", body="b")
        b = self.as_b()
        for i in range(20):
            self.assertEqual(b.post(f"/community/{p.key}/report").status_code, 303)
        self.assertEqual(b.post(f"/community/{p.key}/report").status_code, 429)


class DeleteAccountTests(Base):
    def test_keeps_writing_as_gone_teacher(self):
        p = Post.objects.create(author=self.a, title="남을 제목", body="남을 본문")
        c = Comment.objects.create(post=p, author=self.a, body="남을 댓글")
        r = Report.objects.create(kind="post", key=p.key, reporter=self.a)
        out = StringIO()
        call_command("delete_account", "kim.real@example.com", stdout=out)
        self.assertIn("커뮤니티 글·댓글 연결 끊음 2", out.getvalue())
        self.assertFalse(CommunityProfile.objects.filter(nickname="봄바람").exists())
        p.refresh_from_db()
        c.refresh_from_db()
        r.refresh_from_db()
        self.assertIsNone(p.author)
        self.assertIsNone(c.author)
        self.assertIsNone(r.reporter)
        b = self.as_b()
        html = b.get(f"/community/{p.key}").content.decode()
        self.assertIn("남을 본문", html)
        self.assertIn("남을 댓글", html)
        self.assertEqual(html.count("나간 선생님"), 2)
        self.assertNotIn("봄바람", html)
        self.assertContains(b.get("/community"), "나간 선생님")
        # 빈 닉네임을 다른 사람이 쓸 수 있다
        self.assertEqual(CommunityProfile.objects.filter(nickname_key=nickname_key("봄바람")).count(), 0)


class LegalTests(TestCase):
    def test_terms_and_privacy_mention_community(self):
        terms = self.client.get("/terms").content.decode()
        privacy = self.client.get("/privacy").content.decode()
        self.assertIn("커뮤니티", terms)
        for word in ("닉네임", "신고", "나간 선생님", "쓴 계정과 연결"):
            self.assertIn(word, privacy)
            self.assertIn(word.replace("쓴 계정과 연결", "계정과 연결"), terms)
        self.assertIn("시행일: 2026년 12월 14일", terms)
        self.assertIn("시행일: 2026년 12월 14일", privacy)
