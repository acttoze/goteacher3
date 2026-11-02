"""E3 악성 표본 묶음. 표본은 테스트 안에서 작게 만든다(저장소에 악성 파일을 두지 않음).

불변식: 폭탄(hwpx 압축·목록·크기 속임, hwp OLE 압축 스트림)·엔티티(XXE·billion laughs·UTF-16 DTD)·경로 조작은
웹 판별(sniff, 압축 안 풂) 또는 worker 판별(to_hwpx)에서 HwpError 로 거부된다. 오류 문구에는 문서 내용이 없다.
"""
from __future__ import annotations

import io
import struct
import time
import unittest
import zipfile
import zlib
from functools import lru_cache
from unittest import mock

from lxml import etree

import hwp
from hwp import engine as E
from hwp import safety
from hwp.tests.test_hwp import roster

MB = 1024 * 1024
LEAK = 'root:'   # /etc/passwd 첫 줄. 어느 오류 문구에도 나오면 안 된다
GOOD = [('mimetype', 'application/hwp+zip'), ('version.xml', '<v/>'), ('Contents/header.xml', '<h/>')]


def zp(entries, comp=zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', comp) as z:
        for n, b in entries:
            z.writestr(n, b)
    return out.getvalue()


def hx(section: bytes | str, name='Contents/section0.xml') -> bytes:
    return zp(GOOD + [(name, section)])


XXE = ('<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
       '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"><p>&x;</p></hs:sec>')
LAUGHS = ('<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
          + ''.join(f'<!ENTITY {c} "{("&" + p + ";") * 10}">' for p, c in zip('abcdefgh', 'bcdefghi'))
          + ']><r>&i;</r>')


@lru_cache(maxsize=None)
def ole_streams():
    from hwpx.hwp5.cfb import CompoundFile, read_all_streams
    return read_all_streams(CompoundFile(E.to_hwp(roster())))


def ole(extra) -> bytes:
    from hwpx.hwp5.cfb import build_compound_file
    return build_compound_file(list(ole_streams()) + list(extra))


@lru_cache(maxsize=None)
def zeros_deflated(n_mb: int) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    block = bytes(MB)
    return b''.join(c.compress(block) for _ in range(n_mb)) + c.flush()


class Rejects(unittest.TestCase):
    def assertRejected(self, data, exc=hwp.UnsafeFile, web=True):
        """worker 판별(to_hwpx)은 늘 거부, web=True 면 웹 판별(sniff)도 거부. 문구에 문서 내용 없음."""
        calls = [hwp.to_hwpx] + ([hwp.sniff] if web else [])
        for fn in calls:
            t0 = time.monotonic()
            with self.assertRaises(exc, msg=fn.__name__) as cm:
                fn(data)
            self.assertLess(time.monotonic() - t0, 10, fn.__name__)
            self.assertNotIn(LEAK, str(cm.exception))
        return cm.exception


class ZipBombs(Rejects):
    def test_compression_bomb(self):
        """250MB 공백이 약 250KB로 줄어든 hwpx. 목록의 선언 크기 합에서 걸린다(풀지 않음)."""
        data = hx(b' ' * (250 * MB))
        self.assertLess(len(data), 2 * MB)
        self.assertRejected(data)

    def test_many_small_entries_over_total(self):
        data = zp(GOOD + [(f'Contents/b{i}.bin', bytes(60 * MB)) for i in range(4)])
        self.assertRejected(data)

    def test_lying_sizes(self):
        """지역 머리·중앙 디렉터리의 '풀린 크기'를 작게 속인 항목: 조각 읽기에서 CRC·크기 오류로 거부(웹은 목록만 봄)."""
        data = bytearray(hx(b'<a>' + b' ' * MB + b'</a>'))
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
            info = z.getinfo('Contents/section0.xml')
            cd = z.start_dir
        struct.pack_into('<I', data, info.header_offset + 22, 10)
        pos = cd
        while data[pos:pos + 4] == b'PK\x01\x02':
            n, x, c = struct.unpack_from('<3H', data, pos + 28)
            if data[pos + 46:pos + 46 + n] == b'Contents/section0.xml':
                struct.pack_into('<I', data, pos + 24, 10)
            pos += 46 + n + x + c
        self.assertRejected(bytes(data), web=False)

    def test_listing_bomb_eocd(self):
        """EOCD 가 항목 수·디렉터리 크기를 부풀린 zip: ZipFile 이 목록을 읽기 전에 거부."""
        base = bytearray(hx('<a/>'))
        pos = base.rfind(b'PK\x05\x06')
        for n_total, cd_size in ((60000, 1000), (5, 50 * MB), (0xFFFF, 1000)):
            data = bytearray(base)
            struct.pack_into('<HH', data, pos + 8, n_total, n_total)
            struct.pack_into('<L', data, pos + 12, cd_size)
            self.assertRejected(bytes(data))

    def test_too_many_entries(self):
        self.assertRejected(zp(GOOD + [(f'Contents/{i}.bin', b'') for i in range(hwp.MAX_ENTRIES)],
                               zipfile.ZIP_STORED))

    def test_other_compression_methods(self):
        """bzip2·lzma 는 CPython 이 조각 상한 없이 푼다 → 목록 단계(웹·worker 둘 다)에서 거부."""
        for comp in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            with self.subTest(comp=comp):
                self.assertRejected(zp(GOOD + [('Contents/section0.xml', '<a/>')], comp))


class ZipPaths(Rejects):
    def test_path_traversal(self):
        for name in ('../evil.xml', 'Contents/../../evil.xml', '/etc/evil.xml', 'Contents\\evil.xml',
                     'C:evil.xml'):   # 이름 속 NUL 은 zipfile 이 그 앞에서 자른다(경로로 쓰지 않음)
            with self.subTest(name=repr(name)):
                data = zp(GOOD + [('Contents/section0.xml', '<a/>')])
                data = data.replace(b'Contents/section0.xml', name.encode().ljust(21, b'_')[:21]) \
                    if len(name) <= 21 else zp(GOOD + [('Contents/section0.xml', '<a/>'), (name, '<a/>')])
                self.assertRejected(data)

    def test_duplicate_and_encrypted(self):
        dup = zp(GOOD + [('Contents/section0.xml', '<a/>'), ('Contents/section1.xml', '<b/>')]) \
            .replace(b'Contents/section1.xml', b'Contents/section0.xml')
        self.assertRejected(dup)
        enc = bytearray(hx('<a/>'))
        for sig, off in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
            pos = enc.find(sig)
            while pos >= 0:
                struct.pack_into('<H', enc, pos + off, enc[pos + off] | 1)
                pos = enc.find(sig, pos + 4)
        self.assertRejected(bytes(enc))


class Entities(Rejects):
    def test_xxe(self):
        self.assertRejected(hx(XXE), web=False)

    def test_billion_laughs(self):
        self.assertRejected(hx(LAUGHS), web=False)

    def test_doctype_in_any_xml_part(self):
        for name in ('Contents/header.xml', 'Contents/content.hpf', 'META-INF/container.xml', 'version.xml'):
            data = zp([('mimetype', 'application/hwp+zip'), ('Contents/section0.xml', '<a/>'), (name, XXE)])
            with self.subTest(name=name):
                self.assertRejected(data, web=False)

    def test_utf16_dtd(self):
        """UTF-16(BOM 있음·없음)·다른 인코딩 선언으로 '<!DOCTYPE' 바이트 검색을 피하려는 XML."""
        for body in (XXE.encode('utf-16'), XXE.encode('utf-16-be'),
                     XXE.replace('version="1.0"', 'version="1.0" encoding="UTF-16"').encode('utf-16-le'),
                     XXE.replace('version="1.0"', 'version="1.0" encoding="UTF-7"').encode(),
                     '<?xml version="1.0" encoding="ISO-8859-1"?><a/>'.encode()):
            with self.subTest(head=body[:8]):
                self.assertRejected(hx(body), web=False)

    def test_doctype_across_chunk_boundary(self):
        pad = b'<a>' + b' ' * (safety.CHUNK - 6)
        self.assertRejected(hx(pad + b'<!DOCTYPE r []></a>'), web=False)

    def test_parsers_do_not_resolve_entities(self):
        """두 번째 방어: 우리 파서(PARSER)와 python-hwpx 의 lxml 기본 설정 모두 외부 엔티티를 읽지 않는다.
        python-hwpx 의 opc 파서는 우리와 같은 설정(resolve_entities=False·no_network). 쓰기(hwp5.writer)는 lxml 기본값
        (lxml 5+: 내부 엔티티만, no_network, load_dtd=False) — 그 입력은 check_zip 을 통과한 바이트뿐이다."""
        for parser in (safety.PARSER, etree.XMLParser()):
            try:
                root = etree.fromstring(XXE.encode(), parser)
            except etree.XMLSyntaxError:
                continue
            self.assertNotIn(LEAK, ''.join(root.itertext()))
        from hwpx.opc import xml_utils
        try:   # python-hwpx opc 파서: 거부하거나, 읽어도 외부 엔티티를 펼치지 않음
            root = xml_utils.parse_xml(XXE.encode())
        except Exception:
            return
        self.assertNotIn(LEAK, ''.join(root.itertext()))


class OleBombs(Rejects):
    def test_compressed_stream_bomb(self):
        """압축 스트림 여러 개(각 64MB 0, 합 256MB). python-hwpx 는 스트림마다 256MB까지 풀어 총량 상한이 없다 →
        변환 전 check_ole 이 조각으로 세며 200MB 에서 멈춘다. 웹 판별(sniff)은 풀지 않으므로 worker 에서 거부."""
        z = zeros_deflated(64)
        data = ole((f'BodyText/Section{i}', z) for i in range(1, 5))
        self.assertLess(len(data), hwp.MAX_UPLOAD)
        self.assertEqual(hwp.sniff(data), 'hwp')
        self.assertRejected(data, web=False)

    def test_single_stream_bomb(self):
        """스트림 하나가 python-hwpx 상한(256MB)을 넘어도 우리 상한(200MB)에서 먼저 멈춘다."""
        data = ole([('BinData/BIN0001.bmp', zeros_deflated(300))])
        self.assertRejected(data, web=False)

    def test_too_many_streams(self):
        data = ole((f'BinData/B{i:04d}.bin', b'x') for i in range(hwp.MAX_ENTRIES))
        self.assertRejected(data, web=False)

    def test_check_ole_counts_without_holding(self):
        """세기만 하고 쌓지 않는다: 한도 바로 아래 스트림은 통과(풀린 바이트를 메모리에 두지 않음)."""
        safety.check_ole(ole([('BinData/BIN0001.bmp', zeros_deflated(150))]))

    def test_trailing_bytes_after_stream_end(self):
        """압축 스트림 끝 뒤에 바이트가 붙은 스트림(실제 표본 BinData 에 있음)에서 세기가 멈추지 않던 문제(E3 수정)."""
        c = zlib.compressobj(6, zlib.DEFLATED, -15)
        t0 = time.monotonic()
        raw = c.compress(b'abc' * 1000) + c.flush() + b'tail' * 3
        self.assertEqual(safety._inflated_size(raw, 10 * MB), 3000)
        self.assertEqual(safety._inflated_size(b'\xff' * 20, 10 * MB), 20)
        self.assertLess(time.monotonic() - t0, 1)

    def test_raw_bytes_count_toward_budget(self):
        """짧은 압축 + 긴 꼬리(또는 섹터를 공유한 큰 스트림)를 여러 개: 푼 크기가 작아도 읽은 원래 크기를 총량에 넣는다."""
        c = zlib.compressobj(6, zlib.DEFLATED, -15)
        z = c.compress(b'a') + c.flush() + bytes(300 * 1024)
        data = ole((f'BinData/B{i}.bin', z) for i in range(4))
        with mock.patch.object(safety, 'MAX_UNZIPPED', MB):
            with self.assertRaises(hwp.UnsafeFile):
                safety.check_ole(data)

    def test_to_hwp_rejects_dtd_before_writer(self):
        """python-hwpx hwp5.writer 는 lxml 기본 파서로 hwpx XML 을 읽는다 → to_hwp 도 먼저 check_zip."""
        parts = E.parts_of(roster())
        parts['Contents/header.xml'] = XXE.encode()
        with self.assertRaises(hwp.UnsafeFile):
            E.to_hwp(zp(list(parts.items())))

    def test_broken_ole(self):
        data = bytearray(E.to_hwp(roster()))
        data[0x30:0x34] = b'\xff\xff\xff\x7f'   # 디렉터리 시작 섹터를 망가뜨림
        with self.assertRaises(hwp.HwpError):
            hwp.to_hwpx(bytes(data))


SEC0 = 'Contents/section0.xml'
NS = ('xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
      'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"')


def with_section(data: bytes, sec: bytes) -> bytes:
    """data(hwpx)의 section0 만 sec 로 바꾼 hwpx."""
    parts = E.parts_of(data)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for n, b in parts.items():
            z.writestr(n, sec if n == SEC0 else b, zipfile.ZIP_STORED if n == 'mimetype' else zipfile.ZIP_DEFLATED)
    return out.getvalue()


def roster_sub(old: bytes, new: bytes, count=1) -> bytes:
    """명부 표본 section0 의 속성 값을 바꾼다(앞에서 count 개)."""
    sec = E.parts_of(roster())[SEC0]
    assert old in sec
    return with_section(roster(), sec.replace(old, new, count))


def tbl(R, C, inner='', spans=None, hdr=0) -> str:
    """R×C 표(위 hdr 줄만 글자, 나머지 빈 칸). spans={(행, 열): (rowSpan, colSpan)}, inner 는 (0,0) 칸 안에 넣을 표."""
    spans = spans or {}

    def tc(r, c):
        rs, cs = spans.get((r, c), (1, 1))
        body = inner if inner and (r, c) == (0, 0) else ('<hp:t>h</hp:t>' if r < hdr else '')
        return (f'<hp:tc><hp:subList><hp:p><hp:run>{body}</hp:run></hp:p></hp:subList>'
                f'<hp:cellAddr rowAddr="{r}" colAddr="{c}"/><hp:cellSpan rowSpan="{rs}" colSpan="{cs}"/></hp:tc>')
    rows = ''.join('<hp:tr>' + ''.join(tc(r, c) for c in range(C)) + '</hp:tr>' for r in range(R))
    return f'<hp:tbl rowCnt="{R}" colCnt="{C}">{rows}</hp:tbl>'


def sec_of(*tables: str) -> str:
    return f'<hs:sec {NS}>' + ''.join(f'<hp:p><hp:run>{t}</hp:run></hp:p>' for t in tables) + '</hs:sec>'


class DeclaredSizes(unittest.TestCase):
    """G3: 파일이 선언하는 표 크기(rowCnt·colCnt·cellAddr·cellSpan)를 그대로 루프 범위로 쓰면
    병합 값 하나만 키운 수십 KB 파일이 업로드 검사를 통과한 뒤 worker 를 멈춘다. analyze 에서 UnsafeFile."""

    def assertUnsafe(self, data):
        """표를 읽는 공개 함수가 모두 빨리 UnsafeFile. 문구에 문서 글자(명부 머리글) 없음."""
        calls = [('tables', lambda: hwp.tables(data)), ('candidates', lambda: hwp.candidates(data)),
                 ('schema', lambda: hwp.schema(data, SEC0, 1)),
                 ('fill', lambda: hwp.fill(data, SEC0, 1, {(1, 1): '가'})),
                 ('add_rows', lambda: hwp.add_rows(data, SEC0, 1, 1, 1)),
                 ('shrink', lambda: hwp.shrink(data, SEC0, 1, [(1, 1)], 1))]
        for name, fn in calls:
            t0 = time.monotonic()
            with self.assertRaises(hwp.UnsafeFile, msg=name) as cm:
                fn()
            self.assertLess(time.monotonic() - t0, 2, name)
            for word in ('성명', '번호', '담당', '교장'):
                self.assertNotIn(word, str(cm.exception), name)

    def test_span_bomb(self):
        """신고 재현: 칸 하나의 rowSpan·colSpan 만 키운 hwpx(이전엔 analyze 격자 루프에서 멈춤)."""
        for attr in (b'rowSpan', b'colSpan'):
            bomb = roster_sub(attr + b'="1"', attr + b'="2000000000"')
            self.assertLess(len(bomb), 64 * 1024)
            self.assertEqual(hwp.to_hwpx(bomb)[1], 'hwpx')   # 업로드 검사는 통과하는 파일이다
            self.assertUnsafe(bomb)

    def test_span_bomb_via_hwp(self):
        """hwp 로 올린 경우(퍼징 case_3_210: hwp 비트 뒤집기 → 변환 뒤 같은 루프)."""
        hx_, kind, _ = hwp.to_hwpx(E.to_hwp(roster_sub(b'rowSpan="1"', b'rowSpan="60000"')))
        self.assertEqual(kind, 'hwp')
        self.assertUnsafe(hx_)

    def test_span_past_table_edge(self):
        # 명부 표 5행×3열: 1행 1열에서 열 3칸·행 5칸 병합은 표 밖으로 넘친다
        self.assertUnsafe(with_section(roster(), sec_of(tbl(2, 3), tbl(5, 3, spans={(1, 1): (1, 3)})).encode()))
        self.assertUnsafe(with_section(roster(), sec_of(tbl(2, 3), tbl(5, 3, spans={(1, 1): (5, 1)})).encode()))

    def test_zero_negative_span_and_address(self):
        for old, new in [(b'rowSpan="1"', b'rowSpan="0"'), (b'colSpan="1"', b'colSpan="-5"'),
                         (b'rowAddr="0"', b'rowAddr="-1"'), (b'colAddr="0"', b'colAddr="99999999"')]:
            with self.subTest(new=new):
                self.assertUnsafe(roster_sub(old, new))

    def test_overlapping_cells(self):
        # 두 칸이 같은 자리를 덮는다(겹친 병합으로 격자 루프를 칸 수 × 표 크기로 키우는 것)
        self.assertUnsafe(roster_sub(b'colAddr="1"', b'colAddr="0"'))
        self.assertUnsafe(with_section(roster(), sec_of(tbl(2, 3), tbl(5, 3, spans={(0, 0): (5, 3)})).encode()))

    def test_declared_table_size(self):
        for old, new in [(b'rowCnt="2"', b'rowCnt="2000000000"'), (b'colCnt="3"', b'colCnt="1000000"'),
                         (b'rowCnt="2"', b'rowCnt="0"'), (b'colCnt="3"', b'colCnt="-3"')]:
            with self.subTest(new=new):
                self.assertUnsafe(roster_sub(old, new))
        self.assertEqual(len(hwp.tables(roster_sub(b'rowCnt="2"', b'rowCnt="1999"'))), 2)   # 상한 안이면 읽는다
        with mock.patch.object(E, 'MAX_TABLE_GRID', 14):    # 명부 표 5×3=15
            self.assertUnsafe(roster())

    def test_file_grid_budget(self):
        with mock.patch.object(E, 'MAX_FILE_GRID', 20):     # 결재란 6 + 명부 15 = 21
            self.assertUnsafe(roster())
        with mock.patch.object(E, 'MAX_FILE_GRID', 21):
            self.assertEqual(len(hwp.tables(roster())), 2)

    def test_nesting_depth(self):
        t = tbl(1, 1)
        for _ in range(E.MAX_TABLE_DEPTH - 1):
            t = tbl(2, 2, inner=t)
        hwp.tables(hx(sec_of(t)))                            # 상한 깊이까지는 읽는다
        with self.assertRaises(hwp.UnsafeFile):
            hwp.tables(hx(sec_of(tbl(2, 2, inner=t))))

    def test_largest_allowed_table_is_fast(self):
        """상한에 꽉 찬 표(행 2000 × 열 15, 머리글 1000행)도 분석·칸 매핑이 빨리 끝난다."""
        R, C = E.MAX_TABLE_ROWS, E.MAX_TABLE_GRID // E.MAX_TABLE_ROWS
        data = hx(sec_of(tbl(R, C, hdr=R // 2)))
        t0 = time.monotonic()
        s = hwp.schema(data, SEC0, 0)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((s['header_rows'], len(s['inputs'])), (R // 2, R * C // 2))


class RealDocumentsStillPass(unittest.TestCase):
    def test_roster_both_formats(self):
        self.assertEqual(hwp.to_hwpx(roster())[1], 'hwpx')
        self.assertEqual(hwp.to_hwpx(E.to_hwp(roster()))[1], 'hwp')


if __name__ == '__main__':
    unittest.main()
