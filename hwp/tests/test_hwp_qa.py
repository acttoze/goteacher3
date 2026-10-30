"""QA 추가 경계 사례(E1). test_hwp.py 에 없는 것만 둔다.

계약: 업로드 거부 사유는 모두 HwpError 하위로 나와야 하고(README), DTD/엔티티 선언은 거부해야 한다(safety).
"""
from __future__ import annotations

import io
import re
import struct
import unittest
import warnings
import zipfile

import hwp
from hwp import engine as E
from hwp.tests.test_hwp import make_zip, roster


def _set_declared_size(data: bytes, name: str, size: int) -> bytes:
    """zip 의 로컬·중앙 헤더에 적힌 '풀린 크기'만 거짓으로 바꾼다(실제 내용은 그대로)."""
    b, nb = bytearray(data), name.encode()
    for sig, off, nlo, hdr in ((b'PK\x03\x04', 22, 26, 30), (b'PK\x01\x02', 24, 28, 46)):
        j = b.find(sig)
        while j >= 0:
            nl = struct.unpack('<H', b[j + nlo:j + nlo + 2])[0]
            if bytes(b[j + hdr:j + hdr + nl]) == nb:
                struct.pack_into('<I', b, j + off, size)
            j = b.find(sig, j + 4)
    return bytes(b)


def _deflated(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, x in entries.items():
            z.writestr(n, x)
    return out.getvalue()


def _with_section(sec: bytes) -> bytes:
    parts = dict(E.parts_of(roster()))
    parts['Contents/section0.xml'] = sec
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for n, x in parts.items():
            z.writestr(n, x)
    return out.getvalue()


def _utf16_xxe() -> bytes:
    body = re.sub(r'^<\?xml[^>]*\?>', '', E.parts_of(roster())['Contents/section0.xml'].decode('utf-8'))
    doc = ('<?xml version="1.0" encoding="UTF-16"?>'
           '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>' + body.replace('번호', '&e;', 1))
    return _with_section(doc.encode('utf-16'))


class ZipEdgeTest(unittest.TestCase):
    def test_encrypted_entry(self):
        b = bytearray(make_zip({'a.xml': b'<a/>'}))
        for sig, off in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
            b[b.find(sig) + off] |= 1
        with self.assertRaises(hwp.UnsafeFile):
            hwp.to_hwpx(bytes(b))

    def test_duplicate_names(self):
        out = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(out, 'w') as z:
                z.writestr('a.xml', b'<a/>')
                z.writestr('a.xml', b'<b/>')
        with self.assertRaises(hwp.UnsafeFile):
            hwp.to_hwpx(out.getvalue())

    def test_dotdot_in_middle(self):
        with self.assertRaises(hwp.UnsafeFile):
            hwp.to_hwpx(make_zip({'mimetype': b'application/hwp+zip', 'Contents/../../evil.xml': b'<a/>'}))

    def test_disguised_executable(self):
        with self.assertRaises(hwp.UnsupportedFormat):
            hwp.detect(b'MZ\x90\x00' + b'\0' * 100)

    def test_lying_size_bin_is_hwperror(self):
        """선언 크기를 10바이트로 속인 50MB 압축 항목(약 50KB 파일). 받아들이든 거부하든 HwpError 여야 한다."""
        data = _set_declared_size(_deflated({'mimetype': b'application/hwp+zip',
                                             'BinData/x.bin': b'\0' * 50_000_000}), 'BinData/x.bin', 10)
        self.assertLess(len(data), 100_000)
        try:
            hx, _, _ = hwp.to_hwpx(data)
            hwp.candidates(hx)
        except hwp.HwpError:
            pass

    def test_lying_size_xml_is_hwperror(self):
        data = _set_declared_size(_deflated({'mimetype': b'application/hwp+zip',
                                             'BinData/x.xml': b'<a>' + b' ' * 5_000_000 + b'</a>'}), 'BinData/x.xml', 10)
        with self.assertRaises(hwp.HwpError):
            hwp.to_hwpx(data)


class DtdEdgeTest(unittest.TestCase):
    def test_utf16_dtd_rejected(self):
        """UTF-16 으로 쓴 DTD/엔티티 선언도 거부해야 한다(바이트 '<!DOCTYPE' 검색을 비켜 감)."""
        with self.assertRaises(hwp.UnsafeFile):
            hwp.to_hwpx(_utf16_xxe())

    def test_utf16_xxe_never_leaks(self):
        """어떤 경로에서도 외부 엔티티가 풀려 들어오면 안 된다. 실패는 HwpError 로만."""
        d = _utf16_xxe()
        try:
            hx, _, _ = hwp.to_hwpx(d)
            texts = str([[c.text for c in t.cells] for t in E.analyze(hx)])
            self.assertNotIn('root:', texts)
            c = hwp.candidates(hx)[0]
            hwp.fill(hx, c['section'], c['index'], {(1, 1): '김'})
        except hwp.HwpError:
            pass


class NonHwpxZipTest(unittest.TestCase):
    def test_other_zip_has_no_candidates_and_fill_refuses(self):
        z = make_zip({'[Content_Types].xml': b'<a/>', 'xl/workbook.xml': b'<b/>'})
        hx, kind, _ = hwp.to_hwpx(z)
        self.assertEqual(hwp.candidates(hx), [])
        with self.assertRaises(hwp.FillError):
            hwp.fill(hx, 'Contents/section0.xml', 0, {})


if __name__ == '__main__':
    unittest.main()
