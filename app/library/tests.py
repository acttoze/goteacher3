"""G4 양식 자료실: 가져오기(import_forms)·목록·검색·자세히·내려받기.

- 가져오기는 같은 id면 고친다(중복 없음). 파일은 hwp.sniff를 통과해야 저장한다.
- 보기·내려받기는 로그인 없이. 주소는 slug만(DB 순번·원본 id로는 안 열림).
"""
import json
import shutil
import tempfile
from functools import lru_cache
from io import StringIO
from pathlib import Path
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from spaces.models import Member, School, Space

from .models import FormDoc


@lru_cache(maxsize=None)
def sample_hwpx(text="양식"):
    from hwpx.document import HwpxDocument

    d = HwpxDocument.new()
    d.add_paragraph(text)
    d.add_table(2, 2)
    return d.to_bytes()


def row(i="f001", **kw):
    r = {"id": i, "ca": "학교", "t": f"출장 신청서 양식 {i}", "d": "출장 일시·장소를 적는 양식입니다.",
         "tips": ["복명서는 다녀온 뒤 3일 안에 냅니다."], "file": f"{i}.hwpx",
         "kb": 1, "dl": 999, "hit": 9999, "at": "2026-01-01"}
    r.update(kw)
    return r


class Base(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.src = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.media, True)
        self.addCleanup(shutil.rmtree, self.src, True)
        o = override_settings(MEDIA_ROOT=self.media)
        o.enable()
        self.addCleanup(o.disable)
        (self.src / "files" / "학교").mkdir(parents=True)

    def put(self, name, data):
        (self.src / "files" / "학교" / name).write_bytes(data)

    def run_import(self, rows, js=True):
        if js:  # prototype/assets/forms.js 모양
            p = self.src / "forms.js"
            p.write_text("// 주석\nTR.seedForms = " + json.dumps(rows, ensure_ascii=False) + ";\n", encoding="utf-8")
        else:
            p = self.src / "forms.json"
            p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        out = StringIO()
        call_command("import_forms", str(p), str(self.src / "files"), stdout=out)
        return out.getvalue()


class ImportTests(Base):
    def test_import_creates_with_zero_counts(self):
        self.put("f001.hwpx", sample_hwpx())
        self.run_import([row()])
        f = FormDoc.objects.get()
        self.assertEqual((f.src, f.category, f.title, f.tips), ("f001", "학교", "출장 신청서 양식 f001", row()["tips"]))
        self.assertEqual((f.downloads, f.views), (0, 0))  # 시연용 dl·hit은 가져오지 않는다
        self.assertEqual(f.size, len(sample_hwpx()))
        self.assertEqual(f.filename, "f001.hwpx")
        self.assertNotIn("f001.hwpx", f.file.name.split("/")[0])
        with f.file.open("rb") as fh:
            self.assertEqual(fh.read(), sample_hwpx())

    def test_import_is_idempotent_and_updates(self):
        self.put("f001.hwpx", sample_hwpx())
        self.run_import([row()])
        f = FormDoc.objects.get()
        slug, name = f.slug, f.file.name
        FormDoc.objects.update(downloads=5)
        self.run_import([row()])
        self.run_import([row(t="출장 신청서·복명서 양식", d="고친 설명")])
        f = FormDoc.objects.get()
        self.assertEqual((f.title, f.desc, f.downloads), ("출장 신청서·복명서 양식", "고친 설명", 5))
        self.assertEqual((f.slug, f.file.name), (slug, name))  # 검색 유입 주소는 바뀌지 않는다
        self.assertEqual(len(list(Path(self.media, "forms").iterdir())), 1)

    def test_import_replaces_changed_file(self):
        self.put("f001.hwpx", sample_hwpx())
        self.run_import([row()])
        self.put("f001.hwpx", sample_hwpx("새 판"))
        self.run_import([row()])
        f = FormDoc.objects.get()
        with f.file.open("rb") as fh:
            self.assertEqual(fh.read(), sample_hwpx("새 판"))
        self.assertEqual(len(list(Path(self.media, "forms").iterdir())), 1)

    def test_json_list_also_works(self):
        self.put("f001.hwpx", sample_hwpx())
        self.run_import([row()], js=False)
        self.assertEqual(FormDoc.objects.count(), 1)

    def test_sniff_rejects_non_hwp(self):
        self.put("f001.hwpx", sample_hwpx())
        self.put("f002.hwpx", b"PK\x03\x04 not really a zip")
        self.put("f003.hwpx", b"<script>alert(1)</script>")
        with self.assertRaises(CommandError):
            self.run_import([row(), row("f002"), row("f003")])
        self.assertEqual(list(FormDoc.objects.values_list("src", flat=True)), ["f001"])  # 나머지는 들어간다
        self.assertEqual(len(list(Path(self.media, "forms").iterdir())), 1)

    def test_rejects_missing_file_bad_category_and_path(self):
        self.put("f001.hwpx", sample_hwpx())
        with self.assertRaises(CommandError):
            self.run_import([row("f002"), row("f001", ca="기타"), row("f003", file="../f001.hwpx")])
        self.assertFalse(FormDoc.objects.exists())

    def test_same_title_gets_distinct_slug(self):
        self.put("f001.hwpx", sample_hwpx())
        self.put("f002.hwpx", sample_hwpx())
        self.run_import([row(t="회의록 양식"), row("f002", t="회의록 양식")])
        a, b = FormDoc.objects.order_by("src")
        self.assertNotEqual(a.slug, b.slug)
        self.assertTrue(a.slug.startswith("회의록-양식"))


class PublicTests(Base):
    def setUp(self):
        super().setUp()
        for i in ("f001", "f002", "f003"):
            self.put(f"{i}.hwpx", sample_hwpx())
        self.run_import([
            row("f001", t="출장 신청서·복명서 양식", d="출장 일시·장소"),
            row("f002", ca="교육청", t="위원회 협의 결과서 양식", d="안건별 협의 내용"),
            row("f003", t="<script>alert(1)</script> 양식", d="<img src=x onerror=alert(1)>",
                tips=["<b>굵게</b>"]),
        ])
        self.a = FormDoc.objects.get(src="f001")

    def test_list_without_login(self):
        r = self.client.get("/forms")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "출장 신청서·복명서 양식")
        self.assertContains(r, "위원회 협의 결과서 양식")
        self.assertContains(r, f'href="/forms/{self.a.slug}')
        self.assertContains(r, '<meta name="description"')

    def test_search_and_category(self):
        r = self.client.get("/forms", {"q": "출장"})
        self.assertContains(r, "출장 신청서·복명서 양식")
        self.assertNotContains(r, "위원회 협의 결과서 양식")
        r = self.client.get("/forms", {"q": "협의 내용"})  # 설명으로도 찾는다
        self.assertContains(r, "위원회 협의 결과서 양식")
        r = self.client.get("/forms", {"ca": "교육청"})
        self.assertContains(r, "위원회 협의 결과서 양식")
        self.assertNotContains(r, "출장 신청서·복명서 양식")
        r = self.client.get("/forms", {"q": "없는낱말"})
        self.assertContains(r, "맞는 양식이")

    def test_detail_without_login_counts_view(self):
        r = self.client.get(f"/forms/{self.a.slug}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "이렇게 쓰세요")
        self.assertContains(r, "복명서는 다녀온 뒤 3일 안에 냅니다.")
        self.assertContains(r, "<title>출장 신청서·복명서 양식 · 양식 자료실 · 고티처</title>", html=False)
        self.assertContains(r, '<meta name="description" content="출장 일시·장소">', html=False)
        self.assertContains(r, f'href="/forms/{self.a.slug}/download"')
        self.a.refresh_from_db()
        self.assertEqual(self.a.views, 1)
        self.client.get(f"/forms/{self.a.slug}", HTTP_USER_AGENT="Googlebot/2.1")
        self.a.refresh_from_db()
        self.assertEqual(self.a.views, 1)  # 봇은 세지 않는다

    def test_download_without_login(self):
        r = self.client.get(f"/forms/{self.a.slug}/download")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), sample_hwpx())
        cd = r["Content-Disposition"]
        self.assertTrue(cd.startswith("attachment"))
        self.assertIn('filename="f001.hwpx"', cd)
        self.assertEqual(r["Content-Type"], "application/hwp+zip")
        self.assertIn("no-cache", r["Cache-Control"])
        self.assertNotIn(self.media, cd)
        self.a.refresh_from_db()
        self.assertEqual(self.a.downloads, 1)
        self.client.get(f"/forms/{self.a.slug}/download", HTTP_USER_AGENT="bingbot")
        self.a.refresh_from_db()
        self.assertEqual(self.a.downloads, 1)

    def test_korean_filename_rfc5987(self):
        self.put("출장_신청서.hwpx", sample_hwpx())
        self.run_import([row("f009", file="출장_신청서.hwpx")])
        f = FormDoc.objects.get(src="f009")
        self.assertNotIn("출장", f.file.name)  # 저장 경로에 원래 이름을 쓰지 않는다
        r = self.client.get(f"/forms/{f.slug}/download")
        self.assertIn("filename*=utf-8''" + quote("출장_신청서.hwpx"), r["Content-Disposition"])
        self.assertNotIn("출장", r["Content-Disposition"])  # 헤더는 ASCII만

    def test_unknown_slug_and_sequential_ids_404(self):
        for url in ("/forms/없는-양식", f"/forms/{self.a.pk}", "/forms/f001", f"/forms/{self.a.pk}/download",
                    "/forms/f001/download"):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_escapes_html(self):
        x = FormDoc.objects.get(src="f003")
        for url in ("/forms", f"/forms/{x.slug}"):
            r = self.client.get(url)
            self.assertNotContains(r, "<script>alert(1)</script>")
            self.assertNotContains(r, "<img src=x")
            self.assertNotContains(r, "<b>굵게</b>")
        self.assertContains(r, "&lt;script&gt;")

    def test_no_media_url(self):
        r = self.client.get(f"/forms/{self.a.slug}")
        self.assertNotContains(r, self.a.file.name)
        self.assertEqual(self.client.get("/media/" + self.a.file.name).status_code, 404)

    def test_sitemap_lists_forms(self):
        r = self.client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("xml", r["Content-Type"])
        self.assertContains(r, "/forms/")
        self.assertEqual(r.content.decode().count("<url>"), FormDoc.objects.count() + 2)

    def test_landing_links_forms_with_db_count(self):
        r = self.client.get("/")
        self.assertContains(r, '<a href="/forms">양식 자료실 3건</a>', html=True)

    def test_logged_in_member_sees_shell_menu(self):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        sp = Space.objects.create(school=school, grade=4, name="4학년")
        u = get_user_model().objects.create(username="google:a")
        Member.objects.create(space=sp, name="김가람", cls="4-1", user=u)
        self.client.force_login(u)
        r = self.client.get(f"/forms/{self.a.slug}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="side"')
        self.assertContains(r, 'href="/forms" aria-current="page"')
        r = self.client.get(f"/s/{sp.token}/today")
        self.assertContains(r, 'href="/forms"')


class QAEdgeTests(Base):
    """G4 QA가 더한 경계 사례: HEAD·SITE_URL·slug 충돌 유지·경로 이름·meta 따옴표·여러 낱말 검색."""

    def setUp(self):
        super().setUp()
        for i in ("f001", "f002", "f003"):
            self.put(f"{i}.hwpx", sample_hwpx())
        self.rows = [
            row("f001", t="회의록 양식", d="협의회 회의 내용을 적어요"),
            row("f002", ca="교육청", t="회의록 양식", d='교육청 "회의" 기록 "><script>x</script>'),
            row("f003", t="교외체험학습 신청서 양식", d="체험 기간과 장소"),
        ]
        self.run_import(self.rows)

    def test_head_download_and_detail_do_not_count(self):
        f = FormDoc.objects.get(src="f001")
        r = self.client.head(f"/forms/{f.slug}/download", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(r.status_code, 200)
        self.client.head(f"/forms/{f.slug}", HTTP_USER_AGENT="Mozilla/5.0")
        f.refresh_from_db()
        self.assertEqual((f.downloads, f.views), (0, 0))
        self.assertEqual(self.client.post(f"/forms/{f.slug}/download").status_code, 405)

    def test_colliding_slugs_stay_on_reimport(self):
        before = dict(FormDoc.objects.values_list("src", "slug"))
        self.assertEqual(len(set(before.values())), 3)
        self.run_import(list(reversed(self.rows)))  # 순서가 바뀌어도 주소는 그대로
        self.run_import([row("f001", t="회의록 양식(고침)", d="x")] + self.rows[1:])
        self.assertEqual(dict(FormDoc.objects.values_list("src", "slug")), before)

    @override_settings(SITE_URL="https://t.goteacher.xyz")
    def test_canonical_and_sitemap_use_site_url(self):
        f = FormDoc.objects.get(src="f003")
        r = self.client.get(f"/forms/{f.slug}", {"q": "체험"})
        self.assertContains(r, f'<link rel="canonical" href="https://t.goteacher.xyz/forms/{quote(f.slug)}">',
                            html=False)
        r = self.client.get("/forms", {"q": "체험", "ca": "학교"})
        self.assertContains(r, '<link rel="canonical" href="https://t.goteacher.xyz/forms">', html=False)
        locs = [x.split("</loc>")[0] for x in self.client.get("/sitemap.xml").content.decode().split("<loc>")[1:]]
        self.assertEqual(len(locs), 5)
        self.assertTrue(all(u.startswith("https://t.goteacher.xyz/") for u in locs), locs)

    def test_meta_description_quote_breakout_escaped(self):
        f = FormDoc.objects.get(src="f002")
        r = self.client.get(f"/forms/{f.slug}")
        self.assertNotContains(r, '"><script>x</script>')
        self.assertContains(r, "&quot;&gt;&lt;script&gt;")

    def test_korean_partial_multiword_search(self):
        def titles(**p):
            return self.client.get("/forms", p).content.decode()
        h = titles(q="체험학")  # 낱말 가운데 일부
        self.assertIn("교외체험학습 신청서 양식", h)
        self.assertNotIn("회의록 양식", h)
        h = titles(q="회의록 교육청")  # 낱말마다 AND(설명에 '교육청')
        self.assertEqual(h.count('class="frow"'), 1)
        h = titles(q="회의록 체험")
        self.assertIn("맞는 양식이 자료실에 없어요", h)
        h = titles(q="체험", ca="교육청")
        self.assertIn("교육청 분류에 없어요", h)
        self.assertEqual(titles(ca="엉뚱").count('class="frow"'), 3)  # 모르는 분류는 전체

    def test_rejects_absolute_and_subdir_file_names(self):
        (self.src / "files" / "학교" / "sub").mkdir()
        (self.src / "files" / "학교" / "sub" / "f009.hwpx").write_bytes(sample_hwpx())
        with self.assertRaises(CommandError):
            self.run_import([row("f008", file="/etc/passwd"), row("f009", file="sub/f009.hwpx"),
                             row("f010", file="")])
        self.assertFalse(FormDoc.objects.filter(src__in=["f008", "f009", "f010"]).exists())

    def test_same_file_name_in_two_folders_rejected(self):
        (self.src / "files" / "교육청").mkdir()
        (self.src / "files" / "교육청" / "f001.hwpx").write_bytes(sample_hwpx("다른 판"))
        with self.assertRaises(CommandError):
            self.run_import([row("f001", t="회의록 양식", d="협의회 회의 내용을 적어요")])
        with FormDoc.objects.get(src="f001").file.open("rb") as fh:
            self.assertEqual(fh.read(), sample_hwpx())  # 기존 파일은 그대로
