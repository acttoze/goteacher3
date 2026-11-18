"""W4 다른 동학년의 학교 공간 합류(join_free)와 자리 입장 코드(EntryCode, /enter)."""
import html
import re
import threading
import time
from datetime import date, timedelta
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import DEVICE_COOKIE, new_guest
from analytics.models import DailyUse
from collect.models import Cell, Collection, Field, Notification, Row
from legacy.models import LegacyMap
from today.models import ALL, Event

from . import entry
from .merge import join_free, open_free, open_school
from .models import ENTRY, INVITE, EntryCode, Member, MemberLog, School, Space

User = get_user_model()
SITE = "https://t.example"
JOIN_HINT = "동학년 화면 왼쪽 아래에서 합류해 주세요"


def account(name):
    return User.objects.create(username=f"google:{name}", first_name=name)


def client(user=None):
    c = Client()
    if user:
        c.force_login(user)
    return c


def seat(space, name, cls, user=None, **extra):
    return Member.objects.create(space=space, user=user or account(f"{space.pk}-{name}"), name=name, cls=cls, **extra)


def stuff(space, member, tag):
    c = Collection.objects.create(space=space, created_by=member, title=f"{tag} 취합", source="sheet", unit_head=["학년반", "담임"])
    row = Row.objects.create(collection=c, pos=0, unit=member.cls, who=member.name, member=member)
    event = Event.objects.create(space=space, by=member, date=timezone.localdate(), title=f"{tag} 일정", scope=ALL)
    bell = Notification.objects.create(space=space, member=member, kind=Notification.Kind.SEAT, day=timezone.localdate(),
                                       text=f"{tag} 알림", link=f"/s/{space.token}/c/")
    return c, row, event, bell


def texts(response):
    return [html.unescape(t) for t in re.findall(r'data-text="([^"]*)"', response.content.decode())]


def visible(response):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", response.content.decode()))


@override_settings(SITE_URL=SITE)
class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        patcher = mock.patch("spaces.neis.class_names", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7530001", name="새솔초등학교")
        self.g4 = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.g5 = Space.objects.create(school=self.school, grade=5, name="5학년")
        self.g6 = Space.objects.create(school=self.school, grade=6, name="6학년")
        self.head = seat(self.g4, "김관리", "4-1")
        self.lead = seat(self.g5, "박대표", "5-1")
        self.peer = seat(self.g5, "최동료", "5-2")
        self.guest_user = new_guest("한손님")
        self.guest = seat(self.g5, "한손님", "5-3", user=self.guest_user)
        self.six = seat(self.g6, "정육학", "6-1")
        self.hub = open_free(self.g4, self.head)
        self.school.refresh_from_db()
        self.head.refresh_from_db()
        self.url = f"/s/{self.g5.token}/school-start"


class JoinTests(Base):
    def post(self, user=None, **data):
        return client(user or self.lead.user).post(self.url, {"agree": "1", **data})

    def test_lead_moves_only_own_grade_with_everything(self):
        c, row, event, bell = stuff(self.g5, self.lead, "오학년")
        other, *_ = stuff(self.g6, self.six, "육학년")
        r = self.post()
        self.assertEqual((r.status_code, r["Location"]), (303, f"/s/{self.hub.token}/school"))
        self.g5.refresh_from_db()
        self.g6.refresh_from_db()
        self.assertEqual(self.g5.merged_into, self.hub)
        self.assertIsNone(self.g6.merged_into)
        self.assertEqual(Member.objects.in_space(self.hub).count(), 4)
        self.assertEqual(Member.objects.get(pk=self.peer.pk).group, "g5")
        c.refresh_from_db(), event.refresh_from_db(), bell.refresh_from_db(), other.refresh_from_db()
        self.assertEqual(c.space, self.hub)
        self.assertEqual((event.space, event.scope), (self.hub, "g5"))
        self.assertEqual((bell.space, bell.link), (self.hub, f"/s/{self.hub.token}/c/"))
        self.assertEqual(other.space, self.g6)
        self.assertEqual(Member.objects.get(pk=self.six.pk).space, self.g6)

    def test_joiner_is_not_manager(self):
        self.post()
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_manager)
        self.assertEqual(list(Member.objects.in_space(self.hub).filter(is_manager=True)), [self.head])
        self.assertEqual(client(self.lead.user).post(f"/s/{self.hub.token}/school/members/{self.peer.key}/manager", {"on": "1"}).status_code, 403)

    def test_needs_agreement(self):
        r = client(self.lead.user).post(self.url, {})
        self.assertContains(r, "체크해 주세요", status_code=400)
        self.g5.refresh_from_db()
        self.assertIsNone(self.g5.merged_into)

    def test_non_lead_gets_copy_text_only(self):
        r = client(self.peer.user).get(self.url)
        self.assertNotContains(r, 'name="agree"')
        msg = next(t for t in texts(r) if "합류" in t and self.url in t)
        self.assertIn(self.lead.name, msg)
        self.assertEqual(self.post(self.peer.user).status_code, 403)
        self.g5.refresh_from_db()
        self.assertIsNone(self.g5.merged_into)

    def test_guest_is_told_to_link_account(self):
        Member.objects.filter(pk__in=[self.lead.pk, self.peer.pk]).delete()  # 손님이 대표가 된 동학년
        r = client(self.guest_user).get(self.url)
        self.assertContains(r, "구글 계정으로 잇기")
        self.assertNotContains(r, 'name="agree"')
        self.assertEqual(self.post(self.guest_user).status_code, 403)

    def test_confirm_screen_says_what_moves(self):
        stuff(self.g5, self.lead, "오학년")
        r = client(self.lead.user).get(self.url)
        body = visible(r)
        self.assertIn("우리 5학년 3명", body)
        self.assertIn("취합 1건", body)
        self.assertIn("일정 1건", body)
        self.assertIn("학교 관리자", body)
        self.assertIn(self.head.name, body)
        self.assertIn("초대 링크", body)
        self.assertIn("관리자가 되지 않아요", body)
        self.assertContains(r, "우리 학년 합류")

    def test_personal_events_are_not_counted(self):
        Event.objects.create(space=self.g5, by=self.lead, date=timezone.localdate(), title="내 일정", scope="me")
        self.assertIn("일정 0건", visible(client(self.lead.user).get(self.url)))

    def test_double_join_is_safe(self):
        join_free(self.g5, self.lead)
        with self.assertRaises(ValueError):
            join_free(self.g5, Member.objects.get(pk=self.lead.pk))
        self.assertEqual(self.post().status_code, 303)  # 옛 주소의 두 번째 POST는 학교 공간으로
        self.assertEqual(Member.objects.in_space(self.hub).count(), 4)
        self.assertEqual(Notification.objects.filter(member=self.head).count(), 1)

    def test_same_user_seat_folds_only_for_this_grade(self):
        both = account("겹침")
        kept = seat(self.hub, "겹침", "교무실", user=both, group="office")
        moved = seat(self.g5, "겹침", "5-4", user=both)
        third = seat(self.g6, "겹침", "6-4", user=both)
        c, row, *_ = stuff(self.g5, moved, "겹친 자리")
        join_free(self.g5, self.lead)
        self.assertFalse(Member.objects.filter(pk=moved.pk).exists())
        row.refresh_from_db(), c.refresh_from_db()
        self.assertEqual((row.member, c.created_by), (kept, kept))
        self.assertEqual(Member.objects.get(pk=third.pk).space, self.g6)

    def test_same_day_usage_rows_do_not_collide(self):
        day = timezone.localdate()
        DailyUse.objects.create(day=day, user=self.lead.user, space=self.hub, route="/today", n=1)
        DailyUse.objects.create(day=day, user=self.lead.user, space=self.g5, route="/today", n=2)
        join_free(self.g5, self.lead)
        self.assertEqual(DailyUse.objects.filter(user=self.lead.user, space=self.hub).count(), 1)

    def test_old_invite_is_closed(self):
        invite = self.g5.invite_token
        join_free(self.g5, self.lead)
        c = Client()
        r = c.get(f"/j/{invite}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["retired"])
        self.assertNotIn(INVITE, c.session)

    def test_notifications(self):
        join_free(self.g5, self.lead)
        mine = Notification.objects.get(member=self.head)
        self.assertEqual((mine.space, mine.link), (self.hub, f"/s/{self.hub.token}/school"))
        self.assertIn("5학년", mine.text)
        self.assertIn("3명", mine.text)
        self.assertEqual(Notification.objects.filter(member=self.peer, space=self.hub).count(), 1)
        self.assertEqual(Notification.objects.filter(member=self.guest, space=self.hub).count(), 1)
        self.assertFalse(Notification.objects.filter(member=self.lead).exists())
        self.assertFalse(Notification.objects.filter(member=self.six).exists())

    def test_side_button_changes(self):
        c = client(self.lead.user)
        self.assertContains(c.get(f"/s/{self.g5.token}/today"), "우리 학년 합류")
        join_free(self.g5, self.lead)
        r = c.get(f"/s/{self.hub.token}/today")
        self.assertNotContains(r, "우리 학년 합류")
        self.assertNotContains(r, "school-cta")

    def test_board_cell_copies_join_hint_until_joined(self):
        c = client(self.head.user)
        url = f"/s/{self.hub.token}/school"
        before = c.get(url)
        hint = [t for t in texts(before) if JOIN_HINT in t]
        self.assertEqual(len(hint), 2)  # 5·6학년 칸
        self.assertTrue(all(f"/j/{self.hub.invite_token}" not in t for t in hint))
        self.assertContains(before, "동학년 사용 중 3명")
        join_free(self.g5, self.lead)
        after = c.get(url)
        self.assertEqual(len([t for t in texts(after) if JOIN_HINT in t]), 1)
        self.assertNotContains(after, "동학년 사용 중 3명")
        self.assertTrue(any(f"/j/{self.hub.invite_token}?g=g5" in t for t in texts(after)))

    def test_expired_self_opened_school_refuses(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        self.assertEqual(self.post().status_code, 409)
        self.g5.refresh_from_db()
        self.assertIsNone(self.g5.merged_into)

    def test_outsider_gets_404(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7530002", name="다른초등학교")
        stranger = seat(Space.objects.create(school=other, grade=5, name="5학년"), "남", "5-1")
        self.assertEqual(self.post(stranger.user).status_code, 404)


class PaidSchoolHasNoJoinTests(TestCase):
    def test_ordinary_paid_school(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7530003", name="유료초등학교", plan="school", paid_until=date(2030, 1, 1))
        hub = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name=school.name)
        seat(hub, "관리", "교무실", is_manager=True, group="office")
        g5 = Space.objects.create(school=school, grade=5, name="5학년")
        lead = seat(g5, "대표", "5-1")
        with self.assertRaises(ValueError):
            join_free(g5, lead)
        c = client(lead.user)
        url = f"/s/{g5.token}/school-start"
        self.assertNotContains(c.get(url), 'name="agree"')
        self.assertEqual(c.post(url, {"agree": "1"}).status_code, 409)
        self.assertNotContains(c.get(f"/s/{g5.token}/today"), "우리 학년 합류")
        g5.refresh_from_db()
        self.assertIsNone(g5.merged_into)

    def test_operator_opened_school_loses_join(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7530004", name="전환초등학교")
        g4 = Space.objects.create(school=school, grade=4, name="4학년")
        lead = seat(g4, "대표", "4-1")
        hub = open_free(g4, lead)
        open_school(School.objects.get(pk=school.pk), date(2030, 1, 1), manager=Member.objects.get(pk=lead.pk), identity_checked=True)
        late = Space.objects.create(school=school, grade=5, name="5학년")  # 이관 등으로 남은 동학년
        with self.assertRaises(ValueError):
            join_free(late, seat(late, "늦은", "5-1"))
        self.assertTrue(hub.pk)


class CodeBase(Base):
    def setUp(self):
        super().setUp()
        join_free(self.g5, self.lead)
        self.guest.refresh_from_db()
        self.empty = Member.objects.create(space=self.hub, name="빈자리", cls="교무실", group="office")
        self.admin = client(self.head.user)
        self.base = f"/s/{self.hub.token}/school/members"

    def issue(self, m=None):
        r = self.admin.post(f"{self.base}/{(m or self.empty).key}/entry-code")
        self.assertEqual(r.status_code, 200)
        return r.context["code"]

    def live(self, m=None):
        return EntryCode.objects.filter(member=m or self.empty, used_at__isnull=True)

    def start(self, code, c=None):
        c = c or Client()
        return c, c.post("/enter", {"code": code})

    def claim(self, user, code):
        c, r = self.start(code, client(user))
        self.assertEqual(r.status_code, 303)
        return c, c.post("/enter", {"claim": "1"})


class IssueTests(CodeBase):
    def test_only_hash_is_stored(self):
        code = self.issue()
        row = self.live().get()
        plain = code.replace("-", "")
        self.assertGreaterEqual(len(plain), 8)
        self.assertTrue(set(plain) <= set("ABCDEFGHJKMNPQRSTUVWXYZ23456789"))
        self.assertNotIn(plain, row.code_hash)
        self.assertEqual(row.code_hash, entry.code_hash(code))
        self.assertEqual(row.created_by, self.head.user)
        self.assertEqual((row.expires_at - timezone.now()).days, 13)
        self.assertFalse(MemberLog.objects.filter(after__contains=plain).exists())

    def test_shown_once(self):
        code = self.issue()
        self.assertNotContains(self.admin.get(f"/s/{self.hub.token}/school"), code)

    def test_manager_seat_without_user_refused(self):
        Member.objects.filter(pk=self.empty.pk).update(is_manager=True)
        self.assertEqual(self.admin.post(f"{self.base}/{self.empty.key}/entry-code").status_code, 400)

    def test_managers_only(self):
        self.assertEqual(client(self.peer.user).post(f"{self.base}/{self.empty.key}/entry-code").status_code, 403)
        self.assertFalse(self.live().exists())

    def test_other_school_manager_gets_404(self):
        for kw in ({"self_open": True}, {"self_open": False}):
            other = School.objects.create(neis_atpt="J10", neis_code=f"99{kw['self_open']:d}", name="다른학교", plan="school",
                                          paid_until=date(2030, 1, 1), self_opened_at=timezone.now() if kw["self_open"] else None)
            hub = Space.objects.create(school=other, kind=Space.Kind.SCHOOL, grade=0, name="다른학교")
            boss = seat(hub, "남관리", "교무실", is_manager=True, group="office")
            c = client(boss.user)
            self.assertEqual(c.post(f"/s/{hub.token}/school/members/{self.empty.key}/entry-code").status_code, 404)
            self.assertEqual(c.post(f"{self.base}/{self.empty.key}/entry-code").status_code, 404)
        self.assertFalse(self.live().exists())

    def test_paid_school_manager_can_issue(self):
        school = School.objects.create(neis_atpt="J10", neis_code="7530005", name="유료", plan="school", paid_until=date(2030, 1, 1))
        hub = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name="유료")
        boss = seat(hub, "관리", "교무실", is_manager=True, group="office")
        target = Member.objects.create(space=hub, name="빈", cls="교무실", group="office")
        self.assertEqual(client(boss.user).post(f"/s/{hub.token}/school/members/{target.key}/entry-code").status_code, 200)

    def test_grade_space_has_no_issue_url(self):
        c = client(self.six.user)
        self.assertEqual(c.post(f"/s/{self.g6.token}/school/members/{self.six.key}/entry-code").status_code, 404)

    def test_org_space_has_no_issue_url(self):
        org = School.objects.create(neis_atpt="J10", neis_code="", name="기관", plan="school", paid_until=date(2030, 1, 1))
        hub = Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="기관")
        boss = seat(hub, "관리", "미배정", is_manager=True)
        target = Member.objects.create(space=hub, name="빈", cls="미배정")
        self.assertEqual(client(boss.user).post(f"/s/{hub.token}/school/members/{target.key}/entry-code").status_code, 404)

    def test_expired_plan_refused(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        self.assertEqual(self.admin.post(f"{self.base}/{self.empty.key}/entry-code").status_code, 403)

    def test_reissue_kills_old_code(self):
        first = self.issue()
        second = self.issue()
        self.assertNotEqual(first, second)
        self.assertEqual(self.live().count(), 1)
        _, r = self.start(first)
        self.assertEqual(r.status_code, 400)
        _, r = self.start(second)
        self.assertEqual(r.status_code, 303)



    def test_issuer_must_be_manager_of_this_space(self):
        with self.assertRaises(ValueError):
            entry.issue(self.empty, self.peer.user)
        self.assertFalse(self.live().exists())

    def test_only_seats_without_user(self):
        self.issue(self.empty)
        for m in (self.peer, self.lead, self.guest):
            r = self.admin.post(f"{self.base}/{m.key}/entry-code")
            self.assertEqual(r.status_code, 400)
            self.assertFalse(EntryCode.objects.filter(member=m).exists())

    def test_issue_function_enforces_every_rule(self):
        self.assertTrue(entry.issue(self.empty, self.head.user)[0])
        with self.assertRaises(ValueError):
            entry.issue(self.guest, self.head.user)
        grade_seat = Member.objects.create(space=self.g6, name="육빈", cls="6-9")
        with self.assertRaises(ValueError):
            entry.issue(grade_seat, self.head.user)
        org = School.objects.create(neis_atpt="J10", neis_code="", name="기관", plan="school", paid_until=date(2030, 1, 1))
        org_seat = Member.objects.create(space=Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="기관"), name="빈", cls="미배정")
        with self.assertRaises(ValueError):
            entry.issue(org_seat, self.head.user)
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        with self.assertRaises(ValueError):
            entry.issue(self.empty, self.head.user)

    @override_settings(LEGACY_READONLY=True)
    def test_legacy_school_refused(self):
        LegacyMap.objects.create(kind="school", legacy_id="1", obj_id=self.school.pk)
        with self.assertRaises(ValueError):
            entry.issue(self.empty, self.head.user)
        self.assertNotContains(self.admin.get(f"/s/{self.hub.token}/school"), "entry-code")

    def test_roster_dialog_offers_code_only_without_user(self):
        body = self.admin.get(f"/s/{self.hub.token}/school").content.decode()
        self.assertIn(f"{self.empty.key}/entry-code", body)
        self.assertNotIn(f"{self.guest.key}/entry-code", body)
        self.assertNotIn(f"{self.peer.key}/entry-code", body)

    def test_demoted_or_removed_manager_codes_die(self):
        second = seat(self.hub, "부관리", "교무실", is_manager=True, group="office")
        other = Member.objects.create(space=self.hub, name="빈둘", cls="교무실", group="office")
        c = client(second.user)
        first = c.post(f"{self.base}/{self.empty.key}/entry-code").context["code"]
        self.assertEqual(self.admin.post(f"/s/{self.hub.token}/school/members/{second.key}/manager", {"on": "0"}).status_code, 302)
        self.assertEqual(self.start(first)[1].status_code, 400)
        Member.objects.filter(pk=second.pk).update(is_manager=True)
        c.post(f"{self.base}/{other.key}/entry-code")
        self.assertTrue(self.live(other).exists())
        self.admin.post(f"/s/{self.hub.token}/members/{second.key}/out")
        self.assertFalse(self.live(other).exists())

    def test_operator_opening_clears_unused_codes(self):
        self.issue()
        open_school(School.objects.get(pk=self.school.pk), date(2030, 1, 1), manager=Member.objects.get(pk=self.head.pk), identity_checked=True)
        self.assertFalse(self.live().exists())


class EnterTests(CodeBase):
    def test_login_then_claim(self):
        code = self.issue()
        pw = "long-enough-pass-77"
        user = User.objects.create(username="newteacher", first_name="새선생")
        user.set_password(pw)
        user.save()
        c, r = self.start(code.lower().replace("-", " "))
        self.assertEqual((r.status_code, r["Location"]), (303, "/enter"))
        self.assertNotIn(code.replace("-", ""), str(dict(c.session)))
        self.assertEqual(c.session[ENTRY]["hash"], entry.code_hash(code))
        self.assertTrue(c.session.get_expire_at_browser_close())
        page = c.get("/enter")
        self.assertContains(page, self.empty.name)
        self.assertContains(page, 'action="/auth/login"')
        r = c.post("/auth/login", {"username": "newteacher", "password": pw, "next": "/enter"})
        self.assertEqual(r["Location"], "/enter")
        self.assertContains(c.get("/enter"), "내 계정에 연결")
        r = c.post("/enter", {"claim": "1"})
        self.assertEqual((r.status_code, r["Location"]), (303, f"/s/{self.hub.token}/school"))
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user, user)
        self.assertFalse(self.empty.is_manager)
        self.assertEqual(c.get(f"/s/{self.hub.token}/school").status_code, 200)
        self.assertNotIn(ENTRY, c.session)
        self.assertIsNotNone(EntryCode.objects.get(member=self.empty).used_at)
        self.assertTrue(Notification.objects.filter(member=self.head, about=self.empty).exists())

    def test_remember_applies_only_after_the_seat_is_linked(self):
        code = self.issue()
        c, _ = self.start(code)
        c.post("/signup", {"username": "keeps_pc", "name": "새사람", "password": "long-enough-pass-77",
                           "password2": "long-enough-pass-77", "next": "/enter", "remember": "1"})
        self.assertTrue(c.session.get_expire_at_browser_close())
        self.assertNotIn(DEVICE_COOKIE, c.cookies)
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 303)
        self.assertFalse(c.session.get_expire_at_browser_close())
        self.assertTrue(c.cookies[DEVICE_COOKIE].value)

    def test_signup_then_claim(self):
        code = self.issue(self.empty)
        c, _ = self.start(code)
        self.assertContains(c.get("/enter"), "/signup?next=/enter")
        r = c.post("/signup", {"username": "fresh_one", "name": "새사람", "password": "long-enough-pass-77",
                               "password2": "long-enough-pass-77", "next": "/enter"})
        self.assertEqual(r["Location"], "/enter")
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 303)
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user.username, "fresh_one")

    def test_wrong_code(self):
        _, r = self.start("ABCD-EFGH-JKMN")
        self.assertContains(r, entry.BAD_CODE, status_code=400)

    def test_one_time(self):
        code = self.issue()
        self.claim(account("첫사람"), code)
        _, r = self.start(code, client(account("둘째")))
        self.assertEqual(r.status_code, 400)

    def test_double_click_is_safe(self):
        code = self.issue()
        user = account("두번")
        c, r = self.claim(user, code)
        self.assertEqual(r.status_code, 303)
        s = c.session
        entry.hold(s, entry.code_hash(code))
        s.save()
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 303)
        self.assertEqual(Member.objects.filter(user=user).count(), 1)

    def test_expired(self):
        code = self.issue()
        self.live().update(expires_at=timezone.now() - timedelta(seconds=1))
        _, r = self.start(code)
        self.assertEqual(r.status_code, 400)

    def test_expires_between_check_and_claim(self):
        code = self.issue()
        c, _ = self.start(code, client(account("늦은")))
        self.live().update(expires_at=timezone.now() - timedelta(seconds=1))
        c.post("/enter", {"claim": "1"})
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)

    def test_guest_session_cannot_use_a_code(self):
        code = self.issue()
        c = client(self.guest_user)
        self.assertContains(c.post("/enter", {"code": code}), entry.GUEST_HERE, status_code=403)
        self.assertNotIn(ENTRY, c.session)

    def test_seat_taken_between_check_and_claim_is_refused(self):
        code = self.issue()
        c, _ = self.start(code, client(account("늦은")))
        first = account("먼저")
        Member.objects.filter(pk=self.empty.pk).update(user=first)
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 400)
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user, first)

    def test_checked_code_lapses_after_thirty_minutes(self):
        code = self.issue()
        c, _ = self.start(code)
        s = c.session
        s[ENTRY] = {**s[ENTRY], "until": int(time.time()) - 1}
        s.save()
        self.assertContains(c.get("/enter"), 'name="code"')
        self.assertNotIn(ENTRY, c.session)
        self.assertLessEqual(self.start(code)[0].session[ENTRY]["until"] - time.time(), entry.HOLD_SECONDS)

    def test_session_limit_does_not_block_the_school(self):
        code = self.issue()
        c = Client()
        for _ in range(entry.FAILS_SESSION):
            self.assertEqual(c.post("/enter", {"code": "ABCD-EFGH-JKMN"}).status_code, 400)
        self.assertEqual(c.post("/enter", {"code": code}).status_code, 429)
        self.assertEqual(self.start(code)[1].status_code, 303)  # 같은 IP의 다른 선생님

    def test_attempt_limit_by_ip(self):
        code = self.issue()
        for _ in range(entry.FAILS_IP):
            self.assertEqual(self.start("ABCD-EFGH-JKMN")[1].status_code, 400)
        self.assertEqual(self.start(code)[1].status_code, 429)

    def test_attempt_limit_by_user(self):
        code = self.issue()
        user = account("대입")
        for i in range(entry.FAILS_USER):
            c = client(user)
            self.assertEqual(c.post("/enter", {"code": "ABCD-EFGH-JKMN"}, REMOTE_ADDR=f"10.0.0.{i + 1}").status_code, 400)
        self.assertEqual(client(user).post("/enter", {"code": code}, REMOTE_ADDR="10.0.1.1").status_code, 429)

    def test_user_with_a_seat_is_refused(self):
        code = self.issue()
        c, r = self.claim(self.six.user, code)
        self.assertContains(r, entry.HAS_SEAT, status_code=409)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)
        self.assertIsNone(EntryCode.objects.get(member=self.empty).used_at)

    def test_code_does_not_follow_a_folded_seat(self):
        user = account("겹침")
        kept = seat(self.hub, "겹침", "교무실", user=user, group="office")
        g3 = Space.objects.create(school=self.school, grade=3, name="3학년")
        lead = seat(g3, "삼대표", "3-1")
        gone = seat(g3, "겹침", "3-2", user=user)
        EntryCode.objects.create(member=gone, code_hash="x" * 64, expires_at=timezone.now() + timedelta(days=1), created_by=self.head.user)
        join_free(g3, lead)
        self.assertFalse(EntryCode.objects.filter(member=kept).exists())
        self.assertFalse(EntryCode.objects.filter(code_hash="x" * 64).exists())

    def test_expired_plan_stops_claim(self):
        code = self.issue()
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        self.assertEqual(self.start(code, client(account("늦은")))[1].status_code, 400)


class LostPcTests(CodeBase):
    def test_gate_tells_the_way_without_code(self):
        r = client(self.guest_user).get(f"/s/{self.hub.token}/school")
        self.assertContains(r, "학교 초대 링크로 계정을 만들어", status_code=403)
        self.assertContains(r, "이전 자리를 정리해 달라고", status_code=403)
        self.assertNotContains(r, 'href="/enter"', status_code=403)

    def test_manager_clears_old_seat_then_same_name_can_enter(self):
        col = Collection.objects.create(space=self.hub, created_by=self.head, title="옛 취합", source="sheet", unit_head=["학년반", "담임"])
        field = Field.objects.create(collection=col, pos=0, name="인원", kind="number")
        row = Row.objects.create(collection=col, pos=0, unit=self.guest.cls, who=self.guest.name, member=self.guest)
        Cell.objects.create(row=row, field=field, value="27", by=self.guest.name)
        new = account("새계정")
        c = client(new)
        form = {"group": "g5", "cls": "5-3", "name": self.guest.name}
        c.get(f"/j/{self.hub.invite_token}")
        self.assertContains(c.post("/join", form), "이미 들어온 선생님", status_code=400)
        self.assertEqual(self.admin.post(f"/s/{self.hub.token}/members/{self.guest.key}/out").status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.guest_user.pk).exists())
        self.assertEqual(c.post("/join", form).status_code, 302)
        mine = Member.objects.get(user=new)
        self.assertEqual((mine.space, mine.cls, mine.name), (self.hub, "5-3", self.guest.name))
        row.refresh_from_db()
        self.assertIsNone(row.member)
        self.assertEqual(Cell.objects.get(row=row).value, "27")
        page = self.admin.get(f"/s/{self.hub.token}/c/{col.key}/")
        self.assertContains(page, "27")
        self.assertContains(page, self.guest.name)


@skipUnless(connection.vendor == "postgresql", "동시 요청 직렬화(select_for_update)는 PostgreSQL에서만 확인된다")
@override_settings(SITE_URL=SITE)
class ConcurrentTests(TransactionTestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.school = School.objects.create(neis_atpt="J10", neis_code="7530009", name="동시초등학교")
        g4 = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.g5 = Space.objects.create(school=self.school, grade=5, name="5학년")
        self.g6 = Space.objects.create(school=self.school, grade=6, name="6학년")
        self.head = seat(g4, "관리", "4-1")
        self.five = seat(self.g5, "오대표", "5-1")
        self.six = seat(self.g6, "육대표", "6-1")
        self.hub = open_free(g4, self.head)

    def race(self, jobs):
        out, gate = [], threading.Barrier(len(jobs))

        def go(job):
            try:
                gate.wait()
                out.append(job())
            finally:
                connection.close()
        threads = [threading.Thread(target=go, args=(j,)) for j in jobs]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return out

    def test_double_click_and_two_grades(self):
        a, b, d = client(self.five.user), client(self.five.user), client(self.six.user)
        out = self.race([lambda: a.post(f"/s/{self.g5.token}/school-start", {"agree": "1"}).status_code,
                         lambda: b.post(f"/s/{self.g5.token}/school-start", {"agree": "1"}).status_code,
                         lambda: d.post(f"/s/{self.g6.token}/school-start", {"agree": "1"}).status_code])
        self.assertEqual(out, [303, 303, 303])
        self.assertEqual(Member.objects.in_space(self.hub).count(), 3)
        self.assertEqual(Notification.objects.filter(member=self.head).count(), 2)

    def test_code_used_once_by_two_people(self):
        target = Member.objects.create(space=self.hub, name="빈", cls="교무실", group="office")
        code, _ = entry.issue(target, self.head.user)
        people = [account("가"), account("나"), account("다")]
        clients = [client(u) for u in people]
        for c in clients:
            self.assertEqual(c.post("/enter", {"code": code}).status_code, 303)
        out = self.race([(lambda c=c: c.post("/enter", {"claim": "1"}).status_code) for c in clients])
        self.assertEqual(sorted(out), [303, 400, 400])
        target.refresh_from_db()
        self.assertIn(target.user, people)
        self.assertEqual(Member.objects.filter(user__in=people).count(), 1)

    def test_one_user_two_codes(self):
        a = Member.objects.create(space=self.hub, name="빈1", cls="교무실", group="office")
        b = Member.objects.create(space=self.hub, name="빈2", cls="교무실", group="office")
        user = account("한사람")
        c1, c2 = client(user), client(user)
        c1.post("/enter", {"code": entry.issue(a, self.head.user)[0]})
        c2.post("/enter", {"code": entry.issue(b, self.head.user)[0]})
        out = self.race([lambda: c1.post("/enter", {"claim": "1"}).status_code,
                         lambda: c2.post("/enter", {"claim": "1"}).status_code])
        self.assertEqual(sorted(out), [303, 409])
        self.assertEqual(Member.objects.filter(user=user).count(), 1)
