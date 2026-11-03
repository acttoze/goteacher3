"""H6 QA가 더한 경계. test_print_paste.py에 없는 것만.

- PassingBoundaryTests: 지금 통과하는 경계(토큰 바꿔 끼우기, 별지의 범위, 쿼리 수, 해 넘김).
- OpenDefectTests: QA가 찾은 결함. 고칠 때까지 실패한다. 리드가 '고치지 않음'으로 정하면 그 테스트를 지운다.
쪽 나눔(PDF)은 Django 테스트로 볼 수 없어 여기 없다.
"""
from datetime import date

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from collect.paste import PasteError, html_to_tsv
from spaces.models import School, Space

from .models import Event
from .test_print_paste import HEAD, MON, SAMPLE, UNTIL, Base
from .week_paste import read_week


class PassingBoundaryTests(Base):
    PATHS = ("schedule/print", "schedule/paste", "schedule/paste/preview")

    def test_swapped_tokens_are_404_for_every_path(self):
        """다른 학교 관리자·다른 학교 일반·동학년 회원이 이 학교 토큰으로, 이 학교 회원이 남의 토큰으로."""
        boss = self.seat("교무실", "다른교감", "office", manager=True, space=self.other)
        third = School.objects.create(neis_atpt="J10", neis_code="7000001", name="셋째초", plan="school", paid_until=UNTIL)
        third_hub = Space.objects.create(school=third, kind="school", grade=0, name="셋째초")
        cases = [(boss, self.hub), (self.out, self.hub), (self.gr, self.hub), (self.t41, self.other), (self.t41, third_hub), (self.t41, self.grade)]
        for m, space in cases:
            for path in self.PATHS:
                with self.subTest(member=m.name, space=space.name, path=path):
                    c = self.as_(m)
                    url = f"/s/{space.token}/{path}"
                    self.assertEqual(c.post(url, {"paste": SAMPLE, "w": "2026-10-26"}).status_code if path != "schedule/print" else c.get(url).status_code, 404)
        self.assertEqual(Event.objects.count(), 0)

    def test_bad_token_is_404(self):
        c = self.as_(self.t41)
        for path in self.PATHS:
            self.assertEqual(c.get(f"/s/{self.hub.token[:-1]}x/{path}").status_code, 404)

    def test_extra_sheet_keeps_to_the_viewers_range(self):
        """칸이 넘쳐 별지로 간 일정도 범위 밖은 나가지 않는다."""
        for i in range(12):
            self.ev(self.boss, f"모두의 일정 {i}", "all")
            self.ev(self.t51, f"오학년 비밀 {i}", "g5")
            self.ev(self.t51, f"오일 메모 {i}", "me")
        html = self.as_(self.t41).get(f"{self.base}/schedule/print", {"view": "month", "d": "2026-10-28"}).content.decode()
        self.assertIn("건 별지", html)
        self.assertIn("모두의 일정 11", html)
        self.assertNotIn("오학년 비밀", html)
        self.assertNotIn("오일 메모", html)
        boss = self.as_(self.boss).get(f"{self.base}/schedule/print", {"view": "month", "d": "2026-10-28"}).content.decode()
        self.assertIn("오학년 비밀 11", boss)
        self.assertNotIn("오일 메모", boss)

    def test_member_without_group_sees_school_wide_and_own_only(self):
        loner = self.seat("보건실", "무소속", "")
        self.ev(self.boss, "모두의 행사", "all")
        self.ev(self.t41, "사학년 협의", "g4")
        self.ev(loner, "내 메모", "me")
        html = self.as_(loner).get(f"{self.base}/schedule/print", {"d": "2026-10-28"}).content.decode()
        self.assertIn("모두의 행사", html)
        self.assertIn("내 메모", html)
        self.assertNotIn("사학년 협의", html)

    def test_print_works_at_expired_school(self):
        self.school.paid_until = date(2020, 1, 1)
        self.school.save()
        self.assertEqual(self.as_(self.t41).get(f"{self.base}/schedule/print").status_code, 200)

    def test_print_query_count_does_not_grow_with_events(self):
        c = self.as_(self.t41)
        url = f"{self.base}/schedule/print?view=week&d=2026-10-28"
        self.ev(self.boss, "하나", "all")
        with CaptureQueriesContext(connection) as few:
            c.get(url)
        for i in range(40):
            self.ev(self.boss, f"일정 {i}", "g4" if i % 2 else "all")
        with CaptureQueriesContext(connection) as many:
            c.get(url)
        self.assertEqual(len(few), len(many))

    def test_one_paste_with_many_rows_stays_inside_the_space(self):
        """같은 제목이 다른 학교에 있어도 중복이 아니고, 넣은 줄은 이 공간에만 있다."""
        Event.objects.create(space=self.other, title="학교운영위원회 정기회의", date=date(2026, 10, 29))
        self.as_(self.t41).post(f"{self.base}/schedule/paste", {"paste": SAMPLE, "w": "2026-10-26"})
        self.assertEqual(Event.objects.in_space(self.hub).count(), 5)
        self.assertEqual(Event.objects.in_space(self.other).count(), 1)


class ReadWeekBoundaryTests(SimpleTestCase):
    def one(self, cell, mon=MON):
        return read_week(f"{HEAD}{cell}\t\t제목\t\t", mon)

    def test_year_end_range_and_new_year_date(self):
        mon = date(2026, 12, 28)
        r = self.one("12/30~1/2", mon)["rows"][0]
        self.assertEqual((r["date"], r["end"]), (date(2026, 12, 30), date(2027, 1, 2)))
        self.assertEqual(self.one("1/1", mon)["rows"][0]["date"], date(2027, 1, 1))

    def test_leap_day_in_leap_year_and_not(self):
        self.assertEqual(self.one("2/29", date(2028, 2, 28))["rows"][0]["date"], date(2028, 2, 29))
        for mon in (date(2027, 3, 1), date(2026, 10, 26)):
            d = self.one("2/29", mon)
            self.assertEqual((d["rows"], d["skipped"]), ([], 1))

    def test_clipboard_html_never_carries_markup(self):
        tsv = html_to_tsv("<table><tr><td>날짜</td><td>내용</td></tr><tr><td>10/28</td><td><img src=x onerror=alert(1)>행사<script>alert(2)</script></td></tr></table>")
        self.assertEqual(read_week(tsv, MON)["rows"][0]["title"], "행사")

    def test_rowspan_time_and_date_copied_down(self):
        tsv = html_to_tsv("<table><tr><td>날짜</td><td>시간</td><td>내용</td></tr>"
                          "<tr><td rowspan=3>10/28~30</td><td rowspan=2>2블록</td><td>첫째</td></tr>"
                          "<tr><td>둘째</td></tr><tr><td></td><td>셋째</td></tr></table>")
        rows = read_week(tsv, MON)["rows"]
        self.assertEqual([(r["title"], r["s"], r["end"]) for r in rows],
                         [("첫째", "b2", date(2026, 10, 30)), ("둘째", "b2", date(2026, 10, 30)), ("셋째", None, date(2026, 10, 30))])


class OpenDefectTests(Base):
    """고칠 때까지 실패한다(보고서의 D번호)."""

    def test_d2_toast_query_never_500s(self):
        """_paste_toast: isdigit()은 참이지만 int()가 터지는 값. 주소만 바꾸면 일정 화면이 500."""
        for space, member in ((self.hub, self.t41), (self.grade, self.gr)):
            c = self.as_(member)
            c.raise_request_exception = False
            for added in ("²", "9" * 5000):
                with self.subTest(space=space.name, added=added[:3]):
                    self.assertEqual(c.get(f"/s/{space.token}/schedule", {"added": added, "same": "0"}).status_code, 200)

    def test_d3_date_column_wins_over_weekday_column(self):
        """요일 칸이 앞에 있으면 날짜 칸을 버리고 보고 있는 주로 넣는다(다른 주 표가 말없이 엉뚱한 주로)."""
        r = read_week("요일\t날짜\t내용\n수\t11/4\t행사", MON)["rows"][0]
        self.assertEqual(r["date"], date(2026, 11, 4))

    def test_d4_two_line_header(self):
        """머리글 2줄(계획 > 내용·장소)은 '계획 내용'이 되어 머리글을 못 찾고, 장소 칸이 제목이 된다."""
        tsv = html_to_tsv("<table><tr><td rowspan=2>날짜</td><td colspan=2>계획</td></tr><tr><td>내용</td><td>장소</td></tr>"
                          "<tr><td>10/28</td><td>행사</td><td>강당</td></tr></table>")
        r = read_week(tsv, MON)["rows"][0]
        self.assertEqual((r["title"], r["place"]), ("행사", "강당"))

    def test_d5_merged_title_is_not_also_the_place(self):
        tsv = html_to_tsv("<table><tr><td>날짜</td><td>시간</td><td>내용</td><td>장소</td><td>담당</td></tr>"
                          "<tr><td>10/28</td><td></td><td colspan=2>합쳐진 행사</td><td>김하나</td></tr>"
                          "<tr><td>10/29</td><td></td><td>일반</td><td>강당</td><td>이두리</td></tr></table>")
        r = read_week(tsv, MON)["rows"][0]
        self.assertEqual(r["title"], "합쳐진 행사")
        self.assertEqual(r["place"], "")

    def test_d6_long_title_from_html_is_counted_not_cut(self):
        """TSV는 201자 제목을 세어 빼는데(test_too_long_title_is_counted_not_cut) HTML 길은 말없이 200자로 자른다."""
        tsv = html_to_tsv("<table><tr><td>날짜</td><td>내용</td></tr><tr><td>10/28</td><td>" + "가" * 250 + "</td></tr><tr><td>10/29</td><td>짧음</td></tr></table>")
        d = read_week(tsv, MON)
        self.assertEqual([r["title"] for r in d["rows"]], ["짧음"])
        self.assertEqual(d["skipped"], 1)

    def test_d7_big_html_message_is_about_this_screen(self):
        """1MB 넘는 HTML의 안내가 모으기 문구('받는 사람 500줄, 칸 40개')로 나온다."""
        big = "<table>" + "<tr><td>10/28</td><td>x</td></tr>" * 40000 + "</table>"
        out = self.as_(self.t41).post(f"{self.base}/schedule/paste/preview", {"html": big, "w": "2026-10-26"}).content.decode()
        self.assertIn("500줄", out)
        self.assertNotIn("받는 사람", out)

    def test_d8_blank_date_after_unreadable_date_does_not_borrow_older_date(self):
        """못 읽은 날짜 아래 빈 날짜 줄이 그 앞의 날짜로 들어간다. 안내에는 1줄만 뺐다고 나온다."""
        d = read_week(HEAD + "10/28\t\t첫째\t\t\n어느 날\t\t못 읽음\t\t\n\t\t이어짐\t\t\n10/29\t\t둘째\t\t", MON)
        self.assertEqual([r["title"] for r in d["rows"]], ["첫째", "둘째"])
        self.assertEqual(d["skipped"], 2)


class PasteErrorWordingTests(SimpleTestCase):
    def test_too_many_rows_and_text_messages(self):
        for text in (HEAD + "10/28\t\t제목\t\t\n" * 501, "가\t" * 200_000):
            with self.assertRaises(PasteError) as e:
                read_week(text, MON)
            self.assertIn("500줄", str(e.exception))
