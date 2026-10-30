"""D4 내보내기 — 엑셀(CSV), 합본 표 복사(HTML 표 + TSV), 꼬리말 켜고 끄기. 동작은 prototype collectDetail(#csv·#cptbl·#foot).

불변식
- 회원만. 다른 공간·회원 아님 404, 익명은 로그인으로. 꼬리말 바꾸기는 POST·CSRF.
- 내려받기·복사 응답은 Cache-Control no-store. 파일 이름은 RFC 5987(filename*=UTF-8'').
- CSV: UTF-8 BOM(엑셀 한글), =,+,-,@ 로 시작하는 값 앞에 '(수식 실행 방지).
- 복사 HTML: 값은 이스케이프, 병합 없는 단순 표(한글 클립보드 형식처럼 테두리·글꼴 인라인).
  우리 붙여넣기 파서(paste.html_to_tsv)로 다시 읽으면 같은 TSV가 된다(왕복).
- 표 모양은 prototype sheetOf: 앞 두 칸(unit_head) + 칸들. 여러 줄은 물품 줄마다, 금액(calc)은 계산값, 물품 없는 반은 뺀다.
"""
import csv
import io
import json
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from spaces.models import Member, School, Space

from .models import Collection, Field, Row
from .paste import html_to_tsv, parse
from .presets import PRESETS
from .submit import save_cell

FIX = Path(__file__).parent / "fixtures" / "paste"


@override_settings(SITE_URL="https://t.goteacher.xyz")
class ExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        cls.c = Collection.objects.create(space=cls.a, created_by=cls.owner, title="학예회 참가/인원",
                                          source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        cls.f1 = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.f2 = Field.objects.create(collection=cls.c, pos=1, name="비고", kind="text")
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)
        save_cell(cls.r1, cls.f1, "21", "김가람")
        save_cell(cls.r1, cls.f2, "=HYPERLINK(\"http://x\")", "김가람")
        cls.other = Collection.objects.create(space=cls.b, title="옆 학년", source=Collection.Source.SHEET,
                                              unit_head=["학년반", "담임"])

    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, tail, c=None, space=None):
        c = c or self.c
        return f"/s/{(space or c.space).token}/c/{c.key}/{tail}"

    def rows_of(self, r):
        body = r.content.decode("utf-8")
        self.assertTrue(body.startswith("﻿"))
        return list(csv.reader(io.StringIO(body[1:])))

    def copy(self, c=None):
        r = self.client.get(self.url("copy", c))
        self.assertEqual(r.status_code, 200)
        return json.loads(r.content)

    # ---- 엑셀(CSV)
    def test_csv_download(self):
        r = self.client.get(self.url("export.csv"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("no-store", r["Cache-Control"])
        cd = r["Content-Disposition"]
        self.assertTrue(cd.startswith("attachment;"))
        # 한글 이름은 RFC 5987로만, '/'는 파일 이름에 못 쓰니 바꾼다
        self.assertIn("filename*=utf-8''", cd.lower())
        self.assertIn("%ED%95%99%EC%98%88%ED%9A%8C%20%EC%B0%B8%EA%B0%80_%EC%9D%B8%EC%9B%90_%ED%95%A9%EB%B3%B8.csv", cd)
        self.assertNotIn("/", cd.split("filename*=")[1])
        self.assertIn(b"\r\n", r.content)
        rows = self.rows_of(r)
        self.assertEqual(rows[0], ["학년반", "담임", "참가(명)", "비고"])
        self.assertEqual(rows[1], ["4-1", "김가람", "21", "'=HYPERLINK(\"http://x\")"])  # 수식 실행 막기
        self.assertEqual(rows[2], ["4-2", "박서준", "", ""])  # 안 낸 반도 줄은 있다(prototype)
        self.assertEqual(rows[3], ["고티처로 모았습니다 · t.goteacher.xyz"])  # 꼬리말 기본 켜짐

    def test_csv_formula_prefixes(self):
        for i, v in enumerate(["+1+2", "-3", "@SUM(A1)", "\t=1"]):
            save_cell(self.r2, self.f2, v, "박서준")
            rows = self.rows_of(self.client.get(self.url("export.csv")))
            self.assertEqual(rows[2][3], "'" + v, i)

    def test_csv_footer_off(self):
        Collection.objects.filter(pk=self.c.pk).update(footer_on=False)
        rows = self.rows_of(self.client.get(self.url("export.csv")))
        self.assertEqual(len(rows), 3)
        self.assertNotIn("고티처로", rows[-1][0])

    # ---- 표 복사
    def test_copy_html_and_tsv(self):
        r = self.client.get(self.url("copy"))
        self.assertIn("no-store", r["Cache-Control"])
        d = json.loads(r.content)
        self.assertEqual(d["tsv"].split("\n"), ["학년반\t담임\t참가(명)\t비고",
                                                "4-1\t김가람\t21\t=HYPERLINK(\"http://x\")", "4-2\t박서준\t\t"])
        h = d["html"]
        self.assertIn("<!--StartFragment-->", h)
        self.assertIn('border="1"', h)
        self.assertIn("border:solid #000000 0.4pt", h)  # 한글이 만든 클립보드 HTML과 같은 테두리 표기
        self.assertIn("맑은 고딕", h)
        self.assertNotIn("rowspan", h)
        self.assertNotIn("colspan", h)
        self.assertEqual(h.lower().count("<tr"), 3)
        self.assertNotIn("고티처로 모았습니다", h + d["tsv"])  # 꼬리말은 파일에만(prototype)

    def test_copy_escapes(self):
        save_cell(self.r2, self.f2, "<img src=x onerror=alert(1)>&", "박서준")
        d = self.copy()
        self.assertNotIn("<img", d["html"])
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;&amp;", d["html"])
        self.assertIn("<img src=x onerror=alert(1)>&", d["tsv"])

    def test_copy_round_trip(self):
        """복사한 HTML을 우리 붙여넣기 파서로 다시 읽으면 같은 표."""
        save_cell(self.r2, self.f2, "늦게 <냄> & 확인", "박서준")
        d = self.copy()
        self.assertEqual(html_to_tsv(d["html"]), d["tsv"])
        p = parse(d["tsv"])
        self.assertEqual([f["name"] for f in p["fields"]], ["참가(명)", "비고"])

    def test_copy_round_trip_like_hangul_fixture(self):
        """한글이 만든 클립보드 HTML(실측 표본)과 같은 꼴: table border=1·cellspacing=0, td마다 테두리·글꼴 인라인."""
        hwp = (FIX / "01_leave_plan.html").read_text("utf-8")
        mine = self.copy()["html"]
        for pat in [r'<table border="1" cellspacing="0" cellpadding="0" style="border-collapse:collapse',
                    r'<td valign="middle" style="[^"]*border:solid #000000 0\.4pt', r"font-family:"]:
            self.assertRegex(hwp.replace("'", '"').replace("TABLE", "table").replace("TD", "td"), re.compile(pat, re.I))
            self.assertRegex(mine, re.compile(pat, re.I))

    # ---- 여러 줄·자동 계산, 연수 이수
    def test_multi_items_and_calc(self):
        spec = PRESETS["supplies"]
        c = Collection.objects.create(space=self.a, created_by=self.owner, title="준비물", source=Collection.Source.PRESET,
                                      unit_head=spec["unit_head"], multi=True)
        fs = [Field.objects.create(collection=c, pos=i, name=n, kind=k) for i, (n, k) in enumerate(spec["fields"])]
        r1 = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.owner)
        r1b = Row.objects.create(collection=c, pos=1, unit="4-1", who="김가람", member=self.owner)
        Row.objects.create(collection=c, pos=2, unit="4-2", who="박서준", member=self.m2)  # 물품 없음 → 빠짐
        for r, item in [(r1, ("색종이", "3", "1500", "", "https://a.example/1")), (r1b, ("풀", "2", "800", "", ""))]:
            for f, v in zip(fs, item):
                if v:
                    save_cell(r, f, v, "김가람")
        d = self.copy(c)
        self.assertEqual(d["tsv"].split("\n"), ["학년반\t담임\t물품명\t수량\t단가\t금액\t구입 링크",
                                                "4-1\t김가람\t색종이\t3\t1500\t4500\thttps://a.example/1",
                                                "4-1\t김가람\t풀\t2\t800\t1600\t"])
        self.assertEqual(html_to_tsv(d["html"]), d["tsv"])
        rows = self.rows_of(self.client.get(self.url("export.csv", c)))
        self.assertEqual(rows[1][5], "4500")

    def test_matrix(self):
        spec = PRESETS["training"]
        c = Collection.objects.create(space=self.a, created_by=self.owner, title="연수", source=Collection.Source.PRESET,
                                      unit_head=spec["unit_head"])
        fs = [Field.objects.create(collection=c, pos=i, name=n, kind=k) for i, (n, k) in enumerate(spec["fields"][:3])]
        r = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.owner)
        save_cell(r, fs[0], "A-123", "김가람")
        save_cell(r, fs[2], "해당 없음", "김가람")
        d = self.copy(c)
        self.assertEqual(d["tsv"].split("\n")[1], "4-1\t김가람\tA-123\t\t해당 없음")
        self.assertTrue(d["tsv"].startswith("직위\t성명\t4대폭력 예방교육"))

    # ---- 꼬리말 켜고 끄기
    def test_footer_toggle(self):
        r = self.client.post(self.url("footer"), {}, HTTP_HX_REQUEST="true")  # 체크 없음 = 끄기
        self.assertEqual(r.status_code, 200)
        self.c.refresh_from_db()
        self.assertFalse(self.c.footer_on)
        self.assertIn("꼬리말 없이 내보내요", r.content.decode())
        c2 = Client()
        c2.force_login(self.u2)  # 같은 동학년 회원이면 누구나
        c2.post(self.url("footer"), {"on": "on"}, HTTP_HX_REQUEST="true")
        self.c.refresh_from_db()
        self.assertTrue(self.c.footer_on)

    def test_footer_post_only_and_csrf(self):
        self.assertEqual(self.client.get(self.url("footer")).status_code, 405)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        self.assertEqual(c.post(self.url("footer"), {}).status_code, 403)
        self.c.refresh_from_db()
        self.assertTrue(self.c.footer_on)

    def test_detail_shows_export(self):
        body = self.client.get(self.url("")).content.decode()
        self.assertIn(f'href="{self.url("export.csv")}"', body)
        self.assertIn(f'data-copy-table="{self.url("copy")}"', body)
        self.assertIn('name="on" checked', body)

    # ---- 공간 격리·회원만
    def test_other_space_404(self):
        for tail in ("export.csv", "copy"):
            self.assertEqual(self.client.get(self.url(tail, self.other)).status_code, 404, tail)  # 남의 공간
            self.assertEqual(self.client.get(self.url(tail, self.other, space=self.a)).status_code, 404, tail)  # 내 공간 주소 + 남의 모으기
        self.assertEqual(self.client.post(self.url("footer", self.other, space=self.a)).status_code, 404)
        self.other.refresh_from_db()
        self.assertTrue(self.other.footer_on)

    def test_non_member_and_anonymous(self):
        c = Client()
        c.force_login(self.ub)
        for tail in ("export.csv", "copy"):
            self.assertEqual(c.get(self.url(tail)).status_code, 404)
            r = Client().get(self.url(tail))
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r["Location"].startswith("/start?next="))
        self.assertEqual(c.post(self.url("footer")).status_code, 404)

    # ---- QA 보강(D4): 엑셀 호환 경계값·파일 이름·왕복·꼬리말 반영·잠그기 응답

    def test_csv_quotes_newlines_commas_survive(self):
        v = '첫 줄\n둘째 줄, "따옴표"\r\n셋째'
        save_cell(self.r2, self.f2, v, "박서준")
        r = self.client.get(self.url("export.csv"))
        body = r.content.decode("utf-8")
        self.assertIn('"첫 줄\n둘째 줄, ""따옴표""', body)  # 값 안 줄바꿈·따옴표는 따옴표로 감싸고 ""로
        self.assertEqual(self.rows_of(r)[2][3].replace("\r\n", "\n"), v.replace("\r\n", "\n"))

    def test_csv_formula_cr_and_header_cells(self):
        save_cell(self.r2, self.f2, "\r=1", "박서준")
        Field.objects.filter(pk=self.f1.pk).update(name="=칸")
        Row.objects.filter(pk=self.r2.pk).update(who="@박서준", unit="-4-2")
        rows = self.rows_of(self.client.get(self.url("export.csv")))
        self.assertEqual(rows[0][2], "'=칸")
        self.assertEqual(rows[2][:2], ["'-4-2", "'@박서준"])
        self.assertTrue(rows[2][3].startswith("'\r"))

    def test_csv_plain_values_untouched(self):
        save_cell(self.r2, self.f2, "1-2 반 합동, 50%", "박서준")
        self.assertEqual(self.rows_of(self.client.get(self.url("export.csv")))[2][3], "1-2 반 합동, 50%")

    def test_filename_specials(self):
        for title, want in [('a\\b:c*d?"e<f>|g\nh\ti', "a_b_c_d__e_f__g_h_i_합본.csv"), ("   ", "모으기_합본.csv"),
                            ("2학기 체험학습 (안)", "2학기 체험학습 (안)_합본.csv")]:
            Collection.objects.filter(pk=self.c.pk).update(title=title)
            cd = self.client.get(self.url("export.csv"))["Content-Disposition"]
            self.assertNotIn("\n", cd)
            self.assertNotIn("\r", cd)
            from urllib.parse import unquote
            self.assertEqual(unquote(cd.split("filename*=utf-8''")[1]), want, title)

    def test_round_trip_tricky_values(self):
        for v in ["  앞뒤 공백  ", "줄\n바꿈", "탭\t안", "a&nbsp;b", "<b>굵게</b>", "'홑따옴표\"쌍"]:
            save_cell(self.r2, self.f2, v, "박서준")
            d = self.copy()
            def cells(t):  # 붙여넣기 파서는 칸 앞뒤 공백을 정리한다(clean_text)
                return [[x.strip() for x in ln.split("\t")] for ln in t.split("\n")]
            self.assertEqual(cells(html_to_tsv(d["html"])), cells(d["tsv"]), repr(v))

    def test_hangul_fixture_parses_like_ours(self):
        """한글 실측 표본과 우리 HTML을 같은 파서로 읽어 둘 다 표가 된다(같은 꼴)."""
        for f in sorted(FIX.glob("*.html")):
            self.assertTrue(html_to_tsv(f.read_text("utf-8")).strip(), f.name)
        mine = self.copy()["html"]
        self.assertIn("<!--StartFragment-->", mine)
        self.assertIn("<!--EndFragment-->", mine)
        self.assertLess(mine.index("<!--StartFragment-->"), mine.index("<table"))

    def test_footer_toggle_reflects_in_csv_and_page(self):
        self.client.post(self.url("footer"), {}, HTTP_HX_REQUEST="true")
        self.assertEqual(len(self.rows_of(self.client.get(self.url("export.csv")))), 3)
        body = self.client.get(self.url("")).content.decode()
        self.assertIn('name="on">', body)
        self.assertNotIn('name="on" checked', body)
        self.client.post(self.url("footer"), {"on": "on"}, HTTP_HX_REQUEST="true")
        self.assertEqual(self.rows_of(self.client.get(self.url("export.csv")))[-1], ["고티처로 모았습니다 · t.goteacher.xyz"])

    def test_footer_non_member_and_anonymous_cannot_change(self):
        c = Client()
        c.force_login(self.ub)
        self.assertEqual(c.post(self.url("footer"), {}).status_code, 404)
        r = Client().post(self.url("footer"), {})
        self.assertIn(r.status_code, (302, 403, 404))
        self.c.refresh_from_db()
        self.assertTrue(self.c.footer_on)

    def test_lock_response_keeps_footer_form(self):
        r = self.client.post(self.url("lock"), {}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'hx-post="{self.url("footer")}"', r.content.decode())

    def test_no_store_and_values_not_cached(self):
        for tail in ("export.csv", "copy"):
            cc = self.client.get(self.url(tail))["Cache-Control"]
            self.assertIn("no-store", cc)
            self.assertIn("private", cc)

    def test_detail_title_escaped_in_export_card(self):
        Collection.objects.filter(pk=self.c.pk).update(title="<script>x</script>")
        body = self.client.get(self.url("")).content.decode()
        self.assertNotIn("<b><script>", body)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;_합본.csv", body)

    def test_multi_partial_item_line_kept(self):
        spec = PRESETS["supplies"]
        c = Collection.objects.create(space=self.a, created_by=self.owner, title="준비물2", source=Collection.Source.PRESET,
                                      unit_head=spec["unit_head"], multi=True)
        fs = [Field.objects.create(collection=c, pos=i, name=n, kind=k) for i, (n, k) in enumerate(spec["fields"])]
        r = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.owner)
        save_cell(r, fs[0], "가위", "김가람")  # 수량·단가 없음
        lines = self.copy(c)["tsv"].split("\n")
        self.assertEqual(lines[1].split("\t")[:3], ["4-1", "김가람", "가위"])
        self.assertEqual(len(lines), 2)
