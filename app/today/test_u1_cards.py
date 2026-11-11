"""U1 일정 카드·머리줄 개편(2026-11-10 사용자 결정).

- 카드 아래 줄은 볼 사람 범위('학교 전체')가 아니라 쓴 사람 이름 · 소속, 점 색은 쓴 사람 소속 색(학교 공간만).
- 복무 카드: '복무' 머리글자 없이 당사자 소속 · 시간(명부로 못 맞추면 소속 비움, 점 없음). 넣은 사람 표시는 그대로.
- 취합 마감 카드는 U3(2026-11-11)에 일정에서 빠졌다.
- 내 글(내가 쓴 것)만 ev mine + '내 글'. 관리자가 남의 글을 고칠 수 있어도 내 글이 아니다.
- 나이스 학사일정: '나이스 자동' 표시. 오늘 화면은 학사일정 줄 없이 종일 줄 안에.
- 범례: 사람이 있는 소속의 점 색 + 나이스 자동. 동학년은 점 없음.
- 소속 색: 관리자만 바꾸고(그 밖 403, 다른 공간 404, 플랜 끝 403), 다른 소속이 쓰던 색이면 맞바꿔 겹치지 않는다.
"""
import re
from datetime import date

from django.test import Client

from spaces.models import GROUP_PALETTE, Attendance, Member, School, Space

from .models import ME
from .test_schedule_channels import TODAY, Base, rows_of

HEX = {k: v[0] for k, v in GROUP_PALETTE.items()}


def cards_in(row):
    """줄 HTML → {제목: 카드 HTML}."""
    out = {}
    for c in re.split(r'class="ev[ "]', row)[1:]:
        t = re.search(r'<span class="t">([^<]+)</span>', c)
        if t:
            out[t.group(1).strip()] = re.split(r"</button>|</a>|</div>", c)[0]
    return out


class CardTests(Base):
    def test_school_card_shows_writer_and_group_not_scope(self):
        self.ev(self.t4, "학년 협의회", slot="b2")
        row = rows_of(self.page(self.t5, "week"))["학교"]
        c = cards_in(row)["학년 협의회"]
        self.assertIn("<span>4학년 · 사일</span>", c)
        self.assertIn(f'--gc:{HEX["green"]}', c)  # g4 기본 색
        self.assertNotIn("학교 전체", row)
        self.assertNotIn("mine", c.split(">")[0])

    def test_group_and_me_tabs_use_writer_not_scope(self):
        self.ev(self.t4, "학년 회의", scope="g4")
        self.ev(self.t4, "혼자 할 일", scope=ME)
        g = self.page(self.t4, "week", tab="group")
        self.assertIn("<span>4학년 · 사일</span>", cards_in(g)["학년 회의"])
        self.assertNotIn('class="rest">· 4학년', g)  # 범위(4학년)를 따로 찍지 않는다
        m = self.page(self.t4, "week", tab="me")
        self.assertNotIn("개인(나만)", cards_in(m)["혼자 할 일"])

    def test_mine_only_for_my_own(self):
        mine = self.ev(self.t4, "내 일정")
        theirs = self.ev(self.t5, "남 일정")
        html = self.page(self.t4, "week")
        self.assertRegex(html, r'class="ev mine[^"]*" data-open="#ev-' + mine.key)
        self.assertRegex(html, r'class="ev" data-open="#ev-' + theirs.key)
        self.assertIn('<span class="mine-tag">내 글</span>', cards_in(html)["내 일정"])
        self.assertNotIn("내 글", cards_in(html)["남 일정"])
        boss = self.page(self.boss, "week")  # 관리자는 고칠 수 있어도 내 글이 아니다
        self.assertNotIn("내 글", boss)

    def test_duty_card_has_subject_group_no_kind_word(self):
        self.duty(self.t5, by=self.t4)
        Attendance.objects.create(space=self.hub, member=None, date=TODAY, name="명부밖", kind="출장", time_text="09:00~12:00", by=self.t4)
        row = rows_of(self.page(self.t4, "week"))["복무"]
        self.assertNotIn('<span class="k">복무</span>', row)
        cs = cards_in(row)
        self.assertIn("<span>5학년</span>", cs["오일 출장"])
        self.assertIn(f'--gc:{HEX["teal"]}', cs["오일 출장"])
        self.assertIn("넣은 사람 사일", cs["오일 출장"])
        self.assertNotIn("--gc", cs["명부밖 출장"])
        self.assertIn('<span class="rest">09:00~12:00</span>', cs["명부밖 출장"])
        self.assertIn("내 글", cs["오일 출장"])  # 내가 넣은 줄

    def test_neis_marked_auto(self):
        row = rows_of(self.page(self.t4, "week"))["학교"]
        self.assertRegex(row, r'class="ev neis"[^>]*><span class="t">독서주간 골든벨</span><span class="m"><span class="auto"[^>]*>.*나이스 자동')

    def test_neis_first_in_cell(self):
        self.ev(self.t4, "종일 일정")
        row = rows_of(self.page(self.t4, "week"))["학교"]
        self.assertLess(row.index("독서주간 골든벨"), row.index("종일 일정"))

    def test_month_keeps_dot_and_title(self):
        self.ev(self.t4, "월간 일정")
        html = self.page(self.t5, "month")
        self.assertRegex(html, r'<span class="t"><i class="dot" style="--gc:' + HEX["green"] + r'"[^>]*></i>월간 일정</span>')
        self.assertNotIn("<span>4학년 · 사일</span>", html)

    def test_legend_groups_with_people(self):
        html = self.page(self.t4, "week")
        legend = html[html.index('<div class="legend">'):html.index('class="wgrid')]
        self.assertEqual(re.findall(r'</i>([^<]+)</button>', legend), ["4학년", "5학년", "교무실"])  # U4: 범례 점이 소속 필터 단추
        self.assertIn("나이스 자동", legend)
        self.assertNotIn("sw-mine", html)
        self.assertIn("학년분장", legend)
        self.assertIn("arrange=1", self.page(self.boss, "week"))  # 관리자에게는 바꾸는 곳 링크
        self.assertNotIn("arrange=1", legend)


class TodayTests(Base):
    def today(self, m):
        return self.as_(m).get(f"{self.base}/today").content.decode()

    def test_neis_inside_untimed_row(self):
        html = self.today(self.t4)
        self.assertEqual(re.findall(r'<div class="lbl">([^<]+)</div>', html), ["내 일정", "점심 12:20", "종일"])
        untimed = html[html.index('class="untimed"'):]
        self.assertRegex(untimed, r'<span class="t">독서주간 골든벨</span><span class="auto"')

    def test_neis_failure_and_empty_inside_untimed(self):
        from unittest import mock

        from spaces import neis
        with mock.patch("spaces.neis.schedule", return_value=neis.Failed()):
            untimed = self.today(self.t4).split('class="untimed"')[1]
        self.assertIn("나이스에서 불러오지 못했어요", untimed)
        with mock.patch("spaces.neis.schedule", return_value=[]):
            untimed = self.today(self.t4).split('class="untimed"')[1]
        self.assertIn("오늘 학사일정이 없어요", untimed)

    def test_today_card_writer_group(self):
        self.ev(self.t5, "오늘 행사")
        self.ev(self.t4, "내 행사", slot="b1")
        html = self.today(self.t4)
        self.assertIn("<span>5학년 · 오일</span>", html)
        grid = html[html.index('id="dgrid"'):html.index("<!-- /dgrid -->")]
        self.assertNotIn("학교 전체", grid)
        self.assertIn('<span class="mine-tag">내 글</span>', html)


class GradeSpaceTests(Base):
    def test_grade_space_name_only_no_dot(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7536100", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="4학년")
        a = Member.objects.create(space=g, user=self.t4.user.__class__.objects.create(username="google:가"), name="가", cls="4-1")
        b = Member.objects.create(space=g, user=self.t4.user.__class__.objects.create(username="google:나"), name="나", cls="4-2")
        from .models import Event
        Event.objects.create(space=g, by=b, title="동학년 일", scope="all", date=TODAY)
        c = Client()
        c.force_login(a.user)
        html = c.get(f"/s/{g.token}/schedule", {"mode": "week"}).content.decode()
        card = cards_in(html)["동학년 일"]
        self.assertIn("<span>나</span>", card)
        self.assertNotIn('style="--gc', html)
        self.assertIn("나이스 자동", html)


class GroupColorTests(Base):
    def post(self, m, **data):
        return self.as_(m).post(f"{self.base}/school/group-color?tab=grade&arrange=1", data, headers={"HX-Request": "true"})

    def test_manager_changes_and_swaps(self):
        r = self.post(self.boss, group="g4", color="red")
        self.assertEqual(r.status_code, 200)
        self.hub.refresh_from_db()
        colors = self.hub.group_colors
        self.assertEqual((colors["g4"], colors["g1"]), ("red", "green"))  # g1이 쓰던 빨강 → 맞바꿈
        self.assertEqual(len(set(colors.values())), len(colors))
        self.ev(self.t4, "색 확인")
        self.assertIn(f'--gc:{HEX["red"]}', cards_in(rows_of(self.page(self.t5, "week"))["학교"])["색 확인"])

    def test_arrange_page_has_picker_for_manager_only(self):
        page = self.as_(self.boss).get(f"{self.base}/school?arrange=1").content.decode()
        self.assertIn('name="color"', page)
        self.assertIn("/school/group-color", page)
        self.assertNotIn('name="color"', self.as_(self.t4).get(f"{self.base}/school?arrange=1").content.decode())

    def test_refusals(self):
        self.assertEqual(self.post(self.t4, group="g4", color="red").status_code, 403)
        self.assertEqual(self.post(self.boss, group="g4", color="#ff0000").status_code, 400)
        self.assertEqual(self.post(self.boss, group="nope", color="red").status_code, 400)
        self.assertEqual(self.as_(self.boss).get(f"{self.base}/school/group-color").status_code, 405)
        s = School.objects.create(neis_atpt="J10", neis_code="7536100", name="옆초", plan="school", paid_until=date(2099, 1, 1))
        far = Space.objects.create(school=s, kind="school", grade=0, name="옆초")
        fm = Member.objects.create(space=far, user=self.t4.user.__class__.objects.create(username="google:옆"), name="옆", cls="교무실",
                                   group="office", is_manager=True)
        self.assertEqual(self.post(fm, group="g4", color="red").status_code, 404)
        self.hub.school.paid_until = date(2000, 1, 1)
        self.hub.school.save()
        self.assertEqual(self.post(self.boss, group="g4", color="red").status_code, 403)
        self.hub.refresh_from_db()
        self.assertEqual(self.hub.group_color, "")
