"""한 줄 일정 파서 — prototype/assets/core.js TR.parse를 그대로 옮긴 것(동작 기준은 prototype).

"내일 2블록 4학년 과학실 안전교육 @김민준" → 날짜·기간·블록·장소·대상·참석자·제목.
제목은 쓴 문장 그대로 두고 날짜 표현과 @참석자만 뺀다(교시는 블록 이름으로 바꿔 넣는다).
블록·장소·대상은 검색·장소 충돌 경고용. 유형(행사·회의·복무)은 R1 모델에 없어 옮기지 않았다.
"""
import re
from datetime import date, timedelta

# prototype/assets/data.js TR.slots — 블록 시간표 기본값(학교별 설정은 R2)
SLOTS = [
    {"k": "b1", "label": "1블록", "from": "08:40", "to": "10:30"},
    {"k": "b2", "label": "2블록", "from": "10:30", "to": "12:20"},
    {"k": "b3", "label": "3블록", "from": "12:20", "to": "14:40"},
    {"k": "pm", "label": "방과후", "from": "14:40", "to": "24:00"},
]
SLOT_KEYS = [s["k"] for s in SLOTS]
# TR.rooms(특별실 사전). 긴 이름부터 찾는다.
ROOMS = ["과학실1", "과학실2", "과학실", "음악실", "미술실", "컴퓨터실", "영어실", "도서관", "시청각실", "강당", "체육관",
         "돌봄교실1", "돌봄교실2", "교무실", "교장실", "운동장", "다목적실"]
# TR.toBlock: 예전 교시·시간대 이름 → 블록
TO_BLOCK = {"1": "b1", "2": "b1", "3": "b2", "4": "b2", "5": "b3", "6": "b3", "7": "pm",
            "아침": "b1", "중간놀이": "b2", "점심": "b3", "방과후": "pm"}
JS_DOW = "일월화수목금토"  # JS getDay() 순서


def slot_idx(k):
    return SLOT_KEYS.index(k) if k in SLOT_KEYS else -1


def block_of_time(hhmm):
    """TR.blockOfTime: 시각 → 블록. 첫 블록 전이면 b1, 범위 밖이면 방과후."""
    for s in SLOTS:
        if s["from"] <= hhmm < s["to"]:
            return s["k"]
    return "b1" if hhmm < SLOTS[0]["from"] else "pm"


def slot_text(s, e=None, time=None):
    """TR.slotText: 블록 표기. 없으면 '시간 미정', 1~3블록을 다 쓰면 '종일'."""
    if not s:
        return time or "시간 미정"
    e = e or s
    if slot_idx(s) == 0 and slot_idx(e) >= 2:
        return "종일"
    a, b = SLOTS[slot_idx(s)], SLOTS[slot_idx(e)]
    if a is b:
        return a["label"] + (" " + time if time else "")
    if "블록" in a["label"] and "블록" in b["label"]:
        return a["label"].replace("블록", "") + "~" + b["label"]
    return a["label"] + "~" + b["label"]


def _js_day(d):
    return (d.weekday() + 1) % 7


def _md(today, mo, dd):
    """'10/30' → 날짜. prototype은 2026년 고정 — 오늘보다 반년 넘게 앞이면 다음 해로 본다(12월에 '1/5')."""
    try:
        d = date(today.year, int(mo), int(dd))
    except ValueError:
        return None
    if (today - d).days > 183:
        d = d.replace(year=today.year + 1)
    return d


def parse(raw, today):
    """TR.parse. 반환: {raw, date, end, s, e, time, target, place, who, title}. 날짜가 없으면 None(부르는 쪽이 오늘로)."""
    raw = str(raw)
    st = {"text": f" {raw} ", "keep": f" {raw} "}
    out = {"raw": raw, "date": None, "end": None, "s": None, "e": None, "time": None,
           "target": None, "place": None, "who": [], "title": ""}

    def take(pattern, fn, is_date=False):
        m = re.search(pattern, st["text"])
        if not m:
            return
        fn(m)
        st["text"] = st["text"].replace(m.group(0), " ", 1)
        if is_date:
            st["keep"] = st["keep"].replace(m.group(0), " ", 1)

    def set_(**kw):
        out.update(kw)

    take(r"\s(오늘|금일)\s", lambda m: set_(date=today), True)
    take(r"\s(내일|명일)\s", lambda m: set_(date=today + timedelta(1)), True)
    take(r"\s모레\s", lambda m: set_(date=today + timedelta(2)), True)
    take(r"\s(다음\s?주|담주)\s?([월화수목금])(요일)?\s",
         lambda m: set_(date=today + timedelta((7 - _js_day(today)) + JS_DOW.index(m.group(2)))), True)
    if not out["date"]:
        take(r"\s(이번\s?주\s?)?([월화수목금])요일\s",
             lambda m: set_(date=today + timedelta((JS_DOW.index(m.group(2)) - _js_day(today) + 7) % 7)), True)
    w = r"(?:\([월화수목금토일]\))?"
    if not out["date"]:
        take(r"\s(\d{1,2})\s?[/.월]\s?(\d{1,2})일?" + w + r"\s?[~–]\s?(?:(\d{1,2})\s?[/.월]\s?)?(\d{1,2})일?" + w + r"\s",
             lambda m: set_(date=_md(today, m.group(1), m.group(2)),
                            end=_md(today, m.group(3) or m.group(1), m.group(4))), True)
    if not out["date"]:
        def wk_range(m):
            a, b = JS_DOW.index(m.group(2)), JS_DOW.index(m.group(3))
            d = today + timedelta((a - _js_day(today) + 7) % 7)
            set_(date=d, end=d + timedelta(b - a))
        take(r"\s(이번\s?주\s?)?([월화수목금])\s?[~–]\s?([월화수목금])(요일)?\s", wk_range, True)
    if not out["date"]:
        take(r"\s(\d{1,2})\s?[/.월]\s?(\d{1,2})일?\.?\s", lambda m: set_(date=_md(today, m.group(1), m.group(2))), True)
    if out["end"] and (not out["date"] or out["end"] <= out["date"]):
        out["end"] = None

    def bk(n):
        return "pm" if int(n) >= 4 else "b" + str(max(1, int(n)))

    take(r"\s(\d)\s?[~\-–]\s?(\d)\s?블록\s", lambda m: set_(s=bk(m.group(1)), e=bk(m.group(2))))
    if not out["s"]:
        take(r"\s(\d)\s?블록\s?[~\-–]\s?방과\s?후\s", lambda m: set_(s=bk(m.group(1)), e="pm"))
    if not out["s"]:
        take(r"\s(\d)\s?블록\s", lambda m: set_(s=bk(m.group(1)), e=bk(m.group(1))))

    def as_block(m):  # 교시로 써도 받아 주되, 제목에는 블록 이름으로(화면에 교시가 나오지 않게)
        st["keep"] = st["keep"].replace(m.group(0), " " + slot_text(out["s"], out["e"]) + " ", 1)

    if not out["s"]:
        take(r"\s(\d)\s?[~\-–]\s?(\d)\s?교시\s",
             lambda m: (set_(s=TO_BLOCK.get(m.group(1)), e=TO_BLOCK.get(m.group(2))), as_block(m)))
    if not out["s"]:
        take(r"\s(\d)\s?교시\s", lambda m: (set_(s=TO_BLOCK.get(m.group(1)), e=TO_BLOCK.get(m.group(1))), as_block(m)))
    if not out["s"]:
        take(r"\s(아침|중간놀이|점심|방과\s?후)\s",
             lambda m: set_(s=TO_BLOCK[re.sub(r"\s", "", m.group(1))], e=TO_BLOCK[re.sub(r"\s", "", m.group(1))]))
    if not out["s"]:
        take(r"\s(오전)\s", lambda m: set_(s="b1", e="b2"))
    if not out["s"]:
        take(r"\s(오후)\s(?!\d)", lambda m: set_(s="b3", e="pm"))

    def at_time(m):
        h = int(m.group(2))
        if m.group(1) == "오후" and h < 12:
            h += 12
        if not m.group(1) and h < 8:
            h += 12
        mm = m.group(3) or m.group(4) or ("30" if "반" in m.group(0) else "00")
        out["time"] = f"{h:02d}:{int(mm):02d}"
        if not out["s"]:
            out["s"] = out["e"] = block_of_time(out["time"])

    # 리드 결정(G1): prototype은 '시(…)?'가 먼저 맞아 '3시 반'이 15:00 — '반'을 먼저 본다(30분).
    take(r"\s(오전|오후)?\s?(\d{1,2})(?::(\d{2})|시\s?반|시(?:\s?(\d{1,2})분)?)\s", at_time)
    if out["s"] and not out["e"]:
        out["e"] = out["s"]
    if out["s"] is None:  # 교시 7 넘는 숫자 등으로 블록을 못 찾음
        out["e"] = None

    take(r"\s(\d)\s?-\s?(\d)\s?(반)?\s", lambda m: set_(target=f"{m.group(1)}-{m.group(2)}"))
    if not out["target"]:
        take(r"\s(\d)\s?학년\s?(\d)\s?반\s", lambda m: set_(target=f"{m.group(1)}-{m.group(2)}"))
    if not out["target"]:
        take(r"\s(\d(?:\s?[·,]\s?\d)*)\s?학년(부)?\s",
             lambda m: set_(target=re.sub(r"\s", "", m.group(1)) + "학년" + (m.group(2) or "")))
    if not out["target"]:
        take(r"\s(전교생?|전\s?교직원|교직원|학년부장|담임|전\s?교원)\s",
             lambda m: set_(target=re.sub(r"\s", "", m.group(1))))

    for room in sorted(ROOMS, key=len, reverse=True):
        if room in st["text"]:
            out["place"] = room
            st["text"] = st["text"].replace(room, " ", 1)
            break
    if not out["place"]:
        take(r"\s(\d-\d)\s?교실\s", lambda m: set_(place=m.group(1) + " 교실"))

    out["who"] = re.findall(r"@([가-힣]{2,4})", st["text"])
    out["title"] = re.sub(r"\s+", " ", re.sub(r"@[가-힣]{2,4}", " ", st["keep"])).strip()
    return out
