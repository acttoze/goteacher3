"""T2 기관 그룹 = 일정 줄: 그룹 만들기·이름·지우기·배정(채널 화면), 그룹 줄은 공간 전원이 봄, 끈 줄은 내 판에서만 빠짐,
다른 공간 그룹 키 거부, 공지·취합 그룹, 학교 공간 소속 범위·표시 회귀(설계 4절 ②③)."""
import re
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.test import Client, TestCase
from django.utils import timezone
from unittest import mock

from collect.models import Notification
from collect.recipients import audience_rows, audience_text, read_audience
from notices.models import Notice
from today.models import ALL, ME, Event

from . import neis
from .models import Member, MemberLog, School, Space

User = get_user_model()
UNTIL = date(2099, 2, 28)


def google(name, first="김"):
    return User.objects.create(username=f"google:{name}", first_name=first)


def as_(user):
    c = Client()
    c.force_login(user)
    return c


class Base(TestCase):
    def setUp(self):
        cache.clear()
        caches["ratelimit"].clear()
        p = mock.patch.object(neis, "urlopen")
        self.urlopen = p.start()
        self.addCleanup(p.stop)
        self.space, self.boss = self.org("학교마을협력과", "boss")
        self.base = f"/s/{self.space.token}"
        self.c = as_(self.boss.user)
        self.kim = Member.objects.create(space=self.space, user=google("kim", "김"), name="김", cls="미배정", position="주무관")

    def org(self, name, who):
        school = School.objects.create(neis_atpt="E10", neis_code="", name=name, plan="school", paid_until=UNTIL)
        space = Space.objects.create(school=school, kind=Space.Kind.ORG, grade=0, name=name)
        return space, Member.objects.create(space=space, user=google(who, "관리"), name="관리", cls="미배정", is_manager=True)

    def group(self, name, client=None):
        r = (client or self.c).post(self.base + "/school/groups", {"act": "new", "name": name}, HTTP_HX_REQUEST="true")
        self.space.refresh_from_db()
        return r, next((g["k"] for g in self.space.org_groups if g["n"] == name), None)

    def assign(self, member, key):
        return self.c.post(self.base + "/school/arrange", {"what": "members", "group": key, "keys": f'["{member.key}"]'},
                           HTTP_HX_REQUEST="true")


class GroupCrudTests(Base):
    def test_first_group_takes_org_wide_events_and_key_shape(self):
        e = Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="옛 일정", scope=ALL)
        r, k = self.group("학교혁신팀")
        self.assertEqual(r.status_code, 200)
        self.assertRegex(k, r"^t[0-9a-f]{8}$")
        e.refresh_from_db()
        self.assertEqual(e.scope, k)
        self.group("평생교육팀")
        self.assertEqual([n for _, n in self.space.group_choices], ["학교혁신팀", "평생교육팀"])

    def test_only_manager_and_names_checked(self):
        self.assertEqual(self.group("팀", as_(self.kim.user))[0].status_code, 403)
        self.group("팀")
        for bad in ("팀", "", "미배정"):
            self.assertEqual(self.group(bad)[0].status_code, 400, bad)
        self.assertEqual(len(self.space.org_groups), 1)

    def test_rename_updates_member_cls_and_labels(self):
        _, k = self.group("가팀")
        self.assign(self.kim, k)
        e = Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="회의", scope=k)
        self.c.post(self.base + "/school/groups", {"act": "rename", "key": k, "name": "나팀"})
        self.kim.refresh_from_db()
        self.assertEqual((self.kim.group, self.kim.cls), (k, "나팀"))
        self.assertEqual(Event.objects.select_related("space").get(pk=e.pk).scope_label, "나팀")
        self.assertTrue(MemberLog.objects.filter(before="가팀", after="나팀").exists())

    def test_delete_rules(self):
        _, a = self.group("가팀")
        r = self.c.post(self.base + "/school/groups", {"act": "delete", "key": a}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "마지막 그룹", status_code=400)
        _, b = self.group("나팀")
        self.assign(self.kim, a)
        e = Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="회의", scope=a)
        for to in ("", a, "t00000000"):
            r = self.c.post(self.base + "/school/groups", {"act": "delete", "key": a, "to": to}, HTTP_HX_REQUEST="true")
            self.assertContains(r, "옮길 줄", status_code=400)
        self.c.post(self.base + "/school/groups", {"act": "delete", "key": a, "to": b}, HTTP_HX_REQUEST="true")
        self.space.refresh_from_db()
        self.assertEqual(self.space.group_keys, [b])
        e.refresh_from_db()
        self.kim.refresh_from_db()
        self.assertEqual(e.scope, b)
        self.assertEqual((self.kim.group, self.kim.cls), ("", "미배정"))

    def test_school_space_has_no_group_crud(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        m = Member.objects.create(space=hub, user=google("sb"), name="교감", cls="교무실", group="office", is_manager=True)
        self.assertEqual(as_(m.user).post(f"/s/{hub.token}/school/groups", {"act": "new", "name": "팀"}).status_code, 404)


class AssignTests(Base):
    def test_assign_moves_and_logs_and_rings(self):
        _, k = self.group("학교혁신팀")
        r = self.assign(self.kim, k)
        self.assertEqual(r.status_code, 200)
        self.kim.refresh_from_db()
        self.assertEqual((self.kim.group, self.kim.cls, self.kim.position), (k, "학교혁신팀", "주무관"))
        self.assertTrue(MemberLog.objects.filter(name="김", after="학교혁신팀 · 주무관").exists())
        bell = Notification.objects.get(member=self.kim)
        self.assertIn("그룹을 학교혁신팀 · 주무관으로", bell.text)
        self.assertEqual(bell.link, f"{self.base}/channels")
        self.assign(self.kim, "")
        self.kim.refresh_from_db()
        self.assertEqual((self.kim.group, self.kim.cls), ("", "미배정"))

    def test_other_space_key_is_400_and_member_404(self):
        other, other_boss = self.org("다른과", "ob")
        as_(other_boss.user).post(f"/s/{other.token}/school/groups", {"act": "new", "name": "남의팀"})
        other.refresh_from_db()
        foreign = other.group_keys[0]
        self.group("우리팀")
        self.assertEqual(self.assign(self.kim, foreign).status_code, 400)
        self.assertEqual(self.assign(self.kim, "g4").status_code, 400)
        self.assertEqual(as_(other_boss.user).get(f"{self.base}/channels").status_code, 404)

    def test_group_order(self):
        _, a = self.group("가")
        _, b = self.group("나")
        self.c.post(self.base + "/school/arrange", {"what": "groups", "keys": f'["{b}", "{a}"]'}, HTTP_HX_REQUEST="true")
        self.space.refresh_from_db()
        self.assertEqual(self.space.group_keys, [b, a])

    def test_channels_page(self):
        html = self.c.get(self.base + "/channels").content.decode()
        self.assertIn("먼저 그룹을 만들어 주세요", html)
        self.assertIn(f'href="{self.base}/channels"', html)  # 메뉴 '채널'
        _, k = self.group("학교혁신팀")
        html = self.c.get(self.base + "/channels").content.decode()
        self.assertIn('data-arr-in=""', html)
        self.assertIn(f'data-arr-in="{k}"', html)
        self.assertIn(f"/j/{self.space.invite_token}", html)
        member = as_(self.kim.user).get(self.base + "/channels").content.decode()
        self.assertNotIn("data-arr-box", member)
        self.assertNotIn("/school/groups", member)
        self.assertIn('name="position"', member)
        as_(self.kim.user).post(self.base + "/school/me", {"position": "장학사"})
        self.kim.refresh_from_db()
        self.assertEqual(self.kim.position, "장학사")

    def test_manager_and_temp_password_reopened(self):
        r = self.c.post(f"{self.base}/school/members/{self.kim.key}/manager", {"on": "1"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.kim.refresh_from_db()
        self.assertTrue(self.kim.is_manager)


class ScheduleTests(Base):
    def setUp(self):
        super().setUp()
        _, self.a = self.group("가팀")
        _, self.b = self.group("나팀")
        self.lee = Member.objects.create(space=self.space, user=google("lee", "이"), name="이", cls="미배정")  # 미배정
        self.assign(self.kim, self.a)
        self.today = timezone.localdate()

    def ev(self, by, title, scope):
        return Event.objects.create(space=self.space, by=by, date=self.today, title=title, scope=scope)

    def test_group_lines_seen_by_everyone_but_others_me_by_no_one(self):
        self.ev(self.kim, "가팀회의", self.a)
        self.ev(self.kim, "김혼자", ME)
        for m in (self.lee, self.boss):
            c = as_(m.user)
            for path in ("/today", "/schedule", "/schedule?mode=month", "/schedule?mode=day"):
                html = c.get(self.base + path).content.decode()
                self.assertIn("가팀회의", html, (m.name, path))
                self.assertNotIn("김혼자", html, (m.name, path))
        e = Event.objects.get(title="김혼자")
        self.assertEqual(self.c.get(f"{self.base}/today/e/{e.key}/more").status_code, 404)

    def test_board_lines_are_groups_in_order_and_tabs(self):
        html = as_(self.lee.user).get(self.base + "/schedule").content.decode()
        self.assertLess(html.index(">가팀<"), html.index(">나팀<"))
        self.assertNotIn("tab=group", html)
        self.assertNotIn("기관 전체", re.sub(r"<select.*?</select>|<template.*?</template>", "", html, flags=re.S))

    def test_lines_off_hides_only_for_me(self):
        self.ev(self.kim, "가팀회의", self.a)
        c = as_(self.lee.user)
        r = c.post(self.base + "/settings/lines", {"on": [self.b]}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "바꿨어요")
        self.lee.refresh_from_db()
        self.assertEqual(self.lee.off_lines, {self.a})
        self.assertNotIn("가팀회의", c.get(self.base + "/schedule").content.decode())
        self.assertNotIn("가팀회의", c.get(self.base + "/today").content.decode())
        self.assertIn("가팀회의", as_(self.kim.user).get(self.base + "/schedule").content.decode())
        e = Event.objects.get(title="가팀회의")
        self.assertEqual(c.get(f"{self.base}/today/e/{e.key}/more").status_code, 200)  # 권한이 아니다
        c.post(self.base + "/settings/lines", {})  # 다 끄기는 저장 안 함
        self.lee.refresh_from_db()
        self.assertEqual(self.lee.off_lines, {self.a})
        self.assertIn("보이는 그룹 줄", c.get(self.base + "/settings?a=prefs").content.decode())

    def test_writing_scope_rules(self):
        other, ob = self.org("다른과", "ob2")
        as_(ob.user).post(f"/s/{other.token}/school/groups", {"act": "new", "name": "남의팀"})
        other.refresh_from_db()
        foreign = other.group_keys[0]
        c = as_(self.lee.user)
        cell = {"view": "week", "date": self.today.isoformat(), "line": "칸 일정"}
        self.assertEqual(c.post(self.base + "/today/events", {**cell, "scope": foreign}).status_code, 400)
        self.assertEqual(c.post(self.base + "/today/events", {**cell, "scope": ALL}).status_code, 400)
        c.post(self.base + "/today/events", {**cell, "scope": self.b})
        self.assertEqual(Event.objects.get(title="칸 일정").scope, self.b)
        c.post(self.base + "/today/events", {"line": "한 줄", "scope": foreign})  # 이상한 값은 기본 줄로
        self.assertEqual(Event.objects.get(title="한 줄").scope, self.a)  # 미배정이면 첫 줄
        as_(self.kim.user).post(self.base + "/today/events", {"line": "김 한 줄"})
        self.assertEqual(Event.objects.get(title="김 한 줄").scope, self.a)
        e = Event.objects.get(title="한 줄")
        bad = c.post(f"{self.base}/today/e/{e.key}", {"date": self.today.isoformat(), "slot": "", "place": "", "scope": foreign})
        self.assertEqual(bad.status_code, 400)
        c.post(f"{self.base}/today/e/{e.key}", {"date": self.today.isoformat(), "slot": "", "place": "", "scope": self.b})
        e.refresh_from_db()
        self.assertEqual(e.scope, self.b)

    def test_week_paste_foreign_scope_falls_back(self):
        from today.week_paste import _scope
        req = mock.Mock(space=self.space, member=self.kim, POST={"scope": "g4"})
        self.assertEqual(_scope(req), self.a)

    def test_no_groups_yet_has_one_org_line(self):
        space, boss = self.org("새과", "nb")
        Event.objects.create(space=space, by=boss, date=self.today, title="첫 일정", scope=ALL)
        html = as_(boss.user).get(f"/s/{space.token}/schedule").content.decode()
        self.assertIn("기관 전체", html)
        self.assertIn("첫 일정", html)


class NoticeAndCollectTests(Base):
    def test_notice_groups_are_org_keys_only(self):
        _, k = self.group("가팀")
        page = self.c.get(self.base + "/board?new=1").content.decode()
        self.c.post(self.base + "/board/new", {"title": "팀 공지", "body": "b", "aud": "groups", "groups": [k, "g4", "office"]})
        n = Notice.objects.get(title="팀 공지")
        self.assertEqual(n.audience, f",{k},")
        self.assertEqual(n.audience_text, "가팀")
        self.assertIn("그룹 고르기", page + self.c.get(self.base + "/board/new").content.decode())

    def test_collect_pergroup_and_text(self):
        _, a = self.group("가팀")
        _, b = self.group("나팀")
        self.assign(self.kim, a)
        from django.http import QueryDict
        aud = read_audience(self.space, QueryDict(f"aud=pergroup&aud_pgroups={a}&aud_pgroups=g4"))
        self.assertEqual(aud["groups"], [a])
        rows = audience_rows(self.space, aud)
        self.assertEqual([r["unit"] for r in rows], ["가팀"])
        self.assertEqual(audience_text(aud, 1, self.space), "그룹 1곳(가팀) · 그룹별 1칸")
        self.assertTrue(audience_text({"mode": "all"}, 2, self.space).startswith("기관 전체"))
        html = self.c.get(self.base + "/c/new").content.decode()
        self.assertNotIn("예산 취합 만들기", html)
        self.assertNotIn("전 교직원", html)
        self.assertIn("미배정", html)  # 개인 고르기에 미배정 묶음


class SchoolRegressionTests(TestCase):
    """설계 4절 ②: 고정 choices를 뺀 뒤에도 학교 소속 이름이 그대로. ③: 학교 소속 일정은 다른 소속에게 안 보임."""

    def setUp(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        self.t4 = Member.objects.create(space=self.hub, user=google("t4", "사일"), name="사일", cls="4-1", group="g4", is_manager=True)
        self.t5 = Member.objects.create(space=self.hub, user=google("t5", "오일"), name="오일", cls="5-1", group="g5")
        for p in (mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)

    def test_labels_and_scope(self):
        today = timezone.localdate()
        Event.objects.create(space=self.hub, by=self.t4, date=today, title="학교행사", scope=ALL)
        Event.objects.create(space=self.hub, by=self.t4, date=today, title="사학년협의", scope="g4")
        html = as_(self.t5.user).get(f"/s/{self.hub.token}/schedule").content.decode()
        self.assertIn("사일 · 4학년", html)
        self.assertNotIn("사학년협의", "".join(as_(self.t5.user).get(f"/s/{self.hub.token}/schedule", {"tab": t}).content.decode()
                                          for t in ("school", "group", "me")))
        self.assertIn('data-ph-g5="5학년 · 제목 쓰고 Enter"', html)
        self.assertEqual(self.hub.group_label("office"), "교무실")
        n = Notice.objects.create(space=self.hub, title="t", by=self.t4, by_name="사일", group="g4", audience=",g4,office,")
        self.assertEqual((n.group_label, n.audience_text), ("4학년", "4학년·교무실"))
        self.assertEqual(self.t4.space.group_choices[0], ("g1", "1학년"))


class FixTests(Base):
    """T2 수정: 미확인 기관 임시 비밀번호 거부, 그룹 색 길이, 기관에 open_school·동학년 없음, 지울 때 줄 최소 1개 켬, 기관 주 보기 접기."""

    def test_temp_password_refused_until_operator_confirms(self):
        id_user = User.objects.create(username="idmember", first_name="아이디")
        m = Member.objects.create(space=self.space, user=id_user, name="아이디", cls="미배정")
        r = self.c.post(f"{self.base}/school/members/{m.key}/password", HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 403)
        self.assertIn("운영자 확인 뒤", r.content.decode())
        id_user.refresh_from_db()
        self.assertFalse(id_user.has_usable_password() and id_user.check_password(""))
        self.assertNotIn("/password", self.c.get(self.base + "/channels").content.decode())
        School.objects.filter(pk=self.space.school_id).update(org_checked_at=timezone.now())
        self.assertIn(f"/school/members/{m.key}/password", self.c.get(self.base + "/channels").content.decode())
        self.assertEqual(self.c.post(f"{self.base}/school/members/{m.key}/password", HTTP_HX_REQUEST="true").status_code, 200)

    def test_group_color_fits_thirty_groups(self):
        from .models import GROUP_PALETTE
        longest = max(len(k) for k in GROUP_PALETTE)
        stored = ",".join(f"t{i:08x}={'x' * longest}" for i in range(30))
        self.assertLessEqual(len(stored), Space._meta.get_field("group_color").max_length)
        for i in range(30):
            self.group(f"팀{i}")
        for k in self.space.group_keys:
            self.c.post(self.base + "/school/group-color", {"group": k, "color": "purple"}, HTTP_HX_REQUEST="true")
        self.space.refresh_from_db()
        self.assertEqual(len(self.space.group_colors), 30)

    def test_no_open_school_or_grade_space_for_org(self):
        from .merge import open_school
        with self.assertRaises(ValueError):
            open_school(self.space.school, UNTIL, manager=self.boss)
        u = google("gr")
        c = as_(u)
        s = c.session
        s["new_school"] = {"atpt": "E10", "code": "", "name": "학교마을협력과"}
        s.save()
        c.post("/new/class", {"grade": "4", "cls": "4-1", "name": "김"})
        self.assertFalse(Space.objects.filter(kind=Space.Kind.GRADE).exists())

    def test_deleting_group_keeps_one_line_on(self):
        _, a = self.group("가")
        _, b = self.group("나")
        Member.objects.filter(pk=self.kim.pk).update(lines_off=b)
        Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="회의", scope=a)
        self.c.post(self.base + "/school/groups", {"act": "delete", "key": a, "to": b}, HTTP_HX_REQUEST="true")
        self.kim.refresh_from_db()
        self.assertEqual(self.kim.lines_off, "")
        self.assertIn("회의", as_(self.kim.user).get(self.base + "/schedule").content.decode())

    def test_moved_events_not_in_changed_list(self):
        _, a = self.group("가")
        _, b = self.group("나")
        e = Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="회의", scope=a)
        self.c.post(self.base + "/school/groups", {"act": "delete", "key": a, "to": b}, HTTP_HX_REQUEST="true")
        e.refresh_from_db()
        self.assertIsNone(e.changed_from)

    def test_org_week_folds_empty_lines_school_unchanged(self):
        _, a = self.group("가")
        self.group("나")
        Event.objects.create(space=self.space, by=self.kim, date=timezone.localdate(), title="회의", scope=a)
        html = self.c.get(self.base + "/schedule?mode=week").content.decode()
        self.assertIn('class="wgrid org"', html)
        self.assertIn('<div class="lbl e" data-ch=', html)  # 나 줄(일정 없음)
        self.assertIn(f'<div class="lbl" data-ch="{a}">', html)
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        m = Member.objects.create(space=hub, user=google("sx"), name="교감", cls="교무실", group="office", is_manager=True)
        with mock.patch("spaces.neis.schedule", return_value=[]):
            school_html = as_(m.user).get(f"/s/{hub.token}/schedule?mode=week").content.decode()
        self.assertIn('<div class="wgrid" style', school_html)
        self.assertNotIn('class="lbl e"', school_html)
        self.assertNotIn('class=" e"', school_html)
