"""H5 오늘·일정 학교 규모 — 일정 대상(학교 전체·그룹·나), 쓰기 권한, 주간판 마감 차단, 나에게 온 것 / 학교 전체 토글.

불변식
- 보기: 학교 전체는 모두, 그룹은 그 그룹 회원, 나는 본인. 관리자는 일정 화면과 주소로 남의 '나'를 뺀 전부.
- 쓰기: 쓴 사람과 관리자만. 그룹 글자는 권한 근거가 아니다(그룹을 바꿔도 남의 일정을 못 고친다).
- 안 보이는 일정은 주소로도 404, 장소 겹침 문구에도 제목·이름이 안 나온다.
- 주간판 마감과 학교 전체 토글에 남의 link_token·key가 없다.
- 동학년 공간은 대상을 받지 않고 지금처럼 모두에게 보인다.
"""
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from collect.models import Cell, Collection, Field, Row
from spaces.merge import open_school
from spaces.models import Member, School, Space

from .models import Event

User = get_user_model()
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 30, 11, 5, tzinfo=KST)  # 금요일 2블록
TODAY = NOW.date()
UNTIL = date(2099, 1, 1)


class Base(TestCase):
    def setUp(self):
        p = [mock.patch("django.utils.timezone.now", return_value=NOW),
             mock.patch("spaces.neis.meals", return_value=[]),
             mock.patch("spaces.neis.schedule", return_value=[])]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        self.hub = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        self.base = f"/s/{self.hub.token}"
        self.boss = self.seat("교무실", "부장", "office", manager=True)
        self.t41 = self.seat("4-1", "사일", "g4")
        self.t42 = self.seat("4-2", "사이", "g4")
        self.t51 = self.seat("5-1", "오일", "g5")

    def seat(self, cls, name, group, manager=False, space=None):
        u = User.objects.create(username=f"google:{name}", first_name=name)
        return Member.objects.create(space=space or self.hub, user=u, name=name, cls=cls, group=group, is_manager=manager)

    def as_(self, m):
        c = Client()
        c.force_login(m.user)
        return c

    def ev(self, by, title, scope, **kw):
        return Event.objects.create(space=self.hub, by=by, date=TODAY, title=title, scope=scope, **kw)


class ScopeTests(Base):
    def test_who_sees_what_on_today_and_schedule(self):
        cases = {  # 제목 → 보이는 사람
            "온학교행사": (self.ev(self.boss, "온학교행사", "all"), {self.boss, self.t41, self.t42, self.t51}),
            "사학년협의": (self.ev(self.t41, "사학년협의", "g4"), {self.t41, self.t42}),
            "사일혼자일": (self.ev(self.t41, "사일혼자일", "me"), {self.t41}),
        }
        for title, (_, who) in cases.items():
            for m in (self.t41, self.t42, self.t51):
                r = self.as_(m).get(self.base + "/today")
                self.assertEqual(title in r.content.decode(), m in who, (title, m.name, "/today"))
                # 일정은 탭으로 나뉜다(QC 2026-11-06): 세 탭 중 어디에든 보이는지
                html = "".join(self.as_(m).get(f"{self.base}/schedule", {"tab": t}).content.decode() for t in ("school", "group", "me"))
                self.assertEqual(title in html, m in who, (title, m.name, "/schedule"))

    def test_other_group_address_is_404_for_edit_and_delete(self):
        e = self.ev(self.t41, "사학년협의", "g4", place="과학실")
        c = self.as_(self.t51)
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}", {"date": "2026-10-29", "slot": "", "place": ""}).status_code, 404)
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 404)
        e.refresh_from_db()
        self.assertEqual(e.date, TODAY)

    def test_same_group_or_school_wide_is_visible_but_only_writer_and_manager_write(self):
        for scope in ("g4", "all"):
            e = self.ev(self.t41, f"{scope}행사", scope)
            c = self.as_(self.t42)
            self.assertEqual(c.post(f"{self.base}/today/e/{e.key}", {"date": "2026-10-29", "slot": "", "place": ""}).status_code, 403)
            self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 403)
            self.assertTrue(Event.objects.filter(pk=e.pk, date=TODAY).exists())

    def test_changing_my_group_gives_view_but_no_write(self):
        e = self.ev(self.t41, "사학년협의", "g4")
        Member.objects.filter(pk=self.t51.pk).update(group="g4")
        c = self.as_(self.t51)
        self.assertContains(c.get(f"{self.base}/today"), "사학년협의")
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 403)

    def test_manager_writes_all_and_writer_keeps_ownership(self):
        e = self.ev(self.t41, "사학년협의", "g4")
        r = self.as_(self.boss).post(f"{self.base}/today/e/{e.key}", {"date": "2026-11-02", "slot": "", "place": ""})
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual((e.by, e.changed_by, e.date), (self.t41, self.boss, date(2026, 11, 2)))
        # 관리자가 고쳐도 쓴 사람은 그대로 고칠 수 있고, 바뀐 것에는 바꾼 사람(관리자)이 뜬다
        self.assertEqual(self.as_(self.t41).post(f"{self.base}/today/e/{e.key}", {"date": "2026-11-03", "slot": "", "place": ""}).status_code, 200)
        Event.objects.filter(pk=e.pk).update(changed_by=self.boss)
        self.assertContains(self.as_(self.t42).get(f"{self.base}/today"), "<b>부장</b> 선생님이")
        self.assertEqual(self.as_(self.boss).post(f"{self.base}/today/e/{e.key}/delete").status_code, 200)
        self.assertFalse(Event.objects.filter(pk=e.pk).exists())

    def test_writer_less_event_is_manager_only(self):
        e = self.ev(None, "주인없음", "all")
        self.assertEqual(self.as_(self.t41).post(f"{self.base}/today/e/{e.key}/delete").status_code, 403)
        self.assertEqual(self.as_(self.boss).post(f"{self.base}/today/e/{e.key}/delete").status_code, 200)

    def test_managers_board_other_than_today_shows_all_but_not_others_private(self):
        """QC 2026-11-06: 일정은 학교·내 그룹·개인 탭뿐이라 관리자도 남의 그룹 일정은 판에 없다(주소로는 고칠 수 있다)."""
        self.ev(self.t41, "사학년협의", "g4")
        self.ev(self.t41, "사일혼자일", "me")
        c = self.as_(self.boss)
        for tab in ("school", "group", "me"):
            self.assertNotContains(c.get(f"{self.base}/schedule", {"tab": tab}), "사학년협의")
            self.assertNotContains(c.get(f"{self.base}/schedule", {"tab": tab}), "사일혼자일")
        self.assertNotContains(c.get(f"{self.base}/today"), "사학년협의")  # 오늘은 다른 선생님처럼 내 것

    def test_others_private_is_404_for_manager(self):
        e = self.ev(self.t41, "사일혼자일", "me")
        self.assertEqual(self.as_(self.boss).post(f"{self.base}/today/e/{e.key}/delete").status_code, 404)

    def test_read_only_dialog_has_no_form_for_non_writer(self):
        e = self.ev(self.t41, "온학교행사", "all")
        html = self.as_(self.t51).get(f"{self.base}/today").content.decode()
        self.assertIn(f'id="ev-{e.key}"', html)
        self.assertNotIn(f'/today/e/{e.key}"', html)  # 고치기 폼 주소(P2 본문·댓글 주소 /more는 읽는 사람에게도 있다)
        self.assertNotIn(f"/today/e/{e.key}/delete", html)
        self.assertIn("/today/e/" + e.key, self.as_(self.t41).get(f"{self.base}/today").content.decode())

    def test_other_space_event_is_404_even_for_manager(self):
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        e = Event.objects.create(space=g, date=TODAY, title="옆학교일정")
        self.assertEqual(self.as_(self.boss).post(f"{self.base}/today/e/{e.key}/delete").status_code, 404)
        self.assertNotContains(self.as_(self.boss).get(f"{self.base}/schedule"), "옆학교일정")

    def test_changed_feed_is_only_my_range(self):
        self.ev(self.t41, "사학년바뀜", "g4", changed_from=[["날짜", "a", "b"]])
        self.ev(self.boss, "온학교바뀜", "all", changed_from=[["날짜", "a", "b"]])
        r = self.as_(self.t51).get(f"{self.base}/today")
        self.assertNotContains(r, "사학년바뀜")
        self.assertContains(r, "온학교바뀜")
        self.assertNotContains(self.as_(self.boss).get(f"{self.base}/today"), "사학년바뀜")

    def test_place_conflict_does_not_name_events_i_cannot_see(self):
        self.ev(self.t41, "사학년비밀회의", "g4", slot="b2", slot_end="b2", place="과학실")
        c = self.as_(self.t51)
        r = c.post(f"{self.base}/today/parse", {"line": "오늘 2블록 과학실 오학년수업"})
        self.assertContains(r, "겹쳐요")
        self.assertContains(r, "다른 일정")
        self.assertNotContains(r, "사학년비밀회의")
        self.assertNotContains(r, "사일")
        r = c.post(f"{self.base}/today/events", {"line": "오늘 2블록 과학실 오학년수업"}, headers={"HX-Request": "true"})
        self.assertNotContains(r, "사학년비밀회의")
        r = c.get(f"{self.base}/schedule?tab=group")  # 내가 올린 일정(내 그룹)의 겹침 안내(주간판 대화상자)
        self.assertContains(r, "다른 일정")
        self.assertNotContains(r, "사학년비밀회의")


class WriterlessScopeTests(Base):
    def test_saving_writer_less_as_me_makes_manager_the_writer(self):
        e = self.ev(None, "주인없음", "all")
        c = self.as_(self.boss)
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}", {"date": "2026-10-30", "slot": "", "place": "", "scope": "me"}).status_code, 200)
        e.refresh_from_db()
        self.assertEqual((e.scope, e.by), ("me", self.boss))
        self.assertContains(c.get(f"{self.base}/schedule?tab=me"), "주인없음")

    def test_manager_sees_writer_only_label_on_others_event(self):
        self.ev(self.t41, "사학년협의", "g4")
        self.ev(self.boss, "교무실일", "office")  # 관리자가 쓴 것: '나'를 고르면 쓴 사람(자기)만
        boss2 = self.seat("교무실", "교감", "office", manager=True)
        self.assertContains(self.as_(boss2).get(f"{self.base}/schedule?tab=group"), "쓴 사람만")  # 남이 쓴 내 그룹 일정
        self.assertNotContains(self.as_(self.t41).get(f"{self.base}/schedule?tab=group"), "쓴 사람만")
        ownerless = self.ev(None, "주인없음", "all")
        html = self.as_(self.boss).get(f"{self.base}/schedule").content.decode()
        block = html[html.index(f'id="ev-{ownerless.key}"'):]
        block = block[block.index('name="scope"'):]
        self.assertIn('value="me">개인(나만)', block[:block.index("</select>")])


class QueryCountTests(Base):
    """일정·취합이 늘어도 쿼리 수는 그대로."""

    def count(self, path):
        c = self.as_(self.t51)
        with CaptureQueriesContext(connection) as q:
            self.assertEqual(c.get(self.base + path).status_code, 200)
        return len(q)

    def events(self, n):
        for i in range(n):
            Event.objects.create(space=self.hub, by=self.t51, date=TODAY, title=f"e{i}", scope="g5", slot="b2", slot_end="b2",
                                 place="과학실" if i % 2 else "음악실", changed_from=[["날짜", "a", "b"]])

    def colls(self, n):
        for i in range(n):
            c = Collection.objects.create(space=self.hub, created_by=self.boss, title=f"c{i}", source="sheet",
                                          due=NOW + timedelta(hours=i + 1), unit_head=["직위", "성명"])
            Field.objects.create(collection=c, pos=0, name="칸")
            for j, m in enumerate((self.t51, self.t41)):
                Row.objects.create(collection=c, pos=j, unit=m.cls, who=m.name, member=m)

    def test_counts_do_not_grow_with_events_or_collections(self):
        self.events(2)
        self.colls(2)
        paths = ("/schedule", "/today", "/today?show=school")
        small = {p: self.count(p) for p in paths}
        self.events(40)
        self.colls(10)
        self.assertEqual({p: self.count(p) for p in paths}, small)


class AddTests(Base):
    def add(self, m, **kw):
        r = self.as_(m).post(f"{self.base}/today/events", {"line": "오늘 2블록 회의", **kw}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        return Event.objects.latest("pk")

    def test_default_is_my_group_and_anyone_can_post_school_wide(self):
        self.assertEqual(self.add(self.t41).scope, "g4")
        e = self.add(self.t41, scope="all")
        self.assertEqual((e.scope, e.by), ("all", self.t41))
        self.assertContains(self.as_(self.t51).get(f"{self.base}/today"), f'id="ev-{e.key}"')  # 다른 학년에게도 보인다
        self.assertEqual(self.add(self.t41, scope="me").scope, "me")
        self.assertEqual(self.add(self.boss, scope="g5").scope, "office")  # 남의 그룹 범위는 받지 않고 내 그룹으로(QC 2026-11-06 후속)

    def test_bad_scope_falls_back_to_default(self):
        self.assertEqual(self.add(self.t41, scope="<x>").scope, "g4")

    def test_member_without_group_defaults_to_me(self):
        m = self.seat("지원", "무소속", "")
        self.assertEqual(self.add(m).scope, "me")

    def test_writer_can_change_scope_in_dialog(self):
        e = self.ev(self.t41, "사학년협의", "g4")
        r = self.as_(self.t41).post(f"{self.base}/today/e/{e.key}", {"date": "2026-10-30", "slot": "", "place": "", "scope": "all"})
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.scope, "all")
        self.assertEqual(self.as_(self.t41).post(f"{self.base}/today/e/{e.key}", {"date": "2026-10-30", "slot": "", "place": "", "scope": "<x>"}).status_code, 400)

    def test_school_page_has_scope_select(self):
        for path in ("/today", "/schedule"):
            self.assertContains(self.as_(self.t41).get(self.base + path), 'name="scope"')


class GradeSpaceTests(TestCase):
    """동학년 공간은 대상이 없다: 입력도 화면도 그대로, 누구나 고치고 지운다."""

    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]),
                  mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        self.g = Space.objects.create(school=s, grade=4, name="옆")
        self.a = Member.objects.create(space=self.g, user=User.objects.create(username="google:가"), name="가", cls="4-1")
        self.b = Member.objects.create(space=self.g, user=User.objects.create(username="google:나"), name="나", cls="4-2")

    def test_grade_space_is_unchanged(self):
        ca, cb = Client(), Client()
        ca.force_login(self.a.user)
        cb.force_login(self.b.user)
        base = f"/s/{self.g.token}"
        ca.post(f"{base}/today/events", {"line": "오늘 2블록 회의", "scope": "me"}, headers={"HX-Request": "true"})
        e = Event.objects.get()
        self.assertEqual(e.scope, "all")  # 대상을 받지 않는다
        for path in ("/today", "/schedule"):
            r = cb.get(base + path)
            self.assertContains(r, "회의")
            self.assertNotContains(r, 'name="scope"')
            self.assertNotContains(r, "학교 전체")
            self.assertNotContains(r, "나에게 온 것")
        self.assertNotContains(cb.get(f"{base}/today?show=school"), "나에게 온 것")
        # 바꾸면 by가 바꾼 사람으로(지금 그대로), 지우기도 누구나
        self.assertEqual(cb.post(f"{base}/today/e/{e.key}", {"date": "2026-11-02", "slot": "", "place": ""}).status_code, 200)
        e.refresh_from_db()
        self.assertEqual((e.by, e.changed_by), (self.b, None))
        self.assertEqual(ca.post(f"{base}/today/e/{e.key}/delete").status_code, 200)


class MergeTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(neis_atpt="J10", neis_code="7532000", name="합침초")
        self.g4 = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.g5 = Space.objects.create(school=self.school, grade=5, name="5학년")
        self.a = Member.objects.create(space=self.g4, user=User.objects.create(username="google:사"), name="사", cls="4-1")
        self.b = Member.objects.create(space=self.g5, user=User.objects.create(username="google:오"), name="오", cls="5-1")
        for i in range(3):
            Event.objects.create(space=self.g4, by=self.a, date=TODAY, title=f"사학년옛일정{i}")
        for i in range(2):
            Event.objects.create(space=self.g5, by=self.b, date=TODAY, title=f"오학년옛일정{i}")

    def counts(self, hub):
        return {s: Event.objects.in_space(hub).filter(scope=s).count() for s in ("g4", "g5", "all")}

    def test_merge_records_source_grade_and_is_safe_to_rerun(self):
        hub = open_school(self.school, UNTIL, self.a)
        self.assertEqual(self.counts(hub), {"g4": 3, "g5": 2, "all": 0})
        school_wide = Event.objects.create(space=hub, by=self.a, date=TODAY, title="온학교", scope="all")
        late = Event.objects.create(space=self.g5, by=self.b, date=TODAY, title="늦게쓴오학년")
        open_school(self.school, UNTIL)
        self.assertEqual(self.counts(hub), {"g4": 3, "g5": 3, "all": 1})
        school_wide.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual((school_wide.scope, late.scope, late.space), ("all", "g5", hub))

    def test_merged_old_events_show_only_to_that_grade(self):
        hub = open_school(self.school, UNTIL, self.a)
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        with mock.patch("django.utils.timezone.now", return_value=NOW), \
                mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[]):
            c = Client()
            c.force_login(self.b.user)
            for path in ("/today", "/schedule?tab=group"):
                r = c.get(f"/s/{hub.token}{path}")
                self.assertContains(r, "오학년옛일정0")
                self.assertNotContains(r, "사학년옛일정0")
            e = Event.objects.get(title="사학년옛일정0")
            self.assertEqual(c.post(f"/s/{hub.token}/today/e/{e.key}/delete").status_code, 404)


class WeekDueTests(Base):
    """U3(2026-11-11): 일정 화면에는 취합이 없다. 내가 만든 것·내 줄·내 그룹 칸·남의 것 모두 제목·링크가 없다."""

    def coll(self, by, title, due=NOW + timedelta(hours=1), **row):
        c = Collection.objects.create(space=self.hub, created_by=by, title=title, source="sheet", due=due, unit_head=["직위", "성명"],
                                      audience={"mode": "all"})
        Field.objects.create(collection=c, pos=0, name="칸")
        if row:
            Row.objects.create(collection=c, pos=0, unit="칸", **row)
        return c

    def test_week_has_no_collections_at_all(self):
        cs = [self.coll(self.t41, "사학년비밀조사", member=self.t42, who="사이"),
              self.coll(self.t51, "내가만든조사", member=self.t41, who="사일"),
              self.coll(self.t41, "내줄조사", member=self.t51, who="오일"),
              self.coll(self.t41, "내그룹칸조사", who="오학년", group="g5")]
        for m in (self.t51, self.boss):
            html = self.as_(m).get(f"{self.base}/schedule").content.decode()
            for c in cs:
                for s in (c.title, c.key, c.link_token):
                    self.assertNotIn(s, html, (m.name, c.title))


class ToggleTests(Base):
    def coll(self, by, title, **kw):
        c = Collection.objects.create(space=self.hub, created_by=by, title=title, source="sheet",
                                      unit_head=["직위", "성명"], **kw)
        f = Field.objects.create(collection=c, pos=0, name="칸")
        done = Row.objects.create(collection=c, pos=0, unit="4-1", who="제출한사람이름", member=self.t41)
        Cell.objects.create(row=done, field=f, value="값값값", by="제출한사람이름")
        Row.objects.create(collection=c, pos=1, unit="4-2", who="안낸사람이름", member=self.t42)
        return c

    def test_default_is_mine_with_toggle_visible(self):
        self.coll(self.boss, "온학교조사")
        r = self.as_(self.t51).get(f"{self.base}/today")
        self.assertContains(r, "나에게 온 것")
        self.assertContains(r, "학교 전체")
        self.assertNotContains(r, "온학교조사")

    def test_school_wide_for_member_has_title_due_count_only(self):
        c1 = self.coll(self.boss, "온학교조사", due=NOW + timedelta(days=1))
        r = self.as_(self.t51).get(f"{self.base}/today?show=school")
        self.assertContains(r, "온학교조사")
        self.assertContains(r, "10/31(토)")
        self.assertContains(r, "1/2")
        for secret in (c1.link_token, c1.key, "제출한사람이름", "안낸사람이름", "값값값"):
            self.assertNotContains(r, secret)
        for row in c1.rows.all():
            self.assertNotContains(r, row.key)

    def test_school_wide_lists_only_running_in_due_order(self):
        self.coll(self.boss, "나중마감", due=NOW + timedelta(days=3))
        self.coll(self.boss, "먼저마감", due=NOW + timedelta(days=1))
        self.coll(self.boss, "마감없음")
        self.coll(self.boss, "지난조사", due=NOW - timedelta(days=1))
        self.coll(self.boss, "잠근조사", closed_at=NOW - timedelta(hours=1))
        html = self.as_(self.t51).get(f"{self.base}/today?show=school").content.decode()
        self.assertLess(html.index("먼저마감"), html.index("나중마감"))
        self.assertLess(html.index("나중마감"), html.index("마감없음"))
        self.assertNotIn("지난조사", html)
        self.assertNotIn("잠근조사", html)

    def test_only_manager_and_owner_get_detail_link(self):
        c1 = self.coll(self.t41, "사일조사", due=NOW + timedelta(days=1))
        link = f"/c/{c1.key}/"
        self.assertContains(self.as_(self.boss).get(f"{self.base}/today?show=school"), link)
        self.assertContains(self.as_(self.t41).get(f"{self.base}/today?show=school"), link)
        for m in (self.boss, self.t41):
            self.assertNotContains(self.as_(m).get(f"{self.base}/today?show=school"), c1.link_token)
        self.assertEqual(self.as_(self.t51).get(f"{self.base}{link}").status_code, 404)

    def test_other_school_collection_not_listed(self):
        s = School.objects.create(neis_atpt="C10", neis_code="2", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        Collection.objects.create(space=g, title="옆학교조사", source="sheet", unit_head=["학년반", "담임"])
        self.assertNotContains(self.as_(self.boss).get(f"{self.base}/today?show=school"), "옆학교조사")


@override_settings(SITE_URL="https://t.goteacher.xyz")
class MigrationDefaultTests(TestCase):
    def test_default_scope_is_all(self):
        s = School.objects.create(neis_atpt="C10", neis_code="3", name="옆초")
        g = Space.objects.create(school=s, grade=4, name="옆")
        self.assertEqual(Event.objects.create(space=g, date=TODAY, title="x").scope, "all")
