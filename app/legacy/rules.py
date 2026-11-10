"""P3 구버전 → 고티처 대응 규칙(35번 2절). DB를 건드리지 않는 순수 함수만 둔다(importer가 부른다)."""
import os
import re
from datetime import date, datetime
from html.parser import HTMLParser

import hwp
from django.utils import timezone

from notices.filekinds import ALIAS, judge
from spaces.attendance import KINDS as DUTY_KINDS, PART_DAY

GRADE = re.compile(r"([1-6])학년")
CLASS_NO = re.compile(r"(\d+)반")
OFFICE = {"교무실", "관리자", "교장실", "교무부", "비교과"}
MEAL = {"급식소", "급식실", "영양사실"}
GONE = {"전출", "휴가&휴직"}  # 학교 자리를 만들지 않는 그룹
# 옛 학년 코드(일정 wr_7에 남은 값): 1~6 학년, 10 교무실·11 관리자, 100 행정실, 101 급식소, 그 밖(20·102·1000) 지원
OLD_CODES = {**{n: f"g{n}" for n in range(1, 7)}, 10: "office", 11: "office", 100: "admin", 101: "meal",
             20: "support", 102: "support", 1000: "support"}
DUTY_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*[~∼\-]\s*(\d{1,2}):(\d{2})")
LEGACY_KINDS = ((re.compile(r"(전일\s*)?부재"), "부재"), (re.compile(r"연차"), "연가"))  # D12 이관 보강(J1 낱말표는 그대로)
OVERTIME = re.compile(r"(시\s*간\s*외|초\s*과)\s*근\s*무")
G5_HASH = re.compile(r"sha256:12000:[^:$]+:[^:$]+")
FILE_ALIAS = {**ALIAS, "jfif": "jpg"}
HWP_FAMILY = {"hwp", "hwpx"}


def group_of(name):
    """구 그룹 이름 → 소속 값. 전출·휴직은 None."""
    name = name.strip()
    if name in GONE:
        return None
    m = GRADE.fullmatch(name)
    if m:
        return f"g{m[1]}"
    if name in OFFICE:
        return "office"
    if name == "행정실":
        return "admin"
    if name in MEAL:
        return "meal"
    return "support"


def seat_fields(group_name, mb_4):
    """(그룹 이름, mb_4) → {group, cls, position}. 그룹 이름이 None이면 그룹 없음(지원, 직위 비움). 전출·휴직이면 None."""
    pos = mb_4.strip()
    if group_name is None:
        return {"group": "support", "cls": "기타/지원", "position": ""}
    group = group_of(group_name)
    if group is None:
        return None
    if group.startswith("g"):
        n = group[1]
        m = CLASS_NO.fullmatch(pos)
        if m:
            return {"group": group, "cls": f"{n}-{int(m[1])}", "position": ""}
        return {"group": group, "cls": f"{n}-전담", "position": "" if pos == "전담" else pos[:40]}
    if group == "office":
        return {"group": group, "cls": "교무실", "position": "" if pos == "교직원" else pos[:40]}
    if group == "admin":
        return {"group": group, "cls": "행정실", "position": pos[:40]}
    if group == "meal":
        return {"group": group, "cls": "급식소", "position": pos[:40]}
    return {"group": group, "cls": "기타/지원", "position": " · ".join(x for x in (group_name.strip(), pos) if x)[:40]}


def unique_name(name, cls, taken):
    """같은 반·이름이 있으면 뒤에 (2)·(3)…을 붙인다. taken: {(cls, name)}, 고른 이름을 더한다."""
    name = name.strip()[:40] or "이름 없음"
    out, n = name, 1
    while (cls, out) in taken:
        n += 1
        tail = f"({n})"
        out = name[:40 - len(tail)] + tail
    taken.add((cls, out))
    return out


def day(text):
    try:
        return date.fromisoformat(text.strip()) if len(text.strip()) == 10 else None
    except ValueError:
        return None


def moment(text):
    """'YYYY-MM-DD HH:MM:SS'(서울 시각) → aware datetime. 0000-… 같은 빈 값은 None."""
    try:
        return timezone.make_aware(datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def password_of(mb_password):
    """구 해시 → Django 저장 값. sha256:12000: 형식이 아니면 None(쓸 수 없는 비밀번호로 둔다)."""
    return f"g5pbkdf2${mb_password}" if G5_HASH.fullmatch(mb_password or "") else None


class _Text(HTMLParser):
    """<br>·</p>·</tr>·</div>·</li>·제목 끝은 줄바꿈, 표 칸은 탭, 그림은 [그림]. 스크립트·스타일 내용은 버린다."""

    BREAK = {"p", "tr", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "br":
            self.out.append("\n")
        elif tag == "img":
            self.out.append("[그림]")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th"):
            self.out.append("\t")
        elif tag in self.BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"[ \t\r\n\f]+", " ", data))  # HTML 안의 줄바꿈은 빈칸(브라우저와 같게)


def html_to_text(html):
    p = _Text()
    p.feed(html)
    p.close()
    text = "".join(p.out).replace("\xa0", " ")
    lines = [re.sub(r" {2,}", " ", line).strip(" ").rstrip("\t") for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def body_of(row):
    """일정·게시글 본문 → 평문. wr_option에 html이 없으면 원래 평문이다(엔티티만 풀지 않고 그대로)."""
    content = row.get("wr_content") or ""
    if "html" in (row.get("wr_option") or ""):
        return html_to_text(content)
    return content.replace("\r\n", "\n").strip()


def duty_of(subject):
    """복무 제목 '이름 | 내용' → (이름, 유형, 시간) 또는 (None, 까닭). 사유·장소는 버린다.
    유형은 내용에서 가장 앞에 나온 복무 낱말(J1 붙여넣기 낱말표 + 이관 보강: 부재·연차). 시간은 HH:MM~HH:MM만.
    D12: 유형 낱말이 없는 활동(연수·협의회·사전답사 등)은 출장. 시간외·초과근무만 있는 줄은 버린다."""
    name, bar, text = subject.partition("|")
    name, text = name.strip(), text.strip()
    if not bar or not name:
        return None, "복무 이름 없음"
    if not text:
        return None, "복무 내용 없음"
    hits = [(m.start(), k) for pat, k in (*DUTY_KINDS, *LEGACY_KINDS) for m in [pat.search(text)] if m]
    if not hits and OVERTIME.search(text):
        return None, "시간외·초과근무"
    kind = min(hits)[1] if hits else "출장"
    t = DUTY_TIME.search(text)
    time = f"{int(t[1]):02d}:{t[2]}~{int(t[3]):02d}:{t[4]}" if t else ("" if kind in PART_DAY else "종일")
    return (name[:40], kind, time), ""


BRACKETS = re.compile(r"[(\[（［][^)\]）］]*[)\]）］]?")
TITLE_TAIL = re.compile(r"(선생님|선생|교사|쌤|교장|교감|원감|주무관|실무사|실무원|행정실장|실장|부장|수석|영양사|사서|님)$")
PURE_NAME = re.compile(r"[가-힣]{2,5}")


def duty_name(raw, roster):
    """복무 이름 칸 → 저장할 이름(사유·장소가 섞여도 이름만). 못 정하면 None. 이름 칸 원문은 저장하지 않는다.
    명부 이름이 칸 안에 있으면 그 이름(여럿이면 가장 앞, 같은 자리면 긴 것). 없으면 괄호 안·직함 꼬리를 떼고 한글 2~5자만."""
    hits = [(raw.find(n), -len(n), n) for n in roster if len(n) >= 2 and n in raw]
    if hits:
        return min(hits)[2]
    s = re.sub(r"\s+", "", BRACKETS.sub("", raw))
    while TITLE_TAIL.search(s) and len(TITLE_TAIL.sub("", s)) >= 2:
        s = TITLE_TAIL.sub("", s)
    return s if PURE_NAME.fullmatch(s) else None


class Rejected(Exception):
    """첨부를 받지 않는 까닭(보고서에 그대로 적는다)."""


def judge_file(path, source):
    """디스크 파일 + 원래 이름 → (판별 형식, 넣을 이름). 받지 않으면 Rejected.
    공지·일정 업로드와 같은 판별기(filekinds.judge). 한글끼리(hwp↔hwpx)는 판별 형식으로, jfif는 jpg로 확장자를 고친다."""
    name = re.sub(r"[\x00-\x1f\x7f]", "", (source or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    stem, ext = os.path.splitext(name)
    ext = ext.lower().lstrip(".")
    want = FILE_ALIAS.get(ext, ext)
    with open(path, "rb") as fh:
        try:
            kind = judge(fh)
        except hwp.UnsafeFile as e:
            raise Rejected(str(e)) from None
        except hwp.HwpError:
            kind = ""
    if not kind:
        raise Rejected(f"받지 않는 형식({ext or '확장자 없음'})")
    if kind != want and not (kind in HWP_FAMILY and want in HWP_FAMILY):
        raise Rejected(f"이름과 내용이 다름({ext or '확장자 없음'}→{kind})")
    if kind != want or ext == "jfif":
        name = f"{stem}.{kind}"
    return kind, name[-200:]
