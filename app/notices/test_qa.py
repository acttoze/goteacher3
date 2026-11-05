"""I1 QA가 더한 경계. 구현자 테스트(notices.tests)와 겹치지 않는 것만.

- 파일 이름은 테스트 클라이언트가 고쳐 주지 않도록 멀티파트 본문을 직접 만들어 보낸다.
- 비교 기준: meeting/32_tasks_r2.md I1 "고치면 판이 오르고 판별로 볼 수 있음", 리드 지시의 경계 목록.
"""
import io
import os
import struct
import time
import zipfile
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hwpfiles.models import SourceFile
from hwpfiles.tests import sample_hwp, sample_hwpx
from spaces.models import Member, Space

from .models import Notice, NoticeFile, NoticeVersion
from .tests import PDF, Base, up
from .views import _body_diff


def raw_multipart(fields, files):
    """(본문, content_type). files = [(이름 바이트, 내용)]. 이름을 바이트 그대로 넣는다."""
    b = b"----qaBOUNDARY"
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(b"--" + b + b'\r\nContent-Disposition: form-data; name="' + k.encode() + b'"\r\n\r\n' + v.encode() + b"\r\n")
    for name, data in files:
        out.write(b"--" + b + b'\r\nContent-Disposition: form-data; name="attach"; filename="' + name + b'"\r\n'
                  b"Content-Type: application/octet-stream\r\n\r\n" + data + b"\r\n")
    out.write(b"--" + b + b"--\r\n")
    return out.getvalue(), "multipart/form-data; boundary=" + b.decode()


class QaBase(Base):
    def post_raw(self, name, data=PDF, **fields):
        body, ctype = raw_multipart({"title": "이름 시험", "body": "", "aud": "all", **fields}, [(name, data)])
        return self.client.generic("POST", f"{self.base}/new", body, content_type=ctype)


class FileNameTests(QaBase):
    def saved(self, raw):
        r = self.post_raw(raw)
        self.assertEqual(r.status_code, 302, raw)
        f = NoticeFile.objects.order_by("-pk").first()
        self.assertRegex(f.file.name, rf"^notice/{self.hub.pk}/[\w-]+\.pdf$")  # 경로에 올린 이름 없음
        real = os.path.realpath(f.file.path)
        self.assertTrue(real.startswith(os.path.realpath(settings.MEDIA_ROOT)), real)
        return f

    def test_path_in_name_keeps_only_last_part(self):
        for raw in (b"../../../etc/passwd.pdf", b"..\\..\\windows\\evil.pdf", b"/abs/path/a.pdf", b"C:\\x\\a.pdf"):
            self.assertNotRegex(self.saved(raw).name, r"[/\\]|\.\.", raw)

    def test_control_chars_removed_from_name_and_header(self):
        f = self.saved("a\x01b\x7fc\x1b.pdf".encode())
        self.assertEqual(f.name, "abc.pdf")
        r = self.client.get(f"{self.base}/{f.notice.key}/f/{f.key}")
        self.assertNotRegex(r["Content-Disposition"], r"[\x00-\x1f\x7f]")

    def test_header_injection_name(self):
        # 줄바꿈은 멀티파트 머리에서 잘리므로 URL 인코딩·따옴표·세미콜론으로 머리를 깨 본다.
        for raw in (b'a"; filename="evil.exe.pdf', "x%0d%0aSet-Cookie: a=1.pdf".encode()):
            r = self.post_raw(raw)
            self.assertEqual(r.status_code, 302, raw)
            f = NoticeFile.objects.order_by("-pk").first()
            d = self.client.get(f"{self.base}/{f.notice.key}/f/{f.key}")
            self.assertTrue(d["Content-Disposition"].startswith("attachment"))
            self.assertNotIn("\r", d["Content-Disposition"])
            self.assertNotIn("\n", d["Content-Disposition"])
            self.assertNotIn("Set-Cookie", d.headers)

    def test_long_name_keeps_extension_and_fits_column(self):
        f = self.saved(("가" * 300 + ".pdf").encode())  # 머리 1024바이트 안쪽. 실제 파일 시스템 이름 한도(255)보다 길다
        self.assertLessEqual(len(f.name), 200)
        self.assertTrue(f.name.endswith(".pdf"))
        self.assertEqual(self.client.get(f"{self.base}/{f.notice.key}/f/{f.key}").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/{f.notice.key}/").status_code, 200)

    def test_name_longer_than_multipart_header_limit_is_400_and_saves_nothing(self):
        self.assertEqual(self.post_raw(("가" * 1000 + ".pdf").encode()).status_code, 400)
        self.assertEqual((Notice.objects.count(), NoticeFile.objects.count()), (0, 0))

    def test_bidi_override_cannot_fake_extension(self):
        """‮ 로 '.pdf'를 '.exe'처럼 보이게 하는 이름: 화면에 그 글자가 남으면 안 된다."""
        f = self.saved("계획‮exe.pdf".encode())
        self.assertNotIn("‮", f.name)

    def test_only_dots_or_no_stem_rejected(self):
        for raw in (b".pdf", b"..pdf.", b"a.pdf.", b"a.pdf.exe", b"a.exe", b"a.html", b"a.svg"):
            self.assertEqual(self.post_raw(raw).status_code, 400, raw)
        self.assertEqual(NoticeFile.objects.count(), 0)

    def test_uppercase_extension_ok(self):
        self.assertEqual(self.post_raw(b"A.PDF").status_code, 302)
        self.assertEqual(NoticeFile.objects.get().kind, "pdf")

    def test_xss_in_file_name_escaped_everywhere(self):
        evil = "<img src=x onerror=alert(1)>.pdf"
        f = self.saved(evil.encode())
        n = f.notice
        for url in (f"{self.base}/{n.key}/", f"{self.base}/{n.key}/edit"):
            page = self.client.get(url).content.decode()
            self.assertNotIn("<img src=x", page, url)
            self.assertIn("&lt;img src=x", page, url)
        self.edit(n, attach=[up("둘째.pdf")])
        page = self.client.get(f"{self.base}/{n.key}/?v=2").content.decode()
        self.assertNotIn("<img src=x", page)


class FileContentTests(QaBase):
    def lying_zip(self, size):
        """중앙 디렉터리의 '풀면 크기'만 size로 속인 hwpx."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", b"application/hwp+zip", zipfile.ZIP_STORED)
            z.writestr("Contents/section0.xml", b"<a/>")
        raw = bytearray(buf.getvalue())
        i = raw.rfind(b"PK\x01\x02")  # 마지막 항목(section0)의 중앙 디렉터리
        raw[i + 24:i + 28] = struct.pack("<I", size)
        return bytes(raw)

    def test_forged_declared_size_rejected(self):
        self.assertEqual(self.write(attach=[up("거짓.hwpx", self.lying_zip(0xFFFFFFFE))]).status_code, 400)
        self.assertEqual(NoticeFile.objects.count(), 0)

    def test_too_many_zip_entries_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mimetype", b"application/hwp+zip")
            z.writestr("Contents/a.xml", b"<a/>")
            for i in range(20000):
                z.writestr(f"f{i}", b"")
        self.assertEqual(self.write(attach=[up("많음.hwpx", buf.getvalue())]).status_code, 400)

    def test_one_bad_file_rejects_all_and_saves_nothing(self):
        r = self.write(attach=[up("좋음.pdf"), up("나쁨.pdf", b"MZ exe")])
        self.assertEqual(r.status_code, 400)
        self.assertEqual((Notice.objects.count(), NoticeFile.objects.count()), (0, 0))
        self.assertFalse(os.path.isdir(os.path.join(settings.MEDIA_ROOT, "notice")))

    def test_too_many_files_in_request_is_400_not_500(self):
        self.client.raise_request_exception = False
        self.assertEqual(self.write(attach=[up(f"{i}.pdf") for i in range(101)]).status_code, 400)

    def test_duplicate_names_each_download_own_bytes(self):
        n = self.made(attach=[up("같음.pdf", PDF + b"1"), up("같음.pdf", PDF + b"2")])
        got = {b"".join(self.client.get(f"{self.base}/{n.key}/f/{f.key}").streaming_content) for f in n.files.all()}
        self.assertEqual(got, {PDF + b"1", PDF + b"2"})

    def test_download_types(self):
        n = self.made(attach=[up("a.pdf"), up("b.hwp", sample_hwp()), up("c.hwpx", sample_hwpx())])
        want = {"pdf": "application/pdf", "hwp": "application/x-hwp", "hwpx": "application/hwp+zip"}
        for f in n.files.all():
            r = self.client.get(f"{self.base}/{n.key}/f/{f.key}")
            self.assertEqual(r["Content-Type"], want[f.kind])
            self.assertIn("attachment", r["Content-Disposition"])

    def test_html_inside_pdf_is_never_inline(self):
        n = self.made(attach=[up("h.pdf", b"%PDF-<html><script>alert(1)</script>")])
        r = self.client.get(f"{self.base}/{n.key}/f/{n.files.get().key}")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r["Content-Disposition"].startswith("attachment"))
        self.assertIn("no-store", r["Cache-Control"])


class AttachVersionTests(QaBase):
    def test_replacing_file_with_same_name_bumps_version(self):
        """같은 이름의 새 파일로 바꾸면 내용이 달라졌으므로 판이 올라야 한다('고치면 판이 오른다')."""
        n = self.made(attach=[up("계획.pdf", PDF + b"old")])
        old = n.files.get()
        self.assertEqual(self.edit(n, attach=[up("계획.pdf", PDF + b"new")], remove=[old.key]).status_code, 302)
        self.assertEqual(b"".join(self.client.get(f"{self.base}/{n.key}/f/{n.files.get().key}").streaming_content), PDF + b"new")
        self.assertEqual(n.versions.count(), 2)

    def test_edit_error_keeps_files_and_ticks(self):
        n = self.made(attach=[up("남김.pdf")])
        f = n.files.get()
        r = self.edit(n, title=" ", remove=[f.key])
        self.assertEqual(r.status_code, 400)
        self.assertTrue(NoticeFile.objects.filter(pk=f.pk).exists())
        self.assertContains(r, f'value="{f.key}" checked', status_code=400)

    def test_conflict_does_not_touch_files(self):
        n = self.made(attach=[up("남김.pdf")])
        f = n.files.get()
        self.edit(n, 장소="운동장")
        r = self.edit(n, base_v=1, remove=[f.key], attach=[up("새.pdf")])
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, "다시 골라", status_code=409)
        self.assertEqual(list(n.files.values_list("pk", flat=True)), [f.pk])

    def test_save_after_delete_in_other_tab_404(self):
        n = self.made()
        key = n.key
        ver = n.versions.get()
        form = {"title": "x", "body": "", "aud": "all", "base_v": ver.v}
        self.client.post(f"{self.base}/{key}/delete")
        self.assertEqual(self.client.post(f"{self.base}/{key}/edit", form).status_code, 404)

    def test_missing_or_bad_base_v_is_conflict_not_500(self):
        n = self.made()
        for bad in ("", "abc", "0", "999"):
            self.assertEqual(self.edit(n, base_v=bad, 장소="운동장").status_code, 409, bad)
        self.assertEqual(n.versions.count(), 1)

    def test_delete_with_five_files_and_space_cascade_clear_disk(self):
        n = self.made(attach=[up(f"{i}.pdf") for i in range(5)])
        paths = [f.file.name for f in n.files.all()]
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"{self.base}/{n.key}/delete")
        self.assertFalse(any(default_storage.exists(p) for p in paths))
        self.assertEqual(NoticeFile.objects.count(), 0)
        n2 = self.made(attach=[up("x.pdf")])
        path = n2.files.get().file.name
        with self.captureOnCommitCallbacks(execute=True):
            Space.objects.filter(pk=self.hub.pk).delete()
        self.assertFalse(default_storage.exists(path))

    def test_deleted_file_returns_quota(self):
        n = self.made(attach=[up("x.pdf")])
        before = SourceFile.used_bytes(self.hub)
        self.client.post(f"{self.base}/{n.key}/delete")
        self.assertEqual(SourceFile.used_bytes(self.hub), before - len(PDF))


class AccessTests(QaBase):
    def test_removed_member_loses_everything(self):
        n = self.made(self.as_("m5"), attach=[up("a.pdf")])
        f = n.files.get()
        Member.objects.filter(pk=self.m4b.pk).delete()
        for path in ("", "/new", f"/{n.key}/", f"/{n.key}/?v=1", f"/{n.key}/f/{f.key}"):
            self.assertIn(self.client.get(self.base + path).status_code, (302, 404), path)
        self.client.post(f"{self.base}/{n.key}/delete")
        self.assertTrue(Notice.objects.filter(pk=n.pk).exists())

    def test_blank_group_member_sees_only_all(self):
        grp = self.made(self.as_("m5"), aud="groups", groups=["g5"], title="오학년만")
        every = self.made(self.as_("m5"), title="모두")
        Member.objects.filter(pk=self.m4b.pk).update(group="")
        self.assertEqual(self.client.get(f"{self.base}/{grp.key}/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}/{every.key}/").status_code, 200)
        page = self.client.get(self.base)
        self.assertNotContains(page, "오학년만")

    def test_old_grade_token_goes_to_school_board_but_not_past_audience(self):
        n = self.made(self.as_("m5"), aud="groups", groups=["g5"])
        for sp in (self.g4, self.g5):
            r = self.client.get(f"/s/{sp.token}/board/{n.key}/")
            self.assertIn(r.status_code, (301, 302), sp)
            self.assertIn(self.hub.token, r["Location"])
            self.assertEqual(self.client.get(r["Location"]).status_code, 404)

    def test_other_school_old_token_404(self):
        n = self.made()
        c = self.as_("m4b")
        c.force_login(self.far_user)
        self.assertEqual(c.get(f"/s/{self.g4.token}/board/{n.key}/").status_code, 404)

    def test_csrf_enforced_on_write_edit_delete(self):
        n = self.made()
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u4b)
        for url in ("/new", f"/{n.key}/edit", f"/{n.key}/delete"):
            self.assertEqual(c.post(self.base + url, {"title": "x"}).status_code, 403, url)
        self.assertEqual((Notice.objects.count(), n.versions.count()), (1, 1))

    def test_expired_new_form_cannot_submit_and_file_not_saved(self):
        n = self.made()
        self.school.paid_until = timezone.localdate() - timedelta(days=1)
        self.school.save(update_fields=["paid_until"])
        self.assertNotContains(self.client.get(f"{self.base}/new"), "noticeDlg")  # 쓰기 대화상자 없음
        self.assertEqual(self.write(attach=[up("x.pdf")]).status_code, 403)
        self.assertEqual(self.edit(n, attach=[up("x.pdf")]).status_code, 403)
        self.assertEqual(NoticeFile.objects.count(), 0)
        self.assertFalse(os.path.isdir(os.path.join(settings.MEDIA_ROOT, "notice")))


class RenderTests(QaBase):
    def test_limits_at_exact_boundary_save(self):
        n = self.made(title="가" * 200, 장소="나" * 200, body="다" * 10000)
        self.assertEqual((len(n.title), len(n.versions.get().body)), (200, 10000))

    def test_writer_name_xss_escaped(self):
        Member.objects.filter(pk=self.m4b.pk).update(name="<script>alert(9)</script>")
        n = self.made()
        self.edit(n, self.client, 장소="운동장")
        for url in (self.base, f"{self.base}/{n.key}/"):
            self.assertNotIn("<script>alert(9)", self.client.get(url).content.decode(), url)
        r = self.as_("m4").post(f"{self.base}/{n.key}/edit", {"title": "x", "body": "", "aud": "all", "base_v": 1})
        self.assertEqual(r.status_code, 409)  # 충돌 안내에 앞서 고친 사람 이름이 나온다
        self.assertNotIn("<script>alert(9)", r.content.decode())

    def test_xss_in_diff_and_delete_dialog_and_quote_break(self):
        attr = '"><img src=x onerror=alert(2)>'
        n = self.made(title=attr, 장소=attr, body=attr)
        self.edit(n, title=attr + "2", 장소=attr + "2", body=attr + "2")
        for url in (f"{self.base}/{n.key}/", f"{self.base}/{n.key}/edit", f"{self.base}/{n.key}/?v=1", self.base):
            self.assertNotIn("<img src=x", self.client.get(url).content.decode(), url)
        r = self.write(title="", 장소=attr, body=attr)
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("<img src=x", r.content.decode())

    def test_cleared_item_shows_del_only_added_item_ins_only(self):
        n = self.made(준비물="")
        self.edit(n, 장소="", 준비물="실내화")
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertIn("<del>강당</del></dd>", page)
        self.assertIn("<dd><ins>실내화</ins></dd>", page)
        self.assertNotIn("<dt>일시</dt><dd><del>", page)

    def test_blank_lines_in_body_keep_br(self):
        n = self.made(body="첫째\n\n셋째")
        self.assertIn("첫째<br><br>셋째", self.client.get(f"{self.base}/{n.key}/").content.decode())

    def test_old_version_shows_that_versions_audience_and_v3_diff_uses_v2(self):
        n = self.made()
        self.edit(n, 장소="둘째곳", aud="groups", groups=["g4"])
        n.refresh_from_db()
        self.edit(n, 장소="셋째곳")
        v3 = self.client.get(f"{self.base}/{n.key}/?v=3").content.decode()
        self.assertIn("3차 수정에서 바뀐 부분", v3)
        self.assertIn("<del>둘째곳</del>", v3)
        self.assertNotIn("<del>강당</del>", v3)
        v1 = self.client.get(f"{self.base}/{n.key}/?v=1").content.decode()
        self.assertIn("받는 곳 전체", v1)
        self.assertNotIn("→", v1.split("내용</h2>")[0].split('class="grid"')[-1])

    def test_old_version_shows_that_versions_file_names(self):
        """옛 판은 '그때 모습대로' 다시 그린다: 그 판에 붙어 있던 첨부 이름이 보여야 한다."""
        n = self.made(attach=[up("옛날파일.pdf")])
        self.edit(n, attach=[up("새파일.pdf")], remove=[n.files.get().key])
        self.assertContains(self.client.get(f"{self.base}/{n.key}/?v=1"), "옛날파일.pdf")


class ScaleTests(QaBase):
    def seed(self, count):
        for i in range(count):
            aud = "all" if i % 2 else ",g4,"
            n = Notice.objects.create(space=self.hub, title=f"공지{i:02d}", by=self.m5, by_name="김도윤", group="g5", audience=aud)
            NoticeVersion.objects.create(notice=n, v=1, fields={"제목": n.title}, body="", by="김도윤")

    def test_fifty_notices_pages_and_query_budget(self):
        self.seed(50)
        with CaptureQueriesContext(connection) as q:
            first = self.client.get(self.base)
        self.assertEqual(first.status_code, 200)
        self.assertLess(len(q), 20, [x["sql"][:80] for x in q])
        self.assertEqual(first.context["notices"].paginator.num_pages, 3)
        seen = []
        for p in (1, 2, 3):
            seen += [n.title for n in self.client.get(f"{self.base}?page={p}").context["notices"]]
        self.assertEqual(len(seen), 50)
        self.assertEqual(len(set(seen)), 50)  # 쪽을 넘겨도 겹치거나 빠지지 않는다

    def test_detail_query_count_independent_of_versions_and_files(self):
        counts = []
        n = self.made()
        for i in (1, 30):
            for j in range(n.versions.count(), i):
                NoticeVersion.objects.create(notice=n, v=j + 1, fields={"제목": "t"}, body="", by="김")
            with CaptureQueriesContext(connection) as q:
                self.client.get(f"{self.base}/{n.key}/")
            counts.append(len(q))
        self.assertEqual(counts[0], counts[1])

    def test_body_diff_worst_case_is_fast(self):
        a = "\n".join(f"{i:x}" for i in range(2500))
        b = "\n".join(f"{i:x}" for i in range(2500, 5000))
        c = "\n".join("가나" if i % 2 else "다라" for i in range(3300))
        t = time.monotonic()
        self.assertTrue(_body_diff(a[:10000], b[:10000]))
        self.assertTrue(_body_diff(c[:10000], c[:9990] + "끝"))
        self.assertLess(time.monotonic() - t, 1.0)


class EditQuotaTests(QaBase):
    def test_edit_over_quota_rejected_nothing_changed(self):
        n = self.made()
        with self.settings(HWP_SCHOOL_QUOTA=SourceFile.used_bytes(self.hub) + 1):
            r = self.edit(n, 장소="운동장", attach=[up("c.pdf")])
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "저장 공간", status_code=400)
        self.assertEqual((n.versions.count(), n.files.count()), (1, 0))
