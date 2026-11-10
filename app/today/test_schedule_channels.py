"""QC 일정 탭(2026-11-06) — 학교·그룹·개인 탭. 어제(11-05) 채널 구조를 사용자 결정으로 바꿨다.

불변식
- 탭: 학교 · 내 소속 이름 · 개인. 소속이 없으면 그룹 탭 없음, 동학년 공간은 탭 없음. ?tab=, 마지막 탭은 쿠키.
  탭·모드·기간을 바꿔도, 칸에 쓰고 나서 판을 다시 그려도 탭·모드·날짜가 그대로.
- 학교 탭: 일간·주간·월간 모두 채널 줄 학교·취합·복무. 학교 줄은 scope=all + NEIS만. 칸 안은 종일 → b1 → b2 → b3 → pm.
- 그룹 탭(scope=내 소속)·개인 탭(scope=me, 내가 쓴 것): 일간·주간은 종일·1~3블록·방과후 줄, 월간은 달력 칸에 시간순. 필터 없음.
- 칸이 곧 볼 사람: 학교 all, 그룹 내 소속(다른 그룹은 400), 개인 me. 안내 문구는 범위를 앞에.
- 취합 줄은 이 공간의 모든 마감을 모두에게(제목·시각·만든 사람). 링크는 만든 사람·내 줄·관리자만, 받는 링크는 대상에게만.
- 복무 줄은 누구나 '이름 유형 [시간]'. 명부 이름·KINDS만, 시각 낱말만 저장. 못 읽으면 원문 없이 짧은 안내. 지우기는 넣은 사람·관리자·복무 입력자.
- 남의 개인 일정은 겹침 판정에서도 뺀다. 같은 블록 이름은 제목에 있으면 카드 아래에 다시 쓰지 않는다.
- 격리: 다른 공간은 404. 쿼리 수는 건수와 무관.
"""
import importlib
import re
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from collect.models import Collection, Field, Row
from spaces.models import Attendance, Member, School, Space

from .models import ALL, ME, Event

User = get_user_model()
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 29, 9, 5, tzinfo=KST)  # 목요일
TODAY = NOW.date()
SCHED = [{"ymd": "20261029", "name": "독서주간 골든벨"}]
HX = {"HX-Request": "true"}
BLOCK_ROWS = ["종일", "1블록", "2블록", "3블록", "방과후"]


def grid_of(html):
    """판 격자만(대화상자·아래 목록 앞까지)."""
    i = html.index('class="wgrid') if 'class="wgrid' in html else html.index('class="mgrid')
    return html[i:html.index("</section>", i)].split('class="scrim"')[0]


def rows_of(html):
    """{줄 이름: 그 줄 HTML}, 순서 유지(일간·주간)."""
    parts = re.split(r'<div class="lbl"[^>]*>', grid_of(html))[1:]
    return {re.match(r"[^<]*", p).group(0).strip(): p for p in parts}


def month_labels(html):
    return re.findall(r'<div class="ml"[^>]*>([^<]+)</div>', grid_of(html))


def card(html, e):
    """일정 카드 하나(button)."""
    i = html.index(f'data-open="#ev-{e.key}"')
    return html[i:html.index("</button>", i)]


class Base(TestCase):
    def setUp(self):
        p = [mock.patch("django.utils.timezone.now", return_value=NOW),
             mock.patch("spaces.neis.meals", return_value=[]),
             mock.patch("spaces.neis.schedule", return_value=SCHED)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7535000", name="채널초", plan="school", paid_until=date(2099, 1, 1))
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="채널초")
        self.boss = self.member("부장", "교무실", "office", is_manager=True)
        self.t4 = self.member("사일", "4-1", "g4")
        self.t5 = self.member("오일", "5-1", "g5")
        self.base = f"/s/{self.hub.token}"

    def member(self, name, cls, group, **kw):
        u = User.objects.create(username=f"google:{name}", first_name=name)
        return Member.objects.create(space=self.hub, user=u, name=name, cls=cls, group=group, **kw)

    def as_(self, m):
        c = Client()
        c.force_login(m.user)
        return c

    def ev(self, by, title, scope=ALL, slot="", d=TODAY, **kw):
        return Event.objects.create(space=self.hub, by=by, title=title, scope=scope, slot=slot, slot_end=kw.pop("slot_end", slot), date=d, **kw)

    def due(self, by, title, hour=11, d=TODAY, row_for=None, aud=None):
        c = Collection.objects.create(space=self.hub, created_by=by, title=title, source=Collection.Source.SHEET, unit_head=["학년반"],
                                      due=datetime(d.year, d.month, d.day, hour, 0, tzinfo=KST), audience={"mode": "all"} if aud is None else aud)
        Field.objects.create(collection=c, pos=0, name="칸", kind="number")
        m = row_for or by
        Row.objects.create(collection=c, pos=0, unit=m.cls, who=m.name, member=m)
        return c

    def duty(self, m, d=TODAY, by=None):
        return Attendance.objects.create(space=self.hub, member=m, date=d, name=m.name, kind="출장", by=by or m)

    def page(self, m, mode, tab="school", **kw):
        return self.as_(m).get(f"{self.base}/schedule", {"mode": mode, "tab": tab, **kw}).content.decode()

    def add(self, m, line, slot="", scope=None, mode="week", d="2026-10-27", tab="school"):
        data = {"view": "week", "mode": mode, "w": "2026-10-26", "tab": tab, "line": line, "date": d, "slot": slot}
        if scope:
            data["scope"] = scope
        return self.as_(m).post(f"{self.base}/today/events", data, headers=HX)

    def add_duty(self, m, line, d=TODAY.isoformat(), **kw):
        data = {"view": "week", "mode": "week", "w": "2026-10-26", "tab": "school", "line": line, "date": d, "slot": "", "scope": "duty", **kw}
        return self.as_(m).post(f"{self.base}/today/duty", data, headers=HX)


class WordingTests(Base):
    def test_no_old_wording_anywhere(self):
        e = self.ev(self.t4, "문구 확인")
        c = self.as_(self.t4)
        for path in ("/schedule?mode=day", "/schedule?mode=week", "/schedule?mode=month", "/today",
                     "/schedule/print?view=week&d=2026-10-26", "/schedule/print?view=month&d=2026-10-01"):
            self.assertNotContains(c.get(self.base + path), "시간 미정", msg_prefix=path)
        r = c.post(f"{self.base}/today/parse", {"line": "내일 회의"})
        self.assertNotContains(r, "시간 미정")
        self.assertContains(r, "종일")
        r = c.post(f"{self.base}/today/events", {"line": "내일 회의"}, headers=HX)
        self.assertContains(r, "종일 「회의」 올렸어요")
        self.assertIn('<option value="">종일</option>', self.page(self.t4, "week"))
        self.assertIn(f'id="ev-{e.key}"', self.page(self.t4, "week"))

    def test_no_jeonil_on_screens(self):
        c = self.as_(self.t4)
        self.ev(self.t4, "문구 확인", d=date(2026, 10, 27))
        for path in ("/schedule/paste?w=2026-10-26", "/schedule/print?view=week&d=2026-10-26", "/schedule/print?view=month&d=2026-10-01"):
            self.assertNotContains(c.get(self.base + path), "전일", msg_prefix=path)

    def test_print_all_day_row_has_one_word(self):
        html = self.as_(self.t4).get(f"{self.base}/schedule/print?view=week&d=2026-10-26").content.decode()
        self.assertIn('scope="row">종일<span></span>', html)

    def test_paste_preview_says_all_day(self):
        r = self.as_(self.t4).post(f"{self.base}/schedule/paste/preview", {"w": "2026-10-26", "paste": "10/27\t\t회의"}, headers=HX)
        self.assertNotContains(r, "전일")
        self.assertContains(r, "종일")

    def test_edit_from_full_day_block_range_to_all_day_still_saves(self):
        """'1~3블록'의 표기도 '종일'이다. 글자가 같아도 블록을 지우면 저장돼야 한다."""
        e = self.ev(self.t4, "운동회", slot="b1", slot_end="b3")
        r = self.as_(self.t4).post(f"{self.base}/today/e/{e.key}", {"view": "week", "date": TODAY.isoformat(), "slot": "", "place": "", "scope": ALL},
                                   headers=HX)
        self.assertNotContains(r, "바뀐 것이 없어요")
        e.refresh_from_db()
        self.assertEqual((e.slot, e.slot_end), ("", ""))

    def test_edit_with_nothing_changed_is_still_a_noop(self):
        for slot, end in (("", ""), ("b1", "b3"), ("b2", "b2")):
            e = self.ev(self.t4, f"그대로 {slot}{end}", slot=slot, slot_end=end)
            r = self.as_(self.t4).post(f"{self.base}/today/e/{e.key}", {"view": "week", "date": TODAY.isoformat(), "slot": slot, "place": "", "scope": ALL},
                                       headers=HX)
            self.assertContains(r, "바뀐 것이 없어요", msg_prefix=f"{slot}{end}")

    def test_migration_rewrites_stored_old_wording(self):
        old = self.ev(self.t4, "옛 바뀜", changed_from=[["시간", "시간 미정", "2블록"], ["날짜", "10/1(목)", "10/2(금)"]])
        other = self.ev(self.t4, "그대로", changed_from=[["장소", "강당", "과학실"]])
        before = Event.objects.get(pk=old.pk).updated_at
        importlib.import_module("today.migrations.0003_changed_from_all_day").forward(apps, None)
        old.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(old.changed_from, [["시간", "종일", "2블록"], ["날짜", "10/1(목)", "10/2(금)"]])
        self.assertEqual(other.changed_from, [["장소", "강당", "과학실"]])
        self.assertEqual(old.updated_at, before)  # 바뀐 것 순서는 그대로


class TabTests(Base):
    def tab_links(self, html):
        i = html.index('class="tabs sched-tabs"')
        nav = html[i:html.index("</nav>", i)]
        return re.findall(r'<a href="[^"]*\?tab=(\w+)&amp;mode=(\w+)&amp;w=([\d-]+)"(?: aria-current="page")?>([^<]+)</a>', nav)

    def test_tabs_are_school_my_group_personal(self):
        html = self.page(self.t4, "week")
        self.assertEqual([(t, label) for t, _, _, label in self.tab_links(html)], [("school", "학교"), ("group", "4학년"), ("me", "개인")])
        self.assertIn('aria-current="page">학교</a>', html)
        self.assertEqual([label for *_, label in self.tab_links(self.page(self.boss, "week"))], ["학교", "교무실", "개인"])

    def test_no_group_tab_without_group(self):
        m = self.member("무소속", "지원", "")
        html = self.page(m, "week", tab="group")
        self.assertEqual([t for t, *_ in self.tab_links(html)], ["school", "me"])
        self.assertIn('aria-current="page">학교</a>', html)  # 없는 탭을 주소로 부르면 학교

    def test_tab_links_keep_mode_and_date(self):
        for mode, w, want in (("day", "2026-11-03", "2026-11-03"), ("week", "2026-11-09", "2026-11-09"), ("month", "2026-12-01", "2026-12-01")):
            html = self.page(self.t4, mode, tab="me", w=w)
            for _, m, ww, _ in self.tab_links(html):
                self.assertEqual((m, ww), (mode, want), mode)

    def test_mode_and_period_forms_keep_tab(self):
        html = self.page(self.t4, "week", tab="group")
        title = html[html.index('class="title"'):html.index('id="cellTpl"')]
        self.assertEqual(title.count('<input type="hidden" name="tab" value="group">'), 2)

    def test_cookie_remembers_last_tab(self):
        c = self.as_(self.t4)
        r = c.get(f"{self.base}/schedule", {"tab": "me", "mode": "month"})
        self.assertEqual(r.cookies["gt_sched_tab"].value, "me")
        html = c.get(f"{self.base}/schedule").content.decode()
        self.assertIn('data-tab="me"', html)
        self.assertIn('data-mode="month"', html)
        self.assertIn('class="mgrid plain"', html)

    def test_redraw_after_write_keeps_tab_mode_date(self):
        for tab, scope in (("group", "g4"), ("me", ME), ("school", ALL)):
            for mode in ("day", "week", "month"):
                r = self.add(self.t4, f"{tab}{mode} 일", scope=scope, mode=mode, tab=tab).content.decode()
                self.assertIn(f'data-mode="{mode}"', r)
                self.assertIn(f'data-tab="{tab}"', r)
                self.assertIn(f"{tab}{mode} 일", r)

    def test_dialog_forms_carry_tab(self):
        e = self.ev(self.t4, "사학년 일", "g4")
        html = self.page(self.t4, "week", tab="group")
        dlg = html[html.index(f'id="ev-{e.key}"'):]
        self.assertIn('<input type="hidden" name="tab" value="group">', dlg[:dlg.index("</form>")])

    def test_without_js_goes_back_to_same_tab(self):
        r = self.as_(self.t4).post(f"{self.base}/today/events", {"view": "week", "mode": "week", "w": "2026-10-26", "tab": "me",
                                                                 "line": "폼으로", "date": "2026-10-27", "slot": "", "scope": ME})
        self.assertEqual(r.status_code, 303)
        self.assertIn("tab=me", r["Location"])


class SchoolTabTests(Base):
    def fill(self):
        self.ev(self.t4, "모두 일", ALL, "b2")
        self.ev(self.t4, "사학년 일", "g4", "b2")
        self.ev(self.t4, "혼자 일", ME, "b2")
        self.duty(self.t5)
        self.due(self.t4, "낼 표")

    def test_channel_rows_every_mode(self):
        self.fill()
        for mode in ("day", "week"):
            rows = rows_of(self.page(self.t4, mode))
            self.assertEqual(list(rows), ["학교", "취합", "복무"], mode)
            self.assertIn("모두 일", rows["학교"])
            self.assertIn("독서주간 골든벨", rows["학교"])
            self.assertIn("낼 표", rows["취합"])
            self.assertIn("오일 출장", rows["복무"])
        self.assertIn('wgrid one', self.page(self.t4, "day"))
        self.assertEqual(month_labels(self.page(self.t4, "month")), ["학교", "취합", "복무"] * 5)

    def test_only_school_wide_events(self):
        self.fill()
        for mode in ("day", "week", "month"):
            html = self.page(self.t4, mode)
            self.assertIn("모두 일", html, mode)
            self.assertNotIn("사학년 일", html, mode)
            self.assertNotIn("혼자 일", html, mode)

    def test_order_inside_cell(self):
        for title, slot in (("방과후 일", "pm"), ("셋째 일", "b3"), ("첫째 일", "b1"), ("둘째 일", "b2"), ("종일 일", "")):
            self.ev(self.t4, title, ALL, slot)
        for mode in ("day", "week"):
            row = rows_of(self.page(self.t4, mode))["학교"]
            got = [row.index(t) for t in ("종일 일", "첫째 일", "둘째 일", "셋째 일", "방과후 일")]
            self.assertEqual(got, sorted(got), mode)

    def test_block_name_on_card(self):
        e = self.ev(self.t4, "수업 공개", ALL, "b2", place="과학실")
        for mode in ("day", "week", "month"):
            self.assertIn("2블록", card(self.page(self.t4, mode), e), mode)

    def test_add_cells_school_and_duty_rows_only(self):
        html = self.page(self.t4, "week")
        rows = rows_of(html)
        self.assertEqual(rows["학교"].count('data-slot="" data-scope="all"'), 5)
        self.assertEqual(rows["복무"].count('data-scope="duty"'), 5)
        self.assertEqual(rows["복무"].count(f'data-post="/s/{self.hub.token}/today/duty"'), 5)
        self.assertNotIn("data-cell", rows["취합"])
        self.assertEqual(html.count('<button type="button" class="cell-add"'), 10)
        self.assertEqual(self.page(self.t4, "day").count('<button type="button" class="cell-add"'), 2)
        self.assertEqual(grid_of(self.page(self.t4, "month")).count('<button type="button" class="cell-add"'), 50)  # 5주 × 월~금(Q1) × 학교·복무

    def test_collect_cells_link_to_new_collect_with_day_and_all(self):
        """QC 2026-11-06: 취합 줄 칸에도 ＋. 입력 카드 대신 그 날 마감·전 교직원으로 취합 만들기."""
        week = self.page(self.t4, "week")
        row = rows_of(week)["취합"]
        days = re.findall(r'<a class="cell-add" href="/s/[^/]+/c/new\?aud=all&amp;due_d=([\d-]+)"', row)
        self.assertEqual(len(days), 5)
        self.assertIn(TODAY.isoformat(), days)
        self.assertIn('data-go="', row)
        self.assertEqual(rows_of(self.page(self.t4, "day"))["취합"].count('<a class="cell-add"'), 1)
        self.assertEqual(grid_of(self.page(self.t4, "month")).count('<a class="cell-add"'), 25)
        new = self.as_(self.t4).get(f"{self.base}/c/new", {"aud": "all", "due_d": days[0]}).content.decode()
        self.assertIn(f'name="due_d" value="{days[0]}"', new)
        self.assertNotIn('name="due_t"', new)  # 마감은 날짜만
        self.assertRegex(new, r'value="all" checked><b>전 교직원')

    def test_new_collect_from_schedule_saves_due(self):
        r = self.as_(self.t4).post(f"{self.base}/c/new", {"src": "sheet", "title": "일정에서", "col_name_0": "참가(명)",
                                                          "aud": "all", "due_d": "2026-10-30"})
        self.assertEqual(r.status_code, 302, r.content[:300])
        c = Collection.objects.get(title="일정에서")
        self.assertEqual(c.due, datetime(2026, 10, 30, 23, 59, tzinfo=KST))  # 날짜만: 그날 끝
        self.assertIn("일정에서<", rows_of(self.page(self.t4, "week"))["취합"])
        r = self.as_(self.t4).post(f"{self.base}/c/new", {"src": "sheet", "title": "마감 비움", "col_name_0": "참가(명)",
                                                          "aud": "all", "due_d": ""})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(Collection.objects.get(title="마감 비움").due)
        plain = self.as_(self.t4).get(f"{self.base}/c/new").content.decode()
        self.assertIn('name="due_d" value=""', plain)  # 그냥 들어오면 마감일 칸은 비어 있다(기본 마감 없음)
        self.assertNotIn('type="time"', plain)


class CollectRowTests(Base):
    """취합 줄: 모두에게 제목·시각·만든 사람. 링크는 열 수 있는 사람에게만."""

    def setUp(self):
        super().setUp()
        self.c = self.due(self.t5, "오학년 조사", hour=11)  # 오일이 만들고 오일 줄만
        self.r = self.c.rows.get()

    def due_card(self, html):
        row = rows_of(html)["취합"]
        i = row.index("오학년 조사")
        return row[row.rindex("<", 0, row.rindex('class="ev due"', 0, i)):i + 40]

    def test_everyone_sees_title_time_and_maker(self):
        for m in (self.t4, self.t5, self.boss):
            row = rows_of(self.page(m, "week"))["취합"]
            self.assertIn("오학년 조사<", row, m.name)
            self.assertIn("오일", row, m.name)

    def test_link_only_for_those_who_can_open(self):
        self.assertIn(f'href="/s/{self.hub.token}/c/{self.c.key}/"', self.page(self.t5, "week"))  # 만든 사람
        self.assertIn(f'href="/s/{self.hub.token}/c/{self.c.key}/"', self.page(self.boss, "week"))  # 관리자는 관리 화면
        html = self.page(self.t4, "week")  # 대상 아님
        self.assertIn('<div class="ev due" data-ch="collect"><span class="t"><span class="k">마감</span> 오학년 조사', html)
        for s in (self.c.key, self.c.link_token, self.r.key):
            self.assertNotIn(s, html)
        boss = self.page(self.boss, "week")
        self.assertNotIn(self.c.link_token, boss)  # 관리자에게도 받는 링크는 싣지 않는다

    def test_my_row_gets_my_row_link(self):
        c = self.due(self.t5, "사일도 냄", row_for=self.t4)
        html = self.page(self.t4, "week")
        self.assertIn(f"/c/{c.link_token}/u/{c.rows.get().key}", html)

    def test_due_list_is_still_mine_only(self):
        html = self.page(self.t4, "week")
        self.assertNotIn("오학년 조사", html[html.index('id="due"'):])
        self.assertIn("오학년 조사", self.page(self.t5, "week").split('id="due"')[1])

    def test_not_in_group_or_personal_tab(self):
        for tab in ("group", "me"):
            self.assertNotIn("오학년 조사", grid_of(self.page(self.t5, "week", tab=tab)), tab)


class CollectScopeTests(Base):
    """QC 일정 취합 범위(2026-11-06): 전 교직원 → 학교 탭, 소속 고르기·소속별 1칸 → 고른 소속의 그룹 탭, 그 밖 → 마감 목록에만."""

    def setUp(self):
        super().setUp()
        self.all = self.due(self.boss, "전체조사", hour=9)
        self.g4 = self.due(self.boss, "사학년조사", hour=15, aud={"mode": "groups", "groups": ["g4"], "hr": False})
        self.pg = self.due(self.t5, "소속칸조사", hour=10, aud={"mode": "pergroup", "groups": ["g4", "g5"]})
        self.dept = self.due(self.boss, "부서조사", aud={"mode": "dept", "depts": ["교무부"]}, row_for=self.t4)
        self.people = self.due(self.boss, "사람조사", aud={"mode": "people", "people": [self.t4.key]}, row_for=self.t4)
        self.empty = self.due(self.boss, "옛조사", aud={}, row_for=self.t4)
        self.odd = self.due(self.boss, "이상조사", aud={"mode": "x", "groups": ["g4"]}, row_for=self.t4)
        self.bad = self.due(self.boss, "깨진조사", aud={"mode": "groups", "groups": "g4"}, row_for=self.t4)

    def grids(self, m, tab):
        return {mode: grid_of(self.page(m, mode, tab=tab)) for mode in ("day", "week", "month")}

    def test_school_tab_only_all(self):
        for m in (self.t4, self.t5, self.boss):
            for mode, g in self.grids(m, "school").items():
                self.assertIn("전체조사", g, (m.name, mode))
                for t in ("사학년조사", "소속칸조사", "부서조사", "사람조사", "옛조사", "이상조사", "깨진조사"):
                    self.assertNotIn(t, g, (m.name, mode, t))

    def test_group_tab_only_chosen_groups(self):
        for mode, g in self.grids(self.t4, "group").items():
            self.assertIn("사학년조사", g, mode)
            self.assertIn("소속칸조사", g, mode)
            for t in ("전체조사", "부서조사", "사람조사", "옛조사", "이상조사", "깨진조사"):
                self.assertNotIn(t, g, (mode, t))
        g5 = self.grids(self.t5, "group")
        for mode, g in g5.items():
            self.assertIn("소속칸조사", g, mode)
            self.assertNotIn("사학년조사", g, mode)

    def test_other_group_member_and_manager_never_get_title(self):
        """다른 소속(오일)·관리자(교무실)에게 4학년 취합 제목은 응답 어디에도 없다(만든 사람 아님)."""
        g4 = self.due(self.t4, "사학년만비밀", aud={"mode": "groups", "groups": ["g4"], "hr": False})
        for m in (self.t5, self.boss):
            for tab in ("school", "group", "me"):
                for mode in ("day", "week", "month"):
                    html = self.page(m, mode, tab=tab)
                    for s_ in ("사학년만비밀", g4.key, g4.link_token):
                        self.assertNotIn(s_, html, (m.name, tab, mode))
            r = self.as_(m).post(f"{self.base}/today/events", {"view": "week", "mode": "week", "w": "2026-10-26", "tab": "group",
                                                              "line": "다시 그리기", "date": "2026-10-27", "slot": "", "scope": m.group}, headers=HX)
            self.assertNotIn("사학년만비밀", r.content.decode(), m.name)

    def test_personal_tab_has_none(self):
        for mode, g in self.grids(self.t4, "me").items():
            for t in ("전체조사", "사학년조사", "소속칸조사", "부서조사"):
                self.assertNotIn(t, g, (mode, t))

    def test_other_modes_only_in_due_list(self):
        html = self.page(self.t4, "week")
        due = html.split('id="due"')[1]
        for t in ("부서조사", "사람조사", "옛조사"):
            self.assertIn(t, due, t)  # 내 줄이 있으니 아래 마감 목록에는 그대로
        self.assertNotIn("사학년조사", due)  # 마감 목록 규칙은 그대로(내가 만든 것·내 줄만)

    def test_group_tab_collect_row_on_top_without_add(self):
        for mode in ("day", "week"):
            rows = rows_of(self.page(self.t4, mode, tab="group"))
            self.assertEqual(list(rows), ["취합", *BLOCK_ROWS], mode)
            self.assertIn("사학년조사", rows["취합"])
            self.assertNotIn("data-cell", rows["취합"])
            self.assertNotIn("사학년조사", rows["방과후"])

    def test_month_collect_card_first_in_cell(self):
        self.ev(self.t4, "사학년 종일", "g4")
        g = grid_of(self.page(self.t4, "month", tab="group"))
        self.assertLess(g.index("사학년조사"), g.index("사학년 종일"))
        self.assertLess(g.index("소속칸조사"), g.index("사학년 종일"))

    def test_links_in_group_tab(self):
        mine = self.due(self.t4, "내가만든사학년", aud={"mode": "groups", "groups": ["g4"], "hr": False})
        row = self.due(self.boss, "내줄사학년", aud={"mode": "groups", "groups": ["g4"], "hr": False}, row_for=self.t4)
        html = self.page(self.t4, "week", tab="group")
        self.assertIn(f'href="/s/{self.hub.token}/c/{mine.key}/"', html)
        self.assertIn(f"/c/{row.link_token}/u/{row.rows.get().key}", html)
        self.assertNotIn(self.g4.link_token, html)  # 내 줄이 없는 4학년 취합: 링크 없는 카드
        self.assertNotIn(self.g4.key, html)
        self.assertRegex(html, r'<div class="ev due"><span class="t"><span class="k">마감</span> 사학년조사')
        boss4 = self.member("사부장", "4-부", "g4", is_manager=True)  # 4학년 관리자: 관리 화면
        bhtml = self.page(boss4, "week", tab="group")
        self.assertIn(f'href="/s/{self.hub.token}/c/{self.g4.key}/"', bhtml)
        self.assertNotIn(self.g4.link_token, bhtml)

    def test_grade_space_unchanged(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7537100", name="동학년초2")
        g = Space.objects.create(school=s, grade=4, name="4학년")
        u = User.objects.create(username="google:동")
        m = Member.objects.create(space=g, user=u, name="동", cls="4-1")
        Collection.objects.create(space=g, created_by=m, title="동학년표", source=Collection.Source.SHEET, unit_head=["학년반"],
                                  due=datetime(2026, 10, 29, 11, 0, tzinfo=KST))
        c = Client()
        c.force_login(u)
        self.assertIn("동학년표", rows_of(c.get(f"/s/{g.token}/schedule", {"mode": "week"}).content.decode())["취합"])


class AudienceNoteTests(Base):
    def test_notes_on_each_mode(self):
        html = self.as_(self.t4).get(f"{self.base}/c/new").content.decode()
        box = html[html.index('id="audBox"'):]
        notes = re.findall(r'value="(\w+)"[^>]*><b>[^<]+</b><span>[^<]+</span><em class="anote">([^<]+)</em>', box)
        self.assertEqual(notes, [("all", "학교 일정에 보여요"), ("pergroup", "고른 소속의 일정에 보여요"),
                                 ("people", "일정에는 안 보이고 받는 사람의 마감에만 떠요")])  # 선택지 셋(QC 취합 받는 사람)
        self.assertIn(".amode .anote { margin-left: auto; font-style: normal; font-size: 16px;", html)


class BlockTabTests(Base):
    """그룹·개인 탭: 블록 줄, 월간은 달력 칸, 필터 없음."""

    def test_rows_and_contents(self):
        self.ev(self.t4, "모두 일", ALL, "b1")
        self.ev(self.t4, "사학년 둘째", "g4", "b2")
        self.ev(self.t4, "사학년 종일", "g4")
        self.ev(self.t5, "오학년 일", "g5", "b2")
        self.ev(self.t4, "사일 혼자", ME, "pm")
        self.ev(self.t5, "오일 혼자", ME, "b1")
        self.duty(self.t4)
        for mode in ("day", "week"):
            rows = rows_of(self.page(self.t4, mode, tab="group"))
            self.assertEqual(list(rows), ["취합", *BLOCK_ROWS], mode)  # 그룹 탭은 취합 줄이 맨 위(QC 일정 취합 범위)
            self.assertIn("사학년 둘째", rows["2블록"])
            self.assertIn("사학년 종일", rows["종일"])
            whole = "".join(rows.values())
            for w in ("모두 일", "오학년 일", "사일 혼자", "독서주간", "사일 출장"):
                self.assertNotIn(w, whole, (mode, w))
            rows = rows_of(self.page(self.t4, mode, tab="me"))
            self.assertEqual(list(rows), BLOCK_ROWS, mode)
            self.assertIn("사일 혼자", rows["방과후"])
            whole = "".join(rows.values())
            for w in ("모두 일", "사학년 둘째", "오일 혼자"):
                self.assertNotIn(w, whole, (mode, w))

    def test_personal_tab_only_my_own_even_for_manager(self):
        self.ev(self.t4, "사일 혼자", ME, "b1")
        for mode in ("day", "week", "month"):
            self.assertIn("사일 혼자", self.page(self.t4, mode, tab="me"), mode)
            for tab in ("school", "group", "me"):
                self.assertNotIn("사일 혼자", self.page(self.boss, mode, tab=tab), (mode, tab))
                self.assertNotIn("사일 혼자", self.page(self.t5, mode, tab=tab), (mode, tab))

    def test_add_cells_and_scope(self):
        d = TODAY.isoformat()
        for tab, scope in (("group", "g4"), ("me", ME)):
            html = self.page(self.t4, "day", tab=tab)
            self.assertEqual(html.count('<button type="button" class="cell-add"'), 5, tab)
            for slot in ("", "b1", "b2", "b3", "pm"):
                self.assertIn(f'data-cell data-date="{d}" data-slot="{slot}" data-scope="{scope}"', html)
            self.assertEqual(self.page(self.t4, "week", tab=tab).count('<button type="button" class="cell-add"'), 25, tab)

    def test_group_tab_collect_cells_link_with_my_group(self):
        row = rows_of(self.page(self.t4, "week", tab="group"))["취합"]
        self.assertEqual(row.count('/c/new?aud=pergroup&amp;aud_pgroups=g4&amp;due_d='), 10)  # 칸 5 × (data-go, 링크)
        new = self.as_(self.t4).get(f"{self.base}/c/new", {"aud": "pergroup", "aud_pgroups": "g4", "due_d": TODAY.isoformat()}).content.decode()
        self.assertRegex(new, r'value="pergroup" checked><b>소속')
        self.assertRegex(new, r'name="aud_pgroups" value="g4" checked>')

    def test_block_row_beats_parsed_block_and_all_day_row_keeps_it(self):
        self.add(self.t4, "2블록 칸이 이김", slot="pm", scope="g4", mode="day", tab="group")
        self.assertEqual(Event.objects.get(title="2블록 칸이 이김").slot, "pm")
        self.add(self.t4, "2블록 안전교육", slot="", scope="g4", mode="day", tab="group")
        e = Event.objects.get(title="2블록 안전교육")
        self.assertEqual((e.slot, e.scope, e.date), ("b2", "g4", date(2026, 10, 27)))

    def test_month_is_plain_calendar_in_time_order(self):
        self.ev(self.t4, "방과후 일", "g4", "pm")
        self.ev(self.t4, "첫째 일", "g4", "b1")
        self.ev(self.t4, "종일 일", "g4")
        html = self.page(self.t4, "month", tab="group")
        grid = grid_of(html)
        self.assertIn('class="mgrid plain"', html)
        self.assertEqual(month_labels(html), [])
        self.assertEqual("".join(re.findall(r'<div class="mh[^"]*">(.)</div>', grid)), "월화수목금")  # Q1 기본 표시 요일
        self.assertEqual(grid.count('class="mdh'), 25)
        self.assertEqual(grid.count('data-scope="g4"'), 25)
        self.assertLess(grid.index("종일 일"), grid.index("첫째 일"))
        self.assertLess(grid.index("첫째 일"), grid.index("방과후 일"))

    def test_no_filter_and_no_neis_note(self):
        for tab in ("group", "me"):
            for mode in ("day", "week", "month"):
                html = self.page(self.t4, mode, tab=tab)
                self.assertNotIn('class="btn sec flt-btn"', html, (tab, mode))
                self.assertNotIn("data-ch=", grid_of(html), (tab, mode))
                self.assertNotIn("학사일정이 없어요", html, (tab, mode))


class CellAddTests(Base):
    def test_template_has_hidden_scope_tab_and_no_select(self):
        html = self.page(self.t4, "week")
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertIn('<input type="hidden" name="scope">', tpl)
        self.assertIn('<input type="hidden" name="tab">', tpl)
        self.assertNotIn("<select", tpl)

    def test_scope_comes_from_cell_and_group_is_mine_only(self):
        self.add(self.t4, "모두 볼 일", scope=ALL)
        self.add(self.t4, "혼자 볼 일", scope=ME, tab="me")
        self.add(self.t4, "사학년 볼 일", scope="g4", tab="group")
        self.assertEqual(Event.objects.get(title="모두 볼 일").scope, ALL)
        self.assertEqual(Event.objects.get(title="혼자 볼 일").scope, ME)
        self.assertEqual(Event.objects.get(title="사학년 볼 일").scope, "g4")
        for kw in ({}, {"scope": "<x>"}, {"scope": ""}, {"scope": "g9"}, {"scope": "g5"}, {"scope": "duty"}):
            self.assertEqual(self.add(self.t4, "거절", **kw).status_code, 400, kw)
        self.assertFalse(Event.objects.filter(title="거절").exists())

    def test_line_input_keeps_scope_fallback(self):
        c = self.as_(self.t4)
        c.post(f"{self.base}/today/events", {"line": "오늘 값 없음"}, headers=HX)
        c.post(f"{self.base}/today/events", {"line": "오늘 값 틀림", "scope": "<x>"}, headers=HX)
        self.assertEqual(Event.objects.get(title="값 없음").scope, "g4")
        self.assertEqual(Event.objects.get(title="값 틀림").scope, "g4")

    def test_placeholder_names_scope_first(self):
        html = self.page(self.t4, "week")
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertIn('data-ph-all="학교 전체 · 제목 쓰고 Enter"', tpl)
        self.assertIn('data-ph-g4="4학년 · 제목 쓰고 Enter"', tpl)
        self.assertIn('data-ph-me="나만 보기 · 제목 쓰고 Enter"', tpl)
        self.assertIn('data-ph-duty="예: 김민수 출장 13:00~16:30"', tpl)

    def test_toast_names_audience(self):
        for scope, tab, words in ((ME, "me", "나만 보여요"), (ALL, "school", "학교 전체에 보여요"), ("g4", "group", "4학년에 보여요")):
            self.assertContains(self.add(self.t4, f"칸 {scope}", scope=scope, tab=tab), words)
            r = self.as_(self.t4).post(f"{self.base}/today/events", {"line": f"줄 {scope}", "scope": scope}, headers=HX)
            self.assertIn("올렸어요 · " + words, r.content.decode())

    def test_add_button_fills_rest_of_cell(self):
        html = self.page(self.t4, "week")
        css = html[html.index("<style>"):html.index("</style>")]
        self.assertIn("[data-cell] { display: flex; flex-direction: column; }", css)
        self.assertRegex(css, r"\.cell-add \{[^}]*flex: 1 0 auto;[^}]*min-height: 40px;")
        self.assertRegex(css, r"\.mc \.cell-add \{ min-height: 22px;")
        self.assertNotRegex(css, r"\.cell-add \{[^}]*[^-]height: \d")


class DialogScopeTests(Base):
    def select(self, html, e):
        dlg = html[html.index(f'id="ev-{e.key}"'):]
        i = dlg.index('name="scope"')
        return dlg[i:dlg.index("</select>", i)]

    def test_three_choices(self):
        e = self.ev(self.t4, "사학년 일", "g4")
        sel = self.select(self.page(self.t4, "week", tab="group"), e)
        self.assertEqual(re.findall(r'<option value="(\w+)"[^>]*>([^<]+)</option>', sel), [("all", "학교 전체"), ("g4", "4학년"), ("me", "개인(나만)")])
        self.assertIn('<option value="g4" selected>4학년</option>', sel)
        self.assertNotIn("우리 소속", sel)

    def test_other_group_value_is_kept(self):
        """지금 값이 내 그룹 밖이면(소속을 옮긴 뒤 등) 그 값도 남겨 저장해도 범위가 바뀌지 않게."""
        from .schedule import _dialog_scopes
        self.assertEqual(_dialog_scopes(self.t4, Event(scope="g5")), [("all", "학교 전체"), ("g4", "4학년"), ("g5", "5학년"), ("me", "개인(나만)")])
        self.assertEqual(_dialog_scopes(self.t4, Event(scope="g4")), [("all", "학교 전체"), ("g4", "4학년"), ("me", "개인(나만)")])

    def test_no_group_member_has_two_choices(self):
        m = self.member("무소속", "지원", "")
        e = self.ev(m, "무소속 일", ALL)
        sel = self.select(self.page(m, "week"), e)
        self.assertEqual(re.findall(r'<option value="(\w+)"', sel), ["all", "me"])


class DutyTests(Base):
    def test_add_reads_name_kind_time(self):
        r = self.add_duty(self.t4, "오일 출장 13:00~16:30")
        self.assertEqual(r.status_code, 200)
        a = Attendance.objects.get()
        self.assertEqual((a.name, a.kind, a.time_text, a.date, a.by, a.member), ("오일", "출장", "13:00~16:30", TODAY, self.t4, self.t5))
        self.assertIn("오일 출장", rows_of(r.content.decode())["복무"])
        self.add_duty(self.t4, "오일 선생님 연가")
        self.assertEqual(Attendance.objects.get(kind="연가").time_text, "종일")
        self.add_duty(self.t4, "부 장 육아시간 오후 3시~4시 30분")
        a = Attendance.objects.get(name="부장")
        self.assertEqual((a.kind, a.time_text), ("부재", "15:00~16:30"))
        self.add_duty(self.t4, "사일 병가")
        self.assertEqual(Attendance.objects.get(name="사일").kind, "휴가")
        self.add_duty(self.t5, "사일 부재")
        self.assertEqual(Attendance.objects.filter(name="사일", kind="부재").count(), 1)

    def test_extra_words_are_not_stored(self):
        self.add_duty(self.t4, "오일 조퇴 14:30 병원진료 건강검진")
        a = Attendance.objects.get()
        self.assertEqual(a.time_text, "14:30~")
        stored = " ".join(str(v) for v in Attendance.objects.values_list().get())
        for w in ("병원", "진료", "건강"):
            self.assertNotIn(w, stored)

    def test_bad_line_gets_short_error_without_echo(self):
        for line in ("모르는사람 출장 비밀사유", "오일 비밀사유", "출장 오일", "오일출장", "비밀사유"):
            r = self.add_duty(self.t4, line)
            self.assertEqual(r.status_code, 200, line)
            self.assertEqual(r["HX-Retarget"], "#cellErr")
            self.assertContains(r, "명부에 있는 이름과 유형(출장·외출·조퇴·연가·휴가·지각·부재)으로 써 주세요")
            self.assertNotContains(r, "비밀사유")
            self.assertNotContains(r, "모르는사람")
        self.assertFalse(Attendance.objects.exists())

    def test_date_is_cell_and_within_31_days(self):
        self.add_duty(self.t4, "오일 출장", d="2026-11-29")
        self.assertEqual(Attendance.objects.get().date, date(2026, 11, 29))
        r = self.add_duty(self.t4, "오일 출장", d="2026-11-30")
        self.assertContains(r, "31일 안에만")
        self.assertEqual(self.add_duty(self.t4, "오일 출장", d="2026-02-30").status_code, 400)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_delete_rights(self):
        writer = self.member("입력자", "교무실", "office", can_duty=True)
        for who in (self.t4, self.boss, writer):  # 넣은 사람·관리자·복무 입력자
            a = self.duty(self.t5, by=self.t4)
            r = self.as_(who).post(f"{self.base}/today/duty/{a.key}/delete", {"view": "week", "mode": "week", "tab": "school"}, headers=HX)
            self.assertEqual(r.status_code, 200, who.name)
            self.assertContains(r, 'data-tab="school"')
            self.assertFalse(Attendance.objects.filter(pk=a.pk).exists(), who.name)
        a = self.duty(self.t5, by=self.t4)
        third = self.member("삼일", "3-1", "g3")
        self.assertEqual(self.as_(third).post(f"{self.base}/today/duty/{a.key}/delete").status_code, 403)
        self.assertTrue(Attendance.objects.filter(pk=a.pk).exists())

    def test_subject_can_delete_forged_line(self):
        """남이 내 이름으로 넣은 복무는 당사자(명부로 맞춘 회원)도 지운다."""
        self.add_duty(self.t4, "오일 조퇴 14:00")
        a = Attendance.objects.get()
        self.assertEqual((a.by, a.member), (self.t4, self.t5))
        html = self.page(self.t5, "week")
        self.assertIn(f'id="duty-{a.key}"', html)
        r = self.as_(self.t5).post(f"{self.base}/today/duty/{a.key}/delete", {"view": "week", "tab": "school"}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Attendance.objects.exists())

    def test_card_names_inserter_when_not_the_subject(self):
        forged = self.duty(self.t5, by=self.t4)
        own = Attendance.objects.create(space=self.hub, member=self.t4, date=TODAY, name="사일", kind="연가", by=self.t4)
        pasted = Attendance.objects.create(space=self.hub, member=None, date=TODAY, name="명부밖", kind="출장", by=None)
        unmatched = Attendance.objects.create(space=self.hub, member=None, date=TODAY, name="동명", kind="출장", by=self.boss)
        row = rows_of(self.page(self.t4, "week"))["복무"]
        cards = {re.search(r'<span class="t">([^<]+)</span>', c).group(1): c for c in row.split('class="ev duty')[1:]}
        self.assertIn('<span class="p">넣은 사람 사일</span>', cards["오일 출장"])
        self.assertNotIn("넣은 사람", cards["사일 연가"])
        self.assertNotIn("넣은 사람", cards["명부밖 출장"])
        self.assertIn('<span class="p">넣은 사람 부장</span>', cards["동명 출장"])
        self.assertEqual(row.count("넣은 사람"), 2, (forged, own, pasted, unmatched))

    def test_dialog_only_for_those_who_can_delete(self):
        a = self.duty(self.t5, by=self.t4)
        mine = self.page(self.t4, "week")
        self.assertIn(f'data-open="#duty-{a.key}"', mine)
        self.assertIn(f'id="duty-{a.key}"', mine)
        self.assertIn(f"/today/duty/{a.key}/delete", mine)
        other = self.page(self.member("삼일", "3-1", "g3"), "week")
        self.assertNotIn(a.key, other)
        self.assertIn('<div class="ev duty" data-ch="duty"><span class="t">오일 출장', other)
        self.assertIn(f'id="duty-{a.key}"', self.page(self.t5, "week"))  # 당사자
        self.assertIn(f'id="duty-{a.key}"', self.page(self.boss, "week"))

    def test_other_space_and_grade_space(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7536100", name="옆초", plan="school", paid_until=date(2099, 1, 1))
        far = Space.objects.create(school=s, kind="school", grade=0, name="옆초")
        fm = Member.objects.create(space=far, user=User.objects.create(username="google:옆"), name="옆", cls="4-1", is_manager=True)
        theirs = Attendance.objects.create(space=far, date=TODAY, name="옆", kind="출장", by=fm)
        c = self.as_(self.boss)
        self.assertEqual(c.post(f"{self.base}/today/duty/{theirs.key}/delete").status_code, 404)
        self.assertEqual(c.post(f"/s/{far.token}/today/duty/{theirs.key}/delete").status_code, 404)
        self.assertTrue(Attendance.objects.filter(pk=theirs.pk).exists())
        g = Space.objects.create(school=s, grade=4, name="4학년")
        gm = Member.objects.create(space=g, user=User.objects.create(username="google:가"), name="가", cls="4-1")
        gc = self.as_(gm)
        self.assertEqual(gc.post(f"/s/{g.token}/today/duty", {"view": "week", "line": "가 출장", "date": TODAY.isoformat()}).status_code, 404)

    def test_expired_plan_refuses_duty_write(self):
        School.objects.filter(pk=self.hub.school_id).update(paid_until=date(2020, 1, 1))
        self.assertEqual(self.add_duty(self.t4, "오일 출장").status_code, 403)

    def test_attendance_paste_rights_unchanged(self):
        self.assertEqual(self.as_(self.t4).get(f"{self.base}/school/attendance").status_code, 403)


class ScopeForgeryTests(Base):
    """볼 사람은 학교 전체·나·내 소속만. 남의 그룹 범위로 쓰거나 옮기지 못한다."""

    def edit(self, m, e, scope):
        return self.as_(m).post(f"{self.base}/today/e/{e.key}", {"view": "week", "tab": "school", "date": TODAY.isoformat(), "slot": "b1",
                                                                 "place": "", "scope": scope}, headers=HX)

    def test_edit_refuses_other_group(self):
        e = self.ev(self.t4, "사일 일", ALL)
        for bad in ("g5", "office", "<x>"):
            self.assertEqual(self.edit(self.t4, e, bad).status_code, 400, bad)
            self.assertEqual(self.edit(self.boss, e, bad if bad != "office" else "g5").status_code, 400, bad)  # 관리자도
        for ok in ("g4", ME, ALL):
            self.assertEqual(self.edit(self.t4, e, ok).status_code, 200, ok)
        e.refresh_from_db()
        self.assertEqual(e.scope, ALL)

    def test_edit_keeps_existing_other_group_value(self):
        e = self.ev(self.t4, "옛 오학년 일", "g5")
        self.assertEqual(self.edit(self.t4, e, "g5").status_code, 200)
        self.assertEqual(self.edit(self.t4, e, "g3").status_code, 400)
        e.refresh_from_db()
        self.assertEqual((e.scope, e.slot), ("g5", "b1"))

    def test_line_input_other_group_falls_back(self):
        c = self.as_(self.t4)
        for bad in ("g5", "office"):
            c.post(f"{self.base}/today/events", {"line": f"오늘 {bad} 시도", "scope": bad}, headers=HX)
            self.assertEqual(Event.objects.get(title=f"{bad} 시도").scope, "g4")
        m = self.member("무소속", "지원", "")
        self.as_(m).post(f"{self.base}/today/events", {"line": "오늘 무소속 시도", "scope": "g5"}, headers=HX)
        self.assertEqual(Event.objects.get(title="무소속 시도").scope, ME)

    def test_today_picker_offers_only_allowed(self):
        html = self.as_(self.t4).get(f"{self.base}/today").content.decode()
        sel = html[html.index('name="scope"'):]
        sel = sel[:sel.index("</select>")]
        self.assertEqual(re.findall(r'<option value="(\w+)"', sel), [ALL, ME, "g4"])


class ConflictTests(Base):
    """남의 개인 일정은 '다른 일정'으로도 보이지 않는다(관리자 포함)."""

    def test_others_private_not_in_any_clash(self):
        self.ev(self.t4, "혼자비밀", ME, "b2", place="과학실")
        for m in (self.boss, self.t5):
            mine = self.ev(m, f"{m.name} 수업", ALL, "b2", place="과학실")
            html = self.page(m, "week")
            self.assertNotIn("겹쳐요", html, m.name)
            self.assertNotIn("다른 일정", html, m.name)
            self.assertNotIn("장소 겹침", html, m.name)
            mine.delete()
            r = self.as_(m).post(f"{self.base}/today/parse", {"line": "2026-10-29 2블록 과학실 회의"})
            self.assertNotContains(r, "겹쳐요")

    def test_own_private_still_clashes(self):
        self.ev(self.t4, "내 혼자", ME, "b2", place="과학실")
        r = self.as_(self.t4).post(f"{self.base}/today/parse", {"line": "2026-10-29 2블록 과학실 회의"})
        self.assertContains(r, "내 혼자")

    def test_unreachable_group_still_anonymous(self):
        self.ev(self.t4, "사학년비밀", "g4", "b2", place="과학실")
        r = self.as_(self.t5).post(f"{self.base}/today/parse", {"line": "2026-10-29 2블록 과학실 회의"})
        self.assertContains(r, "다른 일정 (2블록)")
        self.assertNotContains(r, "사학년비밀")


class SameBlockTests(Base):
    def test_typed_block_is_not_repeated_under_title(self):
        self.add(self.t4, "2블록 안전교육", scope=ALL)
        e = Event.objects.get()
        self.assertEqual((e.title, e.slot), ("2블록 안전교육", "b2"))
        for mode in ("week", "month"):
            self.assertEqual(card(self.page(self.t4, mode, w="2026-10-27"), e).count("2블록"), 1, mode)

    def test_range_typed_in_title_not_repeated(self):
        e = self.ev(self.t4, "1~2블록 행사", ALL, "b1", slot_end="b2", place="강당")
        c = card(self.page(self.t4, "week"), e)
        self.assertEqual(c.count("1~2블록"), 1)
        self.assertIn("강당", c)

    def test_block_without_word_in_title_still_shown(self):
        e = self.ev(self.t4, "안전교육", ALL, "b2")
        self.assertIn("2블록", card(self.page(self.t4, "week"), e))


class IsolationTests(Base):
    def test_other_space_404_and_hidden(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7536000", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        u = User.objects.create(username="google:옆")
        m = Member.objects.create(space=g, user=u, name="옆사람", cls="4-1")
        Event.objects.create(space=g, by=m, date=TODAY, title="옆 비밀")
        for tab in ("school", "group", "me"):
            for mode in ("day", "week", "month"):
                self.assertEqual(self.as_(self.t4).get(f"/s/{g.token}/schedule", {"mode": mode, "tab": tab}).status_code, 404)
                self.assertNotIn("옆 비밀", self.page(self.t4, mode, tab=tab), mode)


class FilterMarkupTests(Base):
    def test_channel_filter_only_on_school_tab(self):
        for mode in ("day", "week", "month"):
            html = self.page(self.t4, mode)
            title = html[html.index('class="title"'):html.index('id="cellTpl"')]
            self.assertEqual(title.count('class="btn sec flt-btn"'), 1, mode)
            self.assertEqual(re.findall(r'data-flt="ch" value="(\w+)"', title), ["school", "collect", "duty"], mode)
            self.assertNotIn('data-flt="grp"', html, mode)
            self.assertNotIn("data-group", html, mode)

    def test_queries_do_not_grow_with_cards(self):
        def count(tab, mode):
            with CaptureQueriesContext(connection) as q:
                self.page(self.t4, mode, tab=tab)
            return len(q)
        self.ev(self.t5, "처음")
        self.ev(self.t4, "그룹 처음", "g4", "b1", place="강당")
        self.duty(self.t5)
        self.due(self.t4, "처음 표")
        self.due(self.t5, "남의 표", row_for=self.t4)
        before = {(t, m): count(t, m) for t in ("school", "group") for m in ("day", "week", "month")}
        for i in range(8):
            m = self.member(f"교사{i}", f"3-{i}", "g3")
            self.ev(m, f"일정{i}")
            self.ev(self.t4, f"그룹{i}", "g4", "b1", place="강당")
            self.duty(m)
            self.due(m, f"표{i}", row_for=self.t4 if i % 2 else None)
        for k, n in before.items():
            self.assertEqual(count(*k), n, k)


class GradeSpaceTests(TestCase):
    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]),
                  mock.patch("spaces.neis.schedule", return_value=SCHED)):
            p.start()
            self.addCleanup(p.stop)
        s = School.objects.create(neis_atpt="J10", neis_code="7537000", name="동학년초")
        self.g = Space.objects.create(school=s, grade=4, name="4학년")
        u = User.objects.create(username="google:가")
        self.m = Member.objects.create(space=self.g, user=u, name="가", cls="4-1")
        self.client.force_login(u)
        self.base = f"/s/{self.g.token}"

    def page(self, mode, **kw):
        return self.client.get(f"{self.base}/schedule", {"mode": mode, **kw}).content.decode()

    def test_no_tabs_and_two_channel_rows_every_mode(self):
        Event.objects.create(space=self.g, by=self.m, date=TODAY, slot="b2", title="협의")
        for mode in ("day", "week"):
            html = self.page(mode, tab="me")
            self.assertNotIn('class="tabs sched-tabs"', html, mode)
            rows = rows_of(html)
            self.assertEqual(list(rows), ["동학년", "취합"], mode)
            self.assertIn("협의", rows["동학년"])
            self.assertIn("독서주간 골든벨", rows["동학년"])
            self.assertNotIn("data-cell", rows["취합"])
        self.assertEqual(self.page("week").count('<button type="button" class="cell-add"'), 5)
        self.assertEqual(self.page("day").count('<button type="button" class="cell-add"'), 1)
        self.assertEqual(rows_of(self.page("week"))["취합"].count(f'<a class="cell-add" href="{self.base}/c/new?due_d='), 5)
        self.assertEqual(month_labels(self.page("month")), ["동학년", "취합"] * 5)

    def test_filter_is_two_channels(self):
        for mode in ("day", "week", "month"):
            html = self.page(mode)
            self.assertEqual(re.findall(r'data-flt="ch" value="(\w+)"', html), ["school", "collect"], mode)
            self.assertNotIn('data-flt="grp"', html)
            self.assertNotIn("data-group", html)

    def test_no_scope_field_and_add_is_all(self):
        html = self.page("week")
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertNotIn('name="scope"', tpl)
        self.assertIn('placeholder="제목 쓰고 Enter">', tpl)
        self.assertNotIn("data-ph-", tpl)
        self.client.post(f"{self.base}/today/events", {"view": "week", "line": "회의", "date": "2026-10-27", "slot": "", "scope": ME}, headers=HX)
        self.assertEqual(Event.objects.get().scope, ALL)

    def test_cell_without_scope_is_fine_and_toast_has_no_audience(self):
        r = self.client.post(f"{self.base}/today/events", {"view": "week", "line": "회의", "date": "2026-10-27", "slot": ""}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "에 보여요")

    def test_collection_link_for_everyone_as_before(self):
        peer = Member.objects.create(space=self.g, user=User.objects.create(username="google:나"), name="나", cls="4-2")
        c = Collection.objects.create(space=self.g, created_by=peer, title="학년표", source=Collection.Source.SHEET, unit_head=["학년반"],
                                      due=datetime(2026, 10, 29, 11, 0, tzinfo=KST))
        html = self.page("week")
        self.assertIn(f'href="/c/{c.link_token}"', html)
        self.assertIn("학년표", html.split('id="due"')[1])
