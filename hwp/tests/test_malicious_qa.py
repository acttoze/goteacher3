"""QA(E3) 독립 악성 표본. 구현자 표본(test_malicious)과 따로 이 파일 안에서 만든다.

웹 판별(sniff) 또는 worker 판별(to_hwpx) 중 하나에서 HwpError 로 거부돼야 한다. 앱 업로드 경로 시험
(hwpfiles.test_purge.UploadMaliciousQaTests)도 이 표본을 쓴다.
"""
from __future__ import annotations

import io
import time
import unittest
import zipfile
import zlib
from functools import lru_cache

import hwp
from hwp import engine as E
from hwp.tests.test_hwp import roster

MB = 1024 * 1024


def _rezip(base: bytes, extra=(), replace=None, comp=zipfile.ZIP_DEFLATED) -> bytes:
    """정상 hwpx(base)의 항목을 그대로 옮기고 replace(이름→바이트)·extra 를 더한다."""
    replace = replace or {}
    src = zipfile.ZipFile(io.BytesIO(base))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for i in src.infolist():
            body = replace.get(i.filename, src.read(i))
            c = zipfile.ZIP_STORED if i.filename == 'mimetype' else comp
            z.writestr(i.filename, body, compress_type=c)
        for name, body in extra:
            z.writestr(name, body, compress_type=comp)
    return out.getvalue()


@lru_cache(maxsize=None)
def zip_bomb() -> bytes:
    """정상 문서 + 0 바이트 210MB 항목 하나(압축 후 약 200KB). 조각으로 써서 메모리에 올리지 않는다."""
    out = io.BytesIO(_rezip(roster()))
    with zipfile.ZipFile(out, 'a', zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo('BinData/zero.bin')
        zi.compress_type = zipfile.ZIP_DEFLATED
        with z.open(zi, 'w', force_zip64=True) as fh:
            block = bytes(MB)
            for _ in range(210):
                fh.write(block)
    return out.getvalue()


XXE = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE hh [<!ENTITY q SYSTEM "file:///etc/passwd">]>'
       '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">&q;</hh:head>')
LAUGHS = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY l0 "ㅋㅋㅋㅋㅋㅋㅋㅋ">'
          + ''.join(f'<!ENTITY l{i} "{("&l%d;" % (i - 1)) * 10}">' for i in range(1, 10)) + ']><l>&l9;</l>')
PARAM_DTD = ('<?xml version="1.0"?><!DOCTYPE p SYSTEM "http://203.0.113.1/x.dtd">'
             '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"/>')


def xxe_header() -> bytes:
    return _rezip(roster(), replace={'Contents/header.xml': XXE.encode()})


def laughs_hpf() -> bytes:
    return _rezip(roster(), replace={'Contents/content.hpf': LAUGHS.encode()})


def external_dtd() -> bytes:
    return _rezip(roster(), replace={'META-INF/container.rdf': PARAM_DTD.encode()})


def bz2_entry() -> bytes:
    return _rezip(roster(), comp=zipfile.ZIP_BZIP2)


def lzma_entry() -> bytes:
    return _rezip(roster(), comp=zipfile.ZIP_LZMA)


@lru_cache(maxsize=None)
def _zeros_raw_deflate(n_mb: int) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    block = bytes(MB)
    return b''.join(c.compress(block) for _ in range(n_mb)) + c.flush()


@lru_cache(maxsize=None)
def ole_bomb() -> bytes:
    """정상 hwp 의 스트림 + 압축 스트림 3개(각 80MB 0, 합 240MB > 200MB)."""
    from hwpx.hwp5.cfb import CompoundFile, build_compound_file, read_all_streams
    base = list(read_all_streams(CompoundFile(E.to_hwp(roster()))))
    z = _zeros_raw_deflate(80)
    return build_compound_file(base + [(f'BinData/BIN{i:04d}.png', z) for i in (7, 8, 9)])


SAMPLES = {  # 이름: (만드는 함수, 웹에서 거부되는가)
    'zip_bomb': (zip_bomb, True),
    'xxe_header': (xxe_header, False),
    'laughs_hpf': (laughs_hpf, False),
    'external_dtd_rdf': (external_dtd, False),
    'bzip2_entries': (bz2_entry, True),
    'lzma_entries': (lzma_entry, True),
    'ole_stream_bomb': (ole_bomb, False),
}


class QaMaliciousRejected(unittest.TestCase):
    def test_each_sample_rejected_quickly_without_leak(self):
        for name, (make, web) in SAMPLES.items():
            data = make()
            with self.subTest(name):
                self.assertLess(len(data), hwp.MAX_UPLOAD)
                fns = [hwp.to_hwpx] + ([hwp.sniff] if web else [])
                for fn in fns:
                    t0 = time.monotonic()
                    with self.assertRaises(hwp.HwpError, msg=fn.__name__) as cm:
                        fn(data)
                    self.assertLess(time.monotonic() - t0, 10)
                    self.assertNotIn('root:', str(cm.exception))
                    self.assertNotIn('ㅋ', str(cm.exception))

    def test_base_documents_still_pass(self):
        """표본의 바탕(정상 문서를 옮겨 담기만 한 것)은 통과한다 — 거부가 옮겨 담기 탓이 아님."""
        self.assertEqual(hwp.to_hwpx(_rezip(roster()))[1], 'hwpx')
        self.assertEqual(hwp.to_hwpx(E.to_hwp(roster()))[1], 'hwp')


if __name__ == '__main__':
    unittest.main()
