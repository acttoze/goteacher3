"""C2 표 붙여넣기: 클립보드 HTML 표 → TSV, TSV → 머리글·받는 사람 칸·입력 칸·형식.

한글에서 표를 복사하면 글자(text/plain)에는 칸 구분이 없고 표는 HTML에만 있다(research/05 실측: 649줄×1칸).
그래서 브라우저는 붙여넣은 HTML을 그대로 서버에 한 번 보내고, 서버는 여기서 글자만 뽑아 TSV로 돌려준다.
HTML은 저장하지도, 화면에 다시 그리지도 않는다. 이후로는 TSV 하나만 다룬다(엑셀·직접 고친 글자와 같은 길).

이식 기준: prototype/assets/core.js TR.htmlTableToTsv(colspan은 빈 칸) → 여기서는 병합 칸에 글자를 복사해
2줄 머리글(colspan)은 '위 아래'로 이어 붙이고, 세로 병합(rowspan)된 받는 사람 칸은 아래 줄에도 채운다.
분류 규칙: design/collect-new.html(PRE·guess) + prototype collectNew(빈 줄·'…' 줄 빼기) + 합계 줄 빼기(design 한글 양식).
"""
import re
import unicodedata
from html.parser import HTMLParser


class PasteError(ValueError):
    """사용자에게 그대로 보여 줄 문구. 붙여넣은 값은 담지 않는다."""


MAX_HTML = 1_000_000  # 붙여넣은 HTML 글자 수(한글 표 649칸 ≈ 300KB)
MAX_TEXT = 300_000  # TSV 글자 수(HTML에서 바꾼 결과도 이 안에서 끊는다)
MAX_CELL_RAW = 1000  # 칸 하나에 쌓는 글자(정리 뒤 200자로 자른다)
MAX_DEPTH = 4  # 칸 안 표 중첩
MAX_GRID_ROWS = 1000
MAX_GRID_COLS = 60
MAX_ROWS = 500  # 받는 사람 줄
MAX_FIELDS = 40
MAX_NAME = 100  # Field.name·Row.unit·Row.who 길이

# 받는 사람 칸(미리 채움): design PRE(학년반|반|담임|성명|직위)에 학교 표에 흔한 이름을 더함. 띄어쓰기는 빼고 비교.
UNIT_NAME = re.compile(r"^(학년반|학년|반|학급|학반|담임|담임교사|성명|이름|교사|교사명|직위|직급|부서|연번|순|순번|번호|구분)$")
WHO_NAME = re.compile(r"^(성명|이름|담임|담임교사|교사|교사명)$")
TOTAL = re.compile(r"^(합계|소계|총계|누계|계)$")
FILLER = re.compile(r"^[….·⋮⦙ㆍ\s]+$")
BLANK_BOX = re.compile(r"^[□☐◻\s]+$")  # 한글 양식의 빈 체크 표시는 빈 칸
EXAMPLE = re.compile(r"^(\(?예\)|예시|\(예시\))")
# 형식 추정: design guess() 순서(숫자 → 날짜 → 고르기)
NUM_NAME = re.compile(r"인원|수량|명\)|\(명|금액|원\)|[가-힣]수$")  # '인원수·시수'만. 요일 '수' 단독은 아님
DATE_NAME = re.compile(r"(?<!요)일$|날짜|일자")
SELECT_NAME = re.compile(r"여부|희망")
NUM_VAL = re.compile(r"^[-+]?\d[\d,]*(\.\d+)?\s*(명|원|개|권|회|시간|%)?$")
DATE_VAL = re.compile(r"^(\d{4}\s*[-./]\s*)?\d{1,2}\s*[-./]\s*\d{1,2}\.?(\s*\(.\))?$|^\d{1,2}월\s*\d{1,2}일$")
YES_NO = ["예", "아니오"]
# C7 학생 정보 칸(교직원 정보만 받는다). 받는 사람 줄은 교사 명부라 연락처·전화·주소·이메일은 교사 것이면 허용(리드 결정):
# 학생·보호자·학부모·부모·아동이 붙을 때만 잡는다. 주민·생년월일은 누구 것이든 잡는다.
# '학생 수'·'참가 학생 수'·'아동학대 …교육'(통계·연수 이름)은 걸리지 않는다. 띄어쓰기는 빼고 비교.
# '학생'으로 끝나는 칸(대상 학생), 이름·성명·성함·명/명단(명찰·명부·'학생(명)' 단위는 아님), 메일·e-mail,
# 전화(번호)는 뒤에 다른 낱말이 없을 때만('전화상담' 아님), 주민은 주민번호·주민등록일 때만.
STUDENT = re.compile(r"학생$|(학생|아동|보호자|부모)(\(?(이름|성명|성함)|명(단|$))"
                     r"|(학생|아동|보호자|부모).*(연락처|휴대폰|핸드폰|주소|메일|e-?mail|전화(번호)?(?![가-힣]))"
                     r"|주민(등록)?번호|주민등록|생년월일")
CLASS_VAL = re.compile(r"^[1-6]\s*-\s*([1-9]|1\d|20)$")  # '6-1'(학년-반)은 월-일로 읽지 않는다
TOO_BIG = "표가 너무 커요. 받는 사람 500줄, 칸 40개 안으로 나눠 붙여넣어 주세요."


def student_info(name):
    """칸 이름이 학생 개인정보로 보이면 True(C7). 칸 이름만 보고, 적힌 값은 보지 않는다."""
    return bool(STUDENT.search(_bare(name).lower()))


def clean_text(s):
    """제어·서식 문자를 빼고 공백(&nbsp; 포함)을 한 칸으로."""
    s = "".join(" " if unicodedata.category(ch) in {"Cc", "Zl", "Zp", "Zs"} else ch
                for ch in s if unicodedata.category(ch) != "Cf")
    return " ".join(s.split())


# ---------------------------------------------------------------- HTML → TSV
class _Tables(HTMLParser):
    """table·tr·td/th만 본다. 칸 안 문단·줄바꿈은 띄어쓰기 하나, 칸 안 표는 글자만 이어 붙인다."""

    BREAK = {"br", "p", "div", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.skip, self.deep = [], [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "table":
            if len(self.stack) >= MAX_DEPTH:  # 너무 깊은 표는 글자만 바깥 칸에
                self.deep += 1
            else:
                self.stack.append({"rows": [], "cell": None})
        elif not self.stack:
            return
        elif tag == "tr":
            t = self.stack[-1]
            if len(t["rows"]) < MAX_GRID_ROWS:
                t["rows"].append([])
            t["cell"] = None
        elif tag in ("td", "th"):
            t = self.stack[-1]
            if not t["rows"]:
                t["rows"].append([])
            a = dict(attrs)
            cell = {"text": [], "n": 0, "rs": _span(a.get("rowspan"), MAX_GRID_ROWS), "cs": _span(a.get("colspan"), MAX_GRID_COLS)}
            if len(t["rows"]) < MAX_GRID_ROWS and len(t["rows"][-1]) < MAX_GRID_COLS:  # 한 줄 칸 수 상한
                t["rows"][-1].append(cell)
            t["cell"] = cell
        elif tag in self.BREAK:
            self._add(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag == "table" and self.deep:
            self.deep -= 1
        elif tag == "table" and self.stack:
            t = self.stack.pop()
            if self.stack:  # 칸 안 표: 글자만 바깥 칸에
                self._add(" " + " ".join("".join(c["text"]) for row in t["rows"] for c in row))
            else:
                self.tables.append(t["rows"])
        elif tag in ("td", "th") and self.stack:
            self.stack[-1]["cell"] = None
        elif tag in self.BREAK:
            self._add(" ")

    def handle_data(self, data):
        if not self.skip:
            self._add(data)

    def _add(self, s):
        cell = self.stack[-1]["cell"] if self.stack else None
        if cell is not None and cell["n"] < MAX_CELL_RAW:
            s = s[:MAX_CELL_RAW - cell["n"]]
            cell["text"].append(s)
            cell["n"] += len(s)


def _span(v, cap):
    try:
        return min(cap, max(1, int(str(v).strip()[:6])))
    except (TypeError, ValueError):
        return 1


def _grid(rows):
    """병합 칸을 펼친 격자. 덮인 자리에는 같은 칸(dict)을 둔다."""
    rows = [row for row in rows if row]  # 칸 없는 tr
    grid, n = {}, len(rows)
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in grid:
                c += 1
            if c >= MAX_GRID_COLS:
                break
            a = {"r": r, "c": c, "rs": min(cell["rs"], n - r), "cs": min(cell["cs"], MAX_GRID_COLS - c),
                 "text": clean_text("".join(cell["text"]))[:200]}
            for dr in range(a["rs"]):
                for dc in range(a["cs"]):
                    grid[(r + dr, c + dc)] = a
            c += a["cs"]
    if not grid:
        return []
    R, C = max(r for r, _ in grid) + 1, max(c for _, c in grid) + 1
    return [[grid.get((r, c)) for c in range(C)] for r in range(R)]


def _header_rows(g):
    """머리글 줄 수: 첫 줄에서 아래로 내려온 칸(rowspan) 끝까지, 가로로 합친 칸(colspan) 밑이 갈라져 있으면 한 줄 더."""
    R, hdr, top = len(g), 1, next(a for a in g[0] if a)["r"]  # top: 잘라 낸 제목 줄 수(칸의 r은 원래 표 기준)
    while hdr < min(R, 3):
        anchors = {id(a): a for row in g[:hdr] for a in row if a}.values()
        deeper = max(a["r"] - top + a["rs"] for a in anchors)
        split = any(a["cs"] > 1 and a["r"] - top + a["rs"] == hdr and _split_below(g[hdr], a) for a in anchors)
        nxt = max(deeper, hdr + 1 if split else hdr)
        if nxt == hdr:
            break
        hdr = min(nxt, R, 3)
    return hdr


def _split_below(row, a):
    """합친 머리글 밑이 짧은 이름 여럿으로 갈라졌나(남·여, 교과·주당 시수). 아래로 이어지는 칸·긴 문장은 자료 줄."""
    below = {id(x): x for x in row[a["c"]:a["c"] + a["cs"]] if x}
    return len(below) >= 2 and all(x["text"] and x["rs"] == 1 and len(x["text"]) <= 20 for x in below.values())


def html_to_tsv(html):
    """클립보드 HTML에서 가장 큰 표를 머리글 한 줄 + 자료 줄의 TSV로. 표가 없으면 ''. 너무 크면 PasteError."""
    if len(html) > MAX_HTML:
        raise PasteError(TOO_BIG)
    p = _Tables()
    p.feed(html)
    p.close()
    tables = [t for t in p.tables if any(t)]
    if not tables:
        return ""
    g = _grid(max(tables, key=lambda t: sum(len(r) for r in t)))
    g = [row for row in g if any(row)]
    while len(g) > 1 and g[0][0] and all(a is g[0][0] for a in g[0]):  # 표 맨 위 제목 줄(한 칸이 폭 전체)
        g = g[1:]
    if not g:
        return ""
    hdr = _header_rows(g)
    C, data = len(g[0]), g[hdr:]
    keep = [c for c in range(C) if c == 0 or not _merged(g[:hdr], data, c)]
    head = []
    for c in keep:
        parts = []
        for row in g[:hdr]:
            t = row[c]["text"] if row[c] else ""
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        head.append(_head_name(" ".join(parts)))
    out, size = [], 0
    for line in [head] + [[row[c]["text"] if row[c] else "" for c in keep] for row in data]:
        out.append("\t".join(line))
        size += len(out[-1]) + 1
        if size > MAX_TEXT:  # 세로 병합 복사로 결과가 커질 수 있다
            raise PasteError(TOO_BIG)
    return "\n".join(out)


def _merged(head, data, c):
    """c열을 왼쪽 열에 합치나: 자료 줄에서 늘 같은 칸이거나, 머리글이 같은 칸이고 자료 줄 절반 넘게 같은 칸(아래 요약 줄)."""
    same = [bool(row[c]) and row[c] is row[c - 1] for row in data]
    if not data:
        return False
    if all(same):
        return True
    return all(row[c] and row[c] is row[c - 1] for row in head) and sum(same) * 2 > len(same)


# ---------------------------------------------------------------- TSV → 칸·줄
def _head_name(s):
    s = clean_text(s)
    parts = s.split(" ")
    if len(parts) > 1 and all(len(x) == 1 and "가" <= x <= "힣" for x in parts):  # 한글 표의 '구 분'·'성  명'
        s = "".join(parts)
    return s[:MAX_NAME]


def _bare(s):
    return re.sub(r"\s+", "", s)


def guess_kind(name, values):
    """design guess(): 머리글로 먼저, 글자면 적힌 값으로. 반환 (kind, options)."""
    n = _bare(name)
    vals = [v for v in values if v and not FILLER.match(v) and v != "-"]
    if NUM_NAME.search(n) and sum(bool(NUM_VAL.match(v)) for v in vals) * 2 >= len(vals):  # 값 다수가 글자면 글자
        return "number", []
    if DATE_NAME.search(n):
        return "date", []
    boxes = vals and len(set(vals)) == 1 and vals[0].count("□") >= 2
    if SELECT_NAME.search(n) or boxes:
        if boxes:  # 한글 양식의 '□ 가 □ 나'
            opts = [o.strip() for o in vals[0].split("□") if o.strip()]
        else:
            opts = list(dict.fromkeys(vals))
        if not opts or len(opts) > 20:
            opts = YES_NO if "여부" in n else []
        return "select", [o[:MAX_NAME] for o in opts]
    if vals and all(NUM_VAL.match(v) for v in vals):
        return "number", []
    if vals and all(DATE_VAL.match(v) and not CLASS_VAL.match(v) for v in vals):
        return "date", []
    return "text", []


def parse(text):
    """TSV(첫 줄 머리글) → {"head", "unit_cols", "unit_head", "fields", "rows"}. 문제가 있으면 PasteError."""
    if len(text) > MAX_TEXT:
        raise PasteError(TOO_BIG)
    lines = [[clean_text(c)[:200] for c in line.split("\t")] for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [r for r in lines if any(r)]
    if not lines:
        raise PasteError("표를 붙여넣어 주세요. 한글·엑셀에서 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요.")
    if len(lines) > 1 and all(len(r) == 1 for r in lines):
        raise PasteError("칸 구분이 없어요. 표가 한 줄에 한 칸씩 풀려 들어왔어요. "
                         "한글에서 표를 다시 드래그해 복사하거나, 엑셀에 붙였다가 복사해 붙여넣어 주세요.")
    C = max(len(r) for r in lines)
    if C > MAX_GRID_COLS:
        raise PasteError("칸이 너무 많아요. 60칸 안의 표로 나눠 붙여넣어 주세요.")
    lines = [r + [""] * (C - len(r)) for r in lines]
    data = [r for r in lines[1:]
            if not all(FILLER.match(v) for v in r if v)
            and not EXAMPLE.match(next(v for v in r if v))
            and not any(TOTAL.match(_bare(v)) for v in r)]
    if len(data) > MAX_ROWS:
        raise PasteError("줄이 너무 많아요. 받는 사람 500줄까지 만들 수 있어요.")

    cols = [c for c in range(C) if lines[0][c] or any(r[c] for r in data)]
    head, seen = [], {}
    for c in cols:
        n = _head_name(lines[0][c]) or f"{c + 1}번째 칸"
        seen[n] = seen.get(n, 0) + 1
        head.append(n if seen[n] == 1 else f"{n} ({seen[n]})"[:MAX_NAME])
    values = {c: ["" if BLANK_BOX.match(r[c]) else r[c] for r in data] for c in cols}

    unit = {c for c in cols if UNIT_NAME.match(_bare(_head_name(lines[0][c])))}
    if data and not all(all(v) for v in values.values()):  # 빈 칸이 있는 표: 왼쪽부터 다 채워진 열은 미리 채움
        for c in cols:
            h = _head_name(lines[0][c])
            if not all(values[c]) or DATE_VAL.match(h) or NUM_VAL.match(h):  # '9.18.(월)'·'1' 같은 머리글은 입력 열
                break
            unit.add(c)
    inputs = [c for c in cols if c not in unit]
    if not inputs:  # 이름만 있고 비어 있는 열(예: 순|학년|성명 명단)은 적어 받는 칸
        inputs = [c for c in cols if not any(values[c])]
        unit -= set(inputs)
    if not inputs:
        raise PasteError("받을 칸이 없어요. 선생님들이 채울 빈 칸이 있는 표인지 확인해 주세요.")
    if len(inputs) > MAX_FIELDS:
        raise PasteError("받을 칸이 너무 많아요. 40칸까지 만들 수 있어요.")

    name = dict(zip(cols, head))
    units = [c for c in cols if c in unit]
    who = next((c for c in reversed(units) if WHO_NAME.match(_bare(name[c]))), None)
    others = [c for c in units if c != who]
    unit_head = ([" ".join(name[c] for c in others)] if others else []) + ([name[who]] if who is not None else [])
    rows = []
    for r in data:
        u = " ".join(r[c] for c in others if r[c])[:MAX_NAME]
        w = r[who][:MAX_NAME] if who is not None else ""
        if u or w:
            rows.append({"unit": u, "who": w})
    fields = []
    for c in inputs:
        kind, options = guess_kind(name[c], values[c])
        fields.append({"name": name[c], "kind": kind, "options": options})
    return {"head": head, "unit_cols": [name[c] for c in units], "unit_head": unit_head, "fields": fields, "rows": rows}
