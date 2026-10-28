"""티로드 한글 취합 POC 핵심 모듈.

공정
  load()          hwp/hwpx 파일 → HWPX 패키지 바이트 (hwp는 python-hwpx로 HWPX 변환)
  analyze()       모든 표의 격자(병합 반영)·셀 글자·역할(결재란/틀/자료표) 분석
  pick_table()    취합용 표 후보 순위
  schema()        머리글 행·행 머리를 읽어 '입력 칸' 목록(라벨 포함) 생성
  fill_lib()      python-hwpx table_patch.fill_cells (바이트 보존) 로 값 넣기
  fill_own()      lxml 직접 조작으로 값 넣기 (비교용)
  to_hwp()        채운 HWPX → HWP 5.0 바이너리 (python-hwpx hwp5 writer)
  verify()        다시 파싱해 값·구조·나머지 부분 불변 확인
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
SEC_RE = re.compile(r'Contents/section(\d+)\.xml$')
APPROVAL_RE = re.compile(r'결\s*재|담\s*당|교\s*감|교\s*장|부\s*장|기\s*안|협\s*조|전\s*결')
HEADER_HINT = re.compile(r'성\s*명|이\s*름|반|번\s*호|학\s*년|비\s*고|연\s*번|소\s*속|직\s*위|구\s*분|순|과\s*목|희망|여\s*부|인\s*원|계')
_PARSER = etree.XMLParser(resolve_entities=False, huge_tree=True, no_network=True)


# ---------------------------------------------------------------- 입출력
def load(path: str) -> tuple[bytes, str, dict]:
    """파일을 읽어 (hwpx 바이트, 원본 형식, 변환 보고) 반환."""
    data = open(path, 'rb').read()
    if data[:2] == b'PK':
        return data, 'hwpx', {}
    if data[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        from hwpx.hwp5.package import convert, to_hwpx_bytes
        c = convert(data)
        rep = {'unconverted': sorted(set(map(str, c.report.unconverted or []))),
               'dropped': sorted(set(map(str, c.report.dropped or [])))}
        return to_hwpx_bytes(c.files), 'hwp', rep
    raise ValueError('hwp/hwpx 형식이 아님')


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
        root = etree.fromstring(parts[sec], _PARSER)
        last_text = ''
        idx = 0
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
                    a = tc.find(HP + 'cellAddr'); s = tc.find(HP + 'cellSpan')
                    cells.append(Cell(int(a.get('rowAddr')), int(a.get('colAddr')),
                                      int(s.get('rowSpan', 1)) if s is not None else 1,
                                      int(s.get('colSpan', 1)) if s is not None else 1,
                                      _cell_text(tc), tc.find('.//' + HP + 'tbl') is not None,
                                      tc.get('borderFillIDRef', ''), _has_obj(tc)))
            t = Table(sec, idx, int(el.get('rowCnt', 0)), int(el.get('colCnt', 0)), cells,
                      nested=any(a.tag == HP + 'tc' for a in el.iterancestors()),
                      has_child=el.find('.//' + HP + 'tbl') is not None,
                      before_text=last_text[:60])
            for c in cells:
                for dr in range(c.rs):
                    for dc in range(c.cs):
                        t.grid[(c.row + dr, c.col + dc)] = c
            _classify(t)
            tables.append(t)
            idx += 1
    return tables


def _classify(t: Table):
    alltext = ' '.join(c.text for c in t.cells)
    empties = [c for c in t.cells if not c.text and not c.has_nested and not c.has_obj]
    if t.has_child and len(empties) < 2:
        t.role = 'frame'          # 칸 안에 표를 품은 바깥 틀(자체 입력 칸 거의 없음)
    elif APPROVAL_RE.search(alltext) and t.rows <= 3 and len(t.cells) <= 12:
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
    return sorted([t for t in tables if t.role == 'data'], key=lambda t: -t.score)


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
    # 머리글이 표 전체면(빈 칸이 아래쪽에만 없음) 머리글 없음으로 본다
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
    UNIT = re.compile(r'^\s*(명|개|원|천원|%|회|시간|건|일|학급|권|매)\s*$')
    TOTAL = re.compile(r'^\s*(합\s*계|소\s*계|총\s*계|계)\s*$')
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
    return {'kind': kind, 'header_rows': hdr, 'label_cols': lab, 'total_rows': sorted(total_rows), 'inputs': inputs}


# ---------------------------------------------------------------- 채우기
def fill_lib(hwpx: bytes, section: str, table_index: int, values: dict) -> bytes:
    """python-hwpx 의 바이트 보존 셀 채우기. values: {(row,col): text}"""
    from hwpx.table_patch import fill_cells
    cells = [{'section_path': section, 'table_index': table_index, 'row': r, 'col': c, 'text': v}
             for (r, c), v in values.items()]
    res = fill_cells(hwpx, cells)
    if res.skipped:
        raise RuntimeError(f'건너뛴 칸: {res.skipped[:3]}')
    if not res.open_safety.get('ok', True):
        raise RuntimeError(f'open_safety 실패: {res.open_safety}')
    return res.data


def fill_own(hwpx: bytes, section: str, table_index: int, values: dict) -> bytes:
    """lxml 직접 조작: 칸 첫 문단 첫 run 에 글자를 넣고 linesegarray 제거, 섹션 XML 재직렬화."""
    parts = parts_of(hwpx)
    root = etree.fromstring(parts[section], _PARSER)
    tbl = list(root.iter(HP + 'tbl'))[table_index]
    for tr in tbl.findall(HP + 'tr'):
        for tc in tr.findall(HP + 'tc'):
            a = tc.find(HP + 'cellAddr')
            key = (int(a.get('rowAddr')), int(a.get('colAddr')))
            if key not in values:
                continue
            p = tc.find(HP + 'subList').find(HP + 'p')
            run = p.find(HP + 'run')
            if run is None:
                run = etree.SubElement(p, HP + 'run', charPrIDRef='0')
                p.insert(0, run)
            for old in run.findall(HP + 't'):
                run.remove(old)
            t = etree.Element(HP + 't'); t.text = values[key]
            # run 안 컨트롤(ctrl 등) 뒤가 아니라 맨 앞에 넣는다
            run.insert(0, t)
            for lsa in p.findall(HP + 'linesegarray'):
                p.remove(lsa)
    parts[section] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    return repack(hwpx, parts)


def repack(orig: bytes, parts: dict[str, bytes]) -> bytes:
    """원본 zip 의 항목 순서·압축 방식을 그대로 따라 다시 묶는다(mimetype 맨 앞, 무압축)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(orig)) as zi, zipfile.ZipFile(out, 'w') as zo:
        infos = sorted(zi.infolist(), key=lambda i: i.filename != 'mimetype')
        for i in infos:
            zinfo = zipfile.ZipInfo(i.filename, date_time=i.date_time)
            zinfo.compress_type = zipfile.ZIP_STORED if i.filename == 'mimetype' else i.compress_type
            zinfo.external_attr = i.external_attr
            zo.writestr(zinfo, parts[i.filename])
    return out.getvalue()


# ---------------------------------------------------------------- 검증
def zip_ok(hwpx: bytes) -> list[str]:
    errs = []
    with zipfile.ZipFile(io.BytesIO(hwpx)) as z:
        il = z.infolist()
        if il[0].filename != 'mimetype' or il[0].compress_type != zipfile.ZIP_STORED:
            errs.append('mimetype 첫 항목/무압축 아님')
        if z.read('mimetype') != b'application/hwp+zip':
            errs.append('mimetype 값 이상')
        for need in ('Contents/content.hpf', 'META-INF/container.xml', 'version.xml', 'Contents/header.xml'):
            if need not in z.namelist():
                errs.append(f'{need} 없음')
        if z.testzip() is not None:
            errs.append('CRC 오류')
        for n in z.namelist():
            if n.endswith('.xml') or n.endswith('.hpf'):
                try:
                    etree.fromstring(z.read(n), _PARSER)
                except Exception as e:
                    errs.append(f'{n} XML 오류 {e}')
    return errs


def verify(before: bytes, after: bytes, section: str, table_index: int, values: dict) -> dict:
    """채운 값이 정확히 그 칸에만 들어갔고, 표 구조·다른 칸·다른 파트가 그대로인지."""
    res = {'zip_errors': zip_ok(after)}
    tb = [t for t in analyze(before) if t.section == section]
    ta = [t for t in analyze(after) if t.section == section]
    res['table_count_same'] = len(tb) == len(ta)
    B, A = tb[table_index], ta[table_index]
    res['structure_same'] = [(c.row, c.col, c.rs, c.cs, c.border) for c in B.cells] == [(c.row, c.col, c.rs, c.cs, c.border) for c in A.cells]
    amap = {(c.row, c.col): c.text for c in A.cells}
    bmap = {(c.row, c.col): c.text for c in B.cells}
    res['values_ok'] = sum(1 for k, v in values.items() if amap.get(k) == v)
    res['values_total'] = len(values)
    res['other_cells_changed'] = sum(1 for k in bmap if k not in values and bmap[k] != amap.get(k))
    # 다른 표들 글자 불변
    res['other_tables_changed'] = sum(1 for x, y in zip(tb, ta) if x.index != table_index and [c.text for c in x.cells] != [c.text for c in y.cells])
    pb, pa = parts_of(before), parts_of(after)
    res['parts_changed'] = sorted(n for n in pb if pb[n] != pa.get(n))
    res['parts_added_removed'] = sorted(set(pb) ^ set(pa))
    return res


def xml_diff_stats(a: bytes, b: bytes) -> dict:
    """태그 단위로 잘라 바뀐 태그 수를 센다(바이트 보존 여부 판단용)."""
    import difflib
    ta = re.split(rb'(?=<)', a); tb = re.split(rb'(?=<)', b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    changed = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != 'equal')
    return {'tags_before': len(ta), 'tags_changed': changed}
