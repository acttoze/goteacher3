"""QA: QC 취합 받는 사람(2026-11-06) 경계 사례. 구현 테스트(test_school_aud.SortTests)에 없는 것만.
- 공간 격리: 다른 학교 공간의 사람 키·업무분장은 정렬·줄에 끼지 않는다.
- 옛 범위(groups·dept)를 요약 주소로 보내도 전 교직원으로 읽는다.
- 동학년 공간은 aud_sort·aud를 보내도 반별 명부 그대로.
- 정렬이 붙어도 받는 범위(전 교직원·개인·소속별 1칸)는 그대로(일정 탭 규칙은 U3에 일정에서 취합이 빠지며 없어짐).
- 상자 다시 그리기(part=box) 응답에 템플릿 주석·코드 글자가 새지 않는다.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from spaces.models import Duty, Member, School, Space

from .models import Collection, Row
from .test_school_aud import UNTIL, SchoolBase

User = get_user_model()


class SortIsolationQa(SchoolBase):
    def other(self):
        o = Space.objects.create(school=School.objects.create(neis_atpt="B10", neis_code="9", name="딴초", plan="school", paid_until=UNTIL),
                                 kind="school", grade=0, name="딴초")
        far = Member.objects.create(space=o, user=User.objects.create(username="google:딴"), name="딴사람", cls="교무실", group="office")
        Duty.objects.create(space=o, dept="딴부서", task="x", who="딴사람, 부장, 사일", order=0)
        return o, far

    def test_other_space_duty_does_not_enable_or_shape_sort(self):
        self.other()  # 우리 공간에는 업무분장이 없다
        boss = self.login(self.boss)
        html = boss.get(f"{self.base}/c/new").content.decode()
        self.assertIn("data-sort disabled", html)
        self.assertNotIn("딴부서", html)
        c = self.make(aud="all", aud_sort="dept")
        self.assertEqual((c.unit_head, c.audience.get("sort")), (["직위", "성명"], None))
        self.assertFalse(Row.objects.filter(collection=c, unit="딴부서").exists())

    def test_other_space_person_key_ignored_with_dept_sort(self):
        o, far = self.other()
        Duty.objects.create(space=self.hub, dept="교육과정부", task="일", who="부장", order=0)
        c = self.make(aud="people", aud_people=[far.key, self.boss.key, self.sup.key], aud_sort="dept")
        self.assertEqual([(r.unit, r.who) for r in c.rows.all()], [("교육과정부", "부장"), ("부서 없음", "지원")])
        r = self.login(self.boss).post(f"{self.base}/c/new/aud", {"part": "box", "aud": "people", "aud_sort": "dept", "aud_people": [far.key]})
        self.assertNotContains(r, "딴사람")
        self.assertNotContains(r, "딴부서")
        self.assertContains(r, "받을 사람이나 그룹을 골라 주세요.")

    def test_other_school_member_cannot_redraw_box(self):
        o, far = self.other()
        c = Client()
        c.force_login(far.user)
        self.assertEqual(c.post(f"{self.base}/c/new/aud", {"part": "box", "aud": "all", "aud_sort": "dept"}).status_code, 404)


class OldScopesQa(SchoolBase):
    def test_summary_reads_old_scopes_as_all(self):
        Duty.objects.create(space=self.hub, dept="가", task="일", who="사일", order=0)
        boss = self.login(self.boss)
        for data in ({"aud": "groups", "aud_groups": ["g4"], "aud_hr": "on"}, {"aud": "dept", "aud_depts": ["가"]}):
            r = boss.post(f"{self.base}/c/new/aud", data)
            self.assertContains(r, "전 교직원 9명 · 사람별 1칸", msg_prefix=str(data))
            self.assertNotContains(r, "담임")
            self.assertNotContains(r, "부서 1곳")

    def test_hr_flag_alone_does_not_make_class_rows(self):
        c = self.make(aud="all", aud_hr="on")
        self.assertEqual(c.unit_head, ["직위", "성명"])
        self.assertNotIn("hr", c.audience)


class BoxPartialQa(SchoolBase):
    def test_box_partial_has_no_template_text(self):
        Duty.objects.create(space=self.hub, dept="가", task="일", who="사일", order=0)
        for data in ({"part": "box", "aud": "all", "aud_sort": "dept"}, {"part": "box", "aud": "people", "aud_sort": "group"},
                     {"part": "box", "aud": "pergroup", "aud_pgroups": ["g4"]}):
            body = self.login(self.boss).post(f"{self.base}/c/new/aud", data).content.decode()
            for leak in ("{#", "#}", "{%", "%}", "{{", "}}", "endcomment", "_aud_school"):
                self.assertNotIn(leak, body, (data, leak))
            self.assertIn('id="audSum"', body)


class GradeSpaceSortQa(TestCase):
    def test_grade_space_ignores_sort(self):
        g = Space.objects.create(school=School.objects.create(neis_atpt="C10", neis_code="2", name="옆초"), grade=4, name="옆")
        u = User.objects.create(username="google:가")
        Member.objects.create(space=g, user=u, name="가", cls="4-2")
        Member.objects.create(space=g, name="나", cls="4-1")
        Duty.objects.create(space=g, dept="교육과정부", task="일", who="가", order=0)
        self.client.force_login(u)
        r = self.client.post(f"/s/{g.token}/c/new", {"src": "sheet", "title": "조사", "col_name_0": "칸",
                                                    "aud": "people", "aud_sort": "dept", "aud_people": ["x"]})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.get()
        self.assertEqual((c.unit_head, c.audience), (["학년반", "담임"], {}))
        self.assertEqual([x.unit for x in c.rows.all()], ["4-1", "4-2"])
        self.assertNotContains(self.client.get(f"/s/{g.token}/c/new"), "data-sort")


class ScheduleTabWithSortQa(SchoolBase):
    def test_sort_does_not_change_audience_mode(self):
        Duty.objects.create(space=self.hub, dept="가", task="일", who="사일", order=0)
        c_all = self.make(aud="all", aud_sort="dept")
        c_people = self.make(aud="people", aud_people=[self.t41.key], aud_sort="dept")
        c_pg = self.make(aud="pergroup", aud_pgroups=["g4", "g5"], aud_sort="dept")
        self.assertEqual(c_all.audience["sort"], "dept")
        self.assertEqual([c_all.audience["mode"], c_people.audience["mode"], c_pg.audience["mode"]], ["all", "people", "pergroup"])
        self.assertEqual(c_pg.audience["groups"], ["g4", "g5"])
