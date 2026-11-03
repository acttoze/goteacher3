"""H3 업무분장 표 읽기: TSV(한글 표는 collect.paste.html_to_tsv가 바꾼 것) → 부서·업무·담당 줄. 그 밖의 칸은 버린다."""
import re

from collect.paste import FILLER, MAX_ROWS, MAX_TEXT, TOTAL, PasteError, clean_text

DEPT = re.compile(r"^(부서|부서명|소속|소속부서|부)$")
TASK = re.compile(r"^(업무|업무명|업무내용|담당업무|분장업무|주요업무|사무|담당사무)$")
WHO = re.compile(r"^(담당|담당자|담당자명|담당교사|담당교직원|성명|이름|교사|교사명)$")
HEAD_LINES = 5  # 머리글을 찾는 범위(표 위 제목 줄)
EMPTY = "표를 붙여넣어 주세요. 한글·엑셀에서 표를 드래그해 복사(Ctrl+C)한 뒤 붙여넣으면 돼요."


def _columns(line):
    """이 줄에서 찾은 머리글 {dept·task·who: 열 번호}. 셋 다 있어야 머리글 줄이다."""
    found = {}
    for c, cell in enumerate(line):
        bare = re.sub(r"\s+", "", cell)
        for key, pat in (("dept", DEPT), ("task", TASK), ("who", WHO)):
            if pat.match(bare):
                found.setdefault(key, c)
    return found


def read_duties(text):
    """{"rows": [{dept, task, who}], "skipped": 못 읽은 줄 수, "dropped": 버린 칸 수}. 문제가 있으면 PasteError(붙여넣은 값은 담지 않는다)."""
    if len(text) > MAX_TEXT:
        raise PasteError("표가 너무 커요. 500줄 안으로 나눠 붙여넣어 주세요.")
    lines = [[clean_text(c) for c in line.split("\t")] for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [r for r in lines if any(r)]
    if not lines:
        raise PasteError(EMPTY)
    if all(len(r) == 1 for r in lines):
        raise PasteError("칸 구분이 없어요. 한글에서 표를 다시 드래그해 복사하거나, 엑셀에 붙였다가 복사해 붙여넣어 주세요.")

    head = next((i for i, r in enumerate(lines[:HEAD_LINES]) if len(_columns(r)) == 3), None)
    if head is not None:
        cols, data, width = _columns(lines[head]), lines[head + 1:], len([c for c in lines[head] if c])
    elif max(len(r) for r in lines) == 3 and not any(len(_columns(r)) for r in lines[:HEAD_LINES]):
        cols, data, width = {"dept": 0, "task": 1, "who": 2}, lines, 3  # 머리글 없는 세 칸 표
    else:
        raise PasteError("부서·업무·담당 칸을 찾지 못했어요. 표 첫 줄에 이 이름들이 있는지 확인해 주세요.")

    rows, skipped, dept = [], 0, ""
    for r in data:
        cell = {k: r[c] if c < len(r) else "" for k, c in cols.items()}
        if any(TOTAL.match(re.sub(r"\s+", "", v)) for v in r) or all(FILLER.match(v) for v in r if v):
            continue
        if len(_columns(r)) == 3:  # 쪽이 넘어가며 머리글이 다시 나온 줄
            continue
        dept = cell["dept"] or dept  # 엑셀의 병합 칸은 아래 줄이 비어서 온다
        if not cell["task"] or not dept:
            skipped += 1
            continue
        rows.append({"dept": dept[:100], "task": cell["task"][:200], "who": cell["who"][:100]})
    if not rows:
        raise PasteError("업무 줄을 찾지 못했어요. 부서·업무·담당이 적힌 줄이 있는지 확인해 주세요.")
    if len(rows) > MAX_ROWS:
        raise PasteError("줄이 너무 많아요. 업무 500줄까지 넣을 수 있어요.")
    return {"rows": rows, "skipped": skipped, "dropped": width - 3}
