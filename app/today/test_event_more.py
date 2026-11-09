"""P2 일정 본문·첨부·댓글.

불변식
- 범위 밖(남의 '나'·다른 소속·다른 공간)에는 본문·첨부·댓글이 어떤 주소로도 안 보인다(404, 내려받기 포함).
- 볼 수 있는 사람은 읽고 댓글을 쓴다. 본문·첨부를 고치는 것은 쓴 사람·관리자(writable_by)만.
- 첨부는 공지 첨부와 같은 형식·크기·저장 한도. 막히면 저장되는 것이 없다.
- 댓글이 달리면 일정 쓴 사람에게 🔔(자기 댓글은 없음). 댓글 지우기는 쓴 사람·관리자, 이름 글자만 있는 댓글은 관리자만.
- 플랜이 끝난 학교도 일정 쓰기와 같게 막지 않는다.
"""
import os
import shutil
import tempfile
from datetime import date, timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from collect.models import Notification
from hwpfiles.models import SourceFile
from hwpfiles.tests import sample_hwpx
from notices.views import QUOTA
from spaces.models import School, Space

from .models import BODY_MAX, COMMENT_MAX, Event, EventComment, EventFile
from .test_school import NOW, TODAY, Base

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def up(name, data=PDF):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


class MoreBase(Base):
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        o = override_settings(MEDIA_ROOT=media)
        o.enable()
        self.addCleanup(o.disable)

    def url(self, e, tail=""):
        return f"{self.base}/today/e/{e.key}{tail}"

    def upload(self, who, e, *files):
        return self.as_(who).post(self.url(e, "/files"), {"attach": list(files)}, headers={"HX-Request": "true"})

    def filled(self, by, scope, title="사학년협의"):
        """본문·첨부·댓글이 다 있는 일정."""
        e = self.ev(by, title, scope, body="비밀본문 첫 줄\n둘째 줄")
        self.assertEqual(self.upload(by, e, up("비밀첨부.pdf")).status_code, 200)
        self.assertEqual(self.as_(by).post(self.url(e, "/comments"), {"body": "비밀댓글"}).status_code, 200)
        return e


class ScopeTests(MoreBase):
    def assertHidden(self, who, e):
        c = self.as_(who)
        f, cm = e.files.get(), e.comments.get()
        for path in ("/more", f"/f/{f.key}"):
            self.assertEqual(c.get(self.url(e, path)).status_code, 404, (who.name, path))
        for path, data in (("/body", {"body": "x"}), ("/comments", {"body": "x"}), ("/files", {"attach": [up("a.pdf")]}),
                           (f"/f/{f.key}/delete", {}), (f"/c/{cm.key}/delete", {})):
            self.assertEqual(c.post(self.url(e, path), data).status_code, 404, (who.name, path))
        html = "".join(c.get(f"{self.base}/schedule", {"tab": t}).content.decode() for t in ("school", "group", "me"))
        html += c.get(f"{self.base}/today").content.decode()
        for word in ("비밀본문", "비밀첨부", "비밀댓글", f.key, cm.key):
            self.assertNotIn(word, html, (who.name, word))
        self.assertEqual(e.comments.count(), 1)
        self.assertEqual(e.files.count(), 1)
        e.refresh_from_db()
        self.assertNotEqual(e.body, "x")

    def test_other_group_sees_nothing(self):
        self.assertHidden(self.t51, self.filled(self.t41, "g4"))

    def test_private_is_hidden_from_same_group_and_manager(self):
        e = self.filled(self.t41, "me")
        self.assertHidden(self.t42, e)
        self.assertHidden(self.boss, e)

    def test_other_space_is_404(self):
        e = self.filled(self.t41, "all")
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초", plan="school", paid_until=date(2099, 1, 1))
        hub = Space.objects.create(school=s, kind="school", grade=0, name="옆초")
        boss = self.seat("교무실", "옆부장", "office", manager=True, space=hub)
        c = self.as_(boss)
        f = e.files.get()
        for path in ("/more", f"/f/{f.key}"):
            self.assertEqual(c.get(f"/s/{hub.token}/today/e/{e.key}{path}").status_code, 404)
        self.assertEqual(c.post(f"/s/{hub.token}/today/e/{e.key}/comments", {"body": "x"}).status_code, 404)
        # 내 공간이 아닌 공간 주소는 공간 미들웨어가 막는다
        self.assertNotEqual(c.get(self.url(e, "/more")).status_code, 200)
        self.assertNotEqual(c.get(self.url(e, f"/f/{f.key}")).status_code, 200)

    def test_file_key_of_other_event_is_404(self):
        a, b = self.filled(self.t41, "all", "가일정"), self.ev(self.t42, "나일정", "all")
        f = a.files.get()
        self.assertEqual(self.as_(self.t42).get(self.url(b, f"/f/{f.key}")).status_code, 404)
        self.assertEqual(self.as_(self.t42).post(self.url(b, f"/f/{f.key}/delete")).status_code, 404)

    def test_reader_reads_downloads_and_comments_but_cannot_edit(self):
        e = self.filled(self.t41, "g4")
        c = self.as_(self.t42)
        r = c.get(self.url(e, "/more"))
        self.assertContains(r, "비밀본문 첫 줄<br>둘째 줄")
        self.assertContains(r, "비밀첨부.pdf")
        self.assertContains(r, "비밀댓글")
        self.assertNotContains(r, "/body")
        self.assertNotContains(r, "/files")
        f = e.files.get()
        d = c.get(self.url(e, f"/f/{f.key}"))
        self.assertEqual(d.status_code, 200)
        self.assertIn("attachment", d["Content-Disposition"])
        self.assertEqual(b"".join(d.streaming_content), PDF)
        self.assertEqual(c.post(self.url(e, "/comments"), {"body": "같은 학년 댓글"}).status_code, 200)
        self.assertEqual(c.post(self.url(e, "/body"), {"body": "덮어쓰기"}).status_code, 403)
        self.assertEqual(self.upload(self.t42, e, up("더.pdf")).status_code, 403)
        self.assertEqual(c.post(self.url(e, f"/f/{f.key}/delete")).status_code, 403)
        e.refresh_from_db()
        self.assertEqual((e.body.startswith("비밀본문"), e.files.count(), e.comments.count()), (True, 1, 2))

    def test_manager_edits_others_event(self):
        e = self.filled(self.t41, "g4")
        c = self.as_(self.boss)
        self.assertEqual(c.post(self.url(e, "/body"), {"body": "관리자가 고침"}).status_code, 200)
        self.assertEqual(c.post(self.url(e, f"/f/{e.files.get().key}/delete")).status_code, 200)
        e.refresh_from_db()
        self.assertEqual((e.body, e.files.count()), ("관리자가 고침", 0))


class BodyTests(MoreBase):
    def test_body_keeps_newlines_and_is_not_a_change(self):
        e = self.ev(self.t41, "협의", "all")
        before = e.updated_at
        with mock.patch("django.utils.timezone.now", return_value=NOW + timedelta(hours=1)):
            r = self.as_(self.t41).post(self.url(e, "/body"), {"body": "  준비물: 공책\r\n\r\n색연필  "})
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.body, "준비물: 공책\n\n색연필")
        self.assertEqual(e.updated_at, before)
        self.assertIsNone(e.changed_from)

    def test_body_is_escaped(self):
        e = self.ev(self.t41, "협의", "all", body="<script>alert(1)</script>")
        r = self.as_(self.t42).get(self.url(e, "/more"))
        self.assertNotContains(r, "<script>alert(1)")
        self.assertContains(r, "&lt;script&gt;")

    def test_body_limit(self):
        e = self.ev(self.t41, "협의", "all")
        r = self.as_(self.t41).post(self.url(e, "/body"), {"body": "가" * (BODY_MAX + 1)})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "10,000자까지", status_code=400)
        e.refresh_from_db()
        self.assertEqual(e.body, "")


class FileTests(MoreBase):
    def setUp(self):
        super().setUp()
        self.e = self.ev(self.t41, "협의", "all")

    def assertRejected(self, f, word=""):
        r = self.upload(self.t41, self.e, f)
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, word, status_code=400)
        self.assertEqual(EventFile.objects.count(), 0)

    def test_upload_hwpx_and_pdf(self):
        r = self.upload(self.t41, self.e, up("계획.pdf"), up("명단.hwpx", sample_hwpx()))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sorted(self.e.files.values_list("kind", flat=True)), ["hwpx", "pdf"])
        f = self.e.files.get(kind="pdf")
        self.assertEqual(f.stored, len(PDF))
        self.assertTrue(f.file.name.startswith(f"event/{self.hub.pk}/{f.key}."))
        self.assertNotIn("계획", f.file.name)
        self.assertEqual(SourceFile.used_bytes(self.hub), sum(self.e.files.values_list("stored", flat=True)))

    def test_blocked_kinds(self):
        self.assertRejected(up("공문.exe", PDF), "그림만")
        self.assertRejected(up("공문.pdf", b"MZ\x90\x00 exe"), "내용이 달라요")
        self.assertRejected(up("공문.hwp", PDF), "내용이 달라요")
        self.assertRejected(up("빈.pdf", b""), "빈 파일")

    def test_too_big(self):
        self.assertRejected(up("큰.pdf", b"%PDF-" + b"0" * (20 * 1024 * 1024 - 4)), "20MB")

    def test_declared_body_over_limit_reads_nothing(self):
        r = self.as_(self.t41).post(self.url(self.e, "/files"), {"attach": [up("a.pdf")]}, CONTENT_LENGTH=str(100 * 1024 * 1024 + 1))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(EventFile.objects.count(), 0)

    def test_school_quota(self):
        with override_settings(HWP_SCHOOL_QUOTA=SourceFile.used_bytes(self.hub) + 1):
            self.assertRejected(up("a.pdf"), QUOTA)

    def test_five_files_per_event(self):
        self.assertEqual(self.upload(self.t41, self.e, *[up(f"{i}.pdf") for i in range(5)]).status_code, 200)
        r = self.upload(self.t41, self.e, up("6.pdf"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.e.files.count(), 5)

    def test_event_delete_removes_disk_file(self):
        self.upload(self.t41, self.e, up("a.pdf"))
        path = self.e.files.get().file.path
        self.assertTrue(os.path.exists(path))
        with self.captureOnCommitCallbacks(execute=True):
            self.as_(self.t41).post(self.url(self.e, "/delete"))
        self.assertFalse(Event.objects.filter(pk=self.e.pk).exists())
        self.assertFalse(os.path.exists(path))

    def test_schedule_cell_shows_counts_only(self):
        self.e.body = "본문내용"
        self.e.save()
        self.upload(self.t41, self.e, up("a.pdf"), up("b.pdf"))
        self.as_(self.t42).post(self.url(self.e, "/comments"), {"body": "댓글내용"})
        html = self.as_(self.t42).get(f"{self.base}/schedule", {"tab": "school"}).content.decode()
        self.assertIn('<span class="sr">첨부</span><span class="num">2</span>', html)
        self.assertIn('<span class="sr">댓글</span><span class="num">1</span>', html)
        self.assertIn("본문 있음", html)
        for word in ("본문내용", "댓글내용", "a.pdf"):  # 내용은 대화상자를 열 때 받아 온다
            self.assertNotIn(word, html)


class CommentTests(MoreBase):
    def test_bell_to_author_not_self(self):
        e = self.ev(self.t41, "협의", "g4")
        self.as_(self.t41).post(self.url(e, "/comments"), {"body": "내 댓글"})
        self.assertEqual(Notification.objects.count(), 0)
        r = self.as_(self.t42).post(self.url(e, "/comments"), {"body": "질문 있어요"})
        self.assertEqual(r.status_code, 200)
        n = Notification.objects.get()
        self.assertEqual((n.member, n.kind, n.pushed), (self.t41, Notification.Kind.EV_COMMENT, True))
        self.assertIn("협의", n.text)
        self.assertNotIn("질문 있어요", n.text)
        self.assertTrue(n.link.startswith(f"/s/{self.hub.token}/schedule?") and n.link.endswith(f"#ev-{e.key}"))
        self.assertIn("tab=group", n.link)
        c = EventComment.objects.get(body="질문 있어요")
        self.assertEqual((c.by, c.by_name), (self.t42, "사이"))

    def test_no_bell_when_author_left(self):
        e = Event.objects.create(space=self.hub, by=None, date=TODAY, title="옛일정", scope="all")
        self.assertEqual(self.as_(self.t42).post(self.url(e, "/comments"), {"body": "댓글"}).status_code, 200)
        self.assertEqual(Notification.objects.count(), 0)

    def test_empty_and_too_long(self):
        e = self.ev(self.t41, "협의", "all")
        c = self.as_(self.t42)
        self.assertEqual(c.post(self.url(e, "/comments"), {"body": "  "}).status_code, 400)
        self.assertEqual(c.post(self.url(e, "/comments"), {"body": "가" * (COMMENT_MAX + 1)}).status_code, 400)
        self.assertEqual(e.comments.count(), 0)

    def test_delete_rights(self):
        e = self.ev(self.t41, "협의", "all")
        self.as_(self.t42).post(self.url(e, "/comments"), {"body": "사이 댓글"})
        mine = e.comments.get()
        old = EventComment.objects.create(event=e, by=None, by_name="옛선생님", body="옮겨 온 댓글", at=NOW - timedelta(days=900))
        self.assertEqual(self.as_(self.t41).post(self.url(e, f"/c/{mine.key}/delete")).status_code, 403)  # 일정 쓴 사람이어도 남의 댓글은 못 지움
        self.assertEqual(self.as_(self.t42).post(self.url(e, f"/c/{old.key}/delete")).status_code, 403)
        self.assertEqual(self.as_(self.t42).post(self.url(e, f"/c/{mine.key}/delete")).status_code, 200)
        self.assertEqual(self.as_(self.boss).post(self.url(e, f"/c/{old.key}/delete")).status_code, 200)
        self.assertEqual(e.comments.count(), 0)

    def test_imported_comment_keeps_name_and_date(self):
        e = self.ev(self.t41, "협의", "all")
        EventComment.objects.create(event=e, by=None, by_name="옛선생님", body="옮겨 온 댓글", at=NOW.replace(year=2023, month=3, day=2))
        r = self.as_(self.t42).get(self.url(e, "/more"))
        self.assertContains(r, "옛선생님")
        self.assertContains(r, "2023/3/2")
        self.assertNotContains(r, "/c/")  # 지우기 단추 없음(관리자만)


class PlanTests(MoreBase):
    def test_expired_school_keeps_working_like_events(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        e = self.ev(self.t41, "협의", "all")
        c = self.as_(self.t41)
        self.assertEqual(c.post(self.url(e, "/body"), {"body": "본문"}).status_code, 200)
        self.assertEqual(self.upload(self.t41, e, up("a.pdf")).status_code, 200)
        self.assertEqual(self.as_(self.t42).post(self.url(e, "/comments"), {"body": "댓글"}).status_code, 200)
