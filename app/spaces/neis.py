"""NEIS 오픈API 연동(B5). prototype/grade.html·assets/meal.js의 호출을 서버로 옮긴 것.

- 인증키는 settings.NEIS_KEY(환경변수). 없으면 키 없이 부른다 — NEIS는 이때 한 번에 5건까지만 준다.
- 실패(네트워크·HTTP·NEIS 오류 코드)는 빈 결과 + 경고 로그. 로그에 주소·키를 남기지 않는다.
- 캐시는 Django 기본 캐시. 학교·학급은 하루, 학사·급식은 한 시간.
"""
import json
import logging
import math
import re
from datetime import date
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.core.cache import cache

API = "https://open.neis.go.kr/hub/"
TIMEOUT = 5
DAY = 60 * 60 * 24
HOUR = 60 * 60

log = logging.getLogger(__name__)


class NeisError(Exception):
    pass


def _get(path, params, ttl):
    """행 목록과 전체 건수(list_total_count). 실패하면 ([], 0)."""
    ck = "neis:" + path + ":" + urlencode(sorted(params.items()))
    hit = cache.get(ck)
    if hit is not None:
        return hit
    q = {"Type": "json", "pIndex": 1, "pSize": 100, **params}
    if settings.NEIS_KEY:
        q["KEY"] = settings.NEIS_KEY
    try:
        with urlopen(API + path + "?" + urlencode(q), timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        body = data.get(path)
        if body:
            result = (body[1].get("row", []), int(body[0]["head"][0]["list_total_count"]))
        elif data.get("RESULT", {}).get("CODE") == "INFO-200":  # 해당하는 데이터 없음
            result = ([], 0)
        else:
            raise NeisError(data.get("RESULT", {}).get("CODE", "형식 오류"))
    except (URLError, OSError, ValueError, KeyError, IndexError, NeisError) as e:
        # 주소에 키가 들어 있으므로 예외 메시지·주소는 남기지 않는다.
        log.warning("NEIS %s 실패: %s %s", path, type(e).__name__, e if isinstance(e, NeisError) else "")
        return [], 0
    cache.set(ck, result, ttl)
    return result


def school_year(today=None):
    """학년도: 1·2월은 전년도(grade.html과 같음)."""
    today = today or date.today()
    return today.year - 1 if today.month < 3 else today.year


def search_schools(name):
    """초등학교 이름 검색. (학교 목록, 전체 건수) — 키가 없으면 목록은 5곳까지 잘린다."""
    name = name.strip()
    if len(name) < 2:
        return [], 0
    rows, total = _get("schoolInfo", {"SCHUL_NM": name, "SCHUL_KND_SC_NM": "초등학교"}, DAY)
    return [
        {
            "name": r["SCHUL_NM"],
            "atpt": r["ATPT_OFCDC_SC_CODE"],
            "code": r["SD_SCHUL_CODE"],
            "office": r.get("ATPT_OFCDC_SC_NM", ""),
            "address": r.get("ORG_RDNMA", ""),
        }
        for r in rows
    ], total


def class_names(atpt, code, grade, year=None):
    """한 학년의 반 이름 목록(NEIS 학급정보). 예: ["1", "2", "3", "4"]."""
    params = {"ATPT_OFCDC_SC_CODE": atpt, "SD_SCHUL_CODE": code, "AY": year or school_year(), "GRADE": grade}
    rows, _ = _get("classInfo", params, DAY)
    return sorted({r["CLASS_NM"] for r in rows}, key=lambda s: (not s.isdigit(), s.zfill(3)))


def schedule(atpt, code, start, end):
    """학사일정 [{ymd, name}] — start·end는 'YYYYMMDD'. 토요휴업일은 뺀다."""
    params = {"ATPT_OFCDC_SC_CODE": atpt, "SD_SCHUL_CODE": code, "AA_FROM_YMD": start, "AA_TO_YMD": end}
    rows, _ = _get("SchoolSchedule", params, HOUR)
    return [{"ymd": r["AA_YMD"], "name": r["EVENT_NM"]} for r in rows if r.get("EVENT_NM") != "토요휴업일"]


def _lines(s):
    return [x.replace("&amp;", "&").strip() for x in re.split(r"<br\s*/?>", s or "", flags=re.I) if x.strip()]


def _dish(raw):
    """'돈육김치찌개(자율)(5.9.10)' → 이름·알레르기 번호(1~19)·자율배식."""
    s, allergy = raw, []
    m = re.search(r"\(([\d.\s]+)\)\s*$", s)
    if m:
        allergy = [int(x) for x in m.group(1).split(".") if x.strip().isdigit() and 1 <= int(x) <= 19]
        s = s[: m.start()].strip()
    return {"name": re.sub(r"\([^)]*\)", "", s).replace("*", "").strip(), "allergy": allergy, "self": "(자율)" in s}


def _js_round(value):
    """meal.js와 같게: parseFloat(앞쪽 숫자, "700.4Kcal"도) → Math.round(0.5는 올림). 숫자가 없으면 None."""
    m = re.match(r"\s*\d+(\.\d+)?", str(value or ""))
    return math.floor(float(m.group()) + 0.5) if m else None


def meals(atpt, code, start, end):
    """점심 [{ymd, dishes, kcal, count}] — start·end는 'YYYYMMDD'."""
    params = {
        "ATPT_OFCDC_SC_CODE": atpt, "SD_SCHUL_CODE": code, "MMEAL_SC_CODE": "2",
        "MLSV_FROM_YMD": start, "MLSV_TO_YMD": end,
    }
    rows, _ = _get("mealServiceDietInfo", params, HOUR)
    out = []
    for r in rows:
        kcal = _js_round(r.get("CAL_INFO")) or 0
        out.append({
            "ymd": r["MLSV_YMD"],
            "dishes": [_dish(x) for x in _lines(r.get("DDISH_NM"))],
            "kcal": kcal,
            "count": _js_round(r.get("MLSV_FGR")),
        })
    return out
