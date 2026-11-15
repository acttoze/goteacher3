"""오늘 화면 오늘 일과 판(2026-11-15 사용자 요청): 위 한 줄 입력 대신 판 칸의 ＋, 오늘 하루만, 일정 화면과 같은 채널 줄
(학교·내 소속·개인·복무), 칸 안 카드는 가로로. 칸에서 쓰면 오늘 판(#live)을 돌려준다."""
import re

from .models import ME, Event
from .test_schedule_channels import HX, TODAY, Base


def board(html):
    return html[html.index('id="dgrid"'):html.index("<!-- /dgrid -->")]


def rows(html):
    """{줄 이름: 그 줄 HTML}."""
    parts = re.split(r'<div class="lbl"[^>]*>', board(html))[1:]
    return {re.match(r"[^<]*", p).group(0).strip(): p for p in parts}


class TodayBoardTests(Base):
    def today(self, m):
        return self.as_(m).get(f"{self.base}/today").content.decode()

    def add(self, m, line, scope, d=TODAY.isoformat(), path="events"):
        data = {"view": "today", "mode": "day", "w": d, "tab": "school", "line": line, "date": d, "slot": "", "scope": scope}
        return self.as_(m).post(f"{self.base}/today/{path}", data, headers=HX)

    def test_no_quick_input_and_cell_add_template(self):
        html = self.today(self.t4)
        self.assertNotIn('id="quickForm"', html)
        self.assertNotIn("한 줄로 일정 추가", html)
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertIn('hx-target="#live"', tpl)
        self.assertIn('name="view" value="today"', tpl)
        self.assertIn("js/schedule.js", html)

    def test_today_only_with_channel_rows(self):
        self.ev(self.t4, "학교 행사")
        self.ev(self.t4, "학년 협의", scope="g4", slot="b2")
        self.ev(self.t4, "내 메모", scope=ME)
        self.ev(self.t5, "남의 메모", scope=ME)
        self.ev(self.t5, "오학년 협의", scope="g5")
        self.ev(self.t4, "내일 행사", d=TODAY.replace(day=TODAY.day + 1))
        self.duty(self.t5)
        r = rows(self.today(self.t4))
        self.assertEqual(list(r), ["학교", "4학년", "개인", "복무", "점심"])
        self.assertIn("학교 행사", r["학교"])
        self.assertIn("독서주간 골든벨", r["학교"])
        self.assertIn("학년 협의", r["4학년"])
        self.assertIn("2블록", r["4학년"])  # 줄로 블록을 알 수 없어 카드에 블록 이름
        self.assertIn("내 메모", r["개인"])
        self.assertIn("오일 출장", r["복무"])
        whole = board(self.today(self.t4))
        for hidden in ("남의 메모", "오학년 협의", "내일 행사"):
            self.assertNotIn(hidden, whole)

    def test_cell_add_returns_today_board(self):
        r = self.add(self.t4, "학년 협의회", "g4")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('<div id="live"', html)
        self.assertNotIn("이번 주 바뀐 것", html)  # 일정 화면 판이 아니다
        e = Event.objects.get()
        self.assertEqual((e.scope, e.date, e.by_id), ("g4", TODAY, self.t4.pk))
        self.assertIn("학년 협의회", rows(html)["4학년"])

    def test_cell_add_cannot_widen_scope(self):
        self.assertEqual(self.add(self.t4, "남의 학년", "g5").status_code, 400)
        self.assertFalse(Event.objects.exists())

    def test_empty_line_error_stays_in_cell(self):
        r = self.add(self.t4, "", "all")
        self.assertEqual(r["HX-Retarget"], "#cellErr")

    def test_duty_cell_returns_today_board(self):
        r = self.add(self.t4, "사일 출장 13:00~16:30", "duty", path="duty")
        self.assertEqual(r.status_code, 200)
        self.assertIn("사일 출장", rows(r.content.decode())["복무"])

    def test_edit_dialog_returns_today_board(self):
        e = self.ev(self.t4, "고칠 일정")
        html = self.today(self.t4)
        dlg = html[html.index(f'id="ev-{e.key}"'):]
        self.assertIn('hx-target="#live"', dlg[:dlg.index("</form>")])
        r = self.as_(self.t4).post(f"{self.base}/today/e/{e.key}", {"view": "today", "date": TODAY.isoformat(), "slot": "b1", "place": "", "scope": "all"}, headers=HX)
        self.assertIn('<div id="live"', r.content.decode())
