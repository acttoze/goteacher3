"""E6 칸 확인: 고른 표(SourceFile.picked_schema())의 자동 라벨 → 교사가 다듬기·켜고 끄기·행 머리 배정 → 모으기 칸.

연구 문서 4-2 흐름 3·4(research/04 3장 '사람이 확인해야 할 부분' 2·3), 모습은 design/collect-new.html(한글 양식 '받을 칸').
- 표에 머리글 행이 있으면 열마다 칸 하나(mode "rows"): 열 머리글이 칸 이름, 자료 행 하나가 받는 사람 줄 하나.
  머리글이 없는 신청서형(kv)은 빈 칸마다 칸 하나(mode "cells"): 모든 줄이 같은 칸 주소를 가진다(1인 1부, 묶기는 F1).
- 받는 사람 줄은 항상 동학년 명부(리드 결정, C4). 표의 반·이름 열은 칸으로 받지 않고 명부로 채운다(역할 unit·who).
- 합계·소계 행(hwp.schema total_rows)은 늘 뺀다. 그 밖의 줄도 교사가 끌 수 있다.
- 받을 칸이 다 있는 표 줄만 받는다(_full_rows). 원래 글자가 있는 줄은 끄고 짝짓지 않는다(값을 받고 못 넣는 일 없게).
- 명부 ↔ 표 줄: 행 머리 글자가 반(4-1·1반·4학년 1반)이면 그 반 줄에, 아니면 남은 줄에 차례로. 표 줄이 모자라면
  r=None(다 모이면 F1이 '+행 추가'로 표 끝에 줄을 더해 채움 — research/04 '모자라면 행 추가'). 남는 표 줄은 비워 둔다.
- 칸 매핑(SourceFile.cellmap): 줄(Row.key)마다 [무엇, 행, 열, 단위]. 무엇 = Field.pos 또는 "unit"·"who"(명부 값).
  F1은 이것만으로 hwp.fill(hwpx, section, index, {(행, 열): 값})을 부른다.
POST 값은 모두 다시 검사한다(브라우저를 믿지 않음). 칸 번호·줄 번호가 이 표에 없으면 표가 바뀐 것으로 보고 거부한다.
"""
import re

from .paste import EXAMPLE, FILLER, MAX_FIELDS, MAX_NAME, YES_NO, _head_name, clean_text, guess_kind, student_info

ROLES = [("input", "선생님이 입력"), ("unit", "반 · 명부로 채움"), ("who", "이름 · 명부로 채움"), ("off", "받지 않음")]
ROLE_KEYS = {k for k, _ in ROLES}
KIND_KEYS = {"number", "text", "select", "date"}  # views.KINDS와 같음
WHO_HEAD = re.compile(r"^(성명|이름|담임|담임교사|교사|교사명|담임성명|담임명)$")
UNIT_HEAD = re.compile(r"^(학년반|학년-반|반|학급|학반|학급명)$")
NO_HEAD = re.compile(r"^(연번|순|순번|번호|no\.?)$", re.I)  # 빈 번호 열은 받지 않음이 기본
CLASS_HEAD = re.compile(r"^(?:([1-6])(?:-|학년))?([1-9]|1\d|20)반?$")
ITEM_KEY = re.compile(r"^item_(\d+)_(role|name|kind)$")
ROW_KEY = re.compile(r"^row_(\d+)$")
STALE = "표가 바뀌었어요. 표를 다시 눌러 고른 뒤 칸을 확인해 주세요."
PII_MSG = ("학생 개인정보로 보이는 칸이 켜져 있어요: {}. 고티처는 교직원 정보만 받아요. "
           "이 칸은 「받지 않음」으로 바꾸거나 반별 인원 수로 받아 주세요.")


def _bare(s):
    return re.sub(r"\s+", "", s or "")


def _dedupe(names):
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})"[:MAX_NAME])
    return out


def _guess(head, units):
    kind, options = guess_kind(head, [])
    if units and kind == "text":  # '명'·'원' 단위 칸은 숫자
        kind = "number"
    return kind, options


def items(s):
    """(mode, 칸 확인 줄들, 받을 수 있는 표 줄들). 표 내용으로만 정해지므로 같은 표면 늘 같은 순서·번호다."""
    totals = set(s["total_rows"])
    if s["header_rows"] >= 1:
        by_col = {}
        for i in s["inputs"]:
            by_col.setdefault(i["col"], []).append(i)
        cols = sorted(by_col)
        heads = [s["col_heads"][c] if c < len(s["col_heads"]) else "" for c in cols]
        names = _dedupe([_head_name(h) or f"{c + 1}번째 칸" for c, h in zip(cols, heads)])
        out = []
        for n, (c, h, name) in enumerate(zip(cols, heads, names)):
            units = [i["unit"] for i in by_col[c] if i["unit"]]
            kind, options = _guess(name, units)
            out.append({"i": n, "col": c, "head": h, "name": name, "kind": kind, "options": options,
                        "cells": {i["row"]: (i["row"], c, i["unit"]) for i in by_col[c]}, "where": f"표 {c + 1}번째 열"})
        with_input = {i["row"] for i in s["inputs"]}
        rows = [{"r": r, "head": h, "total": r in totals} for r, h in s["row_heads"] if r in with_input or r in totals]
        mode = "rows"
    else:
        names = _dedupe([_head_name(i["label"]) or f"{i['row'] + 1}행 {i['col'] + 1}열" for i in s["inputs"]])
        out = []
        for n, (i, name) in enumerate(zip(s["inputs"], names)):
            kind, options = _guess(name, [i["unit"]] if i["unit"] else [])
            out.append({"i": n, "col": i["col"], "head": i["label"], "name": name, "kind": kind, "options": options,
                        "cells": {None: (i["row"], i["col"], i["unit"])}, "where": f"표 {i['row'] + 1}행 {i['col'] + 1}열"})
        rows, mode = [], "cells"
    _default_roles(out)
    return mode, out, rows


def _default_roles(out):
    unit = who = False
    for it in out:
        h = _bare(it["head"]).lower()
        it["pii"] = student_info(it["head"])
        if it["pii"] or NO_HEAD.match(h):
            it["role"] = "off"
        elif WHO_HEAD.match(h) and not who:
            it["role"], who = "who", True
        elif UNIT_HEAD.match(h) and not unit:
            it["role"], unit = "unit", True
        else:
            it["role"] = "input"


def read(s, post=None):
    """칸 확인 화면 값과 오류. post가 없으면 자동 추정 그대로. 반환 {"mode", "items", "rows", "error", "stale"}."""
    mode, out, rows = items(s)
    error, stale = "", False
    if post is not None and post.get("hwp_sent"):
        valid_rows = {x["r"] for x in rows if not x["total"]}
        for k in post:
            m, mr = ITEM_KEY.match(k), ROW_KEY.match(k)
            if (m and int(m.group(1)) >= len(out)) or (mr and int(mr.group(1)) not in valid_rows):
                stale = True
        if post.get("hwp_items") != str(len(out)):
            stale = True
        for it in out:
            role = post.get(f"item_{it['i']}_role", "")
            if role in ROLE_KEYS:
                it["role"] = role
            name = clean_text(post.get(f"item_{it['i']}_name", ""))[:MAX_NAME]
            if name:
                it["name"] = name
            kind = post.get(f"item_{it['i']}_kind", "")
            if kind in KIND_KEYS:
                if kind != it["kind"]:
                    it["options"] = []
                it["kind"] = kind
            it["pii"] = student_info(it["name"]) or student_info(it["head"])
        for x in rows:
            x["on"] = not x["total"] and post.get(f"row_{x['r']}") == "on"
    else:  # '…' 줄·'(예)' 줄은 처음에 꺼 둔다(C2 붙여넣기와 같은 규칙)
        for x in rows:
            x["on"] = not x["total"] and not (x["head"] and (FILLER.match(x["head"]) or EXAMPLE.match(x["head"])))
    for it in out:
        if it["kind"] == "select" and not it["options"]:
            it["options"] = list(YES_NO)
    if mode == "rows":
        _full_rows(out, rows, post is None or not post.get("hwp_sent"))
    names = _dedupe([it["name"] for it in out if it["role"] == "input"])
    for it, n in zip([it for it in out if it["role"] == "input"], names):
        it["name"] = n
    inputs = [it for it in out if it["role"] == "input"]
    pii = [it["name"] for it in inputs if it["pii"]]
    if stale:
        error = STALE
    elif not inputs:
        error = "받을 칸을 하나 이상 「선생님이 입력」으로 켜 주세요."
    elif len(inputs) > MAX_FIELDS:
        error = f"받을 칸이 너무 많아요. {MAX_FIELDS}칸까지 만들 수 있어요. 받지 않을 칸은 「받지 않음」으로 꺼 주세요."
    elif sum(it["role"] == "unit" for it in out) > 1 or sum(it["role"] == "who" for it in out) > 1:
        error = "반·이름(명부로 채움)은 열 하나씩만 고를 수 있어요."
    elif pii:
        error = PII_MSG.format(", ".join(pii))
    elif mode == "rows" and not any(x["on"] for x in rows):
        error = ("받을 표 줄을 하나 이상 남겨 주세요." if any(x["full"] for x in rows) else
                 "켠 칸이 다 있는 표 줄이 없어요. 줄마다 칸이 없는 칸(예: 합쳐진 비고)은 「받지 않음」으로 꺼 주세요.")
    return {"mode": mode, "items": out, "rows": rows, "error": error, "stale": stale,
            "inputs": inputs, "n_pre": sum(it["role"] in ("unit", "who") for it in out)}


def _full_rows(out, rows, default):
    """줄마다 받을 칸이 다 있는 표 줄만 받는다(F1 리드 결정: 값을 받고 표에 못 넣는 일이 없게).
    원래 글자가 있는 줄(부분 채움 표)·병합으로 칸이 없는 줄은 x["full"]=False → 끄고 명부와 짝짓지 않는다.
    기본값(처음 화면)에서는 표 줄 절반 미만에만 칸이 있는 열(세로 병합 비고 등)을 「받지 않음」으로 둔다.
    그래도 다 갖춘 줄이 없으면 칸이 가장 적은 열부터 끈다."""
    body = [x for x in rows if not x["total"]]

    def inputs():
        return [it for it in out if it["role"] == "input"]

    def full(r):
        return all(r in it["cells"] for it in inputs())
    if default and body:
        for it in inputs():
            if len(inputs()) > 1 and sum(x["r"] in it["cells"] for x in body) * 2 < len(body):
                it["role"] = "off"
        while len(inputs()) > 1 and not any(full(x["r"]) for x in body):
            min(inputs(), key=lambda it: len(it["cells"]))["role"] = "off"
    for x in rows:
        x["full"] = not x["total"] and full(x["r"])
        x["on"] = x["on"] and x["full"]


def _class_of(text, grade):
    """행 머리 글자 → 반 번호(4-1·1반·4학년 1반·1). 다른 학년이면 None."""
    m = CLASS_HEAD.match(_bare(text))
    if not m or (m.group(1) and int(m.group(1)) != grade):
        return None
    return int(m.group(2))


def _roster_class(unit):
    tail = unit.split("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def assign(rows, roster, grade):
    """명부 줄마다 표 줄 번호(없으면 None = F1 행 추가). 행 머리가 반이면 그 반 줄, 아니면 남은 줄을 차례로."""
    free = [x for x in rows if x["on"]]
    by_class = {}
    for x in free:
        no = _class_of(x["head"], grade)
        if no is not None:
            by_class.setdefault(no, x["r"])
    mine = {_roster_class(m["unit"]) for m in roster}
    out, used = [None] * len(roster), set()
    for k, m in enumerate(roster):
        r = by_class.get(_roster_class(m["unit"]))
        if r is not None and r not in used:
            out[k] = r
            used.add(r)
    rest = iter(x["r"] for x in free if x["r"] not in used and _class_of(x["head"], grade) not in mine)
    for k in range(len(roster)):
        if out[k] is None:
            out[k] = next(rest, None)
    return out


def plan(form, roster, grade):
    """화면에 보일 명부 ↔ 표 줄 짝. [(명부 줄, 표 줄 또는 None)], 남는 표 줄 수."""
    if form["mode"] != "rows":
        return [(m, None) for m in roster], 0
    got = assign(form["rows"], roster, grade)
    on = sum(x["on"] for x in form["rows"])
    return list(zip(roster, got)), on - sum(r is not None for r in got)


def cellmap(s, sec, form, rows, got):
    """칸 매핑 JSON. rows: 만든 Row(명부 순서), got: assign 결과(rows 모드). F1 채우기의 입력."""
    pos = {it["i"]: p for p, it in enumerate(form["inputs"])}
    what = [(pos[it["i"]] if it["role"] == "input" else it["role"], it) for it in form["items"]
            if it["role"] != "off"]
    lines = []
    for k, row in enumerate(rows):
        r = got[k] if form["mode"] == "rows" else None
        cells = []
        for w, it in what:
            cell = it["cells"].get(r) if form["mode"] == "rows" else it["cells"][None]
            if form["mode"] == "rows" and r is None:
                cells.append([w, None, it["col"], next(iter(it["cells"].values()))[2]])
            elif cell:
                cells.append([w, cell[0], cell[1], cell[2]])
        lines.append({"row": row.key, "r": r, "cells": cells})
    skip = sorted(set(s["total_rows"]) | {x["r"] for x in form["rows"] if not x["on"]})
    return {"v": 1, "sec": sec, "section": s["section"], "index": s["index"], "mode": form["mode"],
            "fields": [{"pos": p, "where": it["where"]} for p, it in enumerate(form["inputs"])],
            "cols": [[w, it["col"], next(iter(it["cells"].values()))[2]] for w, it in what] if form["mode"] == "rows" else [],
            "skip_rows": skip, "lines": lines}
