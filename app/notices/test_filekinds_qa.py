"""P4 QA 보강: 구현 테스트에 없던 경계 사례.

- 대문자·섞인 확장자(.PDF·.Xlsx·.HWP)도 공지·일정 둘 다 받는다.
- 실제 작성기가 만든 파일(testdata/: macOS textutil의 Word 97 .doc·.docx·.odt, sips의 jpg·png·gif)이 통과한다.
- 학교 현장에 흔한 'HTML을 .xls로 저장한 파일'은 이름과 내용이 달라 거부.
- 일정 첨부에서도 매크로 문서는 매크로 안내 문구로 거부.
- XLM(Excel 4.0) 매크로 시트가 든 xlsx는 매크로로 거부.
"""
import os

from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile

from today.models import EventFile
from today.test_event_more import MoreBase

from .models import NoticeFile
from .models import Notice
from .test_filekinds import MAIN, macros, samples, zipped
from .tests import Base

DATA = os.path.join(os.path.dirname(__file__), "testdata")
UPPER = [("자료.PDF", "pdf"), ("자료.Xlsx", "xlsx"), ("자료.HWP", "hwp"), ("자료.HWPX", "hwpx"), ("자료.DOC", "doc"),
         ("자료.PPTX", "pptx"), ("자료.ODS", "ods"), ("사진.JPG", "jpg"), ("사진.Png", "png"), ("사진.GIF", "gif")]
REAL = ["doc", "docx", "odt", "jpg", "png", "gif"]
HTML_XLS = (b'<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel">'
            b"<body><table><tr><td>1</td></tr></table></body></html>")


def up(name, data):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


def real(ext):
    with open(os.path.join(DATA, f"real.{ext}"), "rb") as fh:
        return fh.read()


def xlm_xlsx():
    part, ctype = MAIN["xlsx"]
    types = (f'<Types><Override PartName="/{part}" ContentType="{ctype}"/>'
             '<Override PartName="/xl/macrosheets/sheet1.xml" ContentType="application/vnd.ms-excel.macrosheet+xml"/></Types>')
    return zipped([("[Content_Types].xml", types), (part, "<x/>"), ("xl/macrosheets/sheet1.xml", "<x/>")])


class NoticeKindsQA(Base):
    def rejected(self, name, data):
        caches["ratelimit"].clear()
        r = self.write(attach=[up(name, data)])
        self.assertEqual(r.status_code, 400, name)
        self.assertEqual((NoticeFile.objects.count(), Notice.objects.count()), (0, 0), name)
        return r.content.decode()

    def test_upper_case_extensions(self):
        s = samples()
        for name, kind in UPPER:
            with self.subTest(name=name):
                n = self.made(attach=[up(name, s[kind])])
                f = n.files.get()
                self.assertEqual((f.kind, f.name), (kind, name))
                self.assertTrue(f.file.name.endswith(f".{kind}"))
                n.delete()

    def test_real_writer_files(self):
        for ext in REAL:
            with self.subTest(ext=ext):
                n = self.made(attach=[up(f"실제.{ext}", real(ext))])
                self.assertEqual(n.files.get().kind, ext)
                n.delete()

    def test_html_saved_as_xls_rejected(self):
        self.assertIn("진짜 엑셀 파일만", self.rejected("성적.xls", HTML_XLS))

    def test_upper_macro_extension_rejected(self):
        self.rejected("매크로.XLSM", samples()["xlsx"])
        self.assertEqual(NoticeFile.objects.count(), 0)

    def test_xlm_macrosheet_xlsx_rejected(self):
        self.assertIn("매크로가 든 문서", self.rejected("매크로.xlsx", xlm_xlsx()))


class EventKindsQA(MoreBase):
    def setUp(self):
        super().setUp()
        self.e = self.ev(self.t41, "협의", "all")

    def test_upper_case_and_real_files(self):
        s = samples()
        files = [up(n, s[k]) for n, k in UPPER] + [up(f"실제.{x}", real(x)) for x in REAL]
        for i in range(0, len(files), 5):
            r = self.upload(self.t41, self.e, *files[i:i + 5])
            self.assertEqual(r.status_code, 200, [f.name for f in files[i:i + 5]])
            self.e.files.all().delete()

    def test_macro_message_and_html_xls(self):
        name, data = macros()[0]
        r = self.upload(self.t41, self.e, up(name, data))
        self.assertEqual(r.status_code, 400)
        self.assertIn("매크로가 든 문서는 받지 않아요", r.content.decode())
        r = self.upload(self.t41, self.e, up("성적.xls", HTML_XLS))
        self.assertIn("진짜 엑셀 파일만", r.content.decode())
        self.assertEqual(EventFile.objects.count(), 0)
