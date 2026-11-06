"""QC 일정 채널 독립 검증(gt-qa). test_schedule_channels.py에 없는 경계만.

- 남의 개인 일정: 일정 판 말고 다른 표면(오늘·인쇄·바뀐 것·겹침 문구·htmx 응답·주소)까지.
- POST 조작: 칸에 없는 값·남의 일정 주소·다른 공간.
- N+1: 세 모드 모두, 겹침·바뀐 것·개인 일정이 있는 상태로.
- 화면 문구: 제품 파일 전체에서 옛 문구 검색(테스트·design/ 제외).
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from collect.models import Collection, Field, Row
from spaces.models import Attendance, Member, School, Space

from .models import ALL, ME, Event
from .test_schedule_channels import grid_of, rows_of

User = get_user_model()
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 29, 9, 5, tzinfo=KST)  # 목요일
TODAY = NOW.date()
SCHED = [{"ymd": "20261029", "name": "독서주간 골든벨"}]
HX = {"HX-Request": "true"}


class Base(TestCase):
    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]),
                  mock.patch("spaces.neis.schedule", return_value=SCHED)):
            p.start()
            self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7538000", name="검증초", plan="school", paid_until=date(2099, 1, 1))
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="검증초")
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

    def page(self, m, mode, tab="school", **kw):
        return self.as_(m).get(f"{self.base}/schedule", {"mode": mode, "tab": tab, **kw}).content.decode()


class PersonalLeakTests(Base):
    """쓴 사람이 아니면 관리자도 어떤 표면에서도 제목·이름·key를 받지 않는다."""

    def setUp(self):
        super().setUp()
        self.secret = self.ev(self.t4, "비밀상담기록", ME, "b2", place="과학실", target="비밀대상자",
                              changed_from=[["장소", "비밀옛장소", "과학실"]], changed_by=self.t4)

    def viewers(self):
        return (self.boss, self.t5)

    def assert_clean(self, html, where):
        for word in ("비밀상담기록", "비밀대상자", "비밀옛장소", self.secret.key):
            self.assertNotIn(word, html, where)

    def test_every_page(self):
        paths = [f"/schedule?mode={m}" for m in ("day", "week", "month")] + [
            "/today", "/schedule/print?view=week&d=2026-10-26", "/schedule/print?view=month&d=2026-10-01",
            "/schedule/paste?w=2026-10-26"]
        for m in self.viewers():
            c = self.as_(m)
            for path in paths:
                r = c.get(self.base + path)
                self.assertEqual(r.status_code, 200, path)
                self.assert_clean(r.content.decode(), f"{m.name} {path}")

    def test_writer_sees_it_in_personal_row_and_change_list(self):
        html = self.page(self.t4, "week", tab="me")  # QC 2026-11-06: 개인 탭
        self.assertIn("비밀상담기록", rows_of(html)["2블록"])
        self.assertIn("비밀옛장소", html[html.index('id="chg"'):])

    def test_change_list_count_and_item_count_do_not_betray(self):
        for m in self.viewers():
            html = self.page(m, "week")
            self.assertRegex(html, r'id="wkH">[^<]*</h2><span class="n num">1</span>', m.name)  # NEIS 한 건만
            self.assertIn("바뀐 일정이 없어요", html, m.name)

    def test_htmx_response_after_write_is_clean(self):
        """다른 사람이 칸에 추가·고치기·지우기 한 뒤의 판 응답도 같은 필터를 탄다."""
        c = self.as_(self.boss)
        base = {"view": "week", "w": "2026-10-26"}
        for mode in ("day", "week", "month"):
            r = c.post(f"{self.base}/today/events", {**base, "mode": mode, "line": f"{mode} 추가", "date": TODAY.isoformat(), "slot": "", "scope": ALL}, headers=HX)
            self.assertEqual(r.status_code, 200)
            self.assert_clean(r.content.decode(), mode)

    def test_place_clash_says_no_title_or_name(self):
        """QC 2026-11-06: 남의 개인 일정은 겹침 판정에서 뺀다. '다른 일정'으로도 나오지 않는다."""
        for m in self.viewers():
            mine = self.ev(m, f"{m.name} 수업", ALL, "b2", place="과학실")
            html = self.page(m, "week")
            self.assert_clean(html, m.name)
            self.assertNotIn("다른 일정", html, m.name)
            self.assertNotIn("장소 겹침", html, m.name)
            mine.delete()

    def test_preview_and_toast_on_clash_say_no_title(self):
        c = self.as_(self.boss)
        line = "2026-10-29 2블록 과학실 회의"
        self.assert_clean(c.post(f"{self.base}/today/parse", {"line": line}).content.decode(), "미리보기")
        self.assert_clean(c.post(f"{self.base}/today/events", {"line": line}, headers=HX).content.decode(), "토스트")

    def test_manager_cannot_reach_by_address(self):
        url = f"{self.base}/today/e/{self.secret.key}"
        c = self.as_(self.boss)
        data = {"view": "week", "date": TODAY.isoformat(), "slot": "b2", "place": "", "scope": ALL}
        self.assertEqual(c.post(url, data).status_code, 404)
        self.assertEqual(c.post(url + "/delete").status_code, 404)
        self.secret.refresh_from_db()
        self.assertEqual((self.secret.scope, self.secret.title), (ME, "비밀상담기록"))

    def test_group_filter_attribute_is_not_a_leak_path(self):
        """월간 data-group은 볼 수 있는 카드에만. 남의 개인 일정만 있는 날엔 g4 표시가 없다."""
        self.assertNotIn('data-group="g4"', grid_of(self.page(self.boss, "month")))


class TamperTests(Base):
    def add(self, m, **kw):
        data = {"view": "week", "mode": "week", "w": "2026-10-26", "line": "조작 시도", "date": "2026-10-27", "slot": "", **kw}
        return self.as_(m).post(f"{self.base}/today/events", data, headers=HX)

    def test_bad_cell_values_rejected(self):
        for bad in ({"slot": "zz"}, {"slot": "'\"><x"}, {"date": "2026-02-30"}, {"date": "x"}, {"date": ""}):
            self.assertEqual(self.add(self.t4, **bad).status_code, 400, bad)
        self.assertFalse(Event.objects.filter(title="조작 시도").exists())

    def test_extra_fields_cannot_set_owner_or_space(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7539000", name="옆초")
        g = Space.objects.create(school=other, grade=4, name="옆")
        self.add(self.t4, by=self.boss.pk, space=g.pk, key="abc", changed_by=self.boss.pk, scope=ME)
        e = Event.objects.get(title="조작 시도")
        self.assertEqual((e.by_id, e.space_id, e.changed_by_id, e.scope), (self.t4.pk, self.hub.pk, None, ME))
        self.assertNotEqual(e.key, "abc")

    def test_non_writer_cannot_change_visible_event(self):
        e = self.ev(self.t5, "오학년 전체", ALL, "b1")
        for action, data in (("", {"view": "week", "date": TODAY.isoformat(), "slot": "b1", "place": "", "scope": ME}), ("/delete", {})):
            self.assertEqual(self.as_(self.t4).post(f"{self.base}/today/e/{e.key}{action}", data).status_code, 403, action)
        e.refresh_from_db()
        self.assertEqual(e.scope, ALL)

    def test_non_member_group_event_404_for_other_group(self):
        e = self.ev(self.t5, "오학년만", "g5", "b1")
        self.assertEqual(self.as_(self.t4).post(f"{self.base}/today/e/{e.key}/delete").status_code, 404)

    def test_other_space_token_swap(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7530000", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        u = User.objects.create(username="google:옆")
        outsider = Member.objects.create(space=g, user=u, name="옆사람", cls="4-1")
        theirs = Event.objects.create(space=g, by=outsider, date=TODAY, title="옆 일정")
        ours = self.ev(self.t4, "우리 일정")
        mine, them = self.as_(self.t4), self.as_(outsider)
        line = {"view": "week", "line": "넘어가기", "date": TODAY.isoformat(), "slot": ""}
        self.assertEqual(mine.post(f"/s/{g.token}/today/events", line).status_code, 404)
        self.assertEqual(them.post(f"{self.base}/today/events", line).status_code, 404)
        self.assertEqual(mine.post(f"{self.base}/today/e/{theirs.key}/delete").status_code, 404)  # 우리 주소 + 남의 key
        self.assertEqual(mine.post(f"/s/{g.token}/today/e/{theirs.key}/delete").status_code, 404)  # 남의 주소 + 남의 key
        self.assertEqual(them.post(f"/s/{g.token}/today/e/{ours.key}/delete").status_code, 404)
        self.assertEqual(them.get(f"{self.base}/schedule?mode=month").status_code, 404)
        self.assertFalse(Event.objects.filter(title="넘어가기").exists())
        self.assertEqual(Event.objects.filter(pk__in=[theirs.pk, ours.pk]).count(), 2)

    def test_without_js_cell_post_goes_back_to_same_mode(self):
        r = self.as_(self.t4).post(f"{self.base}/today/events", {"view": "week", "mode": "month", "w": "2026-10-01", "line": "폼으로",
                                                                 "date": "2026-10-27", "slot": "", "scope": ME})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(Event.objects.get(title="폼으로").scope, ME)
        self.assertIn("2026-10-01", r["Location"])


class ExistingScopeTests(Base):
    """그룹 일정의 공개 범위는 대화상자 순서가 바뀌어도 그대로."""

    def dialog_select(self, html, e):
        dlg = html[html.index(f'id="ev-{e.key}"'):]
        i = dlg.index('name="scope"')
        return dlg[i:dlg.index("</select>", i)]

    def test_every_mode_selects_stored_group_and_order(self):
        """QC 2026-11-06: 볼 사람은 학교 전체 · 내 그룹 · 개인(나만) 셋."""
        e = self.ev(self.t5, "오학년 일", "g5")
        for mode in ("day", "week", "month"):
            sel = self.dialog_select(self.page(self.t5, mode, tab="group"), e)
            self.assertIn('<option value="g5" selected>5학년</option>', sel, mode)
            self.assertEqual(re.findall(r'value="(\w+)"', sel), ["all", "g5", "me"], mode)
            self.assertEqual(sel.count(" selected"), 1, mode)
            self.assertNotIn("우리 소속", sel, mode)

    def test_manager_saving_dialog_as_rendered_keeps_scope(self):
        e = self.ev(self.t5, "오학년 일", "g5", "b1")
        r = self.as_(self.boss).post(f"{self.base}/today/e/{e.key}", {"view": "week", "date": TODAY.isoformat(), "slot": "b2", "place": "", "scope": "g5"}, headers=HX)
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual((e.scope, e.slot, e.by_id), ("g5", "b2", self.t5.pk))
        self.assertNotContains(self.as_(self.t4).get(f"{self.base}/schedule?mode=week"), "오학년 일")

    def test_edit_without_scope_field_keeps_scope(self):
        e = self.ev(self.t4, "사학년 일", "g4", "b1")
        self.as_(self.t4).post(f"{self.base}/today/e/{e.key}", {"view": "week", "date": TODAY.isoformat(), "slot": "b3", "place": ""}, headers=HX)
        e.refresh_from_db()
        self.assertEqual((e.scope, e.slot), ("g4", "b3"))

    def test_group_event_stays_in_group_tab(self):
        self.ev(self.t4, "사학년 일", "g4", "b1")
        for mode in ("day", "week", "month"):
            self.assertNotIn("사학년 일", grid_of(self.page(self.t4, mode, tab="me")), mode)
            self.assertNotIn("사학년 일", grid_of(self.page(self.t4, mode)), mode)
            self.assertIn("사학년 일", grid_of(self.page(self.t4, mode, tab="group")), mode)
        self.assertIn("사학년 일", rows_of(self.page(self.t4, "day", tab="group"))["1블록"])

    def test_no_our_group_wording_on_other_scope_pickers(self):
        c = self.as_(self.t4)
        for path in ("/today", "/schedule/paste?w=2026-10-26"):
            self.assertNotContains(c.get(self.base + path), "우리 소속", msg_prefix=path)


class SourceRowTests(Base):
    """종류별로 정해진 줄에만, 다른 채널에 새지 않는다."""

    def fill(self):
        self.ev(self.t4, "학교일", ALL, "b1")
        self.ev(self.t4, "그룹일", "g4", "b3")
        self.ev(self.t4, "혼자일", ME, "pm")
        Attendance.objects.create(space=self.hub, member=self.t5, date=TODAY, name="오일", kind="출장", by=self.t5)
        c = Collection.objects.create(space=self.hub, created_by=self.t4, title="낼표", source=Collection.Source.SHEET, unit_head=["학년반"],
                                      due=datetime(2026, 10, 29, 16, 0, tzinfo=KST))
        Field.objects.create(collection=c, pos=0, name="칸", kind="number")
        Row.objects.create(collection=c, pos=0, unit="4-1", who="사일", member=self.t4)

    def month_row(self, grid, ch):
        return "".join(re.findall(rf'<div class="mc[^"]*" data-ch="{ch}"[^>]*>.*?(?=<div class="(?:mc|ml|mdh|ml-h)[ "])', grid, re.S))

    def test_week_each_kind_only_in_own_row(self):
        self.fill()
        rows = rows_of(self.page(self.t4, "week"))
        self.assertNotIn("그룹일", "".join(rows.values()))  # 그룹·개인 일정은 학교 탭에 없다(QC 2026-11-06)
        self.assertNotIn("혼자일", "".join(rows.values()))
        want = {"학교": ("학교일", "독서주간"), "복무": ("오일 출장",), "취합": ("낼표",)}
        for k, words in want.items():
            for w in words:
                self.assertIn(w, rows[k], k)
            for other, ws in want.items():
                if other != k:
                    for w in ws:
                        self.assertNotIn(w, rows[k], f"{w}가 {k} 줄에")

    def test_month_each_kind_only_in_own_row(self):
        self.fill()
        grid = grid_of(self.page(self.t4, "month"))
        self.assertNotIn("그룹일", grid)
        self.assertNotIn("혼자일", grid)
        want = {"school": ("학교일", "독서주간"), "duty": ("오일 출장",), "collect": ("낼표",)}
        for ch, words in want.items():
            html = self.month_row(grid, ch)
            for w in words:
                self.assertIn(w, html, ch)
            for other, ws in want.items():
                if other != ch:
                    for w in ws:
                        self.assertNotIn(w, html, f"{w}가 {ch} 줄에")

    def test_day_pm_due_and_group_event(self):
        self.fill()
        rows = rows_of(self.page(self.t4, "day"))  # 학교 탭 일간도 채널 줄(QC 2026-11-06)
        self.assertIn("낼표", rows["취합"])
        self.assertIn("오일 출장", rows["복무"])
        self.assertIn("그룹일", rows_of(self.page(self.t4, "day", tab="group"))["3블록"])
        self.assertIn("혼자일", rows_of(self.page(self.t4, "day", tab="me"))["방과후"])

    def test_personal_range_event_on_each_day_and_skips_weekend_in_month(self):
        e = self.ev(self.t4, "혼자 출장", ME, d=date(2026, 11, 6), end=date(2026, 11, 9))
        grid = grid_of(self.page(self.t4, "month", tab="me", w="2026-11-01"))
        self.assertEqual(grid.count(f'data-open="#ev-{e.key}"'), 2)
        for w, days in (("2026-11-06", 1), ("2026-11-09", 1)):
            rows = rows_of(self.page(self.t4, "day", tab="me", w=w))
            self.assertEqual(rows["종일"].count(f'data-open="#ev-{e.key}"'), days, w)

    def test_range_event_starting_on_weekend_shows_on_start_day(self):
        e = self.ev(self.t4, "주말 시작", ALL, d=date(2026, 11, 7), end=date(2026, 11, 9))  # 토~월
        grid = grid_of(self.page(self.t4, "month", w="2026-11-01"))
        self.assertEqual(grid.count(f'data-open="#ev-{e.key}"'), 2)  # 토(시작)·월, 일 뺌

    def test_month_outside_day_item_is_kept(self):
        e = self.ev(self.t4, "달 밖 일", ALL, d=date(2026, 10, 2))
        grid = grid_of(self.page(self.t4, "month", w="2026-09-01"))  # 9월 달력 끝 주의 10/2
        self.assertIn(f'data-open="#ev-{e.key}"', grid)
        self.assertRegex(grid, r'class="mdh out[^"]*"><span class="md num">2</span>')

    def test_week_block_range_card_order_in_cell_uses_start_block(self):
        self.ev(self.t4, "뒤 일", ALL, "pm")
        self.ev(self.t4, "앞 일", ALL, "b1", slot_end="b2")
        row = rows_of(self.page(self.t4, "week"))["학교"]
        self.assertLess(row.index("앞 일"), row.index("뒤 일"))


class QueryCountTests(Base):
    """겹침·바뀐 것·개인·복무·마감이 다 있는 상태로 건수를 늘려도 쿼리가 늘지 않는다."""

    def seed(self, tag, m):
        self.ev(m, f"{tag} 학교", ALL, "b1", place="강당", changed_from=[["장소", "옛", "강당"]], changed_by=self.boss)
        self.ev(m, f"{tag} 개인", ME, "b1", place="강당")
        self.ev(m, f"{tag} 그룹", m.group, "b2", place="음악실", target="대상")
        Attendance.objects.create(space=self.hub, member=m, date=TODAY, name=m.name, kind="출장", by=m)
        c = Collection.objects.create(space=self.hub, created_by=m, title=f"{tag} 표", source=Collection.Source.SHEET, unit_head=["학년반"],
                                      due=datetime(2026, 10, 29, 11, 0, tzinfo=KST))
        Field.objects.create(collection=c, pos=0, name="칸", kind="number")
        Row.objects.create(collection=c, pos=0, unit=self.t4.cls, who=self.t4.name, member=self.t4)

    def count(self, viewer, mode):
        c = self.as_(viewer)
        with CaptureQueriesContext(connection) as q:
            self.assertEqual(c.get(f"{self.base}/schedule", {"mode": mode}).status_code, 200)
        return len(q)

    def test_all_modes_all_viewers(self):
        self.seed("처음", self.t5)
        self.seed("내것", self.t4)
        before = {(v.name, mode): self.count(v, mode) for v in (self.t4, self.boss) for mode in ("day", "week", "month")}
        for i in range(10):
            self.seed(f"추가{i}", self.member(f"교사{i}", f"3-{i}", "g3"))
        for v in (self.t4, self.boss):
            for mode in ("day", "week", "month"):
                self.assertEqual(self.count(v, mode), before[(v.name, mode)], f"{v.name} {mode}")


class GradeSpaceEdgeTests(TestCase):
    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]),
                  mock.patch("spaces.neis.schedule", return_value=SCHED)):
            p.start()
            self.addCleanup(p.stop)
        s = School.objects.create(neis_atpt="J10", neis_code="7531000", name="동학년초")
        self.g = Space.objects.create(school=s, grade=4, name="4학년")
        self.me = Member.objects.create(space=self.g, user=User.objects.create(username="google:가"), name="가", cls="4-1")
        self.peer = Member.objects.create(space=self.g, user=User.objects.create(username="google:나"), name="나", cls="4-2")
        self.client.force_login(self.me.user)
        self.base = f"/s/{self.g.token}"

    def page(self, mode):
        return self.client.get(f"{self.base}/schedule", {"mode": mode}).content.decode()

    def test_every_mode_has_only_two_channels_and_no_group_filter(self):
        for mode in ("day", "week", "month"):
            html = self.page(mode)
            self.assertEqual(re.findall(r'data-flt="ch" value="(\w+)"', html), ["school", "collect"], mode)
            self.assertNotIn('data-flt="grp"', html, mode)
            self.assertNotIn("data-group", html, mode)
            self.assertNotIn('data-scope="me"', html, mode)
            self.assertNotIn('data-ch="duty"', html, mode)
            self.assertNotIn('data-ch="me"', html, mode)
            self.assertNotIn(">복무<", html, mode)
            self.assertNotIn(">개인<", html, mode)
            self.assertNotIn('name="scope"', html, mode)

    def test_scope_me_post_is_ignored_in_every_mode(self):
        for mode in ("day", "week", "month"):
            self.client.post(f"{self.base}/today/events", {"view": "week", "mode": mode, "line": f"{mode} 회의", "date": "2026-10-27", "slot": "", "scope": ME}, headers=HX)
            self.assertEqual(Event.objects.get(title=f"{mode} 회의").scope, ALL)

    def test_attendance_row_in_grade_space_is_not_shown(self):
        Attendance.objects.create(space=self.g, member=self.peer, date=TODAY, name="나", kind="출장", by=self.peer)
        for mode in ("day", "week", "month"):
            self.assertNotIn("나 출장", self.page(mode), mode)

    def test_month_two_rows_per_week_and_due_in_collect_row(self):
        c = Collection.objects.create(space=self.g, created_by=self.me, title="학년표", source=Collection.Source.SHEET, unit_head=["학년반"],
                                      due=datetime(2026, 10, 29, 11, 0, tzinfo=KST))
        Field.objects.create(collection=c, pos=0, name="칸", kind="number")
        grid = grid_of(self.page("month"))
        self.assertEqual(re.findall(r'<div class="ml"[^>]*>([^<]+)</div>', grid), ["동학년", "취합"] * 5)
        self.assertRegex(grid, r'data-ch="collect"[^>]*>(?:(?!data-ch="school").)*학년표')

    def test_queries_flat(self):
        def count(mode):
            with CaptureQueriesContext(connection) as q:
                self.page(mode)
            return len(q)
        Event.objects.create(space=self.g, by=self.peer, date=TODAY, title="처음", slot="b1", place="강당")
        before = {m: count(m) for m in ("day", "week", "month")}
        for i in range(10):
            Event.objects.create(space=self.g, by=self.peer, date=TODAY + timedelta(days=i % 2), title=f"일{i}", slot="b1", place="강당")
        for m in before:
            self.assertEqual(count(m), before[m], m)


class WordingFileTests(TestCase):
    def test_old_wording_not_in_product_files(self):
        """템플릿·정적 JS·파이썬·확정 디자인(design/) 어디에도 없다(테스트·옛 문구를 고치는 마이그레이션 제외). QC 2026-11-06에 design/도 포함."""
        roots = [Path(settings.BASE_DIR), Path(settings.BASE_DIR).parent / "web" / "static", Path(settings.BASE_DIR).parent / "design"]
        hits = []
        for root in roots:
            for p in root.rglob("*"):
                if (p.suffix in (".py", ".html", ".js", ".txt") and p.is_file() and not p.name.startswith("test") and "migrations" not in p.parts
                        and "시간 미정" in p.read_text("utf-8", "ignore")):
                    hits.append(str(p))
        self.assertEqual(hits, [])
