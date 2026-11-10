"""S1 QA 보강(gt-qa) — 구현 테스트에 없는 경계만.

- 원본 양식과 머리글 칸의 XF 전체(글꼴·정렬·테두리·바탕·보호·표시형식)·시트 수준 속성(칸 너비·기본 행 높이·병합·틀 고정)이 같다.
- 수량·예상단가에 아주 큰 숫자(숫자 칸 검증 통과, 1000자 이하)가 있어도 내보내기가 500이 되지 않는다.
- 합본 줄이 여러 받는 곳일 때 xls 줄 순서가 합본(명부) 순서다.
"""

import xlrd

from .test_budget import FIX, BudgetBase

ORIG = FIX / "edufine" / "품목내역(통합).xls"


def xf_attrs(book, xf):
    f = book.font_list[xf.font_index]
    # character_set(0·1)은 화면에 보이지 않아 뺀다. weight 700은 BIFF8에서 굵게(bold 비트는 쓰지 않음)
    font = {k: v for k, v in vars(f).items() if k not in ("font_index", "character_set")}
    parts = {p: dict(vars(getattr(xf, p))) for p in ("alignment", "border", "background", "protection")}
    # 선 없는 변의 색은 화면에 보이지 않는다(원본 8, xlwt 0) → 비교에서 뺀다
    for side in ("left", "right", "top", "bottom", "diag"):
        if parts["border"].get(f"{side}_line_style", 1) == 0:
            parts["border"].pop(f"{side}_colour_index", None)
    rgb = {}
    for k in ("top_colour_index", "bottom_colour_index", "left_colour_index", "right_colour_index"):
        if k in parts["border"]:
            rgb[k] = book.colour_map.get(parts["border"][k])
    rgb["bg"] = book.colour_map.get(parts["background"]["pattern_colour_index"])
    rgb["font"] = book.colour_map.get(f.colour_index)
    return {"font": font, **parts, "fmt": book.format_map[xf.format_key].format_str, "rgb": rgb}


class EdufineStructureQA(BudgetBase):
    maxDiff = None

    def get(self, c):
        r = self.client.get(f"/s/{c.space.token}/c/{c.key}/edufine.xls")
        return r, (xlrd.open_workbook(file_contents=r.content, formatting_info=True) if r.status_code == 200 else None)

    def test_header_xf_and_sheet_level_same_as_original(self):
        c = self.make()
        self.fill(c.rows.first(), ("색종이", "15x15", "묶음", "3", "1500", ""))
        r, got = self.get(c)
        want = xlrd.open_workbook(str(ORIG), formatting_info=True)
        gs, ws = got.sheet_by_index(0), want.sheet_by_index(0)
        self.assertEqual((got.nsheets, got.sheet_names()), (want.nsheets, want.sheet_names()))
        self.assertEqual(gs.row_values(0), ws.row_values(0))
        self.assertEqual([gs.cell_type(0, i) for i in range(5)], [ws.cell_type(0, i) for i in range(5)])
        self.assertEqual(gs.merged_cells, ws.merged_cells)
        self.assertEqual((gs.horz_split_pos, gs.vert_split_pos), (ws.horz_split_pos, ws.vert_split_pos))
        self.assertEqual(gs.default_row_height, ws.default_row_height)
        self.assertEqual({k: v.width for k, v in gs.colinfo_map.items() if k < 5}, {k: v.width for k, v in ws.colinfo_map.items()})
        for i in range(5):
            with self.subTest(col=i):
                self.assertEqual(xf_attrs(got, got.xf_list[gs.cell_xf_index(0, i)]), xf_attrs(want, want.xf_list[ws.cell_xf_index(0, i)]))

    def test_huge_number_does_not_500(self):
        c = self.make()
        self.fill(c.rows.first(), ("색종이", "", "개", "9" * 400, "1500", ""))
        r, got = self.get(c)
        self.assertEqual(r.status_code, 200)

    def test_rows_follow_board_order(self):
        c = self.make()
        r1, r2 = c.rows.all()
        self.fill(r2, ("가위", "", "개", "1", "2000", ""))
        self.fill(r1, ("풀", "", "개", "2", "800", ""), ("자", "30cm", "개", "1", "500", ""))
        _, got = self.get(c)
        gs = got.sheet_by_index(0)
        self.assertEqual([gs.cell_value(i, 0) for i in range(1, gs.nrows)], ["풀", "자", "가위"])
        self.assertTrue(all(isinstance(gs.cell_value(i, 3), float) for i in range(1, gs.nrows)))
