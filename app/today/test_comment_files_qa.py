"""P5 일정 댓글 첨부 — QA가 더한 경계 사례(구현 테스트 test_comment_files.py와 겹치지 않는 것만).

- 남의 '나' 일정 댓글 첨부, 같은 학교 동학년 공간, 링크 키 바꿔 끼우기 → 404
- P4 형식(오피스·그림) 받기, 막힌 형식·가짜·매크로 안내와 저장 없음
- 첨부만 있고 댓글 글이 없으면 아무것도 저장 안 함, 지운 댓글의 첨부 주소 404, 남이 못 지움
- 댓글 POST의 CSRF는 그대로 검사(_body_guard가 csrf_exempt로 감싼다)
- 앱 본문 상한(config.bodylimit)과 Traefik up101 정규식이 같은 주소를 고른다
"""
import os
import re
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase

from config import bodylimit
from notices.test_filekinds import BLOCKED, fakes, macros, samples
from notices.views import KINDS_TEXT
from spaces.models import Space

from .models import EventComment, EventFile
from .test_event_more import PDF, MoreBase, up


class CommentFileScopeQA(MoreBase):
    def setUp(self):
        super().setUp()
        self.e = self.ev(self.t41, "사학년협의", "g4")

    def post(self, who, body="댓글", *files, e=None):
        return self.as_(who).post(self.url(e or self.e, "/comments"), {"body": body, "attach": list(files)},
                                  headers={"HX-Request": "true"})

    def test_private_event_comment_file_hidden_from_group_and_manager(self):
        mine = self.ev(self.t41, "혼자", "me")
        self.assertEqual(self.post(self.t41, "내 메모", up("나만.pdf"), e=mine).status_code, 200)
        f = EventFile.objects.get()
        self.assertEqual(self.as_(self.t41).get(self.url(mine, f"/f/{f.key}")).status_code, 200)
        for who in (self.t42, self.boss, self.t51):
            self.assertEqual(self.as_(who).get(self.url(mine, f"/f/{f.key}")).status_code, 404, who.name)
            self.assertEqual(self.as_(who).get(self.url(mine, "/more")).status_code, 404, who.name)

    def test_key_swap_into_reachable_event_is_404(self):
        """볼 수 있는 일정 주소에 볼 수 없는 일정의 댓글 첨부 키를 끼워도 404."""
        self.post(self.t42, "자료", up("사학년만.pdf"))
        f = EventFile.objects.get()
        theirs = self.ev(self.t51, "오학년협의", "g5")
        self.assertEqual(self.as_(self.t51).get(self.url(theirs, f"/f/{f.key}")).status_code, 404)
        whole = self.ev(self.boss, "전체행사", "all")
        self.assertEqual(self.as_(self.t51).get(self.url(whole, f"/f/{f.key}")).status_code, 404)
        self.assertEqual(self.as_(self.t51).post(self.url(whole, f"/f/{f.key}/delete")).status_code, 404)
        self.assertTrue(EventFile.objects.filter(pk=f.pk).exists())

    def test_grade_space_of_same_school_is_404(self):
        self.post(self.t42, "자료", up("a.pdf"))
        f = EventFile.objects.get()
        g = Space.objects.create(school=self.school, grade=4, name="4학년")
        m = self.seat("4-3", "사삼", "g4", space=g)
        c = self.as_(m)
        self.assertEqual(c.get(f"/s/{g.token}/today/e/{self.e.key}/f/{f.key}").status_code, 404)
        self.assertEqual(c.post(f"/s/{g.token}/today/e/{self.e.key}/comments", {"body": "x", "attach": [up("b.pdf")]}).status_code, 404)
        self.assertEqual(EventFile.objects.count(), 1)

    def test_deleted_comment_file_url_is_404_and_others_cannot_delete(self):
        self.post(self.t42, "자료", up("a.pdf"))
        c, f = EventComment.objects.get(), EventFile.objects.get()
        self.assertEqual(self.as_(self.t41).post(self.url(self.e, f"/c/{c.key}/delete")).status_code, 403)  # 일정 쓴 사람도 남의 댓글은 못 지움
        self.assertTrue(EventFile.objects.filter(pk=f.pk).exists())
        self.assertEqual(self.as_(self.boss).post(self.url(self.e, f"/c/{c.key}/delete")).status_code, 200)  # 관리자는 지움
        self.assertEqual(self.as_(self.t42).get(self.url(self.e, f"/f/{f.key}")).status_code, 404)


class CommentFileKindsQA(MoreBase):
    def setUp(self):
        super().setUp()
        self.e = self.ev(self.t41, "사학년협의", "g4")

    def post(self, body, *files):
        return self.as_(self.t42).post(self.url(self.e, "/comments"), {"body": body, "attach": list(files)},
                                       headers={"HX-Request": "true"})

    def test_p4_kinds_accepted(self):
        s = samples()
        for ext in ("xlsx", "docx", "pptx", "xls", "doc", "ppt", "odt", "ods", "odp", "jpg", "png", "gif", "hwp", "hwpx"):
            r = self.post(f"{ext} 자료", up(f"자료.{ext}", s[ext]))
            self.assertEqual(r.status_code, 200, ext)
        self.assertEqual(sorted(EventFile.objects.values_list("kind", flat=True)),
                         sorted(["xlsx", "docx", "pptx", "xls", "doc", "ppt", "odt", "ods", "odp", "jpg", "png", "gif", "hwp", "hwpx"]))

    @mock.patch("today.more.allow", return_value=True)  # 표본 수가 시간당 상한(30)보다 많다
    def test_blocked_fake_macro_rejected_with_guidance(self, _):
        for name, data in BLOCKED + fakes() + macros():
            r = self.post("막힘", up(name, data))
            self.assertEqual(r.status_code, 400, name)
            self.assertContains(r, "「", status_code=400)
        r = self.post("막힘", up("실행.exe", b"MZ\x90\x00"))
        self.assertContains(r, KINDS_TEXT, status_code=400)
        r = self.post("막힘", up("매크로.docx", macros()[4][1]))
        self.assertContains(r, "매크로", status_code=400)
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))

    def test_one_bad_among_good_saves_nothing(self):
        r = self.post("섞임", up("a.pdf"), up("b.exe", b"MZ\x90\x00"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))

    def test_files_without_text_saves_nothing(self):
        r = self.post("   ", up("a.pdf"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))
        self.assertEqual(os.listdir(settings.MEDIA_ROOT), [])

    def test_csrf_still_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.t42.user)
        r = c.post(self.url(self.e, "/comments"), {"body": "x", "attach": [up("a.pdf", PDF)]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((EventComment.objects.count(), EventFile.objects.count()), (0, 0))


COMPOSE = os.path.join(settings.BASE_DIR.parent, "ops", "compose.yml")


class TraefikMatchesAppQA(SimpleTestCase):
    """up101 라우터(Traefik PathRegexp)와 앱 본문 상한이 같은 주소를 MAX_NOTICE_REQUEST_BYTES로 고른다."""

    def router(self):
        text = open(COMPOSE, encoding="utf-8").read()
        m = re.search(r"gt-staging-up101\.rule:.*PathRegexp\(`(.+?)`\)", text)
        self.assertIsNotNone(m)
        return re.compile(m.group(1).replace("$$", "$"))

    def test_same_paths(self):
        rx = self.router()
        paths = ["/s/T/board/new", "/s/T/board/K/edit", "/s/T/today/e/K/files", "/s/T/today/e/K/comments",
                 "/s/T/today/e/K/body", "/s/T/today/e/K/c/C/delete", "/s/T/today/e/K/f/F/delete", "/s/T/today/e/K/more",
                 "/s/T/today/e/K/comments/x", "/s/T/today/e/K/commentsx", "/s/T/today/e/K/files/", "/s/T/hwp/upload"]
        for p in paths:
            app_big = bodylimit._limit(p) == settings.MAX_NOTICE_REQUEST_BYTES
            self.assertEqual(bool(rx.search(p)), app_big, p)
        self.assertTrue(rx.search("/s/T/today/e/K/comments"))
