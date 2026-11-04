"""NEIS 오픈API 연동(B5). prototype/grade.html·assets/meal.js의 호출을 서버로 옮긴 것.

- 인증키는 settings.NEIS_KEY(환경변수). 없으면 키 없이 부른다 — NEIS는 이때 한 번에 5건까지만 준다.
- 실패(네트워크·HTTP·NEIS 오류 코드)는 빈 결과 + 경고 로그. 로그에 주소·키를 남기지 않는다.
  빈 결과는 Failed(빈 list)라 기존 호출부는 그대로 돌고, '없음(INFO-200)'과 가르려면 failed(x)로 본다.
- 캐시는 Django 기본 캐시. 학교·학급은 하루, 학사·급식은 한 시간. 실패도 FAIL_TTL(1분) 캐시 —
  NEIS가 멈췄을 때 같은 요청이 매번 TIMEOUT만큼 워커를 붙잡지 않게.
  급식 실패는 MEAL_FAIL_TTL(10분): TV가 5분마다 부르므로 그보다 길어야 장애 때 TV마다 워커를 붙잡지 않는다. 담당자의 '다시 시도'는 forget_failed_meals로 바로.
- 멈춤 대비(웹 슬롯을 묶지 않게): 요청당 TIMEOUT 2초·재시도 없음. 연결·HTTP 실패가 FAIL_LIMIT번 이어지면 BREAK_SECONDS 동안 부르지 않고 곧장 실패로 돌려준다(차단기).
  NEIS가 답을 하고 오류 코드를 준 것(인증키 등)은 멈춤이 아니라 세지 않는다. 같은 키 동시 호출은 한 스레드만 부르고 나머지는 그 결과를 기다린다.
  차단기 값은 Django 기본 캐시(프로세스 메모리)라 프로세스마다 따로 센다.
"""
import http.client
import json
import logging
import math
import re
import threading
from datetime import date
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.core.cache import cache

API = "https://open.neis.go.kr/hub/"
TIMEOUT = 2
DAY = 60 * 60 * 24
HOUR = 60 * 60
FAIL_TTL = 60
MEAL_FAIL_TTL = 600
FAIL_LIMIT = 3
BREAK_SECONDS = 60
BREAKER = "neis:breaker"
FAILS = "neis:fails"
STALLED = (URLError, OSError, http.client.HTTPException)  # 연결·시간 초과·HTTP 오류·응답 끊김

log = logging.getLogger(__name__)


class NeisError(Exception):
    pass


class Failed(list):
    """불러오기 실패 표시가 붙은 빈 목록. []와 같고 거짓이라 기존 호출부는 '없음'처럼 돈다."""


def failed(rows):
    """schedule()·meals() 결과가 NEIS 실패(네트워크·HTTP·오류 코드)였나. 데이터 없음(INFO-200)이면 False."""
    return isinstance(rows, Failed)


def _cache_key(path, params):
    return "neis:" + path + ":" + urlencode(sorted(params.items()))


def _note_stalled():
    cache.add(FAILS, 0, BREAK_SECONDS * 5)
    try:
        if cache.incr(FAILS) >= FAIL_LIMIT:
            cache.set(BREAKER, True, BREAK_SECONDS)
            cache.delete(FAILS)
    except ValueError:  # 그사이 지워졌다
        pass


def _fetch(path, params):
    """NEIS 한 번 부르기. 성공 (행, 건수)·실패 None."""
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
    except (*STALLED, ValueError, KeyError, IndexError, NeisError) as e:
        # 주소에 키가 들어 있으므로 예외 메시지·주소는 남기지 않는다.
        log.warning("NEIS %s 실패: %s %s", path, type(e).__name__, e if isinstance(e, NeisError) else "")
        if isinstance(e, STALLED):
            _note_stalled()
        return None
    cache.delete(FAILS)
    return result


_flights = {}
_flights_lock = threading.Lock()


def _get(path, params, ttl, fail_ttl=FAIL_TTL):
    """행 목록과 전체 건수(list_total_count). 실패하면 (Failed(), 0)."""
    ck = _cache_key(path, params)
    hit = cache.get(ck)
    if hit is not None:
        return hit
    if cache.get(BREAKER):
        return Failed(), 0
    with _flights_lock:
        flight = _flights.get(ck)
        mine = flight is None
        if mine:
            flight = _flights[ck] = threading.Event()
    if not mine:
        flight.wait(TIMEOUT + 1)
        hit = cache.get(ck)
        return hit if hit is not None else (Failed(), 0)
    try:
        result = _fetch(path, params)
        if result is None:
            result, ttl = (Failed(), 0), fail_ttl
        cache.set(ck, result, ttl)
        return result
    finally:
        with _flights_lock:
            del _flights[ck]
        flight.set()


def school_year(today=None):
    """학년도: 1·2월은 전년도(grade.html과 같음)."""
    today = today or date.today()
    return today.year - 1 if today.month < 3 else today.year


def search_schools(name):
    """초등학교 이름 검색. (학교 목록, 전체 건수) — 키가 없으면 목록은 5곳까지 잘린다. 못 불렀으면 목록이 Failed(failed()로 본다)."""
    name = name.strip()
    if len(name) < 2:
        return [], 0
    rows, total = _get("schoolInfo", {"SCHUL_NM": name, "SCHUL_KND_SC_NM": "초등학교"}, DAY)
    if failed(rows):
        return rows, 0
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
    if failed(rows):
        return rows
    return [{"ymd": r["AA_YMD"], "name": r["EVENT_NM"]} for r in rows if r.get("EVENT_NM") != "토요휴업일"]


def day_off(atpt, code, d):
    """그날이 학교 쉬는 날인가(G2 묶음 알림). 수업공제일 휴업일·공휴일이거나 행사 이름에 '방학'. NEIS 실패면 False."""
    ymd = f"{d:%Y%m%d}"
    params = {"ATPT_OFCDC_SC_CODE": atpt, "SD_SCHUL_CODE": code, "AA_FROM_YMD": ymd, "AA_TO_YMD": ymd}
    rows, _ = _get("SchoolSchedule", params, HOUR)  # schedule()과 같은 캐시 키
    return any(r.get("SBTR_DD_SC_NM") in ("휴업일", "공휴일") or "방학" in r.get("EVENT_NM", "") for r in rows)


def _lines(s):
    return [x.replace("&amp;", "&").strip() for x in re.split(r"<br\s*/?>", s or "", flags=re.I) if x.strip()]


def _dish(raw):
    """'돈육김치찌개(자율)(5.9.10)' → 이름·알레르기 번호(1~19)·자율배식."""
    s, allergy = raw, []
    m = re.search(r"\(([\d.\s]+)\)\s*$", s)
    if m:
        allergy = [int(x) for x in m.group(1).split(".") if x.strip().isdigit() and 1 <= int(x) <= 19]
        s = s[: m.start()].strip()
    name = re.sub(r"\([^)]*\)", "", s).replace("*", "").strip()
    name = re.sub(r"^[.\s]+", "", name)  # 학교가 붙인 앞 점('.찹쌀현미밥') — 리드 결정(G1), meal.js는 별표만 뺌
    return {"name": name, "allergy": allergy, "self": "(자율)" in s}


def _js_round(value):
    """meal.js와 같게: parseFloat(앞쪽 숫자, "700.4Kcal"도) → Math.round(0.5는 올림). 숫자가 없으면 None."""
    m = re.match(r"\s*\d+(\.\d+)?", str(value or ""))
    return math.floor(float(m.group()) + 0.5) if m else None


def _nutr(s):
    """'탄수화물(g) : 90.1' 줄들 → [{name, unit, value}]. 형식이 다른 줄은 뺀다(meal.js와 같음)."""
    found = (re.match(r"(.+?)\((.+?)\)\s*:\s*(\d+(?:\.\d+)?)", x) for x in _lines(s))
    return [{"name": m[1].strip(), "unit": m[2], "value": float(m[3])} for m in found if m]


def _origin(s):
    """'쌀 : 국내산' 줄들 → [[품목, 원산지]]. 비고·원산지 없는 줄은 뺀다(meal.js와 같음)."""
    parts = (re.split(r"\s*:\s*", x) for x in _lines(s))
    pairs = ((p[0].replace("(종류)", "").strip(), p[1].strip() if len(p) > 1 else "") for p in parts)
    return [[item, place] for item, place in pairs if place and item != "비고"]


def _meal_params(atpt, code, start, end):
    return {"ATPT_OFCDC_SC_CODE": atpt, "SD_SCHUL_CODE": code, "MMEAL_SC_CODE": "2",
            "MLSV_FROM_YMD": start, "MLSV_TO_YMD": end}


def forget_failed_meals(atpt, code, start, end):
    """'다시 시도' 단추(M1): 기억해 둔 실패만 지운다. 성공 캐시는 그대로 둔다."""
    ck = _cache_key("mealServiceDietInfo", _meal_params(atpt, code, start, end))
    hit = cache.get(ck)
    if hit is not None and failed(hit[0]):
        cache.delete(ck)


def meals(atpt, code, start, end):
    """점심 [{ymd, dishes, kcal, count, nutr, origin}] — start·end는 'YYYYMMDD'."""
    rows, _ = _get("mealServiceDietInfo", _meal_params(atpt, code, start, end), HOUR, MEAL_FAIL_TTL)
    if failed(rows):
        return rows
    out = []
    for r in rows:
        kcal = _js_round(r.get("CAL_INFO")) or 0
        out.append({
            "ymd": r["MLSV_YMD"],
            "dishes": [_dish(x) for x in _lines(r.get("DDISH_NM"))],
            "kcal": kcal,
            "count": _js_round(r.get("MLSV_FGR")),
            "nutr": _nutr(r.get("NTR_INFO")),
            "origin": _origin(r.get("ORPLC_INFO")),
        })
    return out
