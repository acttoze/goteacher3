"""F1 병합: 모으기의 제출 값(Cell) + 칸 매핑(SourceFile.cellmap, E6) → hwp.fill 에 넣을 {(행, 열): 글자}.

Django 모델을 직접 읽지 않는다(줄은 unit·who·key 속성만, 값은 {Field.pos: 글자}). 그래서 단위 테스트가 쉽다.

규칙(research/04 3장 4-2 · 30번 리드 결정)
- 줄 모드(rows, 머리글 있는 표): cellmap 줄(Row.key)마다 [무엇, 행, 열, 단위]. 무엇 = Field.pos 또는 unit·who(명부 값).
  · 표 줄이 있는 줄(r 있음)은 반·이름(명부 값)을 늘 채우고, 낸 값은 있는 칸만.
  · 표 줄이 모자란 줄(r=None)과 명부 밖 줄(cellmap 에 없는 Row)은 낸 값이 있을 때만 '더할 줄'(extra)로 —
    작업이 표 끝에 줄을 더하고 칸 틀(cellmap cols)로 채운다(리드 결정 ②·③).
- 칸 모드(cells, 머리글 없는 신청서형): 모든 줄이 같은 칸 주소 → 낸 값이 있는 줄마다 한 부(리드 결정 ①).
- 단위 칸(예: '명')은 값 뒤에 단위를 붙인다('3' → '3명'). 빈 값은 넣지 않는다(원래 '명' 그대로).
- 충돌: 같은 칸 주소에 서로 다른 값이 두 번 오면 먼저 온 줄(명부 순서) 값을 두고 주소만 conflicts 에 남긴다.
  Cell 은 (줄, 칸)마다 하나라서 같은 사람 몫을 두 사람이 고쳐 써도 마지막 값 하나다(Cell.by 가 최근 작성자).
  그래서 표에서 겹치는 것은 칸 매핑이 두 줄을 한 칸에 이었을 때뿐이다.
- 칸 없음(dropped): 열마다 칸 하나라서, 원래 글자가 있는 줄(부분 채움 표)에는 그 칸 자리가 없다. 그 줄에 낸 값은
  넣지 않고 수만 센다(합본 화면·CSV 에는 있음).
"""
import re
from dataclasses import dataclass, field

BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
ROLES = ("unit", "who")


def text(w, unit, row, vals):
    """칸 하나에 넣을 글자. 줄바꿈은 빈칸으로(칸 안 문단 나눔은 원본 서식을 바꾸므로)."""
    if w == "unit":
        v = row.unit
    elif w == "who":
        v = row.who
    else:
        v = vals.get(w, "")
    v = " ".join(BAD.sub("", v or "").split())
    if v and unit and w not in ROLES and not v.endswith(unit):
        v += unit
    return v


def has_value(vals):
    return any((v or "").strip() for v in vals.values())


def _dropped(spec_whats, vals):
    return sum(1 for pos, v in vals.items() if (v or "").strip() and pos not in spec_whats)


@dataclass
class Plan:
    values: dict = field(default_factory=dict)      # {(행, 열): 글자} 표에 이미 있는 줄
    extra: list = field(default_factory=list)       # 더할 줄에 들어갈 Row(명부 순서 → 명부 밖)
    conflicts: list = field(default_factory=list)   # [(행, 열)] 같은 칸에 다른 값
    copies: list = field(default_factory=list)      # 칸 모드: [(Row, {(행, 열): 글자})]
    dropped: int = 0                                # 낸 값인데 표에 그 칸이 없음(그 줄 칸에 원래 글자가 있던 표)


def _put(plan, rc, v):
    if rc in plan.values and plan.values[rc] != v:
        if rc not in plan.conflicts:
            plan.conflicts.append(rc)
        return
    plan.values[rc] = v


def plan(cellmap, rows, vals_by_key):
    """rows: 모으기의 줄(Row, 명부 순서), vals_by_key: {Row.key: {Field.pos: 값}}."""
    p = Plan()
    lines = {ln["row"]: ln for ln in cellmap.get("lines", [])}
    if cellmap.get("mode") == "cells":
        template = next((ln["cells"] for ln in cellmap.get("lines", []) if ln["cells"]), [])
        for row in rows:
            vals = vals_by_key.get(row.key, {})
            if not has_value(vals):
                continue
            spec = lines[row.key]["cells"] if row.key in lines else template  # 명부 밖 줄은 같은 칸 틀
            p.dropped += _dropped({w for w, *_ in spec}, vals)
            out = {}
            for w, r, c, unit in spec:
                v = text(w, unit, row, vals)
                if v:
                    out[(r, c)] = v
            p.copies.append((row, out))
        return p
    for row in rows:
        vals = vals_by_key.get(row.key, {})
        ln = lines.get(row.key)
        if ln is None or ln["r"] is None:
            if has_value(vals):
                p.extra.append(row)
                p.dropped += _dropped({w for w, *_ in cellmap.get("cols", [])}, vals)
            continue
        p.dropped += _dropped({w for w, *_ in ln["cells"]}, vals)
        for w, r, c, unit in ln["cells"]:
            v = text(w, unit, row, vals)
            if v:
                _put(p, (r, c), v)
    return p


def extra_values(cellmap, extra, first_row, vals_by_key, step=1):
    """더한 줄 값(칸 틀 cellmap cols 로). [(행, 열, 무엇, 글자)] — first_row = 더한 첫 줄 번호,
    step = 줄 하나의 높이(세로 병합 묶음이면 2 이상)."""
    out = []
    for k, row in enumerate(extra):
        vals = vals_by_key.get(row.key, {})
        for w, c, unit in cellmap.get("cols", []):
            v = text(w, unit, row, vals)
            if v:
                out.append((first_row + k * step, c, w, v))
    return out


def place(items, inputs):
    """더한 줄 값을 그 줄의 실제 입력 칸(병합 반영, hwp.schema inputs)에 맞춘다. 열 c 를 덮는 칸이 그 줄에서
    시작하면 그 칸 주소로. 칸이 없거나 이미 다른 값이 갔으면 못 넣음. ({(행, 열): 글자}, 못 넣은 낸 값 수)."""
    spans = {}
    for i in inputs:
        spans.setdefault(i["row"], []).append((i["col"], i["col"] + i.get("colSpan", 1)))
    out, lost = {}, 0
    for r, c, w, v in items:
        at = next(((r, a) for a, b in spans.get(r, []) if a <= c < b), None)
        if at is None or at in out:
            lost += isinstance(w, int)
            continue
        out[at] = v
    return out, lost


def head_value(ref_head, k, row):
    """칸 틀에 반 열이 없을 때 더한 줄의 행 머리: 위 줄이 번호면 이어 세고, 아니면 받는 곳(Row.unit)."""
    first = (ref_head or "").split(" · ")[0].strip()
    return str(int(first) + 1 + k) if first.isdigit() else text("unit", "", row, {})
