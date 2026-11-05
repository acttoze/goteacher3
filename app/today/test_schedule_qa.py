"""Q2·Q3 QA가 더한 경계: 날짜 끝값(이전·다음 계산이 넘치면 500), 달력 칸 밖 날짜."""
from datetime import date

from .models import Event
from .test_schedule_modes import Base


class DateEdgeTests(Base):
    def test_first_and_last_date_every_mode(self):
        for mode in ("day", "week", "month"):
            for w in ("0001-01-01", "0001-01-02", "9999-12-31", "9999-12-27"):
                self.assertEqual(self.client.get(self.url(), {"mode": mode, "w": w}).status_code, 200, (mode, w))

    def test_cell_at_last_date_saves_and_redraws(self):
        r = self.add("끝날 일정", date_="9999-12-31", slot="", mode="month", w="9999-12-01")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Event.objects.get().date, date(9999, 12, 31))

    def test_month_cell_outside_month_keeps_shown_month(self):
        """11월 달력의 끝 칸(12월 초)에 넣어도 보던 달이 그대로다."""
        r = self.add("다음 달 칸", date_="2026-12-03", slot="", mode="month", w="2026-11-01")
        html = r.content.decode()
        self.assertIn('data-mode="month"', html)
        self.assertIn('data-w="2026-11-01"', html)
        self.assertIn("다음 달 칸", html)
