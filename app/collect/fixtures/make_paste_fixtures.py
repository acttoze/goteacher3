"""C2 픽스처 만들기(개발용, 앱에서 부르지 않음): 한글 양식 보관소의 hwpx 표를 한글 클립보드 HTML처럼 만든다.

실제 한글 2024 클립보드 표본이 저장소에 없어서 흉내 낸다. 한글이 클립보드에 넣는 HTML의 특징:
대문자 태그, td 안 <P CLASS=HStyle0><SPAN STYLE=...>, 빈 칸은 &nbsp;, 칸 안 문단 여러 개, rowspan·colspan.
표 위치는 hwp.candidates(공개 함수)로 찾고, 칸 글자는 hwpx XML을 직접 읽는다(공개 함수에 칸 글자가 없다).
이름처럼 보이는 자료 칸은 ○○○로 바꾼다. 행은 16줄까지만.

    uv run python app/collect/fixtures/make_paste_fixtures.py ~/moon/marketing/goteacher-forms-archive/raw
"""
import glob
import html
import os
import re
import sys
import zipfile

from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
import hwp  # noqa: E402

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
OUT = os.path.join(os.path.dirname(__file__), "paste")
MAX_ROWS = 16
NAME_HEAD = re.compile(r"성\s*명|이\s*름|강사명|명\s*단")
NAME_VAL = re.compile(r"^[가-힣]{2,4}$")
# 학교를 짐작할 단서(지명·업체·기관·학교 고유 일정)는 가상 이름으로. 표본을 더하면 여기에도 더한다.
CLUES = [
    (r"인천\s*연수구", "○○시 ○○구"), (r"옥련동|청학동", "○○동"), (r"\(주\)\s*[가-힣A-Za-z]+", "(주)○○"),
    (r"인천역사교육연구소", "○○역사교육연구소"), (r"CGV\s*연수", "○○ 영화관"), (r"롤러스타", "○○ 롤러장"),
    (r"개교기념일", "학교장 재량"), (r"인천|능허대|논곡|봉강|합일|용유", "○○"),
]

# (저장 이름, 파일 앞부분, 섹션 안 표 순번)
# (저장 이름, 파일 앞부분, 섹션 안 표 순번, 첫 칸 글자) — 앞부분이 같은 파일이 여럿이면 첫 칸 글자로 고른다.
PICKS = [
    ("01_leave_plan", "999b3c9788b641", 11, "연번"),
    ("02_field_trip", "844fe45590af3f", 4, "순"),
    ("03_grade_name", "1932013267__a6", 0, "순"),
    ("04_lecture_log", "1222f56c655326", 19, "날짜"),
    ("05_bus_check", "3556485236__4a99", 2, "구 분"),
    ("06_tutoring", "3556485259__1380", 0, "순"),
    ("07_period_grid", "6a1f6e73fd74", 9, "구분"),
    ("08_budget", "74572c7f0c9766d282665a2cf49536", 13, "구분"),
    ("09_timetable", "33450004__3d68", 7, "도움반"),
    ("10_projects", "978564118fd6b0", 54, "학년"),
    ("11_events", "978564118fd6b0", 95, "행사명"),
    ("12_teacher_hours", "999b3c9788b641", 1, "교과전담"),  # 첫 열이 교사 이름
]


def para_text(p):
    return "".join("".join(t.itertext()) for run in p.findall(HP + "run") for t in run.findall(HP + "t"))


def read_table(hwpx, section, index):
    with zipfile.ZipFile(__import__("io").BytesIO(hwpx)) as z:
        root = etree.fromstring(z.read(section))
    tbl = list(root.iter(HP + "tbl"))[index]
    cells = []
    for tr in tbl.findall(HP + "tr"):
        for tc in tr.findall(HP + "tc"):
            a, s = tc.find(HP + "cellAddr"), tc.find(HP + "cellSpan")
            sub = tc.find(HP + "subList")
            paras = [para_text(p) for p in sub.findall(HP + "p")] if sub is not None else []
            cells.append({"r": int(a.get("rowAddr")), "c": int(a.get("colAddr")),
                          "rs": int(s.get("rowSpan", 1)) if s is not None else 1,
                          "cs": int(s.get("colSpan", 1)) if s is not None else 1, "paras": paras})
    return cells


def mask_clues(text):
    for pat, rep in CLUES:
        text = re.sub(pat, rep, text)
    return text


def anonymize(cells, name_first_col=False):
    heads = {c["c"] for c in cells if c["r"] <= 1 and NAME_HEAD.search(" ".join(c["paras"]))}
    for c in cells:
        c["paras"] = [mask_clues(p) for p in c["paras"]]
    if name_first_col:
        for c in cells:
            if c["r"] > 1 and c["c"] == 0:
                c["paras"] = [re.sub(r"^[가-힣]{3}(?![가-힣])", "○○○", p) for p in c["paras"]]
    for c in cells:
        if c["r"] > 0 and c["c"] in heads:
            c["paras"] = ["○○○" if NAME_VAL.match(p.replace(" ", "")) else p for p in c["paras"]]


def td(c):
    span = (f' rowspan="{c["rs"]}"' if c["rs"] > 1 else "") + (f' colspan="{c["cs"]}"' if c["cs"] > 1 else "")
    paras = c["paras"] or [""]
    body = "\n".join(
        "<P CLASS=HStyle0 STYLE='text-align:center;line-height:130%;'>"
        + (f"<SPAN STYLE='font-family:\"맑은 고딕\";letter-spacing:-5%'>{html.escape(p).replace('  ', ' &nbsp;')}</SPAN>" if p.strip() else "&nbsp;")
        + "</P>" for p in paras)
    return (f"\t<TD valign=\"middle\"{span} style='width:60;height:28;border:solid #000000 0.4pt;"
            f"padding:1.4pt 5.1pt 1.4pt 5.1pt'>\n{body}\n\t</TD>")


def to_html(cells):
    rows = {}
    for c in cells:
        if c["r"] < MAX_ROWS:
            c["rs"] = min(c["rs"], MAX_ROWS - c["r"])
            rows.setdefault(c["r"], []).append(c)
    trs = "\n".join("<TR>\n" + "\n".join(td(c) for c in sorted(rows[r], key=lambda x: x["c"])) + "\n</TR>"
                    for r in sorted(rows))
    return ("<HTML>\n<HEAD>\n<META NAME=\"Generator\" CONTENT=\"Hancom HWP 12.0.0.535 WIN32LEWindows_Unknown_Version\">\n"
            "<STYLE type=\"text/css\">\n<!--\np.HStyle0\n\t{style-name:\"바탕글\"; margin-left:0.0pt; text-align:justify;"
            " line-height:160%; font-size:10.0pt; font-family:함초롬바탕; color:#000000;}\n-->\n</STYLE>\n</HEAD>\n"
            "<BODY>\n<!--StartFragment-->\n<P CLASS=HStyle0>&nbsp;</P>\n\n"
            "<TABLE border=\"1\" cellspacing=\"0\" cellpadding=\"0\" style='border-collapse:collapse;border:none;'>\n"
            f"{trs}\n</TABLE>\n<P CLASS=HStyle0>&nbsp;</P>\n<!--EndFragment-->\n</BODY>\n</HTML>\n")


def main(raw):
    os.makedirs(OUT, exist_ok=True)
    for name, prefix, index, first in PICKS:
        for path in sorted(glob.glob(os.path.join(raw, "*", prefix + "*.hwpx"))):
            data = open(path, "rb").read()
            hits = [c for c in hwp.candidates(data) if c["index"] == index]
            cells = read_table(data, hits[0]["section"], index) if hits else []
            if cells and " ".join(cells[0]["paras"]).strip() == first:
                break
        else:
            raise SystemExit(f"{name}: 표를 못 찾음")
        hit = hits[0]
        anonymize(cells, name_first_col=name == "12_teacher_hours")
        with open(os.path.join(OUT, name + ".html"), "w", encoding="utf-8") as f:
            f.write(to_html(cells))
        print(name, os.path.basename(path)[:24], hit["rows"], "x", hit["cols"])


if __name__ == "__main__":
    main(sys.argv[1])
