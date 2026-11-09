"""P4 첨부 형식 판별(공지·일정 공통). 확장자가 아니라 내용으로 형식을 정하고, 뷰는 그 결과가 확장자와 같을 때만 받는다.

- 서버는 첨부를 풀지 않는다. zip은 hwp.sniff가 먼저 끝 EOCD·중앙 디렉터리 목록(항목 수·이름·암호화·선언 크기)을
  검사하고, 여기서는 목록과 작은 XML 항목([Content_Types].xml·*.rels 또는 mimetype)만 크기 상한을 두고 읽는다.
  XML은 엔티티·네트워크를 끈 파서로 해석한다(문자 참조·주석으로 검사를 피하지 못하게).
- OLE(doc·xls·ppt)는 디렉터리 항목 이름을 보고, xls는 Workbook 스트림의 전역 부분 BIFF 기록만 훑는다(XLM 매크로 시트).
- 매크로 문서(MACRO)와 외부 대상 연결·포함 OLE 개체가 든 문서(EMBED)는 hwp.UnsafeFile.
  ppt는 VBA가 본문 스트림 안에 들어 있어 이름으로는 못 거른다(내려받기는 첨부로만).
"""
import io
import re
import struct
import zipfile

from hwpx.hwp5.cfb import CompoundFile
from lxml import etree

import hwp

TYPES = {
    "hwp": "application/x-hwp", "hwpx": "application/hwp+zip", "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel", "doc": "application/msword", "ppt": "application/vnd.ms-powerpoint",
    "odt": "application/vnd.oasis.opendocument.text", "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "jpg": "image/jpeg", "png": "image/png", "gif": "image/gif",
}
ALIAS = {"jpeg": "jpg"}
LABEL = {"hwp": "한글", "hwpx": "한글", "pdf": "PDF", "xlsx": "엑셀", "xls": "엑셀", "docx": "워드", "doc": "워드",
         "pptx": "파워포인트", "ppt": "파워포인트", "odt": "오픈문서", "ods": "오픈문서", "odp": "오픈문서",
         "jpg": "그림", "png": "그림", "gif": "그림"}
ACCEPT = ",".join(f".{k}" for k in [*TYPES, *ALIAS])  # input accept 속성

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
IMAGES = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"))
OOXML = {  # 주 문서 부분(Override)의 content type. 서식 파일(.xltx 등)·쇼(.ppsx)·매크로판은 여기 없다
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": "pptx",
}
ODF = {b"application/vnd.oasis.opendocument.text": "odt", b"application/vnd.oasis.opendocument.spreadsheet": "ods",
       b"application/vnd.oasis.opendocument.presentation": "odp"}
OLE_MAIN = {"WORDDOCUMENT": "doc", "WORKBOOK": "xls", "BOOK": "xls", "POWERPOINT DOCUMENT": "ppt"}
OLE_MACRO = {"_VBA_PROJECT", "_VBA_PROJECT_CUR", "MACROS", "VBA"}
# xlsx 안의 Excel 4.0 매크로 시트·대화상자 시트(content type 끝 이름)
XLM_TYPES = ("macrosheet+xml", "intlmacrosheet+xml", "dialogsheet+xml")
# 외부 대상이면 문서를 열 때 밖에서 무언가를 불러오는 관계(하이퍼링크·외부 그림은 둔다)
EXTERNAL_DENY = {"attachedtemplate", "oleobject", "frame", "subdocument", "package"}
LOCAL_PATH = re.compile(r"^(file:///)?[a-z]:[\\/]", re.I)  # 내 PC 서식 경로(file:///C:/...) — 흔하고 밖에서 받지 않음
MAX_XML = 256 * 1024          # 읽는 XML 항목 하나의 선언·압축 크기 상한(보통 수 KB)
MAX_RELS_TOTAL = 4 * 1024 * 1024  # *.rels 전체 읽기 상한
MAX_BIFF_RECORDS = 200_000    # xls 전역 부분 기록 수 상한(보통 수천)
BOUNDSHEET, BIFF_EOF = 0x0085, 0x000A
MACRO = "매크로가 든 문서"
EMBED = "외부 연결·포함 개체가 든 문서"
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)


def judge(f):
    """올린 파일의 내용으로 본 형식(TYPES 키). 모르는 형식은 "". 위장·폭탄·매크로면 hwp.UnsafeFile."""
    f.seek(0)
    head = f.read(8)
    if head[:5] == b"%PDF-":
        return "pdf"
    for magic, kind in IMAGES:
        if head.startswith(magic):
            return kind
    if head[:2] != b"PK" and head != OLE:
        return ""
    f.seek(0)
    data = f.read()
    try:
        return hwp.sniff(data)  # 한글이면 여기서 끝. zip이면 목록 검사까지 마친 뒤 UnsupportedFormat
    except hwp.UnsafeFile:
        raise
    except hwp.HwpError:
        pass
    return _ole(data) if head == OLE else _zip(data)


def _ole(data):
    try:
        cf = CompoundFile(data)
        names = {e.name.upper() for e in cf.entries}
        paths = [p.upper() for p in cf.stream_paths()]
    except Exception:  # 깨진 OLE
        return ""
    if names & OLE_MACRO:
        raise hwp.UnsafeFile(MACRO)
    # 포함 개체: ObjectPool 아래 스트림(빈 ObjectPool 저장소는 Word가 늘 만들어 두므로 둔다)·OLE1 꾸러미
    if any(p.startswith("OBJECTPOOL/") or p.rsplit("/", 1)[-1] == "\x01OLE10NATIVE" for p in paths):
        raise hwp.UnsafeFile(EMBED)
    kinds = {OLE_MAIN[n] for n in names if n in OLE_MAIN}
    if len(kinds) != 1:
        return ""
    kind = kinds.pop()
    if kind == "xls":
        book = next((p for p in cf.stream_paths() if p.upper() in ("WORKBOOK", "BOOK")), None)
        try:
            stream = cf.read(book) if book else b""
        except Exception:
            return ""
        _check_biff(stream)
    return kind


def _check_biff(stream):
    """Workbook 전역 부분(첫 EOF까지)의 BOUNDSHEET에서 시트 종류가 매크로 시트(1)·VBA 모듈(6)이면 거부.
    기록 수 상한을 넘거나 EOF 없이 끝나면 확인할 수 없으므로 거부한다."""
    pos, n = 0, 0
    while pos + 4 <= len(stream):
        rec, size = struct.unpack_from("<HH", stream, pos)
        pos += 4
        if rec == BIFF_EOF:
            return
        if rec == BOUNDSHEET and size >= 6 and pos + 6 <= len(stream) and stream[pos + 5] in (1, 6):
            raise hwp.UnsafeFile(MACRO)
        pos += size
        n += 1
        if n > MAX_BIFF_RECORDS:
            break
    raise hwp.UnsafeFile("xls 구조 오류")


def _small(z, info, cap):
    if info.file_size > cap or info.compress_size > cap:
        return None
    try:
        with z.open(info) as fh:
            return fh.read(cap + 1)
    except Exception as e:
        raise hwp.UnsafeFile(f"zip 항목 손상({type(e).__name__})") from e


def _xml(z, info, cap=MAX_XML):
    """작은 XML 항목 → 뿌리 요소. 크거나 해석 못 하거나 DOCTYPE이 있으면 None."""
    raw = _small(z, info, cap)
    if raw is None:
        return None
    try:
        root = etree.fromstring(raw, PARSER)
    except etree.XMLSyntaxError:
        return None
    return None if root.getroottree().docinfo.doctype else root


def _local(el):
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _ooxml(z, infos):
    """[Content_Types].xml의 Default·Override ContentType으로 형식을 정하고, 매크로·외부 연결·포함 OLE를 거른다."""
    names = [n.lower() for n in infos]
    if any(n.rsplit("/", 1)[-1] == "vbaproject.bin" for n in names):
        raise hwp.UnsafeFile(MACRO)
    if any(n.startswith(("xl/macrosheets/", "xl/dialogsheets/")) for n in names):
        raise hwp.UnsafeFile(MACRO)
    if any("/embeddings/oleobject" in "/" + n for n in names):
        raise hwp.UnsafeFile(EMBED)
    root = _xml(z, infos["[Content_Types].xml"])
    if root is None:
        return ""
    types = [(_local(el), el.get("ContentType", "")) for el in root.iter()]
    if any("macroenabled" in t.lower() or "vbaproject" in t.lower() or t.lower().endswith(XLM_TYPES) for _, t in types):
        raise hwp.UnsafeFile(MACRO)
    kinds = {OOXML[t] for tag, t in types if tag == "Override" and t in OOXML}
    if len(kinds) != 1:
        return ""
    budget = MAX_RELS_TOTAL
    for name, info in infos.items():
        low = name.lower()
        if low.startswith("xl/externallinks/") and low.endswith(".xml"):
            # 다른 통합 문서를 참조하는 수식은 받고, DDE·OLE 연결(열 때 프로그램 실행 경로)만 거부한다
            budget -= info.file_size
            link = _xml(z, info, MAX_RELS_TOTAL) if budget >= 0 else None
            if link is None:
                raise hwp.UnsafeFile("외부 연결 정보를 읽지 못함")
            if any(_local(el) in ("ddeLink", "oleLink") for el in link.iter()):
                raise hwp.UnsafeFile(EMBED)
            continue
        if not low.endswith(".rels"):
            continue
        budget -= info.file_size  # 하이퍼링크 많은 시트는 하나가 수백 KB라 항목별이 아니라 합계로 묶는다
        rels = _xml(z, info, MAX_RELS_TOTAL) if budget >= 0 else None
        if rels is None:
            raise hwp.UnsafeFile("관계 정보를 읽지 못함")
        for el in rels.iter():
            if _local(el) != "Relationship" or (el.get("TargetMode") or "").lower() != "external":
                continue
            kind = (el.get("Type") or "").rsplit("/", 1)[-1].lower()
            if kind in EXTERNAL_DENY and not (kind == "attachedtemplate" and LOCAL_PATH.match(el.get("Target") or "")):
                raise hwp.UnsafeFile(EMBED)
    return kinds.pop()


def _zip(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return ""
    with z:
        infos = {i.filename: i for i in z.infolist()}
        if len(infos) > hwp.MAX_ENTRIES:
            raise hwp.UnsafeFile("zip 항목이 너무 많음")
        if "[Content_Types].xml" in infos:
            return _ooxml(z, infos)
        if "mimetype" in infos:
            if any(n.lower().startswith(("basic/", "scripts/")) for n in infos):
                raise hwp.UnsafeFile(MACRO)
            mt = _small(z, infos["mimetype"], 100)
            return ODF.get(mt.strip(), "") if mt else ""
    return ""
