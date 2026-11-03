"""H6 일정 인쇄(주간·월중)와 주간계획 표 붙여넣기.

불변식
- 학교 공간에만 있다: 동학년 공간·다른 공간·비회원은 404.
- 인쇄에는 reachable_q 안의 일정만: 일반 회원에게 남의 '나'·다른 그룹 일정이 없고, 관리자에게도 남의 '나'는 없다.
- 제목은 자르지 않고 이스케이프한다. 유형은 흑백 기호로 구분한다.
- 표 붙여넣기: 회원 누구나, 만료 학교에서도(일정은 계속). 제목은 원문 그대로, 다시 넣어도 중복 없음, 못 읽은 줄은 세어 알림.
- 쓴 사람(by)은 언제나 붙여넣은 사람: 담당 칸의 글자로 다른 회원 이름으로 올릴 수 없다.
"""
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase

from collect.paste import PasteError
from spaces.models import Member, School, Space
from spaces.neis import Failed

from .models import Event
from .parse import event_type, parse
from .print_view import month_cells
from .week_paste import read_week

User = get_user_model()
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 30, 11, 5, tzinfo=KST)  # 금요일
MON = date(2026, 10, 26)
UNTIL = date(2099, 1, 1)
HEAD = "날짜\t시간\t내용\t장소\t담당\n"
SAMPLE = (HEAD + "10/27(화)\t3블록\t3학년 생활안전 체험교실 (실내화 지참)\t강당\t신유진\n"
          "10/28(수)\t\t1학년 학부모 독서교실 신청서 회수\t\t강지은\n"
          "10/29(목)\t15:00\t학교운영위원회 정기회의\t교장실\t한상우\n"
          "10/30(금)\t1블록\t전교 소방 대피훈련 (사이렌 후 운동장 집결)\t운동장\t탁현수\n"
          "\t\t6학년 수학여행 사전 안전교육\t시청각실\t양서윤")


class ReadWeekTests(SimpleTestCase):
    def one(self, cell):
        return read_week(f"{HEAD}{cell}\t\t제목\t\t", MON)["rows"][0]

    def test_sample_like_prototype(self):
        d = read_week(SAMPLE, MON)
        self.assertEqual(len(d["rows"]), 5)
        self.assertEqual(d["skipped"], 0)
        r = d["rows"]
        self.assertEqual((r[0]["date"], r[0]["s"], r[0]["place"]), (date(2026, 10, 27), "b3", "강당"))
        self.assertEqual(r[0]["title"], "3학년 생활안전 체험교실 (실내화 지참)")  # 제목은 원문 그대로
        self.assertIsNone(r[1]["s"])  # 시간이 없으면 전일
        self.assertEqual(r[2]["s"], "pm")  # 15:00 → 방과후
        self.assertEqual(r[4]["date"], date(2026, 10, 30))  # 빈 날짜는 위 날짜를 따라감
        self.assertEqual(r[4]["who"], "양서윤")

    def test_date_forms(self):
        for cell, want in [("10/28", date(2026, 10, 28)), ("10/28(수)", date(2026, 10, 28)), ("10.28.", date(2026, 10, 28)),
                           ("10.28.(수)", date(2026, 10, 28)), ("10월 28일", date(2026, 10, 28)), ("2026-10-28", date(2026, 10, 28)),
                           ("2026.10.28.", date(2026, 10, 28))]:
            with self.subTest(cell):
                self.assertEqual(self.one(cell)["date"], want)

    def test_weekday_is_in_the_week_being_viewed(self):
        for cell, want in [("수요일", date(2026, 10, 28)), ("수", date(2026, 10, 28)), ("월요일", MON), ("금", date(2026, 10, 30))]:
            with self.subTest(cell):
                self.assertEqual(self.one(cell)["date"], want)

    def test_ranges(self):
        for cell, a, b in [("10/28~30", date(2026, 10, 28), date(2026, 10, 30)),
                           ("10/28(수)~10/30(금)", date(2026, 10, 28), date(2026, 10, 30)),
                           ("10/30~11/2", date(2026, 10, 30), date(2026, 11, 2)),
                           ("월~수", MON, date(2026, 10, 28))]:
            with self.subTest(cell):
                r = self.one(cell)
                self.assertEqual((r["date"], r["end"]), (a, b))

    def test_blank_date_under_a_range_stays_the_range(self):
        r = read_week(HEAD + "10/28~30\t\t첫째\t\t\n\t\t둘째\t\t", MON)["rows"]
        self.assertEqual(r[1]["end"], date(2026, 10, 30))

    def test_time_forms(self):
        for cell, s, e in [("2블록", "b2", "b2"), ("1~2블록", "b1", "b2"), ("5교시", "b3", "b3"), ("오전", "b1", "b2"),
                           ("14:00~15:00", "b3", "b3"), ("오후 3시", "pm", "pm")]:
            with self.subTest(cell):
                r = read_week(f"{HEAD}10/28\t{cell}\t제목\t\t", MON)["rows"][0]
                self.assertEqual((r["s"], r["e"]), (s, e))

    def test_unreadable_lines_are_counted_not_guessed(self):
        d = read_week(HEAD + "10/28\t\t첫째\t\t\n어느 날\t\t못 읽음\t\t\n13/45\t\t없는 날\t\t\n10/29\t\t둘째\t\t", MON)
        self.assertEqual([r["title"] for r in d["rows"]], ["첫째", "둘째"])
        self.assertEqual(d["skipped"], 2)

    def test_line_without_any_date_above_is_counted(self):
        d = read_week(HEAD + "\t\t날짜가 없음\t\t\n10/28\t\t있음\t\t", MON)
        self.assertEqual([r["title"] for r in d["rows"]], ["있음"])
        self.assertEqual(d["skipped"], 1)

    def test_day_with_nothing_is_not_an_error(self):
        d = read_week(HEAD + "10/28\t\t\t\t\n10/29\t\t있음\t\t", MON)
        self.assertEqual(len(d["rows"]), 1)
        self.assertEqual(d["skipped"], 0)

    def test_header_found_by_name_and_other_columns_dropped(self):
        text = "10월 5주 주간교육계획\n구분\t장소\t행사\t일자\t학생 이름\n가\t강당\t체험교실\t10/28\t김학생"
        d = read_week(text, MON)
        self.assertEqual(len(d["rows"]), 1)
        self.assertEqual((d["rows"][0]["title"], d["rows"][0]["place"]), ("체험교실", "강당"))
        self.assertNotIn("김학생", str(d["rows"]))

    def test_no_header_is_read_in_order(self):
        d = read_week("10/28\t2블록\t체험교실\t강당\t", MON)
        self.assertEqual((d["rows"][0]["s"], d["rows"][0]["title"]), ("b2", "체험교실"))
        d = read_week("10/28\t체험교실\n10/29\t협의회", MON)
        self.assertEqual([r["title"] for r in d["rows"]], ["체험교실", "협의회"])

    def test_place_and_target_found_in_title_when_no_column(self):
        r = read_week(HEAD + "10/28\t\t4학년 과학실 안전교육\t\t", MON)["rows"][0]
        self.assertEqual((r["place"], r["target"], r["title"]), ("과학실", "4학년", "4학년 과학실 안전교육"))

    def test_student_words_in_title_are_kept(self):
        """C7은 칸 이름 규칙이다. 일정 제목의 '학생 명단 제출'은 업무 이름이라 막지 않는다."""
        r = read_week(HEAD + "10/28\t\t수학여행 학생 명단 제출\t\t", MON)["rows"][0]
        self.assertEqual(r["title"], "수학여행 학생 명단 제출")

    def test_500_lines(self):
        ok = read_week(HEAD + "\n".join(f"10/28\t\t일정{i}\t\t" for i in range(500)), MON)
        self.assertEqual(len(ok["rows"]), 500)
        with self.assertRaises(PasteError):
            read_week(HEAD + "\n".join(f"10/28\t\t일정{i}\t\t" for i in range(501)), MON)

    def test_too_long_title_is_counted_not_cut(self):
        d = read_week(HEAD + "10/28\t\t" + "가" * 201 + "\t\t\n10/28\t\t" + "나" * 200 + "\t\t", MON)
        self.assertEqual([len(r["title"]) for r in d["rows"]], [200])
        self.assertEqual(d["skipped"], 1)

    def test_one_column_is_an_error(self):
        with self.assertRaises(PasteError):
            read_week("가\n나\n다", MON)

    def test_empty_is_an_error(self):
        with self.assertRaises(PasteError):
            read_week(" \n ", MON)

    def test_no_such_day_is_counted(self):
        self.assertEqual(read_week(HEAD + "2/29\t\t없는 날\t\t", MON)["skipped"], 1)

    def test_one_line_input_leap_day_next_year_does_not_crash(self):
        self.assertIsNone(parse("2/29 행사", date(2028, 10, 2))["date"])

    def test_unclear_dates_are_counted_not_shrunk(self):
        """리드 결정: 거꾸로·없는 끝 날짜·평년 2/29·날짜 두 개는 하루로 줄이지 않고 못 읽은 줄로."""
        for cell in ("10/30~10/28", "10/28~45", "2/27~29", "10/28, 10/30", "10/28 10/30", "수요일, 목요일", "2024-10-28"):
            with self.subTest(cell):
                d = read_week(f"{HEAD}{cell}\t\t제목\t\t", MON)
                self.assertEqual((d["rows"], d["skipped"]), ([], 1))

    def test_date_with_weekday_after_and_time_in_one_cell(self):
        for cell in ("10/28 수", "10월 28일 수요일"):
            with self.subTest(cell):
                self.assertEqual(self.one(cell)["date"], date(2026, 10, 28))
        r = read_week("일시\t내용\n10/28 14:00~15:00\t행사\n10/29 1~2블록\t행사2", MON)["rows"]
        self.assertEqual([(x["date"], x["s"], x["e"]) for x in r], [(date(2026, 10, 28), "b3", "b3"), (date(2026, 10, 29), "b1", "b2")])

    def test_block_to_block(self):
        r = read_week(f"{HEAD}10/28\t1블록~2블록\t제목\t\t", MON)["rows"][0]
        self.assertEqual((r["s"], r["e"]), ("b1", "b2"))

    def test_year_is_nearest_to_the_week_being_viewed(self):
        for mon, cell, want in [(date(2027, 1, 4), "12/28", date(2026, 12, 28)), (date(2026, 12, 28), "1/2", date(2027, 1, 2)),
                                (date(2026, 10, 26), "10/28", date(2026, 10, 28)), (date(2027, 11, 1), "2/29", date(2028, 2, 29))]:
            with self.subTest(cell):
                self.assertEqual(read_week(f"{HEAD}{cell}\t\t제목\t\t", mon)["rows"][0]["date"], want)
        r = read_week(f"{HEAD}12/30~1/2\t\t제목\t\t", date(2027, 1, 4))["rows"][0]
        self.assertEqual((r["date"], r["end"]), (date(2026, 12, 30), date(2027, 1, 2)))

    def test_plain_two_line_header(self):
        r = read_week("날짜\t계획\t\n\t내용\t장소\n10/28\t행사\t강당", MON)
        self.assertEqual((r["rows"][0]["title"], r["rows"][0]["place"], r["skipped"]), ("행사", "강당", 0))


class EventTypeTests(SimpleTestCase):
    def test_like_prototype(self):
        for title, want in [("학교운영위원회 정기회의", "meet"), ("학년 협의", "meet"), ("소방 안전교육", "work"), ("서류 제출", "work"),
                            ("김민준 출장", "duty"), ("가을 운동회", "event")]:
            with self.subTest(title):
                self.assertEqual(event_type(title), want)


class MonthCellsTests(SimpleTestCase):
    def test_short_titles_fit(self):
        shown, over = month_cells([{"text": "짧은 제목"} for _ in range(3)], 6)
        self.assertEqual((len(shown), over), (3, []))

    def test_overflow_goes_to_extra_and_reserves_a_line(self):
        items = [{"text": "짧은 제목"} for _ in range(8)]
        shown, over = month_cells(items, 6)
        self.assertEqual((len(shown), len(over)), (5, 3))

    def test_long_title_never_cut(self):
        items = [{"text": "가" * 200}]
        shown, over = month_cells(items, 6)
        self.assertEqual((shown, over), ([], items))


class Base(TestCase):
    def setUp(self):
        p = [mock.patch("django.utils.timezone.now", return_value=NOW),
             mock.patch("spaces.neis.meals", return_value=[]),
             mock.patch("spaces.neis.schedule", return_value=[{"ymd": "20261028", "name": "독서주간 골든벨"}])]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        self.hub = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        self.base = f"/s/{self.hub.token}"
        self.boss = self.seat("교무실", "부장", "office", manager=True)
        self.t41 = self.seat("4-1", "사일", "g4")
        self.t51 = self.seat("5-1", "오일", "g5")
        other = School.objects.create(neis_atpt="J10", neis_code="7000000", name="다른초", plan="school", paid_until=UNTIL)
        self.other = Space.objects.create(school=other, kind="school", grade=0, name="다른초")
        self.grade = Space.objects.create(school=other, grade=4, name="4학년")
        self.out = self.seat("외부", "외부", "g4", space=self.other)
        self.gr = self.seat("4-1", "동학년", "", space=self.grade)

    def seat(self, cls, name, group, manager=False, space=None):
        u = User.objects.create(username=f"google:{name}", first_name=name)
        return Member.objects.create(space=space or self.hub, user=u, name=name, cls=cls, group=group, is_manager=manager)

    def as_(self, m):
        c = Client()
        c.force_login(m.user)
        return c

    def ev(self, by, title, scope, **kw):
        kw.setdefault("date", MON + timedelta(days=2))
        return Event.objects.create(space=self.hub, by=by, title=title, scope=scope, **kw)


class PrintTests(Base):
    def page(self, m, **q):
        q = {"view": "week", "d": "2026-10-28", **q}
        return self.as_(m).get(f"{self.base}/schedule/print", q)

    def test_only_what_the_viewer_can_see(self):
        self.ev(self.boss, "모두의 행사", "all")
        self.ev(self.t41, "사학년 협의", "g4")
        self.ev(self.t51, "오학년 협의", "g5")
        self.ev(self.t51, "오일의 메모", "me")
        self.ev(self.t41, "사일의 메모", "me")
        for view in ("week", "month"):
            with self.subTest(view):
                html = self.page(self.t41, view=view).content.decode()
                self.assertIn("모두의 행사", html)
                self.assertIn("사학년 협의", html)
                self.assertIn("사일의 메모", html)
                self.assertNotIn("오학년 협의", html)
                self.assertNotIn("오일의 메모", html)
                boss = self.page(self.boss, view=view).content.decode()
                self.assertIn("오학년 협의", boss)  # 관리자는 그룹 일정까지(reachable_q)
                self.assertNotIn("오일의 메모", boss)
                self.assertNotIn("사일의 메모", boss)

    def test_other_space_grade_space_and_stranger_404(self):
        Event.objects.create(space=self.other, title="다른 학교 비밀", date=MON)
        self.assertEqual(self.as_(self.out).get(f"{self.base}/schedule/print").status_code, 404)
        self.assertNotIn("다른 학교 비밀", self.page(self.t41).content.decode())
        self.assertEqual(self.as_(self.gr).get(f"/s/{self.grade.token}/schedule/print").status_code, 404)
        self.assertEqual(Client().get(f"{self.base}/schedule/print").status_code, 302)

    def test_header_stamp_and_a4_landscape(self):
        html = self.page(self.t41).content.decode()
        self.assertIn("새솔초", html)
        self.assertIn("10월 26일 ~ 30일", html)
        self.assertRegex(html, r"size:\s*A4 landscape")
        for w in ("담당", "교감", "교장"):
            self.assertIn(f"<th>{w}</th>", html)
        self.assertNotIn("A3 ", html)

    def test_type_symbols_and_legend(self):
        self.ev(self.boss, "가을 운동회", "all")
        self.ev(self.boss, "안전교육", "all")
        self.ev(self.boss, "정기회의", "all")
        self.ev(self.boss, "김민준 출장", "all")
        for view in ("week", "month"):
            html = self.page(self.t41, view=view).content.decode()
            with self.subTest(view):
                for sym, title in [("◆", "가을 운동회"), ("■", "안전교육"), ("▲", "정기회의"), ("○", "김민준 출장")]:
                    self.assertRegex(html, rf'{sym}</span>\s*<span class="t">{title}')
                self.assertIn("◆ 행사", html)
                self.assertIn("○ 복무", html)

    def test_title_escaped_and_never_cut(self):
        long = "가나다라마바사아자차" * 19 + "<b>끝</b>"
        self.ev(self.boss, long, "all")
        for view in ("week", "month"):
            html = self.page(self.t41, view=view).content.decode()
            with self.subTest(view):
                self.assertIn("가나다라마바사아자차" * 19 + "&lt;b&gt;끝&lt;/b&gt;", html)
                self.assertNotIn("<b>끝</b>", html)

    def test_month_grid_has_whole_month_and_sundays_first(self):
        self.ev(self.boss, "월말 행사", "all", date=date(2026, 10, 31))
        html = self.page(self.t41, view="month").content.decode()
        self.assertIn("2026년 10월 월중행사", html)
        self.assertIn("월말 행사", html)
        self.assertLess(html.index(">일</div>"), html.index(">토</div>"))
        self.assertEqual(html.count('class="mc'), 35)  # 2026-10: 목요일 시작, 5주

    def test_month_extra_page_only_when_needed(self):
        html = self.page(self.t41, view="month").content.decode()
        self.assertNotIn("별지", html)
        for i in range(12):
            self.ev(self.boss, f"많은 일정 {i}", "all")
        html = self.page(self.t41, view="month").content.decode()
        self.assertIn("건 별지", html)
        self.assertIn("많은 일정 11", html)

    def test_neis_included_and_failure_said(self):
        self.assertIn("독서주간 골든벨", self.page(self.t41).content.decode())
        with mock.patch("spaces.neis.schedule", return_value=Failed()):
            self.assertIn("나이스에서 불러오지 못했어요", self.page(self.t41).content.decode())

    def test_bad_query_falls_back(self):
        for q in ({"d": "2026-02-30"}, {"d": "zzz"}, {"view": "year"}, {"d": "1000-01-01"}):
            with self.subTest(q):
                self.assertEqual(self.page(self.t41, **q).status_code, 200)

    def test_schedule_page_has_buttons_only_in_school(self):
        html = self.as_(self.t41).get(f"{self.base}/schedule?w=2026-10-26").content.decode()
        self.assertIn(f"{self.base}/schedule/print?view=week&amp;d=2026-10-26", html)
        self.assertIn(f"{self.base}/schedule/paste?w=2026-10-26", html)
        html = self.as_(self.gr).get(f"/s/{self.grade.token}/schedule").content.decode()
        self.assertNotIn("schedule/print", html)
        self.assertNotIn("schedule/paste", html)


class PasteViewTests(Base):
    def post(self, m=None, text=SAMPLE, **kw):
        return self.as_(m or self.t41).post(f"{self.base}/schedule/paste", {"paste": text, "w": "2026-10-26", **kw})

    def test_member_pastes_and_rows_show_in_that_week(self):
        r = self.post()
        self.assertEqual(r.status_code, 303)
        self.assertIn("w=2026-10-26", r["Location"])
        self.assertEqual(Event.objects.in_space(self.hub).count(), 5)
        e = Event.objects.in_space(self.hub).get(title="학교운영위원회 정기회의")
        self.assertEqual((e.scope, e.slot, e.place, e.by), ("all", "pm", "교장실", self.t41))
        # 다른 그룹 회원도 본다(학교 공용)
        html = self.as_(self.t51).get(r["Location"]).content.decode()
        self.assertIn("학교운영위원회 정기회의", html)
        self.assertIn("5건", html)

    def test_who_column_does_not_change_author(self):
        self.seat("5-2", "한상우", "g5")
        self.post()
        e = Event.objects.in_space(self.hub).get(title="학교운영위원회 정기회의")
        self.assertEqual(e.by, self.t41)  # by는 권한 근거: 담당 칸 글자로 넘어가지 않는다
        self.assertIn("담당 한상우", e.target)

    def test_same_paste_twice_makes_no_duplicates(self):
        self.post()
        r = self.post()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(Event.objects.in_space(self.hub).count(), 5)
        self.assertIn("same=5", r["Location"])

    def test_duplicates_inside_one_paste(self):
        self.post(text=HEAD + "10/28\t\t같은 일\t\t\n10/28\t\t같은 일\t\t")
        self.assertEqual(Event.objects.in_space(self.hub).count(), 1)

    def test_other_scope_is_not_a_duplicate(self):
        self.post(text=HEAD + "10/28\t\t같은 일\t\t", scope="g4")
        self.post(text=HEAD + "10/28\t\t같은 일\t\t", scope="all")
        self.assertEqual(Event.objects.in_space(self.hub).count(), 2)

    def test_someone_elses_private_event_does_not_block_mine(self):
        self.ev(self.t51, "같은 일", "me")
        self.post(text=HEAD + "10/28\t\t같은 일\t\t", scope="me")
        self.assertEqual(Event.objects.in_space(self.hub).filter(by=self.t41).count(), 1)

    def test_hidden_same_event_is_not_revealed(self):
        """리드 결정: 내게 안 보이는 일정과는 비교하지 않는다('이미 있음'으로 드러나면 안 된다)."""
        self.ev(self.t51, "오학년 비밀", "g5")
        text = HEAD + "10/28\t\t오학년 비밀\t\t"
        out = self.as_(self.t41).post(f"{self.base}/schedule/paste/preview", {"paste": text, "w": "2026-10-26", "scope": "g5"}).content.decode()
        self.assertNotIn("이미 있", out)
        self.assertIn("1건 넣기", out)
        self.post(text=text, scope="g5")
        self.assertEqual(Event.objects.in_space(self.hub).filter(title="오학년 비밀").count(), 2)
        self.post(m=self.boss, text=text, scope="g5")  # 관리자에게는 보이므로 중복
        self.assertEqual(Event.objects.in_space(self.hub).filter(title="오학년 비밀").count(), 2)

    def test_weekend_told_after_paste(self):
        r = self.post(text=HEAD + "10/31\t\t토요 행사\t\t\n10/30~31\t\t금토 행사\t\t\n10/31~11/1\t\t주말 내내\t\t")
        self.assertIn("weekend=2", r["Location"])
        html = self.as_(self.t41).get(r["Location"]).content.decode()
        self.assertIn("주말 2건은 월중행사표에만 나와요", html)

    def test_scope_default_all_and_bad_scope(self):
        self.post(text=HEAD + "10/28\t\t첫째\t\t")
        self.post(text=HEAD + "10/28\t\t둘째\t\t", scope="zzz")
        self.assertEqual(set(Event.objects.in_space(self.hub).values_list("scope", flat=True)), {"all"})

    def test_expired_school_keeps_working(self):
        self.school.paid_until = date(2020, 1, 1)
        self.school.save()
        self.assertEqual(self.post().status_code, 303)
        self.assertEqual(Event.objects.in_space(self.hub).count(), 5)

    def test_other_space_grade_space_stranger(self):
        self.assertEqual(self.post(m=self.out).status_code, 404)
        c = self.as_(self.gr)
        for url in ("schedule/paste", "schedule/paste/preview"):
            self.assertEqual(c.post(f"/s/{self.grade.token}/{url}", {"paste": SAMPLE}).status_code, 404)
        self.assertEqual(c.get(f"/s/{self.grade.token}/schedule/paste").status_code, 404)
        self.assertEqual(Client().post(f"{self.base}/schedule/paste", {"paste": SAMPLE}).status_code, 302)
        self.assertEqual(Event.objects.count(), 0)

    def test_preview_counts_and_saves_nothing(self):
        r = self.as_(self.t41).post(f"{self.base}/schedule/paste/preview", {"paste": SAMPLE + "\n어느 날\t\t못 읽음\t\t", "w": "2026-10-26"})
        html = r.content.decode()
        self.assertEqual(Event.objects.count(), 0)
        self.assertIn("5건 넣기", html)
        self.assertRegex(html, r"읽지 못한 줄 <span class=\"num\">1</span>")
        self.assertIn("전일", html)
        self.assertIn("3학년 생활안전 체험교실 (실내화 지참)", html)
        self.assertIn("사일", html)  # 등록

    def test_preview_marks_what_is_already_in(self):
        self.post()
        html = self.as_(self.t41).post(f"{self.base}/schedule/paste/preview", {"paste": SAMPLE, "w": "2026-10-26"}).content.decode()
        self.assertIn("이미 있음", html)
        self.assertNotIn("건 넣기", html)

    def test_preview_reads_clipboard_html_with_merged_date(self):
        html = ("<table><tr><td>날짜</td><td>시간</td><td>내용</td><td>장소</td></tr>"
                "<tr><td rowspan=2>10/28(수)</td><td>1블록</td><td>첫째 행사</td><td>강당</td></tr>"
                "<tr><td>2블록</td><td>둘째 행사</td><td>&nbsp;</td></tr></table>")
        r = self.as_(self.t41).post(f"{self.base}/schedule/paste/preview", {"html": html, "w": "2026-10-26"})
        out = r.content.decode()
        self.assertIn('id="tsvOut"', out)
        self.assertIn("2건 넣기", out)
        self.assertEqual(out.count("10/28(수)"), 4)  # 입력 칸으로 보낼 TSV 2줄(병합 칸 복사) + 미리보기 2줄

    def test_title_escaped(self):
        text = HEAD + "10/28\t\t<script>alert(1)</script>\t<img src=x>\t"
        out = self.as_(self.t41).post(f"{self.base}/schedule/paste/preview", {"paste": text, "w": "2026-10-26"}).content.decode()
        self.assertNotIn("<script>alert", out)
        self.assertNotIn("<img src=x>", out)
        self.assertIn("&lt;script&gt;", out)
        r = self.post(text=text)
        self.assertEqual(Event.objects.in_space(self.hub).get().title, "<script>alert(1)</script>")
        page = self.as_(self.t41).get(f"{self.base}/schedule/print?view=week&d=2026-10-28").content.decode()
        self.assertNotIn("<script>alert", page)
        self.assertEqual(r.status_code, 303)

    def test_nothing_readable_is_an_error(self):
        r = self.post(text=HEAD + "어느 날\t\t못 읽음\t\t")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Event.objects.count(), 0)
        self.assertContains(r, "못 읽음", status_code=400)  # 칸에 그대로 남겨 고칠 수 있게

    def test_get_page_and_csrf(self):
        html = self.as_(self.t41).get(f"{self.base}/schedule/paste?w=2026-10-26").content.decode()
        self.assertIn('value="all" selected', html)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.t41.user)
        self.assertEqual(c.post(f"{self.base}/schedule/paste", {"paste": SAMPLE}).status_code, 403)
