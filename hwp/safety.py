"""업로드 판별과 안전 검사. 업로드는 신뢰하지 않는다.

- 형식은 확장자가 아니라 매직 바이트로 판별한다(PK=hwpx, D0CF11E0=hwp).
- 크기·zip 항목 수·풀린 총량에 상한을 두고, 경로 조작·암호화 항목·DTD/엔티티를 거부한다.
- 오류 문구에는 문서 내용을 넣지 않는다(항목 이름·숫자만).

상한은 양식 표본 1,376개 실측(hwpx 최대 17MB·풀린 총량 52MB·항목 67개, hwp 최대 13.8MB)에 여유를 둔 값이다.
"""
from __future__ import annotations

import io
import os
import re
import struct
import zipfile

from lxml import etree

MAX_UPLOAD = 30 * 1024 * 1024        # 올린 파일 하나
MAX_UNZIPPED = 200 * 1024 * 1024     # hwpx 풀린 총량(선언 크기 합)
MAX_ENTRIES = 1000                   # hwpx 항목 수
MAX_CELL_TEXT = 2000                 # 칸 하나에 넣을 글자 수

HWP_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

# 엔티티 해석·네트워크 끔. huge_tree도 끈다(python-hwpx 내부 파서와 같은 설정).
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)

# 게이트 ③(한컴 실기)이 실패하면 GT_HWP_OUTPUT=hwpx 한 줄로 hwp 원본도 hwpx로 돌려준다.
OUTPUT_ENV = 'GT_HWP_OUTPUT'


class HwpError(Exception):
    """hwp 패키지의 모든 오류. 문구에는 문서 내용이 없다."""


class UnsupportedFormat(HwpError):
    pass


class TooLarge(HwpError):
    pass


class UnsafeFile(HwpError):
    pass


class ConvertError(HwpError):
    pass


class FillError(HwpError):
    """채우기·검증 실패. problems 에는 (표·행·열 주소, 사유)만 담는다."""

    def __init__(self, msg: str, problems: list | None = None):
        super().__init__(msg)
        self.problems = problems or []


def detect(data: bytes) -> str:
    """'hwpx' | 'hwp'. 크기 초과·알 수 없는 형식이면 예외."""
    if not isinstance(data, (bytes, bytearray)):
        raise UnsupportedFormat('바이트가 아님')
    if len(data) > MAX_UPLOAD:
        raise TooLarge(f'파일이 {MAX_UPLOAD // (1024 * 1024)}MB를 넘음')
    if data[:2] == b'PK':
        return 'hwpx'
    if data[:8] == HWP_MAGIC:
        return 'hwp'
    raise UnsupportedFormat('hwp/hwpx 형식이 아님')


def _bad_name(n: str) -> bool:
    return (not n or n.startswith('/') or '\\' in n or ':' in n or '\x00' in n
            or any(p == '..' for p in n.split('/')))


# hwp(OLE) 고유 스트림 이름(디렉터리 항목은 UTF-16LE)과 FileHeader 서명. .doc·.xls 도 첫 8바이트는 같다.
HWP_DIR_NAME = 'FileHeader'.encode('utf-16-le')
HWP_SIGNATURE = b'HWP Document File'
HWPX_MIMETYPE = b'application/hwp+zip'
CHUNK = 1024 * 1024          # zip 항목 조각 읽기 크기(worker 메모리 절약)
MAX_CENTRAL_DIR = 1024 * 1024  # 중앙 디렉터리 크기(표본 최대 항목 67개라 수 KB)
EOCD = struct.Struct('<4s4H2LH')  # 서명, 디스크 번호 2, 항목 수 2, 디렉터리 크기·위치, 주석 길이


def _check_eocd(data: bytes) -> None:
    """ZipFile 이 중앙 디렉터리를 통째로 파싱하기 전에(목록 폭탄) 파일 끝 EOCD 만 직접 읽어 거른다.

    EOCD 는 끝에서 22바이트 + 주석(최대 65,535바이트) 안에 있다. ZIP64·분할 zip 은 받지 않는다.
    """
    pos = data.rfind(b'PK\x05\x06', max(0, len(data) - EOCD.size - 0xFFFF))
    if pos < 0 or len(data) - pos < EOCD.size:
        raise UnsafeFile('zip 구조 오류')
    _, disk, cd_disk, n_disk, n_total, cd_size, cd_off, _ = EOCD.unpack_from(data, pos)
    if (0xFFFF in (disk, cd_disk, n_disk, n_total) or 0xFFFFFFFF in (cd_size, cd_off)
            or b'PK\x06\x07' in data[max(0, pos - 20):pos]):
        raise UnsafeFile('ZIP64 zip')
    if disk or cd_disk or n_disk != n_total:
        raise UnsafeFile('분할 zip')
    if n_total > MAX_ENTRIES:
        raise UnsafeFile(f'zip 항목이 {MAX_ENTRIES}개를 넘음')
    if cd_size > MAX_CENTRAL_DIR or cd_size + cd_off > pos:
        raise UnsafeFile('zip 중앙 디렉터리 크기 오류')


def _open_zip(data: bytes) -> zipfile.ZipFile:
    _check_eocd(data)
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as e:
        raise UnsafeFile('zip 구조 오류') from e


def _check_listing(infos) -> None:
    """중앙 디렉터리 목록만 본다(압축은 풀지 않음): 항목 수·이름·암호화·선언 크기 합."""
    if len(infos) > MAX_ENTRIES:
        raise UnsafeFile(f'zip 항목이 {MAX_ENTRIES}개를 넘음')
    names = [i.filename for i in infos]
    if len(set(names)) != len(names):
        raise UnsafeFile('zip 항목 이름 중복')
    total = 0
    for i in infos:
        if _bad_name(i.filename):
            raise UnsafeFile('zip 항목 경로가 허용되지 않음')
        if i.flag_bits & 0x1:
            raise UnsafeFile('암호화된 zip 항목')
        total += i.file_size
        if total > MAX_UNZIPPED:
            raise UnsafeFile(f'풀린 크기가 {MAX_UNZIPPED // (1024 * 1024)}MB를 넘음')


def sniff(data: bytes) -> str:
    """웹 요청 안에서 쓰는 가벼운 판별: 'hwpx' | 'hwp'. 압축을 풀지 않는다(무거운 검사는 to_hwpx/check_zip, worker).

    hwpx: 목록 검사 + mimetype(작은 항목 하나만 읽음) == application/hwp+zip, Contents/ 항목 있음.
    hwp: OLE 디렉터리에 'FileHeader' 스트림 이름과 FileHeader 서명이 있음.
    """
    kind = detect(data)
    if kind == 'hwp':
        if HWP_DIR_NAME not in data or HWP_SIGNATURE not in data:
            raise UnsupportedFormat('hwp 문서가 아님(OLE)')
        return kind
    with _open_zip(data) as z:
        infos = z.infolist()
        _check_listing(infos)
        names = {i.filename: i for i in infos}
        m = names.get('mimetype')
        if m is None or not any(n.startswith('Contents/') for n in names):
            raise UnsupportedFormat('hwpx 문서가 아님(필수 항목 없음)')
        if m.file_size > 64 or m.compress_size > 64:
            raise UnsupportedFormat('hwpx 문서가 아님(mimetype)')
        try:
            with z.open(m) as fh:
                mt = fh.read(65)
        except Exception as e:
            raise UnsafeFile(f'mimetype: zip 항목 손상({type(e).__name__})') from e
        if mt.strip() != HWPX_MIMETYPE:
            raise UnsupportedFormat('hwpx 문서가 아님(mimetype)')
    return kind


def check_zip(data: bytes) -> None:
    """zip 폭탄·경로 조작·암호화·중복 이름·DTD를 거부한다. 통과하면 None.

    항목은 CHUNK 조각으로 끝까지 읽어(통째로 올리지 않음) CRC·선언 크기를 확인한다. zipfile 은 선언 크기
    이상을 내주지 않으므로 목록의 합계가 실제 상한이고, 크기를 속인 항목은 여기서 걸린다.
    """
    with _open_zip(data) as z:
        infos = z.infolist()
        _check_listing(infos)
        for i in infos:
            xml = i.filename.endswith(('.xml', '.hpf', '.rdf'))
            try:
                with z.open(i) as fh:
                    head, tail = b'', b''
                    while chunk := fh.read(CHUNK):
                        if xml:
                            if not head:
                                head = chunk
                                _check_xml_head(i.filename, chunk)
                            buf = tail + chunk
                            _check_xml_chunk(i.filename, buf)
                            tail = buf[-16:]  # '<!DOCTYPE'·'<!ENTITY' 가 조각 경계에 걸쳐도 찾게
            except HwpError:
                raise
            except Exception as e:  # BadZipFile(CRC·크기), zlib.error, NotImplementedError(압축 방식) 등
                raise UnsafeFile(f'{i.filename}: zip 항목 손상({type(e).__name__})') from e


_ENC_RE = re.compile(rb'^<\?xml[^>]*?encoding\s*=\s*["\']([A-Za-z0-9._-]+)["\']')


def _check_xml_head(name: str, body: bytes) -> None:
    """XML 은 UTF-8 만 받는다(한글 문서는 UTF-8). 그래야 바이트 검색으로 DTD/엔티티를 확실히 막는다."""
    if body.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise UnsafeFile(f'{name}: UTF-8 아닌 XML')
    m = _ENC_RE.match(body.removeprefix(b'\xef\xbb\xbf'))
    if m and m.group(1).lower().replace(b'_', b'-') not in (b'utf-8', b'utf8'):
        raise UnsafeFile(f'{name}: UTF-8 아닌 XML')


def _check_xml_chunk(name: str, body: bytes) -> None:
    if b'\x00' in body:
        raise UnsafeFile(f'{name}: UTF-8 아닌 XML')
    if b'<!DOCTYPE' in body or b'<!ENTITY' in body:
        raise UnsafeFile(f'{name}: DTD/엔티티 선언')


def output_format(kind: str) -> str:
    """돌려줄 형식. 기본은 올린 형식 그대로, GT_HWP_OUTPUT=hwpx 면 항상 hwpx."""
    return 'hwpx' if os.environ.get(OUTPUT_ENV, '').strip().lower() == 'hwpx' else kind
