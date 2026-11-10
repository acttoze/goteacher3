"""Q1 표시 요일 독립 검증(gt-qa). test_show_days.py·accounts/test_settings.py DaysTests에 없는 경계만.

- 중간 요일만 끈 주(수 끔): 칸·제목 기간·이어지는 기간 일정·끈 날에만 있는 일정 숨김·건수.
- '이번 주' 경계: 월요일만 켜면 화요일부터 다음 주.
- 지난/다음 주·달 이동 주소는 켠 요일과 무관하게 월요일·1일.
- 같은 계정이라도 공간이 다르면 따로(설정은 Member). 여러 값 붙인 'on'(56)은 버린다.
- 인쇄 화면은 바꾸지 않는다(리드 결정). 동학년 공간에서도 동작. 학교 공간 설정 > 학교 탭에 선생님 목록 없음, 나가기는 남음.
"""
from datetime import date, datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client

from spaces.models import Member, Space

from .models import ALL
from .test_schedule_channels_qa import NOW, Base

User = get_user_model()
WED = date(2026, 10, 28)
HX = {"HX-Request": "true"}


def board(html):
    return html.split('id="ev-')[0]


def set_days(m, days):
    Member.objects.filter(pk=m.pk).update(days_on=days)


class MiddleDayOffQa(Base):
    def setUp(self):
        super().setUp()
        set_days(self.t4, "0134")  # 수요일만 끔
        self.ev(self.boss, "수요 단독행사", ALL, d=WED)
        self.ev(self.boss, "주간 연수기간", ALL, d=date(2026, 10, 26), end=date(2026, 10, 30))

    def test_week_cells_and_spanning_event(self):
        r = self.as_(self.t4).get(f"{self.base}/schedule", {"mode": "week", "tab": "school"})
        html = r.content.decode()
        self.assertIn('style="--n:4"', html)
        self.assertNotIn("<b>수</b>", html)
        self.assertNotIn('data-date="2026-10-28"', html)
        self.assertIn("10월 26일 ~ 30일", html)
        self.assertNotIn("수요 단독행사", board(html))
        dates = {c["date"] for row in r.context["wrows"] for c in row["cells"] for x in c["items"]
                 if x.get("kind") == "ev" and x["e"].title == "주간 연수기간"}
        self.assertEqual(dates, {"2026-10-26", "2026-10-27", "2026-10-29", "2026-10-30"})

    def test_item_count_excludes_off_day(self):
        on = self.as_(self.t4).get(f"{self.base}/schedule", {"mode": "week", "tab": "school"}).context["n_items"]
        set_days(self.t4, "01234")
        full = self.as_(self.t4).get(f"{self.base}/schedule", {"mode": "week", "tab": "school"}).context["n_items"]
        self.assertEqual(full - on, 2)  # 수요일 칸: 단독행사 + 연수기간 하루

    def test_month_has_no_wednesday_column(self):
        r = self.as_(self.t4).get(f"{self.base}/schedule", {"mode": "month", "tab": "school"})
        self.assertEqual([w for w, _ in r.context["dow"]], list("월화목금"))
        self.assertTrue(all(len(wk["heads"]) == 4 for wk in r.context["mweeks"]))
        self.assertNotIn('data-date="2026-10-28"', r.content.decode())

    def test_other_member_unchanged(self):
        html = self.page(self.t5, "week")
        self.assertIn('style="--n:5"', html)
        self.assertIn("수요 단독행사", board(html))


class ThisWeekEdgeQa(Base):
    def test_monday_only_jumps_on_tuesday(self):
        set_days(self.t4, "0")
        tue = datetime(2026, 10, 27, 9, 0, tzinfo=NOW.tzinfo)
        with mock.patch("django.utils.timezone.now", return_value=tue):
            r = self.as_(self.t4).get(f"{self.base}/schedule", {"mode": "week", "tab": "school"})
        self.assertEqual(r.context["w"], "2026-11-02")
        self.assertIn("이번 주 일정", r.content.decode())
        self.assertIn('style="--n:1"', r.content.decode())

    def test_nav_links_stay_on_mondays_and_firsts(self):
        set_days(self.t4, "56")
        c = self.as_(self.t4)
        r = c.get(f"{self.base}/schedule", {"mode": "week", "tab": "school", "w": "2026-11-07"})
        self.assertEqual((r.context["prev_w"], r.context["w"], r.context["next_w"]), ("2026-10-26", "2026-11-02", "2026-11-09"))
        self.assertIn("11월 7일 ~ 8일", r.content.decode())
        r = c.get(f"{self.base}/schedule", {"mode": "month", "tab": "school", "w": "2026-11-15"})
        self.assertEqual((r.context["prev_w"], r.context["next_w"]), ("2026-10-01", "2026-12-01"))
        self.assertEqual([w for w, _ in r.context["dow"]], list("일토"))

    def test_day_view_not_filtered(self):
        """일 보기는 사양(주·월) 밖: 끈 요일도 그날 보기는 그대로."""
        set_days(self.t4, "01234")
        self.ev(self.boss, "토요 단독", ALL, d=date(2026, 10, 31))
        html = self.page(self.t4, "day", w="2026-10-31")
        self.assertIn("토요 단독", html)


class PrintAndPasteQa(Base):
    def test_print_unchanged_by_days(self):
        self.ev(self.boss, "토요 체험", ALL, d=date(2026, 10, 31))
        c = self.as_(self.t4)
        before = [c.get(f"{self.base}/schedule/print", {"view": v}).content for v in ("week", "month")]
        set_days(self.t4, "0123456")
        after = [c.get(f"{self.base}/schedule/print", {"view": v}).content for v in ("week", "month")]
        self.assertEqual(before, after)

    def test_paste_counts_off_days(self):
        set_days(self.t4, "012345")
        c = self.as_(self.t4)
        text = "날짜\t행사\n10/31\t토요 붙임\n11/1\t일요 붙임\n10/29\t목요 붙임"
        r = c.post(f"{self.base}/schedule/paste", {"paste": text, "w": "2026-10-26", "scope": ALL})
        self.assertEqual(r.status_code, 303, r.content[:300])
        self.assertIn("weekend=1", r["Location"])  # 일요일만 꺼져 있다
        html = c.get(r["Location"]).content.decode()
        self.assertIn("꺼 둔 요일 1건은 월중행사표에만", html)
        self.assertIn("토요 붙임", board(html))


class SpacesQa(Base):
    def setUp(self):
        super().setUp()
        self.grade = Space.objects.create(school=self.hub.school, kind="grade", grade=4, name="검증초 4학년")
        self.gm = Member.objects.create(space=self.grade, user=self.t4.user, name="사일", cls="4-1")

    def post_days(self, space, on):
        return self.as_(self.t4).post(f"/s/{space.token}/settings/days", {"on": on}, headers=HX)

    def test_same_user_other_space_unchanged(self):
        self.assertEqual(self.post_days(self.grade, ["5"]).status_code, 200)
        self.gm.refresh_from_db()
        self.t4.refresh_from_db()
        self.assertEqual((self.gm.days_on, self.t4.days_on), ("5", "01234"))

    def test_grade_space_week_follows_days(self):
        set_days(self.gm, "012345")
        html = self.as_(self.t4).get(f"/s/{self.grade.token}/schedule", {"mode": "week"}).content.decode()
        self.assertIn('style="--n:6"', html)
        self.assertIn("<b>토</b>", html)

    def test_multi_char_value_ignored(self):
        r = self.post_days(self.hub, ["56"])
        self.assertContains(r, "요일을 하나는 켜 두어야 해요")
        self.t4.refresh_from_db()
        self.assertEqual(self.t4.days_on, "01234")

    def test_other_school_member_cannot_post(self):
        other = Space.objects.create(school=self.hub.school, kind="grade", grade=5, name="검증초 5학년")
        r = self.as_(self.t4).post(f"/s/{other.token}/settings/days", {"on": ["0"]}, headers=HX)
        self.assertEqual(r.status_code, 404)

    def test_anonymous_cannot_post(self):
        r = Client().post(f"{self.base}/settings/days", {"on": ["0"]})
        self.assertIn(r.status_code, (302, 403, 404))
        self.t4.refresh_from_db()
        self.assertEqual(self.t4.days_on, "01234")

    def test_school_settings_tab_has_no_teacher_list(self):
        for m in (self.boss, self.t4):
            html = self.as_(m).get(f"{self.base}/settings?a=space").content.decode()
            self.assertNotIn("<h2>선생님</h2>", html)
            self.assertNotIn('class="mem"', html)
            self.assertIn("에서 나가기</h2>", html)
            self.assertNotIn("티로드", html)

    def test_prefs_tab_first_but_default_is_me(self):
        html = self.as_(self.t4).get(f"{self.base}/settings").content.decode()
        tabs = html[html.index('aria-label="설정 영역"'):]
        self.assertTrue(tabs.split("<a ")[1].startswith('href="?a=prefs"'))
        self.assertIn('href="?a=me" aria-current="page"', tabs)
