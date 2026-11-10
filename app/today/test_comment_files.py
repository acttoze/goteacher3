"""P5 일정 댓글 첨부.

불변식
- 볼 수 있는 사람이 댓글과 함께 올리고, 내려받기는 그 일정을 볼 수 있는 사람만(범위 밖·다른 공간 404).
- 형식·크기·학교 저장 한도는 일정 첨부와 같다. 댓글당 COMMENT_FILES개. 막히면 댓글도 파일도 저장되지 않는다.
- 댓글 첨부는 일정 첨부 목록·개수·칸 표시에 들지 않고, 일정 첨부 지우기 주소로 지울 수 없다.
- 댓글·일정을 지우면 디스크 파일까지 지운다. 이관(P3)은 이름 글자·원래 시각으로 넣을 수 있다.
"""
import os
from datetime import timedelta

from django.test import override_settings

from hwpfiles.models import SourceFile
from notices.views import QUOTA
from spaces.models import School, Space

from .models import Event, EventComment, EventFile
from .more import COMMENT_FILES
from .test_event_more import PDF, MoreBase, up
from .test_school import NOW, UNTIL


class CommentFileTests(MoreBase):
    def setUp(self):
        super().setUp()
        self.e = self.ev(self.t41, "사학년협의", "g4")

    def post(self, who, body="댓글", *files, e=None):
        return self.as_(who).post(self.url(e or self.e, "/comments"), {"body": body, "attach": list(files)},
                                  headers={"HX-Request": "true"})

    def test_reader_attaches_and_same_group_downloads(self):
        r = self.post(self.t42, "자료 붙여요", up("명단.pdf"))
        self.assertEqual(r.status_code, 200)
        c = EventComment.objects.get()
        f = c.files.get()
        self.assertEqual((f.event, f.name, f.kind, f.stored), (self.e, "명단.pdf", "pdf", len(PDF)))
        self.assertTrue(f.file.name.startswith(f"event/{self.hub.pk}/{f.key}."))
        html = self.as_(self.t41).get(self.url(self.e, "/more")).content.decode()
        self.assertIn("명단.pdf", html)
        self.assertIn(f"/f/{f.key}", html)
        d = self.as_(self.t41).get(self.url(self.e, f"/f/{f.key}"))
        self.assertEqual(d.status_code, 200)
        self.assertIn("attachment", d["Content-Disposition"])
        self.assertEqual(b"".join(d.streaming_content), PDF)
        self.assertEqual(SourceFile.used_bytes(self.hub), len(PDF))

    def test_out_of_range_and_other_space_404(self):
        self.post(self.t42, "자료", up("비밀자료.pdf"))
        f = EventFile.objects.get()
        self.assertEqual(self.as_(self.t51).get(self.url(self.e, f"/f/{f.key}")).status_code, 404)  # 다른 학년
        self.assertNotContains(self.as_(self.t51).get(f"{self.base}/schedule", {"tab": "school"}), "비밀자료")
        self.assertEqual(self.post(self.t51, "x", up("a.pdf")).status_code, 404)
        s = School.objects.create(neis_atpt="C10", neis_code="3", name="옆초", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=s, kind="school", grade=0, name="옆초")
        boss = self.seat("교무실", "옆부장", "office", manager=True, space=hub)
        self.assertEqual(self.as_(boss).get(f"/s/{hub.token}/today/e/{self.e.key}/f/{f.key}").status_code, 404)
        private = self.ev(self.t41, "혼자", "me")
        EventComment.objects.create(event=private, by=self.t41, by_name="사일", body="메모")
        self.assertEqual(self.post(self.t42, "x", up("a.pdf"), e=private).status_code, 404)
        self.assertEqual(EventFile.objects.count(), 1)

    def test_blocked_kind_saves_nothing(self):
        for f in (up("공문.exe", PDF), up("공문.pdf", b"MZ\x90\x00 exe"), up("공문.hwp", PDF)):
            r = self.post(self.t42, "자료", f)
            self.assertEqual(r.status_code, 400)
            self.assertContains(r, "자료", status_code=400)  # 쓴 댓글은 칸에 남긴다
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))

    def test_count_size_and_quota(self):
        r = self.post(self.t42, "많이", *[up(f"{i}.pdf") for i in range(COMMENT_FILES + 1)])
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, f"{COMMENT_FILES}개까지", status_code=400)
        self.assertEqual(self.post(self.t42, "큼", up("큰.pdf", b"%PDF-" + b"0" * (20 * 1024 * 1024 - 4))).status_code, 400)
        with override_settings(HWP_SCHOOL_QUOTA=SourceFile.used_bytes(self.hub) + 1):
            r = self.post(self.t42, "한도", up("a.pdf"))
            self.assertContains(r, QUOTA, status_code=400)
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))
        self.assertEqual(self.post(self.t42, "딱 맞게", *[up(f"{i}.pdf") for i in range(COMMENT_FILES)]).status_code, 200)
        self.assertEqual(EventFile.objects.count(), COMMENT_FILES)

    def test_not_counted_as_event_files(self):
        self.post(self.t42, "자료", up("댓글자료.pdf"))
        f = EventFile.objects.get()
        # 일정 첨부 5개는 댓글 첨부와 따로 센다
        self.assertEqual(self.upload(self.t41, self.e, *[up(f"{i}.pdf") for i in range(5)]).status_code, 200)
        self.assertEqual(self.e.files.count(), 6)
        # 일정 첨부 지우기 주소로는 댓글 첨부를 못 지운다(쓴 사람·관리자도)
        for who in (self.t41, self.boss):
            self.assertEqual(self.as_(who).post(self.url(self.e, f"/f/{f.key}/delete")).status_code, 404)
        html = self.as_(self.t41).get(f"{self.base}/schedule", {"tab": "group"}).content.decode()
        self.assertIn('<span class="sr">첨부</span><span class="num">5</span>', html)

    def test_comment_delete_removes_files(self):
        self.post(self.t42, "자료", up("a.pdf"), up("b.pdf"))
        c = EventComment.objects.get()
        paths = [f.file.path for f in c.files.all()]
        self.assertTrue(all(os.path.exists(p) for p in paths))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.as_(self.t42).post(self.url(self.e, f"/c/{c.key}/delete")).status_code, 200)
        self.assertEqual(EventFile.objects.count(), 0)
        self.assertFalse(any(os.path.exists(p) for p in paths))

    def test_event_delete_removes_comment_files(self):
        self.post(self.t42, "자료", up("a.pdf"))
        path = EventFile.objects.get().file.path
        with self.captureOnCommitCallbacks(execute=True):
            self.as_(self.t41).post(self.url(self.e, "/delete"))
        self.assertFalse(Event.objects.filter(pk=self.e.pk).exists())
        self.assertEqual(EventFile.objects.count(), 0)
        self.assertFalse(os.path.exists(path))

    def test_declared_body_over_limit_saves_nothing(self):
        r = self.as_(self.t42).post(self.url(self.e, "/comments"), {"body": "x", "attach": [up("a.pdf")]},
                                    CONTENT_LENGTH=str(100 * 1024 * 1024 + 1))
        self.assertEqual(r.status_code, 400)
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))

    def test_import_shape(self):
        """P3 이관: 작성자 자리 없이 이름 글자·원래 시각으로 댓글과 첨부를 넣는다."""
        from django.core.files.base import ContentFile
        old = NOW - timedelta(days=800)
        c = EventComment.objects.create(event=self.e, by=None, by_name="옛선생님", body="옮긴 댓글", at=old)
        f = EventFile(event=self.e, comment=c, name="옛자료.hwpx", kind="hwpx", stored=3, at=old)
        f.file.save("hwpx", ContentFile(b"abc"))
        html = self.as_(self.t42).get(self.url(self.e, "/more")).content.decode()
        self.assertIn("옛자료.hwpx", html)
        self.assertEqual(EventFile.objects.get().at, old)


class GuestTests(MoreBase):
    """손님(이름만 들어온 자리)은 댓글 글만. 파일이 붙으면 400, 화면에 파일 고르기 칸이 없다."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        from spaces.models import Member
        self.e = self.ev(self.t41, "협의", "all")
        u = get_user_model().objects.create(username="guest:abc", first_name="손님")
        self.guest = Member.objects.create(space=self.hub, user=u, name="손님", cls="4-3", group="g4")

    def test_guest_cannot_attach_but_can_comment(self):
        c = self.as_(self.guest)
        r = c.post(self.url(self.e, "/comments"), {"body": "자료요", "attach": [up("a.pdf")]})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "계정으로 들어온 선생님만", status_code=400)
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))
        self.assertEqual(c.post(self.url(self.e, "/comments"), {"body": "글만"}).status_code, 200)
        self.assertEqual(EventComment.objects.get().by, self.guest)
        self.assertNotContains(c.get(self.url(self.e, "/more")), 'name="attach"')
        self.assertContains(self.as_(self.t42).get(self.url(self.e, "/more")), 'name="attach"', count=1)

    def test_oversize_message_says_nothing_saved(self):
        r = self.as_(self.t42).post(self.url(self.e, "/comments"), {"body": "x", "attach": [up("a.pdf")]},
                                    CONTENT_LENGTH=str(100 * 1024 * 1024 + 1))
        self.assertContains(r, "댓글과 첨부 모두 저장되지 않았어요", status_code=400)
        self.assertNotContains(r, "방금 쓴 내용은", status_code=400)
