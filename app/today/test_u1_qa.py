"""U1 QA 경계 사례(구현자 test_u1_cards.py에 없는 것만).

- 일간·월간 모드와 그룹 탭에서도 '학교 전체'·'복무' 머리글자가 없다.
- 동학년 공간: 색 저장 주소 404, 오늘 화면에 학사일정 따로 줄 없음.
- 쓴 사람이 없어진 일정, 이름의 HTML은 안전하게.
- 여러 번 바꿔도 소속 색이 겹치지 않고, 바꾼 색이 복무 카드 점에도 간다(마감 카드는 U3에 일정에서 빠짐).
"""
import re
from datetime import date

from django.test import Client

from spaces.models import GROUP_PALETTE, Member, School, Space

from .models import Event
from .test_schedule_channels import TODAY, Base, grid_of, rows_of
from .test_u1_cards import cards_in

HEX = {k: v[0] for k, v in GROUP_PALETTE.items()}


class ModesTests(Base):
    def setUp(self):
        super().setUp()
        self.ev(self.t4, "학년 협의회", slot="b2")
        self.duty(self.t5, by=self.t4)

    def test_day_mode_cards(self):
        rows = rows_of(self.page(self.t4, "day"))
        self.assertNotIn("학교 전체", rows["학교"])
        self.assertIn("<span>4학년 · 사일</span>", cards_in(rows["학교"])["학년 협의회"])
        self.assertNotIn('<span class="k">복무</span>', rows["복무"])
        self.assertIn("<span>5학년</span>", cards_in(rows["복무"])["오일 출장"])

    def test_month_mode_no_scope_or_kind_word(self):
        grid = grid_of(self.page(self.t4, "month"))
        self.assertNotIn("학교 전체", grid)
        self.assertNotIn('<span class="k">복무</span>', grid)

    def test_group_tab_legend(self):
        html = self.page(self.t5, "week", tab="group")
        self.assertIn('<div class="legend">', html)
        self.assertNotIn("학교 전체", grid_of(html))

    def test_me_tab_has_no_legend(self):
        self.assertNotIn('<div class="legend">', self.page(self.t4, "week", tab="me"))

    def test_no_old_brand(self):
        for mode in ("day", "week", "month"):
            self.assertNotIn("티로드", self.page(self.t4, mode))
        self.assertNotIn("티로드", self.as_(self.t4).get(f"{self.base}/today").content.decode())


class EdgeTests(Base):
    def test_writer_gone(self):
        e = self.ev(None, "주인 없는 일정")
        html = self.page(self.t4, "week")
        c = html[html.index(f'data-open="#ev-{e.key}"'):]
        c = c[:c.index("</button>")]
        self.assertNotIn("--gc", c)
        self.assertNotIn("내 글", c)

    def test_name_is_escaped(self):
        bad = self.member("<b>악</b>", "4-2", "g4")
        self.ev(bad, "이름 확인")
        html = self.page(self.t5, "week")
        self.assertNotIn("<b>악</b>", html)
        self.assertIn("4학년 · &lt;b&gt;악&lt;/b&gt;", html)

    def test_colors_stay_unique_and_reach_duty(self):
        c = self.as_(self.boss)
        url = f"{self.base}/school/group-color?tab=grade&arrange=1"
        for g, k in [("g4", "red"), ("g5", "red"), ("office", "teal"), ("g4", "brown"), ("g1", "brown"), ("g5", "pink")]:
            self.assertEqual(c.post(url, {"group": g, "color": k}, headers={"HX-Request": "true"}).status_code, 200)
            self.hub.refresh_from_db()
            v = self.hub.group_colors
            self.assertEqual(len(set(v.values())), len(v), (g, k, v))
            self.assertEqual(v[g], k)
        self.duty(self.t5)
        rows = rows_of(self.page(self.t4, "week"))
        self.assertIn(f'--gc:{HEX["pink"]}', cards_in(rows["복무"])["오일 출장"])


class GradeSpaceTests(Base):
    def setUp(self):
        super().setUp()
        s = School.objects.create(neis_atpt="J10", neis_code="7536100", name="옆초")
        self.g = Space.objects.create(school=s, grade=4, name="4학년")
        U = self.t4.user.__class__
        self.a = Member.objects.create(space=self.g, user=U.objects.create(username="google:가"), name="가", cls="4-1",
                                       is_manager=True)

    def test_group_color_404_in_grade_space(self):
        c = Client()
        c.force_login(self.a.user)
        r = c.post(f"/s/{self.g.token}/school/group-color", {"group": "g4", "color": "red"}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 404)

    def test_grade_today_neis_in_untimed(self):
        c = Client()
        c.force_login(self.a.user)
        html = c.get(f"/s/{self.g.token}/today").content.decode()
        labels = re.findall(r'<div class="lbl">([^<]+)</div>', html)
        self.assertNotIn("학사일정", labels)
        self.assertIn("종일", labels)
        self.assertIn("나이스 자동", html[html.index('class="untimed"'):html.index("<!-- /dgrid -->")])  # 주간 판의 종일 줄(2026-11-11)
        Event.objects.create(space=self.g, by=self.a, title="동학년 일", scope="all", date=TODAY)
        self.assertNotIn('style="--gc', c.get(f"/s/{self.g.token}/today").content.decode())


class CrossSchoolTests(Base):
    def test_other_school_manager_cannot_color(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7536101", name="남초", plan="school", paid_until=date(2099, 1, 1))
        far = Space.objects.create(school=s, kind="school", grade=0, name="남초")
        U = self.t4.user.__class__
        fm = Member.objects.create(space=far, user=U.objects.create(username="google:남"), name="남", cls="교무실",
                                   group="office", is_manager=True)
        r = self.as_(fm).post(f"{self.base}/school/group-color", {"group": "g4", "color": "red"})
        self.assertIn(r.status_code, (403, 404))
        self.hub.refresh_from_db()
        self.assertEqual(self.hub.group_color, "")
        far.refresh_from_db()
        self.assertEqual(far.group_color, "")
