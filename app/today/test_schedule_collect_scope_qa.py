"""QA — QC 일정 취합 범위(2026-11-06). 구현 테스트(test_schedule_channels.CollectScopeTests)에 없는 경계만.

- 그룹 탭 쿼리 수가 소속·부서 취합 건수에 비례하지 않는다.
- 일간·월간 htmx 다시 그리기(쿠키 탭 포함)에서도 다른 소속·관리자에게 제목·key·링크 토큰이 없다.
- 소속 밖에서 만든 사람: 자기 그룹 탭에 없고 마감 목록에만(관리 링크).
- 부서·사람 고르기: 만든 사람·관리자도 일정 탭 어디에도 없다. 관리자에게는 내 것도 내 줄도 아니라 마감 목록에도 없다.
- 동학년 공간은 범위 값과 무관하게 취합 줄에 그대로.
"""
from datetime import datetime

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from collect.models import Collection
from spaces.models import Member, School, Space

from .test_schedule_channels import HX, KST, Base, User, grid_of, rows_of

MODES = ("day", "week", "month")
TABS = ("school", "group", "me")


def due_list(html):
    return html.split('id="due"')[1] if 'id="due"' in html else ""


class GroupTabQueryCountQa(Base):
    def seed(self, i):
        self.due(self.boss, f"소속{i}", aud={"mode": "groups", "groups": ["g4"], "hr": False})
        self.due(self.t5, f"칸{i}", hour=10, aud={"mode": "pergroup", "groups": ["g4", "g5"]})
        self.due(self.boss, f"부서{i}", aud={"mode": "dept", "depts": ["교무부"]}, row_for=self.t4)

    def count(self, m, mode):
        c = self.as_(m)
        with CaptureQueriesContext(connection) as q:
            self.assertEqual(c.get(f"{self.base}/schedule", {"mode": mode, "tab": "group"}).status_code, 200)
        return len(q)

    def test_group_tab_queries_flat(self):
        boss4 = self.member("사관리", "4-부", "g4", is_manager=True)
        self.seed(0)
        before = {(m.name, mode): self.count(m, mode) for m in (self.t4, boss4) for mode in MODES}
        for i in range(1, 9):
            self.seed(i)
        for m in (self.t4, boss4):
            for mode in MODES:
                self.assertIn("소속8", grid_of(self.as_(m).get(f"{self.base}/schedule", {"mode": mode, "tab": "group"}).content.decode()))
                self.assertEqual(self.count(m, mode), before[(m.name, mode)], (m.name, mode))


class HtmxRedrawLeakQa(Base):
    def setUp(self):
        super().setUp()
        self.g = self.due(self.t4, "사학년소속만", aud={"mode": "groups", "groups": ["g4"], "hr": False})
        self.pg = self.due(self.t4, "사학년칸만", hour=15, aud={"mode": "pergroup", "groups": ["g4"]})
        self.secrets = ("사학년소속만", "사학년칸만", self.g.key, self.g.link_token, self.pg.key, self.pg.link_token)

    def redraw(self, m, mode, tab=None, cookie=None):
        c = self.as_(m)
        if cookie:
            c.cookies["gt_sched_tab"] = cookie
        data = {"view": "week", "mode": mode, "w": "2026-10-29", "line": "다시", "date": "2026-10-29", "slot": "", "scope": "me"}
        if tab:
            data["tab"] = tab
        r = c.post(f"{self.base}/today/events", data, headers=HX)
        self.assertEqual(r.status_code, 200, (m.name, mode, tab, cookie))
        return r.content.decode()

    def test_other_group_and_manager_htmx_every_mode_tab(self):
        for m in (self.t5, self.boss):
            for mode in MODES:
                for tab in TABS:
                    for html in (self.redraw(m, mode, tab=tab), self.redraw(m, mode, cookie=tab)):
                        for s in self.secrets:
                            self.assertNotIn(s, html, (m.name, mode, tab, s))

    def test_target_member_htmx_sees_on_group_tab_only(self):
        t4b = self.member("사이", "4-2", "g4")
        for mode in MODES:
            self.assertIn("사학년소속만", grid_of(self.redraw(t4b, mode, tab="group")), mode)
            self.assertIn("사학년칸만", grid_of(self.redraw(t4b, mode, cookie="group")), mode)
            for tab in ("school", "me"):
                g = grid_of(self.redraw(t4b, mode, tab=tab))
                self.assertNotIn("사학년소속만", g, (mode, tab))
                self.assertNotIn("사학년칸만", g, (mode, tab))


class CreatorOutsideGroupQa(Base):
    def test_creator_not_in_chosen_group_sees_only_due_list(self):
        c = self.due(self.t5, "오일이만든사학년", aud={"mode": "groups", "groups": ["g4"], "hr": False}, row_for=self.t4)
        for mode in MODES:
            for tab in TABS:
                html = self.page(self.t5, mode, tab=tab)
                self.assertNotIn("오일이만든사학년", grid_of(html), (mode, tab))
        html = self.page(self.t5, "week", tab="group")
        self.assertIn(f'href="/s/{self.hub.token}/c/{c.key}/"', due_list(html))


class DeptPeopleQa(Base):
    def setUp(self):
        super().setUp()
        self.d = self.due(self.boss, "부서만조사", aud={"mode": "dept", "depts": ["교무부"]}, row_for=self.t4)
        self.p = self.due(self.t5, "사람만조사", aud={"mode": "people", "people": [self.t4.key]}, row_for=self.t4)

    def test_creator_never_in_grid_but_in_due_list_with_manage_link(self):
        for m, c in ((self.boss, self.d), (self.t5, self.p)):
            for mode in MODES:
                for tab in TABS:
                    self.assertNotIn(c.title, grid_of(self.page(m, mode, tab=tab)), (m.name, mode, tab))
            self.assertIn(f'href="/s/{self.hub.token}/c/{c.key}/"', due_list(self.page(m, "week")), m.name)

    def test_row_owner_due_list_links_own_row(self):
        html = self.page(self.t4, "week", tab="group")
        for c in (self.d, self.p):
            self.assertNotIn(c.title, grid_of(html))
            self.assertIn(f"/c/{c.link_token}/u/{c.rows.get().key}", due_list(html))

    def test_other_manager_without_row_sees_nothing(self):
        boss2 = self.member("교감", "교무실2", "office", is_manager=True)
        for mode in MODES:
            for tab in TABS:
                html = self.page(boss2, mode, tab=tab)
                for c in (self.d, self.p):
                    for s in (c.title, c.key, c.link_token):
                        self.assertNotIn(s, html, (mode, tab, s))


class GradeSpaceAudienceQa(Base):
    def test_grade_space_any_audience_in_collect_row(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7537101", name="동학년초3")
        g = Space.objects.create(school=s, grade=4, name="4학년")
        u = User.objects.create(username="google:동3")
        m = Member.objects.create(space=g, user=u, name="동3", cls="4-1")
        for i, aud in enumerate(({"mode": "people", "people": []}, {"mode": "groups", "groups": ["g9"]}, {})):
            Collection.objects.create(space=g, created_by=m, title=f"동학년{i}", source=Collection.Source.SHEET, unit_head=["학년반"],
                                      due=datetime(2026, 10, 29, 11, 0, tzinfo=KST), audience=aud)
        c = Client()
        c.force_login(u)
        row = rows_of(c.get(f"/s/{g.token}/schedule", {"mode": "week"}).content.decode())["취합"]
        for i in range(3):
            self.assertIn(f"동학년{i}", row)
