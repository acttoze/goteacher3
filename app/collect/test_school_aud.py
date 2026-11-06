"""H4 학교 공간 취합: 받는 사람 범위 5가지, 접근 제한, 목록, 한글 줄 맞춤, 저장 한도, 200줄 합본."""
import csv
import re
import io
import shutil
import tempfile
from datetime import date
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hwpfiles.models import ResultFile, SourceFile, space_quota
from spaces.models import Duty, Member, School, Space

from . import hwpform
from .models import Cell, Collection, Field, Row
from .recipients import GROUPS, audience_head, audience_rows, audience_text, make_rows, read_audience, roster_rows, stored_audience
from .test_hwp import table_doc

User = get_user_model()
re_find = re.findall
UNTIL = date(2099, 1, 1)


class SchoolBase(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        o = override_settings(MEDIA_ROOT=media)
        o.enable()
        self.addCleanup(o.disable)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        self.hub = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        self.base = f"/s/{self.hub.token}"
        # 들어온 순서와 줄 순서를 일부러 다르게
        self.boss = self.seat("교무실", "부장", "office", "교무부장", manager=True)
        self.meal = self.seat("급식소", "영양", "meal", "영양교사")
        self.t52 = self.seat("5-2", "오이", "g5", "2반")
        self.t41 = self.seat("4-1", "사일", "g4", "1반")
        self.t4x = self.seat("4-전담", "사담", "g4", "전담")
        self.t42 = self.seat("4-2", "사이", "g4", "2반")
        self.adm = self.seat("행정실", "행정", "admin", "행정실장")
        self.sup = self.seat("기타/지원", "지원", "support", "사서")
        self.t51 = self.seat("5-1", "오일", "g5", "1반")
        self.roster = [self.t41, self.t42, self.t4x, self.t51, self.t52, self.boss, self.adm, self.meal, self.sup]

    def seat(self, cls, name, group, position, manager=False):
        u = User.objects.create(username=f"google:{name}", first_name=name)
        return Member.objects.create(space=self.hub, user=u, name=name, cls=cls, group=group, position=position, is_manager=manager)

    def login(self, m):
        c = Client()
        c.force_login(m.user)
        return c

    def make(self, who=None, **aud):
        """aud=groups·dept는 새로 고를 수 없다(QC 취합 받는 사람 2026-11-06). 이미 만든 옛 취합으로 두고 시험한다:
        전 교직원으로 만든 뒤 범위·줄·머리를 그 옛 범위로 바꿔 저장(그때 만들던 것과 같은 값)."""
        old = aud.get("aud") in ("groups", "dept")
        r = self.login(who or self.boss).post(f"{self.base}/c/new", {"src": "sheet", "title": "조사", "col_name_0": "참가(명)",
                                                                     **({**aud, "aud": "all"} if old else aud)})
        self.assertEqual(r.status_code, 302, r.content[:400])
        c = Collection.objects.latest("pk")
        return self.as_old(c, aud) if old else c

    def as_old(self, c, aud):
        if aud["aud"] == "groups":
            d = {"mode": "groups", "groups": [g for g in GROUPS if g in aud.get("aud_groups", [])], "hr": aud.get("aud_hr") == "on"}
        else:
            known = set(Duty.objects.in_space(self.hub).values_list("dept", flat=True))
            d = {"mode": "dept", "depts": list(dict.fromkeys(x for x in aud.get("aud_depts", []) if x in known))}
        rows = audience_rows(self.hub, d)
        c.rows.all().delete()
        make_rows(c, rows)
        c.audience, c.unit_head = stored_audience(d, rows), audience_head(d)
        c.save()
        return c

    def lines(self, c):
        return [(r.unit, r.who, r.member_id) for r in c.rows.all()]


class AudienceTests(SchoolBase):
    def test_all_is_everyone_in_grade_then_office_order_with_people_head(self):
        c = self.make(aud="all")
        self.assertEqual(c.unit_head, ["직위", "성명"])
        self.assertEqual([r.member_id for r in c.rows.all()], [m.pk for m in self.roster])
        self.assertEqual(c.rows.get(member=self.boss).unit, "교무실 · 교무부장")
        self.assertEqual(c.rows.get(member=self.t41).unit, "4학년 1반")
        self.assertEqual(c.audience["mode"], "all")
        self.assertIn("전 교직원 9명", c.audience["text"])

    def test_groups_hr_only_is_class_rows_in_grade_order(self):
        c = self.make(aud="groups", aud_groups=["g5", "g4", "office"], aud_hr="on")
        self.assertEqual(c.unit_head, ["학년반", "담임"])
        self.assertEqual([(r.unit, r.who) for r in c.rows.all()], [("4-1", "사일"), ("4-2", "사이"), ("5-1", "오일"), ("5-2", "오이")])
        self.assertIn("담임 4명", c.audience["text"])

    def test_groups_without_hr_is_every_member_of_those_groups(self):
        c = self.make(aud="groups", aud_groups=["g4", "admin"])
        self.assertEqual(c.unit_head, ["직위", "성명"])
        self.assertEqual([r.member_id for r in c.rows.all()], [self.t41.pk, self.t42.pk, self.t4x.pk, self.adm.pk])

    def test_pergroup_one_row_for_each_group_anyone_may_write(self):
        c = self.make(aud="pergroup", aud_pgroups=["g5", "g4", "meal"])
        self.assertEqual(c.unit_head, ["그룹", "작성"])
        self.assertEqual([(r.unit, r.who, r.member_id, r.group) for r in c.rows.all()],
                         [("4학년", "", None, "g4"), ("5학년", "", None, "g5"), ("급식소", "", None, "meal")])
        self.assertIn("소속 3곳", c.audience["text"])

    def test_dept_rows_from_duty_each_person_once_order_of_table(self):
        for i, (dept, task, who) in enumerate([("교육과정부", "교육과정", "사일, 부장"), ("방과후돌봄부", "방과후", "오 일"), ("교육과정부", "평가", "사일"),
                                               ("정보부", "정보", "없는이름"), ("행정지원", "시설", "")]):
            Duty.objects.create(space=self.hub, dept=dept, task=task, who=who, order=i)
        c = self.make(aud="dept", aud_depts=["교육과정부", "방과후돌봄부", "정보부", "행정지원"])
        self.assertEqual(c.unit_head, ["부서", "담당"])
        self.assertEqual(self.lines(c), [("교육과정부", "사일", self.t41.pk), ("교육과정부", "부장", self.boss.pk),
                                         ("방과후돌봄부", "오일", self.t51.pk), ("정보부", "없는이름", None)])

    def test_dept_same_name_twice_is_not_given_to_one_member(self):
        Member.objects.create(space=self.hub, name="사일", cls="6-1", group="g6")
        Duty.objects.create(space=self.hub, dept="부", task="일", who="사일", order=0)
        c = self.make(aud="dept", aud_depts=["부"])
        self.assertEqual(self.lines(c), [("부", "사일", None)])

    def test_people_only_those_picked_in_roster_order(self):
        c = self.make(aud="people", aud_people=[self.sup.key, self.t51.key])
        self.assertEqual(c.unit_head, ["직위", "성명"])
        self.assertEqual([r.member_id for r in c.rows.all()], [self.t51.pk, self.sup.pk])
        self.assertEqual(c.audience["text"], "개인 2명 · 사람별 1칸")

    def test_people_key_of_other_space_is_ignored(self):
        other = Space.objects.create(school=School.objects.create(neis_atpt="B10", neis_code="1", name="먼초"), grade=4, name="먼")
        far = Member.objects.create(space=other, name="먼이", cls="4-1")
        c = self.make(aud="people", aud_people=[far.key, self.t41.key])
        self.assertEqual([r.member_id for r in c.rows.all()], [self.t41.pk])

    def test_nobody_picked_is_error_and_makes_nothing(self):
        n = Collection.objects.count()
        for aud in ({"aud": "pergroup"}, {"aud": "people"}):
            r = self.login(self.boss).post(f"{self.base}/c/new", {"src": "sheet", "title": "x", "col_name_0": "칸", **aud})
            self.assertContains(r, "받을 사람이나 그룹을 골라 주세요.", status_code=400)
        self.assertEqual(Collection.objects.count(), n)

    def test_no_audience_field_defaults_to_all_staff(self):
        """기본 범위는 전 교직원(2026-11-06 사용자 결정, 전에는 1~6학년 담임)."""
        c = self.make()
        self.assertEqual(c.audience["mode"], "all")
        self.assertEqual(c.rows.count(), Member.objects.in_space(c.space).count())

    def test_paste_and_hwp_paths_use_audience_too(self):
        r = self.login(self.boss).post(f"{self.base}/c/new", {"src": "paste", "title": "표", "paste": "반\t담임\t참가(명)\n4-1\t\t\n", "aud": "all"})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.latest("pk")
        self.assertEqual((c.unit_head, c.rows.count()), (["직위", "성명"], 9))

    def test_preset_keeps_own_recipients(self):
        r = self.login(self.boss).post(f"{self.base}/c/new", {"src": "preset", "preset": "training", "aud": "groups", "aud_groups": ["g4"]})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.latest("pk")
        self.assertEqual([x.member_id for x in c.rows.all()], [m.pk for m in self.roster])
        self.assertEqual(c.audience, {})

    def test_again_keeps_range_with_current_roster(self):
        c = self.make(aud="groups", aud_groups=["g4"])
        Member.objects.create(space=self.hub, name="새사", cls="4-3", group="g4")
        self.t42.delete()
        r = self.login(self.boss).post(f"{self.base}/c/{c.key}/again")
        self.assertEqual(r.status_code, 303)
        new = Collection.objects.latest("pk")
        self.assertEqual(new.unit_head, ["직위", "성명"])
        self.assertEqual([x.who for x in new.rows.all()], ["사일", "새사", "사담"])
        self.assertEqual(new.audience["groups"], ["g4"])
        self.assertIn("3명", new.audience["text"])

    def test_outside_row_still_works(self):
        c = self.make(aud="all", outside="on")
        r = Client().post(f"/c/{c.link_token}/outside", {"name": "강사"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(c.rows.last().who, "강사")

    def test_new_screen_lists_choices_and_summary_endpoint(self):
        """QC 취합 받는 사람(2026-11-06): 선택지는 전 교직원·소속·개인 셋. 소속 고르기·부서·담임만은 화면에 없다."""
        Duty.objects.create(space=self.hub, dept="교육과정부", task="교육과정", who="사일", order=0)
        c = self.login(self.boss)
        html = c.get(f"{self.base}/c/new").content.decode()
        box = html[html.index('id="audBox"'):html.index('id="audSum"')]
        self.assertEqual(re_find(r'name="aud" value="(\w+)"', box), ["all", "pergroup", "people"])
        for piece in (f'value="{self.t41.key}"', "<b>전 교직원</b>", "<b>소속</b>", "<b>개인</b>", 'name="aud_sort" value="group"'):
            self.assertIn(piece, box)
        for gone in ("aud_groups", "aud_hr", "aud_depts", "담임만", "data-gall", "1~6학년만", "소속 고르기", "사람 고르기", "<b>부서</b>"):
            self.assertNotIn(gone, box)
        r = c.post(f"{self.base}/c/new/aud", {"aud": "pergroup", "aud_pgroups": ["g4", "g5"]})
        self.assertContains(r, "소속 2곳(4학년·5학년) · 소속별 1칸")
        r = c.post(f"{self.base}/c/new/aud", {"aud": "pergroup"})
        self.assertContains(r, "받을 사람이나 그룹을 골라 주세요.")
        self.assertEqual(self.login(self.boss).post(f"/s/{self.hub.token}/c/new/aud", {"aud": "all"}).status_code, 200)

    def test_summary_endpoint_is_school_only(self):
        grade = Space.objects.create(school=School.objects.create(neis_atpt="C10", neis_code="2", name="옆초"), grade=4, name="옆")
        u = User.objects.create(username="google:옆")
        Member.objects.create(space=grade, user=u, name="옆", cls="4-1")
        c = Client()
        c.force_login(u)
        self.assertEqual(c.post(f"/s/{grade.token}/c/new/aud", {"aud": "all"}).status_code, 404)

    def test_stored_text_helpers(self):
        self.assertEqual(audience_text({"mode": "all"}, 9), "전 교직원 9명 · 사람별 1칸")
        grades = [f"g{i}" for i in range(1, 7)]
        self.assertEqual(audience_text({"mode": "groups", "groups": grades, "hr": True}, 24), "1~6학년 담임 24명 · 반별 1칸")
        self.assertEqual(audience_text({"mode": "groups", "groups": [*grades, "office"], "hr": False}, 30), "1~6학년·교무실 30명 · 사람별 1칸")
        self.assertEqual(audience_text({"mode": "dept", "depts": ["가", "나"]}, 3), "부서 2곳 담당 3명 · 사람별 1칸")
        self.assertEqual(audience_head({}), ["학년반", "담임"])


class GroupRowTests(SchoolBase):
    def test_every_group_member_gets_it_until_one_submits(self):
        c = self.make(aud="pergroup", aud_pgroups=["g4"])
        row = c.rows.get()
        for m in (self.t41, self.t42, self.t4x):
            self.assertContains(self.login(m).get(f"{self.base}/c/?tab=todo"), f"/c/{c.link_token}/u/{row.key}")
        self.assertNotContains(self.login(self.t51).get(f"{self.base}/c/?tab=todo"), c.link_token)
        f = c.fields.get()
        r = Client().post(f"/c/{c.link_token}/u/{row.key}", {f"f{f.pos}": "3", "by": "사이"})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "대신 냄")
        self.assertNotContains(self.login(self.t41).get(f"{self.base}/c/?tab=todo"), c.link_token)
        board = self.login(self.boss).get(f"{self.base}/c/{c.key}/live")
        self.assertContains(board, "사이")
        self.assertNotContains(board, "대신 냄")

    def test_csv_writer_column_is_who_submitted(self):
        c = self.make(aud="pergroup", aud_pgroups=["g4", "g5"])
        row = c.rows.get(group="g4")
        Client().post(f"/c/{c.link_token}/u/{row.key}", {"f0": "3", "by": "사이"})
        body = self.login(self.boss).get(f"{self.base}/c/{c.key}/export.csv").content.decode("utf-8-sig")
        self.assertEqual(list(csv.reader(io.StringIO(body))),
                         [["소속", "작성", "참가(명)"], ["4학년", "사이", "3"], ["5학년", "", ""],
                          ["고티처로 모았습니다 · " + settings.SITE_URL.split("//")[1]]])

    def test_recv_pick_and_form_say_anyone_in_group(self):
        c = self.make(aud="pergroup", aud_pgroups=["g4"])
        row = c.rows.get()
        pick = Client().get(f"/c/{c.link_token}")
        self.assertContains(pick, "소속 누구나")
        self.assertNotContains(pick, "이름 없음")
        form = Client().get(f"/c/{c.link_token}/u/{row.key}")
        self.assertContains(form, "소속 누구나")
        self.assertNotContains(form, "이 칸 선생님")
        mine = self.login(self.t41).get(f"/c/{c.link_token}")
        self.assertContains(mine, "unit mine")

    def test_multi_new_line_stays_in_group(self):
        r = self.login(self.boss).post(f"{self.base}/c/new", {"src": "sheet", "title": "물품", "col_name_0": "물품", "multi": "on", "aud": "pergroup", "aud_pgroups": ["g4"]})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.latest("pk")
        row = c.rows.get()
        Client().post(f"/c/{c.link_token}/u/{row.key}", {"line": [row.key, ""], "f0": ["풀", "자"], "by": "사일"})
        self.assertEqual(list(c.rows.values_list("group", flat=True)), ["g4", "g4"])

    def test_group_text_is_view_range_not_permission(self):
        """그룹을 바꿔도 다른 그룹 취합 합본은 열리지 않는다(보기 범위에만)."""
        c = self.make(aud="pergroup", aud_pgroups=["g5"])
        Member.objects.filter(pk=self.t41.pk).update(group="g5")
        self.assertEqual(self.login(self.t41).get(f"{self.base}/c/{c.key}/").status_code, 404)


class AccessTests(SchoolBase):
    """학교 공간: 만든 사람·관리자만, 그 외 회원은 404. 받는 링크는 그대로."""

    def setUp(self):
        super().setUp()
        self.c = self.make(who=self.t41, aud="groups", aud_groups=["g4"], aud_hr="on")
        self.row = self.c.rows.first()
        self.sf = SourceFile.objects.create(space=self.hub, created_by=self.t41, name="양식.hwpx", kind="hwpx", size=2, stored=2, status="done", collection=self.c)
        self.sf.original.save("x", ContentFile(b"PK"))
        self.rf = ResultFile.objects.create(space=self.hub, source=self.sf, collection=self.c, created_by=self.boss, fmt="hwpx", status="done", stored=2)
        self.rf.hwpx.save("hwpx", ContentFile(b"PK"))
        k = self.c.key
        self.gets = [f"/c/{k}/", f"/c/{k}/live", f"/c/{k}/export.csv", f"/c/{k}/copy", f"/hwp/{self.sf.key}/status", f"/hwp/{self.sf.key}/",
                     f"/hwp/{self.sf.key}/hwpx", f"/hwp/{self.sf.key}/pick", f"/hwp/out/{self.rf.key}/hwpx"]
        self.posts = [f"/c/{k}/footer", f"/c/{k}/due", f"/c/{k}/lock", f"/c/{k}/link", f"/c/{k}/again", f"/c/{k}/delete",
                      f"/c/{k}/hwp", f"/hwp/{self.sf.key}/pick", f"/c/new/hwp/{self.sf.key}"]

    def test_other_member_gets_404_on_every_address_even_same_group(self):
        for m in (self.t51, self.t42, self.meal):
            c = self.login(m)
            for path in self.gets:
                self.assertEqual(c.get(self.base + path).status_code, 404, (m.name, path))
            for path in self.posts:
                self.assertEqual(c.post(self.base + path, {"date": "2030-01-01"}).status_code, 404, (m.name, path))
            self.assertEqual(c.get(f"{self.base}/c/{self.c.key}/hwp").status_code, 404)
        self.c.refresh_from_db()
        self.assertIsNone(self.c.closed_at)
        self.assertIsNone(self.c.due)

    def test_owner_and_manager_can_open_everything_they_read(self):
        for m in (self.t41, self.boss):
            c = self.login(m)
            for path in self.gets:
                self.assertEqual(c.get(self.base + path).status_code, 200, (m.name, path))
            self.assertEqual(c.post(f"{self.base}/c/{self.c.key}/footer", {"on": "on"}).status_code, 200)

    def test_manager_can_close_but_not_delete_or_relink(self):
        c = self.login(self.boss)
        self.assertEqual(c.post(f"{self.base}/c/{self.c.key}/due", {"date": "2030-01-01"}).status_code, 200)
        self.assertEqual(c.post(f"{self.base}/c/{self.c.key}/lock").status_code, 200)
        self.c.refresh_from_db()
        self.assertTrue(self.c.closed_at and self.c.due)
        self.assertEqual(c.post(f"{self.base}/c/{self.c.key}/lock", {"open": "1"}).status_code, 200)
        self.assertEqual(c.post(f"{self.base}/c/{self.c.key}/link").status_code, 403)
        self.assertEqual(c.post(f"{self.base}/c/{self.c.key}/delete").status_code, 403)
        self.assertContains(c.get(f"{self.base}/c/{self.c.key}/"), "마감하고 잠그기")

    def test_owner_who_left_manager_takes_over(self):
        Collection.objects.filter(pk=self.c.pk).update(created_by=None)
        self.assertEqual(self.login(self.boss).get(f"{self.base}/c/{self.c.key}/").status_code, 200)
        self.assertEqual(self.login(self.t41).get(f"{self.base}/c/{self.c.key}/").status_code, 404)

    def test_result_made_by_manager_opens_for_collection_owner(self):
        self.assertEqual(self.login(self.t41).get(f"{self.base}/hwp/out/{self.rf.key}/hwpx").status_code, 200)

    def test_new_form_cannot_use_other_members_file(self):
        form = {"src": "hwp", "title": "남의것", "hwp": self.sf.key}
        SourceFile.objects.filter(pk=self.sf.pk).update(collection=None)
        r = self.login(self.t51).post(f"{self.base}/c/new", form)
        self.assertContains(r, "한글 파일을 올려 주세요", status_code=400)

    def test_link_is_anonymous_for_everyone(self):
        anon = Client()
        self.assertEqual(anon.get(f"/c/{self.c.link_token}").status_code, 200)
        r = anon.post(f"/c/{self.c.link_token}/u/{self.row.key}", {"f0": "5", "by": "옆반"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Cell.objects.get(row=self.row).by, "옆반")

    def test_other_school_cannot_reach(self):
        far = Space.objects.create(school=School.objects.create(neis_atpt="B10", neis_code="1", name="먼초"), grade=4, name="먼")
        u = User.objects.create(username="google:먼")
        Member.objects.create(space=far, user=u, name="먼", cls="4-1")
        c = Client()
        c.force_login(u)
        for base in (f"/s/{far.token}", self.base):
            for path in self.gets:
                self.assertEqual(c.get(base + path).status_code, 404, path)

    def test_grade_space_stays_open_to_all_members(self):
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        a = Member.objects.create(space=g, user=User.objects.create(username="google:가"), name="가", cls="4-1")
        b = Member.objects.create(space=g, user=User.objects.create(username="google:나"), name="나", cls="4-2")
        coll = Collection.objects.create(space=g, created_by=a, title="t", source="sheet", unit_head=["학년반", "담임"])
        Field.objects.create(collection=coll, pos=0, name="칸")
        c = self.login(b)
        for path in (f"/c/{coll.key}/", f"/c/{coll.key}/live", f"/c/{coll.key}/export.csv", f"/c/{coll.key}/copy"):
            self.assertEqual(c.get(f"/s/{g.token}{path}").status_code, 200, path)
        self.assertEqual(c.post(f"/s/{g.token}/c/{coll.key}/footer", {"on": "on"}).status_code, 200)
        self.assertEqual(c.post(f"/s/{g.token}/c/{coll.key}/lock").status_code, 403)


class ListTests(SchoolBase):
    def setUp(self):
        super().setUp()
        self.c4 = self.make(who=self.t41, aud="groups", aud_groups=["g4"], aud_hr="on")
        self.c4.title = "사학년비밀조사"
        self.c4.save()
        self.mine = self.make(who=self.boss, aud="people", aud_people=[self.t51.key])

    def test_member_of_other_grade_does_not_see_title_or_link_anywhere(self):
        c = self.login(self.t51)
        for path in ("/c/", "/c/?tab=todo", "/c/?tab=past", "/c/?tab=all", "/today", "/schedule"):
            r = c.get(self.base + path)
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, "사학년비밀조사")
            self.assertNotContains(r, self.c4.link_token)
            self.assertNotContains(r, self.c4.key)

    def test_my_row_shows_in_todo_and_today(self):
        for path in ("/c/?tab=todo", "/today"):
            self.assertContains(self.login(self.t51).get(self.base + path), self.mine.link_token)

    def test_manager_default_is_own_with_one_school_wide_view(self):
        c = self.login(self.boss)
        r = c.get(f"{self.base}/c/")
        self.assertNotContains(r, "사학년비밀조사")
        self.assertContains(r, "학교 전체")
        r = c.get(f"{self.base}/c/?tab=all")
        self.assertContains(r, "사학년비밀조사")
        self.assertContains(r, "사일")  # 누가 만들었는지
        self.assertEqual(r.content.decode().count("/again"), 1)  # 내 것만 다시 걷기

    def test_non_manager_has_no_school_wide_view(self):
        c = self.login(self.t41)
        self.assertNotContains(c.get(f"{self.base}/c/"), "학교 전체")
        self.assertNotContains(c.get(f"{self.base}/c/?tab=all"), self.mine.title + self.mine.key)
        self.assertNotContains(c.get(f"{self.base}/c/?tab=all"), self.mine.key)

    def test_grade_space_has_no_school_wide_view(self):
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        m = Member.objects.create(space=g, user=User.objects.create(username="google:가"), name="가", cls="4-1", is_manager=True)
        self.assertNotContains(self.login(m).get(f"/s/{g.token}/c/"), "학교 전체")


class HwpRowTests(SchoolBase):
    def rows(self, *heads):
        return [{"r": i + 1, "head": h, "on": True} for i, h in enumerate(heads)]

    def test_grade_must_match_in_school_table(self):
        roster = roster_rows(self.hub, "class")
        got = hwpform.assign(self.rows("5-1", "4-1", "4-2", "5-2"), roster, 0)
        self.assertEqual(dict(zip([r["unit"] for r in roster], got)), {"4-1": 2, "4-2": 3, "5-1": 1, "5-2": 4})

    def test_table_without_class_gets_no_cross_grade_fill(self):
        """표에 4-3 줄이 있어도 5-1 선생님이 거기 들어가지 않는다."""
        roster = [r for r in roster_rows(self.hub, "class") if r["unit"] == "5-1"]
        self.assertEqual(hwpform.assign(self.rows("4-3"), roster, 0), [None])

    def test_table_without_class_heads_fills_in_order(self):
        roster = roster_rows(self.hub, "class")[:2]
        self.assertEqual(hwpform.assign(self.rows("", ""), roster, 0), [1, 2])

    def test_grade_space_reads_class_only_heads_as_before(self):
        roster = [{"unit": "4-2"}, {"unit": "4-1"}]
        self.assertEqual(hwpform.assign(self.rows("1반", "2반"), roster, 4), [2, 1])
        self.assertEqual(hwpform.assign(self.rows("5-1", "4-1"), roster, 4), [1, 2])

    def test_people_audience_matches_by_member_class(self):
        rows = audience_rows(self.hub, {"mode": "groups", "groups": ["g4"], "hr": False})
        self.assertEqual(hwpform.assign(self.rows("4-2", "4-1"), rows, 0), [2, 1, None])

    def test_screen_and_create_in_order(self):
        c = self.login(self.boss)
        doc = table_doc([["반", "담임", "참가(명)", "불참(명)"], ["5-1", "", "", ""], ["4-1", "", "", ""], ["4-2", "", "", ""], ["합계", "", "", ""]])
        self.assertEqual(c.post(f"{self.base}/hwp/upload?pick=1", {"file": SimpleUploadedFile("표.hwpx", doc)}).status_code, 200)
        sf = SourceFile.objects.get(name="표.hwpx")
        self.assertEqual(c.post(f"{self.base}/hwp/{sf.key}/pick", {"sec": 0, "index": 1}).status_code, 200)
        r = c.post(f"{self.base}/c/new/hwp/{sf.key}", {"aud": "people", "aud_people": [self.t41.key, self.t42.key]})  # 담임만 범위가 없어져 개인으로
        self.assertEqual(r.status_code, 200)
        who = {x["head"]: (x["who"] or {}).get("who") for x in r.context["hwpf"]["rows"]}
        self.assertEqual(who, {"5-1": None, "4-1": "사일", "4-2": "사이", "합계": None})


class QuotaTests(SchoolBase):
    def test_school_setting_is_used_and_defaults(self):
        self.assertEqual((settings.HWP_SCHOOL_QUOTA, settings.HWP_TOTAL_QUOTA), (2 * 1024**3, 5 * 1024**3))
        grade = Space.objects.create(school=self.school, grade=4, name="옆")
        self.assertEqual((space_quota(self.hub), space_quota(grade)), (settings.HWP_SCHOOL_QUOTA, settings.HWP_SPACE_QUOTA))

    def test_upload_over_school_quota_refused_and_above_grade_quota_allowed(self):
        c = self.login(self.boss)
        doc = table_doc([["반", "참가(명)"], ["4-1", ""], ["4-2", ""]])
        with override_settings(HWP_SPACE_QUOTA=1, HWP_SCHOOL_QUOTA=10_000_000):
            self.assertEqual(c.post(f"{self.base}/hwp/upload?pick=1", {"file": SimpleUploadedFile("가.hwpx", doc)}).status_code, 200)
        with override_settings(HWP_SPACE_QUOTA=10_000_000, HWP_SCHOOL_QUOTA=1):
            r = c.post(f"{self.base}/hwp/upload?pick=1", {"file": SimpleUploadedFile("나.hwpx", doc)})
        self.assertContains(r, "저장 공간이 가득 찼어요", status_code=400)


class ScaleTests(SchoolBase):
    """200줄 합본: 쿼리 수가 줄 수에 따라 늘지 않고, 칩은 그룹별 수로 접고, 바뀐 것 없으면 204."""

    def big(self, n):
        Member.objects.bulk_create([Member(space=self.hub, name=f"교사{i}", cls=f"{i % 6 + 1}-{i % 30 + 1}", group=f"g{i % 6 + 1}", position="담임")
                                    for i in range(n)])
        c = self.make(aud="all")
        return c

    def live(self, c):
        boss = self.login(self.boss)
        with CaptureQueriesContext(connection, ) as q:
            r = boss.get(f"{self.base}/c/{c.key}/live", HTTP_HX_REQUEST="true")
        return r, len(q)

    def test_queries_do_not_grow_with_rows(self):
        _, small = self.live(self.big(10))
        Collection.objects.all().delete()
        c = self.big(190)
        self.assertEqual(c.rows.count(), 209)
        r, large = self.live(c)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(small, large)
        self.assertLessEqual(large, 18)

    def test_detail_queries_do_not_grow_with_rows(self):
        boss = self.login(self.boss)
        c = self.big(10)
        with CaptureQueriesContext(connection) as q1:
            boss.get(f"{self.base}/c/{c.key}/")
        Collection.objects.all().delete()
        c = self.big(190)
        with CaptureQueriesContext(connection) as q2:
            boss.get(f"{self.base}/c/{c.key}/")
        self.assertEqual(len(q1), len(q2))

    def test_many_missing_folds_into_group_counts(self):
        c = self.big(190)
        r, _ = self.live(c)
        html = r.content.decode()
        self.assertNotIn('data-miss="', html)
        self.assertRegex(html, r"4학년 3\d명")
        self.assertIn("교무실 1명", html)
        self.assertIn("미제출 199", html)
        groups = r.context["miss_groups"]
        self.assertEqual(sum(g["n"] for g in groups), 199)
        self.assertEqual([g["label"] for g in groups][:7], ["1학년", "2학년", "3학년", "4학년", "5학년", "6학년", "교무실"])
        self.assertIn(c.link_token, groups[0]["text"])

    def test_few_missing_keep_person_chips(self):
        c = self.make(aud="all")
        r, _ = self.live(c)
        self.assertContains(r, 'data-miss="')
        self.assertFalse(r.context["miss_groups"])

    def test_unchanged_poll_is_204_then_new_submit_returns_board(self):
        c = self.big(40)
        boss = self.login(self.boss)
        html = boss.get(f"{self.base}/c/{c.key}/").content.decode()
        v = html.split("/live?v=")[1].split('"')[0]
        url = f"{self.base}/c/{c.key}/live?v={v}"
        with CaptureQueriesContext(connection) as q:
            r = boss.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.content, b"")
        self.assertLessEqual(len(q), 8)
        row = c.rows.first()
        Client().post(f"/c/{c.link_token}/u/{row.key}", {"f0": "1", "by": "가"})
        self.assertEqual(boss.get(url, HTTP_HX_REQUEST="true").status_code, 200)

    def test_due_change_refreshes_poll(self):
        c = self.make(aud="all")
        boss = self.login(self.boss)
        v = boss.get(f"{self.base}/c/{c.key}/").content.decode().split("/live?v=")[1].split('"')[0]
        boss.post(f"{self.base}/c/{c.key}/lock")
        self.assertEqual(boss.get(f"{self.base}/c/{c.key}/live?v={v}").status_code, 200)

    def test_stamp_refused_on_other_member_too(self):
        c = self.make(aud="all")
        self.assertEqual(self.login(self.t41).get(f"{self.base}/c/{c.key}/live?v=x").status_code, 404)

    def test_full_flow_csv_rows_match_roster(self):
        """교무실: 전 교직원 취합 1건(만들기 → 링크 제출 → 합본 → CSV)."""
        c = self.make(aud="all")
        link = Client()
        self.assertEqual(link.get(f"/c/{c.link_token}").status_code, 200)
        for i, row in enumerate(c.rows.all()):
            r = link.post(f"/c/{c.link_token}/u/{row.key}", {"f0": str(i + 1), "by": row.who})
            self.assertEqual(r.status_code, 200)
        boss = self.login(self.boss)
        board = boss.get(f"{self.base}/c/{c.key}/live")
        self.assertContains(board, "9</b> / 9명")
        rows = list(csv.reader(io.StringIO(boss.get(f"{self.base}/c/{c.key}/export.csv").content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["직위", "성명", "참가(명)"])
        self.assertEqual([x[1] for x in rows[1:10]], [m.name for m in self.roster])
        self.assertEqual([x[0] for x in rows[1:10]], [m.cls_text for m in self.roster])
        self.assertEqual([x[2] for x in rows[1:10]], [str(i + 1) for i in range(9)])
        copied = boss.get(f"{self.base}/c/{c.key}/copy").json()
        self.assertIn("<table", copied["html"])
        self.assertEqual(copied["tsv"].splitlines()[1].split("\t")[1], self.roster[0].name)


class LinkFindTests(SchoolBase):
    """A. 학교 공간 받는 링크: 명단을 펼치지 않고, 쓴 이름과 정확히 같은 줄만."""

    def setUp(self):
        super().setUp()
        self.c = self.make(aud="all")
        self.link = f"/c/{self.c.link_token}"
        self.others = [m for m in self.roster if m not in (self.t41, self.boss)]  # 걷는 사람(부장)의 이름은 원래 보인다

    def assertNoRoster(self, r):
        html = r.content.decode()
        for m in self.others:
            self.assertNotIn(f">{m.name}", html, m.name)
        for word in ("교무부장", "행정실장", "영양교사", "사서", "/u/", "아직"):
            self.assertNotIn(word, html)
        self.assertNotRegex(html, r'class="st[ "]')  # 줄마다의 낸 여부(전체 낸 수 .status는 그대로)

    def test_first_screen_has_no_names_titles_status_or_row_keys(self):
        r = Client().get(self.link)
        self.assertEqual(r.status_code, 200)
        self.assertNoRoster(r)
        self.assertNotIn(self.t41.name, r.content.decode())
        self.assertNotContains(r, "unitQ")
        self.assertContains(r, f'action="{self.link}/find"')

    def test_exact_name_ignoring_spaces_opens_that_row_for_proxy(self):
        c = Client()
        r = c.post(f"{self.link}/find", {"q": " 사 일 "})
        row = self.c.rows.get(member=self.t41)
        self.assertEqual((r.status_code, r["Location"]), (303, f"{self.link}/u/{row.key}"))
        r = c.post(f"{self.link}/u/{row.key}", {"f0": "3", "by": "옆반"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Cell.objects.get(row=row).by, "옆반")

    def test_partial_name_is_not_a_match(self):
        r = Client().post(f"{self.link}/find", {"q": "사"})
        self.assertContains(r, "이름을 찾지 못했어요.")
        self.assertNoRoster(r)
        self.assertNotContains(r, self.t41.name)

    def test_matched_row_screen_has_no_position(self):
        row = self.c.rows.get(member=self.boss)
        r = Client().get(f"{self.link}/u/{row.key}")
        self.assertContains(r, "부장 선생님")
        self.assertContains(r, ">교무실<")
        self.assertNotContains(r, "교무부장")
        r = Client().post(f"{self.link}/u/{row.key}", {"f0": "1", "by": "부장"})
        self.assertNotContains(r, "교무부장")

    def test_same_name_lists_only_those_rows_with_place(self):
        twin = Member.objects.create(space=self.hub, name="사일", cls="교무실", group="office", position="교무행정사")
        c = self.make(aud="all")
        r = Client().post(f"/c/{c.link_token}/find", {"q": "사일"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f"/c/{c.link_token}/u/{c.rows.get(member=twin).key}")
        self.assertContains(r, f"/c/{c.link_token}/u/{c.rows.get(member=self.t41).key}")
        self.assertEqual(r.content.decode().count("/u/"), 2)
        self.assertNotContains(r, "교무행정사")

    def test_class_lookup_only_for_homeroom_rows(self):
        hr = self.make(aud="groups", aud_groups=["g4"], aud_hr="on")
        row = hr.rows.get(member=self.t42)
        for typed in ("4-2", "4학년 2반", "사이"):
            r = Client().post(f"/c/{hr.link_token}/find", {"q": typed})
            self.assertEqual(r["Location"], f"/c/{hr.link_token}/u/{row.key}", typed)
        self.assertContains(Client().post(f"{self.link}/find", {"q": "4-2"}), "이름을 찾지 못했어요.")

    def test_not_found_with_outside_offers_new_row_keeping_typed(self):
        c = self.make(aud="all", outside="on")
        r = Client().post(f"/c/{c.link_token}/find", {"q": "방과후강사"})
        self.assertContains(r, "내 칸 만들기")
        self.assertContains(r, 'value="방과후강사"')
        n = c.rows.count()
        self.assertEqual(c.rows.count(), n)  # 잘못 쓴 이름으로 줄이 저절로 생기지 않는다
        r = Client().post(f"/c/{c.link_token}/outside", {"name": "방과후강사"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(c.rows.last().who, "방과후강사")

    def test_not_found_without_outside_has_no_new_row_form(self):
        r = Client().post(f"{self.link}/find", {"q": "없는사람"})
        self.assertContains(r, "이름을 찾지 못했어요.")
        self.assertNotContains(r, "내 칸 만들기")

    def test_outside_errors_do_not_list_roster(self):
        c = self.make(aud="all", outside="on")
        r = Client().post(f"/c/{c.link_token}/outside", {"name": ""})
        self.assertEqual(r.status_code, 400)
        self.assertNoRoster(r)
        off = Client().post(f"{self.link}/outside", {"name": "강사"})
        self.assertEqual(off.status_code, 403)
        self.assertNoRoster(off)

    def test_member_goes_to_own_row_with_proxy_box(self):
        c = self.login(self.t41)
        row = self.c.rows.get(member=self.t41)
        r = c.get(self.link)
        self.assertEqual((r.status_code, r["Location"]), (302, f"{self.link}/u/{row.key}"))
        page = c.get(r["Location"])
        self.assertContains(page, "다른 선생님 대신 내기")
        self.assertContains(page, f'action="{self.link}/find"')
        self.assertContains(page, f'{self.link}?find=1')
        self.assertEqual(c.get(f"{self.link}?find=1").status_code, 200)

    def test_member_without_row_gets_find(self):
        hr = self.make(aud="groups", aud_groups=["g4"], aud_hr="on")
        r = self.login(self.boss).get(f"/c/{hr.link_token}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="q"')

    def test_same_name_outside_row_is_not_a_redirect_for_member(self):
        """로그인 회원의 바로 가기는 Row.member로만: 이름이 같다고 남의 줄로 가지 않는다."""
        hr = self.make(aud="groups", aud_groups=["g5"], aud_hr="on")
        Row.objects.create(collection=hr, pos=99, unit="명부 밖", who=self.t41.name)
        self.assertEqual(self.login(self.t41).get(f"/c/{hr.link_token}").status_code, 200)

    def test_pergroup_lists_groups_without_status_or_outside_names(self):
        c = self.make(aud="pergroup", aud_pgroups=["g4", "g5"], outside="on")
        row = c.rows.get(group="g4")
        Client().post(f"/c/{c.link_token}/u/{row.key}", {"f0": "1", "by": "사이"})
        Client().post(f"/c/{c.link_token}/outside", {"name": "방과후강사"})
        r = Client().get(f"/c/{c.link_token}")
        self.assertContains(r, "4학년")
        self.assertContains(r, "5학년")
        self.assertNotRegex(r.content.decode(), r'class="st[ "]')
        self.assertNotContains(r, "아직")
        self.assertNotContains(r, " off")
        self.assertNotContains(r, "방과후강사")
        self.assertNotContains(r, "사이")
        self.assertEqual(Client().post(f"/c/{c.link_token}/find", {"q": "4학년"}).status_code, 404)

    def test_closed_link_closes_find(self):
        Collection.objects.filter(pk=self.c.pk).update(closed_at=timezone.now())
        r = Client().post(f"{self.link}/find", {"q": "사일"})
        self.assertContains(r, "마감됐어요", status_code=403)
        self.assertNotContains(r, "/u/", status_code=403)

    def test_rate_limited(self):
        with mock.patch("collect.submit.FIND_PER_HOUR", 2):
            for _ in range(2):
                Client().post(f"{self.link}/find", {"q": "없음"})
            r = Client().post(f"{self.link}/find", {"q": "사일"})
        self.assertEqual(r.status_code, 429)
        self.assertNotContains(r, "/u/", status_code=429)

    def test_noindex_on_link_responses(self):
        row = self.c.rows.first()
        for r in (Client().get(self.link), Client().post(f"{self.link}/find", {"q": "사일"}),
                  Client().get(f"{self.link}/u/{row.key}"), Client().get("/c/없는링크")):
            self.assertIn("noindex", r["X-Robots-Tag"])

    def test_grade_space_link_stays_list_with_noindex_and_no_find(self):
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        a = Member.objects.create(space=g, name="가나", cls="4-1", position="교사")
        c = Collection.objects.create(space=g, created_by=a, title="t", source="sheet", unit_head=["학년반", "담임"])
        Field.objects.create(collection=c, pos=0, name="칸")
        row = Row.objects.create(collection=c, pos=0, unit="4-1", who="가나", member=a)
        r = Client().get(f"/c/{c.link_token}")
        self.assertContains(r, "가나 선생님")
        self.assertContains(r, f"/u/{row.key}")
        self.assertContains(r, "아직")
        self.assertNotContains(r, 'name="q"')
        self.assertIn("noindex", r["X-Robots-Tag"])
        self.assertEqual(Client().post(f"/c/{c.link_token}/find", {"q": "가나"}).status_code, 404)


class NameMatchTests(SchoolBase):
    """B. 학교 공간 '낼 것'은 Row.member와 그룹 칸으로만."""

    def test_same_name_gets_nothing_from_outside_or_unmatched_row(self):
        c = self.make(aud="groups", aud_groups=["g5"], aud_hr="on")
        row = Row.objects.create(collection=c, pos=99, unit="명부 밖", who=self.t41.name)
        for path in ("/c/?tab=todo", "/today"):
            r = self.login(self.t41).get(self.base + path)
            self.assertNotContains(r, c.link_token)
            self.assertNotContains(r, row.key)

    def test_grade_space_keeps_name_match(self):
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        u = User.objects.create(username="google:가")
        Member.objects.create(space=g, user=u, name="가나", cls="4-1")
        c = Collection.objects.create(space=g, title="t", source="sheet", unit_head=["학년반", "담임"])
        Field.objects.create(collection=c, pos=0, name="칸")
        Row.objects.create(collection=c, pos=0, unit="명부 밖", who="가나")
        cl = Client()
        cl.force_login(u)
        self.assertContains(cl.get(f"/s/{g.token}/c/?tab=todo"), c.link_token)

    def test_unmatched_owners_counted_on_preview_and_board(self):
        Duty.objects.create(space=self.hub, dept="가", task="일", who="사일, 없는이름", order=0)
        boss = self.login(self.boss)
        c = self.make(aud="dept", aud_depts=["가"])  # 옛 부서 범위 취합(새로는 못 고른다)
        self.assertContains(boss.get(f"{self.base}/c/{c.key}/live"), "명부와 짝짓지 못한 담당 1명")
        self.assertEqual(boss.post(f"{self.base}/c/{c.key}/again").status_code, 303)
        self.assertEqual(Collection.objects.latest("pk").audience["unmatched"], 1)

    def test_all_matched_shows_no_warning(self):
        Duty.objects.create(space=self.hub, dept="가", task="일", who="사일", order=0)
        c = self.make(aud="dept", aud_depts=["가"])
        self.assertNotContains(self.login(self.boss).get(f"{self.base}/c/{c.key}/live"), "짝짓지 못한")


class InputTests(SchoolBase):
    """D. 명부·업무분장과 맞은 값만 저장, 이상한 입력에 500 없음(PostgreSQL 기준: NUL이 DB로 가면 ValueError)."""

    def test_only_known_values_are_stored(self):
        Duty.objects.create(space=self.hub, dept="가", task="일", who="사일", order=0)
        r = self.login(self.boss).post(f"{self.base}/c/new", {"src": "sheet", "title": "옛 범위", "col_name_0": "칸", "aud": "dept", "aud_depts": ["가"]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Collection.objects.latest("pk").audience["mode"], "all")  # 부서·소속 고르기는 새로 받지 않는다
        c = self.make(aud="people", aud_people=[self.t41.key, "없는키", self.t41.key])
        self.assertEqual(c.audience["people"], [self.t41.key])

    def test_nul_and_junk_do_not_500(self):
        Duty.objects.create(space=self.hub, dept="가", task="일", who="사일", order=0)
        c = self.make(aud="all", outside="on")
        boss = self.login(self.boss)
        boss.raise_request_exception = False
        nul = "a\x00b"
        for aud in ({"aud": nul}, {"aud": "people", "aud_people": [nul]}, {"aud": "dept", "aud_depts": [nul]},
                    {"aud": "groups", "aud_groups": [nul]}, {"aud": "pergroup", "aud_pgroups": [nul]}):
            self.assertLess(boss.post(f"{self.base}/c/new/aud", aud).status_code, 500, aud)
            r = boss.post(f"{self.base}/c/new", {"src": "sheet", "title": nul, "col_name_0": nul, **aud})
            self.assertLess(r.status_code, 500, aud)
        self.assertLess(boss.post(f"{self.base}/c/new", {"src": "hwp", "title": "x", "hwp": nul}).status_code, 500)
        self.assertLess(boss.post(f"{self.base}/c/new", {"src": nul}).status_code, 500)
        self.assertLess(boss.get(f"{self.base}/c/", {"q": nul, "tab": "all"}).status_code, 500)
        anon = Client(raise_request_exception=False)
        for q in (nul, "\x00", "x" * 5000):
            self.assertLess(anon.post(f"/c/{c.link_token}/find", {"q": q}).status_code, 500)
        self.assertLess(anon.post(f"/c/{c.link_token}/outside", {"name": nul}).status_code, 500)
        self.assertEqual(anon.get("/c/a%00b").status_code, 404)
        self.assertEqual(anon.get(f"/c/{c.link_token}/u/a%00b").status_code, 404)
        self.assertEqual(boss.get(f"{self.base}/c/a%00b/").status_code, 404)
        self.assertEqual(boss.get(f"{self.base}/hwp/a%00b/").status_code, 404)
        self.assertEqual(boss.get(f"{self.base}/hwp/out/a%00b/hwpx").status_code, 404)
        self.assertEqual(boss.get("/s/a%00b/today").status_code, 404)


class RecipientKeepsGradeTests(TestCase):
    def test_grade_space_ignores_audience_and_keeps_rows(self):
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        u = User.objects.create(username="google:가")
        Member.objects.create(space=g, user=u, name="가", cls="4-2")
        Member.objects.create(space=g, name="나", cls="4-1")
        Member.objects.create(space=g, name="다", cls="4-전담")
        self.assertEqual(read_audience(g, {"aud": "all"}), {})
        self.client.force_login(u)
        r = self.client.post(f"/s/{g.token}/c/new", {"src": "sheet", "title": "조사", "col_name_0": "칸", "aud": "all"})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.get()
        self.assertEqual((c.unit_head, c.audience), (["학년반", "담임"], {}))
        self.assertEqual([x.unit for x in Row.objects.filter(collection=c)], ["4-1", "4-2"])
        self.assertEqual(timezone.is_aware(c.created_at), True)
        html = self.client.get(f"/s/{g.token}/c/new").content.decode()
        self.assertNotIn('name="aud"', html)


class SortTests(SchoolBase):
    """QC 취합 받는 사람(2026-11-06): 전 교직원·개인은 줄 순서를 소속순(기본) ↔ 부서별로 고른다."""

    def duties(self):
        for i, (dept, who) in enumerate([("교육과정부", "사일, 부장"), ("방과후부", "오 일, 사일"), ("교육과정부", "행정"), ("정보부", "없는이름")]):
            Duty.objects.create(space=self.hub, dept=dept, task=f"일{i}", who=who, order=i)

    def test_default_is_group_order(self):
        self.duties()
        c = self.make(aud="all")
        self.assertEqual((c.unit_head, [r.member_id for r in c.rows.all()]), (["직위", "성명"], [m.pk for m in self.roster]))
        self.assertNotIn("sort", c.audience)

    def test_all_by_dept(self):
        self.duties()
        c = self.make(aud="all", aud_sort="dept")
        self.assertEqual(c.unit_head, ["부서", "성명"])
        self.assertEqual(c.audience["sort"], "dept")
        self.assertIn("전 교직원 9명 · 사람별 1칸 · 부서별", c.audience["text"])
        got = [(r.unit, r.member_id) for r in c.rows.all()]
        # 표 순서로 부서를 묶고(정보부는 명부 사람이 없어 빠짐), 사일은 처음 나온 교육과정부에만, 부서 없는 사람은 맨 뒤 소속순
        self.assertEqual(got, [("교육과정부", self.t41.pk), ("교육과정부", self.boss.pk), ("교육과정부", self.adm.pk),
                               ("방과후부", self.t51.pk)] + [("부서 없음", m.pk) for m in (self.t42, self.t4x, self.t52, self.meal, self.sup)])

    def test_people_by_dept_only_picked(self):
        self.duties()
        c = self.make(aud="people", aud_people=[self.sup.key, self.t51.key, self.boss.key], aud_sort="dept")
        self.assertEqual([(r.unit, r.who) for r in c.rows.all()], [("교육과정부", "부장"), ("방과후부", "오일"), ("부서 없음", "지원")])
        self.assertEqual(c.audience["text"], "개인 3명 · 사람별 1칸 · 부서별")

    def test_dept_sort_without_duty_is_ignored_and_pergroup_has_no_sort(self):
        c = self.make(aud="all", aud_sort="dept")
        self.assertEqual((c.unit_head, c.audience.get("sort")), (["직위", "성명"], None))
        self.duties()
        c = self.make(aud="pergroup", aud_pgroups=["g4"], aud_sort="dept")
        self.assertEqual((c.unit_head, c.audience.get("sort")), (["그룹", "작성"], None))

    def test_again_keeps_sort(self):
        self.duties()
        c = self.make(aud="all", aud_sort="dept")
        Member.objects.create(space=self.hub, name="새사람", cls="3-1", group="g3")
        self.assertEqual(self.login(self.boss).post(f"{self.base}/c/{c.key}/again").status_code, 303)
        new = Collection.objects.latest("pk")
        self.assertEqual((new.unit_head, new.audience["sort"]), (["부서", "성명"], "dept"))
        self.assertEqual(new.rows.get(who="새사람").unit, "부서 없음")  # 지금 명부로, 부서 없는 사람 묶음에(소속순이라 3학년이 먼저)
        self.assertEqual([r.who for r in new.rows.filter(unit="부서 없음")][:2], ["새사람", "사이"])

    def test_old_groups_and_dept_collections_still_work_on_again(self):
        self.duties()
        for aud in ({"aud": "groups", "aud_groups": ["g4"], "aud_hr": "on"}, {"aud": "dept", "aud_depts": ["방과후부"]}):
            c = self.make(**aud)
            self.assertEqual(self.login(self.boss).post(f"{self.base}/c/{c.key}/again").status_code, 303, aud)
            new = Collection.objects.latest("pk")
            self.assertEqual((new.audience["mode"], new.unit_head, [r.who for r in new.rows.all()]),
                             (c.audience["mode"], c.unit_head, [r.who for r in c.rows.all()]), aud)

    def test_sort_button_and_people_list_grouping(self):
        boss = self.login(self.boss)
        html = boss.get(f"{self.base}/c/new").content.decode()
        self.assertRegex(html, r'<button class="btn sec sm" type="button" data-sort disabled aria-describedby="sortNote"[^>]*>소속순</button>')
        self.assertIn("업무분장이 없어 부서별로 정렬할 수 없어요", html)
        self.assertNotIn("{#", html)  # 템플릿 주석이 화면에 새지 않는다
        self.assertNotIn("_aud_school_in.html", html)
        self.duties()
        html = boss.get(f"{self.base}/c/new").content.decode()
        self.assertRegex(html, r'<button class="btn sec sm" type="button" data-sort aria-label="줄 순서 바꾸기: 지금 소속순">소속순</button>')
        self.assertEqual(re_find(r'<div class="pg"><b>([^<]+)</b>', html), ["4학년", "5학년", "교무실", "행정실", "급식소", "기타/지원"])
        r = boss.post(f"{self.base}/c/new/aud", {"part": "box", "aud": "people", "aud_sort": "dept", "aud_people": [self.t51.key]})
        box = r.content.decode()
        self.assertEqual(re_find(r'<div class="pg"><b>([^<]+)</b>', box), ["교육과정부", "방과후부", "부서 없음"])
        self.assertIn('name="aud_sort" value="dept"', box)
        self.assertIn(">부서별</button>", box)
        self.assertIn(f'value="{self.t51.key}" checked', box)
        self.assertIn('id="lockA" hx-swap-oob="true">부서<', box)  # 직접 입력의 자동 칸 이름도 맞춘다
        self.assertIn("개인 1명 · 사람별 1칸 · 부서별", box)

    def test_sort_row_hidden_for_pergroup(self):
        html = self.login(self.boss).post(f"{self.base}/c/new/aud", {"part": "box", "aud": "pergroup", "aud_pgroups": ["g4"]}).content.decode()
        self.assertIn('<div class="asort" data-aud="all people" hidden>', html)
        self.assertIn('<div class="asort" data-aud="all people">', self.login(self.boss).get(f"{self.base}/c/new").content.decode())

    def test_box_redraw_is_school_only_and_other_space_404(self):
        g = Space.objects.create(school=School.objects.create(neis_atpt="C10", neis_code="3", name="옆옆초"), grade=4, name="옆")
        u = User.objects.create(username="google:옆옆")
        Member.objects.create(space=g, user=u, name="옆옆", cls="4-1")
        c = Client()
        c.force_login(u)
        self.assertEqual(c.post(f"/s/{g.token}/c/new/aud", {"part": "box"}).status_code, 404)
        self.assertEqual(c.post(f"{self.base}/c/new/aud", {"part": "box"}).status_code, 404)
