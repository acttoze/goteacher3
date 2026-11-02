"""F2 글자 줄이기(shrink): 채운 칸의 문단 글자 모양만 step_pt 만큼 작게.

불변식: 칸 글자·표 구조·다른 칸 글자 모양·다른 표·다른 파트는 그대로, header 는 기존 글자 모양을 건드리지 않고
새 글자 모양만 덧붙임, zip_ok 통과. 실패는 FillError(주소만).
"""
from __future__ import annotations

import re
import unittest

from lxml import etree

import hwp
from hwp import engine as E

from .test_hwp import roster

HH = '{http://www.hancom.co.kr/hwpml/2011/head}'


def heights(hx, section, index):
    """{(행, 열): [그 칸 직속 문단 run 들의 글자 크기(1/100pt)]}."""
    parts = E.parts_of(hx)
    head = etree.fromstring(parts['Contents/header.xml'], E.PARSER)
    h = {c.get('id'): int(c.get('height')) for c in head.iter(HH + 'charPr')}
    root = etree.fromstring(parts[section], E.PARSER)
    tbl = list(root.iter(E.HP + 'tbl'))[index]
    out = {}
    for tc in tbl.iter(E.HP + 'tc'):
        if next(tc.iterancestors(E.HP + 'tbl')) is not tbl:
            continue
        a = tc.find(E.HP + 'cellAddr')
        runs = [r for p in tc.find(E.HP + 'subList').findall(E.HP + 'p') for r in p.findall(E.HP + 'run')]
        out[(int(a.get('rowAddr')), int(a.get('colAddr')))] = [h[r.get('charPrIDRef')] for r in runs]
    return out


class ShrinkTest(unittest.TestCase):
    def setUp(self):
        self.base, _, _ = hwp.to_hwpx(roster())
        self.sec = hwp.candidates(self.base)[0]['section']
        self.vals = {(1, 1): '아주 긴 이름이 들어가는 칸', (2, 1): '둘째'}
        self.hx = hwp.fill(self.base, self.sec, 1, self.vals)

    def test_only_filled_cells_get_smaller(self):
        out = hwp.shrink(self.hx, self.sec, 1, list(self.vals), 1)
        b, a = heights(self.hx, self.sec, 1), heights(out, self.sec, 1)
        for k in b:
            if k in self.vals:
                self.assertEqual(a[k], [x - 100 for x in b[k]], k)
            else:
                self.assertEqual(a[k], b[k], k)
        # 다른 표(결재란)의 글자 모양도 그대로
        self.assertEqual(heights(out, self.sec, 0), heights(self.hx, self.sec, 0))
        self.assertEqual(hwp.zip_ok(out), [])

    def test_text_structure_and_other_parts_same(self):
        out = hwp.shrink(self.hx, self.sec, 1, list(self.vals), 2)
        for x, y in zip(E.analyze(self.hx), E.analyze(out)):
            self.assertEqual([(c.row, c.col, c.rs, c.cs, c.text, c.border) for c in x.cells],
                             [(c.row, c.col, c.rs, c.cs, c.text, c.border) for c in y.cells])
        pb, pa = E.parts_of(self.hx), E.parts_of(out)
        self.assertEqual(set(pb), set(pa))
        self.assertEqual({n for n in pb if pb[n] != pa[n]}, {self.sec, 'Contents/header.xml'})

    def test_header_keeps_old_char_shapes(self):
        out = hwp.shrink(self.hx, self.sec, 1, list(self.vals), 1)
        hb = E.parts_of(self.hx)['Contents/header.xml']
        ha = E.parts_of(out)['Contents/header.xml']
        old = re.findall(rb'<hh:charPr\b.*?</hh:charPr>', hb, re.S)
        new = re.findall(rb'<hh:charPr\b.*?</hh:charPr>', ha, re.S)
        self.assertEqual(new[:len(old)], old)          # 기존 것은 한 글자도 그대로
        self.assertGreaterEqual(len(new), len(old) + 1)  # 새 글자 모양은 뒤에 덧붙임
        cnt = re.search(rb'<hh:charProperties itemCnt="(\d+)"', ha)
        self.assertEqual(int(cnt.group(1)), len(new))

    def test_steps_accumulate_from_original_and_floor(self):
        b = heights(self.hx, self.sec, 1)[(1, 1)][0]
        two = heights(hwp.shrink(self.hx, self.sec, 1, list(self.vals), 2), self.sec, 1)[(1, 1)][0]
        self.assertEqual(two, b - 200)
        far = heights(hwp.shrink(self.hx, self.sec, 1, list(self.vals), hwp.MAX_SHRINK), self.sec, 1)[(1, 1)][0]
        self.assertEqual(far, max(b - 100 * hwp.MAX_SHRINK, hwp.MIN_FONT))

    def test_zero_step_returns_same(self):
        self.assertIs(hwp.shrink(self.hx, self.sec, 1, list(self.vals), 0), self.hx)

    def test_to_hwp_keeps_values_and_smaller_font(self):
        out = hwp.shrink(self.hx, self.sec, 1, list(self.vals), 2)
        h = hwp.to_hwp(out, self.sec, 1, self.vals)
        back, _, _ = hwp.to_hwpx(h)
        sec = hwp.candidates(back)[0]['section']
        b = heights(self.hx, self.sec, 1)
        a = heights(back, sec, 1)
        self.assertEqual(a[(1, 1)][0], b[(1, 1)][0] - 200)
        self.assertEqual(a[(3, 1)], b[(3, 1)])

    def test_bad_input_is_fill_error_with_address_only(self):
        for cells, step in (([(1, 1)], -1), ([(1, 1)], hwp.MAX_SHRINK + 1), ([(9, 9)], 1), ([(0, 0), 'x'], 1)):
            with self.assertRaises(hwp.FillError) as cm:
                hwp.shrink(self.hx, self.sec, 1, cells, step)
            for addr, _why in cm.exception.problems:
                self.assertEqual(addr[:2], (self.sec, 1))
            self.assertNotIn('아주', str(cm.exception))
        with self.assertRaises(hwp.FillError):
            hwp.shrink(self.hx, self.sec, 7, [(1, 1)], 1)  # 없는 표


if __name__ == '__main__':
    unittest.main()


class ShrinkQAEdgeTest(unittest.TestCase):
    """QA(F2) 경계: 더 줄일 수 없을 때, 문단 줄 배치 캐시, 표 밖 본문, 빈 칸 목록."""

    def setUp(self):
        self.base, _, _ = hwp.to_hwpx(roster())
        self.sec = hwp.candidates(self.base)[0]['section']
        self.vals = {(1, 1): '아주 긴 이름이 들어가는 칸', (2, 1): '둘째'}
        self.hx = hwp.fill(self.base, self.sec, 1, self.vals)

    def test_already_at_floor_returns_input_unchanged(self):
        from unittest import mock
        with mock.patch.object(E, 'MIN_FONT', 100_000):   # 모든 글자가 하한 아래: 바꿀 것이 없다
            self.assertIs(hwp.shrink(self.hx, self.sec, 1, list(self.vals), 3), self.hx)

    def test_empty_cell_list_returns_input(self):
        self.assertIs(hwp.shrink(self.hx, self.sec, 1, [], 2), self.hx)

    def test_layout_cache_dropped_only_in_shrunk_paragraphs(self):
        out = hwp.shrink(self.hx, self.sec, 1, list(self.vals), 1)

        def paras(hx):
            root = etree.fromstring(E.parts_of(hx)[self.sec], E.PARSER)
            res = []
            tbls = list(root.iter(E.HP + 'tbl'))
            for p in root.iter(E.HP + 'p'):
                tc = next(p.iterancestors(E.HP + 'tc'), None)
                key = None
                if tc is not None and next(tc.iterancestors(E.HP + 'tbl')) is tbls[1]:
                    a = tc.find(E.HP + 'cellAddr')
                    key = (int(a.get('rowAddr')), int(a.get('colAddr')))
                lsa = p.find(E.HP + 'linesegarray')
                res.append((key, None if lsa is None else etree.tostring(lsa)))
            return res
        b, a = paras(self.hx), paras(out)
        self.assertEqual(len(b), len(a))
        for (kb, lb), (ka, la) in zip(b, a):
            self.assertEqual(kb, ka)
            if kb in self.vals:
                self.assertIsNone(la, kb)          # 줄인 문단은 한글이 다시 배치
            else:
                self.assertEqual(la, lb, kb)       # 나머지 문단 줄 배치는 그대로

    def test_body_runs_outside_tables_unchanged(self):
        out = hwp.shrink(self.hx, self.sec, 1, list(self.vals), hwp.MAX_SHRINK)

        def body(hx):
            root = etree.fromstring(E.parts_of(hx)[self.sec], E.PARSER)
            return [(r.get('charPrIDRef'), ''.join(t.text or '' for t in r.iter(E.HP + 't')))
                    for r in root.iter(E.HP + 'run') if next(r.iterancestors(E.HP + 'tbl'), None) is None]
        self.assertEqual(body(out), body(self.hx))

    def test_new_char_shape_differs_only_in_height(self):
        out = hwp.shrink(self.hx, self.sec, 1, list(self.vals), 2)
        head_b = etree.fromstring(E.parts_of(self.hx)['Contents/header.xml'], E.PARSER)
        head_a = etree.fromstring(E.parts_of(out)['Contents/header.xml'], E.PARSER)
        old = {c.get('id'): c for c in head_b.iter(HH + 'charPr')}
        new = [c for c in head_a.iter(HH + 'charPr') if c.get('id') not in old]
        self.assertTrue(new)
        root_b = etree.fromstring(E.parts_of(self.hx)[self.sec], E.PARSER)
        src_ids = {r.get('charPrIDRef') for r in root_b.iter(E.HP + 'run')}

        def norm(c):
            x = etree.fromstring(etree.tostring(c))
            x.attrib.pop('id', None)
            x.attrib.pop('height', None)
            return etree.tostring(x)
        olds = {norm(old[i]) for i in src_ids if i in old}
        for c in new:
            self.assertIn(norm(c), olds)            # 글꼴·굵기·색 등은 원래 글자 모양 그대로
