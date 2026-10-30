"""한 줄 일정 파서(prototype TR.parse 이식) — 기대값은 prototype/assets/core.js를 node로 돌린 결과(오늘 = 2026-10-30 금).

design 예문 3개(landing.html)와 core.js 주석 예문, 교시·기간·시각·참석자 표기를 함께 본다.
알려진 prototype 동작도 그대로 옮겼다: '4-1 교실'은 대상 4-1로 먼저 잡혀 장소가 빈다.
리드 결정으로 다른 점: '3시 반'은 15:30(prototype은 15:00).
"""
from datetime import date

from django.test import SimpleTestCase

from .parse import block_of_time, parse, slot_text

TODAY = date(2026, 10, 30)
D = date.fromisoformat

# (입력, date, end, s, e, target, place, who, title)
CASES = [
    ("내일 2블록 4학년 과학실 안전교육 @김민준", "2026-10-31", None, "b2", "b2", "4학년", "과학실", ["김민준"],
     "2블록 4학년 과학실 안전교육"),
    ("모레 3블록 체육관 학예회 리허설", "2026-11-01", None, "b3", "b3", None, "체육관", [], "3블록 체육관 학예회 리허설"),
    ("금요일 방과후 4-1 교실 학년협의", "2026-10-30", None, "pm", "pm", "4-1", None, [], "방과후 4-1 교실 학년협의"),
    ("10/30 현장체험학습", "2026-10-30", None, None, None, None, None, [], "현장체험학습"),
    ("10/28~30 독서주간", "2026-10-28", "2026-10-30", None, None, None, None, [], "독서주간"),
    ("다음주 월요일 오전 교무실 협의", "2026-11-02", None, "b1", "b2", None, "교무실", [], "오전 교무실 협의"),
    ("오늘 3교시 음악실 리코딩 15:30", "2026-10-30", None, "b2", "b2", None, "음악실", [], "2블록 음악실 리코딩 15:30"),
    ("수~금 4학년 상담주간", "2026-11-04", "2026-11-06", None, None, "4학년", None, [], "4학년 상담주간"),
    ("오후 3시 반 4학년 학년협의", None, None, "pm", "pm", "4학년", None, [], "오후 3시 반 4학년 학년협의"),
    ("11월 3일 2~3블록 강당 학예회", "2026-11-03", None, "b2", "b3", None, "강당", [], "2~3블록 강당 학예회"),
    ("1-2교시 4학년 2반 운동장 체육", None, None, "b1", "b1", "4-2", "운동장", [], "1블록 4학년 2반 운동장 체육"),
    ("10.31. 과학실1 실험 @이수빈 @박서준", "2026-10-31", None, None, None, None, "과학실1", ["이수빈", "박서준"], "과학실1 실험"),
    ("전교생 강당 조회 내일 아침", "2026-10-31", None, "b1", "b1", "전교생", "강당", [], "전교생 강당 조회 아침"),
    ("담주 화 학년부장 회의", "2026-11-03", None, None, None, "학년부장", None, [], "학년부장 회의"),
    ("2블록~방과후 다목적실 바자회", None, None, "b2", "pm", None, "다목적실", [], "2블록~방과후 다목적실 바자회"),
    ("1·2학년 운동장 체육대회 11/5(목)", None, None, None, None, "1·2학년", "운동장", [], "1·2학년 운동장 체육대회 11/5(목)"),
    ("4학년부 돌봄교실1 회의 점심", None, None, "b3", "b3", "4학년부", "돌봄교실1", [], "4학년부 돌봄교실1 회의 점심"),
    ("오전 10:30 컴퓨터실 연수", None, None, "b1", "b2", None, "컴퓨터실", [], "오전 10:30 컴퓨터실 연수"),
    ("11/2~11/4 수학여행", "2026-11-02", "2026-11-04", None, None, None, None, [], "수학여행"),
    ("학예회 준비", None, None, None, None, None, None, [], "학예회 준비"),
    ("목요일 교장실 결재 2시", "2026-11-05", None, "b3", "b3", None, "교장실", [], "교장실 결재 2시"),
]


class ParseTests(SimpleTestCase):
    def test_prototype_examples(self):
        for raw, d, end, s, e, target, place, who, title in CASES:
            with self.subTest(raw=raw):
                p = parse(raw, TODAY)
                got = (p["date"], p["end"], p["s"], p["e"], p["target"], p["place"], p["who"], p["title"])
                want = (d and D(d), end and D(end), s, e, target, place, who, title)
                self.assertEqual(got, want)

    def test_year_wraps_in_winter(self):
        """prototype은 2026년 고정. 12월에 '1/5'는 다음 해 1월."""
        self.assertEqual(parse("1/5 졸업식", date(2026, 12, 20))["date"], date(2027, 1, 5))
        self.assertEqual(parse("3/2 개학", date(2026, 10, 30))["date"], date(2027, 3, 2))
        self.assertEqual(parse("9/1 개학", date(2026, 10, 30))["date"], date(2026, 9, 1))

    def test_half_hour(self):
        """리드 결정(G1): prototype과 달리 '반'은 30분('3시 반'·'3시30분' → 15:30). 제목은 그대로."""
        for raw in ("오후 3시 반 4학년 학년협의", "3시 반 학년협의", "3시30분 학년협의", "오후 3시 30분 학년협의"):
            with self.subTest(raw=raw):
                p = parse(raw, TODAY)
                self.assertEqual((p["time"], p["s"]), ("15:30", "pm"))
                self.assertIn("학년협의", p["title"])
        self.assertEqual(parse("오후 3시 반 4학년 학년협의", TODAY)["title"], "오후 3시 반 4학년 학년협의")
        self.assertEqual(parse("10시 반 회의", TODAY)["time"], "10:30")

    def test_bad_date_is_ignored(self):
        p = parse("2/31 행사", TODAY)
        self.assertIsNone(p["date"])

    def test_slot_text_and_time(self):
        self.assertEqual(slot_text("b2", "b3"), "2~3블록")
        self.assertEqual(slot_text("b1", "b3"), "종일")
        self.assertEqual(slot_text("b3", "pm"), "3블록~방과후")
        self.assertEqual(slot_text(None), "시간 미정")
        self.assertEqual(block_of_time("07:00"), "b1")
        self.assertEqual(block_of_time("12:20"), "b3")
        self.assertEqual(block_of_time("16:00"), "pm")
