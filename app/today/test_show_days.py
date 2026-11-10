"""Q1 표시 요일: 켠 요일만 일정 주·월 보기에 칸이 생긴다(사람마다). 기간 일정은 켠 요일 칸에만 이어진다.

불변식
- 기본(월~금): 주 보기는 지금과 같고, 월 보기도 월~금 5칸(리드 결정 2026-11-10). 토를 켜면 토요일 칸에 그날 일정·복무·취합 마감이 보인다.
- 다른 선생님 화면은 그대로(설정은 회원마다).
- '이번 주': 켠 마지막 요일이 지나야 다음 주로 넘어간다.
"""
from datetime import date, datetime
from unittest import mock

from collect.models import Collection
from spaces.models import Attendance, Member

from .models import ALL
from .test_schedule_channels_qa import NOW, Base

SAT = date(2026, 10, 31)
SUN = date(2026, 11, 1)


def has_day(html, wd):
    return f"<b>{wd}</b>" in html or f"<b>{wd} · 오늘</b>" in html


class ShowDaysTests(Base):
    def setUp(self):
        super().setUp()
        self.ev(self.boss, "토요 체험학습", ALL, d=SAT)
        self.ev(self.boss, "일요 행사", ALL, d=SUN)
        Attendance.objects.create(space=self.hub, member=self.t4, date=SAT, name="사일", kind="출장", by=self.t4)
        Collection.objects.create(space=self.hub, created_by=self.boss, title="토요 마감 취합", source=Collection.Source.SHEET,
                                  unit_head=["학년반"], audience={"mode": "all"}, due=datetime(2026, 10, 31, 16, 0, tzinfo=NOW.tzinfo))

    def set_days(self, m, days):
        Member.objects.filter(pk=m.pk).update(days_on=days)

    def test_default_week_is_mon_to_fri(self):
        html = self.page(self.t4, "week")
        for wd in "월화수목금":
            self.assertTrue(has_day(html, wd), wd)
        self.assertFalse(has_day(html, "토"))
        self.assertFalse(has_day(html, "일"))
        self.assertIn('style="--n:5"', html)
        self.assertIn("10월 26일 ~ 30일", html)
        for t in ("토요 체험학습", "토요 마감 취합"):
            self.assertNotIn(t, html.split('id="ev-')[0])  # 판(대화상자 앞)에 없음

    def test_saturday_on_shows_saturday_items(self):
        self.set_days(self.t4, "012345")
        html = self.page(self.t4, "week")
        self.assertTrue(has_day(html, "토"))
        self.assertFalse(has_day(html, "일"))
        self.assertIn('style="--n:6"', html)
        self.assertIn("10월 26일 ~ 31일", html)
        board = html.split('id="ev-')[0]
        for t in ("토요 체험학습", "토요 마감 취합", "사일 출장"):
            self.assertIn(t, board)
        self.assertNotIn("일요 행사", board)
        other = self.page(self.t5, "week")  # 다른 선생님은 그대로
        self.assertFalse(has_day(other, "토"))

    def test_sunday_only(self):
        self.set_days(self.t4, "6")
        html = self.page(self.t4, "week")
        self.assertTrue(has_day(html, "일"))
        self.assertFalse(has_day(html, "월"))
        self.assertIn('style="--n:1"', html)
        self.assertIn("일요 행사", html.split('id="ev-')[0])

    def test_this_week_follows_last_day_on(self):
        sat_noon = datetime(2026, 10, 31, 12, 0, tzinfo=NOW.tzinfo)
        with mock.patch("django.utils.timezone.now", return_value=sat_noon):
            self.assertIn("11월 2일 ~ 6일", self.page(self.t5, "week"))  # 기본: 토요일이면 다음 주
            self.set_days(self.t4, "012345")
            html = self.page(self.t4, "week")
            self.assertIn("10월 26일 ~ 31일", html)
            self.assertIn("<b>토 · 오늘</b>", html)

    def test_month_shows_only_days_on(self):
        self.ev(self.boss, "금월 연수", ALL, d=date(2026, 11, 6), end=date(2026, 11, 9))
        c = self.as_(self.t4)
        r = c.get(f"{self.base}/schedule", {"mode": "month", "tab": "school", "w": "2026-10-01"})
        self.assertEqual([w for w, _ in r.context["dow"]], list("월화수목금"))
        self.assertTrue(all(len(wk["heads"]) == 5 for wk in r.context["mweeks"]))
        self.assertIn('style="--n:5"', r.content.decode())
        self.assertNotIn('data-date="2026-10-31"', r.content.decode())
        self.set_days(self.t4, "0123456")
        r = c.get(f"{self.base}/schedule", {"mode": "month", "tab": "school", "w": "2026-10-01"})
        self.assertEqual([w for w, _ in r.context["dow"]], list("일월화수목금토"))  # 일요일 시작 달력 순서
        self.assertIn("토요 체험학습", r.content.decode().split('id="ev-')[0])
        self.set_days(self.t4, "012345")
        r = c.get(f"{self.base}/schedule", {"mode": "month", "tab": "school", "w": "2026-11-01"})
        self.assertEqual([w for w, _ in r.context["dow"]], list("월화수목금토"))
        cells = {}
        for wk in r.context["mweeks"]:
            for row in wk["rows"]:
                for cell in row["cells"]:
                    cells.setdefault(cell["date"], []).extend(x.get("e").title for x in cell["items"] if x.get("kind") == "ev")
        self.assertIn("금월 연수", cells["2026-11-07"])  # 켠 토요일엔 이어진다
        self.assertNotIn("2026-11-08", cells)  # 끈 일요일은 칸이 없다
        self.assertIn("금월 연수", cells["2026-11-09"])

    def test_month_drops_weeks_with_no_day_in_month(self):
        # 2026년 8월 1일은 토요일: 첫 주(7/26~8/1)의 월~금은 모두 7월이라 그 주를 뺀다
        r = self.as_(self.t4).get(f"{self.base}/schedule", {"mode": "month", "tab": "school", "w": "2026-08-01"})
        self.assertEqual(r.context["mweeks"][0]["heads"][0]["n"], 3)
        self.set_days(self.t4, "0123456")
        r = self.as_(self.t4).get(f"{self.base}/schedule", {"mode": "month", "tab": "school", "w": "2026-08-01"})
        self.assertEqual(r.context["mweeks"][0]["heads"][0]["n"], 26)

    def test_paste_toast_says_off_days(self):
        self.assertIn("주말 2건은 월중행사표에만", self.page(self.t4, "week", added="3", same="0", weekend="2"))
        self.set_days(self.t4, "0123")
        self.assertIn("꺼 둔 요일 2건은 월중행사표에만", self.page(self.t4, "week", added="3", same="0", weekend="2"))
