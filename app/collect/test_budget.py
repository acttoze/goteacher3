"""S1 예산 품목 취합 — 만들기(강조 카드·한 곳당 예산), 직접 입력(S2), 합본 합계·초과, 에듀파인 엑셀(.xls).

불변식
- S2: 받는 사람이 모든 칸을 직접 적는다. 서버가 상품 주소를 읽어 오는 경로는 없다(404).
- 에듀파인 엑셀은 원본 양식(fixtures/edufine/품목내역(통합).xls)과 시트 이름·머리글·서식·칸 너비가 같고, 수량·예상단가는 숫자 칸.
- 공간 격리: 다른 공간의 취합 키로 만들기·내보내기는 404.
"""
from pathlib import Path

import xlrd
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from spaces.models import Member, School, Space

from .export import edufine_rows
from .models import Collection, Field, Row
from .presets import EDUFINE, PRESETS
from .submit import save_cell

FIX = Path(__file__).parent / "fixtures"


class BudgetBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:a"), User.objects.create(username="google:b")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2")
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.ua)

    def make(self, space=None, budget=150000, title="2학기 학습준비물"):
        space = space or self.a
        c = Collection.objects.create(space=space, created_by=self.ma if space == self.a else self.mb, title=title,
                                      source=Collection.Source.BUDGET, unit_head=["학년반", "담임"], multi=True, budget=budget)
        for i, (n, k) in enumerate(PRESETS["supplies"]["fields"]):
            Field.objects.create(collection=c, pos=i, name=n, kind=k)
        Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.ma)
        Row.objects.create(collection=c, pos=1, unit="4-2", who="박서준", member=self.m2)
        return c

    def fill(self, row, *items):
        """items: (내용, 규격, 단위, 수량, 예상단가, 주소). 첫 물품은 명부 줄, 나머지는 같은 받는 곳 새 줄."""
        c = row.collection
        fs = {f.name: f for f in c.fields.all()}
        for i, item in enumerate(items):
            r = row if i == 0 else Row.objects.create(collection=c, pos=10 + i, unit=row.unit, who=row.who, member=row.member)
            for name, v in zip(EDUFINE + ["상품 주소"], item):
                if v:
                    save_cell(r, fs[name], v, row.who)


class BudgetCreateTests(BudgetBase):
    def post(self, space=None, **data):
        return self.client.post(f"/s/{(space or self.a).token}/c/new", data)

    def test_card_on_new_screen(self):
        r = self.client.get(f"/s/{self.a.token}/c/new")
        self.assertContains(r, 'class="card budget-card"')
        self.assertContains(r, "에듀파인 엑셀")
        self.assertContains(r, 'name="budget_go"')
        self.assertNotContains(r, "모으기")

    def test_create_with_budget(self):
        r = self.post(budget_go="1", budget="150,000원", budget_title="2학기 학습준비물", src="paste", due_d="2026-11-20")
        c = Collection.objects.get()
        self.assertRedirects(r, f"/s/{self.a.token}/c/{c.key}/", fetch_redirect_response=False)
        self.assertEqual((c.source, c.budget, c.title, c.multi, c.created_by), ("budget", 150000, "2학기 학습준비물", True, self.ma))
        self.assertEqual([f.name for f in c.fields.all()], EDUFINE + ["금액", "상품 주소"])
        self.assertEqual([r.unit for r in c.rows.all()], ["4-1", "4-2"])
        self.assertIsNotNone(c.due)

    def test_create_without_budget(self):
        self.post(budget_go="1", budget="")
        c = Collection.objects.get()
        self.assertEqual((c.budget, c.title), (None, "학급 예산 품목 취합"))

    def test_bad_budget(self):
        for v in ["십만", "-1", "0", "1.5", "99999999999"]:
            with self.subTest(v):
                r = self.post(budget_go="1", budget=v)
                self.assertEqual(r.status_code, 400)
                self.assertContains(r, "예산은 원 단위 숫자로", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_other_space_404(self):
        self.assertEqual(self.post(self.b, budget_go="1", budget="1000").status_code, 404)
        self.assertFalse(Collection.objects.exists())

    def test_again_keeps_budget(self):
        c = self.make()
        r = self.client.post(f"/s/{self.a.token}/c/{c.key}/again")
        self.assertEqual(r.status_code, 303)
        c2 = Collection.objects.exclude(pk=c.pk).get()
        self.assertEqual((c2.source, c2.budget), ("budget", 150000))


class BudgetRecvTests(BudgetBase):
    def test_form_direct_input_no_server_fetch(self):
        """S2: 받는 화면은 사람이 모두 적는다. 상품 주소는 그냥 적는 칸이고 서버가 읽어 오는 경로는 없다."""
        c = self.make()
        r = self.client.get(f"/c/{c.link_token}/u/{c.rows.first().key}")
        self.assertContains(r, 'data-budget="150000"')
        self.assertContains(r, "150,000")
        self.assertContains(r, 'name="f6" inputmode="url"')
        for gone in ("/shop", "data-shop", "hx-trigger=\"paste", "쿠팡", "채워져요"):
            self.assertNotContains(r, gone)
        self.assertEqual(self.client.post(f"/c/{c.link_token}/shop", {"f6": "https://www.11st.co.kr/products/1"}).status_code, 404)

    def test_direct_input_to_board_and_xls(self):
        """S2: 직접 입력 → 내기 → 합본 합계·남은 금액 → 에듀파인 xls(주소는 빠짐)."""
        c = self.make(budget=10000)
        row = c.rows.first()
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {"line": [row.key, ""], "f0": ["색종이", "풀"], "f1": ["15x15", ""],
                                                                "f2": ["묶음", "개"], "f3": ["3", "2"], "f4": ["1,500", "800"],
                                                                "f6": ["https://www.coupang.com/vp/products/1", ""], "by": "김가람"})
        self.assertEqual(r.status_code, 200)
        body = self.client.get(f"/s/{self.a.token}/c/{c.key}/").content.decode()
        self.assertIn("6,100", body)
        self.assertIn("3,900 남음", body)
        self.assertIn('href="https://www.coupang.com/vp/products/1"', body)
        x = self.client.get(f"/s/{self.a.token}/c/{c.key}/edufine.xls")
        gs = xlrd.open_workbook(file_contents=x.content).sheet_by_index(0)
        self.assertEqual([gs.row_values(i) for i in range(gs.nrows)],
                         [EDUFINE, ["색종이", "15x15", "묶음", 3.0, 1500.0], ["풀", "", "개", 2.0, 800.0]])
        self.assertNotIn(b"coupang", x.content)

    def test_number_digits_capped(self):
        """숫자 칸은 정수 12자리·소수 4자리까지(엑셀 내보내기·계산이 다룰 크기)."""
        c = self.make()
        row = c.rows.first()
        base = {"line": [row.key], "f0": ["풀"], "f1": [""], "f2": ["개"], "f4": ["800"], "f6": [""]}
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {**base, "f3": ["9" * 13]})
        self.assertContains(r, "숫자가 너무 커요", status_code=400)
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {**base, "f3": ["1.23456"]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {**base, "f3": ["000" + "9" * 12]})
        self.assertEqual(r.status_code, 200)

    def test_submit_spec_optional_and_amount(self):
        """규격은 비워도 낸다. 금액 = 수량 × 예상단가(합본 계산)."""
        c = self.make()
        row = c.rows.first()
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {"line": [row.key, ""], "f0": ["색종이", "풀"], "f1": ["", "35g"],
                                                                "f2": ["묶음", "개"], "f3": ["3", "10"], "f4": ["1,500", "800"],
                                                                "f6": ["", ""], "by": "김가람"})
        self.assertEqual(r.status_code, 200, r.content.decode()[:500])
        self.assertEqual(edufine_rows(c), [["색종이", "", "묶음", "3", "1500"], ["풀", "35g", "개", "10", "800"]])


class BudgetBoardTests(BudgetBase):
    def test_totals_and_over(self):
        c = self.make(budget=10000)
        r1, r2 = c.rows.all()
        self.fill(r1, ("색종이", "", "묶음", "3", "1500", "https://www.11st.co.kr/products/1"), ("풀", "", "개", "2", "800", ""))
        self.fill(r2, ("가위", "", "개", "10", "2000", ""))
        body = self.client.get(f"/s/{self.a.token}/c/{c.key}/").content.decode()
        self.assertIn("6,100", body)  # 4500 + 1600
        self.assertIn("3,900 남음", body)
        self.assertIn("20,000", body)
        self.assertIn("10,000 초과", body)
        self.assertIn("예산 초과 1반", body)
        self.assertIn("edufine.xls", body)

    def test_no_xls_button_for_other_collections(self):
        c = Collection.objects.create(space=self.a, created_by=self.ma, title="참가", source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"])
        body = self.client.get(f"/s/{self.a.token}/c/{c.key}/").content.decode()
        self.assertNotIn("edufine.xls", body)
        self.assertNotIn('<th class="r">반 합계</th>', body)


class EdufineXlsTests(BudgetBase):
    def xls(self, c, space=None):
        return self.client.get(f"/s/{(space or c.space).token}/c/{c.key}/edufine.xls")

    def test_same_format_as_original(self):
        c = self.make()
        r1, r2 = c.rows.all()
        self.fill(r1, ("색종이", "15x15", "묶음", "3", "1500", "https://www.11st.co.kr/products/1"), ("풀", "", "개", "2", "800", ""))
        self.fill(r2, ("=SUM(A1)", "", "개", "1", "12000", ""))
        r = self.xls(c)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.ms-excel")
        self.assertIn("filename*=utf-8''2%ED%95%99%EA%B8%B0%20%ED%95%99%EC%8A%B5%EC%A4%80%EB%B9%84%EB%AC%BC_%ED%92%88%EB%AA%A9%EB%82%B4%EC%97%AD.xls",
                      r["Content-Disposition"])
        self.assertIn("no-store", r["Cache-Control"])
        got = xlrd.open_workbook(file_contents=r.content, formatting_info=True)
        want = xlrd.open_workbook(str(FIX / "edufine" / "품목내역(통합).xls"), formatting_info=True)
        self.assertEqual(got.biff_version, 80)  # 진짜 .xls(BIFF8)
        self.assertEqual(got.sheet_names(), want.sheet_names())
        gs, ws = got.sheet_by_index(0), want.sheet_by_index(0)
        self.assertEqual(gs.row_values(0), ws.row_values(0))
        self.assertEqual(gs.default_row_height, ws.default_row_height)
        for i in range(ws.ncols):
            self.assertEqual(gs.colinfo_map[i].width, ws.colinfo_map[i].width)
            gx, wx = got.xf_list[gs.cell_xf_index(0, i)], want.xf_list[ws.cell_xf_index(0, i)]
            gf, wf = got.font_list[gx.font_index], want.font_list[wx.font_index]
            self.assertEqual((gf.name, gf.height, gf.weight, gf.bold, gf.colour_index, gf.italic, gf.underline_type),
                             (wf.name, wf.height, wf.weight, wf.bold, wf.colour_index, wf.italic, wf.underline_type))
            for part in ("alignment", "border", "background"):
                g, w = dict(vars(getattr(gx, part))), dict(vars(getattr(wx, part)))
                if part == "border" and w["left_line_style"] == 0:  # 선 없는 변의 색은 보이지 않는다(xlwt는 0으로 쓴다, 원본은 8)
                    g.pop("left_colour_index"), w.pop("left_colour_index")
                self.assertEqual(g, w, (i, part))
            self.assertEqual(got.format_map[gx.format_key].format_str, want.format_map[wx.format_key].format_str)
            for ci in (8, 9, 10):
                self.assertEqual(got.colour_map[ci], want.colour_map[ci])
        # 데이터 줄: 합본 순서, 글자 3칸 + 숫자 2칸, 받는 곳·주소·꼬리말 없음, 수식으로 바뀌지 않음
        self.assertEqual(gs.nrows, 4)
        self.assertEqual(gs.ncols, 5)
        self.assertEqual([gs.row_values(i) for i in range(1, 4)],
                         [["색종이", "15x15", "묶음", 3.0, 1500.0], ["풀", "", "개", 2.0, 800.0], ["=SUM(A1)", "", "개", 1.0, 12000.0]])
        self.assertEqual([gs.cell_type(1, i) for i in range(5)], [xlrd.XL_CELL_TEXT] * 3 + [xlrd.XL_CELL_NUMBER] * 2)
        self.assertNotIn(b"11st", r.content)

    def test_only_budget_collections(self):
        c = Collection.objects.create(space=self.a, created_by=self.ma, title="참가", source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"])
        self.assertEqual(self.xls(c).status_code, 404)

    def test_other_space_404(self):
        mine, theirs = self.make(), self.make(self.b)
        self.assertEqual(self.xls(theirs, space=self.a).status_code, 404)  # 남의 키를 내 공간 주소로
        self.assertEqual(self.xls(mine, space=self.b).status_code, 404)  # 회원 아닌 공간
        self.client.logout()
        self.assertNotEqual(self.xls(mine).status_code, 200)

    def test_huge_old_value_written_as_text(self):
        """제출 상한 전에 들어간 아주 큰 수도 500이 아니라 글자 칸으로."""
        c = self.make()
        self.fill(c.rows.first(), ("풀", "", "개", "9" * 400, "800", ""))
        r = self.xls(c)
        self.assertEqual(r.status_code, 200)
        gs = xlrd.open_workbook(file_contents=r.content).sheet_by_index(0)
        self.assertEqual((gs.cell_type(1, 3), gs.cell_value(1, 3)), (xlrd.XL_CELL_TEXT, "9" * 400))
        self.assertEqual(gs.cell_type(1, 4), xlrd.XL_CELL_NUMBER)

    def test_empty_collection_header_only(self):
        r = self.xls(self.make())
        gs = xlrd.open_workbook(file_contents=r.content).sheet_by_index(0)
        self.assertEqual((gs.nrows, gs.row_values(0)), (1, EDUFINE))


class OldSuppliesStillWork(BudgetBase):
    """S1 전에 만든 '2학기 학습준비물 신청'(PRESET, 물품명·단가·구입 링크)은 그대로 동작한다(예산 기능은 붙지 않음)."""

    def test_old_preset(self):
        c = Collection.objects.create(space=self.a, created_by=self.ma, title="2학기 학습준비물 신청", source=Collection.Source.PRESET,
                                      unit_head=["학년반", "담임"], multi=True)
        names = [("물품명", "text"), ("수량", "number"), ("단가", "number"), ("금액", "calc"), ("구입 링크", "url")]
        for i, (n, k) in enumerate(names):
            Field.objects.create(collection=c, pos=i, name=n, kind=k)
        row = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.ma)
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {"line": [row.key], "f0": ["풀"], "f1": ["2"], "f2": ["800"], "f4": [""]})
        self.assertEqual(r.status_code, 200)
        body = self.client.get(f"/s/{self.a.token}/c/{c.key}/").content.decode()
        self.assertIn("1,600", body)
        self.assertNotIn("edufine.xls", body)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/c/{c.key}/edufine.xls").status_code, 404)
