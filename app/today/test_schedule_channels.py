"""QC 일정 채널(2026-11-05) — 일정 판을 학교·복무·취합·개인 채널로 나눈다.

불변식
- 일간: 종일 → 1·2·3블록 → 방과후 → 개인. 개인(scope=me)은 개인 줄에만, 블록은 카드에 표시.
- 주간·월간: 왼쪽 줄이 채널. 복무·취합 줄에는 칸 추가가 없다. 동학년은 일정·취합 두 줄.
- 칸의 볼 사람은 줄이 정한다(학교 all, 개인 me). 칸의 slot이 비면 문장에서 읽은 블록을 살린다.
- 격리: 개인 일정은 쓴 사람만(관리자에게도 안 보인다). 다른 공간은 404.
- 필터는 클라이언트(schedule.js). 서버는 표시(data-ch·data-group)와 목록만 낸다. 쿼리 수는 일정 수와 무관.
"""
import re
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from collect.models import Collection, Field, Row
from spaces.models import Attendance, Member, School, Space

from .models import ALL, ME, Event, scope_choices

User = get_user_model()
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 29, 9, 5, tzinfo=KST)  # 목요일
TODAY = NOW.date()
SCHED = [{"ymd": "20261029", "name": "독서주간 골든벨"}]


def grid_of(html):
    """판 격자만(대화상자·아래 목록 앞까지)."""
    i = html.index('class="wgrid') if 'class="wgrid' in html else html.index('class="mgrid"')
    return html[i:html.index("</section>", i)].split('class="scrim"')[0]


def rows_of(html):
    """{줄 이름: 그 줄 HTML}, 순서 유지(일간·주간)."""
    parts = re.split(r'<div class="lbl"[^>]*>', grid_of(html))[1:]
    return {re.match(r"[^<]*", p).group(0).strip(): p for p in parts}


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

    def due(self, by, title, hour=11, d=TODAY):
        c = Collection.objects.create(space=self.hub, created_by=by, title=title, source=Collection.Source.SHEET, unit_head=["학년반"],
                                      due=datetime(d.year, d.month, d.day, hour, 0, tzinfo=KST))
        Field.objects.create(collection=c, pos=0, name="칸", kind="number")
        Row.objects.create(collection=c, pos=0, unit=by.cls, who=by.name, member=by)
        return c

    def duty(self, m, d=TODAY):
        return Attendance.objects.create(space=self.hub, member=m, date=d, name=m.name, kind="출장", by=m)

    def page(self, m, mode, **kw):
        return self.as_(m).get(f"{self.base}/schedule", {"mode": mode, **kw}).content.decode()

    def add(self, m, line, slot="", scope=None, mode="week", d="2026-10-27"):
        data = {"view": "week", "mode": mode, "w": "2026-10-26", "line": line, "date": d, "slot": slot}
        if scope:
            data["scope"] = scope
        return self.as_(m).post(f"{self.base}/today/events", data, headers={"HX-Request": "true"})


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
        r = c.post(f"{self.base}/today/events", {"line": "내일 회의"}, headers={"HX-Request": "true"})
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
        r = self.as_(self.t4).post(f"{self.base}/schedule/paste/preview", {"w": "2026-10-26", "paste": "10/27\t\t회의"}, headers={"HX-Request": "true"})
        self.assertNotContains(r, "전일")
        self.assertContains(r, "종일")

    def test_edit_from_full_day_block_range_to_all_day_still_saves(self):
        """'1~3블록'의 표기도 '종일'이다. 글자가 같아도 블록을 지우면 저장돼야 한다."""
        e = self.ev(self.t4, "운동회", slot="b1", slot_end="b3")
        r = self.as_(self.t4).post(f"{self.base}/today/e/{e.key}", {"view": "week", "date": TODAY.isoformat(), "slot": "", "place": "", "scope": ALL},
                                   headers={"HX-Request": "true"})
        self.assertNotContains(r, "바뀐 것이 없어요")
        e.refresh_from_db()
        self.assertEqual((e.slot, e.slot_end), ("", ""))

    def test_edit_with_nothing_changed_is_still_a_noop(self):
        for slot, end in (("", ""), ("b1", "b3"), ("b2", "b2")):
            e = self.ev(self.t4, f"그대로 {slot}{end}", slot=slot, slot_end=end)
            r = self.as_(self.t4).post(f"{self.base}/today/e/{e.key}", {"view": "week", "date": TODAY.isoformat(), "slot": slot, "place": "", "scope": ALL},
                                       headers={"HX-Request": "true"})
            self.assertContains(r, "바뀐 것이 없어요", msg_prefix=f"{slot}{end}")


class DayTests(Base):
    def test_row_order(self):
        self.assertEqual(list(rows_of(self.page(self.t4, "day"))), ["종일", "1블록", "2블록", "3블록", "방과후", "개인"])
        self.assertIn("나만 보여요", rows_of(self.page(self.t4, "day"))["개인"])

    def test_sources_go_to_their_rows(self):
        mine = self.ev(self.t4, "혼자 할 일", ME, "b2")
        self.ev(self.t4, "혼자 종일", ME)
        self.ev(self.t5, "2블록 수업", ALL, "b2")
        self.ev(self.t5, "모두 종일", ALL)
        self.duty(self.t5)
        self.due(self.t4, "낼 표", hour=11)  # 10:30~12:20 → 2블록
        rows = rows_of(self.page(self.t4, "day"))
        self.assertIn("모두 종일", rows["종일"])
        self.assertIn("독서주간 골든벨", rows["종일"])
        self.assertIn("오일 출장", rows["종일"])
        self.assertIn("2블록 수업", rows["2블록"])
        self.assertIn("낼 표", rows["2블록"])
        for k in ("종일", "1블록", "2블록", "3블록", "방과후"):
            self.assertNotIn("혼자", rows[k], k)
        self.assertIn("혼자 할 일", rows["개인"])
        self.assertIn("혼자 종일", rows["개인"])
        card = rows["개인"][rows["개인"].index(f'data-open="#ev-{mine.key}"'):]
        self.assertIn("2블록", card[:card.index("</button>")])  # 줄로 알 수 없으므로 카드에

    def test_add_cells(self):
        html = self.page(self.t4, "day")
        d = TODAY.isoformat()
        self.assertEqual(html.count('class="cell-add"'), 6)
        for slot in ("", "b1", "b2", "b3", "pm"):
            self.assertIn(f'data-cell data-date="{d}" data-slot="{slot}" data-scope="all"', html)
        self.assertIn(f'data-cell data-date="{d}" data-slot="" data-scope="me"', html)

    def test_block_row_beats_parsed_block_and_all_day_row_keeps_it(self):
        self.add(self.t4, "2블록 칸이 이김", slot="pm", scope=ALL, mode="day")
        e = Event.objects.get(title="2블록 칸이 이김")
        self.assertEqual((e.slot, e.slot_end), ("pm", "pm"))
        self.add(self.t4, "2블록 안전교육", slot="", scope=ALL, mode="day")
        e = Event.objects.get(title="2블록 안전교육")
        self.assertEqual((e.slot, e.slot_end), ("b2", "b2"))


class WeekTests(Base):
    def test_rows_are_channels(self):
        rows = rows_of(self.page(self.t4, "week"))
        self.assertEqual(list(rows), ["학교", "복무", "취합", "개인"])

    def test_only_school_and_personal_rows_can_add(self):
        html = self.page(self.t4, "week")
        self.assertEqual(html.count('class="cell-add"'), 10)
        rows = rows_of(html)
        for k in ("복무", "취합"):
            self.assertNotIn("data-cell", rows[k], k)
            self.assertNotIn("cell-add", rows[k], k)
        self.assertEqual(rows["학교"].count('data-slot="" data-scope="all"'), 5)
        self.assertEqual(rows["개인"].count('data-slot="" data-scope="me"'), 5)

    def test_sources_and_order_inside_cell(self):
        self.ev(self.t4, "방과후 일", ALL, "pm")
        self.ev(self.t4, "셋째 일", ALL, "b3")
        self.ev(self.t4, "첫째 일", ALL, "b1")
        self.ev(self.t4, "종일 일", ALL)
        self.ev(self.t4, "개인 일", ME, "b1")
        self.duty(self.t4)
        self.due(self.t4, "낼 표")
        rows = rows_of(self.page(self.t4, "week"))
        self.assertIn("독서주간 골든벨", rows["학교"])
        self.assertIn("사일 출장", rows["복무"])
        self.assertIn("낼 표", rows["취합"])
        self.assertIn("개인 일", rows["개인"])
        self.assertNotIn("개인 일", rows["학교"])
        school = rows["학교"]
        self.assertLess(school.index("종일 일"), school.index("첫째 일"))
        self.assertLess(school.index("첫째 일"), school.index("셋째 일"))
        self.assertLess(school.index("셋째 일"), school.index("방과후 일"))

    def test_block_name_on_card(self):
        e = self.ev(self.t4, "수업 공개", ALL, "b2", place="과학실")
        rows = rows_of(self.page(self.t4, "week"))["학교"]
        card = rows[rows.index(f'data-open="#ev-{e.key}"'):]
        card = card[:card.index("</button>")]
        self.assertIn("2블록 · ", card)
        self.assertIn("과학실", card)

    def test_block_range_named_once(self):
        e = self.ev(self.t4, "오전 행사", ALL, "b1", slot_end="b2")
        rows = rows_of(self.page(self.t4, "week"))["학교"]
        card = rows[rows.index(f'data-open="#ev-{e.key}"'):]
        card = card[:card.index("</button>")]
        self.assertEqual(card.count("1~2블록"), 1)

    def test_all_day_card_has_no_block_name(self):
        e = self.ev(self.t4, "행사", ALL)
        rows = rows_of(self.page(self.t4, "week"))["학교"]
        card = rows[rows.index(f'data-open="#ev-{e.key}"'):]
        self.assertNotIn("블록", card[:card.index("</button>")])

    def test_parsed_block_kept_when_cell_has_no_slot(self):
        self.add(self.t4, "2블록 안전교육", scope=ALL)
        self.assertEqual(Event.objects.get().slot, "b2")
        self.assertEqual(Event.objects.get().date, date(2026, 10, 27))  # 날짜는 칸이 이긴다


class MonthTests(Base):
    def test_every_week_has_date_head_and_channel_rows(self):
        html = self.page(self.t4, "month")  # 2026년 10월: 5주
        grid = grid_of(html)
        self.assertEqual("".join(re.findall(r'<div class="mh[^"]*">(.)</div>', grid)), "일월화수목금토")
        self.assertEqual(re.findall(r'<div class="ml"[^>]*>([^<]+)</div>', grid), ["학교", "복무", "취합", "개인"] * 5)
        self.assertEqual(grid.count('class="mdh'), 35)
        self.assertEqual(grid.count('class="cell-add"'), 70)
        self.assertIn('class="mdh tcol"', grid.replace(" we", ""))
        self.assertIn("29 · 오늘", grid)
        self.assertRegex(grid, r'class="mdh out[^"]*"')  # 9/27은 달 밖
        self.assertRegex(grid, r'class="mdh[^"]* we[^"]*"')

    def test_only_school_and_personal_rows_can_add(self):
        grid = grid_of(self.page(self.t4, "month"))
        for ch in ("duty", "collect"):
            cells = re.findall(rf'<div class="mc[^"]*" data-ch="{ch}"[^>]*>', grid)
            self.assertEqual(len(cells), 35, ch)
            self.assertFalse([c for c in cells if "data-cell" in c], ch)
        self.assertEqual(grid.count('data-slot="" data-scope="all"'), 35)
        self.assertEqual(grid.count('data-slot="" data-scope="me"'), 35)

    def test_range_event_skips_weekend_and_personal_stays_in_its_row(self):
        e = self.ev(self.t4, "긴 행사", ALL, d=date(2026, 11, 6), end=date(2026, 11, 9))
        p = self.ev(self.t4, "혼자", ME, "b2", d=date(2026, 11, 6))
        grid = grid_of(self.page(self.t4, "month", w="2026-11-01"))
        self.assertEqual(grid.count(f'data-open="#ev-{e.key}"'), 2)
        i = grid.index(f'data-open="#ev-{p.key}"')
        self.assertEqual(re.findall(r'data-ch="(\w+)"[^>]*>', grid[:i])[-1], "me")

    def test_week_view_row_of_group_cards(self):
        self.ev(self.t4, "사학년 씀")
        html = self.page(self.t4, "week")
        self.assertNotIn("data-group", html)  # 그룹 필터는 월간만


class CellAddTests(Base):
    def test_template_has_hidden_scope_and_no_select(self):
        html = self.page(self.t4, "week")
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertIn('<input type="hidden" name="scope">', tpl)
        self.assertNotIn("<select", tpl)

    def test_scope_comes_from_cell(self):
        self.add(self.t4, "모두 볼 일", scope=ALL)
        self.add(self.t4, "혼자 볼 일", scope=ME)
        self.assertEqual(Event.objects.get(title="모두 볼 일").scope, ALL)
        self.assertEqual(Event.objects.get(title="혼자 볼 일").scope, ME)

    def test_cell_without_valid_scope_is_rejected(self):
        for kw in ({}, {"scope": "<x>"}, {"scope": ""}, {"scope": "g9"}):
            self.assertEqual(self.add(self.t4, "거절", **kw).status_code, 400, kw)
        self.assertFalse(Event.objects.exists())

    def test_line_input_keeps_scope_fallback(self):
        c = self.as_(self.t4)
        c.post(f"{self.base}/today/events", {"line": "오늘 값 없음"}, headers={"HX-Request": "true"})
        c.post(f"{self.base}/today/events", {"line": "오늘 값 틀림", "scope": "<x>"}, headers={"HX-Request": "true"})
        self.assertEqual(Event.objects.get(title="값 없음").scope, "g4")
        self.assertEqual(Event.objects.get(title="값 틀림").scope, "g4")

    def test_placeholder_follows_row(self):
        html = self.page(self.t4, "week")
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertIn('data-ph-all="학교 전체 · 제목 쓰고 Enter"', tpl)  # 좁은 칸에서 잘려도 범위가 보이게 앞에
        self.assertIn('data-ph-me="나만 보기 · 제목 쓰고 Enter"', tpl)

    def test_toast_names_audience(self):
        for scope, words in ((ME, "나만 보여요"), (ALL, "학교 전체에 보여요"), ("g5", "5학년에 보여요")):
            self.assertContains(self.add(self.t4, f"칸 {scope}", scope=scope), words)  # 칸 추가
            r = self.as_(self.t4).post(f"{self.base}/today/events", {"line": f"줄 {scope}", "scope": scope}, headers={"HX-Request": "true"})
            self.assertContains(r, words)  # 한 줄 입력
            self.assertIn("올렸어요 · " + words, r.content.decode())


class ScopeChoiceTests(Base):
    def test_order_and_words(self):
        for m in (self.t4, self.boss):
            got = scope_choices(m)
            self.assertEqual(got[:2], [(ALL, "학교 전체"), (ME, "개인(나만)")])
            self.assertEqual([k for k, _ in got[2:]], ["g1", "g2", "g3", "g4", "g5", "g6", "office", "admin", "meal", "support"])
        self.assertNotContains(self.as_(self.t4).get(f"{self.base}/schedule"), "우리 소속")

    def test_dialog_keeps_existing_group_scope_selected(self):
        e = self.ev(self.t4, "사학년 일", "g4")
        html = self.page(self.t4, "week")
        dlg = html[html.index(f'id="ev-{e.key}"'):]
        dlg = dlg[dlg.index('name="scope"'):dlg.index("</select>", dlg.index('name="scope"'))]
        self.assertIn('<option value="g4" selected>4학년</option>', dlg)
        self.assertLess(dlg.index('value="all"'), dlg.index('value="me"'))
        self.assertIn('>개인(나만)</option>', dlg)

    def test_today_line_input_defaults_to_whole_school(self):
        html = self.as_(self.t4).get(f"{self.base}/today").content.decode()
        sel = html[html.index('name="scope"'):]
        self.assertEqual(re.search(r'<option value="([^"]+)"', sel).group(1), ALL)


class IsolationTests(Base):
    def test_personal_is_writer_only_even_for_manager(self):
        self.ev(self.t4, "사일 비밀", ME, "b1")
        self.ev(self.t4, "사일 그룹", "g4", "b1")
        for mode in ("day", "week", "month"):
            self.assertIn("사일 비밀", self.page(self.t4, mode), mode)
            self.assertNotIn("사일 비밀", self.page(self.boss, mode), mode)
            self.assertNotIn("사일 비밀", self.page(self.t5, mode), mode)
            self.assertIn("사일 그룹", self.page(self.boss, mode), mode)  # 관리자는 남의 그룹 일정을 본다(그대로)
            self.assertNotIn("사일 그룹", self.page(self.t5, mode), mode)

    def test_other_space_404_and_hidden(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7536000", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        u = User.objects.create(username="google:옆")
        m = Member.objects.create(space=g, user=u, name="옆사람", cls="4-1")
        Event.objects.create(space=g, by=m, date=TODAY, title="옆 비밀")
        for mode in ("day", "week", "month"):
            self.assertEqual(self.as_(self.t4).get(f"/s/{g.token}/schedule", {"mode": mode}).status_code, 404)
            self.assertNotIn("옆 비밀", self.page(self.t4, mode), mode)


class FilterMarkupTests(Base):
    def test_channel_filter_every_mode_group_only_in_month(self):
        for mode, n in (("day", 1), ("week", 1), ("month", 2)):
            html = self.page(self.t4, mode)
            title = html[html.index('class="title"'):html.index('id="cellTpl"')]
            self.assertEqual(title.count('class="btn sec flt-btn"'), n, mode)
            self.assertRegex(title, r'<button type="button" class="btn sec flt-btn" aria-label="채널 필터" title="채널 필터"')
            self.assertEqual(re.findall(r'data-flt="ch" value="(\w+)"', title), ["school", "duty", "collect", "me"], mode)
            self.assertEqual(len(re.findall(r'data-flt="grp" value="(\w+)"', title)), 10 if mode == "month" else 0, mode)
            self.assertNotIn("flt-btn", html[html.index('id="week"'):], mode)  # #week 밖

    def test_cards_carry_writer_group_in_month(self):
        e = self.ev(self.t4, "일정 카드")
        self.duty(self.t5)
        Attendance.objects.create(space=self.hub, date=TODAY, name="명부에 없음", kind="출장")
        self.due(self.boss, "취합 카드")
        ownerless = self.ev(None, "주인 없음")
        Row.objects.filter(collection__title="취합 카드").update(member=self.t4)  # 사일이 낼 사람이어야 보인다
        html = self.page(self.t4, "month")
        grid = grid_of(html)
        self.assertRegex(grid, rf'data-open="#ev-{e.key}"[^>]*data-ch="school"[^>]*data-group="g4"')
        self.assertRegex(grid, r'class="ev duty" data-ch="duty" data-group="g5"><span class="k">복무</span> 오일 출장')
        self.assertRegex(grid, r'class="ev duty" data-ch="duty"><span class="k">복무</span> 명부에 없음')
        self.assertRegex(grid, r'class="ev due"[^>]*data-ch="collect" data-group="office"')
        self.assertRegex(grid, r'class="ev neis" data-ch="school">독서주간')
        self.assertNotRegex(grid, rf'data-open="#ev-{ownerless.key}"[^>]*data-group')

    def test_queries_do_not_grow_with_cards(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                self.page(self.t4, "month")
            return len(q)
        self.ev(self.t5, "처음")
        self.duty(self.t5)
        self.due(self.t4, "처음 표")
        first = self.due(self.t5, "남의 표")  # 내가 낼 줄을 찾는 쿼리까지 처음부터 돌게
        Row.objects.create(collection=first, pos=1, unit=self.t4.cls, who=self.t4.name, member=self.t4)
        before = count()
        for i in range(8):
            m = self.member(f"교사{i}", f"3-{i}", "g3")
            self.ev(m, f"일정{i}")
            self.duty(m)
            c = self.due(m, f"표{i}")
            Row.objects.create(collection=c, pos=1, unit=self.t4.cls, who=self.t4.name, member=self.t4)
        self.assertEqual(count(), before)


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

    def page(self, mode):
        return self.client.get(f"{self.base}/schedule", {"mode": mode}).content.decode()

    def test_week_has_two_rows(self):
        Event.objects.create(space=self.g, by=self.m, date=TODAY, slot="b2", title="협의")
        html = self.page("week")
        rows = rows_of(html)
        self.assertEqual(list(rows), ["동학년", "취합"])
        self.assertIn("협의", rows["동학년"])
        self.assertIn("독서주간 골든벨", rows["동학년"])
        self.assertEqual(html.count('class="cell-add"'), 5)
        self.assertNotIn("data-cell", rows["취합"])

    def test_day_has_no_personal_row(self):
        self.assertEqual(list(rows_of(self.page("day"))), ["종일", "1블록", "2블록", "3블록", "방과후"])

    def test_month_rows_and_no_group_filter(self):
        html = self.page("month")
        self.assertEqual(re.findall(r'<div class="ml"[^>]*>([^<]+)</div>', html)[:2], ["동학년", "취합"])
        self.assertEqual(html.count('class="btn sec flt-btn"'), 1)
        self.assertEqual(re.findall(r'data-flt="ch" value="(\w+)"', html), ["school", "collect"])
        self.assertNotIn('data-flt="grp"', html)
        self.assertNotIn("data-group", html)

    def test_no_scope_field_and_add_is_all(self):
        html = self.page("week")
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertNotIn('name="scope"', tpl)
        self.assertIn('placeholder="제목 쓰고 Enter">', tpl)  # 동학년 문구 그대로
        self.assertNotIn("data-ph-", tpl)
        self.client.post(f"{self.base}/today/events", {"view": "week", "line": "회의", "date": "2026-10-27", "slot": "", "scope": ME},
                         headers={"HX-Request": "true"})
        self.assertEqual(Event.objects.get().scope, ALL)

    def test_cell_without_scope_is_fine_and_toast_has_no_audience(self):
        r = self.client.post(f"{self.base}/today/events", {"view": "week", "line": "회의", "date": "2026-10-27", "slot": ""}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "에 보여요")
