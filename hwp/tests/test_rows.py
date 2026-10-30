"""F1 행 추가(add_rows): 명부가 표 줄보다 많을 때 표 끝(합계 줄 위)에 빈 줄을 더한다.

불변식: 더한 줄은 비어 있고, 원래 칸 글자·다른 표·다른 파트는 그대로, zip_ok 통과. 실패는 FillError(주소만).
"""
from __future__ import annotations

import unittest

import hwp
from hwp import engine as E

from .test_hwp import roster


class AddRowsTest(unittest.TestCase):
    def setUp(self):
        self.hx, _, _ = hwp.to_hwpx(roster())
        self.sec = hwp.candidates(self.hx)[0]['section']

    def test_rows_added_below_ref_and_blank(self):
        out = hwp.add_rows(self.hx, self.sec, 1, 3, 2)   # 3행(마지막 자료 줄) 아래, 합계 줄 위
        t = E.find_table(E.analyze(out), self.sec, 1)
        self.assertEqual(t.rows, 7)
        text = {(c.row, c.col): c.text for c in t.cells}
        self.assertEqual([text[(r, 0)] for r in range(7)], ['번호', '1', '2', '3', '', '', '합계'])
        self.assertEqual(text[(4, 2)], '')  # 단위 '명'도 지운 빈 줄
        self.assertEqual(hwp.zip_ok(out), [])
        # 더한 줄에 채우기가 그대로 된다
        filled = hwp.fill(out, self.sec, 1, {(4, 1): '새 사람', (5, 2): '3명'})
        t2 = E.find_table(E.analyze(filled), self.sec, 1)
        self.assertEqual({(c.row, c.col): c.text for c in t2.cells}[(4, 1)], '새 사람')

    def test_other_table_untouched(self):
        out = hwp.add_rows(self.hx, self.sec, 1, 3, 1)
        a, b = E.analyze(self.hx)[0], E.analyze(out)[0]
        self.assertEqual([c.text for c in a.cells], [c.text for c in b.cells])

    def test_bad_input_is_fill_error_with_address_only(self):
        for ref, n in ((9, 1), (-1, 1), (1, -1), (1, hwp.MAX_ADD_ROWS + 1)):
            with self.assertRaises(hwp.FillError) as cm:
                hwp.add_rows(self.hx, self.sec, 1, ref, n)
            for addr, _why in cm.exception.problems:
                self.assertEqual(addr[:2], (self.sec, 1))
        with self.assertRaises(hwp.FillError):
            hwp.add_rows(self.hx, self.sec, 7, 1, 1)  # 없는 표

    def test_zero_rows_returns_same(self):
        self.assertIs(hwp.add_rows(self.hx, self.sec, 1, 3, 0), self.hx)


if __name__ == '__main__':
    unittest.main()


def nested_doc() -> bytes:
    """바깥 3x3 표의 (1,1) 칸 안에 2x2 표. 바깥 (0,0)·(0,1)·(1,0) 주소는 안쪽 표 칸 주소와 겹친다."""
    from hwpx.document import HwpxDocument
    d = HwpxDocument.new()
    t = d.add_table(3, 3)
    t.set_cell_text(0, 2, '머리')
    t.cell(1, 1).add_table(2, 2)
    return d.to_bytes()


class InnerAddressTest(unittest.TestCase):
    """python-hwpx 는 바깥 표 칸 주소가 안쪽 표 칸 주소와 같으면 안쪽 표에 쓴다(QA F1 재현: 표본 표 7 → 표 9).
    그래서 그런 칸은 입력 칸이 아니고, fill 도 주소만 담아 거부한다."""

    def setUp(self):
        self.hx = nested_doc()
        self.sec = E.analyze(self.hx)[0].section

    def test_schema_excludes_addresses_shared_with_inner_table(self):
        got = {(i['row'], i['col']) for i in hwp.schema(self.hx, self.sec, 0)['inputs']}
        self.assertTrue(got.isdisjoint({(0, 0), (0, 1), (1, 0), (1, 1)}))   # 겹치는 주소 + 안쪽 표를 품은 칸
        self.assertIn((2, 2), got)

    def test_fill_refuses_shared_address_and_fills_others(self):
        with self.assertRaises(hwp.FillError) as cm:
            hwp.fill(self.hx, self.sec, 0, {(0, 0): 'x'})
        self.assertEqual(cm.exception.problems, [((self.sec, 0, 0, 0), 'address shared with inner table')])
        out = hwp.fill(self.hx, self.sec, 0, {(2, 2): 'x', (2, 0): 'y'})
        inner = E.find_table(E.analyze(out), self.sec, 1)
        self.assertEqual([c.text for c in inner.cells], ['', '', '', ''])


def block_doc() -> bytes:
    """줄 하나가 2칸 높이인 명단: 머리글 1행 + 자료 블록 2개(반·성명·인원 세로 병합, 비고 2칸) + 합계."""
    from hwpx.document import HwpxDocument
    d = HwpxDocument.new()
    t = d.add_table(6, 4)
    for c, x in enumerate(['반', '성명', '인원', '비고']):
        t.set_cell_text(0, c, x)
    for top, cls in ((1, '4-1'), (3, '4-2')):
        for c in range(3):
            t.merge_cells(top, c, top + 1, c)
        t.set_cell_text(top, 0, cls)
        t.set_cell_text(top + 1, 3, '참고')
    t.set_cell_text(5, 0, '합계')
    return d.to_bytes()


class BlockRowsTest(unittest.TestCase):
    """세로 병합으로 줄 하나가 여러 칸 높이면 그 묶음째 복제하고(insert_block_by_clone), 복제된 글자는 비운다."""

    def setUp(self):
        self.hx = block_doc()
        self.sec = E.analyze(self.hx)[0].section

    def test_block_cloned_blank_and_fillable(self):
        out = hwp.add_rows(self.hx, self.sec, 0, 3, 2)
        t = E.find_table(E.analyze(out), self.sec, 0)
        self.assertEqual(t.rows, 10)                               # 2칸 높이 × 2
        m = {(c.row, c.col): c for c in t.cells}
        self.assertEqual([m[(r, 0)].text for r in (1, 3, 5, 7, 9)], ['4-1', '4-2', '', '', '합계'])
        self.assertEqual((m[(5, 1)].rs, m[(6, 3)].text, m[(4, 3)].text), (2, '', '참고'))
        got = {(i['row'], i['col']) for i in hwp.schema(out, self.sec, 0)['inputs']}
        self.assertTrue({(5, 1), (5, 2), (7, 1), (7, 2)} <= got)
        hwp.fill(out, self.sec, 0, {(5, 1): '새 사람', (7, 2): '3'})

    def test_tall_label_from_above_still_single_row_clone(self):
        """여러 줄을 세로로 덮는 큰 칸(학년 등) 아래 줄은 묶음이 아니다: 예전처럼 한 줄 복제로 더한다
        (python-hwpx 는 큰 칸을 늘리지 않고 새 줄에 빈 칸을 만든다)."""
        from hwpx.document import HwpxDocument
        d = HwpxDocument.new()
        t = d.add_table(5, 3)
        for c, x in enumerate(['학년', '반', '인원']):
            t.set_cell_text(0, c, x)
        t.merge_cells(1, 0, 3, 0)
        t.set_cell_text(1, 0, '4학년')
        for r in (1, 2, 3):
            t.set_cell_text(r, 1, f'{r}반')
        t.set_cell_text(4, 0, '합계')
        hx = d.to_bytes()
        sec = E.analyze(hx)[0].section
        out = hwp.add_rows(hx, sec, 0, 3, 2)
        a = E.find_table(E.analyze(out), sec, 0)
        m = {(c.row, c.col): c for c in a.cells}
        self.assertEqual((a.rows, m[(1, 0)].rs, m[(4, 0)].text, m[(6, 0)].text), (7, 3, '', '합계'))
        hwp.fill(out, sec, 0, {(4, 1): '4반', (5, 2): '3'})
