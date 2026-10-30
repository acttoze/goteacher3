"""B5 NEIS 모듈: 실제 응답 형태의 픽스처로 목. 실패하면 빈 결과 + 로그(키 값은 로그에 없음)."""
import io
import json
from unittest import mock
from urllib.error import URLError

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from . import neis


def ok(path, rows, total=None):
    return {path: [
        {"head": [{"list_total_count": len(rows) if total is None else total},
                  {"RESULT": {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다."}}]},
        {"row": rows},
    ]}


NO_DATA = {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}
BAD_KEY = {"RESULT": {"CODE": "ERROR-290", "MESSAGE": "인증키가 유효하지 않습니다."}}

SCHOOLS = ok("schoolInfo", [
    {"ATPT_OFCDC_SC_CODE": "N10", "ATPT_OFCDC_SC_NM": "충청남도교육청", "SD_SCHUL_CODE": "8161025",
     "SCHUL_NM": "공주봉황초등학교", "SCHUL_KND_SC_NM": "초등학교", "ORG_RDNMA": "충청남도 공주시 오거리길 5"},
    {"ATPT_OFCDC_SC_CODE": "S10", "ATPT_OFCDC_SC_NM": "경상남도교육청", "SD_SCHUL_CODE": "9010000",
     "SCHUL_NM": "김해봉황초등학교", "SCHUL_KND_SC_NM": "초등학교", "ORG_RDNMA": "경상남도 김해시"},
])
CLASSES = ok("classInfo", [
    {"ATPT_OFCDC_SC_CODE": "S10", "SD_SCHUL_CODE": "9010000", "AY": "2026", "GRADE": "4", "CLASS_NM": n}
    for n in ["2", "1", "3", "4", "2"]  # 순서가 섞이거나 같은 반이 두 줄 와도
])
SCHEDULE = ok("SchoolSchedule", [
    {"AA_YMD": "20261102", "EVENT_NM": "학부모 상담 주간"},
    {"AA_YMD": "20261107", "EVENT_NM": "토요휴업일"},
])
MEALS = ok("mealServiceDietInfo", [{
    "MLSV_YMD": "20261102", "MMEAL_SC_CODE": "2", "MLSV_FGR": 412.0, "CAL_INFO": "687.3 Kcal",
    "DDISH_NM": "기장밥 <br/>돈육김치찌개(자율)(5.9.10) <br/>떡&amp;잡채*(5.6.13)",
}])


def resp(payload):
    return io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


@override_settings(NEIS_KEY="secret-key-123")
class NeisTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def call(self, payload, fn, *args):
        with mock.patch.object(neis, "urlopen", return_value=resp(payload)) as m:
            return fn(*args), m

    def test_search_schools(self):
        (rows, total), m = self.call(SCHOOLS, neis.search_schools, " 봉황초 ")
        self.assertEqual(total, 2)
        self.assertEqual(rows[1], {"name": "김해봉황초등학교", "atpt": "S10", "code": "9010000",
                                   "office": "경상남도교육청", "address": "경상남도 김해시"})
        url = m.call_args[0][0]
        self.assertIn("KEY=secret-key-123", url)
        self.assertIn("SCHUL_KND_SC_NM=", url)

    def test_short_query_does_not_call(self):
        with mock.patch.object(neis, "urlopen") as m:
            self.assertEqual(neis.search_schools("봉"), ([], 0))
        m.assert_not_called()

    @override_settings(NEIS_KEY="")
    def test_no_key_calls_without_key(self):
        _, m = self.call(SCHOOLS, neis.search_schools, "봉황초")
        self.assertNotIn("KEY=", m.call_args[0][0])

    def test_class_names(self):
        names, m = self.call(CLASSES, neis.class_names, "S10", "9010000", 4, 2026)
        self.assertEqual(names, ["1", "2", "3", "4"])
        self.assertIn("AY=2026", m.call_args[0][0])

    def test_school_year(self):
        from datetime import date
        self.assertEqual(neis.school_year(date(2027, 2, 28)), 2026)
        self.assertEqual(neis.school_year(date(2026, 3, 2)), 2026)

    def test_schedule_skips_saturday_off(self):
        rows, _ = self.call(SCHEDULE, neis.schedule, "S10", "9010000", "20261101", "20261130")
        self.assertEqual(rows, [{"ymd": "20261102", "name": "학부모 상담 주간"}])

    def test_meals_parse(self):
        rows, _ = self.call(MEALS, neis.meals, "S10", "9010000", "20261102", "20261106")
        self.assertEqual(rows[0]["kcal"], 687)
        self.assertEqual(rows[0]["count"], 412)
        self.assertEqual(rows[0]["dishes"], [
            {"name": "기장밥", "allergy": [], "self": False},
            {"name": "돈육김치찌개", "allergy": [5, 9, 10], "self": True},
            {"name": "떡&잡채", "allergy": [5, 6, 13], "self": False},
        ])

    def test_cache_hits_once(self):
        with mock.patch.object(neis, "urlopen", side_effect=lambda *a, **k: resp(SCHOOLS)) as m:
            neis.search_schools("봉황초")
            neis.search_schools("봉황초")
        self.assertEqual(m.call_count, 1)

    def test_no_data_is_empty_without_warning(self):
        with self.assertNoLogs("spaces.neis"):
            (rows, total), _ = self.call(NO_DATA, neis.search_schools, "없는학교")
        self.assertEqual((rows, total), ([], 0))

    def test_error_code_empty_and_logged_without_key(self):
        with self.assertLogs("spaces.neis", "WARNING") as logs:
            names, _ = self.call(BAD_KEY, neis.class_names, "S10", "9010000", 4, 2026)
        self.assertEqual(names, [])
        self.assertIn("ERROR-290", logs.output[0])
        self.assertNotIn("secret-key-123", "".join(logs.output))

    def test_network_failure_empty_logged_not_cached(self):
        err = URLError("https://open.neis.go.kr/hub/schoolInfo?KEY=secret-key-123")
        with self.assertLogs("spaces.neis", "WARNING") as logs, \
                mock.patch.object(neis, "urlopen", side_effect=err):
            self.assertEqual(neis.meals("S10", "9010000", "20261102", "20261106"), [])
        self.assertNotIn("secret-key-123", "".join(logs.output))
        _, m = self.call(MEALS, neis.meals, "S10", "9010000", "20261102", "20261106")
        m.assert_called_once()  # 실패는 캐시하지 않는다


class NeisPrototypeParityTests(SimpleTestCase):
    """QA(B5): prototype/assets/meal.js parseRow와 같은 결과여야 한다(JS Math.round·parseFloat 기준)."""

    def setUp(self):
        cache.clear()

    def parse(self, row):
        with mock.patch.object(neis, "_get", return_value=([row], 1)):
            return neis.meals("S10", "9091016", "20261102", "20261106")[0]

    def test_half_rounds_up_like_js(self):
        # JS Math.round(686.5)=687, Math.round(412.5)=413. 파이썬 round는 짝수 쪽(686·412).
        r = self.parse({"MLSV_YMD": "20261102", "CAL_INFO": "686.5 Kcal", "MLSV_FGR": 412.5, "DDISH_NM": "밥"})
        self.assertEqual((r["kcal"], r["count"]), (687, 413))

    def test_kcal_without_space_like_js(self):
        # JS parseFloat("700.4Kcal")=700.4
        r = self.parse({"MLSV_YMD": "20261102", "CAL_INFO": "700.4Kcal", "DDISH_NM": "밥"})
        self.assertEqual(r["kcal"], 700)

    def test_allergy_spaces_and_out_of_range(self):
        r = self.parse({"MLSV_YMD": "20261102", "CAL_INFO": "",
                        "DDISH_NM": "카레(자율)(1. 2.5)<BR />떡(0.20.3)<br>우유"})
        self.assertEqual(r["dishes"], [
            {"name": "카레", "allergy": [1, 2, 5], "self": True},
            {"name": "떡", "allergy": [3], "self": False},
            {"name": "우유", "allergy": [], "self": False},
        ])
        self.assertEqual((r["kcal"], r["count"]), (0, None))
