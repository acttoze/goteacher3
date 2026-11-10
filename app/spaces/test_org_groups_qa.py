"""T2 QA(gt-qa): 구현자 테스트(test_org_groups.py)에 없는 경계만 더한다.
공간 격리(다른 기관 관리자), 권한(일반 회원·만료 플랜·남의 일정 고치기), 끈 줄(월·일 보기, 학교·동학년 거부),
그룹 이름·개수 한도, 지우기 경계(일정 없는 그룹), 취합 받는 사람(기관 전체·사람 고르기에 미배정), 학교 공간 회귀."""
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import Client
from django.utils import timezone

from collect.recipients import audience_rows, audience_text, read_audience
from notices.models import Notice
from today.models import ME, Event

from .models import Member, School, Space
from .test_org_groups import Base, as_, google

User = get_user_model()
UNTIL = date(2099, 2, 28)


class IsolationQATests(Base):
    """다른 기관의 관리자는 이 공간의 그룹·배정·줄 설정·채널 화면에 들어오지 못한다(404)."""

    def test_other_org_manager_gets_404_everywhere(self):
        _, k = self.group("가팀")
        other, ob = self.org("남의과", "qa_ob")
        c = as_(ob.user)
        for path, data in (("/school/groups", {"act": "new", "name": "침입"}),
                           ("/school/groups", {"act": "rename", "key": k, "name": "침입"}),
                           ("/school/groups", {"act": "delete", "key": k}),
                           ("/school/arrange", {"what": "members", "group": k, "keys": f'["{self.kim.key}"]'}),
                           ("/school/group-color", {"group": k, "color": "blue"}),
                           ("/settings/lines", {"on": [k]}),
                           (f"/school/members/{self.kim.key}/manager", {"on": "1"}),
                           (f"/school/members/{self.kim.key}/password", {})):
            self.assertEqual(c.post(self.base + path, data, HTTP_HX_REQUEST="true").status_code, 404, path)
        self.assertEqual(c.get(self.base + "/channels").status_code, 404)
        self.space.refresh_from_db()
        self.kim.refresh_from_db()
        self.assertEqual([g["n"] for g in self.space.org_groups], ["가팀"])
        self.assertEqual((self.kim.group, self.kim.is_manager), ("", False))

    def test_arrange_with_member_key_of_other_space_moves_nobody(self):
        _, k = self.group("가팀")
        other, ob = self.org("남의과", "qa_ob2")
        r = self.c.post(self.base + "/school/arrange", {"what": "members", "group": k, "keys": f'["{ob.key}"]'},
                        HTTP_HX_REQUEST="true")
        self.assertLess(r.status_code, 500)
        ob.refresh_from_db()
        self.assertEqual(ob.group, "")

    def test_event_of_other_org_is_404(self):
        _, k = self.group("가팀")
        e = Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="우리 일정", scope=k)
        other, ob = self.org("남의과", "qa_ob3")
        c = as_(ob.user)
        self.assertEqual(c.get(f"/s/{other.token}/today/e/{e.key}/more").status_code, 404)
        self.assertNotIn("우리 일정", c.get(f"/s/{other.token}/schedule").content.decode())


class PermissionQATests(Base):
    def test_member_cannot_manage_groups_or_assign(self):
        _, k = self.group("가팀")
        c = as_(self.kim.user)
        for data in ({"act": "rename", "key": k, "name": "바꿈"}, {"act": "delete", "key": k}):
            self.assertEqual(c.post(self.base + "/school/groups", data).status_code, 403)
        self.assertEqual(c.post(self.base + "/school/arrange", {"what": "members", "group": k, "keys": f'["{self.kim.key}"]'}).status_code, 403)
        self.assertEqual(c.post(self.base + "/school/arrange", {"what": "groups", "keys": f'["{k}"]'}).status_code, 403)
        self.kim.refresh_from_db()
        self.assertEqual(self.kim.group, "")

    def test_expired_plan_blocks_group_writes_but_page_reads(self):
        _, k = self.group("가팀")
        School.objects.filter(pk=self.space.school_id).update(paid_until=date(2000, 1, 1))
        self.assertEqual(self.c.post(self.base + "/school/groups", {"act": "new", "name": "나팀"}).status_code, 403)
        self.assertEqual(self.assign(self.kim, k).status_code, 403)
        self.assertEqual(self.c.get(self.base + "/channels").status_code, 200)

    def test_unassigned_member_cannot_edit_others_group_event_but_manager_can(self):
        _, a = self.group("가팀")
        _, b = self.group("나팀")
        lee = Member.objects.create(space=self.space, user=google("qa_lee", "이"), name="이", cls="미배정")
        e = Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="김 일정", scope=a)
        form = {"date": e.date.isoformat(), "slot": "", "place": "", "scope": b}
        r = as_(lee.user).post(f"{self.base}/today/e/{e.key}", form)
        self.assertIn(r.status_code, (403, 404))
        self.assertIn(as_(lee.user).post(f"{self.base}/today/e/{e.key}/delete").status_code, (403, 404))
        e.refresh_from_db()
        self.assertEqual(e.scope, a)
        self.c.post(f"{self.base}/today/e/{e.key}", form)
        e.refresh_from_db()
        self.assertEqual(e.scope, b)

    def test_others_me_hidden_from_manager_in_month_print_and_feed(self):
        _, a = self.group("가팀")
        Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="김혼자", scope=ME)
        for path in ("/schedule?mode=month", "/schedule?tab=me", "/today"):
            self.assertNotIn("김혼자", self.c.get(self.base + path).content.decode(), path)
        self.assertIn("김혼자", as_(self.kim.user).get(self.base + "/schedule?tab=me").content.decode())

    def test_temp_password_for_id_member_in_org(self):
        School.objects.filter(pk=self.space.school_id).update(org_checked_at=timezone.now())  # 리드 결정: 미확인 기관은 거부(FixTests)
        idm = Member.objects.create(space=self.space, user=User.objects.create_user("orgid01", password="x-Init-123"),
                                    name="아이디", cls="미배정")
        r = self.c.post(f"{self.base}/school/members/{idm.key}/password", HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "orgid01")
        self.assertEqual(as_(self.kim.user).post(f"{self.base}/school/members/{idm.key}/password").status_code, 403)


class LinesQATests(Base):
    def setUp(self):
        super().setUp()
        _, self.a = self.group("가팀")
        _, self.b = self.group("나팀")
        Event.objects.create(space=self.space, by=self.boss, date=timezone.localdate(), title="가팀회의", scope=self.a)

    def test_off_line_hidden_in_month_and_day_only_for_me(self):
        c = as_(self.kim.user)
        c.post(self.base + "/settings/lines", {"on": [self.b]}, HTTP_HX_REQUEST="true")
        for path in ("/schedule?mode=month", "/schedule?mode=day"):
            self.assertNotIn("가팀회의", c.get(self.base + path).content.decode(), path)
            self.assertIn("가팀회의", self.c.get(self.base + path).content.decode(), path)
        c.post(self.base + "/settings/lines", {"on": [self.a, self.b]})  # 다시 켜면 보인다
        self.kim.refresh_from_db()
        self.assertEqual(self.kim.lines_off, "")
        self.assertIn("가팀회의", c.get(self.base + "/schedule").content.decode())

    def test_foreign_keys_only_are_not_saved_and_new_group_is_on(self):
        c = as_(self.kim.user)
        c.post(self.base + "/settings/lines", {"on": ["g4", "t00000000"]})
        self.kim.refresh_from_db()
        self.assertEqual(self.kim.lines_off, "")
        c.post(self.base + "/settings/lines", {"on": [self.b]})
        _, n = self.group("다팀")
        self.kim.refresh_from_db()
        self.assertNotIn(n, self.kim.off_lines)
        self.assertIn(">다팀<", c.get(self.base + "/schedule").content.decode())

    def test_school_and_grade_spaces_have_no_lines(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531009", name="큰솔초", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=school, kind="school", grade=0, name="큰솔초")
        t = Member.objects.create(space=hub, user=google("qa_t"), name="교사", cls="4-1", group="g4")
        c = as_(t.user)
        self.assertEqual(c.post(f"/s/{hub.token}/settings/lines", {"on": ["g4"]}).status_code, 404)
        self.assertNotIn("보이는 그룹 줄", c.get(f"/s/{hub.token}/settings?a=prefs").content.decode())
        grade = Space.objects.create(school=school, kind="grade", grade=4, name="4학년")
        g = Member.objects.create(space=grade, user=google("qa_g"), name="담임", cls="4-1")
        self.assertEqual(as_(g.user).post(f"/s/{grade.token}/settings/lines", {"on": ["x"]}).status_code, 404)
        self.assertEqual(as_(g.user).get(f"/s/{grade.token}/channels").status_code, 404)


class GroupLimitsQATests(Base):
    def test_rename_rules(self):
        _, a = self.group("가팀")
        _, b = self.group("나팀")
        r = self.c.post(self.base + "/school/groups", {"act": "rename", "key": a, "name": "나팀"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.post(self.base + "/school/groups", {"act": "rename", "key": a, "name": "가팀"}).status_code, 200)
        self.assertEqual(self.c.post(self.base + "/school/groups", {"act": "rename", "key": "t00000000", "name": "x"}).status_code, 400)
        self.c.post(self.base + "/school/groups", {"act": "rename", "key": a, "name": "가" * 50})
        self.space.refresh_from_db()
        self.assertEqual(self.space.group_label(a), "가" * 30)

    def test_thirty_groups_max_and_lines_off_fits(self):
        keys = [self.group(f"팀{i:02d}")[1] for i in range(30)]
        self.assertEqual(self.group("넘침")[0].status_code, 400)
        c = as_(self.kim.user)
        c.post(self.base + "/settings/lines", {"on": [keys[0]]})
        self.kim.refresh_from_db()
        self.assertEqual(self.kim.off_lines, set(keys[1:]))

    def test_delete_empty_group_needs_no_target_and_resets_order(self):
        _, a = self.group("가팀")
        _, b = self.group("나팀")
        self.assign(self.kim, b)
        r = self.c.post(self.base + "/school/groups", {"act": "delete", "key": b}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.kim.refresh_from_db()
        self.assertEqual((self.kim.group, self.kim.cls, self.kim.order), ("", "미배정", None))
        self.space.refresh_from_db()
        self.assertEqual(self.space.group_keys, [a])

    def test_me_events_of_deleted_group_member_untouched(self):
        _, a = self.group("가팀")
        _, b = self.group("나팀")
        mine = Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="김혼자", scope=ME)
        self.c.post(self.base + "/school/groups", {"act": "delete", "key": a})
        mine.refresh_from_db()
        self.assertEqual(mine.scope, ME)


class CollectQATests(Base):
    def test_all_and_people_include_unassigned(self):
        _, a = self.group("가팀")
        lee = Member.objects.create(space=self.space, user=google("qa_lee2", "이"), name="이", cls="미배정")
        self.assign(self.kim, a)
        rows = audience_rows(self.space, {"mode": "all"})
        self.assertEqual({r["member"].pk for r in rows}, {self.boss.pk, self.kim.pk, lee.pk})
        self.assertTrue(audience_text({"mode": "all"}, len(rows), self.space).startswith("기관 전체 3명"))
        aud = read_audience(self.space, QueryDict(f"aud=people&aud_people={lee.key}&aud_people=zzz"))
        self.assertEqual([r["member"].pk for r in audience_rows(self.space, aud)], [lee.pk])

    def test_pergroup_order_follows_channel_order(self):
        _, a = self.group("가팀")
        _, b = self.group("나팀")
        self.c.post(self.base + "/school/arrange", {"what": "groups", "keys": f'["{b}", "{a}"]'}, HTTP_HX_REQUEST="true")
        self.space.refresh_from_db()
        aud = read_audience(self.space, QueryDict(f"aud=pergroup&aud_pgroups={a}&aud_pgroups={b}"))
        self.assertEqual([r["unit"] for r in audience_rows(self.space, aud)], ["나팀", "가팀"])
        self.assertEqual(audience_text(aud, 2, self.space), "그룹 2곳(전 그룹) · 그룹별 1칸")


class SchoolRegressionQATests(Base):
    """학교 공간: 소속 일정 범위·소속 이름·공지 받는 곳·취합 소속순이 T2 전과 같다."""

    def setUp(self):
        super().setUp()
        school = School.objects.create(neis_atpt="J10", neis_code="7531008", name="한솔초", plan="school", paid_until=UNTIL)
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="한솔초", group_order="office,g1")
        self.t4 = Member.objects.create(space=self.hub, user=google("qa_s4", "사"), name="사", cls="4-1", group="g4")
        self.t5 = Member.objects.create(space=self.hub, user=google("qa_s5", "오"), name="오", cls="5-1", group="g5")
        self.unit = Member.objects.create(space=self.hub, user=google("qa_sx"), name="무", cls="교무실", group="office")
        for p in (mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)

    def test_group_event_stays_within_group_for_members_and_unassigned_style(self):
        Event.objects.create(space=self.hub, by=self.t4, date=timezone.localdate(), title="사학년만", scope="g4")
        for m in (self.t5, self.unit):
            html = "".join(as_(m.user).get(f"/s/{self.hub.token}{p}").content.decode()
                           for p in ("/today", "/schedule", "/schedule?mode=month", "/schedule?mode=day"))
            self.assertNotIn("사학년만", html, m.name)
        self.assertIn("사학년만", as_(self.t4.user).get(f"/s/{self.hub.token}/schedule?tab=group").content.decode())

    def test_school_scope_rejects_org_style_keys(self):
        c = as_(self.t4.user)
        c.post(f"/s/{self.hub.token}/today/events",
               {"view": "week", "date": timezone.localdate().isoformat(), "line": "남의 소속", "scope": "g5"})
        self.assertFalse(Event.objects.filter(title="남의 소속", scope="g5").exists())

    def test_school_names_notice_and_collect_order(self):
        self.assertEqual(self.hub.group_keys[:2], ["office", "g1"])
        self.assertEqual(audience_text({"mode": "all"}, 3, self.hub), audience_text({"mode": "all"}, 3))
        self.assertTrue(audience_text({"mode": "all"}, 3, self.hub).startswith("전 교직원"))
        aud = read_audience(self.hub, QueryDict("aud=pergroup&aud_pgroups=g4&aud_pgroups=office"))
        self.assertEqual([r["unit"] for r in audience_rows(self.hub, aud)], ["교무실", "4학년"])
        c = as_(self.t4.user)
        page = c.get(f"/s/{self.hub.token}/board?new=1").content.decode()
        self.assertIn("4학년", page)
        self.assertIn("교무실", page)
        c.post(f"/s/{self.hub.token}/board/new", {"title": "학년 공지", "body": "b", "aud": "groups", "groups": ["g4", "t00000000"]})
        n = Notice.objects.get(title="학년 공지")
        self.assertEqual((n.audience, n.audience_text), (",g4,", "4학년"))
        self.assertNotIn("학년 공지", as_(self.t5.user).get(f"/s/{self.hub.token}/board").content.decode())
        self.assertIn(c.post(f"/s/{self.hub.token}/school/groups", {"act": "new", "name": "x"}).status_code, (403, 404))


class AnonymousQATests(Base):
    def test_anonymous_cannot_reach_channels_or_lines(self):
        c = Client()
        for path in ("/channels", "/settings?a=prefs"):
            self.assertIn(c.get(self.base + path).status_code, (302, 403, 404), path)
        self.assertIn(c.post(self.base + "/school/groups", {"act": "new", "name": "x"}).status_code, (302, 403, 404))
        self.space.refresh_from_db()
        self.assertEqual(self.space.org_groups, [])
