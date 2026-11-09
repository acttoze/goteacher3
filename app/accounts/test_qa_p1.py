"""P1 QA 보강: 구현 테스트(test_id_login)에 없는 경계 사례만.
임시 비밀번호의 손님·연결 없는 자리·다른 학교(토큰 바꿔 끼우기)·동학년·GET 거부, 잠금 중 아이디 존재 비노출,
비밀번호 바꾸기의 지금 비번 실패 누적, 비활성 계정, 모든 기기 로그아웃이 남의 세션을 지우지 않음, 구 해시 대소문자 로그인."""
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase

from spaces.models import Member, School, Space
from spaces.test_school import JoinBase

from .test_id_login import FAST, LEGACY, PW, login, signup
from .views import LOGIN_FAILS_ID, LOGIN_FAILS_ID_ALL, LOGIN_LOCKED, LOGIN_WRONG

User = get_user_model()


def id_user(username, pw=PW, **kw):
    u = User.objects.create(username=username, **kw)
    u.set_password(pw)
    u.save()
    return u


@FAST
class TempPasswordEdgeTests(JoinBase):
    def setUp(self):
        super().setUp()
        self.manager = Client()
        self.manager.force_login(self.u4)
        self.base = f"/s/{self.hub.token}"

    def seat(self, username, **kw):
        u = id_user(username)
        return u, Member.objects.create(space=self.hub, user=u, name=kw.get("name", username), cls="")

    def test_guest_and_unlinked_seats_refused(self):
        guest = User.objects.create(username="guest:qa1", first_name="손님")
        guest.set_unusable_password()
        guest.save()
        before = guest.password
        g = Member.objects.create(space=self.hub, user=guest, name="손님", cls="")
        empty = Member.objects.create(space=self.hub, user=None, name="빈자리", cls="")
        for m in (g, empty):
            r = self.manager.post(f"{self.base}/school/members/{m.key}/password", headers={"HX-Request": "true"})
            self.assertEqual(r.status_code, 400, m.name)
            self.assertNotIn("temp", r.context)
        guest.refresh_from_db()
        self.assertEqual(guest.password, before)

    def test_swapped_tokens_other_school(self):
        """다른 학교 아이디 회원: 우리 공간 토큰 + 그 자리 키 → 404. 다른 학교 관리자가 자기 토큰으로 우리 자리 → 404."""
        far_school = School.objects.create(neis_atpt="B10", neis_code="7010002", name="먼중학교")
        from spaces.merge import open_school
        far_mgr_user = User.objects.create(username="google:farmgr", first_name="먼관리자")
        far_g = Space.objects.create(school=far_school, grade=4, name="먼중 4학년")
        far_mgr_seat = Member.objects.create(space=far_g, user=far_mgr_user, name="먼관리자", cls="4-1")
        from spaces.test_merge import UNTIL
        far_hub = open_school(far_school, UNTIL, far_mgr_seat)
        far_hub_user = id_user("far_id")
        far_seat = Member.objects.create(space=far_hub, user=far_hub_user, name="먼아이디", cls="")
        _, ours = self.seat("our_id")

        r = self.manager.post(f"{self.base}/school/members/{far_seat.key}/password")
        self.assertEqual(r.status_code, 404)
        far_mgr = Client()
        far_mgr.force_login(far_mgr_user)
        r = far_mgr.post(f"/s/{far_hub.token}/school/members/{ours.key}/password")
        self.assertEqual(r.status_code, 404)
        r = far_mgr.post(f"{self.base}/school/members/{ours.key}/password")  # 남의 공간 토큰
        self.assertIn(r.status_code, (302, 403, 404))
        for u in ("far_id", "our_id"):
            self.assertEqual(login(Client(), u, PW).status_code, 302, u)  # 둘 다 비밀번호 그대로

    def test_grade_space_token_refused(self):
        """학교 공간이 아닌 동학년 공간 주소로는 임시 비밀번호를 줄 수 없다."""
        u = id_user("grade_id")
        m = Member.objects.create(space=self.g5, user=u, name="동학년아이디", cls="5-2")
        c = Client()
        c.force_login(self.u5)
        Member.objects.filter(space=self.g5, user=self.u5).update(is_manager=True)
        r = c.post(f"/s/{self.g5.token}/school/members/{m.key}/password")
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(login(Client(), "grade_id", PW).status_code, 302)

    def test_get_not_allowed_and_full_page_shows_once(self):
        _, m = self.seat("page_id", name="페이지샘")
        self.assertEqual(self.manager.get(f"{self.base}/school/members/{m.key}/password").status_code, 405)
        self.assertEqual(login(Client(), "page_id", PW).status_code, 302)
        r = self.manager.post(f"{self.base}/school/members/{m.key}/password")  # htmx 없이(전체 화면)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, r.context["temp"])
        self.assertNotContains(r, "티로드")
        self.assertIn("no-store", r["Cache-Control"])  # 한 번만 보이게: 캐시 금지

    def test_manager_cannot_reset_self(self):
        mgr = id_user("mgr_id")
        m = Member.objects.create(space=self.hub, user=mgr, name="아이디관리자", cls="", is_manager=True)
        c = Client()
        c.force_login(mgr)
        r = c.post(f"{self.base}/school/members/{m.key}/password", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 400)


@FAST
class LoginEdgeTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()

    def test_locked_message_same_for_existing_and_missing_id(self):
        id_user("realone")
        for name in ("realone", "ghost_one"):  # 보안 검토 S1: 여러 IP로는 아이디 전체 한도에서 잠긴다
            for i in range(LOGIN_FAILS_ID_ALL):
                login(Client(REMOTE_ADDR=f"10.2.{i // 250}.{i % 250}"), name, "wrong-wrong-1")
        a = login(Client(REMOTE_ADDR="10.3.0.1"), "realone", "wrong-wrong-1")
        b = login(Client(REMOTE_ADDR="10.3.0.2"), "ghost_one", "wrong-wrong-1")
        self.assertEqual((a.status_code, b.status_code), (429, 429))
        self.assertContains(a, LOGIN_LOCKED, status_code=429)
        self.assertContains(b, LOGIN_LOCKED, status_code=429)

    def test_inactive_account_same_message(self):
        id_user("sleeper", is_active=False)
        r = login(self.client, "sleeper", PW)
        self.assertContains(r, LOGIN_WRONG, status_code=400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_legacy_hash_other_case_login(self):
        u = User.objects.create(username="LegacyCase", password=LEGACY)
        self.assertEqual(login(self.client, "legacycase", "test-pass-1234").status_code, 302)
        u.refresh_from_db()
        self.assertFalse(u.password.startswith("g5pbkdf2$"))

    def test_signup_cannot_take_legacy_id_in_other_case(self):
        User.objects.create(username="LegacyCase", password=LEGACY)
        self.assertContains(signup(Client(), "LEGACYCASE"), "이미 쓰는 아이디", status_code=400)

    def test_logged_in_member_signup_redirects(self):
        id_user("already")
        login(self.client, "already", PW)
        r = signup(self.client, "second_acct")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(User.objects.filter(username="second_acct").exists())


@FAST
class AccountSessionEdgeTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        school = School.objects.create(neis_atpt="J10", neis_code="9", name="qa초")
        self.space = Space.objects.create(school=school, grade=4, name="qa초 4학년")
        self.me = id_user("me_id")
        self.you = id_user("you_id")
        Member.objects.create(space=self.space, user=self.me, name="나", cls="4-1")
        Member.objects.create(space=self.space, user=self.you, name="너", cls="4-2")
        self.settings = f"/s/{self.space.token}/settings"

    def test_logout_all_keeps_other_users_sessions(self):
        me, you = Client(), Client()
        login(me, "me_id", PW)
        login(you, "you_id", PW)
        me.post(f"{self.settings}/logout-all")
        self.assertEqual(me.get(self.settings).status_code, 302)
        self.assertEqual(you.get(self.settings).status_code, 200)

    def test_wrong_current_password_counts_toward_lock(self):
        login(self.client, "me_id", PW)
        bad = {"current": "nope-nope-1", "password": "new-pass-word-9", "password2": "new-pass-word-9"}
        for _ in range(LOGIN_FAILS_ID):
            self.client.post(f"{self.settings}/password", bad, headers={"HX-Request": "true"})
        r = self.client.post(f"{self.settings}/password", {**bad, "current": PW}, headers={"HX-Request": "true"})
        self.assertContains(r, LOGIN_LOCKED, status_code=400)
        self.me.refresh_from_db()
        self.assertTrue(self.me.check_password(PW))

    def test_new_password_rules_and_mismatch(self):
        login(self.client, "me_id", PW)
        for new, new2 in (("short1", "short1"), ("password", "password"), ("new-pass-word-9", "new-pass-word-8")):
            r = self.client.post(f"{self.settings}/password", {"current": PW, "password": new, "password2": new2},
                                 headers={"HX-Request": "true"})
            self.assertEqual(r.status_code, 400, new)
        self.me.refresh_from_db()
        self.assertTrue(self.me.check_password(PW))
