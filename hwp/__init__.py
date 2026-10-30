"""고티처 한글(HWP/HWPX) 엔진. Django 없이 바이트를 받아 바이트나 dict 를 돌려준다.

흐름(meeting/27 4-2장)
    kind = sniff(upload_bytes)                      # 웹 요청 안: 매직 바이트·크기·가벼운 구조(압축 안 풂)
    hwpx, kind, report = to_hwpx(upload_bytes)      # 판별·제한·zip 가드·hwp→HWPX 변환
    cands = candidates(hwpx)                        # 표 후보 점수순(자동 확정 금지, 1순위 적중 약 70%)
    outline = tables(hwpx)                          # 모든 표의 구조 요약(글자 없음) — 미리보기 겹침 레이어 짝 맞추기
    s = schema(hwpx, section, index)                # 교사가 고른 표의 입력 칸·라벨
    out = fill(hwpx, section, index, values)        # values={(r, c): '값'}. 재파싱 검증 통과해야 반환
    if output_format(kind) == 'hwp':                # 기본은 올린 형식. GT_HWP_OUTPUT=hwpx 면 hwpx
        out_hwp = to_hwp(out, section, index, values)   # HWP 5.0 다시 쓰고 되읽어 값 확인

오류는 모두 HwpError 하위 형식이고, 문구·problems 에는 표·행·열 주소만 담긴다.
"""
from __future__ import annotations

import functools
import io as _io
import zipfile as _zipfile

from . import engine as _e
from .safety import (MAX_CELL_TEXT, MAX_ENTRIES, MAX_UNZIPPED, MAX_UPLOAD, ConvertError,
                     FillError, HwpError, TooLarge, UnsafeFile, UnsupportedFormat, detect,
                     output_format, sniff)

__all__ = [
    'detect', 'sniff', 'to_hwpx', 'candidates', 'tables', 'schema', 'fill', 'to_hwp', 'verify', 'zip_ok', 'output_format',
    'HwpError', 'UnsupportedFormat', 'TooLarge', 'UnsafeFile', 'ConvertError', 'FillError',
    'MAX_UPLOAD', 'MAX_UNZIPPED', 'MAX_ENTRIES', 'MAX_CELL_TEXT',
]



def _guard(fn):
    """공개 함수 경계: 손상 파일에서 나는 파싱 예외(BadZipFile·XMLSyntaxError·KeyError 등)를
    HwpError 로 바꾼다. 문구에는 예외 종류만 남긴다(문서 내용 없음)."""
    @functools.wraps(fn)
    def wrap(*a, **kw):
        try:
            return fn(*a, **kw)
        except HwpError:
            raise
        except MemoryError:
            raise
        except Exception as e:
            raise HwpError(f'{fn.__name__} 실패({type(e).__name__})') from e
    return wrap


to_hwpx = _guard(_e.to_hwpx)
verify = _guard(_e.verify)
zip_ok = _guard(_e.zip_ok)


@_guard
def candidates(hwpx: bytes) -> list[dict]:
    """채울 수 있는 표 후보를 점수순으로. 각 항목은 표 주소(section, index)와 크기·점수·앞 문단."""
    return [{'section': t.section, 'index': t.index, 'rows': t.rows, 'cols': t.cols,
             'score': t.score, 'nested': t.nested, 'before_text': t.before_text}
            for t in _e.pick_table(_e.analyze(hwpx))]


@_guard
def tables(hwpx: bytes) -> list[dict]:
    """모든 표(중첩 포함, 문서 순서)의 구조 요약. 글자는 담지 않는다(DB에 둬도 문서 내용이 없게).
    sec 는 구역 순번(rhwp 의 secIdx 와 같음), parent 는 품은 표의 index(중첩 표만),
    rank 는 candidates 순위(후보가 아니면 None).
    브라우저 미리보기가 rhwp 표와 (구역, 순서, 행·열·칸 수)로 짝을 맞춘다."""
    ts = _e.analyze(hwpx)
    with _zipfile.ZipFile(_io.BytesIO(hwpx)) as z:
        secs = _e.section_names(z.namelist())   # 표 없는 구역도 순번에 넣는다
    rank = {(t.section, t.index): r for r, t in enumerate(_e.pick_table(ts))}
    return [{'section': t.section, 'sec': secs.index(t.section), 'index': t.index, 'rows': t.rows,
             'cols': t.cols, 'cells': len(t.cells), 'nested': t.nested, 'parent': t.parent, 'role': t.role,
             'rank': rank.get((t.section, t.index))} for t in ts]


@_guard
def schema(hwpx: bytes, section: str, index: int) -> dict:
    """고른 표의 입력 칸 목록. inputs[i] = {row, col, rowSpan, colSpan, label, unit}.
    col_heads[c] = 열 머리글, row_heads = [[행, 행 머리 글자], ...](자료 행만). 칸 확인(E6) 화면이 쓴다."""
    return _e.schema(_e.find_table(_e.analyze(hwpx), section, index))


@_guard
def fill(hwpx: bytes, section: str, index: int, values: dict) -> bytes:
    """values {(row, col): 글자} 를 그 표에 넣은 hwpx 를 돌려준다.

    verify(값·구조·다른 칸·다른 표·다른 파트)와 zip_ok 를 통과한 결과만 돌려준다.
    실패하면 FillError(problems=[((구역, 표, 행, 열), 사유)]).
    """
    tables = _e.analyze(hwpx)
    t = _e.find_table(tables, section, index)
    vals = _e.check_values(t, values)
    out = _e.fill_lib(hwpx, section, index, vals) if vals else hwpx
    v = _e.verify(hwpx, out, section, index, vals, tables_before=tables)
    if v['ok']:
        return out
    whole = (section, index, None, None)
    problems = [((section, index) + k, 'value mismatch') for k in v['values_bad']]
    problems += [(whole, f'{key}: {x}') for key in ('zip_errors', 'parts_added_removed') for x in v[key]]
    problems += [(whole, f'{key} false') for key in ('table_count_same', 'structure_same') if not v[key]]
    problems += [(whole, f'{key}={v[key]}') for key in ('other_cells_changed', 'other_tables_changed') if v[key]]
    if not set(v['parts_changed']) <= {section}:
        problems.append((whole, 'other parts changed'))
    raise FillError('채운 결과 검증 실패', problems)


@_guard
def to_hwp(filled_hwpx: bytes, section: str, index: int, values: dict) -> bytes:
    """fill 결과를 HWP 5.0 으로 다시 쓴다. 되읽어 values 가 그대로일 때만 돌려준다.

    values 는 fill 에 넘긴 것과 같게. {} 이면 채우지 않은 왕복이다.
    """
    vals = _e.check_values(_e.find_table(_e.analyze(filled_hwpx), section, index), values)
    try:
        out = _e.to_hwp(filled_hwpx)
    except Exception as e:
        raise FillError(f'HWP 쓰기 실패({type(e).__name__})') from e
    bad = _e.verify_hwp(out, section, index, vals)
    if bad:
        raise FillError('HWP 재읽기 값 불일치', [((section, index) + tuple(k), 'value mismatch') for k in bad])
    return out
