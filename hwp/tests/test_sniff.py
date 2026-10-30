"""웹에서 쓰는 가벼운 구조 검사(sniff)와 worker의 조각 읽기 zip 검사."""
from __future__ import annotations

import io
import unittest
import zipfile
from unittest import mock

import hwp
from hwp import safety
from hwp.tests.test_hwp import roster


def zp(entries, comp=zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', comp) as z:
        for n, b in entries:
            z.writestr(n, b)
    return out.getvalue()


HX = [('mimetype', 'application/hwp+zip'), ('Contents/section0.xml', '<a/>')]


class SniffTest(unittest.TestCase):
    def test_real_documents(self):
        self.assertEqual(hwp.sniff(roster()), 'hwpx')
        self.assertEqual(hwp.sniff(hwp.engine.to_hwp(roster())), 'hwp')

    def test_not_hwpx_zip(self):
        for data in (zp([]), zp([('a.txt', 'x')]), zp([('[Content_Types].xml', '<T/>'), ('word/document.xml', '<d/>')]),
                     zp([('mimetype', 'application/epub+zip'), ('Contents/section0.xml', '<a/>')]),
                     zp([('mimetype', 'application/hwp+zip')])):
            with self.assertRaises(hwp.UnsupportedFormat):
                hwp.sniff(data)

    def test_big_mimetype_not_read(self):
        with self.assertRaises(hwp.UnsupportedFormat):
            hwp.sniff(zp([('mimetype', 'application/hwp+zip' + ' ' * 10_000), ('Contents/section0.xml', '<a/>')]))

    def test_unsafe_names_and_garbage(self):
        for data in (zp(HX + [('../x.xml', '<a/>')]), zp(HX + [('/abs.xml', '<a/>')]), b'PK\x03\x04' + b'x' * 100):
            with self.assertRaises(hwp.UnsafeFile):
                hwp.sniff(data)

    def test_ole_not_hwp(self):
        for data in (safety.HWP_MAGIC + b'\0' * 500,
                     safety.HWP_MAGIC + 'FileHeader'.encode('utf-16-le') + b'\0' * 100,  # 서명 없음
                     safety.HWP_MAGIC + b'HWP Document File' + b'\0' * 100):           # 디렉터리 이름 없음
            with self.assertRaises(hwp.UnsupportedFormat):
                hwp.sniff(data)

    def test_sniff_does_not_decompress(self):
        with mock.patch('zipfile.ZipFile.read', side_effect=AssertionError('read')), \
                mock.patch('hwp.safety.check_zip', side_effect=AssertionError('heavy')):
            self.assertEqual(hwp.sniff(zp(HX)), 'hwpx')


class CentralDirectoryTest(unittest.TestCase):
    """ZipFile 이 중앙 디렉터리를 통째로 파싱하기 전에 EOCD 를 직접 읽어 거른다(목록 폭탄)."""

    def assert_rejected_before_zipfile(self, data):
        with mock.patch('zipfile.ZipFile', side_effect=AssertionError('ZipFile opened')):
            for fn in (hwp.sniff, safety.check_zip):
                with self.assertRaises(hwp.UnsafeFile):
                    fn(data)

    def test_too_many_entries(self):
        self.assert_rejected_before_zipfile(zp(HX + [(f'Contents/x{i}.bin', '') for i in range(1001)], zipfile.ZIP_STORED))

    def test_central_directory_too_big(self):
        names = [('Contents/' + f'{i:04d}' + 'n' * 1100, '') for i in range(990)]
        self.assert_rejected_before_zipfile(zp(HX + names, zipfile.ZIP_STORED))

    def test_zip64_rejected(self):
        data = bytearray(zp(HX))
        e = data.rfind(b'PK\x05\x06')
        data[e + 10:e + 12] = b'\xff\xff'  # 전체 항목 수 0xFFFF → ZIP64
        self.assert_rejected_before_zipfile(bytes(data))
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('mimetype', 'application/hwp+zip')
            with z.open('Contents/section0.xml', 'w', force_zip64=True) as fh:
                fh.write(b'<a/>')
        zipfile.ZipFile(io.BytesIO(out.getvalue())).close()
        data = out.getvalue()
        if b'PK\x06\x06' in data:  # ZIP64 EOCD 가 실제로 쓰였을 때만
            self.assert_rejected_before_zipfile(data)

    def test_no_eocd(self):
        self.assert_rejected_before_zipfile(b'PK\x03\x04' + b'x' * 100)

    def test_ok_at_limit(self):
        self.assertEqual(hwp.sniff(zp(HX + [(f'Contents/x{i}.bin', '') for i in range(998)], zipfile.ZIP_STORED)), 'hwpx')


class StreamCheckTest(unittest.TestCase):
    def test_dtd_across_chunk_boundary(self):
        body = 'x' * 1000 + '<!DOCTYPE a>'
        for chunk in (7, 1001, 1003, 1005):
            with mock.patch.object(safety, 'CHUNK', chunk), self.assertRaises(hwp.UnsafeFile):
                safety.check_zip(zp(HX + [('Contents/section1.xml', body)]))

    def test_streams_in_chunks(self):
        with mock.patch('zipfile.ZipFile.read', side_effect=AssertionError('whole read')):
            safety.check_zip(roster())


if __name__ == '__main__':
    unittest.main()
