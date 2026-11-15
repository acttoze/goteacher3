"""QA(2026-11-15) 오늘 화면 오늘 일과 판 — 구현 테스트(test_today_day_board)에 없는 경계만.
기관(그룹 줄·개인, 끈 줄, 남의 개인), 동학년(한 줄), 소속 없는 학교 회원, 기간 일정, 칸 안 순서, 지우기·복무 지우기 → #live,
htmx 없는 저장, 격리, 일정 화면 대화상자는 그대로 #week."""
import re
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from spaces.models import Member, School, Space

from .models import ALL, ME, Event
from .test_schedule_channels import HX, NOW, TODAY, Base
from .test_today_day_board import board, rows

User = get_user_model()


def client(m):
    c = Client()
    c.force_login(m.user)
    return c


def today_data(d=TODAY, **kw):
    return {"view": "today", "mode": "day", "w": d.isoformat(), "tab": "school", "date": d.isoformat(), "slot": "", **kw}


class SchoolEdgeTests(Base):
    def today(self, m):
        return self.as_(m).get(f"{self.base}/today").content.decode()

    def test_member_without_group_has_no_group_row(self):
        nog = self.member("무소속", "교무실", "")
        self.assertEqual(list(rows(self.today(nog))), ["학교", "개인", "복무", "점심"])

    def test_multi_day_event_shows_and_yesterday_hidden(self):
        self.ev(self.t4, "기간 행사", d=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
        self.ev(self.t4, "어제 행사", d=TODAY - timedelta(days=1))
        whole = board(self.today(self.t4))
        self.assertIn("기간 행사", rows(self.today(self.t4))["학교"])
        self.assertNotIn("어제 행사", whole)

    def test_cards_in_cell_neis_then_untimed_then_blocks(self):
        self.ev(self.t4, "방과후 연수", slot="pm")
        self.ev(self.t4, "둘째 블록 회의", slot="b2")
        self.ev(self.t4, "종일 행사")
        self.ev(self.t4, "첫째 블록 회의", slot="b1")
        row = rows(self.today(self.t4))["학교"]
        order = [row.index(t) for t in ("독서주간 골든벨", "종일 행사", "첫째 블록 회의", "둘째 블록 회의", "방과후 연수")]
        self.assertEqual(order, sorted(order))

    def test_cell_me_and_all_land_in_their_rows(self):
        self.as_(self.t4).post(f"{self.base}/today/events", today_data(line="나만 메모", scope=ME), headers=HX)
        r = self.as_(self.t4).post(f"{self.base}/today/events", today_data(line="모두 공지", scope=ALL), headers=HX)
        got = rows(r.content.decode())
        self.assertIn("나만 메모", got["개인"])
        self.assertIn("모두 공지", got["학교"])
        self.assertNotIn("나만 메모", board(self.today(self.t5)))

    def test_cell_add_without_htmx_goes_back_to_today(self):
        r = self.as_(self.t4).post(f"{self.base}/today/events", today_data(line="새 일정", scope=ALL))
        self.assertEqual((r.status_code, r["Location"]), (303, f"{self.base}/today"))

    def test_delete_dialog_returns_today_board(self):
        e = self.ev(self.t4, "지울 일정")
        html = self.today(self.t4)
        dlg = html[html.index(f'id="ev-{e.key}"'):]
        dlg = dlg[:dlg.index('class="scrim"') if 'class="scrim"' in dlg else len(dlg)]
        self.assertIn(f'hx-post="/s/{self.hub.token}/today/e/{e.key}/delete" hx-include="#evf-{e.key}" hx-target="#live"', dlg)
        r = self.as_(self.t4).post(f"{self.base}/today/e/{e.key}/delete", {"view": "today"}, headers=HX)
        self.assertIn('<div id="live"', r.content.decode())
        self.assertFalse(Event.objects.filter(pk=e.pk).exists())

    def test_duty_delete_dialog_returns_today_board(self):
        a = self.duty(self.t4)
        html = self.today(self.t4)
        dlg = html[html.index(f'id="duty-{a.key}"'):]
        self.assertIn('hx-target="#live"', dlg[:dlg.index("</form>")])
        self.assertIn('name="view" value="today"', dlg[:dlg.index("</form>")])
        r = self.as_(self.t4).post(f"{self.base}/today/duty/{a.key}/delete", {"view": "today"}, headers=HX)
        html = r.content.decode()
        self.assertIn('<div id="live"', html)
        self.assertNotIn("사일 출장", rows(html)["복무"])

    def test_schedule_page_dialogs_still_target_week(self):
        e = self.ev(self.t4, "일정 화면 대화상자")
        a = self.duty(self.t4)
        html = self.page(self.t4, "week")
        ev = html[html.index(f'id="ev-{e.key}"'):]
        ev = ev[:ev.index("</form>")]
        self.assertIn('hx-target="#week"', ev)
        self.assertIn('name="view" value="week"', ev)
        dy = html[html.index(f'id="duty-{a.key}"'):]
        self.assertIn('hx-target="#week"', dy[:dy.index("</form>")])
        self.assertNotIn('id="live"', html)

    def test_other_space_cannot_post_or_edit_via_today(self):
        other_school = School.objects.create(neis_atpt="J10", neis_code="1", name="남의초", plan="school", paid_until=date(2099, 1, 1))
        other = Space.objects.create(school=other_school, kind="school", grade=0, name="남의초")
        u = User.objects.create(username="google:outsider", first_name="밖")
        out = Member.objects.create(space=other, user=u, name="밖", cls="4-1", group="g4")
        e = self.ev(self.t4, "우리 일정")
        c = client(out)
        r1 = c.post(f"{self.base}/today/events", today_data(line="침입", scope=ALL), headers=HX)
        self.assertIn(r1.status_code, (403, 404))
        self.assertFalse(Event.objects.filter(title="침입").exists())
        # 내 공간 토큰 + 남의 공간 일정 키
        r2 = c.post(f"/s/{other.token}/today/e/{e.key}", {"view": "today", "date": TODAY.isoformat(), "slot": "", "place": "", "scope": "all"}, headers=HX)
        self.assertEqual(r2.status_code, 404)
        r3 = c.post(f"/s/{other.token}/today/e/{e.key}/delete", {"view": "today"}, headers=HX)
        self.assertEqual(r3.status_code, 404)
        self.assertTrue(Event.objects.filter(pk=e.pk).exists())
        self.assertNotIn("우리 일정", c.get(f"/s/{other.token}/today").content.decode())


class OrgTodayBoardTests(TestCase):
    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="E10", neis_code="", name="협력과", plan="school", paid_until=date(2099, 2, 28))
        self.space = Space.objects.create(school=school, kind=Space.Kind.ORG, grade=0, name="협력과",
                                          org_groups=[{"k": "ga", "n": "가팀"}, {"k": "na", "n": "나팀"}])
        mk = lambda n, g: Member.objects.create(space=self.space, user=User.objects.create(username=f"google:{n}", first_name=n),
                                                name=n, cls="미배정", group=g)
        self.kim, self.lee = mk("김", "ga"), mk("이", "na")
        self.base = f"/s/{self.space.token}"

    def ev(self, by, title, scope):
        return Event.objects.create(space=self.space, by=by, date=TODAY, title=title, scope=scope)

    def test_group_rows_and_me_row_no_duty_no_lunch(self):
        self.ev(self.kim, "가팀회의", "ga")
        self.ev(self.kim, "나팀회의", "na")
        self.ev(self.kim, "김혼자", ME)
        html = client(self.lee).get(f"{self.base}/today").content.decode()
        self.assertNotIn('id="quickForm"', html)
        r = rows(html)
        self.assertEqual(list(r), ["가팀", "나팀", "개인"])
        self.assertIn("가팀회의", r["가팀"])
        self.assertIn("나팀회의", r["나팀"])
        self.assertNotIn("김혼자", board(html))

    def test_off_line_hidden_and_cell_add_to_group(self):
        self.lee.lines_off = "ga"
        self.lee.save()
        self.ev(self.kim, "가팀회의", "ga")
        html = client(self.lee).get(f"{self.base}/today").content.decode()
        self.assertEqual(list(rows(html)), ["나팀", "개인"])
        self.assertNotIn("가팀회의", board(html))
        r = client(self.lee).post(f"{self.base}/today/events", today_data(line="나팀 협의", scope="na"), headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertIn("나팀 협의", rows(r.content.decode())["나팀"])


class GradeTodayBoardTests(TestCase):
    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7530000", name="동학년초")
        self.a = Space.objects.create(school=school, grade=4, name="4학년")
        self.b = Space.objects.create(school=school, grade=5, name="5학년")
        self.me = Member.objects.create(space=self.a, name="정", cls="4-2", user=User.objects.create(username="google:ga"))
        self.mb = Member.objects.create(space=self.b, name="박", cls="5-1", user=User.objects.create(username="google:gb"))

    def test_one_row_and_cell_add_returns_live(self):
        Event.objects.create(space=self.b, by=self.mb, date=TODAY, title="오학년 일정")
        c = client(self.me)
        html = c.get(f"/s/{self.a.token}/today").content.decode()
        self.assertNotIn('id="quickForm"', html)
        self.assertEqual(list(rows(html)), [self.a.word, "점심"])
        self.assertNotIn("오학년 일정", html)
        tpl = html[html.index('<template id="cellTpl">'):html.index("</template>")]
        self.assertNotIn('name="scope"', tpl)
        r = c.post(f"/s/{self.a.token}/today/events", today_data(line="학년 협의"), headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertIn("학년 협의", rows(r.content.decode())[self.a.word])
        # 다른 동학년 토큰으로는 못 쓴다
        r2 = c.post(f"/s/{self.b.token}/today/events", today_data(line="침입"), headers=HX)
        self.assertIn(r2.status_code, (403, 404))
        self.assertFalse(Event.objects.filter(title="침입").exists())
