"""hwp 패키지 단위 테스트. 실행: uv run python -m unittest discover -s hwp/tests -t ."""
from __future__ import annotations

import io
import os
import unittest
import zipfile
from functools import lru_cache
from pathlib import Path
from unittest import mock

import hwp
from hwp import engine as E

EXAMPLES = Path(__file__).resolve().parents[2] / 'research' / 'hwp_poc' / 'examples'


@lru_cache(maxsize=None)
def roster() -> bytes:
    """결재란(2x3) + 명단 표(머리글 1행, 번호 열, 빈 칸, 합계 행)."""
    from hwpx.document import HwpxDocument
    d = HwpxDocument.new()
    d.add_paragraph('결재')
    a = d.add_table(2, 3)
    for c, x in enumerate(['담당', '부장', '교장']):
        a.set_cell_text(0, c, x)
    d.add_paragraph('연수 신청 명단')
    t = d.add_table(5, 3)
    for c, x in enumerate(['번호', '성명', '인원']):
        t.set_cell_text(0, c, x)
    for r in (1, 2, 3):
        t.set_cell_text(r, 0, str(r))
        t.set_cell_text(r, 2, '명')
    t.set_cell_text(4, 0, '합계')
    return d.to_bytes()


def make_zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return out.getvalue()


class AnalyzeTest(unittest.TestCase):
    def test_candidates_and_schema(self):
        hx, kind, rep = hwp.to_hwpx(roster())
        self.assertEqual((kind, rep), ('hwpx', {}))
        roles = [t.role for t in E.analyze(hx)]
        self.assertEqual(roles, ['approval', 'data'])
        cands = hwp.candidates(hx)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual((c['index'], c['rows'], c['cols']), (1, 5, 3))
        s = hwp.schema(hx, c['section'], c['index'])
        self.assertEqual((s['kind'], s['header_rows'], s['label_cols'], s['total_rows']), ('list', 1, 1, [4]))
        cells = {(i['row'], i['col']): i for i in s['inputs']}
        self.assertEqual(sorted(cells), [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)])  # 합계 행 제외
        self.assertEqual(cells[(1, 1)]['label'], '1 / 성명')
        self.assertEqual(cells[(1, 2)]['unit'], '명')

    def test_tables_outline_has_no_text(self):
        hx, _, _ = hwp.to_hwpx(roster())
        out = hwp.tables(hx)
        self.assertEqual([(t['sec'], t['index'], t['rows'], t['cols'], t['cells'], t['role'], t['rank'], t['parent'])
                          for t in out],
                         [(0, 0, 2, 3, 6, 'approval', None, None), (0, 1, 5, 3, 15, 'data', 0, None)])
        self.assertNotIn('명단', repr(out))   # 문서 글자는 담지 않는다
        self.assertNotIn('담당', repr(out))
        with self.assertRaises(hwp.HwpError):
            hwp.tables(b'not a zip')

    def test_tables_nested_parent(self):
        from hwpx.document import HwpxDocument
        d = HwpxDocument.new()
        d.add_table(1, 1)
        outer = d.add_table(2, 2)
        inner = outer.cell(1, 1).add_table(2, 2)
        inner.cell(0, 0).add_table(1, 2)
        d.add_table(1, 3)
        out = hwp.tables(d.to_bytes())
        self.assertEqual([(t['index'], t['nested'], t['parent']) for t in out],
                         [(0, False, None), (1, False, None), (2, True, 1), (3, True, 2), (4, False, None)])

    def test_unknown_table(self):
        with self.assertRaises(hwp.FillError):
            hwp.schema(roster(), 'Contents/section0.xml', 9)


class FillTest(unittest.TestCase):
    def setUp(self):
        self.hx = roster()
        self.c = hwp.candidates(self.hx)[0]
        self.sec, self.idx = self.c['section'], self.c['index']

    def test_fill_hwpx_then_reparse(self):
        vals = {(1, 1): '김하나', (2, 1): '이둘', (1, 2): '3명'}
        out = hwp.fill(self.hx, self.sec, self.idx, vals)
        self.assertEqual(hwp.zip_ok(out), [])
        v = hwp.verify(self.hx, out, self.sec, self.idx, vals)
        self.assertTrue(v['ok'], v)
        self.assertEqual(v['parts_changed'], [self.sec])
        got = {(c.row, c.col): c.text for c in E.analyze(out)[self.idx].cells}
        self.assertEqual(got[(1, 1)], '김하나')
        self.assertEqual(got[(0, 1)], '성명')              # 머리글 그대로
        self.assertEqual(E.analyze(out)[0].role, 'approval')  # 다른 표 그대로

    def test_fill_hwp_output(self):
        vals = {(3, 1): '박셋'}
        out = hwp.to_hwp(hwp.fill(self.hx, self.sec, self.idx, vals), self.sec, self.idx, vals)
        self.assertEqual(hwp.detect(out), 'hwp')
        hx2, kind, _ = hwp.to_hwpx(out)
        got = {(c.row, c.col): c.text for c in E.analyze(hx2)[self.idx].cells}
        self.assertEqual((kind, got[(3, 1)]), ('hwp', '박셋'))

    def test_control_chars_removed(self):
        out = hwp.fill(self.hx, self.sec, self.idx, {(1, 1): '가\x00나\x07'})
        got = {(c.row, c.col): c.text for c in E.analyze(out)[self.idx].cells}
        self.assertEqual(got[(1, 1)], '가나')

    def test_bad_values_rejected_with_address_only(self):
        for vals in ({(9, 9): 'x'}, {(1, 1): 3}, {(1, 1): 'x' * (hwp.MAX_CELL_TEXT + 1)}, {'1,1': 'x'}):
            with self.assertRaises(hwp.FillError) as cm:
                hwp.fill(self.hx, self.sec, self.idx, vals)
            addr, why = cm.exception.problems[0]
            self.assertEqual(addr[:2], (self.sec, self.idx))
            self.assertNotIn('x' * 5, str(cm.exception) + why)

    def test_output_format_setting(self):
        old = os.environ.pop(hwp.safety.OUTPUT_ENV, None)
        try:
            self.assertEqual(hwp.output_format('hwp'), 'hwp')
            os.environ[hwp.safety.OUTPUT_ENV] = 'hwpx'
            self.assertEqual(hwp.output_format('hwp'), 'hwpx')
        finally:
            os.environ.pop(hwp.safety.OUTPUT_ENV, None)
            if old is not None:
                os.environ[hwp.safety.OUTPUT_ENV] = old


@unittest.skipUnless((EXAMPLES / '도서구입희망신청서_취합결과.hwp').exists(), 'POC 예제 없음')
class PocExampleTest(unittest.TestCase):
    def test_real_hwp_roundtrip(self):
        data = (EXAMPLES / '도서구입희망신청서_취합결과.hwp').read_bytes()
        hx, kind, _ = hwp.to_hwpx(data)
        self.assertEqual(kind, 'hwp')
        # 이미 채운 결과물이라 빈 칸 후보가 없다. 가장 큰 표의 글자 칸 하나를 덮어쓴다.
        t = max(E.analyze(hx), key=lambda t: len(t.cells))
        c = next(c for c in reversed(t.cells) if c.text and not c.has_nested and not c.has_obj)
        vals = {(c.row, c.col): '바꾼값'}
        out = hwp.to_hwp(hwp.fill(hx, t.section, t.index, vals), t.section, t.index, vals)
        hx2, _, _ = hwp.to_hwpx(out)
        t2 = [x for x in E.analyze(hx2) if x.section == t.section][t.index]
        self.assertEqual({(x.row, x.col): x.text for x in t2.cells}[(c.row, c.col)], '바꾼값')


class SafetyTest(unittest.TestCase):
    def test_magic_bytes(self):
        self.assertEqual(hwp.detect(b'PK\x03\x04rest'), 'hwpx')
        self.assertEqual(hwp.detect(hwp.safety.HWP_MAGIC + b'x'), 'hwp')
        for bad in (b'%PDF-1.7', b'', b'\xd0\xcf\x11\xe0'):
            with self.assertRaises(hwp.UnsupportedFormat):
                hwp.detect(bad)

    def test_too_large(self):
        with self.assertRaises(hwp.TooLarge):
            hwp.detect(b'PK' + b'\0' * hwp.MAX_UPLOAD)

    def test_path_traversal(self):
        for name in ('../evil.xml', '/abs.xml', 'a\\b.xml', 'C:x.xml'):
            with self.assertRaises(hwp.UnsafeFile, msg=name):
                hwp.to_hwpx(make_zip({'mimetype': b'application/hwp+zip', name: b'<a/>'}))

    def test_doctype_entity(self):
        xxe = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'
        with self.assertRaises(hwp.UnsafeFile):
            hwp.to_hwpx(make_zip({'mimetype': b'application/hwp+zip', 'Contents/section0.xml': xxe}))

    def test_zip_bomb_total(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for i in range(3):
                z.writestr(f'BinData/b{i}.bin', b'\0' * 600_000)
        data = out.getvalue()
        self.assertLess(len(data), 20_000)     # 압축률 100배 넘는 작은 파일
        with mock.patch.object(hwp.safety, 'MAX_UNZIPPED', 1_000_000):
            with self.assertRaises(hwp.UnsafeFile):
                hwp.to_hwpx(data)

    def test_too_many_entries(self):
        with self.assertRaises(hwp.UnsafeFile):
            hwp.to_hwpx(make_zip({f'f{i}': b'' for i in range(hwp.MAX_ENTRIES + 1)}))

    def test_broken_zip_and_hwp(self):
        with self.assertRaises(hwp.UnsafeFile):
            hwp.to_hwpx(b'PK\x03\x04garbage')
        with self.assertRaises(hwp.ConvertError):
            hwp.to_hwpx(hwp.safety.HWP_MAGIC + b'\0' * 600)


if __name__ == '__main__':
    unittest.main()
