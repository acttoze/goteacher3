"""I1 공지: 판 관리, 대상, 권한, 첨부, 격리. 확인·현황·쪽지 링크·🔔은 I2."""
import io
import os
import zipfile
from datetime import timedelta
from functools import lru_cache
from unittest import mock

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hwpfiles.models import SourceFile
from hwpfiles.retention import orphan_files, purge
from hwpfiles.tests import sample_hwp, sample_hwpx
from spaces.merge import open_school
from spaces.models import Member, School
from spaces.test_merge import UNTIL, MergeBase

from . import views
from .models import Notice, NoticeFile, NoticeVersion

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def up(name, data=PDF):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


@lru_cache(maxsize=None)
def zip_bomb():
    """hwpx 겉모습에 풀면 201MB인 항목."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", b"application/hwp+zip", zipfile.ZIP_STORED)
        z.writestr("Contents/section0.xml", b"0" * (201 * 1024 * 1024))
    return buf.getvalue()


class Base(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)  # m4 관리자(g4), m4b(g4), m5(g5)
        self.base = f"/s/{self.hub.token}/board"
        self.m4.refresh_from_db()
        self.m4b.refresh_from_db()
        self.m5.refresh_from_db()
        self.u = {"m4": self.u4, "m4b": self.u4b, "m5": self.u5}
        self.client.force_login(self.u4b)

    def as_(self, who):
        c = Client()
        c.force_login(self.u[who])
        return c

    def write(self, client=None, **data):
        form = {"title": "학예회 리허설", "일시": "10/29 2블록", "장소": "강당", "대상": "", "준비물": "",
                "body": "첫 줄\n둘째 줄", "aud": "all", **data}
        return (client or self.client).post(f"{self.base}/new", form)

    def made(self, client=None, **data):
        r = self.write(client, **data)
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        return Notice.objects.order_by("-pk").first()

    def edit(self, n, client=None, base_v=None, **data):
        ver = n.versions.order_by("-v").first()
        form = {"title": ver.fields["제목"], "body": ver.body, "aud": "all" if n.audience == "all" else "groups",
                "groups": [g for g in n.audience.split(",") if g], "base_v": ver.v if base_v is None else base_v,
                **{k: ver.fields.get(k, "") for k in ("일시", "장소", "대상", "준비물")}, **data}
        return (client or self.client).post(f"{self.base}/{n.key}/edit", form)


class WriteTests(Base):
    def test_write_makes_v1_with_writer_and_group(self):
        n = self.made()
        v = n.versions.get()
        self.assertEqual((n.space, n.by, n.by_name, n.group, n.audience, n.must), (self.hub, self.m4b, "정하은", "g4", "all", False))
        self.assertEqual((v.v, v.by, v.body), (1, "정하은", "첫 줄\n둘째 줄"))
        self.assertEqual(v.fields["일시"], "10/29 2블록")

    def test_form_has_student_info_line_and_items(self):
        page = self.client.get(f"{self.base}/new")
        self.assertContains(page, "학생 개인정보")
        for label in ("일시", "장소", "대상", "준비물", "첨부"):
            self.assertContains(page, label)

    def test_title_required_and_limits_keep_values(self):
        r = self.write(title=" ", body="남는 본문")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "남는 본문", status_code=400)
        self.assertEqual(self.write(title="가" * 201).status_code, 400)
        self.assertEqual(self.write(body="가" * 10001).status_code, 400)
        r = self.write(장소="가" * 201)
        self.assertContains(r, "항목은 200자", status_code=400)
        self.assertNotContains(r, "dict_items", status_code=400)
        self.assertEqual(Notice.objects.count(), 0)

    def test_nul_in_text_does_not_break(self):
        self.assertEqual(self.made(title="제목\x00있음").title, "제목있음")

    def test_groups_required_when_group_audience_and_bad_values_dropped(self):
        self.assertEqual(self.write(aud="groups").status_code, 400)
        self.assertEqual(self.write(aud="groups", groups=["hack"]).status_code, 400)
        self.assertEqual(self.made(aud="groups", groups=["g5", "office", "hack"]).audience, ",g5,office,")

    def test_empty_items_hidden_on_detail(self):
        page = self.client.get(f"{self.base}/{self.made().key}/")
        self.assertContains(page, "<dt>일시</dt>")
        self.assertNotContains(page, "<dt>준비물</dt>")
        self.assertNotContains(page, "<dt>대상</dt>")


class VersionTests(Base):
    def test_edit_bumps_version_shows_only_changed_items(self):
        n = self.made()
        self.assertEqual(self.edit(n, 장소="체육관").status_code, 302)
        self.assertEqual(list(n.versions.order_by("v").values_list("v", flat=True)), [1, 2])
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertIn("2차 수정에서 바뀐 부분", page)
        self.assertIn("<del>강당</del>", page)
        self.assertIn("<ins>체육관</ins>", page)
        self.assertNotIn("<del>10/29 2블록</del>", page)
        self.assertNotIn("<ins>10/29 2블록</ins>", page)
        self.assertNotIn("본문 바뀜", page)

    def test_old_version_viewable_by_server_render(self):
        n = self.made()
        self.edit(n, 장소="체육관", title="새 제목", body="새 본문")
        old = self.client.get(f"{self.base}/{n.key}/?v=1").content.decode()
        self.assertIn("강당", old)
        self.assertIn("학예회 리허설", old)
        self.assertIn("첫 줄", old)
        self.assertNotIn("체육관", old)
        self.assertNotIn("새 본문", old)
        self.assertIn("예전 내용", old)
        new = self.client.get(f"{self.base}/{n.key}/?v=2").content.decode()
        self.assertIn("<ins>체육관</ins>", new)
        self.assertIn("새 제목", new)

    def test_bad_version_404(self):
        n = self.made()
        for v in ("9", "abc", "0", "-1", "9" * 30):
            self.assertEqual(self.client.get(f"{self.base}/{n.key}/?v={v}").status_code, 404, v)

    def test_title_change_is_a_change(self):
        n = self.made()
        self.edit(n, title="새 제목")
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertIn("<del>학예회 리허설</del>", page)
        n.refresh_from_db()
        self.assertEqual(n.title, "새 제목")

    def test_body_change_marks_and_line_diff(self):
        n = self.made()
        self.edit(n, body="첫 줄\n고친 줄\n<b>셋째</b>")
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertIn("본문 바뀜", page)
        self.assertIn("<del>둘째 줄</del>", page)
        self.assertIn("<ins>고친 줄</ins>", page)
        self.assertNotIn("<del>첫 줄</del>", page)
        self.assertNotIn("<ins>첫 줄</ins>", page)
        self.assertNotIn("<b>셋째</b>", page)

    def test_no_change_no_version(self):
        n = self.made()
        at = n.updated_at
        self.assertEqual(self.edit(n).status_code, 302)
        n.refresh_from_db()
        self.assertEqual((n.versions.count(), n.updated_at), (1, at))

    def test_audience_change_recorded(self):
        n = self.made()
        self.edit(n, aud="groups", groups=["g5"])
        n.refresh_from_db()
        self.assertEqual(n.audience, ",g5,")
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertIn("<del>전체</del>", page)
        self.assertIn("<ins>5학년</ins>", page)

    def test_editor_recorded_on_version_writer_kept(self):
        n = self.made()
        self.edit(n, self.as_("m4"), 장소="체육관")
        n.refresh_from_db()
        self.assertEqual((n.by, n.versions.get(v=2).by), (self.m4b, "송민재"))

    def test_conflict_later_saver_gets_notice_and_nothing_saved(self):
        n = self.made()
        form = self.client.get(f"{self.base}/{n.key}/edit").content.decode()
        self.assertIn('name="base_v" value="1"', form)
        self.edit(n, self.as_("m4"), 장소="운동장")
        n.refresh_from_db()
        r = self.edit(n, base_v=1, 장소="체육관", body="내 본문")
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, "먼저 고쳤어요", status_code=409)
        self.assertContains(r, "송민재", status_code=409)
        self.assertContains(r, "내 본문", status_code=409)  # 쓴 내용은 그대로
        self.assertContains(r, 'name="base_v" value="2"', status_code=409)
        self.assertEqual(n.versions.count(), 2)
        n.refresh_from_db()
        self.assertEqual(n.versions.get(v=2).fields["장소"], "운동장")
        self.assertEqual(self.edit(n, base_v=2, 장소="체육관", body="내 본문").status_code, 302)  # 확인하고 다시 저장
        self.assertEqual(n.versions.count(), 3)

    def test_xss_escaped_body_only_linebreaks(self):
        evil = "<script>alert(1)</script>"
        n = self.made(title=evil, 장소=evil, body=f"{evil}\n둘째")
        self.edit(n, 장소=f"{evil}2", body=f"{evil}\n셋째")
        for url in (self.base, f"{self.base}/{n.key}/", f"{self.base}/{n.key}/?v=1", f"{self.base}/{n.key}/edit"):
            page = self.client.get(url).content.decode()
            self.assertNotIn("<script>alert", page, url)
            self.assertIn("&lt;script&gt;", page, url)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;<br>둘째", self.client.get(f"{self.base}/{n.key}/?v=1").content.decode())


class AudienceTests(Base):
    def test_audience_filters_list_detail_old_version_and_files(self):
        n = self.made(self.as_("m5"), aud="groups", groups=["g5"], attach=[up("a.pdf")], title="오학년만")
        f = n.files.get()
        self.edit(n, self.as_("m5"), 장소="체육관")
        out = self.client  # m4b는 g4
        self.assertNotContains(out.get(self.base), "오학년만")
        for url in (f"/{n.key}/", f"/{n.key}/?v=1", f"/{n.key}/f/{f.key}", f"/{n.key}/edit"):
            self.assertEqual(out.get(self.base + url).status_code, 404, url)
        for url in (f"/{n.key}/edit", f"/{n.key}/delete"):
            self.assertEqual(out.post(self.base + url, {"title": "x"}).status_code, 404, url)
        self.assertTrue(Notice.objects.filter(pk=n.pk).exists())

    def test_writer_group_member_and_manager_see(self):
        n = self.made(aud="groups", groups=["g5"], title="오학년만")  # m4b가 씀, 자기 그룹 아님
        for c in (self.client, self.as_("m5"), self.as_("m4")):
            self.assertContains(c.get(self.base), "오학년만")
            self.assertEqual(c.get(f"{self.base}/{n.key}/").status_code, 200)

    def test_group_name_inside_other_value_does_not_match(self):
        Member.objects.filter(pk=self.m4b.pk).update(group="g1")
        n = self.made(self.as_("m5"), aud="groups", groups=["g5", "office"])
        self.assertEqual(self.client.get(f"{self.base}/{n.key}/").status_code, 404)

    def test_all_seen_by_everyone(self):
        n = self.made(self.as_("m5"))
        self.assertEqual(self.client.get(f"{self.base}/{n.key}/").status_code, 200)

    def test_writer_who_left_keeps_notice_manager_only_edits(self):
        n = self.made(self.as_("m5"), title="나간 분 공지")
        Member.objects.filter(pk=self.m5.pk).delete()
        n.refresh_from_db()
        self.assertIsNone(n.by)
        self.assertContains(self.client.get(self.base), "김도윤")
        self.assertEqual(self.edit(n, 장소="운동장").status_code, 403)
        self.assertEqual(self.edit(n, self.as_("m4"), 장소="운동장").status_code, 302)


class PermissionTests(Base):
    def test_others_cannot_edit_or_delete_manager_can(self):
        n = self.made(self.as_("m5"))
        self.assertEqual(self.client.get(f"{self.base}/{n.key}/edit").status_code, 403)
        self.assertEqual(self.edit(n, 장소="운동장").status_code, 403)
        self.assertEqual(self.client.post(f"{self.base}/{n.key}/delete").status_code, 403)
        self.assertEqual(n.versions.count(), 1)
        page = self.client.get(f"{self.base}/{n.key}/")
        self.assertNotContains(page, "/edit")
        self.assertNotContains(page, "/delete")
        mgr = self.as_("m4")
        self.assertContains(mgr.get(f"{self.base}/{n.key}/"), "/edit")
        self.assertEqual(self.edit(n, mgr, 장소="운동장").status_code, 302)
        self.assertEqual(mgr.post(f"{self.base}/{n.key}/delete").status_code, 302)
        self.assertFalse(Notice.objects.filter(pk=n.pk).exists())

    def test_writer_can_delete(self):
        n = self.made()
        self.assertEqual(self.client.post(f"{self.base}/{n.key}/delete").status_code, 302)
        self.assertEqual(NoticeVersion.objects.filter(notice_id=n.pk).count(), 0)

    def test_position_text_gives_no_power(self):
        n = self.made(self.as_("m5"))
        Member.objects.filter(pk=self.m4b.pk).update(position="교장", cls="교무실", group="office")
        self.assertEqual(self.edit(n, 장소="운동장").status_code, 403)
        self.assertEqual(self.client.post(f"{self.base}/{n.key}/delete").status_code, 403)

    def test_delete_is_post_only(self):
        n = self.made()
        self.assertEqual(self.client.get(f"{self.base}/{n.key}/delete").status_code, 405)


class IsolationTests(Base):
    def setUp(self):
        super().setUp()
        self.n = self.made(attach=[up("a.pdf")])
        self.far_hub = open_school(self.far_school, UNTIL, self.far_member)
        self.far_c = Client()
        self.far_c.force_login(self.far_user)
        self.far_base = f"/s/{self.far_hub.token}/board"

    def test_other_school_404(self):
        f = self.n.files.get()
        for path in (f"/{self.n.key}/", f"/{self.n.key}/?v=1", f"/{self.n.key}/edit", f"/{self.n.key}/f/{f.key}"):
            self.assertEqual(self.far_c.get(self.far_base + path).status_code, 404, path)  # 내 공간 주소 + 남의 키
            self.assertEqual(self.far_c.get(self.base + path).status_code, 404, path)      # 남의 공간 주소
        self.assertEqual(self.far_c.post(f"{self.far_base}/{self.n.key}/delete").status_code, 404)
        self.assertEqual(self.far_c.post(f"{self.far_base}/{self.n.key}/edit", {"title": "x"}).status_code, 404)
        self.assertNotContains(self.far_c.get(self.far_base), self.n.title)

    def test_file_of_other_notice_404(self):
        other = self.made(title="다른 공지")
        f = self.n.files.get()
        self.assertEqual(self.client.get(f"{self.base}/{other.key}/f/{f.key}").status_code, 404)

    def test_grade_space_has_no_board(self):
        far = School.objects.create(neis_atpt="C10", neis_code="1", name="동학년초")
        sp = far.spaces.create(grade=1, name="1학년")
        Member.objects.create(space=sp, user=self.far_user, name="다른학교", cls="1-1")
        base = f"/s/{sp.token}/board"
        for path in ("", "/new", f"/{self.n.key}/"):
            self.assertEqual(self.far_c.get(base + path).status_code, 404, path)
        self.assertNotContains(self.far_c.get(f"/s/{sp.token}/today"), f"/s/{sp.token}/board")

    def test_anonymous_sent_to_login(self):
        self.assertEqual(Client().get(self.base).status_code, 302)
        f = self.n.files.get()
        self.assertEqual(Client().get(f"{self.base}/{self.n.key}/f/{f.key}").status_code, 302)


class ExpiredTests(Base):
    def setUp(self):
        super().setUp()
        self.n = self.made(attach=[up("a.pdf")])
        self.school.paid_until = timezone.localdate() - timedelta(days=1)
        self.school.save(update_fields=["paid_until"])

    def test_read_200_write_403(self):
        f = self.n.files.get()
        for path in ("", f"/{self.n.key}/", f"/{self.n.key}/?v=1", f"/{self.n.key}/f/{f.key}"):
            self.assertEqual(self.client.get(self.base + path).status_code, 200, path)
        self.assertEqual(self.write().status_code, 403)
        self.assertEqual(self.edit(self.n, 장소="운동장").status_code, 403)
        self.assertEqual(self.client.post(f"{self.base}/{self.n.key}/delete").status_code, 403)
        self.assertEqual((Notice.objects.count(), self.n.versions.count()), (1, 1))

    def test_write_buttons_hidden(self):
        self.assertNotContains(self.client.get(self.base), f"{self.base}/new")
        self.assertNotContains(self.client.get(f"{self.base}/{self.n.key}/"), "/edit")


class AttachTests(Base):
    def assertRejected(self, f, text=""):
        before = NoticeFile.objects.count()
        r = self.write(attach=[f])
        self.assertEqual(r.status_code, 400, f.name)
        if text:
            self.assertContains(r, text, status_code=400)
        self.assertEqual(NoticeFile.objects.count(), before)
        self.assertEqual(Notice.objects.count(), 0)

    def test_real_files_accepted(self):
        n = self.made(attach=[up("계획.pdf"), up("명단.hwpx", sample_hwpx()), up("양식.hwp", sample_hwp())])
        self.assertEqual(sorted(n.files.values_list("kind", flat=True)), ["hwp", "hwpx", "pdf"])
        for f in n.files.all():
            self.assertNotIn("계획", f.file.name)  # 저장 경로에 올린 이름을 쓰지 않는다
            self.assertTrue(f.file.name.startswith(f"notice/{self.hub.pk}/"))
            self.assertEqual(f.stored, f.file.size)

    def test_forged_extension_rejected(self):
        self.assertRejected(up("공문.pdf", b"MZ\x90\x00 exe"), "PDF")
        self.assertRejected(up("공문.hwp", b"MZ\x90\x00 exe"))
        self.assertRejected(up("공문.hwpx", b"PK\x05\x06" + b"\0" * 18))
        self.assertRejected(up("공문.hwp", PDF))                  # 내용은 PDF, 이름은 hwp
        self.assertRejected(up("공문.pdf", sample_hwpx()))        # 내용은 hwpx, 이름은 pdf
        self.assertRejected(up("공문.exe", PDF))                  # 내용은 PDF, 허용 안 되는 이름
        self.assertRejected(up("공문", PDF))

    def test_non_hwp_ole_and_zip_rejected(self):
        self.assertRejected(up("공문.hwp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600))  # doc·xls 겉모습
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", "<a/>")
        self.assertRejected(up("공문.hwpx", buf.getvalue()))     # docx 겉모습

    def test_zip_bomb_rejected(self):
        self.assertRejected(up("폭탄.hwpx", zip_bomb()), "안전하지")
        self.assertEqual(os.path.isdir(os.path.join(settings.MEDIA_ROOT, "notice")), False)

    def test_too_big_rejected(self):
        self.assertRejected(up("큰.pdf", b"%PDF-" + b"0" * (20 * 1024 * 1024 - 4)), "20MB")

    def test_exactly_20mb_ok(self):
        self.assertEqual(self.write(attach=[up("딱.pdf", b"%PDF-" + b"0" * (20 * 1024 * 1024 - 5))]).status_code, 302)

    def test_empty_file_rejected(self):
        self.assertRejected(up("빈.pdf", b""))

    def test_count_limit_new_and_edit(self):
        r = self.write(attach=[up(f"{i}.pdf") for i in range(6)])
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "5개", status_code=400)
        n = self.made(attach=[up(f"{i}.pdf") for i in range(5)])
        self.assertEqual(self.edit(n, attach=[up("6.pdf")]).status_code, 400)
        self.assertEqual((n.files.count(), n.versions.count()), (5, 1))
        drop = n.files.first()
        self.assertEqual(self.edit(n, attach=[up("6.pdf")], remove=[drop.key]).status_code, 302)  # 하나 빼면 들어감
        self.assertEqual(n.files.count(), 5)

    def test_change_recorded_as_version_and_old_file_removed(self):
        n = self.made(attach=[up("옛.pdf")])
        old = n.files.get()
        path = old.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.edit(n, attach=[up("새.pdf")], remove=[old.key]).status_code, 302)
        self.assertEqual(n.versions.count(), 2)
        self.assertEqual(n.versions.get(v=1).fields["첨부"], "옛.pdf")
        self.assertEqual(n.versions.get(v=2).fields["첨부"], "새.pdf")
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertIn("<del>옛.pdf</del>", page)
        self.assertIn("<ins>새.pdf</ins>", page)
        self.assertFalse(default_storage.exists(path))

    def test_remove_key_of_other_notice_ignored(self):
        other = self.made(attach=[up("남.pdf")], title="남의 것")
        n = self.made()
        self.edit(n, remove=[other.files.get().key], 장소="운동장")
        self.assertEqual(other.files.count(), 1)

    def test_download_headers(self):
        n = self.made(attach=[up('한글 "이름"; x.pdf')])
        f = n.files.get()
        r = self.client.get(f"{self.base}/{n.key}/f/{f.key}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), PDF)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertNotIn("\n", r["Content-Disposition"])
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", r["Content-Security-Policy"])

    def test_missing_file_on_disk_404(self):
        n = self.made(attach=[up("a.pdf")])
        f = n.files.get()
        default_storage.delete(f.file.name)
        self.assertEqual(self.client.get(f"{self.base}/{n.key}/f/{f.key}").status_code, 404)

    def test_quota_counts_attachments_and_rejects(self):
        before = SourceFile.used_bytes(self.hub)
        self.made(attach=[up("b.pdf")])
        self.assertEqual(SourceFile.used_bytes(self.hub), before + len(PDF))
        with override_settings(HWP_SCHOOL_QUOTA=SourceFile.used_bytes(self.hub) + 1):
            r = self.write(attach=[up("c.pdf")])
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "저장 공간", status_code=400)
        with override_settings(HWP_TOTAL_QUOTA=SourceFile.used_bytes() + 1):
            self.assertEqual(self.write(attach=[up("c.pdf")]).status_code, 400)

    def test_upload_rate_limited(self):
        with mock.patch.object(views, "UPLOAD_PER_HOUR", 1):
            self.assertEqual(self.write(attach=[up("a.pdf")]).status_code, 302)
            self.assertEqual(self.write(attach=[up("b.pdf")]).status_code, 429)
            self.assertEqual(self.write().status_code, 302)  # 첨부 없는 글은 세지 않는다

    def test_delete_notice_removes_file(self):
        n = self.made(attach=[up("a.pdf")])
        path = n.files.get().file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"{self.base}/{n.key}/delete")
        self.assertFalse(default_storage.exists(path))


class SameNameTests(Base):
    def test_same_name_swap_listed_as_change_key_not_on_screen(self):
        n = self.made(attach=[up("계획.pdf", PDF + b"old")])
        old = n.files.get()
        self.edit(n, attach=[up("계획.pdf", PDF + b"new")], remove=[old.key])
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertIn("<del>계획.pdf</del>", page)
        self.assertIn("<ins>계획.pdf</ins>", page)
        self.assertNotIn(old.key, page)

    def test_unchanged_file_no_version(self):
        n = self.made(attach=[up("계획.pdf")])
        self.edit(n)
        self.assertEqual(n.versions.count(), 1)


class OldVersionFilesTests(Base):
    def test_old_version_names_gone_as_text_kept_as_link_new_hidden(self):
        n = self.made(attach=[up("남음.pdf"), up("없어짐.pdf")])
        keep, gone = n.files.order_by("pk")
        self.edit(n, attach=[up("새것.pdf")], remove=[gone.key])
        old = self.client.get(f"{self.base}/{n.key}/?v=1").content.decode()
        self.assertIn("그때 첨부", old)
        self.assertIn("없어짐.pdf", old)
        self.assertIn("지금은 없음", old)
        self.assertNotIn(gone.key, old)
        self.assertIn(f"/f/{keep.key}", old)
        self.assertNotIn("새것.pdf", old.split("<aside")[1].split("고친 기록")[0])
        new = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertNotIn("지금은 없음", new)
        self.assertNotIn("그때 첨부", new)
        self.assertEqual(new.count("/f/"), 2)


class BodyGuardTests(Base):
    def big(self, url):
        with mock.patch("django.http.multipartparser.MultiPartParser.parse") as parse:
            r = self.client.post(url, data=b"x", content_type="multipart/form-data; boundary=b",
                                 CONTENT_LENGTH=str(views.MAX_REQUEST + 1))
        parse.assert_not_called()
        return r

    def test_declared_too_big_rejected_before_reading_body(self):
        n = self.made()
        for url in (f"{self.base}/new", f"{self.base}/{n.key}/edit"):
            r = self.big(url)
            self.assertContains(r, "100MB", status_code=400)
        self.assertEqual((Notice.objects.count(), n.versions.count()), (1, 1))

    def test_too_big_still_respects_gates(self):
        n = self.made(self.as_("m5"), aud="groups", groups=["g5"])
        self.assertEqual(self.big(f"{self.base}/{n.key}/edit").status_code, 404)
        other = self.made(self.as_("m5"))
        self.assertEqual(self.big(f"{self.base}/{other.key}/edit").status_code, 403)
        self.school.paid_until = timezone.localdate() - timedelta(days=1)
        self.school.save(update_fields=["paid_until"])
        self.assertEqual(self.big(f"{self.base}/new").status_code, 403)

    def test_csrf_token_works_through_guard(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u4b)
        page = c.get(f"{self.base}/new")
        token = str(page.context["csrf_token"])
        form = {"title": "토큰", "body": "", "aud": "all", "csrfmiddlewaretoken": token}
        self.assertEqual(c.post(f"{self.base}/new", form).status_code, 302)
        form.pop("csrfmiddlewaretoken")
        self.assertEqual(c.post(f"{self.base}/new", form).status_code, 403)

    def test_broken_multipart_shows_guidance(self):
        boundary = b"bb"
        body = (b"--bb\r\nContent-Disposition: form-data; name=\"attach\"; filename=\"" + "가".encode() * 1000
                + b".pdf\"\r\n\r\nx\r\n--bb--\r\n")
        r = self.client.generic("POST", f"{self.base}/new", body, content_type="multipart/form-data; boundary=" + boundary.decode())
        self.assertContains(r, "파일 이름이 너무 길거나", status_code=400)


class PurgeTests(Base):
    def test_stored_size_corrected_for_attachment(self):
        n = self.made(attach=[up("a.pdf")])
        NoticeFile.objects.filter(notice=n).update(stored=999)
        purge(timezone.now())
        self.assertEqual(n.files.get().stored, len(PDF))

    def test_referenced_kept_orphan_removed(self):
        n = self.made(attach=[up("a.pdf")])
        path = n.files.get().file.name
        default_storage.save(f"notice/{self.hub.pk}/orphan.pdf", io.BytesIO(b"x"))
        later = timezone.now() + timedelta(hours=2)
        self.assertEqual(orphan_files(later), [f"notice/{self.hub.pk}/orphan.pdf"])
        purge(later)
        self.assertTrue(default_storage.exists(path))
        self.assertFalse(default_storage.exists(f"notice/{self.hub.pk}/orphan.pdf"))

    def test_purge_does_not_expire_notices(self):
        n = self.made(attach=[up("a.pdf")])
        Notice.objects.filter(pk=n.pk).update(created_at=timezone.now() - timedelta(days=4000),
                                              updated_at=timezone.now() - timedelta(days=4000))
        purge(timezone.now())
        self.assertEqual(n.files.count(), 1)


class ListTests(Base):
    def seed(self, count):
        Notice.objects.all().delete()
        for i in range(count):
            n = Notice.objects.create(space=self.hub, title=f"공지{i:02d}", by=self.m5, by_name="김도윤", group="g5")
            NoticeVersion.objects.create(notice=n, v=1, fields={"제목": n.title}, body="", by="김도윤")
            NoticeVersion.objects.create(notice=n, v=2, fields={"제목": n.title}, body="", by="김도윤")

    def test_row_shows_version_writer_group(self):
        self.seed(1)
        page = self.client.get(self.base)
        self.assertContains(page, "2차 수정")
        self.assertContains(page, "5학년 김도윤")
        self.assertContains(page, "공지00")

    def test_query_count_independent_of_rows(self):
        counts = []
        for n in (2, 50):
            self.seed(n)
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(self.client.get(self.base).status_code, 200)
            counts.append(len(q))
        self.assertEqual(counts[0], counts[1])

    def test_pagination(self):
        self.seed(25)
        first = self.client.get(self.base)
        self.assertContains(first, "공지24")
        self.assertNotContains(first, "공지04")
        self.assertContains(first, "?page=2")
        second = self.client.get(f"{self.base}?page=2")
        self.assertContains(second, "공지04")
        self.assertNotContains(second, "공지24")
        self.assertEqual(self.client.get(f"{self.base}?page=abc").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}?page=999").status_code, 200)

    def test_empty(self):
        page = self.client.get(self.base)
        self.assertContains(page, "아직 올라온 공지가 없어요")
        self.assertContains(page, ">공지")
