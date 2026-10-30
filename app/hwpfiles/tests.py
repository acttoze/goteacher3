"""E2 한글 양식 올리기·변환(큐)·상태.

불변식
- 회원만, 다른 공간의 파일은 404. CSRF 필요, GET 405.
- 형식은 매직 바이트로. 이름만 바꾼 파일·30MB 초과·안전하지 않은 zip은 400(저장 안 함).
- hwp는 크기·시간과 상관없이 큐로 간다(REDIS_URL 없으면 그 자리에서). hwpx는 검사만 하고 바로 완료.
- 변환 실패는 '실패' + 안내. 안내·DB에 문서 내용이 없다.
"""
import io
import os
import re
import shutil
import tempfile
import zipfile
from functools import lru_cache
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import include, path

import hwp
from hwp import engine as E
from spaces.models import Member, School, Space

from . import tasks, views
from .models import SourceFile

urlpatterns = [path("s/<str:space_token>/", include("hwpfiles.urls"))]
S = SourceFile.Status
SECRET = "비밀학생이름"


@lru_cache(maxsize=None)
def sample_hwpx():
    from hwpx.document import HwpxDocument

    d = HwpxDocument.new()
    d.add_paragraph("연수 신청 명단")
    t = d.add_table(3, 2)
    t.set_cell_text(0, 0, "성명")
    t.set_cell_text(1, 0, SECRET)
    return d.to_bytes()


@lru_cache(maxsize=None)
def sample_hwp():
    return E.to_hwp(sample_hwpx())


def upload(name, data):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


@override_settings(ROOT_URLCONF=__name__, REDIS_URL="")
class UploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:a"), User.objects.create(username="google:b")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        o = override_settings(MEDIA_ROOT=self.media)
        o.enable()
        self.addCleanup(o.disable)
        self.client.force_login(self.ua)

    def post(self, f, space=None, client=None):
        url = f"/s/{(space or self.a).token}/hwp/upload"
        return (client or self.client).post(url, {"file": f})

    def status_url(self, sf, space=None):
        return f"/s/{(space or self.a).token}/hwp/{sf.key}/status"

    # ---- 업로드 → 큐 → 완료
    def test_hwp_upload_converts_to_done(self):
        r = self.post(upload("명단.hwp", sample_hwp()))
        self.assertEqual(r.status_code, 200)
        sf = SourceFile.objects.get()
        self.assertEqual((sf.space, sf.created_by, sf.kind, sf.status, sf.name), (self.a, self.ma, "hwp", S.DONE, "명단.hwp"))
        self.assertTrue(sf.converted_at)
        self.assertNotIn("명단", sf.original.name)  # 저장 경로에 올린 이름을 쓰지 않는다
        with sf.hwpx_file().open("rb") as fh:
            hx = fh.read()
        self.assertEqual(hwp.detect(hx), "hwpx")
        self.assertTrue(hwp.candidates(hx) is not None)
        self.assertContains(r, "읽기 끝났어요")
        self.assertNotContains(r, "hx-trigger")  # 끝나면 폴링 멈춤
        r = self.client.get(self.status_url(sf))
        self.assertContains(r, "읽기 끝났어요")

    @override_settings(REDIS_URL="redis://example.invalid:6379/0")
    def test_hwpx_also_goes_to_queue(self):
        """무거운 zip·XML 전수 검사는 웹이 아니라 worker에서(폭탄 hwpx로 앱 컨테이너가 죽지 않게)."""
        with mock.patch("config.queue.get_queue") as gq, mock.patch("hwp.safety.check_zip") as heavy:
            r = self.post(upload("양식.hwpx", sample_hwpx()))
        self.assertEqual(r.status_code, 200)
        heavy.assert_not_called()
        sf = SourceFile.objects.get()
        gq.assert_called_once_with("hwp")
        gq.return_value.enqueue.assert_called_once_with(tasks.convert, sf.pk)
        self.assertEqual((sf.kind, sf.status), ("hwpx", S.PENDING))
        tasks.convert(sf.pk)
        sf.refresh_from_db()
        self.assertEqual((sf.status, sf.hwpx_file(), sf.hwpx.name), (S.DONE, sf.original, ""))

    # ---- 큐 경로: 시간과 상관없이 hwp는 모두 큐로
    @override_settings(REDIS_URL="redis://example.invalid:6379/0")
    def test_hwp_goes_to_queue_and_polls(self):
        with mock.patch("config.queue.get_queue") as gq, mock.patch.object(hwp, "to_hwpx") as conv:
            r = self.post(upload("큰양식.hwp", sample_hwp()))
        conv.assert_not_called()  # 요청 안에서 변환하지 않는다
        sf = SourceFile.objects.get()
        gq.assert_called_once_with("hwp")  # 한글 변환 전용 큐(D3 리마인드와 분리)
        gq.return_value.enqueue.assert_called_once_with(tasks.convert, sf.pk)
        self.assertEqual(sf.status, S.PENDING)
        self.assertContains(r, f'hx-get="/s/{self.a.token}/hwp/{sf.key}/status"')
        self.assertContains(r, 'hx-trigger="every 2s"')
        # worker가 맡으면 완료(6.6초 넘는 변환이어도 같은 경로)
        tasks.convert(sf.pk)
        sf.refresh_from_db()
        self.assertEqual(sf.status, S.DONE)
        tasks.convert(sf.pk)  # 두 번 와도 한 번만
        self.assertNotContains(self.client.get(self.status_url(sf)), "hx-trigger")

    def test_stuck_job_shows_failed(self):
        with override_settings(REDIS_URL="redis://example.invalid:6379/0"), mock.patch("config.queue.get_queue"):
            self.post(upload("a.hwp", sample_hwp()))
        sf = SourceFile.objects.get()
        SourceFile.objects.filter(pk=sf.pk).update(created_at=sf.created_at.replace(year=2000))
        r = self.client.get(self.status_url(sf))
        self.assertContains(r, "너무 오래")
        self.assertEqual(SourceFile.objects.get().status, S.FAILED)

    # ---- 변환 실패
    def test_convert_failure_marks_failed_without_content(self):
        with mock.patch.object(hwp, "to_hwpx", side_effect=hwp.ConvertError(f"hwp 변환 실패({SECRET})")):
            r = self.post(upload("깨진.hwp", sample_hwp()))
        sf = SourceFile.objects.get()
        self.assertEqual((sf.status, sf.error), (S.FAILED, "ConvertError"))
        self.assertContains(r, "변환하지 못했어요")
        self.assertNotContains(r, SECRET)
        self.assertEqual(sf.original.name, "")
        self.assertEqual(os.listdir(os.path.join(self.media, "hwp", str(self.a.pk))), [])

    # ---- 상한: 회원당 진행 중 2건, 공간당 저장 1GB
    @override_settings(REDIS_URL="redis://example.invalid:6379/0")
    def test_in_flight_limit_per_member(self):
        with mock.patch("config.queue.get_queue"):
            for _ in range(2):
                self.assertEqual(self.post(upload("a.hwp", sample_hwp())).status_code, 200)
            r = self.post(upload("a.hwp", sample_hwp()))
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "끝난 뒤", status_code=429)
        self.assertEqual(SourceFile.objects.count(), 2)

    def test_space_quota(self):
        self.post(upload("a.hwp", sample_hwp()))
        used = SourceFile.objects.get().stored
        self.assertGreater(used, len(sample_hwp()))  # 원본 + 변환한 HWPX
        with override_settings(HWP_SPACE_QUOTA=used + len(sample_hwp()) - 1):
            r = self.post(upload("b.hwp", sample_hwp()))
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "저장 공간", status_code=400)
        self.assertEqual(SourceFile.objects.count(), 1)

    def test_service_quota(self):
        self.post(upload("a.hwp", sample_hwp()))
        used = SourceFile.objects.get().stored
        with override_settings(HWP_TOTAL_QUOTA=used + len(sample_hwp()) - 1):
            c = Client()
            c.force_login(self.ub)  # 다른 공간이어도 서비스 전체 합계로
            r = c.post(f"/s/{self.b.token}/hwp/upload", {"file": upload("b.hwp", sample_hwp())})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "서비스 저장 공간", status_code=400)
        self.assertEqual(SourceFile.objects.count(), 1)

    def test_stuck_expire_deletes_files(self):
        with override_settings(REDIS_URL="redis://example.invalid:6379/0"), mock.patch("config.queue.get_queue"):
            self.post(upload("a.hwp", sample_hwp()))
        sf = SourceFile.objects.get()
        SourceFile.objects.filter(pk=sf.pk).update(created_at=sf.created_at.replace(year=2000))
        self.client.get(self.status_url(sf))
        sf.refresh_from_db()
        self.assertEqual((sf.status, sf.original.name, sf.stored), (S.FAILED, "", 0))
        self.assertEqual(os.listdir(os.path.join(self.media, "hwp", str(self.a.pk))), [])

    def test_no_orphan_file_or_row_when_write_fails(self):
        with mock.patch("django.core.files.storage.FileSystemStorage._save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.post(upload("a.hwp", sample_hwp()))
        self.assertFalse(SourceFile.objects.exists())

    def test_unexpected_error_marks_failed_and_reraises(self):
        with mock.patch.object(hwp, "to_hwpx", side_effect=MemoryError), self.assertRaises(MemoryError):
            self.post(upload("a.hwp", sample_hwp()))
        self.assertEqual(SourceFile.objects.get().status, S.FAILED)

    # ---- 거부(400, 저장 안 함)
    def assert_rejected(self, r, text):
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, text, status_code=400)
        self.assertFalse(SourceFile.objects.exists())

    def test_disguised_file_rejected(self):
        self.assert_rejected(self.post(upload("양식.hwp", b"%PDF-1.7 not hwp" * 10)), "한글 파일(.hwp, .hwpx)만")
        self.assert_rejected(self.post(upload("양식.hwpx", b"MZ\x90\x00" * 10)), "한글 파일(.hwp, .hwpx)만")

    def test_unsafe_zip_rejected(self):
        evil = _zip([("mimetype", "application/hwp+zip"), ("Contents/section0.xml", "<a/>"), ("../evil.xml", "<a/>")])
        self.assert_rejected(self.post(upload("양식.hwpx", evil)), "안전하지 않아")

    def test_dtd_hwpx_fails_in_worker_and_original_deleted(self):
        dtd = _zip([("mimetype", "application/hwp+zip"),
                    ("Contents/section0.xml", '<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>')])
        r = self.post(upload("양식.hwpx", dtd))  # 웹은 가벼운 구조 검사만 → 통과, worker가 거부
        self.assertEqual(r.status_code, 200)
        sf = SourceFile.objects.get()
        self.assertEqual((sf.status, sf.error, sf.original.name), (S.FAILED, "UnsafeFile", ""))
        self.assertContains(r, "안전하지 않아")
        self.assertEqual(os.listdir(os.path.join(self.media, "hwp", str(self.a.pk))), [])  # 실패하면 원본 바로 삭제

    def test_over_30mb_rejected(self):
        big = hwp.safety.HWP_MAGIC + b"\0" * (30 * 1024 * 1024)
        self.assert_rejected(self.post(upload("큰.hwp", big)), "30MB 이하")

    def test_declared_length_rejected_before_reading(self):
        url = f"/s/{self.a.token}/hwp/upload"
        r = self.client.generic("POST", url, b"x", CONTENT_TYPE="multipart/form-data; boundary=x",
                                CONTENT_LENGTH=str(40 * 1024 * 1024))
        self.assert_rejected(r, "30MB 이하")

    def test_empty_rejected(self):
        self.assert_rejected(self.client.post(f"/s/{self.a.token}/hwp/upload", {}), "골라 주세요")

    # ---- 공간 격리·인증·CSRF
    def test_other_space_404(self):
        self.post(upload("a.hwp", sample_hwp()))
        sf = SourceFile.objects.get()
        self.assertEqual(self.client.get(self.status_url(sf, self.b)).status_code, 404)  # 회원 아님
        c = Client()
        c.force_login(self.ub)
        self.assertEqual(c.get(self.status_url(sf, self.b)).status_code, 404)  # 내 공간 주소로 남의 파일
        self.assertEqual(c.get(self.status_url(sf)).status_code, 404)  # 남의 공간 주소
        self.assertEqual(c.post(f"/s/{self.a.token}/hwp/upload", {"file": upload("a.hwp", sample_hwp())}).status_code, 404)

    def test_anonymous_redirected(self):
        r = Client().post(f"/s/{self.a.token}/hwp/upload", {"file": upload("a.hwp", sample_hwp())})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(SourceFile.objects.exists())

    def test_csrf_and_method(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        r = c.post(f"/s/{self.a.token}/hwp/upload", {"file": upload("a.hwp", sample_hwp())})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/hwp/upload").status_code, 405)

    def test_report_labels(self):
        sf = SourceFile(report={"unconverted": [], "dropped": ["range-tag-0", "label-item", "foo", "range-tag-1"]})
        self.assertEqual(sf.report_labels(), ["형광펜·교정 부호 범위", "라벨 문서 항목", "기타 요소"])

    def test_upload_limit_matches_engine(self):
        from django.conf import settings

        self.assertEqual(settings.HWP_MAX_UPLOAD, hwp.MAX_UPLOAD)
        self.assertIn("30MB", views.REJECT["TooLarge"])


def _zip(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, d in entries:
            z.writestr(n, d)
    return out.getvalue()


@override_settings(ROOT_URLCONF=__name__, REDIS_URL="")
class QaEdgeTests(UploadTests):
    """QA(E2) 경계 사례. UploadTests의 준비를 그대로 쓰되 그 테스트는 다시 돌리지 않는다."""

    def run(self, result=None):  # 부모 테스트 메서드는 건너뛴다(중복 실행 방지)
        if not self._testMethodName.startswith("test_qa_"):
            return result
        return super().run(result)

    # ---- 매직 바이트 위장: PK로 시작하지만 한글 문서가 아닌 zip
    def test_qa_non_hwpx_zip_rejected(self):
        docx = _zip([("[Content_Types].xml", "<Types/>"), ("word/document.xml", "<w:document xmlns:w='x'/>")])
        for data in (docx, _zip([]), _zip([("a.txt", "hello")])):
            r = self.post(upload("양식.hwpx", data))
            self.assert_rejected(r, "한글 파일(.hwp, .hwpx)만")

    def test_qa_pk_header_garbage_rejected(self):
        self.assert_rejected(self.post(upload("양식.hwpx", b"PK\x03\x04" + b"x" * 100)), "안전하지 않아")

    # ---- 매직 바이트 위장: OLE(.doc·.xls)는 hwp와 첫 8바이트가 같다
    def test_qa_ole_non_hwp_rejected(self):
        doc = hwp.safety.HWP_MAGIC + b"\0" * 500
        self.assert_rejected(self.post(upload("양식.hwp", doc)), "한글 파일(.hwp, .hwpx)만")

    # ---- 크기 경계: 정확히 30MB는 받고(형식 판정으로 넘어감), 1바이트 넘으면 거부
    def test_qa_size_boundary(self):
        limit = hwp.MAX_UPLOAD
        over = hwp.safety.HWP_MAGIC + b"\0" * (limit + 1 - 8)
        self.assert_rejected(self.post(upload("큰.hwp", over)), "30MB 이하")
        exact = b"PK" + b"\0" * (limit - 2)
        r = self.post(upload("딱.hwpx", exact))
        self.assertNotContains(r, "30MB 이하", status_code=r.status_code)

    def test_qa_declared_length_just_over(self):
        url = f"/s/{self.a.token}/hwp/upload"
        r = self.client.generic("POST", url, b"x", CONTENT_TYPE="multipart/form-data; boundary=x",
                                CONTENT_LENGTH=str(hwp.MAX_UPLOAD + 1024 * 1024 + 1))
        self.assert_rejected(r, "30MB 이하")

    # ---- 멈춘 작업: 600초(작업 상한 2배) 경계, 변환 중도 실패로, 완료는 그대로
    def _make(self, status, age):
        from datetime import timedelta

        from django.utils import timezone

        with override_settings(REDIS_URL="redis://example.invalid:6379/0"), mock.patch("config.queue.get_queue"):
            self.post(upload("a.hwp", sample_hwp()))
        sf = SourceFile.objects.latest("id")
        SourceFile.objects.filter(pk=sf.pk).update(status=status, created_at=timezone.now() - timedelta(seconds=age))
        return sf

    def test_qa_stuck_boundary(self):
        young = self._make(S.PENDING, 590)
        self.assertContains(self.client.get(self.status_url(young)), 'hx-trigger="every 2s"')
        self.assertEqual(SourceFile.objects.get(pk=young.pk).status, S.PENDING)
        conv = self._make(S.CONVERTING, 610)
        self.assertContains(self.client.get(self.status_url(conv)), "너무 오래")
        self.assertEqual(SourceFile.objects.get(pk=conv.pk).error, "Timeout")
        done = self._make(S.DONE, 10_000)
        self.client.get(self.status_url(done))
        self.assertEqual(SourceFile.objects.get(pk=done.pk).status, S.DONE)

    def test_qa_late_worker_after_expire_does_nothing(self):
        sf = self._make(S.PENDING, 700)
        self.client.get(self.status_url(sf))
        tasks.convert(sf.pk)  # 늦게 온 작업은 실패 상태를 덮지 않는다
        self.assertEqual(SourceFile.objects.get(pk=sf.pk).status, S.FAILED)

    # ---- 다른 공간: 남의 파일 키를 내 공간 주소에 끼워도 404(같은 학교 다른 동학년·다른 학교)
    def test_qa_other_school_404(self):
        self.post(upload("a.hwp", sample_hwp()))
        sf = SourceFile.objects.get()
        other = School.objects.create(neis_atpt="B10", neis_code="8888888", name="다른초")
        sp = Space.objects.create(school=other, grade=4, name="4학년")
        u = get_user_model().objects.create(username="google:c")
        Member.objects.create(space=sp, name="최하늘", cls="4-1", user=u)
        c = Client()
        c.force_login(u)
        self.assertEqual(c.get(self.status_url(sf, sp)).status_code, 404)
        self.assertEqual(c.get(self.status_url(sf)).status_code, 404)
        self.assertEqual(c.get(f"/s/{sp.token}/hwp/{sf.key[:-1]}x/status").status_code, 404)


@override_settings(ROOT_URLCONF=__name__, REDIS_URL="")
class PreviewTests(UploadTests):
    """E4 쪽 미리보기·HWPX 내려주기. 파일은 이 뷰로만, 회원·같은 공간·완료된 것만, 캐시 금지."""

    def run(self, result=None):
        if not self._testMethodName.startswith("test_pv_"):
            return result
        return super().run(result)

    def _done(self, name="명단.hwp", data=None):
        self.post(upload(name, data or sample_hwp()))
        return SourceFile.objects.get(name=name)

    def pv(self, sf, space=None):
        return f"/s/{(space or self.a).token}/hwp/{sf.key}/"

    def dl(self, sf, space=None):
        return f"/s/{(space or self.a).token}/hwp/{sf.key}/hwpx"

    def assert_no_cache(self, r):
        cc = r["Cache-Control"]
        for part in ("no-store", "no-cache", "private", "max-age=0"):
            self.assertIn(part, cc)

    def test_pv_hwpx_download_member_only(self):
        sf = self._done()
        r = self.client.get(self.dl(sf))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/hwp+zip")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn(".hwpx", r["Content-Disposition"])
        self.assert_no_cache(r)
        body = b"".join(r.streaming_content)
        with sf.hwpx_file().open("rb") as fh:
            self.assertEqual(body, fh.read())
        self.assertEqual(hwp.detect(body), "hwpx")

    def test_pv_original_hwpx_served_as_is(self):
        sf = self._done("양식.hwpx", sample_hwpx())
        r = self.client.get(self.dl(sf))
        self.assertEqual(b"".join(r.streaming_content), sample_hwpx())

    def test_pv_isolation(self):
        sf = self._done()
        for url in (self.dl, self.pv):
            self.assertEqual(self.client.get(url(sf, self.b)).status_code, 404)  # 회원 아닌 공간
            c = Client()
            c.force_login(self.ub)
            self.assertEqual(c.get(url(sf, self.b)).status_code, 404)  # 내 공간 주소에 남의 파일 키
            self.assertEqual(c.get(url(sf)).status_code, 404)  # 남의 공간 주소
            self.assertEqual(Client().get(url(sf)).status_code, 302)  # 로그인 안 함
            self.assertEqual(self.client.post(url(sf)).status_code, 405)

    def test_pv_not_done_or_failed_404(self):
        sf = self._done()
        for st in (S.PENDING, S.CONVERTING):
            SourceFile.objects.filter(pk=sf.pk).update(status=st)
            self.assertEqual(self.client.get(self.dl(sf)).status_code, 404)
        sf.refresh_from_db()
        sf.discard("ConvertError")
        self.assertEqual(self.client.get(self.dl(sf)).status_code, 404)
        r = self.client.get(self.pv(sf))
        self.assertContains(r, "그릴 파일이 없어요")

    def test_pv_page_tables_outline_and_csp(self):
        sf = self._done()
        self.assertEqual([(t["rows"], t["cols"], t["rank"]) for t in sf.report["tables"]], [(3, 2, 0)])
        r = self.client.get(self.pv(sf))
        self.assertEqual(r.status_code, 200)
        self.assert_no_cache(r)
        csp = r["Content-Security-Policy"]
        self.assertIn("script-src 'self' 'wasm-unsafe-eval'", csp)
        self.assertNotIn("http", csp.replace(views.FONT_SRC, ""))  # 외부 출처는 글꼴 경로 하나뿐
        self.assertContains(r, f'data-src="/s/{self.a.token}/hwp/{sf.key}/hwpx"')
        self.assertContains(r, "hwp/preview.js")
        self.assertContains(r, '"rows": 3')
        self.assertNotContains(r, SECRET)  # 문서 글자는 화면 HTML에 싣지 않는다
        self.assertNotContains(r, "연수 신청")
        self.assertNotIn(SECRET, repr(sf.report))

    def test_pv_busy_page_polls(self):
        with override_settings(REDIS_URL="redis://example.invalid:6379/0"), mock.patch("config.queue.get_queue"):
            self.post(upload("a.hwp", sample_hwp()))
        sf = SourceFile.objects.get()
        r = self.client.get(self.pv(sf))
        self.assertContains(r, 'hx-trigger="every 2s"')
        self.assertContains(r, "쪽 미리보기가 나와요")

    def test_pv_done_but_file_missing_404(self):
        sf = self._done()
        os.remove(sf.hwpx_file().path)  # 디스크에서 사라진 파일(볼륨 복구 실수 등): 500이 아니라 404
        self.assertEqual(self.client.get(self.dl(sf)).status_code, 404)

    def test_pv_report_tables_keep_section_number_only(self):
        sf = self._done()
        for t in sf.report["tables"]:
            self.assertNotIn("section", t)
        self.assertNotIn("Contents/", repr(sf.report))
        self.assertEqual(sf.report["tables"][0]["sec"], 0)

    def test_pv_media_not_public(self):
        from django.conf import settings

        sf = self._done()
        self.assertEqual(self.client.get(f"{settings.MEDIA_URL}{sf.original.name}").status_code, 404)


class PreviewQaTests(PreviewTests):
    """E4 QA가 더한 경계. CSP 외부 출처 범위, 고정 사본 무결성, WASM 응답 형식, E4 이전에 변환된 파일."""

    def run(self, result=None):
        if not self._testMethodName.startswith("test_pvqa_"):
            return result
        return super(PreviewTests, self).run(result)

    def test_pvqa_csp_font_only_in_style_and_font(self):
        from django.conf import settings

        sf = self._done()
        csp = dict(d.strip().split(" ", 1) for d in self.client.get(self.pv(sf))["Content-Security-Policy"].split(";"))
        for name, value in csp.items():
            if name in ("style-src", "font-src"):
                self.assertIn(views.FONT_SRC, value)
                self.assertNotIn("http", value.replace(views.FONT_SRC, ""))
            else:
                self.assertNotIn("http", value, name)  # 스크립트·연결·이미지는 자기 출처만
        self.assertNotIn("'unsafe-eval'", csp["script-src"])
        # gt.css가 실제로 읽는 글꼴 주소가 허용 경로 아래에 있어야 콘솔 오류 없이 글꼴이 뜬다
        css = (settings.STATICFILES_DIRS[0] / "css" / "gt.css").read_text()
        imports = re.findall(r'@import url\("(https?://[^"]+)"\)', css)
        self.assertTrue(imports)
        for url in imports:
            self.assertTrue(url.startswith(views.FONT_SRC), url)

    def test_pvqa_vendored_rhwp_matches_source(self):
        import hashlib
        from django.conf import settings

        d = settings.STATICFILES_DIRS[0] / "hwp"
        js = (d / "preview.js").read_text()
        folder = re.search(r"from '\./(rhwp-[0-9.]+)/rhwp\.js'", js).group(1)
        src = (d / folder / "SOURCE.txt").read_text()
        rows = re.findall(r"^sha256\s+([0-9a-f]{64})\s+(\S+)$", src, re.M)
        self.assertEqual({n for _, n in rows}, {"rhwp.js", "rhwp_bg.wasm", "LICENSE"})
        for digest, name in rows:
            self.assertEqual(hashlib.sha256((d / folder / name).read_bytes()).hexdigest(), digest, name)
        self.assertIn(folder.removeprefix("rhwp-"), src.splitlines()[0])

    def test_pvqa_wasm_served_as_application_wasm(self):
        from whitenoise.media_types import MediaTypes

        self.assertEqual(MediaTypes().get_type("rhwp_bg.wasm"), "application/wasm")

    def test_pvqa_file_converted_before_e4_has_no_outline(self):
        sf = self._done()
        SourceFile.objects.filter(pk=sf.pk).update(report={})  # E4 이전 변환본: report에 tables 없음
        r = self.client.get(self.pv(sf))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<script id="hwpTables" type="application/json">[]</script>', html=False)
        self.assertEqual(self.client.get(self.dl(sf)).status_code, 200)
