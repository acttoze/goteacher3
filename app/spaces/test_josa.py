"""리드 결정(G1): 조사는 숫자로 끝나면 읽는 소리 받침으로 고른다. 화면·알림 문구가 같은 함수(spaces.views._josa)를 쓴다."""
from django.test import SimpleTestCase

from .views import _josa


class JosaTests(SimpleTestCase):
    def test_hangul(self):
        self.assertEqual([_josa(w, "을", "를") for w in ("장소", "시간", "")], ["를", "을", "를"])
        self.assertEqual([_josa(w, "으로", "로") for w in ("교실", "체육관", "학교")], ["로", "으로", "로"])

    def test_numbers_by_reading(self):
        cases = {  # 마지막 수를 읽은 소리: 받침 있으면 을/으로(ㄹ 받침은 로)
            "15:30": ("을", "으로"),  # 삼십
            "13:10": ("을", "으로"),  # 십
            "13:00": ("을", "으로"),  # 영
            "3블록 13:10": ("을", "으로"),
            "11/2": ("를", "로"),  # 이
            "4-1": ("을", "로"),  # 일
            "10/5": ("를", "로"),  # 오
            "6": ("을", "으로"),  # 육
            "100": ("을", "으로"),  # 백
            "1000": ("을", "으로"),  # 천
            "3": ("을", "으로"),  # 삼
            "9": ("를", "로"),  # 구
        }
        for w, (a, b) in cases.items():
            with self.subTest(w=w):
                self.assertEqual((_josa(w, "을", "를"), _josa(w, "으로", "로")), (a, b))

    def test_other_ending_falls_back(self):
        self.assertEqual(_josa("100ml", "을", "를"), "를")
