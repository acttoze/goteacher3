"""W4 QA 보강: 구현 테스트에 없는 경계(격리·평문 누출·코드 수명·구글 경로·시도 제한 우회·동시 요청)."""
import logging
import re
import threading
from datetime import date, timedelta
from unittest import mock, skipUnless

from django.apps import apps
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.db import connection, models
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import new_guest
from collect.models import Collection
from legacy.models import LegacyMap

from . import entry
from .merge import join_free, open_free, open_school, remove_manager
from .models import ENTRY, INVITE, EntryCode, Member, School, Space
from .test_w4_join import SITE, Base, CodeBase, User, account, client, seat, stuff, visible

GOOGLE = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec", "SITE_URL": SITE}
TOKEN = {"access_token": "at"}
INFO = {"sub": "w4-qa", "email": "t@example.com", "email_verified": True, "name": "구글선생"}
PW = "long-enough-pass-77"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def all_text_values():
    """DB의 글자 칸 전부(세션 포함)."""
    for model in apps.get_models():
        names = [f.name for f in model._meta.concrete_fields if isinstance(f, (models.CharField, models.TextField))]
        if names:
            for row in model._base_manager.values_list(*names):
                yield from (str(v) for v in row if v)


class JoinIsolation(Base):
    def test_confirm_says_it_cannot_be_undone_and_who_sees(self):
        body = visible(client(self.lead.user).get(self.url))
        self.assertIn("되돌릴 수 없어요", body)
        self.assertIn("초대 링크는 닫혀요", body)
        self.assertIn(f"지금 1명이에요({self.head.name} 선생님", body)
        self.assertIn("취합과 낸 값을 보고, 선생님을 내보내고, 내보낸 분의 취합을 넘겨받거나 지울 수 있어요", body)
        self.assertIn("합본은 못 열 수 있어요", body)

    def test_other_grade_and_school_manager_cannot_join_this_grade(self):
        for user in (self.six.user, self.head.user):
            self.assertEqual(client(user).post(self.url, {"agree": "1"}).status_code, 404)
        self.g5.refresh_from_db()
        self.assertIsNone(self.g5.merged_into)

    def test_other_grade_stays_outside_after_join(self):
        join_free(self.g5, self.lead)
        c = client(self.six.user)
        self.assertEqual(c.get(f"/s/{self.g5.token}/today").status_code, 404)
        self.assertEqual(c.get(f"/s/{self.hub.token}/today").status_code, 404)
        self.assertEqual(c.get(f"/s/{self.g6.token}/today").status_code, 200)

    def test_invite_opened_before_join_cannot_seat_anyone(self):
        c = Client()
        c.get(f"/j/{self.g5.invite_token}")
        self.assertIn(INVITE, c.session)
        join_free(self.g5, self.lead)
        before = Member.objects.count()
        c.post("/join", {"cls": "5-9", "name": "늦은손님"})
        self.assertEqual(Member.objects.count(), before)

    def test_submit_link_keeps_working(self):
        col, *_ = stuff(self.g5, self.lead, "오학년")
        before = Client().get(f"/c/{col.link_token}").status_code
        join_free(self.g5, self.lead)
        self.assertEqual(Client().get(f"/c/{col.link_token}").status_code, before)
        self.assertEqual(Collection.objects.get(pk=col.pk).link_token, col.link_token)

    def test_moved_collection_opens_for_manager_and_owner_only(self):
        col, *_ = stuff(self.g5, self.lead, "오학년")
        plain = seat(self.hub, "일반", "교무실", group="office")
        hub = join_free(self.g5, self.lead)
        url = f"/s/{hub.token}/c/{col.key}/"
        self.assertEqual(client(self.head.user).get(url).status_code, 200)
        self.assertEqual(client(self.lead.user).get(url).status_code, 200)
        self.assertEqual(client(plain.user).get(url).status_code, 404)
        self.assertEqual(client(self.peer.user).get(url).status_code, 404)

    def test_lead_already_in_hub_stays_plain_member(self):
        kept = seat(self.hub, self.lead.name, "교무실", user=self.lead.user, group="office")
        hub = join_free(self.g5, self.lead)
        kept.refresh_from_db()
        self.assertFalse(kept.is_manager)
        self.assertEqual(list(Member.objects.filter(user=self.lead.user)), [kept])
        self.assertEqual(Member.objects.in_space(hub).filter(is_manager=True).get(), self.head)


class JoinNotice(Base):
    def test_screen_names_managers_and_what_they_can_do(self):
        body = visible(client(self.lead.user).get(self.url))
        self.assertIn(f"지금 1명이에요({self.head.name} 선생님", body)
        self.assertNotIn(" 외)", body)
        for words in ("취합과 낸 값을 보고", "선생님을 내보내고", "넘겨받거나 지울 수 있어요", "합본은 못 열 수 있어요", "되돌릴 수 없어요"):
            self.assertIn(words, body)

    def test_several_managers_are_counted(self):
        seat(self.hub, "부관리", "교무실", is_manager=True, group="office")
        body = visible(client(self.lead.user).get(self.url))
        self.assertIn(f"지금 2명이에요({self.head.name} 선생님", body)
        self.assertIn(" 외)", body)


class HeldCode(CodeBase):
    def assert_gone_with_browser(self, c):
        """확인한 코드가 세션에 있으면 그 세션은 브라우저를 닫을 때 끝나야 한다(기준 7')."""
        self.assertIn(ENTRY, c.session)
        self.assertTrue(c.session.get_expire_at_browser_close())
        morsel = c.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual((morsel["max-age"], morsel["expires"]), ("", ""))

    def member_login(self):
        user = User(username="held_user", first_name="확인")
        user.set_password(PW)
        user.save()
        return user

    def test_checked_code_lives_in_a_browser_session(self):
        c, _ = self.start(self.issue())
        self.assert_gone_with_browser(c)

    def test_plain_login_keeps_it_browser_only(self):
        self.member_login()
        c, _ = self.start(self.issue())
        c.post("/auth/login", {"username": "held_user", "password": PW, "next": "/enter", "remember": "0"})
        self.assert_gone_with_browser(c)

    def test_remember_login_still_ends_with_browser(self):
        self.member_login()
        c, _ = self.start(self.issue())
        c.post("/auth/login", {"username": "held_user", "password": PW, "next": "/enter", "remember": "1"})
        self.assert_gone_with_browser(c)

    def test_remember_signup_still_ends_with_browser(self):
        c, _ = self.start(self.issue())
        c.post("/signup", {"username": "held_new", "name": "새", "password": PW, "password2": PW, "next": "/enter", "remember": "1"})
        self.assert_gone_with_browser(c)

    def test_login_does_not_extend_the_thirty_minutes(self):
        self.member_login()
        c, _ = self.start(self.issue())
        until = c.session[ENTRY]["until"]
        with mock.patch("spaces.entry.time.time", return_value=until - 600):
            c.post("/auth/login", {"username": "held_user", "password": PW, "next": "/enter", "remember": "0"})
        self.assertEqual(c.session[ENTRY]["until"], until)

    def test_claim_works_until_the_last_second_then_stops(self):
        user = self.member_login()
        c, _ = self.start(self.issue(), client(user))
        until = c.session[ENTRY]["until"]
        with mock.patch("spaces.entry.time.time", return_value=until):
            self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 302)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)
        c, _ = self.start(self.issue(), client(user))
        with mock.patch("spaces.entry.time.time", return_value=c.session[ENTRY]["until"] - 1):
            self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 303)
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user, user)

    def test_guest_is_told_to_log_out_and_can_after_that(self):
        code = self.issue()
        c = client(self.guest_user)
        r = c.post("/enter", {"code": code})
        self.assertContains(r, entry.GUEST_HERE, status_code=403)
        self.assertContains(r, 'action="/auth/logout"', status_code=403)
        self.assertEqual(c.session["_auth_user_id"], str(self.guest_user.pk))
        c.post("/auth/logout")
        self.assertEqual(c.post("/enter", {"code": code}).status_code, 303)

    @override_settings(LEGACY_READONLY=True)
    def test_school_turning_legacy_after_issue_stops_claim(self):
        code = self.issue()
        c, _ = self.start(code, client(account("늦은이")))
        LegacyMap.objects.create(kind="school", legacy_id="w4-qa", obj_id=self.school.pk)
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 400)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)


class CodeClosure(CodeBase):
    def setUp(self):
        super().setUp()
        self.second = seat(self.hub, "부관리", "교무실", is_manager=True, group="office")
        self.deputy = client(self.second.user)
        self.other = Member.objects.create(space=self.hub, name="빈둘", cls="교무실", group="office")

    def by_deputy(self, m=None):
        return self.deputy.post(f"{self.base}/{(m or self.empty).key}/entry-code").context["code"]

    def demote(self):
        r = self.admin.post(f"/s/{self.hub.token}/school/members/{self.second.key}/manager", {"on": "0"})
        self.assertEqual(r.status_code, 302)

    def test_leaving_manager_takes_own_codes_only(self):
        self.by_deputy()
        mine = self.issue(self.other)
        self.deputy.post(f"/s/{self.hub.token}/settings/leave", {"confirm": self.hub.name})
        self.assertFalse(Member.objects.filter(pk=self.second.pk).exists())
        self.assertFalse(self.live(self.empty).exists())
        self.assertIsNotNone(entry._find(entry.code_hash(mine)))

    def test_demotion_landing_after_the_gate_leaves_no_code(self):
        """관리자 확인(게이트)을 지난 뒤 해제가 먼저 끝나는 순서를 그대로 재현한다."""
        real = entry.issue

        def late(member, by):
            remove_manager(Member.objects.get(pk=self.second.pk))
            return real(member, by)
        with mock.patch("spaces.entry.issue", side_effect=late):
            self.deputy.post(f"{self.base}/{self.empty.key}/entry-code")
        self.assertFalse(EntryCode.objects.filter(created_by=self.second.user, used_at__isnull=True).exists())

    def test_demotion_stops_a_checked_code(self):
        c, _ = self.start(self.by_deputy(), client(account("대기")))
        self.demote()
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 400)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)

    def test_used_code_and_linked_seat_survive_demotion(self):
        taker = account("이미")
        self.claim(taker, self.by_deputy())
        self.demote()
        self.assertTrue(EntryCode.objects.filter(member=self.empty, used_at__isnull=False).exists())
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user, taker)

    def test_operator_reset_keeps_history_but_closes_every_open_code(self):
        self.claim(account("이미"), self.by_deputy())
        dead = self.issue(self.other)
        open_school(School.objects.get(pk=self.school.pk), date(2030, 1, 1), manager=Member.objects.get(pk=self.head.pk), identity_checked=True)
        self.assertIsNone(entry._find(entry.code_hash(dead)))
        self.assertEqual(EntryCode.objects.filter(used_at__isnull=True).count(), 0)
        self.assertEqual(EntryCode.objects.filter(used_at__isnull=False).count(), 1)


class LostPcEdges(CodeBase):
    def test_removed_guest_browser_reaches_the_invite_login(self):
        old_pc = client(self.guest_user)
        self.assertEqual(self.admin.post(f"/s/{self.hub.token}/members/{self.guest.key}/out").status_code, 302)
        self.assertEqual(old_pc.get(f"/j/{self.hub.invite_token}").status_code, 302)
        r = old_pc.get("/join")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.wsgi_request.user.is_authenticated)


class CodeSecrecy(CodeBase):
    def test_plain_code_is_nowhere_after_the_whole_flow(self):
        cap = Capture()
        root = logging.getLogger()
        old = root.level
        root.addHandler(cap)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old)
        self.addCleanup(root.removeHandler, cap)
        code = self.issue()
        plain = code.replace("-", "")
        c, r = self.start(code)
        self.assertNotContains(c.get("/enter"), plain)
        self.assertTrue(Session.objects.exists())
        self.assertFalse([s for s in c.session.values() if plain in str(s) or code in str(s)])
        c.post("/signup", {"username": "fresh_two", "name": "새사람", "password": PW, "password2": PW, "next": "/enter"})
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 303)
        leaked = [v for v in all_text_values() if plain in v or code in v]
        self.assertEqual(leaked, [])
        self.assertEqual([x for x in cap.lines if plain in x or code in x], [])

    def test_wrong_code_is_not_echoed(self):
        _, r = self.start("ZZZZ-YYYY-XXXX")
        self.assertNotContains(r, "ZZZZ", status_code=400)

    def test_responses_are_not_cached(self):
        r = self.admin.post(f"{self.base}/{self.empty.key}/entry-code")
        self.assertIn("no-store", r["Cache-Control"])
        self.assertIn("no-store", Client().get("/enter")["Cache-Control"])

    def test_issue_is_post_only_and_csrf_guarded(self):
        self.assertEqual(self.admin.get(f"{self.base}/{self.empty.key}/entry-code").status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.head.user)
        self.assertEqual(strict.post(f"{self.base}/{self.empty.key}/entry-code").status_code, 403)
        self.assertFalse(self.live().exists())
        self.assertEqual(Client(enforce_csrf_checks=True).post("/enter", {"code": "ABCD-EFGH-JKMN"}).status_code, 403)


class CodeScope(CodeBase):
    def test_hub_manager_cannot_reach_unjoined_grade_seat(self):
        empty = Member.objects.create(space=self.g6, name="육빈자리", cls="6-9")
        self.assertEqual(self.admin.post(f"{self.base}/{empty.key}/entry-code").status_code, 404)
        self.assertEqual(self.admin.post(f"/s/{self.g6.token}/school/members/{empty.key}/entry-code").status_code, 404)
        self.assertFalse(EntryCode.objects.filter(member=empty).exists())

    def test_code_on_grade_seat_never_claims(self):
        empty = Member.objects.create(space=self.g6, name="육빈자리", cls="6-9")
        with self.assertRaises(ValueError):
            entry.issue(empty, self.head.user)
        row = EntryCode.objects.create(member=empty, code_hash="y" * 64, expires_at=timezone.now() + timedelta(days=1))  # 규칙 전에 생긴 줄이 있어도
        with self.assertRaises(ValueError):
            entry.claim(row.code_hash, account("다른이"))

    def test_seat_made_manager_after_issue_is_closed(self):
        code = self.issue(self.empty)
        c, _ = self.start(code, client(account("늦은이")))
        Member.objects.filter(pk=self.empty.pk).update(is_manager=True)
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 400)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)

    def test_removed_seat_kills_code(self):
        code = self.issue(self.empty)
        self.empty.delete()
        self.assertEqual(self.start(code)[1].status_code, 400)

    def test_claimed_seat_gets_no_new_code(self):
        code = self.issue()
        self.claim(account("새주인"), code)
        self.assertEqual(self.admin.post(f"{self.base}/{self.empty.key}/entry-code").status_code, 400)

    def test_claim_gives_no_manager_power(self):
        code = self.issue()
        c, _ = self.claim(account("새주인"), code)
        self.assertEqual(c.post(f"{self.base}/{self.empty.key}/entry-code").status_code, 403)


class CodeLife(CodeBase):
    def test_valid_until_fourteen_days_then_dead(self):
        code = self.issue()
        row = self.live().get()
        self.assertAlmostEqual((row.expires_at - timezone.now()).total_seconds(), 14 * 86400, delta=60)
        hashed = entry.code_hash(code)
        with mock.patch("django.utils.timezone.now", return_value=row.expires_at - timedelta(seconds=1)):
            self.assertIsNotNone(entry._find(hashed))
        with mock.patch("django.utils.timezone.now", return_value=row.expires_at + timedelta(seconds=1)):
            self.assertIsNone(entry._find(hashed))
            with self.assertRaises(ValueError):
                entry.claim(hashed, account("늦은이"))

    def test_reissue_between_check_and_claim(self):
        first = self.issue()
        c, _ = self.start(first, client(account("먼저")))
        self.issue()
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 400)
        self.assertNotIn(ENTRY, c.session)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)

    def test_second_person_loses_after_first_claims(self):
        code = self.issue(self.empty)
        first, second = account("첫째"), account("둘째")
        a, _ = self.start(code, client(first))
        b, _ = self.start(code, client(second))
        self.assertEqual(a.post("/enter", {"claim": "1"}).status_code, 303)
        self.assertEqual(b.post("/enter", {"claim": "1"}).status_code, 400)
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user, first)
        self.assertFalse(Member.objects.filter(user=second).exists())

    def test_refused_person_leaves_code_for_the_right_one(self):
        code = self.issue()
        self.claim(self.six.user, code)
        taker = account("진짜주인")
        self.assertEqual(self.claim(taker, code)[1].status_code, 303)
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user, taker)

    def test_guest_cannot_claim_with_planted_session(self):
        code = self.issue(self.empty)
        c = client(new_guest("다른손님"))
        s = c.session
        entry.hold(s, entry.code_hash(code))
        s.save()
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 302)
        self.empty.refresh_from_db()
        self.assertIsNone(self.empty.user)

    def test_logout_forgets_checked_code(self):
        code = self.issue()
        c, _ = self.start(code, client(account("잠깐")))
        c.logout()
        self.assertNotContains(c.get("/enter"), self.empty.name)



class CodeInput(CodeBase):
    def test_odd_input_is_a_plain_refusal(self):
        for raw in ("", " ", "\x00" * 12, "한글코드한글코드한글코", "A" * 5000, "<script>", "ABCD-EFGH-JKM\x00"):
            self.assertEqual(self.start(raw)[1].status_code, 400, repr(raw))

    def test_forged_forwarded_header_does_not_reset_ip_limit(self):
        code = self.issue()
        for i in range(entry.FAILS_IP):
            Client().post("/enter", {"code": "ABCD-EFGH-JKMN"}, HTTP_X_FORWARDED_FOR=f"9.9.9.{i % 250}, 203.0.113.7")
        r = Client().post("/enter", {"code": code}, HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.7")
        self.assertEqual(r.status_code, 429)

    def test_right_codes_do_not_use_up_the_school_ip(self):
        code = self.issue()
        for _ in range(entry.FAILS_IP + 5):
            self.assertEqual(self.start(code)[1].status_code, 303)


@override_settings(**GOOGLE)
class CodeLoginRoutes(CodeBase):
    def test_google_route_keeps_the_code(self):
        code = self.issue()
        c, _ = self.start(code)
        page = c.get("/enter")
        self.assertContains(page, 'name="via" value="google"')
        r = c.get(c.post("/auth/login", {"via": "google", "next": "/enter", "remember": "0"})["Location"])
        state = c.session["google_state"]
        self.assertEqual(r.status_code, 302)
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, INFO]):
            r = c.get("/auth/google/callback", {"state": state, "code": "abc"})
        self.assertEqual(r["Location"], "/enter")
        self.assertContains(c.get("/enter"), "내 계정에 연결")
        self.assertEqual(c.post("/enter", {"claim": "1"})["Location"], f"/s/{self.hub.token}/school")
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.user.username, "google:w4-qa")

    def test_wrong_password_then_right_one_still_lands_on_enter(self):
        user = User(username="retry_user", first_name="재시도")
        user.set_password(PW)
        user.save()
        c, _ = self.start(self.issue())
        self.assertContains(c.get("/enter"), 'name="next" value="/enter"')
        bad = c.post("/auth/login", {"username": "retry_user", "password": "wrong-pass-000", "next": "/enter"})
        self.assertContains(bad, 'name="next" value="/enter"', status_code=bad.status_code)
        r = c.post("/auth/login", {"username": "retry_user", "password": PW, "next": "/enter"})
        self.assertEqual(r["Location"], "/enter")
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 303)


@skipUnless(connection.vendor == "postgresql", "동시 요청 직렬화는 PostgreSQL에서만 확인된다")
@override_settings(SITE_URL=SITE)
class Races(TransactionTestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        patcher = mock.patch("spaces.neis.class_names", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7530077", name="경합초등학교")
        g4 = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.g5 = Space.objects.create(school=self.school, grade=5, name="5학년")
        self.head = seat(g4, "관리", "4-1")
        self.lead = seat(self.g5, "오대표", "5-1")
        self.hub = open_free(g4, self.head)
        self.target = Member.objects.create(space=self.hub, name="빈", cls="교무실", group="office")

    def login(self, user):
        c = Client(raise_request_exception=False)
        c.force_login(user)
        return c

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
        [t.join(30) for t in threads]
        self.assertEqual(len(out), len(jobs))
        return out

    def test_simultaneous_reissue_leaves_one_live_code(self):
        url = f"/s/{self.hub.token}/school/members/{self.target.key}/entry-code"
        admins = [self.login(self.head.user) for _ in range(5)]
        out = self.race([(lambda c=c: c.post(url)) for c in admins])
        self.assertEqual([r.status_code for r in out], [200] * 5)
        self.assertEqual(EntryCode.objects.filter(member=self.target, used_at__isnull=True).count(), 1)
        codes = [re.search(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}\b", r.content.decode()).group() for r in out]  # 스레드에서는 r.context가 섞인다
        self.assertEqual(len([c for c in codes if entry._find(entry.code_hash(c))]), 1)

    def test_reissue_against_claim_never_breaks(self):
        code, _ = entry.issue(self.target, self.head.user)
        user = account("경합")
        taker = self.login(user)
        self.assertEqual(taker.post("/enter", {"code": code}).status_code, 303)
        admin = self.login(self.head.user)
        url = f"/s/{self.hub.token}/school/members/{self.target.key}/entry-code"
        out = self.race([lambda: taker.post("/enter", {"claim": "1"}).status_code, lambda: admin.post(url).status_code])
        self.target.refresh_from_db()
        self.assertNotIn(500, out)
        if self.target.user_id:  # 잇기가 먼저: 재발급은 거절
            self.assertEqual(sorted(out), [303, 400])
            self.assertFalse(EntryCode.objects.filter(member=self.target, used_at__isnull=True).exists())
        else:  # 재발급이 먼저: 옛 코드는 거절
            self.assertEqual(sorted(out), [200, 400])

    def test_join_against_claim_does_not_deadlock(self):
        code, _ = entry.issue(self.target, self.head.user)
        taker = self.login(account("합류중입장"))
        self.assertEqual(taker.post("/enter", {"code": code}).status_code, 303)
        leader = self.login(self.lead.user)
        out = self.race([lambda: taker.post("/enter", {"claim": "1"}).status_code,
                         lambda: leader.post(f"/s/{self.g5.token}/school-start", {"agree": "1"}).status_code])
        self.assertEqual(out.count(303), 2)
        self.g5.refresh_from_db()
        self.assertEqual(self.g5.merged_into, self.hub)
