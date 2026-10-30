"""한글 양식 엔진. research/hwp_poc/troad_hwp.py 를 옮긴 것이다(분석·스키마·채우기·검증).

POC와 달라진 점은 서비스에 필요한 것뿐이다.
- 입력은 경로가 아니라 바이트. 먼저 safety 의 판별·제한·zip 가드를 통과해야 한다.
- 채운 결과는 verify(재파싱)·zip_ok 를 모두 통과해야 돌려준다. 아니면 FillError.
- 비교용 fill_own·repack·xml_diff_stats 는 옮기지 않았다(POC에 근거로 남아 있다).
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from .safety import (MAX_CELL_TEXT, PARSER, ConvertError, FillError, HwpError,
                     check_zip, detect)

HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
SEC_RE = re.compile(r'Contents/section(\d+)\.xml$')
# 결재란: 칸 글자(띄어쓰기 뺀 것)가 직함 그대로인 칸. 본문 표 머리글의 '담당'·'담당 업무'나
# 글상자 문장 속 '교장' 같은 것은 결재란이 아니다(E5: 안건 제출서·명단 표가 결재란으로 빠지던 문제).
APPROVAL_CELL = re.compile(r'^(결재|전결|대결|담당|담당자|기안|기안자|계|교감|교장|학교장|원감|원장|협조|검토|확인|'
                           r'담임|담임교사|주무관|장학사|장학관|국장|과장|팀장|\S{0,6}부장|\S{0,4}실장)$')
APPROVAL_SIGN = {'결재', '전결', '대결'}
HEADER_HINT = re.compile(r'성\s*명|이\s*름|반|번\s*호|학\s*년|비\s*고|연\s*번|소\s*속|직\s*위|구\s*분|순|과\s*목|희망|여\s*부|인\s*원|계')
UNIT = re.compile(r'^\s*(명|개|원|천원|%|회|시간|건|일|학급|권|매)\s*$')
TOTAL = re.compile(r'^\s*(합\s*계|소\s*계|총\s*계|계)\s*$')
# XML 1.0 에 넣을 수 없는 제어 문자(탭·줄바꿈 제외)
BAD_CHARS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')


# ---------------------------------------------------------------- 입출력
def to_hwpx(data: bytes) -> tuple[bytes, str, dict]:
    """올린 바이트 → (hwpx 바이트, 원본 형식 'hwp'|'hwpx', 변환 보고).

    변환 보고: {'unconverted': [...], 'dropped': [...]} — 요소 종류 이름만 담긴다.
    """
    kind = detect(data)
    if kind == 'hwpx':
        check_zip(data)
        return bytes(data), kind, {}
    from hwpx.hwp5.package import convert, to_hwpx_bytes
    try:
        c = convert(bytes(data))
        hwpx = to_hwpx_bytes(c.files)
    except HwpError:
        raise
    except Exception as e:  # 손상·암호·배포용 등. 내용 대신 예외 종류만.
        raise ConvertError(f'hwp 변환 실패({type(e).__name__})') from e
    check_zip(hwpx)
    rep = {'unconverted': sorted(set(map(str, c.report.unconverted or []))),
           'dropped': sorted(set(map(str, c.report.dropped or [])))}
    return hwpx, kind, rep


def parts_of(hwpx: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(hwpx)) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}


def section_names(parts) -> list[str]:
    return sorted([n for n in parts if SEC_RE.match(n)], key=lambda n: int(SEC_RE.match(n).group(1)))


def to_hwp(hwpx: bytes) -> bytes:
    from hwpx.hwp5.writer import write_hwp5
    return write_hwp5(parts_of(hwpx))


# ---------------------------------------------------------------- 분석
@dataclass
class Cell:
    row: int
    col: int
    rs: int
    cs: int
    text: str
    has_nested: bool
    border: str
    has_obj: bool = False   # 메모·도형·글상자·그림 등 개체가 든 칸(빈 칸처럼 보여도 입력 칸 아님)


@dataclass
class Table:
    section: str
    index: int            # 섹션 안 문서 순서(중첩 표 포함) — fill_cells 의 table_index 와 같음
    rows: int
    cols: int
    cells: list[Cell]
    nested: bool          # 다른 표의 칸 안에 들어 있는 표
    has_child: bool       # 칸 안에 다른 표를 품은 표(틀)
    before_text: str = ''  # 표 바로 앞 문단 글자(제목 추정)
    parent: int | None = None  # 이 표를 품은 가장 가까운 표의 index(중첩 표만)
    role: str = ''
    score: float = 0.0
    grid: dict = field(default_factory=dict)


def _cell_text(tc) -> str:
    out = []
    sub = tc.find(HP + 'subList')
    if sub is None:
        return ''
    for p in sub.findall(HP + 'p'):
        line = []
        for run in p.findall(HP + 'run'):
            for t in run.findall(HP + 't'):
                line.append(''.join(t.itertext()))
        out.append(''.join(line))
    return '\n'.join(out).strip()


OBJ_TAGS = {HP + x for x in ('rect', 'ellipse', 'container', 'pic', 'line', 'polygon', 'curve', 'arc',
                              'textart', 'ole', 'equation', 'fieldBegin', 'video', 'chart', 'connectLine')}


def _has_obj(tc) -> bool:
    sub = tc.find(HP + 'subList')
    return sub is not None and any(e.tag in OBJ_TAGS for e in sub.iter())


def _para_text(p) -> str:
    return ''.join(''.join(t.itertext()) for run in p.findall(HP + 'run') for t in run.findall(HP + 't'))


def analyze(hwpx: bytes) -> list[Table]:
    parts = parts_of(hwpx)
    tables: list[Table] = []
    for sec in section_names(parts):
        root = etree.fromstring(parts[sec], PARSER)
        last_text = ''
        idx = 0
        seen = {}  # 표 요소 → index (중첩 표의 부모 찾기)
        # 문서 순서로 문단과 표를 훑는다(표 앞 제목 추정용)
        for el in root.iter(HP + 'p', HP + 'tbl'):
            if el.tag == HP + 'p':
                if not any(a.tag == HP + 'tc' for a in el.iterancestors()):
                    t = _para_text(el).strip()
                    if t:
                        last_text = t
                continue
            cells = []
            for tr in el.findall(HP + 'tr'):
                for tc in tr.findall(HP + 'tc'):
                    a = tc.find(HP + 'cellAddr')
                    s = tc.find(HP + 'cellSpan')
                    cells.append(Cell(int(a.get('rowAddr')), int(a.get('colAddr')),
                                      int(s.get('rowSpan', 1)) if s is not None else 1,
                                      int(s.get('colSpan', 1)) if s is not None else 1,
                                      _cell_text(tc), tc.find('.//' + HP + 'tbl') is not None,
                                      tc.get('borderFillIDRef', ''), _has_obj(tc)))
            t = Table(sec, idx, int(el.get('rowCnt', 0)), int(el.get('colCnt', 0)), cells,
                      nested=any(a.tag == HP + 'tc' for a in el.iterancestors()),
                      parent=next((seen[a] for a in el.iterancestors(HP + 'tbl') if a in seen), None),
                      has_child=el.find('.//' + HP + 'tbl') is not None,
                      before_text=last_text[:60])
            for c in cells:
                for dr in range(c.rs):
                    for dc in range(c.cs):
                        t.grid[(c.row + dr, c.col + dc)] = c
            _classify(t)
            tables.append(t)
            seen[el] = idx
            idx += 1
    return tables


def _is_approval(t: Table) -> bool:
    """작은 표(3행·12칸 이하)에 '결재·전결' 칸이 있거나 서로 다른 직함 칸이 2개 이상."""
    if t.rows > 3 or len(t.cells) > 12:
        return False
    titles = {x for x in (re.sub(r'\s+', '', c.text) for c in t.cells) if APPROVAL_CELL.match(x)}
    return bool(titles & APPROVAL_SIGN) or len(titles) >= 2


def _classify(t: Table):
    alltext = ' '.join(c.text for c in t.cells)
    empties = [c for c in t.cells if not c.text and not c.has_nested and not c.has_obj]
    # 칸 안에 표를 품은 바깥 틀(자체 입력 칸 거의 없음). 3행 이하에 표를 품은 칸이 너비 전체이면
    # 빈 칸 몇 개는 모서리 장식이다('참고' 상자 등, E5: 결재란 규칙을 좁히자 1순위로 올라오던 것).
    corner = t.rows <= 3 and len(empties) <= 4 and any(c.has_nested and c.cs == t.cols for c in t.cells)
    if t.has_child and (len(empties) < 2 or corner):
        t.role = 'frame'
    elif _is_approval(t):
        t.role = 'approval'       # 결재란
    elif t.rows <= 1 or t.cols <= 1 or len(t.cells) <= 2:
        t.role = 'layout'         # 제목 상자·글상자 대용
    elif not empties:
        t.role = 'filled'         # 빈 칸 없음(안내 표 등)
    else:
        t.role = 'data'
    if t.role == 'data':
        hints = len(HEADER_HINT.findall(alltext))
        t.score = len(empties) + 2 * min(hints, 5) + t.rows * 0.5 + (3 if re.search(r'명단|명부|현황|조사|신청', t.before_text) else 0)


def pick_table(tables: list[Table]) -> list[Table]:
    """취합용 표 후보를 점수순으로. 1순위 적중률은 약 70%라 확정은 사람이 한다."""
    return sorted([t for t in tables if t.role == 'data'], key=lambda t: -t.score)


def find_table(tables: list[Table], section: str, index: int) -> Table:
    for t in tables:
        if t.section == section and t.index == index:
            return t
    raise FillError('표 없음', [((section, index, None, None), 'table not found')])


# ---------------------------------------------------------------- 칸 매핑
def schema(t: Table) -> dict:
    """머리글 행 수, 행 머리 열 수, 입력 칸(라벨 포함)을 추정한다."""
    R, C = t.rows, t.cols
    anchors = {(c.row, c.col): c for c in t.cells}

    def row_full(r):
        cs = [c for (rr, cc), c in anchors.items() if rr == r]
        return cs and all(c.text for c in cs)

    hdr = 0
    while hdr < R - 1 and row_full(hdr):
        hdr += 1
    data_rows = range(hdr, R)
    # 행 머리 열: 자료 행들에서 '항상 글자가 있는' 왼쪽 열들
    lab = 0
    for c in range(C):
        col_cells = {id(t.grid.get((r, c))): t.grid.get((r, c)) for r in data_rows if t.grid.get((r, c))}
        if col_cells and all(x.text for x in col_cells.values()):
            lab += 1
        else:
            break
    kind = 'list' if hdr >= 1 and (R - hdr) >= 2 else 'kv'
    inputs = []
    total_rows = {r for (r, c), x in anchors.items() if c < max(lab, 1) and TOTAL.match(x.text or '')}
    for (r, c), cell in sorted(anchors.items()):
        unit = UNIT.match(cell.text or '')
        if (cell.text and not unit) or cell.has_nested or cell.has_obj or r < hdr:
            continue
        if r in total_rows:      # 합계 행은 입력이 아니라 자동 계산 대상
            continue
        col_lab = []
        for hr in range(hdr):
            h = t.grid.get((hr, c))
            if h and h.text and (not col_lab or col_lab[-1] != h.text):
                col_lab.append(' '.join(h.text.split()))
        row_lab = []
        for cc in range(c):
            x = t.grid.get((r, cc))
            if x and x.text and x is not cell and (not row_lab or row_lab[-1] != x.text):
                row_lab.append(' '.join(x.text.split()))
        if kind == 'list' and not row_lab:
            row_lab = [f'{r - hdr + 1}행']
        if kind == 'kv':
            row_lab = row_lab[-1:]
            if not row_lab:  # 왼쪽이 비면 위 칸을 라벨로
                up = t.grid.get((r - 1, c)) if r else None
                row_lab = [' '.join(up.text.split())] if up and up.text else []
        label = ' / '.join(x for x in [' · '.join(row_lab), ' > '.join(col_lab)] if x) or f'({r},{c})'
        inputs.append({'row': r, 'col': c, 'rowSpan': cell.rs, 'colSpan': cell.cs, 'label': label,
                       'unit': unit.group(1) if unit else ''})
    # 같은 라벨이 여러 칸이면(예: 1반 행이 3줄) 순번을 붙인다
    seen = {}
    for i in inputs:
        seen[i['label']] = seen.get(i['label'], 0) + 1
    cnt = {}
    for i in inputs:
        if seen[i['label']] > 1:
            cnt[i['label']] = cnt.get(i['label'], 0) + 1
            i['label'] += f" #{cnt[i['label']]}"
    return {'section': t.section, 'index': t.index, 'rows': R, 'cols': C,
            'kind': kind, 'header_rows': hdr, 'label_cols': lab, 'total_rows': sorted(total_rows), 'inputs': inputs}


# ---------------------------------------------------------------- 채우기
def check_values(t: Table, values: dict) -> dict:
    """values {(row,col): 글자} 를 검사하고 제어 문자를 지운 사본을 돌려준다. 문제는 주소로만 알린다."""
    if not isinstance(values, dict):
        raise FillError('values 는 {(행,열): 글자} 사전이어야 함')
    anchors = {(c.row, c.col): c for c in t.cells}
    out, problems = {}, []
    for k, v in values.items():
        addr = (t.section, t.index) + (tuple(k) if isinstance(k, tuple) else (k,))
        if not (isinstance(k, tuple) and len(k) == 2 and all(isinstance(x, int) for x in k)):
            problems.append((addr, 'bad address'))
        elif k not in anchors:
            problems.append((addr, 'not an anchor cell'))
        elif anchors[k].has_nested or anchors[k].has_obj:
            problems.append((addr, 'cell holds table/object'))
        elif not isinstance(v, str):
            problems.append((addr, 'value not str'))
        elif len(v) > MAX_CELL_TEXT:
            problems.append((addr, f'value longer than {MAX_CELL_TEXT}'))
        else:
            out[k] = BAD_CHARS.sub('', v)
    if problems:
        raise FillError(f'입력 칸 오류 {len(problems)}건', problems)
    return out


def fill_lib(hwpx: bytes, section: str, table_index: int, values: dict) -> bytes:
    """python-hwpx 의 바이트 보존 셀 채우기. values: {(row,col): text}"""
    from hwpx.table_patch import fill_cells
    cells = [{'section_path': section, 'table_index': table_index, 'row': r, 'col': c, 'text': v}
             for (r, c), v in values.items()]
    res = fill_cells(hwpx, cells)
    if res.skipped:
        raise FillError(f'건너뛴 칸 {len(res.skipped)}건',
                        [((s.section_path, s.table_index, s.row, s.col), s.reason) for s in res.skipped])
    if not res.open_safety.get('ok', True):
        raise FillError('open_safety 실패')
    return res.data


# ---------------------------------------------------------------- 검증
def zip_ok(hwpx: bytes) -> list[str]:
    errs = []
    with zipfile.ZipFile(io.BytesIO(hwpx)) as z:
        il = z.infolist()
        if il[0].filename != 'mimetype' or il[0].compress_type != zipfile.ZIP_STORED:
            errs.append('mimetype 첫 항목/무압축 아님')
        if 'mimetype' not in z.namelist() or z.read('mimetype') != b'application/hwp+zip':
            errs.append('mimetype 값 이상')
        for need in ('Contents/content.hpf', 'META-INF/container.xml', 'version.xml', 'Contents/header.xml'):
            if need not in z.namelist():
                errs.append(f'{need} 없음')
        if z.testzip() is not None:
            errs.append('CRC 오류')
        for n in z.namelist():
            if n.endswith('.xml') or n.endswith('.hpf'):
                try:
                    etree.fromstring(z.read(n), PARSER)
                except Exception as e:
                    errs.append(f'{n} XML 오류({type(e).__name__})')
    return errs


def verify(before: bytes, after: bytes, section: str, table_index: int, values: dict,
           tables_before: list[Table] | None = None) -> dict:
    """채운 값이 정확히 그 칸에만 들어갔고, 표 구조·다른 칸·다른 파트가 그대로인지."""
    res = {'zip_errors': zip_ok(after)}
    tb = [t for t in (tables_before or analyze(before)) if t.section == section]
    ta = [t for t in analyze(after) if t.section == section]
    res['table_count_same'] = len(tb) == len(ta)
    B, A = tb[table_index], ta[table_index]
    res['structure_same'] = [(c.row, c.col, c.rs, c.cs, c.border) for c in B.cells] == [(c.row, c.col, c.rs, c.cs, c.border) for c in A.cells]
    amap = {(c.row, c.col): c.text for c in A.cells}
    bmap = {(c.row, c.col): c.text for c in B.cells}
    res['values_bad'] = sorted(k for k, v in values.items() if amap.get(k) != v.strip())
    res['values_ok'] = len(values) - len(res['values_bad'])
    res['values_total'] = len(values)
    res['other_cells_changed'] = sum(1 for k in bmap if k not in values and bmap[k] != amap.get(k))
    # 다른 표들 글자 불변
    res['other_tables_changed'] = sum(1 for x, y in zip(tb, ta) if x.index != table_index and [c.text for c in x.cells] != [c.text for c in y.cells])
    pb, pa = parts_of(before), parts_of(after)
    res['parts_changed'] = sorted(n for n in pb if pb[n] != pa.get(n))
    res['parts_added_removed'] = sorted(set(pb) ^ set(pa))
    res['ok'] = (not res['zip_errors'] and res['table_count_same'] and res['structure_same']
                 and not res['values_bad'] and res['other_cells_changed'] == 0
                 and res['other_tables_changed'] == 0 and not res['parts_added_removed']
                 and set(res['parts_changed']) <= {section})
    return res


def verify_hwp(hwp: bytes, section: str, table_index: int, values: dict) -> list:
    """HWP 5.0 으로 다시 쓴 결과를 HWPX로 되읽어 값이 그대로인지. 틀린 칸 주소 목록."""
    hx, _, _ = to_hwpx(hwp)
    ta = [t for t in analyze(hx) if t.section == section]
    if table_index >= len(ta):
        return sorted(values) or [('table', table_index)]
    amap = {(c.row, c.col): c.text for c in ta[table_index].cells}
    return sorted(k for k, v in values.items() if amap.get(k) != v.strip())
