"""P2 일정 본문·첨부·댓글 — QA가 더한 경계 사례(구현 테스트 test_event_more.py와 겹치지 않는 것만).

- 동학년 공간: 누구나 본문·첨부를 고치고, 댓글 🔔은 일정 쓴 사람에게. 다른 동학년은 404.
- 다른 일정의 댓글 key로 지우기 → 404. 로그인 없이 → 내용 없음.
- 댓글·파일 이름 이스케이프, 내려받기 응답 헤더.
- 고아 파일 정리(event/), 저장 크기 어긋남, 계정 삭제 때 댓글 이름 지움, 학교 합치기 뒤에도 그대로 보임.
"""
import os
from datetime import UTC, datetime, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client

from collect.models import Notification
from hwpfiles import retention
from spaces.merge import open_school
from spaces.models import Member, School, Space

from .models import Event, EventComment, EventFile
from .test_event_more import PDF, MoreBase, up
from .test_school import TODAY, UNTIL, User


class GradeSpaceTests(MoreBase):
    def setUp(self):
        super().setUp()
        self.s2 = School.objects.create(neis_atpt="J10", neis_code="1111111", name="동학년초")
        self.g = Space.objects.create(school=self.s2, grade=4, name="4학년")
        self.a = self.seat("4-1", "가교사", "", space=self.g)
        self.b = self.seat("4-2", "나교사", "", space=self.g)
        self.other = Space.objects.create(school=self.s2, grade=5, name="5학년")
        self.o = self.seat("5-1", "다교사", "", space=self.other)
        self.e = Event.objects.create(space=self.g, by=self.a, date=TODAY, title="학년협의", scope="all", body="학년본문")

    def gurl(self, tail=""):
        return f"/s/{self.g.token}/today/e/{self.e.key}{tail}"

    def test_any_member_edits_and_bell_goes_to_author(self):
        c = self.as_(self.b)
        self.assertContains(c.get(self.gurl("/more")), "학년본문")
        self.assertEqual(c.post(self.gurl("/body"), {"body": "나교사가 고침"}).status_code, 200)
        self.assertEqual(c.post(self.gurl("/files"), {"attach": [up("a.pdf")]}).status_code, 200)
        self.assertEqual(c.post(self.gurl("/comments"), {"body": "확인했어요"}).status_code, 200)
        self.e.refresh_from_db()
        self.assertEqual((self.e.body, self.e.files.count()), ("나교사가 고침", 1))
        n = Notification.objects.get()
        self.assertEqual(n.member, self.a)
        self.assertTrue(n.link.startswith(f"/s/{self.g.token}/schedule?w=") and "tab=" not in n.link, n.link)
        self.assertEqual(c.get(self.gurl(f"/f/{self.e.files.get().key}")).status_code, 200)

    def test_other_grade_space_is_404(self):
        self.as_(self.a).post(self.gurl("/files"), {"attach": [up("a.pdf")]})
        f = self.e.files.get()
        c = self.as_(self.o)
        for path in ("/more", f"/f/{f.key}"):
            self.assertEqual(c.get(f"/s/{self.other.token}/today/e/{self.e.key}{path}").status_code, 404, path)
            self.assertNotEqual(c.get(self.gurl(path)).status_code, 200, path)
        self.assertEqual(c.post(f"/s/{self.other.token}/today/e/{self.e.key}/comments", {"body": "x"}).status_code, 404)
        self.assertEqual(self.e.comments.count(), 0)

    def test_merge_into_school_keeps_more(self):
        self.as_(self.a).post(self.gurl("/files"), {"attach": [up("a.pdf")]})
        self.as_(self.b).post(self.gurl("/comments"), {"body": "합치기 전 댓글"})
        hub = open_school(self.s2, UNTIL, manager=self.a)
        self.e.refresh_from_db()
        self.assertEqual(self.e.space, hub)
        c = self.as_(self.b)
        r = c.get(f"/s/{hub.token}/today/e/{self.e.key}/more")
        self.assertContains(r, "합치기 전 댓글")
        self.assertContains(r, "a.pdf")
        self.assertEqual(c.get(f"/s/{hub.token}/today/e/{self.e.key}/f/{self.e.files.get().key}").status_code, 200)
        # 옛 4학년 일정은 g4만: 5학년은 못 본다
        self.assertEqual(self.as_(self.o).get(f"/s/{hub.token}/today/e/{self.e.key}/more").status_code, 404)
        self.assertTrue(Notification.objects.get().link.startswith(f"/s/{hub.token}/"))


class EdgeTests(MoreBase):
    def test_comment_key_of_other_event_is_404(self):
        a, b = self.ev(self.t41, "가", "all"), self.ev(self.t42, "나", "all")
        self.as_(self.t42).post(self.url(a, "/comments"), {"body": "가에 단 댓글"})
        cm = a.comments.get()
        self.assertEqual(self.as_(self.t42).post(self.url(b, f"/c/{cm.key}/delete")).status_code, 404)
        self.assertEqual(self.as_(self.boss).post(self.url(b, f"/c/{cm.key}/delete")).status_code, 404)
        self.assertEqual(a.comments.count(), 1)

    def test_anonymous_gets_nothing(self):
        e = self.filled(self.t41, "all")
        f = e.files.get()
        c = Client()
        for path in ("/more", f"/f/{f.key}"):
            r = c.get(self.url(e, path))
            self.assertNotEqual(r.status_code, 200, path)
            self.assertNotIn("비밀", r.content.decode(errors="ignore"))
        self.assertNotEqual(c.post(self.url(e, "/comments"), {"body": "x"}).status_code, 200)
        self.assertEqual(e.comments.count(), 1)

    def test_get_on_write_paths_is_405(self):
        e = self.filled(self.t41, "all")
        c = self.as_(self.t41)
        for path in ("/body", "/files", "/comments", f"/f/{e.files.get().key}/delete", f"/c/{e.comments.get().key}/delete"):
            self.assertEqual(c.get(self.url(e, path)).status_code, 405, path)

    def test_comment_and_file_name_are_escaped(self):
        e = self.ev(self.t41, "협의", "all")
        self.as_(self.t41).post(self.url(e, "/files"), {"attach": [up('<img src=x onerror=alert(1)>.pdf')]})
        self.as_(self.t42).post(self.url(e, "/comments"), {"body": "<script>alert(2)</script>\n둘째"})
        html = self.as_(self.t42).get(self.url(e, "/more")).content.decode()
        self.assertNotIn("<script>alert(2)", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;<br>둘째", html)

    def test_download_headers(self):
        e = self.filled(self.t41, "all")
        r = self.as_(self.t42).get(self.url(e, f"/f/{e.files.get().key}"))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", r["Content-Security-Policy"])
        self.assertIn("no-store", r.get("Cache-Control", ""))

    def test_author_left_only_manager_edits(self):
        e = self.ev(self.t41, "옛일정", "all")
        Event.objects.filter(pk=e.pk).update(by=None)
        self.assertEqual(self.as_(self.t42).post(self.url(e, "/body"), {"body": "x"}).status_code, 403)
        self.assertEqual(self.as_(self.boss).post(self.url(e, "/body"), {"body": "관리자"}).status_code, 200)

    def test_reader_cannot_delete_event_with_files(self):
        e = self.filled(self.t41, "all")
        path = e.files.get().file.path
        self.assertNotEqual(self.as_(self.t42).post(self.url(e, "/delete")).status_code, 200)
        self.assertTrue(Event.objects.filter(pk=e.pk).exists())
        self.assertTrue(os.path.exists(path))

    def test_file_delete_removes_disk_and_frees_quota(self):
        e = self.filled(self.t41, "all")
        f = e.files.get()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.as_(self.t41).post(self.url(e, f"/f/{f.key}/delete")).status_code, 200)
        self.assertFalse(default_storage.exists(f.file.name))
        from hwpfiles.models import SourceFile
        self.assertEqual(SourceFile.used_bytes(self.hub), 0)


class RetentionTests(MoreBase):
    def test_orphan_event_file_and_stored_mismatch(self):
        e = self.filled(self.t41, "all")
        f = e.files.get()
        orphan = default_storage.save(f"event/{self.hub.pk}/stray.pdf", ContentFile(PDF))
        later = datetime.now(UTC) + timedelta(hours=2)  # 파일 시각은 실제 시계(timezone.now는 고정돼 있다)
        found = retention.orphan_files(later)
        self.assertIn(orphan, found)
        self.assertNotIn(f.file.name, found)
        self.assertEqual(retention.stored_mismatches(), [])
        EventFile.objects.filter(pk=f.pk).update(stored=1)
        self.assertEqual([(type(r).__name__, n) for r, n in retention.stored_mismatches()], [("EventFile", len(PDF))])


class DeleteAccountTests(MoreBase):
    def test_comment_name_forgotten(self):
        e = self.ev(self.t41, "협의", "all")
        self.as_(self.t42).post(self.url(e, "/comments"), {"body": "남길 댓글"})
        call_command("delete_account", "사이", stdout=StringIO())
        c = EventComment.objects.get()
        self.assertEqual((c.by, c.by_name, c.body), (None, "나간 선생님", "남길 댓글"))
        self.assertFalse(Member.objects.filter(name="사이").exists())
        self.assertFalse(User.objects.filter(username="google:사이").exists())
