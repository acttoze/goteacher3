"""H5 독립 검증(gt-qa): test_school.py에 없는 경계만. 관리 명령으로 합치기, 실제 그룹 고치기 경로, 대상 올리기·바꾸기 시도,
옛 주소·다른 학교 주소, 응답 원문의 키, 알림, 플랜 만료·끄기, 동학년 공간에 대상 값을 보내는 시도."""
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase

from collect.models import Cell, Collection, Field, Notification, Row
from spaces.merge import open_school
from spaces.models import Member, School, Space

from .models import Event
from .test_school import NOW, TODAY, UNTIL, Base

User = get_user_model()
EDIT = {"date": "2026-11-02", "slot": "", "place": ""}


class GroupEditQa(Base):
    """그룹은 /school/me에서 본인이 고친다. 보기만 따라가고 권한은 따라가지 않는다."""

    def move(self, m, **data):
        return self.as_(m).post(f"{self.base}/school/me", data)

    def test_group_cannot_be_set_to_scope_words(self):
        secret = self.ev(self.t41, "사일혼자일", "me")
        wide = self.ev(self.t41, "사학년협의", "g4")
        for bad in ("me", "all", "", "<x>"):
            self.assertEqual(self.move(self.t51, group=bad, role_custom="x").status_code, 400, bad)
            self.t51.refresh_from_db()
            self.assertEqual(self.t51.group, "g5", bad)
        c = self.as_(self.t51)
        self.assertEqual(c.post(f"{self.base}/today/e/{secret.key}/delete").status_code, 404)
        self.assertEqual(c.post(f"{self.base}/today/e/{wide.key}/delete").status_code, 404)

    def test_moving_in_gives_view_no_write_and_moving_out_keeps_my_own(self):
        e = self.ev(self.t41, "사학년협의", "g4")
        other = self.ev(self.t42, "사이학년일", "g4")
        self.assertEqual(self.move(self.t51, group="g4", cls="4-3").status_code, 302)
        self.t51.refresh_from_db()
        self.assertEqual(self.t51.group, "g4")
        c = self.as_(self.t51)
        self.assertContains(c.get(f"{self.base}/today"), "사학년협의")
        for data in (EDIT, {**EDIT, "scope": "all"}, {**EDIT, "view": "week"}):
            self.assertEqual(c.post(f"{self.base}/today/e/{e.key}", data).status_code, 403)
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 403)
        e.refresh_from_db()
        self.assertEqual((e.date, e.scope, e.by, e.changed_by), (TODAY, "g4", self.t41, None))

        self.assertEqual(self.move(self.t41, group="g5", cls="5-3").status_code, 302)  # 쓴 사람이 옮겨 가도
        self.t41.refresh_from_db()
        c = self.as_(self.t41)
        self.assertContains(c.get(f"{self.base}/today"), "사학년협의")
        self.assertNotContains(c.get(f"{self.base}/today"), "사이학년일")
        self.assertEqual(c.post(f"{self.base}/today/e/{other.key}/delete").status_code, 404)
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}", EDIT).status_code, 200)

    def test_group_is_not_manager(self):
        """교무실·행정실 그룹이어도 관리자 표시가 없으면 쓰기 권한이 없다."""
        office = self.seat("교무실", "실무사", "office")
        e = self.ev(self.boss, "교무회의", "office")
        wide = self.ev(self.t41, "온학교", "all")
        c = self.as_(office)
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 403)
        self.assertEqual(c.post(f"{self.base}/today/e/{wide.key}/delete").status_code, 403)


class ReachQa(Base):
    def test_raw_response_has_no_key_title_or_name_of_out_of_range_events(self):
        out = [self.ev(self.t41, "사학년비밀", "g4", place="사학년실", target="사학년대상"), self.ev(self.t41, "혼자비밀", "me")]
        for m in (self.t51, self.seat("지원", "무소속", "")):
            c = self.as_(m)
            for path in ("/today", "/today?show=school", "/schedule", f"/schedule?w={TODAY:%Y-%m-%d}"):
                html = c.get(self.base + path).content.decode()
                for e in out:
                    for s in (e.key, e.title, e.place or e.title, e.target or e.title):
                        self.assertNotIn(s, html, (m.name, path))

    def test_multiday_other_group_is_hidden_on_every_day(self):
        self.ev(self.t41, "사학년수련", "g4", end=TODAY + timedelta(days=4))
        c = self.as_(self.t51)
        self.assertNotContains(c.get(f"{self.base}/schedule?w=2026-11-02"), "사학년수련")

    def test_group_less_member_sees_school_wide_and_own_only(self):
        m = self.seat("지원", "무소속", "")
        self.ev(self.t41, "사학년일", "g4")
        self.ev(self.t41, "온학교", "all")
        e = Event.objects.create(space=self.hub, by=m, date=TODAY, title="내것", scope="me")
        html = self.as_(m).get(f"{self.base}/today").content.decode()
        self.assertIn("온학교", html)
        self.assertIn("내것", html)
        self.assertNotIn("사학년일", html)
        self.assertEqual(self.as_(m).post(f"{self.base}/today/e/{e.key}", EDIT).status_code, 200)

    def test_managers_own_private_is_hidden_from_others_and_visible_to_self(self):
        e = self.ev(self.boss, "부장혼자", "me")
        self.assertContains(self.as_(self.boss).get(f"{self.base}/today"), "부장혼자")
        other_boss = self.seat("교무실", "교감", "office", manager=True)
        c = self.as_(other_boss)
        self.assertNotContains(c.get(f"{self.base}/schedule"), "부장혼자")
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}", EDIT).status_code, 404)

    def test_conflict_with_others_private_is_anonymous_even_for_manager(self):
        self.ev(self.t41, "혼자비밀", "me", slot="b2", slot_end="b2", place="과학실")
        c = self.as_(self.boss)
        for path, headers in (("/today/parse", {}), ("/today/events", {"HX-Request": "true"})):
            r = c.post(self.base + path, {"line": "오늘 2블록 과학실 부장회의"}, headers=headers)
            self.assertNotContains(r, "다른 일정")  # QC 2026-11-06: 남의 개인 일정은 겹침 판정에서도 뺀다
            self.assertNotContains(r, "겹쳐요")
            self.assertNotContains(r, "혼자비밀")
            self.assertNotContains(r, ", 사일)")  # 겹침 문구의 이름 자리("학사일정"과 헷갈리지 않게)
        r = c.post(f"{self.base}/today/events", {"line": "오늘 2블록 과학실 또회의", "view": "week"}, headers={"HX-Request": "true"})
        self.assertNotContains(r, "혼자비밀")

    def test_conflict_names_events_i_can_reach(self):
        self.ev(self.t41, "온학교수업", "all", slot="b2", slot_end="b2", place="과학실")
        r = self.as_(self.t51).post(f"{self.base}/today/parse", {"line": "오늘 2블록 과학실 오학년수업"})
        self.assertContains(r, "온학교수업")

    def test_writer_less_conflict_and_dialog_do_not_break(self):
        self.ev(None, "주인없음", "all", slot="b2", slot_end="b2", place="과학실")
        c = self.as_(self.t51)
        self.assertContains(c.post(f"{self.base}/today/parse", {"line": "오늘 2블록 과학실 수업"}), "알 수 없음")
        self.assertContains(c.get(f"{self.base}/today"), "주인없음")
        self.assertContains(c.get(f"{self.base}/schedule"), "주인없음")

    def test_other_school_token_and_key_swap_is_404(self):
        s = School.objects.create(neis_atpt="C10", neis_code="9", name="옆초", plan="school", paid_until=UNTIL)
        far = Space.objects.create(school=s, kind="school", grade=0, name="옆초")
        fm = Member.objects.create(space=far, user=User.objects.create(username="google:옆"), name="옆", cls="4-1", group="g4", is_manager=True)
        far_ev = Event.objects.create(space=far, by=fm, date=TODAY, title="옆학교일", scope="all")
        mine = self.ev(self.t41, "온학교", "all")
        c = self.as_(self.boss)  # 내 학교 관리자가 옆 학교 토큰으로
        for path in ("/today", "/today?show=school", "/schedule"):
            self.assertEqual(c.get(f"/s/{far.token}{path}").status_code, 404)
        self.assertEqual(c.post(f"/s/{far.token}/today/events", {"line": "오늘 침입"}).status_code, 404)
        self.assertEqual(c.post(f"/s/{far.token}/today/e/{far_ev.key}/delete").status_code, 404)
        self.assertEqual(c.post(f"{self.base}/today/e/{far_ev.key}/delete").status_code, 404)  # 내 토큰 + 옆 학교 일정
        self.assertEqual(self.as_(fm).post(f"/s/{far.token}/today/e/{mine.key}/delete").status_code, 404)  # 옆 토큰 + 내 일정
        self.assertTrue(Event.objects.filter(pk__in=[far_ev.pk, mine.pk]).count() == 2)

    def test_anonymous_cannot_write_or_read(self):
        e = self.ev(self.t41, "온학교", "all")
        c = Client()
        self.assertEqual(c.get(f"{self.base}/today").status_code, 302)
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 302)
        self.assertTrue(Event.objects.filter(pk=e.pk).exists())

    def test_get_on_write_address_is_405(self):
        e = self.ev(self.t41, "온학교", "all")
        c = self.as_(self.t41)
        self.assertEqual(c.get(f"{self.base}/today/e/{e.key}").status_code, 405)
        self.assertEqual(c.get(f"{self.base}/today/e/{e.key}/delete").status_code, 405)


class BellQa(Base):
    def test_events_make_no_notification_and_bell_has_no_event_text(self):
        c = self.as_(self.t41)
        c.post(f"{self.base}/today/events", {"line": "오늘 2블록 비밀협의", "scope": "g4"}, headers={"HX-Request": "true"})
        e = Event.objects.get(title__contains="비밀협의")
        c.post(f"{self.base}/today/e/{e.key}", EDIT)
        self.assertEqual(Notification.objects.count(), 0)
        for m in (self.t42, self.t51, self.boss):
            self.assertNotContains(self.as_(m).get(f"{self.base}/bell"), "비밀협의")


class PlanQa(Base):
    def test_expired_plan_keeps_today_schedule_and_scope(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        e = self.ev(self.t41, "사학년협의", "g4")
        c = self.as_(self.t51)
        for path in ("/today", "/today?show=school", "/schedule"):
            self.assertEqual(c.get(self.base + path).status_code, 200)
        self.assertNotContains(c.get(f"{self.base}/today"), "사학년협의")
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 404)
        r = c.post(f"{self.base}/today/events", {"line": "오늘 만료뒤일정", "scope": "g5"}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Event.objects.get(title="만료뒤일정").scope, "g5")

    def test_plan_off_keeps_scope_rules(self):
        School.objects.filter(pk=self.school.pk).update(plan="free")
        e = self.ev(self.t41, "사학년협의", "g4")
        c = self.as_(self.t51)
        self.assertNotContains(c.get(f"{self.base}/today"), "사학년협의")
        self.assertEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 404)


class MergeCommandQa(TestCase):
    """관리 명령 그대로. 건수·다른 학교·한 사람 두 자리·손님·옛 주소."""

    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]),
                  mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7533000", name="합침초")
        self.g4 = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.g5 = Space.objects.create(school=self.school, grade=5, name="5학년")
        self.boss_user = User.objects.create(username="google:부장", first_name="부장")
        self.boss = Member.objects.create(space=self.g4, user=self.boss_user, name="부장", cls="4-1")
        self.a2 = Member.objects.create(space=self.g4, user=User.objects.create(username="google:사이"), name="사이", cls="4-2")
        self.guest = Member.objects.create(space=self.g4, user=User.objects.create(username="guest:abc"), name="손님", cls="4-3")
        self.b = Member.objects.create(space=self.g5, user=User.objects.create(username="google:오"), name="오", cls="5-1")
        self.twin = Member.objects.create(space=self.g5, user=self.a2.user, name="사이", cls="5-전담")  # 한 사람 두 자리
        self.far_school = School.objects.create(neis_atpt="C10", neis_code="8", name="옆초")
        self.far = Space.objects.create(school=self.far_school, grade=4, name="옆")
        self.far_ev = Event.objects.create(space=self.far, date=TODAY, title="옆학교일정")
        for i in range(4):
            Event.objects.create(space=self.g4, by=self.boss, date=TODAY, title=f"사학년옛{i}")
        Event.objects.create(space=self.g4, by=None, date=TODAY, title="사학년주인없음")
        Event.objects.create(space=self.g5, by=self.b, date=TODAY, title="오학년옛0")
        self.twin_ev = Event.objects.create(space=self.g5, by=self.twin, date=TODAY, title="오학년쌍둥이")
        self.key_of = {e.title: e.key for e in Event.objects.all()}

    def run_cmd(self):
        call_command("school_plan", "7533000", "--on", "--until", "2099-01-01", f"--manager={self.boss.key}", stdout=StringIO())

    def login(self, m_user):
        c = Client()
        c.force_login(m_user)
        return c

    def state(self):
        return sorted(Event.objects.exclude(pk=self.far_ev.pk).values_list("key", "title", "scope", "space", "by"))

    def test_counts_rerun_and_other_school(self):
        self.run_cmd()
        hub = Space.objects.get(school=self.school, kind="school")
        self.assertEqual(Event.objects.filter(space=hub, scope="g4").count(), 5)
        self.assertEqual(Event.objects.filter(space=hub, scope="g5").count(), 2)
        self.assertEqual(Event.objects.filter(space__in=[self.g4, self.g5]).count(), 0)
        self.assertEqual(Event.objects.filter(space=hub).exclude(scope__in=["g4", "g5"]).count(), 0)
        before = self.state()
        self.assertEqual({e.key for e in Event.objects.filter(space=hub)}, set(self.key_of.values()) - {self.far_ev.key})
        self.run_cmd()
        self.run_cmd()
        self.assertEqual(self.state(), before)
        self.far_ev.refresh_from_db()
        self.assertEqual((self.far_ev.scope, self.far_ev.space), ("all", self.far))

    def test_visibility_after_merge_including_guest_and_twin(self):
        self.run_cmd()
        hub = Space.objects.get(school=self.school, kind="school")
        base = f"/s/{hub.token}"
        b_html = self.login(self.b.user).get(f"{base}/today").content.decode()
        self.assertIn("오학년옛0", b_html)
        self.assertNotIn("사학년옛0", b_html)
        self.assertNotIn(self.key_of["사학년주인없음"], b_html)
        guest = self.login(self.guest.user)
        self.assertContains(guest.get(f"{base}/today"), "사학년옛0")
        self.assertNotContains(guest.get(f"{base}/today"), "오학년옛0")
        self.assertEqual(guest.post(f"{base}/today/e/{self.key_of['사학년옛0']}/delete").status_code, 403)
        # 두 자리였던 사람: 지운 자리의 일정이 남은 자리로 옮겨 와 계속 고친다
        twin = self.login(self.a2.user)
        self.assertEqual(twin.post(f"{base}/today/e/{self.key_of['오학년쌍둥이']}", EDIT).status_code, 200)
        # 관리자: 쓴 사람 없는 옛 일정도 지운다
        self.assertEqual(self.login(self.boss_user).post(f"{base}/today/e/{self.key_of['사학년주인없음']}/delete").status_code, 200)

    def test_old_address_write_is_not_executed_and_outsider_gets_404(self):
        self.run_cmd()
        key = self.key_of["사학년옛0"]
        c = self.login(self.b.user)  # 5학년이 옛 4학년 주소로
        r = c.post(f"/s/{self.g4.token}/today/e/{key}/delete")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(Event.objects.filter(key=key).exists())
        r = c.get(f"/s/{self.g4.token}/today", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "사학년옛0")
        outsider = self.login(User.objects.create(username="google:남"))
        self.assertEqual(outsider.get(f"/s/{self.g4.token}/today").status_code, 404)


class LeakQa(Base):
    def coll(self, by, title, due=NOW + timedelta(hours=1), **row):
        c = Collection.objects.create(space=self.hub, created_by=by, title=title, source="sheet", due=due, unit_head=["직위", "성명"],
                                      audience={"mode": "all"})
        f = Field.objects.create(collection=c, pos=0, name="칸")
        if row:
            r = Row.objects.create(collection=c, pos=0, unit="칸", **row)
            Cell.objects.create(row=r, field=f, value="제출값값", by="제출자이름")
        return c

    def test_week_for_group_less_member_has_no_others_collection(self):
        """그룹 없는 회원: 빈 그룹 값이 그룹 없는 줄과 맞아 버리지 않아야 한다(링크 없음).
        U3(2026-11-11): 일정에는 취합 제목도 없다."""
        m = self.seat("지원", "무소속", "")
        c = self.coll(self.t41, "남의조사", who="이름만")  # group="" member=None
        html = self.as_(m).get(f"{self.base}/schedule").content.decode()
        for s in (c.title, c.key, c.link_token):
            self.assertNotIn(s, html)

    def test_week_raw_response_has_no_others_tokens_for_member_and_manager(self):
        other = self.coll(self.t41, "사학년비밀조사", member=self.t42, who="사이")
        row = other.rows.get()
        for m in (self.t51, self.boss):  # U3: 일정에는 취합 제목·링크·관리 key 모두 없다
            html = self.as_(m).get(f"{self.base}/schedule").content.decode()
            for s in (other.title, other.link_token, row.key, "제출값값", other.key):
                self.assertNotIn(s, html, m.name)

    def test_toggle_raw_response_has_no_tokens_for_member(self):
        other = self.coll(self.t41, "사학년조사", member=self.t42, who="사이")
        row = other.rows.get()
        html = self.as_(self.t51).get(f"{self.base}/today?show=school").content.decode()
        for s in (other.key, other.link_token, row.key, "제출값값", "제출자이름"):
            self.assertNotIn(s, html)
        self.assertNotIn(f'/c/{other.key}', html)

    def test_toggle_shows_my_row_without_link_when_not_owner(self):
        """내 줄이 있어도 토글 목록에는 받는 링크를 싣지 않는다(내기는 '내가 낼 것'에서)."""
        mine = self.coll(self.t41, "내줄있는조사", member=self.t51, who="오일")
        html = self.as_(self.t51).get(f"{self.base}/today?show=school").content.decode()
        self.assertNotIn(mine.link_token, html)


class GradeSpaceQa(TestCase):
    """동학년 공간: 대상 값을 보내도 받지 않고, 다른 학교가 합쳐져도 이 공간의 일정은 그대로다."""

    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]),
                  mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        s = School.objects.create(neis_atpt="C10", neis_code="4", name="무료초")
        self.g = Space.objects.create(school=s, grade=4, name="무료4")
        self.a = Member.objects.create(space=self.g, user=User.objects.create(username="google:가"), name="가", cls="4-1")
        self.b = Member.objects.create(space=self.g, user=User.objects.create(username="guest:나"), name="나", cls="4-2")
        self.base = f"/s/{self.g.token}"

    def login(self, m):
        c = Client()
        c.force_login(m.user)
        return c

    def test_edit_ignores_scope_and_guest_can_write_everything(self):
        e = Event.objects.create(space=self.g, by=self.a, date=TODAY, title="협의")
        r = self.login(self.b).post(f"{self.base}/today/e/{e.key}", {**EDIT, "scope": "me"})
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual((e.scope, e.by), ("all", self.b))
        self.assertEqual(self.login(self.a).post(f"{self.base}/today/e/{e.key}/delete").status_code, 200)

    def test_changing_group_value_has_no_effect_on_grade_space(self):
        e = Event.objects.create(space=self.g, by=self.a, date=TODAY, title="협의", scope="all")
        Member.objects.filter(pk=self.b.pk).update(group="g5")
        self.assertContains(self.login(self.b).get(f"{self.base}/today"), "협의")
        self.assertEqual(self.login(self.b).post(f"{self.base}/today/e/{e.key}/delete").status_code, 200)

    def test_other_school_merge_leaves_this_space_alone(self):
        Event.objects.create(space=self.g, by=self.a, date=TODAY, title="협의")
        other = School.objects.create(neis_atpt="J10", neis_code="5", name="켠학교")
        seat = Member.objects.create(space=Space.objects.create(school=other, grade=4, name="켠4"),
                                     user=get_user_model().objects.create(username="google:other"), name="다른", cls="4-1")
        open_school(other, UNTIL, seat)
        e = Event.objects.get(title="협의")
        self.assertEqual((e.space, e.scope), (self.g, "all"))
        self.g.refresh_from_db()
        self.assertIsNone(self.g.merged_into)


class WriterlessPrivateQa(Base):
    def test_manager_cannot_orphan_writer_less_event(self):
        e = self.ev(None, "주인없음", "all")
        c = self.as_(self.boss)
        c.post(f"{self.base}/today/e/{e.key}", {"date": "2026-10-30", "slot": "", "place": "", "scope": "me"})
        self.assertNotEqual(c.post(f"{self.base}/today/e/{e.key}/delete").status_code, 404)


class MergeFeedQa(TestCase):
    def test_merge_does_not_bump_updated_at(self):
        """합쳐도 옛 일정이 '바뀐 것'에 새로 뜨지 않는다."""
        school = School.objects.create(neis_atpt="J10", neis_code="7534000", name="합침초")
        g4 = Space.objects.create(school=school, grade=4, name="4학년")
        e = Event.objects.create(space=g4, date=TODAY, title="옛일정")
        old = NOW - timedelta(days=30)
        Event.objects.filter(pk=e.pk).update(updated_at=old)
        seat = Member.objects.create(space=g4, user=get_user_model().objects.create(username="google:feed"), name="합침", cls="4-1")
        open_school(school, UNTIL, seat)
        e.refresh_from_db()
        self.assertEqual((e.updated_at, e.scope), (old, "g4"))
