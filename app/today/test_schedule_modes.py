"""Q1·Q2·Q3 — 오늘 일과의 더보기, 일정 칸 안 추가, 일간·주간·월간(meeting/33_qc_r2.md).

불변식
- 격리: 세 모드 모두 reachable_q, 다른 공간은 404. 칸 추가도 공간 안에서만.
- 칸이 날짜·블록을 이긴다. 장소·대상·제목은 한 줄 파서 결과.
- 저장·고치기·지우기 뒤 지금 모드·지금 날짜를 그대로 다시 그린다.
- 마지막 모드는 쿠키(gt_sched_mode, 1년, HttpOnly). 칸 추가는 schedule.js(인라인 금지).
"""
import re
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from collect.models import Collection, Field, Row
from spaces.models import Attendance, Member, School, Space

from .models import ALL, ME, Event

User = get_user_model()
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 29, 11, 5, tzinfo=KST)  # 목요일
TODAY = NOW.date()
MON = date(2026, 10, 26)
SCHED = [{"ymd": "20261029", "name": "독서주간 골든벨"}]


class Base(TestCase):
    def setUp(self):
        p = [mock.patch("django.utils.timezone.now", return_value=NOW),
             mock.patch("spaces.neis.meals", return_value=[]),
             mock.patch("spaces.neis.schedule", return_value=SCHED)]
        self.now, _, self.sched = (x.start() for x in p)
        for x in p:
            self.addCleanup(x.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7530000", name="고티처초")
        self.a = Space.objects.create(school=school, grade=4, name="4학년")
        self.b = Space.objects.create(school=school, grade=5, name="5학년")
        self.ua, ub = (User.objects.create(username=f"google:m{n}") for n in "ab")
        self.me = Member.objects.create(space=self.a, name="정하은", cls="4-2", user=self.ua)
        self.mb = Member.objects.create(space=self.b, name="박다온", cls="5-1", user=ub)
        self.client.force_login(self.ua)

    def url(self, space=None):
        return f"/s/{(space or self.a).token}/schedule"

    def add(self, line, date_="2026-10-27", slot="b1", mode="week", w="2026-10-26", space=None, client=None, **kw):
        return (client or self.client).post(
            f"/s/{(space or self.a).token}/today/events",
            {"view": "week", "mode": mode, "w": w, "line": line, "date": date_, "slot": slot, **kw},
            headers={"HX-Request": "true"})


class MoreLinkTests(Base):
    def test_today_card_links_to_schedule(self):
        html = self.client.get(f"/s/{self.a.token}/today").content.decode()
        head = html[html.index('id="dayH"'):]
        head = head[:head.index('class="day"')]
        self.assertIn(f'<a class="tlink" href="{self.url()}">더보기</a>', head)

    def test_link_stays_after_htmx_redraw(self):
        e = Event.objects.create(space=self.a, by=self.me, date=TODAY, title="지울 일")
        r = self.client.post(f"/s/{self.a.token}/today/e/{e.key}/delete", headers={"HX-Request": "true"})
        self.assertContains(r, ">더보기</a>")


class CellAddTests(Base):
    def test_line_input_removed_and_card_template_present(self):
        html = self.client.get(self.url()).content.decode()
        self.assertNotIn('id="addForm"', html)
        self.assertNotIn('id="parsed"', html)
        self.assertEqual(html.count('name="line"'), 1)
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertIn('name="line"', tpl)
        self.assertIn(f'hx-post="/s/{self.a.token}/today/events"', tpl)
        self.assertIn("csrfmiddlewaretoken", tpl)
        self.assertNotIn('name="scope"', tpl)  # 동학년은 볼 사람이 없다

    def test_no_inline_script_on_any_mode(self):
        for mode in ("day", "week", "month"):
            html = self.client.get(self.url(), {"mode": mode}).content.decode()
            for tag in re.findall(r"<script[^>]*>", html):
                self.assertIn("src=", tag)
            self.assertNotRegex(html, r"\son[a-z]+=")

    def test_every_cell_has_a_real_add_button_even_when_full(self):
        for i in range(6):
            Event.objects.create(space=self.a, by=self.me, date=MON + timedelta(days=1), slot="b1", title=f"꽉 찬 일 {i}")
        html = self.client.get(self.url(), {"mode": "week"}).content.decode()
        self.assertEqual(html.count('class="cell-add"'), 5)  # 5일 × 일정 줄(취합 줄에는 없다)
        self.assertIn('aria-label="10/27(화) 동학년에 일정 추가"', html)
        day = self.client.get(self.url(), {"mode": "day", "w": "2026-10-27"}).content.decode()
        self.assertEqual(day.count('class="cell-add"'), 5)  # 종일 + 블록 4
        self.assertIn('aria-label="10/27(화) 1블록에 일정 추가"', day)
        self.assertIn('aria-label="10/27(화) 종일에 일정 추가"', day)
        # 꽉 찬 칸에서도 단추는 일정 뒤에 있다
        cell = html[html.index('data-date="2026-10-27" data-slot=""'):]
        cell = cell[:cell.index('data-date="2026-10-28"')]
        self.assertLess(cell.rindex("꽉 찬 일 5"), cell.index('class="cell-add"'))
        self.assertRegex(html, r'<button type="button" class="cell-add" aria-label=')

    def test_cell_beats_parsed_date_and_slot(self):
        r = self.add("11/20 3블록 과학실 4학년 학예회 리허설")
        self.assertEqual(r.status_code, 200)
        e = Event.objects.get()
        self.assertEqual((e.date, e.end, e.slot, e.slot_end), (date(2026, 10, 27), None, "b1", "b1"))
        self.assertEqual(e.place, "과학실")
        self.assertEqual(e.target, "4학년")
        self.assertEqual(e.by, self.me)
        self.assertEqual(e.title, "3블록 과학실 4학년 학예회 리허설")  # 제목은 파서 결과(쓴 문장에서 날짜만 뺀다)
        html = r.content.decode()
        self.assertIn('id="week"', html)
        self.assertIn("리허설", html)
        self.assertIn("data-toast-now", html)
        self.assertIn(f'data-open="#ev-{e.key}"', html)

    def test_cell_drops_parsed_range(self):
        self.add("10/28~10/30 독서 기간")
        self.assertIsNone(Event.objects.get().end)

    def test_cell_without_slot_keeps_parsed_block(self):
        self.add("2블록 회의", slot="")
        e = Event.objects.get()
        self.assertEqual((e.slot, e.slot_end), ("b2", "b2"))
        self.add("그냥 회의", slot="")
        self.assertEqual(Event.objects.get(title="그냥 회의").slot, "")

    def test_conflict_notice_kept(self):
        Event.objects.create(space=self.a, by=self.me, date=date(2026, 10, 27), slot="b1", place="과학실", title="안전교육")
        self.assertContains(self.add("과학실 실험"), "겹쳐요")

    def test_bad_cell_is_rejected(self):
        for d, s in (("2026-02-30", "b1"), ("x", "b1"), ("", "b1"), ("2026-10-27", "b9")):
            self.assertEqual(self.add("회의", date_=d, slot=s).status_code, 400, (d, s))
        self.assertFalse(Event.objects.exists())

    def test_error_stays_in_card(self):
        r = self.add("내일")
        self.assertEqual(r["HX-Retarget"], "#cellErr")
        self.assertEqual(r["HX-Reswap"], "outerHTML")
        self.assertContains(r, 'id="cellErr"')
        self.assertContains(r, "일정 이름도 함께")
        self.assertFalse(Event.objects.exists())

    def test_other_space_is_404(self):
        self.assertEqual(self.add("몰래", space=self.b).status_code, 404)
        self.assertFalse(Event.objects.exists())

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        self.assertEqual(self.add("회의", client=c).status_code, 403)

    def test_today_screen_ignores_cell_fields(self):
        """오늘 화면 한 줄 입력은 그대로: date·slot을 보내도 파서 결과를 쓰고 오늘 판을 돌려준다."""
        r = self.client.post(f"/s/{self.a.token}/today/events", {"line": "10/30 2블록 회의", "date": "2026-11-20", "slot": "pm"},
                             headers={"HX-Request": "true"})
        e = Event.objects.get()
        self.assertEqual((e.date, e.slot), (date(2026, 10, 30), "b2"))
        self.assertContains(r, 'id="live"')

    def test_today_screen_line_input_stays(self):
        self.assertContains(self.client.get(f"/s/{self.a.token}/today"), 'name="line"')

    def test_title_escaped_in_board(self):
        html = self.add("<script>alert(1)</script>").content.decode()
        self.assertNotIn("<script>alert", html)


class ModeTests(Base):
    def setUp(self):
        super().setUp()
        self.ev = Event.objects.create(space=self.a, by=self.me, date=TODAY, slot="b2", title="수업 공개", place="4-2 교실")

    def test_default_is_week(self):
        html = self.client.get(self.url()).content.decode()
        self.assertIn('data-mode="week"', html)
        self.assertIn("이번 주 일정", html)
        self.assertRegex(html, r'<button name="mode" value="week" aria-pressed="true">주간</button>')

    def test_day_mode(self):
        r = self.client.get(self.url(), {"mode": "day"})
        html = r.content.decode()
        self.assertIn('data-mode="day"', html)
        self.assertIn("오늘 일정", html)
        self.assertIn("10월 29일 목요일", html)
        self.assertIn("수업 공개", html)
        self.assertIn('wgrid one', html)
        self.assertEqual(html.count('class="cell-add"'), 5)
        self.assertIn(f'data-date="{TODAY}" data-slot="b2"', html)
        self.assertEqual([*self.sched.call_args[0][2:]], ["20261029", "20261029"])
        for w in ("2026-10-28", "2026-10-30"):
            self.assertContains(r, f'name="w" value="{w}"')
        self.assertContains(self.client.get(self.url(), {"mode": "day", "w": "2026-10-30"}), "내일 일정")
        self.assertContains(self.client.get(self.url(), {"mode": "day", "w": "2026-10-28"}), "어제 일정")
        self.assertContains(self.client.get(self.url(), {"mode": "day", "w": "2026-11-20"}), "11월 20일 금요일 일정")

    def test_day_mode_shows_weekend(self):
        Event.objects.create(space=self.a, by=self.me, date=date(2026, 10, 31), title="토요 행사")
        self.assertContains(self.client.get(self.url(), {"mode": "day", "w": "2026-10-31"}), "토요 행사")

    def test_month_mode(self):
        Event.objects.create(space=self.a, by=self.me, date=date(2026, 10, 31), title="토요 행사")
        Event.objects.create(space=self.a, by=self.me, date=date(2026, 10, 5), slot="pm", title="공백없는아주아주아주긴제목" * 3)
        r = self.client.get(self.url(), {"mode": "month"})
        html = r.content.decode()
        self.assertIn('data-mode="month"', html)
        self.assertIn("이번 달 일정", html)
        self.assertIn('class="mgrid"', html)
        # 2026년 10월: 9/27(일)부터 10/31(토)까지 5주, 일~토(prototype)
        self.assertEqual(html.count('class="cell-add"'), 35)
        heads = re.findall(r'<div class="mh[^"]*">(.)</div>', html)
        self.assertEqual("".join(heads), "일월화수목금토")
        self.assertIn('data-date="2026-09-27" data-slot=""', html)
        self.assertIn('data-date="2026-10-31" data-slot=""', html)
        self.assertIn('aria-label="10/31(토) 동학년에 일정 추가"', html)
        self.assertIn("토요 행사", html)
        self.assertIn("공백없는아주아주아주긴제목" * 3, html)  # 자르지 않는다
        self.assertEqual([*self.sched.call_args[0][2:]], ["20260927", "20261031"])
        self.assertContains(r, 'name="w" value="2026-09-01"')
        self.assertContains(r, 'name="w" value="2026-11-01"')
        self.assertContains(self.client.get(self.url(), {"mode": "month", "w": "2026-11-15"}), "다음 달 일정")
        self.assertContains(self.client.get(self.url(), {"mode": "month", "w": "2027-03-01"}), "2027년 3월 일정")

    def test_month_range_event_skips_following_weekend(self):
        e = Event.objects.create(space=self.a, by=self.me, date=date(2026, 11, 6), end=date(2026, 11, 9), title="긴 행사")
        html = self.client.get(self.url(), {"mode": "month", "w": "2026-11-01"}).content.decode()
        # 금·월은 칸에 나오고 토·일은 건너뛴다. 대화상자는 하나
        self.assertEqual(html.count(f'data-open="#ev-{e.key}"'), 2)
        self.assertEqual(html.count(f'id="ev-{e.key}"'), 1)

    def test_all_sources_in_every_mode(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=date(2099, 1, 1))
        hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        u = User.objects.create(username="google:boss", first_name="부장")
        boss = Member.objects.create(space=hub, user=u, name="부장", cls="교무실", group="office", is_manager=True)
        Event.objects.create(space=hub, by=boss, date=TODAY, slot="b1", title="학교 일정", scope=ALL)
        Attendance.objects.create(space=hub, member=boss, date=TODAY, name="부장", kind="출장", by=boss)
        c = Collection.objects.create(space=hub, created_by=boss, title="걷을 표", source=Collection.Source.SHEET,
                                      unit_head=["학년반"], due=NOW.replace(hour=15, minute=0))
        Field.objects.create(collection=c, pos=0, name="칸", kind="number")
        Row.objects.create(collection=c, pos=0, unit="교무실", who="부장", member=boss)
        cl = Client()
        cl.force_login(u)
        for mode in ("day", "week", "month"):
            html = cl.get(f"/s/{hub.token}/schedule", {"mode": mode}).content.decode()
            self.assertIn("학교 일정", html, mode)
            self.assertIn("부장 출장", html, mode)
            self.assertIn("걷을 표", html, mode)
            self.assertIn("독서주간 골든벨", html, mode)

    def test_print_and_paste_buttons_follow_mode(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7532000", name="한솔초", plan="school", paid_until=date(2099, 1, 1))
        hub = Space.objects.create(school=school, kind="school", grade=0, name="한솔초")
        u = User.objects.create(username="google:t1", first_name="교사")
        Member.objects.create(space=hub, user=u, name="교사", cls="4-1", group="g4")
        cl = Client()
        cl.force_login(u)
        base = f"/s/{hub.token}/schedule"
        day = cl.get(base, {"mode": "day"}).content.decode()
        self.assertIn(f"{base}/print?view=week&amp;d=2026-10-29", day)
        self.assertIn(f"{base}/paste?w=2026-10-29", day)
        month = cl.get(base, {"mode": "month", "w": "2026-11-15"}).content.decode()
        self.assertIn(f"{base}/print?view=month&amp;d=2026-11-01", month)
        self.assertIn(f"{base}/paste?w=2026-11-02", month)  # 11/1은 일요일 → 그 달 첫 평일

    # ---------- 쿠키 ----------
    def test_cookie_remembers_mode(self):
        r = self.client.get(self.url(), {"mode": "month"})
        c = r.cookies["gt_sched_mode"]
        self.assertEqual(c.value, "month")
        self.assertEqual(c["max-age"], 365 * 86400)
        self.assertTrue(c["httponly"])
        self.assertEqual(c["samesite"], "Lax")
        again = self.client.get(self.url())
        self.assertContains(again, 'data-mode="month"')
        self.assertNotIn("gt_sched_mode", again.cookies)
        self.assertContains(self.client.get(self.url(), {"mode": "day"}), 'data-mode="day"')
        self.assertContains(self.client.get(self.url()), 'data-mode="day"')

    def test_bad_mode_and_bad_date(self):
        r = self.client.get(self.url(), {"mode": "<x>"})
        self.assertContains(r, 'data-mode="week"')
        self.assertNotIn("gt_sched_mode", r.cookies)
        self.client.cookies["gt_sched_mode"] = "<x>"
        self.assertContains(self.client.get(self.url()), 'data-mode="week"')
        for mode in ("day", "week", "month"):
            for bad in ("x", "2026-02-30", "99999-01-01", "1800-01-01"):
                self.assertEqual(self.client.get(self.url(), {"mode": mode, "w": bad}).status_code, 200)

    def test_mode_switch_keeps_place(self):
        """지금 보는 곳을 넘긴다: 다른 주를 보다 일간으로 가면 그 주 월요일, 이번 주면 오늘."""
        html = self.client.get(self.url(), {"mode": "week"}).content.decode()
        self.assertIn('<input type="hidden" name="w" value="2026-10-29">', html)
        html = self.client.get(self.url(), {"mode": "week", "w": "2026-11-11"}).content.decode()
        self.assertIn('<input type="hidden" name="w" value="2026-11-09">', html)
        html = self.client.get(self.url(), {"mode": "month", "w": "2026-11-20"}).content.decode()
        self.assertIn('<input type="hidden" name="w" value="2026-11-02">', html)

    # ---------- 쓰기 뒤 같은 모드·같은 날짜 ----------
    def test_add_redraws_same_mode_and_date(self):
        r = self.add("칸에서 쓴 일", date_="2026-11-20", slot="", mode="month", w="2026-11-01")
        html = r.content.decode()
        self.assertIn('data-mode="month"', html)
        self.assertIn('data-w="2026-11-01"', html)
        self.assertIn("칸에서 쓴 일", html)
        self.assertEqual(Event.objects.get(title="칸에서 쓴 일").slot, "")
        r = self.add("하루 칸", date_="2026-11-20", slot="pm", mode="day", w="2026-11-20")
        html = r.content.decode()
        self.assertIn('data-mode="day"', html)
        self.assertIn('data-w="2026-11-20"', html)
        self.assertIn("하루 칸", html)

    def test_dialog_form_carries_mode_and_date(self):
        for mode, w in (("day", "2026-10-29"), ("week", "2026-10-26"), ("month", "2026-10-01")):
            html = self.client.get(self.url(), {"mode": mode, "w": w}).content.decode()
            dlg = html[html.index(f'id="ev-{self.ev.key}"'):]
            dlg = dlg[:dlg.index("</form>")]
            self.assertIn('name="view" value="week"', dlg, mode)
            self.assertIn(f'name="mode" value="{mode}"', dlg)
            self.assertIn(f'name="w" value="{w}"', dlg)

    def test_edit_and_delete_redraw_same_mode(self):
        data = {"view": "week", "mode": "month", "w": "2026-10-01", "date": "2026-10-30", "slot": "b3", "place": "체육관"}
        r = self.client.post(f"/s/{self.a.token}/today/e/{self.ev.key}", data, headers={"HX-Request": "true"})
        self.assertContains(r, 'data-mode="month"')
        self.assertContains(r, "체육관")
        r = self.client.post(f"/s/{self.a.token}/today/e/{self.ev.key}/delete", {"view": "week", "mode": "day", "w": "2026-10-30"},
                             headers={"HX-Request": "true"})
        self.assertContains(r, 'data-mode="day"')
        self.assertContains(r, 'data-w="2026-10-30"')
        self.assertContains(r, "지웠어요")

    def test_add_without_htmx_returns_to_same_mode(self):
        r = self.client.post(f"/s/{self.a.token}/today/events",
                             {"view": "week", "mode": "month", "w": "2026-11-15", "line": "회의", "date": "2026-11-20", "slot": ""})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r["Location"], self.url() + "?w=2026-11-01&mode=month")

    def test_lists_are_limited_to_shown_period(self):
        Event.objects.create(space=self.a, by=self.me, date=date(2026, 11, 3), title="다음 달 바뀐 일", changed_from=[["장소", "강당", "체육관"]])
        Event.objects.create(space=self.a, by=self.me, date=TODAY, title="오늘 바뀐 일", changed_from=[["장소", "강당", "체육관"]])
        html = self.client.get(self.url(), {"mode": "month"}).content.decode()  # 11/1은 10월 판 격자 밖
        lst = html[html.index('id="chg"'):]
        self.assertIn("오늘 바뀐 일", lst)
        self.assertNotIn("다음 달 바뀐 일", lst)
        self.assertIn("이 달 바뀐 것", html)
        self.assertIn("이 달 마감", html)
        self.assertIn("이 날 바뀐 것", self.client.get(self.url(), {"mode": "day"}).content.decode())

    def test_neis_empty_note_per_mode(self):
        self.sched.return_value = []
        self.assertContains(self.client.get(self.url(), {"mode": "day"}), "이 날 학사일정이 없어요.")
        self.assertContains(self.client.get(self.url(), {"mode": "month"}), "이 달 학사일정이 없어요.")

    # ---------- 격리 ----------
    def test_other_space_in_every_mode(self):
        Event.objects.create(space=self.b, by=self.mb, date=TODAY, slot="b1", title="옆 학년 비밀")
        for mode in ("day", "week", "month"):
            self.assertNotContains(self.client.get(self.url(), {"mode": mode}), "옆 학년 비밀")
            self.assertEqual(self.client.get(self.url(self.b), {"mode": mode}).status_code, 404)

    def test_reachable_q_in_every_mode(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7533000", name="다솔초", plan="school", paid_until=date(2099, 1, 1))
        hub = Space.objects.create(school=school, kind="school", grade=0, name="다솔초")
        seats = [Member.objects.create(space=hub, user=User.objects.create(username=f"google:{n}", first_name=n), name=n, cls=c, group=g)
                 for n, c, g in (("사일", "4-1", "g4"), ("오일", "5-1", "g5"))]
        t4, t5 = seats
        Event.objects.create(space=hub, by=t4, date=TODAY, slot="b1", title="사학년만", scope="g4")
        Event.objects.create(space=hub, by=t4, date=TODAY, slot="b1", title="사일 혼자", scope=ME)
        Event.objects.create(space=hub, by=t4, date=TODAY, slot="b1", title="모두 보는 일", scope=ALL)
        cl = Client()
        cl.force_login(t5.user)
        for mode in ("day", "week", "month"):
            html = cl.get(f"/s/{hub.token}/schedule", {"mode": mode}).content.decode()
            self.assertIn("모두 보는 일", html, mode)
            self.assertNotIn("사학년만", html, mode)
            self.assertNotIn("사일 혼자", html, mode)


class SchoolCardTests(TestCase):
    def setUp(self):
        p = [mock.patch("django.utils.timezone.now", return_value=NOW),
             mock.patch("spaces.neis.schedule", return_value=[])]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7534000", name="가람초", plan="school", paid_until=date(2099, 1, 1))
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="가람초")
        self.u = User.objects.create(username="google:s1", first_name="사일")
        self.m = Member.objects.create(space=self.hub, user=self.u, name="사일", cls="4-1", group="g4")
        self.client.force_login(self.u)

    def test_card_scope_is_hidden_and_filled_from_cell(self):
        html = self.client.get(f"/s/{self.hub.token}/schedule").content.decode()
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertIn('<input type="hidden" name="scope">', tpl)
        self.assertNotIn("<select", tpl)

    def test_scope_saved(self):
        r = self.client.post(f"/s/{self.hub.token}/today/events",
                             {"view": "week", "mode": "week", "w": "2026-10-26", "line": "나만 볼 일", "date": "2026-10-27", "slot": "b1", "scope": ME},
                             headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Event.objects.get().scope, ME)
